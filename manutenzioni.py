"""
MedInventory - Manutenzioni (Maintenance) Blueprint
CRUD for maintenance records + Scadenzario (deadline tracking).
"""

import os
from datetime import datetime

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, g, current_app, send_from_directory
)
from werkzeug.utils import secure_filename

from auth import login_required
from models import query_one, query_all, execute, log_attivita

manutenzioni_bp = Blueprint('manutenzioni', __name__)

ALLOWED_VERBALE_EXT = {'pdf'}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_divisione_filter(table_alias='a'):
    """Return SQL WHERE clause and params for division filtering."""
    div = g.divisione_attiva
    if div and div.get('id') != 'tutte':
        return f"AND {table_alias}.divisione_id = ?", [div['id']]
    elif g.user['ruolo'] == 'admin':
        return "", []
    else:
        ids = [d['id'] for d in g.divisioni]
        if not ids:
            return "AND 1=0", []
        placeholders = ','.join('?' * len(ids))
        return f"AND {table_alias}.divisione_id IN ({placeholders})", ids


def _validate_manutenzione(form_data):
    """Validate maintenance form data. Returns (cleaned_data, errors)."""
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

    # Required: tipo
    data['tipo'] = form_data.get('tipo', '')
    if data['tipo'] not in ('preventiva', 'correttiva', 'verifica', 'calibrazione'):
        errors['tipo'] = "Seleziona un tipo di manutenzione."

    # Required: data_intervento
    data['data_intervento'] = form_data.get('data_intervento', '').strip()
    if not data['data_intervento']:
        errors['data_intervento'] = "La data dell'intervento è obbligatoria."
    else:
        try:
            datetime.strptime(data['data_intervento'], '%Y-%m-%d')
        except ValueError:
            errors['data_intervento'] = "Formato data non valido (YYYY-MM-DD)."

    # Optional: periodicita_giorni
    periodicita = form_data.get('periodicita_giorni', '').strip()
    if periodicita:
        try:
            data['periodicita_giorni'] = int(periodicita)
            if data['periodicita_giorni'] <= 0:
                errors['periodicita_giorni'] = "La periodicità deve essere positiva."
        except ValueError:
            errors['periodicita_giorni'] = "Valore non valido."
    else:
        data['periodicita_giorni'] = None

    # Optional: prossima_scadenza
    data['prossima_scadenza'] = form_data.get('prossima_scadenza', '').strip() or None

    # Optional text fields
    data['tecnico_ditta'] = form_data.get('tecnico_ditta', '').strip() or None
    data['descrizione'] = form_data.get('descrizione', '').strip() or None
    data['esito'] = form_data.get('esito', '').strip() or None

    # Optional: costo
    costo = form_data.get('costo', '').strip()
    if costo:
        try:
            data['costo'] = float(costo.replace(',', '.'))
        except ValueError:
            errors['costo'] = "Importo non valido."
    else:
        data['costo'] = None

    return data, errors


def _save_verbale(file_obj, manutenzione_id):
    """Save uploaded PDF verbale for a manutenzione. Returns relative path or None."""
    if not file_obj or not file_obj.filename:
        return None
    ext = file_obj.filename.rsplit('.', 1)[-1].lower()
    if ext not in ALLOWED_VERBALE_EXT:
        return None
    uploads_dir = os.path.join(current_app.config['UPLOADS_PATH'], 'verbali')
    os.makedirs(uploads_dir, exist_ok=True)
    safe_name = secure_filename(f"{int(datetime.now().timestamp())}_{file_obj.filename}")
    full_path = os.path.join(uploads_dir, safe_name)
    file_obj.save(full_path)
    return f"verbali/{safe_name}"


# ---------------------------------------------------------------------------
# Manutenzioni CRUD Routes
# ---------------------------------------------------------------------------

@manutenzioni_bp.route('/manutenzioni')
@login_required
def lista():
    """List maintenance records with filters."""
    div_clause, div_params = _get_divisione_filter('a')

    search = request.args.get('search', '').strip()
    tipo = request.args.get('tipo', '')
    data_da = request.args.get('data_da', '')
    data_a = request.args.get('data_a', '')
    page = request.args.get('page', 1, type=int)
    per_page = 25

    where_clauses = ["1=1"]
    params = []

    if search:
        where_clauses.append(
            "(a.matricola LIKE ? OR a.marca LIKE ? OR a.modello LIKE ? "
            "OR m.tecnico_ditta LIKE ? OR m.descrizione LIKE ?)"
        )
        s = f'%{search}%'
        params.extend([s, s, s, s, s])

    if tipo:
        where_clauses.append("m.tipo = ?")
        params.append(tipo)

    if data_da:
        where_clauses.append("m.data_intervento >= ?")
        params.append(data_da)

    if data_a:
        where_clauses.append("m.data_intervento <= ?")
        params.append(data_a)

    where_sql = " AND ".join(where_clauses)

    # Count
    count_sql = f"""
        SELECT COUNT(*) as cnt FROM manutenzioni m
        JOIN apparecchi a ON m.apparecchio_id = a.id
        WHERE {where_sql} {div_clause}
    """
    total = query_one(count_sql, params + div_params)['cnt']

    offset = (page - 1) * per_page
    total_pages = max(1, (total + per_page - 1) // per_page)

    # Query
    data_sql = f"""
        SELECT m.*, a.marca, a.modello, a.matricola,
               d.nome as divisione_nome, d.colore as divisione_colore,
               u.nome || ' ' || u.cognome as creato_da_nome
        FROM manutenzioni m
        JOIN apparecchi a ON m.apparecchio_id = a.id
        LEFT JOIN divisioni d ON a.divisione_id = d.id
        LEFT JOIN utenti u ON m.created_by = u.id
        WHERE {where_sql} {div_clause}
        ORDER BY m.data_intervento DESC
        LIMIT ? OFFSET ?
    """
    manutenzioni = query_all(data_sql, params + div_params + [per_page, offset])

    context = {
        'manutenzioni': manutenzioni,
        'filtri': {'search': search, 'tipo': tipo, 'data_da': data_da, 'data_a': data_a},
        'pagination': {'page': page, 'per_page': per_page, 'total': total, 'total_pages': total_pages},
    }

    if request.args.get('partial'):
        return render_template('partials/manutenzioni_table.html', **context)

    return render_template('manutenzioni/lista.html', **context)


@manutenzioni_bp.route('/manutenzioni/nuova', methods=['GET', 'POST'])
@login_required
def nuova():
    """Create a new maintenance record."""
    # Pre-fill apparecchio if coming from detail page
    apparecchio_id = request.args.get('apparecchio_id', '')

    if request.method == 'GET':
        apparecchi = _get_accessible_apparecchi()
        return render_template('manutenzioni/form.html',
                               manutenzione=None, errors={},
                               apparecchi=apparecchi,
                               form_data={'apparecchio_id': apparecchio_id})

    data, errors = _validate_manutenzione(request.form)

    if errors:
        apparecchi = _get_accessible_apparecchi()
        return render_template('manutenzioni/form.html',
                               manutenzione=None, errors=errors,
                               apparecchi=apparecchi, form_data=request.form)

    cursor = execute(
        """INSERT INTO manutenzioni
           (apparecchio_id, tipo, data_intervento, prossima_scadenza,
            periodicita_giorni, tecnico_ditta, descrizione, esito, costo, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data['apparecchio_id'], data['tipo'], data['data_intervento'],
         data['prossima_scadenza'], data['periodicita_giorni'],
         data['tecnico_ditta'], data['descrizione'], data['esito'],
         data['costo'], g.user['id'])
    )
    manutenzione_id = cursor.lastrowid

    # Save verbale if uploaded
    verbale_file = request.files.get('verbale')
    if verbale_file and verbale_file.filename:
        verbale_path = _save_verbale(verbale_file, manutenzione_id)
        if verbale_path:
            execute("UPDATE manutenzioni SET verbale_path = ? WHERE id = ?",
                    (verbale_path, manutenzione_id))

    # Get apparecchio info for log
    app_info = query_one("SELECT marca, modello, matricola FROM apparecchi WHERE id = ?",
                         (data['apparecchio_id'],))
    log_attivita(g.user['id'], 'creazione', 'manutenzioni', manutenzione_id,
                 f"Manutenzione {data['tipo']} per {app_info['marca']} {app_info['modello']}",
                 request.remote_addr)

    flash('Manutenzione registrata con successo.', 'success')
    return redirect(url_for('apparecchi.dettaglio', id=data['apparecchio_id']))


@manutenzioni_bp.route('/manutenzioni/<int:id>/modifica', methods=['GET', 'POST'])
@login_required
def modifica(id):
    """Edit a maintenance record."""
    manutenzione = query_one(
        """SELECT m.*, a.marca, a.modello, a.matricola, a.divisione_id
           FROM manutenzioni m
           JOIN apparecchi a ON m.apparecchio_id = a.id
           WHERE m.id = ?""", (id,)
    )
    if not manutenzione:
        flash('Manutenzione non trovata.', 'danger')
        return redirect(url_for('manutenzioni.lista'))

    if g.user['ruolo'] != 'admin':
        accessible_ids = [d['id'] for d in g.divisioni]
        if manutenzione['divisione_id'] not in accessible_ids:
            flash('Accesso non autorizzato.', 'danger')
            return redirect(url_for('manutenzioni.lista'))

    if request.method == 'GET':
        apparecchi = _get_accessible_apparecchi()
        return render_template('manutenzioni/form.html',
                               manutenzione=manutenzione, errors={},
                               apparecchi=apparecchi, form_data=manutenzione)

    data, errors = _validate_manutenzione(request.form)

    if errors:
        apparecchi = _get_accessible_apparecchi()
        return render_template('manutenzioni/form.html',
                               manutenzione=manutenzione, errors=errors,
                               apparecchi=apparecchi, form_data=request.form)

    execute(
        """UPDATE manutenzioni SET
           apparecchio_id=?, tipo=?, data_intervento=?, prossima_scadenza=?,
           periodicita_giorni=?, tecnico_ditta=?, descrizione=?, esito=?, costo=?
           WHERE id=?""",
        (data['apparecchio_id'], data['tipo'], data['data_intervento'],
         data['prossima_scadenza'], data['periodicita_giorni'],
         data['tecnico_ditta'], data['descrizione'], data['esito'],
         data['costo'], id)
    )

    # Update verbale if new file uploaded
    verbale_file = request.files.get('verbale')
    if verbale_file and verbale_file.filename:
        verbale_path = _save_verbale(verbale_file, id)
        if verbale_path:
            execute("UPDATE manutenzioni SET verbale_path = ? WHERE id = ?",
                    (verbale_path, id))

    log_attivita(g.user['id'], 'modifica', 'manutenzioni', id,
                 f"Manutenzione {data['tipo']} modificata", request.remote_addr)

    flash('Manutenzione aggiornata con successo.', 'success')
    return redirect(url_for('apparecchi.dettaglio', id=data['apparecchio_id']))


@manutenzioni_bp.route('/manutenzioni/<int:id>/elimina', methods=['POST'])
@login_required
def elimina(id):
    """Delete a maintenance record."""
    manutenzione = query_one(
        """SELECT m.*, a.divisione_id FROM manutenzioni m
           JOIN apparecchi a ON m.apparecchio_id = a.id
           WHERE m.id = ?""", (id,)
    )
    if not manutenzione:
        flash('Manutenzione non trovata.', 'danger')
        return redirect(url_for('manutenzioni.lista'))

    if g.user['ruolo'] != 'admin':
        accessible_ids = [d['id'] for d in g.divisioni]
        if manutenzione['divisione_id'] not in accessible_ids:
            flash('Accesso non autorizzato.', 'danger')
            return redirect(url_for('manutenzioni.lista'))

    # Delete associated verbale file if present
    if manutenzione.get('verbale_path'):
        full_path = os.path.join(current_app.config['UPLOADS_PATH'], manutenzione['verbale_path'])
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
            except OSError:
                pass

    execute("DELETE FROM manutenzioni WHERE id = ?", (id,))

    log_attivita(g.user['id'], 'eliminazione', 'manutenzioni', id,
                 f"Manutenzione {manutenzione['tipo']} eliminata", request.remote_addr)

    flash('Manutenzione eliminata.', 'warning')
    return redirect(url_for('apparecchi.dettaglio', id=manutenzione['apparecchio_id']))


@manutenzioni_bp.route('/manutenzioni/<int:id>/verbale')
@login_required
def scarica_verbale(id):
    """Download PDF verbale for a manutenzione."""
    manutenzione = query_one("SELECT * FROM manutenzioni WHERE id = ?", (id,))
    if not manutenzione or not manutenzione.get('verbale_path'):
        flash('Verbale non trovato.', 'danger')
        return redirect(url_for('manutenzioni.lista'))

    uploads_path = current_app.config['UPLOADS_PATH']
    directory = os.path.join(uploads_path, 'verbali')
    filename = os.path.basename(manutenzione['verbale_path'])
    return send_from_directory(directory, filename, as_attachment=True)


# ---------------------------------------------------------------------------
# Scadenzario
# ---------------------------------------------------------------------------

@manutenzioni_bp.route('/scadenzario')
@login_required
def scadenzario():
    """Deadline tracking view with priority badges."""
    div_clause, div_params = _get_divisione_filter('ps')

    tipo = request.args.get('tipo', '')
    priorita = request.args.get('priorita', '')
    tipo_record = request.args.get('tipo_record', '')  # 'manutenzione' | 'verifica' | ''

    where_clauses = ["1=1"]
    params = []

    if tipo:
        where_clauses.append("ps.tipo_manutenzione = ?")
        params.append(tipo)

    if priorita:
        where_clauses.append("ps.priorita = ?")
        params.append(priorita)

    if tipo_record == 'manutenzione':
        where_clauses.append("ps.tipo_record = 'manutenzione'")
    elif tipo_record == 'verifica':
        where_clauses.append("ps.tipo_record = 'verifica'")

    where_sql = " AND ".join(where_clauses)
    # Fix div_clause to use 'ps' alias
    div_clause_ps = div_clause.replace('a.divisione_id', 'ps.divisione_id')

    # Summary counts by priority
    summary_sql = f"""
        SELECT priorita, COUNT(*) as cnt
        FROM prossime_scadenze ps
        WHERE 1=1 {div_clause_ps}
        GROUP BY priorita
    """
    summary_rows = query_all(summary_sql, div_params)
    summary = {r['priorita']: r['cnt'] for r in summary_rows}

    # Summary counts by tipo_record
    tipo_summary_rows = query_all(
        f"""SELECT tipo_record, COUNT(*) as cnt
            FROM prossime_scadenze ps
            WHERE 1=1 {div_clause_ps}
            GROUP BY tipo_record""",
        div_params
    )
    tipo_summary = {r['tipo_record']: r['cnt'] for r in tipo_summary_rows}

    # Main query
    data_sql = f"""
        SELECT ps.*, d.nome as divisione_nome, d.colore as divisione_colore
        FROM prossime_scadenze ps
        LEFT JOIN divisioni d ON ps.divisione_id = d.id
        WHERE {where_sql} {div_clause_ps}
        ORDER BY ps.prossima_scadenza ASC
    """
    scadenze = query_all(data_sql, params + div_params)

    context = {
        'scadenze': scadenze,
        'summary': summary,
        'tipo_summary': tipo_summary,
        'filtri': {'tipo': tipo, 'priorita': priorita, 'tipo_record': tipo_record},
    }

    if request.args.get('partial'):
        return render_template('partials/scadenze_table.html', **context)

    return render_template('manutenzioni/scadenzario.html', **context)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_accessible_apparecchi():
    """Get list of apparecchi accessible by current user."""
    div_clause, div_params = _get_divisione_filter('a')
    return query_all(
        f"""SELECT a.id, a.matricola, a.marca, a.modello, d.nome as divisione_nome
            FROM apparecchi a
            LEFT JOIN divisioni d ON a.divisione_id = d.id
            WHERE a.stato != 'dismesso' {div_clause}
            ORDER BY a.marca, a.modello""",
        div_params
    )
