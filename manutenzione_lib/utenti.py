"""Operazioni sugli account, fuori da Flask.

Riceve una sqlite3.Connection e non apre transazioni proprie: le apre e le
chiude il chiamante, come in utente_service.py e struttura_service.py. E'
cio' che permette a manutenzione.py di azzerare gli utenti e creare
l'accesso di rimpiazzo dentro la stessa transazione.
"""
from dataclasses import dataclass
from datetime import datetime

from werkzeug.security import generate_password_hash

# I due soli metodi che werkzeug.security._hash_internal implementa oggi.
# Tutto il resto fa SOLLEVARE check_password_hash, non restituire False:
# vedi stato_impronta.
METODI_VERIFICABILI = ('pbkdf2', 'scrypt')


class UtenteInesistente(ValueError):
    pass


class EmailGiaInUso(ValueError):
    pass


class PasswordDebole(ValueError):
    pass


class AccessoNonGarantito(RuntimeError):
    """L'azzeramento lascerebbe l'installazione senza nessuno che possa entrare.

    Sollevata DOPO le cancellazioni, di proposito: il controllo interessante
    e' sullo stato finale, non su quello iniziale, ed e' il chiamante ad
    annullare la transazione.
    """


@dataclass
class Rimpiazzo:
    email: str
    password: str
    ruolo: str = 'superadmin'
    struttura_id: int = None
    nome: str = 'Amministratore'
    cognome: str = 'Sistema'


def valida_password(password):
    """Stesse regole di crea_superadmin.valida_password, che le aveva per primo."""
    errori = []
    if len(password or '') < 8:
        errori.append('almeno 8 caratteri')
    if not any(c.isupper() for c in password or ''):
        errori.append('almeno una lettera maiuscola')
    if not any(c.isdigit() for c in password or ''):
        errori.append('almeno un numero')
    return errori


def stato_impronta(password_hash):
    """Come si comportera' check_password_hash davanti a questa impronta.

    - 'ok'                 la verifica avviene, e dira' vero o falso;
    - 'metodo_sconosciuto' la verifica SOLLEVA ValueError. auth.py:422 non la
                           cattura: il login risponde 500. E' il regalo di una
                           migrazione da werkzeug 2, dove le impronte erano
                           'sha256$sale$impronta';
    - 'malformata'         non ha la forma metodo$sale$impronta, quindi
                           check_password_hash torna False senza sollevare
                           (e' il caso del sentinella '!utente-eliminato').
    """
    parti = (password_hash or '').split('$', 2)
    if len(parti) != 3:
        return 'malformata'
    metodo = parti[0].split(':')[0]
    return 'ok' if metodo in METODI_VERIFICABILI else 'metodo_sconosciuto'


def elenco(conn, struttura_id=None):
    sql = ("SELECT id, email, nome, cognome, ruolo, struttura_id, attivo, "
           "       eliminato_il, password_hash "
           "FROM utenti")
    parametri = ()
    if struttura_id is not None:
        sql += ' WHERE struttura_id = ?'
        parametri = (struttura_id,)
    sql += ' ORDER BY ruolo, email'

    righe = []
    for r in conn.execute(sql, parametri):
        voce = dict(r)
        voce['impronta'] = stato_impronta(voce.pop('password_hash'))
        righe.append(voce)
    return righe


def _adesso():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def imposta_password(conn, email, password):
    """Nuova password per un account esistente, che viene anche riattivato.

    Riattivare fa parte dell'operazione: chi arriva qui lo fa perche' non
    riesce ad entrare, e un attivo = 0 lasciato in piedi restituirebbe
    'credenziali non valide' su una password appena impostata - esattamente
    il vicolo cieco che questo strumento serve a togliere di mezzo.
    """
    errori = valida_password(password)
    if errori:
        raise PasswordDebole(', '.join(errori))
    riga = conn.execute('SELECT id FROM utenti WHERE email = ?', (email,)).fetchone()
    if riga is None:
        raise UtenteInesistente(f"Nessun utente con indirizzo {email}.")
    conn.execute(
        "UPDATE utenti SET password_hash = ?, attivo = 1, primo_accesso = 1, "
        "reset_hash = NULL, reset_scadenza = NULL, updated_at = ? WHERE id = ?",
        (generate_password_hash(password), _adesso(), riga[0]))
    return riga[0]


def crea_accesso(conn, email, password, ruolo, struttura_id=None,
                 nome='Nome', cognome='Cognome'):
    errori = valida_password(password)
    if errori:
        raise PasswordDebole(', '.join(errori))
    if conn.execute('SELECT 1 FROM utenti WHERE email = ?', (email,)).fetchone():
        raise EmailGiaInUso(f"L'indirizzo {email} e' gia' in uso.")
    cur = conn.execute(
        """INSERT INTO utenti (email, password_hash, nome, cognome, ruolo,
                               struttura_id, primo_accesso, attivo)
           VALUES (?, ?, ?, ?, ?, ?, 1, 1)""",
        (email, generate_password_hash(password), nome, cognome, ruolo,
         struttura_id))
    return cur.lastrowid


def esiste_accesso_valido(conn, struttura_id=None):
    """C'e' qualcuno che puo' entrare, adesso?

    Non basta contare le righe: l'utente deve essere attivo, non cancellato,
    e la sua impronta deve essere verificabile - un admin con un
    'sha256$...' addosso non e' un accesso, e' un errore 500.

    Con struttura_id, un superadmin globale conta: amministra tutte le
    strutture, quindi anche quella.
    """
    sql = ("SELECT email, password_hash, ruolo, struttura_id FROM utenti "
           "WHERE attivo = 1 AND eliminato_il IS NULL")
    for riga in conn.execute(sql):
        if stato_impronta(riga['password_hash']) != 'ok':
            continue
        if struttura_id is None:
            return True
        if riga['ruolo'] == 'superadmin':
            return True
        if riga['ruolo'] == 'admin' and riga['struttura_id'] == struttura_id:
            return True
    return False


def azzera(conn, *, struttura_id=None, definitivo=False, rimpiazzo=None):
    """Cancella gli utenti in ambito e conserva tutto il resto.

    Non apre ne' chiude la transazione: e' il chiamante a farlo, ed e' il
    motivo per cui l'accesso di rimpiazzo puo' nascere nello stesso istante
    in cui muoiono gli altri. Se alla fine nessuno puo' entrare, solleva
    AccessoNonGarantito e il chiamante annulla: un'installazione senza
    accesso e' esattamente il guasto che questo strumento ripara, non uno
    che deve saper produrre.

    definitivo=False (predefinito) lascia le righe come voci storiche e non
    tocca le otto colonne *_by: su un registro di elettromedicali 'chi ha
    inserito questo apparecchio' e' tracciabilita'. definitivo=True cancella
    le righe e azzera quei riferimenti.
    """
    from utente_service import cancella_utente
    from struttura_service import _rimuovi_utenti

    sql = 'SELECT id, email FROM utenti WHERE eliminato_il IS NULL'
    parametri = ()
    if struttura_id is not None:
        sql += ' AND struttura_id = ?'
        parametri = (struttura_id,)
    bersagli = conn.execute(sql, parametri).fetchall()
    coinvolti = [r['email'] for r in bersagli]
    ids = [r['id'] for r in bersagli]

    if definitivo:
        # _rimuovi_utenti non tocca sessioni ne' utenti_divisioni: ci pensano
        # le FOREIGN KEY ... ON DELETE CASCADE, ma solo se sono accese.
        conn.execute('PRAGMA foreign_keys = ON')
        _rimuovi_utenti(conn, ids, annota_email=True)
    else:
        for utente_id in ids:
            cancella_utente(conn, utente_id)

    rimpiazzo_id = None
    if rimpiazzo is not None:
        rimpiazzo_id = crea_accesso(
            conn, rimpiazzo.email, rimpiazzo.password, rimpiazzo.ruolo,
            rimpiazzo.struttura_id, rimpiazzo.nome, rimpiazzo.cognome)

    if not esiste_accesso_valido(conn, struttura_id):
        raise AccessoNonGarantito(
            "L'operazione lascerebbe l'installazione senza nessun accesso "
            "valido. Indica un accesso di rimpiazzo (--nuovo-admin EMAIL).")

    ambito = f'struttura {struttura_id}' if struttura_id else 'tutte le strutture'
    semantica = 'definitivo' if definitivo else 'conservativo'
    conn.execute(
        """INSERT INTO log_attivita (utente_id, azione, entita, dettagli, struttura_id)
           VALUES (NULL, 'azzeramento_utenti', 'utenti', ?, ?)""",
        (f'manutenzione.py: azzeramento {semantica} su {ambito}, '
         f'{len(coinvolti)} utenti', struttura_id))

    return {'coinvolti': coinvolti, 'semantica': semantica,
            'rimpiazzo_id': rimpiazzo_id}
