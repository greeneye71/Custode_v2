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
    flash, g, current_app, send_from_directory, abort
)
from werkzeug.utils import secure_filename

import allegati
from auth import login_required
from models import (query_one, query_all, execute, log_attivita, upload_subdir,
                    nome_file_unico, apparecchio_accessibile, filtro_divisione)

verifiche_bp = Blueprint('verifiche', __name__)

ALLOWED_DOC_EXT = {'pdf'}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_accessible_apparecchi():
    """Get list of apparecchi accessible by current user."""
    div_clause, div_params = filtro_divisione('a')
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
            # Verifica struttura E divisione: un utente non deve poter agganciare
            # il record a un apparecchio fuori dal proprio scope.
            if not apparecchio_accessibile(data['apparecchio_id']):
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
    # M05: estensione e contenuto, prima di scrivere su disco.
    if allegati.verifica(file_obj, ALLOWED_DOC_EXT):
        return None
    uploads_dir, rel_prefix = upload_subdir('verifiche', struttura_id)
    safe_name = nome_file_unico(file_obj.filename)
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
    div_clause, div_params = filtro_divisione('a')

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
    verifica = query_one(
        """SELECT v.*, a.marca, a.modello, a.matricola, a.divisione_id
           FROM verifiche v
           JOIN apparecchi a ON v.apparecchio_id = a.id
           WHERE v.id = ?""",
        (id,)
    )
    # Il cancello e' apparecchio_accessibile: verifica struttura e divisione
    # sull'apparecchio a cui appartiene la verifica. Stesso messaggio, stesso
    # redirect di "non trovata" quando la riga non si trova e quando non e'
    # accessibile: chi tenta non deve poter distinguere i due casi.
    if not verifica or not apparecchio_accessibile(verifica['apparecchio_id']):
        flash('Verifica non trovata.', 'danger')
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
    verifica = query_one(
        """SELECT v.*, a.divisione_id, v.apparecchio_id FROM verifiche v
           JOIN apparecchi a ON v.apparecchio_id = a.id
           WHERE v.id = ?""",
        (id,)
    )
    if not verifica or not apparecchio_accessibile(verifica['apparecchio_id']):
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

    # Isolamento multi-tenant: l'apparecchio deve essere nello scope dell'utente
    if not apparecchio_accessibile(verifica['apparecchio_id']):
        flash('Documento non trovato.', 'danger')
        return redirect(url_for('verifiche.lista'))

    uploads_path = current_app.config['UPLOADS_PATH']
    rel = verifica['documento_path']
    # Il percorso deve restare dentro uploads/ (difesa in profondità)
    resolved = os.path.realpath(os.path.join(uploads_path, rel))
    if not resolved.startswith(os.path.realpath(uploads_path) + os.sep):
        abort(403)
    return send_from_directory(os.path.dirname(resolved),
                               os.path.basename(resolved),
                               as_attachment=True)


# ---------------------------------------------------------------------------
# Import massivo AI (percorso storico, ora reindirizzato)
# ---------------------------------------------------------------------------
#
# Fino alla 2.8.0 le verifiche avevano un proprio import AI, parallelo a quello
# unificato di /import. Convalidava la divisione solo contro la struttura,
# riverificava apparecchio_accessibile() per il solo override manuale e non per
# il match automatico, e non aveva test. Due percorsi autorizzativi per la
# stessa scrittura sono uno di troppo: queste rotte restano solo per non
# rompere i vecchi segnalibri e reindirizzano all'import unificato.


@verifiche_bp.route('/verifiche/import')
@login_required
def import_upload():
    """Reindirizza all'import unificato."""
    return redirect(url_for('import.upload'))


@verifiche_bp.route('/verifiche/import/analizza', methods=['POST'])
@login_required
def import_analizza():
    """Reindirizza all'import unificato."""
    flash("L'import delle verifiche passa dalla pagina di import unificata.", 'info')
    return redirect(url_for('import.upload'))


@verifiche_bp.route('/verifiche/import/<int:id>/preview')
@login_required
def import_preview(id):
    """Reindirizza alla preview dell'import unificato."""
    return redirect(url_for('import.preview', id=id))


@verifiche_bp.route('/verifiche/import/<int:id>/esegui', methods=['POST'])
@login_required
def import_esegui(id):
    """Reindirizza all'esecuzione dell'import unificato."""
    return redirect(url_for('import.preview', id=id))
