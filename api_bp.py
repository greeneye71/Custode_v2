"""
MedInventory - REST API v1
Autenticazione: Bearer token (tabella api_tokens).
Tutti gli endpoint sono scoped alla struttura del token.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, request, jsonify, g
from models import query_one, query_all, execute, log_attivita

api_bp = Blueprint('api', __name__, url_prefix='/api/v1')

# Ogni quanto si riscrive api_tokens.ultimo_utilizzo. Il campo serve a capire
# se un token e' ancora in uso, non a contare le chiamate: al minuto esatto non
# lo guarda nessuno.
INTERVALLO_ULTIMO_UTILIZZO = timedelta(minutes=5)


def _ultimo_utilizzo_da_aggiornare(ultimo_utilizzo, adesso=None):
    """Dice se ultimo_utilizzo e' abbastanza vecchio da valere una scrittura.

    SQLite serializza gli scrittori: fino alla 2.8.1 ogni GET dell'API apriva
    una transazione in scrittura su api_tokens, mettendo in coda l'importazione,
    lo scheduler e gli utenti del gestionale. Un client che interroga
    /apparecchi ogni pochi secondi bloccava il database per il solo gusto di
    aggiornare un timestamp che nessuno legge cosi' spesso.

    CURRENT_TIMESTAMP di SQLite e' UTC nel formato 'YYYY-MM-DD HH:MM:SS', quindi
    il confronto fra stringhe segue l'ordine cronologico.
    """
    if not ultimo_utilizzo:
        return True
    adesso = adesso or datetime.now(timezone.utc)
    soglia = (adesso - INTERVALLO_ULTIMO_UTILIZZO).strftime('%Y-%m-%d %H:%M:%S')
    return str(ultimo_utilizzo)[:19] <= soglia


def _token_auth(scope='read'):
    """Decorator: verifica token Bearer e popola g.api_struttura_id."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            auth = request.headers.get('Authorization', '')
            if not auth.startswith('Bearer '):
                return jsonify({'errore': 'Token mancante'}), 401
            raw_token = auth[7:]
            token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

            token = query_one("""
                SELECT t.*, s.id as sid, s.nome as struttura_nome
                FROM api_tokens t
                JOIN strutture s ON s.id = t.struttura_id
                WHERE t.token_hash = ? AND t.attivo = 1
                  AND (t.scadenza IS NULL OR t.scadenza >= date('now'))
                  AND s.attiva = 1
            """, (token_hash,))

            if not token:
                return jsonify({'errore': 'Token non valido o scaduto'}), 401

            scopes_list = (token['scopes'] or '').split()
            if scope == 'write' and 'write' not in scopes_list:
                return jsonify({'errore': 'Permessi insufficienti'}), 403

            if _ultimo_utilizzo_da_aggiornare(token['ultimo_utilizzo']):
                try:
                    execute(
                        "UPDATE api_tokens SET ultimo_utilizzo=CURRENT_TIMESTAMP WHERE id=?",
                        (token['id'],)
                    )
                except Exception:
                    pass

            g.api_struttura_id = token['sid']
            g.api_struttura_nome = token['struttura_nome']
            g.api_token_nome = token['nome']
            return f(*args, **kwargs)
        return decorated
    return decorator


def _paginazione():
    """Pagina e dimensione richieste, riportate dentro limiti sensati.

    request.args.get(type=int) restituisce None su un valore non numerico e
    non ha limite inferiore: fino alla 2.7.1 ?per_page=-1 arrivava a SQLite
    come LIMIT -1, che significa 'nessun limite', e ?page=0 dava un OFFSET
    negativo.
    """
    page = request.args.get('page', 1, type=int) or 1
    per_page = request.args.get('per_page', 50, type=int) or 50
    page = max(1, page)
    per_page = max(1, min(per_page, 200))
    return page, per_page, (page - 1) * per_page


def _pagina(query_result, page, per_page, total):
    return {
        'dati': [dict(r) for r in query_result],
        'paginazione': {'pagina': page, 'per_pagina': per_page, 'totale': total},
    }


@api_bp.route('/apparecchi')
@_token_auth('read')
def lista_apparecchi():
    page, per_page, offset = _paginazione()

    total = query_one(
        "SELECT COUNT(*) as c FROM apparecchi WHERE struttura_id=? AND stato!='dismesso'",
        (g.api_struttura_id,)
    )['c']

    apparecchi = query_all("""
        SELECT a.id, a.descrizione, a.marca, a.modello, a.matricola,
               a.numero_inventario, a.stato, a.ubicazione,
               d.nome as divisione
        FROM apparecchi a
        JOIN divisioni d ON d.id = a.divisione_id
        WHERE a.struttura_id = ? AND a.stato != 'dismesso'
        ORDER BY a.marca, a.modello
        LIMIT ? OFFSET ?
    """, (g.api_struttura_id, per_page, offset))

    return jsonify(_pagina(apparecchi, page, per_page, total))


@api_bp.route('/apparecchi/<int:apparecchio_id>')
@_token_auth('read')
def dettaglio_apparecchio(apparecchio_id):
    app_ = query_one("""
        SELECT a.*, d.nome as divisione
        FROM apparecchi a
        JOIN divisioni d ON d.id = a.divisione_id
        WHERE a.id = ? AND a.struttura_id = ?
    """, (apparecchio_id, g.api_struttura_id))
    if not app_:
        return jsonify({'errore': 'Apparecchio non trovato'}), 404
    return jsonify(dict(app_))


@api_bp.route('/scadenze')
@_token_auth('read')
def scadenze():
    page, per_page, offset = _paginazione()

    total = query_one("""
        SELECT COUNT(*) as c FROM prossime_scadenze ps
        JOIN apparecchi a ON a.id = ps.apparecchio_id
        WHERE a.struttura_id = ?
    """, (g.api_struttura_id,))['c']

    rows = query_all("""
        SELECT ps.apparecchio_id, ps.descrizione, ps.marca, ps.modello, ps.matricola,
               ps.tipo_manutenzione, ps.prossima_scadenza, ps.giorni_rimasti, ps.priorita
        FROM prossime_scadenze ps
        JOIN apparecchi a ON a.id = ps.apparecchio_id
        WHERE a.struttura_id = ?
        ORDER BY ps.prossima_scadenza
        LIMIT ? OFFSET ?
    """, (g.api_struttura_id, per_page, offset))

    return jsonify(_pagina(rows, page, per_page, total))


@api_bp.route('/manutenzioni')
@_token_auth('read')
def lista_manutenzioni():
    page, per_page, offset = _paginazione()

    total = query_one("""
        SELECT COUNT(*) as c FROM manutenzioni m
        JOIN apparecchi a ON a.id = m.apparecchio_id
        WHERE a.struttura_id = ?
    """, (g.api_struttura_id,))['c']

    rows = query_all("""
        SELECT m.id, m.tipo, m.data_intervento, m.prossima_scadenza,
               m.tecnico_ditta, m.esito, m.costo,
               a.descrizione as apparecchio, a.matricola
        FROM manutenzioni m
        JOIN apparecchi a ON a.id = m.apparecchio_id
        WHERE a.struttura_id = ?
        ORDER BY m.data_intervento DESC
        LIMIT ? OFFSET ?
    """, (g.api_struttura_id, per_page, offset))

    return jsonify(_pagina(rows, page, per_page, total))


@api_bp.route('/manutenzioni', methods=['POST'])
@_token_auth('write')
def crea_manutenzione():
    data = request.get_json(silent=True) or {}
    required = ('apparecchio_id', 'tipo', 'data_intervento')
    mancanti = [k for k in required if data.get(k) is None or data.get(k) == '']
    if mancanti:
        return jsonify({'errore': f'Campi mancanti: {", ".join(mancanti)}'}), 400

    tipi_validi = ('preventiva', 'correttiva', 'verifica', 'calibrazione')
    if data['tipo'] not in tipi_validi:
        return jsonify({'errore': f'tipo deve essere uno di: {tipi_validi}'}), 400

    from datetime import datetime as _dt
    for campo_data in ['data_intervento', 'prossima_scadenza']:
        val = data.get(campo_data)
        if val:
            try:
                _dt.strptime(val, '%Y-%m-%d')
            except ValueError:
                return jsonify({'errore': f'{campo_data} deve essere nel formato YYYY-MM-DD'}), 400

    # manutenzioni.esito e' testo libero (nessun CHECK): non si valida.
    # Questi due invece finivano in colonne numeriche e un valore non numerico
    # o negativo usciva come 500 invece che come 400.
    for campo_num, tipo_num in (('costo', float), ('periodicita_giorni', int)):
        val = data.get(campo_num)
        if val is None or val == '':
            continue
        try:
            num = tipo_num(val)
        except (TypeError, ValueError):
            return jsonify({'errore': f'{campo_num} deve essere numerico'}), 400
        if num < 0:
            return jsonify({'errore': f'{campo_num} non puo essere negativo'}), 400
        data[campo_num] = num

    app_ = query_one(
        "SELECT id FROM apparecchi WHERE id=? AND struttura_id=?",
        (data['apparecchio_id'], g.api_struttura_id)
    )
    if not app_:
        return jsonify({'errore': 'Apparecchio non trovato nella struttura'}), 404

    cur = execute("""
        INSERT INTO manutenzioni
            (apparecchio_id, tipo, data_intervento, prossima_scadenza,
             periodicita_giorni, tecnico_ditta, descrizione, esito, costo, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
    """, (
        data['apparecchio_id'], data['tipo'], data['data_intervento'],
        data.get('prossima_scadenza'), data.get('periodicita_giorni'),
        data.get('tecnico_ditta'), data.get('descrizione'),
        data.get('esito'), data.get('costo'),
    ))

    # Anche le scritture via API finiscono nel registro attivita': senza
    # questa voce una manutenzione creata da un token compariva dal nulla.
    # utente_id resta NULL — l'autore e' il token, non una persona.
    log_attivita(None, 'creazione', 'manutenzioni', cur.lastrowid,
                 f"Creata via API (token: {getattr(g, 'api_token_nome', '?')}) "
                 f"per apparecchio {data['apparecchio_id']}",
                 request.remote_addr, struttura_id=g.api_struttura_id)

    return jsonify({'id': cur.lastrowid, 'messaggio': 'Manutenzione creata'}), 201
