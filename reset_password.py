"""
MedInventory - Reset della password dalla schermata di accesso

La temporanea vale ACCANTO alla password attuale e scade. E' la scelta che
distingue questa soluzione da quella ovvia (sostituire la password), e il
motivo e' che sulla schermata di accesso chiunque puo' digitare l'indirizzo di
un collega: se il reset sostituisse la password, chiunque conosca l'email di
un collega potrebbe buttarlo fuori dal proprio account. In un reparto, prima
di un turno, non e' un fastidio teorico.

Volutamente estraneo a Flask, come utente_service.py e posta.py: riceve una
sqlite3.Connection e il chiamante apre e chiude la transazione.
"""

import secrets
from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash


# Il tempo di leggere un'email, non di lasciare una credenziale valida in una
# casella di posta.
DURATA_MINUTI = 30

# Le stesse soglie del login (auth.login): stessa tabella, stesse regole,
# nessuna macchina nuova da mantenere.
SOGLIA_IP = 5
FINESTRA_IP_MINUTI = 15
SOGLIA_EMAIL = 10
FINESTRA_EMAIL_MINUTI = 30

FORMATO_ORA = '%Y-%m-%d %H:%M:%S'


def genera_temporanea():
    """La password temporanea. Stesso generatore del reset dell'amministratore."""
    return secrets.token_urlsafe(10)


def troppe_richieste(conn, ip, email):
    """Se questa richiesta va rifiutata senza spedire niente.

    Conta insieme i tentativi di accesso falliti e le richieste di reset: chi
    sta provando le password di un indirizzo non deve poter continuare a
    farsi mandare temporanee su quella casella, e viceversa.
    """
    da_ip = conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE ip_address = ? "
        f"AND esito IN ('fallito', 'reset') "
        f"AND created_at > datetime('now', '-{FINESTRA_IP_MINUTI} minutes')",
        (ip,)).fetchone()[0]
    if da_ip >= SOGLIA_IP:
        return True

    per_email = conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE email = ? "
        f"AND esito IN ('fallito', 'reset') "
        f"AND created_at > datetime('now', '-{FINESTRA_EMAIL_MINUTI} minutes')",
        (email,)).fetchone()[0]
    return per_email >= SOGLIA_EMAIL


def registra_richiesta(conn, ip, email):
    """La riga che fa da contatore. Va scritta SEMPRE, anche quando l'indirizzo
    non esiste: e' l'unico posto in cui restano le richieste per indirizzi
    sconosciuti, che in log_attivita non possono entrare."""
    conn.execute(
        "INSERT INTO login_attempts (ip_address, email, esito) VALUES (?, ?, 'reset')",
        (ip, email))


def destinatario_valido(conn, email):
    """L'utente a cui spedire, o None.

    Disattivato o cancellato: nessuna email. Non potrebbe comunque entrare, e
    chi ha chiesto vede lo stesso messaggio di tutti gli altri.
    """
    return conn.execute(
        "SELECT id, nome, cognome, email, struttura_id FROM utenti "
        "WHERE email = ? AND attivo = 1 AND eliminato_il IS NULL",
        (email,)).fetchone()


def registra_reset(conn, utente_id, temporanea):
    """Salva l'impronta della temporanea con la sua scadenza. Torna la scadenza.

    L'impronta e non la temporanea: e' una password a tutti gli effetti, e nel
    database le password non stanno in chiaro.
    """
    conn.execute(
        f"UPDATE utenti SET reset_hash = ?, "
        f"reset_scadenza = datetime('now', '+{DURATA_MINUTI} minutes'), "
        f"updated_at = datetime('now') WHERE id = ?",
        (generate_password_hash(temporanea), utente_id))
    return conn.execute("SELECT reset_scadenza FROM utenti WHERE id = ?",
                        (utente_id,)).fetchone()[0]


def azzera_reset(conn, utente_id):
    """Toglie il reset in sospeso.

    Chiamato anche quando si entra con la password normale: se l'utente se l'e'
    ricordata, la temporanea non ha piu' motivo di restare valida — ne' nella
    casella di posta di chi l'ha ricevuta.
    """
    conn.execute(
        "UPDATE utenti SET reset_hash = NULL, reset_scadenza = NULL WHERE id = ?",
        (utente_id,))


def consuma_temporanea(conn, utente_id, password):
    """Prova la password come temporanea. True se era quella giusta e valida.

    Riuscendo: la temporanea sparisce (vale una volta sola), l'utente e'
    obbligato a sceglierne una nuova con le regole gia' in vigore, e le altre
    sessioni si chiudono — chi e' rimasto dentro con la vecchia password non
    deve restarci.
    """
    riga = conn.execute(
        "SELECT reset_hash, reset_scadenza FROM utenti WHERE id = ?",
        (utente_id,)).fetchone()
    if riga is None:
        return False
    impronta, scadenza = riga[0], riga[1]
    if not impronta or not scadenza:
        return False
    # Confronto fatto da SQLite: reset_scadenza e' scritta con il suo orologio
    # (UTC), e misurarla con datetime.now() — l'ora locale — avrebbe accorciato
    # o allungato la durata dello scarto del fuso.
    if conn.execute("SELECT ? < datetime('now')", (scadenza,)).fetchone()[0]:
        return False
    if not check_password_hash(impronta, password):
        return False

    conn.execute(
        "UPDATE utenti SET reset_hash = NULL, reset_scadenza = NULL, "
        "primo_accesso = 1, updated_at = datetime('now') WHERE id = ?",
        (utente_id,))
    conn.execute("DELETE FROM sessioni WHERE utente_id = ?", (utente_id,))
    return True


def messaggio_email(nome, temporanea, scadenza):
    """(oggetto, corpo) dell'email con la temporanea.

    La riga sull'ignorare il messaggio non e' cortesia: e' quello che rende
    comprensibile la scelta di non distruggere la password attuale. Chi riceve
    questa email senza averla chiesta deve poter capire in una frase che non
    gli e' successo niente.
    """
    # La scadenza arriva come l'ha scritta SQLite, cioe' in UTC, e va mostrata
    # nell'ora di chi legge: scriverla cosi' com'e' significherebbe dire a un
    # utente italiano che la password scade due ore prima di quando scade
    # davvero — e trentacinque minuti dopo averla ricevuta gli sembrerebbe di
    # essere in ritardo mentre ha ancora tempo.
    quando = scadenza
    try:
        quando = (datetime.strptime(scadenza, FORMATO_ORA)
                  .replace(tzinfo=timezone.utc)
                  .astimezone()
                  .strftime('%d/%m/%Y alle %H:%M'))
    except (TypeError, ValueError):
        pass

    oggetto = "MedInventory - password temporanea"
    corpo = (
        f"Ciao {nome},\n\n"
        f"e' stata richiesta una password temporanea per il tuo accesso a MedInventory.\n\n"
        f"    {temporanea}\n\n"
        f"Va usata entro il {quando} ({DURATA_MINUTI} minuti dalla richiesta). "
        f"Al primo accesso ti verra' chiesto di sceglierne una nuova.\n\n"
        f"Se non sei stato tu a chiederla, puoi ignorare questo messaggio: la tua "
        f"password attuale funziona ancora e non e' stata cambiata.\n"
    )
    return oggetto, corpo
