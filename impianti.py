"""
MedInventory - Impianti Blueprint
Anagrafica impianti (elettrico, idraulico, ...), piano di manutenzione a
catalogo, interventi e documenti collegati.
"""

import os
import time

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, g, send_file, abort
)
from werkzeug.utils import secure_filename

from auth import login_required, tecnico_o_admin_required
from models import (query_one, query_all, execute, log_attivita,
                    upload_subdir, filtro_divisione, impianto_accessibile)
import impianti_service
from impianti_catalogo import voci_per_tipo, voci_mancanti

impianti_bp = Blueprint('impianti', __name__, url_prefix='/impianti')

TIPI_IMPIANTO = ('elettrico', 'idraulico', 'riscaldamento', 'climatizzazione',
                 'antincendio', 'gas_medicali', 'ascensori', 'rete_dati', 'altro')
STATI_IMPIANTO = ('attivo', 'in_manutenzione', 'fuori_servizio', 'dismesso')

PER_PAGINA = 25


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valida_impianto(form, edit_id=None):
    """Valida i campi del form impianto. Restituisce (dati, errori)."""
    errori = []
    dati = {}

    nome = (form.get('nome') or '').strip()
    if not nome:
        errori.append('Il nome e\' obbligatorio.')
    dati['nome'] = nome

    tipo = (form.get('tipo') or '').strip()
    if tipo not in TIPI_IMPIANTO:
        errori.append('Tipo impianto non valido.')
    dati['tipo'] = tipo

    stato = (form.get('stato') or 'attivo').strip()
    if stato not in STATI_IMPIANTO:
        errori.append('Stato non valido.')
    dati['stato'] = stato

    divisione_id = form.get('divisione_id')
    divisione = None
    if divisione_id:
        divisione = query_one(
            "SELECT * FROM divisioni WHERE id = ? AND struttura_id = ?",
            (divisione_id, g.struttura_id))
    if not divisione:
        errori.append('Divisione non valida.')
    elif g.user['ruolo'] not in ('admin', 'superadmin', 'tecnico') and \
            divisione['id'] not in [d['id'] for d in g.divisioni]:
        errori.append('Divisione non accessibile.')
    dati['divisione_id'] = divisione['id'] if divisione else None

    if nome:
        query = ("SELECT id FROM impianti WHERE struttura_id = ? AND nome = ?")
        params = [g.struttura_id, nome]
        if edit_id:
            query += " AND id != ?"
            params.append(edit_id)
        if query_one(query, tuple(params)):
            errori.append('Esiste gia\' un impianto con questo nome.')

    dati['descrizione'] = (form.get('descrizione') or '').strip() or None
    dati['ubicazione'] = (form.get('ubicazione') or '').strip() or None
    dati['identificativo'] = (form.get('identificativo') or '').strip() or None
    dati['note'] = (form.get('note') or '').strip() or None

    manutentore_id = form.get('manutentore_id')
    dati['manutentore_id'] = int(manutentore_id) if manutentore_id else None

    anno = form.get('anno_installazione')
    dati['anno_installazione'] = None
    if anno:
        try:
            anno_int = int(anno)
            if anno_int < 1900 or anno_int > 2100:
                errori.append('Anno installazione non plausibile.')
            else:
                dati['anno_installazione'] = anno_int
        except ValueError:
            errori.append('Anno installazione non valido.')

    tipo_custom = (form.get('tipo_custom') or '').strip() or None
    dati['tipo_custom'] = tipo_custom if tipo == 'altro' else None

    return dati, errori


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@impianti_bp.route('')
@login_required
def lista():
    tipo = request.args.get('tipo', '')
    stato = request.args.get('stato', '')
    q = request.args.get('q', '').strip()
    pagina = max(1, request.args.get('pagina', 1, type=int))

    where = ["i.struttura_id = ?"]
    params = [g.struttura_id]

    if tipo:
        where.append("i.tipo = ?")
        params.append(tipo)

    if stato:
        where.append("i.stato = ?")
        params.append(stato)
    else:
        where.append("i.stato != 'dismesso'")

    if q:
        where.append("(i.nome LIKE ? OR i.ubicazione LIKE ? OR i.identificativo LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])

    clausola, param_div = filtro_divisione('i')
    where_sql = " AND ".join(where) + (" " + clausola if clausola else "")
    params = params + list(param_div)

    totale = query_one(
        f"SELECT COUNT(*) AS n FROM impianti i WHERE {where_sql}",
        tuple(params))['n']

    offset = (pagina - 1) * PER_PAGINA
    impianti = query_all(
        f"""SELECT i.*, d.nome AS divisione_nome, d.colore AS divisione_colore,
                   m.ragione_sociale AS manutentore_nome,
                   (SELECT MIN(s.prossima_scadenza) FROM impianti_scadenze s
                    WHERE s.impianto_id = i.id AND s.attiva = 1) AS prima_scadenza
             FROM impianti i
             LEFT JOIN divisioni d ON d.id = i.divisione_id
             LEFT JOIN manutentori m ON m.id = i.manutentore_id
             WHERE {where_sql}
             ORDER BY i.nome
             LIMIT ? OFFSET ?""",
        tuple(params) + (PER_PAGINA, offset))

    totale_pagine = max(1, (totale + PER_PAGINA - 1) // PER_PAGINA)

    contesto = dict(
        impianti=impianti, tipo=tipo, stato=stato, q=q, pagina=pagina,
        totale=totale, totale_pagine=totale_pagine, per_pagina=PER_PAGINA,
        tipi=TIPI_IMPIANTO, stati=STATI_IMPIANTO)

    if request.args.get('partial'):
        return render_template('partials/impianti_table.html', **contesto)
    return render_template('impianti/lista.html', **contesto)


@impianti_bp.route('/nuovo', methods=['GET', 'POST'])
@tecnico_o_admin_required
def nuovo():
    if request.method == 'POST':
        dati, errori = _valida_impianto(request.form)
        if not errori:
            impianto_id = execute(
                """INSERT INTO impianti
                   (struttura_id, divisione_id, nome, tipo, tipo_custom,
                    descrizione, ubicazione, anno_installazione, identificativo,
                    stato, manutentore_id, note, created_by, updated_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (g.struttura_id, dati['divisione_id'], dati['nome'], dati['tipo'],
                 dati['tipo_custom'], dati['descrizione'], dati['ubicazione'],
                 dati['anno_installazione'], dati['identificativo'],
                 dati['stato'], dati['manutentore_id'], dati['note'],
                 g.user['id'], g.user['id'])
            ).lastrowid
            creati = impianti_service.applica_catalogo(
                impianto_id, dati['tipo'], request.form.getlist('catalogo'),
                time.strftime('%Y-%m-%d'))
            log_attivita(g.user['id'], 'creazione', 'impianto', impianto_id,
                         f"Impianto {dati['nome']} ({creati} voci di piano)")
            flash(f"Impianto \"{dati['nome']}\" creato.", 'success')
            return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))

        for errore in errori:
            flash(errore, 'danger')
        divisioni = query_all(
            "SELECT * FROM divisioni WHERE struttura_id = ? AND attiva = 1"
            " ORDER BY nome", (g.struttura_id,))
        manutentori = query_all(
            "SELECT * FROM manutentori WHERE struttura_id = ? AND attivo = 1"
            " ORDER BY ragione_sociale", (g.struttura_id,))
        return render_template(
            'impianti/form.html', impianto=None, form_data=request.form,
            errori=errori, divisioni=divisioni, manutentori=manutentori,
            tipi=TIPI_IMPIANTO, stati=STATI_IMPIANTO,
            catalogo={t: voci_per_tipo(t) for t in TIPI_IMPIANTO})

    divisioni = query_all(
        "SELECT * FROM divisioni WHERE struttura_id = ? AND attiva = 1"
        " ORDER BY nome", (g.struttura_id,))
    manutentori = query_all(
        "SELECT * FROM manutentori WHERE struttura_id = ? AND attivo = 1"
        " ORDER BY ragione_sociale", (g.struttura_id,))
    return render_template(
        'impianti/form.html', impianto=None, form_data={},
        errori=[], divisioni=divisioni, manutentori=manutentori,
        tipi=TIPI_IMPIANTO, stati=STATI_IMPIANTO,
        catalogo={t: voci_per_tipo(t) for t in TIPI_IMPIANTO})


@impianti_bp.route('/<int:impianto_id>')
@login_required
def dettaglio(impianto_id):
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))

    componenti = query_all(
        "SELECT * FROM impianti_componenti WHERE impianto_id = ? ORDER BY descrizione",
        (impianto_id,))
    documenti = query_all(
        "SELECT * FROM impianti_documenti WHERE impianto_id = ?"
        " ORDER BY uploaded_at DESC", (impianto_id,))
    piano = query_all(
        """SELECT s.*, CAST(julianday(s.prossima_scadenza) - julianday('now')
                            AS INTEGER) AS giorni_rimasti
           FROM impianti_scadenze s
           WHERE s.impianto_id = ? AND s.attiva = 1
           ORDER BY s.prossima_scadenza""",
        (impianto_id,))
    interventi = query_all(
        """SELECT iv.*, m.ragione_sociale AS manutentore_nome,
                  s.nome AS scadenza_nome
           FROM impianti_interventi iv
           LEFT JOIN manutentori m ON m.id = iv.manutentore_id
           LEFT JOIN impianti_scadenze s ON s.id = iv.scadenza_id
           WHERE iv.impianto_id = ?
           ORDER BY iv.data_intervento DESC""",
        (impianto_id,))
    divisioni = query_all(
        "SELECT * FROM divisioni WHERE struttura_id = ? AND attiva = 1"
        " ORDER BY nome", (impianto['struttura_id'],))
    manutentori = query_all(
        "SELECT * FROM manutentori WHERE struttura_id = ? AND attivo = 1"
        " ORDER BY ragione_sociale", (impianto['struttura_id'],))

    return render_template(
        'impianti/dettaglio.html', impianto=impianto, componenti=componenti,
        documenti=documenti, piano=piano, interventi=interventi,
        divisioni=divisioni, manutentori=manutentori,
        tipi=TIPI_IMPIANTO, stati=STATI_IMPIANTO,
        voci_catalogo=voci_mancanti(impianto['tipo'], [p['nome'] for p in piano]))
