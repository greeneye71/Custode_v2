"""Il giro completo del reset dalla schermata di accesso.

La scelta che questi test difendono piu' di tutte: la temporanea vale ACCANTO
alla password attuale. Sulla pagina di accesso chiunque puo' digitare
l'indirizzo di un collega, e se il reset sostituisse la password, chiunque
conosca l'email di un collega potrebbe buttarlo fuori dal suo account.
"""
import email as modulo_email
import re

import pytest
from werkzeug.security import generate_password_hash


class SMTPFinto:
    inviati = []

    def __init__(self, host, port, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        pass

    def login(self, utente, password):
        pass

    def sendmail(self, mittente, destinatario, testo):
        SMTPFinto.inviati.append((destinatario,
                                  modulo_email.message_from_string(testo)))


def corpo(messaggio):
    return messaggio.get_payload(decode=True).decode('utf-8')


def temporanea_spedita():
    """La password temporanea letta dall'email, come la leggerebbe l'utente."""
    _destinatario, messaggio = SMTPFinto.inviati[-1]
    righe = [r.strip() for r in corpo(messaggio).splitlines() if r.strip()]
    # E' la riga isolata fra il saluto e le istruzioni.
    for riga in righe:
        if re.fullmatch(r'[A-Za-z0-9_-]{10,}', riga):
            return riga
    raise AssertionError(f"nessuna temporanea riconoscibile in: {righe}")


@pytest.fixture
def posta(app, monkeypatch):
    SMTPFinto.inviati = []
    monkeypatch.setattr('smtplib.SMTP', SMTPFinto)
    app.config['APP_CONFIG'] = dict(app.config.get('APP_CONFIG') or {})
    app.config['APP_CONFIG'].update({
        'smtp_host': 'smtp.sistema.it', 'smtp_port': 2525,
        'smtp_user': 'sistema@sistema.it', 'smtp_password': 'segreta',
    })
    return SMTPFinto


@pytest.fixture
def dati(app):
    from models import execute
    with app.app_context():
        s = execute("INSERT INTO strutture (nome,codice,attiva) "
                    "VALUES ('Clinica A','A',1)").lastrowid
        h = generate_password_hash('Passw0rd!')
        mario = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,"
            "primo_accesso) VALUES ('mario@a.it',?,'Mario','Rossi','utente',?,0)",
            (h, s)).lastrowid
        spento = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,"
            "primo_accesso,attivo) VALUES ('spento@a.it',?,'S','S','utente',?,0,0)",
            (h, s)).lastrowid
        sparito = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,"
            "primo_accesso,eliminato_il) VALUES ('sparito@a.it',?,'C','C','utente',?,0,"
            "'2026-01-01 10:00:00')", (h, s)).lastrowid
    return {'s': s, 'mario': mario, 'spento': spento, 'sparito': sparito}


def chiedi(client, indirizzo='mario@a.it'):
    return client.post('/password-dimenticata', data={'email': indirizzo},
                       follow_redirects=True)


# ---------------------------------------------------------------------------
# Il giro completo
# ---------------------------------------------------------------------------

def test_si_chiede_arriva_l_email_e_si_entra_con_la_temporanea(client, app, posta, dati):
    chiedi(client)
    assert len(posta.inviati) == 1
    destinatario, messaggio = posta.inviati[0]
    assert destinatario == 'mario@a.it'

    risposta = client.post('/login', data={'email': 'mario@a.it',
                                           'password': temporanea_spedita()},
                           follow_redirects=False)
    # Entrato, e mandato subito a scegliersene una nuova.
    assert risposta.status_code == 302
    assert '/cambio-password' in risposta.headers['Location']


def test_la_password_vecchia_funziona_ancora_mentre_il_reset_e_in_sospeso(
        client, app, posta, dati):
    """L'asserzione che distingue questa soluzione da quella semplice, ed e' il
    motivo per cui e' stata scelta: nessuno puo' chiudere fuori nessuno
    chiedendo un reset a nome suo."""
    chiedi(client)
    risposta = client.post('/login', data={'email': 'mario@a.it',
                                           'password': 'Passw0rd!'},
                           follow_redirects=False)
    assert risposta.status_code == 302
    assert '/cambio-password' not in risposta.headers['Location']


def test_entrando_con_la_propria_password_il_reset_sparisce(client, app, posta, dati):
    """Se l'utente se l'e' ricordata, la temporanea non ha piu' motivo di
    restare valida — nemmeno nella casella di posta di chi l'ha ricevuta."""
    from models import query_one
    chiedi(client)
    temporanea = temporanea_spedita()
    client.post('/login', data={'email': 'mario@a.it', 'password': 'Passw0rd!'})
    client.get('/logout')

    with app.app_context():
        riga = query_one("SELECT reset_hash, reset_scadenza FROM utenti WHERE id=?",
                         (dati['mario'],))
        assert riga['reset_hash'] is None and riga['reset_scadenza'] is None

    risposta = client.post('/login', data={'email': 'mario@a.it',
                                           'password': temporanea})
    assert risposta.status_code == 200   # rimasto sulla pagina di accesso
    with client.session_transaction() as sessione:
        assert 'token' not in sessione


def test_la_temporanea_non_entra_due_volte(client, app, posta, dati):
    chiedi(client)
    temporanea = temporanea_spedita()
    client.post('/login', data={'email': 'mario@a.it', 'password': temporanea})
    client.get('/logout')

    risposta = client.post('/login', data={'email': 'mario@a.it',
                                           'password': temporanea})
    assert risposta.status_code == 200
    with client.session_transaction() as sessione:
        assert 'token' not in sessione


def test_una_temporanea_scaduta_non_entra(client, app, posta, dati):
    from models import execute
    chiedi(client)
    temporanea = temporanea_spedita()
    with app.app_context():
        execute("UPDATE utenti SET reset_scadenza = datetime('now','-1 minute') "
                "WHERE id = ?", (dati['mario'],))

    risposta = client.post('/login', data={'email': 'mario@a.it',
                                           'password': temporanea})
    assert risposta.status_code == 200
    with client.session_transaction() as sessione:
        assert 'token' not in sessione


def test_usare_la_temporanea_chiude_le_altre_sessioni(client, app, posta, dati):
    """Chi e' rimasto dentro con la vecchia password non deve restarci: se il
    reset e' servito perche' qualcuno si e' preso l'account, quella sessione e'
    la sua."""
    from models import execute, query_one
    with app.app_context():
        execute("INSERT INTO sessioni (utente_id, token, expires_at) "
                "VALUES (?, 'altrove', datetime('now','+8 hours'))", (dati['mario'],))
    chiedi(client)
    client.post('/login', data={'email': 'mario@a.it',
                                'password': temporanea_spedita()})
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM sessioni "
                         "WHERE utente_id=? AND token='altrove'",
                         (dati['mario'],))['n'] == 0


# ---------------------------------------------------------------------------
# Cosa non si deve rivelare
# ---------------------------------------------------------------------------

def messaggio_unico(risposta):
    return "Se l&#39;indirizzo e&#39; registrato" in risposta.get_data(as_text=True) \
        or "Se l'indirizzo e' registrato" in risposta.get_data(as_text=True)


def test_un_indirizzo_sconosciuto_riceve_lo_stesso_messaggio(client, app, posta, dati):
    risposta = chiedi(client, 'nessuno@x.it')
    assert messaggio_unico(risposta)
    assert posta.inviati == []


def test_un_utente_disattivato_riceve_lo_stesso_messaggio(client, app, posta, dati):
    risposta = chiedi(client, 'spento@a.it')
    assert messaggio_unico(risposta)
    assert posta.inviati == []


def test_un_utente_cancellato_riceve_lo_stesso_messaggio(client, app, posta, dati):
    """Cancellato ma con la riga ancora al suo posto: e' esattamente il caso in
    cui una svista fa spedire una temporanea a un account che non esiste piu'."""
    risposta = chiedi(client, 'sparito@a.it')
    assert messaggio_unico(risposta)
    assert posta.inviati == []


def test_un_utente_valido_riceve_lo_stesso_messaggio(client, app, posta, dati):
    """L'altra meta' del punto: se il messaggio di chi esiste fosse diverso,
    i tre test qui sopra non proverebbero niente."""
    risposta = chiedi(client)
    assert messaggio_unico(risposta)
    assert len(posta.inviati) == 1


def test_un_indirizzo_sconosciuto_non_finisce_nel_registro(client, app, posta, dati):
    """Non c'e' un utente a cui legare la voce, e scrivere nel registro di
    sistema indirizzi forniti da chi passa vuol dire lasciare a un estraneo la
    penna. Il contatore resta in login_attempts, che e' il posto fatto per
    quello."""
    from models import query_one
    chiedi(client, 'nessuno@x.it')
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM log_attivita "
                         "WHERE dettagli LIKE '%nessuno@x.it%' "
                         "   OR azione LIKE 'reset%'")['n'] == 0
        assert query_one("SELECT COUNT(*) AS n FROM login_attempts "
                         "WHERE email='nessuno@x.it' AND esito='reset'")['n'] == 1


def test_la_temporanea_spedita_finisce_nel_registro_con_la_struttura(
        client, app, posta, dati):
    """La lezione della 2.6.1: una voce con struttura_id nullo e' invisibile in
    /admin/log-attivita proprio a chi deve leggerla."""
    from models import query_one
    chiedi(client)
    with app.app_context():
        riga = query_one("SELECT struttura_id FROM log_attivita "
                         "WHERE azione='reset_password_richiesto'")
        assert riga is not None
        assert riga['struttura_id'] == dati['s']


def test_l_uso_della_temporanea_finisce_nel_registro(client, app, posta, dati):
    from models import query_one
    chiedi(client)
    client.post('/login', data={'email': 'mario@a.it',
                                'password': temporanea_spedita()})
    with app.app_context():
        riga = query_one("SELECT struttura_id FROM log_attivita "
                         "WHERE azione='reset_password_usato'")
        assert riga is not None
        assert riga['struttura_id'] == dati['s']


# ---------------------------------------------------------------------------
# Limite e disponibilita'
# ---------------------------------------------------------------------------

def test_il_limite_blocca_le_richieste_ripetute(client, app, posta, dati):
    from reset_password import SOGLIA_IP
    for _ in range(SOGLIA_IP):
        chiedi(client)
    assert len(posta.inviati) == SOGLIA_IP
    chiedi(client)
    assert len(posta.inviati) == SOGLIA_IP   # la sesta non parte


def test_il_limite_scatta_anche_per_un_indirizzo_inesistente(client, app, posta, dati):
    """Il limite si guarda PRIMA di sapere se l'utente esiste. Se si guardasse
    dopo, il tempo di risposta diverso fra indirizzo noto e ignoto rivelerebbe
    quello che il messaggio unico nasconde — e qui si vede la differenza: le
    richieste per un indirizzo che non esiste devono contare anche loro."""
    from models import query_one
    from reset_password import SOGLIA_IP
    for _ in range(SOGLIA_IP + 3):
        chiedi(client, 'nessuno@x.it')
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM login_attempts "
                         "WHERE esito='reset'")['n'] == SOGLIA_IP + 3


def test_senza_smtp_il_collegamento_non_compare(client, app, dati):
    app.config['APP_CONFIG'] = dict(app.config.get('APP_CONFIG') or {})
    app.config['APP_CONFIG'].update({'smtp_host': '', 'smtp_user': ''})
    pagina = client.get('/login').get_data(as_text=True)
    assert 'password-dimenticata' not in pagina


def test_con_smtp_il_collegamento_compare(client, app, posta, dati):
    pagina = client.get('/login').get_data(as_text=True)
    assert 'password-dimenticata' in pagina


def test_senza_smtp_la_pagina_rimanda_indietro_dicendo_perche(client, app, dati):
    """Raggiungibile a mano anche senza il collegamento. Non deve accettare una
    richiesta che non ha modo di consegnare."""
    app.config['APP_CONFIG'] = dict(app.config.get('APP_CONFIG') or {})
    app.config['APP_CONFIG'].update({'smtp_host': '', 'smtp_user': ''})
    risposta = client.get('/password-dimenticata', follow_redirects=True)
    testo = risposta.get_data(as_text=True)
    assert 'server di posta' in testo
    assert 'name="email"' in testo   # e' tornato sulla schermata di accesso


def test_se_l_email_non_parte_il_reset_non_resta_aperto(client, app, posta, dati,
                                                        monkeypatch):
    """Una temporanea valida che nessuno ha in mano non serve a niente e resta
    valida per mezz'ora."""
    from models import query_one

    def esplode(*args, **kwargs):
        raise OSError('server irraggiungibile')

    monkeypatch.setattr('smtplib.SMTP', esplode)
    chiedi(client)
    with app.app_context():
        riga = query_one("SELECT reset_hash FROM utenti WHERE id=?", (dati['mario'],))
        assert riga['reset_hash'] is None
