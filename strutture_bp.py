"""
MedInventory - Gestione Strutture (superadmin)
"""

import base64
import hashlib
import re

from cryptography.fernet import Fernet
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, g, current_app
)
from auth import superadmin_required, login_required
from models import query_all, query_one, execute, log_attivita, get_db, \
    get_struttura_config_all, set_struttura_config
from ai_service import ANTHROPIC_MODELS, GEMINI_MODELS, OPENAI_MODELS, AI_PROVIDERS

strutture_bp = Blueprint('strutture', __name__, url_prefix='/strutture')


# ---------------------------------------------------------------------------
# Helpers codice auto-generazione
# ---------------------------------------------------------------------------

def _codice_base_da_nome(nome):
    """Genera un codice base di 2-5 lettere dalle iniziali del nome."""
    pulito = re.sub(r'[^a-zA-Z0-9\s]', '', nome).upper()
    parole = pulito.split()
    if len(parole) >= 2:
        base = ''.join(p[0] for p in parole if p)[:6]
    elif parole:
        base = parole[0][:6]
    else:
        base = 'X'
    return re.sub(r'[^A-Z0-9]', '', base) or 'X'


def _codice_univoco_struttura(db, nome):
    """Genera un codice univoco per la tabella strutture."""
    base = _codice_base_da_nome(nome)
    existing = {r[0] for r in db.execute("SELECT codice FROM strutture").fetchall()}
    codice = base
    n = 1
    while codice in existing:
        codice = f"{base}{n}"
        n += 1
    return codice


def _codice_univoco_divisione(db, nome, struttura_id, esclude_id=None):
    """Genera un codice univoco per divisioni nella struttura."""
    base = _codice_base_da_nome(nome)
    q = "SELECT codice FROM divisioni WHERE struttura_id=?"
    params = [struttura_id]
    if esclude_id:
        q += " AND id!=?"
        params.append(esclude_id)
    existing = {r[0] for r in db.execute(q, params).fetchall()}
    codice = base
    n = 1
    while codice in existing:
        codice = f"{base}{n}"
        n += 1
    return codice


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


_TIPI_STRUTTURA = ('ospedale', 'clinica_privata', 'rsa', 'ambulatorio',
                   'poliambulatorio', 'laboratorio', 'altro')


def _crea_divisione_predefinita(db, struttura_id, nome_struttura):
    """Crea la divisione iniziale della struttura appena registrata."""
    codice = _codice_univoco_divisione(db, nome_struttura, struttura_id)
    cur = db.execute(
        """INSERT INTO divisioni (nome, codice, colore, descrizione, struttura_id)
           VALUES (?, ?, ?, ?, ?)""",
        (
            nome_struttura,
            codice,
            '#0ea5e9',
            'Divisione predefinita creata automaticamente alla creazione della struttura.',
            struttura_id,
        )
    )
    return cur.lastrowid


def _get_divisioni_struttura(struttura_id):
    return query_all(
        "SELECT * FROM divisioni WHERE struttura_id = ? ORDER BY nome",
        (struttura_id,)
    )


def _sync_divisioni_struttura(db, struttura_id, form):
    existing_ids = form.getlist('existing_div_id')
    existing_nomi = form.getlist('existing_div_nome')
    existing_colori = form.getlist('existing_div_colore')
    existing_descrizioni = form.getlist('existing_div_descrizione')
    active_ids = {int(v) for v in form.getlist('existing_div_attiva') if str(v).isdigit()}

    new_nomi_raw = [n.strip() for n in form.getlist('new_div_nome') if n.strip()]

    # Validazione unicità nomi nell'intero batch prima di toccare il DB
    all_nomi_batch = [
        (existing_nomi[idx] if idx < len(existing_nomi) else '').strip()
        for idx, v in enumerate(existing_ids)
        if (existing_nomi[idx] if idx < len(existing_nomi) else '').strip()
    ] + new_nomi_raw
    if len(all_nomi_batch) != len(set(n.lower() for n in all_nomi_batch)):
        raise ValueError('Sono presenti divisioni con lo stesso nome. Ogni divisione deve avere un nome univoco.')

    for idx, div_id_raw in enumerate(existing_ids):
        try:
            div_id = int(div_id_raw)
        except (TypeError, ValueError):
            continue

        nome = (existing_nomi[idx] if idx < len(existing_nomi) else '').strip()
        colore = (existing_colori[idx] if idx < len(existing_colori) else '#0ea5e9').strip() or '#0ea5e9'
        descrizione = (existing_descrizioni[idx] if idx < len(existing_descrizioni) else '').strip() or None
        attiva = 1 if div_id in active_ids else 0

        if not nome:
            raise ValueError('Ogni divisione esistente deve avere un nome.')

        codice = _codice_univoco_divisione(db, nome, struttura_id, esclude_id=div_id)
        cur = db.execute(
            """UPDATE divisioni
               SET nome=?, codice=?, colore=?, descrizione=?, attiva=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND struttura_id=?""",
            (nome, codice, colore, descrizione, attiva, div_id, struttura_id)
        )
        if cur.rowcount == 0:
            raise ValueError('Una delle divisioni selezionate non appartiene alla struttura corrente.')

    new_nomi = form.getlist('new_div_nome')
    new_colori = form.getlist('new_div_colore')
    new_descrizioni = form.getlist('new_div_descrizione')

    for idx, nome_raw in enumerate(new_nomi):
        nome = nome_raw.strip()
        colore = (new_colori[idx] if idx < len(new_colori) else '#0ea5e9').strip() or '#0ea5e9'
        descrizione = (new_descrizioni[idx] if idx < len(new_descrizioni) else '').strip() or None

        if not any([nome, descrizione]):
            continue
        if not nome:
            raise ValueError('Ogni nuova divisione deve avere un nome.')

        codice = _codice_univoco_divisione(db, nome, struttura_id)
        db.execute(
            """INSERT INTO divisioni (nome, codice, colore, descrizione, struttura_id)
               VALUES (?, ?, ?, ?, ?)""",
            (nome, codice, colore, descrizione, struttura_id)
        )


def _leggi_form_struttura(form):
    """Estrae e normalizza tutti i campi struttura dal form POST."""
    modalita = form.get('modalita', 'standard')
    if modalita not in ('standard', 'avanzata'):
        modalita = 'standard'
    tipo = form.get('tipo', 'altro')
    if tipo not in _TIPI_STRUTTURA:
        tipo = 'altro'
    return {
        'nome':               form.get('nome', '').strip(),
        'descrizione':        form.get('descrizione', '').strip() or None,
        'tipo':               tipo,
        'indirizzo':          form.get('indirizzo', '').strip() or None,
        'telefono':           form.get('telefono', '').strip() or None,
        'email_notifiche':    form.get('email_notifiche', '').strip() or None,
        'pec':                form.get('pec', '').strip() or None,
        'responsabile':       form.get('responsabile', '').strip() or None,
        'email_responsabile': form.get('email_responsabile', '').strip() or None,
        'codice_fiscale':     form.get('codice_fiscale', '').strip() or None,
        'partita_iva':        form.get('partita_iva', '').strip() or None,
        'data_attivazione':   form.get('data_attivazione', '').strip() or None,
        'scadenza_contratto': form.get('scadenza_contratto', '').strip() or None,
        'note':               form.get('note', '').strip() or None,
        'modalita':           modalita,
    }


@strutture_bp.route('/nuova', methods=['GET', 'POST'])
@superadmin_required
def nuova():
    if request.method == 'POST':
        dati = _leggi_form_struttura(request.form)
        if not dati['nome']:
            flash('Il nome è obbligatorio.', 'danger')
            return render_template('strutture/form.html', struttura=request.form,
                                   tipi_struttura=_TIPI_STRUTTURA, divisioni=[])
        try:
            db = get_db()
            codice = _codice_univoco_struttura(db, dati['nome'])
            cur = db.execute(
                """INSERT INTO strutture
                   (nome, codice, descrizione, tipo, indirizzo, telefono,
                    email_notifiche, pec, responsabile, email_responsabile,
                    codice_fiscale, partita_iva, data_attivazione,
                    scadenza_contratto, note, modalita)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (dati['nome'], codice, dati['descrizione'], dati['tipo'],
                 dati['indirizzo'], dati['telefono'], dati['email_notifiche'],
                 dati['pec'], dati['responsabile'], dati['email_responsabile'],
                 dati['codice_fiscale'], dati['partita_iva'],
                 dati['data_attivazione'], dati['scadenza_contratto'],
                 dati['note'], dati['modalita'])
            )
            struttura_id = cur.lastrowid
            divisione_id = _crea_divisione_predefinita(db, struttura_id, dati['nome'])
            db.commit()
            log_attivita(g.user['id'], 'crea', 'struttura', struttura_id,
                         f'Struttura "{dati["nome"]}" creata')
            log_attivita(g.user['id'], 'creazione', 'divisioni', divisione_id,
                         f'Divisione predefinita creata per struttura "{dati["nome"]}"',
                         struttura_id=struttura_id)
            flash(f'Struttura "{dati["nome"]}" creata con successo.', 'success')
            return redirect(url_for('strutture.index'))
        except Exception as e:
            try:
                get_db().rollback()
            except Exception:
                pass
            current_app.logger.error(f'Errore creazione struttura: {e}')
            flash('Errore durante il salvataggio. Riprovare.', 'danger')
        return render_template('strutture/form.html', struttura=request.form,
                               tipi_struttura=_TIPI_STRUTTURA, divisioni=[])
    return render_template('strutture/form.html', struttura=None,
                           tipi_struttura=_TIPI_STRUTTURA, divisioni=[])


@strutture_bp.route('/<int:struttura_id>/modifica', methods=['GET', 'POST'])
@superadmin_required
def modifica(struttura_id):
    struttura = query_one("SELECT * FROM strutture WHERE id = ?", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index'))

    divisioni = _get_divisioni_struttura(struttura_id)

    if request.method == 'POST':
        dati = _leggi_form_struttura(request.form)
        attiva = 1 if request.form.get('attiva') else 0

        if not dati['nome']:
            flash('Il nome è obbligatorio.', 'danger')
            return render_template('strutture/form.html',
                                   struttura=dict(struttura) | dict(request.form),
                                   tipi_struttura=_TIPI_STRUTTURA,
                                   divisioni=divisioni)

        try:
            db = get_db()
            db.execute(
                """UPDATE strutture SET
                   nome=?, descrizione=?, tipo=?, indirizzo=?,
                   telefono=?, email_notifiche=?, pec=?, responsabile=?,
                   email_responsabile=?, codice_fiscale=?, partita_iva=?,
                   data_attivazione=?, scadenza_contratto=?, note=?,
                   modalita=?, attiva=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (dati['nome'], dati['descrizione'], dati['tipo'],
                 dati['indirizzo'], dati['telefono'], dati['email_notifiche'],
                 dati['pec'], dati['responsabile'], dati['email_responsabile'],
                 dati['codice_fiscale'], dati['partita_iva'],
                 dati['data_attivazione'], dati['scadenza_contratto'],
                 dati['note'], dati['modalita'], attiva, struttura_id)
            )
            _sync_divisioni_struttura(db, struttura_id, request.form)
            # Invalida sessioni degli utenti se la struttura viene disattivata
            if not attiva:
                db.execute(
                    "DELETE FROM sessioni WHERE utente_id IN "
                    "(SELECT id FROM utenti WHERE struttura_id=?)",
                    (struttura_id,)
                )
            db.commit()
            log_attivita(g.user['id'], 'modifica', 'struttura', struttura_id,
                         f'Struttura "{dati["nome"]}" modificata')
            flash('Struttura aggiornata.', 'success')
            return redirect(url_for('strutture.index'))
        except ValueError as e:
            try:
                db.rollback()
            except Exception:
                pass
            flash(str(e), 'danger')
        except Exception as e:
            try:
                get_db().rollback()
            except Exception:
                pass
            current_app.logger.error(f'Errore modifica struttura {struttura_id}: {e}')
            flash('Errore durante il salvataggio. Riprovare.', 'danger')
        struttura = dict(struttura) | dict(request.form)
        divisioni = _get_divisioni_struttura(struttura_id)

    return render_template('strutture/form.html', struttura=struttura,
                           tipi_struttura=_TIPI_STRUTTURA, divisioni=divisioni)


@strutture_bp.route('/<int:struttura_id>/config', methods=['GET', 'POST'])
@login_required
def config(struttura_id):
    ruolo = g.user['ruolo']
    if ruolo not in ('admin', 'superadmin'):
        flash('Accesso non autorizzato.', 'danger')
        return redirect(url_for('index'))
    if ruolo == 'admin' and g.user.get('struttura_id') != struttura_id:
        flash('Non puoi accedere alla configurazione di un\'altra struttura.', 'danger')
        return redirect(url_for('index'))

    is_admin_only = (ruolo == 'admin')

    struttura = query_one("SELECT * FROM strutture WHERE id = ?", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index') if not is_admin_only else url_for('index'))

    if request.method == 'POST':
        if is_admin_only:
            # Admin uses the AJAX test-ai endpoint for AI config, not this form
            return redirect(url_for('strutture.config', struttura_id=struttura_id))

        # Superadmin only: save SMTP + report fields
        chiavi_smtp_report = [
            'smtp_host', 'smtp_port', 'smtp_user', 'smtp_from', 'smtp_use_tls',
            'report_frequenza', 'report_schedulato_attivo',
        ]
        CHECKBOX_KEYS = {'smtp_use_tls', 'report_schedulato_attivo'}
        for chiave in chiavi_smtp_report:
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
        log_attivita(g.user['id'], 'modifica', 'strutture_config', struttura_id,
                     'Configurazione SMTP/report salvata', request.remote_addr)
        return redirect(url_for('strutture.config', struttura_id=struttura_id))

    cfg = get_struttura_config_all(struttura_id)
    return render_template('strutture/config.html',
                           struttura=struttura,
                           cfg=cfg,
                           is_admin_only=is_admin_only,
                           ai_providers=AI_PROVIDERS,
                           anthropic_models=ANTHROPIC_MODELS,
                           gemini_models=GEMINI_MODELS,
                           openai_models=OPENAI_MODELS)


# ============================================================================
# DIVISIONE MANAGEMENT (dal contesto struttura)
# ============================================================================

@strutture_bp.route('/<int:struttura_id>/divisioni/<int:div_id>/elimina', methods=['POST'])
@superadmin_required
def elimina_divisione(struttura_id, div_id):
    struttura = query_one("SELECT id FROM strutture WHERE id=?", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index'))

    div = query_one("SELECT * FROM divisioni WHERE id=? AND struttura_id=?", (div_id, struttura_id))
    if not div:
        flash('Divisione non trovata.', 'danger')
        return redirect(url_for('strutture.modifica', struttura_id=struttura_id))

    count_app = query_one(
        "SELECT COUNT(*) as cnt FROM apparecchi WHERE divisione_id=?",
        (div_id,)
    )
    if count_app and count_app['cnt'] > 0:
        flash(f'Impossibile eliminare "{div["nome"]}": ha {count_app["cnt"]} apparecchi associati (inclusi dismessi). Riassegnali prima.', 'danger')
        return redirect(url_for('strutture.modifica', struttura_id=struttura_id))

    count_div = query_one(
        "SELECT COUNT(*) as cnt FROM divisioni WHERE struttura_id=?", (struttura_id,)
    )
    if count_div and count_div['cnt'] <= 1:
        flash('Non puoi eliminare l\'ultima divisione della struttura.', 'danger')
        return redirect(url_for('strutture.modifica', struttura_id=struttura_id))

    try:
        db = get_db()
        # Annulla FK nullable che puntano alla divisione (nessuna ha CASCADE)
        db.execute("UPDATE utenti SET divisione_default_id=NULL WHERE divisione_default_id=?", (div_id,))
        db.execute("UPDATE email_config SET divisione_id=NULL WHERE divisione_id=?", (div_id,))
        db.execute("UPDATE import_history SET divisione_id=NULL WHERE divisione_id=?", (div_id,))
        db.execute("DELETE FROM divisioni WHERE id=? AND struttura_id=?", (div_id, struttura_id))
        db.commit()
        log_attivita(g.user['id'], 'elimina', 'divisioni', div_id,
                     f'Eliminata divisione "{div["nome"]}" dalla struttura {struttura_id}',
                     struttura_id=struttura_id)
        flash(f'Divisione "{div["nome"]}" eliminata.', 'success')
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        current_app.logger.error(f'Errore eliminazione divisione {div_id}: {e}')
        flash('Errore durante l\'eliminazione della divisione.', 'danger')
    return redirect(url_for('strutture.modifica', struttura_id=struttura_id))


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
