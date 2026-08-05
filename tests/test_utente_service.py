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


def test_l_ultimo_admin_di_una_struttura_non_si_cancella(conn):
    """Senza, quella struttura resta senza nessuno che possa amministrarla."""
    from utente_service import motivo_rifiuto, cancella_utente
    con, ids, _s, _ap = conn
    assert motivo_rifiuto(con, ids['admin1']) is None   # ce ne sono due
    cancella_utente(con, ids['admin1'])
    con.commit()
    assert motivo_rifiuto(con, ids['admin2']) == 'ultimo_admin'


def test_un_admin_disattivato_non_conta_come_superstite(conn):
    """Rovescia una scelta precedente, che contava tutti gli admin esistenti
    (attivi o no) per non obbligare l'operatore a ragionare sullo stato di
    attivazione mentre cancellava. La giustificazione era che un admin
    disattivato "si riattiva con un clic da chiunque altro amministri la
    struttura" — falsa proprio qui, dove il conteggio decide: admin2 e'
    disattivato e admin1 e' l'unico rimasto in grado di entrare, quindi dopo
    la sua cancellazione in questa struttura non c'e' nessuno che possa
    riattivare admin2. Il rimedio (riattivare admin2, o nominare un altro
    admin) e' un passo in piu' per l'operatore, ma e' l'unico che non lascia
    la struttura senza amministratori."""
    from utente_service import motivo_rifiuto
    con, ids, _s, _ap = conn
    con.execute("UPDATE utenti SET attivo = 0 WHERE id = ?", (ids['admin2'],))
    con.commit()
    assert motivo_rifiuto(con, ids['admin1']) == 'ultimo_admin'


def test_l_ultimo_superadmin_non_si_cancella(conn):
    from utente_service import motivo_rifiuto
    con, _ids, _s, _ap = conn
    sa = con.execute(
        "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo) "
        "VALUES ('super@x.it','h','S','S','superadmin')").lastrowid
    con.commit()
    assert motivo_rifiuto(con, sa) == 'ultimo_superadmin'


def test_con_due_superadmin_si_puo_cancellare(conn):
    from utente_service import motivo_rifiuto
    con, _ids, _s, _ap = conn
    uno = con.execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo) "
                      "VALUES ('s1@x.it','h','S','1','superadmin')").lastrowid
    con.execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo) "
                "VALUES ('s2@x.it','h','S','2','superadmin')")
    con.commit()
    assert motivo_rifiuto(con, uno) is None


def test_un_superadmin_disattivato_non_conta_come_superstite(conn):
    """Come per gli admin di struttura, e con meno rimedi: se l'unico altro
    superadmin e' disattivato, cancellare questo lascia il deployment senza
    nessuno che possa riattivarlo — non c'e' un ruolo piu' alto a cui
    chiedere, si esce solo da riga di comando."""
    from utente_service import motivo_rifiuto
    con, _ids, _s, _ap = conn
    uno = con.execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo) "
                      "VALUES ('s1@x.it','h','S','1','superadmin')").lastrowid
    con.execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,attivo) "
                "VALUES ('s2@x.it','h','S','2','superadmin',0)")
    con.commit()
    assert motivo_rifiuto(con, uno) == 'ultimo_superadmin'


def test_un_utente_gia_cancellato_non_si_ricancella(conn):
    from utente_service import motivo_rifiuto, cancella_utente
    con, ids, _s, _ap = conn
    cancella_utente(con, ids['mario'])
    con.commit()
    assert motivo_rifiuto(con, ids['mario']) == 'gia_cancellato'


def test_un_admin_gia_cancellato_non_conta_come_ultimo(conn):
    """admin2 cancellato non e' piu' un amministratore della struttura: se
    contasse, admin1 risulterebbe cancellabile mentre e' l'unico rimasto."""
    from utente_service import motivo_rifiuto, cancella_utente
    con, ids, _s, _ap = conn
    cancella_utente(con, ids['admin2'])
    con.commit()
    assert motivo_rifiuto(con, ids['admin1']) == 'ultimo_admin'


def test_utente_inesistente(conn):
    from utente_service import motivo_rifiuto
    con, _ids, _s, _ap = conn
    assert motivo_rifiuto(con, 99999) == 'inesistente'
