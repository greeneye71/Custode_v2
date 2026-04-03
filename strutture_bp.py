"""
MedInventory - Gestione Strutture (superadmin)
"""

import base64
import hashlib

from cryptography.fernet import Fernet
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, g, current_app
)
from auth import superadmin_required
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
        if modalita not in ('standard', 'avanzata'):
            modalita = 'standard'

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
                current_app.logger.error(f'Errore creazione struttura: {e}')
                flash('Errore durante il salvataggio. Riprovare.', 'danger')
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
        if modalita not in ('standard', 'avanzata'):
            modalita = 'standard'
        attiva = 1 if request.form.get('attiva') else 0

        if not nome or not codice:
            flash('Nome e codice sono obbligatori.', 'danger')
            return render_template('strutture/form.html', struttura=dict(struttura) | dict(request.form))

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
            current_app.logger.error(f'Errore modifica struttura {struttura_id}: {e}')
            flash('Errore durante il salvataggio. Riprovare.', 'danger')
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
        CHECKBOX_KEYS = {'smtp_use_tls', 'report_schedulato_attivo'}
        for chiave in chiavi_visibili:
            if chiave in CHECKBOX_KEYS:
                valore = '1' if request.form.get(chiave) else ''
            else:
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


# ============================================================================
# API TOKEN MANAGEMENT
# ============================================================================

@strutture_bp.route('/<int:struttura_id>/tokens')
@superadmin_required
def api_tokens(struttura_id):
    from flask import abort
    struttura = query_one("SELECT * FROM strutture WHERE id=?", (struttura_id,))
    if not struttura:
        abort(404)
    tokens = query_all(
        "SELECT * FROM api_tokens WHERE struttura_id=? ORDER BY created_at DESC",
        (struttura_id,)
    )
    return render_template('strutture/api_tokens.html', struttura=struttura, tokens=tokens)


@strutture_bp.route('/<int:struttura_id>/tokens/nuovo', methods=['POST'])
@superadmin_required
def nuovo_token(struttura_id):
    struttura = query_one("SELECT id, nome FROM strutture WHERE id=? AND attiva=1", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index'))

    import hashlib
    import secrets as _secrets
    nome = request.form.get('nome', '').strip()
    scopes = request.form.get('scopes', 'read')
    scadenza = request.form.get('scadenza') or None

    if not nome:
        flash('Il nome del token è obbligatorio.', 'danger')
        return redirect(url_for('strutture.api_tokens', struttura_id=struttura_id))

    if scopes not in ('read', 'read write'):
        scopes = 'read'

    raw = _secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()

    execute(
        """INSERT INTO api_tokens (struttura_id, nome, token_hash, scopes, scadenza, created_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (struttura_id, nome, token_hash, scopes, scadenza, g.user['id'])
    )
    log_attivita(g.user['id'], 'crea_token', 'api_tokens', None,
                 f'Token "{nome}" creato per struttura {struttura_id}',
                 struttura_id=struttura_id)
    flash(f'Token creato. Copia ora, non sarà più visibile: {raw}', 'warning')
    return redirect(url_for('strutture.api_tokens', struttura_id=struttura_id))


@strutture_bp.route('/<int:struttura_id>/tokens/<int:token_id>/revoca', methods=['POST'])
@superadmin_required
def revoca_token(struttura_id, token_id):
    cur = execute(
        "UPDATE api_tokens SET attivo=0 WHERE id=? AND struttura_id=?",
        (token_id, struttura_id)
    )
    if cur.rowcount == 0:
        flash('Token non trovato.', 'warning')
    else:
        log_attivita(g.user['id'], 'revoca_token', 'api_tokens', token_id,
                     dettagli=f'Token {token_id} revocato per struttura {struttura_id}',
                     struttura_id=struttura_id)
        flash('Token revocato.', 'success')
    return redirect(url_for('strutture.api_tokens', struttura_id=struttura_id))
