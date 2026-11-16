"""M06, B01, M05: tre difetti residui dell'audit del 28/08/2026.

M06 — con debug=True il reloader di Werkzeug tiene vivi due processi e
init_scheduler() viene chiamato prima di app.run(): lo scheduler partiva in
entrambi, quindi ogni ciclo (posta, backup, avvisi di scadenza) girava due
volte. Due verbali importati dallo stesso messaggio, due email allo stesso
destinatario.

B01 — ogni chiamata dell'API riscriveva api_tokens.ultimo_utilizzo. SQLite
serializza gli scrittori: una GET di sola lettura apriva una transazione in
scrittura e metteva in coda l'importazione, lo scheduler e gli utenti del
gestionale. Ora si riscrive al massimo ogni cinque minuti.

M05 — le rotte allegati di impianti.py componevano il percorso unendo
UPLOADS_PATH e il valore del database senza verificare dove finisse, mentre
apparecchi, manutenzioni e verifiche lo fanno da tempo. Un filepath che
risale portava send_file e os.remove fuori dalla cartella degli allegati.
"""
import hashlib
import os

import pytest
from werkzeug.security import generate_password_hash

from models import execute, query_one


# ---------------------------------------------------------------------------
# M06 — un solo scheduler per installazione
# ---------------------------------------------------------------------------

def test_senza_debug_lo_scheduler_parte():
    """In produzione WERKZEUG_RUN_MAIN non c'e': un controllo sulla sola
    variabile d'ambiente lascerebbe l'installazione senza scheduler."""
    from scheduler import deve_avviare_scheduler
    assert deve_avviare_scheduler(debug=False, env={}) is True


def test_senza_debug_la_variabile_del_reloader_e_ininfluente():
    from scheduler import deve_avviare_scheduler
    assert deve_avviare_scheduler(debug=False, env={'WERKZEUG_RUN_MAIN': 'true'}) is True


def test_in_debug_il_processo_padre_non_avvia_lo_scheduler():
    """Il padre resta vivo a sorvegliare il figlio: se avviasse anche lui lo
    scheduler, ogni ciclo partirebbe due volte."""
    from scheduler import deve_avviare_scheduler
    assert deve_avviare_scheduler(debug=True, env={}) is False


def test_in_debug_lo_scheduler_va_nel_processo_ricaricato():
    from scheduler import deve_avviare_scheduler
    assert deve_avviare_scheduler(debug=True, env={'WERKZEUG_RUN_MAIN': 'true'}) is True


@pytest.mark.parametrize('valore', ['false', '', '1', 'True'])
def test_solo_il_valore_true_conta(valore):
    """Werkzeug scrive esattamente 'true'. Qualunque altro valore viene da
    qualcun altro e non dice che siamo nel processo ricaricato."""
    from scheduler import deve_avviare_scheduler
    assert deve_avviare_scheduler(debug=True, env={'WERKZEUG_RUN_MAIN': valore}) is False


def test_senza_env_esplicito_legge_os_environ(monkeypatch):
    from scheduler import deve_avviare_scheduler
    monkeypatch.delenv('WERKZEUG_RUN_MAIN', raising=False)
    assert deve_avviare_scheduler(debug=True) is False
    monkeypatch.setenv('WERKZEUG_RUN_MAIN', 'true')
    assert deve_avviare_scheduler(debug=True) is True


# ---------------------------------------------------------------------------
# B01 — ultimo_utilizzo non si riscrive a ogni chiamata
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402


def _timestamp(delta):
    """Timestamp nello stesso formato di CURRENT_TIMESTAMP di SQLite (UTC)."""
    return (datetime.now(timezone.utc) + delta).strftime('%Y-%m-%d %H:%M:%S')


@pytest.mark.parametrize('valore', [None, '', 0])
def test_un_token_mai_usato_si_aggiorna(valore):
    from api_bp import _ultimo_utilizzo_da_aggiornare
    assert _ultimo_utilizzo_da_aggiornare(valore) is True


def test_un_utilizzo_vecchio_si_aggiorna():
    from api_bp import _ultimo_utilizzo_da_aggiornare
    assert _ultimo_utilizzo_da_aggiornare(_timestamp(timedelta(minutes=-6))) is True


def test_un_utilizzo_recente_non_si_aggiorna():
    """E' il caso normale: il client che interroga l'API ogni pochi secondi."""
    from api_bp import _ultimo_utilizzo_da_aggiornare
    assert _ultimo_utilizzo_da_aggiornare(_timestamp(timedelta(seconds=-30))) is False


def test_al_limite_dei_cinque_minuti_si_aggiorna():
    from api_bp import _ultimo_utilizzo_da_aggiornare, INTERVALLO_ULTIMO_UTILIZZO
    adesso = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
    esatto = (adesso - INTERVALLO_ULTIMO_UTILIZZO).strftime('%Y-%m-%d %H:%M:%S')
    assert _ultimo_utilizzo_da_aggiornare(esatto, adesso=adesso) is True


def test_i_millisecondi_non_confondono_il_confronto():
    """Un ripristino o un import possono lasciare il timestamp con la parte
    frazionaria: il confronto guarda i primi 19 caratteri."""
    from api_bp import _ultimo_utilizzo_da_aggiornare
    assert _ultimo_utilizzo_da_aggiornare(_timestamp(timedelta(seconds=-30)) + '.123') is False


@pytest.fixture
def token_api(app):
    """Una struttura con un token di lettura. Restituisce il token in chiaro."""
    with app.app_context():
        sid = execute(
            "INSERT INTO strutture (nome, codice, attiva) VALUES ('Clinica T', 'CLT', 1)"
        ).lastrowid
        execute(
            "INSERT INTO utenti (struttura_id, nome, cognome, email, password_hash,"
            " ruolo, attivo) VALUES (?, 'Admin', 'T', 'admin.t@test.it', ?, 'admin', 1)",
            (sid, generate_password_hash('Passw0rd!')))
        segreto = 'segreto-di-prova'
        tid = execute(
            "INSERT INTO api_tokens (struttura_id, nome, token_hash, scopes, attivo)"
            " VALUES (?, 'Integrazione', ?, 'read', 1)",
            (sid, hashlib.sha256(segreto.encode()).hexdigest())).lastrowid
    return {'segreto': segreto, 'id': tid, 'struttura': sid}


def _chiama(client, token_api):
    return client.get('/api/v1/apparecchi',
                      headers={'Authorization': 'Bearer ' + token_api['segreto']})


def _ultimo(app, token_api):
    with app.app_context():
        return query_one("SELECT ultimo_utilizzo FROM api_tokens WHERE id = ?",
                         (token_api['id'],))['ultimo_utilizzo']


def test_la_prima_chiamata_registra_l_utilizzo(client, app, token_api):
    assert _chiama(client, token_api).status_code == 200
    assert _ultimo(app, token_api)


def test_la_chiamata_successiva_non_scrive_piu(client, app, token_api):
    """Il difetto: due GET consecutive erano due transazioni in scrittura."""
    _chiama(client, token_api)
    prima = _ultimo(app, token_api)
    with app.app_context():
        execute("UPDATE api_tokens SET ultimo_utilizzo = ? WHERE id = ?",
                (_timestamp(timedelta(seconds=-10)), token_api['id']))
        atteso = _ultimo(app, token_api)
    _chiama(client, token_api)
    assert _ultimo(app, token_api) == atteso
    assert prima  # la prima chiamata aveva comunque scritto


def test_dopo_l_intervallo_l_utilizzo_torna_aggiornato(client, app, token_api):
    """Il campo serve a riconoscere i token abbandonati: deve restare vero."""
    with app.app_context():
        execute("UPDATE api_tokens SET ultimo_utilizzo = ? WHERE id = ?",
                (_timestamp(timedelta(hours=-2)), token_api['id']))
        vecchio = _ultimo(app, token_api)
    _chiama(client, token_api)
    assert _ultimo(app, token_api) != vecchio


# ---------------------------------------------------------------------------
# M05 — gli allegati degli impianti restano dentro uploads
# ---------------------------------------------------------------------------

@pytest.fixture
def impianto_con_allegati(app, tmp_path):
    """Un impianto della struttura dell'utente, con due documenti: uno
    regolare e uno il cui filepath esce da uploads."""
    with app.app_context():
        sid = execute(
            "INSERT INTO strutture (nome, codice, attiva) VALUES ('Clinica M', 'CLM', 1)"
        ).lastrowid
        did = execute(
            "INSERT INTO divisioni (struttura_id, nome, codice) VALUES (?, 'Div M', 'DVM')",
            (sid,)).lastrowid
        execute(
            "INSERT INTO utenti (struttura_id, nome, cognome, email, password_hash,"
            " ruolo, attivo) VALUES (?, 'Admin', 'M', 'admin.m@test.it', ?, 'admin', 1)",
            (sid, generate_password_hash('Passw0rd!')))
        imp = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Cabina M', 'elettrico')", (sid, did)).lastrowid

        uploads = app.config['UPLOADS_PATH']
        os.makedirs(os.path.join(uploads, 'impianti'), exist_ok=True)
        with open(os.path.join(uploads, 'impianti', 'buono.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4 buono')
        buono = execute(
            "INSERT INTO impianti_documenti (impianto_id, tipo, filename, filepath)"
            " VALUES (?, 'certificato', 'buono.pdf', 'impianti/buono.pdf')",
            (imp,)).lastrowid

        # Il bersaglio sta fuori da uploads: e' il file che il difetto
        # permetteva di scaricare e di cancellare.
        fuori = tmp_path / 'segreto.txt'
        fuori.write_text('dati di un altro tenant', encoding='utf-8')
        relativo = os.path.relpath(str(fuori), uploads)
        cattivo = execute(
            "INSERT INTO impianti_documenti (impianto_id, tipo, filename, filepath)"
            " VALUES (?, 'altro', 'segreto.txt', ?)", (imp, relativo)).lastrowid
        intervento = execute(
            "INSERT INTO impianti_interventi (impianto_id, tipo, data_intervento,"
            " verbale_path) VALUES (?, 'ordinaria', '2026-08-01', ?)",
            (imp, relativo)).lastrowid
    return {'impianto': imp, 'buono': buono, 'cattivo': cattivo,
            'intervento': intervento, 'fuori': fuori,
            'email': 'admin.m@test.it'}


def entra(client, email):
    return client.post('/login', data={'email': email, 'password': 'Passw0rd!'},
                       follow_redirects=True)


def test_il_percorso_di_un_allegato_regolare_si_risolve(app):
    from impianti import _percorso_allegato
    with app.test_request_context():
        risolto = _percorso_allegato('impianti/x.pdf')
    assert risolto
    assert risolto.startswith(os.path.realpath(app.config['UPLOADS_PATH']))


@pytest.mark.parametrize('relativo', [
    '../fuori.txt',
    'impianti/../../fuori.txt',
    os.path.join(os.path.abspath(os.sep), 'Windows', 'win.ini'),
    '',
    None,
])
def test_un_percorso_che_esce_da_uploads_viene_rifiutato(app, relativo):
    from impianti import _percorso_allegato
    with app.test_request_context():
        assert _percorso_allegato(relativo) is None


def test_il_documento_regolare_si_scarica(client, impianto_con_allegati):
    entra(client, impianto_con_allegati['email'])
    risposta = client.get('/impianti/documenti/%d' % impianto_con_allegati['buono'])
    assert risposta.status_code == 200
    assert b'buono' in risposta.data


def test_il_documento_fuori_da_uploads_non_si_scarica(client, impianto_con_allegati):
    entra(client, impianto_con_allegati['email'])
    risposta = client.get('/impianti/documenti/%d' % impianto_con_allegati['cattivo'],
                          follow_redirects=True)
    assert 'dati di un altro tenant' not in risposta.get_data(as_text=True)
    assert 'File non presente sul server' in risposta.get_data(as_text=True)


def test_eliminare_il_documento_non_cancella_il_file_fuori(client, app, impianto_con_allegati):
    """La riga sparisce comunque, come per un file gia' mancante: quello che
    non deve succedere e' la remove fuori dalla cartella degli allegati."""
    entra(client, impianto_con_allegati['email'])
    client.post('/impianti/documenti/%d/elimina' % impianto_con_allegati['cattivo'],
                follow_redirects=True)
    assert impianto_con_allegati['fuori'].exists()
    with app.app_context():
        assert query_one("SELECT id FROM impianti_documenti WHERE id = ?",
                         (impianto_con_allegati['cattivo'],)) is None


def test_eliminare_un_documento_regolare_cancella_il_file(client, app, impianto_con_allegati):
    entra(client, impianto_con_allegati['email'])
    percorso = os.path.join(app.config['UPLOADS_PATH'], 'impianti', 'buono.pdf')
    client.post('/impianti/documenti/%d/elimina' % impianto_con_allegati['buono'],
                follow_redirects=True)
    assert not os.path.exists(percorso)


def test_il_verbale_fuori_da_uploads_non_si_scarica(client, impianto_con_allegati):
    entra(client, impianto_con_allegati['email'])
    risposta = client.get('/impianti/interventi/%d/verbale' % impianto_con_allegati['intervento'],
                          follow_redirects=True)
    assert 'dati di un altro tenant' not in risposta.get_data(as_text=True)
    assert 'Verbale non presente sul server' in risposta.get_data(as_text=True)
