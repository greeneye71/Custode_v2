"""
MedInventory - Stampe

Prospetti PDF pensati per la carta. Questo modulo e' l'unico che conosce lo
scope dell'utente: fa le query, valida i parametri e passa dati gia' filtrati
al motore in report_service.py, che dell'applicazione non sa nulla.
"""

import io
from collections import namedtuple
from datetime import datetime, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, g, send_file)
from werkzeug.utils import secure_filename

from auth import login_required
from models import query_all, percorso_logo_struttura

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


def _filtro_tutte_le_divisioni():
    """Clausola SQL e parametri per l'ambito 'tutte le divisioni'.

    Segue la tabella di ambito della specifica: per admin, tecnico e
    superadmin 'tutte le divisioni' significa l'intera struttura, quindi
    filtra per struttura_id; per l'utente resta l'elenco delle sole
    divisioni assegnate. Il filtro per struttura_id include anche le
    divisioni disattivate della struttura (che restano fuori da g.divisioni
    ma sono comunque nell'ambito 'struttura'), cosi' l'inventario stampato
    non perde apparecchi che invece compaiono a video. struttura_id viene
    sempre da g.struttura_id, mai da un parametro dell'URL: l'isolamento fra
    strutture resta invariato.

    Nota: questo non e' lo stesso criterio di models.filtro_divisione(),
    che onora prima g.divisione_attiva e mette il superadmin nel ramo delle
    divisioni anziche' in quello della struttura.

    Restituisce (None, []) se non c'e' alcun ambito su cui stampare.
    """
    ruolo = g.user['ruolo']
    struttura_id = getattr(g, 'struttura_id', None)
    if ruolo in ('admin', 'tecnico', 'superadmin') and struttura_id:
        return "a.struttura_id = ?", [struttura_id]

    ids = [d['id'] for d in _divisioni_accessibili()]
    if not ids:
        return None, []
    segnaposto = ','.join('?' * len(ids))
    return f"a.divisione_id IN ({segnaposto})", ids


Ambito = namedtuple('Ambito', 'clausola parametri etichetta nome_divisione')


def _ambito_richiesto(divisione_id):
    """Traduce il parametro divisione_id nell'ambito su cui stampare.

    Restituisce (Ambito, errore); errore e' la coppia (messaggio, categoria)
    da passare a flash(), oppure None.

    Vive in un solo posto perche' e' il codice che decide cosa un utente puo'
    vedere: tenerne una copia per prospetto significa che una correzione
    all'isolamento applicata a una sola delle due lascia l'altra scoperta, e
    la differenza non si nota finche' qualcuno non stampa il prospetto
    sbagliato. Sia l'inventario sia le scadenze devono mostrare esattamente
    lo stesso insieme allo stesso utente.
    """
    if divisione_id == 'tutte':
        clausola, parametri = _filtro_tutte_le_divisioni()
        if clausola is None:
            return None, ('Nessuna divisione accessibile.', 'warning')
        return Ambito(clausola, parametri, '', ''), None

    divisione = _divisione_in_scope(divisione_id)
    if not divisione:
        return None, ('Divisione non disponibile.', 'danger')
    return Ambito('a.divisione_id = ?', [divisione['id']],
                  f"Divisione: {divisione['nome']}", divisione['nome']), None


def _contesto_base(titolo, ambito='', **extra):
    struttura = getattr(g, 'struttura', None)
    contesto = {
        'struttura_nome': (struttura or {}).get('nome') or 'MedInventory',
        'titolo': titolo,
        'ambito': ambito,
        'logo_path': percorso_logo_struttura(struttura),
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


def _excel(buffer, nome_file):
    return send_file(
        buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True, download_name=f'{nome_file}.xlsx')


@stampe_bp.route('', strict_slashes=False)
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

    includi_dismessi = request.args.get('dismessi') == '1'

    ambito, errore = _ambito_richiesto(request.args.get('divisione_id', 'tutte'))
    if errore:
        flash(*errore)
        return redirect(url_for('stampe.index'))

    filtro_stato = '' if includi_dismessi else "AND a.stato != 'dismesso'"
    righe = query_all(
        f"""SELECT a.marca, a.modello, a.descrizione, a.matricola, a.ubicazione,
                   d.nome AS divisione_nome
            FROM apparecchi a
            LEFT JOIN divisioni d ON d.id = a.divisione_id
            WHERE {ambito.clausola} {filtro_stato}
            ORDER BY d.nome, a.marca, a.modello""",
        ambito.parametri)

    # Piu' di una divisione presente fra le righe restituite: raggruppa il
    # prospetto per divisione (vale sia per l'ambito 'tutte' sia, in teoria,
    # per l'ambito di una singola divisione, dove pero' e' sempre falso).
    raggruppa = len({r['divisione_nome'] for r in righe}) > 1

    contesto = _contesto_base('Inventario apparecchi elettromedicali',
                              ambito.etichetta, raggruppa=raggruppa)
    nome = _nome_file('inventario', ambito.nome_divisione)
    if request.args.get('formato') == 'excel':
        from export_service import stampa_inventario_excel
        titolo = 'Inventario apparecchi elettromedicali'
        return _excel(stampa_inventario_excel(righe, titolo), nome)
    return _pdf(stampa_inventario(righe, contesto), nome)


TIPI_SCADENZA = {
    'manutenzioni': ('manutenzione', 'Scadenze manutenzioni'),
    'verifiche': ('verifica', 'Scadenze verifiche di sicurezza elettrica'),
}


def _intervallo(periodo, da, a):
    """Confini (inizio, fine) del periodo, in formato ISO.

    Servono due forme diverse: le scelte rapide sono finestre mobili che
    partono sempre da oggi, 'date' e' un intervallo libero i cui due confini
    vengono entrambi dall'utente. Restituisce (inizio, fine, errore).
    """
    oggi = datetime.now().date()
    if periodo == '30g':
        return oggi.isoformat(), (oggi + timedelta(days=30)).isoformat(), None
    if periodo == '90g':
        return oggi.isoformat(), (oggi + timedelta(days=90)).isoformat(), None
    if periodo == 'anno':
        return oggi.isoformat(), oggi.replace(month=12, day=31).isoformat(), None
    if periodo == 'anno_prossimo':
        return oggi.isoformat(), oggi.replace(year=oggi.year + 1, month=12, day=31).isoformat(), None
    if periodo == 'date':
        try:
            inizio = datetime.strptime(da or '', '%Y-%m-%d').date()
            fine = datetime.strptime(a or '', '%Y-%m-%d').date()
        except ValueError:
            return None, None, 'Indica due date valide nel formato anno-mese-giorno.'
        if fine < inizio:
            return None, None, 'La data finale precede quella iniziale.'
        return inizio.isoformat(), fine.isoformat(), None
    return None, None, 'Periodo non riconosciuto.'


@stampe_bp.route('/scadenze/<tipo>')
@login_required
def scadenze(tipo):
    from report_service import stampa_scadenze

    if tipo not in TIPI_SCADENZA:
        flash('Tipo di prospetto non riconosciuto.', 'danger')
        return redirect(url_for('stampe.index'))
    tipo_record, titolo = TIPI_SCADENZA[tipo]

    inizio, fine, errore = _intervallo(request.args.get('periodo', '30g'),
                                       request.args.get('da'), request.args.get('a'))
    if errore:
        flash(errore, 'danger')
        return redirect(url_for('stampe.index'))

    # Un solo "oggi" per l'intero prospetto: deve valere sia per il confine
    # delle scadute sia (tramite _intervallo) per quello delle scelte rapide.
    # Se le due sezioni usassero definizioni diverse - qui datetime.now()
    # locale, nella query date('now') di SQLite, che e' in UTC - fra
    # mezzanotte e l'una (le due con l'ora legale) una scadenza di ieri non
    # soddisferebbe ne' "< ieri" ne' ">= oggi": sparirebbe dal prospetto
    # senza alcun segnale, proprio nella fascia oraria in cui e' piu'
    # probabile che nessuno se ne accorga prima che diventi un problema.
    oggi = datetime.now().date().isoformat()

    ambito, errore = _ambito_richiesto(request.args.get('divisione_id', 'tutte'))
    if errore:
        flash(*errore)
        return redirect(url_for('stampe.index'))

    # La vista prossime_scadenze non porta struttura_id: per applicare lo
    # stesso criterio di ambito dell'inventario (_filtro_tutte_le_divisioni,
    # che referenzia a.struttura_id / a.divisione_id) serve un join con
    # apparecchi. Cosi' i due prospetti mostrano lo stesso insieme allo
    # stesso utente.
    base = f"""SELECT ps.marca, ps.modello, ps.matricola, ps.ubicazione,
                      ps.prossima_scadenza, ps.giorni_rimasti,
                      d.nome AS divisione_nome
               FROM prossime_scadenze ps
               JOIN apparecchi a ON a.id = ps.apparecchio_id
               LEFT JOIN divisioni d ON d.id = ps.divisione_id
               WHERE ps.tipo_record = ? AND {ambito.clausola}"""

    scadute = query_all(
        base + " AND ps.prossima_scadenza < ? ORDER BY ps.prossima_scadenza",
        [tipo_record] + ambito.parametri + [oggi])
    # Le due sezioni devono partizionare le scadenze, non sovrapporsi. Le
    # scadute sono per definizione tutto cio' che precede oggi, qualunque
    # periodo sia stato scelto; con un intervallo libero che parte nel passato
    # ("cosa scade in tutto il 2026") l'utente puo' indicare un inizio
    # anteriore a oggi, e senza questo confine ogni scadenza compresa fra i
    # due estremi finirebbe in entrambe le query: stampata due volte, e
    # contata due volte nel totale in calce.
    in_scadenza = query_all(
        base + " AND ps.prossima_scadenza >= ? AND ps.prossima_scadenza <= ?"
               " ORDER BY ps.prossima_scadenza",
        [tipo_record] + ambito.parametri + [max(inizio, oggi), fine])

    contesto = _contesto_base(
        titolo, ambito.etichetta,
        fine_periodo=datetime.strptime(fine, '%Y-%m-%d').strftime('%d/%m/%Y'),
        mostra_spunta=request.args.get('spunta') == '1')
    nome = _nome_file(f'scadenze-{tipo}', ambito.nome_divisione)
    if request.args.get('formato') == 'excel':
        from export_service import stampa_scadenze_excel
        return _excel(stampa_scadenze_excel(scadute, in_scadenza, titolo), nome)
    return _pdf(stampa_scadenze(scadute, in_scadenza, contesto), nome)
