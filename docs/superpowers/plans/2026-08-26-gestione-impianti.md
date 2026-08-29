# Gestione Impianti — Implementation Plan (MedInventory 2.7.0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aggiungere a MedInventory la gestione degli impianti delle divisioni —
anagrafica, componenti, documentazione iniziale, piano di manutenzione/verifica
con periodicità, storico interventi e avvisi email automatici — affiancandola
alla gestione degli apparecchi senza toccarne il funzionamento.

**Architecture:** Un blueprint nuovo (`impianti.py`) con la logica di dominio in
due moduli separati (`impianti_service.py` per il calcolo delle scadenze e degli
avvisi, `impianti_catalogo.py` per le periodicità standard precompilate). Sette
tabelle nuove più quattro colonne opzionali su `divisioni`, tutte create da
`models.apply_schema_updates()`. Una vista gemella `prossime_scadenze_impianti`
lascia intatta `prossime_scadenze`: i punti che devono mostrare entrambe le
origini fanno UNION al momento della query. Gli avvisi sono un task nuovo dello
scheduler esistente, con una tabella anti-duplicato che rende ogni ora un
tentativo ripetibile.

**Tech Stack:** Flask 3.x (application factory + blueprint), SQLite3 (WAL, FK
ON), HTMX (`?partial=1`), Bootstrap 5, fpdf2 (libretto PDF), pytest.

**Spec:** `docs/superpowers/specs/2026-08-26-impianti-design.md`

## Global Constraints

- **Lingua:** ogni testo UI, commento, nome di variabile e valore di database in
  italiano.
- **Isolamento multi-tenant:** ogni query su `impianti` e figlie è limitata alla
  struttura del chiamante. Le tabelle figlie NON hanno `struttura_id`: si passa
  sempre da un JOIN con `impianti`. Ogni rotta con `<int:impianto_id>` chiama
  `models.impianto_accessibile()` prima di leggere o scrivere.
- **Filtro divisione:** il helper reale è `models.filtro_divisione(table_alias='a')`
  — restituisce `(clausola, parametri)` con la clausola che inizia sempre con
  `"AND "`. Per gli impianti si usa `filtro_divisione('i')`. **Non esiste**
  `_get_divisione_filter()`: CLAUDE.md e la spec lo nominano per errore.
- **URL dello scadenzario:** `/scadenzario` (blueprint `manutenzioni`, rotta
  registrata senza prefisso). **Non** `/manutenzioni/scadenzario`: la spec §6.4
  sbaglia, e un path errato fa passare i test di isolamento su un 404.
- **Rotte relative:** `impianti_bp` ha `url_prefix='/impianti'`. I decoratori
  usano path relativi (`@impianti_bp.route('/')`, `'/nuovo'`,
  `'/<int:impianto_id>'`) — la tabella della spec §6.2 elenca URL completi; usarli
  nei decoratori raddoppierebbe il prefisso in `/impianti/impianti/...`.
- **Rotte figlie per id proprio:** documenti, scadenze e interventi si
  raggiungono con il loro id (`/documenti/<int:documento_id>`,
  `/piano/<int:scadenza_id>/modifica`, `/interventi/<int:intervento_id>/verbale`),
  non annidati sotto `<int:impianto_id>` come nella tabella della spec §6.2: e'
  una scelta deliberata: il permesso si verifica comunque risalendo alla riga con
  `impianto_accessibile(riga['impianto_id'])`, e l'id nell'URL non e' mai fidato.
  Non "correggere" queste rotte verso la forma annidata: i template e i test del
  piano usano questa forma.
- **Template:** i quattro nuovi template seguono per struttura e classi quelli
  gia' presenti in `templates/apparecchi/` (`lista.html`, `form.html`,
  `dettaglio.html`) e `templates/partials/apparecchi_table.html`. Il piano
  descrive che cosa deve contenere ciascuno; lo stile si copia da li'.
- **Soft delete:** gli impianti non si cancellano — `stato='dismesso'`. Stati
  validi: `attivo`, `in_manutenzione`, `fuori_servizio`, `dismesso`.
- **SQL parametrico:** solo `?`. Le clausole costruite in f-string sono solo
  quelle prodotte da `filtro_divisione()`. In una f-string SQL scrivere
  `strftime('%Y-%m', ...)`, mai la forma raddoppiata.
- **CSRF:** `CSRFProtect` globale. Ogni form POST ha `{{ csrf_token() }}`,
  anche i form vuoti guidati da JS.
- **Log attività:** ogni azione significativa chiama `log_attivita()` da
  `models.py`.
- **Upload:** sempre via `models.upload_subdir('impianti', struttura_id)` →
  `(uploads_dir, rel_prefix)`; nome file `f"{int(time.time())}_{secure_filename(...)}"`;
  estensioni in whitelist.
- **Versione:** 2.6.4 → **2.7.0**. `PRAGMA user_version` 200 → **270**.
- **Migrazioni:** ogni DDL nuovo va in `models.apply_schema_updates()` (lista
  `migrations`, eseguita in un `try/except` tollerante che salta gli step già
  applicati) **e** in `schema.sql` per le installazioni nuove.

---

## File Structure

**Creati:**

| File | Responsabilità |
|---|---|
| `impianti.py` | Blueprint `impianti_bp`: rotte, validazione dei form, upload, HTMX. Nessuna regola di calcolo. |
| `impianti_service.py` | Dominio: aritmetica delle scadenze, registrazione intervento, applicazione catalogo, selezione avvisi e destinatari. Nessun `request`, nessun `render_template`. |
| `impianti_catalogo.py` | La costante `CATALOGO` (periodicità standard per tipo) e due funzioni di lettura. Nessun accesso al DB. |
| `templates/impianti/lista.html` | Elenco con filtri. |
| `templates/impianti/form.html` | Creazione e modifica. |
| `templates/impianti/dettaglio.html` | Schede: anagrafica, componenti, documenti, piano, interventi. |
| `templates/impianti/manutentori.html` | Anagrafica manutentori. |
| `templates/partials/impianti_table.html` | Frammento tabella per `?partial=1`. |
| `tests/test_impianti.py` | Fixture condivisa + test di ogni task. |

**Modificati:**

| File | Modifica |
|---|---|
| `models.py` | `migrations` (7 tabelle, 4 ALTER, vista), `PRAGMA user_version`, `impianto_accessibile()`. |
| `schema.sql` | Stesso DDL per le installazioni nuove; `PRAGMA user_version = 270`. |
| `app.py` | Registrazione blueprint (blocco 391-418); contatori dashboard (475-495); liste dashboard (526-545); versione. |
| `auth.py` | Contatore badge scadenze (341-361). |
| `manutenzioni.py` | `scadenzario()` (370-441): filtro `origine`, UNION. |
| `scheduler.py` | Task `impianti_alerts` + `_send_impianti_alerts()`; sezione IMPIANTI in `_invia_digest()`. |
| `export_service.py` | Sezione IMPIANTI in `genera_report_scadenze_pdf()`. |
| `templates/base.html` | Voce di menu (riga ~68 desktop, ~239 menu ridotto). |
| `struttura_service.py` | `COLONNE_ALLEGATI`, `contenuto_struttura()`. |
| `importa_installazione.py` | Chiavi naturali e `COLONNE_FILE` delle nuove entità. |
| `config.example.json`, `config.json`, `tests/test_manutenzione.py` | Versione 2.7.0. |
| `CLAUDE.md` | Tabelle, blueprint, servizi, convenzioni. |

---

## Task 1: Schema, migrazioni e vista

**Files:**
- Modify: `models.py:375-512` (lista `migrations`), `models.py:340,345,780,787` (`user_version`)
- Modify: `schema.sql:510` (`PRAGMA user_version`)
- Test: `tests/test_impianti.py` (nuovo)

**Interfaces:**
- Consumes: `models.get_db()`, `models.execute()`, `models.query_one()`, `models.query_all()`
- Produces: tabelle `manutentori`, `impianti`, `impianti_componenti`,
  `impianti_documenti`, `impianti_scadenze`, `impianti_interventi`,
  `impianti_avvisi_inviati`; colonne `divisioni.indirizzo|email|telefono|responsabile`;
  vista `prossime_scadenze_impianti` con colonne `impianto_id, struttura_id,
  divisione_id, impianto_nome, tipo, tipo_custom, ubicazione, scadenza_id,
  scadenza_nome, riferimento_normativo, periodicita_mesi, giorni_anticipo,
  componente_descrizione, prossima_scadenza, giorni_rimasti, priorita`.
  Fixture `ambiente(app)` ed helper `entra(client, email)` in `tests/test_impianti.py`.

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# tests/test_impianti.py
"""Impianti: schema, isolamento, piano di manutenzione, avvisi."""
import pytest
from werkzeug.security import generate_password_hash

from models import execute, query_one, query_all


@pytest.fixture
def ambiente(app):
    """Due strutture con una divisione e un admin ciascuna.

    Modellata su tests/test_isolamento.py: le righe si inseriscono con
    execute() dentro un app_context, senza passare dalle rotte.
    """
    with app.app_context():
        dati = {}
        for chiave, nome, codice, email in (
            ('a', 'Clinica A', 'CLA', 'admin.a@test.it'),
            ('b', 'Clinica B', 'CLB', 'admin.b@test.it'),
        ):
            sid = execute(
                "INSERT INTO strutture (nome, codice, attiva, email_notifiche,"
                " email_responsabile) VALUES (?, ?, 1, ?, ?)",
                (nome, codice, f'notifiche.{chiave}@test.it',
                 f'responsabile.{chiave}@test.it')
            )
            did = execute(
                "INSERT INTO divisioni (struttura_id, nome, email) VALUES (?, ?, ?)",
                (sid, f'Divisione {chiave.upper()}', f'divisione.{chiave}@test.it')
            )
            uid = execute(
                "INSERT INTO utenti (struttura_id, nome, cognome, email,"
                " password_hash, ruolo, attivo) VALUES (?, ?, ?, ?, ?, 'admin', 1)",
                (sid, 'Admin', chiave.upper(), email,
                 generate_password_hash('Passw0rd!'))
            )
            dati[chiave] = {'struttura': sid, 'divisione': did,
                            'utente': uid, 'email': email}
        return dati


def entra(client, email):
    """Login con la password della fixture."""
    return client.post('/login', data={'email': email, 'password': 'Passw0rd!'},
                       follow_redirects=True)


def test_schema_impianti_creato(app, ambiente):
    """Le tabelle e la vista esistono dopo apply_schema_updates()."""
    attese = {'manutentori', 'impianti', 'impianti_componenti',
              'impianti_documenti', 'impianti_scadenze', 'impianti_interventi',
              'impianti_avvisi_inviati'}
    with app.app_context():
        nomi = {r['name'] for r in query_all(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert attese <= nomi
        viste = {r['name'] for r in query_all(
            "SELECT name FROM sqlite_master WHERE type='view'")}
        assert 'prossime_scadenze_impianti' in viste
        colonne = {r['name'] for r in query_all("PRAGMA table_info(divisioni)")}
        assert {'indirizzo', 'email', 'telefono', 'responsabile'} <= colonne


def test_vista_impianti_classifica_e_esclude_dismessi(app, ambiente):
    """La vista dà la priorità giusta e salta gli impianti dismessi."""
    with app.app_context():
        a = ambiente['a']
        attivo = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Cabina elettrica', 'elettrico')",
            (a['struttura'], a['divisione'])
        )
        dismesso = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo, stato)"
            " VALUES (?, ?, 'Vecchia centrale', 'riscaldamento', 'dismesso')",
            (a['struttura'], a['divisione'])
        )
        for impianto, giorni in ((attivo, -3), (attivo, 5), (attivo, 200),
                                 (dismesso, 1)):
            execute(
                "INSERT INTO impianti_scadenze (impianto_id, nome,"
                " periodicita_mesi, prossima_scadenza)"
                " VALUES (?, 'Verifica', 24, date('now', ?))",
                (impianto, f'{giorni} days')
            )
        righe = query_all(
            "SELECT priorita FROM prossime_scadenze_impianti WHERE impianto_id = ?",
            (attivo,))
        assert [r['priorita'] for r in righe] == ['scaduto', 'urgente', 'ok']
        assert query_all(
            "SELECT 1 FROM prossime_scadenze_impianti WHERE impianto_id = ?",
            (dismesso,)) == []
```

- [ ] **Step 2: Eseguire il test e vederlo fallire**

Run: `python -m pytest tests/test_impianti.py -q`
Expected: FAIL — `no such table: impianti` / `assert attese <= nomi`.

- [ ] **Step 3: Aggiungere le migrazioni**

In `models.py`, in fondo alla lista `migrations` (prima della parentesi quadra
di chiusura, dopo `"ALTER TABLE utenti ADD COLUMN reset_scadenza DATETIME"`):

```python
        # --- Impianti (2.7.0) -------------------------------------------
        # Dati opzionali della divisione: servono come destinatari degli
        # avvisi di scadenza degli impianti.
        "ALTER TABLE divisioni ADD COLUMN indirizzo TEXT",
        "ALTER TABLE divisioni ADD COLUMN email TEXT",
        "ALTER TABLE divisioni ADD COLUMN telefono TEXT",
        "ALTER TABLE divisioni ADD COLUMN responsabile TEXT",
        """CREATE TABLE IF NOT EXISTS manutentori (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            struttura_id INTEGER NOT NULL,
            ragione_sociale TEXT NOT NULL,
            indirizzo TEXT,
            telefono TEXT,
            email TEXT,
            partita_iva TEXT,
            note TEXT,
            attivo INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE,
            UNIQUE (struttura_id, ragione_sociale)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_manutentori_struttura ON manutentori(struttura_id)",
        "CREATE INDEX IF NOT EXISTS idx_manutentori_attivo ON manutentori(attivo)",
        """CREATE TABLE IF NOT EXISTS impianti (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            struttura_id INTEGER NOT NULL,
            divisione_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'altro' CHECK(tipo IN (
                'elettrico', 'idraulico', 'riscaldamento', 'climatizzazione',
                'antincendio', 'gas_medicali', 'ascensori', 'rete_dati', 'altro')),
            tipo_custom TEXT,
            descrizione TEXT,
            ubicazione TEXT,
            anno_installazione INTEGER,
            identificativo TEXT,
            stato TEXT NOT NULL DEFAULT 'attivo' CHECK(stato IN (
                'attivo', 'in_manutenzione', 'fuori_servizio', 'dismesso')),
            manutentore_id INTEGER,
            note TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER,
            updated_by INTEGER,
            FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE,
            FOREIGN KEY (divisione_id) REFERENCES divisioni(id) ON DELETE RESTRICT,
            FOREIGN KEY (manutentore_id) REFERENCES manutentori(id) ON DELETE SET NULL,
            FOREIGN KEY (created_by) REFERENCES utenti(id),
            FOREIGN KEY (updated_by) REFERENCES utenti(id),
            UNIQUE (struttura_id, nome)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_impianti_struttura ON impianti(struttura_id)",
        "CREATE INDEX IF NOT EXISTS idx_impianti_divisione ON impianti(divisione_id)",
        "CREATE INDEX IF NOT EXISTS idx_impianti_tipo ON impianti(tipo)",
        "CREATE INDEX IF NOT EXISTS idx_impianti_stato ON impianti(stato)",
        """CREATE TABLE IF NOT EXISTS impianti_componenti (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            impianto_id INTEGER NOT NULL,
            descrizione TEXT NOT NULL,
            marca TEXT,
            modello TEXT,
            matricola TEXT,
            ubicazione TEXT,
            note TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (impianto_id) REFERENCES impianti(id) ON DELETE CASCADE
        )""",
        "CREATE INDEX IF NOT EXISTS idx_impianti_componenti_impianto"
        " ON impianti_componenti(impianto_id)",
        """CREATE TABLE IF NOT EXISTS impianti_documenti (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            impianto_id INTEGER NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'altro' CHECK(tipo IN (
                'progetto', 'dichiarazione_conformita', 'collaudo', 'certificato',
                'libretto', 'planimetria', 'verbale', 'altro')),
            descrizione TEXT,
            data_documento DATE,
            emittente_ragione_sociale TEXT,
            emittente_indirizzo TEXT,
            emittente_telefono TEXT,
            emittente_email TEXT,
            filename TEXT NOT NULL,
            filepath TEXT NOT NULL,
            filesize INTEGER,
            uploaded_by INTEGER,
            uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (impianto_id) REFERENCES impianti(id) ON DELETE CASCADE,
            FOREIGN KEY (uploaded_by) REFERENCES utenti(id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_impianti_documenti_impianto"
        " ON impianti_documenti(impianto_id)",
        "CREATE INDEX IF NOT EXISTS idx_impianti_documenti_tipo"
        " ON impianti_documenti(tipo)",
        """CREATE TABLE IF NOT EXISTS impianti_scadenze (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            impianto_id INTEGER NOT NULL,
            componente_id INTEGER,
            nome TEXT NOT NULL,
            riferimento_normativo TEXT,
            periodicita_mesi INTEGER,
            prossima_scadenza DATE NOT NULL,
            giorni_anticipo INTEGER NOT NULL DEFAULT 30,
            email_extra TEXT,
            avvisa_manutentore INTEGER NOT NULL DEFAULT 1,
            attiva INTEGER NOT NULL DEFAULT 1,
            note TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (impianto_id) REFERENCES impianti(id) ON DELETE CASCADE,
            FOREIGN KEY (componente_id) REFERENCES impianti_componenti(id)
                ON DELETE SET NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_impianti_scadenze_impianto"
        " ON impianti_scadenze(impianto_id)",
        "CREATE INDEX IF NOT EXISTS idx_impianti_scadenze_prossima"
        " ON impianti_scadenze(prossima_scadenza)",
        "CREATE INDEX IF NOT EXISTS idx_impianti_scadenze_attiva"
        " ON impianti_scadenze(attiva)",
        """CREATE TABLE IF NOT EXISTS impianti_interventi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            impianto_id INTEGER NOT NULL,
            scadenza_id INTEGER,
            componente_id INTEGER,
            tipo TEXT NOT NULL DEFAULT 'ordinaria' CHECK(tipo IN (
                'verifica', 'ordinaria', 'straordinaria', 'riparazione')),
            data_intervento DATE NOT NULL,
            esito TEXT CHECK(esito IN ('positivo', 'negativo', 'con_riserva')),
            manutentore_id INTEGER,
            tecnico_ditta TEXT,
            descrizione TEXT,
            costo REAL,
            verbale_path TEXT,
            note TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER,
            FOREIGN KEY (impianto_id) REFERENCES impianti(id) ON DELETE CASCADE,
            FOREIGN KEY (scadenza_id) REFERENCES impianti_scadenze(id)
                ON DELETE SET NULL,
            FOREIGN KEY (componente_id) REFERENCES impianti_componenti(id)
                ON DELETE SET NULL,
            FOREIGN KEY (manutentore_id) REFERENCES manutentori(id)
                ON DELETE SET NULL,
            FOREIGN KEY (created_by) REFERENCES utenti(id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_impianti_interventi_impianto"
        " ON impianti_interventi(impianto_id)",
        "CREATE INDEX IF NOT EXISTS idx_impianti_interventi_scadenza"
        " ON impianti_interventi(scadenza_id)",
        "CREATE INDEX IF NOT EXISTS idx_impianti_interventi_data"
        " ON impianti_interventi(data_intervento)",
        """CREATE TABLE IF NOT EXISTS impianti_avvisi_inviati (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scadenza_id INTEGER NOT NULL,
            soglia TEXT NOT NULL,
            scadenza_target DATE NOT NULL,
            destinatari TEXT,
            inviato_il DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (scadenza_id) REFERENCES impianti_scadenze(id)
                ON DELETE CASCADE,
            UNIQUE (scadenza_id, soglia, scadenza_target)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_impianti_avvisi_scadenza"
        " ON impianti_avvisi_inviati(scadenza_id)",
        # La vista si ricrea a ogni avvio: cambiarne il corpo in un rilascio
        # successivo non richiede altro che modificare queste righe.
        "DROP VIEW IF EXISTS prossime_scadenze_impianti",
        """CREATE VIEW prossime_scadenze_impianti AS
        SELECT
          i.id AS impianto_id, i.struttura_id, i.divisione_id,
          i.nome AS impianto_nome, i.tipo, i.tipo_custom, i.ubicazione,
          s.id AS scadenza_id, s.nome AS scadenza_nome, s.riferimento_normativo,
          s.periodicita_mesi, s.giorni_anticipo,
          c.descrizione AS componente_descrizione,
          s.prossima_scadenza,
          CAST((julianday(s.prossima_scadenza) - julianday('now')) AS INTEGER)
              AS giorni_rimasti,
          CASE
            WHEN julianday(s.prossima_scadenza) - julianday('now') < 0  THEN 'scaduto'
            WHEN julianday(s.prossima_scadenza) - julianday('now') <= 7  THEN 'urgente'
            WHEN julianday(s.prossima_scadenza) - julianday('now') <= 15 THEN 'attenzione'
            WHEN julianday(s.prossima_scadenza) - julianday('now') <= 30 THEN 'avviso'
            ELSE 'ok'
          END AS priorita
        FROM impianti i
        INNER JOIN impianti_scadenze s ON i.id = s.impianto_id
        LEFT JOIN impianti_componenti c ON c.id = s.componente_id
        WHERE i.stato != 'dismesso'
          AND s.attiva = 1
        ORDER BY s.prossima_scadenza ASC""",
```

- [ ] **Step 4: Ripetere lo stesso DDL in `schema.sql`**

Aggiungere in fondo a `schema.sql`, **prima** della riga `PRAGMA user_version`,
le stesse sette `CREATE TABLE`, i relativi indici e la `CREATE VIEW` (senza il
`DROP VIEW`, che nello schema iniziale non serve, e con `IF NOT EXISTS` sulla
vista). Aggiungere le quattro colonne direttamente nella definizione di
`divisioni` (`indirizzo TEXT`, `email TEXT`, `telefono TEXT`, `responsabile TEXT`)
invece che con `ALTER TABLE`. Poi portare la versione:

```sql
PRAGMA user_version = 270;
```

E in `models.py` sostituire il valore atteso `200` con `270` nei quattro punti
che lo nominano (righe ~340, ~345, ~780, ~787).

- [ ] **Step 5: Eseguire i test e vederli passare**

Run: `python -m pytest tests/test_impianti.py -q`
Expected: PASS (2 test).

Run: `python -m pytest tests/ -q`
Expected: PASS — nessuna regressione (in particolare `tests/test_isolamento.py`).

- [ ] **Step 6: Commit**

```bash
git add models.py schema.sql tests/test_impianti.py
git commit -m "feat(impianti): schema, migrazioni e vista prossime_scadenze_impianti"
```

---

## Task 2: `impianto_accessibile()`

**Files:**
- Modify: `models.py:838-865` (subito dopo `apparecchio_accessibile()`)
- Test: `tests/test_impianti.py`

**Interfaces:**
- Consumes: `models.query_one()`, `flask.g`
- Produces: `models.impianto_accessibile(impianto_id) -> sqlite3.Row | None`

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# tests/test_impianti.py — in coda
def test_impianto_accessibile_isola_le_strutture(app, ambiente):
    """Un admin non raggiunge l'impianto dell'altra struttura, nemmeno per id."""
    from flask import g
    from models import impianto_accessibile

    with app.app_context():
        b = ambiente['b']
        impianto_b = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Impianto segreto B', 'idraulico')",
            (b['struttura'], b['divisione'])
        )

    with app.test_request_context():
        g.user = {'id': ambiente['a']['utente'], 'ruolo': 'admin'}
        g.struttura_id = ambiente['a']['struttura']
        g.divisioni = []
        assert impianto_accessibile(impianto_b) is None

        g.struttura_id = ambiente['b']['struttura']
        riga = impianto_accessibile(impianto_b)
        assert riga is not None and riga['nome'] == 'Impianto segreto B'


def test_impianto_accessibile_rispetta_le_divisioni(app, ambiente):
    """Un utente semplice vede solo gli impianti delle sue divisioni."""
    from flask import g
    from models import impianto_accessibile

    with app.app_context():
        a = ambiente['a']
        altra_div = execute(
            "INSERT INTO divisioni (struttura_id, nome) VALUES (?, 'Altra')",
            (a['struttura'],))
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Quadro Altra', 'elettrico')",
            (a['struttura'], altra_div))

    with app.test_request_context():
        g.user = {'id': 99, 'ruolo': 'utente'}
        g.struttura_id = ambiente['a']['struttura']
        g.divisioni = [{'id': ambiente['a']['divisione']}]
        assert impianto_accessibile(impianto) is None
        g.divisioni = [{'id': altra_div}]
        assert impianto_accessibile(impianto) is not None
```

- [ ] **Step 2: Eseguire il test e vederlo fallire**

Run: `python -m pytest tests/test_impianti.py -q -k accessibile`
Expected: FAIL — `ImportError: cannot import name 'impianto_accessibile'`.

- [ ] **Step 3: Implementare**

In `models.py`, subito dopo `apparecchio_accessibile()`:

```python
def impianto_accessibile(impianto_id):
    """Verifica che l'impianto sia nello scope dell'utente corrente.

    Gemella di apparecchio_accessibile(): struttura per l'isolamento
    multi-tenant, divisione per i ruoli non amministrativi. Le tabelle figlie
    degli impianti non portano struttura_id, quindi ogni rotta che tocca
    componenti, documenti, piano o interventi passa prima di qui.
    Restituisce la riga dell'impianto, oppure None.
    """
    struttura_id = getattr(g, 'struttura_id', None)
    if struttura_id:
        riga = query_one(
            "SELECT * FROM impianti WHERE id = ? AND struttura_id = ?",
            (impianto_id, struttura_id)
        )
    elif g.user['ruolo'] == 'superadmin':
        riga = query_one("SELECT * FROM impianti WHERE id = ?", (impianto_id,))
    else:
        return None
    if not riga:
        return None
    if g.user['ruolo'] not in ('admin', 'superadmin', 'tecnico'):
        accessibili = [d['id'] for d in getattr(g, 'divisioni', [])]
        if riga['divisione_id'] not in accessibili:
            return None
    return riga
```

- [ ] **Step 4: Eseguire i test e vederli passare**

Run: `python -m pytest tests/test_impianti.py -q`
Expected: PASS (4 test).

- [ ] **Step 5: Commit**

```bash
git add models.py tests/test_impianti.py
git commit -m "feat(impianti): models.impianto_accessibile()"
```

---

## Task 3: Catalogo delle periodicità standard

**Files:**
- Create: `impianti_catalogo.py`
- Test: `tests/test_impianti.py`

**Interfaces:**
- Consumes: niente (modulo puro, nessun import di Flask o sqlite3)
- Produces:
  - `CATALOGO: dict[str, list[dict]]` — chiave = `impianti.tipo`, voce =
    `{'nome': str, 'mesi': int, 'riferimento': str}`
  - `voci_per_tipo(tipo: str) -> list[dict]`
  - `voci_mancanti(tipo: str, nomi_presenti: Iterable[str]) -> list[dict]`

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# tests/test_impianti.py — in coda
def test_catalogo_copre_ogni_tipo_e_filtra_le_voci_presenti():
    """Ogni tipo ha una voce nel catalogo; voci_mancanti esclude i doppioni."""
    from impianti_catalogo import CATALOGO, voci_per_tipo, voci_mancanti

    tipi = {'elettrico', 'idraulico', 'riscaldamento', 'climatizzazione',
            'antincendio', 'gas_medicali', 'ascensori', 'rete_dati', 'altro'}
    assert set(CATALOGO) == tipi
    for voci in CATALOGO.values():
        for v in voci:
            assert set(v) == {'nome', 'mesi', 'riferimento'}
            assert isinstance(v['mesi'], int) and v['mesi'] > 0

    elettrico = voci_per_tipo('elettrico')
    assert any(v['nome'] == 'Verifica impianto di terra' and v['mesi'] == 24
               for v in elettrico)
    assert voci_per_tipo('inesistente') == []

    mancanti = voci_mancanti('elettrico', ['Verifica impianto di terra'])
    assert [v['nome'] for v in mancanti] == ['Prova interruttori differenziali']
```

- [ ] **Step 2: Eseguire il test e vederlo fallire**

Run: `python -m pytest tests/test_impianti.py -q -k catalogo`
Expected: FAIL — `ModuleNotFoundError: No module named 'impianti_catalogo'`.

- [ ] **Step 3: Implementare**

```python
# impianti_catalogo.py
"""Periodicità standard proposte alla creazione di un impianto.

Una costante, non una tabella: sono valori di legge o di norma tecnica, uguali
per tutte le strutture, e vanno aggiornati con il codice. Il catalogo è una
*proposta*: le voci scelte diventano righe di impianti_scadenze, e da quel
momento vivono di vita propria. Modificare CATALOGO non riscrive nessun piano
già esistente.
"""

CATALOGO = {
    'elettrico': [
        {'nome': 'Verifica impianto di terra', 'mesi': 24,
         'riferimento': 'DPR 462/01'},
        {'nome': 'Prova interruttori differenziali', 'mesi': 6,
         'riferimento': 'CEI 64-8'},
    ],
    'idraulico': [
        {'nome': 'Analisi legionella', 'mesi': 12,
         'riferimento': 'Linee guida 07/05/2015'},
    ],
    'riscaldamento': [
        {'nome': 'Manutenzione e controllo fumi', 'mesi': 12,
         'riferimento': 'DPR 74/2013'},
    ],
    'climatizzazione': [
        {'nome': 'Pulizia filtri e batterie', 'mesi': 6, 'riferimento': ''},
        {'nome': 'Controllo perdite F-gas', 'mesi': 12,
         'riferimento': 'Reg. UE 517/2014'},
    ],
    'antincendio': [
        {'nome': 'Controllo estintori', 'mesi': 6, 'riferimento': 'UNI 9994-1'},
        {'nome': 'Controllo idranti', 'mesi': 6, 'riferimento': 'UNI 10779'},
        {'nome': 'Verifica rivelazione incendi', 'mesi': 6,
         'riferimento': 'UNI 11224'},
    ],
    'gas_medicali': [
        {'nome': 'Verifica periodica impianto', 'mesi': 12,
         'riferimento': 'UNI EN ISO 7396-1'},
    ],
    'ascensori': [
        {'nome': 'Verifica periodica', 'mesi': 24, 'riferimento': 'DPR 162/99'},
        {'nome': 'Manutenzione ordinaria', 'mesi': 6, 'riferimento': 'DPR 162/99'},
    ],
    'rete_dati': [],
    'altro': [],
}


def voci_per_tipo(tipo):
    """Le voci di catalogo di un tipo di impianto. Lista vuota se sconosciuto."""
    return list(CATALOGO.get(tipo, []))


def voci_mancanti(tipo, nomi_presenti):
    """Le voci di catalogo non ancora nel piano dell'impianto.

    Il confronto è sul nome normalizzato (senza spazi ai bordi, minuscolo):
    riproporre una voce già inserita a mano con la stessa dicitura sarebbe un
    doppione che l'utente deve poi cancellare.
    """
    presenti = {(n or '').strip().lower() for n in nomi_presenti}
    return [v for v in voci_per_tipo(tipo)
            if v['nome'].strip().lower() not in presenti]
```

- [ ] **Step 4: Eseguire i test e vederli passare**

Run: `python -m pytest tests/test_impianti.py -q`
Expected: PASS (5 test).

- [ ] **Step 5: Commit**

```bash
git add impianti_catalogo.py tests/test_impianti.py
git commit -m "feat(impianti): catalogo delle periodicita' standard"
```

---

## Task 4: Servizio — aritmetica delle scadenze e registrazione intervento

**Files:**
- Create: `impianti_service.py`
- Test: `tests/test_impianti.py`

**Interfaces:**
- Consumes: `models.execute()`, `models.query_one()`, `impianti_catalogo.voci_per_tipo()`
- Produces:
  - `aggiungi_mesi(data: str | date, mesi: int) -> str` (ISO `YYYY-MM-DD`)
  - `registra_intervento(impianto_id: int, dati: dict, utente_id: int|None=None)
    -> tuple[int, str | None]` — `(intervento_id, nuova_scadenza_iso_o_None)`.
    `dati` accetta le chiavi `scadenza_id, componente_id, tipo, data_intervento,
    esito, manutentore_id, tecnico_ditta, descrizione, costo, verbale_path, note`.
  - `applica_catalogo(impianto_id: int, tipo: str, nomi_scelti: list[str],
    partenza: str) -> int` — numero di righe di piano create.

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# tests/test_impianti.py — in coda
def test_aggiungi_mesi_taglia_il_giorno_sui_mesi_corti():
    """31 gennaio + 1 mese = 28/29 febbraio, non un errore."""
    from impianti_service import aggiungi_mesi
    assert aggiungi_mesi('2026-01-31', 1) == '2026-02-28'
    assert aggiungi_mesi('2024-01-31', 1) == '2024-02-29'
    assert aggiungi_mesi('2026-03-15', 24) == '2028-03-15'
    assert aggiungi_mesi('2026-12-31', 2) == '2027-02-28'


def _impianto_con_piano(ambiente, periodicita=24, scadenza='2026-01-10'):
    a = ambiente['a']
    impianto = execute(
        "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
        " VALUES (?, ?, 'Cabina', 'elettrico')",
        (a['struttura'], a['divisione']))
    scad = execute(
        "INSERT INTO impianti_scadenze (impianto_id, nome, periodicita_mesi,"
        " prossima_scadenza) VALUES (?, 'Verifica di terra', ?, ?)",
        (impianto, periodicita, scadenza))
    return impianto, scad


def test_intervento_positivo_sposta_la_scadenza(app, ambiente):
    from impianti_service import registra_intervento
    with app.app_context():
        impianto, scad = _impianto_con_piano(ambiente)
        _, nuova = registra_intervento(impianto, {
            'scadenza_id': scad, 'tipo': 'verifica',
            'data_intervento': '2026-01-08', 'esito': 'positivo'})
        assert nuova == '2028-01-08'
        riga = query_one("SELECT * FROM impianti_scadenze WHERE id = ?", (scad,))
        assert riga['prossima_scadenza'] == '2028-01-08'
        assert riga['attiva'] == 1


def test_intervento_negativo_non_sposta_nulla(app, ambiente):
    from impianti_service import registra_intervento
    with app.app_context():
        impianto, scad = _impianto_con_piano(ambiente)
        _, nuova = registra_intervento(impianto, {
            'scadenza_id': scad, 'tipo': 'verifica',
            'data_intervento': '2026-01-08', 'esito': 'negativo'})
        assert nuova is None
        riga = query_one("SELECT * FROM impianti_scadenze WHERE id = ?", (scad,))
        assert riga['prossima_scadenza'] == '2026-01-10'
        assert riga['attiva'] == 1


def test_intervento_con_riserva_sposta_come_positivo(app, ambiente):
    from impianti_service import registra_intervento
    with app.app_context():
        impianto, scad = _impianto_con_piano(ambiente, periodicita=6)
        _, nuova = registra_intervento(impianto, {
            'scadenza_id': scad, 'tipo': 'ordinaria',
            'data_intervento': '2026-01-08', 'esito': 'con_riserva'})
        assert nuova == '2026-07-08'


def test_scadenza_una_tantum_si_chiude(app, ambiente):
    """periodicita_mesi NULL: eseguita una volta, la riga esce dal piano."""
    from impianti_service import registra_intervento
    with app.app_context():
        impianto, scad = _impianto_con_piano(ambiente, periodicita=None)
        _, nuova = registra_intervento(impianto, {
            'scadenza_id': scad, 'tipo': 'straordinaria',
            'data_intervento': '2026-01-08', 'esito': 'positivo'})
        assert nuova is None
        riga = query_one("SELECT * FROM impianti_scadenze WHERE id = ?", (scad,))
        assert riga['attiva'] == 0


def test_intervento_senza_scadenza_e_solo_storico(app, ambiente):
    from impianti_service import registra_intervento
    with app.app_context():
        impianto, scad = _impianto_con_piano(ambiente)
        iid, nuova = registra_intervento(impianto, {
            'tipo': 'riparazione', 'data_intervento': '2026-01-08',
            'descrizione': 'Sostituito interruttore'})
        assert nuova is None
        assert query_one("SELECT * FROM impianti_interventi WHERE id = ?",
                         (iid,))['descrizione'] == 'Sostituito interruttore'
        assert query_one("SELECT prossima_scadenza FROM impianti_scadenze"
                         " WHERE id = ?", (scad,))['prossima_scadenza'] == '2026-01-10'


def test_applica_catalogo_crea_il_piano(app, ambiente):
    from impianti_service import applica_catalogo
    with app.app_context():
        a = ambiente['a']
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Antincendio piano 1', 'antincendio')",
            (a['struttura'], a['divisione']))
        creati = applica_catalogo(
            impianto, 'antincendio',
            ['Controllo estintori', 'Controllo idranti'], '2026-01-01')
        assert creati == 2
        righe = query_all(
            "SELECT nome, periodicita_mesi, prossima_scadenza,"
            " riferimento_normativo FROM impianti_scadenze"
            " WHERE impianto_id = ? ORDER BY nome", (impianto,))
        assert [r['nome'] for r in righe] == ['Controllo estintori',
                                              'Controllo idranti']
        assert righe[0]['periodicita_mesi'] == 6
        assert righe[0]['prossima_scadenza'] == '2026-07-01'
        assert righe[0]['riferimento_normativo'] == 'UNI 9994-1'
        # Un nome non in catalogo viene ignorato, non inventato.
        assert applica_catalogo(impianto, 'antincendio', ['Fantasia'],
                                '2026-01-01') == 0
```

- [ ] **Step 2: Eseguire i test e vederli fallire**

Run: `python -m pytest tests/test_impianti.py -q -k "aggiungi_mesi or intervento or catalogo_crea"`
Expected: FAIL — `ModuleNotFoundError: No module named 'impianti_service'`.

- [ ] **Step 3: Implementare**

```python
# impianti_service.py
"""Regole di dominio degli impianti.

Qui sta il calcolo, non la presentazione: nessun import di request o
render_template. Il blueprint valida i form e chiama queste funzioni; lo
scheduler chiama avvisi_da_inviare() e destinatari().
"""

import calendar
import logging
from datetime import date, datetime

from models import execute, query_one, query_all
from impianti_catalogo import voci_per_tipo

logger = logging.getLogger('medinventory.impianti')

#: Esiti che confermano l'esecuzione della verifica e fanno ripartire il ciclo.
#: 'con_riserva' sta qui perche' la verifica e' stata fatta: le riserve sono
#: prescrizioni da chiudere, non un rinvio della scadenza. 'negativo' invece
#: lascia la riga scaduta, che e' esattamente lo stato reale dell'impianto.
ESITI_CHE_RINNOVANO = ('positivo', 'con_riserva')


def aggiungi_mesi(data, mesi):
    """Somma mesi a una data, tagliando il giorno sui mesi corti.

    31 gennaio + 1 mese = 28 (o 29) febbraio: un timedelta di giorni fissi
    sfaserebbe progressivamente le periodicita' lunghe.
    Restituisce una stringa ISO 'YYYY-MM-DD'.
    """
    if isinstance(data, str):
        data = datetime.strptime(data[:10], '%Y-%m-%d').date()
    totale = data.month - 1 + int(mesi)
    anno = data.year + totale // 12
    mese = totale % 12 + 1
    giorno = min(data.day, calendar.monthrange(anno, mese)[1])
    return date(anno, mese, giorno).isoformat()


def registra_intervento(impianto_id, dati, utente_id=None):
    """Registra un intervento e, se serve, fa avanzare la riga di piano.

    Restituisce (intervento_id, nuova_scadenza | None). La nuova scadenza si
    calcola dalla data dell'intervento, non dalla scadenza precedente: se la
    verifica e' stata fatta in ritardo, il ciclo riparte da quando e' stata
    fatta davvero.
    """
    intervento_id = execute(
        """INSERT INTO impianti_interventi
           (impianto_id, scadenza_id, componente_id, tipo, data_intervento,
            esito, manutentore_id, tecnico_ditta, descrizione, costo,
            verbale_path, note, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (impianto_id, dati.get('scadenza_id'), dati.get('componente_id'),
         dati.get('tipo', 'ordinaria'), dati['data_intervento'],
         dati.get('esito'), dati.get('manutentore_id'),
         dati.get('tecnico_ditta'), dati.get('descrizione'), dati.get('costo'),
         dati.get('verbale_path'), dati.get('note'), utente_id)
    )

    scadenza_id = dati.get('scadenza_id')
    if not scadenza_id:
        return intervento_id, None

    riga = query_one(
        "SELECT * FROM impianti_scadenze WHERE id = ? AND impianto_id = ?",
        (scadenza_id, impianto_id)
    )
    if not riga:
        return intervento_id, None

    if dati.get('esito') not in ESITI_CHE_RINNOVANO:
        return intervento_id, None

    if riga['periodicita_mesi']:
        nuova = aggiungi_mesi(dati['data_intervento'], riga['periodicita_mesi'])
        execute(
            "UPDATE impianti_scadenze SET prossima_scadenza = ?,"
            " updated_at = datetime('now') WHERE id = ?",
            (nuova, scadenza_id)
        )
        return intervento_id, nuova

    # Una tantum: eseguita, esce dal piano. Resta nello storico interventi.
    execute(
        "UPDATE impianti_scadenze SET attiva = 0, updated_at = datetime('now')"
        " WHERE id = ?", (scadenza_id,)
    )
    return intervento_id, None


def applica_catalogo(impianto_id, tipo, nomi_scelti, partenza):
    """Crea le righe di piano per le voci di catalogo scelte.

    'partenza' e' la data da cui contare la prima scadenza (di norma la data di
    creazione dell'impianto). Le voci non presenti in catalogo sono ignorate.
    Restituisce il numero di righe create.
    """
    scelti = {(n or '').strip().lower() for n in nomi_scelti}
    creati = 0
    for voce in voci_per_tipo(tipo):
        if voce['nome'].strip().lower() not in scelti:
            continue
        execute(
            """INSERT INTO impianti_scadenze
               (impianto_id, nome, riferimento_normativo, periodicita_mesi,
                prossima_scadenza)
               VALUES (?, ?, ?, ?, ?)""",
            (impianto_id, voce['nome'], voce['riferimento'] or None,
             voce['mesi'], aggiungi_mesi(partenza, voce['mesi']))
        )
        creati += 1
    return creati
```

- [ ] **Step 4: Eseguire i test e vederli passare**

Run: `python -m pytest tests/test_impianti.py -q`
Expected: PASS (12 test).

- [ ] **Step 5: Commit**

```bash
git add impianti_service.py tests/test_impianti.py
git commit -m "feat(impianti): servizio scadenze, interventi e applicazione catalogo"
```

---

## Task 5: Blueprint — lista, creazione, dettaglio

**Files:**
- Create: `impianti.py`, `templates/impianti/lista.html`,
  `templates/impianti/form.html`, `templates/impianti/dettaglio.html`,
  `templates/partials/impianti_table.html`
- Modify: `app.py:391-418` (registrazione blueprint), `templates/base.html:68,239`
- Test: `tests/test_impianti.py`

**Interfaces:**
- Consumes: `models.filtro_divisione('i')`, `models.impianto_accessibile()`,
  `models.query_one/query_all/execute/log_attivita`,
  `impianti_service.applica_catalogo()`, `impianti_catalogo.voci_per_tipo()`,
  `auth.login_required`, `auth.tecnico_o_admin_required`
- Produces: blueprint `impianti_bp` con endpoint `impianti.lista`,
  `impianti.nuovo`, `impianti.dettaglio`; helper interno
  `_valida_impianto(form, edit_id=None) -> (dict, list[str])`; costanti
  `TIPI_IMPIANTO`, `STATI_IMPIANTO`

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# tests/test_impianti.py — in coda
def test_lista_impianti_isola_le_strutture(client, app, ambiente):
    with app.app_context():
        for chiave, nome in (('a', 'Cabina A'), ('b', 'SEGRETO-B')):
            d = ambiente[chiave]
            execute("INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
                    " VALUES (?, ?, ?, 'elettrico')",
                    (d['struttura'], d['divisione'], nome))
    entra(client, ambiente['a']['email'])
    corpo = client.get('/impianti').get_data(as_text=True)
    assert 'Cabina A' in corpo
    assert 'SEGRETO-B' not in corpo


def test_lista_impianti_partial_e_solo_il_frammento(client, app, ambiente):
    entra(client, ambiente['a']['email'])
    corpo = client.get('/impianti?partial=1').get_data(as_text=True)
    assert '<html' not in corpo.lower()


def test_creazione_impianto_con_catalogo(client, app, ambiente):
    entra(client, ambiente['a']['email'])
    with app.app_context():
        divisione = ambiente['a']['divisione']
    risposta = client.post('/impianti/nuovo', data={
        'nome': 'Cabina MT', 'tipo': 'elettrico', 'divisione_id': divisione,
        'ubicazione': 'Piano interrato',
        'catalogo': ['Verifica impianto di terra'],
    }, follow_redirects=True)
    assert risposta.status_code == 200
    with app.app_context():
        riga = query_one("SELECT * FROM impianti WHERE nome = 'Cabina MT'")
        assert riga['struttura_id'] == ambiente['a']['struttura']
        piano = query_all("SELECT * FROM impianti_scadenze WHERE impianto_id = ?",
                          (riga['id'],))
        assert len(piano) == 1 and piano[0]['periodicita_mesi'] == 24


def test_tipo_custom_solo_con_tipo_altro(client, app, ambiente):
    entra(client, ambiente['a']['email'])
    with app.app_context():
        divisione = ambiente['a']['divisione']
    client.post('/impianti/nuovo', data={
        'nome': 'Fotovoltaico', 'tipo': 'elettrico', 'divisione_id': divisione,
        'tipo_custom': 'Solare'}, follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT tipo_custom FROM impianti"
                         " WHERE nome = 'Fotovoltaico'")['tipo_custom'] is None


def test_dettaglio_impianto_altrui_non_raggiungibile(client, app, ambiente):
    with app.app_context():
        b = ambiente['b']
        impianto_b = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'SEGRETO-B', 'idraulico')",
            (b['struttura'], b['divisione']))
    entra(client, ambiente['a']['email'])
    corpo = client.get(f'/impianti/{impianto_b}',
                       follow_redirects=True).get_data(as_text=True)
    assert 'SEGRETO-B' not in corpo
```

- [ ] **Step 2: Eseguire i test e vederli fallire**

Run: `python -m pytest tests/test_impianti.py -q -k "lista or creazione or custom or dettaglio"`
Expected: FAIL — 404 su `/impianti`.

- [ ] **Step 3: Creare il blueprint**

```python
# impianti.py
"""Blueprint degli impianti: anagrafica, componenti, documenti, piano, interventi.

Le rotte hanno path relativi: il prefisso /impianti sta gia' nel Blueprint.
Ogni rotta con <int:impianto_id> passa da impianto_accessibile(): le tabelle
figlie non portano struttura_id, quindi l'isolamento e' tutto qui.
"""

import os
import time

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, g, send_file, abort)
from werkzeug.utils import secure_filename

from auth import login_required, tecnico_o_admin_required
from models import (query_one, query_all, execute, log_attivita, upload_subdir,
                    filtro_divisione, impianto_accessibile)
import impianti_service
from impianti_catalogo import voci_per_tipo, voci_mancanti

impianti_bp = Blueprint('impianti', __name__, url_prefix='/impianti')

TIPI_IMPIANTO = ('elettrico', 'idraulico', 'riscaldamento', 'climatizzazione',
                 'antincendio', 'gas_medicali', 'ascensori', 'rete_dati', 'altro')
STATI_IMPIANTO = ('attivo', 'in_manutenzione', 'fuori_servizio', 'dismesso')
PER_PAGINA = 25


def _valida_impianto(form, edit_id=None):
    """Valida il form e restituisce (dati, errori).

    tipo_custom sopravvive solo con tipo='altro': il CHECK del database non lo
    puo' esprimere, quindi la regola vive qui, in un punto solo.
    """
    errori = []
    nome = (form.get('nome') or '').strip()
    if not nome:
        errori.append('Il nome è obbligatorio.')
    tipo = form.get('tipo') or 'altro'
    if tipo not in TIPI_IMPIANTO:
        errori.append('Tipo di impianto non valido.')
    stato = form.get('stato') or 'attivo'
    if stato not in STATI_IMPIANTO:
        errori.append('Stato non valido.')

    divisione_id = form.get('divisione_id', type=int)
    divisione = query_one(
        "SELECT * FROM divisioni WHERE id = ? AND struttura_id = ?",
        (divisione_id, getattr(g, 'struttura_id', None))
    ) if divisione_id else None
    if not divisione:
        errori.append('Divisione non valida.')
    elif g.user['ruolo'] not in ('admin', 'superadmin', 'tecnico'):
        if divisione_id not in [d['id'] for d in getattr(g, 'divisioni', [])]:
            errori.append('Divisione non accessibile.')

    if nome and divisione:
        doppione = query_one(
            "SELECT id FROM impianti WHERE struttura_id = ? AND nome = ?"
            " AND id != ?", (g.struttura_id, nome, edit_id or -1))
        if doppione:
            errori.append('Esiste già un impianto con questo nome.')

    anno = form.get('anno_installazione', type=int)
    if anno is not None and not (1900 <= anno <= 2100):
        errori.append('Anno di installazione non plausibile.')

    dati = {
        'nome': nome,
        'tipo': tipo,
        'tipo_custom': (form.get('tipo_custom') or '').strip() or None
                       if tipo == 'altro' else None,
        'descrizione': (form.get('descrizione') or '').strip() or None,
        'ubicazione': (form.get('ubicazione') or '').strip() or None,
        'anno_installazione': anno,
        'identificativo': (form.get('identificativo') or '').strip() or None,
        'stato': stato,
        'manutentore_id': form.get('manutentore_id', type=int) or None,
        'note': (form.get('note') or '').strip() or None,
        'divisione_id': divisione_id,
    }
    return dati, errori


@impianti_bp.route('/')
@login_required
def lista():
    """Elenco degli impianti nello scope dell'utente."""
    div_clause, div_params = filtro_divisione('i')
    where = ["1=1"]
    parametri = []

    tipo = request.args.get('tipo', '')
    if tipo in TIPI_IMPIANTO:
        where.append("i.tipo = ?")
        parametri.append(tipo)
    stato = request.args.get('stato', '')
    if stato in STATI_IMPIANTO:
        where.append("i.stato = ?")
        parametri.append(stato)
    else:
        where.append("i.stato != 'dismesso'")
    ricerca = (request.args.get('q') or '').strip()
    if ricerca:
        where.append("(i.nome LIKE ? OR i.ubicazione LIKE ?"
                     " OR i.identificativo LIKE ?)")
        parametri.extend([f'%{ricerca}%'] * 3)

    where_sql = " AND ".join(where)
    page = max(1, request.args.get('page', 1, type=int))

    totale = query_one(
        f"SELECT COUNT(*) as cnt FROM impianti i WHERE {where_sql} {div_clause}",
        parametri + div_params
    )['cnt']

    impianti = query_all(
        f"""SELECT i.*, d.nome as divisione_nome, d.colore as divisione_colore,
                   m.ragione_sociale as manutentore_nome,
                   (SELECT MIN(s.prossima_scadenza) FROM impianti_scadenze s
                     WHERE s.impianto_id = i.id AND s.attiva = 1)
                       as prima_scadenza
            FROM impianti i
            LEFT JOIN divisioni d ON d.id = i.divisione_id
            LEFT JOIN manutentori m ON m.id = i.manutentore_id
            WHERE {where_sql} {div_clause}
            ORDER BY i.nome LIMIT ? OFFSET ?""",
        parametri + div_params + [PER_PAGINA, (page - 1) * PER_PAGINA]
    )

    contesto = {
        'impianti': impianti,
        'filtri': {'tipo': tipo, 'stato': stato, 'q': ricerca},
        'tipi': TIPI_IMPIANTO,
        'stati': STATI_IMPIANTO,
        'pagination': {
            'page': page, 'per_page': PER_PAGINA, 'total': totale,
            'total_pages': max(1, (totale + PER_PAGINA - 1) // PER_PAGINA),
        },
    }
    if request.args.get('partial'):
        return render_template('partials/impianti_table.html', **contesto)
    return render_template('impianti/lista.html', **contesto)


@impianti_bp.route('/nuovo', methods=['GET', 'POST'])
@tecnico_o_admin_required
def nuovo():
    """Creazione di un impianto, con il piano proposto dal catalogo."""
    if request.method == 'POST':
        dati, errori = _valida_impianto(request.form)
        if errori:
            for e in errori:
                flash(e, 'danger')
        else:
            impianto_id = execute(
                """INSERT INTO impianti
                   (struttura_id, divisione_id, nome, tipo, tipo_custom,
                    descrizione, ubicazione, anno_installazione, identificativo,
                    stato, manutentore_id, note, created_by, updated_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (g.struttura_id, dati['divisione_id'], dati['nome'], dati['tipo'],
                 dati['tipo_custom'], dati['descrizione'], dati['ubicazione'],
                 dati['anno_installazione'], dati['identificativo'],
                 dati['stato'], dati['manutentore_id'], dati['note'],
                 g.user['id'], g.user['id'])
            )
            creati = impianti_service.applica_catalogo(
                impianto_id, dati['tipo'], request.form.getlist('catalogo'),
                time.strftime('%Y-%m-%d'))
            log_attivita('creazione', 'impianto', impianto_id,
                         f"Impianto {dati['nome']} ({creati} voci di piano)")
            flash('Impianto creato.', 'success')
            return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))

    divisioni = query_all(
        "SELECT * FROM divisioni WHERE struttura_id = ? ORDER BY nome",
        (getattr(g, 'struttura_id', None),))
    manutentori = query_all(
        "SELECT * FROM manutentori WHERE struttura_id = ? AND attivo = 1"
        " ORDER BY ragione_sociale", (getattr(g, 'struttura_id', None),))
    return render_template(
        'impianti/form.html', impianto=None, divisioni=divisioni,
        manutentori=manutentori, tipi=TIPI_IMPIANTO, stati=STATI_IMPIANTO,
        catalogo={t: voci_per_tipo(t) for t in TIPI_IMPIANTO})


@impianti_bp.route('/<int:impianto_id>')
@login_required
def dettaglio(impianto_id):
    """Scheda dell'impianto: anagrafica, componenti, documenti, piano, storico."""
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))

    componenti = query_all(
        "SELECT * FROM impianti_componenti WHERE impianto_id = ?"
        " ORDER BY descrizione", (impianto_id,))
    documenti = query_all(
        "SELECT * FROM impianti_documenti WHERE impianto_id = ?"
        " ORDER BY data_documento DESC, id DESC", (impianto_id,))
    piano = query_all(
        """SELECT s.*, c.descrizione as componente_descrizione,
                  CAST(julianday(s.prossima_scadenza) - julianday('now')
                       AS INTEGER) as giorni_rimasti
           FROM impianti_scadenze s
           LEFT JOIN impianti_componenti c ON c.id = s.componente_id
           WHERE s.impianto_id = ? ORDER BY s.attiva DESC, s.prossima_scadenza""",
        (impianto_id,))
    interventi = query_all(
        """SELECT i.*, m.ragione_sociale as manutentore_nome,
                  s.nome as scadenza_nome
           FROM impianti_interventi i
           LEFT JOIN manutentori m ON m.id = i.manutentore_id
           LEFT JOIN impianti_scadenze s ON s.id = i.scadenza_id
           WHERE i.impianto_id = ? ORDER BY i.data_intervento DESC, i.id DESC""",
        (impianto_id,))
    divisione = query_one("SELECT * FROM divisioni WHERE id = ?",
                          (impianto['divisione_id'],))
    manutentori = query_all(
        "SELECT * FROM manutentori WHERE struttura_id = ? AND attivo = 1"
        " ORDER BY ragione_sociale", (impianto['struttura_id'],))

    return render_template(
        'impianti/dettaglio.html', impianto=impianto, divisione=divisione,
        componenti=componenti, documenti=documenti, piano=piano,
        interventi=interventi, manutentori=manutentori,
        voci_catalogo=voci_mancanti(impianto['tipo'],
                                    [p['nome'] for p in piano]))
```

- [ ] **Step 4: Registrare il blueprint e la voce di menu**

In `app.py`, nel blocco delle registrazioni (391-418), accanto agli altri:

```python
    from impianti import impianti_bp
    app.register_blueprint(impianti_bp)
```

In `templates/base.html`, dopo la voce "Apparecchi" del menu desktop (~riga 68)
e la corrispondente del menu ridotto (~riga 239):

```html
<li class="nav-item">
  <a class="nav-link {{ 'active' if request.blueprint == 'impianti' }}"
     href="{{ url_for('impianti.lista') }}">
    <i class="bi bi-diagram-3"></i> Impianti
  </a>
</li>
```

- [ ] **Step 5: Creare i template**

`templates/partials/impianti_table.html` — solo la tabella, nessun `<html>`:

```html
<table class="table table-hover align-middle">
  <thead>
    <tr>
      <th>Nome</th><th>Tipo</th><th>Divisione</th><th>Ubicazione</th>
      <th>Stato</th><th>Prossima scadenza</th>
    </tr>
  </thead>
  <tbody>
    {% for i in impianti %}
    <tr>
      <td><a href="{{ url_for('impianti.dettaglio', impianto_id=i.id) }}">{{ i.nome }}</a></td>
      <td>{{ i.tipo_custom or i.tipo|replace('_', ' ')|capitalize }}</td>
      <td>{{ i.divisione_nome or '-' }}</td>
      <td>{{ i.ubicazione or '-' }}</td>
      <td>{{ i.stato|replace('_', ' ')|capitalize }}</td>
      <td>{{ i.prima_scadenza or '-' }}</td>
    </tr>
    {% else %}
    <tr><td colspan="6" class="text-muted text-center">Nessun impianto.</td></tr>
    {% endfor %}
  </tbody>
</table>
```

`templates/impianti/lista.html` estende `base.html`, contiene il form dei filtri
(`hx-get="{{ url_for('impianti.lista') }}?partial=1"`, `hx-target="#tabella-impianti"`),
il pulsante "Nuovo impianto" e `<div id="tabella-impianti">{% include 'partials/impianti_table.html' %}</div>`.

`templates/impianti/form.html` — form POST verso `impianti.nuovo` o
`impianti.modifica`, con `{{ csrf_token() }}`, i campi dell'anagrafica, la select
di `tipo` (che mostra/nasconde `tipo_custom` via JS su `change`), e — solo in
creazione — i checkbox `name="catalogo"` con `value="{{ voce.nome }}"` per le
voci di `catalogo[tipo]`.

`templates/impianti/dettaglio.html` — schede Bootstrap (`nav-tabs`) su
anagrafica, componenti, documenti, piano, interventi; ogni tabella con il suo
form di inserimento e `{{ csrf_token() }}`.

- [ ] **Step 6: Eseguire i test e vederli passare**

Run: `python -m pytest tests/test_impianti.py -q`
Expected: PASS (17 test).

- [ ] **Step 7: Commit**

```bash
git add impianti.py app.py templates/impianti templates/partials/impianti_table.html templates/base.html tests/test_impianti.py
git commit -m "feat(impianti): blueprint con lista, creazione e dettaglio"
```

---

## Task 6: Modifica, dismissione e componenti

**Files:**
- Modify: `impianti.py` (rotte nuove), `templates/impianti/dettaglio.html`
- Test: `tests/test_impianti.py`

**Interfaces:**
- Consumes: `_valida_impianto()`, `impianto_accessibile()`
- Produces: endpoint `impianti.modifica`, `impianti.dismetti`,
  `impianti.componenti`, `impianti.elimina_componente`

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# tests/test_impianti.py — in coda
def _crea_impianto(ambiente, chiave='a', nome='Cabina'):
    d = ambiente[chiave]
    return execute("INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
                   " VALUES (?, ?, ?, 'elettrico')",
                   (d['struttura'], d['divisione'], nome))


def test_dismissione_non_cancella(client, app, ambiente):
    with app.app_context():
        impianto = _crea_impianto(ambiente)
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto}/dismetti', follow_redirects=True)
    with app.app_context():
        riga = query_one("SELECT * FROM impianti WHERE id = ?", (impianto,))
        assert riga is not None and riga['stato'] == 'dismesso'


def test_componente_su_impianto_altrui_rifiutato(client, app, ambiente):
    with app.app_context():
        impianto_b = _crea_impianto(ambiente, 'b', 'Impianto B')
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto_b}/componenti',
                data={'descrizione': 'Intruso'}, follow_redirects=True)
    with app.app_context():
        assert query_all("SELECT 1 FROM impianti_componenti"
                         " WHERE impianto_id = ?", (impianto_b,)) == []


def test_componente_aggiunto_e_rimosso(client, app, ambiente):
    with app.app_context():
        impianto = _crea_impianto(ambiente)
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto}/componenti', data={
        'descrizione': 'Quadro generale', 'marca': 'ABB'},
        follow_redirects=True)
    with app.app_context():
        comp = query_one("SELECT * FROM impianti_componenti WHERE impianto_id = ?",
                         (impianto,))
        assert comp['descrizione'] == 'Quadro generale'
    client.post(f'/impianti/{impianto}/componenti/{comp["id"]}/elimina',
                follow_redirects=True)
    with app.app_context():
        assert query_all("SELECT 1 FROM impianti_componenti WHERE id = ?",
                         (comp['id'],)) == []
```

- [ ] **Step 2: Eseguire i test e vederli fallire**

Run: `python -m pytest tests/test_impianti.py -q -k "dismissione or componente"`
Expected: FAIL — 404.

- [ ] **Step 3: Implementare**

In coda a `impianti.py`:

```python
@impianti_bp.route('/<int:impianto_id>/modifica', methods=['GET', 'POST'])
@tecnico_o_admin_required
def modifica(impianto_id):
    """Modifica dell'anagrafica. Il piano non si tocca da qui."""
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))

    if request.method == 'POST':
        dati, errori = _valida_impianto(request.form, edit_id=impianto_id)
        if errori:
            for e in errori:
                flash(e, 'danger')
        else:
            execute(
                """UPDATE impianti SET divisione_id = ?, nome = ?, tipo = ?,
                       tipo_custom = ?, descrizione = ?, ubicazione = ?,
                       anno_installazione = ?, identificativo = ?, stato = ?,
                       manutentore_id = ?, note = ?, updated_by = ?,
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (dati['divisione_id'], dati['nome'], dati['tipo'],
                 dati['tipo_custom'], dati['descrizione'], dati['ubicazione'],
                 dati['anno_installazione'], dati['identificativo'],
                 dati['stato'], dati['manutentore_id'], dati['note'],
                 g.user['id'], impianto_id)
            )
            log_attivita('modifica', 'impianto', impianto_id,
                         f"Impianto {dati['nome']}")
            flash('Impianto aggiornato.', 'success')
            return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))

    divisioni = query_all(
        "SELECT * FROM divisioni WHERE struttura_id = ? ORDER BY nome",
        (impianto['struttura_id'],))
    manutentori = query_all(
        "SELECT * FROM manutentori WHERE struttura_id = ? AND attivo = 1"
        " ORDER BY ragione_sociale", (impianto['struttura_id'],))
    return render_template(
        'impianti/form.html', impianto=impianto, divisioni=divisioni,
        manutentori=manutentori, tipi=TIPI_IMPIANTO, stati=STATI_IMPIANTO,
        catalogo={})


@impianti_bp.route('/<int:impianto_id>/dismetti', methods=['POST'])
@tecnico_o_admin_required
def dismetti(impianto_id):
    """Cancellazione logica: stato 'dismesso', righe intatte."""
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))
    execute("UPDATE impianti SET stato = 'dismesso', updated_by = ?,"
            " updated_at = datetime('now') WHERE id = ?",
            (g.user['id'], impianto_id))
    log_attivita('dismissione', 'impianto', impianto_id,
                 f"Impianto {impianto['nome']} dismesso")
    flash('Impianto dismesso.', 'success')
    return redirect(url_for('impianti.lista'))


@impianti_bp.route('/<int:impianto_id>/componenti', methods=['POST'])
@tecnico_o_admin_required
def componenti(impianto_id):
    """Aggiunge un componente all'impianto."""
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))
    descrizione = (request.form.get('descrizione') or '').strip()
    if not descrizione:
        flash('La descrizione del componente è obbligatoria.', 'danger')
        return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))
    execute(
        """INSERT INTO impianti_componenti
           (impianto_id, descrizione, marca, modello, matricola, ubicazione, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (impianto_id, descrizione,
         (request.form.get('marca') or '').strip() or None,
         (request.form.get('modello') or '').strip() or None,
         (request.form.get('matricola') or '').strip() or None,
         (request.form.get('ubicazione') or '').strip() or None,
         (request.form.get('note') or '').strip() or None)
    )
    log_attivita('creazione', 'impianto_componente', impianto_id,
                 f"Componente {descrizione} su {impianto['nome']}")
    flash('Componente aggiunto.', 'success')
    return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))


@impianti_bp.route('/<int:impianto_id>/componenti/<int:componente_id>/elimina',
                   methods=['POST'])
@tecnico_o_admin_required
def elimina_componente(impianto_id, componente_id):
    """Elimina un componente. Le righe di piano che lo citano restano, con
    componente_id a NULL (ON DELETE SET NULL)."""
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))
    execute("DELETE FROM impianti_componenti WHERE id = ? AND impianto_id = ?",
            (componente_id, impianto_id))
    log_attivita('eliminazione', 'impianto_componente', impianto_id,
                 f"Componente {componente_id} di {impianto['nome']}")
    flash('Componente eliminato.', 'success')
    return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))
```

- [ ] **Step 4: Eseguire i test e vederli passare**

Run: `python -m pytest tests/test_impianti.py -q`
Expected: PASS (20 test).

- [ ] **Step 5: Commit**

```bash
git add impianti.py templates/impianti/dettaglio.html tests/test_impianti.py
git commit -m "feat(impianti): modifica, dismissione e componenti"
```

---

## Task 7: Documentazione iniziale (upload, download, eliminazione)

**Files:**
- Modify: `impianti.py`, `templates/impianti/dettaglio.html`
- Test: `tests/test_impianti.py`

**Interfaces:**
- Consumes: `models.upload_subdir('impianti', struttura_id)`
- Produces: endpoint `impianti.carica_documento`, `impianti.scarica_documento`,
  `impianti.elimina_documento`; costanti `TIPI_DOCUMENTO`, `ESTENSIONI_DOCUMENTO`

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# tests/test_impianti.py — in coda
import io


def test_documento_caricato_con_emittente(client, app, ambiente):
    with app.app_context():
        impianto = _crea_impianto(ambiente, nome='Cabina doc')
    entra(client, ambiente['a']['email'])
    risposta = client.post(f'/impianti/{impianto}/documenti', data={
        'tipo': 'dichiarazione_conformita',
        'descrizione': 'DiCo quadro generale',
        'data_documento': '2020-05-12',
        'emittente_ragione_sociale': 'Elettro Srl',
        'emittente_email': 'info@elettro.it',
        'documento': (io.BytesIO(b'%PDF-1.4 finto'), 'dico.pdf'),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert risposta.status_code == 200
    with app.app_context():
        doc = query_one("SELECT * FROM impianti_documenti WHERE impianto_id = ?",
                        (impianto,))
        assert doc['tipo'] == 'dichiarazione_conformita'
        assert doc['emittente_ragione_sociale'] == 'Elettro Srl'
        assert doc['filepath'].startswith('strutture/')
        assert doc['filesize'] > 0


def test_documento_estensione_non_ammessa_rifiutata(client, app, ambiente):
    with app.app_context():
        impianto = _crea_impianto(ambiente, nome='Cabina exe')
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto}/documenti', data={
        'tipo': 'altro',
        'documento': (io.BytesIO(b'MZ'), 'virus.exe'),
    }, content_type='multipart/form-data', follow_redirects=True)
    with app.app_context():
        assert query_all("SELECT 1 FROM impianti_documenti"
                         " WHERE impianto_id = ?", (impianto,)) == []


def test_documento_altrui_non_scaricabile(client, app, ambiente):
    with app.app_context():
        impianto_b = _crea_impianto(ambiente, 'b', 'Cabina B')
        doc_b = execute(
            "INSERT INTO impianti_documenti (impianto_id, tipo, filename,"
            " filepath) VALUES (?, 'progetto', 'segreto.pdf', 'x/segreto.pdf')",
            (impianto_b,))
    entra(client, ambiente['a']['email'])
    risposta = client.get(f'/impianti/documenti/{doc_b}')
    assert risposta.status_code in (302, 403, 404)
```

- [ ] **Step 2: Eseguire i test e vederli fallire**

Run: `python -m pytest tests/test_impianti.py -q -k documento`
Expected: FAIL — 404.

- [ ] **Step 3: Implementare**

In `impianti.py`, accanto alle altre costanti:

```python
TIPI_DOCUMENTO = ('progetto', 'dichiarazione_conformita', 'collaudo',
                  'certificato', 'libretto', 'planimetria', 'verbale', 'altro')
ESTENSIONI_DOCUMENTO = {'pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx', 'xls',
                        'xlsx', 'dwg', 'dxf', 'zip'}
```

E in coda al file:

```python
@impianti_bp.route('/<int:impianto_id>/documenti', methods=['POST'])
@login_required
def carica_documento(impianto_id):
    """Carica un documento dell'impianto con i dati dell'emittente.

    L'emittente e' testo libero, non una chiave esterna: le ditte che firmano
    progetti e collaudi cambiano a ogni documento e non tornano piu'. I
    manutentori, che invece tornano, hanno una tabella loro.
    """
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))

    file = request.files.get('documento')
    if not file or not file.filename:
        flash('Nessun file selezionato.', 'warning')
        return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ESTENSIONI_DOCUMENTO:
        flash('Formato file non supportato.', 'danger')
        return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))

    tipo = request.form.get('tipo', 'altro')
    if tipo not in TIPI_DOCUMENTO:
        tipo = 'altro'

    uploads_dir, rel_prefix = upload_subdir('impianti', impianto['struttura_id'])
    filename = f"{int(time.time())}_{secure_filename(file.filename)}"
    filepath = os.path.join(uploads_dir, filename)
    file.save(filepath)

    execute(
        """INSERT INTO impianti_documenti
           (impianto_id, tipo, descrizione, data_documento,
            emittente_ragione_sociale, emittente_indirizzo, emittente_telefono,
            emittente_email, filename, filepath, filesize, uploaded_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (impianto_id, tipo,
         (request.form.get('descrizione') or '').strip() or None,
         (request.form.get('data_documento') or '').strip() or None,
         (request.form.get('emittente_ragione_sociale') or '').strip() or None,
         (request.form.get('emittente_indirizzo') or '').strip() or None,
         (request.form.get('emittente_telefono') or '').strip() or None,
         (request.form.get('emittente_email') or '').strip() or None,
         secure_filename(file.filename), f"{rel_prefix}/{filename}",
         os.path.getsize(filepath), g.user['id'])
    )
    log_attivita('creazione', 'impianto_documento', impianto_id,
                 f"Documento {tipo} su {impianto['nome']}")
    flash('Documento caricato.', 'success')
    return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))


@impianti_bp.route('/documenti/<int:documento_id>')
@login_required
def scarica_documento(documento_id):
    """Scarica un documento. Il permesso passa dall'impianto, non dal file."""
    doc = query_one("SELECT * FROM impianti_documenti WHERE id = ?",
                    (documento_id,))
    if not doc or not impianto_accessibile(doc['impianto_id']):
        abort(404)
    from flask import current_app
    percorso = os.path.join(current_app.config['UPLOADS_PATH'], doc['filepath'])
    if not os.path.exists(percorso):
        flash('File non presente sul server.', 'danger')
        return redirect(url_for('impianti.dettaglio',
                                impianto_id=doc['impianto_id']))
    return send_file(percorso, as_attachment=True,
                     download_name=doc['filename'])


@impianti_bp.route('/documenti/<int:documento_id>/elimina', methods=['POST'])
@tecnico_o_admin_required
def elimina_documento(documento_id):
    """Elimina un documento e il file su disco."""
    doc = query_one("SELECT * FROM impianti_documenti WHERE id = ?",
                    (documento_id,))
    if not doc or not impianto_accessibile(doc['impianto_id']):
        abort(404)
    from flask import current_app
    percorso = os.path.join(current_app.config['UPLOADS_PATH'], doc['filepath'])
    if os.path.exists(percorso):
        try:
            os.remove(percorso)
        except OSError:
            # La riga sparisce comunque: un file rimasto sul disco e' meno
            # dannoso di un elenco che mostra un documento gia' revocato.
            pass
    execute("DELETE FROM impianti_documenti WHERE id = ?", (documento_id,))
    log_attivita('eliminazione', 'impianto_documento', doc['impianto_id'],
                 f"Documento {doc['filename']}")
    flash('Documento eliminato.', 'success')
    return redirect(url_for('impianti.dettaglio',
                            impianto_id=doc['impianto_id']))
```

- [ ] **Step 4: Aggiungere la scheda "Documenti" al dettaglio**

In `templates/impianti/dettaglio.html`, nella scheda documenti: la tabella dei
documenti (tipo, descrizione, data, emittente, link a
`impianti.scarica_documento`, form POST di eliminazione con `{{ csrf_token() }}`)
e il form `enctype="multipart/form-data"` verso `impianti.carica_documento`
con `tipo`, `descrizione`, `data_documento`, i quattro campi `emittente_*` e
`documento`.

- [ ] **Step 5: Eseguire i test e vederli passare**

Run: `python -m pytest tests/test_impianti.py -q`
Expected: PASS (23 test).

- [ ] **Step 6: Commit**

```bash
git add impianti.py templates/impianti/dettaglio.html tests/test_impianti.py
git commit -m "feat(impianti): documentazione iniziale con dati dell'emittente"
```

---

## Task 8: Piano di manutenzione (CRUD + catalogo differito)

**Files:**
- Modify: `impianti.py`, `templates/impianti/dettaglio.html`
- Test: `tests/test_impianti.py`

**Interfaces:**
- Consumes: `impianti_service.applica_catalogo()`, `impianti_catalogo.voci_mancanti()`
- Produces: endpoint `impianti.nuova_scadenza`, `impianti.modifica_scadenza`,
  `impianti.sospendi_scadenza`, `impianti.piano_catalogo`; helper
  `_valida_scadenza(form) -> (dict, list[str])`

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# tests/test_impianti.py — in coda
def test_scadenza_creata_a_mano(client, app, ambiente):
    with app.app_context():
        impianto = _crea_impianto(ambiente, nome='Cabina piano')
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto}/piano/nuova', data={
        'nome': 'Termografia quadri', 'periodicita_mesi': '12',
        'prossima_scadenza': '2027-03-01', 'giorni_anticipo': '45',
        'email_extra': 'perito@test.it', 'avvisa_manutentore': '1',
    }, follow_redirects=True)
    with app.app_context():
        riga = query_one("SELECT * FROM impianti_scadenze WHERE impianto_id = ?",
                         (impianto,))
        assert riga['nome'] == 'Termografia quadri'
        assert riga['giorni_anticipo'] == 45
        assert riga['email_extra'] == 'perito@test.it'
        assert riga['avvisa_manutentore'] == 1


def test_scadenza_una_tantum_senza_periodicita(client, app, ambiente):
    with app.app_context():
        impianto = _crea_impianto(ambiente, nome='Cabina una tantum')
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto}/piano/nuova', data={
        'nome': 'Collaudo iniziale', 'periodicita_mesi': '',
        'prossima_scadenza': '2026-09-01'}, follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT periodicita_mesi FROM impianti_scadenze"
                         " WHERE impianto_id = ?",
                         (impianto,))['periodicita_mesi'] is None


def test_scadenza_sospesa_esce_dalla_vista(client, app, ambiente):
    with app.app_context():
        impianto, scad = _impianto_con_piano(ambiente, scadenza='2026-09-01')
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/piano/{scad}/sospendi', follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT attiva FROM impianti_scadenze WHERE id = ?",
                         (scad,))['attiva'] == 0
        assert query_all("SELECT 1 FROM prossime_scadenze_impianti"
                         " WHERE scadenza_id = ?", (scad,)) == []


def test_catalogo_differito_offre_solo_le_voci_mancanti(client, app, ambiente):
    with app.app_context():
        a = ambiente['a']
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Antincendio B1', 'antincendio')",
            (a['struttura'], a['divisione']))
        execute("INSERT INTO impianti_scadenze (impianto_id, nome,"
                " periodicita_mesi, prossima_scadenza)"
                " VALUES (?, 'Controllo estintori', 6, '2026-09-01')", (impianto,))
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto}/piano/catalogo', data={
        'catalogo': ['Controllo idranti', 'Controllo estintori']},
        follow_redirects=True)
    with app.app_context():
        nomi = [r['nome'] for r in query_all(
            "SELECT nome FROM impianti_scadenze WHERE impianto_id = ?"
            " ORDER BY nome", (impianto,))]
        assert nomi == ['Controllo estintori', 'Controllo idranti']
```

- [ ] **Step 2: Eseguire i test e vederli fallire**

Run: `python -m pytest tests/test_impianti.py -q -k "scadenza_creata or una_tantum_senza or sospesa or catalogo_differito"`
Expected: FAIL — 404.

- [ ] **Step 3: Implementare**

In coda a `impianti.py`:

```python
def _valida_scadenza(form):
    """Valida una riga di piano. Restituisce (dati, errori)."""
    errori = []
    nome = (form.get('nome') or '').strip()
    if not nome:
        errori.append('Il nome della verifica è obbligatorio.')
    prossima = (form.get('prossima_scadenza') or '').strip()
    if not prossima:
        errori.append('La data della prossima scadenza è obbligatoria.')

    periodicita = form.get('periodicita_mesi', type=int)
    if periodicita is not None and not (1 <= periodicita <= 600):
        errori.append('Periodicità non valida (1-600 mesi).')
    anticipo = form.get('giorni_anticipo', type=int)
    if anticipo is None:
        anticipo = 30
    if not (0 <= anticipo <= 365):
        errori.append('Giorni di anticipo non validi (0-365).')

    return {
        'nome': nome,
        'riferimento_normativo':
            (form.get('riferimento_normativo') or '').strip() or None,
        # Vuoto significa una tantum: eseguita una volta, la riga si chiude.
        'periodicita_mesi': periodicita or None,
        'prossima_scadenza': prossima,
        'giorni_anticipo': anticipo,
        'email_extra': (form.get('email_extra') or '').strip() or None,
        'avvisa_manutentore': 1 if form.get('avvisa_manutentore') else 0,
        'componente_id': form.get('componente_id', type=int) or None,
        'note': (form.get('note') or '').strip() or None,
    }, errori


@impianti_bp.route('/<int:impianto_id>/piano/nuova', methods=['POST'])
@tecnico_o_admin_required
def nuova_scadenza(impianto_id):
    """Aggiunge una riga al piano di manutenzione/verifica."""
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))
    dati, errori = _valida_scadenza(request.form)
    if errori:
        for e in errori:
            flash(e, 'danger')
        return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))
    execute(
        """INSERT INTO impianti_scadenze
           (impianto_id, componente_id, nome, riferimento_normativo,
            periodicita_mesi, prossima_scadenza, giorni_anticipo, email_extra,
            avvisa_manutentore, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (impianto_id, dati['componente_id'], dati['nome'],
         dati['riferimento_normativo'], dati['periodicita_mesi'],
         dati['prossima_scadenza'], dati['giorni_anticipo'],
         dati['email_extra'], dati['avvisa_manutentore'], dati['note'])
    )
    log_attivita('creazione', 'impianto_scadenza', impianto_id,
                 f"Piano: {dati['nome']} su {impianto['nome']}")
    flash('Voce di piano aggiunta.', 'success')
    return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))


@impianti_bp.route('/piano/<int:scadenza_id>/modifica', methods=['POST'])
@tecnico_o_admin_required
def modifica_scadenza(scadenza_id):
    """Modifica una riga di piano."""
    riga = query_one("SELECT * FROM impianti_scadenze WHERE id = ?",
                     (scadenza_id,))
    if not riga or not impianto_accessibile(riga['impianto_id']):
        abort(404)
    dati, errori = _valida_scadenza(request.form)
    if errori:
        for e in errori:
            flash(e, 'danger')
    else:
        execute(
            """UPDATE impianti_scadenze SET componente_id = ?, nome = ?,
                   riferimento_normativo = ?, periodicita_mesi = ?,
                   prossima_scadenza = ?, giorni_anticipo = ?, email_extra = ?,
                   avvisa_manutentore = ?, note = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (dati['componente_id'], dati['nome'], dati['riferimento_normativo'],
             dati['periodicita_mesi'], dati['prossima_scadenza'],
             dati['giorni_anticipo'], dati['email_extra'],
             dati['avvisa_manutentore'], dati['note'], scadenza_id)
        )
        log_attivita('modifica', 'impianto_scadenza', riga['impianto_id'],
                     f"Piano: {dati['nome']}")
        flash('Voce di piano aggiornata.', 'success')
    return redirect(url_for('impianti.dettaglio',
                            impianto_id=riga['impianto_id']))


@impianti_bp.route('/piano/<int:scadenza_id>/sospendi', methods=['POST'])
@tecnico_o_admin_required
def sospendi_scadenza(scadenza_id):
    """Sospende o riattiva una riga di piano.

    Sospendere, non cancellare: gli interventi gia' registrati continuano a
    puntarla, e riattivarla ricostruisce il ciclo senza reinserire nulla.
    """
    riga = query_one("SELECT * FROM impianti_scadenze WHERE id = ?",
                     (scadenza_id,))
    if not riga or not impianto_accessibile(riga['impianto_id']):
        abort(404)
    nuovo = 0 if riga['attiva'] else 1
    execute("UPDATE impianti_scadenze SET attiva = ?,"
            " updated_at = datetime('now') WHERE id = ?", (nuovo, scadenza_id))
    log_attivita('modifica', 'impianto_scadenza', riga['impianto_id'],
                 f"Piano: {riga['nome']} {'riattivata' if nuovo else 'sospesa'}")
    flash('Voce riattivata.' if nuovo else 'Voce sospesa.', 'success')
    return redirect(url_for('impianti.dettaglio',
                            impianto_id=riga['impianto_id']))


@impianti_bp.route('/<int:impianto_id>/piano/catalogo', methods=['POST'])
@tecnico_o_admin_required
def piano_catalogo(impianto_id):
    """Aggiunge al piano voci di catalogo non ancora presenti."""
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))
    presenti = [r['nome'] for r in query_all(
        "SELECT nome FROM impianti_scadenze WHERE impianto_id = ?",
        (impianto_id,))]
    mancanti = {v['nome'] for v in voci_mancanti(impianto['tipo'], presenti)}
    scelti = [n for n in request.form.getlist('catalogo') if n in mancanti]
    creati = impianti_service.applica_catalogo(
        impianto_id, impianto['tipo'], scelti, time.strftime('%Y-%m-%d'))
    log_attivita('creazione', 'impianto_scadenza', impianto_id,
                 f"Catalogo: {creati} voci su {impianto['nome']}")
    flash(f'Aggiunte {creati} voci di piano.', 'success')
    return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))
```

- [ ] **Step 4: Aggiungere la scheda "Piano" al dettaglio**

In `templates/impianti/dettaglio.html`: tabella di `piano` (nome, riferimento,
componente, periodicità, prossima scadenza con badge colorato su
`giorni_rimasti`, anticipo, destinatari extra), il form di inserimento verso
`impianti.nuova_scadenza`, un form POST per riga verso
`impianti.sospendi_scadenza`, e — se `voci_catalogo` non è vuota — i checkbox
`name="catalogo"` verso `impianti.piano_catalogo`. Ogni form con `{{ csrf_token() }}`.

- [ ] **Step 5: Eseguire i test e vederli passare**

Run: `python -m pytest tests/test_impianti.py -q`
Expected: PASS (27 test).

- [ ] **Step 6: Commit**

```bash
git add impianti.py templates/impianti/dettaglio.html tests/test_impianti.py
git commit -m "feat(impianti): piano di manutenzione e catalogo differito"
```

---

## Task 9: Interventi e verbali

**Files:**
- Modify: `impianti.py`, `templates/impianti/dettaglio.html`
- Test: `tests/test_impianti.py`

**Interfaces:**
- Consumes: `impianti_service.registra_intervento()`,
  `models.upload_subdir('impianti', struttura_id)`
- Produces: endpoint `impianti.nuovo_intervento`, `impianti.scarica_verbale`

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# tests/test_impianti.py — in coda
def test_intervento_da_rotta_sposta_la_scadenza(client, app, ambiente):
    with app.app_context():
        impianto, scad = _impianto_con_piano(ambiente, periodicita=12,
                                             scadenza='2026-02-01')
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto}/interventi/nuovo', data={
        'scadenza_id': scad, 'tipo': 'verifica',
        'data_intervento': '2026-01-20', 'esito': 'positivo',
        'descrizione': 'Verifica eseguita',
        'verbale': (io.BytesIO(b'%PDF-1.4 verbale'), 'verbale.pdf'),
    }, content_type='multipart/form-data', follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT prossima_scadenza FROM impianti_scadenze"
                         " WHERE id = ?", (scad,))['prossima_scadenza'] == '2027-01-20'
        intervento = query_one("SELECT * FROM impianti_interventi"
                               " WHERE impianto_id = ?", (impianto,))
        assert intervento['verbale_path'].startswith('strutture/')


def test_intervento_su_impianto_altrui_rifiutato(client, app, ambiente):
    with app.app_context():
        impianto_b = _crea_impianto(ambiente, 'b', 'Cabina B int')
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/{impianto_b}/interventi/nuovo', data={
        'tipo': 'ordinaria', 'data_intervento': '2026-01-20'},
        follow_redirects=True)
    with app.app_context():
        assert query_all("SELECT 1 FROM impianti_interventi"
                         " WHERE impianto_id = ?", (impianto_b,)) == []
```

- [ ] **Step 2: Eseguire i test e vederli fallire**

Run: `python -m pytest tests/test_impianti.py -q -k "intervento_da_rotta or intervento_su_impianto"`
Expected: FAIL — 404.

- [ ] **Step 3: Implementare**

In coda a `impianti.py`:

```python
@impianti_bp.route('/<int:impianto_id>/interventi/nuovo', methods=['POST'])
@login_required
def nuovo_intervento(impianto_id):
    """Registra un intervento; il servizio decide se il piano avanza."""
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))

    data_intervento = (request.form.get('data_intervento') or '').strip()
    if not data_intervento:
        flash('La data dell\'intervento è obbligatoria.', 'danger')
        return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))

    tipo = request.form.get('tipo', 'ordinaria')
    if tipo not in ('verifica', 'ordinaria', 'straordinaria', 'riparazione'):
        tipo = 'ordinaria'
    esito = request.form.get('esito') or None
    if esito not in ('positivo', 'negativo', 'con_riserva', None):
        esito = None

    # La scadenza indicata deve appartenere a questo impianto: senza il
    # controllo, un id qualunque farebbe avanzare il piano di un'altra
    # struttura.
    scadenza_id = request.form.get('scadenza_id', type=int) or None
    if scadenza_id and not query_one(
            "SELECT 1 FROM impianti_scadenze WHERE id = ? AND impianto_id = ?",
            (scadenza_id, impianto_id)):
        scadenza_id = None

    verbale_path = None
    file = request.files.get('verbale')
    if file and file.filename:
        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        if ext not in ESTENSIONI_DOCUMENTO:
            flash('Formato del verbale non supportato.', 'danger')
            return redirect(url_for('impianti.dettaglio',
                                    impianto_id=impianto_id))
        uploads_dir, rel_prefix = upload_subdir('impianti',
                                                impianto['struttura_id'])
        filename = f"{int(time.time())}_{secure_filename(file.filename)}"
        file.save(os.path.join(uploads_dir, filename))
        verbale_path = f"{rel_prefix}/{filename}"

    _, nuova = impianti_service.registra_intervento(impianto_id, {
        'scadenza_id': scadenza_id,
        'componente_id': request.form.get('componente_id', type=int) or None,
        'tipo': tipo,
        'data_intervento': data_intervento,
        'esito': esito,
        'manutentore_id': request.form.get('manutentore_id', type=int) or None,
        'tecnico_ditta': (request.form.get('tecnico_ditta') or '').strip() or None,
        'descrizione': (request.form.get('descrizione') or '').strip() or None,
        'costo': request.form.get('costo', type=float),
        'verbale_path': verbale_path,
        'note': (request.form.get('note') or '').strip() or None,
    }, utente_id=g.user['id'])

    log_attivita('creazione', 'impianto_intervento', impianto_id,
                 f"Intervento {tipo} del {data_intervento} su {impianto['nome']}")
    if nuova:
        flash(f'Intervento registrato. Prossima scadenza: {nuova}.', 'success')
    elif esito == 'negativo':
        flash('Intervento registrato con esito negativo: la scadenza resta '
              'aperta.', 'warning')
    else:
        flash('Intervento registrato.', 'success')
    return redirect(url_for('impianti.dettaglio', impianto_id=impianto_id))


@impianti_bp.route('/interventi/<int:intervento_id>/verbale')
@login_required
def scarica_verbale(intervento_id):
    """Scarica il verbale di un intervento."""
    intervento = query_one("SELECT * FROM impianti_interventi WHERE id = ?",
                           (intervento_id,))
    if (not intervento or not intervento['verbale_path']
            or not impianto_accessibile(intervento['impianto_id'])):
        abort(404)
    from flask import current_app
    percorso = os.path.join(current_app.config['UPLOADS_PATH'],
                            intervento['verbale_path'])
    if not os.path.exists(percorso):
        flash('Verbale non presente sul server.', 'danger')
        return redirect(url_for('impianti.dettaglio',
                                impianto_id=intervento['impianto_id']))
    return send_file(percorso, as_attachment=True,
                     download_name=os.path.basename(intervento['verbale_path']))
```

- [ ] **Step 4: Aggiungere la scheda "Interventi" al dettaglio**

In `templates/impianti/dettaglio.html`: tabella dello storico (data, tipo,
esito con badge, voce di piano, manutentore, costo, link al verbale) e il form
`enctype="multipart/form-data"` verso `impianti.nuovo_intervento` con la select
delle voci di piano attive, `tipo`, `data_intervento`, `esito`,
`manutentore_id`, `tecnico_ditta`, `descrizione`, `costo`, `verbale`,
più `{{ csrf_token() }}`.

- [ ] **Step 5: Eseguire i test e vederli passare**

Run: `python -m pytest tests/test_impianti.py -q`
Expected: PASS (29 test).

- [ ] **Step 6: Commit**

```bash
git add impianti.py templates/impianti/dettaglio.html tests/test_impianti.py
git commit -m "feat(impianti): registrazione interventi con verbale"
```

---

## Task 10: Anagrafica manutentori

**Files:**
- Modify: `impianti.py`
- Create: `templates/impianti/manutentori.html`
- Test: `tests/test_impianti.py`

**Interfaces:**
- Produces: endpoint `impianti.manutentori`, `impianti.nuovo_manutentore`,
  `impianti.modifica_manutentore`, `impianti.elimina_manutentore`

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# tests/test_impianti.py — in coda
def test_manutentore_creato_nella_struttura_giusta(client, app, ambiente):
    entra(client, ambiente['a']['email'])
    client.post('/impianti/manutentori/nuovo', data={
        'ragione_sociale': 'Termo Service Srl', 'email': 'info@termo.it',
        'telefono': '0300000'}, follow_redirects=True)
    with app.app_context():
        riga = query_one("SELECT * FROM manutentori"
                         " WHERE ragione_sociale = 'Termo Service Srl'")
        assert riga['struttura_id'] == ambiente['a']['struttura']


def test_elenco_manutentori_isolato(client, app, ambiente):
    with app.app_context():
        execute("INSERT INTO manutentori (struttura_id, ragione_sociale)"
                " VALUES (?, 'DITTA-SEGRETA-B')", (ambiente['b']['struttura'],))
    entra(client, ambiente['a']['email'])
    corpo = client.get('/impianti/manutentori').get_data(as_text=True)
    assert 'DITTA-SEGRETA-B' not in corpo


def test_manutentore_eliminato_non_cancella_gli_impianti(client, app, ambiente):
    """ON DELETE SET NULL: l'impianto resta, senza manutentore."""
    with app.app_context():
        a = ambiente['a']
        mid = execute("INSERT INTO manutentori (struttura_id, ragione_sociale)"
                      " VALUES (?, 'Elimina Srl')", (a['struttura'],))
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo,"
            " manutentore_id) VALUES (?, ?, 'Cabina M', 'elettrico', ?)",
            (a['struttura'], a['divisione'], mid))
    entra(client, ambiente['a']['email'])
    client.post(f'/impianti/manutentori/{mid}/elimina', follow_redirects=True)
    with app.app_context():
        riga = query_one("SELECT * FROM impianti WHERE id = ?", (impianto,))
        assert riga is not None and riga['manutentore_id'] is None
```

- [ ] **Step 2: Eseguire i test e vederli fallire**

Run: `python -m pytest tests/test_impianti.py -q -k manutentore`
Expected: FAIL — 404.

- [ ] **Step 3: Implementare**

In coda a `impianti.py`:

```python
def _manutentore_in_scope(manutentore_id):
    """La riga del manutentore, solo se della struttura attiva."""
    struttura_id = getattr(g, 'struttura_id', None)
    if not struttura_id:
        return None
    return query_one(
        "SELECT * FROM manutentori WHERE id = ? AND struttura_id = ?",
        (manutentore_id, struttura_id))


def _dati_manutentore(form):
    """Campi del manutentore. Restituisce (dati, errori)."""
    ragione = (form.get('ragione_sociale') or '').strip()
    errori = [] if ragione else ['La ragione sociale è obbligatoria.']
    return {
        'ragione_sociale': ragione,
        'indirizzo': (form.get('indirizzo') or '').strip() or None,
        'telefono': (form.get('telefono') or '').strip() or None,
        'email': (form.get('email') or '').strip() or None,
        'partita_iva': (form.get('partita_iva') or '').strip() or None,
        'note': (form.get('note') or '').strip() or None,
    }, errori


@impianti_bp.route('/manutentori')
@tecnico_o_admin_required
def manutentori():
    """Anagrafica delle ditte manutentrici della struttura."""
    elenco = query_all(
        "SELECT * FROM manutentori WHERE struttura_id = ?"
        " ORDER BY attivo DESC, ragione_sociale",
        (getattr(g, 'struttura_id', None),))
    return render_template('impianti/manutentori.html', manutentori=elenco)


@impianti_bp.route('/manutentori/nuovo', methods=['POST'])
@tecnico_o_admin_required
def nuovo_manutentore():
    """Crea un manutentore nella struttura attiva."""
    struttura_id = getattr(g, 'struttura_id', None)
    if not struttura_id:
        flash('Nessuna struttura attiva.', 'danger')
        return redirect(url_for('impianti.manutentori'))
    dati, errori = _dati_manutentore(request.form)
    if errori:
        for e in errori:
            flash(e, 'danger')
        return redirect(url_for('impianti.manutentori'))
    if query_one("SELECT 1 FROM manutentori WHERE struttura_id = ?"
                 " AND ragione_sociale = ?",
                 (struttura_id, dati['ragione_sociale'])):
        flash('Manutentore già presente.', 'warning')
        return redirect(url_for('impianti.manutentori'))
    mid = execute(
        """INSERT INTO manutentori (struttura_id, ragione_sociale, indirizzo,
               telefono, email, partita_iva, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (struttura_id, dati['ragione_sociale'], dati['indirizzo'],
         dati['telefono'], dati['email'], dati['partita_iva'], dati['note'])
    )
    log_attivita('creazione', 'manutentore', mid, dati['ragione_sociale'])
    flash('Manutentore aggiunto.', 'success')
    return redirect(url_for('impianti.manutentori'))


@impianti_bp.route('/manutentori/<int:manutentore_id>/modifica',
                   methods=['POST'])
@tecnico_o_admin_required
def modifica_manutentore(manutentore_id):
    """Modifica i dati di un manutentore."""
    if not _manutentore_in_scope(manutentore_id):
        abort(404)
    dati, errori = _dati_manutentore(request.form)
    if errori:
        for e in errori:
            flash(e, 'danger')
        return redirect(url_for('impianti.manutentori'))
    execute(
        """UPDATE manutentori SET ragione_sociale = ?, indirizzo = ?,
               telefono = ?, email = ?, partita_iva = ?, note = ?,
               attivo = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (dati['ragione_sociale'], dati['indirizzo'], dati['telefono'],
         dati['email'], dati['partita_iva'], dati['note'],
         1 if request.form.get('attivo') else 0, manutentore_id)
    )
    log_attivita('modifica', 'manutentore', manutentore_id,
                 dati['ragione_sociale'])
    flash('Manutentore aggiornato.', 'success')
    return redirect(url_for('impianti.manutentori'))


@impianti_bp.route('/manutentori/<int:manutentore_id>/elimina',
                   methods=['POST'])
@tecnico_o_admin_required
def elimina_manutentore(manutentore_id):
    """Elimina un manutentore. Impianti e interventi restano, senza il
    riferimento (ON DELETE SET NULL)."""
    riga = _manutentore_in_scope(manutentore_id)
    if not riga:
        abort(404)
    execute("DELETE FROM manutentori WHERE id = ?", (manutentore_id,))
    log_attivita('eliminazione', 'manutentore', manutentore_id,
                 riga['ragione_sociale'])
    flash('Manutentore eliminato.', 'success')
    return redirect(url_for('impianti.manutentori'))
```

- [ ] **Step 4: Creare il template**

`templates/impianti/manutentori.html` estende `base.html`: tabella (ragione
sociale, partita IVA, telefono, email, stato attivo, azioni), form di
inserimento e, per riga, un form di modifica (collassabile) e uno di
eliminazione. Tutti con `{{ csrf_token() }}`.

- [ ] **Step 5: Eseguire i test e vederli passare**

Run: `python -m pytest tests/test_impianti.py -q`
Expected: PASS (32 test).

- [ ] **Step 6: Commit**

```bash
git add impianti.py templates/impianti/manutentori.html tests/test_impianti.py
git commit -m "feat(impianti): anagrafica manutentori"
```

---

## Task 11: Scadenzario unificato, dashboard e badge

**Files:**
- Modify: `manutenzioni.py:370-441` (`scadenzario`), `app.py:475-495` e
  `app.py:526-545`, `auth.py:341-361`,
  `templates/manutenzioni/scadenzario.html`, `templates/partials/scadenze_table.html`
- Test: `tests/test_impianti.py`

**Interfaces:**
- Consumes: viste `prossime_scadenze` e `prossime_scadenze_impianti`,
  `models.filtro_divisione()`
- Produces: in `manutenzioni.py`, `_scadenze_unificate(origine, filtri) ->
  list[sqlite3.Row]` con le colonne normalizzate `origine, oggetto_id, oggetto,
  dettaglio, divisione_id, tipo, prossima_scadenza, giorni_rimasti, priorita`;
  filtro di richiesta `origine` ∈ `tutto` (default) | `apparecchi` | `impianti`

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# tests/test_impianti.py — in coda
def test_scadenzario_mostra_entrambe_le_origini(client, app, ambiente):
    """URL reale: /scadenzario, non /manutenzioni/scadenzario."""
    with app.app_context():
        a = ambiente['a']
        apparecchio = execute(
            "INSERT INTO apparecchi (struttura_id, divisione_id, marca, modello,"
            " matricola, descrizione) VALUES (?, ?, 'ACME', 'X1', 'MAT-APP',"
            " 'Elettrobisturi')", (a['struttura'], a['divisione']))
        execute("INSERT INTO manutenzioni (apparecchio_id, tipo,"
                " data_intervento, prossima_scadenza)"
                " VALUES (?, 'preventiva', date('now', '-30 days'),"
                " date('now', '+5 days'))", (apparecchio,))
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Cabina scadenzario', 'elettrico')",
            (a['struttura'], a['divisione']))
        execute("INSERT INTO impianti_scadenze (impianto_id, nome,"
                " periodicita_mesi, prossima_scadenza)"
                " VALUES (?, 'Verifica di terra', 24, date('now', '+3 days'))",
                (impianto,))

    entra(client, ambiente['a']['email'])
    tutto = client.get('/scadenzario').get_data(as_text=True)
    assert 'MAT-APP' in tutto or 'Elettrobisturi' in tutto
    assert 'Cabina scadenzario' in tutto

    solo_impianti = client.get('/scadenzario?origine=impianti').get_data(as_text=True)
    assert 'Cabina scadenzario' in solo_impianti
    assert 'MAT-APP' not in solo_impianti

    solo_apparecchi = client.get('/scadenzario?origine=apparecchi').get_data(as_text=True)
    assert 'Cabina scadenzario' not in solo_apparecchi


def test_scadenzario_non_mostra_impianti_di_altra_struttura(client, app, ambiente):
    with app.app_context():
        b = ambiente['b']
        impianto_b = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'SEGRETO-B-SCAD', 'elettrico')",
            (b['struttura'], b['divisione']))
        execute("INSERT INTO impianti_scadenze (impianto_id, nome,"
                " periodicita_mesi, prossima_scadenza)"
                " VALUES (?, 'Verifica', 24, date('now', '+2 days'))",
                (impianto_b,))
    entra(client, ambiente['a']['email'])
    assert 'SEGRETO-B-SCAD' not in client.get('/scadenzario').get_data(as_text=True)


def test_badge_conta_anche_gli_impianti(client, app, ambiente):
    with app.app_context():
        a = ambiente['a']
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Cabina badge', 'elettrico')",
            (a['struttura'], a['divisione']))
        execute("INSERT INTO impianti_scadenze (impianto_id, nome,"
                " periodicita_mesi, prossima_scadenza)"
                " VALUES (?, 'Verifica', 24, date('now', '-1 days'))",
                (impianto,))
    entra(client, ambiente['a']['email'])
    with client:
        client.get('/')
        from flask import g
        assert g.scadenze_alert_count >= 1
```

- [ ] **Step 2: Eseguire i test e vederli fallire**

Run: `python -m pytest tests/test_impianti.py -q -k "scadenzario or badge"`
Expected: FAIL — `assert 'Cabina scadenzario' in tutto` (lo scadenzario mostra
solo gli apparecchi), `g.scadenze_alert_count >= 1` falso.

- [ ] **Step 3: Unificare lo scadenzario**

In `manutenzioni.py`, prima di `scadenzario()`:

```python
#: Colonne comuni alle due origini. La UNION si fa su queste, non su SELECT *:
#: le due viste hanno colonne diverse e l'ordine dei campi non coincide.
_SCADENZE_APPARECCHI = """
    SELECT 'apparecchio' AS origine, ps.apparecchio_id AS oggetto_id,
           COALESCE(ps.descrizione, ps.marca || ' ' || ps.modello) AS oggetto,
           ps.matricola AS dettaglio, ps.divisione_id, ps.tipo,
           ps.prossima_scadenza, ps.giorni_rimasti, ps.priorita
    FROM prossime_scadenze ps
    WHERE 1=1 {filtro_div}
"""

_SCADENZE_IMPIANTI = """
    SELECT 'impianto' AS origine, psi.impianto_id AS oggetto_id,
           psi.impianto_nome AS oggetto,
           psi.scadenza_nome AS dettaglio, psi.divisione_id,
           COALESCE(psi.tipo_custom, psi.tipo) AS tipo,
           psi.prossima_scadenza, psi.giorni_rimasti, psi.priorita
    FROM prossime_scadenze_impianti psi
    WHERE 1=1 {filtro_div}
"""


def _scadenze_unificate(origine, priorita=''):
    """Le scadenze delle due origini, normalizzate sulle stesse colonne.

    Il filtro di divisione si applica separatamente ai due rami: le viste hanno
    alias diversi (ps, psi) e filtro_divisione() nomina l'alias nella clausola.
    """
    from models import filtro_divisione

    rami, parametri = [], []
    if origine in ('tutto', 'apparecchi'):
        clausola, valori = filtro_divisione('ps')
        rami.append(_SCADENZE_APPARECCHI.format(filtro_div=clausola))
        parametri.extend(valori)
    if origine in ('tutto', 'impianti'):
        clausola, valori = filtro_divisione('psi')
        rami.append(_SCADENZE_IMPIANTI.format(filtro_div=clausola))
        parametri.extend(valori)
    if not rami:
        return []

    sql = " UNION ALL ".join(rami)
    if priorita:
        sql = (f"SELECT * FROM ({sql}) WHERE priorita = ?")
        parametri.append(priorita)
    sql += " ORDER BY prossima_scadenza ASC"
    return query_all(sql, parametri)
```

Poi, dentro `scadenzario()`, sostituire la query delle scadenze con:

```python
    origine = request.args.get('origine', 'tutto')
    if origine not in ('tutto', 'apparecchi', 'impianti'):
        origine = 'tutto'
    priorita = request.args.get('priorita', '')
    scadenze = _scadenze_unificate(origine, priorita)
```

e aggiungere `origine` al dizionario `filtri` del contesto. Le aggregazioni
`summary` e `tipo_summary` si calcolano dalla lista già ottenuta, così coprono
entrambe le origini senza una seconda query:

```python
    from collections import Counter
    summary = Counter(s['priorita'] for s in scadenze)
    tipo_summary = Counter(s['tipo'] for s in scadenze)
```

I template `templates/manutenzioni/scadenzario.html` e
`templates/partials/scadenze_table.html` prendono una colonna "Oggetto"
(`{{ s.oggetto }}` con `{{ s.dettaglio }}` sotto, link a
`impianti.dettaglio` o `apparecchi.dettaglio` secondo `s.origine`) e il selettore
`origine` fra i filtri.

- [ ] **Step 4: Sommare le due origini nella dashboard e nel badge**

In `app.py`, il contatore "Stat 2" (475-495) diventa, in ognuno dei quattro rami,
la somma dei due conteggi. Il ramo per divisione:

```python
        priorita_attive = ('scaduto', 'urgente', 'attenzione', 'avviso')
        if div and div.get('id') != 'tutte':
            r = query_one(
                """SELECT (SELECT COUNT(*) FROM prossime_scadenze
                            WHERE divisione_id = ?
                              AND priorita IN ('scaduto','urgente','attenzione','avviso'))
                        + (SELECT COUNT(*) FROM prossime_scadenze_impianti
                            WHERE divisione_id = ?
                              AND priorita IN ('scaduto','urgente','attenzione','avviso'))
                        AS cnt""",
                [div['id'], div['id']]
            )
```

Il ramo admin/tecnico con struttura (la vista impianti porta già
`struttura_id`, quindi non serve il JOIN):

```python
                r = query_one(
                    """SELECT (SELECT COUNT(*) FROM prossime_scadenze ps
                                JOIN apparecchi a ON a.id = ps.apparecchio_id
                                WHERE a.struttura_id = ?
                                  AND ps.priorita IN ('scaduto','urgente','attenzione','avviso'))
                            + (SELECT COUNT(*) FROM prossime_scadenze_impianti
                                WHERE struttura_id = ?
                                  AND priorita IN ('scaduto','urgente','attenzione','avviso'))
                            AS cnt""",
                    [struttura_id, struttura_id]
                )
```

Il ramo senza struttura somma i due `COUNT(*)` senza filtro; il ramo per elenco
di divisioni ripete il segnaposto `IN ({ph})` su entrambe le sottoquery,
passando `ids + ids`.

La lista "Upcoming deadlines" (526-545) usa `_scadenze_unificate('tutto')`,
importata da `manutenzioni`, e prende i primi 10 elementi; il template
`dashboard.html` legge `oggetto`/`dettaglio`/`origine` invece di
`marca`/`modello`/`matricola`.

In `auth.py` (341-361) la stessa somma, con le sole priorità
`('scaduto','urgente','attenzione')` già usate lì. Il ramo costruito in stringa
alla riga 361 diventa:

```python
            sql = ("SELECT (SELECT COUNT(*) FROM prossime_scadenze"
                   " WHERE divisione_id IN (" + ph + ")"
                   " AND priorita IN ('scaduto','urgente','attenzione'))"
                   " + (SELECT COUNT(*) FROM prossime_scadenze_impianti"
                   " WHERE divisione_id IN (" + ph + ")"
                   " AND priorita IN ('scaduto','urgente','attenzione')) AS cnt")
            result = query_one(sql, tuple(ids) + tuple(ids))
```

- [ ] **Step 5: Eseguire i test e vederli passare**

Run: `python -m pytest tests/test_impianti.py -q`
Expected: PASS (35 test).

Run: `python -m pytest tests/ -q`
Expected: PASS — in particolare i test dello scadenzario esistenti.

- [ ] **Step 6: Commit**

```bash
git add manutenzioni.py app.py auth.py templates/manutenzioni/scadenzario.html templates/partials/scadenze_table.html templates/dashboard.html tests/test_impianti.py
git commit -m "feat(impianti): scadenzario unificato, contatori dashboard e badge"
```

---

## Task 12: Libretto impianto PDF

**Files:**
- Modify: `export_service.py` (nuova funzione), `impianti.py` (rotta),
  `templates/impianti/dettaglio.html` (pulsante)
- Test: `tests/test_impianti.py`

**Interfaces:**
- Consumes: `fpdf.FPDF` (stessa classe locale usata dagli altri report)
- Produces: `export_service.genera_libretto_impianto(impianto_id, output_path)
  -> str` (il percorso scritto); endpoint `impianti.libretto`

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# tests/test_impianti.py — in coda
def test_libretto_pdf_generato(app, ambiente, tmp_path):
    from export_service import genera_libretto_impianto
    with app.app_context():
        impianto, scad = _impianto_con_piano(ambiente, scadenza='2027-01-10')
        execute("INSERT INTO impianti_componenti (impianto_id, descrizione)"
                " VALUES (?, 'Quadro generale')", (impianto,))
        execute("INSERT INTO impianti_interventi (impianto_id, tipo,"
                " data_intervento, esito) VALUES (?, 'verifica', '2025-01-10',"
                " 'positivo')", (impianto,))
        percorso = str(tmp_path / 'libretto.pdf')
        genera_libretto_impianto(impianto, percorso)
    import os
    assert os.path.exists(percorso) and os.path.getsize(percorso) > 500
    with open(percorso, 'rb') as f:
        assert f.read(4) == b'%PDF'


def test_libretto_di_altra_struttura_non_scaricabile(client, app, ambiente):
    with app.app_context():
        impianto_b = _crea_impianto(ambiente, 'b', 'Cabina B libretto')
    entra(client, ambiente['a']['email'])
    assert client.get(f'/impianti/{impianto_b}/libretto.pdf').status_code in (302, 404)
```

- [ ] **Step 2: Eseguire i test e vederli fallire**

Run: `python -m pytest tests/test_impianti.py -q -k libretto`
Expected: FAIL — `ImportError: cannot import name 'genera_libretto_impianto'`.

- [ ] **Step 3: Implementare la generazione**

In coda a `export_service.py`:

```python
def genera_libretto_impianto(impianto_id, output_path):
    """Il libretto dell'impianto: anagrafica, componenti, documenti, piano,
    storico interventi.

    Dei documenti riporta i metadati, non i file: e' un indice di cosa esiste e
    chi l'ha emesso, non un archivio da stampare.
    """
    from fpdf import FPDF
    from models import query_one, query_all

    impianto = query_one(
        """SELECT i.*, d.nome as divisione_nome, s.nome as struttura_nome,
                  m.ragione_sociale as manutentore_nome
           FROM impianti i
           LEFT JOIN divisioni d ON d.id = i.divisione_id
           LEFT JOIN strutture s ON s.id = i.struttura_id
           LEFT JOIN manutentori m ON m.id = i.manutentore_id
           WHERE i.id = ?""", (impianto_id,))
    if not impianto:
        raise ValueError(f"Impianto {impianto_id} inesistente")

    componenti = query_all(
        "SELECT * FROM impianti_componenti WHERE impianto_id = ?"
        " ORDER BY descrizione", (impianto_id,))
    documenti = query_all(
        "SELECT * FROM impianti_documenti WHERE impianto_id = ?"
        " ORDER BY data_documento DESC, id DESC", (impianto_id,))
    piano = query_all(
        "SELECT * FROM impianti_scadenze WHERE impianto_id = ?"
        " ORDER BY attiva DESC, prossima_scadenza", (impianto_id,))
    interventi = query_all(
        """SELECT i.*, m.ragione_sociale as manutentore_nome
           FROM impianti_interventi i
           LEFT JOIN manutentori m ON m.id = i.manutentore_id
           WHERE i.impianto_id = ? ORDER BY i.data_intervento DESC""",
        (impianto_id,))

    class PDF(FPDF):
        def header(self):
            self.set_font('Helvetica', 'B', 14)
            self.cell(0, 8, _t(f"Libretto impianto — {impianto['nome']}"),
                      new_x='LMARGIN', new_y='NEXT')
            self.set_font('Helvetica', '', 9)
            self.cell(0, 5, _t(f"{impianto['struttura_nome'] or ''} — "
                               f"{impianto['divisione_nome'] or ''}"),
                      new_x='LMARGIN', new_y='NEXT')
            self.ln(2)

        def footer(self):
            self.set_y(-12)
            self.set_font('Helvetica', 'I', 8)
            self.cell(0, 8, _t(f"Pagina {self.page_no()}"), align='C')

    def _t(testo):
        """fpdf2 con i font core non digerisce tutto l'UTF-8."""
        return str(testo or '').encode('latin-1', 'replace').decode('latin-1')

    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def titolo(testo):
        pdf.ln(3)
        pdf.set_font('Helvetica', 'B', 11)
        pdf.cell(0, 7, _t(testo), new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('Helvetica', '', 9)

    def riga(etichetta, valore):
        pdf.cell(45, 5, _t(etichetta), border=0)
        pdf.multi_cell(0, 5, _t(valore if valore not in (None, '') else '-'))

    titolo('Anagrafica')
    riga('Tipo', impianto['tipo_custom'] or impianto['tipo'])
    riga('Stato', impianto['stato'])
    riga('Ubicazione', impianto['ubicazione'])
    riga('Identificativo', impianto['identificativo'])
    riga('Anno installazione', impianto['anno_installazione'])
    riga('Manutentore', impianto['manutentore_nome'])
    riga('Descrizione', impianto['descrizione'])

    titolo('Componenti')
    if componenti:
        for c in componenti:
            pdf.multi_cell(0, 5, _t(
                f"- {c['descrizione']} "
                f"({c['marca'] or '-'} {c['modello'] or ''} "
                f"mat. {c['matricola'] or '-'})"))
    else:
        pdf.cell(0, 5, _t('Nessun componente censito.'),
                 new_x='LMARGIN', new_y='NEXT')

    titolo('Documentazione')
    if documenti:
        for d in documenti:
            pdf.multi_cell(0, 5, _t(
                f"- [{d['tipo']}] {d['descrizione'] or d['filename']} — "
                f"{d['data_documento'] or 's.d.'} — "
                f"{d['emittente_ragione_sociale'] or 'emittente non indicato'}"))
    else:
        pdf.cell(0, 5, _t('Nessun documento caricato.'),
                 new_x='LMARGIN', new_y='NEXT')

    titolo('Piano di manutenzione e verifica')
    if piano:
        for p in piano:
            periodo = (f"ogni {p['periodicita_mesi']} mesi"
                       if p['periodicita_mesi'] else 'una tantum')
            stato = '' if p['attiva'] else ' [sospesa]'
            pdf.multi_cell(0, 5, _t(
                f"- {p['nome']} ({periodo}) — prossima: "
                f"{p['prossima_scadenza']} — "
                f"{p['riferimento_normativo'] or 'nessun riferimento'}{stato}"))
    else:
        pdf.cell(0, 5, _t('Piano non ancora definito.'),
                 new_x='LMARGIN', new_y='NEXT')

    titolo('Storico interventi')
    if interventi:
        for i in interventi:
            pdf.multi_cell(0, 5, _t(
                f"- {i['data_intervento']} [{i['tipo']}] "
                f"{i['esito'] or 'esito non indicato'} — "
                f"{i['manutentore_nome'] or i['tecnico_ditta'] or '-'} — "
                f"{i['descrizione'] or ''}"))
    else:
        pdf.cell(0, 5, _t('Nessun intervento registrato.'),
                 new_x='LMARGIN', new_y='NEXT')

    pdf.output(output_path)
    return output_path
```

- [ ] **Step 4: Aggiungere la rotta**

In coda a `impianti.py`:

```python
@impianti_bp.route('/<int:impianto_id>/libretto.pdf')
@login_required
def libretto(impianto_id):
    """Scarica il libretto dell'impianto in PDF."""
    impianto = impianto_accessibile(impianto_id)
    if not impianto:
        flash('Impianto non trovato.', 'danger')
        return redirect(url_for('impianti.lista'))

    import tempfile
    from export_service import genera_libretto_impianto

    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        percorso = tmp.name
    genera_libretto_impianto(impianto_id, percorso)
    log_attivita('esportazione', 'impianto', impianto_id,
                 f"Libretto di {impianto['nome']}")
    nome = secure_filename(f"libretto_{impianto['nome']}.pdf")
    return send_file(percorso, as_attachment=True, download_name=nome)
```

E nel dettaglio, il pulsante:

```html
<a class="btn btn-outline-secondary"
   href="{{ url_for('impianti.libretto', impianto_id=impianto.id) }}">
  <i class="bi bi-file-earmark-pdf"></i> Libretto PDF
</a>
```

- [ ] **Step 5: Eseguire i test e vederli passare**

Run: `python -m pytest tests/test_impianti.py -q`
Expected: PASS (37 test).

- [ ] **Step 6: Commit**

```bash
git add export_service.py impianti.py templates/impianti/dettaglio.html tests/test_impianti.py
git commit -m "feat(impianti): libretto impianto in PDF"
```

---

## Task 13: Avvisi email di scadenza

**Files:**
- Modify: `impianti_service.py` (selezione avvisi e destinatari),
  `scheduler.py:37-71` e in coda alla classe, `scheduler.py:258-306`
  (`_invia_digest`), `export_service.py:474-530`
  (`genera_report_scadenze_pdf`), `templates/strutture/` (interruttore
  `avvisi_impianti_attivi`)
- Test: `tests/test_impianti.py`

**Interfaces:**
- Consumes: `models.get_struttura_config()`, `posta.invia()`, `models.query_all()`
- Produces:
  - `impianti_service.SOGLIE` — ordine di gravità crescente
  - `impianti_service.avvisi_da_inviare(struttura_id) -> list[dict]` con chiavi
    `scadenza_id, impianto_id, impianto_nome, divisione_id, divisione_nome,
    scadenza_nome, riferimento_normativo, prossima_scadenza, giorni_rimasti,
    giorni_anticipo, email_extra, avvisa_manutentore, manutentore_email,
    ultimo_intervento, soglia`
  - `impianti_service.destinatari(struttura, avviso) -> list[str]`
  - `impianti_service.corpo_avviso(struttura, avviso) -> tuple[str, str]`
    (oggetto, testo)
  - `impianti_service.registra_avviso(scadenza_id, soglia, scadenza_target,
    destinatari) -> None`
  - `scheduler.BackgroundScheduler._send_impianti_alerts()`
  - chiave di configurazione per struttura `avvisi_impianti_attivi` (default `'1'`)

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# tests/test_impianti.py — in coda
def _scadenza_fra(ambiente, giorni, anticipo=30, extra=None, manutentore=None):
    a = ambiente['a']
    impianto = execute(
        "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo,"
        " manutentore_id) VALUES (?, ?, ?, 'elettrico', ?)",
        (a['struttura'], a['divisione'], f'Cabina {giorni}', manutentore))
    return execute(
        "INSERT INTO impianti_scadenze (impianto_id, nome, periodicita_mesi,"
        " prossima_scadenza, giorni_anticipo, email_extra)"
        " VALUES (?, 'Verifica di terra', 24, date('now', ?), ?, ?)",
        (impianto, f'{giorni} days', anticipo, extra))


def test_soglie_avvisi(app, ambiente):
    """Solo la soglia più grave raggiunta, e mai prima dell'anticipo."""
    from impianti_service import avvisi_da_inviare
    with app.app_context():
        sid = ambiente['a']['struttura']
        lontana = _scadenza_fra(ambiente, 60)
        anticipo = _scadenza_fra(ambiente, 20)
        imminente = _scadenza_fra(ambiente, 3)
        scaduta = _scadenza_fra(ambiente, -45)

        per_id = {a['scadenza_id']: a for a in avvisi_da_inviare(sid)}
        assert lontana not in per_id
        assert per_id[anticipo]['soglia'] == 'anticipo'
        assert per_id[imminente]['soglia'] == 'imminente'
        assert per_id[scaduta]['soglia'] == 'sollecito_1'


def test_avviso_non_si_ripete(app, ambiente):
    from impianti_service import avvisi_da_inviare, registra_avviso
    with app.app_context():
        sid = ambiente['a']['struttura']
        scad = _scadenza_fra(ambiente, 20)
        avviso = [a for a in avvisi_da_inviare(sid)
                  if a['scadenza_id'] == scad][0]
        registra_avviso(scad, avviso['soglia'], avviso['prossima_scadenza'],
                        ['x@test.it'])
        assert [a for a in avvisi_da_inviare(sid)
                if a['scadenza_id'] == scad] == []


def test_avviso_riparte_dopo_lo_spostamento_della_scadenza(app, ambiente):
    """scadenza_target sta nella chiave: il ciclo successivo avvisa di nuovo."""
    from impianti_service import (avvisi_da_inviare, registra_avviso,
                                  registra_intervento)
    with app.app_context():
        sid = ambiente['a']['struttura']
        scad = _scadenza_fra(ambiente, 20)
        riga = query_one("SELECT * FROM impianti_scadenze WHERE id = ?", (scad,))
        registra_avviso(scad, 'anticipo', riga['prossima_scadenza'], ['x@test.it'])
        # Verifica eseguita: la scadenza si sposta di 24 mesi, poi la si
        # riporta indietro per simulare il ciclo successivo.
        registra_intervento(riga['impianto_id'], {
            'scadenza_id': scad, 'tipo': 'verifica',
            'data_intervento': '2026-01-01', 'esito': 'positivo'})
        execute("UPDATE impianti_scadenze SET prossima_scadenza ="
                " date('now', '+20 days') WHERE id = ?", (scad,))
        assert [a for a in avvisi_da_inviare(sid)
                if a['scadenza_id'] == scad] != []


def test_destinatari_in_cascata(app, ambiente):
    from impianti_service import avvisi_da_inviare, destinatari
    with app.app_context():
        a = ambiente['a']
        mid = execute("INSERT INTO manutentori (struttura_id, ragione_sociale,"
                      " email) VALUES (?, 'Ditta', 'ditta@test.it')",
                      (a['struttura'],))
        scad = _scadenza_fra(ambiente, 10, extra='perito@test.it, ,perito@test.it',
                             manutentore=mid)
        struttura = query_one("SELECT * FROM strutture WHERE id = ?",
                              (a['struttura'],))
        avviso = [x for x in avvisi_da_inviare(a['struttura'])
                  if x['scadenza_id'] == scad][0]
        elenco = destinatari(struttura, avviso)
        assert elenco.count('perito@test.it') == 1
        assert 'responsabile.a@test.it' in elenco
        assert 'divisione.a@test.it' in elenco
        assert 'ditta@test.it' in elenco
        assert '' not in elenco


def test_avviso_senza_destinatari_non_e_un_errore(app, ambiente):
    from impianti_service import avvisi_da_inviare, destinatari
    with app.app_context():
        b = ambiente['b']
        execute("UPDATE strutture SET email_responsabile = NULL,"
                " email_notifiche = NULL WHERE id = ?", (b['struttura'],))
        execute("UPDATE divisioni SET email = NULL WHERE id = ?",
                (b['divisione'],))
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Cabina muta', 'elettrico')",
            (b['struttura'], b['divisione']))
        execute("INSERT INTO impianti_scadenze (impianto_id, nome,"
                " periodicita_mesi, prossima_scadenza)"
                " VALUES (?, 'Verifica', 24, date('now', '+10 days'))",
                (impianto,))
        struttura = query_one("SELECT * FROM strutture WHERE id = ?",
                              (b['struttura'],))
        avviso = avvisi_da_inviare(b['struttura'])[0]
        assert destinatari(struttura, avviso) == []
```

- [ ] **Step 2: Eseguire i test e vederli fallire**

Run: `python -m pytest tests/test_impianti.py -q -k "soglie or avviso or destinatari"`
Expected: FAIL — `ImportError: cannot import name 'avvisi_da_inviare'`.

- [ ] **Step 3: Implementare la selezione e i destinatari**

In coda a `impianti_service.py`:

```python
#: Soglie in ordine di gravita' crescente. Sono cumulative: una scadenza a 3
#: giorni ha superato sia 'anticipo' sia 'imminente'. Si invia solo la piu'
#: grave non ancora registrata, ma si registrano tutte quelle raggiunte —
#: altrimenti la soglia saltata partirebbe al giro dopo, fuori tempo massimo.
SOGLIE = ('anticipo', 'imminente', 'scaduto')


def _soglie_raggiunte(giorni_rimasti, giorni_anticipo):
    """Le soglie superate da una scadenza, dalla piu' lieve alla piu' grave.

    'scaduto' scatta a giorni_rimasti <= 0, mentre la vista classifica
    'scaduto' con < 0: il giorno stesso della scadenza la vista dice ancora
    'urgente', ma un avviso che parte il giorno dopo arriva tardi. La
    divergenza e' voluta.
    """
    raggiunte = []
    if giorni_rimasti <= giorni_anticipo:
        raggiunte.append('anticipo')
    if giorni_rimasti <= 7:
        raggiunte.append('imminente')
    if giorni_rimasti <= 0:
        raggiunte.append('scaduto')
        # Solleciti mensili finche' la verifica non viene registrata.
        mesi = int(-giorni_rimasti) // 30
        if mesi >= 1:
            raggiunte.append(f'sollecito_{mesi}')
    return raggiunte


def avvisi_da_inviare(struttura_id):
    """Le scadenze della struttura che hanno un avviso da spedire.

    Un elemento per scadenza, con la soglia piu' grave non ancora registrata in
    impianti_avvisi_inviati. Le scadenze sospese e gli impianti dismessi sono
    gia' esclusi dalla vista.
    """
    righe = query_all(
        """SELECT v.*, d.nome as divisione_nome, d.email as divisione_email,
                  s.email_extra, s.avvisa_manutentore,
                  m.email as manutentore_email,
                  (SELECT MAX(i.data_intervento) FROM impianti_interventi i
                    WHERE i.scadenza_id = v.scadenza_id) as ultimo_intervento
           FROM prossime_scadenze_impianti v
           JOIN impianti_scadenze s ON s.id = v.scadenza_id
           JOIN impianti imp ON imp.id = v.impianto_id
           LEFT JOIN divisioni d ON d.id = v.divisione_id
           LEFT JOIN manutentori m ON m.id = imp.manutentore_id
           WHERE v.struttura_id = ?""",
        (struttura_id,)
    )

    avvisi = []
    for r in righe:
        raggiunte = _soglie_raggiunte(r['giorni_rimasti'], r['giorni_anticipo'])
        if not raggiunte:
            continue
        gia_inviate = {x['soglia'] for x in query_all(
            "SELECT soglia FROM impianti_avvisi_inviati"
            " WHERE scadenza_id = ? AND scadenza_target = ?",
            (r['scadenza_id'], r['prossima_scadenza']))}
        da_fare = [s for s in raggiunte if s not in gia_inviate]
        if not da_fare:
            continue
        avviso = dict(r)
        avviso['soglia'] = da_fare[-1]          # la piu' grave
        avviso['soglie_coperte'] = da_fare      # tutte quelle da registrare
        avvisi.append(avviso)
    return avvisi


def destinatari(struttura, avviso):
    """Gli indirizzi a cui spedire l'avviso, in cascata e senza doppioni.

    1) responsabile della struttura (o, in mancanza, l'indirizzo di notifica)
    2) email della divisione
    3) indirizzi extra della riga di piano (elenco separato da virgole)
    4) manutentore dell'impianto, se la riga lo prevede
    """
    elenco = []
    responsabile = (struttura['email_responsabile']
                    or struttura['email_notifiche'])
    for candidato in (responsabile, avviso.get('divisione_email')):
        if candidato:
            elenco.append(candidato)
    for pezzo in (avviso.get('email_extra') or '').split(','):
        if pezzo.strip():
            elenco.append(pezzo.strip())
    if avviso.get('avvisa_manutentore') and avviso.get('manutentore_email'):
        elenco.append(avviso['manutentore_email'])

    visti, puliti = set(), []
    for indirizzo in elenco:
        chiave = indirizzo.strip().lower()
        if chiave and chiave not in visti:
            visti.add(chiave)
            puliti.append(indirizzo.strip())
    return puliti


ETICHETTE_SOGLIA = {
    'anticipo': 'in scadenza',
    'imminente': 'in scadenza imminente',
    'scaduto': 'SCADUTA',
}


def corpo_avviso(struttura, avviso):
    """Oggetto e testo dell'avviso. L'oggetto nomina la struttura: il mittente
    e' lo stesso per tutte le strutture del deployment."""
    soglia = avviso['soglia']
    etichetta = (ETICHETTE_SOGLIA.get(soglia)
                 or f"SCADUTA — sollecito n. {soglia.rsplit('_', 1)[-1]}")
    oggetto = (f"[{struttura['nome']}] {avviso['impianto_nome']}: "
               f"{avviso['scadenza_nome']} {etichetta}")

    giorni = avviso['giorni_rimasti']
    quando = (f"mancano {giorni} giorni" if giorni > 0
              else f"scaduta da {-giorni} giorni" if giorni < 0
              else 'scade oggi')
    righe = [
        f"Struttura: {struttura['nome']}",
        f"Divisione: {avviso.get('divisione_nome') or '-'}",
        f"Impianto: {avviso['impianto_nome']}"
        + (f" ({avviso['ubicazione']})" if avviso.get('ubicazione') else ''),
        f"Verifica: {avviso['scadenza_nome']}",
        f"Riferimento: {avviso.get('riferimento_normativo') or '-'}",
        f"Scadenza: {avviso['prossima_scadenza']} ({quando})",
        f"Ultimo intervento registrato: {avviso.get('ultimo_intervento') or 'nessuno'}",
        "",
        "Messaggio automatico di MedInventory.",
    ]
    return oggetto, "\n".join(righe)


def registra_avviso(scadenza_id, soglia, scadenza_target, indirizzi):
    """Segna una soglia come inviata. Scritta solo dopo un invio riuscito:
    una riga scritta in anticipo trasformerebbe un errore SMTP in un avviso
    perso per sempre."""
    execute(
        """INSERT OR IGNORE INTO impianti_avvisi_inviati
           (scadenza_id, soglia, scadenza_target, destinatari)
           VALUES (?, ?, ?, ?)""",
        (scadenza_id, soglia, scadenza_target, ', '.join(indirizzi))
    )
```

- [ ] **Step 4: Eseguire i test del servizio e vederli passare**

Run: `python -m pytest tests/test_impianti.py -q -k "soglie or avviso or destinatari"`
Expected: PASS.

- [ ] **Step 5: Aggiungere il task allo scheduler**

In `scheduler.py`, nella lista `_tasks` (dopo `deadline_alerts`):

```python
            {
                'name': 'impianti_alerts',
                # Ogni ora, senza finestra oraria fissa: la tabella
                # impianti_avvisi_inviati impedisce i doppioni, quindi ogni ora
                # successiva e' un tentativo ripetuto gratis se l'SMTP era giu'.
                'func': self._send_impianti_alerts,
                'interval': 3600,
                'last_run': 0,
            },
```

E il metodo, dopo `_send_deadline_alerts()`:

```python
    def _send_impianti_alerts(self):
        """Avvisi di scadenza degli impianti, una email per voce di piano.

        Non c'e' digest: ogni verifica ha destinatari propri (il manutentore
        della riga, l'indirizzo extra del perito) e un messaggio unico non
        potrebbe rispettarli.
        """
        with self.app.app_context():
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            from models import query_all, get_struttura_config
            from posta import invia
            import impianti_service

            if datetime.now().hour < 7:
                return
            if not self._config_smtp()['host']:
                logger.warning("SMTP di sistema non configurato: avvisi "
                               "impianti non inviati.")
                return

            strutture = query_all("SELECT * FROM strutture WHERE attiva = 1")
            for struttura in strutture:
                sid = struttura['id']
                if get_struttura_config(sid, 'avvisi_impianti_attivi', '1') != '1':
                    continue
                try:
                    for avviso in impianti_service.avvisi_da_inviare(sid):
                        indirizzi = impianti_service.destinatari(struttura, avviso)
                        if not indirizzi:
                            # Configurazione incompleta, non un guasto: la
                            # struttura non ha indicato nessun destinatario.
                            logger.info(
                                f"Nessun destinatario per la scadenza "
                                f"{avviso['scadenza_id']} ({struttura['nome']})")
                            continue
                        oggetto, testo = impianti_service.corpo_avviso(
                            struttura, avviso)
                        msg = MIMEMultipart()
                        msg['Subject'] = oggetto
                        msg.attach(MIMEText(testo, 'plain', 'utf-8'))
                        if invia(self.app.config.get('APP_CONFIG'),
                                 ', '.join(indirizzi), msg):
                            for soglia in avviso['soglie_coperte']:
                                impianti_service.registra_avviso(
                                    avviso['scadenza_id'], soglia,
                                    avviso['prossima_scadenza'], indirizzi)
                            logger.info(f"Avviso impianto inviato a "
                                        f"{indirizzi} ({struttura['nome']})")
                        else:
                            logger.error(
                                f"Avviso impianto non partito per la scadenza "
                                f"{avviso['scadenza_id']} ({struttura['nome']})")
                except Exception as e:
                    logger.error(f"Errore avvisi impianti struttura "
                                 f"{struttura['nome']}: {e}")
```

- [ ] **Step 6: Aggiungere la sezione IMPIANTI a digest e report PDF**

In `scheduler.py._invia_digest()`, dopo il ciclo sulle priorità degli
apparecchi e prima della composizione del messaggio:

```python
        if get_struttura_config(struttura['id'], 'avvisi_impianti_attivi',
                                '1') == '1':
            impianti = query_all("""
                SELECT v.*, d.nome as divisione_nome
                FROM prossime_scadenze_impianti v
                LEFT JOIN divisioni d ON d.id = v.divisione_id
                WHERE v.struttura_id = ?
                  AND v.priorita IN ('scaduto', 'urgente', 'attenzione', 'avviso')
                ORDER BY v.prossima_scadenza
            """, (struttura['id'],))
            if impianti:
                righe.append("\nIMPIANTI")
                righe.append("-" * 30)
                for i in impianti:
                    righe.append(
                        f"  {i['impianto_nome']} — {i['scadenza_nome']} — "
                        f"{i['divisione_nome'] or '-'} — scade: "
                        f"{i['prossima_scadenza']} ({i['giorni_rimasti']} gg)")
```

`get_struttura_config` va aggiunta all'import locale della funzione. Il
`return` anticipato quando `scadenze` è vuoto diventa condizionato anche agli
impianti: la struttura può non avere apparecchi in scadenza ma avere impianti.

In `export_service.genera_report_scadenze_pdf()`, dopo la tabella degli
apparecchi, una sezione analoga che legge `prossime_scadenze_impianti` filtrata
per `struttura_id` e stampata solo se non vuota.

- [ ] **Step 7: Esporre l'interruttore in configurazione**

Nel template della configurazione per struttura (dove compare
`avvisi_scadenza_attivi`), aggiungere la checkbox
`name="avvisi_impianti_attivi"` e, nel handler POST di `strutture_bp` che salva
quelle preferenze, la riga corrispondente:

```python
    set_struttura_config(struttura_id, 'avvisi_impianti_attivi',
                         '1' if request.form.get('avvisi_impianti_attivi') else '0')
```

- [ ] **Step 8: Eseguire i test e vederli passare**

Run: `python -m pytest tests/test_impianti.py -q`
Expected: PASS (43 test).

Run: `python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add impianti_service.py scheduler.py export_service.py strutture_bp.py templates/strutture tests/test_impianti.py
git commit -m "feat(impianti): avvisi email di scadenza con soglie e anti-duplicato"
```

---

## Task 14: Perimetro struttura, import, versione 2.7.0 e documentazione

**Files:**
- Modify: `struttura_service.py:47-59` e `struttura_service.py:272-340`,
  `importa_installazione.py:51-70` e le costanti `COLONNE_FILE`,
  `config.example.json`, `config.json`, `app.py` (stringa di versione),
  `tests/test_manutenzione.py`, `CLAUDE.md`
- Test: `tests/test_impianti.py`

**Interfaces:**
- Consumes: `struttura_service.COLONNE_ALLEGATI`,
  `importa_installazione.COLONNE_FILE`
- Produces: nessuna nuova API; le nuove entità entrano nel perimetro degli
  allegati, nel conteggio di `contenuto_struttura()` e nel round-trip di import

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# tests/test_impianti.py — in coda
def test_perimetro_allegati_include_gli_impianti():
    """Un allegato di impianto deve stare nel perimetro della struttura."""
    from struttura_service import COLONNE_ALLEGATI
    coppie = {(t, c) for t, c in COLONNE_ALLEGATI}
    assert ('impianti_documenti', 'filepath') in coppie
    assert ('impianti_interventi', 'verbale_path') in coppie


def test_contenuto_struttura_conta_gli_impianti(app, ambiente):
    from struttura_service import contenuto_struttura
    with app.app_context():
        _crea_impianto(ambiente, nome='Cabina conteggio')
        contenuto = contenuto_struttura(ambiente['a']['struttura'])
        assert contenuto['conteggi']['impianti'] == 1


def test_versione_allineata():
    import json
    with open('config.example.json', encoding='utf-8') as f:
        assert json.load(f)['version'] == '2.7.0'
```

- [ ] **Step 2: Eseguire i test e vederli fallire**

Run: `python -m pytest tests/test_impianti.py -q -k "perimetro or contenuto or versione"`
Expected: FAIL — la tupla non è in `COLONNE_ALLEGATI`, `KeyError: 'impianti'`,
`assert '2.6.4' == '2.7.0'`.

- [ ] **Step 3: Estendere il perimetro della struttura**

In `struttura_service.py`, dentro `COLONNE_ALLEGATI` (47-59), aggiungere:

```python
    ('impianti_documenti', 'filepath'),
    ('impianti_interventi', 'verbale_path'),
```

E in `contenuto_struttura()` (272-340) i conteggi delle nuove entità, con lo
stesso stile delle voci esistenti — le figlie passano da un JOIN su `impianti`,
che è l'unica a portare `struttura_id`:

```python
    conteggi['impianti'] = query_one(
        "SELECT COUNT(*) as cnt FROM impianti WHERE struttura_id = ?",
        (struttura_id,))['cnt']
    conteggi['impianti_documenti'] = query_one(
        "SELECT COUNT(*) as cnt FROM impianti_documenti d"
        " JOIN impianti i ON i.id = d.impianto_id WHERE i.struttura_id = ?",
        (struttura_id,))['cnt']
    conteggi['impianti_interventi'] = query_one(
        "SELECT COUNT(*) as cnt FROM impianti_interventi t"
        " JOIN impianti i ON i.id = t.impianto_id WHERE i.struttura_id = ?",
        (struttura_id,))['cnt']
    conteggi['manutentori'] = query_one(
        "SELECT COUNT(*) as cnt FROM manutentori WHERE struttura_id = ?",
        (struttura_id,))['cnt']
```

La funzione di eliminazione della struttura non richiede modifiche: `impianti`
e `manutentori` hanno `ON DELETE CASCADE` su `strutture`, e le figlie cascatano
da `impianti`. I file su disco stanno già sotto
`uploads/strutture/<id>/impianti/`, dentro `cartella_struttura()`.

- [ ] **Step 4: Estendere l'importatore**

In `importa_installazione.py`, aggiungere alle chiavi naturali le nuove entità
(stesso stile delle esistenti):

- `manutentori` → `(struttura_id, ragione_sociale)`
- `impianti` → `(struttura_id, nome)`
- `impianti_componenti` → `(impianto_id, descrizione, matricola)`
- `impianti_documenti` → `(impianto_id, filename, data_documento)`
- `impianti_scadenze` → `(impianto_id, nome, prossima_scadenza)`
- `impianti_interventi` → `(impianto_id, data_intervento, tipo, scadenza_id)`

`impianti_avvisi_inviati` **non** si importa: come `sessioni` e
`login_attempts`, appartiene al deployment di origine — reimportarla farebbe
tacere avvisi che qui non sono mai partiti.

In `COLONNE_FILE` aggiungere:

```python
    ('impianti_documenti', 'filepath', 'impianti'),
    ('impianti_interventi', 'verbale_path', 'impianti'),
```

Le nuove tabelle non esistono nelle installazioni di origine più vecchie: il
lettore per introspezione le salta da solo se assenti, e non serve nessuna voce
in `RINOMINI`.

- [ ] **Step 5: Portare la versione a 2.7.0**

```bash
grep -rn "2\.6\.4" --include=*.py --include=*.json . | grep -v node_modules
```

Sostituire in `config.example.json`, `config.json`, `app.py` e
`tests/test_manutenzione.py`. `load_config()` riscrive `version` in
`config.json` al primo avvio, ma allinearlo evita che una installazione già
avviata resti indietro fino al riavvio.

- [ ] **Step 6: Aggiornare CLAUDE.md**

Portare l'intestazione a **MedInventory v2.7.0**; aggiungere `impianti.py` alla
tabella dei blueprint (prefisso `/impianti`, "Impianti delle divisioni:
anagrafica, componenti, documentazione, piano di manutenzione, interventi"),
`impianti_service.py` e `impianti_catalogo.py` alla tabella dei servizi, le
sette tabelle nuove all'elenco delle tabelle chiave, e una riga sulla vista
`prossime_scadenze_impianti` accanto a quella su `prossime_scadenze`. Nella
sezione "Key Conventions", estendere la riga sull'isolamento: le tabelle figlie
degli impianti non hanno `struttura_id`, si passa da `impianto_accessibile()` e
da un JOIN su `impianti`. Correggere il nome del filtro di divisione:
`models.filtro_divisione(table_alias)`, non `_get_divisione_filter()`.

- [ ] **Step 7: Eseguire l'intera suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (circa quattro minuti: il round-trip di
`importa_installazione.py` lancia lo script come sottoprocesso).

- [ ] **Step 8: Commit**

```bash
git add struttura_service.py importa_installazione.py config.example.json config.json app.py tests/ CLAUDE.md
git commit -m "feat(impianti): perimetro struttura, import e versione 2.7.0"
```

---

## Note per l'esecutore

- **Ordine:** i task 1-4 sono fondamenta e vanno in sequenza. I task 6-10
  dipendono solo dal 5 e possono essere riordinati. Il task 11 richiede il 5,
  il 13 richiede il 4 e il 8.
- **Nomi dei parametri di rotta:** ovunque `impianto_id`, `scadenza_id`,
  `componente_id`, `documento_id`, `intervento_id`, `manutentore_id`. Le
  chiamate `url_for` nei template li usano per nome.
- **Verifica manuale finale:** avviare `python app.py`, creare un impianto con
  due voci di catalogo, caricare un documento, registrare un intervento con
  esito positivo e controllare che la scadenza si sposti, aprire `/scadenzario`
  e verificare che il filtro `origine` funzioni, scaricare il libretto PDF.
