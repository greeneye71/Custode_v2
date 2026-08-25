"""Il registro attivita' deve contenere la struttura di chi ha agito.

Il difetto che questi test inchiodano non si vede da un log che si riempie:
le righe c'erano, ma con `struttura_id` NULL. /admin/log-attivita filtra per
struttura per tutti tranne il superadmin, quindi l'admin di struttura vedeva
un registro vuoto mentre il database era pieno. Si vede solo chiedendo la
pagina da un admin di struttura dopo un'operazione ordinaria.
"""
import hashlib

import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def scenario(app):
    """Due strutture, un admin ciascuna, una divisione nella prima."""
    from models import execute
    with app.app_context():
        a = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        b = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica B','B',1)").lastrowid
        div_a = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Oculistica','OCU',?)",
                        (a,)).lastrowid
        hash_pw = generate_password_hash('Passw0rd!')
        for email, struttura in (('admin@a.it', a), ('admin@b.it', b)):
            execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
                    "VALUES (?,?,'A','A','admin',?,0)", (email, hash_pw, struttura))
    return {'a': a, 'b': b, 'div_a': div_a}


def entra(client, email):
    client.post('/login', data={'email': email, 'password': 'Passw0rd!'})


def crea_apparecchio(client, divisione_id, matricola):
    return client.post('/apparecchi/nuovo', data={
        'matricola': matricola, 'marca': 'REXXAM', 'modello': 'OZY',
        'divisione_id': divisione_id, 'stato': 'funzionante',
    }, follow_redirects=True)


def test_admin_di_struttura_vede_le_proprie_voci(client, scenario):
    """La riga scritta da un'operazione ordinaria deve tornare nella pagina.

    E' il test che sarebbe fallito prima della correzione: l'inserimento
    riusciva, la voce veniva scritta, e la pagina restava vuota.
    """
    entra(client, 'admin@a.it')
    crea_apparecchio(client, scenario['div_a'], 'OCU-1')

    pagina = client.get('/admin/log-attivita').data.decode('utf-8', errors='replace')
    assert 'OCU-1' in pagina


def test_admin_non_vede_le_voci_di_un_altra_struttura(client, scenario):
    """Il default automatico non deve rendere il registro globale."""
    entra(client, 'admin@a.it')
    crea_apparecchio(client, scenario['div_a'], 'OCU-1')
    client.get('/logout')

    entra(client, 'admin@b.it')
    pagina = client.get('/admin/log-attivita').data.decode('utf-8', errors='replace')
    assert 'OCU-1' not in pagina


def test_operazione_globale_resta_senza_struttura(app, client, scenario):
    """`struttura_id=None` esplicito non deve essere sovrascritto dal default.

    Backup, restore e configurazione di sistema non appartengono alla
    struttura che un superadmin sta impersonando: la sentinella serve
    proprio a distinguere "non indicata" da "deliberatamente globale".
    """
    from models import log_attivita, query_one
    from flask import g

    with app.test_request_context('/'):
        g.struttura_id = scenario['a']
        log_attivita(None, 'backup_creazione', 'backup', None, 'globale', None,
                     struttura_id=None)

    with app.app_context():
        riga = query_one("SELECT struttura_id FROM log_attivita WHERE entita='backup'")
    assert riga['struttura_id'] is None


def test_log_fuori_da_una_richiesta_non_esplode(app):
    """Thread di background e script: `g` esiste ma non ha la struttura.

    Fuori da un contesto di richiesta l'accesso a `g` solleva RuntimeError,
    che getattr non cattura: senza l'intercettazione ogni log dello scheduler
    farebbe fallire l'operazione che stava registrando.
    """
    from models import log_attivita, query_one

    with app.app_context():
        log_attivita(None, 'controllo_email', 'sistema', None, 'da scheduler')
        riga = query_one("SELECT struttura_id FROM log_attivita WHERE entita='sistema'")
    assert riga['struttura_id'] is None


def test_scrittura_via_api_finisce_nel_registro(app, client, scenario):
    """Una manutenzione creata da un token non deve comparire dal nulla."""
    from models import execute, query_one

    token = 'tok-di-prova'
    with app.app_context():
        apparecchio_id = execute(
            "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato) "
            "VALUES (?,?,'OCU-9','REXXAM','OZY','funzionante')",
            (scenario['div_a'], scenario['a'])).lastrowid
        execute("INSERT INTO api_tokens (struttura_id,nome,token_hash,scopes,attivo) "
                "VALUES (?,'Gestionale',?, 'read write',1)",
                (scenario['a'], hashlib.sha256(token.encode()).hexdigest()))

    risposta = client.post('/api/v1/manutenzioni',
                           json={'apparecchio_id': apparecchio_id, 'tipo': 'preventiva',
                                 'data_intervento': '2026-01-15'},
                           headers={'Authorization': f'Bearer {token}'})
    assert risposta.status_code == 201

    with app.app_context():
        riga = query_one("SELECT utente_id, struttura_id, dettagli FROM log_attivita "
                         "WHERE entita='manutenzioni'")
    assert riga is not None, "la scrittura via API non ha lasciato traccia nel registro"
    assert riga['struttura_id'] == scenario['a']
    # L'autore e' il token, non una persona: utente_id resta NULL e la pagina
    # del registro lo mostra come tale (templates/admin/log_attivita.html:87).
    assert riga['utente_id'] is None
    assert 'Gestionale' in riga['dettagli']
