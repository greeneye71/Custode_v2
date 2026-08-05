"""
MedInventory - Cancellazione di un utente

Volutamente estraneo a Flask, come struttura_service.py: riceve una
sqlite3.Connection e il chiamante apre e chiude la transazione.

Da non confondere con struttura_service._rimuovi_utenti, che fa l'operazione
OPPOSTA su cio' che conta: quella azzera gli otto riferimenti *_by perche'
sta sparendo un'intera struttura e nessuno restera' a chiedersi chi avesse
inserito cosa. Qui la struttura resta viva, e quei riferimenti sono
esattamente il dato da conservare: su un registro di apparecchi
elettromedicali "chi ha inserito questo apparecchio" e' tracciabilita'.
"""

from datetime import datetime

from struttura_service import RIFERIMENTI_UTENTE


# password_hash e' NOT NULL: non basta svuotarla. Questo valore non e'
# un'impronta valida, quindi nessuna password puo' corrispondergli.
PASSWORD_INUTILIZZABILE = '!utente-eliminato'


def email_liberata(email, utente_id):
    """La forma in cui si sposta l'indirizzo di un utente cancellato.

    utenti.email e' UNIQUE: senza spostarla, ricreare un account con lo stesso
    indirizzo sarebbe impossibile. L'id nel suffisso non e' decorativo — se la
    persona viene ricreata e ricancellata, il secondo account ha un id diverso
    e le due voci storiche non collidono fra loro.
    """
    return f"{email}#eliminato-{utente_id}"


def conteggi_riferimenti(conn, utente_id):
    """Quante righe portano il nome di questo utente, per tabella.

    Somma per tabella: apparecchi compare due volte in RIFERIMENTI_UTENTE
    (created_by e updated_by) e le due vanno addizionate, non sovrascritte.
    """
    conteggi = {}
    for tabella, colonna in RIFERIMENTI_UTENTE:
        n = conn.execute(
            f"SELECT COUNT(*) FROM {tabella} WHERE {colonna} = ?", (utente_id,)
        ).fetchone()[0]
        conteggi[tabella] = conteggi.get(tabella, 0) + n
    return conteggi


def cancella_utente(conn, utente_id):
    """Distrugge l'account e lascia la riga come voce storica.

    Restituisce l'identita' di prima (email ORIGINALE, nome, cognome, ruolo,
    struttura) e i conteggi: dopo la cancellazione nel database c'e' solo la
    forma spostata, e il registro deve poter dire chi era.
    """
    riga = conn.execute(
        "SELECT email, nome, cognome, ruolo, struttura_id FROM utenti WHERE id = ?",
        (utente_id,)).fetchone()
    if riga is None:
        raise ValueError(f"Utente {utente_id} inesistente.")

    email, nome, cognome, ruolo, struttura_id = riga
    esito = {'email': email, 'nome': nome, 'cognome': cognome, 'ruolo': ruolo,
             'struttura_id': struttura_id,
             'conteggi': conteggi_riferimenti(conn, utente_id)}

    # Le otto colonne *_by non si toccano: sono il punto della scelta.
    conn.execute("DELETE FROM sessioni WHERE utente_id = ?", (utente_id,))
    conn.execute("DELETE FROM utenti_divisioni WHERE utente_id = ?", (utente_id,))
    conn.execute("DELETE FROM tecnici_strutture WHERE tecnico_id = ?", (utente_id,))
    # reset_hash e reset_scadenza vanno azzerate insieme alla password: sono
    # una password anche loro. Oggi non basterebbero a entrare — il login
    # cerca l'utente con attivo = 1 — ma lasciare una credenziale viva su una
    # riga che rappresenta un account distrutto e' esattamente il genere di
    # dettaglio che una modifica futura al login trasforma in un buco.
    conn.execute(
        "UPDATE utenti SET email = ?, password_hash = ?, attivo = 0, "
        "reset_hash = NULL, reset_scadenza = NULL, "
        "eliminato_il = ?, updated_at = ? WHERE id = ?",
        (email_liberata(email, utente_id), PASSWORD_INUTILIZZABILE,
         datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
         datetime.now().strftime('%Y-%m-%d %H:%M:%S'), utente_id))
    return esito


def motivo_rifiuto(conn, utente_id):
    """Perche' questo utente non si puo' cancellare, declassare o disattivare,
    o None se si puo'.

    Guarda solo il database: i rifiuti che dipendono da CHI chiede (se stessi,
    l'ambito dell'admin) stanno nella rotta, che e' l'unica a sapere chi e'
    l'utente corrente.

    La domanda e' sempre e solo una: "dopo questa operazione resta qualcuno in
    grado di amministrare la struttura ADESSO?". Si contano quindi solo gli
    amministratori esistenti (``eliminato_il IS NULL``) e ATTIVI.

    Perche' anche per la cancellazione, dove per un giro si erano contati tutti
    gli admin esistenti, attivi o no: l'idea era che un admin disattivato
    "esiste ancora e si riattiva con un clic da chiunque altro amministri la
    struttura". Quella premessa e' falsa proprio nel caso in cui il conteggio
    decide qualcosa — se l'altro admin e' disattivato, in quella struttura non
    c'e' piu' nessuno che possa riattivarlo. Le tre operazioni portano allo
    stesso identico stato finale (zero amministratori in grado di entrare), e
    contando in due modi diversi si finiva per vietare la piu' lieve
    (disattivazione, reversibile con un clic) e permettere le due peggiori
    (declassamento e cancellazione). Ora il rimedio e' lo stesso in tutti i
    casi: prima si riattiva o si nomina un altro amministratore, poi si
    procede.
    """
    riga = conn.execute(
        "SELECT ruolo, struttura_id, eliminato_il FROM utenti WHERE id = ?",
        (utente_id,)).fetchone()
    if riga is None:
        return 'inesistente'
    ruolo, struttura_id, eliminato_il = riga
    if eliminato_il is not None:
        return 'gia_cancellato'

    if ruolo == 'superadmin':
        rimasti = conn.execute(
            "SELECT COUNT(*) FROM utenti WHERE ruolo = 'superadmin' "
            "AND eliminato_il IS NULL AND attivo = 1 AND id != ?", (utente_id,)).fetchone()[0]
        if rimasti == 0:
            return 'ultimo_superadmin'

    if ruolo == 'admin' and struttura_id is not None:
        rimasti = conn.execute(
            "SELECT COUNT(*) FROM utenti WHERE ruolo = 'admin' AND struttura_id = ? "
            "AND eliminato_il IS NULL AND attivo = 1 AND id != ?",
            (struttura_id, utente_id)).fetchone()[0]
        if rimasti == 0:
            return 'ultimo_admin'

    return None
