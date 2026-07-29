"""Nessuna rotta deve servire dati a chi non ha uno scope.

Il difetto che questi test inchiodano non e' un errore di calcolo: e' un ramo
che restituisce "nessun filtro" invece di "nessun dato". Non si vede leggendo
una pagina che funziona, si vede solo chiedendola da un account senza scope.
"""
import pytest
from werkzeug.security import generate_password_hash


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
    '/apparecchi',
    '/manutenzioni',
    '/manutenzioni/scadenzario',
    '/verifiche',
    '/export/apparecchi/excel',
    '/export/apparecchi/pdf',
]


@pytest.mark.parametrize('rotta', ROTTE)
def test_admin_senza_struttura_non_ottiene_dati_altrui(client, app, due_strutture, rotta):
    """Il caso concreto: la struttura dell'admin sparisce e lui resta senza
    scope. Non deve diventare un lasciapassare su tutte le altre."""
    entra(client, 'admin@a.it')
    orfana(app, due_strutture['a'])
    risposta = client.get(rotta, follow_redirects=True)
    assert b'SEGRETO-B' not in risposta.data


@pytest.mark.parametrize('rotta', ROTTE)
def test_utente_senza_divisioni_non_ottiene_dati(client, app, due_strutture, rotta):
    """Controprova sul ramo che gia' funziona: se questo fallisce, la
    correzione ha rotto il caso sano invece di sistemare quello guasto."""
    entra(client, 'nessuno@a.it')
    risposta = client.get(rotta, follow_redirects=True)
    assert b'SEGRETO-B' not in risposta.data
    assert b'OCU-1' not in risposta.data


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
