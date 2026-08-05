"""posta.py: l'unico posto da cui parte la posta.

Estratto dallo scheduler quando la password temporanea del reset ha avuto
bisogno di spedire anche lei. I test dello scheduler restano dove sono e
provano il comportamento visto da li'; questi provano il modulo per quello che
gli altri chiamanti gli chiedono.
"""
from email.mime.text import MIMEText

import pytest


class SMTPFinto:
    inviati = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        SMTPFinto.inviati.append('starttls')

    def login(self, utente, password):
        pass

    def sendmail(self, mittente, destinatario, testo):
        SMTPFinto.inviati.append((self.host, self.port, mittente, destinatario))


@pytest.fixture
def smtp_finto(monkeypatch):
    SMTPFinto.inviati = []
    monkeypatch.setattr('smtplib.SMTP', SMTPFinto)
    return SMTPFinto


CONFIGURATO = {'smtp_host': 'smtp.sistema.it', 'smtp_port': 2525,
               'smtp_user': 'sistema@sistema.it', 'smtp_password': 'segreta'}


def test_senza_host_il_server_non_e_configurato():
    from posta import smtp_configurato
    assert smtp_configurato(dict(CONFIGURATO, smtp_host='')) is False


def test_senza_utente_il_server_non_e_configurato():
    """L'utente e' anche il mittente: senza, il messaggio non avrebbe un From.
    Va controllato a parte dall'host, perche' una configurazione a meta' e' il
    caso reale — si compila l'host e ci si ferma."""
    from posta import smtp_configurato
    assert smtp_configurato(dict(CONFIGURATO, smtp_user='')) is False


def test_con_host_e_utente_il_server_e_configurato():
    from posta import smtp_configurato
    assert smtp_configurato(CONFIGURATO) is True


def test_una_configurazione_vuota_non_esplode():
    """Un'installazione appena creata non ha ancora la posta, e la schermata di
    accesso chiede lo stesso se e' configurata."""
    from posta import smtp_configurato
    assert smtp_configurato(None) is False
    assert smtp_configurato({}) is False


def test_il_tls_booleano_e_quello_stringa_valgono_uguale():
    """smtp_use_tls arriva come booleano dal JSON globale, ma nei database
    delle installazioni piu' vecchie era la stringa '1' o '0'."""
    from posta import parametri
    assert parametri(dict(CONFIGURATO, smtp_use_tls=True))['usa_tls'] is True
    assert parametri(dict(CONFIGURATO, smtp_use_tls='1'))['usa_tls'] is True
    assert parametri(dict(CONFIGURATO, smtp_use_tls=False))['usa_tls'] is False
    assert parametri(dict(CONFIGURATO, smtp_use_tls='0'))['usa_tls'] is False


def test_una_porta_vuota_ripiega_su_587():
    from posta import parametri
    assert parametri(dict(CONFIGURATO, smtp_port=''))['porta'] == 587


def test_senza_configurazione_non_si_spedisce_e_si_torna_falso(smtp_finto):
    """Chi chiama deve poter distinguere «non partita» da «partita», ma senza
    ricevere un'eccezione: i chiamanti sono un thread di fondo e una rotta che
    non deve rivelare niente a chi passa."""
    from posta import invia
    assert invia({'smtp_host': '', 'smtp_user': ''}, 'tizio@x.it',
                 MIMEText('ciao')) is False
    assert smtp_finto.inviati == []


def test_il_messaggio_parte_col_mittente_di_sistema(smtp_finto):
    from posta import invia
    msg = MIMEText('ciao')
    assert invia(CONFIGURATO, 'tizio@x.it', msg) is True
    assert ('smtp.sistema.it', 2525, 'sistema@sistema.it', 'tizio@x.it') \
        in smtp_finto.inviati
    assert msg['From'] == 'sistema@sistema.it'
    assert msg['To'] == 'tizio@x.it'


def test_un_server_irraggiungibile_non_solleva(monkeypatch):
    """Il thread di fondo dello scheduler morirebbe in silenzio, e la rotta del
    reset direbbe a chi passa che quell'indirizzo esiste."""
    from posta import invia

    def esplode(*args, **kwargs):
        raise OSError('connessione rifiutata')

    monkeypatch.setattr('smtplib.SMTP', esplode)
    assert invia(CONFIGURATO, 'tizio@x.it', MIMEText('ciao')) is False
