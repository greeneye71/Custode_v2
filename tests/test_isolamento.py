"""Nessuna rotta deve servire dati a chi non ha uno scope.

Il difetto che questi test inchiodano non e' un errore di calcolo: e' un ramo
che restituisce "nessun filtro" invece di "nessun dato". Non si vede leggendo
una pagina che funziona, si vede solo chiedendola da un account senza scope.
"""
import io

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader
from werkzeug.security import generate_password_hash


def _testo_html(dati):
    """Le rotte HTML mostrano il dato cosi' com'e' nel markup: basta decodificare."""
    return dati.decode('utf-8', errors='replace')


def _testo_excel(dati):
    """Un xlsx e' uno zip: la stringa cercata non compare mai nei byte grezzi.
    Bisogna aprire il foglio e leggere i valori delle celle davvero."""
    wb = load_workbook(io.BytesIO(dati))
    ws = wb.active
    valori = {str(c.value) for riga in ws.iter_rows() for c in riga if c.value is not None}
    return '\n'.join(valori)


def _testo_pdf(dati):
    """Il testo di un PDF vive dentro stream di contenuto: va estratto, non
    cercato nei byte grezzi (vedi tests/test_report_service.py:testo_di)."""
    lettore = PdfReader(io.BytesIO(dati))
    return '\n'.join(pagina.extract_text() for pagina in lettore.pages)


@pytest.fixture
def due_strutture(app):
    """Struttura A con un admin che restera' orfano, struttura B con i dati
    che nessuno di A deve poter vedere."""
    from models import execute
    with app.app_context():
        a = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        b = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica B','B',1)").lastrowid
        da = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Oculistica','OCU',?)", (a,)).lastrowid
        db_ = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Cardiologia','CAR',?)", (b,)).lastrowid
        hash_pw = generate_password_hash('Passw0rd!')
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
                "VALUES ('admin@a.it',?,'A','A','admin',?,0)", (hash_pw, a))
        senza = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('nessuno@a.it',?,'N','N','utente',?,0)", (hash_pw, a)).lastrowid
        app_a = execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato) "
                        "VALUES (?,?,'OCU-1','REXXAM','OZY','funzionante')", (da, a)).lastrowid
        app_b = execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato) "
                        "VALUES (?,?,'SEGRETO-B','SIEMENS','Y1','funzionante')", (db_, b)).lastrowid
        for ap in (app_a, app_b):
            execute("INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento,prossima_scadenza) "
                    "VALUES (?,'preventiva',date('now','-1 year'),date('now','+30 days'))", (ap,))
            execute("INSERT INTO verifiche (apparecchio_id,data_verifica,prossima_scadenza,esito) "
                    "VALUES (?,date('now','-1 year'),date('now','+60 days'),'positivo')", (ap,))
    return {'a': a, 'b': b, 'senza': senza, 'app_b': app_b}


def entra(client, email):
    client.post('/login', data={'email': email, 'password': 'Passw0rd!'})


def orfana(app, struttura_id):
    """Riproduce lo stato che si crea disattivando o eliminando la struttura."""
    from models import execute
    with app.app_context():
        execute("UPDATE utenti SET struttura_id = NULL WHERE struttura_id = ?", (struttura_id,))


ROTTE = [
    # (url, estrattore) — ogni rotta va interrogata nel formato in cui parla
    # davvero: identita' per l'HTML, foglio letto per l'xlsx, testo estratto
    # per il PDF. La versione precedente cercava la stringa nei byte grezzi
    # della risposta per tutte le rotte: per l'xlsx (uno zip) e il PDF (testo
    # dentro stream di contenuto) l'asserzione passava qualunque cosa
    # facesse il codice, difetto compreso — un test che non prova nulla.
    ('/apparecchi', _testo_html),
    ('/manutenzioni', _testo_html),
    # Nota: la rotta e' registrata come '/scadenzario', non
    # '/manutenzioni/scadenzario' (il blueprint non ha url_prefix e questo
    # unico @manutenzioni_bp.route non ripete '/manutenzioni' come fanno gli
    # altri) — verificato con app.url_map.iter_rules(). E' un'inconsistenza
    # di naming preesistente e indipendente da questo fix; url_for('manutenzioni.
    # scadenzario') la risolve comunque correttamente, quindi l'app non ne
    # risente, ma un test che chiama l'URL sbagliato riceve sempre 404 e
    # "SEGRETO-B non c'e'" e' vero per il motivo sbagliato: usiamo l'URL reale.
    ('/scadenzario', _testo_html),
    ('/verifiche', _testo_html),
    ('/export/apparecchi/excel', _testo_excel),
    ('/export/apparecchi/pdf', _testo_pdf),
]


@pytest.mark.parametrize('rotta,estrattore', ROTTE)
def test_admin_senza_struttura_non_ottiene_dati_altrui(client, app, due_strutture, rotta, estrattore):
    """Il caso concreto: la struttura dell'admin sparisce e lui resta senza
    scope. Non deve diventare un lasciapassare su tutte le altre."""
    entra(client, 'admin@a.it')
    orfana(app, due_strutture['a'])
    risposta = client.get(rotta, follow_redirects=True)
    assert 'SEGRETO-B' not in estrattore(risposta.data)


@pytest.mark.parametrize('rotta,estrattore', ROTTE)
def test_utente_senza_divisioni_non_ottiene_dati(client, app, due_strutture, rotta, estrattore):
    """Controprova sul ramo che gia' funziona: se questo fallisce, la
    correzione ha rotto il caso sano invece di sistemare quello guasto."""
    entra(client, 'nessuno@a.it')
    risposta = client.get(rotta, follow_redirects=True)
    testo = estrattore(risposta.data)
    assert 'SEGRETO-B' not in testo
    assert 'OCU-1' not in testo


def test_admin_con_struttura_vede_i_propri(client, due_strutture):
    """Il filtro deve restare permissivo dove e' giusto che lo sia: un test
    che verifica solo le negazioni passerebbe anche con 'AND 1=0' ovunque."""
    entra(client, 'admin@a.it')
    risposta = client.get('/apparecchi')
    assert b'OCU-1' in risposta.data
    assert b'SEGRETO-B' not in risposta.data


def test_apparecchio_accessibile_rifiuta_senza_struttura(app, due_strutture):
    """models.apparecchio_accessibile ha lo stesso difetto del filtro:
    'struttura_id = ? OR ? IS NULL' accetta qualunque apparecchio quando la
    struttura attiva e' None. E' il controllo che protegge i download degli
    allegati, quindi non basta correggere le liste."""
    from flask import g
    from models import apparecchio_accessibile, query_one
    with app.test_request_context():
        g.user = query_one("SELECT * FROM utenti WHERE email='admin@a.it'")
        g.struttura_id = None
        g.divisioni = []
        assert apparecchio_accessibile(due_strutture['app_b']) is None


def test_apparecchio_accessibile_lascia_passare_il_superadmin(app, due_strutture):
    """Un superadmin che non impersona ha struttura_id None per progetto: e'
    il suo stato normale, non un difetto. La correzione deve distinguerlo
    dagli altri ruoli invece di negare a tutti."""
    from flask import g
    from models import apparecchio_accessibile, execute, query_one
    with app.app_context():
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,primo_accesso) "
                "VALUES ('super@x.it','x','S','S','superadmin',0)")
    with app.test_request_context():
        g.user = query_one("SELECT * FROM utenti WHERE email='super@x.it'")
        g.struttura_id = None
        g.divisioni = []
        assert apparecchio_accessibile(due_strutture['app_b']) is not None


def test_gli_utenti_orfani_vengono_disattivati_all_avvio(app):
    """Un admin senza struttura non deve restare un account funzionante di
    cui nessuno sa nulla: dopo il Task 1 non vede piu' dati, ma entra ancora.
    I superadmin hanno struttura_id NULL per progetto e non vanno toccati; i
    tecnici nemmeno, perche' sono legati alle strutture da tecnici_strutture."""
    from models import execute, query_one, apply_schema_updates
    with app.app_context():
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,attivo) "
                "VALUES ('orfano@a.it','x','O','O','admin',1)")
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,attivo) "
                "VALUES ('super@x.it','x','S','S','superadmin',1)")
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,attivo) "
                "VALUES ('tec@x.it','x','T','T','tecnico',1)")

        apply_schema_updates()

        assert query_one("SELECT attivo FROM utenti WHERE email='orfano@a.it'")['attivo'] == 0
        assert query_one("SELECT attivo FROM utenti WHERE email='super@x.it'")['attivo'] == 1
        assert query_one("SELECT attivo FROM utenti WHERE email='tec@x.it'")['attivo'] == 1
