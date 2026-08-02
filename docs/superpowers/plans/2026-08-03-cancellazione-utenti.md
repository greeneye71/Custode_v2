# Cancellazione degli utenti — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cancellare un utente togliendogli l'accesso senza cancellare chi ha inserito cosa.

**Architecture:** Un modulo `utente_service.py` senza Flask, come `struttura_service.py`: riceve una `sqlite3.Connection`, il chiamante apre e chiude la transazione. La riga di `utenti` sopravvive come voce storica con l'account distrutto, cosi' le otto chiavi esterne `*_by` continuano a risolvere. Le rotte in `admin.py` fanno autorizzazione, conferma e registro.

**Tech Stack:** Python 3.13, Flask 3.x, SQLite3, Jinja2, Bootstrap 5, pytest.

**Spec:** `docs/superpowers/specs/2026-08-02-cancellazione-utenti-design.md`

## Global Constraints

- **Punto di partenza:** branch `feat/2.6.2` creato da `main` a `b2d08da` (MedInventory 2.6.1), **245 test verdi**. Verificare con `python -m pytest tests/ -q` prima di iniziare.
- Questo e' il **primo** di cinque piani della 2.6.2. Non toccare la versione: la release la chiude l'ultimo piano.
- **Lingua italiana** per interfaccia, commenti, nomi di variabili, valori di database e messaggi di commit.
- **`utente_service.py` non importa Flask** e non deve mai importarlo.
- **SQL parametrizzato**: sempre `?`. I nomi di tabella e colonna che finiscono in una f-string vengono da costanti del modulo, mai dal form.
- **CSRF**: ogni form POST porta `{{ csrf_token() }}`.
- **`log_attivita()`** per la cancellazione, **dentro la transazione, prima del commit**, e con `struttura_id` valorizzato. Sono le due lezioni della 2.6.1.
- **MAI PowerShell `Get-Content`/`Set-Content` su file sorgente**: corrompe i caratteri tipografici e aggiunge un BOM.
- **Ogni modifica a un test preesistente va dichiarata** nel rapporto, con il perche'.
- Non scrivere nel database, in `uploads/` o in `backups/` reali del repository.

## Struttura dei file

| File | Responsabilita' |
|---|---|
| `utente_service.py` (nuovo) | `cancella_utente()`, `motivo_rifiuto()`, `conteggi_riferimenti()`, `email_liberata()`. Nessun Flask. |
| `schema.sql` (modifica) | la colonna `eliminato_il` per le installazioni nuove |
| `models.py` (modifica) | la stessa colonna per le installazioni esistenti, in `apply_schema_updates()` |
| `admin.py` (modifica) | rotte di conferma ed esecuzione; filtro degli elenchi; protezione di tecnici e superadmin nel modulo generico; `tecnico_elimina` portata sulla primitiva |
| `strutture_bp.py` (modifica) | i due elenchi della scheda struttura |
| `struttura_service.py` (modifica) | il conteggio utenti di `contenuto_struttura` |
| `templates/admin/utente_elimina.html` (nuovo) | la pagina di conferma |
| `templates/admin/utenti.html` (modifica) | «Elimina» al posto di «Disattiva» |
| `templates/admin/utente_form.html` (modifica) | la casella `attivo` |
| `tests/test_utente_service.py` (nuovo) | la primitiva, su database temporanei |
| `tests/test_utenti_routes.py` (nuovo) | autorizzazione, rifiuti, elenchi, giro completo |

---

## Task 1: La colonna `eliminato_il`

**Files:**
- Modify: `schema.sql` (tabella `utenti`)
- Modify: `models.py` (lista `migrations` in `apply_schema_updates`, riga ~386)
- Test: `tests/test_migrazioni.py`

**Interfaces:**
- Produces: la colonna `utenti.eliminato_il DATETIME`, `NULL` per un utente normale.

- [ ] **Step 1: Scrivere il test che fallisce**

In coda a `tests/test_migrazioni.py`:

```python
def test_la_colonna_eliminato_il_arriva_anche_su_un_database_esistente(app):
    """La colonna sta in schema.sql per le installazioni nuove, ma
    un'installazione gia' in servizio non riesegue schema.sql sulle tabelle che
    esistono gia' (sono tutte CREATE TABLE IF NOT EXISTS): serve la migrazione
    incrementale, che gira a ogni avvio."""
    from models import get_db, apply_schema_updates
    with app.app_context():
        db = get_db()
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("ALTER TABLE utenti DROP COLUMN eliminato_il")
        db.commit()
        assert 'eliminato_il' not in [r[1] for r in db.execute("PRAGMA table_info(utenti)")]

        apply_schema_updates()

        colonne = [r[1] for r in db.execute("PRAGMA table_info(utenti)")]
        assert 'eliminato_il' in colonne
        # E gli utenti esistenti non risultano cancellati.
        assert db.execute(
            "SELECT COUNT(*) FROM utenti WHERE eliminato_il IS NOT NULL").fetchone()[0] == 0
```

- [ ] **Step 2: Eseguire il test per verificare che fallisca**

Run: `python -m pytest tests/test_migrazioni.py -q`
Expected: FAIL, `no such column: eliminato_il` al `DROP COLUMN`.

- [ ] **Step 3: Aggiungere la colonna in `schema.sql`**

Nella tabella `utenti`, dopo `struttura_id INTEGER,   -- NULL per superadmin`:

```sql
  -- Valorizzata = utente cancellato: l'account e' distrutto ma la riga resta,
  -- perche' otto colonne *_by referenziano utenti(id) e su un registro di
  -- elettromedicali "chi ha inserito questo apparecchio" non deve sparire.
  eliminato_il DATETIME,
```

- [ ] **Step 4: Aggiungere la migrazione in `models.py`**

Nella lista `migrations`, in coda:

```python
        # Cancellazione degli utenti (2.6.2): la riga sopravvive come voce
        # storica, questa colonna la distingue da un utente normale.
        "ALTER TABLE utenti ADD COLUMN eliminato_il DATETIME",
```

- [ ] **Step 5: Eseguire i test**

Run: `python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 6: Provare la sensibilita'**

Togli la riga aggiunta alla lista `migrations` ed esegui
`python -m pytest tests/test_migrazioni.py -q`: deve fallire il test nuovo con
`assert 'eliminato_il' in [...]`. Rimetti a posto e verifica con `git diff` che il
file sia tornato identico.

- [ ] **Step 7: Commit**

```bash
git add schema.sql models.py tests/test_migrazioni.py
git commit -m "feat(utenti): colonna eliminato_il per la cancellazione"
```

---

## Task 2: La primitiva di cancellazione

**Files:**
- Create: `utente_service.py`
- Test: `tests/test_utente_service.py`

**Interfaces:**
- Consumes: `struttura_service.RIFERIMENTI_UTENTE` (otto coppie tabella/colonna).
- Produces:
  - `utente_service.PASSWORD_INUTILIZZABILE` — stringa costante
  - `utente_service.email_liberata(email, utente_id) -> str`
  - `utente_service.conteggi_riferimenti(conn, utente_id) -> dict` — chiavi: i nomi di tabella di `RIFERIMENTI_UTENTE`, valori interi
  - `utente_service.cancella_utente(conn, utente_id) -> dict` — chiavi `email` (l'originale), `nome`, `cognome`, `ruolo`, `struttura_id`, `conteggi`

Il chiamante apre e chiude la transazione, come per `struttura_service.rimuovi_strutture`.

- [ ] **Step 1: Scrivere i test che falliscono**

Crea `tests/test_utente_service.py`:

```python
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
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `python -m pytest tests/test_utente_service.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'utente_service'`.

- [ ] **Step 3: Scrivere `utente_service.py`**

```python
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
```

- [ ] **Step 4: Eseguire i test**

Run: `python -m pytest tests/test_utente_service.py -q`
Expected: PASS, 8 test.

- [ ] **Step 5: Provare la sensibilita'**

Due prove, ognuna seguita da `git checkout -- utente_service.py` e da un `git diff` vuoto:

1. Aggiungi in `cancella_utente`, prima dell'UPDATE, l'azzeramento dei riferimenti
   che `struttura_service._rimuovi_utenti` fa —
   `for t, c in RIFERIMENTI_UTENTE: conn.execute(f"UPDATE {t} SET {c} = NULL WHERE {c} = ?", (utente_id,))`
   — cioe' la confusione fra le due primitive che il docstring mette in guardia:
   deve fallire `test_chi_ha_inserito_l_apparecchio_si_legge_ancora`.
2. In `conteggi_riferimenti` sostituisci `conteggi.get(tabella, 0) + n` con `n`:
   deve fallire `test_conteggi_riferimenti_conta_tutte_le_colonne` con `assert 1 == 2`.

- [ ] **Step 6: Commit**

```bash
git add utente_service.py tests/test_utente_service.py
git commit -m "feat(utenti): primitiva di cancellazione che conserva l'autore"
```

---

## Task 3: I rifiuti

**Files:**
- Modify: `utente_service.py`
- Test: `tests/test_utente_service.py`

**Interfaces:**
- Consumes: la colonna `eliminato_il` (Task 1).
- Produces: `utente_service.motivo_rifiuto(conn, utente_id) -> str | None` — restituisce
  `'inesistente'`, `'gia_cancellato'`, `'ultimo_admin'`, `'ultimo_superadmin'`, oppure
  `None` se la cancellazione e' ammessa.

I rifiuti che dipendono da **chi chiede** (se stessi, l'ambito dell'admin) restano nella
rotta: questa funzione vede solo il database.

- [ ] **Step 1: Scrivere i test che falliscono**

In coda a `tests/test_utente_service.py`:

```python
def test_l_ultimo_admin_di_una_struttura_non_si_cancella(conn):
    """Senza, quella struttura resta senza nessuno che possa amministrarla."""
    from utente_service import motivo_rifiuto, cancella_utente
    con, ids, _s, _ap = conn
    assert motivo_rifiuto(con, ids['admin1']) is None   # ce ne sono due
    cancella_utente(con, ids['admin1'])
    con.commit()
    assert motivo_rifiuto(con, ids['admin2']) == 'ultimo_admin'


def test_si_contano_tutti_gli_admin_esistenti_non_solo_gli_attivi(conn):
    """Il freno non deve obbligare l'operatore a ragionare sullo stato di
    attivazione mentre sta cancellando."""
    from utente_service import motivo_rifiuto
    con, ids, _s, _ap = conn
    con.execute("UPDATE utenti SET attivo = 0 WHERE id = ?", (ids['admin2'],))
    con.commit()
    assert motivo_rifiuto(con, ids['admin1']) is None


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
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `python -m pytest tests/test_utente_service.py -q`
Expected: FAIL, `ImportError: cannot import name 'motivo_rifiuto'`.

- [ ] **Step 3: Implementare**

In coda a `utente_service.py`:

```python
def motivo_rifiuto(conn, utente_id):
    """Perche' questo utente non si puo' cancellare, o None se si puo'.

    Guarda solo il database: i rifiuti che dipendono da CHI chiede (se stessi,
    l'ambito dell'admin) stanno nella rotta, che e' l'unica a sapere chi e'
    l'utente corrente.

    Un utente gia' cancellato non conta come amministratore della struttura:
    se contasse, l'ultimo admin rimasto risulterebbe cancellabile.
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
            "AND eliminato_il IS NULL AND id != ?", (utente_id,)).fetchone()[0]
        if rimasti == 0:
            return 'ultimo_superadmin'

    if ruolo == 'admin' and struttura_id is not None:
        # Tutti gli admin esistenti, attivi o no: il freno non deve obbligare
        # a ragionare sullo stato di attivazione mentre si cancella.
        rimasti = conn.execute(
            "SELECT COUNT(*) FROM utenti WHERE ruolo = 'admin' AND struttura_id = ? "
            "AND eliminato_il IS NULL AND id != ?", (struttura_id, utente_id)).fetchone()[0]
        if rimasti == 0:
            return 'ultimo_admin'

    return None
```

- [ ] **Step 4: Eseguire i test**

Run: `python -m pytest tests/test_utente_service.py -q`
Expected: PASS, 15 test.

- [ ] **Step 5: Provare la sensibilita'**

Due prove, con ripristino e `git diff` vuoto dopo ognuna:

1. Togli `AND eliminato_il IS NULL` dal conteggio degli admin: deve fallire
   `test_un_admin_gia_cancellato_non_conta_come_ultimo`.
2. Aggiungi `AND attivo = 1` allo stesso conteggio: deve fallire
   `test_si_contano_tutti_gli_admin_esistenti_non_solo_gli_attivi`.

- [ ] **Step 6: Commit**

```bash
git add utente_service.py tests/test_utente_service.py
git commit -m "feat(utenti): i rifiuti che proteggono l'ultimo amministratore"
```

---

## Task 4: Le rotte e la pagina di conferma

**Files:**
- Modify: `admin.py` (dopo `utente_reset_password`, riga ~327)
- Create: `templates/admin/utente_elimina.html`
- Test: `tests/test_utenti_routes.py`

**Interfaces:**
- Consumes: `utente_service.cancella_utente`, `motivo_rifiuto`, `conteggi_riferimenti`;
  `admin._check_utente_scope` (esistente).
- Produces: le rotte `admin.utente_elimina_conferma` (`GET /admin/utenti/<id>/elimina`) e
  `admin.utente_elimina` (`POST /admin/utenti/<id>/elimina`).

- [ ] **Step 1: Scrivere i test che falliscono**

Crea `tests/test_utenti_routes.py`:

```python
"""Le rotte della cancellazione utenti: chi puo', cosa si rifiuta, cosa resta."""
import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def dati(app):
    from models import execute
    with app.app_context():
        a = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        b = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica B','B',1)").lastrowid
        da = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Ocu','OCU',?)",
                     (a,)).lastrowid
        h = generate_password_hash('Passw0rd!')
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,primo_accesso) "
                "VALUES ('super@x.it',?,'S','S','superadmin',0)", (h,))
        admin_a = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('admin@a.it',?,'A','A','admin',?,0)", (h, a)).lastrowid
        secondo = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('admin2@a.it',?,'A','Due','admin',?,0)", (h, a)).lastrowid
        mario = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('mario@a.it',?,'M','Rossi','utente',?,0)", (h, a)).lastrowid
        altrui = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('utente@b.it',?,'U','B','utente',?,0)", (h, b)).lastrowid
        tec = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('tec@x.it',?,'T','T','tecnico',NULL,0)", (h,)).lastrowid
        execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,"
                "stato,created_by) VALUES (?,?,'M-1','REXXAM','OZY','funzionante',?)",
                (da, a, mario))
    return {'a': a, 'admin_a': admin_a, 'secondo': secondo, 'mario': mario,
            'altrui': altrui, 'tec': tec}


def entra(client, email):
    client.post('/login', data={'email': email, 'password': 'Passw0rd!'})


def test_la_pagina_di_conferma_dice_chi_si_sta_cancellando(client, dati):
    """Non si digita nulla per confermare: l'unica difesa contro "ho cliccato la
    riga sbagliata" e' che la pagina dica di chi si tratta."""
    entra(client, 'admin@a.it')
    testo = client.get(f"/admin/utenti/{dati['mario']}/elimina").get_data(as_text=True)
    assert 'mario@a.it' in testo
    assert 'Rossi' in testo


def test_la_pagina_di_conferma_dice_cosa_resta(client, dati):
    entra(client, 'admin@a.it')
    testo = client.get(f"/admin/utenti/{dati['mario']}/elimina").get_data(as_text=True)
    assert 'non e' in testo.lower() and 'reversibile' in testo.lower()


def test_la_cancellazione_riuscita(client, app, dati):
    from models import query_one
    entra(client, 'admin@a.it')
    r = client.post(f"/admin/utenti/{dati['mario']}/elimina", follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        riga = query_one("SELECT email, eliminato_il FROM utenti WHERE id=?", (dati['mario'],))
        assert riga['eliminato_il'] is not None
        assert riga['email'] != 'mario@a.it'


def test_l_ultimo_admin_non_si_cancella_dalla_rotta(client, app, dati):
    from models import query_one
    entra(client, 'super@x.it')
    client.post(f"/admin/utenti/{dati['secondo']}/elimina", follow_redirects=True)
    r = client.post(f"/admin/utenti/{dati['admin_a']}/elimina", follow_redirects=True)
    assert 'amministratore' in r.get_data(as_text=True).lower()
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['admin_a'],))['eliminato_il'] is None


def test_nessuno_cancella_se_stesso(client, app, dati):
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['admin_a']}/elimina", follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['admin_a'],))['eliminato_il'] is None


def test_un_admin_non_cancella_utenti_di_altre_strutture(client, app, dati):
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['altrui']}/elimina", follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['altrui'],))['eliminato_il'] is None


def test_un_admin_non_cancella_un_tecnico(client, app, dati):
    """Un tecnico e' un account condiviso fra strutture, non proprieta' di una."""
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['tec']}/elimina", follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['tec'],))['eliminato_il'] is None


def test_il_registro_conserva_l_email_originale(client, app, dati):
    """Dopo la cancellazione nel database c'e' solo la forma spostata."""
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['mario']}/elimina", follow_redirects=True)
    with app.app_context():
        voce = query_one("SELECT dettagli, struttura_id FROM log_attivita "
                         "WHERE azione='eliminazione' AND entita='utenti'")
        assert voce is not None
        assert 'mario@a.it' in voce['dettagli']
        assert voce['struttura_id'] == dati['a']


def test_un_utente_cancellato_non_entra_piu(client, app, dati):
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['mario']}/elimina", follow_redirects=True)
    client.get('/logout')
    r = client.post('/login', data={'email': 'mario@a.it', 'password': 'Passw0rd!'},
                    follow_redirects=True)
    assert 'dashboard' not in r.get_data(as_text=True).lower()
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `python -m pytest tests/test_utenti_routes.py -q`
Expected: FAIL, 404 sulle rotte inesistenti.

- [ ] **Step 3: Aggiungere le rotte**

In `admin.py`, dopo `utente_reset_password`:

```python
MESSAGGI_RIFIUTO = {
    'inesistente': 'Utente non trovato.',
    'gia_cancellato': 'Questo utente e\' gia\' stato cancellato.',
    'ultimo_admin': "E' l'ultimo amministratore della struttura: senza di lui "
                    "nessuno potrebbe piu' gestirla. Nomina prima un altro "
                    "amministratore, poi cancella questo.",
    'ultimo_superadmin': "E' l'ultimo superamministratore: cancellandolo nessuno "
                         "potrebbe piu' creare strutture, fare backup o riparare "
                         "una struttura rimasta senza amministratore.",
}


def _utente_cancellabile(id):
    """(utente, messaggio_di_rifiuto). Se il messaggio c'e', non si procede."""
    utente = query_one("SELECT * FROM utenti WHERE id = ?", (id,))
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
        db.commit()
    except Exception as e:
        db.rollback()
        current_app.logger.error(f'Cancellazione utente {id} fallita: {e}', exc_info=True)
        flash("Cancellazione fallita, nulla e' stato modificato. Controlla il log.", 'danger')
        return redirect(url_for('admin.utenti'))

    flash(f"Utente {esito['nome']} {esito['cognome']} eliminato. "
          f"L'indirizzo {esito['email']} e' di nuovo utilizzabile.", 'success')
    return redirect(url_for('admin.utenti'))
```

Verifica che `current_app` sia fra gli import di `admin.py`; se manca, aggiungilo alla riga
di `flask`.

- [ ] **Step 4: Creare `templates/admin/utente_elimina.html`**

```html
{% extends "base.html" %}
{% block title %}Elimina utente{% endblock %}
{% block content %}
<h2 class="mb-4"><i class="bi bi-person-x text-danger me-2"></i>Elimina utente</h2>

<div class="card border-danger shadow-sm mb-4">
  <div class="card-body">
    <h4 class="mb-1">{{ utente.nome }} {{ utente.cognome }}</h4>
    <p class="mb-2">
      <code>{{ utente.email }}</code>
      <span class="badge bg-secondary ms-2">{{ utente.ruolo }}</span>
    </p>
    <p class="text-muted mb-0">
      {% if utente.struttura_nome %}Struttura: {{ utente.struttura_nome }}{% endif %}
    </p>
  </div>
</div>

<ul class="mb-4">
  <li><strong>L'operazione non e' reversibile.</strong> A differenza della
      disattivazione, non esiste un modo per riportarlo indietro: chi sbaglia
      ricrea l'account e riassegna le divisioni.</li>
  <li>Il suo <strong>nome resta</strong> sulle schede che ha inserito:
    {% set totale = conteggi.values() | sum %}
    {% if totale %}{{ totale }} righe fra apparecchi, interventi e documenti
    continueranno a dire che le ha inserite lui.
    {% else %}non ha inserito nulla.{% endif %}</li>
  <li>L'indirizzo <code>{{ utente.email }}</code> <strong>torna libero</strong>
      per un account nuovo.</li>
</ul>

<form method="post" action="{{ url_for('admin.utente_elimina', id=utente.id) }}">
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
  <div class="d-flex gap-2">
    <button class="btn btn-danger">Elimina definitivamente</button>
    <a href="{{ url_for('admin.utenti') }}" class="btn btn-outline-secondary">Annulla</a>
  </div>
</form>
{% endblock %}
```

- [ ] **Step 5: Eseguire i test**

Run: `python -m pytest tests/ -q`
Expected: PASS. Dichiara il numero reale.

- [ ] **Step 6: Provare la sensibilita'**

Tre prove, con ripristino e `git diff` vuoto dopo ognuna:

1. Togli da `_utente_cancellabile` il controllo `utente['id'] == g.user['id']`: deve
   fallire `test_nessuno_cancella_se_stesso`.
2. Togli la chiamata a `motivo_rifiuto`: deve fallire
   `test_l_ultimo_admin_non_si_cancella_dalla_rotta`.
3. Togli `esito['struttura_id']` dalla chiamata a `log_attivita` (ultimo parametro): deve
   fallire `test_il_registro_conserva_l_email_originale` sull'asserzione di
   `struttura_id`.

- [ ] **Step 7: Commit**

```bash
git add admin.py templates/admin/utente_elimina.html tests/test_utenti_routes.py
git commit -m "feat(utenti): cancellazione con pagina di conferma e registro"
```

---

## Task 5: Gli elenchi

**Files:**
- Modify: `admin.py:96`, `admin.py:106`, `admin.py:1218`
- Modify: `strutture_bp.py:389`, `strutture_bp.py:392`
- Modify: `struttura_service.py:302`
- Test: `tests/test_utenti_routes.py`

**Interfaces:**
- Consumes: la colonna `eliminato_il` (Task 1), `cancella_utente` (Task 2).
- Produces: niente.

E' il punto in cui questa modifica puo' sbagliare in silenzio: se un elenco se ne
dimentica, un utente cancellato ricompare selezionabile. **Un test per ciascuno.**

- [ ] **Step 1: Scrivere i test che falliscono**

In coda a `tests/test_utenti_routes.py`:

```python
def _cancella(client, id):
    client.post(f"/admin/utenti/{id}/elimina", follow_redirects=True)


def test_non_compare_nell_elenco_utenti_dell_admin(client, dati):
    entra(client, 'admin@a.it')
    _cancella(client, dati['mario'])
    assert 'mario@a.it' not in client.get('/admin/utenti').get_data(as_text=True)


def test_non_compare_nell_elenco_utenti_del_superadmin(client, dati):
    entra(client, 'admin@a.it')
    _cancella(client, dati['mario'])
    client.get('/logout')
    entra(client, 'super@x.it')
    assert 'mario@a.it' not in client.get('/admin/utenti').get_data(as_text=True)


def test_un_tecnico_cancellato_non_compare_nell_elenco_tecnici(client, dati):
    entra(client, 'super@x.it')
    _cancella(client, dati['tec'])
    assert 'tec@x.it' not in client.get('/admin/tecnici').get_data(as_text=True)


def test_non_compare_nella_scheda_della_struttura(client, dati):
    entra(client, 'admin@a.it')
    _cancella(client, dati['mario'])
    client.get('/logout')
    entra(client, 'super@x.it')
    assert 'mario@a.it' not in client.get(f"/strutture/{dati['a']}").get_data(as_text=True)


def test_non_e_contato_fra_gli_utenti_della_struttura(client, app, dati):
    """contenuto_struttura conta gli utenti prima di cancellare una struttura:
    contarne di cancellati direbbe all'operatore un numero che non esiste."""
    from struttura_service import contenuto_struttura
    from models import get_db
    entra(client, 'admin@a.it')
    with app.app_context():
        prima = contenuto_struttura(get_db(), dati['a'], '/tmp/x')['utenti']
    _cancella(client, dati['mario'])
    with app.app_context():
        dopo = contenuto_struttura(get_db(), dati['a'], '/tmp/x')['utenti']
    assert dopo == prima - 1
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `python -m pytest tests/test_utenti_routes.py -q`
Expected: FAIL, gli indirizzi compaiono ancora.

- [ ] **Step 3: Filtrare i sei punti**

`admin.py:96` — il ramo superadmin non ha una `WHERE`: aggiungerla prima di `GROUP BY`:

```sql
            WHERE u.eliminato_il IS NULL
            GROUP BY u.id ORDER BY u.ruolo, u.cognome, u.nome
```

`admin.py:106` — il ramo admin:

```sql
            WHERE u.struttura_id = ? AND u.ruolo != 'superadmin' AND u.eliminato_il IS NULL
```

`admin.py:1218` — l'elenco tecnici:

```sql
        WHERE u.ruolo = 'tecnico' AND u.eliminato_il IS NULL
```

`strutture_bp.py:389`:

```python
        "SELECT nome, cognome, email, ruolo, attivo FROM utenti "
        "WHERE struttura_id = ? AND eliminato_il IS NULL ORDER BY cognome, nome", (struttura_id,))
```

`strutture_bp.py:392`:

```python
        "WHERE ts.struttura_id = ? AND u.eliminato_il IS NULL ORDER BY u.cognome, u.nome",
```

`struttura_service.py:302`:

```python
        'utenti': conta("SELECT COUNT(*) FROM utenti WHERE struttura_id = ? "
                        "AND eliminato_il IS NULL"),
```

- [ ] **Step 4: Eseguire i test**

Run: `python -m pytest tests/ -q`
Expected: PASS. Dichiara il numero reale.

- [ ] **Step 5: Provare la sensibilita', uno per uno**

Togli il filtro da **un punto per volta**, esegui i test, verifica che cada il test
corrispondente e **solo quello**, poi rimetti a posto. Cinque prove piu' quella del
conteggio. Se togliendo un filtro non cade nulla, quel punto non e' coperto: dillo invece
di aggiustarlo.

- [ ] **Step 6: Commit**

```bash
git add admin.py strutture_bp.py struttura_service.py tests/test_utenti_routes.py
git commit -m "feat(utenti): un utente cancellato sparisce da tutti gli elenchi"
```

---

## Task 6: Il modulo generico non declassa piu' nessuno

**Files:**
- Modify: `admin.py:195-276` (`utente_modifica`), `admin.py:96`
- Test: `tests/test_utenti_routes.py`

**Interfaces:**
- Consumes: niente.
- Produces: niente.

E' il difetto piu' grave chiuso da questo piano. Oggi `/admin/utenti` per il superadmin
elenca anche tecnici e superadmin, le loro schede si aprono nel modulo generico, e quel
modulo riduce il ruolo a `('admin', 'utente')` **con un'assegnazione silenziosa**
(`admin.py:240-241`). Misurato eseguendo le rotte vere: un tecnico salvato da li' diventa
`utente` con una struttura, e le sue assegnazioni in `tecnici_strutture` restano; **l'unico
superadmin che salva la propria scheda si declassa, e il deployment resta senza
superadmin** — `/strutture` 308, `/admin/backup` 302.

- [ ] **Step 1: Scrivere i test che falliscono**

In coda a `tests/test_utenti_routes.py`:

```python
def test_un_tecnico_non_compare_nell_elenco_utenti(client, dati):
    """I tecnici hanno la loro pagina, che sa gestire le assegnazioni alle
    strutture; il modulo generico no."""
    entra(client, 'super@x.it')
    assert 'tec@x.it' not in client.get('/admin/utenti').get_data(as_text=True)


def test_il_modulo_generico_rifiuta_un_tecnico(client, app, dati):
    """Un URL scritto a mano non deve poter declassare nessuno."""
    from models import query_one
    entra(client, 'super@x.it')
    client.post(f"/admin/utenti/{dati['tec']}/modifica",
                data={'nome': 'T', 'cognome': 'T', 'email': 'tec@x.it',
                      'ruolo': 'tecnico', 'struttura_id': dati['a']},
                follow_redirects=True)
    with app.app_context():
        u = query_one("SELECT ruolo, struttura_id FROM utenti WHERE id=?", (dati['tec'],))
        assert u['ruolo'] == 'tecnico'
        assert u['struttura_id'] is None


def test_salvare_un_superadmin_non_lo_declassa(client, app, dati):
    """Il caso peggiore: l'unico superadmin salva la propria scheda e il
    deployment resta senza superadmin. Misurato prima della correzione:
    ruolo 'utente', zero superadmin, /admin/backup 302."""
    from models import query_one
    entra(client, 'super@x.it')
    sa = query_one("SELECT id FROM utenti WHERE email='super@x.it'")['id']
    client.post(f"/admin/utenti/{sa}/modifica",
                data={'nome': 'S', 'cognome': 'S', 'email': 'super@x.it',
                      'ruolo': 'superadmin', 'struttura_id': dati['a']},
                follow_redirects=True)
    with app.app_context():
        u = query_one("SELECT ruolo, struttura_id FROM utenti WHERE id=?", (sa,))
        assert u['ruolo'] == 'superadmin'
        assert u['struttura_id'] is None
        assert query_one("SELECT COUNT(*) AS n FROM utenti "
                         "WHERE ruolo='superadmin'")['n'] == 1


def test_un_superadmin_puo_ancora_correggersi_il_nome(client, app, dati):
    """Non esiste una pagina di profilo: /cambio-password fa solo la password.
    Quella scheda e' l'unico posto dove un superadmin puo' correggersi."""
    from models import query_one
    entra(client, 'super@x.it')
    sa = query_one("SELECT id FROM utenti WHERE email='super@x.it'")['id']
    client.post(f"/admin/utenti/{sa}/modifica",
                data={'nome': 'Giovanni', 'cognome': 'Bergamaschi',
                      'email': 'super@x.it', 'ruolo': 'superadmin'},
                follow_redirects=True)
    with app.app_context():
        u = query_one("SELECT nome, ruolo FROM utenti WHERE id=?", (sa,))
        assert u['nome'] == 'Giovanni'
        assert u['ruolo'] == 'superadmin'


def test_un_ruolo_non_ammesso_su_un_utente_normale_e_un_errore(client, app, dati):
    """Non piu' una riscrittura muta: chi manda un valore che non esiste deve
    vedere un errore, non ritrovarsi l'utente declassato in silenzio."""
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['mario']}/modifica",
                data={'nome': 'M', 'cognome': 'Rossi', 'email': 'mario@a.it',
                      'ruolo': 'superadmin'},
                follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT ruolo FROM utenti WHERE id=?",
                         (dati['mario'],))['ruolo'] == 'utente'
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `python -m pytest tests/test_utenti_routes.py -q`
Expected: FAIL — il tecnico compare, e viene declassato.

- [ ] **Step 3: Escludere i tecnici dall'elenco del superadmin**

`admin.py:96`, la `WHERE` aggiunta al Task 5 diventa:

```sql
            WHERE u.eliminato_il IS NULL AND u.ruolo != 'tecnico'
```

- [ ] **Step 4: Proteggere tecnici e superadmin nel modulo generico**

In `utente_modifica`, subito dopo il recupero dell'utente e il controllo di scope,
**prima di qualunque altra cosa**:

```python
    if utente['ruolo'] == 'tecnico':
        flash('I tecnici si gestiscono dalla loro pagina.', 'warning')
        return redirect(url_for('admin.tecnici'))
```

E nel ramo POST, al posto di `if ruolo not in ('admin', 'utente'): ruolo = 'utente'`
(riga ~240):

```python
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
```

- [ ] **Step 5: Eseguire i test**

Run: `python -m pytest tests/ -q`
Expected: PASS. Dichiara il numero reale.

- [ ] **Step 6: Provare la sensibilita'**

Tre prove, con ripristino e `git diff` vuoto dopo ognuna:

1. Rimetti `if ruolo not in ('admin','utente'): ruolo = 'utente'` al posto del blocco
   nuovo: devono fallire `test_salvare_un_superadmin_non_lo_declassa` e
   `test_un_ruolo_non_ammesso_su_un_utente_normale_e_un_errore`.
2. Togli il rifiuto sui tecnici in `utente_modifica`: deve fallire
   `test_il_modulo_generico_rifiuta_un_tecnico`.
3. Togli `AND u.ruolo != 'tecnico'` dall'elenco: deve fallire
   `test_un_tecnico_non_compare_nell_elenco_utenti`.

- [ ] **Step 7: Commit**

```bash
git add admin.py tests/test_utenti_routes.py
git commit -m "fix(utenti): il modulo generico non declassa piu' tecnici e superadmin"
```

---

## Task 7: «Elimina» al posto di «Disattiva», e `attivo` nel modulo

**Files:**
- Modify: `templates/admin/utenti.html:82-90`
- Modify: `templates/admin/utente_form.html`
- Modify: `admin.py` (`utente_modifica`: leggere `attivo` dal form; rimuovere
  `utente_toggle` e la sua rotta)
- Test: `tests/test_utenti_routes.py`

**Interfaces:**
- Consumes: le rotte del Task 4.
- Produces: niente.

`attivo` oggi si cambia **soltanto** dal pulsante che sta per sparire, e il sistema
disattiva utenti da solo (`models.py:624`, gli orfani senza struttura), dicendo nel log
«Riassegnalo a una struttura per riabilitarlo». Senza la casella nel modulo quella frase
diventa un'istruzione impossibile.

- [ ] **Step 1: Scrivere i test che falliscono**

In coda a `tests/test_utenti_routes.py`:

```python
def test_un_utente_disattivato_si_riattiva_dal_modulo(client, app, dati):
    """Il sistema disattiva da solo gli utenti rimasti senza struttura: senza
    questa casella resterebbero disattivati per sempre."""
    from models import execute, query_one
    with app.app_context():
        execute("UPDATE utenti SET attivo = 0 WHERE id = ?", (dati['mario'],))
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['mario']}/modifica",
                data={'nome': 'M', 'cognome': 'Rossi', 'email': 'mario@a.it',
                      'ruolo': 'utente', 'attivo': '1'},
                follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT attivo FROM utenti WHERE id=?", (dati['mario'],))['attivo'] == 1


def test_si_puo_disattivare_dal_modulo(client, app, dati):
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['mario']}/modifica",
                data={'nome': 'M', 'cognome': 'Rossi', 'email': 'mario@a.it',
                      'ruolo': 'utente'},
                follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT attivo FROM utenti WHERE id=?", (dati['mario'],))['attivo'] == 0


def test_l_elenco_offre_elimina_e_non_disattiva(client, dati):
    entra(client, 'admin@a.it')
    testo = client.get('/admin/utenti').get_data(as_text=True)
    assert f"/admin/utenti/{dati['mario']}/elimina" in testo
    assert '/toggle' not in testo
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `python -m pytest tests/test_utenti_routes.py -q`
Expected: FAIL.

- [ ] **Step 3: La casella nel modulo**

In `templates/admin/utente_form.html`, dopo il campo del ruolo (riga ~48):

```html
                    {% if utente %}
                    <div class="col-12">
                        <div class="form-check">
                            <input class="form-check-input" type="checkbox" name="attivo"
                                   id="attivo" value="1"
                                   {% if form_data.get('attivo') %}checked{% endif %}>
                            <label class="form-check-label" for="attivo">
                                Account attivo
                            </label>
                            <div class="form-text">
                                Un utente rimasto senza struttura viene disattivato
                                automaticamente all'avvio: riassegnalo e riattivalo qui.
                            </div>
                        </div>
                    </div>
                    {% endif %}
```

In `utente_modifica`, nel ramo GET, aggiungi `attivo` a `form_data` con il valore
dell'utente; e nel ramo POST, prima dell'UPDATE:

```python
    attivo = 1 if form.get('attivo') else 0
```

aggiungendolo all'UPDATE dell'utente.

- [ ] **Step 4: «Elimina» al posto di «Disattiva»**

In `templates/admin/utenti.html`, sostituisci il form del toggle (righe 82-90) con:

```html
                                <a href="{{ url_for('admin.utente_elimina_conferma', id=u.id) }}"
                                   class="btn btn-outline-danger btn-action" title="Elimina">
                                    <i class="bi bi-person-x"></i>
                                </a>
```

E in `admin.py` rimuovi la rotta `utente_toggle` e la sua funzione: non ha piu' chiamanti.

- [ ] **Step 5: Eseguire i test**

Run: `python -m pytest tests/ -q`
Expected: PASS. Dichiara il numero reale. Se un test preesistente citava
`utente_toggle`, dichiaralo nel rapporto con il perche' della modifica.

- [ ] **Step 6: Provare la sensibilita'**

Togli `attivo` dall'UPDATE di `utente_modifica`: deve fallire
`test_un_utente_disattivato_si_riattiva_dal_modulo`. Ripristina e verifica `git diff`
vuoto.

- [ ] **Step 7: Commit**

```bash
git add admin.py templates/admin/utenti.html templates/admin/utente_form.html tests/test_utenti_routes.py
git commit -m "feat(utenti): Elimina al posto di Disattiva, attivo nel modulo"
```

---

## Task 8: `tecnico_elimina` sulla primitiva

**Files:**
- Modify: `admin.py:1369-1400`
- Test: `tests/test_utenti_routes.py`

**Interfaces:**
- Consumes: `utente_service.cancella_utente` (Task 2), `motivo_rifiuto` (Task 3).
- Produces: niente.

`tecnico_elimina` azzera dieci colonne prima di cancellare, fra cui
`manutenzioni.updated_by` e `verifiche.updated_by`, **che non esistono**: solo `apparecchi`
ha `updated_by`. Verificato eseguendo la rotta vera: **HTTP 500, tecnico non cancellato.**
E' il quarto elenco divergente delle stesse colonne nel progetto.

- [ ] **Step 1: Scrivere i test che falliscono**

In coda a `tests/test_utenti_routes.py`:

```python
def test_cancellare_un_tecnico_non_restituisce_piu_500(client, app, dati):
    """Prima della correzione: HTTP 500 e tecnico ancora presente, perche' la
    rotta azzerava manutenzioni.updated_by e verifiche.updated_by, colonne che
    non esistono."""
    from models import execute, query_one
    with app.app_context():
        div = query_one("SELECT id FROM divisioni WHERE struttura_id=?", (dati['a'],))['id']
        execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,"
                "stato,created_by) VALUES (?,?,'T-1','A','B','funzionante',?)",
                (div, dati['a'], dati['tec']))
    entra(client, 'super@x.it')
    r = client.post(f"/admin/tecnici/{dati['tec']}/elimina", follow_redirects=False)
    assert r.status_code != 500
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['tec'],))['eliminato_il'] is not None


def test_cancellando_un_tecnico_l_autore_resta(client, app, dati):
    """Con la primitiva nuova il nome resta: un miglioramento, non solo una
    riparazione."""
    from models import execute, query_one
    with app.app_context():
        div = query_one("SELECT id FROM divisioni WHERE struttura_id=?", (dati['a'],))['id']
        ap = execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,"
                     "modello,stato,created_by) VALUES (?,?,'T-2','A','B','funzionante',?)",
                     (div, dati['a'], dati['tec'])).lastrowid
    entra(client, 'super@x.it')
    client.post(f"/admin/tecnici/{dati['tec']}/elimina", follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT created_by FROM apparecchi WHERE id=?",
                         (ap,))['created_by'] == dati['tec']
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `python -m pytest tests/test_utenti_routes.py -q`
Expected: FAIL con status 500.

- [ ] **Step 3: Portare la rotta sulla primitiva**

Sostituisci il corpo di `tecnico_elimina` (dopo il recupero del tecnico) con:

```python
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
        flash("Cancellazione fallita, nulla e' stato modificato. Controlla il log.", 'danger')
        return redirect(url_for('admin.tecnici'))

    flash(f"Tecnico {esito['nome']} {esito['cognome']} eliminato.", 'success')
    return redirect(url_for('admin.tecnici'))
```

L'elenco di dieci colonne azzerate sparisce: era il quarto elenco divergente.

- [ ] **Step 4: Eseguire i test**

Run: `python -m pytest tests/ -q`
Expected: PASS. Dichiara il numero reale.

- [ ] **Step 5: Provare la sensibilita'**

Rimetti nella rotta l'azzeramento di `manutenzioni.updated_by`: deve fallire
`test_cancellare_un_tecnico_non_restituisce_piu_500`. Ripristina e verifica `git diff`
vuoto.

- [ ] **Step 6: Commit**

```bash
git add admin.py tests/test_utenti_routes.py
git commit -m "fix(utenti): cancellare un tecnico non restituisce piu' 500"
```

---

## Autoverifica del piano

**Copertura della spec.** La colonna e la meccanica → Task 1 e 2. I rifiuti → Task 3
(database) e Task 4 (chi chiede). L'autorizzazione → Task 4, riusando
`_check_utente_scope`. La pagina di conferma con i tre punti → Task 4. Il registro con
l'email originale e `struttura_id` → Task 4. I sei elenchi → Task 5, uno per uno. I tre
difetti esistenti: il declassamento silenzioso → Task 6, il 500 → Task 8, il quarto
elenco divergente → Task 8. La casella `attivo` e la sparizione del toggle → Task 7.
Tutti i test elencati nella spec hanno un test corrispondente.

**Segnaposto.** Nessun «TBD», nessun «simile al Task N», nessun passo senza il codice che
serve.

**Coerenza dei nomi.** `cancella_utente`, `motivo_rifiuto`, `conteggi_riferimenti`,
`email_liberata`, `PASSWORD_INUTILIZZABILE` sono definiti nei Task 2 e 3 e usati con gli
stessi nomi nei Task 4, 5 e 8. `MESSAGGI_RIFIUTO` e `_utente_cancellabile` nascono nel
Task 4 e il Task 8 usa il primo. Le chiavi di `motivo_rifiuto` coincidono con quelle di
`MESSAGGI_RIFIUTO`.

**Un rischio che l'implementatore deve conoscere.** Il Task 8 usa `MESSAGGI_RIFIUTO`, che
il Task 4 definisce vicino alle rotte utenti mentre `tecnico_elimina` sta molto piu' in
basso nello stesso file. E' lo stesso modulo, quindi funziona; ma se qualcuno spostasse
le rotte dei tecnici in un blueprint proprio, quel riferimento andrebbe con loro.
