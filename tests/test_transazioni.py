"""M03: le operazioni multi-step non erano atomiche.

models.execute() faceva commit dopo ogni istruzione. Una registrazione
composta da piu' scritture - la scheda dell'apparecchio e i suoi accessori,
l'intervento e l'avanzamento del piano di manutenzione, la riga importata e
il suo stato - poteva quindi fermarsi a meta' e lasciare il database in uno
stato che nessuna schermata mostra come rotto: un intervento registrato su
una scadenza ferma risulta insieme fatto e ancora dovuto.

Questi test inchiodano il contratto di models.transazione() e i punti dove
viene usato. Il criterio e' sempre lo stesso: dopo un errore a meta'
sequenza non deve restare niente della sequenza.
"""
import os
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

from models import execute, in_transazione, query_one, transazione


def _conta_su_disco(app, sql, params=()):
    """Legge il database da una seconda connessione.

    Serve a distinguere 'scritto e committato' da 'scritto e ancora dentro
    la transazione': la connessione della richiesta vede comunque le proprie
    scritture, una connessione esterna vede solo cio' che e' stato committato.
    """
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def _struttura(nome='Clinica A', codice='CLA'):
    return execute("INSERT INTO strutture (nome, codice, attiva) VALUES (?, ?, 1)",
                   (nome, codice)).lastrowid


@pytest.fixture
def ambiente(app):
    """Una struttura con divisione, admin, impianto e una voce di piano."""
    with app.app_context():
        sid = _struttura()
        did = execute("INSERT INTO divisioni (struttura_id, nome, codice)"
                      " VALUES (?, 'Oculistica', 'OCU')", (sid,)).lastrowid
        uid = execute(
            "INSERT INTO utenti (struttura_id, nome, cognome, email,"
            " password_hash, ruolo, attivo, primo_accesso)"
            " VALUES (?, 'Admin', 'A', 'admin@a.it', ?, 'admin', 1, 0)",
            (sid, generate_password_hash('Passw0rd!'))).lastrowid
        iid = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Quadro generale', 'elettrico')",
            (sid, did)).lastrowid
        scad = execute(
            "INSERT INTO impianti_scadenze (impianto_id, nome, periodicita_mesi,"
            " prossima_scadenza) VALUES (?, 'Verifica impianto di terra', 24,"
            " '2026-01-01')", (iid,)).lastrowid
        return {'struttura': sid, 'divisione': did, 'utente': uid,
                'impianto': iid, 'scadenza': scad}


def entra(client, email='admin@a.it'):
    return client.post('/login', data={'email': email, 'password': 'Passw0rd!'},
                       follow_redirects=True)


# ---------------------------------------------------------------------------
# Il contratto di transazione()
# ---------------------------------------------------------------------------

def test_fuori_dal_blocco_execute_committa_subito(app):
    """Chi non apre una transazione non cambia comportamento."""
    with app.app_context():
        _struttura('Senza blocco', 'SB')
        assert _conta_su_disco(
            app, "SELECT COUNT(*) FROM strutture WHERE codice = 'SB'") == 1


def test_dentro_il_blocco_il_commit_e_rimandato(app):
    """Una scrittura dentro il blocco non deve essere visibile fuori finche'
    il blocco non e' finito: e' quello che rende atomica la sequenza."""
    with app.app_context():
        with transazione():
            _struttura('Dentro', 'DEN')
            assert _conta_su_disco(
                app, "SELECT COUNT(*) FROM strutture WHERE codice = 'DEN'") == 0
        assert _conta_su_disco(
            app, "SELECT COUNT(*) FROM strutture WHERE codice = 'DEN'") == 1


def test_un_errore_annulla_tutte_le_scritture_del_blocco(app):
    """Il difetto M03 in miniatura: la prima scrittura riesce, la seconda no.
    Prima della correzione la prima restava committata."""
    with app.app_context():
        with pytest.raises(RuntimeError):
            with transazione():
                _struttura('Prima', 'PRI')
                raise RuntimeError('la seconda scrittura fallisce')
        assert query_one(
            "SELECT COUNT(*) AS n FROM strutture WHERE codice = 'PRI'")['n'] == 0
        assert _conta_su_disco(
            app, "SELECT COUNT(*) FROM strutture WHERE codice = 'PRI'") == 0


def test_il_blocco_annidato_lo_decide_il_piu_esterno(app):
    """registra_intervento() apre la propria transazione anche quando la rotta
    ne ha gia' aperta una. Il blocco interno non deve committare per conto suo:
    se il chiamante fallisce dopo, deve saltare tutto."""
    with app.app_context():
        with pytest.raises(RuntimeError):
            with transazione():
                with transazione():
                    _struttura('Annidata', 'ANN')
                # uscito dal blocco interno: ancora niente su disco
                assert _conta_su_disco(
                    app, "SELECT COUNT(*) FROM strutture WHERE codice = 'ANN'") == 0
                raise RuntimeError('il chiamante fallisce dopo')
        assert _conta_su_disco(
            app, "SELECT COUNT(*) FROM strutture WHERE codice = 'ANN'") == 0


def test_lo_stato_del_blocco_viene_azzerato(app):
    """Dopo un errore il contatore deve tornare a zero, altrimenti le scritture
    successive della stessa richiesta resterebbero senza commit."""
    with app.app_context():
        assert in_transazione() is False
        with pytest.raises(RuntimeError):
            with transazione():
                assert in_transazione() is True
                raise RuntimeError('errore')
        assert in_transazione() is False
        _struttura('Dopo errore', 'DOP')
        assert _conta_su_disco(
            app, "SELECT COUNT(*) FROM strutture WHERE codice = 'DOP'") == 1


def test_il_registro_attivita_partecipa_alla_transazione(app, ambiente):
    """log_attivita() passa da execute(): la riga di registro deve sparire con
    la scrittura che documenta, non sopravviverle."""
    from models import log_attivita
    with app.app_context():
        with pytest.raises(RuntimeError):
            with transazione():
                log_attivita(ambiente['utente'], 'creazione', 'apparecchi', 1,
                             'Voce che non deve restare')
                raise RuntimeError('errore dopo il log')
        assert query_one(
            "SELECT COUNT(*) AS n FROM log_attivita WHERE dettagli LIKE ?",
            ('%non deve restare%',))['n'] == 0


# ---------------------------------------------------------------------------
# I punti di chiamata
# ---------------------------------------------------------------------------

def test_intervento_e_avanzamento_del_piano_stanno_insieme(app, ambiente, monkeypatch):
    """Se l'avanzamento della scadenza fallisce, l'intervento non deve restare:
    la verifica risulterebbe fatta mentre il piano la chiede ancora."""
    import impianti_service
    reale = impianti_service.execute

    def execute_che_fallisce_in_update(sql, params=()):
        if sql.lstrip().upper().startswith('UPDATE'):
            raise RuntimeError('avanzamento del piano fallito')
        return reale(sql, params)

    with app.app_context():
        monkeypatch.setattr(impianti_service, 'execute', execute_che_fallisce_in_update)
        with pytest.raises(RuntimeError):
            impianti_service.registra_intervento(ambiente['impianto'], {
                'scadenza_id': ambiente['scadenza'],
                'tipo': 'verifica',
                'data_intervento': '2026-08-29',
                'esito': 'positivo',
            }, utente_id=ambiente['utente'])
        monkeypatch.undo()

        assert query_one("SELECT COUNT(*) AS n FROM impianti_interventi")['n'] == 0
        assert query_one("SELECT prossima_scadenza FROM impianti_scadenze WHERE id = ?",
                         (ambiente['scadenza'],))['prossima_scadenza'] == '2026-01-01'


def test_la_rotta_intervento_annulla_tutto_se_il_registro_fallisce(app, client,
                                                                  ambiente,
                                                                  monkeypatch):
    """La rotta apre la transazione piu' esterna: un errore dopo il servizio
    deve riportare indietro anche l'intervento e la scadenza."""
    import impianti

    def log_che_fallisce(*args, **kwargs):
        raise RuntimeError('registro non scrivibile')

    entra(client)
    monkeypatch.setattr(impianti, 'log_attivita', log_che_fallisce)
    try:
        client.post('/impianti/%d/interventi/nuovo' % ambiente['impianto'], data={
            'data_intervento': '2026-08-29', 'tipo': 'verifica',
            'esito': 'positivo', 'scadenza_id': str(ambiente['scadenza']),
        })
    except RuntimeError:
        pass
    monkeypatch.undo()

    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM impianti_interventi")['n'] == 0
        assert query_one("SELECT prossima_scadenza FROM impianti_scadenze WHERE id = ?",
                         (ambiente['scadenza'],))['prossima_scadenza'] == '2026-01-01'


def test_apparecchio_e_accessori_stanno_insieme(app, client, ambiente, monkeypatch):
    """Un errore sugli accessori non deve lasciare a catalogo una scheda senza
    di essi: chi la guarda non ha modo di accorgersi che manca qualcosa."""
    import apparecchi

    def accessori_che_falliscono(*args, **kwargs):
        raise RuntimeError('accessori non salvati')

    entra(client)
    monkeypatch.setattr(apparecchi, '_save_accessori', accessori_che_falliscono)
    try:
        client.post('/apparecchi/nuovo', data={
            'matricola': 'OCU-1', 'marca': 'REXXAM', 'modello': 'OZY',
            'divisione_id': str(ambiente['divisione']), 'stato': 'funzionante',
        })
    except RuntimeError:
        pass
    monkeypatch.undo()

    with app.app_context():
        assert query_one(
            "SELECT COUNT(*) AS n FROM apparecchi WHERE matricola = 'OCU-1'")['n'] == 0


def test_gli_allegati_della_riga_fallita_vengono_rimossi(app, tmp_path):
    """Il rollback annulla le righe, non i file: senza la pulizia esplicita la
    cartella dei verbali si riempie di documenti che nessun record cita."""
    from import_bp import _rimuovi_file_copiati

    copiato = tmp_path / 'verbale.pdf'
    copiato.write_bytes(b'%PDF-1.4')
    mai_copiato = tmp_path / 'assente.pdf'

    with app.app_context():
        _rimuovi_file_copiati([str(copiato), str(mai_copiato)])

    assert not os.path.exists(str(copiato))
