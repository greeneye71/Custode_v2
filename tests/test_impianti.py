"""Impianti: schema, isolamento, piano di manutenzione, avvisi."""
import pytest
from werkzeug.security import generate_password_hash

from models import execute, query_one, query_all


@pytest.fixture
def ambiente(app):
    """Due strutture con una divisione e un admin ciascuna.

    Modellata su tests/test_isolamento.py: le righe si inseriscono con
    execute() dentro un app_context, senza passare dalle rotte.
    """
    with app.app_context():
        dati = {}
        for chiave, nome, codice, email in (
            ('a', 'Clinica A', 'CLA', 'admin.a@test.it'),
            ('b', 'Clinica B', 'CLB', 'admin.b@test.it'),
        ):
            sid = execute(
                "INSERT INTO strutture (nome, codice, attiva, email_notifiche,"
                " email_responsabile) VALUES (?, ?, 1, ?, ?)",
                (nome, codice, f'notifiche.{chiave}@test.it',
                 f'responsabile.{chiave}@test.it')
            ).lastrowid
            did = execute(
                "INSERT INTO divisioni (struttura_id, nome, codice, email)"
                " VALUES (?, ?, ?, ?)",
                (sid, f'Divisione {chiave.upper()}', f'DIV-{chiave.upper()}',
                 f'divisione.{chiave}@test.it')
            ).lastrowid
            uid = execute(
                "INSERT INTO utenti (struttura_id, nome, cognome, email,"
                " password_hash, ruolo, attivo) VALUES (?, ?, ?, ?, ?, 'admin', 1)",
                (sid, 'Admin', chiave.upper(), email,
                 generate_password_hash('Passw0rd!'))
            ).lastrowid
            dati[chiave] = {'struttura': sid, 'divisione': did,
                            'utente': uid, 'email': email}
        return dati


def entra(client, email):
    """Login con la password della fixture."""
    return client.post('/login', data={'email': email, 'password': 'Passw0rd!'},
                       follow_redirects=True)


def test_schema_impianti_creato(app, ambiente):
    """Le tabelle e la vista esistono dopo apply_schema_updates()."""
    attese = {'manutentori', 'impianti', 'impianti_componenti',
              'impianti_documenti', 'impianti_scadenze', 'impianti_interventi',
              'impianti_avvisi_inviati'}
    with app.app_context():
        nomi = {r['name'] for r in query_all(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert attese <= nomi
        viste = {r['name'] for r in query_all(
            "SELECT name FROM sqlite_master WHERE type='view'")}
        assert 'prossime_scadenze_impianti' in viste
        colonne = {r['name'] for r in query_all("PRAGMA table_info(divisioni)")}
        assert {'indirizzo', 'email', 'telefono', 'responsabile'} <= colonne


def test_vista_impianti_classifica_e_esclude_dismessi(app, ambiente):
    """La vista dà la priorità giusta e salta gli impianti dismessi."""
    with app.app_context():
        a = ambiente['a']
        attivo = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Cabina elettrica', 'elettrico')",
            (a['struttura'], a['divisione'])
        ).lastrowid
        dismesso = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo, stato)"
            " VALUES (?, ?, 'Vecchia centrale', 'riscaldamento', 'dismesso')",
            (a['struttura'], a['divisione'])
        ).lastrowid
        for impianto, giorni in ((attivo, -3), (attivo, 5), (attivo, 200),
                                 (dismesso, 1)):
            execute(
                "INSERT INTO impianti_scadenze (impianto_id, nome,"
                " periodicita_mesi, prossima_scadenza)"
                " VALUES (?, 'Verifica', 24, date('now', ?))",
                (impianto, f'{giorni} days')
            )
        righe = query_all(
            "SELECT priorita FROM prossime_scadenze_impianti WHERE impianto_id = ?",
            (attivo,))
        assert [r['priorita'] for r in righe] == ['scaduto', 'urgente', 'ok']
        assert query_all(
            "SELECT 1 FROM prossime_scadenze_impianti WHERE impianto_id = ?",
            (dismesso,)) == []


def test_impianto_accessibile_isola_le_strutture(app, ambiente):
    """Un admin non raggiunge l'impianto dell'altra struttura, nemmeno per id."""
    from flask import g
    from models import impianto_accessibile

    with app.app_context():
        b = ambiente['b']
        impianto_b = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Impianto segreto B', 'idraulico')",
            (b['struttura'], b['divisione'])
        ).lastrowid

    with app.test_request_context():
        g.user = {'id': ambiente['a']['utente'], 'ruolo': 'admin'}
        g.struttura_id = ambiente['a']['struttura']
        g.divisioni = []
        assert impianto_accessibile(impianto_b) is None

        g.struttura_id = ambiente['b']['struttura']
        riga = impianto_accessibile(impianto_b)
        assert riga is not None and riga['nome'] == 'Impianto segreto B'


def test_impianto_accessibile_rispetta_le_divisioni(app, ambiente):
    """Un utente semplice vede solo gli impianti delle sue divisioni."""
    from flask import g
    from models import impianto_accessibile

    with app.app_context():
        a = ambiente['a']
        altra_div = execute(
            "INSERT INTO divisioni (struttura_id, nome, codice) VALUES (?, 'Altra', 'ALT')",
            (a['struttura'],)).lastrowid
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Quadro Altra', 'elettrico')",
            (a['struttura'], altra_div)).lastrowid

    with app.test_request_context():
        g.user = {'id': 99, 'ruolo': 'utente'}
        g.struttura_id = ambiente['a']['struttura']
        g.divisioni = [{'id': ambiente['a']['divisione']}]
        assert impianto_accessibile(impianto) is None
        g.divisioni = [{'id': altra_div}]
        assert impianto_accessibile(impianto) is not None


def test_catalogo_copre_ogni_tipo_e_filtra_le_voci_presenti():
    """Ogni tipo ha una voce nel catalogo; voci_mancanti esclude i doppioni."""
    from impianti_catalogo import CATALOGO, voci_per_tipo, voci_mancanti

    tipi = {'elettrico', 'idraulico', 'riscaldamento', 'climatizzazione',
            'antincendio', 'gas_medicali', 'ascensori', 'rete_dati', 'altro'}
    assert set(CATALOGO) == tipi
    for voci in CATALOGO.values():
        for v in voci:
            assert set(v) == {'nome', 'mesi', 'riferimento'}
            assert isinstance(v['mesi'], int) and v['mesi'] > 0

    elettrico = voci_per_tipo('elettrico')
    assert any(v['nome'] == 'Verifica impianto di terra' and v['mesi'] == 24
               for v in elettrico)
    assert voci_per_tipo('inesistente') == []

    mancanti = voci_mancanti('elettrico', ['Verifica impianto di terra'])
    assert [v['nome'] for v in mancanti] == ['Prova interruttori differenziali']


def test_aggiungi_mesi_taglia_il_giorno_sui_mesi_corti():
    """31 gennaio + 1 mese = 28/29 febbraio, non un errore."""
    from impianti_service import aggiungi_mesi
    assert aggiungi_mesi('2026-01-31', 1) == '2026-02-28'
    assert aggiungi_mesi('2024-01-31', 1) == '2024-02-29'
    assert aggiungi_mesi('2026-03-15', 24) == '2028-03-15'
    assert aggiungi_mesi('2026-12-31', 2) == '2027-02-28'


def _impianto_con_piano(ambiente, periodicita=24, scadenza='2026-01-10'):
    a = ambiente['a']
    impianto = execute(
        "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
        " VALUES (?, ?, 'Cabina', 'elettrico')",
        (a['struttura'], a['divisione'])).lastrowid
    scad = execute(
        "INSERT INTO impianti_scadenze (impianto_id, nome, periodicita_mesi,"
        " prossima_scadenza) VALUES (?, 'Verifica di terra', ?, ?)",
        (impianto, periodicita, scadenza)).lastrowid
    return impianto, scad


def test_intervento_positivo_sposta_la_scadenza(app, ambiente):
    from impianti_service import registra_intervento
    with app.app_context():
        impianto, scad = _impianto_con_piano(ambiente)
        _, nuova = registra_intervento(impianto, {
            'scadenza_id': scad, 'tipo': 'verifica',
            'data_intervento': '2026-01-08', 'esito': 'positivo'})
        assert nuova == '2028-01-08'
        riga = query_one("SELECT * FROM impianti_scadenze WHERE id = ?", (scad,))
        assert riga['prossima_scadenza'] == '2028-01-08'
        assert riga['attiva'] == 1


def test_intervento_negativo_non_sposta_nulla(app, ambiente):
    from impianti_service import registra_intervento
    with app.app_context():
        impianto, scad = _impianto_con_piano(ambiente)
        _, nuova = registra_intervento(impianto, {
            'scadenza_id': scad, 'tipo': 'verifica',
            'data_intervento': '2026-01-08', 'esito': 'negativo'})
        assert nuova is None
        riga = query_one("SELECT * FROM impianti_scadenze WHERE id = ?", (scad,))
        assert riga['prossima_scadenza'] == '2026-01-10'
        assert riga['attiva'] == 1


def test_intervento_con_riserva_sposta_come_positivo(app, ambiente):
    from impianti_service import registra_intervento
    with app.app_context():
        impianto, scad = _impianto_con_piano(ambiente, periodicita=6)
        _, nuova = registra_intervento(impianto, {
            'scadenza_id': scad, 'tipo': 'ordinaria',
            'data_intervento': '2026-01-08', 'esito': 'con_riserva'})
        assert nuova == '2026-07-08'


def test_scadenza_una_tantum_si_chiude(app, ambiente):
    """periodicita_mesi NULL: eseguita una volta, la riga esce dal piano."""
    from impianti_service import registra_intervento
    with app.app_context():
        impianto, scad = _impianto_con_piano(ambiente, periodicita=None)
        _, nuova = registra_intervento(impianto, {
            'scadenza_id': scad, 'tipo': 'straordinaria',
            'data_intervento': '2026-01-08', 'esito': 'positivo'})
        assert nuova is None
        riga = query_one("SELECT * FROM impianti_scadenze WHERE id = ?", (scad,))
        assert riga['attiva'] == 0


def test_intervento_senza_scadenza_e_solo_storico(app, ambiente):
    from impianti_service import registra_intervento
    with app.app_context():
        impianto, scad = _impianto_con_piano(ambiente)
        iid, nuova = registra_intervento(impianto, {
            'tipo': 'riparazione', 'data_intervento': '2026-01-08',
            'descrizione': 'Sostituito interruttore'})
        assert nuova is None
        assert query_one("SELECT * FROM impianti_interventi WHERE id = ?",
                         (iid,))['descrizione'] == 'Sostituito interruttore'
        assert query_one("SELECT prossima_scadenza FROM impianti_scadenze"
                         " WHERE id = ?", (scad,))['prossima_scadenza'] == '2026-01-10'


def test_applica_catalogo_crea_il_piano(app, ambiente):
    from impianti_service import applica_catalogo
    with app.app_context():
        a = ambiente['a']
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Antincendio piano 1', 'antincendio')",
            (a['struttura'], a['divisione'])).lastrowid
        creati = applica_catalogo(
            impianto, 'antincendio',
            ['Controllo estintori', 'Controllo idranti'], '2026-01-01')
        assert creati == 2
        righe = query_all(
            "SELECT nome, periodicita_mesi, prossima_scadenza,"
            " riferimento_normativo FROM impianti_scadenze"
            " WHERE impianto_id = ? ORDER BY nome", (impianto,))
        assert [r['nome'] for r in righe] == ['Controllo estintori',
                                              'Controllo idranti']
        assert righe[0]['periodicita_mesi'] == 6
        assert righe[0]['prossima_scadenza'] == '2026-07-01'
        assert righe[0]['riferimento_normativo'] == 'UNI 9994-1'
        # Un nome non in catalogo viene ignorato, non inventato.
        assert applica_catalogo(impianto, 'antincendio', ['Fantasia'],
                                '2026-01-01') == 0
