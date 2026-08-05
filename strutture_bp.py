"""
MedInventory - Gestione Strutture (superadmin)
"""

import httpx
import os
import re
from datetime import datetime

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, g, current_app, jsonify
)
from werkzeug.utils import secure_filename
from auth import superadmin_required, login_required, tecnico_o_superadmin_required
from models import query_all, query_one, execute, log_attivita, get_db, \
    get_struttura_config_all, set_struttura_config, get_struttura_config, \
    upload_subdir, percorso_logo_struttura
from ai_service import ANTHROPIC_MODELS, GEMINI_MODELS, OPENAI_MODELS, AI_PROVIDERS, AI_PROVIDER_DEFAULTS

strutture_bp = Blueprint('strutture', __name__, url_prefix='/strutture')


# ---------------------------------------------------------------------------
# Helpers codice auto-generazione
# ---------------------------------------------------------------------------

def _codice_base_da_nome(nome):
    """Genera un codice base di 2-5 lettere dalle iniziali del nome."""
    pulito = re.sub(r'[^a-zA-Z0-9\s]', '', nome).upper()
    parole = pulito.split()
    if len(parole) >= 2:
        base = ''.join(p[0] for p in parole if p)[:6]
    elif parole:
        base = parole[0][:6]
    else:
        base = 'X'
    return re.sub(r'[^A-Z0-9]', '', base) or 'X'


def _codice_univoco_struttura(db, nome):
    """Genera un codice univoco per la tabella strutture."""
    base = _codice_base_da_nome(nome)
    existing = {r[0] for r in db.execute("SELECT codice FROM strutture").fetchall()}
    codice = base
    n = 1
    while codice in existing:
        codice = f"{base}{n}"
        n += 1
    return codice


def _codice_univoco_divisione(db, nome, struttura_id, esclude_id=None):
    """Genera un codice univoco per divisioni nella struttura."""
    base = _codice_base_da_nome(nome)
    q = "SELECT codice FROM divisioni WHERE struttura_id=?"
    params = [struttura_id]
    if esclude_id:
        q += " AND id!=?"
        params.append(esclude_id)
    existing = {r[0] for r in db.execute(q, params).fetchall()}
    codice = base
    n = 1
    while codice in existing:
        codice = f"{base}{n}"
        n += 1
    return codice


@strutture_bp.route('/')
@tecnico_o_superadmin_required
def index():
    if g.user['ruolo'] == 'tecnico':
        strutture = query_all("""
            SELECT s.*,
                   COUNT(DISTINCT d.id) as num_divisioni,
                   COUNT(DISTINCT u.id) as num_utenti,
                   COUNT(DISTINCT a.id) as num_apparecchi
            FROM strutture s
            JOIN tecnici_strutture ts ON ts.struttura_id = s.id AND ts.tecnico_id = ?
            LEFT JOIN divisioni d ON d.struttura_id = s.id AND d.attiva = 1
            LEFT JOIN utenti u ON u.struttura_id = s.id AND u.attivo = 1
            LEFT JOIN apparecchi a ON a.struttura_id = s.id AND a.stato != 'dismesso'
            GROUP BY s.id
            ORDER BY s.nome
        """, (g.user['id'],))
    else:
        strutture = query_all("""
            SELECT s.*,
                   COUNT(DISTINCT d.id) as num_divisioni,
                   COUNT(DISTINCT u.id) as num_utenti,
                   COUNT(DISTINCT a.id) as num_apparecchi
            FROM strutture s
            LEFT JOIN divisioni d ON d.struttura_id = s.id AND d.attiva = 1
            LEFT JOIN utenti u ON u.struttura_id = s.id AND u.attivo = 1
            LEFT JOIN apparecchi a ON a.struttura_id = s.id AND a.stato != 'dismesso'
            GROUP BY s.id
            ORDER BY s.nome
        """)
    return render_template('strutture/index.html', strutture=strutture)


_TIPI_STRUTTURA = ('ospedale', 'clinica_privata', 'rsa', 'ambulatorio',
                   'poliambulatorio', 'laboratorio', 'altro')


def _crea_divisione_predefinita(db, struttura_id, nome_struttura):
    """Crea la divisione iniziale della struttura appena registrata."""
    codice = _codice_univoco_divisione(db, nome_struttura, struttura_id)
    cur = db.execute(
        """INSERT INTO divisioni (nome, codice, colore, descrizione, struttura_id)
           VALUES (?, ?, ?, ?, ?)""",
        (
            nome_struttura,
            codice,
            '#0ea5e9',
            'Divisione predefinita creata automaticamente alla creazione della struttura.',
            struttura_id,
        )
    )
    return cur.lastrowid


def _get_divisioni_struttura(struttura_id):
    return query_all(
        "SELECT * FROM divisioni WHERE struttura_id = ? ORDER BY nome",
        (struttura_id,)
    )


def _sync_divisioni_struttura(db, struttura_id, form):
    existing_ids = form.getlist('existing_div_id')
    existing_nomi = form.getlist('existing_div_nome')
    existing_colori = form.getlist('existing_div_colore')
    existing_descrizioni = form.getlist('existing_div_descrizione')
    active_ids = {int(v) for v in form.getlist('existing_div_attiva') if str(v).isdigit()}

    new_nomi_raw = [n.strip() for n in form.getlist('new_div_nome') if n.strip()]

    # Validazione unicità nomi nell'intero batch prima di toccare il DB
    all_nomi_batch = [
        (existing_nomi[idx] if idx < len(existing_nomi) else '').strip()
        for idx, v in enumerate(existing_ids)
        if (existing_nomi[idx] if idx < len(existing_nomi) else '').strip()
    ] + new_nomi_raw
    if len(all_nomi_batch) != len(set(n.lower() for n in all_nomi_batch)):
        raise ValueError('Sono presenti divisioni con lo stesso nome. Ogni divisione deve avere un nome univoco.')

    for idx, div_id_raw in enumerate(existing_ids):
        try:
            div_id = int(div_id_raw)
        except (TypeError, ValueError):
            continue

        nome = (existing_nomi[idx] if idx < len(existing_nomi) else '').strip()
        colore = (existing_colori[idx] if idx < len(existing_colori) else '#0ea5e9').strip() or '#0ea5e9'
        descrizione = (existing_descrizioni[idx] if idx < len(existing_descrizioni) else '').strip() or None
        attiva = 1 if div_id in active_ids else 0

        if not nome:
            raise ValueError('Ogni divisione esistente deve avere un nome.')

        codice = _codice_univoco_divisione(db, nome, struttura_id, esclude_id=div_id)
        cur = db.execute(
            """UPDATE divisioni
               SET nome=?, codice=?, colore=?, descrizione=?, attiva=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND struttura_id=?""",
            (nome, codice, colore, descrizione, attiva, div_id, struttura_id)
        )
        if cur.rowcount == 0:
            raise ValueError('Una delle divisioni selezionate non appartiene alla struttura corrente.')

    new_nomi = form.getlist('new_div_nome')
    new_colori = form.getlist('new_div_colore')
    new_descrizioni = form.getlist('new_div_descrizione')

    for idx, nome_raw in enumerate(new_nomi):
        nome = nome_raw.strip()
        colore = (new_colori[idx] if idx < len(new_colori) else '#0ea5e9').strip() or '#0ea5e9'
        descrizione = (new_descrizioni[idx] if idx < len(new_descrizioni) else '').strip() or None

        if not any([nome, descrizione]):
            continue
        if not nome:
            raise ValueError('Ogni nuova divisione deve avere un nome.')

        codice = _codice_univoco_divisione(db, nome, struttura_id)
        db.execute(
            """INSERT INTO divisioni (nome, codice, colore, descrizione, struttura_id)
               VALUES (?, ?, ?, ?, ?)""",
            (nome, codice, colore, descrizione, struttura_id)
        )


def _leggi_form_struttura(form):
    """Estrae e normalizza tutti i campi struttura dal form POST."""
    modalita = 'avanzata'  # tutte le strutture sono sempre avanzate
    tipo = form.get('tipo', 'altro')
    if tipo not in _TIPI_STRUTTURA:
        tipo = 'altro'
    return {
        'nome':               form.get('nome', '').strip(),
        'descrizione':        form.get('descrizione', '').strip() or None,
        'tipo':               tipo,
        'indirizzo':          form.get('indirizzo', '').strip() or None,
        'telefono':           form.get('telefono', '').strip() or None,
        'email_notifiche':    form.get('email_notifiche', '').strip() or None,
        'pec':                form.get('pec', '').strip() or None,
        'responsabile':       form.get('responsabile', '').strip() or None,
        'email_responsabile': form.get('email_responsabile', '').strip() or None,
        'codice_fiscale':     form.get('codice_fiscale', '').strip() or None,
        'partita_iva':        form.get('partita_iva', '').strip() or None,
        'data_attivazione':   form.get('data_attivazione', '').strip() or None,
        'scadenza_contratto': form.get('scadenza_contratto', '').strip() or None,
        'note':               form.get('note', '').strip() or None,
        'modalita':           modalita,
    }


@strutture_bp.route('/nuova', methods=['GET', 'POST'])
@tecnico_o_superadmin_required
def nuova():
    if request.method == 'POST':
        dati = _leggi_form_struttura(request.form)
        if not dati['nome']:
            flash('Il nome è obbligatorio.', 'danger')
            return render_template('strutture/form.html', struttura=request.form,
                                   tipi_struttura=_TIPI_STRUTTURA, divisioni=[])
        try:
            db = get_db()
            codice = _codice_univoco_struttura(db, dati['nome'])
            cur = db.execute(
                """INSERT INTO strutture
                   (nome, codice, descrizione, tipo, indirizzo, telefono,
                    email_notifiche, pec, responsabile, email_responsabile,
                    codice_fiscale, partita_iva, data_attivazione,
                    scadenza_contratto, note, modalita)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (dati['nome'], codice, dati['descrizione'], dati['tipo'],
                 dati['indirizzo'], dati['telefono'], dati['email_notifiche'],
                 dati['pec'], dati['responsabile'], dati['email_responsabile'],
                 dati['codice_fiscale'], dati['partita_iva'],
                 dati['data_attivazione'], dati['scadenza_contratto'],
                 dati['note'], dati['modalita'])
            )
            struttura_id = cur.lastrowid
            divisione_id = _crea_divisione_predefinita(db, struttura_id, dati['nome'])
            # Se tecnico, auto-assegna alla nuova struttura
            if g.user['ruolo'] == 'tecnico':
                db.execute(
                    "INSERT OR IGNORE INTO tecnici_strutture (tecnico_id, struttura_id) VALUES (?, ?)",
                    (g.user['id'], struttura_id)
                )
            db.commit()
            # Seed AI config di default dalla config globale
            from app import load_config as _load_config
            _cfg = _load_config()
            _ai_defaults_map = {
                'default_ai_provider':       'ai_provider',
                'default_anthropic_api_key': 'anthropic_api_key',
                'default_gemini_api_key':    'gemini_api_key',
                'default_openai_api_key':    'openai_api_key',
                'default_ai_local_base_url': 'ai_local_base_url',
                'default_ai_local_model':    'ai_local_model',
                'default_ai_import_model':   'ai_import_model',
                'default_ai_email_model':    'ai_email_model',
            }
            for _cfg_key, _struttura_key in _ai_defaults_map.items():
                _val = _cfg.get(_cfg_key)
                if _val:
                    set_struttura_config(struttura_id, _struttura_key, _val)
            log_attivita(g.user['id'], 'crea', 'struttura', struttura_id,
                         f'Struttura "{dati["nome"]}" creata')
            log_attivita(g.user['id'], 'creazione', 'divisioni', divisione_id,
                         f'Divisione predefinita creata per struttura "{dati["nome"]}"',
                         struttura_id=struttura_id)
            flash(f'Struttura "{dati["nome"]}" creata con successo.', 'success')
            return redirect(url_for('strutture.index'))
        except Exception as e:
            try:
                get_db().rollback()
            except Exception:
                pass
            current_app.logger.error(f'Errore creazione struttura: {e}')
            flash('Errore durante il salvataggio. Riprovare.', 'danger')
        return render_template('strutture/form.html', struttura=request.form,
                               tipi_struttura=_TIPI_STRUTTURA, divisioni=[])
    return render_template('strutture/form.html', struttura=None,
                           tipi_struttura=_TIPI_STRUTTURA, divisioni=[])


@strutture_bp.route('/<int:struttura_id>/modifica', methods=['GET', 'POST'])
@superadmin_required
def modifica(struttura_id):
    struttura = query_one("SELECT * FROM strutture WHERE id = ?", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index'))

    divisioni = _get_divisioni_struttura(struttura_id)

    if request.method == 'POST':
        dati = _leggi_form_struttura(request.form)
        attiva = 1 if request.form.get('attiva') else 0

        if not dati['nome']:
            flash('Il nome è obbligatorio.', 'danger')
            return render_template('strutture/form.html',
                                   struttura=dict(struttura) | dict(request.form),
                                   tipi_struttura=_TIPI_STRUTTURA,
                                   divisioni=divisioni)

        try:
            db = get_db()
            db.execute(
                """UPDATE strutture SET
                   nome=?, descrizione=?, tipo=?, indirizzo=?,
                   telefono=?, email_notifiche=?, pec=?, responsabile=?,
                   email_responsabile=?, codice_fiscale=?, partita_iva=?,
                   data_attivazione=?, scadenza_contratto=?, note=?,
                   modalita=?, attiva=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (dati['nome'], dati['descrizione'], dati['tipo'],
                 dati['indirizzo'], dati['telefono'], dati['email_notifiche'],
                 dati['pec'], dati['responsabile'], dati['email_responsabile'],
                 dati['codice_fiscale'], dati['partita_iva'],
                 dati['data_attivazione'], dati['scadenza_contratto'],
                 dati['note'], dati['modalita'], attiva, struttura_id)
            )
            _sync_divisioni_struttura(db, struttura_id, request.form)
            # Cascade quando la struttura viene disattivata
            if not attiva:
                # Invalida sessioni degli utenti della struttura
                db.execute(
                    "DELETE FROM sessioni WHERE utente_id IN "
                    "(SELECT id FROM utenti WHERE struttura_id=?)",
                    (struttura_id,)
                )
                # Disabilita email_config delle divisioni della struttura
                db.execute(
                    "UPDATE email_config SET attivo=0 WHERE divisione_id IN "
                    "(SELECT id FROM divisioni WHERE struttura_id=?)",
                    (struttura_id,)
                )
            db.commit()
            log_attivita(g.user['id'], 'modifica', 'struttura', struttura_id,
                         f'Struttura "{dati["nome"]}" modificata')
            flash('Struttura aggiornata.', 'success')
            return redirect(url_for('strutture.index'))
        except ValueError as e:
            try:
                db.rollback()
            except Exception:
                pass
            flash(str(e), 'danger')
        except Exception as e:
            try:
                get_db().rollback()
            except Exception:
                pass
            current_app.logger.error(f'Errore modifica struttura {struttura_id}: {e}')
            flash('Errore durante il salvataggio. Riprovare.', 'danger')
        struttura = dict(struttura) | dict(request.form)
        divisioni = _get_divisioni_struttura(struttura_id)

    return render_template('strutture/form.html', struttura=struttura,
                           tipi_struttura=_TIPI_STRUTTURA, divisioni=divisioni)


@strutture_bp.route('/<int:struttura_id>')
@superadmin_required
def scheda(struttura_id):
    from struttura_service import contenuto_struttura

    struttura = query_one("SELECT * FROM strutture WHERE id = ?", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index'))

    single = current_app.config.get('APP_CONFIG', {}).get('single_struttura', False)
    contenuto = contenuto_struttura(get_db(), struttura_id,
                                    current_app.config['UPLOADS_PATH'],
                                    single_struttura=single)
    divisioni = _get_divisioni_struttura(struttura_id)
    utenti = query_all(
        "SELECT nome, cognome, email, ruolo, attivo FROM utenti "
        "WHERE struttura_id = ? AND eliminato_il IS NULL ORDER BY cognome, nome", (struttura_id,))
    # eliminato_il IS NULL qui e' oggi IRRAGGIUNGIBILE, ma per due ragioni
    # insieme, non una sola — e la premessa "il JOIN non lo trova comunque"
    # da sola era gia' stata smentita una volta: tecnico_modifica ricreava
    # le righe di tecnici_strutture su un tecnico gia' cancellato (bug
    # corretto in questo stesso giro), e da quel momento il JOIN sotto lo
    # trovava eccome, filtro o non filtro. E' la combinazione delle due cose
    # a tenere il filtro non portante:
    #   1. utente_service.cancella_utente toglie le righe di
    #      tecnici_strutture del tecnico che cancella;
    #   2. tecnico_modifica rifiuta (piu' sotto) un tecnico con eliminato_il
    #      valorizzato, quindi non puo' piu' ricrearle.
    # Se una delle due cambiasse — si decidesse di conservare lo storico
    # delle assegnazioni invece di cancellarle, o tecnico_modifica smettesse
    # di rifiutare un tecnico cancellato — questo filtro tornerebbe
    # portante. Per questo nessun test lo copre direttamente, non per
    # dimenticanza: vedi test_un_tecnico_cancellato_non_compare_nella_scheda_della_struttura
    # in tests/test_utenti_routes.py.
    tecnici = query_all(
        "SELECT u.nome, u.cognome, u.email FROM utenti u "
        "JOIN tecnici_strutture ts ON ts.tecnico_id = u.id "
        "WHERE ts.struttura_id = ? AND u.eliminato_il IS NULL ORDER BY u.cognome, u.nome", (struttura_id,))

    return render_template('strutture/scheda.html', struttura=struttura,
                           contenuto=contenuto, divisioni=divisioni,
                           utenti=utenti, tecnici=tecnici,
                           single_struttura=single)


@strutture_bp.route('/<int:struttura_id>/riattiva', methods=['POST'])
@superadmin_required
def riattiva(struttura_id):
    struttura = query_one("SELECT id, nome FROM strutture WHERE id = ?", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index'))

    execute("UPDATE strutture SET attiva = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (struttura_id,))
    log_attivita(g.user['id'], 'riattivazione', 'strutture', struttura_id,
                 f'Struttura "{struttura["nome"]}" riattivata', request.remote_addr)
    flash(f'Struttura "{struttura["nome"]}" riattivata.', 'success')
    return redirect(url_for('strutture.index'))


@strutture_bp.route('/<int:struttura_id>/esporta', methods=['POST'])
@superadmin_required
def esporta(struttura_id):
    from struttura_service import esporta_struttura, InstallazioneNonMigrataError

    struttura = query_one("SELECT id, nome FROM strutture WHERE id = ?", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index'))

    single = current_app.config.get('APP_CONFIG', {}).get('single_struttura', False)
    try:
        destinazione = esporta_struttura(
            current_app.config['DATABASE_PATH'],
            current_app.config['UPLOADS_PATH'],
            struttura_id,
            os.path.join(current_app.config['BACKUPS_PATH'], 'strutture'),
            single_struttura=single,
        )
    except InstallazioneNonMigrataError as e:
        # Non e' un errore imprevisto: e' esporta_struttura che si rifiuta
        # apposta di produrre un archivio incompleto. L'operatore deve sapere
        # cosa sta succedendo e cosa fare, non solo "controlla il log".
        current_app.logger.warning(
            f'Esportazione struttura {struttura_id} rifiutata: {e}')
        flash(str(e), 'danger')
        return redirect(url_for('strutture.scheda', struttura_id=struttura_id))
    except Exception as e:
        current_app.logger.error(f'Esportazione struttura {struttura_id} fallita: {e}',
                                 exc_info=True)
        flash('Esportazione fallita. Controlla il log.', 'danger')
        return redirect(url_for('strutture.scheda', struttura_id=struttura_id))

    log_attivita(g.user['id'], 'esportazione', 'strutture', struttura_id,
                 f'Struttura "{struttura["nome"]}" esportata in {destinazione}',
                 request.remote_addr)
    flash(f'Archivio creato in {destinazione}', 'success')
    return redirect(url_for('strutture.scheda', struttura_id=struttura_id))


@strutture_bp.route('/<int:struttura_id>/elimina', methods=['GET'])
@superadmin_required
def conferma_eliminazione(struttura_id):
    from struttura_service import (contenuto_struttura, anteprima_cancellazione_file,
                                   percorsi_installazione_non_migrata)

    struttura = query_one("SELECT * FROM strutture WHERE id = ?", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index'))
    if struttura['attiva']:
        flash('Per eliminare la struttura, disattivala prima dalla modifica.', 'warning')
        return redirect(url_for('strutture.scheda', struttura_id=struttura_id))

    single = current_app.config.get('APP_CONFIG', {}).get('single_struttura', False)
    # scheda.html nasconde il pulsante Elimina in modalita' single (non
    # esiste un sottoalbero uploads/ da isolare per struttura): la rotta deve
    # rifiutare allo stesso modo, non solo il template nascondere il link.
    if single:
        flash("L'eliminazione di una struttura non e' disponibile in modalita' "
             "single-struttura.", 'warning')
        return redirect(url_for('strutture.scheda', struttura_id=struttura_id))

    non_migrati = percorsi_installazione_non_migrata(
        get_db(), current_app.config['UPLOADS_PATH'], struttura_id)
    if non_migrati:
        # Mostrare comunque la pagina significherebbe promettere un conteggio
        # di file che l'esportazione (chiamata dalla POST che segue) non
        # potrebbe onorare: si rifiuta qui, PRIMA di far scrivere all'operatore
        # il nome di conferma di una cancellazione che non puo' completarsi
        # in sicurezza.
        flash(
            f"Impossibile procedere: {len(non_migrati)} allegati di questa struttura "
            "esistono su disco ma fuori dallo schema multi-struttura (probabile "
            "installazione promossa da single a multi con toggle_modalita.py, che "
            "non sposta i file). Sposta manualmente questi allegati sotto "
            f"uploads/strutture/{struttura_id}/<tipo>/ prima di esportare o cancellare "
            "questa struttura.", 'danger')
        return redirect(url_for('strutture.scheda', struttura_id=struttura_id))

    contenuto = contenuto_struttura(get_db(), struttura_id,
                                    current_app.config['UPLOADS_PATH'],
                                    single_struttura=single)
    # contenuto['file'] conta l'intero sottoalbero su disco in modalita'
    # multi (orfani compresi): la cancellazione invece passa dalle righe
    # (_percorsi_allegati). La pagina deve mostrare quanti file verranno
    # DAVVERO cancellati, non il totale su disco: altrimenti promette la
    # cancellazione di file che sopravvivono.
    anteprima = anteprima_cancellazione_file(get_db(), struttura_id,
                                             current_app.config['UPLOADS_PATH'],
                                             single_struttura=single)
    return render_template('strutture/elimina.html',
                           struttura=struttura, contenuto=contenuto,
                           anteprima=anteprima)


@strutture_bp.route('/<int:struttura_id>/elimina', methods=['POST'])
@superadmin_required
def elimina(struttura_id):
    from struttura_service import elimina_struttura, InstallazioneNonMigrataError

    struttura = query_one("SELECT * FROM strutture WHERE id = ?", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index'))
    if struttura['attiva']:
        flash('Per eliminare la struttura, disattivala prima dalla modifica.', 'warning')
        return redirect(url_for('strutture.scheda', struttura_id=struttura_id))

    single = current_app.config.get('APP_CONFIG', {}).get('single_struttura', False)
    # Stessa guardia della GET/scheda.html: in single il pulsante non c'e',
    # la rotta non deve restare raggiungibile lo stesso (POST diretta).
    if single:
        flash("L'eliminazione di una struttura non e' disponibile in modalita' "
             "single-struttura.", 'warning')
        return redirect(url_for('strutture.scheda', struttura_id=struttura_id))

    if request.form.get('conferma_nome', '').strip() != struttura['nome']:
        flash('Il nome scritto non corrisponde: la struttura non e\' stata eliminata.', 'danger')
        return redirect(url_for('strutture.conferma_eliminazione', struttura_id=struttura_id))

    # elimina_struttura apre una propria connessione sullo stesso file per
    # cancellare le righe: commit qui per non lasciare scritture pendenti
    # sulla connessione della richiesta mentre l'altra e' aperta.
    get_db().commit()

    try:
        esito = elimina_struttura(
            current_app.config['DATABASE_PATH'],
            current_app.config['UPLOADS_PATH'],
            struttura_id,
            os.path.join(current_app.config['BACKUPS_PATH'], 'strutture'),
            single_struttura=single,
        )
    except InstallazioneNonMigrataError as e:
        # elimina_struttura chiama esporta_struttura per prima: questo arriva
        # PRIMA di toccare il database, quindi "nulla e' stato cancellato" e'
        # vero qui, a differenza del ramo sotto (che copre un fallimento a
        # meta' operazione). L'operatore deve sapere cosa fare, non solo che
        # e' fallito.
        current_app.logger.warning(
            f'Eliminazione struttura {struttura_id} rifiutata: {e}')
        flash(str(e), 'danger')
        return redirect(url_for('strutture.scheda', struttura_id=struttura_id))
    except Exception as e:
        current_app.logger.error(f'Eliminazione struttura {struttura_id} fallita: {e}',
                                 exc_info=True)
        # elimina_struttura fa archivio, poi database, poi file: un'eccezione
        # puo' arrivare anche DOPO che il database e' gia' stato modificato
        # (es. un errore imprevisto durante la pulizia dei file, non
        # catturato perche' non e' un OSError). In quel caso la struttura
        # non c'e' piu': dire "nulla e' stato cancellato" sarebbe falso, e
        # l'operazione - irreversibile - deve comunque finire nel registro.
        ancora_presente = query_one("SELECT id FROM strutture WHERE id = ?", (struttura_id,))
        if not ancora_presente:
            log_attivita(
                g.user['id'], 'eliminazione', 'strutture', struttura_id,
                f'Struttura "{struttura["nome"]}" eliminata dal database, ma un errore '
                f'e\' avvenuto dopo (durante l\'archiviazione o la pulizia dei file): {e}. '
                'Controlla il log applicativo.',
                request.remote_addr)
            flash(f'Struttura "{struttura["nome"]}" e\' stata eliminata dal database, ma '
                 'un errore e\' avvenuto durante la pulizia successiva. Controlla il log.',
                 'warning')
            return redirect(url_for('strutture.index'))
        flash('Eliminazione fallita, nulla e\' stato cancellato. Controlla il log.', 'danger')
        return redirect(url_for('strutture.scheda', struttura_id=struttura_id))

    log_attivita(
        g.user['id'], 'eliminazione', 'strutture', struttura_id,
        f'Struttura "{esito["nome"]}" eliminata: {esito["apparecchi"]} apparecchi, '
        f'{esito["manutenzioni"]} manutenzioni, {esito["verifiche"]} verifiche, '
        f'{esito["utenti"]} utenti. Archivio: {esito["archivio"]}. '
        f'File non rimossi: {len(esito["file_non_rimossi"])}.',
        request.remote_addr)

    if esito['file_non_rimossi']:
        current_app.logger.warning(
            f'Eliminazione struttura {struttura_id}: {len(esito["file_non_rimossi"])} '
            f'file non rimossi: {esito["file_non_rimossi"]}')
        flash(
            f'Struttura "{esito["nome"]}" eliminata. Archivio in {esito["archivio"]}. '
            f'Attenzione: {len(esito["file_non_rimossi"])} file allegati non sono stati '
            'rimossi dal disco (dettagli nel log applicativo); pulisci_uploads.py li individuera\'.',
            'warning')
    else:
        flash(f'Struttura "{esito["nome"]}" eliminata. Archivio in {esito["archivio"]}', 'success')
    return redirect(url_for('strutture.index'))


def _fetch_anthropic_models(api_key):
    """Fetch available Anthropic models via /v1/models API. Falls back to hardcoded list on network errors.
    Raises on HTTP errors (invalid key, server error) so the caller can return ok: False."""
    import httpx
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(
                'https://api.anthropic.com/v1/models',
                headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01'}
            )
            r.raise_for_status()
            data = r.json()
            ids = [m['id'] for m in data.get('data', []) if m.get('id')]
            return ids if ids else [m[0] for m in ANTHROPIC_MODELS]
    except httpx.HTTPStatusError:
        raise
    except Exception:
        return [m[0] for m in ANTHROPIC_MODELS]


def _fetch_gemini_models(api_key):
    """Fetch available Gemini models via /v1beta/models API. Falls back to hardcoded list on network errors.
    Raises on HTTP errors (invalid key, server error) so the caller can return ok: False."""
    import httpx
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(
                'https://generativelanguage.googleapis.com/v1beta/models',
                params={'key': api_key}
            )
            r.raise_for_status()
            data = r.json()
            ids = [
                m['name'].split('/')[-1]
                for m in data.get('models', [])
                if 'generateContent' in m.get('supportedGenerationMethods', [])
            ]
            return ids if ids else [m[0] for m in GEMINI_MODELS]
    except httpx.HTTPStatusError:
        raise
    except Exception:
        return [m[0] for m in GEMINI_MODELS]


def _fetch_openai_models(api_key):
    """Fetch available OpenAI models via /v1/models API. Falls back to hardcoded list on network errors.
    Raises on HTTP errors (invalid key, server error) so the caller can return ok: False."""
    import httpx
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(
                'https://api.openai.com/v1/models',
                headers={'Authorization': f'Bearer {api_key}'}
            )
            r.raise_for_status()
            data = r.json()
            ids = [m['id'] for m in data.get('data', []) if m.get('id')]
            gpt_ids = [m for m in ids if m.startswith('gpt-')]
            return gpt_ids if gpt_ids else ids if ids else [m[0] for m in OPENAI_MODELS]
    except httpx.HTTPStatusError:
        raise
    except Exception:
        return [m[0] for m in OPENAI_MODELS]


def _fetch_local_models(base_url):
    """Fetch models from local OpenAI-compatible server. Raises ValueError on failure."""
    import httpx
    base_url = base_url.rstrip('/')
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(f"{base_url}/v1/models")
            r.raise_for_status()
            data = r.json()
            models = [
                m.get('id') or m.get('name')
                for m in (data.get('data') or data.get('models') or [])
            ]
            models = [m for m in models if m]
            if models:
                return models
    except Exception:
        pass
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(f"{base_url}/api/tags")
            r.raise_for_status()
            data = r.json()
            models = [m.get('name') for m in (data.get('models') or []) if m.get('name')]
            if models:
                return models
    except Exception:
        pass
    raise ValueError('Server AI locale non raggiungibile o nessun modello trovato.')


@strutture_bp.route('/<int:struttura_id>/config', methods=['GET', 'POST'])
@login_required
def config(struttura_id):
    ruolo = g.user['ruolo']
    if ruolo not in ('admin', 'superadmin'):
        flash('Accesso non autorizzato.', 'danger')
        return redirect(url_for('index'))
    if ruolo == 'admin' and g.user.get('struttura_id') != struttura_id:
        flash('Non puoi accedere alla configurazione di un\'altra struttura.', 'danger')
        return redirect(url_for('index'))

    is_admin_only = (ruolo == 'admin')

    struttura = query_one("SELECT * FROM strutture WHERE id = ?", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index') if not is_admin_only else url_for('index'))

    if request.method == 'POST':
        if is_admin_only:
            # Admin uses the AJAX test-ai endpoint for AI config, not this form
            return redirect(url_for('strutture.config', struttura_id=struttura_id))

        # Solo le preferenze: dalla 2.6.2 il server di posta e' unico e vive
        # nella configurazione di sistema. Le chiavi smtp_* qui non si scrivono
        # piu' — nessuno le leggerebbe — e la migrazione in
        # models.apply_schema_updates() ha gia' cancellato quelle esistenti,
        # password cifrata compresa.
        CHIAVI_PREFERENZE = ['avvisi_scadenza_attivi', 'avvisi_scadenza_formato',
                             'report_frequenza']
        CASELLE = {'avvisi_scadenza_attivi'}
        for chiave in CHIAVI_PREFERENZE:
            if chiave in CASELLE:
                valore = '1' if request.form.get(chiave) else ''
            else:
                valore = request.form.get(chiave, '').strip()
            if valore:
                set_struttura_config(struttura_id, chiave, valore)
            else:
                # Una casella non spuntata non compare affatto nel POST: senza
                # questa cancellazione l'interruttore si accenderebbe e non si
                # potrebbe piu' spegnere dall'interfaccia.
                execute(
                    "DELETE FROM strutture_config WHERE struttura_id=? AND chiave=?",
                    (struttura_id, chiave)
                )

        flash('Configurazione salvata.', 'success')
        log_attivita(g.user['id'], 'modifica', 'strutture_config', struttura_id,
                     'Preferenze avvisi di scadenza salvate', request.remote_addr)
        return redirect(url_for('strutture.config', struttura_id=struttura_id))

    cfg = get_struttura_config_all(struttura_id)
    return render_template('strutture/config.html',
                           struttura=struttura,
                           cfg=cfg,
                           is_admin_only=is_admin_only,
                           ai_providers=AI_PROVIDERS,
                           anthropic_models=ANTHROPIC_MODELS,
                           gemini_models=GEMINI_MODELS,
                           openai_models=OPENAI_MODELS)


@strutture_bp.route('/<int:struttura_id>/config/test-ai', methods=['POST'])
@login_required
def test_ai_config(struttura_id):
    """Save AI settings and test the connection. Returns JSON with model list."""
    ruolo = g.user['ruolo']
    if ruolo not in ('admin', 'superadmin'):
        return jsonify({'ok': False, 'message': 'Accesso non autorizzato'}), 403
    if ruolo == 'admin' and g.user.get('struttura_id') != struttura_id:
        return jsonify({'ok': False, 'message': 'Struttura non consentita'}), 403

    struttura = query_one("SELECT id FROM strutture WHERE id = ?", (struttura_id,))
    if not struttura:
        return jsonify({'ok': False, 'message': 'Struttura non trovata'}), 404

    data = request.json or {}
    provider = (data.get('provider') or 'anthropic').strip()

    valid_providers = [p[0] for p in AI_PROVIDERS]
    if provider not in valid_providers:
        return jsonify({'ok': False, 'message': f'Provider non valido: {provider}', 'models': []}), 400

    fields_to_save = {
        'ai_provider':       provider,
        'ai_import_model':   (data.get('ai_import_model') or '').strip(),
        'ai_email_model':    (data.get('ai_email_model') or '').strip(),
        'ai_local_base_url': (data.get('local_base_url') or '').strip(),
        'ai_local_model':    (data.get('local_model') or '').strip(),
    }
    for chiave, valore in fields_to_save.items():
        if valore:
            set_struttura_config(struttura_id, chiave, valore)
        else:
            execute(
                "DELETE FROM strutture_config WHERE struttura_id=? AND chiave=?",
                (struttura_id, chiave)
            )

    key_fields = {
        'anthropic_api_key': (data.get('api_key') or '').strip(),
        'gemini_api_key':    (data.get('gemini_api_key') or '').strip(),
        'openai_api_key':    (data.get('openai_api_key') or '').strip(),
    }
    for chiave, valore in key_fields.items():
        if valore:
            set_struttura_config(struttura_id, chiave, valore)

    active_anthropic_key = get_struttura_config(struttura_id, 'anthropic_api_key') or ''
    active_gemini_key    = get_struttura_config(struttura_id, 'gemini_api_key') or ''
    active_openai_key    = get_struttura_config(struttura_id, 'openai_api_key') or ''
    active_base_url      = get_struttura_config(struttura_id, 'ai_local_base_url') or ''

    try:
        if provider == 'anthropic':
            if not active_anthropic_key:
                return jsonify({'ok': False, 'message': 'Chiave API Anthropic non configurata.', 'models': []})
            models = _fetch_anthropic_models(active_anthropic_key)
        elif provider == 'gemini':
            if not active_gemini_key:
                return jsonify({'ok': False, 'message': 'Chiave API Google Gemini non configurata.', 'models': []})
            models = _fetch_gemini_models(active_gemini_key)
        elif provider == 'openai':
            if not active_openai_key:
                return jsonify({'ok': False, 'message': 'Chiave API OpenAI non configurata.', 'models': []})
            models = _fetch_openai_models(active_openai_key)
        else:
            if not active_base_url:
                return jsonify({'ok': False, 'message': 'URL server AI non configurato.', 'models': []})
            models = _fetch_local_models(active_base_url)

        log_attivita(g.user['id'], 'modifica', 'strutture_config', struttura_id,
                     f'Test AI {provider}: OK, {len(models)} modelli', request.remote_addr,
                     struttura_id=struttura_id)
        return jsonify({
            'ok': True,
            'models': models,
            'message': f'Connessione OK — {len(models)} modelli disponibili'
        })

    except httpx.HTTPStatusError as e:
        return jsonify({'ok': False, 'models': [],
                        'message': f'Errore HTTP {e.response.status_code}: chiave API non valida o accesso negato.'})
    except Exception as e:
        return jsonify({'ok': False, 'models': [], 'message': f'Errore: {str(e)[:200]}'})


# ============================================================================
# DIVISIONE MANAGEMENT (dal contesto struttura)
# ============================================================================

@strutture_bp.route('/<int:struttura_id>/divisioni/<int:div_id>/elimina', methods=['POST'])
@superadmin_required
def elimina_divisione(struttura_id, div_id):
    struttura = query_one("SELECT id FROM strutture WHERE id=?", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index'))

    div = query_one("SELECT * FROM divisioni WHERE id=? AND struttura_id=?", (div_id, struttura_id))
    if not div:
        flash('Divisione non trovata.', 'danger')
        return redirect(url_for('strutture.modifica', struttura_id=struttura_id))

    count_app = query_one(
        "SELECT COUNT(*) as cnt FROM apparecchi WHERE divisione_id=?",
        (div_id,)
    )
    if count_app and count_app['cnt'] > 0:
        flash(f'Impossibile eliminare "{div["nome"]}": ha {count_app["cnt"]} apparecchi associati (inclusi dismessi). Riassegnali prima.', 'danger')
        return redirect(url_for('strutture.modifica', struttura_id=struttura_id))

    count_div = query_one(
        "SELECT COUNT(*) as cnt FROM divisioni WHERE struttura_id=?", (struttura_id,)
    )
    if count_div and count_div['cnt'] <= 1:
        flash('Non puoi eliminare l\'ultima divisione della struttura.', 'danger')
        return redirect(url_for('strutture.modifica', struttura_id=struttura_id))

    try:
        db = get_db()
        # Annulla FK nullable che puntano alla divisione (nessuna ha CASCADE)
        db.execute("UPDATE utenti SET divisione_default_id=NULL WHERE divisione_default_id=?", (div_id,))
        db.execute("UPDATE email_config SET divisione_id=NULL WHERE divisione_id=?", (div_id,))
        db.execute("UPDATE import_history SET divisione_id=NULL WHERE divisione_id=?", (div_id,))
        db.execute("DELETE FROM divisioni WHERE id=? AND struttura_id=?", (div_id, struttura_id))
        db.commit()
        log_attivita(g.user['id'], 'elimina', 'divisioni', div_id,
                     f'Eliminata divisione "{div["nome"]}" dalla struttura {struttura_id}',
                     struttura_id=struttura_id)
        flash(f'Divisione "{div["nome"]}" eliminata.', 'success')
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        current_app.logger.error(f'Errore eliminazione divisione {div_id}: {e}')
        flash('Errore durante l\'eliminazione della divisione.', 'danger')
    return redirect(url_for('strutture.modifica', struttura_id=struttura_id))


# ============================================================================
# API TOKEN MANAGEMENT
# ============================================================================

@strutture_bp.route('/<int:struttura_id>/tokens')
@superadmin_required
def api_tokens(struttura_id):
    from flask import abort
    struttura = query_one("SELECT * FROM strutture WHERE id=?", (struttura_id,))
    if not struttura:
        abort(404)
    tokens = query_all(
        "SELECT * FROM api_tokens WHERE struttura_id=? ORDER BY created_at DESC",
        (struttura_id,)
    )
    return render_template('strutture/api_tokens.html', struttura=struttura, tokens=tokens)


@strutture_bp.route('/<int:struttura_id>/tokens/nuovo', methods=['POST'])
@superadmin_required
def nuovo_token(struttura_id):
    struttura = query_one("SELECT id, nome FROM strutture WHERE id=? AND attiva=1", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index'))

    import hashlib
    import secrets as _secrets
    nome = request.form.get('nome', '').strip()
    scopes = request.form.get('scopes', 'read')
    scadenza = request.form.get('scadenza') or None

    if not nome:
        flash('Il nome del token è obbligatorio.', 'danger')
        return redirect(url_for('strutture.api_tokens', struttura_id=struttura_id))

    if scopes not in ('read', 'read write'):
        scopes = 'read'

    raw = _secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()

    execute(
        """INSERT INTO api_tokens (struttura_id, nome, token_hash, scopes, scadenza, created_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (struttura_id, nome, token_hash, scopes, scadenza, g.user['id'])
    )
    log_attivita(g.user['id'], 'crea_token', 'api_tokens', None,
                 f'Token "{nome}" creato per struttura {struttura_id}',
                 struttura_id=struttura_id)
    flash(f'Token creato. Copia ora, non sarà più visibile: {raw}', 'warning')
    return redirect(url_for('strutture.api_tokens', struttura_id=struttura_id))


@strutture_bp.route('/<int:struttura_id>/tokens/<int:token_id>/revoca', methods=['POST'])
@superadmin_required
def revoca_token(struttura_id, token_id):
    cur = execute(
        "DELETE FROM api_tokens WHERE id=? AND struttura_id=?",
        (token_id, struttura_id)
    )
    if cur.rowcount == 0:
        flash('Token non trovato.', 'warning')
    else:
        log_attivita(g.user['id'], 'revoca_token', 'api_tokens', token_id,
                     dettagli=f'Token {token_id} revocato per struttura {struttura_id}',
                     struttura_id=struttura_id)
        flash('Token revocato.', 'success')
    return redirect(url_for('strutture.api_tokens', struttura_id=struttura_id))


# ============================================================================
# LOGO DELLA STRUTTURA (testata dei prospetti stampati)
# ============================================================================

ESTENSIONI_LOGO = {'png', 'jpg', 'jpeg'}


@strutture_bp.route('/<int:struttura_id>/logo', methods=['POST'])
@login_required
def carica_logo(struttura_id):
    """Carica il logo mostrato nella testata dei prospetti stampati."""
    ruolo = g.user['ruolo']
    if ruolo not in ('admin', 'superadmin'):
        flash('Accesso non autorizzato.', 'danger')
        return redirect(url_for('index'))
    if ruolo == 'admin' and g.user.get('struttura_id') != struttura_id:
        flash('Non puoi modificare un\'altra struttura.', 'danger')
        return redirect(url_for('index'))

    file = request.files.get('logo')
    if not file or not file.filename:
        flash('Nessun file selezionato.', 'warning')
        return redirect(url_for('strutture.config', struttura_id=struttura_id))

    estensione = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if estensione not in ESTENSIONI_LOGO:
        flash('Formato non supportato. Usa PNG o JPG.', 'danger')
        return redirect(url_for('strutture.config', struttura_id=struttura_id))

    precedente = query_one("SELECT logo_path FROM strutture WHERE id = ?", (struttura_id,))
    vecchio_logo_path = (precedente or {}).get('logo_path')

    cartella, prefisso = upload_subdir('loghi', struttura_id)
    nome = secure_filename(f"{int(datetime.now().timestamp())}_{file.filename}")
    nuovo_logo_path = f"{prefisso}/{nome}"
    file.save(os.path.join(cartella, nome))
    execute("UPDATE strutture SET logo_path = ? WHERE id = ?",
            (nuovo_logo_path, struttura_id))

    # Il file precedente non serve piu': senza questo, ogni sostituzione del
    # logo lascia un orfano su disco (e' cio' che pulisci_uploads.py esiste
    # per ripulire quando e' gia' successo altrove - qui si evita che succeda).
    if vecchio_logo_path and vecchio_logo_path != nuovo_logo_path:
        vecchio_percorso = percorso_logo_struttura({'logo_path': vecchio_logo_path})
        if vecchio_percorso and os.path.exists(vecchio_percorso):
            try:
                os.remove(vecchio_percorso)
            except OSError:
                current_app.logger.warning(f'Logo precedente non rimosso: {vecchio_percorso}')

    log_attivita(g.user['id'], 'modifica', 'strutture', struttura_id,
                 'Logo aggiornato', request.remote_addr, struttura_id=struttura_id)
    flash('Logo aggiornato.', 'success')
    return redirect(url_for('strutture.config', struttura_id=struttura_id))
