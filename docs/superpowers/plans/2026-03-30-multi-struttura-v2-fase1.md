# MedInventory v2 — Multi-Struttura Fase 1 — Piano di Implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trasformare MedInventory da single-tenant a multi-struttura mantenendo compatibilità v1.x tramite flag `single_struttura`.

**Architecture:** Un unico codebase Flask con SQLite condiviso. Ogni struttura è isolata tramite `struttura_id` in sessione e applicato come filtro su tutte le query. Il flag `single_struttura: true` in `config.local.json` preserva il comportamento v1.x invariato. La modalità `ingegneria_clinica` (vs `standard`) attiva le funzioni avanzate per struttura.

**Tech Stack:** Flask 3.x · SQLite3 (WAL) · HTMX · Bootstrap 5 · qrcode[pil] (nuova dipendenza)

**Spec di riferimento:** `docs/superpowers/specs/2026-03-30-multi-struttura-v2-fase1-design.md`

**Nota:** il progetto non ha un test suite automatico. Ogni task include passi di verifica manuale da eseguire con l'app in esecuzione (`python app.py`).

---

## Mappa dei file

### File nuovi
| File | Responsabilità |
|------|----------------|
| `migrate_v2_0.py` | Migration idempotente da v1.x a v2.0 |
| `strutture_bp.py` | CRUD strutture (superadmin) + config per-struttura |
| `api_bp.py` | REST API `/api/v1` autenticata con token Bearer |
| `templates/strutture/index.html` | Lista strutture |
| `templates/strutture/form.html` | Crea/modifica struttura |
| `templates/strutture/config.html` | Config AI e SMTP per struttura |
| `templates/strutture/api_tokens.html` | Gestione token API |
| `templates/admin/sicurezza.html` | Sblocco IP/utenti bloccati |
| `templates/partials/struttura_switcher.html` | Dropdown impersonation superadmin |

### File modificati
| File | Modifica principale |
|------|---------------------|
| `schema.sql` | Nuove tabelle, nuove colonne, UNIQUE aggiornato |
| `migrate_v2_0.py` | (nuovo — vedi sopra) |
| `auth.py` | Nuovi decoratori, sessione arricchita, rate limiting, logout globale, impersonation |
| `admin.py` | Scoping per struttura, `/admin/sicurezza` |
| `app.py` | Registrazione nuovi blueprint, `inject_globals()` aggiornato, versione → 2.0.0 |
| `models.py` | Helper `get_struttura_config()`, `set_struttura_config()`, `log_attivita` aggiornato con `struttura_id` |
| `scheduler.py` | Iterazione per struttura, digest email, report schedulati |
| `apparecchi.py` | Generazione QR code |
| `export_bp.py` | Report schedulati PDF/Excel |
| `ai_service.py` | Legge config AI da `strutture_config` con fallback globale |
| `email_monitor.py` | Scoping per struttura |
| `requirements.txt` | Aggiunge `qrcode[pil]` |
| `config.local.example.json` | Aggiunge campo `single_struttura` |
| `seed.py` | Aggiornato per nuovo schema |
| `templates/base.html` | Switcher struttura, breadcrumb superadmin, menu per modalità |
| `templates/apparecchi/detail.html` | Pulsante QR code |

---

## FASE A — Foundation (Tasks 1–6)

---

### Task 1: Aggiornamento schema.sql

**File:**
- Modifica: `schema.sql`

- [ ] **Step 1: Aggiungere le nuove tabelle in fondo a `schema.sql`** (prima della VIEW `prossime_scadenze`)

```sql
-- ============================================
-- STRUTTURE
-- ============================================
CREATE TABLE IF NOT EXISTS strutture (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  nome            TEXT NOT NULL,
  codice          TEXT UNIQUE NOT NULL,
  descrizione     TEXT,
  indirizzo       TEXT,
  email_notifiche TEXT,
  modalita        TEXT NOT NULL DEFAULT 'standard'
                  CHECK(modalita IN ('standard', 'ingegneria_clinica')),
  attiva          INTEGER DEFAULT 1,
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_strutture_codice ON strutture(codice);
CREATE INDEX IF NOT EXISTS idx_strutture_attiva ON strutture(attiva);

-- ============================================
-- STRUTTURE_CONFIG (configurazione per-struttura)
-- ============================================
CREATE TABLE IF NOT EXISTS strutture_config (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  struttura_id INTEGER NOT NULL,
  chiave       TEXT NOT NULL,
  valore       TEXT,
  UNIQUE(struttura_id, chiave),
  FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE
);
-- Chiavi valide: ai_provider, anthropic_api_key, ai_import_model,
-- ai_email_model, ai_local_base_url, ai_local_model,
-- smtp_host, smtp_port, smtp_user, smtp_password_encrypted, smtp_from,
-- smtp_use_tls, report_frequenza, report_schedulato_attivo

CREATE INDEX IF NOT EXISTS idx_strutture_config_struttura ON strutture_config(struttura_id);

-- ============================================
-- API_TOKENS
-- ============================================
CREATE TABLE IF NOT EXISTS api_tokens (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  struttura_id    INTEGER NOT NULL,
  nome            TEXT NOT NULL,
  token_hash      TEXT UNIQUE NOT NULL,
  scopes          TEXT DEFAULT 'read',
  ultimo_utilizzo DATETIME,
  scadenza        DATE,
  attivo          INTEGER DEFAULT 1,
  created_by      INTEGER,
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE,
  FOREIGN KEY (created_by) REFERENCES utenti(id)
);

CREATE INDEX IF NOT EXISTS idx_api_tokens_struttura ON api_tokens(struttura_id);
CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON api_tokens(token_hash);

-- ============================================
-- LOGIN_ATTEMPTS (rate limiting)
-- ============================================
CREATE TABLE IF NOT EXISTS login_attempts (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ip_address TEXT NOT NULL,
  email      TEXT,
  esito      TEXT NOT NULL CHECK(esito IN ('fallito', 'bloccato', 'riuscito')),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_login_attempts_ip    ON login_attempts(ip_address, created_at);
CREATE INDEX IF NOT EXISTS idx_login_attempts_email ON login_attempts(email, created_at);
```

- [ ] **Step 2: Aggiornare le tabelle esistenti** — aggiungere `struttura_id` ai commenti delle tabelle `divisioni`, `utenti`, `apparecchi`, `log_attivita`. Il campo verrà aggiunto fisicamente da `migrate_v2_0.py`; lo schema.sql riflette lo stato finale per le nuove installazioni.

In `schema.sql`, nella definizione di `divisioni`, aggiungere la colonna e il FK:
```sql
-- Nella CREATE TABLE divisioni, aggiungere dopo 'updated_at':
  struttura_id INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY (struttura_id) REFERENCES strutture(id)
```

In `utenti`, aggiungere dopo `updated_at`:
```sql
  struttura_id INTEGER,   -- NULL per superadmin
  FOREIGN KEY (struttura_id) REFERENCES strutture(id)
```

Estendere il CHECK del ruolo in `utenti`:
```sql
  ruolo TEXT NOT NULL CHECK(ruolo IN ('superadmin', 'admin', 'utente')),
```

In `apparecchi`, aggiungere dopo `updated_at`:
```sql
  struttura_id INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY (struttura_id) REFERENCES strutture(id)
```
E cambiare il UNIQUE:
```sql
  UNIQUE(struttura_id, modello, matricola),
```
(rimuovere il vecchio `UNIQUE(modello, matricola)`)

In `log_attivita`, aggiungere dopo `ip_address`:
```sql
  struttura_id INTEGER,
  FOREIGN KEY (struttura_id) REFERENCES strutture(id)
```

- [ ] **Step 3: Commit**

```bash
git add schema.sql
git commit -m "feat: aggiorna schema.sql per multi-struttura v2.0"
```

---

### Task 2: Script migrate_v2_0.py

Questo è lo script più critico: deve essere idempotente (sicuro da rieseguire), non perdere dati e funzionare su installazioni v1.x esistenti.

**File:**
- Crea: `migrate_v2_0.py`

- [ ] **Step 1: Creare `migrate_v2_0.py`**

```python
#!/usr/bin/env python3
"""
migrate_v2_0.py — Migrazione MedInventory v1.x → v2.0
Script idempotente. Sicuro da eseguire più volte.

Eseguire PRIMA di avviare l'app v2.0:
    python migrate_v2_0.py
"""

import sqlite3
import json
import os
import shutil
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.local.json')
FALLBACK_CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    with open(FALLBACK_CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_db_path(config):
    db_path = config.get('database_path', 'data/database.sqlite')
    if not os.path.isabs(db_path):
        db_path = os.path.join(BASE_DIR, db_path)
    return db_path


def table_exists(db, name):
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def column_exists(db, table, column):
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def index_exists(db, name):
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone()
    return row is not None


def run_safe(db, sql, desc=""):
    """Esegue SQL ignorando errori 'già esistente'."""
    try:
        db.execute(sql)
        if desc:
            logger.info(f"  OK: {desc}")
    except Exception as e:
        logger.debug(f"  Skip ({desc}): {e}")


def migrate(db, config):
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute("PRAGMA legacy_alter_table = ON")

    # ----------------------------------------------------------------
    # 1. Nuove tabelle
    # ----------------------------------------------------------------
    logger.info("1. Creazione nuove tabelle...")

    run_safe(db, """CREATE TABLE IF NOT EXISTS strutture (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      nome            TEXT NOT NULL,
      codice          TEXT UNIQUE NOT NULL,
      descrizione     TEXT,
      indirizzo       TEXT,
      email_notifiche TEXT,
      modalita        TEXT NOT NULL DEFAULT 'ingegneria_clinica'
                      CHECK(modalita IN ('standard', 'ingegneria_clinica')),
      attiva          INTEGER DEFAULT 1,
      created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
    )""", "strutture")

    run_safe(db, """CREATE TABLE IF NOT EXISTS strutture_config (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      struttura_id INTEGER NOT NULL,
      chiave       TEXT NOT NULL,
      valore       TEXT,
      UNIQUE(struttura_id, chiave),
      FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE
    )""", "strutture_config")

    run_safe(db, """CREATE TABLE IF NOT EXISTS api_tokens (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      struttura_id    INTEGER NOT NULL,
      nome            TEXT NOT NULL,
      token_hash      TEXT UNIQUE NOT NULL,
      scopes          TEXT DEFAULT 'read',
      ultimo_utilizzo DATETIME,
      scadenza        DATE,
      attivo          INTEGER DEFAULT 1,
      created_by      INTEGER,
      created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE,
      FOREIGN KEY (created_by) REFERENCES utenti(id)
    )""", "api_tokens")

    run_safe(db, """CREATE TABLE IF NOT EXISTS login_attempts (
      id         INTEGER PRIMARY KEY AUTOINCREMENT,
      ip_address TEXT NOT NULL,
      email      TEXT,
      esito      TEXT NOT NULL CHECK(esito IN ('fallito', 'bloccato', 'riuscito')),
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""", "login_attempts")

    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_strutture_codice ON strutture(codice)",
        "CREATE INDEX IF NOT EXISTS idx_strutture_attiva ON strutture(attiva)",
        "CREATE INDEX IF NOT EXISTS idx_strutture_config_struttura ON strutture_config(struttura_id)",
        "CREATE INDEX IF NOT EXISTS idx_api_tokens_struttura ON api_tokens(struttura_id)",
        "CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON api_tokens(token_hash)",
        "CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip_address, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_login_attempts_email ON login_attempts(email, created_at)",
    ]:
        run_safe(db, idx_sql)

    db.commit()

    # ----------------------------------------------------------------
    # 2. Struttura di default
    # ----------------------------------------------------------------
    logger.info("2. Struttura di default...")
    struttura_nome = config.get('structure_name', config.get('app_name', 'Struttura Principale'))
    struttura_exists = db.execute("SELECT id FROM strutture LIMIT 1").fetchone()
    if not struttura_exists:
        db.execute(
            """INSERT INTO strutture (nome, codice, modalita) VALUES (?, ?, 'ingegneria_clinica')""",
            (struttura_nome, 'DEFAULT')
        )
        db.commit()
        logger.info(f"  Struttura '{struttura_nome}' creata.")
    else:
        logger.info("  Struttura di default già presente.")

    struttura_id = db.execute("SELECT id FROM strutture ORDER BY id LIMIT 1").fetchone()[0]

    # ----------------------------------------------------------------
    # 3. Colonne struttura_id nelle tabelle esistenti
    # ----------------------------------------------------------------
    logger.info("3. Aggiunta colonne struttura_id...")

    for table in ('divisioni', 'apparecchi', 'log_attivita'):
        if not column_exists(db, table, 'struttura_id'):
            db.execute(f"ALTER TABLE {table} ADD COLUMN struttura_id INTEGER")
            db.execute(f"UPDATE {table} SET struttura_id = ?", (struttura_id,))
            db.commit()
            logger.info(f"  struttura_id aggiunto a {table}")

    if not column_exists(db, 'utenti', 'struttura_id'):
        db.execute("ALTER TABLE utenti ADD COLUMN struttura_id INTEGER")
        # superadmin lasciato a NULL; tutti gli altri associati alla struttura di default
        db.execute(
            "UPDATE utenti SET struttura_id = ? WHERE ruolo != 'superadmin'",
            (struttura_id,)
        )
        db.commit()
        logger.info("  struttura_id aggiunto a utenti")

    # ----------------------------------------------------------------
    # 4. Estensione CHECK ruolo utenti
    # ----------------------------------------------------------------
    # SQLite non supporta ALTER TABLE MODIFY COLUMN: il CHECK è nel DDL originale.
    # Aggiungiamo 'superadmin' al CHECK ricreando la tabella utenti.
    # Lo facciamo solo se necessario (nessun utente superadmin esiste già).
    logger.info("4. Verifica CHECK ruolo utenti...")
    utenti_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='utenti'"
    ).fetchone()[0]
    if 'superadmin' not in utenti_sql:
        logger.info("  Ricreazione tabella utenti per aggiornare CHECK ruolo...")
        _recreate_utenti(db)
    else:
        logger.info("  CHECK ruolo già aggiornato.")

    # ----------------------------------------------------------------
    # 5. Ricreazione tabella apparecchi (nuovo UNIQUE struttura_id + modello + matricola)
    # ----------------------------------------------------------------
    logger.info("5. Aggiornamento UNIQUE su apparecchi...")
    app_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='apparecchi'"
    ).fetchone()[0]
    if 'struttura_id, modello, matricola' not in app_sql:
        logger.info("  Ricreazione tabella apparecchi per aggiornare UNIQUE...")
        _recreate_apparecchi(db, struttura_id)
    else:
        logger.info("  UNIQUE su apparecchi già aggiornato.")

    db.commit()

    # ----------------------------------------------------------------
    # 6. config.local.json — aggiunta single_struttura
    # ----------------------------------------------------------------
    logger.info("6. Aggiornamento config.local.json...")
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            local_cfg = json.load(f)
        if 'single_struttura' not in local_cfg:
            local_cfg['single_struttura'] = True
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(local_cfg, f, indent=2, ensure_ascii=False)
            logger.info("  single_struttura: true aggiunto a config.local.json")

    # ----------------------------------------------------------------
    # 7. File sentinella versione
    # ----------------------------------------------------------------
    logger.info("7. File sentinella versione...")
    notice_path = os.path.join(BASE_DIR, 'data', '.version_notice')
    if not os.path.exists(notice_path):
        with open(notice_path, 'w', encoding='utf-8') as f:
            json.dump({
                'old_version': '1.x',
                'new_version': '2.0.0',
                'upgraded_at': datetime.now().strftime('%d/%m/%Y %H:%M'),
            }, f)

    # ----------------------------------------------------------------
    # 8. PRAGMA user_version → 200
    # ----------------------------------------------------------------
    db.execute("PRAGMA user_version = 200")
    db.commit()

    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA legacy_alter_table = OFF")
    logger.info("Migrazione completata. PRAGMA user_version = 200")


def _recreate_utenti(db):
    """Ricrea la tabella utenti con CHECK ruolo aggiornato (include 'superadmin')."""
    db.execute("ALTER TABLE utenti RENAME TO _utenti_old")
    db.execute("""CREATE TABLE utenti (
      id                   INTEGER PRIMARY KEY AUTOINCREMENT,
      email                TEXT UNIQUE NOT NULL,
      password_hash        TEXT NOT NULL,
      nome                 TEXT NOT NULL,
      cognome              TEXT NOT NULL,
      ruolo                TEXT NOT NULL CHECK(ruolo IN ('superadmin', 'admin', 'utente')),
      struttura_id         INTEGER,
      divisione_default_id INTEGER,
      attivo               INTEGER DEFAULT 1,
      primo_accesso        INTEGER DEFAULT 1,
      ultimo_accesso       DATETIME,
      created_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY (struttura_id) REFERENCES strutture(id),
      FOREIGN KEY (divisione_default_id) REFERENCES divisioni(id)
    )""")
    # Copia i dati — struttura_id potrebbe già esistere o no
    cols_old = [row[1] for row in db.execute("PRAGMA table_info(_utenti_old)").fetchall()]
    if 'struttura_id' in cols_old:
        db.execute("""INSERT INTO utenti
            SELECT id, email, password_hash, nome, cognome, ruolo,
                   struttura_id, divisione_default_id, attivo, primo_accesso,
                   ultimo_accesso, created_at, updated_at
            FROM _utenti_old""")
    else:
        db.execute("""INSERT INTO utenti
            SELECT id, email, password_hash, nome, cognome, ruolo,
                   NULL, divisione_default_id, attivo, primo_accesso,
                   ultimo_accesso, created_at, updated_at
            FROM _utenti_old""")
    db.execute("DROP TABLE _utenti_old")
    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_utenti_email ON utenti(email)",
        "CREATE INDEX IF NOT EXISTS idx_utenti_ruolo ON utenti(ruolo)",
        "CREATE INDEX IF NOT EXISTS idx_utenti_attivo ON utenti(attivo)",
    ]:
        db.execute(idx)
    db.commit()


def _recreate_apparecchi(db, struttura_id):
    """Ricrea la tabella apparecchi con UNIQUE(struttura_id, modello, matricola)."""
    db.execute("ALTER TABLE apparecchi RENAME TO _apparecchi_old")
    db.execute("""CREATE TABLE apparecchi (
      id                   INTEGER PRIMARY KEY AUTOINCREMENT,
      struttura_id         INTEGER NOT NULL,
      divisione_id         INTEGER NOT NULL,
      descrizione          TEXT,
      matricola            TEXT NOT NULL,
      numero_inventario    TEXT,
      marca                TEXT NOT NULL,
      modello              TEXT NOT NULL,
      anno_fabbricazione   INTEGER,
      classificazione      TEXT CHECK(classificazione IN ('I', 'IIa', 'IIb', 'III')),
      ubicazione           TEXT,
      stato                TEXT DEFAULT 'funzionante'
                           CHECK(stato IN ('funzionante', 'in_manutenzione', 'dismesso', 'da_sostituire')),
      connesso_rete        INTEGER DEFAULT 0,
      ip_address           TEXT,
      mac_address          TEXT,
      hostname             TEXT,
      porta                INTEGER,
      protocollo           TEXT,
      url_interfaccia      TEXT,
      fornitore            TEXT,
      codice_fornitore     TEXT,
      garanzia_scadenza    DATE,
      contratto_manutenzione TEXT,
      note                 TEXT,
      foto_path            TEXT,
      soggetto_verifica    INTEGER NOT NULL DEFAULT 1,
      created_by           INTEGER,
      updated_by           INTEGER,
      created_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(struttura_id, modello, matricola),
      FOREIGN KEY (struttura_id) REFERENCES strutture(id),
      FOREIGN KEY (divisione_id) REFERENCES divisioni(id),
      FOREIGN KEY (created_by) REFERENCES utenti(id),
      FOREIGN KEY (updated_by) REFERENCES utenti(id)
    )""")
    cols_old = [row[1] for row in db.execute("PRAGMA table_info(_apparecchi_old)").fetchall()]
    sv_col = 'soggetto_verifica' if 'soggetto_verifica' in cols_old else f"1"
    sid_col = 'struttura_id' if 'struttura_id' in cols_old else str(struttura_id)
    db.execute(f"""INSERT INTO apparecchi
        SELECT id, {sid_col}, divisione_id, descrizione, matricola,
               numero_inventario, marca, modello, anno_fabbricazione, classificazione,
               ubicazione, stato, connesso_rete, ip_address, mac_address, hostname,
               porta, protocollo, url_interfaccia, fornitore, codice_fornitore,
               garanzia_scadenza, contratto_manutenzione, note, foto_path,
               {sv_col}, created_by, updated_by, created_at, updated_at
        FROM _apparecchi_old""")
    db.execute("DROP TABLE _apparecchi_old")
    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_apparecchi_divisione ON apparecchi(divisione_id)",
        "CREATE INDEX IF NOT EXISTS idx_apparecchi_struttura ON apparecchi(struttura_id)",
        "CREATE INDEX IF NOT EXISTS idx_apparecchi_matricola ON apparecchi(matricola)",
        "CREATE INDEX IF NOT EXISTS idx_apparecchi_stato ON apparecchi(stato)",
        "CREATE INDEX IF NOT EXISTS idx_apparecchi_descrizione ON apparecchi(descrizione)",
        "CREATE INDEX IF NOT EXISTS idx_apparecchi_marca ON apparecchi(marca)",
    ]:
        db.execute(idx)
    db.commit()


def main():
    config = load_config()
    db_path = get_db_path(config)

    if not os.path.exists(db_path):
        logger.error(f"Database non trovato: {db_path}")
        logger.error("Eseguire prima 'python seed.py' per una nuova installazione.")
        return

    # Backup preventivo
    backup_path = db_path + f".bak_pre_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(db_path, backup_path)
    logger.info(f"Backup creato: {backup_path}")

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        migrate(db, config)
    except Exception as e:
        logger.error(f"Errore durante la migrazione: {e}")
        db.close()
        logger.error(f"Ripristino dal backup: {backup_path}")
        shutil.copy2(backup_path, db_path)
        raise
    finally:
        db.close()

    logger.info("=" * 50)
    logger.info("Migrazione v2.0 completata con successo.")
    logger.info(f"Backup pre-migrazione: {backup_path}")
    logger.info("Avviare l'applicazione con: python app.py")


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Verificare lo script su una copia del database**

```bash
# Copia il DB in un percorso temporaneo per il test
cp data/database.sqlite data/database_test.sqlite
# Modifica temporanea: punta a database_test.sqlite
# poi esegui:
python migrate_v2_0.py
# Controllare l'output: nessun errore, "Migrazione v2.0 completata con successo"

# Verificare il risultato:
python -c "
import sqlite3
db = sqlite3.connect('data/database.sqlite')
print('strutture:', db.execute('SELECT * FROM strutture').fetchall())
print('user_version:', db.execute('PRAGMA user_version').fetchone()[0])
print('struttura_id su divisioni:', db.execute('SELECT struttura_id FROM divisioni LIMIT 1').fetchone())
db.close()
"
```

**Atteso:** strutture contiene 1 riga, user_version=200, struttura_id non NULL.

- [ ] **Step 3: Commit**

```bash
git add migrate_v2_0.py
git commit -m "feat: aggiunge migrate_v2_0.py — migrazione idempotente a v2.0"
```

---

### Task 3: Helpers models.py per strutture_config

**File:**
- Modifica: `models.py`

- [ ] **Step 1: Aggiungere le funzioni helper in fondo a `models.py`**

```python
# ---------------------------------------------------------------------------
# Strutture config helpers
# ---------------------------------------------------------------------------

def get_struttura_config(struttura_id, chiave, default=None):
    """Legge un valore di configurazione per-struttura.
    Se non presente, restituisce il valore globale da APP_CONFIG o il default.
    """
    row = query_one(
        "SELECT valore FROM strutture_config WHERE struttura_id = ? AND chiave = ?",
        (struttura_id, chiave)
    )
    if row and row['valore'] is not None:
        return row['valore']
    # Fallback al config globale
    try:
        from flask import current_app
        return current_app.config['APP_CONFIG'].get(chiave, default)
    except RuntimeError:
        return default


def set_struttura_config(struttura_id, chiave, valore):
    """Inserisce o aggiorna un valore di configurazione per-struttura."""
    execute(
        """INSERT INTO strutture_config (struttura_id, chiave, valore)
           VALUES (?, ?, ?)
           ON CONFLICT(struttura_id, chiave) DO UPDATE SET valore = excluded.valore""",
        (struttura_id, chiave, valore)
    )


def get_struttura_config_all(struttura_id):
    """Restituisce tutti i valori di configurazione per-struttura come dict."""
    rows = query_all(
        "SELECT chiave, valore FROM strutture_config WHERE struttura_id = ?",
        (struttura_id,)
    )
    return {r['chiave']: r['valore'] for r in rows}
```

- [ ] **Step 2: Aggiornare `log_attivita` per accettare `struttura_id`**

Trovare la funzione `log_attivita` in `models.py` e aggiornarla:

```python
def log_attivita(utente_id, azione, entita, entita_id=None, dettagli=None,
                 ip_address=None, struttura_id=None):
    """Log an activity to the log_attivita table."""
    execute(
        """INSERT INTO log_attivita
               (utente_id, azione, entita, entita_id, dettagli, ip_address, struttura_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (utente_id, azione, entita, entita_id, dettagli, ip_address, struttura_id)
    )
```

- [ ] **Step 3: Commit**

```bash
git add models.py
git commit -m "feat: aggiunge helpers strutture_config e struttura_id a log_attivita"
```

---

### Task 4: Aggiornamento auth.py — nuovi decoratori, sessione, rate limiting, logout globale

**File:**
- Modifica: `auth.py`

- [ ] **Step 1: Aggiungere i nuovi decoratori dopo `admin_required`**

```python
def superadmin_required(f):
    """Decorator: richiede ruolo superadmin."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if g.user['ruolo'] != 'superadmin':
            flash('Accesso riservato al superamministratore.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def admin_struttura_required(f):
    """Decorator: richiede ruolo admin (della struttura) o superadmin."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if g.user['ruolo'] not in ('admin', 'superadmin'):
            flash('Accesso non autorizzato.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def modalita_avanzata_required(f):
    """Decorator: richiede modalita='ingegneria_clinica' per la struttura corrente."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if g.user['ruolo'] == 'superadmin':
            return f(*args, **kwargs)  # superadmin bypassa sempre
        struttura_modalita = getattr(g, 'struttura_modalita', 'ingegneria_clinica')
        if struttura_modalita != 'ingegneria_clinica':
            flash('Funzione disponibile solo in modalità Ingegneria Clinica.', 'warning')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function
```

- [ ] **Step 2: Aggiornare `_load_user_from_session` per popolare i dati della struttura**

Sostituire il corpo di `_load_user_from_session` dopo la riga `g.user = {...}`:

```python
    # Popola dati struttura nella sessione
    struttura = None
    if g.user['ruolo'] == 'superadmin':
        # Il superadmin può impersonare una struttura tramite sessione
        struttura_impersonata_id = session.get('struttura_impersonata_id')
        if struttura_impersonata_id:
            struttura = query_one(
                "SELECT * FROM strutture WHERE id = ? AND attiva = 1",
                (struttura_impersonata_id,)
            )
    else:
        struttura_id = g.user.get('struttura_id')
        if struttura_id:
            struttura = query_one(
                "SELECT * FROM strutture WHERE id = ? AND attiva = 1",
                (struttura_id,)
            )

    g.struttura = struttura
    g.struttura_id = struttura['id'] if struttura else None
    g.struttura_nome = struttura['nome'] if struttura else None
    g.struttura_modalita = struttura['modalita'] if struttura else 'ingegneria_clinica'
    g.is_superadmin_impersonating = (
        g.user['ruolo'] == 'superadmin' and struttura is not None
    )
```

La query della sessione deve anche includere `struttura_id` nell'utente. Aggiornare la query SQL in `_load_user_from_session`:

```python
    sess = query_one(
        """SELECT s.*, u.id as user_id, u.email, u.nome, u.cognome, u.ruolo,
                  u.attivo, u.primo_accesso, u.divisione_default_id, u.struttura_id
           FROM sessioni s
           JOIN utenti u ON s.utente_id = u.id
           WHERE s.token = ? AND s.expires_at > datetime('now') AND u.attivo = 1""",
        (token,)
    )
```

E nel dizionario `g.user` aggiungere `struttura_id`:
```python
    g.user = {
        'id': sess['user_id'],
        'email': sess['email'],
        'nome': sess['nome'],
        'cognome': sess['cognome'],
        'ruolo': sess['ruolo'],
        'primo_accesso': sess['primo_accesso'],
        'divisione_default_id': sess['divisione_default_id'],
        'struttura_id': sess['struttura_id'],
    }
```

- [ ] **Step 3: Aggiungere rate limiting nella route `login` (POST)**

All'inizio del blocco `POST` di `auth_bp.route('/login')`, prima della query sull'utente:

```python
    # Rate limiting
    ip = request.remote_addr
    from datetime import datetime, timedelta
    blocco_limite = datetime.now() - timedelta(minutes=10)
    tentativi = query_one(
        """SELECT COUNT(*) as cnt FROM login_attempts
           WHERE ip_address = ? AND esito = 'fallito'
             AND created_at > ?""",
        (ip, blocco_limite.strftime('%Y-%m-%d %H:%M:%S'))
    )
    if tentativi and tentativi['cnt'] >= 5:
        # Registra tentativo bloccato
        execute(
            "INSERT INTO login_attempts (ip_address, email, esito) VALUES (?, ?, 'bloccato')",
            (ip, email)
        )
        flash('Troppi tentativi falliti. Riprova tra 15 minuti.', 'danger')
        return render_template('login.html', email=email), 429
```

Dopo la verifica della password (blocco `if not check_password_hash(...)`), registrare il tentativo fallito:
```python
        execute(
            "INSERT INTO login_attempts (ip_address, email, esito) VALUES (?, ?, 'fallito')",
            (ip, email)
        )
```

Al login riuscito, registrare successo e azzerare i tentativi:
```python
    execute(
        "INSERT INTO login_attempts (ip_address, email, esito) VALUES (?, ?, 'riuscito')",
        (ip, email)
    )
    execute(
        "DELETE FROM login_attempts WHERE ip_address = ? OR email = ?",
        (ip, email)
    )
```

- [ ] **Step 4: Aggiungere route impersonation superadmin**

```python
@auth_bp.route('/impersona/<int:struttura_id>')
@superadmin_required
def impersona_struttura(struttura_id):
    """Superadmin entra nel contesto di una struttura specifica."""
    struttura = query_one("SELECT id, nome FROM strutture WHERE id = ? AND attiva = 1", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index'))
    session['struttura_impersonata_id'] = struttura_id
    flash(f'Stai operando come: {struttura["nome"]}', 'info')
    return redirect(url_for('index'))


@auth_bp.route('/esci-impersonazione')
@superadmin_required
def esci_impersonazione():
    """Superadmin torna alla vista globale."""
    session.pop('struttura_impersonata_id', None)
    return redirect(url_for('strutture.index'))
```

- [ ] **Step 5: Aggiungere route logout globale**

```python
@auth_bp.route('/logout-ovunque', methods=['POST'])
@login_required
def logout_ovunque():
    """Revoca tutte le sessioni dell'utente corrente (o di un utente specifico per admin)."""
    target_id = request.form.get('utente_id', type=int) or g.user['id']
    # Solo admin/superadmin possono revocare sessioni altrui
    if target_id != g.user['id'] and g.user['ruolo'] not in ('admin', 'superadmin'):
        flash('Non autorizzato.', 'danger')
        return redirect(url_for('index'))
    token_corrente = session.get('token')
    if target_id == g.user['id']:
        # Revoca tutto tranne la sessione corrente
        execute(
            "DELETE FROM sessioni WHERE utente_id = ? AND token != ?",
            (target_id, token_corrente)
        )
        flash('Tutte le altre sessioni sono state revocate.', 'success')
    else:
        execute("DELETE FROM sessioni WHERE utente_id = ?", (target_id,))
        flash('Sessioni revocate.', 'success')
    return redirect(request.referrer or url_for('index'))
```

- [ ] **Step 6: Esportare i nuovi decoratori da auth.py**

Verificare che le importazioni nel resto del codebase (`from auth import login_required, admin_required`) continuino a funzionare. I nuovi decoratori (`superadmin_required`, `admin_struttura_required`, `modalita_avanzata_required`) vanno importati esplicitamente dove servono.

- [ ] **Step 7: Verifica manuale**

```bash
python app.py
```
- Login con credenziali errate 5 volte → deve apparire messaggio di blocco
- Login con credenziali corrette → funziona normalmente
- Navigare a `/impersona/1` senza essere superadmin → redirect con flash "Accesso riservato"

- [ ] **Step 8: Commit**

```bash
git add auth.py
git commit -m "feat: nuovi decoratori superadmin, rate limiting, logout globale, impersonation"
```

---

### Task 5: Blueprint strutture_bp.py

**File:**
- Crea: `strutture_bp.py`
- Crea: `templates/strutture/index.html`
- Crea: `templates/strutture/form.html`
- Crea: `templates/strutture/config.html`

- [ ] **Step 1: Creare `strutture_bp.py`**

```python
"""
MedInventory - Gestione Strutture (superadmin)
"""

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, g
)
from auth import superadmin_required, login_required
from models import query_all, query_one, execute, log_attivita, \
    get_struttura_config_all, set_struttura_config

strutture_bp = Blueprint('strutture', __name__, url_prefix='/strutture')


@strutture_bp.route('/')
@superadmin_required
def index():
    strutture = query_all("""
        SELECT s.*,
               COUNT(DISTINCT d.id) as num_divisioni,
               COUNT(DISTINCT u.id) as num_utenti,
               COUNT(DISTINCT a.id) as num_apparecchi
        FROM strutture s
        LEFT JOIN divisioni d ON d.struttura_id = s.id AND d.attiva = 1
        LEFT JOIN utenti u ON u.struttura_id = s.id AND u.attivo = 1
        LEFT JOIN apparecchi a ON a.struttura_id = s.id AND a.stato != 'dismesso'
        GROUP BY s.id
        ORDER BY s.nome
    """)
    return render_template('strutture/index.html', strutture=strutture)


@strutture_bp.route('/nuova', methods=['GET', 'POST'])
@superadmin_required
def nuova():
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        codice = request.form.get('codice', '').strip().upper()
        descrizione = request.form.get('descrizione', '').strip()
        indirizzo = request.form.get('indirizzo', '').strip()
        email_notifiche = request.form.get('email_notifiche', '').strip()
        modalita = request.form.get('modalita', 'standard')

        if not nome or not codice:
            flash('Nome e codice sono obbligatori.', 'danger')
            return render_template('strutture/form.html', struttura=request.form)

        try:
            cur = execute(
                """INSERT INTO strutture (nome, codice, descrizione, indirizzo, email_notifiche, modalita)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (nome, codice, descrizione or None, indirizzo or None,
                 email_notifiche or None, modalita)
            )
            log_attivita(g.user['id'], 'crea', 'struttura', cur.lastrowid,
                         f'Struttura "{nome}" creata')
            flash(f'Struttura "{nome}" creata con successo.', 'success')
            return redirect(url_for('strutture.index'))
        except Exception as e:
            if 'UNIQUE' in str(e):
                flash(f'Il codice "{codice}" è già in uso.', 'danger')
            else:
                flash(f'Errore: {e}', 'danger')
        return render_template('strutture/form.html', struttura=request.form)

    return render_template('strutture/form.html', struttura=None)


@strutture_bp.route('/<int:struttura_id>/modifica', methods=['GET', 'POST'])
@superadmin_required
def modifica(struttura_id):
    struttura = query_one("SELECT * FROM strutture WHERE id = ?", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index'))

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        codice = request.form.get('codice', '').strip().upper()
        descrizione = request.form.get('descrizione', '').strip()
        indirizzo = request.form.get('indirizzo', '').strip()
        email_notifiche = request.form.get('email_notifiche', '').strip()
        modalita = request.form.get('modalita', 'standard')
        attiva = 1 if request.form.get('attiva') else 0

        try:
            execute(
                """UPDATE strutture SET nome=?, codice=?, descrizione=?, indirizzo=?,
                   email_notifiche=?, modalita=?, attiva=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (nome, codice, descrizione or None, indirizzo or None,
                 email_notifiche or None, modalita, attiva, struttura_id)
            )
            log_attivita(g.user['id'], 'modifica', 'struttura', struttura_id,
                         f'Struttura "{nome}" modificata')
            flash('Struttura aggiornata.', 'success')
            return redirect(url_for('strutture.index'))
        except Exception as e:
            flash(f'Errore: {e}', 'danger')
        struttura = dict(struttura) | dict(request.form)

    return render_template('strutture/form.html', struttura=struttura)


@strutture_bp.route('/<int:struttura_id>/config', methods=['GET', 'POST'])
@superadmin_required
def config(struttura_id):
    struttura = query_one("SELECT * FROM strutture WHERE id = ?", (struttura_id,))
    if not struttura:
        flash('Struttura non trovata.', 'danger')
        return redirect(url_for('strutture.index'))

    chiavi_visibili = [
        'ai_provider', 'anthropic_api_key', 'ai_import_model',
        'ai_email_model', 'ai_local_base_url', 'ai_local_model',
        'smtp_host', 'smtp_port', 'smtp_user', 'smtp_from', 'smtp_use_tls',
        'report_frequenza', 'report_schedulato_attivo',
    ]
    # smtp_password_encrypted: gestito separatamente (non mostrato in chiaro)

    if request.method == 'POST':
        for chiave in chiavi_visibili:
            valore = request.form.get(chiave, '').strip()
            if valore:
                set_struttura_config(struttura_id, chiave, valore)
            else:
                # Rimuovi la chiave (usa default globale)
                execute(
                    "DELETE FROM strutture_config WHERE struttura_id=? AND chiave=?",
                    (struttura_id, chiave)
                )
        # Password SMTP: solo se fornita, cifrata con Fernet (stessa chiave di email_config)
        smtp_password = request.form.get('smtp_password', '').strip()
        if smtp_password:
            from cryptography.fernet import Fernet
            from flask import current_app
            key = current_app.config['APP_CONFIG'].get('encryption_key', '')
            if key:
                import base64, hashlib
                fernet_key = base64.urlsafe_b64encode(
                    hashlib.sha256(key.encode()).digest()
                )
                f = Fernet(fernet_key)
                encrypted = f.encrypt(smtp_password.encode()).decode()
                set_struttura_config(struttura_id, 'smtp_password_encrypted', encrypted)

        flash('Configurazione salvata.', 'success')
        return redirect(url_for('strutture.config', struttura_id=struttura_id))

    cfg = get_struttura_config_all(struttura_id)
    return render_template('strutture/config.html',
                           struttura=struttura, cfg=cfg, chiavi=chiavi_visibili)
```

- [ ] **Step 2: Creare `templates/strutture/index.html`**

Template Bootstrap 5 che estende `base.html`. Mostra una tabella con colonne:
Nome, Codice, Modalità, Divisioni, Utenti, Apparecchi, Stato, Azioni (Modifica, Config, Entra).
Il pulsante "Entra" chiama `/impersona/<id>`.

```html
{% extends "base.html" %}
{% block title %}Strutture{% endblock %}
{% block content %}
<div class="d-flex justify-content-between align-items-center mb-4">
  <h2><i class="bi bi-building me-2"></i>Strutture</h2>
  <a href="{{ url_for('strutture.nuova') }}" class="btn btn-primary">
    <i class="bi bi-plus-lg me-1"></i>Nuova struttura
  </a>
</div>
<div class="card shadow-sm">
  <div class="table-responsive">
    <table class="table table-hover mb-0">
      <thead class="table-light">
        <tr>
          <th>Nome</th><th>Codice</th><th>Modalità</th>
          <th class="text-center">Divisioni</th>
          <th class="text-center">Utenti</th>
          <th class="text-center">Apparecchi</th>
          <th class="text-center">Stato</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {% for s in strutture %}
        <tr>
          <td>{{ s.nome }}</td>
          <td><code>{{ s.codice }}</code></td>
          <td>
            {% if s.modalita == 'ingegneria_clinica' %}
              <span class="badge bg-primary">Ingegneria Clinica</span>
            {% else %}
              <span class="badge bg-secondary">Standard</span>
            {% endif %}
          </td>
          <td class="text-center">{{ s.num_divisioni }}</td>
          <td class="text-center">{{ s.num_utenti }}</td>
          <td class="text-center">{{ s.num_apparecchi }}</td>
          <td class="text-center">
            {% if s.attiva %}
              <span class="badge bg-success">Attiva</span>
            {% else %}
              <span class="badge bg-danger">Disattiva</span>
            {% endif %}
          </td>
          <td class="text-end">
            <a href="{{ url_for('auth.impersona_struttura', struttura_id=s.id) }}"
               class="btn btn-sm btn-outline-primary me-1">
              <i class="bi bi-box-arrow-in-right"></i> Entra
            </a>
            <a href="{{ url_for('strutture.modifica', struttura_id=s.id) }}"
               class="btn btn-sm btn-outline-secondary me-1">
              <i class="bi bi-pencil"></i>
            </a>
            <a href="{{ url_for('strutture.config', struttura_id=s.id) }}"
               class="btn btn-sm btn-outline-secondary">
              <i class="bi bi-gear"></i>
            </a>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 3: Creare `templates/strutture/form.html`**

Form con campi: nome, codice, descrizione, indirizzo, email_notifiche, modalità (select), attiva (checkbox per modifica). Estende `base.html`.

- [ ] **Step 4: Commit**

```bash
git add strutture_bp.py templates/strutture/
git commit -m "feat: aggiunge strutture_bp con CRUD strutture (superadmin)"
```

---

### Task 6: Aggiornamento app.py — inject_globals, flag single_struttura, registrazione blueprint

**File:**
- Modifica: `app.py`

- [ ] **Step 1: Aggiornare `APP_VERSION` e registrare i nuovi blueprint**

In `app.py`, cambiare:
```python
APP_VERSION = "2.0.0"
```

Nel blocco di registrazione dei blueprint (cercare `app.register_blueprint`), aggiungere:
```python
from strutture_bp import strutture_bp
from api_bp import api_bp   # creato nel Task 12

app.register_blueprint(strutture_bp)
app.register_blueprint(api_bp)
```

- [ ] **Step 2: Aggiornare `inject_globals()` per iniettare dati struttura e modalità**

Trovare la funzione `inject_globals` (o il `context_processor` equivalente) e aggiungere:

```python
@app.context_processor
def inject_globals():
    config = current_app.config.get('APP_CONFIG', {})
    single_struttura = config.get('single_struttura', False)

    struttura = getattr(g, 'struttura', None)
    struttura_modalita = getattr(g, 'struttura_modalita', 'ingegneria_clinica')
    is_superadmin_impersonating = getattr(g, 'is_superadmin_impersonating', False)

    # In modalità single_struttura la modalità è sempre ingegneria_clinica
    if single_struttura:
        struttura_modalita = 'ingegneria_clinica'

    return dict(
        app_name=config.get('app_name', 'MedInventory'),
        app_version=APP_VERSION,
        organization=config.get('organization', ''),
        single_struttura=single_struttura,
        g_struttura=struttura,
        g_struttura_modalita=struttura_modalita,
        g_is_superadmin_impersonating=is_superadmin_impersonating,
        # variabili già esistenti:
        divisioni=getattr(g, 'divisioni', []),
        divisione_attiva=getattr(g, 'divisione_attiva', None),
        scadenze_alert_count=getattr(g, 'scadenze_alert_count', 0),
        current_user=getattr(g, 'user', None),
    )
```

- [ ] **Step 3: Aggiungere `single_struttura` alla lista `LOCAL_CONFIG_KEYS`**

In `app.py`, trovare `LOCAL_CONFIG_KEYS` e aggiungere `'single_struttura'`:
```python
LOCAL_CONFIG_KEYS = frozenset({
    ...
    'single_struttura',   # <-- aggiunto
})
```

- [ ] **Step 4: Aggiornare `config.local.example.json`**

Aprire `config.local.example.json` e aggiungere:
```json
"single_struttura": false
```
(default `false` per nuove installazioni multi-struttura; `migrate_v2_0.py` lo imposta a `true` sulle installazioni esistenti)

- [ ] **Step 5: Aggiornare `templates/base.html`** — breadcrumb superadmin e voci menu avanzate

Nel navbar, aggiungere il breadcrumb di impersonation (visibile solo se `g_is_superadmin_impersonating`):

```html
{% if g_is_superadmin_impersonating %}
<div class="alert alert-info alert-sm py-1 px-3 mb-0 rounded-0 border-0 d-flex align-items-center">
  <i class="bi bi-shield-lock me-2"></i>
  <small>Superadmin › <strong>{{ g_struttura.nome }}</strong></small>
  <a href="{{ url_for('auth.esci_impersonazione') }}" class="btn btn-sm btn-outline-dark ms-3 py-0">
    Esci
  </a>
</div>
{% endif %}
```

Nel menu di navigazione, avvolgere le voci avanzate:
```html
{% if g_struttura_modalita == 'ingegneria_clinica' %}
  <!-- Link a: Email Monitor, Report, Audit Log, API Tokens -->
{% endif %}

{% if current_user and current_user.ruolo == 'superadmin' and not single_struttura %}
  <!-- Link a: /strutture/ -->
{% endif %}
```

Aggiungere il dropdown struttura per il superadmin (partial):
```html
{% if current_user and current_user.ruolo == 'superadmin' and not single_struttura %}
  {% include 'partials/struttura_switcher.html' %}
{% endif %}
```

- [ ] **Step 6: Creare `templates/partials/struttura_switcher.html`**

```html
<li class="nav-item dropdown">
  <a class="nav-link dropdown-toggle" href="#" role="button" data-bs-toggle="dropdown">
    <i class="bi bi-building me-1"></i>
    {% if g_struttura %}{{ g_struttura.nome }}{% else %}Vista globale{% endif %}
  </a>
  <ul class="dropdown-menu dropdown-menu-end">
    <li>
      <a class="dropdown-item {% if not g_struttura %}active{% endif %}"
         href="{{ url_for('auth.esci_impersonazione') }}">
        <i class="bi bi-globe me-2"></i>Vista globale
      </a>
    </li>
    <li><hr class="dropdown-divider"></li>
    {% for s in strutture_list %}
    <li>
      <a class="dropdown-item {% if g_struttura and g_struttura.id == s.id %}active{% endif %}"
         href="{{ url_for('auth.impersona_struttura', struttura_id=s.id) }}">
        {{ s.nome }}
      </a>
    </li>
    {% endfor %}
  </ul>
</li>
```

Aggiungere `strutture_list` a `inject_globals()` solo per il superadmin:
```python
strutture_list = []
if getattr(g, 'user', None) and g.user.get('ruolo') == 'superadmin':
    from models import query_all as _qall
    strutture_list = _qall("SELECT id, nome FROM strutture WHERE attiva=1 ORDER BY nome")
```

- [ ] **Step 7: Verifica manuale**

```bash
python app.py
```
- Navigare a `/strutture/` → lista strutture (visibile solo con utente `superadmin`)
- Creare una nuova struttura → appare nella lista
- Cliccare "Entra" → breadcrumb visibile nel navbar
- Cliccare "Esci" → breadcrumb sparisce

- [ ] **Step 8: Commit**

```bash
git add app.py config.local.example.json templates/base.html templates/partials/struttura_switcher.html
git commit -m "feat: inject_globals aggiornato, flag single_struttura, menu modalita e switcher struttura"
```

---

## FASE B — Sicurezza (Tasks 7–8)

---

### Task 7: Dashboard superadmin e pagina /admin/sicurezza

**File:**
- Modifica: `admin.py`
- Crea: `templates/admin/sicurezza.html`
- Modifica: `templates/admin/index.html` (o file dashboard esistente)

- [ ] **Step 1: Aggiungere route dashboard superadmin**

In `admin.py` (o creare una route in `app.py`), aggiungere:

```python
@admin_bp.route('/superadmin/dashboard')
@superadmin_required
def superadmin_dashboard():
    """Dashboard aggregata cross-struttura per il superadmin."""
    strutture = query_all("""
        SELECT s.*,
               COUNT(DISTINCT a.id) as tot_apparecchi,
               SUM(CASE WHEN ps.priorita IN ('scaduto','urgente') THEN 1 ELSE 0 END) as scadenze_critiche
        FROM strutture s
        LEFT JOIN apparecchi a ON a.struttura_id = s.id AND a.stato != 'dismesso'
        LEFT JOIN prossime_scadenze ps ON ps.apparecchio_id = a.id
        WHERE s.attiva = 1
        GROUP BY s.id
        ORDER BY scadenze_critiche DESC, s.nome
    """)
    totali = query_one("""
        SELECT COUNT(DISTINCT a.id) as tot_apparecchi,
               SUM(CASE WHEN a.stato='funzionante' THEN 1 ELSE 0 END) as funzionanti,
               SUM(CASE WHEN a.stato='in_manutenzione' THEN 1 ELSE 0 END) as in_manutenzione,
               SUM(CASE WHEN a.stato='da_sostituire' THEN 1 ELSE 0 END) as da_sostituire
        FROM apparecchi a WHERE a.stato != 'dismesso'
    """)
    return render_template('admin/superadmin_dashboard.html',
                           strutture=strutture, totali=totali)
```

- [ ] **Step 2: Aggiungere route `/admin/sicurezza`**

```python
@admin_bp.route('/sicurezza')
@admin_required
def sicurezza():
    """Visualizza e sblocca IP/utenti bloccati dal rate limiting."""
    from datetime import datetime, timedelta
    limite = (datetime.now() - timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S')
    ip_bloccati = query_all("""
        SELECT ip_address, email, COUNT(*) as tentativi, MAX(created_at) as ultimo
        FROM login_attempts
        WHERE esito = 'fallito' AND created_at > ?
        GROUP BY ip_address
        HAVING COUNT(*) >= 5
        ORDER BY ultimo DESC
    """, (limite,))
    return render_template('admin/sicurezza.html', ip_bloccati=ip_bloccati)


@admin_bp.route('/sicurezza/sblocca', methods=['POST'])
@admin_required
def sblocca_ip():
    """Rimuove i tentativi falliti per un IP specifico."""
    ip = request.form.get('ip_address')
    if ip:
        execute("DELETE FROM login_attempts WHERE ip_address = ?", (ip,))
        flash(f'IP {ip} sbloccato.', 'success')
    return redirect(url_for('admin.sicurezza'))
```

- [ ] **Step 3: Creare `templates/admin/sicurezza.html`**

Template che mostra una tabella degli IP bloccati con pulsante "Sblocca" per ciascuno.
Estende `base.html`.

```html
{% extends "base.html" %}
{% block title %}Sicurezza{% endblock %}
{% block content %}
<h2 class="mb-4"><i class="bi bi-shield-lock me-2"></i>Sicurezza — IP bloccati</h2>
{% if ip_bloccati %}
<div class="card shadow-sm">
  <table class="table table-hover mb-0">
    <thead class="table-light">
      <tr><th>IP</th><th>Email</th><th>Tentativi</th><th>Ultimo tentativo</th><th></th></tr>
    </thead>
    <tbody>
    {% for ip in ip_bloccati %}
    <tr>
      <td><code>{{ ip.ip_address }}</code></td>
      <td>{{ ip.email or '—' }}</td>
      <td><span class="badge bg-danger">{{ ip.tentativi }}</span></td>
      <td>{{ ip.ultimo }}</td>
      <td>
        <form method="post" action="{{ url_for('admin.sblocca_ip') }}">
          <input type="hidden" name="ip_address" value="{{ ip.ip_address }}">
          <button class="btn btn-sm btn-warning">Sblocca</button>
        </form>
      </td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% else %}
<div class="alert alert-success">Nessun IP bloccato al momento.</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Commit**

```bash
git add admin.py templates/admin/sicurezza.html templates/admin/superadmin_dashboard.html
git commit -m "feat: dashboard superadmin e pagina sicurezza sblocco IP"
```

---

## FASE C — Dashboard, Config, Notifiche (Tasks 8–10)

---

### Task 8: Config per-struttura — AI e SMTP

**File:**
- Modifica: `ai_service.py`
- Modifica: `email_monitor.py`

- [ ] **Step 1: Aggiornare `ai_service.py` per leggere config da struttura**

Trovare il punto in cui `ai_service.py` legge la configurazione AI (tipicamente all'inizio delle funzioni `extract_text`, `parse_document`, o nella classe/funzione principale). Aggiungere un parametro opzionale `struttura_id`:

```python
def get_ai_config(struttura_id=None):
    """Restituisce la configurazione AI per la struttura, con fallback al globale."""
    from flask import current_app
    from models import get_struttura_config
    global_cfg = current_app.config.get('APP_CONFIG', {})

    if struttura_id:
        provider = get_struttura_config(struttura_id, 'ai_provider') or global_cfg.get('ai_provider', 'anthropic')
        api_key  = get_struttura_config(struttura_id, 'anthropic_api_key') or global_cfg.get('anthropic_api_key', '')
        model_import = get_struttura_config(struttura_id, 'ai_import_model') or global_cfg.get('ai_import_model', '')
        model_email  = get_struttura_config(struttura_id, 'ai_email_model') or global_cfg.get('ai_email_model', '')
        local_url    = get_struttura_config(struttura_id, 'ai_local_base_url') or global_cfg.get('ai_local_base_url', '')
        local_model  = get_struttura_config(struttura_id, 'ai_local_model') or global_cfg.get('ai_local_model', '')
    else:
        provider     = global_cfg.get('ai_provider', 'anthropic')
        api_key      = global_cfg.get('anthropic_api_key', '')
        model_import = global_cfg.get('ai_import_model', '')
        model_email  = global_cfg.get('ai_email_model', '')
        local_url    = global_cfg.get('ai_local_base_url', '')
        local_model  = global_cfg.get('ai_local_model', '')

    return {
        'provider': provider,
        'api_key': api_key,
        'model_import': model_import,
        'model_email': model_email,
        'local_base_url': local_url,
        'local_model': local_model,
    }
```

Passare `struttura_id` nelle chiamate principali di `ai_service.py` dove ora legge direttamente `current_app.config['APP_CONFIG']`.

- [ ] **Step 2: Commit**

```bash
git add ai_service.py email_monitor.py
git commit -m "feat: config AI e SMTP per-struttura con fallback globale"
```

---

### Task 9: Digest email scadenze — scheduler

**File:**
- Modifica: `scheduler.py`

- [ ] **Step 1: Aggiornare `_send_deadline_alerts` in `scheduler.py`**

La funzione attuale `_send_deadline_alerts` (se esiste come stub) va sostituita con:

```python
def _send_deadline_alerts(self):
    """Invia digest email scadenze a ogni struttura attiva con email_notifiche configurata."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    with self.app.app_context():
        from models import query_all, get_struttura_config
        strutture = query_all(
            "SELECT * FROM strutture WHERE attiva=1 AND email_notifiche IS NOT NULL"
        )
        global_cfg = self.app.config.get('APP_CONFIG', {})

        for struttura in strutture:
            sid = struttura['id']
            # Controlla frequenza configurata
            frequenza = get_struttura_config(sid, 'report_frequenza', 'settimanale')
            attivo = get_struttura_config(sid, 'report_schedulato_attivo', '1')
            if attivo != '1':
                continue
            if not self._is_digest_due(frequenza):
                continue

            scadenze = query_all("""
                SELECT ps.*, a.matricola, d.nome as divisione_nome
                FROM prossime_scadenze ps
                JOIN apparecchi a ON a.id = ps.apparecchio_id
                JOIN divisioni d ON d.id = ps.divisione_id
                WHERE ps.divisione_id IN (
                    SELECT id FROM divisioni WHERE struttura_id = ?
                )
                AND ps.priorita IN ('scaduto', 'urgente', 'attenzione', 'avviso')
                ORDER BY ps.priorita, ps.prossima_scadenza
            """, (sid,))

            if not scadenze:
                continue

            self._invia_digest(struttura, scadenze, global_cfg)


def _is_digest_due(self, frequenza):
    """Controlla se è il momento giusto per inviare il digest."""
    now = datetime.now()
    if frequenza == 'giornaliero':
        return now.hour == 7  # invia alle 7:00
    elif frequenza == 'settimanale':
        return now.weekday() == 0 and now.hour == 7  # lunedì alle 7:00
    elif frequenza == 'mensile':
        return now.day == 1 and now.hour == 7  # primo del mese alle 7:00
    return False


def _invia_digest(self, struttura, scadenze, global_cfg):
    """Costruisce e invia l'email digest."""
    from models import get_struttura_config
    sid = struttura['id']

    smtp_host = get_struttura_config(sid, 'smtp_host') or global_cfg.get('smtp_host', '')
    smtp_port = int(get_struttura_config(sid, 'smtp_port') or global_cfg.get('smtp_port', 587))
    smtp_user = get_struttura_config(sid, 'smtp_user') or global_cfg.get('smtp_user', '')
    smtp_pass = get_struttura_config(sid, 'smtp_password_encrypted') or global_cfg.get('smtp_password', '')
    smtp_from = get_struttura_config(sid, 'smtp_from') or smtp_user
    use_tls   = (get_struttura_config(sid, 'smtp_use_tls') or '1') == '1'

    if not smtp_host or not smtp_user:
        logger.warning(f"SMTP non configurato per struttura {struttura['nome']}")
        return

    # Corpo email (testo semplice)
    righe = [f"Scadenzario — {struttura['nome']}", "=" * 40, ""]
    priorita_labels = {
        'scaduto': '🔴 SCADUTO',
        'urgente': '🟠 URGENTE (≤7gg)',
        'attenzione': '🟡 ATTENZIONE (≤15gg)',
        'avviso': '🔵 AVVISO (≤30gg)',
    }
    for priorita, label in priorita_labels.items():
        gruppo = [s for s in scadenze if s['priorita'] == priorita]
        if gruppo:
            righe.append(f"\n{label}")
            righe.append("-" * 30)
            for s in gruppo:
                righe.append(
                    f"  {s['descrizione'] or s['marca'] + ' ' + s['modello']} "
                    f"(mat. {s['matricola']}) — {s['divisione_nome']} — "
                    f"scade: {s['prossima_scadenza']} ({s['giorni_rimasti']} gg)"
                )

    corpo = "\n".join(righe)

    try:
        msg = MIMEMultipart()
        msg['From'] = smtp_from
        msg['To'] = struttura['email_notifiche']
        msg['Subject'] = f"Scadenzario {struttura['nome']} — {datetime.now().strftime('%d/%m/%Y')}"
        msg.attach(MIMEText(corpo, 'plain', 'utf-8'))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if use_tls:
                server.starttls()
            if smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, struttura['email_notifiche'], msg.as_string())
        logger.info(f"Digest inviato a {struttura['email_notifiche']} ({struttura['nome']})")
    except Exception as e:
        logger.error(f"Errore invio digest {struttura['nome']}: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add scheduler.py
git commit -m "feat: digest email scadenze per-struttura nello scheduler"
```

---

## FASE D — Funzioni Avanzate (Tasks 10–13)

---

### Task 10: QR code apparecchi

**File:**
- Modifica: `requirements.txt`
- Modifica: `apparecchi.py`
- Modifica: `templates/apparecchi/detail.html`

- [ ] **Step 1: Aggiungere dipendenza**

In `requirements.txt`, aggiungere:
```
qrcode[pil]>=7.4
```

Installare:
```bash
pip install "qrcode[pil]"
```

- [ ] **Step 2: Aggiungere route QR code in `apparecchi.py`**

```python
@apparecchi_bp.route('/<int:apparecchio_id>/qr')
@login_required
@modalita_avanzata_required
def qr_code(apparecchio_id):
    """Genera e restituisce il QR code PNG per l'apparecchio."""
    import qrcode
    import io
    from flask import send_file, request as req

    apparecchio = query_one("SELECT * FROM apparecchi WHERE id = ?", (apparecchio_id,))
    if not apparecchio:
        abort(404)

    # URL assoluto alla scheda apparecchio
    url = req.host_url.rstrip('/') + url_for('apparecchi.dettaglio', apparecchio_id=apparecchio_id)

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)

    nome_file = f"qr_{apparecchio['matricola']}.png"
    return send_file(buf, mimetype='image/png',
                     as_attachment=True, download_name=nome_file)
```

Aggiungere anche import in cima al file:
```python
from auth import modalita_avanzata_required
```

- [ ] **Step 3: Aggiungere pulsante QR nella scheda apparecchio**

In `templates/apparecchi/detail.html`, nel gruppo pulsanti azioni, aggiungere:
```html
{% if g_struttura_modalita == 'ingegneria_clinica' %}
<a href="{{ url_for('apparecchi.qr_code', apparecchio_id=apparecchio.id) }}"
   class="btn btn-outline-secondary btn-sm"
   title="Scarica QR Code">
  <i class="bi bi-qr-code me-1"></i>QR Code
</a>
{% endif %}
```

- [ ] **Step 4: Verifica manuale**

```bash
python app.py
```
- Aprire la scheda di un apparecchio in modalità ingegneria_clinica
- Cliccare "QR Code" → download di un PNG
- Aprire il PNG → scannerizzare con un telefono → reindirizza alla pagina corretta

- [ ] **Step 5: Commit**

```bash
git add requirements.txt apparecchi.py templates/apparecchi/detail.html
git commit -m "feat: generazione QR code per apparecchi (modalita ingegneria_clinica)"
```

---

### Task 11: API REST /api/v1

**File:**
- Crea: `api_bp.py`
- Modifica: `admin.py` (UI gestione token)
- Crea: `templates/strutture/api_tokens.html`

- [ ] **Step 1: Creare `api_bp.py`**

```python
"""
MedInventory - REST API v1
Autenticazione: Bearer token (tabella api_tokens).
Tutti gli endpoint sono scoped alla struttura del token.
"""

import hashlib
import secrets
from functools import wraps

from flask import Blueprint, request, jsonify, g, current_app
from models import query_one, query_all, execute

api_bp = Blueprint('api', __name__, url_prefix='/api/v1')


def _token_auth(scope='read'):
    """Decorator: verifica token Bearer e popola g.api_struttura_id."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            auth = request.headers.get('Authorization', '')
            if not auth.startswith('Bearer '):
                return jsonify({'errore': 'Token mancante'}), 401
            raw_token = auth[7:]
            token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

            token = query_one("""
                SELECT t.*, s.id as sid, s.nome as struttura_nome
                FROM api_tokens t
                JOIN strutture s ON s.id = t.struttura_id
                WHERE t.token_hash = ? AND t.attivo = 1
                  AND (t.scadenza IS NULL OR t.scadenza >= date('now'))
                  AND s.attiva = 1
            """, (token_hash,))

            if not token:
                return jsonify({'errore': 'Token non valido o scaduto'}), 401

            if scope == 'write' and 'write' not in (token['scopes'] or ''):
                return jsonify({'errore': 'Permessi insufficienti'}), 403

            # Aggiorna ultimo utilizzo (non bloccante)
            try:
                execute(
                    "UPDATE api_tokens SET ultimo_utilizzo=CURRENT_TIMESTAMP WHERE id=?",
                    (token['id'],)
                )
            except Exception:
                pass

            g.api_struttura_id = token['sid']
            g.api_struttura_nome = token['struttura_nome']
            return f(*args, **kwargs)
        return decorated
    return decorator


def _pagina(query_result, page, per_page, total):
    return {
        'dati': query_result,
        'paginazione': {'pagina': page, 'per_pagina': per_page, 'totale': total},
    }


@api_bp.route('/apparecchi')
@_token_auth('read')
def lista_apparecchi():
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 200)
    offset = (page - 1) * per_page

    total = query_one(
        "SELECT COUNT(*) as c FROM apparecchi WHERE struttura_id=? AND stato!='dismesso'",
        (g.api_struttura_id,)
    )['c']

    apparecchi = query_all("""
        SELECT a.id, a.descrizione, a.marca, a.modello, a.matricola,
               a.numero_inventario, a.stato, a.ubicazione,
               d.nome as divisione
        FROM apparecchi a
        JOIN divisioni d ON d.id = a.divisione_id
        WHERE a.struttura_id = ? AND a.stato != 'dismesso'
        ORDER BY a.marca, a.modello
        LIMIT ? OFFSET ?
    """, (g.api_struttura_id, per_page, offset))

    return jsonify(_pagina(apparecchi, page, per_page, total))


@api_bp.route('/apparecchi/<int:apparecchio_id>')
@_token_auth('read')
def dettaglio_apparecchio(apparecchio_id):
    app_ = query_one("""
        SELECT a.*, d.nome as divisione
        FROM apparecchi a
        JOIN divisioni d ON d.id = a.divisione_id
        WHERE a.id = ? AND a.struttura_id = ?
    """, (apparecchio_id, g.api_struttura_id))
    if not app_:
        return jsonify({'errore': 'Apparecchio non trovato'}), 404
    return jsonify(dict(app_))


@api_bp.route('/scadenze')
@_token_auth('read')
def scadenze():
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 200)
    offset = (page - 1) * per_page

    total = query_one("""
        SELECT COUNT(*) as c FROM prossime_scadenze ps
        JOIN apparecchi a ON a.id = ps.apparecchio_id
        WHERE a.struttura_id = ?
    """, (g.api_struttura_id,))['c']

    rows = query_all("""
        SELECT ps.apparecchio_id, ps.descrizione, ps.marca, ps.modello, ps.matricola,
               ps.tipo_manutenzione, ps.prossima_scadenza, ps.giorni_rimasti, ps.priorita
        FROM prossime_scadenze ps
        JOIN apparecchi a ON a.id = ps.apparecchio_id
        WHERE a.struttura_id = ?
        ORDER BY ps.prossima_scadenza
        LIMIT ? OFFSET ?
    """, (g.api_struttura_id, per_page, offset))

    return jsonify(_pagina(rows, page, per_page, total))


@api_bp.route('/manutenzioni')
@_token_auth('read')
def lista_manutenzioni():
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 200)
    offset = (page - 1) * per_page

    total = query_one("""
        SELECT COUNT(*) as c FROM manutenzioni m
        JOIN apparecchi a ON a.id = m.apparecchio_id
        WHERE a.struttura_id = ?
    """, (g.api_struttura_id,))['c']

    rows = query_all("""
        SELECT m.id, m.tipo, m.data_intervento, m.prossima_scadenza,
               m.tecnico_ditta, m.esito, m.costo,
               a.descrizione as apparecchio, a.matricola
        FROM manutenzioni m
        JOIN apparecchi a ON a.id = m.apparecchio_id
        WHERE a.struttura_id = ?
        ORDER BY m.data_intervento DESC
        LIMIT ? OFFSET ?
    """, (g.api_struttura_id, per_page, offset))

    return jsonify(_pagina(rows, page, per_page, total))


@api_bp.route('/manutenzioni', methods=['POST'])
@_token_auth('write')
def crea_manutenzione():
    data = request.get_json(silent=True) or {}
    required = ('apparecchio_id', 'tipo', 'data_intervento')
    mancanti = [k for k in required if not data.get(k)]
    if mancanti:
        return jsonify({'errore': f'Campi mancanti: {", ".join(mancanti)}'}), 400

    tipi_validi = ('preventiva', 'correttiva', 'verifica', 'calibrazione')
    if data['tipo'] not in tipi_validi:
        return jsonify({'errore': f'tipo deve essere uno di: {tipi_validi}'}), 400

    # Verifica che l'apparecchio appartenga alla struttura del token
    app_ = query_one(
        "SELECT id FROM apparecchi WHERE id=? AND struttura_id=?",
        (data['apparecchio_id'], g.api_struttura_id)
    )
    if not app_:
        return jsonify({'errore': 'Apparecchio non trovato nella struttura'}), 404

    cur = execute("""
        INSERT INTO manutenzioni
            (apparecchio_id, tipo, data_intervento, prossima_scadenza,
             periodicita_giorni, tecnico_ditta, descrizione, esito, costo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data['apparecchio_id'], data['tipo'], data['data_intervento'],
        data.get('prossima_scadenza'), data.get('periodicita_giorni'),
        data.get('tecnico_ditta'), data.get('descrizione'),
        data.get('esito'), data.get('costo'),
    ))

    return jsonify({'id': cur.lastrowid, 'messaggio': 'Manutenzione creata'}), 201
```

- [ ] **Step 2: UI gestione token in `admin.py` (o `strutture_bp.py`)**

Aggiungere a `strutture_bp.py`:

```python
@strutture_bp.route('/<int:struttura_id>/tokens')
@superadmin_required
def api_tokens(struttura_id):
    struttura = query_one("SELECT * FROM strutture WHERE id=?", (struttura_id,))
    if not struttura:
        abort(404)
    tokens = query_all(
        "SELECT * FROM api_tokens WHERE struttura_id=? ORDER BY created_at DESC",
        (struttura_id,)
    )
    return render_template('strutture/api_tokens.html', struttura=struttura, tokens=tokens)


@strutture_bp.route('/<int:struttura_id>/tokens/nuovo', methods=['POST'])
@superadmin_required
def nuovo_token(struttura_id):
    import hashlib, secrets
    nome = request.form.get('nome', '').strip()
    scopes = request.form.get('scopes', 'read')
    scadenza = request.form.get('scadenza') or None

    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()

    execute(
        """INSERT INTO api_tokens (struttura_id, nome, token_hash, scopes, scadenza, created_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (struttura_id, nome, token_hash, scopes, scadenza, g.user['id'])
    )
    flash(f'Token creato. Copia ora — non sarà più visibile: {raw}', 'warning')
    return redirect(url_for('strutture.api_tokens', struttura_id=struttura_id))


@strutture_bp.route('/<int:struttura_id>/tokens/<int:token_id>/revoca', methods=['POST'])
@superadmin_required
def revoca_token(struttura_id, token_id):
    execute(
        "UPDATE api_tokens SET attivo=0 WHERE id=? AND struttura_id=?",
        (token_id, struttura_id)
    )
    flash('Token revocato.', 'success')
    return redirect(url_for('strutture.api_tokens', struttura_id=struttura_id))
```

- [ ] **Step 3: Creare `templates/strutture/api_tokens.html`**

Template con: tabella dei token (nome, scopi, scadenza, ultimo utilizzo, stato), form per creare nuovo token, pulsante revoca.

- [ ] **Step 4: Verifica manuale**

```bash
python app.py
# In un altro terminale:
curl -H "Authorization: Bearer TOKEN_COPIATO" http://localhost:5000/api/v1/apparecchi
# Atteso: JSON con lista apparecchi e paginazione
curl -H "Authorization: Bearer TOKEN_SBAGLIATO" http://localhost:5000/api/v1/apparecchi
# Atteso: 401 {"errore": "Token non valido o scaduto"}
```

- [ ] **Step 5: Commit**

```bash
git add api_bp.py strutture_bp.py templates/strutture/api_tokens.html
git commit -m "feat: REST API /api/v1 con token Bearer e 5 endpoint"
```

---

### Task 12: Audit log avanzato

**File:**
- Modifica: `admin.py`
- Modifica: `templates/admin/log.html` (o creare se non esiste)

- [ ] **Step 1: Aggiornare la route log in `admin.py`**

```python
@admin_bp.route('/log')
@admin_required
def log_attivita_view():
    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    # Filtri
    utente_id = request.args.get('utente_id', type=int)
    entita = request.args.get('entita', '')
    data_da = request.args.get('data_da', '')
    data_a = request.args.get('data_a', '')

    where = []
    params = []

    # Scope struttura (superadmin vede tutto, admin vede solo la propria)
    if g.user['ruolo'] != 'superadmin':
        struttura_id = g.user.get('struttura_id') or getattr(g, 'struttura_id', None)
        where.append("l.struttura_id = ?")
        params.append(struttura_id)

    if utente_id:
        where.append("l.utente_id = ?")
        params.append(utente_id)
    if entita:
        where.append("l.entita = ?")
        params.append(entita)
    if data_da:
        where.append("l.created_at >= ?")
        params.append(data_da)
    if data_a:
        where.append("l.created_at <= ?")
        params.append(data_a + ' 23:59:59')

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    total = query_one(
        f"SELECT COUNT(*) as c FROM log_attivita l {where_sql}", params
    )['c']

    logs = query_all(f"""
        SELECT l.*, u.nome || ' ' || u.cognome as utente_nome
        FROM log_attivita l
        LEFT JOIN utenti u ON u.id = l.utente_id
        {where_sql}
        ORDER BY l.created_at DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])

    utenti = query_all("SELECT id, nome, cognome FROM utenti WHERE attivo=1 ORDER BY cognome")
    entita_list = query_all("SELECT DISTINCT entita FROM log_attivita ORDER BY entita")

    # Export CSV
    if request.args.get('export') == 'csv':
        import csv, io
        from flask import Response
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=[
            'created_at','utente_nome','azione','entita','entita_id','dettagli','ip_address'
        ])
        writer.writeheader()
        all_logs = query_all(f"""
            SELECT l.created_at, u.nome || ' ' || u.cognome as utente_nome,
                   l.azione, l.entita, l.entita_id, l.dettagli, l.ip_address
            FROM log_attivita l LEFT JOIN utenti u ON u.id = l.utente_id
            {where_sql} ORDER BY l.created_at DESC
        """, params)
        writer.writerows(all_logs)
        return Response(
            output.getvalue(), mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=log_attivita.csv'}
        )

    total_pages = (total + per_page - 1) // per_page
    return render_template('admin/log.html',
                           logs=logs, utenti=utenti, entita_list=entita_list,
                           page=page, total_pages=total_pages, total=total,
                           filtri={'utente_id': utente_id, 'entita': entita,
                                   'data_da': data_da, 'data_a': data_a})
```

- [ ] **Step 2: Aggiornare `templates/admin/log.html`**

Aggiungere:
- Form filtri (utente, entità, data da/a) con GET
- Pulsante "Esporta CSV"
- Paginazione
- Tabella con colonne: Data/ora, Utente, Azione, Entità, ID, Dettagli, IP

- [ ] **Step 3: Commit**

```bash
git add admin.py templates/admin/log.html
git commit -m "feat: audit log avanzato con filtri, paginazione ed export CSV"
```

---

### Task 13: Report schedulati

**File:**
- Modifica: `scheduler.py`
- Modifica: `export_bp.py`

- [ ] **Step 1: Aggiungere task report schedulati allo scheduler**

In `scheduler.py`, nel metodo `start()`, aggiungere un nuovo task:

```python
{
    'name': 'report_schedulati',
    'func': self._send_scheduled_reports,
    'interval': 3600,   # controlla ogni ora
    'last_run': 0,
},
```

Implementare `_send_scheduled_reports`:

```python
def _send_scheduled_reports(self):
    """Invia report periodici PDF/Excel alle strutture con report_schedulato_attivo=1."""
    with self.app.app_context():
        from models import query_all, get_struttura_config
        strutture = query_all("SELECT * FROM strutture WHERE attiva=1")
        global_cfg = self.app.config.get('APP_CONFIG', {})

        for struttura in strutture:
            sid = struttura['id']
            if get_struttura_config(sid, 'report_schedulato_attivo', '0') != '1':
                continue
            frequenza = get_struttura_config(sid, 'report_frequenza', 'settimanale')
            if not self._is_digest_due(frequenza):
                continue

            try:
                self._genera_e_invia_report(struttura, global_cfg)
            except Exception as e:
                logger.error(f"Errore report struttura {struttura['nome']}: {e}")


def _genera_e_invia_report(self, struttura, global_cfg):
    """Genera PDF scadenzario e lo invia via email alla struttura."""
    from export_service import genera_report_scadenze_pdf
    import tempfile, os

    sid = struttura['id']
    email_dest = struttura.get('email_notifiche')
    if not email_dest:
        return

    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        genera_report_scadenze_pdf(struttura_id=sid, output_path=tmp_path)
        self._invia_pdf_allegato(struttura, tmp_path, global_cfg)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
```

- [ ] **Step 2: Aggiungere `_invia_pdf_allegato` in `scheduler.py`**

```python
def _invia_pdf_allegato(self, struttura, pdf_path, global_cfg):
    """Invia il PDF come allegato email alla struttura."""
    import smtplib, os
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication
    from models import get_struttura_config

    sid = struttura['id']
    smtp_host = get_struttura_config(sid, 'smtp_host') or global_cfg.get('smtp_host', '')
    smtp_port = int(get_struttura_config(sid, 'smtp_port') or global_cfg.get('smtp_port', 587))
    smtp_user = get_struttura_config(sid, 'smtp_user') or global_cfg.get('smtp_user', '')
    smtp_pass = get_struttura_config(sid, 'smtp_password_encrypted') or global_cfg.get('smtp_password', '')
    smtp_from = get_struttura_config(sid, 'smtp_from') or smtp_user
    use_tls   = (get_struttura_config(sid, 'smtp_use_tls') or '1') == '1'

    if not smtp_host or not smtp_user or not struttura.get('email_notifiche'):
        logger.warning(f"SMTP non configurato per struttura {struttura['nome']}")
        return

    msg = MIMEMultipart()
    msg['From'] = smtp_from
    msg['To'] = struttura['email_notifiche']
    msg['Subject'] = f"Report scadenze {struttura['nome']} — {datetime.now().strftime('%d/%m/%Y')}"
    msg.attach(MIMEText("In allegato il report periodico delle scadenze.", 'plain', 'utf-8'))

    with open(pdf_path, 'rb') as f:
        attach = MIMEApplication(f.read(), _subtype='pdf')
        attach.add_header('Content-Disposition', 'attachment',
                          filename=f"scadenze_{struttura['codice']}.pdf")
        msg.attach(attach)

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if use_tls:
                server.starttls()
            if smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, struttura['email_notifiche'], msg.as_string())
        logger.info(f"Report PDF inviato a {struttura['email_notifiche']} ({struttura['nome']})")
    except Exception as e:
        logger.error(f"Errore invio PDF {struttura['nome']}: {e}")
```

- [ ] **Step 3: Aggiungere `genera_report_scadenze_pdf` in `export_service.py`**

```python
def genera_report_scadenze_pdf(struttura_id, output_path):
    """Genera un PDF con lo scadenzario per struttura e lo salva in output_path."""
    from models import query_all, query_one
    from fpdf import FPDF
    from datetime import datetime

    struttura = query_one("SELECT * FROM strutture WHERE id=?", (struttura_id,))
    scadenze = query_all("""
        SELECT ps.*, a.matricola, d.nome as divisione_nome
        FROM prossime_scadenze ps
        JOIN apparecchi a ON a.id = ps.apparecchio_id
        JOIN divisioni d ON d.id = ps.divisione_id
        WHERE d.struttura_id = ?
          AND ps.priorita IN ('scaduto','urgente','attenzione','avviso')
        ORDER BY ps.priorita, ps.prossima_scadenza
    """, (struttura_id,))

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font('Helvetica', 'B', 16)
    pdf.cell(0, 10, f"Scadenzario — {struttura['nome']}", ln=True)
    pdf.set_font('Helvetica', '', 10)
    pdf.cell(0, 6, f"Generato il {datetime.now().strftime('%d/%m/%Y %H:%M')}", ln=True)
    pdf.ln(4)

    colori = {'scaduto': (220,53,69), 'urgente': (255,140,0),
              'attenzione': (255,193,7), 'avviso': (13,110,253)}

    for s in scadenze:
        r, g_, b = colori.get(s['priorita'], (0,0,0))
        pdf.set_text_color(r, g_, b)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.cell(0, 5, f"[{s['priorita'].upper()}] {s['descrizione'] or s['marca']+' '+s['modello']} — {s['divisione_nome']}", ln=True)
        pdf.set_text_color(0,0,0)
        pdf.set_font('Helvetica', '', 9)
        pdf.cell(0, 5, f"  Mat: {s['matricola']} | Scade: {s['prossima_scadenza']} ({s['giorni_rimasti']} gg) | Tipo: {s['tipo_manutenzione']}", ln=True)

    pdf.output(output_path)
```

- [ ] **Step 4: Aggiungere pulsante "Esci da tutti i dispositivi" nel template profilo**

In `templates/auth/profilo.html` (o equivalente), aggiungere:
```html
<form method="post" action="{{ url_for('auth.logout_ovunque') }}"
      onsubmit="return confirm('Revocare tutte le sessioni attive?')">
  <button class="btn btn-outline-danger btn-sm">
    <i class="bi bi-box-arrow-right me-1"></i>Esci da tutti i dispositivi
  </button>
</form>
```

- [ ] **Step 5: Commit**

```bash
git add scheduler.py export_service.py export_bp.py templates/
git commit -m "feat: report schedulati PDF per-struttura nello scheduler"
```

---

## Verifica Finale

- [ ] **Eseguire la migrazione sul DB di sviluppo**

```bash
python migrate_v2_0.py
# Atteso: "Migrazione v2.0 completata con successo"
```

- [ ] **Avviare l'app e verificare la compatibilità v1.x**

```bash
# Con single_struttura: true in config.local.json
python app.py
# - Login funziona come prima
# - Nessuna voce "Strutture" nel menu
# - Apparecchi, manutenzioni, verifiche funzionano normalmente
# - Nessun errore nei log
```

- [ ] **Testare multi-struttura**

```bash
# Impostare single_struttura: false in config.local.json
# Creare un utente superadmin via seed.py o SQL diretto:
python -c "
import sqlite3
from werkzeug.security import generate_password_hash
db = sqlite3.connect('data/database.sqlite')
db.execute(\"INSERT INTO utenti (email, password_hash, nome, cognome, ruolo) VALUES ('super@test.local', ?, 'Super', 'Admin', 'superadmin')\", (generate_password_hash('admin123'),))
db.commit()
db.close()
print('Superadmin creato: super@test.local / admin123')
"
python app.py
# - Login come superadmin → dashboard superadmin
# - Creare una seconda struttura → appare nella lista
# - Entrare nella struttura → breadcrumb visibile
# - QR code visibile in modalità ingegneria_clinica
# - API /api/v1/apparecchi con token → risponde con JSON
```

- [ ] **Commit finale di versione**

```bash
git add -A
git commit -m "feat: MedInventory v2.0.0 — multi-struttura Fase 1 completa"
```
