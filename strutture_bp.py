"""
MedInventory - Gestione Strutture (superadmin)
"""

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, g
)
from auth import superadmin_required, login_required
from models import query_all, query_one, execute, log_attivita, \
    get_struttura_config_all, set_struttura_config

strutture_bp = Blueprint('strutture', __name__, url_prefix='/strutture')


@strutture_bp.route('/')
@superadmin_required
def index():
    strutture = query_all("""
        SELECT s.*,
               COUNT(DISTINCT d.id) as num_divisioni,
               COUNT(DISTINCT u.id) as num_utenti,
               COUNT(DISTINCT a.id) as num_apparecchi
        FROM strutture s
        LEFT JOIN divisioni d ON d.struttura_id = s.id AND d.attiva = 1
        LEFT JOIN utenti u ON u.struttura_id = s.id AND u.attivo = 1
        LEFT JOIN apparecchi a ON a.struttura_id = s.id AND a.stato != 'dismesso'
        GROUP BY s.id
        ORDER BY s.nome
    """)
    return render_template('strutture/index.html', strutture=strutture)


@strutture_bp.route('/nuova', methods=['GET', 'POST'])
@superadmin_required
def nuova():
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        codice = request.form.get('codice', '').strip().upper()
        descrizione = request.form.get('descrizione', '').strip()
        indirizzo = request.form.get('indirizzo', '').strip()
        email_notifiche = request.form.get('email_notifiche', '').strip()
        modalita = request.form.get('modalita', 'standard')

        if not nome or not codice:
            flash('Nome e codice sono obbligatori.', 'danger')
            return render_template('strutture/form.html', struttura=request.form)

        try:
            cur = execute(
                """INSERT INTO strutture (nome, codice, descrizione, indirizzo, email_notifiche, modalita)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (nome, codice, descrizione or None, indirizzo or None,
                 email_notifiche or None, modalita)
            )
            log_attivita(g.user['id'], 'crea', 'struttura', cur.lastrowid,
                         f'Struttura "{nome}" creata')
            flash(f'Struttura "{nome}" creata con successo.', 'success')
            return redirect(url_for('strutture.index'))
        except Exception as e:
            if 'UNIQUE' in str(e):
                flash(f'Il codice "{codice}" è già in uso.', 'danger')
            else:
                flash(f'Errore: {e}', 'danger')
        return render_template('strutture/form.html', struttura=request.form)

    return render_template('strutture/form.html', struttura=None)


@strutture_bp.route('/<int:struttura_id>/modifica', methods=['GET', 'POST'])
@superadmin_required
def modifica(struttura_id):
    struttura = query_one("SELECT * FROM strutture WHERE id = ?", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index'))

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        codice = request.form.get('codice', '').strip().upper()
        descrizione = request.form.get('descrizione', '').strip()
        indirizzo = request.form.get('indirizzo', '').strip()
        email_notifiche = request.form.get('email_notifiche', '').strip()
        modalita = request.form.get('modalita', 'standard')
        attiva = 1 if request.form.get('attiva') else 0

        try:
            execute(
                """UPDATE strutture SET nome=?, codice=?, descrizione=?, indirizzo=?,
                   email_notifiche=?, modalita=?, attiva=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (nome, codice, descrizione or None, indirizzo or None,
                 email_notifiche or None, modalita, attiva, struttura_id)
            )
            log_attivita(g.user['id'], 'modifica', 'struttura', struttura_id,
                         f'Struttura "{nome}" modificata')
            flash('Struttura aggiornata.', 'success')
            return redirect(url_for('strutture.index'))
        except Exception as e:
            flash(f'Errore: {e}', 'danger')
        struttura = dict(struttura) | dict(request.form)

    return render_template('strutture/form.html', struttura=struttura)


@strutture_bp.route('/<int:struttura_id>/config', methods=['GET', 'POST'])
@superadmin_required
def config(struttura_id):
    struttura = query_one("SELECT * FROM strutture WHERE id = ?", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index'))

    chiavi_visibili = [
        'ai_provider', 'anthropic_api_key', 'ai_import_model',
        'ai_email_model', 'ai_local_base_url', 'ai_local_model',
        'smtp_host', 'smtp_port', 'smtp_user', 'smtp_from', 'smtp_use_tls',
        'report_frequenza', 'report_schedulato_attivo',
    ]

    if request.method == 'POST':
        for chiave in chiavi_visibili:
            valore = request.form.get(chiave, '').strip()
            if valore:
                set_struttura_config(struttura_id, chiave, valore)
            else:
                execute(
                    "DELETE FROM strutture_config WHERE struttura_id=? AND chiave=?",
                    (struttura_id, chiave)
                )
        smtp_password = request.form.get('smtp_password', '').strip()
        if smtp_password:
            from cryptography.fernet import Fernet
            from flask import current_app
            import base64, hashlib
            key = current_app.config['APP_CONFIG'].get('encryption_key', '')
            if key:
                fernet_key = base64.urlsafe_b64encode(
                    hashlib.sha256(key.encode()).digest()
                )
                f = Fernet(fernet_key)
                encrypted = f.encrypt(smtp_password.encode()).decode()
                set_struttura_config(struttura_id, 'smtp_password_encrypted', encrypted)

        flash('Configurazione salvata.', 'success')
        return redirect(url_for('strutture.config', struttura_id=struttura_id))

    cfg = get_struttura_config_all(struttura_id)
    return render_template('strutture/config.html',
                           struttura=struttura, cfg=cfg, chiavi=chiavi_visibili)
