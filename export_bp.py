"""
MedInventory - Export Blueprint
Routes for exporting data to Excel and PDF.
"""

from flask import (
    Blueprint, request, g, current_app, send_file
)

from auth import login_required
from models import query_all

export_bp = Blueprint('export', __name__)


def _get_divisione_filter():
    """Build division filter SQL clause."""
    div = getattr(g, 'divisione_attiva', None)
    if div and div.get('id') != 'tutte':
        return "AND a.divisione_id = ?", [div['id']]
    elif getattr(g, 'user', {}).get('ruolo') == 'admin':
        return "", []
    else:
        ids = [d['id'] for d in getattr(g, 'divisioni', [])]
        if ids:
            ph = ','.join('?' * len(ids))
            return f"AND a.divisione_id IN ({ph})", ids
        return "AND 1=0", []


def _get_divisione_nome():
    """Get current active division name."""
    div = getattr(g, 'divisione_attiva', None)
    if div and div.get('id') != 'tutte':
        return div.get('nome', '')
    return 'Tutte le divisioni'


# ============================================================================
# EXPORT APPARECCHI
# ============================================================================

@export_bp.route('/export/apparecchi/excel')
@login_required
def apparecchi_excel():
    """Export apparecchi to Excel."""
    from export_service import export_apparecchi_excel

    div_clause, div_params = _get_divisione_filter()
    apparecchi = query_all(
        f"""SELECT a.*, d.nome as divisione_nome
            FROM apparecchi a
            LEFT JOIN divisioni d ON a.divisione_id = d.id
            WHERE a.stato != 'dismesso' {div_clause}
            ORDER BY a.marca, a.modello""",
        div_params
    )

    divisione_nome = _get_divisione_nome()
    buffer = export_apparecchi_excel(apparecchi, divisione_nome)

    return send_file(
        buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'apparecchi_{divisione_nome.replace(" ", "_").lower()}.xlsx'
    )


@export_bp.route('/export/apparecchi/pdf')
@login_required
def apparecchi_pdf():
    """Export apparecchi to PDF."""
    from export_service import export_apparecchi_pdf

    div_clause, div_params = _get_divisione_filter()
    apparecchi = query_all(
        f"""SELECT a.*, d.nome as divisione_nome
            FROM apparecchi a
            LEFT JOIN divisioni d ON a.divisione_id = d.id
            WHERE a.stato != 'dismesso' {div_clause}
            ORDER BY a.marca, a.modello""",
        div_params
    )

    config = current_app.config['APP_CONFIG']
    divisione_nome = _get_divisione_nome()
    buffer = export_apparecchi_pdf(
        apparecchi,
        divisione_nome=divisione_nome,
        structure_name=config.get('structure_name', ''),
        app_name=config.get('app_name', 'MedInventory')
    )

    return send_file(
        buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'apparecchi_{divisione_nome.replace(" ", "_").lower()}.pdf'
    )


# ============================================================================
# EXPORT MANUTENZIONI
# ============================================================================

@export_bp.route('/export/manutenzioni/excel')
@login_required
def manutenzioni_excel():
    """Export manutenzioni to Excel."""
    from export_service import export_manutenzioni_excel

    div_clause, div_params = _get_divisione_filter()
    manutenzioni = query_all(
        f"""SELECT m.*, a.marca, a.modello, a.matricola,
                   d.nome as divisione_nome
            FROM manutenzioni m
            JOIN apparecchi a ON m.apparecchio_id = a.id
            LEFT JOIN divisioni d ON a.divisione_id = d.id
            WHERE 1=1 {div_clause}
            ORDER BY m.data_intervento DESC""",
        div_params
    )

    divisione_nome = _get_divisione_nome()
    buffer = export_manutenzioni_excel(manutenzioni, divisione_nome)

    return send_file(
        buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'manutenzioni_{divisione_nome.replace(" ", "_").lower()}.xlsx'
    )


# ============================================================================
# EXPORT SCADENZARIO
# ============================================================================

# ============================================================================
# EXPORT VERIFICHE
# ============================================================================

@export_bp.route('/export/verifiche/excel')
@login_required
def verifiche_excel():
    """Export verifiche to Excel."""
    from export_service import export_verifiche_excel

    div_clause, div_params = _get_divisione_filter()
    verifiche = query_all(
        f"""SELECT v.*, a.marca, a.modello, a.matricola,
                   d.nome as divisione_nome
            FROM verifiche v
            JOIN apparecchi a ON v.apparecchio_id = a.id
            LEFT JOIN divisioni d ON a.divisione_id = d.id
            WHERE 1=1 {div_clause}
            ORDER BY v.data_verifica DESC""",
        div_params
    )

    divisione_nome = _get_divisione_nome()
    buffer = export_verifiche_excel(verifiche, divisione_nome)

    return send_file(
        buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'verifiche_{divisione_nome.replace(" ", "_").lower()}.xlsx'
    )


@export_bp.route('/export/verifiche/pdf')
@login_required
def verifiche_pdf():
    """Export verifiche to PDF."""
    from export_service import export_verifiche_pdf

    div_clause, div_params = _get_divisione_filter()
    verifiche = query_all(
        f"""SELECT v.*, a.marca, a.modello, a.matricola,
                   d.nome as divisione_nome
            FROM verifiche v
            JOIN apparecchi a ON v.apparecchio_id = a.id
            LEFT JOIN divisioni d ON a.divisione_id = d.id
            WHERE 1=1 {div_clause}
            ORDER BY v.data_verifica DESC""",
        div_params
    )

    config = current_app.config['APP_CONFIG']
    divisione_nome = _get_divisione_nome()
    buffer = export_verifiche_pdf(
        verifiche,
        divisione_nome=divisione_nome,
        structure_name=config.get('structure_name', ''),
        app_name=config.get('app_name', 'MedInventory')
    )

    return send_file(
        buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'verifiche_{divisione_nome.replace(" ", "_").lower()}.pdf'
    )


# ============================================================================
# EXPORT SCADENZARIO
# ============================================================================

@export_bp.route('/export/scadenzario/excel')
@login_required
def scadenzario_excel():
    """Export scadenzario to Excel."""
    from export_service import export_scadenzario_excel

    div = getattr(g, 'divisione_attiva', None)
    if div and div.get('id') != 'tutte':
        scadenze = query_all(
            """SELECT ps.*, d.nome as divisione_nome
               FROM prossime_scadenze ps
               LEFT JOIN divisioni d ON ps.divisione_id = d.id
               WHERE ps.divisione_id = ?
               ORDER BY ps.prossima_scadenza ASC""",
            [div['id']]
        )
    else:
        scadenze = query_all(
            """SELECT ps.*, d.nome as divisione_nome
               FROM prossime_scadenze ps
               LEFT JOIN divisioni d ON ps.divisione_id = d.id
               ORDER BY ps.prossima_scadenza ASC"""
        )

    divisione_nome = _get_divisione_nome()
    buffer = export_scadenzario_excel(scadenze, divisione_nome)

    return send_file(
        buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'scadenzario_{divisione_nome.replace(" ", "_").lower()}.xlsx'
    )


@export_bp.route('/export/scadenzario/pdf')
@login_required
def scadenzario_pdf():
    """Export scadenzario to PDF."""
    from export_service import export_scadenzario_pdf

    div = getattr(g, 'divisione_attiva', None)
    if div and div.get('id') != 'tutte':
        scadenze = query_all(
            """SELECT ps.*, d.nome as divisione_nome
               FROM prossime_scadenze ps
               LEFT JOIN divisioni d ON ps.divisione_id = d.id
               WHERE ps.divisione_id = ?
               ORDER BY ps.prossima_scadenza ASC""",
            [div['id']]
        )
    else:
        scadenze = query_all(
            """SELECT ps.*, d.nome as divisione_nome
               FROM prossime_scadenze ps
               LEFT JOIN divisioni d ON ps.divisione_id = d.id
               ORDER BY ps.prossima_scadenza ASC"""
        )

    config = current_app.config['APP_CONFIG']
    divisione_nome = _get_divisione_nome()
    buffer = export_scadenzario_pdf(
        scadenze,
        divisione_nome=divisione_nome,
        structure_name=config.get('structure_name', ''),
        app_name=config.get('app_name', 'MedInventory')
    )

    return send_file(
        buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'scadenzario_{divisione_nome.replace(" ", "_").lower()}.pdf'
    )
