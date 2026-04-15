"""
MedInventory - Verifiche di Sicurezza Elettrica Blueprint
CRUD per verifiche di sicurezza elettrica + import massivo AI.
Pattern identico a manutenzioni.py.
"""

import os
import json
from datetime import datetime, timedelta

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, g, current_app, send_from_directory
)
from werkzeug.utils import secure_filename

from auth import login_required
from models import query_one, query_all, execute, log_attivita, upload_subdir

verifiche_bp = Blueprint('verifiche', __name__)

ALLOWED_DOC_EXT = {'pdf'}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_divisione_filter(table_alias='a'):
    """Return SQL WHERE clause and params for division filtering."""
    div = g.divisione_attiva
    if div and div.get('id') != 'tutte':
        return f"AND {table_alias}.divisione_id = ?", [div['id']]
    elif g.user['ruolo'] in ('admin', 'tecnico'):
        struttura_id = getattr(g, 'struttura_id', None)
        if struttura_id:
            return f"AND {table_alias}.struttura_id = ?", [struttura_id]
        return "", []
    else:
        ids = [d['id'] for d in g.divisioni]
        if not ids:
            return "AND 1=0", []
        placeholders = ','.join('?' * len(ids))
        return f"AND {table_alias}.divisione_id IN ({placeholders})", ids


def _get_accessible_apparecchi():
    """Get list of apparecchi accessible by current user."""
    div_clause, div_params = _get_divisione_filter('a')
    return query_all(
        f"""SELECT a.id, a.matricola, a.marca, a.modello, d.nome as divisione_nome
            FROM apparecchi a
            LEFT JOIN divisioni d ON a.divisione_id = d.id
            WHERE a.stato != 'dismesso' AND a.soggetto_verifica = 1 {div_clause}
            ORDER BY a.marca, a.modello""",
        div_params
    )


def _validate_verifica(form_data):
    """Validate verifica form data. Returns (cleaned_data, errors)."""
    errors = {}
    data = {}

    # Required: apparecchio_id
    data['apparecchio_id'] = form_data.get('apparecchio_id', '')
    if not data['apparecchio_id']:
        errors['apparecchio_id'] = "Seleziona un apparecchio."
    else:
        try:
            data['apparecchio_id'] = int(data['apparecchio_id'])
            app = query_one("SELECT id FROM apparecchi WHERE id = ?", (data['apparecchio_id'],))
            if not app:
                errors['apparecchio_id'] = "Apparecchio non trovato."
        except ValueError:
            errors['apparecchio_id'] = "Apparecchio non valido."

    # Required: data_verifica
    data['data_verifica'] = form_data.get('data_verifica', '').strip()
    if not data['data_verifica']:
        errors['data_verifica'] = "La data della verifica è obbligatoria."
    else:
        try:
            datetime.strptime(data['data_verifica'], '%Y-%m-%d')
        except ValueError:
            errors['data_verifica'] = "Formato data non valido (YYYY-MM-DD)."

    # Required: esito
    data['esito'] = form_data.get('esito', '')
    if data['esito'] not in ('positivo', 'negativo', 'con_riserva'):
        errors['esito'] = "Seleziona un esito valido."

    # Periodicita_giorni: solo 365 (1 anno) o 730 (2 anni), default 730
    periodicita = form_data.get('periodicita_giorni', '730').strip()
    data['periodicita_giorni'] = 365 if periodicita == '365' else 730

    # Optional: prossima_scadenza — auto-calcola se assente e periodicita presente
    data['prossima_scadenza'] = form_data.get('prossima_scadenza', '').strip() or None
    if not data['prossima_scadenza'] and data.get('data_verifica') and data.get('periodicita_giorni'):
        try:
            d = datetime.strptime(data['data_verifica'], '%Y-%m-%d')
            d += timedelta(days=data['periodicita_giorni'])
            data['prossima_scadenza'] = d.strftime('%Y-%m-%d')
        except ValueError:
            pass

    # Optional text fields
    data['tecnico_ditta'] = form_data.get('tecnico_ditta', '').strip() or None
    data['note'] = form_data.get('note', '').strip() or None

    return data, errors


def _save_documento(file_obj, verifica_id, struttura_id=None):
    """Save uploaded PDF document for a verifica. Returns relative path or None."""
    if not file_obj or not file_obj.filename:
        return None
    ext = file_obj.filename.rsplit('.', 1)[-1].lower()
    if ext not in ALLOWED_DOC_EXT:
        return None
    uploads_dir, rel_prefix = upload_subdir('verifiche', struttura_id)
    safe_name = secure_filename(f"{int(datetime.now().timestamp())}_{file_obj.filename}")
    full_path = os.path.join(uploads_dir, safe_name)
    file_obj.save(full_path)
    return f"{rel_prefix}/{safe_name}"


# ---------------------------------------------------------------------------
# CRUD Routes
# ---------------------------------------------------------------------------

@verifiche_bp.route('/verifiche')
@login_required
def lista():
    """List verifiche with filters."""
    div_clause, div_params = _get_divisione_filter('a')

    search = request.args.get('search', '').strip()
    esito = request.args.get('esito', '')
    data_da = request.args.get('data_da', '')
    data_a = request.args.get('data_a', '')
    page = request.args.get('page', 1, type=int)
    per_page = 25

    where_clauses = ["1=1"]
    params = []

    if search:
        where_clauses.append(
            "(a.matricola LIKE ? OR a.marca LIKE ? OR a.modello LIKE ? "
            "OR v.tecnico_ditta LIKE ? OR v.note LIKE ?)"
        )
        s = f'%{search}%'
        params.extend([s, s, s, s, s])

    if esito:
        where_clauses.append("v.esito = ?")
        params.append(esito)

    if data_da:
        where_clauses.append("v.data_verifica >= ?")
        params.append(data_da)

    if data_a:
        where_clauses.append("v.data_verifica <= ?")
        params.append(data_a)

    where_sql = " AND ".join(where_clauses)

    count_sql = f"""
        SELECT COUNT(*) as cnt FROM verifiche v
        JOIN apparecchi a ON v.apparecchio_id = a.id
        WHERE {where_sql} {div_clause}
    """
    total = query_one(count_sql, params + div_params)['cnt']

    offset = (page - 1) * per_page
    total_pages = max(1, (total + per_page - 1) // per_page)

    data_sql = f"""
        SELECT v.*, a.marca, a.modello, a.matricola,
               d.nome as divisione_nome, d.colore as divisione_colore,
               u.nome || ' ' || u.cognome as creato_da_nome
        FROM verifiche v
        JOIN apparecchi a ON v.apparecchio_id = a.id
        LEFT JOIN divisioni d ON a.divisione_id = d.id
        LEFT JOIN utenti u ON v.created_by = u.id
        WHERE {where_sql} {div_clause}
        ORDER BY v.data_verifica DESC
        LIMIT ? OFFSET ?
    """
    verifiche = query_all(data_sql, params + div_params + [per_page, offset])

    context = {
        'verifiche': verifiche,
        'filtri': {'search': search, 'esito': esito, 'data_da': data_da, 'data_a': data_a},
        'pagination': {'page': page, 'per_page': per_page, 'total': total, 'total_pages': total_pages},
    }

    if request.args.get('partial'):
        return render_template('partials/verifiche_table.html', **context)

    return render_template('verifiche/lista.html', **context)


@verifiche_bp.route('/verifiche/nuova', methods=['GET', 'POST'])
@login_required
def nuova():
    """Create a new verifica record."""
    apparecchio_id = request.args.get('apparecchio_id', '')

    if request.method == 'GET':
        apparecchi = _get_accessible_apparecchi()
        return render_template('verifiche/form.html',
                               verifica=None, errors={},
                               apparecchi=apparecchi,
                               form_data={'apparecchio_id': apparecchio_id})

    data, errors = _validate_verifica(request.form)

    if errors:
        apparecchi = _get_accessible_apparecchi()
        return render_template('verifiche/form.html',
                               verifica=None, errors=errors,
                               apparecchi=apparecchi, form_data=request.form)

    cursor = execute(
        """INSERT INTO verifiche
           (apparecchio_id, data_verifica, prossima_scadenza,
            periodicita_giorni, esito, tecnico_ditta, note, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (data['apparecchio_id'], data['data_verifica'], data['prossima_scadenza'],
         data['periodicita_giorni'], data['esito'],
         data['tecnico_ditta'], data['note'], g.user['id'])
    )
    verifica_id = cursor.lastrowid

    # Save documento if uploaded
    doc_file = request.files.get('documento')
    if doc_file and doc_file.filename:
        doc_path = _save_documento(doc_file, verifica_id, getattr(g, 'struttura_id', None))
        if doc_path:
            execute("UPDATE verifiche SET documento_path = ? WHERE id = ?",
                    (doc_path, verifica_id))

    app_info = query_one("SELECT marca, modello, matricola FROM apparecchi WHERE id = ?",
                         (data['apparecchio_id'],))
    log_attivita(g.user['id'], 'creazione', 'verifiche', verifica_id,
                 f"Verifica {data['esito']} per {app_info['marca']} {app_info['modello']}",
                 request.remote_addr)

    flash('Verifica registrata con successo.', 'success')
    return redirect(url_for('apparecchi.dettaglio', id=data['apparecchio_id']))


@verifiche_bp.route('/verifiche/<int:id>/modifica', methods=['GET', 'POST'])
@login_required
def modifica(id):
    """Edit a verifica record."""
    struttura_id = getattr(g, 'struttura_id', None)
    verifica = query_one(
        """SELECT v.*, a.marca, a.modello, a.matricola, a.divisione_id
           FROM verifiche v
           JOIN apparecchi a ON v.apparecchio_id = a.id
           WHERE v.id = ? AND (a.struttura_id = ? OR ? IS NULL)""",
        (id, struttura_id, struttura_id)
    )
    if not verifica:
        flash('Verifica non trovata.', 'danger')
        return redirect(url_for('verifiche.lista'))

    if g.user['ruolo'] not in ('admin', 'superadmin', 'tecnico'):
        accessible_ids = [d['id'] for d in g.divisioni]
        if verifica['divisione_id'] not in accessible_ids:
            flash('Accesso non autorizzato.', 'danger')
            return redirect(url_for('verifiche.lista'))

    if request.method == 'GET':
        apparecchi = _get_accessible_apparecchi()
        return render_template('verifiche/form.html',
                               verifica=verifica, errors={},
                               apparecchi=apparecchi, form_data=verifica)

    data, errors = _validate_verifica(request.form)

    if errors:
        apparecchi = _get_accessible_apparecchi()
        return render_template('verifiche/form.html',
                               verifica=verifica, errors=errors,
                               apparecchi=apparecchi, form_data=request.form)

    execute(
        """UPDATE verifiche SET
           apparecchio_id=?, data_verifica=?, prossima_scadenza=?,
           periodicita_giorni=?, esito=?, tecnico_ditta=?, note=?
           WHERE id=?""",
        (data['apparecchio_id'], data['data_verifica'], data['prossima_scadenza'],
         data['periodicita_giorni'], data['esito'],
         data['tecnico_ditta'], data['note'], id)
    )

    # Update documento if new file uploaded
    doc_file = request.files.get('documento')
    if doc_file and doc_file.filename:
        doc_path = _save_documento(doc_file, id, getattr(g, 'struttura_id', None))
        if doc_path:
            execute("UPDATE verifiche SET documento_path = ? WHERE id = ?",
                    (doc_path, id))

    log_attivita(g.user['id'], 'modifica', 'verifiche', id,
                 f"Verifica {data['esito']} modificata", request.remote_addr)

    flash('Verifica aggiornata con successo.', 'success')
    return redirect(url_for('apparecchi.dettaglio', id=data['apparecchio_id']))


@verifiche_bp.route('/verifiche/<int:id>/elimina', methods=['POST'])
@login_required
def elimina(id):
    """Delete a verifica record."""
    struttura_id = getattr(g, 'struttura_id', None)
    verifica = query_one(
        """SELECT v.*, a.divisione_id, v.apparecchio_id FROM verifiche v
           JOIN apparecchi a ON v.apparecchio_id = a.id
           WHERE v.id = ? AND (a.struttura_id = ? OR ? IS NULL)""",
        (id, struttura_id, struttura_id)
    )
    if not verifica:
        flash('Verifica non trovata.', 'danger')
        return redirect(url_for('verifiche.lista'))

    if g.user['ruolo'] not in ('admin', 'superadmin', 'tecnico'):
        flash('Non autorizzato a eliminare verifiche.', 'danger')
        return redirect(url_for('verifiche.lista'))

    # Delete associated document file if present
    if verifica['documento_path']:
        full_path = os.path.join(current_app.config['UPLOADS_PATH'], verifica['documento_path'])
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
            except OSError:
                pass

    execute("DELETE FROM verifiche WHERE id = ?", (id,))

    log_attivita(g.user['id'], 'eliminazione', 'verifiche', id,
                 f"Verifica {verifica['esito']} eliminata", request.remote_addr)

    flash('Verifica eliminata.', 'warning')
    return redirect(url_for('apparecchi.dettaglio', id=verifica['apparecchio_id']))


@verifiche_bp.route('/verifiche/<int:id>/documento')
@login_required
def scarica_documento(id):
    """Download PDF document for a verifica."""
    verifica = query_one("SELECT * FROM verifiche WHERE id = ?", (id,))
    if not verifica or not verifica['documento_path']:
        flash('Documento non trovato.', 'danger')
        return redirect(url_for('verifiche.lista'))

    uploads_path = current_app.config['UPLOADS_PATH']
    rel = verifica['documento_path']
    directory = os.path.join(uploads_path, os.path.dirname(rel))
    filename = os.path.basename(rel)
    return send_from_directory(directory, filename, as_attachment=True)


# ---------------------------------------------------------------------------
# Import massivo AI
# ---------------------------------------------------------------------------

@verifiche_bp.route('/verifiche/import')
@login_required
def import_upload():
    """Pagina upload per import massivo verifiche."""
    divisioni = query_all("SELECT * FROM divisioni WHERE attiva = 1 ORDER BY nome")
    return render_template('verifiche/import_upload.html', divisioni=divisioni)


@verifiche_bp.route('/verifiche/import/analizza', methods=['POST'])
@login_required
def import_analizza():
    """Receive file, extract text, call AI, save preview."""
    from ai_service import extract_text_from_file, analyze_verifiche_with_ai

    file = request.files.get('file')
    divisione_id = request.form.get('divisione_id') or None

    if not file or not file.filename:
        flash('Seleziona un file da importare.', 'danger')
        return redirect(url_for('verifiche.import_upload'))

    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext not in ('pdf', 'xlsx', 'xls', 'csv'):
        flash('Formato file non supportato. Usa PDF, Excel o CSV.', 'danger')
        return redirect(url_for('verifiche.import_upload'))

    uploads_dir = os.path.join(current_app.config['UPLOADS_PATH'], 'import')
    os.makedirs(uploads_dir, exist_ok=True)
    safe_name = f"{int(datetime.now().timestamp())}_{secure_filename(file.filename)}"
    filepath = os.path.join(uploads_dir, safe_name)
    file.save(filepath)

    try:
        text = extract_text_from_file(filepath, ext)
    except Exception as e:
        flash(f'Errore estrazione testo: {e}', 'danger')
        return redirect(url_for('verifiche.import_upload'))

    config = current_app.config['APP_CONFIG']
    struttura_id = getattr(g, 'struttura_id', None)

    from ai_service import check_ai_configured, get_ai_config
    ai_ok, ai_error = check_ai_configured(config=config, struttura_id=struttura_id)
    if not ai_ok:
        flash(ai_error, 'danger')
        return redirect(url_for('verifiche.import_upload'))

    ai_cfg = get_ai_config(struttura_id=struttura_id, config=config)
    api_key = ai_cfg['api_key']
    model = ai_cfg['model_email']

    try:
        items, ai_response = analyze_verifiche_with_ai(
            text, api_key, model, config=config, struttura_id=struttura_id)
    except Exception as e:
        flash(f'Errore analisi AI: {e}', 'danger')
        return redirect(url_for('verifiche.import_upload'))

    # Save import_history
    cursor = execute(
        """INSERT INTO import_history
           (tipo_import, filename, filepath, tipo_documento, divisione_id,
            totale_righe, stato, ai_prompt, ai_response, imported_by)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
        ('verifica_elettrica', file.filename, f"import/{safe_name}", ext,
         int(divisione_id) if divisione_id else None,
         len(items),
         f"[VERIFICA_BATCH_SYSTEM_PROMPT + testo ({len(text)} chars)]",
         ai_response, g.user['id'])
    )
    import_id = cursor.lastrowid

    # Match matricola → apparecchio for preview
    for i, item in enumerate(items):
        matricola = item.get('matricola', '').strip()
        match_id = None
        match_confidence = 0.0
        if matricola:
            app_row = query_one(
                "SELECT id FROM apparecchi WHERE matricola = ? AND stato != 'dismesso'",
                (matricola,)
            )
            if app_row:
                match_id = app_row['id']
                match_confidence = 1.0

        execute(
            """INSERT INTO import_preview
               (import_id, riga_numero, dati_estratti, apparecchio_match_id,
                match_confidence, stato)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (import_id, i + 1, json.dumps(item), match_id, match_confidence)
        )

    log_attivita(g.user['id'], 'import_analisi', 'verifiche', import_id,
                 f"Analisi AI: {len(items)} verifiche estratte da {file.filename}",
                 request.remote_addr)

    return redirect(url_for('verifiche.import_preview', id=import_id))


@verifiche_bp.route('/verifiche/import/<int:id>/preview')
@login_required
def import_preview(id):
    """Show AI preview for import review."""
    record = query_one("SELECT * FROM import_history WHERE id = ?", (id,))
    if not record or record['tipo_import'] != 'verifica_elettrica':
        flash('Import non trovato.', 'danger')
        return redirect(url_for('verifiche.import_upload'))

    righe = query_all(
        """SELECT ip.*, a.marca, a.modello, a.matricola as app_matricola,
                  d.nome as divisione_nome
           FROM import_preview ip
           LEFT JOIN apparecchi a ON ip.apparecchio_match_id = a.id
           LEFT JOIN divisioni d ON a.divisione_id = d.id
           WHERE ip.import_id = ?
           ORDER BY ip.riga_numero""",
        (id,)
    )

    # Deserialize JSON data for template
    for r in righe:
        try:
            r['dati'] = json.loads(r['dati_estratti'])
        except Exception:
            r['dati'] = {}

    divisioni = query_all("SELECT * FROM divisioni WHERE attiva = 1 ORDER BY nome")
    apparecchi_list = _get_accessible_apparecchi()

    return render_template('verifiche/import_preview.html',
                           record=record, righe=righe,
                           divisioni=divisioni,
                           apparecchi_list=apparecchi_list)


@verifiche_bp.route('/verifiche/import/<int:id>/esegui', methods=['POST'])
@login_required
def import_esegui(id):
    """Execute import of selected verifiche rows."""
    record = query_one("SELECT * FROM import_history WHERE id = ?", (id,))
    if not record or record['tipo_import'] != 'verifica_elettrica':
        flash('Import non trovato.', 'danger')
        return redirect(url_for('verifiche.import_upload'))

    selected_rows = request.form.getlist('righe_selezionate')
    divisione_id_default = request.form.get('divisione_id') or None

    imported = 0
    errors = 0

    for row_id in selected_rows:
        riga = query_one("SELECT * FROM import_preview WHERE id = ? AND import_id = ?",
                         (int(row_id), id))
        if not riga:
            continue

        try:
            dati = json.loads(riga['dati_estratti'])
        except Exception:
            errors += 1
            continue

        # Determina apparecchio_id: match o override dall'utente
        app_id_override = request.form.get(f'apparecchio_id_{row_id}')
        apparecchio_id = int(app_id_override) if app_id_override else riga['apparecchio_match_id']

        if not apparecchio_id:
            errors += 1
            continue

        # Auto-calcola prossima_scadenza se mancante
        prossima = dati.get('prossima_scadenza')
        if not prossima and dati.get('data_verifica') and dati.get('periodicita_giorni'):
            try:
                d = datetime.strptime(dati['data_verifica'], '%Y-%m-%d')
                d += timedelta(days=int(dati['periodicita_giorni']))
                prossima = d.strftime('%Y-%m-%d')
            except Exception:
                pass

        try:
            cursor = execute(
                """INSERT INTO verifiche
                   (apparecchio_id, data_verifica, prossima_scadenza,
                    periodicita_giorni, esito, tecnico_ditta, note, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    apparecchio_id,
                    dati.get('data_verifica'),
                    prossima,
                    dati.get('periodicita_giorni', 365),
                    dati.get('esito', 'positivo'),
                    dati.get('tecnico_ditta'),
                    dati.get('note'),
                    g.user['id']
                )
            )
            execute("UPDATE import_preview SET stato = 'imported' WHERE id = ?", (int(row_id),))
            imported += 1
        except Exception:
            errors += 1

    # Update import_history
    execute(
        """UPDATE import_history SET stato = 'completed', righe_importate = ?,
                  righe_errori = ?, completed_at = datetime('now') WHERE id = ?""",
        (imported, errors, id)
    )

    log_attivita(g.user['id'], 'import_esecuzione', 'verifiche', id,
                 f"Importate {imported} verifiche, {errors} errori",
                 request.remote_addr)

    flash(f'Import completato: {imported} verifiche importate, {errors} errori.', 'success')
    return redirect(url_for('verifiche.lista'))
