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
    conn.execute(
        "UPDATE utenti SET email = ?, password_hash = ?, attivo = 0, "
        "eliminato_il = ?, updated_at = ? WHERE id = ?",
        (email_liberata(email, utente_id), PASSWORD_INUTILIZZABILE,
         datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
         datetime.now().strftime('%Y-%m-%d %H:%M:%S'), utente_id))
    return esito


def motivo_rifiuto(conn, utente_id, per_disattivazione=False):
    """Perche' questo utente non si puo' cancellare (o disattivare), o None se si puo'.

    Guarda solo il database: i rifiuti che dipendono da CHI chiede (se stessi,
    l'ambito dell'admin) stanno nella rotta, che e' l'unica a sapere chi e'
    l'utente corrente.

    Un utente gia' cancellato non conta come amministratore della struttura:
    se contasse, l'ultimo admin rimasto risulterebbe cancellabile.

    ``per_disattivazione`` distingue due domande che sembrano la stessa ma non
    lo sono, e per questo contano cose diverse:

    - CANCELLAZIONE (default, False): la domanda e' "esiste ancora un altro
      amministratore, anche se disattivato?". Un admin disattivato ma non
      cancellato esiste ancora e si puo' riattivare con un clic da chiunque
      altro amministri la struttura: la sua sola presenza nella riga basta a
      non lasciare la struttura orfana in modo permanente, ed e' il freno che
      non obbliga l'operatore a ragionare sullo stato di attivazione mentre
      sta cancellando. Si contano quindi tutti gli admin esistenti,
      ``attivo`` o no.
    - DISATTIVAZIONE (per_disattivazione=True): la domanda e' "resta qualcuno
      che possa amministrare la struttura ADESSO?". Un admin gia' disattivato
      non puo' entrare: contarlo come superstite invertirebbe il freno,
      lasciando la struttura senza nessuno in grado di gestirla nell'immediato
      pur segnalando (a torto) che qualcuno resta. Si contano quindi solo gli
      admin/superadmin ``attivi``.
    """
    riga = conn.execute(
        "SELECT ruolo, struttura_id, eliminato_il FROM utenti WHERE id = ?",
        (utente_id,)).fetchone()
    if riga is None:
        return 'inesistente'
    ruolo, struttura_id, eliminato_il = riga
    if eliminato_il is not None:
        return 'gia_cancellato'

    filtro_attivo = " AND attivo = 1" if per_disattivazione else ""

    if ruolo == 'superadmin':
        rimasti = conn.execute(
            "SELECT COUNT(*) FROM utenti WHERE ruolo = 'superadmin' "
            f"AND eliminato_il IS NULL{filtro_attivo} AND id != ?", (utente_id,)).fetchone()[0]
        if rimasti == 0:
            return 'ultimo_superadmin'

    if ruolo == 'admin' and struttura_id is not None:
        rimasti = conn.execute(
            "SELECT COUNT(*) FROM utenti WHERE ruolo = 'admin' AND struttura_id = ? "
            f"AND eliminato_il IS NULL{filtro_attivo} AND id != ?", (struttura_id, utente_id)).fetchone()[0]
        if rimasti == 0:
            return 'ultimo_admin'

    return None
