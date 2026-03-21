"""
MedInventory - Authentication module
Handles login, logout, password change, session management, and access decorators.
"""

import json
import os
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, g, current_app
)
from werkzeug.security import check_password_hash, generate_password_hash

from models import query_one, query_all, execute, log_attivita

auth_bp = Blueprint('auth', __name__)


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
    """Decorator: require admin role. Must be used after @login_required."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if g.user['ruolo'] != 'admin':
            flash('Accesso non autorizzato.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


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
                  u.attivo, u.primo_accesso, u.divisione_default_id
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
    }

    # Load accessible divisions
    if g.user['ruolo'] == 'admin':
        g.divisioni = query_all(
            "SELECT * FROM divisioni WHERE attiva = 1 ORDER BY nome"
        )
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
    elif div_attiva_id == 'tutte' and g.user['ruolo'] == 'admin':
        g.divisione_attiva = {'id': 'tutte', 'nome': 'Tutte le divisioni', 'colore': '#6b7280'}
    elif g.divisioni:
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
    else:
        result = query_one(
            """SELECT COUNT(*) as cnt FROM prossime_scadenze
               WHERE priorita IN ('scaduto', 'urgente', 'attenzione')"""
        )
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

    # Find user
    user = query_one(
        "SELECT * FROM utenti WHERE email = ? AND attivo = 1",
        (email,)
    )

    if not user or not check_password_hash(user['password_hash'], password):
        flash('Credenziali non valide.', 'danger')
        return render_template('login.html', email=email)

    # Create session
    config = current_app.config['APP_CONFIG']
    lifetime_hours = config.get('session_lifetime_hours', 8)
    token = str(uuid.uuid4())
    expires_at = datetime.now() + timedelta(hours=lifetime_hours)

    execute(
        """INSERT INTO sessioni (utente_id, token, expires_at)
           VALUES (?, ?, ?)""",
        (user['id'], token, expires_at.strftime('%Y-%m-%d %H:%M:%S'))
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
    if user['ruolo'] == 'admin':
        _check_version_notice()

    # Redirect based on primo_accesso
    if user['primo_accesso']:
        return redirect(url_for('auth.cambio_password'))

    return redirect(url_for('index'))


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
    if divisione_id == 'tutte' and g.user['ruolo'] == 'admin':
        session['divisione_attiva_id'] = 'tutte'
    else:
        try:
            div_id = int(divisione_id)
            accessible_ids = [d['id'] for d in g.divisioni]
            if div_id in accessible_ids:
                session['divisione_attiva_id'] = div_id
        except (ValueError, TypeError):
            pass

    # Redirect back to referrer or dashboard
    return redirect(request.referrer or url_for('index'))
