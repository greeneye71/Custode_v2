"""
MedInventory - Stampe

Prospetti PDF pensati per la carta. Questo modulo e' l'unico che conosce lo
scope dell'utente: fa le query, valida i parametri e passa dati gia' filtrati
al motore in report_service.py, che dell'applicazione non sa nulla.
"""

import io
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, g, send_file)
from werkzeug.utils import secure_filename

from auth import login_required
from models import query_all

stampe_bp = Blueprint('stampe', __name__, url_prefix='/stampe')


def _divisioni_accessibili():
    """Divisioni su cui l'utente corrente puo' stampare.

    Per un utente non amministrativo sono quelle assegnate: 'tutta la struttura'
    per lui significa l'insieme delle sue divisioni, non il resto dei reparti.
    """
    return list(getattr(g, 'divisioni', []) or [])


def _divisione_in_scope(divisione_id):
    """Restituisce la divisione solo se l'utente puo' stamparla, altrimenti None."""
    try:
        cercata = int(divisione_id)
    except (TypeError, ValueError):
        return None
    for divisione in _divisioni_accessibili():
        if divisione['id'] == cercata:
            return divisione
    return None


def _contesto_base(titolo, ambito='', **extra):
    struttura = getattr(g, 'struttura', None)
    contesto = {
        'struttura_nome': (struttura or {}).get('nome') or 'MedInventory',
        'titolo': titolo,
        'ambito': ambito,
        'logo_path': None,
        'mostra_firma': request.args.get('firma') == '1',
    }
    contesto.update(extra)
    return contesto


def _nome_file(prefisso, ambito):
    parti = [prefisso]
    struttura = getattr(g, 'struttura', None)
    if struttura and struttura.get('nome'):
        parti.append(struttura['nome'])
    if ambito:
        parti.append(ambito)
    parti.append(datetime.now().strftime('%Y%m%d'))
    grezzo = '_'.join(p.replace(' ', '-').lower() for p in parti if p)
    return secure_filename(grezzo)


def _pdf(contenuto, nome_file):
    return send_file(io.BytesIO(contenuto), mimetype='application/pdf',
                     as_attachment=True, download_name=f'{nome_file}.pdf')


@stampe_bp.route('')
@login_required
def index():
    divisioni = _divisioni_accessibili()
    if not getattr(g, 'struttura_id', None):
        flash('Per generare le stampe entra prima nel contesto di una struttura.', 'warning')
    return render_template('stampe/index.html',
                           divisioni=divisioni,
                           multi_divisione=len(divisioni) > 1)


@stampe_bp.route('/inventario')
@login_required
def inventario():
    from report_service import stampa_inventario

    divisione_id = request.args.get('divisione_id', 'tutte')
    includi_dismessi = request.args.get('dismessi') == '1'

    if divisione_id == 'tutte':
        divisioni = _divisioni_accessibili()
        ids = [d['id'] for d in divisioni]
        ambito = ''
        raggruppa = len(ids) > 1
    else:
        divisione = _divisione_in_scope(divisione_id)
        if not divisione:
            flash('Divisione non disponibile.', 'danger')
            return redirect(url_for('stampe.index'))
        ids = [divisione['id']]
        ambito = f"Divisione: {divisione['nome']}"
        raggruppa = False

    if not ids:
        flash('Nessuna divisione accessibile.', 'warning')
        return redirect(url_for('stampe.index'))

    segnaposto = ','.join('?' * len(ids))
    filtro_stato = '' if includi_dismessi else "AND a.stato != 'dismesso'"
    righe = query_all(
        f"""SELECT a.marca, a.modello, a.matricola, a.ubicazione,
                   d.nome AS divisione_nome
            FROM apparecchi a
            LEFT JOIN divisioni d ON d.id = a.divisione_id
            WHERE a.divisione_id IN ({segnaposto}) {filtro_stato}
            ORDER BY d.nome, a.marca, a.modello""",
        ids)

    contesto = _contesto_base('Inventario apparecchi elettromedicali', ambito,
                              raggruppa=raggruppa)
    return _pdf(stampa_inventario(righe, contesto),
                _nome_file('inventario', ambito.replace('Divisione: ', '')))
