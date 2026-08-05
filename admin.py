"""
MedInventory - Admin Blueprint
User management, division management, configuration editor, email config.
All routes require admin role.
"""

import json
import os
from datetime import datetime, timedelta

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, g, current_app, session as flask_session, jsonify
)
from werkzeug.security import generate_password_hash

from auth import (admin_required, superadmin_required,
                  tecnico_o_admin_required, operazione_globale_required)
from models import query_one, query_all, execute, log_attivita, get_db
from ai_service import AI_PROVIDERS

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


# ============================================================================
# USER MANAGEMENT
# ============================================================================

def _get_mia_struttura_id():
    """Restituisce lo struttura_id dell'utente corrente (None per superadmin)."""
    if g.user['ruolo'] == 'superadmin':
        return None
    return g.user.get('struttura_id') or getattr(g, 'struttura_id', None)


def _check_utente_scope(utente):
    """
    Verifica che l'admin corrente possa gestire l'utente dato.
    Superadmin può sempre. Admin non può toccare utenti superadmin
    né utenti di altre strutture.
    """
    if g.user['ruolo'] == 'superadmin':
        return True
    if utente['ruolo'] == 'superadmin':
        return False
    struttura_id = _get_mia_struttura_id()
    return utente.get('struttura_id') == struttura_id


def _divisioni_per_struttura(struttura_id=None):
    """Restituisce le divisioni attive di strutture attive."""
    if struttura_id:
        return query_all(
            "SELECT * FROM divisioni WHERE attiva=1 AND struttura_id=? ORDER BY nome",
            (struttura_id,)
        )
    return query_all("""
        SELECT d.* FROM divisioni d
        JOIN strutture s ON s.id = d.struttura_id AND s.attiva = 1
        WHERE d.attiva = 1 ORDER BY d.struttura_id, d.nome
    """)


def _assegna_divisioni(utente_id, divisioni_sel, struttura_id, ruolo):
    """Cancella e reinserisce le divisioni utente, verificando l'appartenenza alla struttura."""
    execute("DELETE FROM utenti_divisioni WHERE utente_id = ?", (utente_id,))
    for div_id in divisioni_sel:
        try:
            div_id_int = int(div_id)
            if struttura_id:
                div = query_one(
                    "SELECT id FROM divisioni WHERE id=? AND struttura_id=?",
                    (div_id_int, struttura_id)
                )
            else:
                div = query_one("SELECT id FROM divisioni WHERE id=?", (div_id_int,))
            if div:
                execute(
                    """INSERT INTO utenti_divisioni (utente_id, divisione_id, ruolo_divisione)
                       VALUES (?, ?, ?)""",
                    (utente_id, div_id_int, ruolo)
                )
        except Exception:
            pass


@admin_bp.route('/utenti')
@admin_required
def utenti():
    """Lista utenti: superadmin vede tutti, admin vede solo la propria struttura."""
    is_superadmin = g.user['ruolo'] == 'superadmin'
    if is_superadmin:
        users = query_all("""
            SELECT u.*, s.nome as struttura_nome,
                   GROUP_CONCAT(d.nome, ', ') as divisioni_nomi
            FROM utenti u
            LEFT JOIN strutture s ON s.id = u.struttura_id
            LEFT JOIN utenti_divisioni ud ON u.id = ud.utente_id
            LEFT JOIN divisioni d ON ud.divisione_id = d.id
            WHERE u.eliminato_il IS NULL AND u.ruolo != 'tecnico'
            GROUP BY u.id ORDER BY u.ruolo, u.cognome, u.nome
        """)
    else:
        struttura_id = _get_mia_struttura_id()
        users = query_all("""
            SELECT u.*, GROUP_CONCAT(d.nome, ', ') as divisioni_nomi
            FROM utenti u
            LEFT JOIN utenti_divisioni ud ON u.id = ud.utente_id
            LEFT JOIN divisioni d ON ud.divisione_id = d.id
            WHERE u.struttura_id = ? AND u.ruolo != 'superadmin' AND u.eliminato_il IS NULL
            GROUP BY u.id ORDER BY u.cognome, u.nome
        """, (struttura_id,))
    return render_template('admin/utenti.html', utenti=users, is_superadmin=is_superadmin)


@admin_bp.route('/struttura/<int:struttura_id>/divisioni-json')
@admin_required
def struttura_divisioni_json(struttura_id):
    """Restituisce le divisioni attive di una struttura in JSON (usato da AJAX nel form utente)."""
    from flask import jsonify
    divisioni = _divisioni_per_struttura(struttura_id)
    return jsonify([{'id': d['id'], 'nome': d['nome'], 'colore': d['colore']} for d in divisioni])


@admin_bp.route('/utenti/nuovo', methods=['GET', 'POST'])
@admin_required
def utente_nuovo():
    """Crea un nuovo utente. Superadmin sceglie la struttura, admin usa la propria."""
    is_superadmin = g.user['ruolo'] == 'superadmin'
    mia_struttura_id = _get_mia_struttura_id()

    strutture = (query_all("SELECT id, nome FROM strutture WHERE attiva=1 ORDER BY nome")
                 if is_superadmin else [])

    if is_superadmin:
        divisioni = []  # caricate via AJAX al cambio struttura
    else:
        divisioni = _divisioni_per_struttura(mia_struttura_id)

    if request.method == 'GET':
        return render_template('admin/utente_form.html',
                               utente=None, errors={}, form_data={},
                               divisioni=divisioni, strutture=strutture,
                               is_superadmin=is_superadmin)

    # POST
    errors = {}
    form = request.form
    nome = form.get('nome', '').strip()
    cognome = form.get('cognome', '').strip()
    email = form.get('email', '').strip().lower()
    ruolo = form.get('ruolo', 'utente')
    password = form.get('password', '').strip()
    divisioni_sel = form.getlist('divisioni')

    struttura_id = (form.get('struttura_id', type=int) if is_superadmin else mia_struttura_id)

    if ruolo not in ('admin', 'utente'):
        ruolo = 'utente'

    if not nome:
        errors['nome'] = 'Il nome è obbligatorio.'
    if not cognome:
        errors['cognome'] = 'Il cognome è obbligatorio.'
    if not email:
        errors['email'] = "L'email è obbligatoria."
    elif query_one("SELECT id FROM utenti WHERE email = ?", (email,)):
        errors['email'] = 'Questo indirizzo email è già registrato.'
    if not password or len(password) < 8:
        errors['password'] = 'La password deve essere di almeno 8 caratteri.'
    if is_superadmin and not struttura_id:
        errors['struttura_id'] = 'Selezionare una struttura.'

    if errors:
        return render_template('admin/utente_form.html',
                               utente=None, errors=errors, form_data=form,
                               divisioni=divisioni, strutture=strutture,
                               is_superadmin=is_superadmin)

    password_hash = generate_password_hash(password)
    cursor = execute(
        """INSERT INTO utenti (email, password_hash, nome, cognome, ruolo, struttura_id, primo_accesso)
           VALUES (?, ?, ?, ?, ?, ?, 1)""",
        (email, password_hash, nome, cognome, ruolo, struttura_id)
    )
    user_id = cursor.lastrowid
    _assegna_divisioni(user_id, divisioni_sel, struttura_id, ruolo)

    log_attivita(g.user['id'], 'creazione', 'utenti', user_id,
                 f"Creato utente: {nome} {cognome} ({email})", request.remote_addr,
                 struttura_id=struttura_id)
    flash(f'Utente {nome} {cognome} creato. Password temporanea: {password}', 'success')
    return redirect(url_for('admin.utenti'))


@admin_bp.route('/utenti/<int:id>/modifica', methods=['GET', 'POST'])
@admin_required
def utente_modifica(id):
    """Modifica utente. Admin può modificare solo utenti della propria struttura."""
    utente = query_one("SELECT * FROM utenti WHERE id = ?", (id,))
    if not utente:
        flash('Utente non trovato.', 'danger')
        return redirect(url_for('admin.utenti'))

    if not _check_utente_scope(utente):
        flash('Non hai i permessi per modificare questo utente.', 'danger')
        return redirect(url_for('admin.utenti'))

    if utente['ruolo'] == 'tecnico':
        flash('I tecnici si gestiscono dalla loro pagina.', 'warning')
        return redirect(url_for('admin.tecnici'))

    if utente['eliminato_il'] is not None:
        flash(MESSAGGI_RIFIUTO['gia_cancellato'], 'danger')
        return redirect(url_for('admin.utenti'))

    is_superadmin = g.user['ruolo'] == 'superadmin'
    mia_struttura_id = _get_mia_struttura_id()
    struttura_id_utente = utente.get('struttura_id') or mia_struttura_id

    strutture = (query_all("SELECT id, nome FROM strutture WHERE attiva=1 ORDER BY nome")
                 if is_superadmin else [])

    divisioni = _divisioni_per_struttura(struttura_id_utente)

    utente_div_ids = [str(r['divisione_id']) for r in
                      query_all("SELECT divisione_id FROM utenti_divisioni WHERE utente_id=?", (id,))]

    if request.method == 'GET':
        form_data = dict(utente)
        form_data['divisioni'] = utente_div_ids
        return render_template('admin/utente_form.html',
                               utente=utente, errors={}, form_data=form_data,
                               divisioni=divisioni, strutture=strutture,
                               is_superadmin=is_superadmin)

    # POST
    errors = {}
    form = request.form
    nome = form.get('nome', '').strip()
    cognome = form.get('cognome', '').strip()
    email = form.get('email', '').strip().lower()
    ruolo = form.get('ruolo', 'utente')
    divisioni_sel = form.getlist('divisioni')

    struttura_id = (form.get('struttura_id', type=int) or struttura_id_utente
                    if is_superadmin else mia_struttura_id)
    attivo = 1 if form.get('attivo') else 0

    # Due cose non si possono cambiare su se stessi da questo modulo: 'attivo'
    # e 'ruolo'. Entrambe vengono ignorate (non bloccano il resto del
    # salvataggio: correggersi il nome deve continuare a funzionare), e
    # l'operatore viene avvisato di cosa e' stato ignorato — un flash muto
    # ("Utente aggiornato") sarebbe una piccola bugia.
    ignorati_su_se_stessi = []

    if utente['id'] == g.user['id']:
        # Non ci si puo' disattivare da soli. Il freno era esplicito nella
        # rotta /toggle rimossa da questo task: spostare 'attivo' nel modulo
        # generico senza portarlo con se' bastava a renderlo raggiungibile in
        # silenzio, anche solo salvando la propria scheda senza spuntare la
        # casella. Per l'unico superadmin - o l'unico admin di un'installazione
        # single-struttura, che puo' non avere alcun superadmin - sarebbe un
        # blocco totale: auth.py pretende attivo=1 sia in sessione sia al
        # login, e dall'applicazione non ci sarebbe piu' modo di rimediare.
        if not attivo:
            ignorati_su_se_stessi.append('disattivare il tuo account')
        attivo = 1
        # Simmetrico: non ci si puo' declassare (ne' promuovere) da soli.
        # Senza questo freno, un admin che apre la propria scheda e sceglie
        # 'Utente' nel menu Ruolo si autodeclassa con un solo POST — e se e'
        # l'ultimo admin della struttura (o l'unico admin di
        # un'installazione single-struttura senza superadmin) la struttura
        # resta senza nessuno che possa amministrarla, con la stessa identica
        # conseguenza e lo stesso rimedio da riga di comando che questo piano
        # ha gia' chiuso per la cancellazione.
        if ruolo != utente['ruolo']:
            ignorati_su_se_stessi.append('cambiare il tuo ruolo')
        ruolo = utente['ruolo']
    # Stesso motivo per cui la cancellazione rifiuta l'ultimo admin di una
    # struttura: disattivarlo la lascerebbe senza nessuno che possa
    # gestirla. Si applica solo quando l'account si sta davvero spegnendo
    # (1 -> 0): riattivare, o lasciare 'attivo' invariato, non deve mai
    # essere ostacolato da qui.
    elif utente['attivo'] and not attivo:
        from utente_service import motivo_rifiuto
        motivo = motivo_rifiuto(get_db(), id)
        if motivo:
            flash(MESSAGGI_RIFIUTO[motivo], 'danger')
            return redirect(url_for('admin.utenti'))

    # Stesso rifiuto, per la stessa ragione: declassare l'ultimo admin di una
    # struttura la lascerebbe senza nessuno che possa gestirla — e' lo stesso
    # stato finale della disattivazione qui sopra, per di piu' senza la
    # riattivazione con un clic come rimedio. Si applica solo alla transizione
    # vera admin -> utente: promuovere un utente ad admin, o lasciare il ruolo
    # invariato, non deve mai essere bloccato da qui.
    if utente['ruolo'] == 'admin' and ruolo == 'utente':
        from utente_service import motivo_rifiuto
        motivo = motivo_rifiuto(get_db(), id)
        if motivo:
            flash(MESSAGGI_RIFIUTO[motivo], 'danger')
            return redirect(url_for('admin.utenti'))

    # Il modulo generico gestisce 'admin' e 'utente'. Il ruolo di un superadmin
    # non e' modificabile da qui, e non gli si assegna una struttura: fino alla
    # 2.6.1 salvare la propria scheda declassava l'unico superadmin del
    # deployment a 'utente', lasciando nessuno che potesse creare strutture o
    # fare backup.
    if utente['ruolo'] == 'superadmin':
        ruolo = 'superadmin'
        struttura_id = None
    elif ruolo not in ('admin', 'utente'):
        errors['ruolo'] = 'Ruolo non ammesso.'
        ruolo = utente['ruolo']

    if not nome:
        errors['nome'] = 'Il nome è obbligatorio.'
    if not cognome:
        errors['cognome'] = 'Il cognome è obbligatorio.'
    if not email:
        errors['email'] = "L'email è obbligatoria."
    else:
        existing = query_one("SELECT id FROM utenti WHERE email=? AND id!=?", (email, id))
        if existing:
            errors['email'] = 'Questo indirizzo email è già registrato.'

    if errors:
        divisioni_post = _divisioni_per_struttura(struttura_id or struttura_id_utente)
        form_data = dict(form)
        form_data['divisioni'] = divisioni_sel
        return render_template('admin/utente_form.html',
                               utente=utente, errors=errors, form_data=form_data,
                               divisioni=divisioni_post, strutture=strutture,
                               is_superadmin=is_superadmin)

    execute(
        """UPDATE utenti SET nome=?, cognome=?, email=?, ruolo=?, struttura_id=?,
           attivo=?, updated_at=datetime('now') WHERE id=?""",
        (nome, cognome, email, ruolo, struttura_id, attivo, id)
    )
    _assegna_divisioni(id, divisioni_sel, struttura_id, ruolo)

    log_attivita(g.user['id'], 'modifica', 'utenti', id,
                 f"Modificato utente: {nome} {cognome}", request.remote_addr,
                 struttura_id=struttura_id)
    if ignorati_su_se_stessi:
        flash("Non puoi " + " ne' ".join(ignorati_su_se_stessi) +
              " da solo: il resto della modifica e' stato salvato.", 'warning')
    else:
        flash('Utente aggiornato.', 'success')
    return redirect(url_for('admin.utenti'))


@admin_bp.route('/utenti/<int:id>/reset-password', methods=['POST'])
@admin_required
def utente_reset_password(id):
    """Reset password. Admin può agire solo sulla propria struttura."""
    utente = query_one("SELECT * FROM utenti WHERE id = ?", (id,))
    if not utente:
        flash('Utente non trovato.', 'danger')
        return redirect(url_for('admin.utenti'))
    if not _check_utente_scope(utente):
        flash('Non hai i permessi per questa operazione.', 'danger')
        return redirect(url_for('admin.utenti'))

    if utente['ruolo'] == 'tecnico':
        flash('I tecnici si gestiscono dalla loro pagina.', 'warning')
        return redirect(url_for('admin.tecnici'))

    if utente['eliminato_il'] is not None:
        flash(MESSAGGI_RIFIUTO['gia_cancellato'], 'danger')
        return redirect(url_for('admin.utenti'))

    import secrets
    temp_password = secrets.token_urlsafe(10)
    execute(
        """UPDATE utenti SET password_hash=?, primo_accesso=1, updated_at=datetime('now')
           WHERE id=?""",
        (generate_password_hash(temp_password), id)
    )
    execute("DELETE FROM sessioni WHERE utente_id = ?", (id,))

    log_attivita(g.user['id'], 'reset_password', 'utenti', id,
                 f"Reset password per: {utente['nome']} {utente['cognome']}", request.remote_addr)
    flash(f"Password resettata per {utente['nome']} {utente['cognome']}. "
          f"Nuova password temporanea: {temp_password}", 'warning')
    return redirect(url_for('admin.utenti'))


MESSAGGI_RIFIUTO = {
    'inesistente': 'Utente non trovato.',
    'gia_cancellato': 'Questo utente e\' gia\' stato cancellato.',
    'ultimo_admin': "E' l'ultimo amministratore attivo della struttura: senza "
                    "di lui nessuno potrebbe piu' gestirla. Nomina prima un "
                    "altro amministratore (o riattiva quello disattivato), "
                    "poi procedi.",
    'ultimo_superadmin': "E' l'ultimo superamministratore attivo: senza di lui "
                         "nessuno potrebbe piu' creare strutture, fare backup o "
                         "riparare una struttura rimasta senza amministratore.",
}


def _utente_cancellabile(id):
    """(utente, messaggio_di_rifiuto). Se il messaggio c'e', non si procede."""
    utente = query_one(
        """SELECT u.*, s.nome AS struttura_nome
           FROM utenti u LEFT JOIN strutture s ON s.id = u.struttura_id
           WHERE u.id = ?""", (id,))
    if not utente:
        return None, MESSAGGI_RIFIUTO['inesistente']
    if not _check_utente_scope(utente):
        return None, 'Non hai i permessi per questa operazione.'
    if utente['ruolo'] == 'tecnico':
        return None, ('I tecnici si gestiscono dalla loro pagina: sono account '
                      'condivisi fra strutture.')
    if utente['id'] == g.user['id']:
        return None, 'Non puoi cancellare il tuo account.'
    from utente_service import motivo_rifiuto
    motivo = motivo_rifiuto(get_db(), id)
    if motivo:
        return None, MESSAGGI_RIFIUTO[motivo]
    return utente, None


@admin_bp.route('/utenti/<int:id>/elimina', methods=['GET'])
@admin_required
def utente_elimina_conferma(id):
    from utente_service import conteggi_riferimenti

    utente, rifiuto = _utente_cancellabile(id)
    if rifiuto:
        flash(rifiuto, 'danger')
        return redirect(url_for('admin.utenti'))

    return render_template('admin/utente_elimina.html', utente=utente,
                           conteggi=conteggi_riferimenti(get_db(), id))


@admin_bp.route('/utenti/<int:id>/elimina', methods=['POST'])
@admin_required
def utente_elimina(id):
    from utente_service import cancella_utente

    utente, rifiuto = _utente_cancellabile(id)
    if rifiuto:
        flash(rifiuto, 'danger')
        return redirect(url_for('admin.utenti'))

    db = get_db()
    try:
        esito = cancella_utente(db, id)
        conteggi = ', '.join(f"{n} {t}" for t, n in sorted(esito['conteggi'].items()) if n)
        # Dentro la transazione e prima del commit, con struttura_id: e' la
        # lezione della 2.6.1, dove la voce nasceva fuori dal try e con
        # struttura_id nullo, quindi invisibile proprio a chi doveva leggerla.
        log_attivita(
            g.user['id'], 'eliminazione', 'utenti', id,
            f"Utente eliminato: {esito['nome']} {esito['cognome']} <{esito['email']}>, "
            f"ruolo {esito['ruolo']}. Righe che portano il suo nome: "
            f"{conteggi or 'nessuna'}",
            request.remote_addr, esito['struttura_id'])
        # Il punto di non ritorno vero e' la chiamata sopra, non questa riga:
        # log_attivita passa da models.execute(), che fa gia' db.commit() su
        # questa stessa connessione (g.db), rendendo durevoli sia la voce di
        # log sia l'UPDATE di cancella_utente() fatto poco sopra. Il commit
        # qui sotto resta comunque come rete di sicurezza — se domani
        # log_attivita smettesse di commitare da sola, l'ordine "registra
        # prima, commit dopo" continuerebbe a valere — ma non e' lui il
        # varco: e' per questo che il ramo except sotto non puo' assumere
        # che un'eccezione qui significhi "non e' successo nulla".
        db.commit()
    except Exception as e:
        current_app.logger.error(f'Cancellazione utente {id} fallita: {e}', exc_info=True)
        # log_attivita puo' aver gia' reso durevole la cancellazione (vedi
        # sopra) prima che il guasto avvenisse: db.rollback() non annulla
        # cio' che e' gia' stato committato da un'altra chiamata a commit
        # sulla stessa connessione. Bisogna quindi controllare lo stato
        # reale della riga, non presumerlo dal fatto che sia arrivata
        # un'eccezione, prima di scegliere il messaggio per l'operatore.
        db.rollback()
        riga = query_one("SELECT eliminato_il FROM utenti WHERE id = ?", (id,))
        if riga and riga['eliminato_il'] is not None:
            flash("L'utente risulta gia' cancellato: la registrazione era "
                  "andata a buon fine, ma subito dopo qualcosa e' fallito. "
                  "Controlla il log applicativo per i dettagli.", 'warning')
        else:
            flash("Cancellazione fallita, nulla e' stato modificato. Controlla il log.", 'danger')
        return redirect(url_for('admin.utenti'))

    flash(f"Utente {esito['nome']} {esito['cognome']} eliminato. "
          f"L'indirizzo {esito['email']} e' di nuovo utilizzabile.", 'success')
    return redirect(url_for('admin.utenti'))


# ============================================================================
# DIVISION MANAGEMENT
# ============================================================================


def _get_struttura_divisioni_admin():
    """Restituisce la struttura corrente nel pannello divisioni."""
    if g.user['ruolo'] == 'superadmin':
        return request.args.get('struttura_id', type=int)
    # admin e tecnico: usa la struttura attiva (dalla sessione per tecnico)
    return getattr(g, 'struttura_id', None) or _get_mia_struttura_id()


def _get_divisione_in_scope(divisione_id):
    """Carica una divisione verificando lo scope dell'utente corrente."""
    if g.user['ruolo'] == 'superadmin':
        return query_one(
            """SELECT d.*, s.nome AS struttura_nome
               FROM divisioni d
               LEFT JOIN strutture s ON s.id = d.struttura_id
               WHERE d.id = ?""",
            (divisione_id,)
        )

    struttura_id = getattr(g, 'struttura_id', None) or _get_mia_struttura_id()
    return query_one(
        """SELECT d.*, s.nome AS struttura_nome
           FROM divisioni d
           LEFT JOIN strutture s ON s.id = d.struttura_id
           WHERE d.id = ? AND d.struttura_id = ?""",
        (divisione_id, struttura_id)
    )


@admin_bp.route('/divisioni')
@tecnico_o_admin_required
def divisioni():
    """List all divisions scoped by structure."""
    is_superadmin = g.user['ruolo'] == 'superadmin'
    struttura_id = _get_struttura_divisioni_admin()

    if is_superadmin:
        strutture = query_all(
            "SELECT id, nome FROM strutture WHERE attiva = 1 ORDER BY nome"
        )
        params = []
        where = ""
        if struttura_id:
            where = "WHERE d.struttura_id = ?"
            params = [struttura_id]
    else:
        struttura_id = getattr(g, 'struttura_id', None) or _get_mia_struttura_id()
        strutture = []
        where = "WHERE d.struttura_id = ?"
        params = [struttura_id]

    divs = query_all(
        f"""SELECT d.*, s.nome as struttura_nome,
                  (SELECT COUNT(*) FROM apparecchi a WHERE a.divisione_id = d.id AND a.stato != 'dismesso') as num_apparecchi,
                  (SELECT COUNT(*) FROM utenti_divisioni ud WHERE ud.divisione_id = d.id) as num_utenti
           FROM divisioni d
           LEFT JOIN strutture s ON s.id = d.struttura_id
           {where}
           ORDER BY s.nome, d.nome""",
        params
    )
    return render_template(
        'admin/divisioni.html',
        divisioni=divs,
        strutture=strutture,
        struttura_id_attiva=struttura_id,
        is_superadmin=is_superadmin,
    )


@admin_bp.route('/divisioni/nuova', methods=['POST'])
@tecnico_o_admin_required
def divisione_nuova():
    """Create a new division in the selected structure."""
    from strutture_bp import _codice_univoco_divisione
    from models import get_db as _get_db

    nome = request.form.get('nome', '').strip()
    colore = request.form.get('colore', '#0ea5e9')
    descrizione = request.form.get('descrizione', '').strip()
    struttura_id = (request.form.get('struttura_id', type=int)
                    if g.user['ruolo'] == 'superadmin'
                    else getattr(g, 'struttura_id', None) or _get_mia_struttura_id())

    if not nome or not struttura_id:
        flash('Nome e struttura sono obbligatori.', 'danger')
        return redirect(url_for('admin.divisioni'))

    try:
        db = _get_db()
        codice = _codice_univoco_divisione(db, nome, struttura_id)
        cursor = execute(
            """INSERT INTO divisioni (nome, codice, colore, descrizione, struttura_id)
               VALUES (?, ?, ?, ?, ?)""",
            (nome, codice, colore, descrizione or None, struttura_id)
        )
        log_attivita(g.user['id'], 'creazione', 'divisioni', cursor.lastrowid,
                     f"Creata divisione: {nome}", request.remote_addr,
                     struttura_id=struttura_id)
        flash(f'Divisione "{nome}" creata.', 'success')
    except Exception:
        flash('Errore: nome già esistente nella struttura selezionata.', 'danger')

    if g.user['ruolo'] == 'superadmin':
        return redirect(url_for('admin.divisioni', struttura_id=struttura_id))
    return redirect(url_for('admin.divisioni'))


@admin_bp.route('/divisioni/<int:id>/modifica', methods=['POST'])
@tecnico_o_admin_required
def divisione_modifica(id):
    """Edit a division within the current scope."""
    div = _get_divisione_in_scope(id)
    if not div:
        flash('Divisione non trovata.', 'danger')
        return redirect(url_for('admin.divisioni'))

    nome = request.form.get('nome', '').strip()
    colore = request.form.get('colore', '#0ea5e9')
    descrizione = request.form.get('descrizione', '').strip()
    target_struttura_id = (request.form.get('struttura_id', type=int)
                           if g.user['ruolo'] == 'superadmin'
                           else div['struttura_id'])

    if not nome or not target_struttura_id:
        flash('Nome e struttura sono obbligatori.', 'danger')
        return redirect(url_for('admin.divisioni'))

    try:
        if g.user['ruolo'] == 'superadmin' and target_struttura_id != div['struttura_id']:
            target = query_one(
                "SELECT id FROM strutture WHERE id = ? AND attiva = 1",
                (target_struttura_id,)
            )
            if not target:
                flash('Struttura di destinazione non valida.', 'danger')
                return redirect(url_for('admin.divisioni', struttura_id=div['struttura_id']))

            link_app = query_one(
                "SELECT COUNT(*) AS cnt FROM apparecchi WHERE divisione_id = ?",
                (id,)
            )
            link_utenti = query_one(
                "SELECT COUNT(*) AS cnt FROM utenti_divisioni WHERE divisione_id = ?",
                (id,)
            )
            if (link_app and link_app['cnt']) or (link_utenti and link_utenti['cnt']):
                flash('Non puoi spostare la divisione su un\'altra struttura finché ha apparecchi o utenti associati.', 'danger')
                return redirect(url_for('admin.divisioni', struttura_id=div['struttura_id']))

        execute(
            """UPDATE divisioni SET nome=?, colore=?, descrizione=?, struttura_id=?,
                      updated_at=datetime('now')
               WHERE id=? AND struttura_id=?""",
            (nome, colore, descrizione or None, target_struttura_id, id, div['struttura_id'])
        )
        log_attivita(g.user['id'], 'modifica', 'divisioni', id,
                     f"Modificata divisione: {nome}", request.remote_addr,
                     struttura_id=target_struttura_id)
        flash(f'Divisione "{nome}" aggiornata.', 'success')
    except Exception:
        flash('Errore: nome già esistente nella struttura selezionata.', 'danger')

    if g.user['ruolo'] == 'superadmin':
        return redirect(url_for('admin.divisioni', struttura_id=target_struttura_id))
    return redirect(url_for('admin.divisioni'))


@admin_bp.route('/divisioni/<int:id>/toggle', methods=['POST'])
@tecnico_o_admin_required
def divisione_toggle(id):
    """Toggle division active/inactive within the current scope."""
    div = _get_divisione_in_scope(id)
    if not div:
        flash('Divisione non trovata.', 'danger')
        return redirect(url_for('admin.divisioni'))

    new_status = 0 if div['attiva'] else 1
    execute(
        "UPDATE divisioni SET attiva = ?, updated_at = datetime('now') WHERE id = ? AND struttura_id = ?",
        (new_status, id, div['struttura_id'])
    )

    action = 'attivata' if new_status else 'disattivata'
    flash(f"Divisione \"{div['nome']}\" {action}.", 'success')
    if g.user['ruolo'] == 'superadmin':
        return redirect(url_for('admin.divisioni', struttura_id=div['struttura_id']))
    return redirect(url_for('admin.divisioni'))


# ============================================================================
# CONFIGURATION
# ============================================================================

@admin_bp.route('/configurazione', methods=['GET', 'POST'])
@operazione_globale_required
def configurazione():
    """Edit application configuration."""
    from app import load_config, save_config

    config = load_config()

    if request.method == 'POST':
        # Update config from form
        config['app_name'] = request.form.get('app_name', 'MedInventory').strip()
        config['organization'] = request.form.get('organization', 'Studio Bergamaschi').strip()

        port = request.form.get('port', '5000').strip()
        try:
            config['port'] = int(port)
        except ValueError:
            pass

        lifetime = request.form.get('session_lifetime_hours', '8').strip()
        try:
            config['session_lifetime_hours'] = int(lifetime)
        except ValueError:
            pass

        interval = request.form.get('email_check_interval_minutes', '15').strip()
        try:
            config['email_check_interval_minutes'] = int(interval)
        except ValueError:
            pass

        retention = request.form.get('backup_retention', '4').strip()
        try:
            config['backup_retention'] = int(retention)
        except ValueError:
            pass

        # IMAP email monitoring
        config['imap_enabled'] = bool(request.form.get('imap_enabled'))
        config['imap_account'] = request.form.get('imap_account', '').strip()
        imap_password = request.form.get('imap_password', '').strip()
        if imap_password and imap_password != '••••••••':
            config['imap_password'] = imap_password
        config['imap_server'] = request.form.get('imap_server', '').strip()
        imap_port = request.form.get('imap_port', '993').strip()
        try:
            config['imap_port'] = int(imap_port)
        except ValueError:
            pass
        config['imap_ssl'] = bool(request.form.get('imap_ssl'))

        # Alert email / SMTP
        config['alert_email_enabled'] = bool(request.form.get('alert_email_enabled'))
        config['alert_email_to'] = request.form.get('alert_email_to', '').strip()
        config['smtp_host'] = request.form.get('smtp_host', '').strip()
        smtp_port = request.form.get('smtp_port', '587').strip()
        try:
            config['smtp_port'] = int(smtp_port)
        except ValueError:
            pass
        config['smtp_user'] = request.form.get('smtp_user', '').strip()
        smtp_password = request.form.get('smtp_password', '').strip()
        if smtp_password and smtp_password != '••••••••':
            config['smtp_password'] = smtp_password
        config['smtp_use_tls'] = bool(request.form.get('smtp_use_tls'))

        save_config(config)

        log_attivita(g.user['id'], 'modifica', 'configurazione', None,
                     'Configurazione aggiornata', request.remote_addr)

        flash('Configurazione salvata. Alcune modifiche richiedono il riavvio.', 'success')
        return redirect(url_for('admin.configurazione'))

    # Mask sensitive fields for display
    display_config = dict(config)
    display_config['imap_password_set'] = bool(display_config.get('imap_password'))
    display_config['smtp_password_set'] = bool(display_config.get('smtp_password'))
    for _k in ('default_anthropic_api_key', 'default_gemini_api_key', 'default_openai_api_key'):
        display_config[_k + '_set'] = bool(display_config.get(_k))

    return render_template('admin/configurazione.html', config=display_config,
                           ai_providers=AI_PROVIDERS)


@admin_bp.route('/configurazione/test-ai', methods=['POST'])
@operazione_globale_required
def test_default_ai():
    """Test the global default AI configuration. Saves AI fields and returns JSON with model list."""
    import httpx
    from app import load_config, save_config
    from strutture_bp import (_fetch_anthropic_models, _fetch_gemini_models,
                              _fetch_openai_models, _fetch_local_models)

    data = request.json or {}
    provider = (data.get('provider') or '').strip()

    valid_providers = [p[0] for p in AI_PROVIDERS]
    if provider not in valid_providers:
        return jsonify({'ok': False, 'message': f'Provider non valido: {provider}', 'models': []}), 400

    config = load_config()

    # Salva provider
    config['default_ai_provider'] = provider

    # Modelli e URL locale
    for cfg_key, payload_field in (
        ('default_ai_import_model',   'ai_import_model'),
        ('default_ai_email_model',    'ai_email_model'),
        ('default_ai_local_base_url', 'local_base_url'),
        ('default_ai_local_model',    'local_model'),
    ):
        val = (data.get(payload_field) or '').strip()
        if val:
            config[cfg_key] = val
        else:
            config.pop(cfg_key, None)

    # Chiavi API (solo se fornite — non sovrascrivere se vuote)
    for cfg_key, payload_field in (
        ('default_anthropic_api_key', 'api_key'),
        ('default_gemini_api_key',    'gemini_api_key'),
        ('default_openai_api_key',    'openai_api_key'),
    ):
        val = (data.get(payload_field) or '').strip()
        if val:
            config[cfg_key] = val

    save_config(config)

    # Recupera chiavi attive (appena salvate o già presenti)
    anthropic_key = config.get('default_anthropic_api_key', '')
    gemini_key    = config.get('default_gemini_api_key', '')
    openai_key    = config.get('default_openai_api_key', '')
    base_url      = config.get('default_ai_local_base_url', '')

    try:
        if provider == 'anthropic':
            if not anthropic_key:
                return jsonify({'ok': False, 'message': 'Chiave API Anthropic non configurata.', 'models': []})
            models = _fetch_anthropic_models(anthropic_key)
        elif provider == 'gemini':
            if not gemini_key:
                return jsonify({'ok': False, 'message': 'Chiave API Google Gemini non configurata.', 'models': []})
            models = _fetch_gemini_models(gemini_key)
        elif provider == 'openai':
            if not openai_key:
                return jsonify({'ok': False, 'message': 'Chiave API OpenAI non configurata.', 'models': []})
            models = _fetch_openai_models(openai_key)
        else:
            if not base_url:
                return jsonify({'ok': False, 'message': 'URL server AI non configurato.', 'models': []})
            models = _fetch_local_models(base_url)

        log_attivita(g.user['id'], 'modifica', 'configurazione', None,
                     f'Test AI default {provider}: OK, {len(models)} modelli', request.remote_addr)
        return jsonify({
            'ok': True,
            'models': models,
            'message': f'Connessione OK — {len(models)} modelli disponibili',
        })

    except httpx.HTTPStatusError as e:
        return jsonify({'ok': False, 'models': [],
                        'message': f'Errore HTTP {e.response.status_code}: chiave API non valida o accesso negato.'})
    except Exception as e:
        return jsonify({'ok': False, 'models': [], 'message': f'Errore: {str(e)[:200]}'})


# ============================================================================
# EMAIL CONFIG
# ============================================================================

@admin_bp.route('/email-config')
@operazione_globale_required
def email_config():
    """Redirect to unified configurazione page (IMAP config moved there)."""
    return redirect(url_for('admin.configurazione') + '#email-imap')


@admin_bp.route('/email-config/nuova', methods=['POST'])
@operazione_globale_required
def email_config_nuova():
    """Create email config for a division."""
    divisione_id = request.form.get('divisione_id')
    email_account = request.form.get('email_account', '').strip()
    email_password = request.form.get('email_password', '').strip()
    imap_server = request.form.get('imap_server', '').strip()
    imap_port = request.form.get('imap_port', '993').strip()

    if not email_account or not imap_server:
        flash('Account email e server IMAP sono obbligatori.', 'danger')
        return redirect(url_for('admin.email_config'))

    # Encrypt password
    from email_monitor import get_fernet
    config = current_app.config['APP_CONFIG']
    f = get_fernet(config)
    encrypted_password = f.encrypt(email_password.encode()).decode()

    try:
        execute(
            """INSERT INTO email_config
               (divisione_id, email_account, email_password_encrypted, imap_server, imap_port)
               VALUES (?, ?, ?, ?, ?)""",
            (int(divisione_id) if divisione_id else None,
             email_account, encrypted_password, imap_server, int(imap_port))
        )
        flash('Configurazione email creata.', 'success')
    except Exception as e:
        flash(f'Errore nella creazione: {str(e)}', 'danger')

    return redirect(url_for('admin.email_config'))


@admin_bp.route('/email-config/<int:id>/toggle', methods=['POST'])
@operazione_globale_required
def email_config_toggle(id):
    """Toggle email config active/inactive."""
    ec = query_one("SELECT * FROM email_config WHERE id = ?", (id,))
    if not ec:
        flash('Configurazione non trovata.', 'danger')
        return redirect(url_for('admin.email_config'))

    new_status = 0 if ec['attivo'] else 1
    execute("UPDATE email_config SET attivo = ?, updated_at = datetime('now') WHERE id = ?",
            (new_status, id))

    action = 'attivata' if new_status else 'disattivata'
    flash(f"Configurazione email {action}.", 'success')
    return redirect(url_for('admin.email_config'))


# ============================================================================
# BACKUP MANAGEMENT
# ============================================================================

@admin_bp.route('/backup')
@operazione_globale_required
def backup():
    """Backup management page."""
    from backup_service import list_backups

    backups_path = current_app.config['BACKUPS_PATH']
    db_path = current_app.config['DATABASE_PATH']
    config = current_app.config['APP_CONFIG']

    backups = list_backups(backups_path)

    # Database size
    db_size = '?'
    if os.path.exists(db_path):
        size_bytes = os.path.getsize(db_path)
        if size_bytes < 1024:
            db_size = f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            db_size = f"{size_bytes / 1024:.1f} KB"
        else:
            db_size = f"{size_bytes / (1024 * 1024):.1f} MB"

    return render_template('admin/backup.html',
                           backups=backups,
                           db_size=db_size,
                           retention=config.get('backup_retention', 4))


@admin_bp.route('/backup/crea', methods=['POST'])
@operazione_globale_required
def backup_crea():
    """Create a manual backup."""
    from backup_service import create_backup

    try:
        db_path = current_app.config['DATABASE_PATH']
        backups_path = current_app.config['BACKUPS_PATH']
        config = current_app.config['APP_CONFIG']
        retention = config.get('backup_retention', 4)

        result = create_backup(db_path, backups_path, retention)

        log_attivita(g.user['id'], 'backup_creazione', 'backup', None,
                     f"Backup creato: {result['filename']}", request.remote_addr)

        flash(f"Backup creato: {result['filename']} ({result['size'] / 1024:.1f} KB)", 'success')
    except Exception as e:
        flash(f"Errore durante il backup: {str(e)}", 'danger')

    return redirect(url_for('admin.backup'))


@admin_bp.route('/backup/<filename>/scarica')
@operazione_globale_required
def backup_scarica(filename):
    """Download a backup file."""
    from flask import send_from_directory
    from werkzeug.utils import safe_join

    backups_path = current_app.config['BACKUPS_PATH']
    if (not filename.startswith('medinventory_backup_')
            or not filename.endswith('.sqlite')
            or '/' in filename or '\\' in filename or '..' in filename):
        flash('Nome file non valido.', 'danger')
        return redirect(url_for('admin.backup'))

    return send_from_directory(backups_path, filename, as_attachment=True)


@admin_bp.route('/backup/<filename>/ripristina', methods=['POST'])
@operazione_globale_required
def backup_ripristina(filename):
    """Restore database from a backup."""
    from backup_service import create_backup, restore_backup

    backups_path = current_app.config['BACKUPS_PATH']
    db_path = current_app.config['DATABASE_PATH']

    # Strict filename validation: no path separators or traversal sequences
    if (not filename.startswith('medinventory_backup_')
            or not filename.endswith('.sqlite')
            or '/' in filename or '\\' in filename or '..' in filename):
        flash('Nome file non valido.', 'danger')
        return redirect(url_for('admin.backup'))

    # Verify the resolved path stays inside backups_path
    backup_path = os.path.join(backups_path, filename)
    if not os.path.realpath(backup_path).startswith(
            os.path.realpath(backups_path) + os.sep):
        flash('Percorso backup non valido.', 'danger')
        return redirect(url_for('admin.backup'))

    try:
        # Create a safety backup of the current DB before overwriting it
        retention = current_app.config['APP_CONFIG'].get('backup_retention', 4)
        create_backup(db_path, backups_path, retention=retention + 1)

        restore_backup(backup_path, db_path)

        log_attivita(g.user['id'], 'backup_ripristino', 'backup', None,
                     f"Ripristinato da: {filename}", request.remote_addr)

        flash(f"Database ripristinato da {filename}. Riavvia l'applicazione per applicare le modifiche.", 'warning')
    except Exception as e:
        flash(f"Errore durante il ripristino: {str(e)}", 'danger')

    return redirect(url_for('admin.backup'))


@admin_bp.route('/backup/<filename>/elimina', methods=['POST'])
@superadmin_required
def backup_elimina(filename):
    """Delete a backup file."""
    from backup_service import delete_backup

    backups_path = current_app.config['BACKUPS_PATH']

    if (not filename.startswith('medinventory_backup_')
            or not filename.endswith('.sqlite')
            or '/' in filename or '\\' in filename or '..' in filename):
        flash('Nome file non valido.', 'danger')
        return redirect(url_for('admin.backup'))

    backup_path = os.path.join(backups_path, filename)

    try:
        delete_backup(backup_path)
        flash(f"Backup {filename} eliminato.", 'info')
    except Exception as e:
        flash(f"Errore durante l'eliminazione: {str(e)}", 'danger')

    return redirect(url_for('admin.backup'))


@admin_bp.route('/reset-database', methods=['POST'])
@operazione_globale_required
def reset_database():
    """Cancella tutto il database, crea un backup automatico e reinizializza con i dati default."""
    from backup_service import create_backup
    from models import close_db, init_db, apply_schema_updates, execute as db_execute
    from werkzeug.security import generate_password_hash

    db_path = current_app.config['DATABASE_PATH']
    backups_path = current_app.config['BACKUPS_PATH']
    config = current_app.config['APP_CONFIG']
    retention = config.get('backup_retention', 4)

    try:
        # 1. Backup automatico prima di qualsiasi modifica
        backup_result = create_backup(db_path, backups_path, retention)

        # 2. Chiudi la connessione corrente al DB
        close_db()

        # 3. Elimina il file DB e i journal WAL/SHM
        for suffix in ('', '-wal', '-shm'):
            fpath = db_path + suffix
            if os.path.exists(fpath):
                os.remove(fpath)

        # 4. Reinizializza lo schema da schema.sql
        init_db()
        apply_schema_updates()

        # 5. Seed: struttura di default + 2 divisioni + utente admin predefinito
        c = db_execute(
            """INSERT INTO strutture (nome, codice, descrizione, modalita, attiva)
               VALUES (?,?,?,?,?)""",
            ('Struttura Principale', 'DEFAULT',
             'Struttura predefinita (rinominare da Amministrazione → Strutture)',
             'avanzata', 1)
        )
        struttura_id = c.lastrowid

        c = db_execute(
            """INSERT INTO divisioni (nome, codice, colore, descrizione, struttura_id)
               VALUES (?,?,?,?,?)""",
            ('Divisione 1', 'DIV1', '#0ea5e9',
             'Prima divisione (rinominare da pannello admin)', struttura_id)
        )
        div1_id = c.lastrowid
        c = db_execute(
            """INSERT INTO divisioni (nome, codice, colore, descrizione, struttura_id)
               VALUES (?,?,?,?,?)""",
            ('Divisione 2', 'DIV2', '#10b981',
             'Seconda divisione (rinominare da pannello admin)', struttura_id)
        )
        div2_id = c.lastrowid

        password_hash = generate_password_hash('admin123')
        c = db_execute(
            """INSERT INTO utenti (email, password_hash, nome, cognome, ruolo, struttura_id, primo_accesso)
               VALUES (?,?,?,?,?,?,1)""",
            ('admin@medinventory.local', password_hash, 'Amministratore', 'Sistema', 'admin',
             struttura_id)
        )
        admin_id = c.lastrowid

        for div_id in (div1_id, div2_id):
            db_execute(
                "INSERT INTO utenti_divisioni (utente_id, divisione_id, ruolo_divisione) VALUES (?,?,?)",
                (admin_id, div_id, 'admin')
            )

        # 6. Invalida la sessione corrente e reindirizza al login
        flask_session.clear()

        flash(
            f'Database azzerato. Backup salvato: <strong>{backup_result["filename"]}</strong>. '
            f'Accedi con <code>admin@medinventory.local</code> / <code>admin123</code>.',
            'warning'
        )
        return redirect(url_for('auth.login'))

    except Exception as e:
        flash(f'Errore durante il reset del database: {str(e)}', 'danger')
        return redirect(url_for('admin.configurazione'))


@admin_bp.route('/reset-parziale', methods=['POST'])
@operazione_globale_required
def reset_parziale():
    """Cancella tutti i dati di inventario mantenendo utenti e divisioni."""
    from backup_service import create_backup
    from models import get_db

    db_path = current_app.config['DATABASE_PATH']
    backups_path = current_app.config['BACKUPS_PATH']
    config = current_app.config['APP_CONFIG']
    retention = config.get('backup_retention', 4)

    try:
        # 1. Backup automatico prima di qualsiasi modifica
        backup_result = create_backup(db_path, backups_path, retention)

        # 2. Cancella i dati nell'ordine corretto rispettando i vincoli FK
        # Le tabelle figlie vengono svuotate prima delle tabelle padre.
        # I nomi tabella sono costanti hardcoded, non input utente.
        db = get_db()
        # Log prima di cancellare — dopo il DELETE FROM log_attivita la voce sarebbe persa
        log_attivita(g.user['id'], 'reset_parziale', 'database', None,
                     f"Reset parziale DB. Backup: {backup_result['filename']}", request.remote_addr)
        db.execute("DELETE FROM import_preview")
        db.execute("DELETE FROM import_history")
        db.execute("DELETE FROM documenti")
        db.execute("DELETE FROM manutenzioni")
        db.execute("DELETE FROM verifiche")
        db.execute("DELETE FROM apparecchi")
        db.execute("DELETE FROM email_config")
        db.execute("DELETE FROM log_attivita")
        db.commit()

        flash(
            f'Reset parziale completato. Backup salvato: <strong>{backup_result["filename"]}</strong>. '
            f'Utenti e divisioni mantenuti.',
            'success'
        )
        return redirect(url_for('admin.configurazione'))

    except Exception as e:
        flash(f'Errore durante il reset parziale: {str(e)}', 'danger')
        return redirect(url_for('admin.configurazione'))


# ============================================================================
# LOG ATTIVITA
# ============================================================================

@admin_bp.route('/log-attivita')
@admin_required
def log_attivita_view():
    """Visualizza il registro delle attività degli utenti."""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    # Filtri
    utente_id = request.args.get('utente_id', type=int)
    entita = request.args.get('entita', '')
    data_da = request.args.get('data_da', '')
    data_a = request.args.get('data_a', '')

    where = []
    params = []

    # Scope struttura (superadmin vede tutto, admin vede solo la propria)
    if g.user['ruolo'] != 'superadmin':
        struttura_id = g.user.get('struttura_id') or getattr(g, 'struttura_id', None)
        where.append("l.struttura_id = ?")
        params.append(struttura_id)

    if utente_id:
        where.append("l.utente_id = ?")
        params.append(utente_id)
    if entita:
        where.append("l.entita = ?")
        params.append(entita)
    if data_da:
        where.append("l.created_at >= ?")
        params.append(data_da)
    if data_a:
        where.append("l.created_at <= ?")
        params.append(data_a + ' 23:59:59')

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    total = query_one(
        f"SELECT COUNT(*) as c FROM log_attivita l {where_sql}", params
    )['c']

    # Export CSV
    if request.args.get('export') == 'csv':
        import csv, io as _io
        from flask import Response
        output = _io.StringIO()
        writer = csv.DictWriter(output, fieldnames=[
            'created_at', 'utente_nome', 'azione', 'entita', 'entita_id', 'dettagli', 'ip_address'
        ])
        writer.writeheader()
        all_logs = query_all(f"""
            SELECT l.created_at, u.nome || ' ' || u.cognome as utente_nome,
                   l.azione, l.entita, l.entita_id, l.dettagli, l.ip_address
            FROM log_attivita l LEFT JOIN utenti u ON u.id = l.utente_id
            {where_sql} ORDER BY l.created_at DESC
        """, params)
        writer.writerows([dict(r) for r in all_logs])
        return Response(
            output.getvalue(), mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=log_attivita.csv'}
        )

    logs = query_all(f"""
        SELECT l.*, u.nome || ' ' || u.cognome as utente_nome
        FROM log_attivita l
        LEFT JOIN utenti u ON u.id = l.utente_id
        {where_sql}
        ORDER BY l.created_at DESC
        LIMIT ? OFFSET ?
    """, list(params) + [per_page, offset])

    if g.user['ruolo'] != 'superadmin':
        _struttura_id = g.user.get('struttura_id') or getattr(g, 'struttura_id', None)
        utenti = query_all(
            "SELECT id, nome, cognome FROM utenti WHERE attivo=1 AND struttura_id=? ORDER BY cognome",
            (_struttura_id,)
        )
        entita_list_rows = query_all(
            "SELECT DISTINCT entita FROM log_attivita WHERE struttura_id=? ORDER BY entita",
            (_struttura_id,)
        )
    else:
        utenti = query_all("SELECT id, nome, cognome FROM utenti WHERE attivo=1 ORDER BY cognome")
        entita_list_rows = query_all("SELECT DISTINCT entita FROM log_attivita ORDER BY entita")
    entita_list = [r['entita'] for r in entita_list_rows]

    import math
    pages = max(1, math.ceil(total / per_page))
    pagination = {'page': page, 'pages': pages, 'total': total}

    filtri = {'utente_id': utente_id or '', 'entita': entita,
              'data_da': data_da, 'data_a': data_a}

    return render_template('admin/log_attivita.html',
                           logs=logs, utenti=utenti, entita_list=entita_list,
                           pagination=pagination, total=total, filtri=filtri)


# ============================================================================
# SUPERADMIN DASHBOARD
# ============================================================================

@admin_bp.route('/superadmin/dashboard')
@superadmin_required
def superadmin_dashboard():
    """Dashboard aggregata cross-struttura per il superadmin."""
    strutture = query_all("""
        SELECT s.*,
               COUNT(DISTINCT a.id) as tot_apparecchi,
               SUM(CASE WHEN ps.priorita IN ('scaduto','urgente') THEN 1 ELSE 0 END) as scadenze_critiche
        FROM strutture s
        LEFT JOIN apparecchi a ON a.struttura_id = s.id AND a.stato != 'dismesso'
        LEFT JOIN prossime_scadenze ps ON ps.apparecchio_id = a.id
        WHERE s.attiva = 1
        GROUP BY s.id
        ORDER BY scadenze_critiche DESC, s.nome
    """)
    totali = query_one("""
        SELECT COUNT(DISTINCT a.id) as tot_apparecchi,
               SUM(CASE WHEN a.stato='funzionante' THEN 1 ELSE 0 END) as funzionanti,
               SUM(CASE WHEN a.stato='in_manutenzione' THEN 1 ELSE 0 END) as in_manutenzione,
               SUM(CASE WHEN a.stato='da_sostituire' THEN 1 ELSE 0 END) as da_sostituire
        FROM apparecchi a WHERE a.stato != 'dismesso'
    """)
    return render_template('admin/superadmin_dashboard.html',
                           strutture=strutture, totali=totali)


# ============================================================================
# SICUREZZA — Rate limiting / IP bloccati
# ============================================================================

@admin_bp.route('/sicurezza')
@admin_required
def sicurezza():
    """Visualizza e sblocca IP/utenti bloccati dal rate limiting."""
    # Finestra calcolata da SQLite: created_at e' scritta con
    # CURRENT_TIMESTAMP, che e' UTC, e confrontarla con l'ora locale teneva
    # questo elenco sempre vuoto (vedi la nota in auth.login).
    ip_bloccati = query_all("""
        SELECT ip_address, email, COUNT(*) as tentativi, MAX(created_at) as ultimo
        FROM login_attempts
        WHERE esito = 'fallito' AND created_at > datetime('now', '-15 minutes')
        GROUP BY ip_address
        HAVING COUNT(*) > 5
        ORDER BY ultimo DESC
    """)
    return render_template('admin/sicurezza.html', ip_bloccati=ip_bloccati)


@admin_bp.route('/sicurezza/sblocca', methods=['POST'])
@admin_required
def sblocca_ip():
    """Rimuove i tentativi falliti per un IP specifico."""
    import ipaddress
    ip = request.form.get('ip_address', '').strip()
    if not ip:
        return redirect(url_for('admin.sicurezza'))
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        flash('Indirizzo IP non valido.', 'danger')
        return redirect(url_for('admin.sicurezza'))
    execute("DELETE FROM login_attempts WHERE ip_address = ?", (ip,))
    log_attivita(g.user['id'], 'sblocca_ip', 'login_attempts', None,
                 f'IP {ip} sbloccato', ip_address=request.remote_addr,
                 struttura_id=getattr(g, 'struttura_id', None))
    flash(f'IP {ip} sbloccato.', 'success')
    return redirect(url_for('admin.sicurezza'))


# ============================================================================
# GESTIONE TECNICI (solo superadmin)
# ============================================================================

@admin_bp.route('/tecnici')
@superadmin_required
def tecnici():
    """Lista tecnici con strutture assegnate."""
    tecnici_list = query_all("""
        SELECT u.id, u.nome, u.cognome, u.email, u.attivo, u.ultimo_accesso,
               GROUP_CONCAT(s.nome, ', ') as strutture_nomi,
               COUNT(ts.struttura_id) as num_strutture
        FROM utenti u
        LEFT JOIN tecnici_strutture ts ON u.id = ts.tecnico_id
        LEFT JOIN strutture s ON ts.struttura_id = s.id
        WHERE u.ruolo = 'tecnico' AND u.eliminato_il IS NULL
        GROUP BY u.id
        ORDER BY u.cognome, u.nome
    """)
    return render_template('admin/tecnici.html', tecnici=tecnici_list)


@admin_bp.route('/tecnici/nuovo', methods=['GET', 'POST'])
@superadmin_required
def tecnico_nuovo():
    """Crea un nuovo tecnico."""
    strutture = query_all("SELECT id, nome FROM strutture WHERE attiva=1 ORDER BY nome")

    if request.method == 'GET':
        return render_template('admin/tecnico_form.html',
                               tecnico=None, errors={},
                               strutture=strutture, strutture_assegnate=[])

    errors = {}
    nome     = request.form.get('nome', '').strip()
    cognome  = request.form.get('cognome', '').strip()
    email    = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '').strip()
    strutture_sel = request.form.getlist('strutture')

    if not nome:
        errors['nome'] = 'Il nome è obbligatorio.'
    if not cognome:
        errors['cognome'] = 'Il cognome è obbligatorio.'
    if not email:
        errors['email'] = "L'email è obbligatoria."
    elif query_one("SELECT id FROM utenti WHERE email = ?", (email,)):
        errors['email'] = 'Questo indirizzo email è già registrato.'
    if not password or len(password) < 8:
        errors['password'] = 'La password deve essere di almeno 8 caratteri.'

    if errors:
        return render_template('admin/tecnico_form.html',
                               tecnico=None, errors=errors,
                               strutture=strutture, strutture_assegnate=strutture_sel)

    password_hash = generate_password_hash(password)
    try:
        cursor = execute(
            """INSERT INTO utenti (email, password_hash, nome, cognome, ruolo, primo_accesso, struttura_id)
               VALUES (?, ?, ?, ?, 'tecnico', 1, NULL)""",
            (email, password_hash, nome, cognome)
        )
    except Exception as exc:
        import sqlite3 as _sqlite3
        if isinstance(exc, _sqlite3.IntegrityError):
            if 'UNIQUE' in str(exc):
                errors['email'] = 'Questo indirizzo email è già registrato.'
            else:
                errors['_form'] = f'Errore database: {exc}. Riavviare il server per applicare le migrazioni.'
        else:
            errors['_form'] = f'Errore imprevisto: {exc}'
        return render_template('admin/tecnico_form.html',
                               tecnico=None, errors=errors,
                               strutture=strutture, strutture_assegnate=strutture_sel)
    tecnico_id = cursor.lastrowid

    for sid in strutture_sel:
        try:
            execute(
                "INSERT INTO tecnici_strutture (tecnico_id, struttura_id) VALUES (?, ?)",
                (tecnico_id, int(sid))
            )
        except Exception:
            pass

    log_attivita(g.user['id'], 'creazione', 'utenti', tecnico_id,
                 f"Tecnico creato: {nome} {cognome}", request.remote_addr)
    flash(f"Tecnico {nome} {cognome} creato con successo.", 'success')
    return redirect(url_for('admin.tecnici'))


@admin_bp.route('/tecnici/<int:id>/modifica', methods=['GET', 'POST'])
@superadmin_required
def tecnico_modifica(id):
    """Modifica un tecnico e le strutture assegnate."""
    tecnico = query_one("SELECT * FROM utenti WHERE id = ? AND ruolo = 'tecnico'", (id,))
    if not tecnico:
        flash('Tecnico non trovato.', 'danger')
        return redirect(url_for('admin.tecnici'))

    if tecnico['eliminato_il'] is not None:
        flash(MESSAGGI_RIFIUTO['gia_cancellato'], 'danger')
        return redirect(url_for('admin.tecnici'))

    strutture = query_all("SELECT id, nome FROM strutture WHERE attiva=1 ORDER BY nome")
    strutture_assegnate = [
        r['struttura_id'] for r in
        query_all("SELECT struttura_id FROM tecnici_strutture WHERE tecnico_id = ?", (id,))
    ]

    if request.method == 'GET':
        return render_template('admin/tecnico_form.html',
                               tecnico=tecnico, errors={},
                               strutture=strutture, strutture_assegnate=strutture_assegnate)

    errors = {}
    nome          = request.form.get('nome', '').strip()
    cognome       = request.form.get('cognome', '').strip()
    email         = request.form.get('email', '').strip().lower()
    strutture_sel = request.form.getlist('strutture')
    nuova_pw      = request.form.get('password', '').strip()

    if not nome:
        errors['nome'] = 'Il nome è obbligatorio.'
    if not cognome:
        errors['cognome'] = 'Il cognome è obbligatorio.'
    if not email:
        errors['email'] = "L'email è obbligatoria."
    elif query_one("SELECT id FROM utenti WHERE email = ? AND id != ?", (email, id)):
        errors['email'] = 'Email già usata da un altro utente.'
    if nuova_pw and len(nuova_pw) < 8:
        errors['password'] = 'La password deve essere di almeno 8 caratteri.'

    if errors:
        return render_template('admin/tecnico_form.html',
                               tecnico=tecnico, errors=errors,
                               strutture=strutture, strutture_assegnate=strutture_sel)

    if nuova_pw:
        execute(
            """UPDATE utenti SET nome=?, cognome=?, email=?, password_hash=?,
                      updated_at=datetime('now') WHERE id=?""",
            (nome, cognome, email, generate_password_hash(nuova_pw), id)
        )
    else:
        execute(
            "UPDATE utenti SET nome=?, cognome=?, email=?, updated_at=datetime('now') WHERE id=?",
            (nome, cognome, email, id)
        )

    execute("DELETE FROM tecnici_strutture WHERE tecnico_id = ?", (id,))
    for sid in strutture_sel:
        try:
            execute(
                "INSERT INTO tecnici_strutture (tecnico_id, struttura_id) VALUES (?, ?)",
                (id, int(sid))
            )
        except Exception:
            pass

    log_attivita(g.user['id'], 'modifica', 'utenti', id,
                 f"Tecnico modificato: {nome} {cognome}", request.remote_addr)
    flash(f"Tecnico {nome} {cognome} aggiornato.", 'success')
    return redirect(url_for('admin.tecnici'))


@admin_bp.route('/tecnici/<int:id>/elimina', methods=['POST'])
@superadmin_required
def tecnico_elimina(id):
    """Elimina un tecnico sulla stessa primitiva usata per gli altri utenti."""
    tecnico = query_one("SELECT * FROM utenti WHERE id = ? AND ruolo = 'tecnico'", (id,))
    if not tecnico:
        flash('Tecnico non trovato.', 'danger')
        return redirect(url_for('admin.tecnici'))

    from utente_service import cancella_utente, motivo_rifiuto

    motivo = motivo_rifiuto(get_db(), id)
    if motivo:
        flash(MESSAGGI_RIFIUTO[motivo], 'danger')
        return redirect(url_for('admin.tecnici'))

    db = get_db()
    try:
        esito = cancella_utente(db, id)
        log_attivita(g.user['id'], 'eliminazione', 'utenti', id,
                     f"Tecnico eliminato: {esito['nome']} {esito['cognome']} "
                     f"<{esito['email']}>", request.remote_addr)
        db.commit()
    except Exception as e:
        db.rollback()
        current_app.logger.error(f'Cancellazione tecnico {id} fallita: {e}', exc_info=True)
        riga = query_one("SELECT eliminato_il FROM utenti WHERE id = ?", (id,))
        if riga and riga['eliminato_il'] is not None:
            flash("Il tecnico risulta gia' cancellato: la registrazione era "
                  "andata a buon fine, ma subito dopo qualcosa e' fallito. "
                  "Controlla il log applicativo per i dettagli.", 'warning')
        else:
            flash("Cancellazione fallita, nulla e' stato modificato. Controlla il log.", 'danger')
        return redirect(url_for('admin.tecnici'))

    flash(f"Tecnico {esito['nome']} {esito['cognome']} eliminato.", 'success')
    return redirect(url_for('admin.tecnici'))
