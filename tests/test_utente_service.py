"""La cancellazione di un utente: l'accesso muore, la storia resta.

E' l'opposto di struttura_service._rimuovi_utenti, che azzera gli otto
riferimenti perche' li' sparisce tutta la struttura. Qui i riferimenti sono
esattamente cio' che si vuole conservare.
"""
import os
import sqlite3

import pytest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def conn(tmp_path):
    """Una struttura con due admin, un utente e un apparecchio inserito da lui."""
    percorso = str(tmp_path / 'prova.db')
    con = sqlite3.connect(percorso)
    with open(os.path.join(RADICE, 'schema.sql'), encoding='utf-8') as f:
        con.executescript(f.read())
    con.execute("PRAGMA foreign_keys = ON")

    s = con.execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
    d = con.execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Ocu','OCU',?)",
                    (s,)).lastrowid
    ids = {}
    for etichetta, email, ruolo in (('admin1', 'admin1@a.it', 'admin'),
                                    ('admin2', 'admin2@a.it', 'admin'),
                                    ('mario', 'mario@a.it', 'utente')):
        ids[etichetta] = con.execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id) "
            "VALUES (?,'hash-vero','N','C',?,?)", (email, ruolo, s)).lastrowid
    ap = con.execute(
        "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato,created_by) "
        "VALUES (?,?,'M-1','REXXAM','OZY','funzionante',?)", (d, s, ids['mario'])).lastrowid
    con.execute("INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento,created_by) "
                "VALUES (?,'preventiva','2026-01-01',?)", (ap, ids['mario']))
    con.execute("INSERT INTO utenti_divisioni (utente_id,divisione_id,ruolo_divisione) "
                "VALUES (?,?,'utente')", (ids['mario'], d))
    con.execute("INSERT INTO sessioni (utente_id,token,expires_at) "
                "VALUES (?, 'tok', datetime('now','+1 day'))", (ids['mario'],))
    con.commit()
    return con, ids, s, ap


def test_chi_ha_inserito_l_apparecchio_si_legge_ancora(conn):
    """L'unica asserzione che distingue questa soluzione dalla cancellazione
    fisica, ed e' la ragione per cui e' stata scelta."""
    from utente_service import cancella_utente
    con, ids, _s, ap = conn
    cancella_utente(con, ids['mario'])
    con.commit()

    autore = con.execute(
        "SELECT u.nome, u.cognome FROM apparecchi a JOIN utenti u ON u.id = a.created_by "
        "WHERE a.id = ?", (ap,)).fetchone()
    assert autore == ('N', 'C')


def test_l_account_e_distrutto(conn):
    from utente_service import cancella_utente, PASSWORD_INUTILIZZABILE
    con, ids, _s, _ap = conn
    cancella_utente(con, ids['mario'])
    con.commit()

    riga = con.execute(
        "SELECT email, password_hash, attivo, eliminato_il FROM utenti WHERE id = ?",
        (ids['mario'],)).fetchone()
    assert riga[0] != 'mario@a.it'          # spostata
    assert riga[1] == PASSWORD_INUTILIZZABILE
    assert riga[2] == 0
    assert riga[3] is not None


def test_l_indirizzo_torna_libero(conn):
    """Se la persona rientra fra due anni le si crea un account nuovo con la
    stessa email. La colonna e' UNIQUE, quindi senza spostare la vecchia non si
    potrebbe."""
    from utente_service import cancella_utente
    con, ids, s, _ap = conn
    cancella_utente(con, ids['mario'])
    con.commit()

    con.execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id) "
                "VALUES ('mario@a.it','nuovo','M','R','utente',?)", (s,))
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM utenti WHERE email = 'mario@a.it'").fetchone()[0] == 1


def test_due_cancellazioni_dello_stesso_indirizzo_non_collidono(conn):
    """Mario cancellato, ricreato, ricancellato: la forma spostata contiene
    l'id, che e' diverso, quindi le due voci storiche convivono."""
    from utente_service import cancella_utente
    con, ids, s, _ap = conn
    cancella_utente(con, ids['mario'])
    con.commit()
    nuovo = con.execute(
        "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id) "
        "VALUES ('mario@a.it','nuovo','M','R','utente',?)", (s,)).lastrowid
    con.commit()

    cancella_utente(con, nuovo)
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM utenti WHERE nome='M' OR nome='N'").fetchone()[0] >= 2


def test_sessioni_e_assegnazioni_spariscono(conn):
    """L'utente esce subito, non al prossimo accesso; e le assegnazioni a
    divisioni senza account non significano piu' niente."""
    from utente_service import cancella_utente
    con, ids, _s, _ap = conn
    cancella_utente(con, ids['mario'])
    con.commit()

    assert con.execute("SELECT COUNT(*) FROM sessioni WHERE utente_id = ?",
                       (ids['mario'],)).fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM utenti_divisioni WHERE utente_id = ?",
                       (ids['mario'],)).fetchone()[0] == 0


def test_nome_ruolo_e_struttura_restano(conn):
    from utente_service import cancella_utente
    con, ids, s, _ap = conn
    cancella_utente(con, ids['mario'])
    con.commit()
    riga = con.execute("SELECT nome, cognome, ruolo, struttura_id FROM utenti WHERE id = ?",
                       (ids['mario'],)).fetchone()
    assert riga == ('N', 'C', 'utente', s)


def test_restituisce_l_email_originale_e_i_conteggi(conn):
    """Il registro deve poter dire chi era: dopo la cancellazione nel database
    c'e' solo la forma spostata."""
    from utente_service import cancella_utente
    con, ids, s, _ap = conn
    esito = cancella_utente(con, ids['mario'])
    con.commit()

    assert esito['email'] == 'mario@a.it'
    assert esito['ruolo'] == 'utente'
    assert esito['struttura_id'] == s
    assert esito['conteggi']['apparecchi'] == 1
    assert esito['conteggi']['manutenzioni'] == 1


def test_conteggi_riferimenti_conta_tutte_le_colonne(conn):
    """apparecchi compare due volte in RIFERIMENTI_UTENTE (created_by e
    updated_by): il conteggio per tabella deve sommarle, non sovrascriverle."""
    from utente_service import conteggi_riferimenti
    con, ids, _s, ap = conn
    con.execute("UPDATE apparecchi SET updated_by = ? WHERE id = ?", (ids['mario'], ap))
    con.commit()
    assert conteggi_riferimenti(con, ids['mario'])['apparecchi'] == 2
