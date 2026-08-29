"""I controlli su un'installazione.

Ogni controllo e' una funzione (conn, config, fotografia) -> Esito | None:
None quando va tutto bene. Nessuno stampa e nessuno esce - la presentazione
sta in manutenzione.py, cosi' i controlli si testano chiamandoli.

Il rimedio non e' una frase, e' il comando da eseguire: chi legge la
diagnosi su un'installazione altrui non deve dover indovinare il seguito.
"""
from dataclasses import dataclass

from manutenzione_lib import stato as mstato
from manutenzione_lib.utenti import stato_impronta

# Finestra e soglia del blocco per tentativi, le stesse che applica auth.py.
BLOCCO_MINUTI = 30
BLOCCO_TENTATIVI = 5


@dataclass
class Esito:
    gravita: str     # 'errore' | 'avviso'
    titolo: str
    dettaglio: str
    rimedio: str


def _elenco_breve(valori, massimo=5):
    valori = list(valori)
    if len(valori) <= massimo:
        return ', '.join(valori)
    return ', '.join(valori[:massimo]) + f' (e altri {len(valori) - massimo})'


def controllo_integrita(conn, config, fotografia):
    sezione = fotografia.get('database', {})
    if not sezione.get('disponibile'):
        return None
    if sezione.get('integrity_check') != 'ok':
        return Esito('errore', 'Database corrotto',
                     sezione['integrity_check'],
                     'Ripristina un backup: python manutenzione.py backup --elenca')
    return None


def controllo_chiavi_esterne(conn, config, fotografia):
    sezione = fotografia.get('database', {})
    violazioni = sezione.get('foreign_key_check') or []
    if violazioni:
        return Esito('errore', 'Riferimenti pendenti',
                     f'{len(violazioni)} violazioni di chiave esterna, '
                     f'prima fra tutte {violazioni[0]}',
                     'Ripristina un backup: python manutenzione.py backup --elenca')
    return None


def controllo_migrazioni(conn, config, fotografia):
    pendenti = (fotografia.get('schema') or {}).get('pendenti') or []
    if pendenti:
        return Esito('errore', 'Migrazioni non applicate',
                     f"Da applicare: {', '.join(pendenti)}",
                     'python manutenzione.py migra')
    return None


def controllo_versione_dichiarata(conn, config, fotografia):
    """B05: un database che non dichiara la propria versione.

    Con PRAGMA user_version a 0 la versione va dedotta dalla forma delle
    tabelle, e strumenti diversi possono dedurla in modo diverso: e' cosi'
    che un file gia' aggiornato si e' fatto riconoscere come v1.1. Non e'
    un guasto - le migrazioni pendenti le segnala controllo_migrazioni -
    ma prima della messa in servizio va sanato.
    """
    schema = fotografia.get('schema') or {}
    if not schema.get('disponibile'):
        return None
    if schema.get('pendenti'):
        return None
    if schema.get('user_version'):
        return None
    return Esito('avviso', 'Versione dello schema non dichiarata',
                 'Il database ha PRAGMA user_version = 0: le tabelle sono '
                 'quelle attuali, ma il file non dichiara la propria '
                 'versione e ogni strumento deve dedurla.',
                 'Fai un backup verificato, poi avvia il programma una '
                 'volta oppure esegui python manutenzione.py migra, '
                 'infine ripeti la diagnosi')


def controllo_nessun_utente_attivo(conn, config, fotografia):
    sezione = fotografia.get('utenti') or {}
    if not sezione.get('disponibile'):
        return None
    if sezione.get('totale_attivi', 0) == 0:
        return Esito('errore', 'Nessun utente attivo',
                     "Nessuno puo' entrare in questa installazione.",
                     'python manutenzione.py utenti superadmin')
    return None


def controllo_strutture_senza_admin(conn, config, fotografia):
    if not mstato.tabella_esiste(conn, 'strutture'):
        return None
    # Su uno schema v1.x utenti non ha ne' struttura_id ne' eliminato_il:
    # la domanda "quale struttura non ha un admin" non ha senso li'.
    if not mstato.colonna_esiste(conn, 'utenti', 'struttura_id'):
        return None
    if not mstato.colonna_esiste(conn, 'utenti', 'eliminato_il'):
        return None
    orfane = [r[0] for r in conn.execute(
        """SELECT s.nome FROM strutture s
           WHERE NOT EXISTS (
               SELECT 1 FROM utenti u
               WHERE u.struttura_id = s.id AND u.ruolo = 'admin'
                 AND u.attivo = 1 AND u.eliminato_il IS NULL)""")]
    if orfane:
        return Esito('errore', 'Struttura senza amministratore attivo',
                     _elenco_breve(orfane),
                     'python manutenzione.py utenti elenca')
    return None


def controllo_impronte(conn, config, fotografia):
    if not mstato.tabella_esiste(conn, 'utenti'):
        return None
    rotte = [r[0] for r in conn.execute(
        'SELECT email, password_hash FROM utenti WHERE attivo = 1')
        if stato_impronta(r[1]) == 'metodo_sconosciuto']
    if rotte:
        return Esito(
            'errore', 'Password non verificabile',
            f"Impronta in un formato che werkzeug non sa piu' verificare: "
            f"{_elenco_breve(rotte)}. Il login solleva un'eccezione e "
            f"risponde 500, non 'credenziali non valide'.",
            'python manutenzione.py utenti password <email>')
    return None


def controllo_utenti_disattivati(conn, config, fotografia):
    if not mstato.tabella_esiste(conn, 'utenti'):
        return None
    dove = 'attivo = 0'
    if mstato.colonna_esiste(conn, 'utenti', 'eliminato_il'):
        dove += ' AND eliminato_il IS NULL'
    spenti = [r[0] for r in conn.execute(f'SELECT email FROM utenti WHERE {dove}')]
    if spenti:
        return Esito(
            'avviso', 'Utenti disattivati',
            f"Ricevono 'credenziali non valide' come chi sbaglia password: "
            f"{_elenco_breve(spenti)}",
            'python manutenzione.py utenti password <email>')
    return None


def controllo_blocco_accessi(conn, config, fotografia):
    if not mstato.tabella_esiste(conn, 'login_attempts'):
        return None
    bloccate = [r[0] for r in conn.execute(
        f"""SELECT email, COUNT(*) AS n FROM login_attempts
            WHERE esito = 'fallito'
              AND created_at > datetime('now', '-{BLOCCO_MINUTI} minutes')
            GROUP BY email HAVING n >= {BLOCCO_TENTATIVI}""")]
    if bloccate:
        return Esito(
            'avviso', 'Accessi bloccati per tentativi ripetuti',
            f'Bloccati per {BLOCCO_MINUTI} minuti: {_elenco_breve(bloccate)}',
            "Attendi la scadenza, oppure svuota login_attempts per quell'indirizzo")
    return None


def controllo_account_cancellati(conn, config, fotografia):
    if not mstato.tabella_esiste(conn, 'utenti'):
        return None
    cancellati = [r[0] for r in conn.execute(
        "SELECT email FROM utenti WHERE email LIKE '%#eliminato-%'")]
    if cancellati:
        return Esito('avviso', 'Account cancellati',
                     f"Righe storiche, non piu' utilizzabili per entrare: "
                     f"{_elenco_breve(cancellati)}",
                     'Nessuna azione necessaria')
    return None


def controllo_modalita(conn, config, fotografia):
    sezione = fotografia.get('modalita') or {}
    if not sezione.get('disponibile'):
        return None
    if sezione.get('single_struttura') and sezione.get('strutture', 0) > 1:
        return Esito('avviso', "Modalita' incoerente",
                     f"single_struttura e' attiva ma le strutture sono "
                     f"{sezione['strutture']}",
                     'python manutenzione.py modalita --multi')
    return None


def controllo_uploads(conn, config, fotografia):
    sezione = fotografia.get('uploads') or {}
    if not sezione.get('disponibile'):
        return Esito('errore', 'Cartella uploads assente',
                     f"{sezione.get('percorso')}: {sezione.get('motivo')}",
                     'Crea la cartella o correggi uploads_path in config.local.json')
    if sezione.get('mancanti'):
        return Esito('avviso', 'Allegati mancanti sul disco',
                     f"{len(sezione['mancanti'])} righe puntano a file che non "
                     f"ci sono, la prima e' {sezione['mancanti'][0]}",
                     'Ripristina un backup, o rimuovi i riferimenti dalle schede')
    if sezione.get('orfani'):
        return Esito('avviso', 'File orfani',
                     f"{sezione['orfani']} file che nessuna riga referenzia",
                     'python manutenzione.py uploads --elimina')
    return None


def controllo_chiavi_ai(conn, config, fotografia):
    sezione = fotografia.get('ai') or {}
    provider = sezione.get('provider')
    if provider in ('anthropic', 'gemini', 'openai') and not sezione['chiavi'].get(provider):
        return Esito('avviso', 'Chiave AI assente',
                     f"Il provider predefinito e' {provider} ma la chiave "
                     f"globale manca",
                     "Impostala in config.local.json o per struttura "
                     "dall'interfaccia")
    return None


def controllo_posta(conn, config, fotografia):
    sezione = fotografia.get('posta') or {}
    if sezione.get('smtp_host'):
        return None
    if not mstato.tabella_esiste(conn, 'strutture_config'):
        return None
    attivi = conn.execute(
        "SELECT COUNT(*) FROM strutture_config "
        "WHERE chiave = 'avvisi_scadenza_attivi' AND valore IN ('1', 'true')"
    ).fetchone()[0]
    if attivi:
        return Esito('avviso', 'Avvisi attivi senza SMTP',
                     f'{attivi} strutture hanno gli avvisi di scadenza attivi '
                     f"ma il server di posta non e' configurato",
                     'Configura smtp_host in config.local.json')
    return None


def controllo_sessioni_scadute(conn, config, fotografia):
    if not mstato.tabella_esiste(conn, 'sessioni'):
        return None
    scadute = conn.execute(
        "SELECT COUNT(*) FROM sessioni WHERE expires_at <= datetime('now')").fetchone()[0]
    if scadute > 100:
        return Esito('avviso', 'Sessioni scadute accumulate',
                     f'{scadute} righe scadute in sessioni',
                     "Lo scheduler le pulisce all'avvio dell'applicazione")
    return None


CONTROLLI = (
    controllo_integrita,
    controllo_chiavi_esterne,
    controllo_migrazioni,
    controllo_versione_dichiarata,
    controllo_nessun_utente_attivo,
    controllo_strutture_senza_admin,
    controllo_impronte,
    controllo_utenti_disattivati,
    controllo_blocco_accessi,
    controllo_account_cancellati,
    controllo_modalita,
    controllo_uploads,
    controllo_chiavi_ai,
    controllo_posta,
    controllo_sessioni_scadute,
)


def esegui(conn, config, fotografia):
    """Tutti i controlli, errori prima degli avvisi.

    Un controllo che esplode non ferma gli altri: diventa esso stesso un
    errore da mostrare. Su un database malmesso - il caso normale per questo
    strumento - una singola query che fallisce non deve nascondere le
    tredici diagnosi rimaste.
    """
    esiti = []
    pendenti = bool((fotografia.get('schema') or {}).get('pendenti'))
    for controllo in CONTROLLI:
        try:
            risultato = controllo(conn, config, fotografia)
        except Exception as e:
            # Su uno schema indietro il rimedio non e' una segnalazione: e' la
            # migrazione. Dire "segnalalo" a chi ha semplicemente un database
            # vecchio lo manda a sbattere.
            rimedio = ('python manutenzione.py migra' if pendenti
                       else 'Segnalalo a Studio Bergamaschi')
            risultato = Esito('errore', f'Controllo fallito: {controllo.__name__}',
                              str(e), rimedio)
        if risultato is not None:
            esiti.append(risultato)
    esiti.sort(key=lambda e: 0 if e.gravita == 'errore' else 1)
    return esiti


def ci_sono_errori(esiti):
    return any(e.gravita == 'errore' for e in esiti)
