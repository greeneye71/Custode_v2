"""Gli avvisi di scadenza: un interruttore, un formato, un solo server di posta.

Fino alla 2.6.1 c'erano due percorsi separati — un digest di testo acceso da
report_schedulato_attivo e un report PDF acceso da report_pdf_attivo, chiave
che nessun modulo ha mai scritto — e ogni struttura poteva avere un proprio
server SMTP. Ora il percorso e' uno e il server e' quello di sistema.
"""
import email
import io

import pytest
from pypdf import PdfReader


class SMTPFinto:
    """Sostituto di smtplib.SMTP che registra i messaggi invece di spedirli.

    Registra anche host/porta/credenziali: servono a provare che il server
    usato e' quello di sistema e non uno per struttura.
    """
    inviati = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.utente = None
        self.tls = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        self.tls = True

    def login(self, utente, password):
        self.utente = utente
        self.password = password

    def sendmail(self, mittente, destinatario, testo):
        SMTPFinto.inviati.append({
            'host': self.host, 'porta': self.port, 'utente': self.utente,
            'tls': self.tls, 'mittente': mittente, 'destinatario': destinatario,
            'messaggio': email.message_from_string(testo),
        })


def oggetto(messaggio):
    """L'oggetto decodificato.

    Un oggetto con un carattere non ASCII (qui il trattino lungo) viaggia
    codificato RFC 2047 — '=?utf-8?q?Scadenzario_Clinica_Alfa...' — quindi
    cercarci dentro il nome della struttura senza decodificare funziona per
    caso e smette di funzionare appena il nome contiene un accento.
    """
    from email.header import decode_header, make_header
    return str(make_header(decode_header(messaggio['Subject'])))


def corpo_testo(messaggio):
    """Il testo del primo pezzo text/plain di un messaggio MIME."""
    for parte in messaggio.walk():
        if parte.get_content_type() == 'text/plain':
            return parte.get_payload(decode=True).decode('utf-8')
    return ''


def allegati_pdf(messaggio):
    """(nome, testo estratto) di ogni allegato PDF."""
    trovati = []
    for parte in messaggio.walk():
        if parte.get_content_type() == 'application/pdf':
            dati = parte.get_payload(decode=True)
            lettore = PdfReader(io.BytesIO(dati))
            testo = "\n".join(p.extract_text() for p in lettore.pages)
            trovati.append((parte.get_filename(), testo))
    return trovati


@pytest.fixture
def posta(app, monkeypatch):
    """Scheduler pronto a inviare: SMTP finto, orologio neutralizzato,
    server di sistema configurato."""
    from scheduler import BackgroundScheduler
    SMTPFinto.inviati = []
    monkeypatch.setattr('smtplib.SMTP', SMTPFinto)
    app.config['APP_CONFIG'] = dict(app.config.get('APP_CONFIG') or {})
    app.config['APP_CONFIG'].update({
        'smtp_host': 'smtp.sistema.it', 'smtp_port': 2525,
        'smtp_user': 'sistema@sistema.it', 'smtp_password': 'segreta',
        'smtp_use_tls': True,
    })
    scheduler = BackgroundScheduler(app)
    monkeypatch.setattr(scheduler, '_periodo_digest', lambda frequenza, now=None: 'test')
    return scheduler


@pytest.fixture
def struttura_con_scadenza(app):
    """Una struttura con un destinatario e una manutenzione scaduta."""
    from models import execute
    with app.app_context():
        s = execute("INSERT INTO strutture (nome,codice,attiva,email_notifiche) "
                    "VALUES ('Clinica Alfa','ALF',1,'direzione@alfa.it')").lastrowid
        d = execute("INSERT INTO divisioni (nome,codice,struttura_id) "
                    "VALUES ('Oculistica','OCU',?)", (s,)).lastrowid
        a = execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,"
                    "modello,stato,ubicazione) VALUES (?,?,'R-00015','REXXAM','OZY',"
                    "'funzionante','Sala 1')", (d, s)).lastrowid
        execute("INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento,prossima_scadenza) "
                "VALUES (?,'preventiva',date('now','-1 year'),date('now','-10 days'))", (a,))
    return s


def accendi(app, struttura_id, formato):
    from models import set_struttura_config
    with app.app_context():
        set_struttura_config(struttura_id, 'avvisi_scadenza_attivi', '1')
        set_struttura_config(struttura_id, 'avvisi_scadenza_formato', formato)


def test_il_formato_testo_manda_il_digest(app, posta, struttura_con_scadenza):
    """Il digest continua a nominare struttura e divisione: e' il modo in cui
    chi riceve capisce di chi si parla, ora che il mittente e' unico per tutte
    le strutture e non dice piu' niente."""
    accendi(app, struttura_con_scadenza, 'testo')
    posta._send_deadline_alerts()

    assert len(SMTPFinto.inviati) == 1
    inviata = SMTPFinto.inviati[0]
    assert inviata['destinatario'] == 'direzione@alfa.it'
    assert 'Clinica Alfa' in oggetto(inviata['messaggio'])
    corpo = corpo_testo(inviata['messaggio'])
    assert 'Clinica Alfa' in corpo
    assert 'Oculistica' in corpo
    assert 'R-00015' in corpo
    assert allegati_pdf(inviata['messaggio']) == []


def test_il_formato_pdf_manda_il_report_allegato(app, posta, struttura_con_scadenza):
    """Percorso mai stato raggiungibile prima: report_pdf_attivo, la chiave che
    scheduler._send_scheduled_reports leggeva, non veniva scritta da nessun
    modulo e da nessun template. Il codice del report c'era, funzionante, dalla
    2.5, e restava a zero per sempre."""
    accendi(app, struttura_con_scadenza, 'pdf')
    posta._send_deadline_alerts()

    assert len(SMTPFinto.inviati) == 1
    allegati = allegati_pdf(SMTPFinto.inviati[0]['messaggio'])
    assert len(allegati) == 1
    nome, testo_pdf = allegati[0]
    assert nome.endswith('.pdf')
    assert 'R-00015' in testo_pdf


def test_il_corpo_dell_email_col_pdf_nomina_la_struttura(app, posta, struttura_con_scadenza):
    """Era «In allegato il report periodico delle scadenze», e basta: nessuna
    struttura, in un deployment che ne ospita diverse e ora spedisce tutte
    dallo stesso indirizzo. Con l'allegato aperto si capisce, ma il messaggio
    da solo no."""
    accendi(app, struttura_con_scadenza, 'pdf')
    posta._send_deadline_alerts()

    corpo = corpo_testo(SMTPFinto.inviati[0]['messaggio'])
    assert 'Clinica Alfa' in corpo


def test_una_struttura_che_aveva_un_server_proprio_usa_ora_quello_di_sistema(
        app, posta, struttura_con_scadenza):
    """Righe come queste sopravvivono solo in un database che non ha ancora
    ricevuto la migrazione del Task 1 (per esempio un archivio importato da
    un'installazione piu' vecchia). Non devono dirottare la posta: se lo
    facessero, l'invio si fermerebbe contro un server che non esiste."""
    from models import set_struttura_config
    accendi(app, struttura_con_scadenza, 'testo')
    with app.app_context():
        set_struttura_config(struttura_con_scadenza, 'smtp_host', 'smtp.inesistente.local')
        set_struttura_config(struttura_con_scadenza, 'smtp_user', 'vecchio@clinica.it')
        set_struttura_config(struttura_con_scadenza, 'smtp_from', 'vecchio@clinica.it')

    posta._send_deadline_alerts()

    assert len(SMTPFinto.inviati) == 1
    inviata = SMTPFinto.inviati[0]
    assert inviata['host'] == 'smtp.sistema.it'
    assert inviata['porta'] == 2525
    assert inviata['utente'] == 'sistema@sistema.it'
    assert inviata['mittente'] == 'sistema@sistema.it'


def test_senza_interruttore_non_parte_niente(app, posta, struttura_con_scadenza):
    """Una struttura che non ha chiesto gli avvisi non ne riceve, anche se ha
    scadenze e un destinatario configurato."""
    posta._send_deadline_alerts()
    assert SMTPFinto.inviati == []


def test_senza_server_di_sistema_non_si_spedisce_e_non_si_esplode(
        app, posta, struttura_con_scadenza):
    """Un'installazione che non ha ancora configurato la posta e' normale. Il
    task periodico deve saltarla e proseguire, non sollevare: gira dentro un
    thread di fondo dove un'eccezione non la vede nessuno."""
    accendi(app, struttura_con_scadenza, 'testo')
    app.config['APP_CONFIG']['smtp_host'] = ''

    posta._send_deadline_alerts()

    assert SMTPFinto.inviati == []
