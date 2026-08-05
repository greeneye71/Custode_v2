"""Il blocco anti-forza-bruta del login.

Esisteva dalla v1 e non e' mai scattato. La finestra veniva calcolata con
datetime.now() — l'ora locale — e confrontata con login_attempts.created_at,
che ha DEFAULT CURRENT_TIMESTAMP, cioe' l'ora UTC di SQLite. Su qualunque
installazione a est di Greenwich le due differiscono di una o due ore, e una
riga appena scritta risultava piu' vecchia della finestra: il conteggio dei
tentativi falliti tornava sempre zero. L'unico freno rimasto era il secondo di
attesa dopo ogni tentativo.
"""
import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def utente(app):
    from models import execute
    with app.app_context():
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,primo_accesso) "
                "VALUES ('mario@x.it',?,'Mario','Rossi','utente',0)",
                (generate_password_hash('Passw0rd!'),))


def sbaglia(client, email='mario@x.it'):
    return client.post('/login', data={'email': email, 'password': 'sbagliata'})


def test_dopo_cinque_tentativi_falliti_lo_stesso_ip_e_bloccato(client, app, utente):
    for _ in range(5):
        assert sbaglia(client).status_code == 200
    assert sbaglia(client).status_code == 429


def test_il_blocco_ferma_anche_la_password_giusta(client, app, utente):
    """Bloccare solo i tentativi sbagliati non servirebbe a niente: chi indovina
    al sesto tentativo entrerebbe lo stesso."""
    for _ in range(5):
        sbaglia(client)
    risposta = client.post('/login', data={'email': 'mario@x.it',
                                           'password': 'Passw0rd!'})
    assert risposta.status_code == 429
    with client.session_transaction() as sessione:
        assert 'token' not in sessione


def test_i_tentativi_vecchi_non_bloccano_piu(client, app, utente):
    """Il blocco e' temporaneo: passata la finestra si riprova. Altrimenti una
    giornata storta chiuderebbe fuori per sempre."""
    from models import execute
    for _ in range(5):
        sbaglia(client)
    with app.app_context():
        execute("UPDATE login_attempts SET created_at = datetime('now','-2 hours')")

    risposta = client.post('/login', data={'email': 'mario@x.it',
                                           'password': 'Passw0rd!'},
                           follow_redirects=False)
    assert risposta.status_code != 429


def test_il_blocco_viene_registrato(client, app, utente):
    """La pagina Sicurezza dell'amministratore legge queste righe: senza, un
    attacco in corso non si vede da nessuna parte."""
    from models import query_one
    for _ in range(6):
        sbaglia(client)
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM login_attempts "
                         "WHERE esito='bloccato'")['n'] >= 1
