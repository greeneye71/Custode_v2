"""
MedInventory - Authentication module
Handles login, logout, password change, session management, and access decorators.
"""

import json
import logging
import os
import uuid
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, g, current_app
)
from werkzeug.security import check_password_hash, generate_password_hash

from models import query_one, query_all, execute, log_attivita, get_db

auth_bp = Blueprint('auth', __name__)

logger = logging.getLogger('medinventory.auth')


# ---------------------------------------------------------------------------
# Version notice helper
# ---------------------------------------------------------------------------

def _check_version_notice():
    """Se esiste il file sentinella di aggiornamento versione, mostra un flash
    informativo all'admin e poi elimina il file (notifica una volta sola).
    """
    notice_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'data', '.version_notice'
    )
    if not os.path.exists(notice_path):
        return
    try:
        with open(notice_path, 'r', encoding='utf-8') as f:
            info = json.load(f)
        old = info.get('old_version', '?')
        new = info.get('new_version', '?')
        at  = info.get('upgraded_at', '')
        flash(
            f'Applicazione aggiornata dalla versione {old} alla {new} ({at}). '
            f'Il file config.json è stato salvato in backup automaticamente.',
            'info'
        )
        os.remove(notice_path)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def login_required(f):
    """Decorator: redirect to login if user is not authenticated."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _load_user_from_session():
            flash('Sessione scaduta. Effettua il login.', 'warning')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator: require admin or superadmin role. Must be used after @login_required."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if g.user['ruolo'] not in ('admin', 'superadmin'):
            flash('Accesso non autorizzato.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def superadmin_required(f):
    """Decorator: richiede ruolo superadmin."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if g.user['ruolo'] != 'superadmin':
            flash('Accesso riservato al superamministratore.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def tecnico_o_superadmin_required(f):
    """Decorator: richiede ruolo superadmin o tecnico."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if g.user['ruolo'] not in ('superadmin', 'tecnico'):
            flash('Accesso non autorizzato.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def tecnico_o_admin_required(f):
    """Decorator: richiede ruolo admin, superadmin o tecnico."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if g.user['ruolo'] not in ('admin', 'superadmin', 'tecnico'):
            flash('Accesso non autorizzato.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def admin_struttura_required(f):
    """Decorator: richiede ruolo admin (della struttura) o superadmin che stia impersonando."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        is_admin = g.user['ruolo'] in ('admin',)
        is_superadmin_impersonating = getattr(g, 'is_superadmin_impersonating', False)
        if not (is_admin or is_superadmin_impersonating):
            flash('Per accedere a questa funzione, entra prima nel contesto di una struttura.', 'warning')
            return redirect(url_for('strutture.index') if 'strutture' in current_app.blueprints else url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def operazione_globale_required(f):
    """Decorator: operazioni che agiscono sull'INTERO database, non su una singola
    struttura — backup, ripristino, reset, configurazione globale (chiavi AI di
    default, credenziali IMAP/SMTP).

    Riservate al superadmin: un admin di struttura non deve poter scaricare il DB
    di tutti i tenant né azzerare i dati altrui.

    Eccezione: nelle installazioni a struttura singola non esiste un superadmin
    (seed.py crea solo un 'admin'), e "globale" coincide con "la mia struttura".
    Lì l'admin mantiene l'accesso.
    """
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if g.user['ruolo'] == 'superadmin':
            return f(*args, **kwargs)
        if g.user['ruolo'] == 'admin' and _installazione_singola_struttura():
            return f(*args, **kwargs)
        flash('Operazione riservata al superamministratore.', 'danger')
        return redirect(url_for('index'))
    return decorated_function


def _installazione_singola_struttura():
    """True se il deployment ospita una sola struttura (nessun isolamento da
    garantire fra tenant diversi)."""
    if current_app.config.get('APP_CONFIG', {}).get('single_struttura', False):
        return True
    row = query_one("SELECT COUNT(*) AS cnt FROM strutture WHERE attiva = 1")
    return bool(row) and row['cnt'] <= 1


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _load_user_from_session():
    """Load user from session token. Returns True if valid, False otherwise."""
    token = session.get('token')
    if not token:
        return False

    # Find valid session
    sess = query_one(
        """SELECT s.*, u.id as user_id, u.email, u.nome, u.cognome, u.ruolo,
                  u.attivo, u.primo_accesso, u.divisione_default_id, u.struttura_id
           FROM sessioni s
           JOIN utenti u ON s.utente_id = u.id
           WHERE s.token = ? AND s.expires_at > datetime('now') AND u.attivo = 1""",
        (token,)
    )
    if not sess:
        session.clear()
        return False

    # Store user in g
    g.user = {
        'id': sess['user_id'],
        'email': sess['email'],
        'nome': sess['nome'],
        'cognome': sess['cognome'],
        'ruolo': sess['ruolo'],
        'primo_accesso': sess['primo_accesso'],
        'divisione_default_id': sess['divisione_default_id'],
        'struttura_id': sess['struttura_id'],
    }

    # Popola dati struttura nella sessione
    struttura = None
    if g.user['ruolo'] == 'superadmin':
        # Il superadmin può impersonare una struttura tramite sessione
        struttura_impersonata_id = session.get('struttura_impersonata_id')
        if struttura_impersonata_id:
            struttura = query_one(
                "SELECT * FROM strutture WHERE id = ? AND attiva = 1",
                (struttura_impersonata_id,)
            )
            if struttura is None:
                session.pop('struttura_impersonata_id', None)
    elif g.user['ruolo'] == 'tecnico':
        struttura_impersonata_id = session.get('struttura_impersonata_id')
        if struttura_impersonata_id:
            allowed = query_one(
                "SELECT struttura_id FROM tecnici_strutture WHERE tecnico_id = ? AND struttura_id = ?",
                (g.user['id'], struttura_impersonata_id)
            )
            if allowed:
                struttura = query_one(
                    "SELECT * FROM strutture WHERE id = ? AND attiva = 1",
                    (struttura_impersonata_id,)
                )
            if struttura is None:
                session.pop('struttura_impersonata_id', None)
    else:
        struttura_id = g.user.get('struttura_id')
        if struttura_id:
            struttura = query_one(
                "SELECT * FROM strutture WHERE id = ? AND attiva = 1",
                (struttura_id,)
            )

    g.struttura = struttura
    g.struttura_id = struttura['id'] if struttura else None
    g.struttura_nome = struttura['nome'] if struttura else None
    g.is_superadmin_impersonating = (
        g.user['ruolo'] == 'superadmin' and struttura is not None
    )

    # Load accessible divisions
    if g.user['ruolo'] == 'superadmin':
        # Superadmin: vede le divisioni della struttura impersonata, o nessuna
        if g.struttura_id:
            g.divisioni = query_all(
                "SELECT * FROM divisioni WHERE attiva = 1 AND struttura_id = ? ORDER BY nome",
                (g.struttura_id,)
            )
        else:
            g.divisioni = []
    elif g.user['ruolo'] in ('admin', 'tecnico'):
        struttura_id = g.struttura_id
        if struttura_id:
            g.divisioni = query_all(
                "SELECT * FROM divisioni WHERE attiva = 1 AND struttura_id = ? ORDER BY nome",
                (struttura_id,)
            )
        else:
            g.divisioni = []
    else:
        g.divisioni = query_all(
            """SELECT d.* FROM divisioni d
               JOIN utenti_divisioni ud ON d.id = ud.divisione_id
               WHERE ud.utente_id = ? AND d.attiva = 1
               ORDER BY d.nome""",
            (g.user['id'],)
        )

    # Set active division
    div_attiva_id = session.get('divisione_attiva_id')
    accessible_ids = [d['id'] for d in g.divisioni]

    if div_attiva_id and div_attiva_id in accessible_ids:
        g.divisione_attiva = next(d for d in g.divisioni if d['id'] == div_attiva_id)
    elif div_attiva_id == 'tutte' and g.user['ruolo'] in ('admin', 'superadmin', 'tecnico'):
        g.divisione_attiva = {'id': 'tutte', 'nome': 'Tutte le divisioni', 'colore': '#6b7280'}
    elif g.divisioni:
        if g.user['ruolo'] == 'tecnico':
            # Il tecnico vede tutte le divisioni per default
            g.divisione_attiva = {'id': 'tutte', 'nome': 'Tutte le divisioni', 'colore': '#6b7280'}
            session['divisione_attiva_id'] = 'tutte'
        else:
            # Default to first accessible division
            g.divisione_attiva = g.divisioni[0]
            session['divisione_attiva_id'] = g.divisioni[0]['id']
    else:
        g.divisione_attiva = None

    # Count deadline alerts (for badge in navbar)
    if g.divisione_attiva and g.divisione_attiva.get('id') != 'tutte':
        result = query_one(
            """SELECT COUNT(*) as cnt FROM prossime_scadenze
               WHERE divisione_id = ? AND priorita IN ('scaduto', 'urgente', 'attenzione')""",
            (g.divisione_attiva['id'],)
        )
    elif g.user['ruolo'] in ('admin', 'superadmin', 'tecnico') and g.struttura_id:
        result = query_one(
            """SELECT COUNT(*) as cnt FROM prossime_scadenze ps
               JOIN apparecchi a ON a.id = ps.apparecchio_id
               WHERE a.struttura_id = ? AND ps.priorita IN ('scaduto', 'urgente', 'attenzione')""",
            (g.struttura_id,)
        )
    elif g.user['ruolo'] in ('admin', 'superadmin', 'tecnico'):
        result = query_one(
            """SELECT COUNT(*) as cnt FROM prossime_scadenze
               WHERE priorita IN ('scaduto', 'urgente', 'attenzione')"""
        )
    else:
        ids = [d['id'] for d in g.divisioni]
        if ids:
            ph = ','.join('?' * len(ids))
            sql = ("SELECT COUNT(*) as cnt FROM prossime_scadenze"
                   " WHERE divisione_id IN (" + ph + ") AND priorita IN ('scaduto','urgente','attenzione')")
            result = query_one(sql, tuple(ids))
        else:
            result = None
    g.scadenze_alert_count = result['cnt'] if result else 0

    return True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page."""
    if request.method == 'GET':
        # If already logged in, redirect
        if session.get('token'):
            if _load_user_from_session():
                if g.user['primo_accesso']:
                    return redirect(url_for('auth.cambio_password'))
                return redirect(url_for('index'))
        return render_template('login.html')

    # POST: process login
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')

    if not email or not password:
        flash('Inserisci email e password.', 'danger')
        return render_template('login.html', email=email)

    import time as _time
    ip = request.remote_addr

    # Blocco per IP: 5 falliti negli ultimi 15 minuti.
    #
    # La finestra si calcola con l'orologio di SQLite, non con datetime.now():
    # created_at ha DEFAULT CURRENT_TIMESTAMP, che in SQLite e' UTC, mentre
    # datetime.now() e' l'ora locale. In Italia le due differiscono di un'ora
    # o due, e il confronto faceva risultare piu' vecchia della finestra anche
    # una riga appena scritta: il blocco non e' mai scattato, in nessuna delle
    # due forme, su nessuna installazione a est di Greenwich.
    tentativi_ip = query_one(
        """SELECT COUNT(*) as cnt FROM login_attempts
           WHERE ip_address = ? AND esito = 'fallito'
             AND created_at > datetime('now', '-15 minutes')""",
        (ip,)
    )
    if tentativi_ip and tentativi_ip['cnt'] >= 5:
        execute(
            "INSERT INTO login_attempts (ip_address, email, esito) VALUES (?, ?, 'bloccato')",
            (ip, email)
        )
        flash('Troppi tentativi falliti. Riprova tra 15 minuti.', 'danger')
        return render_template('login.html', email=email), 429

    # Blocco per email: 10 falliti da qualsiasi IP negli ultimi 30 minuti.
    # Stessa nota sull'orologio del blocco per IP qui sopra.
    tentativi_email = query_one(
        """SELECT COUNT(*) as cnt FROM login_attempts
           WHERE email = ? AND esito = 'fallito'
             AND created_at > datetime('now', '-30 minutes')""",
        (email,)
    )
    if tentativi_email and tentativi_email['cnt'] >= 10:
        execute(
            "INSERT INTO login_attempts (ip_address, email, esito) VALUES (?, ?, 'bloccato')",
            (ip, email)
        )
        flash('Account temporaneamente bloccato per troppi tentativi. Riprova tra 30 minuti.', 'danger')
        return render_template('login.html', email=email), 429

    # Find user
    user = query_one(
        "SELECT * FROM utenti WHERE email = ? AND attivo = 1",
        (email,)
    )

    from reset_password import azzera_reset, consuma_temporanea

    # La password temporanea del reset vale ACCANTO a quella attuale, non al
    # suo posto: si prova solo dopo che quella vera ha fallito. Chi non ha
    # chiesto nessun reset non passa mai di qui — reset_hash e' NULL.
    con_temporanea = False
    if user and not check_password_hash(user['password_hash'], password):
        con_temporanea = consuma_temporanea(get_db(), user['id'], password)

    if not user or not (con_temporanea
                        or check_password_hash(user['password_hash'], password)):
        execute(
            "INSERT INTO login_attempts (ip_address, email, esito) VALUES (?, ?, 'fallito')",
            (ip, email)
        )
        _time.sleep(1)  # Rallenta il brute force: 1 tentativo/sec per IP
        flash('Credenziali non valide.', 'danger')
        return render_template('login.html', email=email)

    if con_temporanea:
        # consuma_temporanea ha gia' messo primo_accesso = 1 e chiuso le altre
        # sessioni; la riga letta prima dice ancora il valore vecchio.
        log_attivita(user['id'], 'reset_password_usato', 'utenti', user['id'],
                     'Accesso con la password temporanea richiesta dalla schermata '
                     'di accesso', request.remote_addr, user['struttura_id'])
    else:
        # Entrato con la sua password: se aveva un reset in sospeso non ha piu'
        # motivo di restare valido, ne' qui ne' nella casella di chi l'ha
        # ricevuto.
        azzera_reset(get_db(), user['id'])

    # Create session
    config = current_app.config['APP_CONFIG']
    lifetime_hours = config.get('session_lifetime_hours', 8)
    token = str(uuid.uuid4())

    # Scadenza calcolata da SQLite, come il confronto che la legge:
    # _load_user_from_session e la pulizia dello scheduler usano entrambi
    # datetime('now'), cioe' UTC. Scritta con datetime.now(), la sessione
    # durava le ore configurate PIU' lo scarto del fuso orario.
    execute(
        """INSERT INTO sessioni (utente_id, token, expires_at)
           VALUES (?, ?, datetime('now', ?))""",
        (user['id'], token, '+{} hours'.format(int(lifetime_hours)))
    )

    # Update last access
    execute(
        "UPDATE utenti SET ultimo_accesso = datetime('now') WHERE id = ?",
        (user['id'],)
    )

    # Set Flask session
    session['token'] = token
    session.permanent = True

    # Log activity
    log_attivita(user['id'], 'login', 'utenti', user['id'],
                 f"Login da {request.remote_addr}", request.remote_addr)

    # Notifica aggiornamento versione al primo admin che logga
    if user['ruolo'] in ('admin', 'superadmin'):
        _check_version_notice()

    # Clear rate limit records on successful login
    execute(
        "INSERT INTO login_attempts (ip_address, email, esito) VALUES (?, ?, 'riuscito')",
        (ip, email)
    )
    execute(
        "DELETE FROM login_attempts WHERE ip_address = ? AND esito = 'fallito'",
        (ip,)
    )

    # Redirect based on primo_accesso. Chi e' entrato con la temporanea deve
    # sceglierne una nuova subito: la riga letta all'inizio diceva ancora 0.
    if user['primo_accesso'] or con_temporanea:
        return redirect(url_for('auth.cambio_password'))

    # Tecnico: seleziona struttura se non ancora impostata
    if user['ruolo'] == 'tecnico':
        strutture_assegnate = query_all(
            """SELECT s.id FROM strutture s
               JOIN tecnici_strutture ts ON s.id = ts.struttura_id
               WHERE ts.tecnico_id = ? AND s.attiva = 1""",
            (user['id'],)
        )
        if not strutture_assegnate:
            execute("DELETE FROM sessioni WHERE token = ?", (token,))
            session.clear()
            flash("Nessuna struttura assegnata. Contattare l'amministratore.", 'danger')
            return render_template('login.html', email=email)
        if len(strutture_assegnate) == 1:
            session['struttura_impersonata_id'] = strutture_assegnate[0]['id']
        else:
            return redirect(url_for('auth.tecnico_seleziona_struttura_page'))

    return redirect(url_for('index'))


@auth_bp.route('/password-dimenticata', methods=['GET', 'POST'])
def password_dimenticata():
    """Chiede una password temporanea per email.

    La risposta e' sempre la stessa, che l'indirizzo esista o no, sia attivo o
    no, sia cancellato o no. Non si dice se un account esiste: il progetto
    prevede di stare dietro un tunnel Cloudflare ed essere raggiungibile da
    fuori, e su Internet quella differenza e' l'elenco degli indirizzi validi
    su cui poi provare le password.
    """
    from email.mime.text import MIMEText

    from posta import invia, smtp_configurato
    from reset_password import (destinatario_valido, genera_temporanea,
                                messaggio_email, registra_reset,
                                registra_richiesta, troppe_richieste)

    config = current_app.config['APP_CONFIG']
    if not smtp_configurato(config):
        # Senza posta la funzione non ha come consegnare niente. Nel log resta
        # scritto perche', cosi' chi la cerca non la cerca a lungo.
        logger.warning("Richiesta di reset password ignorata: SMTP di sistema "
                       "non configurato.")
        flash("Il reset della password non e' disponibile: il server di posta "
              "non e' configurato. Rivolgiti all'amministratore.", 'warning')
        return redirect(url_for('auth.login'))

    if request.method == 'GET':
        return render_template('password_dimenticata.html')

    email = request.form.get('email', '').strip().lower()
    ip = request.remote_addr
    RISPOSTA = ("Se l'indirizzo e' registrato, riceverai a breve un'email con una "
                "password temporanea. Se non arriva nulla, controlla l'indirizzo "
                "inserito o rivolgiti all'amministratore.")

    if not email:
        flash('Inserisci il tuo indirizzo email.', 'danger')
        return render_template('password_dimenticata.html')

    db = get_db()

    # Il limite si guarda PRIMA di sapere se l'utente esiste: al contrario, il
    # tempo di risposta diverso fra indirizzo noto e ignoto rivelerebbe quello
    # che il messaggio unico nasconde.
    if troppe_richieste(db, ip, email):
        registra_richiesta(db, ip, email)
        db.commit()
        flash(RISPOSTA, 'info')
        return redirect(url_for('auth.login'))

    # Il commit va fatto SUBITO, non alla fine: le due uscite qui sotto
    # tornano prima di arrivarci, e senza questa riga la richiesta non
    # verrebbe contata proprio nei due casi che il limite deve fermare — le
    # ripetute su un indirizzo che non esiste.
    registra_richiesta(db, ip, email)
    db.commit()

    utente = destinatario_valido(db, email)
    if utente is None:  # nota: la riga del contatore e' gia' committata sopra
        # Nessuna voce in log_attivita: non c'e' un utente a cui legarla, e
        # scriverci dentro indirizzi forniti da chi passa vorrebbe dire
        # lasciare a un estraneo la penna sul registro di sistema. La riga in
        # login_attempts, che e' il posto fatto per questo, c'e' gia'.
        flash(RISPOSTA, 'info')
        return redirect(url_for('auth.login'))

    utente_id, nome, _cognome, indirizzo, struttura_id = utente
    temporanea = genera_temporanea()
    scadenza = registra_reset(db, utente_id, temporanea)
    oggetto, corpo = messaggio_email(nome, temporanea, scadenza)

    if invia(config, indirizzo, MIMEText(corpo, 'plain', 'utf-8')):
        log_attivita(utente_id, 'reset_password_richiesto', 'utenti', utente_id,
                     'Password temporanea inviata per email', ip, struttura_id)
    else:
        # L'email non e' partita: la temporanea non deve restare valida, o
        # resterebbe un reset aperto che nessuno ha in mano.
        from reset_password import azzera_reset
        azzera_reset(db, utente_id)
        logger.error("Password temporanea non spedita a %s: reset annullato.",
                     indirizzo)

    db.commit()
    flash(RISPOSTA, 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/logout')
def logout():
    """Logout and clear session."""
    token = session.get('token')
    if token:
        # Get user info for logging before clearing
        sess = query_one(
            "SELECT utente_id FROM sessioni WHERE token = ?", (token,)
        )
        if sess:
            log_attivita(sess['utente_id'], 'logout', 'utenti', sess['utente_id'],
                         None, request.remote_addr)
        execute("DELETE FROM sessioni WHERE token = ?", (token,))
    session.clear()
    flash('Logout effettuato.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/logout-ovunque', methods=['POST'])
@admin_required
def logout_ovunque():
    """Revoca tutte le sessioni di tutti gli utenti della struttura corrente (struttura-wide)."""
    if not g.struttura_id:
        flash('Nessuna struttura attiva.', 'warning')
        return redirect(url_for('index'))
    execute(
        "DELETE FROM sessioni WHERE utente_id IN (SELECT id FROM utenti WHERE struttura_id = ?)",
        (g.struttura_id,)
    )
    log_attivita(g.user['id'], 'logout_ovunque', 'struttura', g.struttura_id,
                 None, request.remote_addr, struttura_id=g.struttura_id)
    session.clear()
    flash('Tutte le sessioni della struttura sono state revocate, inclusa la tua.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/cambio-password', methods=['GET', 'POST'])
def cambio_password():
    """Force password change on first access."""
    token = session.get('token')
    if not token or not _load_user_from_session():
        return redirect(url_for('auth.login'))

    if request.method == 'GET':
        return render_template('cambio_password.html')

    # POST: process password change
    password_attuale = request.form.get('password_attuale', '')
    nuova_password = request.form.get('nuova_password', '')
    conferma_password = request.form.get('conferma_password', '')

    errors = {}

    # Validate current password
    user = query_one("SELECT * FROM utenti WHERE id = ?", (g.user['id'],))
    if not check_password_hash(user['password_hash'], password_attuale):
        errors['password_attuale'] = 'Password attuale non corretta.'

    # Validate new password
    if len(nuova_password) < 8:
        errors['nuova_password'] = 'La password deve essere di almeno 8 caratteri.'
    elif not any(c.isupper() for c in nuova_password):
        errors['nuova_password'] = 'La password deve contenere almeno una lettera maiuscola.'
    elif not any(c.isdigit() for c in nuova_password):
        errors['nuova_password'] = 'La password deve contenere almeno un numero.'

    if nuova_password != conferma_password:
        errors['conferma_password'] = 'Le password non coincidono.'

    if errors:
        return render_template('cambio_password.html', errors=errors)

    # Update password
    new_hash = generate_password_hash(nuova_password)
    execute(
        """UPDATE utenti SET password_hash = ?, primo_accesso = 0,
                  updated_at = datetime('now')
           WHERE id = ?""",
        (new_hash, g.user['id'])
    )

    # Delete all sessions for this user (force re-login)
    execute("DELETE FROM sessioni WHERE utente_id = ?", (g.user['id'],))
    session.clear()

    log_attivita(g.user['id'], 'cambio_password', 'utenti', g.user['id'],
                 None, request.remote_addr)

    flash('Password modificata con successo. Effettua il login con la nuova password.', 'success')
    return redirect(url_for('auth.login'))


@auth_bp.route('/divisione/<divisione_id>')
@login_required
def cambia_divisione(divisione_id):
    """Switch the active division."""
    if divisione_id == 'tutte' and g.user['ruolo'] in ('admin', 'superadmin', 'tecnico'):
        session['divisione_attiva_id'] = 'tutte'
    else:
        try:
            div_id = int(divisione_id)
            accessible_ids = [d['id'] for d in g.divisioni]
            if div_id in accessible_ids:
                session['divisione_attiva_id'] = div_id
        except (ValueError, TypeError):
            pass

    # Redirect back to referrer or dashboard (safe: validate same host)
    ref = request.referrer
    if ref:
        from urllib.parse import urlparse
        ref_host = urlparse(ref).netloc
        own_host = urlparse(request.host_url).netloc
        if ref_host != own_host:
            ref = None
    return redirect(ref or url_for('index'))


@auth_bp.route('/impersona/<int:struttura_id>')
@superadmin_required
def impersona_struttura(struttura_id):
    """Superadmin entra nel contesto di una struttura specifica."""
    struttura = query_one(
        "SELECT id, nome FROM strutture WHERE id = ? AND attiva = 1", (struttura_id,)
    )
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index'))
    session['struttura_impersonata_id'] = struttura_id
    log_attivita(
        g.user['id'], 'impersonazione', 'struttura', struttura_id,
        dettagli=f"Superadmin impersona struttura {struttura_id}",
        ip_address=request.remote_addr,
        struttura_id=struttura_id
    )
    flash(f'Stai operando come: {struttura["nome"]}', 'info')
    return redirect(url_for('index'))


@auth_bp.route('/esci-impersonazione')
@superadmin_required
def esci_impersonazione():
    """Superadmin torna alla vista globale."""
    session.pop('struttura_impersonata_id', None)
    return redirect(url_for('strutture.index'))


@auth_bp.route('/tecnico/seleziona-struttura')
@login_required
def tecnico_seleziona_struttura_page():
    """Pagina di selezione struttura per tecnico con più strutture assegnate."""
    if g.user['ruolo'] != 'tecnico':
        return redirect(url_for('index'))
    strutture = query_all(
        """SELECT s.id, s.nome FROM strutture s
           JOIN tecnici_strutture ts ON s.id = ts.struttura_id
           WHERE ts.tecnico_id = ? AND s.attiva = 1
           ORDER BY s.nome""",
        (g.user['id'],)
    )
    return render_template('auth/seleziona_struttura_tecnico.html', strutture=strutture)


@auth_bp.route('/tecnico/struttura/<int:struttura_id>')
@login_required
def tecnico_seleziona_struttura(struttura_id):
    """Tecnico imposta la struttura attiva (verifica accesso)."""
    if g.user['ruolo'] != 'tecnico':
        flash('Accesso non autorizzato.', 'danger')
        return redirect(url_for('index'))
    allowed = query_one(
        "SELECT struttura_id FROM tecnici_strutture WHERE tecnico_id = ? AND struttura_id = ?",
        (g.user['id'], struttura_id)
    )
    if not allowed:
        flash('Struttura non assegnata.', 'danger')
        return redirect(url_for('auth.tecnico_seleziona_struttura_page'))
    struttura = query_one(
        "SELECT nome FROM strutture WHERE id = ? AND attiva = 1", (struttura_id,)
    )
    if not struttura:
        flash('Struttura non trovata o non attiva.', 'danger')
        return redirect(url_for('auth.tecnico_seleziona_struttura_page'))
    session['struttura_impersonata_id'] = struttura_id
    return redirect(url_for('index'))
