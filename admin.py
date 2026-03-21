"""
MedInventory - Admin Blueprint
User management, division management, configuration editor, email config.
All routes require admin role.
"""

import json
import os

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, g, current_app, session as flask_session
)
from werkzeug.security import generate_password_hash

from auth import admin_required
from models import query_one, query_all, execute, log_attivita

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

ANTHROPIC_MODELS = [
    ('claude-opus-4-6',            'Claude Opus 4.6 — Massima potenza'),
    ('claude-sonnet-4-6',          'Claude Sonnet 4.6 — Bilanciato (consigliato import)'),
    ('claude-sonnet-4-20250514',   'Claude Sonnet 4 — Maggio 2025'),
    ('claude-haiku-4-5-20251001',  'Claude Haiku 4.5 — Veloce (consigliato email)'),
    ('claude-3-5-sonnet-20241022', 'Claude 3.5 Sonnet — Ottobre 2024'),
    ('claude-3-haiku-20240307',    'Claude 3 Haiku — Legacy veloce'),
]

AI_PROVIDERS = [
    ('anthropic',          'Anthropic Claude (Cloud)'),
    ('ollama',             'Ollama (Locale)'),
    ('lmstudio',           'LM Studio (Locale)'),
    ('openai_compatible',  'Altro OpenAI-compatibile'),
]

AI_PROVIDER_DEFAULTS = {
    'ollama':            'http://localhost:11434',
    'lmstudio':          'http://localhost:1234',
    'openai_compatible': 'http://localhost:8080',
}


# ============================================================================
# USER MANAGEMENT
# ============================================================================

@admin_bp.route('/utenti')
@admin_required
def utenti():
    """List all users."""
    users = query_all(
        """SELECT u.*, GROUP_CONCAT(d.nome, ', ') as divisioni_nomi
           FROM utenti u
           LEFT JOIN utenti_divisioni ud ON u.id = ud.utente_id
           LEFT JOIN divisioni d ON ud.divisione_id = d.id
           GROUP BY u.id
           ORDER BY u.cognome, u.nome"""
    )
    return render_template('admin/utenti.html', utenti=users)


@admin_bp.route('/utenti/nuovo', methods=['GET', 'POST'])
@admin_required
def utente_nuovo():
    """Create a new user."""
    divisioni = query_all("SELECT * FROM divisioni WHERE attiva = 1 ORDER BY nome")

    if request.method == 'GET':
        return render_template('admin/utente_form.html',
                               utente=None, errors={}, form_data={},
                               divisioni=divisioni)

    # POST: validate and create
    errors = {}
    form = request.form

    nome = form.get('nome', '').strip()
    cognome = form.get('cognome', '').strip()
    email = form.get('email', '').strip().lower()
    ruolo = form.get('ruolo', 'utente')
    password = form.get('password', '').strip()
    divisioni_sel = form.getlist('divisioni')

    if not nome:
        errors['nome'] = 'Il nome è obbligatorio.'
    if not cognome:
        errors['cognome'] = 'Il cognome è obbligatorio.'
    if not email:
        errors['email'] = "L'email è obbligatoria."
    elif query_one("SELECT id FROM utenti WHERE email = ?", (email,)):
        errors['email'] = 'Questo indirizzo email è già registrato.'
    if not password or len(password) < 8:
        errors['password'] = 'La password deve essere di almeno 8 caratteri.'
    if ruolo not in ('admin', 'utente'):
        ruolo = 'utente'

    if errors:
        return render_template('admin/utente_form.html',
                               utente=None, errors=errors, form_data=form,
                               divisioni=divisioni)

    password_hash = generate_password_hash(password)
    cursor = execute(
        """INSERT INTO utenti (email, password_hash, nome, cognome, ruolo, primo_accesso)
           VALUES (?, ?, ?, ?, ?, 1)""",
        (email, password_hash, nome, cognome, ruolo)
    )
    user_id = cursor.lastrowid

    # Assign divisions
    for div_id in divisioni_sel:
        try:
            execute(
                """INSERT INTO utenti_divisioni (utente_id, divisione_id, ruolo_divisione)
                   VALUES (?, ?, ?)""",
                (user_id, int(div_id), ruolo)
            )
        except Exception:
            pass

    log_attivita(g.user['id'], 'creazione', 'utenti', user_id,
                 f"Creato utente: {nome} {cognome} ({email})", request.remote_addr)

    flash(f'Utente {nome} {cognome} creato con successo. Password temporanea: {password}', 'success')
    return redirect(url_for('admin.utenti'))


@admin_bp.route('/utenti/<int:id>/modifica', methods=['GET', 'POST'])
@admin_required
def utente_modifica(id):
    """Edit a user."""
    utente = query_one("SELECT * FROM utenti WHERE id = ?", (id,))
    if not utente:
        flash('Utente non trovato.', 'danger')
        return redirect(url_for('admin.utenti'))

    divisioni = query_all("SELECT * FROM divisioni WHERE attiva = 1 ORDER BY nome")
    utente_divisioni = query_all(
        "SELECT divisione_id FROM utenti_divisioni WHERE utente_id = ?", (id,)
    )
    utente_div_ids = [str(ud['divisione_id']) for ud in utente_divisioni]

    if request.method == 'GET':
        form_data = dict(utente)
        form_data['divisioni'] = utente_div_ids
        return render_template('admin/utente_form.html',
                               utente=utente, errors={}, form_data=form_data,
                               divisioni=divisioni)

    # POST
    errors = {}
    form = request.form

    nome = form.get('nome', '').strip()
    cognome = form.get('cognome', '').strip()
    email = form.get('email', '').strip().lower()
    ruolo = form.get('ruolo', 'utente')
    divisioni_sel = form.getlist('divisioni')

    if not nome:
        errors['nome'] = 'Il nome è obbligatorio.'
    if not cognome:
        errors['cognome'] = 'Il cognome è obbligatorio.'
    if not email:
        errors['email'] = "L'email è obbligatoria."
    else:
        existing = query_one("SELECT id FROM utenti WHERE email = ? AND id != ?", (email, id))
        if existing:
            errors['email'] = 'Questo indirizzo email è già registrato.'
    if ruolo not in ('admin', 'utente'):
        ruolo = 'utente'

    if errors:
        form_data = dict(form)
        form_data['divisioni'] = divisioni_sel
        return render_template('admin/utente_form.html',
                               utente=utente, errors=errors, form_data=form_data,
                               divisioni=divisioni)

    execute(
        """UPDATE utenti SET nome=?, cognome=?, email=?, ruolo=?, updated_at=datetime('now')
           WHERE id=?""",
        (nome, cognome, email, ruolo, id)
    )

    # Update divisions: delete all, re-insert
    execute("DELETE FROM utenti_divisioni WHERE utente_id = ?", (id,))
    for div_id in divisioni_sel:
        try:
            execute(
                """INSERT INTO utenti_divisioni (utente_id, divisione_id, ruolo_divisione)
                   VALUES (?, ?, ?)""",
                (id, int(div_id), ruolo)
            )
        except Exception:
            pass

    log_attivita(g.user['id'], 'modifica', 'utenti', id,
                 f"Modificato utente: {nome} {cognome}", request.remote_addr)

    flash('Utente aggiornato.', 'success')
    return redirect(url_for('admin.utenti'))


@admin_bp.route('/utenti/<int:id>/toggle', methods=['POST'])
@admin_required
def utente_toggle(id):
    """Toggle user active/inactive."""
    utente = query_one("SELECT * FROM utenti WHERE id = ?", (id,))
    if not utente:
        flash('Utente non trovato.', 'danger')
        return redirect(url_for('admin.utenti'))

    # Don't allow deactivating yourself
    if utente['id'] == g.user['id']:
        flash('Non puoi disattivare il tuo account.', 'warning')
        return redirect(url_for('admin.utenti'))

    new_status = 0 if utente['attivo'] else 1
    execute("UPDATE utenti SET attivo = ?, updated_at = datetime('now') WHERE id = ?",
            (new_status, id))

    action = 'attivato' if new_status else 'disattivato'
    log_attivita(g.user['id'], action, 'utenti', id,
                 f"Utente {action}: {utente['nome']} {utente['cognome']}", request.remote_addr)

    flash(f"Utente {utente['nome']} {utente['cognome']} {action}.", 'success')
    return redirect(url_for('admin.utenti'))


@admin_bp.route('/utenti/<int:id>/reset-password', methods=['POST'])
@admin_required
def utente_reset_password(id):
    """Reset user password to a temporary one."""
    utente = query_one("SELECT * FROM utenti WHERE id = ?", (id,))
    if not utente:
        flash('Utente non trovato.', 'danger')
        return redirect(url_for('admin.utenti'))

    import secrets
    temp_password = secrets.token_urlsafe(10)
    password_hash = generate_password_hash(temp_password)

    execute(
        """UPDATE utenti SET password_hash=?, primo_accesso=1, updated_at=datetime('now')
           WHERE id=?""",
        (password_hash, id)
    )
    # Delete all sessions
    execute("DELETE FROM sessioni WHERE utente_id = ?", (id,))

    log_attivita(g.user['id'], 'reset_password', 'utenti', id,
                 f"Reset password per: {utente['nome']} {utente['cognome']}", request.remote_addr)

    flash(f"Password resettata per {utente['nome']} {utente['cognome']}. "
          f"Nuova password temporanea: {temp_password}", 'warning')
    return redirect(url_for('admin.utenti'))


# ============================================================================
# DIVISION MANAGEMENT
# ============================================================================

@admin_bp.route('/divisioni')
@admin_required
def divisioni():
    """List all divisions."""
    divs = query_all(
        """SELECT d.*,
                  (SELECT COUNT(*) FROM apparecchi a WHERE a.divisione_id = d.id AND a.stato != 'dismesso') as num_apparecchi,
                  (SELECT COUNT(*) FROM utenti_divisioni ud WHERE ud.divisione_id = d.id) as num_utenti
           FROM divisioni d ORDER BY d.nome"""
    )
    return render_template('admin/divisioni.html', divisioni=divs)


@admin_bp.route('/divisioni/nuova', methods=['POST'])
@admin_required
def divisione_nuova():
    """Create a new division."""
    nome = request.form.get('nome', '').strip()
    codice = request.form.get('codice', '').strip().upper()
    colore = request.form.get('colore', '#0ea5e9')
    descrizione = request.form.get('descrizione', '').strip()

    if not nome or not codice:
        flash('Nome e codice sono obbligatori.', 'danger')
        return redirect(url_for('admin.divisioni'))

    try:
        cursor = execute(
            """INSERT INTO divisioni (nome, codice, colore, descrizione)
               VALUES (?, ?, ?, ?)""",
            (nome, codice, colore, descrizione or None)
        )
        log_attivita(g.user['id'], 'creazione', 'divisioni', cursor.lastrowid,
                     f"Creata divisione: {nome}", request.remote_addr)
        flash(f'Divisione "{nome}" creata.', 'success')
    except Exception as e:
        flash(f'Errore: nome o codice già esistente.', 'danger')

    return redirect(url_for('admin.divisioni'))


@admin_bp.route('/divisioni/<int:id>/modifica', methods=['POST'])
@admin_required
def divisione_modifica(id):
    """Edit a division."""
    nome = request.form.get('nome', '').strip()
    codice = request.form.get('codice', '').strip().upper()
    colore = request.form.get('colore', '#0ea5e9')
    descrizione = request.form.get('descrizione', '').strip()

    if not nome or not codice:
        flash('Nome e codice sono obbligatori.', 'danger')
        return redirect(url_for('admin.divisioni'))

    try:
        execute(
            """UPDATE divisioni SET nome=?, codice=?, colore=?, descrizione=?,
                      updated_at=datetime('now')
               WHERE id=?""",
            (nome, codice, colore, descrizione or None, id)
        )
        log_attivita(g.user['id'], 'modifica', 'divisioni', id,
                     f"Modificata divisione: {nome}", request.remote_addr)
        flash(f'Divisione "{nome}" aggiornata.', 'success')
    except Exception:
        flash('Errore: nome o codice già esistente.', 'danger')

    return redirect(url_for('admin.divisioni'))


@admin_bp.route('/divisioni/<int:id>/toggle', methods=['POST'])
@admin_required
def divisione_toggle(id):
    """Toggle division active/inactive."""
    div = query_one("SELECT * FROM divisioni WHERE id = ?", (id,))
    if not div:
        flash('Divisione non trovata.', 'danger')
        return redirect(url_for('admin.divisioni'))

    new_status = 0 if div['attiva'] else 1
    execute("UPDATE divisioni SET attiva = ?, updated_at = datetime('now') WHERE id = ?",
            (new_status, id))

    action = 'attivata' if new_status else 'disattivata'
    flash(f"Divisione \"{div['nome']}\" {action}.", 'success')
    return redirect(url_for('admin.divisioni'))


# ============================================================================
# CONFIGURATION
# ============================================================================

@admin_bp.route('/configurazione', methods=['GET', 'POST'])
@admin_required
def configurazione():
    """Edit application configuration."""
    from app import load_config, save_config

    config = load_config()

    if request.method == 'POST':
        # Update config from form
        config['app_name'] = request.form.get('app_name', 'MedInventory').strip()
        config['organization'] = request.form.get('organization', 'Studio Bergamaschi').strip()
        config['structure_name'] = request.form.get('structure_name', '').strip()

        port = request.form.get('port', '5000').strip()
        try:
            config['port'] = int(port)
        except ValueError:
            pass

        lifetime = request.form.get('session_lifetime_hours', '8').strip()
        try:
            config['session_lifetime_hours'] = int(lifetime)
        except ValueError:
            pass

        # AI provider
        config['ai_provider'] = request.form.get('ai_provider', 'anthropic').strip()

        # API key (only update if not empty placeholder)
        api_key = request.form.get('anthropic_api_key', '').strip()
        if api_key and api_key != '••••••••':
            config['anthropic_api_key'] = api_key

        config['ai_import_model'] = request.form.get('ai_import_model', config.get('ai_import_model', '')).strip()
        config['ai_email_model'] = request.form.get('ai_email_model', config.get('ai_email_model', '')).strip()
        config['ai_verifiche_model'] = request.form.get('ai_verifiche_model', config.get('ai_verifiche_model', '')).strip()

        # Local AI settings
        config['ai_local_base_url'] = request.form.get('ai_local_base_url', '').strip()
        config['ai_local_model'] = request.form.get('ai_local_model', '').strip()

        interval = request.form.get('email_check_interval_minutes', '15').strip()
        try:
            config['email_check_interval_minutes'] = int(interval)
        except ValueError:
            pass

        retention = request.form.get('backup_retention', '4').strip()
        try:
            config['backup_retention'] = int(retention)
        except ValueError:
            pass

        # IMAP email monitoring
        config['imap_enabled'] = bool(request.form.get('imap_enabled'))
        config['imap_account'] = request.form.get('imap_account', '').strip()
        imap_password = request.form.get('imap_password', '').strip()
        if imap_password and imap_password != '••••••••':
            config['imap_password'] = imap_password
        config['imap_server'] = request.form.get('imap_server', '').strip()
        imap_port = request.form.get('imap_port', '993').strip()
        try:
            config['imap_port'] = int(imap_port)
        except ValueError:
            pass
        config['imap_ssl'] = bool(request.form.get('imap_ssl'))

        # Alert email / SMTP
        config['alert_email_enabled'] = bool(request.form.get('alert_email_enabled'))
        config['alert_email_to'] = request.form.get('alert_email_to', '').strip()
        config['smtp_host'] = request.form.get('smtp_host', '').strip()
        smtp_port = request.form.get('smtp_port', '587').strip()
        try:
            config['smtp_port'] = int(smtp_port)
        except ValueError:
            pass
        config['smtp_user'] = request.form.get('smtp_user', '').strip()
        smtp_password = request.form.get('smtp_password', '').strip()
        if smtp_password and smtp_password != '••••••••':
            config['smtp_password'] = smtp_password
        config['smtp_use_tls'] = bool(request.form.get('smtp_use_tls'))

        save_config(config)

        log_attivita(g.user['id'], 'modifica', 'configurazione', None,
                     'Configurazione aggiornata', request.remote_addr)

        flash('Configurazione salvata. Alcune modifiche richiedono il riavvio.', 'success')
        return redirect(url_for('admin.configurazione'))

    # Mask sensitive fields for display
    display_config = dict(config)
    if display_config.get('anthropic_api_key'):
        key = display_config['anthropic_api_key']
        display_config['anthropic_api_key_masked'] = key[:8] + '••••••••' + key[-4:] if len(key) > 12 else '••••••••'
    else:
        display_config['anthropic_api_key_masked'] = ''

    display_config['imap_password_set'] = bool(display_config.get('imap_password'))
    display_config['smtp_password_set'] = bool(display_config.get('smtp_password'))

    return render_template('admin/configurazione.html', config=display_config,
                           anthropic_models=ANTHROPIC_MODELS,
                           ai_providers=AI_PROVIDERS,
                           ai_provider_defaults=AI_PROVIDER_DEFAULTS)


# ============================================================================
# AI LOCAL MODELS PROXY
# ============================================================================

@admin_bp.route('/ai-models', methods=['POST'])
@admin_required
def ai_models_proxy():
    """Proxy request to local AI server to fetch available models (avoids CORS)."""
    from flask import jsonify
    import httpx

    base_url = request.json.get('base_url', '').strip().rstrip('/')
    if not base_url:
        return jsonify({'error': 'URL non specificato'}), 400

    models = []

    # Try OpenAI-compatible endpoint first (/v1/models)
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(f"{base_url}/v1/models")
            r.raise_for_status()
            data = r.json()
            models = [m.get('id') or m.get('name') for m in (data.get('data') or data.get('models') or [])]
            if models:
                return jsonify({'models': models})
    except Exception:
        pass

    # Fallback: Ollama native API (/api/tags)
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(f"{base_url}/api/tags")
            r.raise_for_status()
            data = r.json()
            models = [m.get('name') for m in (data.get('models') or [])]
            if models:
                return jsonify({'models': models})
    except Exception:
        pass

    if not models:
        return jsonify({'error': 'Nessun modello trovato o server non raggiungibile'}), 502

    return jsonify({'models': models})


# ============================================================================
# EMAIL CONFIG
# ============================================================================

@admin_bp.route('/email-config')
@admin_required
def email_config():
    """Redirect to unified configurazione page (IMAP config moved there)."""
    return redirect(url_for('admin.configurazione') + '#email-imap')


@admin_bp.route('/email-config/nuova', methods=['POST'])
@admin_required
def email_config_nuova():
    """Create email config for a division."""
    divisione_id = request.form.get('divisione_id')
    email_account = request.form.get('email_account', '').strip()
    email_password = request.form.get('email_password', '').strip()
    imap_server = request.form.get('imap_server', '').strip()
    imap_port = request.form.get('imap_port', '993').strip()

    if not email_account or not imap_server:
        flash('Account email e server IMAP sono obbligatori.', 'danger')
        return redirect(url_for('admin.email_config'))

    # Encrypt password
    from email_monitor import get_fernet
    config = current_app.config['APP_CONFIG']
    f = get_fernet(config)
    encrypted_password = f.encrypt(email_password.encode()).decode()

    try:
        execute(
            """INSERT INTO email_config
               (divisione_id, email_account, email_password_encrypted, imap_server, imap_port)
               VALUES (?, ?, ?, ?, ?)""",
            (int(divisione_id) if divisione_id else None,
             email_account, encrypted_password, imap_server, int(imap_port))
        )
        flash('Configurazione email creata.', 'success')
    except Exception as e:
        flash(f'Errore nella creazione: {str(e)}', 'danger')

    return redirect(url_for('admin.email_config'))


@admin_bp.route('/email-config/<int:id>/toggle', methods=['POST'])
@admin_required
def email_config_toggle(id):
    """Toggle email config active/inactive."""
    ec = query_one("SELECT * FROM email_config WHERE id = ?", (id,))
    if not ec:
        flash('Configurazione non trovata.', 'danger')
        return redirect(url_for('admin.email_config'))

    new_status = 0 if ec['attivo'] else 1
    execute("UPDATE email_config SET attivo = ?, updated_at = datetime('now') WHERE id = ?",
            (new_status, id))

    action = 'attivata' if new_status else 'disattivata'
    flash(f"Configurazione email {action}.", 'success')
    return redirect(url_for('admin.email_config'))


# ============================================================================
# BACKUP MANAGEMENT
# ============================================================================

@admin_bp.route('/backup')
@admin_required
def backup():
    """Backup management page."""
    from backup_service import list_backups

    backups_path = current_app.config['BACKUPS_PATH']
    db_path = current_app.config['DATABASE_PATH']
    config = current_app.config['APP_CONFIG']

    backups = list_backups(backups_path)

    # Database size
    db_size = '?'
    if os.path.exists(db_path):
        size_bytes = os.path.getsize(db_path)
        if size_bytes < 1024:
            db_size = f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            db_size = f"{size_bytes / 1024:.1f} KB"
        else:
            db_size = f"{size_bytes / (1024 * 1024):.1f} MB"

    return render_template('admin/backup.html',
                           backups=backups,
                           db_size=db_size,
                           retention=config.get('backup_retention', 4))


@admin_bp.route('/backup/crea', methods=['POST'])
@admin_required
def backup_crea():
    """Create a manual backup."""
    from backup_service import create_backup

    try:
        db_path = current_app.config['DATABASE_PATH']
        backups_path = current_app.config['BACKUPS_PATH']
        config = current_app.config['APP_CONFIG']
        retention = config.get('backup_retention', 4)

        result = create_backup(db_path, backups_path, retention)

        log_attivita(g.user['id'], 'backup_creazione', 'backup', None,
                     f"Backup creato: {result['filename']}", request.remote_addr)

        flash(f"Backup creato: {result['filename']} ({result['size'] / 1024:.1f} KB)", 'success')
    except Exception as e:
        flash(f"Errore durante il backup: {str(e)}", 'danger')

    return redirect(url_for('admin.backup'))


@admin_bp.route('/backup/<filename>/scarica')
@admin_required
def backup_scarica(filename):
    """Download a backup file."""
    from flask import send_from_directory
    from werkzeug.utils import safe_join

    backups_path = current_app.config['BACKUPS_PATH']
    # Security: ensure the filename is valid
    if not filename.startswith('medinventory_backup_') or not filename.endswith('.sqlite'):
        flash('Nome file non valido.', 'danger')
        return redirect(url_for('admin.backup'))

    return send_from_directory(backups_path, filename, as_attachment=True)


@admin_bp.route('/backup/<filename>/ripristina', methods=['POST'])
@admin_required
def backup_ripristina(filename):
    """Restore database from a backup."""
    from backup_service import restore_backup

    backups_path = current_app.config['BACKUPS_PATH']
    db_path = current_app.config['DATABASE_PATH']

    if not filename.startswith('medinventory_backup_') or not filename.endswith('.sqlite'):
        flash('Nome file non valido.', 'danger')
        return redirect(url_for('admin.backup'))

    backup_path = os.path.join(backups_path, filename)

    try:
        restore_backup(backup_path, db_path)

        log_attivita(g.user['id'], 'backup_ripristino', 'backup', None,
                     f"Ripristinato da: {filename}", request.remote_addr)

        flash(f"Database ripristinato da {filename}. Riavvia l'applicazione per applicare le modifiche.", 'warning')
    except Exception as e:
        flash(f"Errore durante il ripristino: {str(e)}", 'danger')

    return redirect(url_for('admin.backup'))


@admin_bp.route('/backup/<filename>/elimina', methods=['POST'])
@admin_required
def backup_elimina(filename):
    """Delete a backup file."""
    from backup_service import delete_backup

    backups_path = current_app.config['BACKUPS_PATH']

    if not filename.startswith('medinventory_backup_') or not filename.endswith('.sqlite'):
        flash('Nome file non valido.', 'danger')
        return redirect(url_for('admin.backup'))

    backup_path = os.path.join(backups_path, filename)

    try:
        delete_backup(backup_path)
        flash(f"Backup {filename} eliminato.", 'info')
    except Exception as e:
        flash(f"Errore durante l'eliminazione: {str(e)}", 'danger')

    return redirect(url_for('admin.backup'))


@admin_bp.route('/reset-database', methods=['POST'])
@admin_required
def reset_database():
    """Cancella tutto il database, crea un backup automatico e reinizializza con i dati default."""
    from backup_service import create_backup
    from models import close_db, init_db, apply_schema_updates, execute as db_execute
    from werkzeug.security import generate_password_hash

    db_path = current_app.config['DATABASE_PATH']
    backups_path = current_app.config['BACKUPS_PATH']
    config = current_app.config['APP_CONFIG']
    retention = config.get('backup_retention', 4)

    try:
        # 1. Backup automatico prima di qualsiasi modifica
        backup_result = create_backup(db_path, backups_path, retention)

        # 2. Chiudi la connessione corrente al DB
        close_db()

        # 3. Elimina il file DB e i journal WAL/SHM
        for suffix in ('', '-wal', '-shm'):
            fpath = db_path + suffix
            if os.path.exists(fpath):
                os.remove(fpath)

        # 4. Reinizializza lo schema da schema.sql
        init_db()
        apply_schema_updates()

        # 5. Seed: 2 divisioni + utente admin predefinito
        c = db_execute(
            "INSERT INTO divisioni (nome, codice, colore, descrizione) VALUES (?,?,?,?)",
            ('Divisione 1', 'DIV1', '#0ea5e9', 'Prima divisione (rinominare da pannello admin)')
        )
        div1_id = c.lastrowid
        c = db_execute(
            "INSERT INTO divisioni (nome, codice, colore, descrizione) VALUES (?,?,?,?)",
            ('Divisione 2', 'DIV2', '#10b981', 'Seconda divisione (rinominare da pannello admin)')
        )
        div2_id = c.lastrowid

        password_hash = generate_password_hash('admin123')
        c = db_execute(
            """INSERT INTO utenti (email, password_hash, nome, cognome, ruolo, primo_accesso)
               VALUES (?,?,?,?,?,1)""",
            ('admin@medinventory.local', password_hash, 'Amministratore', 'Sistema', 'admin')
        )
        admin_id = c.lastrowid

        for div_id in (div1_id, div2_id):
            db_execute(
                "INSERT INTO utenti_divisioni (utente_id, divisione_id, ruolo_divisione) VALUES (?,?,?)",
                (admin_id, div_id, 'admin')
            )

        # 6. Invalida la sessione corrente e reindirizza al login
        flask_session.clear()

        flash(
            f'Database azzerato. Backup salvato: <strong>{backup_result["filename"]}</strong>. '
            f'Accedi con <code>admin@medinventory.local</code> / <code>admin123</code>.',
            'warning'
        )
        return redirect(url_for('auth.login'))

    except Exception as e:
        flash(f'Errore durante il reset del database: {str(e)}', 'danger')
        return redirect(url_for('admin.configurazione'))


@admin_bp.route('/reset-parziale', methods=['POST'])
@admin_required
def reset_parziale():
    """Cancella tutti i dati di inventario mantenendo utenti e divisioni."""
    from backup_service import create_backup
    from models import get_db

    db_path = current_app.config['DATABASE_PATH']
    backups_path = current_app.config['BACKUPS_PATH']
    config = current_app.config['APP_CONFIG']
    retention = config.get('backup_retention', 4)

    try:
        # 1. Backup automatico prima di qualsiasi modifica
        backup_result = create_backup(db_path, backups_path, retention)

        # 2. Cancella i dati nell'ordine corretto rispettando i vincoli FK
        # Le tabelle figlie vengono svuotate prima delle tabelle padre.
        # I nomi tabella sono costanti hardcoded, non input utente.
        db = get_db()
        db.execute("DELETE FROM import_preview")
        db.execute("DELETE FROM import_history")
        db.execute("DELETE FROM documenti")
        db.execute("DELETE FROM manutenzioni")
        db.execute("DELETE FROM verifiche")
        db.execute("DELETE FROM apparecchi")
        db.execute("DELETE FROM email_config")
        db.execute("DELETE FROM log_attivita")
        db.commit()

        log_attivita(g.user['id'], 'reset_parziale', 'database', None,
                     f"Reset parziale DB. Backup: {backup_result['filename']}", request.remote_addr)

        flash(
            f'Reset parziale completato. Backup salvato: <strong>{backup_result["filename"]}</strong>. '
            f'Utenti e divisioni mantenuti.',
            'success'
        )
        return redirect(url_for('admin.configurazione'))

    except Exception as e:
        flash(f'Errore durante il reset parziale: {str(e)}', 'danger')
        return redirect(url_for('admin.configurazione'))


# ============================================================================
# LOG ATTIVITA
# ============================================================================

@admin_bp.route('/log-attivita')
@admin_required
def log_attivita_view():
    """View activity logs."""
    page = request.args.get('page', 1, type=int)
    per_page = 50

    search = request.args.get('search', '').strip()
    entita = request.args.get('entita', '').strip()
    data_da = request.args.get('data_da', '').strip()
    data_a = request.args.get('data_a', '').strip()

    filtri = {'search': search, 'entita': entita, 'data_da': data_da, 'data_a': data_a}

    # Build WHERE clauses
    where = ["1=1"]
    params = []

    if search:
        where.append("(la.azione LIKE ? OR la.dettagli LIKE ?)")
        params.extend([f'%{search}%', f'%{search}%'])
    if entita:
        where.append("la.entita = ?")
        params.append(entita)
    if data_da:
        where.append("la.created_at >= ?")
        params.append(data_da)
    if data_a:
        where.append("la.created_at <= ? || ' 23:59:59'")
        params.append(data_a)

    where_clause = " AND ".join(where)

    # Count total
    total = query_one(
        f"SELECT COUNT(*) as cnt FROM log_attivita la WHERE {where_clause}",
        params
    )['cnt']

    # Get page of logs
    offset = (page - 1) * per_page
    logs = query_all(
        f"""SELECT la.*, u.nome || ' ' || u.cognome as utente_nome
            FROM log_attivita la
            LEFT JOIN utenti u ON la.utente_id = u.id
            WHERE {where_clause}
            ORDER BY la.created_at DESC
            LIMIT ? OFFSET ?""",
        params + [per_page, offset]
    )

    # Distinct entities for filter
    entita_list_rows = query_all(
        "SELECT DISTINCT entita FROM log_attivita ORDER BY entita"
    )
    entita_list = [r['entita'] for r in entita_list_rows]

    import math
    pages = max(1, math.ceil(total / per_page))
    pagination = {'page': page, 'pages': pages, 'total': total}

    return render_template('admin/log_attivita.html',
                           logs=logs, filtri=filtri, pagination=pagination,
                           entita_list=entita_list)
