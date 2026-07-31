"""
MedInventory - Apparecchi (Medical Devices) Blueprint
Full CRUD: list with filters, create, edit, detail, soft-delete, photo/document upload.
"""

import io
import ipaddress
import os
import re
import time
from datetime import datetime

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, g, current_app, send_from_directory, send_file, abort
)
from werkzeug.utils import secure_filename

from auth import login_required
from models import (query_one, query_all, execute, log_attivita, upload_subdir,
                    apparecchio_accessibile, filtro_divisione, get_db)

apparecchi_bp = Blueprint('apparecchi', __name__)

ALLOWED_IMAGE_EXT = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
ALLOWED_DOC_EXT = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'jpg', 'jpeg', 'png'}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_apparecchio(form_data, edit_id=None):
    """Validate form data for apparecchio. Returns (cleaned_data, errors)."""
    errors = {}
    data = {}

    # Required fields
    data['matricola'] = form_data.get('matricola', '').strip()
    if not data['matricola']:
        errors['matricola'] = 'La matricola è obbligatoria.'
    else:
        # Check unique on (struttura_id, modello, matricola)
        modello_check = form_data.get('modello', '').strip()
        struttura_id_check = getattr(g, 'struttura_id', None)
        existing = query_one(
            """SELECT id FROM apparecchi
               WHERE matricola = ? AND modello = ? AND id != ?
               AND (struttura_id = ? OR (struttura_id IS NULL AND ? IS NULL))""",
            (data['matricola'], modello_check, edit_id or 0,
             struttura_id_check, struttura_id_check)
        )
        if existing:
            errors['matricola'] = 'Questa combinazione modello + matricola è già presente in questa struttura.'

    data['marca'] = form_data.get('marca', '').strip()
    if not data['marca']:
        errors['marca'] = 'La marca è obbligatoria.'

    data['modello'] = form_data.get('modello', '').strip()
    if not data['modello']:
        errors['modello'] = 'Il modello è obbligatorio.'

    data['divisione_id'] = form_data.get('divisione_id', '')
    if not data['divisione_id']:
        errors['divisione_id'] = 'La divisione è obbligatoria.'
    else:
        try:
            data['divisione_id'] = int(data['divisione_id'])
            # Verifica che la divisione appartenga alla struttura corrente
            struttura_id = getattr(g, 'struttura_id', None)
            if struttura_id is not None:
                div_ok = query_one(
                    "SELECT id FROM divisioni WHERE id = ? AND struttura_id = ?",
                    (data['divisione_id'], struttura_id)
                )
                if not div_ok:
                    errors['divisione_id'] = 'Divisione non valida.'
            # Per i ruoli non amministrativi la divisione dev'essere fra quelle
            # assegnate: altrimenti un utente potrebbe spostare o creare
            # apparecchi in reparti a cui non ha accesso.
            if g.user['ruolo'] not in ('admin', 'superadmin', 'tecnico'):
                if data['divisione_id'] not in [d['id'] for d in g.divisioni]:
                    errors['divisione_id'] = 'Divisione non accessibile.'
        except ValueError:
            errors['divisione_id'] = 'Divisione non valida.'

    # Optional fields
    data['descrizione'] = form_data.get('descrizione', '').strip() or None
    data['numero_inventario'] = form_data.get('numero_inventario', '').strip() or None

    anno = form_data.get('anno_fabbricazione', '').strip()
    if anno:
        try:
            data['anno_fabbricazione'] = int(anno)
            if data['anno_fabbricazione'] < 1900 or data['anno_fabbricazione'] > datetime.now().year:
                errors['anno_fabbricazione'] = f'Anno non valido (1900-{datetime.now().year}).'
        except ValueError:
            errors['anno_fabbricazione'] = 'Anno non valido.'
    else:
        data['anno_fabbricazione'] = None

    classificazione = form_data.get('classificazione', '').strip()
    data['classificazione'] = classificazione if classificazione in ('I', 'IIa', 'IIb', 'III') else None

    data['ubicazione'] = form_data.get('ubicazione', '').strip() or None
    data['stato'] = form_data.get('stato', 'funzionante')
    if data['stato'] not in ('funzionante', 'in_manutenzione', 'dismesso', 'da_sostituire'):
        data['stato'] = 'funzionante'

    # Network fields
    data['connesso_rete'] = 1 if form_data.get('connesso_rete') else 0
    data['ip_address'] = form_data.get('ip_address', '').strip() or None
    data['mac_address'] = form_data.get('mac_address', '').strip() or None
    data['hostname'] = form_data.get('hostname', '').strip() or None

    porta = form_data.get('porta', '').strip()
    if porta:
        try:
            data['porta'] = int(porta)
            if not (1 <= data['porta'] <= 65535):
                errors['porta'] = 'Porta non valida (1-65535).'
        except ValueError:
            errors['porta'] = 'Porta non valida.'
    else:
        data['porta'] = None

    data['protocollo'] = form_data.get('protocollo', '').strip() or None
    data['url_interfaccia'] = form_data.get('url_interfaccia', '').strip() or None

    # IP validation if connected
    if data['connesso_rete'] and data['ip_address']:
        try:
            ipaddress.ip_address(data['ip_address'])
        except ValueError:
            errors['ip_address'] = 'Formato IP non valido (es. 192.168.1.100).'

    # Supplier fields
    data['fornitore'] = form_data.get('fornitore', '').strip() or None
    data['codice_fornitore'] = form_data.get('codice_fornitore', '').strip() or None
    data['garanzia_scadenza'] = form_data.get('garanzia_scadenza', '').strip() or None
    data['contratto_manutenzione'] = form_data.get('contratto_manutenzione', '').strip() or None
    data['note'] = form_data.get('note', '').strip() or None
    data['soggetto_verifica'] = 1 if form_data.get('soggetto_verifica') else 0

    return data, errors


# ---------------------------------------------------------------------------
# Accessori helpers
# ---------------------------------------------------------------------------

def _save_accessori(apparecchio_id, form_data, user_id):
    """Delete all existing accessories and re-insert from form data."""
    execute("DELETE FROM accessori WHERE apparecchio_id = ?", (apparecchio_id,))
    descrizioni = form_data.getlist('acc_descrizione[]')
    produttori  = form_data.getlist('acc_produttore[]')
    modelli     = form_data.getlist('acc_modello[]')
    matricole   = form_data.getlist('acc_matricola[]')
    for i, descrizione in enumerate(descrizioni):
        descrizione = descrizione.strip()
        if not descrizione:
            continue
        execute(
            """INSERT INTO accessori
               (apparecchio_id, descrizione, produttore, modello, matricola, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (apparecchio_id,
             descrizione,
             (produttori[i].strip() if i < len(produttori) else '') or None,
             (modelli[i].strip()    if i < len(modelli)    else '') or None,
             (matricole[i].strip()  if i < len(matricole)  else '') or None,
             user_id)
        )


def _parse_accessori_from_form(form_data):
    """Ricostruisce la lista accessori dal form (usato sul re-render per errori di validazione)."""
    result = []
    descrizioni = form_data.getlist('acc_descrizione[]')
    produttori  = form_data.getlist('acc_produttore[]')
    modelli     = form_data.getlist('acc_modello[]')
    matricole   = form_data.getlist('acc_matricola[]')
    for i, d in enumerate(descrizioni):
        result.append({
            'descrizione': d,
            'produttore':  produttori[i] if i < len(produttori) else '',
            'modello':     modelli[i]    if i < len(modelli)    else '',
            'matricola':   matricole[i]  if i < len(matricole)  else '',
        })
    return result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@apparecchi_bp.route('/apparecchi')
@login_required
def lista():
    """List apparecchi with filters and search."""
    div_clause, div_params = filtro_divisione()

    # Gather filters
    search = request.args.get('search', '').strip()
    stato = request.args.get('stato', '')
    marca = request.args.get('marca', '')
    classificazione = request.args.get('classificazione', '')
    ubicazione = request.args.get('ubicazione', '').strip()
    connesso_rete = request.args.get('connesso_rete', '')
    verifica_el = request.args.get('verifica_el', '')
    mostra_dismessi = request.args.get('mostra_dismessi', '0')
    page = request.args.get('page', 1, type=int)
    per_page = 25

    # Base filter: hide dismissed unless toggle is on or stato filter explicitly selects them
    if stato and stato != 'tutti':
        where_clauses = [f"a.stato = ?"]
        params = [stato]
    elif mostra_dismessi == '1':
        where_clauses = ["1=1"]
        params = []
    else:
        where_clauses = ["a.stato != 'dismesso'"]
        params = []

    if search:
        where_clauses.append(
            "(a.matricola LIKE ? OR a.marca LIKE ? OR a.modello LIKE ? "
            "OR a.descrizione LIKE ? OR a.ubicazione LIKE ? OR a.fornitore LIKE ?)"
        )
        s = f'%{search}%'
        params.extend([s, s, s, s, s, s])

    if marca:
        where_clauses.append("a.marca = ?")
        params.append(marca)

    if classificazione:
        where_clauses.append("a.classificazione = ?")
        params.append(classificazione)

    if ubicazione:
        where_clauses.append("a.ubicazione LIKE ?")
        params.append(f'%{ubicazione}%')

    if connesso_rete:
        where_clauses.append("a.connesso_rete = 1")

    if verifica_el == 'scaduta':
        where_clauses.append(
            """a.soggetto_verifica = 1 AND EXISTS (
                SELECT 1 FROM verifiche v WHERE v.apparecchio_id = a.id
                AND v.prossima_scadenza < date('now')
                AND v.id = (SELECT id FROM verifiche WHERE apparecchio_id = a.id
                            ORDER BY data_verifica DESC, id DESC LIMIT 1))"""
        )
    elif verifica_el == 'in_scadenza':
        where_clauses.append(
            """a.soggetto_verifica = 1 AND EXISTS (
                SELECT 1 FROM verifiche v WHERE v.apparecchio_id = a.id
                AND v.prossima_scadenza >= date('now')
                AND v.prossima_scadenza <= date('now', '+30 days')
                AND v.id = (SELECT id FROM verifiche WHERE apparecchio_id = a.id
                            ORDER BY data_verifica DESC, id DESC LIMIT 1))"""
        )
    elif verifica_el == 'assente':
        where_clauses.append(
            "a.soggetto_verifica = 1 AND NOT EXISTS "
            "(SELECT 1 FROM verifiche WHERE apparecchio_id = a.id)"
        )
    elif verifica_el == 'esente':
        where_clauses.append("a.soggetto_verifica = 0")

    where_sql = " AND ".join(where_clauses)

    # Count total
    count_sql = f"""
        SELECT COUNT(*) as cnt FROM apparecchi a
        WHERE {where_sql} {div_clause}
    """
    total = query_one(count_sql, params + div_params)['cnt']

    # Paginate
    offset = (page - 1) * per_page
    total_pages = max(1, (total + per_page - 1) // per_page)

    # Query data (con ultima verifica elettrica per riga)
    data_sql = f"""
        SELECT a.*, d.nome as divisione_nome, d.colore as divisione_colore,
               uv.ultima_verifica_data, uv.ultima_verifica_scadenza,
               CASE
                   WHEN a.soggetto_verifica = 0 THEN 'esente'
                   WHEN uv.ultima_verifica_data IS NULL THEN 'assente'
                   WHEN uv.ultima_verifica_scadenza IS NULL
                        OR uv.ultima_verifica_scadenza >= date('now') THEN 'ok'
                   ELSE 'scaduta'
               END as stato_verifica_el
        FROM apparecchi a
        LEFT JOIN divisioni d ON a.divisione_id = d.id
        LEFT JOIN (
            SELECT v1.apparecchio_id,
                   v1.data_verifica as ultima_verifica_data,
                   v1.prossima_scadenza as ultima_verifica_scadenza
            FROM verifiche v1
            WHERE v1.id = (
                SELECT id FROM verifiche
                WHERE apparecchio_id = v1.apparecchio_id
                ORDER BY data_verifica DESC, id DESC LIMIT 1
            )
        ) uv ON uv.apparecchio_id = a.id
        WHERE {where_sql} {div_clause}
        ORDER BY a.marca, a.modello
        LIMIT ? OFFSET ?
    """
    apparecchi = query_all(data_sql, params + div_params + [per_page, offset])

    # Get distinct values for filter dropdowns
    marche_stato_filter = "" if mostra_dismessi == '1' else "AND a.stato != 'dismesso'"
    marche = query_all(
        f"SELECT DISTINCT marca FROM apparecchi a WHERE 1=1 {marche_stato_filter} {div_clause} ORDER BY marca",
        div_params
    )

    context = {
        'apparecchi': apparecchi,
        'marche': marche,
        'filtri': {
            'search': search, 'stato': stato, 'marca': marca,
            'classificazione': classificazione, 'ubicazione': ubicazione,
            'connesso_rete': connesso_rete, 'verifica_el': verifica_el,
            'mostra_dismessi': mostra_dismessi,
        },
        'pagination': {
            'page': page, 'per_page': per_page, 'total': total,
            'total_pages': total_pages,
        },
    }

    # HTMX partial response
    if request.args.get('partial'):
        return render_template('partials/apparecchi_table.html', **context)

    return render_template('apparecchi/lista.html', **context)


@apparecchi_bp.route('/apparecchi/duplicati')
@login_required
def duplicati():
    """Coppie di schede che potrebbero descrivere lo stesso apparecchio."""
    from fusione_service import candidati_duplicati, CRITERI

    if g.user['ruolo'] not in ('admin', 'superadmin', 'tecnico'):
        flash('Non autorizzato.', 'danger')
        return redirect(url_for('apparecchi.lista'))

    # Volutamente NON filtro_divisione(): quella clausola onora
    # g.divisione_attiva, e per un admin/tecnico che non ha ancora scelto
    # "Tutte le divisioni" g.divisione_attiva e' una divisione SPECIFICA
    # (auth.py, ramo "Default to first accessible division") - il
    # confronto vedrebbe solo gli apparecchi di quel reparto e, nel caso
    # comune di un duplicato fra due reparti diversi, l'elenco direbbe che
    # non ci sono duplicati quando invece ce ne sono. I tre ruoli ammessi
    # qui sopra hanno comunque accesso a tutta la struttura -
    # apparecchio_accessibile salta il controllo di divisione per loro -
    # quindi confrontare l'intera struttura non allarga la visibilita', e'
    # l'ambito giusto per questa domanda. Il superadmin che non impersona
    # nessuna struttura non vede nulla, come nel resto del progetto.
    struttura_id = getattr(g, 'struttura_id', None)
    if struttura_id:
        div_clause, div_params = "AND a.struttura_id = ?", [struttura_id]
    else:
        div_clause, div_params = "AND 1=0", []
    righe = query_all(
        f"""SELECT a.id, a.matricola, a.marca, a.modello, a.ubicazione, a.descrizione,
                   (SELECT COUNT(*) FROM manutenzioni m WHERE m.apparecchio_id = a.id) AS n_manut,
                   (SELECT COUNT(*) FROM verifiche v WHERE v.apparecchio_id = a.id) AS n_verif
            FROM apparecchi a
            WHERE a.stato != 'dismesso' {div_clause}
            ORDER BY a.marca, a.modello, a.matricola""",
        div_params)

    coppie = candidati_duplicati(righe)
    return render_template('apparecchi/duplicati.html', coppie=coppie, criteri=CRITERI)


def _due_schede_fondibili(id, altro_id):
    """Le due schede, se l'utente puo' agire su entrambe. Altrimenti (None, None).

    apparecchio_accessibile controlla struttura E divisione: e' l'unico modo
    accettabile di raggiungere un apparecchio per id.
    """
    if g.user['ruolo'] not in ('admin', 'superadmin', 'tecnico'):
        return None, None
    return apparecchio_accessibile(id), apparecchio_accessibile(altro_id)


@apparecchi_bp.route('/apparecchi/<int:id>/fondi/<int:altro_id>')
@login_required
def fondi(id, altro_id):
    """Confronto delle due schede prima della fusione."""
    from fusione_service import CAMPI_FONDIBILI, valori_predefiniti

    uno, due = _due_schede_fondibili(id, altro_id)
    if not uno or not due:
        flash('Apparecchio non trovato o non autorizzato.', 'danger')
        return redirect(url_for('apparecchi.lista'))

    differenze = [
        {'campo': c, 'a': uno.get(c), 'b': due.get(c)}
        for c in CAMPI_FONDIBILI if uno.get(c) != due.get(c)
    ]
    interventi = query_all(
        """SELECT 'manutenzione' AS tipo, id, data_intervento AS data, tipo AS sottotipo,
                  verbale_path AS allegato, apparecchio_id
           FROM manutenzioni WHERE apparecchio_id IN (?, ?)
           UNION ALL
           SELECT 'verifica', id, data_verifica, esito, documento_path, apparecchio_id
           FROM verifiche WHERE apparecchio_id IN (?, ?)
           ORDER BY data DESC""",
        (id, altro_id, id, altro_id))

    return render_template('apparecchi/fondi.html', uno=uno, due=due,
                           differenze=differenze, interventi=interventi,
                           predefiniti=valori_predefiniti(uno, due))


@apparecchi_bp.route('/apparecchi/<int:id>/fondi/<int:altro_id>', methods=['POST'])
@login_required
def esegui_fusione(id, altro_id):
    """Esegue la fusione. Tutto in una transazione sola."""
    from fusione_service import (fondi_apparecchi, CAMPI_FONDIBILI,
                                 FusioneCollisioneError, FusioneRifiutataError)

    uno, due = _due_schede_fondibili(id, altro_id)
    if not uno or not due:
        flash('Apparecchio non trovato o non autorizzato.', 'danger')
        return redirect(url_for('apparecchi.lista'))

    try:
        id_principale = int(request.form.get('principale', 0))
    except (TypeError, ValueError):
        id_principale = 0
    if id_principale not in (id, altro_id):
        flash('Scegli quale scheda deve sopravvivere.', 'warning')
        return redirect(url_for('apparecchi.fondi', id=id, altro_id=altro_id))
    id_scartato = altro_id if id_principale == id else id

    valori = {}
    for campo in CAMPI_FONDIBILI:
        if f'campo_{campo}' in request.form:
            valore = request.form.get(f'campo_{campo}')
            valori[campo] = valore if valore != '' else None

    interventi_scartati = []
    for chiave in request.form.getlist('scarta'):
        tipo, _sep, id_int = chiave.partition(':')
        if id_int.isdigit():
            interventi_scartati.append((tipo, int(id_int)))

    db = get_db()
    try:
        esito = fondi_apparecchi(db, id_principale, id_scartato, valori,
                                 interventi_scartati)

        # Il registro e' l'unica rete che resta dopo la fusione: la scheda
        # scartata non c'e' piu' nel database, quindi il messaggio deve
        # riversarla per intero, non solo nei campi che il form poteva far
        # scegliere. esito['scartato'] e' dict(scartato) preso PRIMA della
        # cancellazione (vedi fondi_apparecchi): itera su quello, non su
        # CAMPI_FONDIBILI, altrimenti colonne come divisione_id (NOT NULL
        # nello schema, e quindi indispensabile per ricreare la riga a mano)
        # sparirebbero dal registro senza che nessuna fusione futura le
        # rimpianga finche' non serve davvero ricostruire.
        scartato = esito['scartato']
        campi = ' '.join(
            f"{c}={v!r}" for c, v in scartato.items() if v not in (None, ''))

        # log_attivita chiama models.execute(), che fa il proprio commit
        # sulla stessa connessione (get_db() e' per-richiesta): chiamandola
        # QUI, prima del commit esplicito qui sotto, quel commit copre
        # insieme la fusione e la voce di registro. Se la scrittura del
        # registro fallisce, l'eccezione arriva al blocco "except Exception"
        # sotto e db.rollback() annulla anche la fusione: meglio nessuna
        # fusione che una fusione senza alcuna traccia di cosa conteneva la
        # scheda cancellata.
        log_attivita(
            g.user['id'], 'fusione', 'apparecchi', id_principale,
            f"Fusi \"{scartato['marca']} {scartato['modello']} {scartato['matricola']}\" "
            f"(id {id_scartato}) in id {id_principale}. Scheda scartata: {campi}. "
            f"Spostati: {esito['manutenzioni']} manutenzioni, {esito['verifiche']} verifiche, "
            f"{esito['documenti']} documenti, {esito['accessori']} accessori. "
            f"Scartati: {esito['interventi_scartati']} interventi. "
            f"Valori scelti: {', '.join(esito['valori_scelti']) or 'nessuno'}",
            request.remote_addr)

        # A questo punto la fusione e' gia' durevole: models.execute(),
        # chiamata da log_attivita qui sopra, ha gia' fatto commit() sulla
        # stessa connessione di richiesta (get_db() e' per-richiesta), e
        # quel commit copre tutto cio' che fondi_apparecchi ha scritto prima
        # di lei. Questa riga e' quindi un no-op in ogni fusione che arriva
        # fin qui - non e' lei a rendere durevole la fusione, oggi. Resta
        # comunque, e non va tolta credendola ridondante: e' la rete per il
        # giorno in cui la registrazione verra' spostata, sostituita da
        # un'implementazione che non passa piu' da models.execute(), o
        # tolta del tutto - a quel punto sarebbe l'unica cosa a reggere, e
        # la sua assenza sarebbe un bug silenzioso scoperto solo quando
        # qualcuno si accorge che le fusioni non sopravvivono al riavvio.
        # E l'ordine sopra - registrazione PRIMA, commit DOPO - e' voluto,
        # non un dettaglio: se log_attivita fallisce, l'eccezione arriva al
        # blocco "except Exception" sotto e db.rollback() annulla anche la
        # fusione. Una fusione senza la sua voce di registro non deve
        # risultare avvenuta: e' l'unica traccia che resta della scheda
        # cancellata, e la fusione non si annulla dall'interfaccia.
        db.commit()
    except FusioneCollisioneError as e:
        db.rollback()
        flash(str(e), 'danger')
        return redirect(url_for('apparecchi.fondi', id=id, altro_id=altro_id))
    except FusioneRifiutataError as e:
        db.rollback()
        flash(f'Fusione non eseguita: {e}', 'danger')
        return redirect(url_for('apparecchi.fondi', id=id, altro_id=altro_id))
    except Exception as e:
        db.rollback()
        current_app.logger.error(f'Fusione {id_principale}<-{id_scartato} fallita: {e}',
                                 exc_info=True)
        flash('Fusione fallita, nulla e\' stato modificato. Controlla il log.', 'danger')
        return redirect(url_for('apparecchi.fondi', id=id, altro_id=altro_id))

    flash(f"Schede fuse: {esito['manutenzioni']} manutenzioni e "
          f"{esito['verifiche']} verifiche trasferite.", 'success')
    return redirect(url_for('apparecchi.dettaglio', id=id_principale))


@apparecchi_bp.route('/apparecchi/nuovo', methods=['GET', 'POST'])
@login_required
def nuovo():
    """Create a new apparecchio."""
    if request.method == 'GET':
        return render_template('apparecchi/form.html',
                               apparecchio=None, errors={},
                               divisioni=g.divisioni, form_data={'soggetto_verifica': 1},
                               accessori=[])

    data, errors = _validate_apparecchio(request.form)

    if errors:
        return render_template('apparecchi/form.html',
                               apparecchio=None, errors=errors,
                               divisioni=g.divisioni, form_data=request.form,
                               accessori=_parse_accessori_from_form(request.form))

    # Deriva struttura_id dalla divisione selezionata (fonte autoritativa)
    div_row = query_one("SELECT struttura_id FROM divisioni WHERE id=?", (data['divisione_id'],))
    struttura_id = div_row['struttura_id'] if div_row else getattr(g, 'struttura_id', None)

    cursor = execute(
        """INSERT INTO apparecchi
           (divisione_id, struttura_id, descrizione, matricola, numero_inventario,
            marca, modello, anno_fabbricazione, classificazione,
            ubicazione, stato, soggetto_verifica, connesso_rete, ip_address, mac_address,
            hostname, porta, protocollo, url_interfaccia,
            fornitore, codice_fornitore, garanzia_scadenza,
            contratto_manutenzione, note, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data['divisione_id'], struttura_id, data['descrizione'], data['matricola'],
         data['numero_inventario'], data['marca'], data['modello'],
         data['anno_fabbricazione'], data['classificazione'],
         data['ubicazione'], data['stato'], data['soggetto_verifica'],
         data['connesso_rete'], data['ip_address'], data['mac_address'], data['hostname'],
         data['porta'], data['protocollo'], data['url_interfaccia'],
         data['fornitore'], data['codice_fornitore'], data['garanzia_scadenza'],
         data['contratto_manutenzione'], data['note'], g.user['id'])
    )

    _save_accessori(cursor.lastrowid, request.form, g.user['id'])

    log_attivita(g.user['id'], 'creazione', 'apparecchi', cursor.lastrowid,
                 f"Creato: {data['marca']} {data['modello']} ({data['matricola']})",
                 request.remote_addr)

    flash(f"Apparecchio {data['marca']} {data['modello']} creato con successo.", 'success')
    return redirect(url_for('apparecchi.dettaglio', id=cursor.lastrowid))


@apparecchi_bp.route('/apparecchi/<int:id>')
@login_required
def dettaglio(id):
    """Show apparecchio detail page."""
    # Cancello di accesso (struttura + divisione): se non si apre, l'apparecchio
    # non e' raggiungibile da qui. La query di visualizzazione sotto non deve
    # ripetere il filtro di struttura: apparecchio_accessibile e' l'unico posto
    # che decide.
    if not apparecchio_accessibile(id):
        flash('Apparecchio non trovato.', 'danger')
        return redirect(url_for('apparecchi.lista'))

    apparecchio = query_one(
        """SELECT a.*, d.nome as divisione_nome, d.colore as divisione_colore,
                  u1.nome || ' ' || u1.cognome as creato_da,
                  u2.nome || ' ' || u2.cognome as modificato_da
           FROM apparecchi a
           LEFT JOIN divisioni d ON a.divisione_id = d.id
           LEFT JOIN utenti u1 ON a.created_by = u1.id
           LEFT JOIN utenti u2 ON a.updated_by = u2.id
           WHERE a.id = ?""",
        (id,)
    )

    # Get maintenance history
    manutenzioni = query_all(
        """SELECT m.*, u.nome || ' ' || u.cognome as creato_da_nome
           FROM manutenzioni m
           LEFT JOIN utenti u ON m.created_by = u.id
           WHERE m.apparecchio_id = ?
           ORDER BY m.data_intervento DESC
           LIMIT 20""",
        (id,)
    )

    # Get verifiche history
    verifiche = query_all(
        """SELECT v.*, u.nome || ' ' || u.cognome as creato_da_nome
           FROM verifiche v
           LEFT JOIN utenti u ON v.created_by = u.id
           WHERE v.apparecchio_id = ?
           ORDER BY v.data_verifica DESC
           LIMIT 20""",
        (id,)
    )

    # Get accessories
    accessori = query_all(
        "SELECT * FROM accessori WHERE apparecchio_id = ? ORDER BY id",
        (id,)
    )

    # Get documents
    documenti = query_all(
        "SELECT * FROM documenti WHERE apparecchio_id = ? ORDER BY uploaded_at DESC",
        (id,)
    )

    # Next deadline
    prossima_scadenza = query_one(
        """SELECT * FROM prossime_scadenze WHERE apparecchio_id = ?
           ORDER BY prossima_scadenza ASC LIMIT 1""",
        (id,)
    )

    # Get recent activity log for this apparecchio
    attivita_recente = query_all(
        """SELECT la.*, u.nome || ' ' || u.cognome as utente_nome
           FROM log_attivita la
           LEFT JOIN utenti u ON la.utente_id = u.id
           WHERE (la.entita = 'apparecchi' AND la.entita_id = ?)
              OR (la.entita = 'manutenzioni' AND la.entita_id IN
                  (SELECT id FROM manutenzioni WHERE apparecchio_id = ?))
              OR (la.entita = 'verifiche' AND la.entita_id IN
                  (SELECT id FROM verifiche WHERE apparecchio_id = ?))
           ORDER BY la.created_at DESC
           LIMIT 15""",
        (id, id, id)
    )

    # Calcola stato verifica elettrica
    from datetime import date as _date
    ultima_verifica = verifiche[0] if verifiche else None
    if not apparecchio['soggetto_verifica']:
        stato_verifica_el = 'esente'
    elif not ultima_verifica:
        stato_verifica_el = 'assente'
    elif not ultima_verifica.get('prossima_scadenza'):
        stato_verifica_el = 'ok'
    elif ultima_verifica['prossima_scadenza'] >= _date.today().isoformat():
        stato_verifica_el = 'ok'
    else:
        stato_verifica_el = 'scaduta'

    return render_template('apparecchi/dettaglio.html',
                           apparecchio=apparecchio,
                           manutenzioni=manutenzioni,
                           verifiche=verifiche,
                           accessori=accessori,
                           documenti=documenti,
                           prossima_scadenza=prossima_scadenza,
                           stato_verifica_el=stato_verifica_el,
                           ultima_verifica=ultima_verifica,
                           attivita_recente=attivita_recente)


@apparecchi_bp.route('/apparecchi/<int:id>/qr')
@login_required
def qr_code(id):
    """Genera e restituisce il QR code PNG per l'apparecchio."""
    import qrcode

    apparecchio = apparecchio_accessibile(id)
    if not apparecchio:
        abort(404)

    # URL assoluto alla scheda apparecchio
    url = request.host_url.rstrip('/') + url_for('apparecchi.dettaglio', id=id)

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)

    nome_file = secure_filename(f"qr_{apparecchio['matricola']}.png")
    return send_file(buf, mimetype='image/png',
                     as_attachment=True, download_name=nome_file)


@apparecchi_bp.route('/apparecchi/<int:id>/modifica', methods=['GET', 'POST'])
@login_required
def modifica(id):
    """Edit an apparecchio."""
    apparecchio = apparecchio_accessibile(id)
    if not apparecchio:
        flash('Apparecchio non trovato.', 'danger')
        return redirect(url_for('apparecchi.lista'))

    if request.method == 'GET':
        accessori = query_all(
            "SELECT * FROM accessori WHERE apparecchio_id = ? ORDER BY id", (id,)
        )
        return render_template('apparecchi/form.html',
                               apparecchio=apparecchio, errors={},
                               divisioni=g.divisioni, form_data=apparecchio,
                               accessori=accessori)

    data, errors = _validate_apparecchio(request.form, edit_id=id)

    if errors:
        return render_template('apparecchi/form.html',
                               apparecchio=apparecchio, errors=errors,
                               divisioni=g.divisioni, form_data=request.form,
                               accessori=_parse_accessori_from_form(request.form))

    execute(
        """UPDATE apparecchi SET
           divisione_id=?, descrizione=?, matricola=?, numero_inventario=?,
           marca=?, modello=?, anno_fabbricazione=?, classificazione=?,
           ubicazione=?, stato=?, soggetto_verifica=?, connesso_rete=?, ip_address=?, mac_address=?,
           hostname=?, porta=?, protocollo=?, url_interfaccia=?,
           fornitore=?, codice_fornitore=?, garanzia_scadenza=?,
           contratto_manutenzione=?, note=?, updated_by=?, updated_at=datetime('now')
           WHERE id=?""",
        (data['divisione_id'], data['descrizione'], data['matricola'],
         data['numero_inventario'], data['marca'], data['modello'],
         data['anno_fabbricazione'], data['classificazione'],
         data['ubicazione'], data['stato'], data['soggetto_verifica'],
         data['connesso_rete'], data['ip_address'], data['mac_address'], data['hostname'],
         data['porta'], data['protocollo'], data['url_interfaccia'],
         data['fornitore'], data['codice_fornitore'], data['garanzia_scadenza'],
         data['contratto_manutenzione'], data['note'], g.user['id'], id)
    )

    _save_accessori(id, request.form, g.user['id'])

    log_attivita(g.user['id'], 'modifica', 'apparecchi', id,
                 f"Modificato: {data['marca']} {data['modello']}",
                 request.remote_addr)

    flash('Apparecchio aggiornato con successo.', 'success')
    return redirect(url_for('apparecchi.dettaglio', id=id))


@apparecchi_bp.route('/apparecchi/<int:id>/dismetti', methods=['POST'])
@login_required
def dismetti(id):
    """Soft-delete: set stato to 'dismesso'."""
    if g.user['ruolo'] not in ('admin', 'superadmin', 'tecnico'):
        flash('Non autorizzato a dismettere apparecchi.', 'danger')
        return redirect(url_for('apparecchi.lista'))
    apparecchio = apparecchio_accessibile(id)
    if not apparecchio:
        flash('Apparecchio non trovato.', 'danger')
        return redirect(url_for('apparecchi.lista'))

    execute(
        """UPDATE apparecchi SET stato = 'dismesso', updated_by = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (g.user['id'], id)
    )

    log_attivita(g.user['id'], 'dismissione', 'apparecchi', id,
                 f"Dismesso: {apparecchio['marca']} {apparecchio['modello']}",
                 request.remote_addr)

    flash(f"Apparecchio {apparecchio['marca']} {apparecchio['modello']} dismesso.", 'warning')
    return redirect(url_for('apparecchi.lista'))


@apparecchi_bp.route('/apparecchi/<int:id>/foto', methods=['POST'])
@login_required
def upload_foto(id):
    """Upload a photo for an apparecchio."""
    apparecchio = apparecchio_accessibile(id)
    if not apparecchio:
        flash('Apparecchio non trovato.', 'danger')
        return redirect(url_for('apparecchi.lista'))

    file = request.files.get('foto')
    if not file or not file.filename:
        flash('Nessun file selezionato.', 'warning')
        return redirect(url_for('apparecchi.dettaglio', id=id))

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_IMAGE_EXT:
        flash('Formato immagine non supportato. Usa JPG, PNG, GIF o WebP.', 'danger')
        return redirect(url_for('apparecchi.dettaglio', id=id))

    # Save file
    struttura_id = getattr(g, 'struttura_id', None)
    uploads_dir, rel_prefix = upload_subdir('foto', struttura_id)
    filename = f"{int(time.time())}_{secure_filename(file.filename)}"
    filepath = os.path.join(uploads_dir, filename)
    file.save(filepath)

    # Update database
    execute(
        """UPDATE apparecchi SET foto_path = ?, updated_by = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (f"{rel_prefix}/{filename}", g.user['id'], id)
    )

    flash('Foto caricata con successo.', 'success')
    return redirect(url_for('apparecchi.dettaglio', id=id))


@apparecchi_bp.route('/apparecchi/<int:id>/documento', methods=['POST'])
@login_required
def upload_documento(id):
    """Upload a document for an apparecchio."""
    apparecchio = apparecchio_accessibile(id)
    if not apparecchio:
        flash('Apparecchio non trovato.', 'danger')
        return redirect(url_for('apparecchi.lista'))

    file = request.files.get('documento')
    tipo = request.form.get('tipo_documento', 'report')

    if not file or not file.filename:
        flash('Nessun file selezionato.', 'warning')
        return redirect(url_for('apparecchi.dettaglio', id=id))

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_DOC_EXT:
        flash('Formato file non supportato.', 'danger')
        return redirect(url_for('apparecchi.dettaglio', id=id))

    if tipo not in ('manuale', 'certificato', 'foto', 'report'):
        tipo = 'report'

    # Save file
    struttura_id = getattr(g, 'struttura_id', None)
    uploads_dir, rel_prefix = upload_subdir('documenti', struttura_id)
    filename = f"{int(time.time())}_{secure_filename(file.filename)}"
    filepath = os.path.join(uploads_dir, filename)
    file.save(filepath)

    # Get file size
    filesize = os.path.getsize(filepath)

    # Insert into documenti table
    execute(
        """INSERT INTO documenti (apparecchio_id, tipo, filename, filepath, filesize, uploaded_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (id, tipo, file.filename, f"{rel_prefix}/{filename}", filesize, g.user['id'])
    )

    flash(f'Documento "{file.filename}" caricato con successo.', 'success')
    return redirect(url_for('apparecchi.dettaglio', id=id))


@apparecchi_bp.route('/apparecchi/<int:id>/documento/<int:doc_id>/scarica')
@login_required
def scarica_documento(id, doc_id):
    """Download a document."""
    # Verifica lo scope (struttura + divisione) prima di servire il file
    if not apparecchio_accessibile(id):
        flash('Apparecchio non trovato.', 'danger')
        return redirect(url_for('apparecchi.lista'))

    doc = query_one(
        "SELECT * FROM documenti WHERE id = ? AND apparecchio_id = ?",
        (doc_id, id)
    )
    if not doc:
        flash('Documento non trovato.', 'danger')
        return redirect(url_for('apparecchi.dettaglio', id=id))

    uploads_dir = current_app.config['UPLOADS_PATH']
    # Validate path stays within uploads directory (defense-in-depth)
    resolved = os.path.realpath(os.path.join(uploads_dir, doc['filepath']))
    if not resolved.startswith(os.path.realpath(uploads_dir) + os.sep):
        flash('Accesso al file non consentito.', 'danger')
        return redirect(url_for('apparecchi.dettaglio', id=id))
    return send_from_directory(uploads_dir, doc['filepath'],
                               as_attachment=True,
                               download_name=doc['filename'])
