"""
MedInventory - Migrazione v1.1
Script standalone da eseguire una sola volta prima di avviare l'app v1.1.

Operazioni:
1. Crea tabella `verifiche` + indici (idempotente)
2. Ricrea `import_history` con CHECK aggiornato (rename-copy pattern)
3. Ricrea vista `prossime_scadenze` con UNION manutenzioni + verifiche

Uso: python migrate_v1_1.py
"""

import sqlite3
import os
import sys
import shutil
from datetime import datetime

# --- Localizza il DB ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

try:
    import json
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)
    DB_PATH = os.path.join(BASE_DIR, config.get('database_path', 'data/database.sqlite'))
except Exception:
    DB_PATH = os.path.join(BASE_DIR, 'data', 'database.sqlite')

if not os.path.exists(DB_PATH):
    print(f"ERRORE: Database non trovato: {DB_PATH}")
    sys.exit(1)

print(f"Database: {DB_PATH}")

# --- Backup preventivo ---
backup_path = DB_PATH + f".bak_v1_1_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
shutil.copy2(DB_PATH, backup_path)
print(f"Backup creato: {backup_path}")

conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA foreign_keys = OFF")
conn.execute("PRAGMA journal_mode = WAL")

try:
    # ------------------------------------------------------------------ #
    # 1. Tabella verifiche                                                 #
    # ------------------------------------------------------------------ #
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    if 'verifiche' not in tables:
        print("Creazione tabella verifiche...")
        conn.executescript("""
CREATE TABLE IF NOT EXISTS verifiche (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  apparecchio_id INTEGER NOT NULL,
  data_verifica DATE NOT NULL,
  prossima_scadenza DATE,
  periodicita_giorni INTEGER DEFAULT 365,
  esito TEXT NOT NULL CHECK(esito IN ('positivo', 'negativo', 'con_riserva')),
  tecnico_ditta TEXT,
  note TEXT,
  documento_path TEXT,
  created_by INTEGER,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (apparecchio_id) REFERENCES apparecchi(id) ON DELETE CASCADE,
  FOREIGN KEY (created_by) REFERENCES utenti(id)
);
CREATE INDEX IF NOT EXISTS idx_verifiche_apparecchio ON verifiche(apparecchio_id);
CREATE INDEX IF NOT EXISTS idx_verifiche_scadenza ON verifiche(prossima_scadenza);
CREATE INDEX IF NOT EXISTS idx_verifiche_data ON verifiche(data_verifica);
        """)
        print("  OK: tabella verifiche creata.")
    else:
        print("  SKIP: tabella verifiche già esistente.")

    # ------------------------------------------------------------------ #
    # 2. Ricrea import_history con CHECK aggiornato                        #
    # ------------------------------------------------------------------ #
    # Controlla il CHECK attuale
    create_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='import_history'"
    ).fetchone()

    needs_update = False
    if create_sql and 'verifica_elettrica' not in create_sql[0]:
        needs_update = True

    if needs_update:
        print("Aggiornamento CHECK in import_history...")
        conn.executescript("""
ALTER TABLE import_history RENAME TO import_history_old;

CREATE TABLE import_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tipo_import TEXT NOT NULL CHECK(tipo_import IN ('inventario', 'verbale_email', 'verifica_elettrica')),
  filename TEXT NOT NULL,
  filepath TEXT NOT NULL,
  tipo_documento TEXT,
  divisione_id INTEGER,
  email_from TEXT,
  email_subject TEXT,
  totale_righe INTEGER,
  righe_importate INTEGER,
  righe_errori INTEGER,
  stato TEXT CHECK(stato IN ('pending', 'processing', 'completed', 'failed')),
  ai_prompt TEXT,
  ai_response TEXT,
  errori_dettaglio TEXT,
  imported_by INTEGER,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  completed_at DATETIME,
  FOREIGN KEY (divisione_id) REFERENCES divisioni(id),
  FOREIGN KEY (imported_by) REFERENCES utenti(id)
);

INSERT INTO import_history SELECT * FROM import_history_old;
DROP TABLE import_history_old;

CREATE INDEX IF NOT EXISTS idx_import_history_tipo ON import_history(tipo_import);
CREATE INDEX IF NOT EXISTS idx_import_history_stato ON import_history(stato);
CREATE INDEX IF NOT EXISTS idx_import_history_created ON import_history(created_at);
        """)
        print("  OK: import_history aggiornato.")
    else:
        print("  SKIP: import_history già aggiornato.")

    # ------------------------------------------------------------------ #
    # 3. Ricrea vista prossime_scadenze con UNION                          #
    # ------------------------------------------------------------------ #
    print("Ricreazione vista prossime_scadenze...")
    conn.executescript("""
DROP VIEW IF EXISTS prossime_scadenze;

CREATE VIEW prossime_scadenze AS
SELECT
  a.id AS apparecchio_id,
  a.divisione_id, a.codice_interno, a.marca, a.modello, a.matricola, a.ubicazione,
  m.id AS manutenzione_id,
  m.id AS record_id,
  'manutenzione' AS tipo_record,
  m.tipo AS tipo_manutenzione,
  m.prossima_scadenza,
  CAST((julianday(m.prossima_scadenza) - julianday('now')) AS INTEGER) AS giorni_rimasti,
  CASE
    WHEN julianday(m.prossima_scadenza) - julianday('now') < 0  THEN 'scaduto'
    WHEN julianday(m.prossima_scadenza) - julianday('now') <= 7  THEN 'urgente'
    WHEN julianday(m.prossima_scadenza) - julianday('now') <= 15 THEN 'attenzione'
    WHEN julianday(m.prossima_scadenza) - julianday('now') <= 30 THEN 'avviso'
    ELSE 'ok'
  END AS priorita
FROM apparecchi a
INNER JOIN manutenzioni m ON a.id = m.apparecchio_id
WHERE a.stato != 'dismesso' AND m.prossima_scadenza IS NOT NULL

UNION ALL

SELECT
  a.id AS apparecchio_id,
  a.divisione_id, a.codice_interno, a.marca, a.modello, a.matricola, a.ubicazione,
  NULL AS manutenzione_id,
  v.id AS record_id,
  'verifica' AS tipo_record,
  'verifica_elettrica' AS tipo_manutenzione,
  v.prossima_scadenza,
  CAST((julianday(v.prossima_scadenza) - julianday('now')) AS INTEGER) AS giorni_rimasti,
  CASE
    WHEN julianday(v.prossima_scadenza) - julianday('now') < 0  THEN 'scaduto'
    WHEN julianday(v.prossima_scadenza) - julianday('now') <= 7  THEN 'urgente'
    WHEN julianday(v.prossima_scadenza) - julianday('now') <= 15 THEN 'attenzione'
    WHEN julianday(v.prossima_scadenza) - julianday('now') <= 30 THEN 'avviso'
    ELSE 'ok'
  END AS priorita
FROM apparecchi a
INNER JOIN verifiche v ON a.id = v.apparecchio_id
WHERE a.stato != 'dismesso' AND v.prossima_scadenza IS NOT NULL

ORDER BY prossima_scadenza ASC;
    """)
    print("  OK: vista prossime_scadenze ricreata con UNION.")

    conn.commit()
    print("\nMigrazione v1.1 completata con successo.")

except Exception as e:
    conn.rollback()
    print(f"\nERRORE durante la migrazione: {e}")
    print(f"Il database originale è stato preservato. Ripristina da: {backup_path}")
    sys.exit(1)
finally:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()
