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
TIPI_DOCUMENTO = ('progetto', 'dichiarazione_conformita', 'collaudo',
                  'certificato', 'libretto', 'planimetria', 'verbale', 'altro')
ESTENSIONI_DOCUMENTO = {'pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx', 'xls',
                        'xlsx', 'dwg', 'dxf', 'zip'}

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


@impianti_bp.route('/<int:impianto_id>/modifica', methods=['GET', 'POST'])
@tecnico_o_admin_required
def modifica(impianto_id):
    """Modifica dell'anagrafica. Il piano non si tocca da qui."""
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))

    if request.method == 'POST':
        dati, errori = _valida_impianto(request.form, edit_id=impianto_id)
        if errori:
            for e in errori:
                flash(e, 'danger')
        else:
            execute(
                """UPDATE impianti SET divisione_id = ?, nome = ?, tipo = ?,
                       tipo_custom = ?, descrizione = ?, ubicazione = ?,
                       anno_installazione = ?, identificativo = ?, stato = ?,
                       manutentore_id = ?, note = ?, updated_by = ?,
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (dati['divisione_id'], dati['nome'], dati['tipo'],
                 dati['tipo_custom'], dati['descrizione'], dati['ubicazione'],
                 dati['anno_installazione'], dati['identificativo'],
                 dati['stato'], dati['manutentore_id'], dati['note'],
                 g.user['id'], impianto_id)
            )
            log_attivita(g.user['id'], 'modifica', 'impianto', impianto_id,
                         f"Impianto {dati['nome']}")
            flash('Impianto aggiornato.', 'success')
            return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))

    divisioni = query_all(
        "SELECT * FROM divisioni WHERE struttura_id = ? ORDER BY nome",
        (impianto['struttura_id'],))
    manutentori = query_all(
        "SELECT * FROM manutentori WHERE struttura_id = ? AND attivo = 1"
        " ORDER BY ragione_sociale", (impianto['struttura_id'],))
    form_data = request.form if request.method == 'POST' else impianto
    return render_template(
        'impianti/form.html', impianto=impianto, form_data=form_data,
        divisioni=divisioni, manutentori=manutentori, tipi=TIPI_IMPIANTO,
        stati=STATI_IMPIANTO, catalogo={})


@impianti_bp.route('/<int:impianto_id>/dismetti', methods=['POST'])
@tecnico_o_admin_required
def dismetti(impianto_id):
    """Cancellazione logica: stato 'dismesso', righe intatte."""
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))
    execute("UPDATE impianti SET stato = 'dismesso', updated_by = ?,"
            " updated_at = datetime('now') WHERE id = ?",
            (g.user['id'], impianto_id))
    log_attivita(g.user['id'], 'dismissione', 'impianto', impianto_id,
                 f"Impianto {impianto['nome']} dismesso")
    flash('Impianto dismesso.', 'success')
    return redirect(url_for('impianti.lista'))


@impianti_bp.route('/<int:impianto_id>/componenti', methods=['POST'])
@tecnico_o_admin_required
def componenti(impianto_id):
    """Aggiunge un componente all'impianto."""
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))
    descrizione = (request.form.get('descrizione') or '').strip()
    if not descrizione:
        flash('La descrizione del componente è obbligatoria.', 'danger')
        return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))
    execute(
        """INSERT INTO impianti_componenti
           (impianto_id, descrizione, marca, modello, matricola, ubicazione, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (impianto_id, descrizione,
         (request.form.get('marca') or '').strip() or None,
         (request.form.get('modello') or '').strip() or None,
         (request.form.get('matricola') or '').strip() or None,
         (request.form.get('ubicazione') or '').strip() or None,
         (request.form.get('note') or '').strip() or None)
    )
    log_attivita(g.user['id'], 'creazione', 'impianto_componente', impianto_id,
                 f"Componente {descrizione} su {impianto['nome']}")
    flash('Componente aggiunto.', 'success')
    return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))


@impianti_bp.route('/<int:impianto_id>/componenti/<int:componente_id>/elimina',
                   methods=['POST'])
@tecnico_o_admin_required
def elimina_componente(impianto_id, componente_id):
    """Elimina un componente. Le righe di piano che lo citano restano, con
    componente_id a NULL (ON DELETE SET NULL)."""
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))
    execute("DELETE FROM impianti_componenti WHERE id = ? AND impianto_id = ?",
            (componente_id, impianto_id))
    log_attivita(g.user['id'], 'eliminazione', 'impianto_componente', impianto_id,
                 f"Componente {componente_id} di {impianto['nome']}")
    flash('Componente eliminato.', 'success')
    return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))


@impianti_bp.route('/<int:impianto_id>/documenti', methods=['POST'])
@login_required
def carica_documento(impianto_id):
    """Carica un documento dell'impianto con i dati dell'emittente.

    L'emittente e' testo libero, non una chiave esterna: le ditte che firmano
    progetti e collaudi cambiano a ogni documento e non tornano piu'. I
    manutentori, che invece tornano, hanno una tabella loro.
    """
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))

    file = request.files.get('documento')
    if not file or not file.filename:
        flash('Nessun file selezionato.', 'warning')
        return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ESTENSIONI_DOCUMENTO:
        flash('Formato file non supportato.', 'danger')
        return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))

    tipo = request.form.get('tipo', 'altro')
    if tipo not in TIPI_DOCUMENTO:
        tipo = 'altro'

    uploads_dir, rel_prefix = upload_subdir('impianti', impianto['struttura_id'])
    filename = f"{int(time.time())}_{secure_filename(file.filename)}"
    filepath = os.path.join(uploads_dir, filename)
    file.save(filepath)

    execute(
        """INSERT INTO impianti_documenti
           (impianto_id, tipo, descrizione, data_documento,
            emittente_ragione_sociale, emittente_indirizzo, emittente_telefono,
            emittente_email, filename, filepath, filesize, uploaded_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (impianto_id, tipo,
         (request.form.get('descrizione') or '').strip() or None,
         (request.form.get('data_documento') or '').strip() or None,
         (request.form.get('emittente_ragione_sociale') or '').strip() or None,
         (request.form.get('emittente_indirizzo') or '').strip() or None,
         (request.form.get('emittente_telefono') or '').strip() or None,
         (request.form.get('emittente_email') or '').strip() or None,
         secure_filename(file.filename), f"{rel_prefix}/{filename}",
         os.path.getsize(filepath), g.user['id'])
    )
    log_attivita(g.user['id'], 'creazione', 'impianto_documento', impianto_id,
                 f"Documento {tipo} su {impianto['nome']}")
    flash('Documento caricato.', 'success')
    return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))


@impianti_bp.route('/documenti/<int:documento_id>')
@login_required
def scarica_documento(documento_id):
    """Scarica un documento. Il permesso passa dall'impianto, non dal file."""
    doc = query_one("SELECT * FROM impianti_documenti WHERE id = ?",
                    (documento_id,))
    if not doc or not impianto_accessibile(doc['impianto_id']):
        abort(404)
    from flask import current_app
    percorso = os.path.join(current_app.config['UPLOADS_PATH'], doc['filepath'])
    if not os.path.exists(percorso):
        flash('File non presente sul server.', 'danger')
        return redirect(url_for('impianti.dettaglio',
                                impianto_id=doc['impianto_id']))
    return send_file(percorso, as_attachment=True,
                     download_name=doc['filename'])


@impianti_bp.route('/documenti/<int:documento_id>/elimina', methods=['POST'])
@tecnico_o_admin_required
def elimina_documento(documento_id):
    """Elimina un documento e il file su disco."""
    doc = query_one("SELECT * FROM impianti_documenti WHERE id = ?",
                    (documento_id,))
    if not doc or not impianto_accessibile(doc['impianto_id']):
        abort(404)
    from flask import current_app
    percorso = os.path.join(current_app.config['UPLOADS_PATH'], doc['filepath'])
    if os.path.exists(percorso):
        try:
            os.remove(percorso)
        except OSError:
            # La riga sparisce comunque: un file rimasto sul disco e' meno
            # dannoso di un elenco che mostra un documento gia' revocato.
            pass
    execute("DELETE FROM impianti_documenti WHERE id = ?", (documento_id,))
    log_attivita(g.user['id'], 'eliminazione', 'impianto_documento',
                 doc['impianto_id'], f"Documento {doc['filename']}")
    flash('Documento eliminato.', 'success')
    return redirect(url_for('impianti.dettaglio',
                            impianto_id=doc['impianto_id']))


# ---------------------------------------------------------------------------
# Piano di manutenzione
# ---------------------------------------------------------------------------

def _valida_scadenza(form, impianto_id):
    """Valida una riga di piano. Restituisce (dati, errori).

    ``componente_id`` arriva dal form: senza verifica sarebbe possibile
    agganciare la scadenza a un componente di un impianto altrui (e quindi
    di un'altra struttura), che la vista prossime_scadenze_impianti
    ricongiunge senza scoping. Va risolto sull'impianto di destinazione,
    non accettato per fiducia.
    """
    errori = []
    nome = (form.get('nome') or '').strip()
    if not nome:
        errori.append('Il nome della verifica è obbligatorio.')
    prossima = (form.get('prossima_scadenza') or '').strip()
    if not prossima:
        errori.append('La data della prossima scadenza è obbligatoria.')

    periodicita = form.get('periodicita_mesi', type=int)
    if periodicita is not None and not (1 <= periodicita <= 600):
        errori.append('Periodicità non valida (1-600 mesi).')
    anticipo = form.get('giorni_anticipo', type=int)
    if anticipo is None:
        anticipo = 30
    if not (0 <= anticipo <= 365):
        errori.append('Giorni di anticipo non validi (0-365).')

    componente_id = form.get('componente_id', type=int)
    if componente_id is not None:
        riga_componente = query_one(
            "SELECT id FROM impianti_componenti WHERE id = ? AND impianto_id = ?",
            (componente_id, impianto_id))
        if not riga_componente:
            errori.append('Componente non valido.')
            componente_id = None

    return {
        'nome': nome,
        'riferimento_normativo':
            (form.get('riferimento_normativo') or '').strip() or None,
        # Vuoto significa una tantum: eseguita una volta, la riga si chiude.
        'periodicita_mesi': periodicita or None,
        'prossima_scadenza': prossima,
        'giorni_anticipo': anticipo,
        'email_extra': (form.get('email_extra') or '').strip() or None,
        'avvisa_manutentore': 1 if form.get('avvisa_manutentore') else 0,
        'componente_id': componente_id,
        'note': (form.get('note') or '').strip() or None,
    }, errori


@impianti_bp.route('/<int:impianto_id>/piano/nuova', methods=['POST'])
@tecnico_o_admin_required
def nuova_scadenza(impianto_id):
    """Aggiunge una riga al piano di manutenzione/verifica."""
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))
    dati, errori = _valida_scadenza(request.form, impianto_id)
    if errori:
        for e in errori:
            flash(e, 'danger')
        return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))
    execute(
        """INSERT INTO impianti_scadenze
           (impianto_id, componente_id, nome, riferimento_normativo,
            periodicita_mesi, prossima_scadenza, giorni_anticipo, email_extra,
            avvisa_manutentore, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (impianto_id, dati['componente_id'], dati['nome'],
         dati['riferimento_normativo'], dati['periodicita_mesi'],
         dati['prossima_scadenza'], dati['giorni_anticipo'],
         dati['email_extra'], dati['avvisa_manutentore'], dati['note'])
    )
    log_attivita(g.user['id'], 'creazione', 'impianto_scadenza', impianto_id,
                 f"Piano: {dati['nome']} su {impianto['nome']}")
    flash('Voce di piano aggiunta.', 'success')
    return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))


@impianti_bp.route('/piano/<int:scadenza_id>/modifica', methods=['POST'])
@tecnico_o_admin_required
def modifica_scadenza(scadenza_id):
    """Modifica una riga di piano."""
    riga = query_one("SELECT * FROM impianti_scadenze WHERE id = ?",
                     (scadenza_id,))
    if not riga or not impianto_accessibile(riga['impianto_id']):
        abort(404)
    dati, errori = _valida_scadenza(request.form, riga['impianto_id'])
    if errori:
        for e in errori:
            flash(e, 'danger')
    else:
        execute(
            """UPDATE impianti_scadenze SET componente_id = ?, nome = ?,
                   riferimento_normativo = ?, periodicita_mesi = ?,
                   prossima_scadenza = ?, giorni_anticipo = ?, email_extra = ?,
                   avvisa_manutentore = ?, note = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (dati['componente_id'], dati['nome'], dati['riferimento_normativo'],
             dati['periodicita_mesi'], dati['prossima_scadenza'],
             dati['giorni_anticipo'], dati['email_extra'],
             dati['avvisa_manutentore'], dati['note'], scadenza_id)
        )
        log_attivita(g.user['id'], 'modifica', 'impianto_scadenza',
                     riga['impianto_id'], f"Piano: {dati['nome']}")
        flash('Voce di piano aggiornata.', 'success')
    return redirect(url_for('impianti.dettaglio',
                            impianto_id=riga['impianto_id']))


@impianti_bp.route('/piano/<int:scadenza_id>/sospendi', methods=['POST'])
@tecnico_o_admin_required
def sospendi_scadenza(scadenza_id):
    """Sospende o riattiva una riga di piano.

    Sospendere, non cancellare: gli interventi gia' registrati continuano a
    puntarla, e riattivarla ricostruisce il ciclo senza reinserire nulla.
    """
    riga = query_one("SELECT * FROM impianti_scadenze WHERE id = ?",
                     (scadenza_id,))
    if not riga or not impianto_accessibile(riga['impianto_id']):
        abort(404)
    nuovo = 0 if riga['attiva'] else 1
    execute("UPDATE impianti_scadenze SET attiva = ?,"
            " updated_at = datetime('now') WHERE id = ?", (nuovo, scadenza_id))
    log_attivita(g.user['id'], 'modifica', 'impianto_scadenza',
                 riga['impianto_id'],
                 f"Piano: {riga['nome']} {'riattivata' if nuovo else 'sospesa'}")
    flash('Voce riattivata.' if nuovo else 'Voce sospesa.', 'success')
    return redirect(url_for('impianti.dettaglio',
                            impianto_id=riga['impianto_id']))


@impianti_bp.route('/<int:impianto_id>/piano/catalogo', methods=['POST'])
@tecnico_o_admin_required
def piano_catalogo(impianto_id):
    """Aggiunge al piano voci di catalogo non ancora presenti."""
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))
    presenti = [r['nome'] for r in query_all(
        "SELECT nome FROM impianti_scadenze WHERE impianto_id = ?",
        (impianto_id,))]
    mancanti = {v['nome'] for v in voci_mancanti(impianto['tipo'], presenti)}
    scelti = [n for n in request.form.getlist('catalogo') if n in mancanti]
    creati = impianti_service.applica_catalogo(
        impianto_id, impianto['tipo'], scelti, time.strftime('%Y-%m-%d'))
    log_attivita(g.user['id'], 'creazione', 'impianto_scadenza', impianto_id,
                 f"Catalogo: {creati} voci su {impianto['nome']}")
    flash(f'Aggiunte {creati} voci di piano.', 'success')
    return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))


# ---------------------------------------------------------------------------
# Interventi
# ---------------------------------------------------------------------------

@impianti_bp.route('/<int:impianto_id>/interventi/nuovo', methods=['POST'])
@login_required
def nuovo_intervento(impianto_id):
    """Registra un intervento; il servizio decide se il piano avanza."""
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))

    data_intervento = (request.form.get('data_intervento') or '').strip()
    if not data_intervento:
        flash('La data dell\'intervento è obbligatoria.', 'danger')
        return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))

    tipo = request.form.get('tipo', 'ordinaria')
    if tipo not in ('verifica', 'ordinaria', 'straordinaria', 'riparazione'):
        tipo = 'ordinaria'
    esito = request.form.get('esito') or None
    if esito not in ('positivo', 'negativo', 'con_riserva', None):
        esito = None

    # La scadenza indicata deve appartenere a questo impianto: senza il
    # controllo, un id qualunque farebbe avanzare il piano di un'altra
    # struttura.
    scadenza_id = request.form.get('scadenza_id', type=int) or None
    if scadenza_id and not query_one(
            "SELECT 1 FROM impianti_scadenze WHERE id = ? AND impianto_id = ?",
            (scadenza_id, impianto_id)):
        scadenza_id = None

    # Stesso discorso per il componente: deve essere di questo impianto.
    componente_id = request.form.get('componente_id', type=int) or None
    if componente_id and not query_one(
            "SELECT 1 FROM impianti_componenti WHERE id = ? AND impianto_id = ?",
            (componente_id, impianto_id)):
        componente_id = None

    # E per il manutentore: deve essere della stessa struttura dell'impianto.
    manutentore_id = request.form.get('manutentore_id', type=int) or None
    if manutentore_id and not query_one(
            "SELECT 1 FROM manutentori WHERE id = ? AND struttura_id = ?",
            (manutentore_id, impianto['struttura_id'])):
        manutentore_id = None

    verbale_path = None
    file = request.files.get('verbale')
    if file and file.filename:
        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        if ext not in ESTENSIONI_DOCUMENTO:
            flash('Formato del verbale non supportato.', 'danger')
            return redirect(url_for('impianti.dettaglio',
                                    impianto_id=impianto_id))
        uploads_dir, rel_prefix = upload_subdir('impianti',
                                                impianto['struttura_id'])
        filename = f"{int(time.time())}_{secure_filename(file.filename)}"
        file.save(os.path.join(uploads_dir, filename))
        verbale_path = f"{rel_prefix}/{filename}"

    _, nuova = impianti_service.registra_intervento(impianto_id, {
        'scadenza_id': scadenza_id,
        'componente_id': componente_id,
        'tipo': tipo,
        'data_intervento': data_intervento,
        'esito': esito,
        'manutentore_id': manutentore_id,
        'tecnico_ditta': (request.form.get('tecnico_ditta') or '').strip() or None,
        'descrizione': (request.form.get('descrizione') or '').strip() or None,
        'costo': request.form.get('costo', type=float),
        'verbale_path': verbale_path,
        'note': (request.form.get('note') or '').strip() or None,
    }, utente_id=g.user['id'])

    log_attivita(g.user['id'], 'creazione', 'impianto_intervento', impianto_id,
                 f"Intervento {tipo} del {data_intervento} su {impianto['nome']}")
    if nuova:
        flash(f'Intervento registrato. Prossima scadenza: {nuova}.', 'success')
    elif esito == 'negativo':
        flash('Intervento registrato con esito negativo: la scadenza resta '
              'aperta.', 'warning')
    else:
        flash('Intervento registrato.', 'success')
    return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))


@impianti_bp.route('/interventi/<int:intervento_id>/verbale')
@login_required
def scarica_verbale(intervento_id):
    """Scarica il verbale di un intervento."""
    intervento = query_one("SELECT * FROM impianti_interventi WHERE id = ?",
                           (intervento_id,))
    if (not intervento or not intervento['verbale_path']
            or not impianto_accessibile(intervento['impianto_id'])):
        abort(404)
    from flask import current_app
    percorso = os.path.join(current_app.config['UPLOADS_PATH'],
                            intervento['verbale_path'])
    if not os.path.exists(percorso):
        flash('Verbale non presente sul server.', 'danger')
        return redirect(url_for('impianti.dettaglio',
                                impianto_id=intervento['impianto_id']))
    return send_file(percorso, as_attachment=True,
                     download_name=os.path.basename(intervento['verbale_path']))


# ---------------------------------------------------------------------------
# Manutentori
# ---------------------------------------------------------------------------

def _manutentore_in_scope(manutentore_id):
    """La riga del manutentore, solo se della struttura attiva."""
    struttura_id = getattr(g, 'struttura_id', None)
    if not struttura_id:
        return None
    return query_one(
        "SELECT * FROM manutentori WHERE id = ? AND struttura_id = ?",
        (manutentore_id, struttura_id))


def _dati_manutentore(form):
    """Campi del manutentore. Restituisce (dati, errori)."""
    ragione = (form.get('ragione_sociale') or '').strip()
    errori = [] if ragione else ['La ragione sociale è obbligatoria.']
    return {
        'ragione_sociale': ragione,
        'indirizzo': (form.get('indirizzo') or '').strip() or None,
        'telefono': (form.get('telefono') or '').strip() or None,
        'email': (form.get('email') or '').strip() or None,
        'partita_iva': (form.get('partita_iva') or '').strip() or None,
        'note': (form.get('note') or '').strip() or None,
    }, errori


@impianti_bp.route('/manutentori')
@tecnico_o_admin_required
def manutentori():
    """Anagrafica delle ditte manutentrici della struttura."""
    elenco = query_all(
        "SELECT * FROM manutentori WHERE struttura_id = ?"
        " ORDER BY attivo DESC, ragione_sociale",
        (getattr(g, 'struttura_id', None),))
    return render_template('impianti/manutentori.html', manutentori=elenco)


@impianti_bp.route('/manutentori/nuovo', methods=['POST'])
@tecnico_o_admin_required
def nuovo_manutentore():
    """Crea un manutentore nella struttura attiva."""
    struttura_id = getattr(g, 'struttura_id', None)
    if not struttura_id:
        flash('Nessuna struttura attiva.', 'danger')
        return redirect(url_for('impianti.manutentori'))
    dati, errori = _dati_manutentore(request.form)
    if errori:
        for e in errori:
            flash(e, 'danger')
        return redirect(url_for('impianti.manutentori'))
    if query_one("SELECT 1 FROM manutentori WHERE struttura_id = ?"
                 " AND ragione_sociale = ?",
                 (struttura_id, dati['ragione_sociale'])):
        flash('Manutentore già presente.', 'warning')
        return redirect(url_for('impianti.manutentori'))
    mid = execute(
        """INSERT INTO manutentori (struttura_id, ragione_sociale, indirizzo,
               telefono, email, partita_iva, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (struttura_id, dati['ragione_sociale'], dati['indirizzo'],
         dati['telefono'], dati['email'], dati['partita_iva'], dati['note'])
    ).lastrowid
    log_attivita(g.user['id'], 'creazione', 'manutentore', mid,
                 dati['ragione_sociale'])
    flash('Manutentore aggiunto.', 'success')
    return redirect(url_for('impianti.manutentori'))


@impianti_bp.route('/manutentori/<int:manutentore_id>/modifica',
                   methods=['POST'])
@tecnico_o_admin_required
def modifica_manutentore(manutentore_id):
    """Modifica i dati di un manutentore."""
    if not _manutentore_in_scope(manutentore_id):
        abort(404)
    dati, errori = _dati_manutentore(request.form)
    if errori:
        for e in errori:
            flash(e, 'danger')
        return redirect(url_for('impianti.manutentori'))
    execute(
        """UPDATE manutentori SET ragione_sociale = ?, indirizzo = ?,
               telefono = ?, email = ?, partita_iva = ?, note = ?,
               attivo = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (dati['ragione_sociale'], dati['indirizzo'], dati['telefono'],
         dati['email'], dati['partita_iva'], dati['note'],
         1 if request.form.get('attivo') else 0, manutentore_id)
    )
    log_attivita(g.user['id'], 'modifica', 'manutentore', manutentore_id,
                 dati['ragione_sociale'])
    flash('Manutentore aggiornato.', 'success')
    return redirect(url_for('impianti.manutentori'))


@impianti_bp.route('/manutentori/<int:manutentore_id>/elimina',
                   methods=['POST'])
@tecnico_o_admin_required
def elimina_manutentore(manutentore_id):
    """Elimina un manutentore. Impianti e interventi restano, senza il
    riferimento (ON DELETE SET NULL)."""
    riga = _manutentore_in_scope(manutentore_id)
    if not riga:
        abort(404)
    execute("DELETE FROM manutentori WHERE id = ?", (manutentore_id,))
    log_attivita(g.user['id'], 'eliminazione', 'manutentore', manutentore_id,
                 riga['ragione_sociale'])
    flash('Manutentore eliminato.', 'success')
    return redirect(url_for('impianti.manutentori'))
