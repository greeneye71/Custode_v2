"""
MedInventory - Import Blueprint
AI-powered unified import: upload, classify, analyze, preview, approve.
Supports: inventario, verbale di manutenzione, verifica di sicurezza elettrica.
"""

import os
import json
import time
import shutil

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, g, current_app
)
from werkzeug.utils import secure_filename

from auth import login_required
from models import query_one, query_all, execute, log_attivita

import_bp = Blueprint('import', __name__)

ALLOWED_IMPORT_EXT = {'xlsx', 'xls', 'pdf', 'csv'}


def _parse_email_ai_response(raw):
    """Parse AI response JSON, handling string-wrapped JSON."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, str):
            start = parsed.find('{')
            end = parsed.rfind('}') + 1
            if start >= 0 and end > start:
                return json.loads(parsed[start:end])
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}

DOC_TYPE_LABELS = {
    'inventario': 'Inventario',
    'verbale_manutenzione': 'Verbale di Manutenzione',
    'verifica_elettrica': 'Verifica di Sicurezza Elettrica',
}


@import_bp.route('/import')
@login_required
def upload():
    """Upload page for unified document import."""
    return render_template('import/upload.html', divisioni=g.divisioni)


@import_bp.route('/import/analizza', methods=['POST'])
@login_required
def analizza():
    """Upload file, classify document type, analyze with AI, create preview."""
    file = request.files.get('file')
    divisione_id = request.form.get('divisione_id')

    if not file or not file.filename:
        flash('Nessun file selezionato.', 'warning')
        return redirect(url_for('import.upload'))

    if not divisione_id:
        flash('Seleziona una divisione.', 'warning')
        return redirect(url_for('import.upload'))

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_IMPORT_EXT:
        flash(f'Formato non supportato. Usa: {", ".join(ALLOWED_IMPORT_EXT)}', 'danger')
        return redirect(url_for('import.upload'))

    # Save uploaded file
    uploads_dir = os.path.join(current_app.config['UPLOADS_PATH'], 'import')
    os.makedirs(uploads_dir, exist_ok=True)
    timestamp = int(time.time())
    filename = f"{timestamp}_{secure_filename(file.filename)}"
    filepath = os.path.join(uploads_dir, filename)
    file.save(filepath)

    # Check AI config
    config = current_app.config['APP_CONFIG']
    api_key = config.get('anthropic_api_key', '')

    from ai_service import (
        extract_text_from_file, check_ai_configured,
        classify_document_type, classify_document_type_from_pdf
    )

    ai_ok, ai_error = check_ai_configured(config)
    if not ai_ok:
        flash(ai_error, 'danger')
        return redirect(url_for('import.upload'))

    try:
        # Step 1: Extract text
        text = extract_text_from_file(filepath, ext)
        is_scanned = not text or len(text.strip()) < 10

        # Step 2: Classify document type
        classify_model = config.get('ai_email_model', 'claude-haiku-4-5-20251001')
        if is_scanned and ext == 'pdf':
            from ai_service import is_anthropic_provider
            if not is_anthropic_provider(config):
                flash('PDF scansionato: non supportato con provider AI locale. '
                      'Utilizzare Anthropic Claude.', 'danger')
                return redirect(url_for('import.upload'))
            doc_type = classify_document_type_from_pdf(
                filepath, api_key, classify_model, config=config)
        else:
            doc_type = classify_document_type(
                text, api_key, classify_model, config=config)

        # Step 3: Route to type-specific analysis
        if doc_type == 'inventario':
            return _process_inventario(
                filepath, ext, text, is_scanned, file.filename, filename,
                int(divisione_id), config, api_key)
        elif doc_type == 'verbale_manutenzione':
            return _process_verbali(
                filepath, ext, text, is_scanned, file.filename, filename,
                int(divisione_id), config, api_key, timestamp)
        elif doc_type == 'verifica_elettrica':
            return _process_verifiche(
                filepath, ext, text, is_scanned, file.filename, filename,
                int(divisione_id), config, api_key, timestamp)

    except Exception as e:
        flash(f'Errore durante l\'analisi: {str(e)}', 'danger')
        return redirect(url_for('import.upload'))


# ---------------------------------------------------------------------------
# Type-specific analysis helpers
# ---------------------------------------------------------------------------

def _process_inventario(filepath, ext, text, is_scanned, orig_name, safe_name,
                        divisione_id, config, api_key):
    """Analyze as inventory document (existing flow)."""
    from ai_service import (
        analyze_inventory_with_ai, analyze_inventory_from_pdf_document,
        find_duplicates
    )
    model = config.get('ai_import_model', 'claude-sonnet-4-20250514')

    if is_scanned and ext == 'pdf':
        items, ai_response = analyze_inventory_from_pdf_document(
            filepath, api_key, model, config=config)
        text = f"[PDF scansionato — analisi diretta AI ({len(ai_response)} chars)]"
    else:
        items, ai_response = analyze_inventory_with_ai(
            text, api_key, model, config=config)

    if not items:
        flash("L'analisi AI non ha trovato apparecchi nel documento.", 'warning')
        return redirect(url_for('import.upload'))

    enriched_items = find_duplicates(items, divisione_id)

    cursor = execute(
        """INSERT INTO import_history
           (tipo_import, filename, filepath, tipo_documento, divisione_id,
            totale_righe, stato, ai_prompt, ai_response, imported_by)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
        ('inventario', orig_name, f"import/{safe_name}", ext,
         divisione_id, len(items),
         f"[System prompt + extracted text ({len(text)} chars)]",
         ai_response, g.user['id'])
    )
    import_id = cursor.lastrowid

    for i, item in enumerate(enriched_items):
        execute(
            """INSERT INTO import_preview
               (import_id, riga_numero, dati_estratti, apparecchio_match_id,
                match_confidence, stato)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (import_id, i + 1, json.dumps(item['data']),
             item['match_id'], item['match_confidence'])
        )

    log_attivita(g.user['id'], 'import_analisi', 'import_history', import_id,
                 f"Inventario: {orig_name} ({len(items)} apparecchi trovati)",
                 request.remote_addr)

    flash(f'Documento classificato come <strong>Inventario</strong>. '
          f'Trovati {len(items)} apparecchi.', 'success')
    return redirect(url_for('import.preview', id=import_id))


def _process_verbali(filepath, ext, text, is_scanned, orig_name, safe_name,
                     divisione_id, config, api_key, timestamp):
    """Analyze as maintenance report(s). PDF pages are split individually."""
    from ai_service import (
        parse_verbale_with_ai, parse_verbale_from_pdf_document,
        get_pdf_page_count, extract_text_from_pdf_page, split_pdf_pages
    )
    model = config.get('ai_email_model', 'claude-haiku-4-5-20251001')
    all_items = []

    if ext == 'pdf':
        page_count = get_pdf_page_count(filepath)

        if page_count > 1:
            # Multi-page PDF: split and analyze page by page
            pages_dir = os.path.join(
                current_app.config['UPLOADS_PATH'], 'import', f'pages_{timestamp}')
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
                        item['_page_file'] = os.path.relpath(
                            page_path, current_app.config['UPLOADS_PATH'])
                    all_items.extend(items)
                except Exception as e:
                    all_items.append({
                        'matricola': '', 'tipo': '', 'data_intervento': '',
                        'descrizione': f'Errore analisi pagina {i+1}: {e}',
                        '_pagina': i + 1,
                        '_page_file': os.path.relpath(
                            page_path, current_app.config['UPLOADS_PATH']),
                        '_errore': True,
                    })
        else:
            # Single page
            try:
                if is_scanned:
                    items, _ = parse_verbale_from_pdf_document(
                        filepath, api_key, model, config=config)
                else:
                    items, _ = parse_verbale_with_ai(
                        text, api_key, model, config=config)
                for item in items:
                    item['_pagina'] = 1
                    item['_page_file'] = f"import/{safe_name}"
                all_items = items
            except Exception as e:
                flash(f'Errore analisi verbale: {e}', 'danger')
                return redirect(url_for('import.upload'))
    else:
        # Non-PDF (Excel, CSV): analyze whole text
        try:
            items, _ = parse_verbale_with_ai(text, api_key, model, config=config)
            for item in items:
                item['_pagina'] = 0
            all_items = items
        except Exception as e:
            flash(f'Errore analisi verbale: {e}', 'danger')
            return redirect(url_for('import.upload'))

    if not all_items:
        flash("L'analisi AI non ha trovato interventi di manutenzione.", 'warning')
        return redirect(url_for('import.upload'))

    # Match matricole to apparecchi
    _match_apparecchi(all_items)

    # Save to import_history + import_preview
    cursor = execute(
        """INSERT INTO import_history
           (tipo_import, filename, filepath, tipo_documento, divisione_id,
            totale_righe, stato, ai_prompt, ai_response, imported_by)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
        ('verbale_manutenzione', orig_name, f"import/{safe_name}", ext,
         divisione_id, len(all_items),
         f"[VERBALE_SYSTEM_PROMPT — {len(all_items)} interventi]",
         json.dumps(all_items, ensure_ascii=False), g.user['id'])
    )
    import_id = cursor.lastrowid

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

    log_attivita(g.user['id'], 'import_analisi', 'import_history', import_id,
                 f"Verbali: {orig_name} ({len(all_items)} interventi trovati)",
                 request.remote_addr)

    flash(f'Documento classificato come <strong>Verbale di Manutenzione</strong>. '
          f'Trovati {len(all_items)} interventi.', 'success')
    return redirect(url_for('import.preview', id=import_id))


def _process_verifiche(filepath, ext, text, is_scanned, orig_name, safe_name,
                       divisione_id, config, api_key, timestamp):
    """Analyze as electrical safety verification(s). PDF pages split individually."""
    from ai_service import (
        analyze_verifiche_with_ai, analyze_verifiche_from_pdf_document,
        get_pdf_page_count, extract_text_from_pdf_page, split_pdf_pages
    )
    model = config.get('ai_email_model', 'claude-haiku-4-5-20251001')
    all_items = []

    if ext == 'pdf':
        page_count = get_pdf_page_count(filepath)

        if page_count > 1:
            pages_dir = os.path.join(
                current_app.config['UPLOADS_PATH'], 'import', f'pages_{timestamp}')
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
                        item['_page_file'] = os.path.relpath(
                            page_path, current_app.config['UPLOADS_PATH'])
                    all_items.extend(items)
                except Exception as e:
                    all_items.append({
                        'matricola': '', 'data_verifica': '', 'esito': '',
                        'note': f'Errore analisi pagina {i+1}: {e}',
                        '_pagina': i + 1,
                        '_page_file': os.path.relpath(
                            page_path, current_app.config['UPLOADS_PATH']),
                        '_errore': True,
                    })
        else:
            try:
                if is_scanned:
                    items, _ = analyze_verifiche_from_pdf_document(
                        filepath, api_key, model, config=config)
                else:
                    items, _ = analyze_verifiche_with_ai(
                        text, api_key, model, config=config)
                for item in items:
                    item['_pagina'] = 1
                    item['_page_file'] = f"import/{safe_name}"
                all_items = items
            except Exception as e:
                flash(f'Errore analisi verifiche: {e}', 'danger')
                return redirect(url_for('import.upload'))
    else:
        try:
            items, _ = analyze_verifiche_with_ai(text, api_key, model, config=config)
            for item in items:
                item['_pagina'] = 0
            all_items = items
        except Exception as e:
            flash(f'Errore analisi verifiche: {e}', 'danger')
            return redirect(url_for('import.upload'))

    if not all_items:
        flash("L'analisi AI non ha trovato verifiche di sicurezza.", 'warning')
        return redirect(url_for('import.upload'))

    _match_apparecchi(all_items)

    cursor = execute(
        """INSERT INTO import_history
           (tipo_import, filename, filepath, tipo_documento, divisione_id,
            totale_righe, stato, ai_prompt, ai_response, imported_by)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
        ('verifica_elettrica', orig_name, f"import/{safe_name}", ext,
         divisione_id, len(all_items),
         f"[VERIFICA_BATCH_SYSTEM_PROMPT — {len(all_items)} verifiche]",
         json.dumps(all_items, ensure_ascii=False), g.user['id'])
    )
    import_id = cursor.lastrowid

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

    log_attivita(g.user['id'], 'import_analisi', 'import_history', import_id,
                 f"Verifiche: {orig_name} ({len(all_items)} verifiche trovate)",
                 request.remote_addr)

    flash(f'Documento classificato come <strong>Verifica di Sicurezza Elettrica</strong>. '
          f'Trovate {len(all_items)} verifiche.', 'success')
    return redirect(url_for('import.preview', id=import_id))


def _match_apparecchi(items):
    """Match matricole in items to existing apparecchi. Sets _match_id on each item."""
    # Batch-fetch all unique matricole in one query
    matricole = list({(item.get('matricola') or '').strip() for item in items} - {''})
    lookup = {}
    if matricole:
        placeholders = ','.join('?' * len(matricole))
        rows = query_all(
            f"SELECT id, matricola FROM apparecchi WHERE matricola IN ({placeholders}) AND stato != 'dismesso'",
            matricole)
        lookup = {r['matricola']: r['id'] for r in rows}
    for item in items:
        matricola = (item.get('matricola') or '').strip()
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
        apparecchi_list = query_all(
            """SELECT a.id, a.matricola, a.marca, a.modello, d.nome as divisione_nome
               FROM apparecchi a
               LEFT JOIN divisioni d ON a.divisione_id = d.id
               WHERE a.stato != 'dismesso'
               ORDER BY a.matricola""")

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
                       ubicazione = COALESCE(?, ubicazione),
                       fornitore = COALESCE(?, fornitore),
                       note = COALESCE(?, note),
                       updated_by = ?, updated_at = datetime('now')
                       WHERE id = ?""",
                    (data.get('ubicazione'), data.get('fornitore'),
                     data.get('note'), g.user['id'], row['apparecchio_match_id'])
                )
            else:
                execute(
                    """INSERT INTO apparecchi
                       (divisione_id, matricola, descrizione, numero_inventario,
                        marca, modello, anno_fabbricazione, classificazione,
                        ubicazione, fornitore, ip_address, note, created_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (import_rec['divisione_id'],
                     data.get('matricola', ''),
                     data.get('descrizione'),
                     data.get('numero_inventario'),
                     data.get('marca', ''),
                     data.get('modello', ''),
                     data.get('anno_fabbricazione'),
                     data.get('classificazione'),
                     data.get('ubicazione'),
                     data.get('fornitore'),
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

            # Determine apparecchio: user override or AI match
            app_override = request.form.get(f'apparecchio_id_{row_id}')
            apparecchio_id = int(app_override) if app_override else row['apparecchio_match_id']

            if not apparecchio_id:
                execute("UPDATE import_preview SET stato = 'rejected', "
                        "note_revisione = 'Nessun apparecchio associato' WHERE id = ?",
                        (int(row_id),))
                errors += 1
                continue

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

            cursor = execute(
                """INSERT INTO manutenzioni
                   (apparecchio_id, tipo, data_intervento, prossima_scadenza,
                    periodicita_giorni, tecnico_ditta, descrizione, esito,
                    costo, verbale_path, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    apparecchio_id,
                    data.get('tipo', 'preventiva'),
                    data.get('data_intervento'),
                    data.get('prossima_scadenza'),
                    int(data['periodicita_giorni']) if data.get('periodicita_giorni') else None,
                    data.get('tecnico_ditta'),
                    data.get('descrizione'),
                    data.get('esito'),
                    float(data['costo']) if data.get('costo') else None,
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

            app_override = request.form.get(f'apparecchio_id_{row_id}')
            apparecchio_id = int(app_override) if app_override else row['apparecchio_match_id']

            if not apparecchio_id:
                execute("UPDATE import_preview SET stato = 'rejected', "
                        "note_revisione = 'Nessun apparecchio associato' WHERE id = ?",
                        (int(row_id),))
                errors += 1
                continue

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

            # Auto-calculate prossima_scadenza
            periodicita = int(data.get('periodicita_giorni', 365))
            prossima = data.get('prossima_scadenza')
            if not prossima and data.get('data_verifica'):
                try:
                    d = datetime.strptime(data['data_verifica'], '%Y-%m-%d')
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
                    data.get('data_verifica'),
                    prossima,
                    periodicita,
                    data.get('esito', 'positivo'),
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
    """Import history."""
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
    """Email verbale queue: pending items for manual review."""
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

    counts = query_one(
        """SELECT COUNT(*) as total,
                  SUM(CASE WHEN stato = 'completed' THEN 1 ELSE 0 END) as completed,
                  SUM(CASE WHEN stato = 'failed' THEN 1 ELSE 0 END) as failed
           FROM import_history WHERE tipo_import = 'verbale_email'"""
    )
    completed_count = counts['completed'] or 0
    failed_count = counts['failed'] or 0
    total_count = counts['total'] or 0

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
    """Detail view for a pending email verbale."""
    record = query_one("SELECT * FROM import_history WHERE id = ?", (id,))
    if not record:
        flash('Record non trovato.', 'danger')
        return redirect(url_for('import.email_queue'))

    parsed = _parse_email_ai_response(record.get('ai_response'))

    apparecchio = None
    matricola = parsed.get('matricola', '').strip()
    if matricola:
        apparecchio = query_one(
            """SELECT a.*, d.nome as divisione_nome
               FROM apparecchi a
               LEFT JOIN divisioni d ON a.divisione_id = d.id
               WHERE a.matricola = ? AND a.stato != 'dismesso'""",
            (matricola,)
        )

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

    try:
        execute(
            """INSERT INTO manutenzioni
               (apparecchio_id, tipo, data_intervento, prossima_scadenza,
                periodicita_giorni, tecnico_ditta, descrizione, esito, costo, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(apparecchio_id),
                request.form.get('tipo', 'preventiva'),
                request.form.get('data_intervento'),
                request.form.get('prossima_scadenza') or None,
                int(request.form.get('periodicita_giorni')) if request.form.get('periodicita_giorni') else None,
                request.form.get('tecnico_ditta') or None,
                request.form.get('descrizione') or None,
                request.form.get('esito') or None,
                float(request.form.get('costo')) if request.form.get('costo') else None,
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


@import_bp.route('/import/email/<int:id>/scarta')
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
