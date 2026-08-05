"""reset_password.py: la logica della temporanea, senza Flask di mezzo."""
import sqlite3

import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def conn(tmp_path):
    """Le sole tabelle che il modulo tocca."""
    c = sqlite3.connect(str(tmp_path / 'reset.sqlite'))
    c.execute("""CREATE TABLE utenti (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
        nome TEXT, cognome TEXT, struttura_id INTEGER,
        attivo INTEGER DEFAULT 1, primo_accesso INTEGER DEFAULT 0,
        eliminato_il DATETIME, updated_at DATETIME,
        reset_hash TEXT, reset_scadenza DATETIME)""")
    c.execute("""CREATE TABLE sessioni (
        id INTEGER PRIMARY KEY AUTOINCREMENT, utente_id INTEGER, token TEXT)""")
    c.execute("""CREATE TABLE login_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ip_address TEXT NOT NULL,
        email TEXT, esito TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    return c


def crea_utente(conn, email='mario@x.it', attivo=1, eliminato=None):
    return conn.execute(
        "INSERT INTO utenti (email,password_hash,nome,cognome,struttura_id,attivo,eliminato_il) "
        "VALUES (?,?,'Mario','Rossi',3,?,?)",
        (email, generate_password_hash('Passw0rd!'), attivo, eliminato)).lastrowid


def test_la_temporanea_valida_entra(conn):
    from reset_password import registra_reset, consuma_temporanea
    uid = crea_utente(conn)
    registra_reset(conn, uid, 'tempor4nea')
    assert consuma_temporanea(conn, uid, 'tempor4nea') is True


def test_la_temporanea_vale_una_volta_sola(conn):
    """Resta in una casella di posta: se valesse ancora dopo l'uso, chiunque
    legga quella casella in seguito entrerebbe di nuovo."""
    from reset_password import registra_reset, consuma_temporanea
    uid = crea_utente(conn)
    registra_reset(conn, uid, 'tempor4nea')
    assert consuma_temporanea(conn, uid, 'tempor4nea') is True
    assert consuma_temporanea(conn, uid, 'tempor4nea') is False


def test_una_temporanea_scaduta_non_entra(conn):
    from reset_password import registra_reset, consuma_temporanea
    uid = crea_utente(conn)
    registra_reset(conn, uid, 'tempor4nea')
    conn.execute("UPDATE utenti SET reset_scadenza = datetime('now','-1 minute') "
                 "WHERE id = ?", (uid,))
    assert consuma_temporanea(conn, uid, 'tempor4nea') is False


def test_una_password_sbagliata_non_entra_come_temporanea(conn):
    from reset_password import registra_reset, consuma_temporanea
    uid = crea_utente(conn)
    registra_reset(conn, uid, 'tempor4nea')
    assert consuma_temporanea(conn, uid, 'un-altra') is False
    # E il tentativo sbagliato non ha bruciato quella buona.
    assert consuma_temporanea(conn, uid, 'tempor4nea') is True


def test_senza_reset_in_sospeso_non_entra_niente(conn):
    """Il caso di gran lunga piu' frequente: la stragrande maggioranza degli
    utenti non ha mai chiesto un reset, e per loro questa strada dev'essere
    chiusa qualunque cosa si digiti."""
    from reset_password import consuma_temporanea
    uid = crea_utente(conn)
    assert consuma_temporanea(conn, uid, '') is False
    assert consuma_temporanea(conn, uid, 'qualsiasi') is False


def test_usare_la_temporanea_obbliga_a_cambiarla_e_chiude_le_sessioni(conn):
    """La temporanea e' arrivata per email: deve durare il tempo di entrare e
    sceglierne una nuova. E chi e' rimasto dentro con la vecchia password non
    deve restarci."""
    from reset_password import registra_reset, consuma_temporanea
    uid = crea_utente(conn)
    conn.execute("INSERT INTO sessioni (utente_id, token) VALUES (?, 'vecchia')", (uid,))
    registra_reset(conn, uid, 'tempor4nea')

    consuma_temporanea(conn, uid, 'tempor4nea')

    riga = conn.execute("SELECT primo_accesso, reset_hash, reset_scadenza "
                        "FROM utenti WHERE id = ?", (uid,)).fetchone()
    assert riga[0] == 1
    assert riga[1] is None and riga[2] is None
    assert conn.execute("SELECT COUNT(*) FROM sessioni WHERE utente_id = ?",
                        (uid,)).fetchone()[0] == 0


def test_la_temporanea_non_finisce_in_chiaro_nel_database(conn):
    """E' una password a tutti gli effetti. Chi legge il database non deve
    poter entrare con gli account di tutti quelli che hanno un reset aperto."""
    from reset_password import registra_reset
    uid = crea_utente(conn)
    registra_reset(conn, uid, 'tempor4nea')
    impronta = conn.execute("SELECT reset_hash FROM utenti WHERE id = ?",
                            (uid,)).fetchone()[0]
    assert 'tempor4nea' not in impronta


def test_azzera_reset_toglie_il_reset_in_sospeso(conn):
    from reset_password import registra_reset, azzera_reset, consuma_temporanea
    uid = crea_utente(conn)
    registra_reset(conn, uid, 'tempor4nea')
    azzera_reset(conn, uid)
    assert consuma_temporanea(conn, uid, 'tempor4nea') is False


def test_un_utente_disattivato_non_e_un_destinatario(conn):
    """Non potrebbe comunque entrare: spedirgli una temporanea sarebbe solo un
    modo di dire a chi ha chiesto che quell'account esiste."""
    from reset_password import destinatario_valido
    crea_utente(conn, 'spento@x.it', attivo=0)
    assert destinatario_valido(conn, 'spento@x.it') is None


def test_un_utente_cancellato_non_e_un_destinatario(conn):
    from reset_password import destinatario_valido
    crea_utente(conn, 'sparito@x.it', eliminato='2026-01-01 10:00:00')
    assert destinatario_valido(conn, 'sparito@x.it') is None


def test_un_indirizzo_sconosciuto_non_e_un_destinatario(conn):
    from reset_password import destinatario_valido
    crea_utente(conn)
    assert destinatario_valido(conn, 'nessuno@x.it') is None


def test_un_utente_normale_e_un_destinatario(conn):
    from reset_password import destinatario_valido
    crea_utente(conn)
    riga = destinatario_valido(conn, 'mario@x.it')
    assert riga is not None
    # struttura_id serve al registro attivita': senza, la voce sarebbe
    # invisibile in /admin/log-attivita proprio a chi deve leggerla.
    assert riga[4] == 3


def test_il_limite_conta_anche_i_tentativi_di_accesso_falliti(conn):
    """Chi sta provando le password di un indirizzo non deve poter continuare
    a farsi mandare temporanee su quella casella."""
    from reset_password import troppe_richieste, SOGLIA_IP
    for _ in range(SOGLIA_IP):
        conn.execute("INSERT INTO login_attempts (ip_address,email,esito) "
                     "VALUES ('10.0.0.9','mario@x.it','fallito')")
    assert troppe_richieste(conn, '10.0.0.9', 'mario@x.it') is True


def test_il_limite_conta_le_richieste_di_reset(conn):
    from reset_password import troppe_richieste, registra_richiesta, SOGLIA_IP
    for _ in range(SOGLIA_IP):
        registra_richiesta(conn, '10.0.0.9', 'mario@x.it')
    assert troppe_richieste(conn, '10.0.0.9', 'mario@x.it') is True


def test_il_limite_per_indirizzo_scatta_da_qualunque_ip(conn):
    """Il limite per IP non basta: chi ha una rete di indirizzi diversi
    riempirebbe comunque la casella del collega."""
    from reset_password import troppe_richieste, registra_richiesta, SOGLIA_EMAIL
    for n in range(SOGLIA_EMAIL):
        registra_richiesta(conn, f'10.0.0.{n}', 'mario@x.it')
    assert troppe_richieste(conn, '10.0.1.1', 'mario@x.it') is True


def test_sotto_soglia_non_si_blocca_nessuno(conn):
    from reset_password import troppe_richieste, registra_richiesta, SOGLIA_IP
    for _ in range(SOGLIA_IP - 1):
        registra_richiesta(conn, '10.0.0.9', 'mario@x.it')
    assert troppe_richieste(conn, '10.0.0.9', 'mario@x.it') is False


def test_le_richieste_vecchie_non_contano_piu(conn):
    """Il blocco e' temporaneo: passata la finestra si riprova, altrimenti
    basterebbe una giornata storta per restare chiusi fuori per sempre."""
    from reset_password import troppe_richieste, SOGLIA_IP
    for _ in range(SOGLIA_IP + 2):
        conn.execute("INSERT INTO login_attempts (ip_address,email,esito,created_at) "
                     "VALUES ('10.0.0.9','mario@x.it','reset',datetime('now','-2 hours'))")
    assert troppe_richieste(conn, '10.0.0.9', 'mario@x.it') is False


def test_l_email_dice_la_temporanea_la_scadenza_e_di_ignorarla(conn):
    """La riga sull'ignorare non e' cortesia: e' quello che rende comprensibile
    la scelta di non distruggere la password attuale. Chi riceve questa email
    senza averla chiesta deve capire in una frase che non gli e' successo
    niente."""
    from reset_password import messaggio_email
    from datetime import datetime, timezone
    scadenza_utc = '2026-08-05 18:30:00'
    oggetto, corpo = messaggio_email('Mario', 'tempor4nea', scadenza_utc)
    assert 'tempor4nea' in corpo
    # L'ora va mostrata in quella di chi legge: la scadenza nel database e'
    # scritta da SQLite, cioe' in UTC, e riportarla cruda direbbe a un utente
    # italiano che la password scade due ore prima di quando scade davvero.
    atteso = (datetime.strptime(scadenza_utc, '%Y-%m-%d %H:%M:%S')
              .replace(tzinfo=timezone.utc).astimezone().strftime('%d/%m/%Y alle %H:%M'))
    assert atteso in corpo
    assert 'ignorare' in corpo.lower()
    assert 'funziona ancora' in corpo.lower()
    assert 'password' in oggetto.lower()
