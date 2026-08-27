"""Impianti: schema, isolamento, piano di manutenzione, avvisi."""
import io
import re
from datetime import datetime as _orig_datetime

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


def test_lista_impianti_isola_le_strutture(client, app, ambiente):
    with app.app_context():
        for chiave, nome in (('a', 'Cabina A'), ('b', 'SEGRETO-B')):
            d = ambiente[chiave]
            execute("INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
                    " VALUES (?, ?, ?, 'elettrico')",
                    (d['struttura'], d['divisione'], nome))
    entra(client, ambiente['a']['email'])
    corpo = client.get('/impianti').get_data(as_text=True)
    assert 'Cabina A' in corpo
    assert 'SEGRETO-B' not in corpo


def test_lista_impianti_partial_e_solo_il_frammento(client, app, ambiente):
    entra(client, ambiente['a']['email'])
    corpo = client.get('/impianti?partial=1').get_data(as_text=True)
    assert '<html' not in corpo.lower()


def test_creazione_impianto_con_catalogo(client, app, ambiente):
    entra(client, ambiente['a']['email'])
    with app.app_context():
        divisione = ambiente['a']['divisione']
    risposta = client.post('/impianti/nuovo', data={
        'nome': 'Cabina MT', 'tipo': 'elettrico', 'divisione_id': divisione,
        'ubicazione': 'Piano interrato',
        'catalogo': ['Verifica impianto di terra'],
    }, follow_redirects=True)
    assert risposta.status_code == 200
    with app.app_context():
        riga = query_one("SELECT * FROM impianti WHERE nome = 'Cabina MT'")
        assert riga['struttura_id'] == ambiente['a']['struttura']
        piano = query_all("SELECT * FROM impianti_scadenze WHERE impianto_id = ?",
                          (riga['id'],))
        assert len(piano) == 1 and piano[0]['periodicita_mesi'] == 24


def test_tipo_custom_solo_con_tipo_altro(client, app, ambiente):
    entra(client, ambiente['a']['email'])
    with app.app_context():
        divisione = ambiente['a']['divisione']
    client.post('/impianti/nuovo', data={
        'nome': 'Fotovoltaico', 'tipo': 'elettrico', 'divisione_id': divisione,
        'tipo_custom': 'Solare'}, follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT tipo_custom FROM impianti"
                         " WHERE nome = 'Fotovoltaico'")['tipo_custom'] is None


def test_dettaglio_impianto_altrui_non_raggiungibile(client, app, ambiente):
    with app.app_context():
        b = ambiente['b']
        impianto_b = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'SEGRETO-B', 'idraulico')",
            (b['struttura'], b['divisione'])).lastrowid
    entra(client, ambiente['a']['email'])
    corpo = client.get(f'/impianti/{impianto_b}',
                       follow_redirects=True).get_data(as_text=True)
    assert 'SEGRETO-B' not in corpo


def _crea_impianto(ambiente, chiave='a', nome='Cabina'):
    d = ambiente[chiave]
    return execute("INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
                   " VALUES (?, ?, ?, 'elettrico')",
                   (d['struttura'], d['divisione'], nome)).lastrowid


def test_dismissione_non_cancella(client, app, ambiente):
    with app.app_context():
        impianto = _crea_impianto(ambiente)
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto}/dismetti', follow_redirects=True)
    with app.app_context():
        riga = query_one("SELECT * FROM impianti WHERE id = ?", (impianto,))
        assert riga is not None and riga['stato'] == 'dismesso'


def test_componente_su_impianto_altrui_rifiutato(client, app, ambiente):
    with app.app_context():
        impianto_b = _crea_impianto(ambiente, 'b', 'Impianto B')
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto_b}/componenti',
                data={'descrizione': 'Intruso'}, follow_redirects=True)
    with app.app_context():
        assert query_all("SELECT 1 FROM impianti_componenti"
                         " WHERE impianto_id = ?", (impianto_b,)) == []


def test_componente_aggiunto_e_rimosso(client, app, ambiente):
    with app.app_context():
        impianto = _crea_impianto(ambiente)
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto}/componenti', data={
        'descrizione': 'Quadro generale', 'marca': 'ABB'},
        follow_redirects=True)
    with app.app_context():
        comp = query_one("SELECT * FROM impianti_componenti WHERE impianto_id = ?",
                         (impianto,))
        assert comp['descrizione'] == 'Quadro generale'
    client.post(f'/impianti/{impianto}/componenti/{comp["id"]}/elimina',
                follow_redirects=True)
    with app.app_context():
        assert query_all("SELECT 1 FROM impianti_componenti WHERE id = ?",
                         (comp['id'],)) == []


def test_documento_caricato_con_emittente(client, app, ambiente):
    with app.app_context():
        impianto = _crea_impianto(ambiente, nome='Cabina doc')
    entra(client, ambiente['a']['email'])
    risposta = client.post(f'/impianti/{impianto}/documenti', data={
        'tipo': 'dichiarazione_conformita',
        'descrizione': 'DiCo quadro generale',
        'data_documento': '2020-05-12',
        'emittente_ragione_sociale': 'Elettro Srl',
        'emittente_email': 'info@elettro.it',
        'documento': (io.BytesIO(b'%PDF-1.4 finto'), 'dico.pdf'),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert risposta.status_code == 200
    with app.app_context():
        doc = query_one("SELECT * FROM impianti_documenti WHERE impianto_id = ?",
                        (impianto,))
        assert doc['tipo'] == 'dichiarazione_conformita'
        assert doc['emittente_ragione_sociale'] == 'Elettro Srl'
        assert doc['filepath'].startswith('strutture/')
        assert doc['filesize'] > 0


def test_documento_estensione_non_ammessa_rifiutata(client, app, ambiente):
    with app.app_context():
        impianto = _crea_impianto(ambiente, nome='Cabina exe')
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto}/documenti', data={
        'tipo': 'altro',
        'documento': (io.BytesIO(b'MZ'), 'virus.exe'),
    }, content_type='multipart/form-data', follow_redirects=True)
    with app.app_context():
        assert query_all("SELECT 1 FROM impianti_documenti"
                         " WHERE impianto_id = ?", (impianto,)) == []


def test_documento_altrui_non_scaricabile(client, app, ambiente):
    with app.app_context():
        impianto_b = _crea_impianto(ambiente, 'b', 'Cabina B')
        doc_b = execute(
            "INSERT INTO impianti_documenti (impianto_id, tipo, filename,"
            " filepath) VALUES (?, 'progetto', 'segreto.pdf', 'x/segreto.pdf')",
            (impianto_b,)).lastrowid
    entra(client, ambiente['a']['email'])
    risposta = client.get(f'/impianti/documenti/{doc_b}')
    assert risposta.status_code in (302, 403, 404)


def test_scadenza_creata_a_mano(client, app, ambiente):
    with app.app_context():
        impianto = _crea_impianto(ambiente, nome='Cabina piano')
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto}/piano/nuova', data={
        'nome': 'Termografia quadri', 'periodicita_mesi': '12',
        'prossima_scadenza': '2027-03-01', 'giorni_anticipo': '45',
        'email_extra': 'perito@test.it', 'avvisa_manutentore': '1',
    }, follow_redirects=True)
    with app.app_context():
        riga = query_one("SELECT * FROM impianti_scadenze WHERE impianto_id = ?",
                         (impianto,))
        assert riga['nome'] == 'Termografia quadri'
        assert riga['giorni_anticipo'] == 45
        assert riga['email_extra'] == 'perito@test.it'
        assert riga['avvisa_manutentore'] == 1


def test_scadenza_una_tantum_senza_periodicita(client, app, ambiente):
    with app.app_context():
        impianto = _crea_impianto(ambiente, nome='Cabina una tantum')
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto}/piano/nuova', data={
        'nome': 'Collaudo iniziale', 'periodicita_mesi': '',
        'prossima_scadenza': '2026-09-01'}, follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT periodicita_mesi FROM impianti_scadenze"
                         " WHERE impianto_id = ?",
                         (impianto,))['periodicita_mesi'] is None


def test_scadenza_sospesa_esce_dalla_vista(client, app, ambiente):
    with app.app_context():
        impianto, scad = _impianto_con_piano(ambiente, scadenza='2026-09-01')
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/piano/{scad}/sospendi', follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT attiva FROM impianti_scadenze WHERE id = ?",
                         (scad,))['attiva'] == 0
        assert query_all("SELECT 1 FROM prossime_scadenze_impianti"
                         " WHERE scadenza_id = ?", (scad,)) == []


def test_catalogo_differito_offre_solo_le_voci_mancanti(client, app, ambiente):
    with app.app_context():
        a = ambiente['a']
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Antincendio B1', 'antincendio')",
            (a['struttura'], a['divisione'])).lastrowid
        execute("INSERT INTO impianti_scadenze (impianto_id, nome,"
                " periodicita_mesi, prossima_scadenza)"
                " VALUES (?, 'Controllo estintori', 6, '2026-09-01')", (impianto,))
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto}/piano/catalogo', data={
        'catalogo': ['Controllo idranti', 'Controllo estintori']},
        follow_redirects=True)
    with app.app_context():
        nomi = [r['nome'] for r in query_all(
            "SELECT nome FROM impianti_scadenze WHERE impianto_id = ?"
            " ORDER BY nome", (impianto,))]
        assert nomi == ['Controllo estintori', 'Controllo idranti']


def test_scadenza_con_componente_altrui_rifiutata(client, app, ambiente):
    with app.app_context():
        impianto_a = _crea_impianto(ambiente, nome='Cabina A')
        impianto_b = _crea_impianto(ambiente, 'b', 'Cabina B')
        componente_b = execute(
            "INSERT INTO impianti_componenti (impianto_id, descrizione)"
            " VALUES (?, 'Quadro B')", (impianto_b,)).lastrowid
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto_a}/piano/nuova', data={
        'nome': 'Termografia quadri', 'periodicita_mesi': '12',
        'prossima_scadenza': '2027-03-01', 'componente_id': str(componente_b),
    }, follow_redirects=True)
    with app.app_context():
        assert query_all(
            "SELECT 1 FROM impianti_scadenze"
            " WHERE impianto_id = ? AND componente_id = ?",
            (impianto_a, componente_b)) == []


def test_intervento_da_rotta_sposta_la_scadenza(client, app, ambiente):
    with app.app_context():
        impianto, scad = _impianto_con_piano(ambiente, periodicita=12,
                                             scadenza='2026-02-01')
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto}/interventi/nuovo', data={
        'scadenza_id': scad, 'tipo': 'verifica',
        'data_intervento': '2026-01-20', 'esito': 'positivo',
        'descrizione': 'Verifica eseguita',
        'verbale': (io.BytesIO(b'%PDF-1.4 verbale'), 'verbale.pdf'),
    }, content_type='multipart/form-data', follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT prossima_scadenza FROM impianti_scadenze"
                         " WHERE id = ?", (scad,))['prossima_scadenza'] == '2027-01-20'
        intervento = query_one("SELECT * FROM impianti_interventi"
                               " WHERE impianto_id = ?", (impianto,))
        assert intervento['verbale_path'].startswith('strutture/')


def test_intervento_su_impianto_altrui_rifiutato(client, app, ambiente):
    with app.app_context():
        impianto_b = _crea_impianto(ambiente, 'b', 'Cabina B int')
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto_b}/interventi/nuovo', data={
        'tipo': 'ordinaria', 'data_intervento': '2026-01-20'},
        follow_redirects=True)
    with app.app_context():
        assert query_all("SELECT 1 FROM impianti_interventi"
                         " WHERE impianto_id = ?", (impianto_b,)) == []


def test_manutentore_creato_nella_struttura_giusta(client, app, ambiente):
    entra(client, ambiente['a']['email'])
    client.post('/impianti/manutentori/nuovo', data={
        'ragione_sociale': 'Termo Service Srl', 'email': 'info@termo.it',
        'telefono': '0300000'}, follow_redirects=True)
    with app.app_context():
        riga = query_one("SELECT * FROM manutentori"
                         " WHERE ragione_sociale = 'Termo Service Srl'")
        assert riga['struttura_id'] == ambiente['a']['struttura']


def test_elenco_manutentori_isolato(client, app, ambiente):
    with app.app_context():
        execute("INSERT INTO manutentori (struttura_id, ragione_sociale)"
                " VALUES (?, 'DITTA-SEGRETA-B')", (ambiente['b']['struttura'],))
    entra(client, ambiente['a']['email'])
    corpo = client.get('/impianti/manutentori').get_data(as_text=True)
    assert 'DITTA-SEGRETA-B' not in corpo


def test_manutentore_eliminato_non_cancella_gli_impianti(client, app, ambiente):
    """ON DELETE SET NULL: l'impianto resta, senza manutentore."""
    with app.app_context():
        a = ambiente['a']
        mid = execute("INSERT INTO manutentori (struttura_id, ragione_sociale)"
                      " VALUES (?, 'Elimina Srl')", (a['struttura'],)).lastrowid
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo,"
            " manutentore_id) VALUES (?, ?, 'Cabina M', 'elettrico', ?)",
            (a['struttura'], a['divisione'], mid)).lastrowid
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/manutentori/{mid}/elimina', follow_redirects=True)
    with app.app_context():
        riga = query_one("SELECT * FROM impianti WHERE id = ?", (impianto,))
        assert riga is not None and riga['manutentore_id'] is None



def test_scadenzario_mostra_entrambe_le_origini(client, app, ambiente):
    """URL reale: /scadenzario, non /manutenzioni/scadenzario."""
    with app.app_context():
        a = ambiente['a']
        apparecchio = execute(
            "INSERT INTO apparecchi (struttura_id, divisione_id, marca, modello,"
            " matricola, descrizione) VALUES (?, ?, 'ACME', 'X1', 'MAT-APP',"
            " 'Elettrobisturi')", (a['struttura'], a['divisione'])).lastrowid
        execute("INSERT INTO manutenzioni (apparecchio_id, tipo,"
                " data_intervento, prossima_scadenza)"
                " VALUES (?, 'preventiva', date('now', '-30 days'),"
                " date('now', '+5 days'))", (apparecchio,))
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Cabina scadenzario', 'elettrico')",
            (a['struttura'], a['divisione'])).lastrowid
        execute("INSERT INTO impianti_scadenze (impianto_id, nome,"
                " periodicita_mesi, prossima_scadenza)"
                " VALUES (?, 'Verifica di terra', 24, date('now', '+3 days'))",
                (impianto,))

    entra(client, ambiente['a']['email'])
    tutto = client.get('/scadenzario').get_data(as_text=True)
    assert 'MAT-APP' in tutto or 'Elettrobisturi' in tutto
    assert 'Cabina scadenzario' in tutto

    solo_impianti = client.get('/scadenzario?origine=impianti').get_data(as_text=True)
    assert 'Cabina scadenzario' in solo_impianti
    assert 'MAT-APP' not in solo_impianti

    solo_apparecchi = client.get('/scadenzario?origine=apparecchi').get_data(as_text=True)
    assert 'Cabina scadenzario' not in solo_apparecchi


def test_scadenzario_non_mostra_impianti_di_altra_struttura(client, app, ambiente):
    with app.app_context():
        b = ambiente['b']
        impianto_b = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'SEGRETO-B-SCAD', 'elettrico')",
            (b['struttura'], b['divisione'])).lastrowid
        execute("INSERT INTO impianti_scadenze (impianto_id, nome,"
                " periodicita_mesi, prossima_scadenza)"
                " VALUES (?, 'Verifica', 24, date('now', '+2 days'))",
                (impianto_b,))
    entra(client, ambiente['a']['email'])
    assert 'SEGRETO-B-SCAD' not in client.get('/scadenzario').get_data(as_text=True)


def test_badge_conta_anche_gli_impianti(client, app, ambiente):
    with app.app_context():
        a = ambiente['a']
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Cabina badge', 'elettrico')",
            (a['struttura'], a['divisione'])).lastrowid
        execute("INSERT INTO impianti_scadenze (impianto_id, nome,"
                " periodicita_mesi, prossima_scadenza)"
                " VALUES (?, 'Verifica', 24, date('now', '-1 days'))",
                (impianto,))
    entra(client, ambiente['a']['email'])
    with client:
        client.get('/')
        from flask import g
        assert g.scadenze_alert_count >= 1


def test_scadenzario_summary_sopravvive_al_filtro_priorita(client, app, ambiente):
    """Le card di riepilogo devono contare TUTTE le priorità anche quando la
    lista è filtrata su una sola: altrimenti l'utente perde la via d'uscita
    dal filtro (bug: summary calcolato sulla lista già filtrata)."""
    with app.app_context():
        a = ambiente['a']
        apparecchio = execute(
            "INSERT INTO apparecchi (struttura_id, divisione_id, marca, modello,"
            " matricola, descrizione) VALUES (?, ?, 'ACME', 'X2', 'MAT-SUM',"
            " 'Elettrobisturi')", (a['struttura'], a['divisione'])).lastrowid
        execute("INSERT INTO manutenzioni (apparecchio_id, tipo,"
                " data_intervento, prossima_scadenza)"
                " VALUES (?, 'preventiva', date('now', '-30 days'),"
                " date('now', '-1 days'))", (apparecchio,))
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Cabina summary', 'elettrico')",
            (a['struttura'], a['divisione'])).lastrowid
        execute("INSERT INTO impianti_scadenze (impianto_id, nome,"
                " periodicita_mesi, prossima_scadenza)"
                " VALUES (?, 'Verifica di terra', 24, date('now', '+60 days'))",
                (impianto,))

    entra(client, ambiente['a']['email'])
    filtrato = client.get('/scadenzario?priorita=scaduto').get_data(as_text=True)

    # La riga scaduta (apparecchio) deve comparire nella tabella filtrata...
    assert 'MAT-SUM' in filtrato
    # ...ma la card "OK" deve comunque contare l'impianto a +60gg, non azzerarsi.
    match = re.search(r'text-success">\s*(\d+)\s*</div>\s*<small class="text-muted">OK', filtrato)
    assert match is not None
    assert int(match.group(1)) >= 1


def test_catalogo_non_ripropone_una_voce_sospesa(client, app, ambiente):
    """Il catalogo mostra solo cio' che piano_catalogo() accetterebbe.

    piano_catalogo() scarta i nomi gia' presenti senza guardare 'attiva':
    finche' il dettaglio calcolava le voci mancanti sul solo piano attivo,
    una voce sospesa tornava fra i checkbox e il POST non creava nulla.
    """
    a = ambiente['a']
    with app.app_context():
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Antincendio sospeso', 'antincendio')",
            (a['struttura'], a['divisione'])).lastrowid
        execute("INSERT INTO impianti_scadenze (impianto_id, nome,"
                " periodicita_mesi, prossima_scadenza, attiva)"
                " VALUES (?, 'Controllo estintori', 6, '2026-09-01', 0)",
                (impianto,))
    entra(client, a['email'])
    html = client.get(f'/impianti/{impianto}').data.decode('utf-8')
    assert 'value="Controllo estintori"' not in html
    # Controprova: le altre voci del catalogo restano offerte.
    assert 'name="catalogo"' in html


def test_libretto_pdf_generato(app, ambiente, tmp_path):
    from export_service import genera_libretto_impianto
    with app.app_context():
        impianto, scad = _impianto_con_piano(ambiente, scadenza='2027-01-10')
        execute("INSERT INTO impianti_componenti (impianto_id, descrizione)"
                " VALUES (?, 'Quadro generale')", (impianto,))
        execute("INSERT INTO impianti_interventi (impianto_id, tipo,"
                " data_intervento, esito) VALUES (?, 'verifica', '2025-01-10',"
                " 'positivo')", (impianto,))
        percorso = str(tmp_path / 'libretto.pdf')
        genera_libretto_impianto(impianto, percorso)
    import os
    assert os.path.exists(percorso) and os.path.getsize(percorso) > 500
    with open(percorso, 'rb') as f:
        assert f.read(4) == b'%PDF'


def test_libretto_con_piu_voci_per_sezione(app, ambiente, tmp_path):
    """Due voci per sezione, non una.

    Con una voce sola il libretto passava anche quando multi_cell() lasciava
    il cursore a destra: e' la seconda riga che sfonda il margine e solleva
    FPDFException. Il caso reale ha sempre piu' di una voce.
    """
    from export_service import genera_libretto_impianto
    with app.app_context():
        impianto, _ = _impianto_con_piano(ambiente, scadenza='2027-01-10')
        execute("INSERT INTO impianti_scadenze (impianto_id, nome,"
                " periodicita_mesi, prossima_scadenza)"
                " VALUES (?, 'Termografia quadri', 12, '2027-06-01')",
                (impianto,))
        for descrizione in ('Quadro generale', 'Quadro di piano'):
            execute("INSERT INTO impianti_componenti (impianto_id, descrizione)"
                    " VALUES (?, ?)", (impianto, descrizione))
        for data in ('2025-01-10', '2025-07-10'):
            execute("INSERT INTO impianti_interventi (impianto_id, tipo,"
                    " data_intervento, esito) VALUES (?, 'verifica', ?,"
                    " 'positivo')", (impianto, data))
        for nome in ('progetto.pdf', 'collaudo.pdf'):
            execute("INSERT INTO impianti_documenti (impianto_id, tipo,"
                    " descrizione, filename, filepath)"
                    " VALUES (?, 'progetto', ?, ?, ?)",
                    (impianto, nome, nome, f'impianti/{nome}'))
        percorso = str(tmp_path / 'libretto-multi.pdf')
        genera_libretto_impianto(impianto, percorso)
    import os
    assert os.path.exists(percorso) and os.path.getsize(percorso) > 500


def test_libretto_di_altra_struttura_non_scaricabile(client, app, ambiente):
    with app.app_context():
        impianto_b = _crea_impianto(ambiente, 'b', 'Cabina B libretto')
    entra(client, ambiente['a']['email'])
    assert client.get(f'/impianti/{impianto_b}/libretto.pdf').status_code == 302


def _scadenza_fra(ambiente, giorni, anticipo=30, extra=None, manutentore=None):
    a = ambiente['a']
    impianto = execute(
        "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo,"
        " manutentore_id) VALUES (?, ?, ?, 'elettrico', ?)",
        (a['struttura'], a['divisione'], f'Cabina {giorni}', manutentore)).lastrowid
    return execute(
        "INSERT INTO impianti_scadenze (impianto_id, nome, periodicita_mesi,"
        " prossima_scadenza, giorni_anticipo, email_extra)"
        " VALUES (?, 'Verifica di terra', 24, date('now', ?), ?, ?)",
        (impianto, f'{giorni} days', anticipo, extra)).lastrowid


def test_soglie_avvisi(app, ambiente):
    """Solo la soglia più grave raggiunta, e mai prima dell'anticipo."""
    from impianti_service import avvisi_da_inviare
    with app.app_context():
        sid = ambiente['a']['struttura']
        lontana = _scadenza_fra(ambiente, 60)
        anticipo = _scadenza_fra(ambiente, 20)
        imminente = _scadenza_fra(ambiente, 3)
        scaduta = _scadenza_fra(ambiente, -45)

        per_id = {a['scadenza_id']: a for a in avvisi_da_inviare(sid)}
        assert lontana not in per_id
        assert per_id[anticipo]['soglia'] == 'anticipo'
        assert per_id[imminente]['soglia'] == 'imminente'
        assert per_id[scaduta]['soglia'] == 'sollecito_1'


def test_avviso_non_si_ripete(app, ambiente):
    from impianti_service import avvisi_da_inviare, registra_avviso
    with app.app_context():
        sid = ambiente['a']['struttura']
        scad = _scadenza_fra(ambiente, 20)
        avviso = [a for a in avvisi_da_inviare(sid)
                  if a['scadenza_id'] == scad][0]
        registra_avviso(scad, avviso['soglia'], avviso['prossima_scadenza'],
                        ['x@test.it'])
        assert [a for a in avvisi_da_inviare(sid)
                if a['scadenza_id'] == scad] == []


def test_avviso_riparte_dopo_lo_spostamento_della_scadenza(app, ambiente):
    """scadenza_target sta nella chiave: il ciclo successivo avvisa di nuovo."""
    from impianti_service import (avvisi_da_inviare, registra_avviso,
                                  registra_intervento)
    with app.app_context():
        sid = ambiente['a']['struttura']
        scad = _scadenza_fra(ambiente, 20)
        riga = query_one("SELECT * FROM impianti_scadenze WHERE id = ?", (scad,))
        registra_avviso(scad, 'anticipo', riga['prossima_scadenza'], ['x@test.it'])
        # Verifica eseguita: la scadenza si sposta di 24 mesi, poi la si
        # riporta indietro per simulare il ciclo successivo. +21 e non +20:
        # deve atterrare su una data diversa da quella gia' registrata, sennò
        # la chiave di deduplica (scadenza_id, soglia, scadenza_target) resta
        # la stessa e il test non starebbe verificando nulla di diverso dal
        # caso "non si ripete".
        registra_intervento(riga['impianto_id'], {
            'scadenza_id': scad, 'tipo': 'verifica',
            'data_intervento': '2026-01-01', 'esito': 'positivo'})
        execute("UPDATE impianti_scadenze SET prossima_scadenza ="
                " date('now', '+21 days') WHERE id = ?", (scad,))
        assert [a for a in avvisi_da_inviare(sid)
                if a['scadenza_id'] == scad] != []


def test_destinatari_in_cascata(app, ambiente):
    from impianti_service import avvisi_da_inviare, destinatari
    with app.app_context():
        a = ambiente['a']
        mid = execute("INSERT INTO manutentori (struttura_id, ragione_sociale,"
                      " email) VALUES (?, 'Ditta', 'ditta@test.it')",
                      (a['struttura'],)).lastrowid
        scad = _scadenza_fra(ambiente, 10, extra='perito@test.it, ,perito@test.it',
                             manutentore=mid)
        struttura = query_one("SELECT * FROM strutture WHERE id = ?",
                              (a['struttura'],))
        avviso = [x for x in avvisi_da_inviare(a['struttura'])
                  if x['scadenza_id'] == scad][0]
        elenco = destinatari(struttura, avviso)
        assert elenco.count('perito@test.it') == 1
        assert 'responsabile.a@test.it' in elenco
        assert 'divisione.a@test.it' in elenco
        assert 'ditta@test.it' in elenco
        assert '' not in elenco


def test_destinatari_vuoto_senza_configurazione(app, ambiente):
    """destinatari() torna [] (non solleva) se la struttura non ha indicato
    ne' responsabile, ne' notifiche, ne' email di divisione."""
    from impianti_service import avvisi_da_inviare, destinatari
    with app.app_context():
        b = ambiente['b']
        execute("UPDATE strutture SET email_responsabile = NULL,"
                " email_notifiche = NULL WHERE id = ?", (b['struttura'],))
        execute("UPDATE divisioni SET email = NULL WHERE id = ?",
                (b['divisione'],))
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Cabina muta', 'elettrico')",
            (b['struttura'], b['divisione'])).lastrowid
        scad = execute(
            "INSERT INTO impianti_scadenze (impianto_id, nome,"
            " periodicita_mesi, prossima_scadenza)"
            " VALUES (?, 'Verifica', 24, date('now', '+10 days'))",
            (impianto,)).lastrowid
        struttura = query_one("SELECT * FROM strutture WHERE id = ?",
                              (b['struttura'],))
        # Selezionata per id: [0] dipende dall'ordine, che cambia se la
        # fixture cresce di altre scadenze per la struttura b.
        avviso = [x for x in avvisi_da_inviare(b['struttura'])
                  if x['scadenza_id'] == scad][0]
        assert destinatari(struttura, avviso) == []


def test_corpo_avviso_nomina_la_struttura(app, ambiente):
    """Vincolo di progetto: la posta e' unica per tutto il deployment dalla
    2.6.2, quindi ogni messaggio deve identificare la propria struttura."""
    from impianti_service import avvisi_da_inviare, corpo_avviso
    with app.app_context():
        a = ambiente['a']
        scad = _scadenza_fra(ambiente, 10)
        struttura = query_one("SELECT * FROM strutture WHERE id = ?",
                              (a['struttura'],))
        avviso = [x for x in avvisi_da_inviare(a['struttura'])
                  if x['scadenza_id'] == scad][0]
        oggetto, testo = corpo_avviso(struttura, avviso)
        assert struttura['nome'] in oggetto
        assert struttura['nome'] in testo


class _OreDiUfficio(_orig_datetime):
    """Ferma l'orologio dello scheduler dopo le 7: _send_impianti_alerts()
    esce subito prima di quell'ora, e il test non deve dipendere da quando
    viene lanciata la suite."""

    @classmethod
    def now(cls, tz=None):
        return _orig_datetime(2026, 1, 1, 9, 0, 0)


def test_impianti_alerts_un_invio_per_indirizzo(app, ambiente, monkeypatch):
    """Regressione del difetto bloccante: un invio per indirizzo, non un
    unico invio con destinatari uniti da virgola. Fallisce contro la
    versione con ', '.join(indirizzi) perche' li' invia() viene chiamata una
    sola volta con un solo argomento contenente la virgola."""
    import scheduler as scheduler_module
    monkeypatch.setattr(scheduler_module, 'datetime', _OreDiUfficio)

    chiamate = []

    def finto_invia(cfg, destinatario, messaggio):
        chiamate.append(destinatario)
        return True

    monkeypatch.setattr(scheduler_module, 'invia', finto_invia)

    with app.app_context():
        app.config['APP_CONFIG']['smtp_host'] = 'smtp.test.it'
        scad = _scadenza_fra(ambiente, 10)  # 2 destinatari di default: a['struttura'] ha responsabile e la divisione ha email

        s = scheduler_module.BackgroundScheduler(app)
        s._send_impianti_alerts()

        assert len(chiamate) == 2
        for destinatario in chiamate:
            assert ',' not in destinatario
        assert set(chiamate) == {'responsabile.a@test.it', 'divisione.a@test.it'}

        righe = query_all("SELECT * FROM impianti_avvisi_inviati"
                          " WHERE scadenza_id = ?", (scad,))
        assert len(righe) == 1
        assert righe[0]['soglia'] == 'anticipo'


def test_impianti_alerts_senza_destinatari_non_invia_e_non_registra(app, ambiente, monkeypatch):
    import scheduler as scheduler_module
    monkeypatch.setattr(scheduler_module, 'datetime', _OreDiUfficio)

    chiamate = []
    monkeypatch.setattr(scheduler_module, 'invia',
                        lambda cfg, destinatario, messaggio: chiamate.append(destinatario) or True)

    with app.app_context():
        app.config['APP_CONFIG']['smtp_host'] = 'smtp.test.it'
        b = ambiente['b']
        execute("UPDATE strutture SET email_responsabile = NULL,"
                " email_notifiche = NULL WHERE id = ?", (b['struttura'],))
        execute("UPDATE divisioni SET email = NULL WHERE id = ?",
                (b['divisione'],))
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Cabina muta', 'elettrico')",
            (b['struttura'], b['divisione'])).lastrowid
        scad = execute(
            "INSERT INTO impianti_scadenze (impianto_id, nome,"
            " periodicita_mesi, prossima_scadenza)"
            " VALUES (?, 'Verifica', 24, date('now', '+10 days'))",
            (impianto,)).lastrowid

        s = scheduler_module.BackgroundScheduler(app)
        s._send_impianti_alerts()

        assert chiamate == []
        righe = query_all("SELECT * FROM impianti_avvisi_inviati"
                          " WHERE scadenza_id = ?", (scad,))
        assert righe == []


def test_impianti_alerts_invio_fallito_non_registra(app, ambiente, monkeypatch):
    """Se invia() torna False non si scrive la riga: il giro successivo deve
    ritentare, non considerare l'avviso gia' spedito."""
    import scheduler as scheduler_module
    monkeypatch.setattr(scheduler_module, 'datetime', _OreDiUfficio)
    monkeypatch.setattr(scheduler_module, 'invia',
                        lambda cfg, destinatario, messaggio: False)

    with app.app_context():
        app.config['APP_CONFIG']['smtp_host'] = 'smtp.test.it'
        scad = _scadenza_fra(ambiente, 10)

        s = scheduler_module.BackgroundScheduler(app)
        s._send_impianti_alerts()

        righe = query_all("SELECT * FROM impianti_avvisi_inviati"
                          " WHERE scadenza_id = ?", (scad,))
        assert righe == []
