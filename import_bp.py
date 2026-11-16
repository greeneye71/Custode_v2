"""
MedInventory - Import Blueprint
AI-powered unified import: upload, classify, analyze, preview, approve.
Supports: inventario, verbale di manutenzione, verifica di sicurezza elettrica.
"""

import json
import logging
import os
import shutil
import time
import uuid

from flask import (
    Blueprint, jsonify, render_template, request, redirect, url_for,
    flash, g, current_app
)
from werkzeug.utils import secure_filename

import allegati
import coda_import
from auth import login_required
from models import (query_one, query_all, execute, log_attivita, upload_subdir,
                    nome_file_unico, apparecchio_accessibile,
                    divisione_accessibile, filtro_divisione,
                    scegli_apparecchio, transazione)
from validazione_dominio import (valida_apparecchio, valida_manutenzione,
                                 valida_verifica, messaggio_errori)

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

def _righe_email(record):
    """Le righe di un import email: una per ogni elemento estratto dal documento.

    I record creati prima della 2.8.0 non hanno righe in import_preview: per
    loro si ricostruisce l'unica riga che la revisione manuale mostrava, cosi'
    la coda storica resta lavorabile senza migrazione dei dati.
    """
    righe = query_all(
        """SELECT id, riga_numero, dati_estratti, apparecchio_match_id, stato,
                  note_revisione
           FROM import_preview
           WHERE import_id = ?
           ORDER BY riga_numero, id""",
        (record['id'],)
    )
    if righe:
        return [{
            'preview_id': r['id'],
            'numero': r['riga_numero'] or 0,
            'dati': _parse_email_ai_response(r['dati_estratti']),
            'apparecchio_match_id': r['apparecchio_match_id'],
            'stato': r['stato'] or 'pending',
            'nota': r['note_revisione'],
        } for r in righe]
    return [{
        'preview_id': None,
        'numero': 1,
        'dati': _parse_email_ai_response(record.get('ai_response')),
        'apparecchio_match_id': None,
        'stato': 'imported' if record.get('stato') == 'completed' else 'pending',
        'nota': None,
    }]


def _aggiorna_stato_import(import_id):
    """Allinea import_history alle sue righe.

    Il record esce dalla coda solo quando nessuna riga e' piu' in attesa: fino
    alla 2.8.0 la prima conferma lo marcava 'completed' e gli altri interventi
    dello stesso verbale non erano piu' raggiungibili da nessuna pagina.
    """
    conteggi = query_one(
        """SELECT COUNT(*) AS totale,
                  SUM(CASE WHEN stato = 'imported' THEN 1 ELSE 0 END) AS importate,
                  SUM(CASE WHEN stato = 'pending' THEN 1 ELSE 0 END) AS pendenti
           FROM import_preview WHERE import_id = ?""",
        (import_id,)
    )
    totale = (conteggi or {}).get('totale') or 0
    if not totale:
        # Record storico senza righe: resta il comportamento a riga singola.
        execute(
            """UPDATE import_history SET stato = 'completed', righe_importate = 1,
                      completed_at = datetime('now') WHERE id = ?""",
            (import_id,)
        )
        return 0
    importate = conteggi['importate'] or 0
    pendenti = conteggi['pendenti'] or 0
    if pendenti:
        execute(
            "UPDATE import_history SET stato = 'pending', righe_importate = ? WHERE id = ?",
            (importate, import_id)
        )
    else:
        execute(
            """UPDATE import_history SET stato = ?, righe_importate = ?,
                      completed_at = datetime('now') WHERE id = ?""",
            ('completed' if importate else 'failed', importate, import_id)
        )
    return pendenti


DOC_TYPE_LABELS = {
    'inventario': 'Inventario',
    'verbale_manutenzione': 'Verbale di Manutenzione',
    'verifica_elettrica': 'Verifica di Sicurezza Elettrica',
}


def _ruolo_vede_ogni_divisione():
    """True per i ruoli che leggono l'intera struttura, divisioni comprese.

    Gli import arrivati dalla posta hanno divisione_id NULL: non appartengono
    ad alcun reparto, quindi possono gestirli solo questi ruoli.
    """
    return getattr(g, 'user', {}).get('ruolo') in ('admin', 'tecnico', 'superadmin')


def get_import_in_scope(import_id):
    """Restituisce il record di import solo se e' nello scope di chi chiede.

    Senza questo controllo qualunque utente autenticato potrebbe leggere le
    estrazioni AI di un altro tenant — o eseguirne l'import — indovinando l'id.
    Oltre alla struttura si controlla la divisione: fino alla 2.8.0 un ruolo
    'utente' apriva ed eseguiva gli import dei reparti a cui non e' assegnato.
    """
    struttura_id = getattr(g, 'struttura_id', None)
    if struttura_id is None:
        # Superadmin senza impersonazione: vista globale.
        if g.user['ruolo'] == 'superadmin':
            return query_one("SELECT * FROM import_history WHERE id = ?", (import_id,))
        return None
    rec = query_one(
        "SELECT * FROM import_history WHERE id = ? AND struttura_id = ?",
        (import_id, struttura_id)
    )
    if not rec:
        return None
    if not _ruolo_vede_ogni_divisione():
        ids = [d['id'] for d in getattr(g, 'divisioni', [])]
        if rec['divisione_id'] not in ids:
            return None
    return rec


def _scope_import(alias='ih'):
    """Clausola di scope per gli elenchi di import_history.

    Stessa semantica di get_import_in_scope(): superadmin senza impersonazione
    vede tutto, chi ha una struttura attiva vede la sua, chiunque altro non vede
    nulla. Fino alla 2.7.1 gli elenchi cadevano invece su una query senza
    filtro, e un admin senza struttura attiva leggeva gli import di tutti.
    """
    struttura_id = getattr(g, 'struttura_id', None)
    if struttura_id is None:
        if g.user['ruolo'] == 'superadmin':
            return '', []
        return 'AND 1=0', []
    if _ruolo_vede_ogni_divisione():
        return f'AND {alias}.struttura_id = ?', [struttura_id]
    # Stesso perimetro di get_import_in_scope(): l'elenco non deve mostrare
    # righe che l'utente non potrebbe comunque aprire.
    ids = [d['id'] for d in getattr(g, 'divisioni', [])]
    if not ids:
        return 'AND 1=0', []
    segnaposto = ','.join('?' * len(ids))
    return (f'AND {alias}.struttura_id = ? AND {alias}.divisione_id IN ({segnaposto})',
            [struttura_id] + ids)


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

    # g.divisioni e' gia' scoped alla struttura corrente per ogni ruolo
    # (auth.py): saltare il controllo per l'admin permetteva di attribuire
    # l'import a una divisione di un'altra struttura, semplicemente
    # indovinandone l'id. Nessun ruolo va escluso da questo controllo.
    accessible_ids = [d['id'] for d in g.divisioni]
    if divisione_id not in accessible_ids:
        flash('Divisione non accessibile.', 'danger')
        return redirect(url_for('import.upload'))

    rifiuto = allegati.verifica(
        file, ALLOWED_IMPORT_EXT,
        f'Formato non supportato. Usa: {", ".join(ALLOWED_IMPORT_EXT)}')
    if rifiuto:
        flash(rifiuto, 'danger')
        return redirect(url_for('import.upload'))
    ext = allegati.estensione(file.filename)

    # Check AI config before saving file
    config = current_app.config['APP_CONFIG']
    from ai_service import check_ai_configured
    _struttura_id_check = getattr(g, 'struttura_id', None)
    ai_ok, ai_error = check_ai_configured(config=config, struttura_id=_struttura_id_check)
    if not ai_ok:
        flash(ai_error, 'danger')
        return redirect(url_for('import.upload'))

    # struttura_id derivata dalla divisione selezionata (fonte autoritativa)
    _div_row = query_one("SELECT struttura_id FROM divisioni WHERE id = ?", (divisione_id,))
    _struttura_import = _div_row['struttura_id'] if _div_row else _struttura_id_check

    # M07: uno slot di analisi si prenota prima di salvare il file. Senza tetto
    # ogni upload faceva partire un thread in piu', e bastavano pochi documenti
    # per saturare processo e quota AI. Si rifiuta prima di scrivere su disco:
    # un file salvato e un import 'processing' che non parte sarebbero peggio
    # del rifiuto.
    if not coda_import.prenota(_struttura_import, config):
        flash("Ci sono gia' troppe analisi AI in corso. "
              'Attendi che una finisca e riprova.', 'warning')
        return redirect(url_for('import.upload'))

    # Da qui allo start del lavoro lo slot e' prenotato ma nessuno lo
    # rilascera': se il salvataggio o l'INSERT falliscono va restituito a mano,
    # altrimenti dopo qualche errore il deployment non accetta piu' import.
    try:
        # Save uploaded file (in cartella scoped per struttura: i sorgenti di import
        # sono serviti da /uploads/<path>, che isola solo i percorsi strutture/<id>/)
        uploads_dir, _import_rel_prefix = upload_subdir('import', _struttura_import)
        # Identifica la cartella pages_<...> in cui viene spezzato il PDF: con il
        # solo secondo, due import avviati insieme scrivevano le proprie pagine
        # nella stessa cartella e si mescolavano.
        timestamp = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        filename = nome_file_unico(file.filename)
        filepath = os.path.join(uploads_dir, filename)
        file.save(filepath)

        # Create import_history record in 'processing' state.
        # tipo_import is set to 'inventario' as placeholder; the background thread
        # will update it once the document is classified.
        cursor = execute(
            """INSERT INTO import_history
               (tipo_import, filename, filepath, tipo_documento, divisione_id,
                struttura_id, stato, imported_by)
               VALUES ('inventario', ?, ?, ?, ?, ?, 'processing', ?)""",
            (file.filename, f"{_import_rel_prefix}/{filename}", ext, divisione_id,
             _struttura_import, g.user['id'])
        )
        import_id = cursor.lastrowid

        # Capture request-context values before leaving the request
        app_obj = current_app._get_current_object()
        uploads_path = current_app.config['UPLOADS_PATH']
        from ai_service import get_ai_config as _gac
        # _struttura_import (non g.struttura_id) e' il valore autoritativo: e'
        # lo stesso gia' usato sopra per l'INSERT in import_history.
        # g.struttura_id puo' essere None (superadmin che non impersona, admin
        # la cui struttura e' stata disattivata) mentre l'import ha comunque una
        # struttura precisa, derivata dalla divisione scelta: passare
        # g.struttura_id al thread faceva perdere il filtro di struttura al
        # match automatico degli apparecchi (_match_apparecchi), che allora
        # cercava su tutte le strutture.
        _struttura_id = _struttura_import
        _ai_cfg = _gac(struttura_id=_struttura_id, config=config)
        api_key = _ai_cfg['api_key']
        user_id = g.user['id']
        remote_addr = request.remote_addr

        # Lo slot prenotato sopra viene rilasciato da coda_import.avvia() quando
        # il lavoro finisce, riuscito o fallito che sia.
        coda_import.avvia(
            _struttura_id,
            _run_import_async,
            args=(app_obj, import_id, filepath, ext, file.filename, filename,
                  divisione_id, config, api_key, timestamp,
                  user_id, remote_addr, uploads_path, _struttura_id),
            nome=f'import-{import_id}',
        )
    except Exception:
        coda_import.rilascia(_struttura_import)
        raise

    return redirect(url_for('import.attendi', id=import_id))


@import_bp.route('/import/<int:id>/attendi')
@login_required
def attendi(id):
    """Waiting page: polls status while AI analysis runs in background."""
    import_rec = get_import_in_scope(id)
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
    import_rec = get_import_in_scope(id)
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
                if not is_anthropic_provider(config=config, struttura_id=struttura_id):
                    execute(
                        "UPDATE import_history SET stato='failed', errori_dettaglio=? WHERE id=?",
                        ('PDF scansionato non supportato con provider AI locale.', import_id))
                    return
                doc_type = classify_document_type_from_pdf(
                    filepath, api_key, classify_model, config=config, struttura_id=struttura_id)
            else:
                doc_type = classify_document_type(
                    text, api_key, classify_model, config=config, struttura_id=struttura_id)

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
            filepath, api_key, model, config=config, struttura_id=struttura_id)
        text_summary = f"[PDF scansionato — analisi diretta AI ({len(ai_response)} chars)]"
    else:
        items, ai_response = analyze_inventory_with_ai(text, api_key, model, config=config, struttura_id=struttura_id)
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
    # Thread di background: fuori da una richiesta il default automatico non
    # trova nessuna struttura, va passata quella dell'import.
    log_attivita(user_id, 'import_analisi', 'import_history', import_id,
                 f"Inventario: {orig_name} ({len(items)} apparecchi trovati)", remote_addr,
                 struttura_id=struttura_id)


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
                            page_path, api_key, model, config=config, struttura_id=struttura_id)
                    else:
                        items, _ = parse_verbale_with_ai(
                            page_text, api_key, model, config=config, struttura_id=struttura_id)
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
                    filepath, api_key, model, config=config, struttura_id=struttura_id)
            else:
                items, _ = parse_verbale_with_ai(text, api_key, model, config=config, struttura_id=struttura_id)
            for item in items:
                item['_pagina'] = 1
                item['_page_file'] = os.path.relpath(filepath, uploads_path)
            all_items = items
    else:
        items, _ = parse_verbale_with_ai(text, api_key, model, config=config, struttura_id=struttura_id)
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
        confidenza = item.pop('_match_confidenza', 1.0 if match_id else 0.0)
        execute(
            """INSERT INTO import_preview
               (import_id, riga_numero, dati_estratti, apparecchio_match_id,
                match_confidence, stato)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (import_id, i + 1, json.dumps(item, ensure_ascii=False),
             match_id, confidenza)
        )

    execute("UPDATE import_history SET stato='completed' WHERE id=?", (import_id,))
    log_attivita(user_id, 'import_analisi', 'import_history', import_id,
                 f"Verbali: {orig_name} ({len(all_items)} interventi trovati)", remote_addr,
                 struttura_id=struttura_id)


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
                            page_path, api_key, model, config=config, struttura_id=struttura_id)
                    else:
                        items, _ = analyze_verifiche_with_ai(
                            page_text, api_key, model, config=config, struttura_id=struttura_id)
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
                    filepath, api_key, model, config=config, struttura_id=struttura_id)
            else:
                items, _ = analyze_verifiche_with_ai(text, api_key, model, config=config, struttura_id=struttura_id)
            for item in items:
                item['_pagina'] = 1
                item['_page_file'] = os.path.relpath(filepath, uploads_path)
            all_items = items
    else:
        items, _ = analyze_verifiche_with_ai(text, api_key, model, config=config, struttura_id=struttura_id)
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
        confidenza = item.pop('_match_confidenza', 1.0 if match_id else 0.0)
        execute(
            """INSERT INTO import_preview
               (import_id, riga_numero, dati_estratti, apparecchio_match_id,
                match_confidence, stato)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (import_id, i + 1, json.dumps(item, ensure_ascii=False),
             match_id, confidenza)
        )

    execute("UPDATE import_history SET stato='completed' WHERE id=?", (import_id,))
    log_attivita(user_id, 'import_analisi', 'import_history', import_id,
                 f"Verifiche: {orig_name} ({len(all_items)} verifiche trovate)", remote_addr,
                 struttura_id=struttura_id)


def _match_apparecchi(items, struttura_id=None):
    """Aggancia gli item agli apparecchi esistenti, o li lascia in scelta manuale.

    Scrive su ogni item `_match_id`, `_match_confidenza` e, quando la matricola
    e' condivisa da piu' apparecchi senza che il documento dica quale,
    `_match_ambiguo` con l'elenco dei candidati.

    Lo schema impone UNIQUE(struttura_id, modello, matricola): la matricola da
    sola non e' una chiave. Fino alla 2.8.0 qui si prendeva una riga qualsiasi
    fra quelle omonime, quindi il verbale poteva finire sull'apparecchio
    sbagliato in modo dipendente dall'ordine di inserimento.

    Senza struttura_id non si cerca: la matricola non è unica fra strutture
    diverse, e fino alla 2.7.1 il fallback senza filtro poteva agganciare le
    righe importate all'apparecchio di un altro tenant. Nessuno scope, nessun
    match: le righe restano da abbinare a mano.
    """
    matricole = list({(item.get('matricola') or '').strip() for item in items} - {''})
    per_matricola = {}
    if matricole and struttura_id:
        placeholders = ','.join('?' * len(matricole))
        rows = query_all(
            f"SELECT id, matricola, marca, modello FROM apparecchi "
            f"WHERE LOWER(matricola) IN ({placeholders}) AND stato != 'dismesso' AND struttura_id = ?",
            [m.lower() for m in matricole] + [struttura_id])
        for r in rows:
            per_matricola.setdefault((r['matricola'] or '').lower(), []).append(r)
    for item in items:
        matricola = (item.get('matricola') or '').strip().lower()
        candidati = per_matricola.get(matricola, [])
        riga, motivo = scegli_apparecchio(candidati,
                                          modello=item.get('modello'),
                                          marca=item.get('marca'))
        item['_match_id'] = riga['id'] if riga else None
        item['_match_confidenza'] = 1.0 if motivo == 'matricola' else (0.8 if riga else 0.0)
        item.pop('_match_ambiguo', None)
        if motivo == 'ambiguo':
            item['_match_ambiguo'] = [
                {'id': c['id'], 'marca': c['marca'], 'modello': c['modello']}
                for c in candidati
            ]


# ---------------------------------------------------------------------------
# Preview & Execute (unified for all types)
# ---------------------------------------------------------------------------

@import_bp.route('/import/<int:id>/preview')
@login_required
def preview(id):
    """Preview page: show extracted items with match info. Adapts to tipo_import."""
    import_rec = get_import_in_scope(id)
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
    divisioni_list = []
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
        elif getattr(g, 'user', {}).get('ruolo') in ('admin', 'tecnico', 'superadmin')                 and getattr(g, 'struttura_id', None):
            apparecchi_list = query_all(
                """SELECT a.id, a.matricola, a.marca, a.modello, d.nome as divisione_nome
                   FROM apparecchi a
                   LEFT JOIN divisioni d ON a.divisione_id = d.id
                   WHERE a.stato != 'dismesso' AND a.struttura_id = ?
                   ORDER BY a.matricola""",
                (g.struttura_id,)
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

        # Divisioni accessibili per il form "crea nuovo apparecchio"
        if tipo == 'verifica_elettrica':
            struttura_id = getattr(g, 'struttura_id', None) or g.user.get('struttura_id')
            if not struttura_id and import_rec.get('divisione_id'):
                div_row = query_one(
                    "SELECT struttura_id FROM divisioni WHERE id=?",
                    (import_rec['divisione_id'],)
                )
                if div_row:
                    struttura_id = div_row['struttura_id']
            if struttura_id:
                divisioni_list = query_all(
                    "SELECT id, nome, colore FROM divisioni WHERE attiva=1 AND struttura_id=? ORDER BY nome",
                    (struttura_id,)
                )

    tipo_label = DOC_TYPE_LABELS.get(tipo, tipo)

    # Divisione attiva corrente (per preselezionare nel form "crea nuovo")
    divisione_attiva_id = None
    div_attiva = getattr(g, 'divisione_attiva', None)
    if div_attiva and div_attiva.get('id') != 'tutte':
        divisione_attiva_id = div_attiva.get('id')

    return render_template('import/preview.html',
                           import_rec=import_rec, rows=rows,
                           nuovi=nuovi, trovati=trovati,
                           tipo_label=tipo_label,
                           apparecchi_list=apparecchi_list,
                           divisioni_list=divisioni_list,
                           divisione_attiva_id=divisione_attiva_id)


@import_bp.route('/import/<int:id>/esegui', methods=['POST'])
@login_required
def esegui(id):
    """Execute import for selected rows. Branches by tipo_import."""
    import_rec = get_import_in_scope(id)
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

    # M03: chiusura dell'import e riga di registro insieme. Un import segnato
    # 'completed' di cui il registro non sa nulla e' l'unica traccia che resta
    # all'operatore quando qualcosa va storto.
    with transazione():
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


def _rimuovi_file_copiati(percorsi):
    """M03: il rollback annulla le righe, non i file gia' copiati su disco.

    Ripulisce gli allegati della riga fallita: senza questo la cartella dei
    verbali si riempirebbe di documenti che nessun record cita piu'.
    """
    for percorso in percorsi:
        try:
            os.remove(percorso)
        except OSError:
            current_app.logger.warning(
                "Allegato di import non rimosso dopo il rollback: %s", percorso)


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
            # M03: la scheda e lo stato della riga di anteprima si scrivono
            # insieme. Fuori transazione un errore lascerebbe l'apparecchio
            # inserito e la riga ancora "da importare", pronta a duplicarlo
            # al tentativo successivo.
            with transazione():
                data = json.loads(row['dati_estratti'])
                if row['apparecchio_match_id']:
                    # Il match nasce nel thread di analisi: prima di riscrivere la
                    # scheda va riverificato nello scope di chi esegue l'import,
                    # struttura e divisione comprese.
                    if not apparecchio_accessibile(row['apparecchio_match_id']):
                        raise ValueError("Apparecchio abbinato non accessibile")
                    # M14: stessi controlli del form anche qui. Marca, modello e
                    # matricola non vengono riscritti su una scheda esistente,
                    # quindi in questo ramo non sono obbligatori.
                    data, errori = valida_apparecchio(data, richiedi_identificativi=False)
                    if errori:
                        raise ValueError(messaggio_errori(errori))
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
                    # La divisione di destinazione e' quella dichiarata nell'import:
                    # va convalidata contro lo scope di chi esegue, non contro la
                    # sola struttura, altrimenti si crea una scheda in un reparto
                    # non assegnato.
                    div_row = divisione_accessibile(import_rec['divisione_id'])
                    if not div_row:
                        raise ValueError("Divisione di destinazione non accessibile")
                    # M14: marca, modello e matricola obbligatori come nel form;
                    # date e anno passano dalla stessa normalizzazione.
                    data, errori = valida_apparecchio(data)
                    if errori:
                        raise ValueError(messaggio_errori(errori))
                    # struttura_id dalla sessione (authoritative); fallback alla
                    # divisione solo per il superadmin che non impersona nessuno.
                    imp_struttura_id = getattr(g, 'struttura_id', None) or div_row['struttura_id']
                    execute(
                        """INSERT INTO apparecchi
                           (divisione_id, struttura_id, matricola, descrizione, numero_inventario,
                            marca, modello, anno_fabbricazione, classificazione,
                            ubicazione, fornitore, codice_fornitore, garanzia_scadenza,
                            contratto_manutenzione, ip_address, note, created_by)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (import_rec['divisione_id'],
                         imp_struttura_id,
                         data.get('matricola'),
                         data.get('descrizione'),
                         data.get('numero_inventario'),
                         data.get('marca'),
                         data.get('modello'),
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
        copiati = []
        try:
            # M03: manutenzione e stato della riga in una sola transazione;
            # gli allegati gia' copiati vengono rimossi a mano nel except,
            # perche' il rollback non tocca il disco.
            with transazione():
                data = json.loads(row['dati_estratti'])

                # Bug K: skip error items produced during page analysis
                if data.get('_errore'):
                    execute("UPDATE import_preview SET stato = 'rejected', "
                            "note_revisione = 'Errore analisi pagina' WHERE id = ?",
                            (int(row_id),))
                    errors += 1
                    continue

                # M14: validazione di dominio condivisa con il form, prima di
                # copiare allegati o di toccare il database.
                data, errori = valida_manutenzione(data)
                if errori:
                    raise ValueError(messaggio_errori(errori))

                # Determine apparecchio: user override or AI match
                app_override = request.form.get(f'apparecchio_id_{row_id}')
                if app_override:
                    try:
                        apparecchio_id = int(app_override)
                    except (ValueError, TypeError):
                        apparecchio_id = None
                    # L'override arriva dal form: verifica struttura e divisione
                    # per ogni ruolo (prima l'admin non veniva controllato affatto).
                    if apparecchio_id and not apparecchio_accessibile(apparecchio_id):
                        apparecchio_id = None
                else:
                    apparecchio_id = row['apparecchio_match_id']
                    # Il match arriva dall'analisi in background: va riverificato
                    # nello scope di chi esegue l'import, esattamente come
                    # l'override manuale sopra. Senza questo controllo un
                    # apparecchio_match_id di un'altra struttura (es. perche' il
                    # thread di analisi aveva perso il filtro di struttura)
                    # passerebbe senza che nessuno lo controlli.
                    if apparecchio_id and not apparecchio_accessibile(apparecchio_id):
                        apparecchio_id = None

                if not apparecchio_id:
                    execute("UPDATE import_preview SET stato = 'rejected', "
                            "note_revisione = 'Nessun apparecchio associato' WHERE id = ?",
                            (int(row_id),))
                    errors += 1
                    continue

                data_intervento = data['data_intervento']
                tipo = data['tipo']

                # Copy page PDF to verbali folder if available
                verbale_path = None
                page_file = data.get('_page_file')
                if page_file:
                    src = os.path.join(current_app.config['UPLOADS_PATH'], page_file)
                    if os.path.exists(src):
                        verbali_dir, verbali_prefix = upload_subdir(
                            'verbali', import_rec.get('struttura_id'))
                        dest_name = f"{batch_ts}_{idx}_{os.path.basename(page_file)}"
                        dest = os.path.join(verbali_dir, dest_name)
                        shutil.copy2(src, dest)
                        copiati.append(dest)
                        verbale_path = f"{verbali_prefix}/{dest_name}"

                periodicita_giorni = data['periodicita_giorni']
                costo = data['costo']

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
                        data['prossima_scadenza'],
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
            _rimuovi_file_copiati(copiati)
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
        copiati = []
        try:
            # M03: verifica, eventuale apparecchio creato al volo e stato
            # della riga sono un'unica scrittura. Prima un errore a meta'
            # poteva lasciare la scheda nuova senza la sua verifica.
            with transazione():
                data = json.loads(row['dati_estratti'])

                # Bug K: skip error items produced during page analysis
                if data.get('_errore'):
                    execute("UPDATE import_preview SET stato = 'rejected', "
                            "note_revisione = 'Errore analisi pagina' WHERE id = ?",
                            (int(row_id),))
                    errors += 1
                    continue

                # M14: validazione di dominio condivisa con il form, prima di
                # creare apparecchi o copiare allegati per una riga da scartare.
                # Un esito assente o incomprensibile non diventa piu' 'positivo'.
                data, errori = valida_verifica(data)
                if errori:
                    raise ValueError(messaggio_errori(errori))

                # Risoluzione apparecchio: crea nuovo, override manuale, o match AI
                crea_nuovo = request.form.get(f'crea_nuovo_{row_id}') == '1'

                if crea_nuovo:
                    n_marca = request.form.get(f'nuovo_marca_{row_id}', '').strip()
                    n_modello = request.form.get(f'nuovo_modello_{row_id}', '').strip()
                    n_matricola = request.form.get(f'nuovo_matricola_{row_id}', '').strip()
                    n_descrizione = request.form.get(f'nuovo_descrizione_{row_id}', '').strip()
                    n_divisione_id = request.form.get(f'nuovo_divisione_id_{row_id}', type=int)

                    if not (n_marca and n_modello and n_matricola and n_divisione_id):
                        raise ValueError(
                            "Marca, modello, matricola e divisione sono obbligatori "
                            "per creare un nuovo apparecchio"
                        )

                    # La divisione arriva dal form: struttura *e* assegnazione.
                    # Il controllo precedente si fermava alla struttura, cosi' un
                    # ruolo 'utente' creava schede in qualunque reparto del tenant.
                    div_check = divisione_accessibile(n_divisione_id)
                    if not div_check or not div_check['attiva']:
                        raise ValueError("Divisione non accessibile")

                    cur = execute(
                        """INSERT INTO apparecchi
                           (divisione_id, struttura_id, matricola, marca, modello, descrizione, created_by)
                           VALUES (?,?,?,?,?,?,?)""",
                        (n_divisione_id, div_check['struttura_id'],
                         n_matricola, n_marca, n_modello, n_descrizione or None, g.user['id'])
                    )
                    apparecchio_id = cur.lastrowid
                    log_attivita(g.user['id'], 'creazione', 'apparecchi', apparecchio_id,
                                 f"Creato da import verifica: {n_marca} {n_modello} ({n_matricola})",
                                 request.remote_addr,
                                 struttura_id=div_check['struttura_id'])
                else:
                    app_override = request.form.get(f'apparecchio_id_{row_id}')
                    if app_override:
                        try:
                            apparecchio_id = int(app_override)
                        except (ValueError, TypeError):
                            apparecchio_id = None
                        # L'override arriva dal form: verifica struttura e divisione
                        # per ogni ruolo (prima admin/superadmin non erano controllati).
                        if apparecchio_id and not apparecchio_accessibile(apparecchio_id):
                            apparecchio_id = None
                    else:
                        apparecchio_id = row['apparecchio_match_id']
                        # Stesso motivo di _execute_verbali: il match automatico
                        # non e' gia' verificato nello scope di chi esegue
                        # l'import, va controllato qui come l'override manuale.
                        if apparecchio_id and not apparecchio_accessibile(apparecchio_id):
                            apparecchio_id = None

                if not apparecchio_id:
                    execute("UPDATE import_preview SET stato = 'rejected', "
                            "note_revisione = 'Nessun apparecchio associato' WHERE id = ?",
                            (int(row_id),))
                    errors += 1
                    continue

                data_verifica = data['data_verifica']
                esito = data['esito']

                # Copy page PDF to verifiche folder if available
                documento_path = None
                page_file = data.get('_page_file')
                if page_file:
                    src = os.path.join(current_app.config['UPLOADS_PATH'], page_file)
                    if os.path.exists(src):
                        verifiche_dir, verifiche_prefix = upload_subdir(
                            'verifiche', import_rec.get('struttura_id'))
                        dest_name = f"{batch_ts}_{idx}_{os.path.basename(page_file)}"
                        dest = os.path.join(verifiche_dir, dest_name)
                        shutil.copy2(src, dest)
                        copiati.append(dest)
                        documento_path = f"{verifiche_prefix}/{dest_name}"

                periodicita = data['periodicita_giorni']
                prossima = data['prossima_scadenza']
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
            _rimuovi_file_copiati(copiati)
            execute("UPDATE import_preview SET stato = 'rejected', note_revisione = ? WHERE id = ?",
                    (str(e), int(row_id)))
            errors += 1

    return imported, errors


@import_bp.route('/import/storico')
@login_required
def storico():
    """Import history. Filtrato per struttura dell'utente."""
    clausola, parametri = _scope_import()
    imports = query_all(
        f"""SELECT ih.*, d.nome as divisione_nome, u.nome || ' ' || u.cognome as utente_nome
            FROM import_history ih
            LEFT JOIN divisioni d ON ih.divisione_id = d.id
            LEFT JOIN utenti u ON ih.imported_by = u.id
            WHERE 1=1 {clausola}
            ORDER BY ih.created_at DESC
            LIMIT 50""",
        parametri
    )
    return render_template('import/storico.html', imports=imports)


# ============================================================================
# EMAIL QUEUE (verbali received via IMAP)
# ============================================================================

@import_bp.route('/import/email')
@login_required
def email_queue():
    """Email verbale queue: pending items for manual review. Filtrato per struttura."""
    clausola, parametri = _scope_import()
    pending = query_all(
        f"""SELECT ih.*, d.nome as divisione_nome
            FROM import_history ih
            LEFT JOIN divisioni d ON ih.divisione_id = d.id
            WHERE ih.tipo_import = 'verbale_email' AND ih.stato = 'pending'
                  {clausola}
            ORDER BY ih.created_at DESC""",
        parametri
    )

    for item in pending:
        righe = _righe_email(item)
        pendenti = [r for r in righe if r['stato'] == 'pending']
        prima = (pendenti or righe)[0]['dati']
        item['matricola_estratta'] = prima.get('matricola', '')
        item['tipo_estratto'] = prima.get('tipo', '')
        item['righe_totali'] = len(righe)
        item['righe_pendenti'] = len(pendenti)

    counts = query_one(
        f"""SELECT COUNT(*) as total,
                   SUM(CASE WHEN ih.stato = 'completed' THEN 1 ELSE 0 END) as completed,
                   SUM(CASE WHEN ih.stato = 'failed' THEN 1 ELSE 0 END) as failed
            FROM import_history ih
            WHERE ih.tipo_import = 'verbale_email' {clausola}""",
        parametri
    )
    completed_count = counts['completed'] or 0
    failed_count = counts['failed'] or 0
    total_count = counts['total'] or 0

    recent_completed = query_all(
        f"""SELECT ih.*, d.nome as divisione_nome
            FROM import_history ih
            LEFT JOIN divisioni d ON ih.divisione_id = d.id
            WHERE ih.tipo_import = 'verbale_email' AND ih.stato = 'completed'
                  {clausola}
            ORDER BY ih.created_at DESC LIMIT 10""",
        parametri
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
    record = get_import_in_scope(id)
    if not record:
        flash('Record non trovato.', 'danger')
        return redirect(url_for('import.email_queue'))

    righe = _righe_email(record)

    struttura_id = getattr(g, 'struttura_id', None)

    # L'elenco proposto e il match sulla matricola passano dallo scope di chi
    # guarda: fino alla 2.8.0 mostravano tutti gli apparecchi della struttura,
    # reparti non assegnati compresi. Il superadmin che non impersona nessuno
    # resta l'unico caso senza filtro.
    if struttura_id:
        clausola_div, parametri_div = filtro_divisione('a')
    elif g.user['ruolo'] == 'superadmin':
        clausola_div, parametri_div = '', []
    else:
        clausola_div, parametri_div = 'AND 1=0', []

    # Una matricola puo' appartenere a piu' modelli (UNIQUE e' su
    # struttura+modello+matricola): si prendono tutti i candidati e si decide
    # solo se il verbale dice abbastanza. Altrimenti nessun preselezionato e
    # l'operatore sceglie dall'elenco.
    for riga in righe:
        riga['apparecchio'] = None
        riga['candidati_ambigui'] = []
        if riga['stato'] != 'pending':
            continue
        matricola = (riga['dati'].get('matricola') or '').strip()
        if not matricola:
            continue
        candidati = query_all(
            f"""SELECT a.*, d.nome as divisione_nome
                FROM apparecchi a
                LEFT JOIN divisioni d ON a.divisione_id = d.id
                WHERE a.matricola = ? AND a.stato != 'dismesso' {clausola_div}""",
            [matricola] + parametri_div
        )
        riga['apparecchio'], motivo = scegli_apparecchio(
            candidati,
            modello=riga['dati'].get('modello'),
            marca=riga['dati'].get('marca'))
        if motivo == 'ambiguo':
            riga['candidati_ambigui'] = candidati

    apparecchi_list = query_all(
        f"""SELECT a.id, a.matricola, a.marca, a.modello, d.nome as divisione_nome
            FROM apparecchi a
            LEFT JOIN divisioni d ON a.divisione_id = d.id
            WHERE a.stato != 'dismesso' {clausola_div}
            ORDER BY a.matricola""",
        parametri_div
    )

    return render_template('import/email_dettaglio.html',
                           record=record,
                           righe_pendenti=[r for r in righe if r['stato'] == 'pending'],
                           righe_chiuse=[r for r in righe if r['stato'] != 'pending'],
                           apparecchi_list=apparecchi_list)


@import_bp.route('/import/email/<int:id>/conferma', methods=['POST'])
@login_required
def email_conferma(id):
    """Confirm and import a pending email verbale."""
    record = get_import_in_scope(id)
    if not record:
        flash('Record non trovato.', 'danger')
        return redirect(url_for('import.email_queue'))

    # La riga da confermare: un verbale puo' contenere piu' interventi e ognuno
    # si conferma per conto suo. I record storici non hanno righe e arrivano qui
    # senza preview_id.
    riga = None
    preview_id = request.form.get('preview_id')
    if preview_id:
        try:
            preview_id = int(preview_id)
        except (ValueError, TypeError):
            flash('Riga non valida.', 'danger')
            return redirect(url_for('import.email_dettaglio', id=id))
        riga = query_one(
            "SELECT * FROM import_preview WHERE id = ? AND import_id = ?",
            (preview_id, id)
        )
        if not riga:
            flash('Riga non trovata.', 'danger')
            return redirect(url_for('import.email_dettaglio', id=id))
        if riga['stato'] != 'pending':
            flash('Riga gia\' lavorata.', 'warning')
            return redirect(url_for('import.email_dettaglio', id=id))

    apparecchio_id = request.form.get('apparecchio_id')
    if not apparecchio_id:
        flash('Seleziona un apparecchio.', 'warning')
        return redirect(url_for('import.email_dettaglio', id=id))

    # L'apparecchio arriva dal form: struttura *e* divisione. Il controllo
    # precedente si fermava alla struttura, e senza struttura attiva non
    # controllava nulla.
    try:
        apparecchio_id = int(apparecchio_id)
    except (ValueError, TypeError):
        flash('Apparecchio non valido.', 'danger')
        return redirect(url_for('import.email_dettaglio', id=id))
    if not apparecchio_accessibile(apparecchio_id):
        flash('Apparecchio non trovato o non accessibile.', 'danger')
        return redirect(url_for('import.email_queue'))

    # FIX 7: validazione tipo manutenzione
    TIPI_VALIDI = ('preventiva', 'correttiva', 'verifica', 'calibrazione')
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
                apparecchio_id,
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

        if riga:
            execute(
                """UPDATE import_preview
                   SET stato = 'imported', apparecchio_match_id = ?,
                       note_revisione = 'Confermata manualmente'
                   WHERE id = ?""",
                (apparecchio_id, preview_id)
            )
        pendenti = _aggiorna_stato_import(id)

        dettaglio = f"Confermato verbale email: {record.get('filename', '')}"
        if riga:
            dettaglio += f" (riga {riga['riga_numero']})"
        log_attivita(g.user['id'], 'import_email_conferma', 'import_history', id,
                     dettaglio, request.remote_addr,
                     struttura_id=record.get('struttura_id'))

        if pendenti:
            flash(f'Manutenzione importata. Restano {pendenti} righe da rivedere.', 'success')
            return redirect(url_for('import.email_dettaglio', id=id))
        flash('Manutenzione importata con successo dal verbale email.', 'success')
        return redirect(url_for('import.email_queue'))

    except Exception as e:
        flash(f'Errore durante l\'importazione: {str(e)}', 'danger')
        return redirect(url_for('import.email_dettaglio', id=id))


@import_bp.route('/import/email/<int:id>/scarta', methods=['POST'])
@login_required
def email_scarta(id):
    """Discard a pending email verbale."""
    record = get_import_in_scope(id)
    if not record:
        flash('Record non trovato.', 'danger')
        return redirect(url_for('import.email_queue'))

    preview_id = request.form.get('preview_id')
    if preview_id:
        # Si scarta la singola riga: le altre restano in coda.
        try:
            preview_id = int(preview_id)
        except (ValueError, TypeError):
            flash('Riga non valida.', 'danger')
            return redirect(url_for('import.email_dettaglio', id=id))
        riga = query_one(
            "SELECT * FROM import_preview WHERE id = ? AND import_id = ?",
            (preview_id, id)
        )
        if not riga or riga['stato'] != 'pending':
            flash('Riga non trovata o gia\' lavorata.', 'warning')
            return redirect(url_for('import.email_dettaglio', id=id))
        execute(
            """UPDATE import_preview SET stato = 'rejected',
                      note_revisione = 'Scartata manualmente' WHERE id = ?""",
            (preview_id,)
        )
        pendenti = _aggiorna_stato_import(id)
        log_attivita(g.user['id'], 'import_email_scarta', 'import_history', id,
                     f"Scartata riga {riga['riga_numero']} del verbale email: "
                     f"{record.get('filename', '')}", request.remote_addr,
                     struttura_id=record.get('struttura_id'))
        flash('Riga scartata.', 'info')
        if pendenti:
            return redirect(url_for('import.email_dettaglio', id=id))
        return redirect(url_for('import.email_queue'))

    execute(
        "UPDATE import_preview SET stato = 'rejected', "
        "note_revisione = 'Scartata manualmente' WHERE import_id = ? AND stato = 'pending'",
        (id,)
    )
    execute(
        "UPDATE import_history SET stato = 'failed', errori_dettaglio = 'Scartato manualmente' WHERE id = ?",
        (id,)
    )

    log_attivita(g.user['id'], 'import_email_scarta', 'import_history', id,
                 f"Scartato verbale email: {record.get('filename', '')}", request.remote_addr,
                 struttura_id=record.get('struttura_id'))

    flash('Verbale email scartato.', 'info')
    return redirect(url_for('import.email_queue'))
