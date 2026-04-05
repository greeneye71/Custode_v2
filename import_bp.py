"""
MedInventory - Import Blueprint
AI-powered unified import: upload, classify, analyze, preview, approve.
Supports: inventario, verbale di manutenzione, verifica di sicurezza elettrica.
"""

import json
import logging
import os
import shutil
import threading
import time

from flask import (
    Blueprint, jsonify, render_template, request, redirect, url_for,
    flash, g, current_app
)
from werkzeug.utils import secure_filename

from auth import login_required
from models import query_one, query_all, execute, log_attivita

import_bp = Blueprint('import', __name__)
logger = logging.getLogger('medinventory.import')

ALLOWED_IMPORT_EXT = {'xlsx', 'xls', 'pdf', 'csv'}


def _parse_email_ai_response(raw):
    """Parse AI response JSON, handling string-wrapped JSON and array responses."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        # FIX 9: se l'AI restituisce un array JSON, usa il primo elemento
        if isinstance(parsed, list) and parsed:
            return parsed[0] if isinstance(parsed[0], dict) else {}
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str):
            start = parsed.find('{')
            end = parsed.rfind('}') + 1
            if start >= 0 and end > start:
                return json.loads(parsed[start:end])
        return {}
    except (json.JSONDecodeError, TypeError):
        return {}

DOC_TYPE_LABELS = {
    'inventario': 'Inventario',
    'verbale_manutenzione': 'Verbale di Manutenzione',
    'verifica_elettrica': 'Verifica di Sicurezza Elettrica',
}

TIPI_VALIDI_MANUTENZIONE = {'preventiva', 'correttiva', 'verifica', 'calibrazione'}
ESITI_VALIDI_VERIFICA = {'positivo', 'negativo', 'con_riserva'}


@import_bp.route('/import')
@login_required
def upload():
    """Upload page for unified document import."""
    return render_template('import/upload.html', divisioni=g.divisioni)


@import_bp.route('/import/analizza', methods=['POST'])
@login_required
def analizza():
    """Upload file, create pending import record, start AI analysis in background thread."""
    file = request.files.get('file')
    divisione_id = request.form.get('divisione_id')

    if not file or not file.filename:
        flash('Nessun file selezionato.', 'warning')
        return redirect(url_for('import.upload'))

    if not divisione_id:
        flash('Seleziona una divisione.', 'warning')
        return redirect(url_for('import.upload'))

    try:
        divisione_id = int(divisione_id)
    except ValueError:
        flash('Divisione non valida.', 'danger')
        return redirect(url_for('import.upload'))

    if g.user['ruolo'] != 'admin':
        accessible_ids = [d['id'] for d in g.divisioni]
        if divisione_id not in accessible_ids:
            flash('Divisione non accessibile.', 'danger')
            return redirect(url_for('import.upload'))

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_IMPORT_EXT:
        flash(f'Formato non supportato. Usa: {", ".join(ALLOWED_IMPORT_EXT)}', 'danger')
        return redirect(url_for('import.upload'))

    # Check AI config before saving file
    config = current_app.config['APP_CONFIG']
    from ai_service import check_ai_configured
    ai_ok, ai_error = check_ai_configured(config)
    if not ai_ok:
        flash(ai_error, 'danger')
        return redirect(url_for('import.upload'))

    # Save uploaded file
    uploads_dir = os.path.join(current_app.config['UPLOADS_PATH'], 'import')
    os.makedirs(uploads_dir, exist_ok=True)
    timestamp = int(time.time())
    filename = f"{timestamp}_{secure_filename(file.filename)}"
    filepath = os.path.join(uploads_dir, filename)
    file.save(filepath)

    # Create import_history record in 'processing' state.
    # tipo_import is set to 'inventario' as placeholder; the background thread
    # will update it once the document is classified.
    cursor = execute(
        """INSERT INTO import_history
           (tipo_import, filename, filepath, tipo_documento, divisione_id,
            stato, imported_by)
           VALUES ('inventario', ?, ?, ?, ?, 'processing', ?)""",
        (file.filename, f"import/{filename}", ext, divisione_id, g.user['id'])
    )
    import_id = cursor.lastrowid

    # Capture request-context values before leaving the request
    app_obj = current_app._get_current_object()
    uploads_path = current_app.config['UPLOADS_PATH']
    from ai_service import get_ai_config as _gac
    _struttura_id = getattr(g, 'struttura_id', None)
    _ai_cfg = _gac(struttura_id=_struttura_id, config=config)
    api_key = _ai_cfg['api_key']
    user_id = g.user['id']
    remote_addr = request.remote_addr

    t = threading.Thread(
        target=_run_import_async,
        args=(app_obj, import_id, filepath, ext, file.filename, filename,
              divisione_id, config, api_key, timestamp,
              user_id, remote_addr, uploads_path, _struttura_id),
        daemon=True,
        name=f'import-{import_id}',
    )
    t.start()

    return redirect(url_for('import.attendi', id=import_id))


@import_bp.route('/import/<int:id>/attendi')
@login_required
def attendi(id):
    """Waiting page: polls status while AI analysis runs in background."""
    import_rec = query_one("SELECT * FROM import_history WHERE id = ?", (id,))
    if not import_rec:
        flash('Import non trovato.', 'danger')
        return redirect(url_for('import.upload'))
    # If already done (e.g. page refresh), redirect straight to result
    if import_rec['stato'] == 'completed':
        return redirect(url_for('import.preview', id=id))
    if import_rec['stato'] == 'failed':
        flash(import_rec.get('errori_dettaglio') or 'Analisi fallita.', 'danger')
        return redirect(url_for('import.upload'))
    return render_template('import/attendi.html', import_rec=import_rec)


@import_bp.route('/import/<int:id>/stato')
@login_required
def stato(id):
    """Return current processing status as JSON (polled by attendi.html)."""
    import_rec = query_one(
        "SELECT stato, tipo_import, errori_dettaglio FROM import_history WHERE id = ?",
        (id,))
    if not import_rec:
        return jsonify({'stato': 'failed', 'errori_dettaglio': 'Import non trovato.'})

    resp = {'stato': import_rec['stato']}
    if import_rec['stato'] == 'completed':
        resp['redirect'] = url_for('import.preview', id=id)
        resp['tipo_label'] = DOC_TYPE_LABELS.get(
            import_rec['tipo_import'], import_rec['tipo_import'])
    elif import_rec['stato'] == 'failed':
        resp['errori_dettaglio'] = import_rec['errori_dettaglio'] or 'Errore sconosciuto.'
    return jsonify(resp)


# ---------------------------------------------------------------------------
# Background async analysis (no Flask request context)
# ---------------------------------------------------------------------------

def _run_import_async(app, import_id, filepath, ext, orig_name, safe_name,
                      divisione_id, config, api_key, timestamp,
                      user_id, remote_addr, uploads_path, struttura_id=None):
    """Thread target: classify + analyze document, populate import_preview."""
    with app.app_context():
        try:
            from ai_service import (
                extract_text_from_file,
                classify_document_type, classify_document_type_from_pdf,
                is_anthropic_provider,
            )

            text = extract_text_from_file(filepath, ext)
            is_scanned = not text or len(text.strip()) < 10

            from ai_service import get_ai_config
            ai_cfg = get_ai_config(struttura_id=struttura_id, config=config)
            classify_model = ai_cfg['model_email']
            if is_scanned and ext == 'pdf':
                if not is_anthropic_provider(config):
                    execute(
                        "UPDATE import_history SET stato='failed', errori_dettaglio=? WHERE id=?",
                        ('PDF scansionato non supportato con provider AI locale.', import_id))
                    return
                doc_type = classify_document_type_from_pdf(
                    filepath, api_key, classify_model, config=config)
            else:
                doc_type = classify_document_type(
                    text, api_key, classify_model, config=config)

            if doc_type not in ('inventario', 'verbale_manutenzione', 'verifica_elettrica'):
                execute(
                    "UPDATE import_history SET stato='failed', errori_dettaglio=? WHERE id=?",
                    ('Tipo documento non riconosciuto dall\'AI.', import_id))
                return

            execute("UPDATE import_history SET tipo_import=? WHERE id=?",
                    (doc_type, import_id))

            if doc_type == 'inventario':
                _run_inventario(import_id, filepath, ext, text, is_scanned,
                                orig_name, safe_name, divisione_id, config, api_key,
                                user_id, remote_addr, uploads_path,
                                struttura_id=struttura_id)
            elif doc_type == 'verbale_manutenzione':
                _run_verbali(import_id, filepath, ext, text, is_scanned,
                             orig_name, safe_name, divisione_id, config, api_key,
                             timestamp, user_id, remote_addr, uploads_path,
                             struttura_id=struttura_id)
            else:
                _run_verifiche(import_id, filepath, ext, text, is_scanned,
                               orig_name, safe_name, divisione_id, config, api_key,
                               timestamp, user_id, remote_addr, uploads_path,
                               struttura_id=struttura_id)

        except Exception as e:
            logger.error(f"Import async error (import_id={import_id}): {e}", exc_info=True)
            try:
                execute(
                    "UPDATE import_history SET stato='failed', errori_dettaglio=? WHERE id=?",
                    (str(e)[:1000], import_id))
            except Exception:
                pass


def _run_inventario(import_id, filepath, ext, text, is_scanned,
                    orig_name, safe_name, divisione_id, config, api_key,
                    user_id, remote_addr, uploads_path, struttura_id=None):
    """Analyze inventory document and populate import_preview. Runs in background thread."""
    from ai_service import (
        analyze_inventory_with_ai, analyze_inventory_from_pdf_document,
        find_duplicates, get_ai_config,
    )
    ai_cfg = get_ai_config(config=config, struttura_id=struttura_id)
    model = ai_cfg['model_import']

    if is_scanned and ext == 'pdf':
        items, ai_response = analyze_inventory_from_pdf_document(
            filepath, api_key, model, config=config)
        text_summary = f"[PDF scansionato — analisi diretta AI ({len(ai_response)} chars)]"
    else:
        items, ai_response = analyze_inventory_with_ai(text, api_key, model, config=config)
        text_summary = f"[System prompt + extracted text ({len(text)} chars)]"

    if not items:
        execute(
            "UPDATE import_history SET stato='failed', errori_dettaglio=? WHERE id=?",
            ('Nessun apparecchio trovato nel documento.', import_id))
        return

    enriched_items = find_duplicates(items, divisione_id, struttura_id=struttura_id)

    execute(
        """UPDATE import_history SET
           tipo_import='inventario', totale_righe=?, ai_prompt=?, ai_response=?
           WHERE id=?""",
        (len(items), text_summary, ai_response, import_id))

    for i, item in enumerate(enriched_items):
        execute(
            """INSERT INTO import_preview
               (import_id, riga_numero, dati_estratti, apparecchio_match_id,
                match_confidence, stato)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (import_id, i + 1, json.dumps(item['data']),
             item['match_id'], item['match_confidence'])
        )

    execute("UPDATE import_history SET stato='completed' WHERE id=?", (import_id,))
    log_attivita(user_id, 'import_analisi', 'import_history', import_id,
                 f"Inventario: {orig_name} ({len(items)} apparecchi trovati)", remote_addr)


def _run_verbali(import_id, filepath, ext, text, is_scanned,
                 orig_name, safe_name, divisione_id, config, api_key,
                 timestamp, user_id, remote_addr, uploads_path, struttura_id=None):
    """Analyze maintenance report(s). Runs in background thread."""
    from ai_service import (
        parse_verbale_with_ai, parse_verbale_from_pdf_document,
        get_pdf_page_count, extract_text_from_pdf_page, split_pdf_pages,
        get_ai_config,
    )
    ai_cfg = get_ai_config(config=config, struttura_id=struttura_id)
    model = ai_cfg['model_import']
    all_items = []

    if ext == 'pdf':
        page_count = get_pdf_page_count(filepath)
        if page_count > 1:
            pages_dir = os.path.join(uploads_path, 'import', f'pages_{timestamp}')
            page_paths = split_pdf_pages(filepath, pages_dir)
            for i, page_path in enumerate(page_paths):
                page_text = extract_text_from_pdf_page(filepath, i)
                page_is_scanned = not page_text or len(page_text.strip()) < 10
                try:
                    if page_is_scanned:
                        items, _ = parse_verbale_from_pdf_document(
                            page_path, api_key, model, config=config)
                    else:
                        items, _ = parse_verbale_with_ai(
                            page_text, api_key, model, config=config)
                    for item in items:
                        item['_pagina'] = i + 1
                        item['_page_file'] = os.path.relpath(page_path, uploads_path)
                    all_items.extend(items)
                except Exception as e:
                    all_items.append({
                        'matricola': '', 'tipo': '', 'data_intervento': '',
                        'descrizione': f'Errore analisi pagina {i+1}: {e}',
                        '_pagina': i + 1,
                        '_page_file': os.path.relpath(page_path, uploads_path),
                        '_errore': True,
                    })
        else:
            if is_scanned:
                items, _ = parse_verbale_from_pdf_document(
                    filepath, api_key, model, config=config)
            else:
                items, _ = parse_verbale_with_ai(text, api_key, model, config=config)
            for item in items:
                item['_pagina'] = 1
                item['_page_file'] = f"import/{safe_name}"
            all_items = items
    else:
        items, _ = parse_verbale_with_ai(text, api_key, model, config=config)
        for item in items:
            item['_pagina'] = 0
        all_items = items

    if not all_items:
        execute(
            "UPDATE import_history SET stato='failed', errori_dettaglio=? WHERE id=?",
            ('Nessun intervento di manutenzione trovato nel documento.', import_id))
        return

    _match_apparecchi(all_items, struttura_id=struttura_id)

    execute(
        """UPDATE import_history SET
           tipo_import='verbale_manutenzione', totale_righe=?, ai_prompt=?, ai_response=?
           WHERE id=?""",
        (len(all_items),
         f"[VERBALE_SYSTEM_PROMPT — {len(all_items)} interventi]",
         json.dumps(all_items, ensure_ascii=False), import_id))

    for i, item in enumerate(all_items):
        match_id = item.pop('_match_id', None)
        execute(
            """INSERT INTO import_preview
               (import_id, riga_numero, dati_estratti, apparecchio_match_id,
                match_confidence, stato)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (import_id, i + 1, json.dumps(item, ensure_ascii=False),
             match_id, 1.0 if match_id else 0.0)
        )

    execute("UPDATE import_history SET stato='completed' WHERE id=?", (import_id,))
    log_attivita(user_id, 'import_analisi', 'import_history', import_id,
                 f"Verbali: {orig_name} ({len(all_items)} interventi trovati)", remote_addr)


def _run_verifiche(import_id, filepath, ext, text, is_scanned,
                   orig_name, safe_name, divisione_id, config, api_key,
                   timestamp, user_id, remote_addr, uploads_path, struttura_id=None):
    """Analyze electrical safety verification(s). Runs in background thread."""
    from ai_service import (
        analyze_verifiche_with_ai, analyze_verifiche_from_pdf_document,
        get_pdf_page_count, extract_text_from_pdf_page, split_pdf_pages,
        get_ai_config,
    )
    ai_cfg = get_ai_config(config=config, struttura_id=struttura_id)
    model = ai_cfg['model_import']
    all_items = []

    if ext == 'pdf':
        page_count = get_pdf_page_count(filepath)
        if page_count > 1:
            pages_dir = os.path.join(uploads_path, 'import', f'pages_{timestamp}')
            page_paths = split_pdf_pages(filepath, pages_dir)
            for i, page_path in enumerate(page_paths):
                page_text = extract_text_from_pdf_page(filepath, i)
                page_is_scanned = not page_text or len(page_text.strip()) < 10
                try:
                    if page_is_scanned:
                        items, _ = analyze_verifiche_from_pdf_document(
                            page_path, api_key, model, config=config)
                    else:
                        items, _ = analyze_verifiche_with_ai(
                            page_text, api_key, model, config=config)
                    for item in items:
                        item['_pagina'] = i + 1
                        item['_page_file'] = os.path.relpath(page_path, uploads_path)
                    all_items.extend(items)
                except Exception as e:
                    all_items.append({
                        'matricola': '', 'data_verifica': '', 'esito': '',
                        'note': f'Errore analisi pagina {i+1}: {e}',
                        '_pagina': i + 1,
                        '_page_file': os.path.relpath(page_path, uploads_path),
                        '_errore': True,
                    })
        else:
            if is_scanned:
                items, _ = analyze_verifiche_from_pdf_document(
                    filepath, api_key, model, config=config)
            else:
                items, _ = analyze_verifiche_with_ai(text, api_key, model, config=config)
            for item in items:
                item['_pagina'] = 1
                item['_page_file'] = f"import/{safe_name}"
            all_items = items
    else:
        items, _ = analyze_verifiche_with_ai(text, api_key, model, config=config)
        for item in items:
            item['_pagina'] = 0
        all_items = items

    if not all_items:
        execute(
            "UPDATE import_history SET stato='failed', errori_dettaglio=? WHERE id=?",
            ('Nessuna verifica di sicurezza trovata nel documento.', import_id))
        return

    _match_apparecchi(all_items, struttura_id=struttura_id)

    execute(
        """UPDATE import_history SET
           tipo_import='verifica_elettrica', totale_righe=?, ai_prompt=?, ai_response=?
           WHERE id=?""",
        (len(all_items),
         f"[VERIFICA_BATCH_SYSTEM_PROMPT — {len(all_items)} verifiche]",
         json.dumps(all_items, ensure_ascii=False), import_id))

    for i, item in enumerate(all_items):
        match_id = item.pop('_match_id', None)
        execute(
            """INSERT INTO import_preview
               (import_id, riga_numero, dati_estratti, apparecchio_match_id,
                match_confidence, stato)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (import_id, i + 1, json.dumps(item, ensure_ascii=False),
             match_id, 1.0 if match_id else 0.0)
        )

    execute("UPDATE import_history SET stato='completed' WHERE id=?", (import_id,))
    log_attivita(user_id, 'import_analisi', 'import_history', import_id,
                 f"Verifiche: {orig_name} ({len(all_items)} verifiche trovate)", remote_addr)


def _match_apparecchi(items, struttura_id=None):
    """Match matricole in items to existing apparecchi. Sets _match_id on each item.
    Filtra per struttura_id quando disponibile per garantire isolamento multi-tenant.
    """
    matricole = list({(item.get('matricola') or '').strip() for item in items} - {''})
    lookup = {}
    if matricole:
        placeholders = ','.join('?' * len(matricole))
        if struttura_id:
            rows = query_all(
                f"SELECT id, matricola FROM apparecchi WHERE LOWER(matricola) IN ({placeholders}) AND stato != 'dismesso' AND struttura_id = ?",
                [m.lower() for m in matricole] + [struttura_id])
        else:
            rows = query_all(
                f"SELECT id, matricola FROM apparecchi WHERE LOWER(matricola) IN ({placeholders}) AND stato != 'dismesso'",
                [m.lower() for m in matricole])
        lookup = {r['matricola'].lower(): r['id'] for r in rows}
    for item in items:
        matricola = (item.get('matricola') or '').strip().lower()
        item['_match_id'] = lookup.get(matricola)


# ---------------------------------------------------------------------------
# Preview & Execute (unified for all types)
# ---------------------------------------------------------------------------

@import_bp.route('/import/<int:id>/preview')
@login_required
def preview(id):
    """Preview page: show extracted items with match info. Adapts to tipo_import."""
    import_rec = query_one("SELECT * FROM import_history WHERE id = ?", (id,))
    if not import_rec:
        flash('Import non trovato.', 'danger')
        return redirect(url_for('import.upload'))

    rows = query_all(
        """SELECT ip.*, a.marca as match_marca, a.modello as match_modello,
                  a.matricola as match_matricola
           FROM import_preview ip
           LEFT JOIN apparecchi a ON ip.apparecchio_match_id = a.id
           WHERE ip.import_id = ?
           ORDER BY ip.riga_numero""",
        (id,)
    )

    for row in rows:
        try:
            row['parsed_data'] = json.loads(row['dati_estratti'])
        except (json.JSONDecodeError, TypeError):
            row['parsed_data'] = {}

    nuovi = sum(1 for r in rows if not r['apparecchio_match_id'])
    trovati = sum(1 for r in rows if r['apparecchio_match_id'])

    # For verbali/verifiche: provide apparecchi list for manual selection
    apparecchi_list = []
    tipo = import_rec['tipo_import']
    if tipo in ('verbale_manutenzione', 'verifica_elettrica'):
        div = getattr(g, 'divisione_attiva', None)
        if div and div.get('id') != 'tutte':
            apparecchi_list = query_all(
                """SELECT a.id, a.matricola, a.marca, a.modello, d.nome as divisione_nome
                   FROM apparecchi a
                   LEFT JOIN divisioni d ON a.divisione_id = d.id
                   WHERE a.stato != 'dismesso' AND a.divisione_id = ?
                   ORDER BY a.matricola""",
                [div['id']]
            )
        elif getattr(g, 'user', {}).get('ruolo') == 'admin':
            apparecchi_list = query_all(
                """SELECT a.id, a.matricola, a.marca, a.modello, d.nome as divisione_nome
                   FROM apparecchi a
                   LEFT JOIN divisioni d ON a.divisione_id = d.id
                   WHERE a.stato != 'dismesso'
                   ORDER BY a.matricola"""
            )
        else:
            ids = [d['id'] for d in getattr(g, 'divisioni', [])]
            if ids:
                ph = ','.join('?' * len(ids))
                apparecchi_list = query_all(
                    f"""SELECT a.id, a.matricola, a.marca, a.modello, d.nome as divisione_nome
                       FROM apparecchi a
                       LEFT JOIN divisioni d ON a.divisione_id = d.id
                       WHERE a.stato != 'dismesso' AND a.divisione_id IN ({ph})
                       ORDER BY a.matricola""",
                    ids
                )

    tipo_label = DOC_TYPE_LABELS.get(tipo, tipo)

    return render_template('import/preview.html',
                           import_rec=import_rec, rows=rows,
                           nuovi=nuovi, trovati=trovati,
                           tipo_label=tipo_label,
                           apparecchi_list=apparecchi_list)


@import_bp.route('/import/<int:id>/esegui', methods=['POST'])
@login_required
def esegui(id):
    """Execute import for selected rows. Branches by tipo_import."""
    import_rec = query_one("SELECT * FROM import_history WHERE id = ?", (id,))
    if not import_rec:
        flash('Import non trovato.', 'danger')
        return redirect(url_for('import.upload'))

    selected_ids = request.form.getlist('selected')
    if not selected_ids:
        flash('Nessuna riga selezionata.', 'warning')
        return redirect(url_for('import.preview', id=id))

    tipo = import_rec['tipo_import']

    if tipo == 'inventario':
        imported, errors = _execute_inventario(id, selected_ids, import_rec)
    elif tipo == 'verbale_manutenzione':
        imported, errors = _execute_verbali(id, selected_ids, import_rec)
    elif tipo == 'verifica_elettrica':
        imported, errors = _execute_verifiche(id, selected_ids, import_rec)
    else:
        flash(f'Tipo import non riconosciuto: {tipo}', 'danger')
        return redirect(url_for('import.preview', id=id))

    execute(
        """UPDATE import_history SET
           stato = 'completed', righe_importate = ?, righe_errori = ?,
           completed_at = datetime('now')
           WHERE id = ?""",
        (imported, errors, id)
    )

    log_attivita(g.user['id'], 'import_esecuzione', 'import_history', id,
                 f"{DOC_TYPE_LABELS.get(tipo, tipo)}: {imported} importati, {errors} errori",
                 request.remote_addr)

    flash(f'Import completato: {imported} importati, {errors} errori.', 'success')

    if tipo == 'inventario':
        return redirect(url_for('apparecchi.lista'))
    elif tipo == 'verbale_manutenzione':
        return redirect(url_for('manutenzioni.lista'))
    else:
        return redirect(url_for('verifiche.lista'))


def _execute_inventario(import_id, selected_ids, import_rec):
    """Insert/update apparecchi from inventory import."""
    imported = 0
    errors = 0

    for row_id in selected_ids:
        row = query_one("SELECT * FROM import_preview WHERE id = ? AND import_id = ?",
                        (int(row_id), import_id))
        if not row:
            continue
        try:
            data = json.loads(row['dati_estratti'])
            if row['apparecchio_match_id']:
                execute(
                    """UPDATE apparecchi SET
                       descrizione = COALESCE(descrizione, ?),
                       anno_fabbricazione = COALESCE(anno_fabbricazione, ?),
                       classificazione = COALESCE(classificazione, ?),
                       ubicazione = COALESCE(?, ubicazione),
                       fornitore = COALESCE(?, fornitore),
                       codice_fornitore = COALESCE(codice_fornitore, ?),
                       garanzia_scadenza = COALESCE(garanzia_scadenza, ?),
                       contratto_manutenzione = COALESCE(contratto_manutenzione, ?),
                       note = COALESCE(?, note),
                       updated_by = ?, updated_at = datetime('now')
                       WHERE id = ?""",
                    (data.get('descrizione'), data.get('anno_fabbricazione'),
                     data.get('classificazione'),
                     data.get('ubicazione'), data.get('fornitore'),
                     data.get('codice_fornitore'), data.get('garanzia_scadenza'),
                     data.get('contratto_manutenzione'),
                     data.get('note'), g.user['id'], row['apparecchio_match_id'])
                )
            else:
                # Deriva struttura_id dalla divisione selezionata
                div_row = query_one(
                    "SELECT struttura_id FROM divisioni WHERE id=?",
                    (import_rec['divisione_id'],)
                )
                imp_struttura_id = (div_row['struttura_id'] if div_row
                                    else getattr(g, 'struttura_id', None))
                execute(
                    """INSERT INTO apparecchi
                       (divisione_id, struttura_id, matricola, descrizione, numero_inventario,
                        marca, modello, anno_fabbricazione, classificazione,
                        ubicazione, fornitore, codice_fornitore, garanzia_scadenza,
                        contratto_manutenzione, ip_address, note, created_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (import_rec['divisione_id'],
                     imp_struttura_id,
                     data.get('matricola', ''),
                     data.get('descrizione'),
                     data.get('numero_inventario'),
                     data.get('marca', ''),
                     data.get('modello', ''),
                     data.get('anno_fabbricazione'),
                     data.get('classificazione'),
                     data.get('ubicazione'),
                     data.get('fornitore'),
                     data.get('codice_fornitore'),
                     data.get('garanzia_scadenza'),
                     data.get('contratto_manutenzione'),
                     data.get('ip_address'),
                     data.get('note'),
                     g.user['id'])
                )
            execute("UPDATE import_preview SET stato = 'imported' WHERE id = ?", (int(row_id),))
            imported += 1
        except Exception as e:
            execute("UPDATE import_preview SET stato = 'rejected', note_revisione = ? WHERE id = ?",
                    (str(e), int(row_id)))
            errors += 1

    return imported, errors


def _execute_verbali(import_id, selected_ids, import_rec):
    """Insert manutenzioni from verbale import. Copies page PDF as verbale."""
    imported = 0
    errors = 0
    batch_ts = int(time.time())

    for idx, row_id in enumerate(selected_ids):
        row = query_one("SELECT * FROM import_preview WHERE id = ? AND import_id = ?",
                        (int(row_id), import_id))
        if not row:
            continue
        try:
            data = json.loads(row['dati_estratti'])

            # Bug K: skip error items produced during page analysis
            if data.get('_errore'):
                execute("UPDATE import_preview SET stato = 'rejected', "
                        "note_revisione = 'Errore analisi pagina' WHERE id = ?",
                        (int(row_id),))
                errors += 1
                continue

            # Determine apparecchio: user override or AI match
            app_override = request.form.get(f'apparecchio_id_{row_id}')
            if app_override:
                try:
                    apparecchio_id = int(app_override)
                except (ValueError, TypeError):
                    apparecchio_id = None
                # Bug I: validate override is within accessible divisions
                if apparecchio_id and g.user['ruolo'] != 'admin':
                    accessible_ids = [d['id'] for d in g.divisioni]
                    app_rec = query_one(
                        "SELECT divisione_id FROM apparecchi WHERE id = ?", (apparecchio_id,))
                    if not app_rec or app_rec['divisione_id'] not in accessible_ids:
                        apparecchio_id = None
            else:
                apparecchio_id = row['apparecchio_match_id']

            if not apparecchio_id:
                execute("UPDATE import_preview SET stato = 'rejected', "
                        "note_revisione = 'Nessun apparecchio associato' WHERE id = ?",
                        (int(row_id),))
                errors += 1
                continue

            # Bug E: validate required date field
            data_intervento = (data.get('data_intervento') or '').strip()
            if not data_intervento:
                raise ValueError("data_intervento mancante")

            # Normalize tipo to valid set
            tipo_raw = (data.get('tipo') or 'preventiva').strip().lower()
            tipo = tipo_raw if tipo_raw in TIPI_VALIDI_MANUTENZIONE else 'preventiva'

            # Copy page PDF to verbali folder if available
            verbale_path = None
            page_file = data.get('_page_file')
            if page_file:
                src = os.path.join(current_app.config['UPLOADS_PATH'], page_file)
                if os.path.exists(src):
                    verbali_dir = os.path.join(current_app.config['UPLOADS_PATH'], 'verbali')
                    os.makedirs(verbali_dir, exist_ok=True)
                    dest_name = f"{batch_ts}_{idx}_{os.path.basename(page_file)}"
                    dest = os.path.join(verbali_dir, dest_name)
                    shutil.copy2(src, dest)
                    verbale_path = f"verbali/{dest_name}"

            # Safe periodicita conversion (handles floats like "365.0")
            periodicita_giorni = None
            if data.get('periodicita_giorni'):
                try:
                    periodicita_giorni = int(float(data['periodicita_giorni']))
                except (ValueError, TypeError):
                    periodicita_giorni = None

            try:
                costo = float(data['costo']) if data.get('costo') else None
            except (ValueError, TypeError):
                costo = None

            cursor = execute(
                """INSERT INTO manutenzioni
                   (apparecchio_id, tipo, data_intervento, prossima_scadenza,
                    periodicita_giorni, tecnico_ditta, descrizione, esito,
                    costo, verbale_path, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    apparecchio_id,
                    tipo,
                    data_intervento,
                    data.get('prossima_scadenza') or None,
                    periodicita_giorni,
                    data.get('tecnico_ditta'),
                    data.get('descrizione'),
                    data.get('esito'),
                    costo,
                    verbale_path,
                    g.user['id']
                )
            )
            execute("UPDATE import_preview SET stato = 'imported' WHERE id = ?", (int(row_id),))
            imported += 1
        except Exception as e:
            execute("UPDATE import_preview SET stato = 'rejected', note_revisione = ? WHERE id = ?",
                    (str(e), int(row_id)))
            errors += 1

    return imported, errors


def _execute_verifiche(import_id, selected_ids, import_rec):
    """Insert verifiche from verification import. Copies page PDF as documento."""
    from datetime import datetime, timedelta
    imported = 0
    errors = 0
    batch_ts = int(time.time())

    for idx, row_id in enumerate(selected_ids):
        row = query_one("SELECT * FROM import_preview WHERE id = ? AND import_id = ?",
                        (int(row_id), import_id))
        if not row:
            continue
        try:
            data = json.loads(row['dati_estratti'])

            # Bug K: skip error items produced during page analysis
            if data.get('_errore'):
                execute("UPDATE import_preview SET stato = 'rejected', "
                        "note_revisione = 'Errore analisi pagina' WHERE id = ?",
                        (int(row_id),))
                errors += 1
                continue

            app_override = request.form.get(f'apparecchio_id_{row_id}')
            if app_override:
                try:
                    apparecchio_id = int(app_override)
                except (ValueError, TypeError):
                    apparecchio_id = None
                # Bug I: validate override is within accessible divisions
                if apparecchio_id and g.user['ruolo'] != 'admin':
                    accessible_ids = [d['id'] for d in g.divisioni]
                    app_rec = query_one(
                        "SELECT divisione_id FROM apparecchi WHERE id = ?", (apparecchio_id,))
                    if not app_rec or app_rec['divisione_id'] not in accessible_ids:
                        apparecchio_id = None
            else:
                apparecchio_id = row['apparecchio_match_id']

            if not apparecchio_id:
                execute("UPDATE import_preview SET stato = 'rejected', "
                        "note_revisione = 'Nessun apparecchio associato' WHERE id = ?",
                        (int(row_id),))
                errors += 1
                continue

            # Bug E: validate required date field
            data_verifica = (data.get('data_verifica') or '').strip()
            if not data_verifica:
                raise ValueError("data_verifica mancante")

            # Normalize esito to valid set
            esito_raw = (data.get('esito') or 'positivo').strip().lower()
            esito = esito_raw if esito_raw in ESITI_VALIDI_VERIFICA else 'positivo'

            # Copy page PDF to verifiche folder if available
            documento_path = None
            page_file = data.get('_page_file')
            if page_file:
                src = os.path.join(current_app.config['UPLOADS_PATH'], page_file)
                if os.path.exists(src):
                    verifiche_dir = os.path.join(current_app.config['UPLOADS_PATH'], 'verifiche')
                    os.makedirs(verifiche_dir, exist_ok=True)
                    dest_name = f"{batch_ts}_{idx}_{os.path.basename(page_file)}"
                    dest = os.path.join(verifiche_dir, dest_name)
                    shutil.copy2(src, dest)
                    documento_path = f"verifiche/{dest_name}"

            # Bug F: safe periodicita conversion (handles floats like "365.0"); default 730 (2 anni)
            periodicita = int(float(data.get('periodicita_giorni') or 730))
            prossima = data.get('prossima_scadenza') or None
            if not prossima:
                try:
                    d = datetime.strptime(data_verifica, '%Y-%m-%d')
                    d += timedelta(days=periodicita)
                    prossima = d.strftime('%Y-%m-%d')
                except ValueError:
                    pass

            cursor = execute(
                """INSERT INTO verifiche
                   (apparecchio_id, data_verifica, prossima_scadenza,
                    periodicita_giorni, esito, tecnico_ditta, note,
                    documento_path, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    apparecchio_id,
                    data_verifica,
                    prossima,
                    periodicita,
                    esito,
                    data.get('tecnico_ditta'),
                    data.get('note'),
                    documento_path,
                    g.user['id']
                )
            )
            execute("UPDATE import_preview SET stato = 'imported' WHERE id = ?", (int(row_id),))
            imported += 1
        except Exception as e:
            execute("UPDATE import_preview SET stato = 'rejected', note_revisione = ? WHERE id = ?",
                    (str(e), int(row_id)))
            errors += 1

    return imported, errors


@import_bp.route('/import/storico')
@login_required
def storico():
    """Import history. Filtrato per struttura dell'utente."""
    struttura_id = getattr(g, 'struttura_id', None)
    if struttura_id:
        imports = query_all(
            """SELECT ih.*, d.nome as divisione_nome, u.nome || ' ' || u.cognome as utente_nome
               FROM import_history ih
               LEFT JOIN divisioni d ON ih.divisione_id = d.id
               LEFT JOIN utenti u ON ih.imported_by = u.id
               WHERE d.struttura_id = ?
               ORDER BY ih.created_at DESC
               LIMIT 50""",
            (struttura_id,)
        )
    else:
        imports = query_all(
            """SELECT ih.*, d.nome as divisione_nome, u.nome || ' ' || u.cognome as utente_nome
               FROM import_history ih
               LEFT JOIN divisioni d ON ih.divisione_id = d.id
               LEFT JOIN utenti u ON ih.imported_by = u.id
               ORDER BY ih.created_at DESC
               LIMIT 50"""
        )
    return render_template('import/storico.html', imports=imports)


# ============================================================================
# EMAIL QUEUE (verbali received via IMAP)
# ============================================================================

@import_bp.route('/import/email')
@login_required
def email_queue():
    """Email verbale queue: pending items for manual review. Filtrato per struttura."""
    struttura_id = getattr(g, 'struttura_id', None)
    if struttura_id:
        pending = query_all(
            """SELECT ih.*, d.nome as divisione_nome
               FROM import_history ih
               LEFT JOIN divisioni d ON ih.divisione_id = d.id
               WHERE ih.tipo_import = 'verbale_email' AND ih.stato = 'pending'
                     AND d.struttura_id = ?
               ORDER BY ih.created_at DESC""",
            (struttura_id,)
        )
    else:
        pending = query_all(
            """SELECT ih.*, d.nome as divisione_nome
               FROM import_history ih
               LEFT JOIN divisioni d ON ih.divisione_id = d.id
               WHERE ih.tipo_import = 'verbale_email' AND ih.stato = 'pending'
               ORDER BY ih.created_at DESC"""
        )

    for item in pending:
        parsed = _parse_email_ai_response(item.get('ai_response'))
        item['matricola_estratta'] = parsed.get('matricola', '')
        item['tipo_estratto'] = parsed.get('tipo', '')

    if struttura_id:
        counts = query_one(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN ih.stato = 'completed' THEN 1 ELSE 0 END) as completed,
                      SUM(CASE WHEN ih.stato = 'failed' THEN 1 ELSE 0 END) as failed
               FROM import_history ih
               LEFT JOIN divisioni d ON ih.divisione_id = d.id
               WHERE ih.tipo_import = 'verbale_email' AND d.struttura_id = ?""",
            (struttura_id,)
        )
    else:
        counts = query_one(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN stato = 'completed' THEN 1 ELSE 0 END) as completed,
                      SUM(CASE WHEN stato = 'failed' THEN 1 ELSE 0 END) as failed
               FROM import_history WHERE tipo_import = 'verbale_email'"""
        )
    completed_count = counts['completed'] or 0
    failed_count = counts['failed'] or 0
    total_count = counts['total'] or 0

    if struttura_id:
        recent_completed = query_all(
            """SELECT ih.*, d.nome as divisione_nome
               FROM import_history ih
               LEFT JOIN divisioni d ON ih.divisione_id = d.id
               WHERE ih.tipo_import = 'verbale_email' AND ih.stato = 'completed'
                     AND d.struttura_id = ?
               ORDER BY ih.created_at DESC LIMIT 10""",
            (struttura_id,)
        )
    else:
        recent_completed = query_all(
            """SELECT ih.*, d.nome as divisione_nome
               FROM import_history ih
               LEFT JOIN divisioni d ON ih.divisione_id = d.id
               WHERE ih.tipo_import = 'verbale_email' AND ih.stato = 'completed'
               ORDER BY ih.created_at DESC LIMIT 10"""
        )

    return render_template('import/email_queue.html',
                           pending=pending,
                           completed_count=completed_count,
                           failed_count=failed_count,
                           total_count=total_count,
                           recent_completed=recent_completed)


@import_bp.route('/import/email/<int:id>')
@login_required
def email_dettaglio(id):
    """Detail view for a pending email verbale. Filtrato per struttura."""
    record = query_one("SELECT * FROM import_history WHERE id = ?", (id,))
    if not record:
        flash('Record non trovato.', 'danger')
        return redirect(url_for('import.email_queue'))

    parsed = _parse_email_ai_response(record.get('ai_response'))

    struttura_id = getattr(g, 'struttura_id', None)

    apparecchio = None
    matricola = parsed.get('matricola', '').strip()
    if matricola:
        if struttura_id:
            apparecchio = query_one(
                """SELECT a.*, d.nome as divisione_nome
                   FROM apparecchi a
                   LEFT JOIN divisioni d ON a.divisione_id = d.id
                   WHERE a.matricola = ? AND a.stato != 'dismesso' AND a.struttura_id = ?""",
                (matricola, struttura_id)
            )
        else:
            apparecchio = query_one(
                """SELECT a.*, d.nome as divisione_nome
                   FROM apparecchi a
                   LEFT JOIN divisioni d ON a.divisione_id = d.id
                   WHERE a.matricola = ? AND a.stato != 'dismesso'""",
                (matricola,)
            )

    if struttura_id:
        apparecchi_list = query_all(
            """SELECT a.id, a.matricola, a.marca, a.modello, d.nome as divisione_nome
               FROM apparecchi a
               LEFT JOIN divisioni d ON a.divisione_id = d.id
               WHERE a.stato != 'dismesso' AND a.struttura_id = ?
               ORDER BY a.matricola""",
            (struttura_id,)
        )
    else:
        apparecchi_list = query_all(
            """SELECT a.id, a.matricola, a.marca, a.modello, d.nome as divisione_nome
               FROM apparecchi a
               LEFT JOIN divisioni d ON a.divisione_id = d.id
               WHERE a.stato != 'dismesso'
               ORDER BY a.matricola"""
        )

    return render_template('import/email_dettaglio.html',
                           record=record, parsed=parsed,
                           apparecchio=apparecchio,
                           apparecchi_list=apparecchi_list)


@import_bp.route('/import/email/<int:id>/conferma', methods=['POST'])
@login_required
def email_conferma(id):
    """Confirm and import a pending email verbale."""
    record = query_one("SELECT * FROM import_history WHERE id = ?", (id,))
    if not record:
        flash('Record non trovato.', 'danger')
        return redirect(url_for('import.email_queue'))

    apparecchio_id = request.form.get('apparecchio_id')
    if not apparecchio_id:
        flash('Seleziona un apparecchio.', 'warning')
        return redirect(url_for('import.email_dettaglio', id=id))

    # FIX 5: verifica che l'apparecchio appartenga alla struttura dell'utente
    if apparecchio_id:
        struttura_id = getattr(g, 'struttura_id', None)
        if struttura_id:
            appar = query_one(
                "SELECT id FROM apparecchi WHERE id = ? AND struttura_id = ?",
                (apparecchio_id, struttura_id)
            )
            if not appar:
                flash('Apparecchio non trovato o non accessibile.', 'danger')
                return redirect(url_for('import.email_queue'))

    # FIX 7: validazione tipo manutenzione
    TIPI_VALIDI = ('preventiva', 'correttiva', 'straordinaria', 'verifica_elettrica', 'collaudo')
    tipo = request.form.get('tipo', 'preventiva')
    if tipo not in TIPI_VALIDI:
        tipo = 'preventiva'

    # FIX 8: gestione sicura periodicita_giorni e costo
    periodicita_raw = request.form.get('periodicita_giorni')
    try:
        periodicita = int(float(periodicita_raw)) if periodicita_raw else None
    except (ValueError, TypeError):
        periodicita = None

    costo_raw = request.form.get('costo')
    try:
        costo = float(costo_raw) if costo_raw else None
    except (ValueError, TypeError):
        costo = None

    try:
        execute(
            """INSERT INTO manutenzioni
               (apparecchio_id, tipo, data_intervento, prossima_scadenza,
                periodicita_giorni, tecnico_ditta, descrizione, esito, costo, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(apparecchio_id),
                tipo,
                request.form.get('data_intervento'),
                request.form.get('prossima_scadenza') or None,
                periodicita,
                request.form.get('tecnico_ditta') or None,
                request.form.get('descrizione') or None,
                request.form.get('esito') or None,
                costo,
                g.user['id']
            )
        )

        execute(
            """UPDATE import_history SET stato = 'completed', righe_importate = 1,
                      completed_at = datetime('now') WHERE id = ?""",
            (id,)
        )

        log_attivita(g.user['id'], 'import_email_conferma', 'import_history', id,
                     f"Confermato verbale email: {record.get('filename', '')}", request.remote_addr)

        flash('Manutenzione importata con successo dal verbale email.', 'success')
        return redirect(url_for('import.email_queue'))

    except Exception as e:
        flash(f'Errore durante l\'importazione: {str(e)}', 'danger')
        return redirect(url_for('import.email_dettaglio', id=id))


@import_bp.route('/import/email/<int:id>/scarta', methods=['POST'])
@login_required
def email_scarta(id):
    """Discard a pending email verbale."""
    record = query_one("SELECT * FROM import_history WHERE id = ?", (id,))
    if not record:
        flash('Record non trovato.', 'danger')
        return redirect(url_for('import.email_queue'))

    execute(
        "UPDATE import_history SET stato = 'failed', errori_dettaglio = 'Scartato manualmente' WHERE id = ?",
        (id,)
    )

    log_attivita(g.user['id'], 'import_email_scarta', 'import_history', id,
                 f"Scartato verbale email: {record.get('filename', '')}", request.remote_addr)

    flash('Verbale email scartato.', 'info')
    return redirect(url_for('import.email_queue'))
