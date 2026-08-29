#!/usr/bin/env python3
"""
migrate.py — Strumento unificato di migrazione MedInventory
Analizza il database, mostra la versione corrente e applica le migrazioni necessarie.

Uso:
    python migrate.py              # analisi + applicazione interattiva
    python migrate.py --check      # solo analisi, nessuna modifica
    python migrate.py --yes        # applica senza chiedere conferma
    python migrate.py --db PATH    # specifica il database esplicitamente
"""

import argparse
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime

# DDL delle tabelle impianti, condiviso con models.apply_schema_updates().
# schema_impianti non importa Flask: migrate.py resta eseguibile da solo e
# puo' continuare a puntare con --db a un'altra installazione.
from schema_impianti import (
    DDL_IMPIANTI,
    SCHEMA_VERSION_IMPIANTI,
    tabelle_impianti_presenti,
)

# Su Windows la console non è UTF-8: senza questo, stampare accenti o
# caratteri di riquadro fa fallire lo script con UnicodeEncodeError
# (succede appena l'output viene rediretto su file o log).
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

GREEN  = '\033[92m'
YELLOW = '\033[93m'
RED    = '\033[91m'
CYAN   = '\033[96m'
BOLD   = '\033[1m'
RESET  = '\033[0m'

def _supports_color():
    return hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()

def ok(msg):
    prefix = f'{GREEN}✓{RESET} ' if _supports_color() else '[OK] '
    print(prefix + msg)

def skip(msg):
    prefix = f'{YELLOW}–{RESET} ' if _supports_color() else '[--] '
    print(prefix + msg)

def warn(msg):
    prefix = f'{YELLOW}!{RESET} ' if _supports_color() else '[!!] '
    print(prefix + msg)

def err(msg):
    prefix = f'{RED}✗{RESET} ' if _supports_color() else '[ERR] '
    print(prefix + msg)

def header(msg):
    if _supports_color():
        print(f'\n{BOLD}{CYAN}{msg}{RESET}')
    else:
        print(f'\n=== {msg} ===')

def separator():
    print('─' * 60)

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None

def column_exists(conn, table, column):
    if not table_exists(conn, table):
        return False
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)

def table_sql(conn, name):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row[0] if row else ''

def get_user_version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]

def run_sql(conn, sql, desc=''):
    """Esegue SQL ignorando errori 'già esistente'."""
    try:
        conn.execute(sql)
    except Exception as e:
        if desc:
            skip(f'  {desc}: {e}')

# ---------------------------------------------------------------------------
# Caricamento configurazione e percorso database
# ---------------------------------------------------------------------------

def load_db_path(explicit_path=None):
    base_dir = os.path.dirname(os.path.abspath(__file__))

    if explicit_path:
        return os.path.abspath(explicit_path)

    # Prova config.local.json poi config.json
    for cfg_name in ('config.local.json', 'config.json'):
        cfg_path = os.path.join(base_dir, cfg_name)
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                db = cfg.get('database_path', 'data/database.sqlite')
                if not os.path.isabs(db):
                    db = os.path.join(base_dir, db)
                return db
            except Exception:
                pass

    # Fallback
    return os.path.join(base_dir, 'data', 'database.sqlite')

def load_config():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for cfg_name in ('config.local.json', 'config.json'):
        cfg_path = os.path.join(base_dir, cfg_name)
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
    return {}

# ---------------------------------------------------------------------------
# Definizione migrazioni
# ---------------------------------------------------------------------------
# Ogni migrazione ha:
#   id         — identificatore stringa
#   version    — numero versione (per ordinamento e display)
#   desc       — descrizione breve
#   is_applied — fn(conn) → bool: True se già applicata (idempotente)
#   apply      — fn(conn, config) → None: applica la migrazione

class Migration:
    def __init__(self, mid, version, desc, is_applied, apply):
        self.id = mid
        self.version = version
        self.desc = desc
        self._is_applied = is_applied
        self._apply = apply

    def applied(self, conn):
        try:
            return self._is_applied(conn)
        except Exception:
            return False

    def apply(self, conn, config):
        self._apply(conn, config)


# ============================================================================
# v1.1 — Tabella verifiche + aggiornamento import_history + vista
# ============================================================================

def _applied_v1_1(conn):
    if not table_exists(conn, 'verifiche'):
        return False
    sql = table_sql(conn, 'import_history')
    return sql and 'verifica_elettrica' in sql

def _apply_v1_1(conn, config):
    # 1. Tabella verifiche
    if not table_exists(conn, 'verifiche'):
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
        ok('  tabella verifiche creata')
    else:
        skip('  tabella verifiche già presente')

    # 2. import_history — aggiorna CHECK
    sql = table_sql(conn, 'import_history')
    if sql and 'verifica_elettrica' not in sql:
        conn.executescript("""
ALTER TABLE import_history RENAME TO import_history_old;
CREATE TABLE import_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tipo_import TEXT NOT NULL CHECK(tipo_import IN
      ('inventario', 'verbale_email', 'verbale_manutenzione', 'verifica_elettrica')),
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
        ok('  import_history aggiornato')
    else:
        skip('  import_history già aggiornato')

    # 3. Vista prossime_scadenze (sempre ricreata)
    conn.executescript("""
DROP VIEW IF EXISTS prossime_scadenze;
CREATE VIEW prossime_scadenze AS
SELECT a.id AS apparecchio_id, a.divisione_id,
       a.descrizione, a.marca, a.modello, a.matricola, a.ubicazione,
       m.id AS manutenzione_id, m.id AS record_id,
       'manutenzione' AS tipo_record, m.tipo AS tipo_manutenzione, m.prossima_scadenza,
       CAST((julianday(m.prossima_scadenza) - julianday('now')) AS INTEGER) AS giorni_rimasti,
       CASE WHEN julianday(m.prossima_scadenza) - julianday('now') < 0  THEN 'scaduto'
            WHEN julianday(m.prossima_scadenza) - julianday('now') <= 7  THEN 'urgente'
            WHEN julianday(m.prossima_scadenza) - julianday('now') <= 15 THEN 'attenzione'
            WHEN julianday(m.prossima_scadenza) - julianday('now') <= 30 THEN 'avviso'
            ELSE 'ok' END AS priorita
FROM apparecchi a
INNER JOIN manutenzioni m ON a.id = m.apparecchio_id
WHERE a.stato != 'dismesso' AND m.prossima_scadenza IS NOT NULL
UNION ALL
SELECT a.id AS apparecchio_id, a.divisione_id,
       a.descrizione, a.marca, a.modello, a.matricola, a.ubicazione,
       NULL AS manutenzione_id, v.id AS record_id,
       'verifica' AS tipo_record, 'verifica_elettrica' AS tipo_manutenzione, v.prossima_scadenza,
       CAST((julianday(v.prossima_scadenza) - julianday('now')) AS INTEGER) AS giorni_rimasti,
       CASE WHEN julianday(v.prossima_scadenza) - julianday('now') < 0  THEN 'scaduto'
            WHEN julianday(v.prossima_scadenza) - julianday('now') <= 7  THEN 'urgente'
            WHEN julianday(v.prossima_scadenza) - julianday('now') <= 15 THEN 'attenzione'
            WHEN julianday(v.prossima_scadenza) - julianday('now') <= 30 THEN 'avviso'
            ELSE 'ok' END AS priorita
FROM apparecchi a
INNER JOIN verifiche v ON a.id = v.apparecchio_id
WHERE a.stato != 'dismesso' AND v.prossima_scadenza IS NOT NULL
ORDER BY prossima_scadenza ASC;
    """)
    ok('  vista prossime_scadenze ricreata')
    conn.commit()


# ============================================================================
# v1.2 — descrizione vs codice_interno, accessori, stato da_sostituire
# ============================================================================

def _applied_v1_2(conn):
    return (column_exists(conn, 'apparecchi', 'descrizione') and
            not column_exists(conn, 'apparecchi', 'codice_interno'))

def _apply_v1_2(conn, config):
    if _applied_v1_2(conn):
        skip('  v1.2 già applicata')
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE IF EXISTS apparecchi_new")

    total = conn.execute("SELECT COUNT(*) FROM apparecchi").fetchone()[0]

    has_soggetto = column_exists(conn, 'apparecchi', 'soggetto_verifica')
    has_codice   = column_exists(conn, 'apparecchi', 'codice_interno')
    source_col   = 'codice_interno' if has_codice else 'descrizione'
    soggetto_sel = 'soggetto_verifica' if has_soggetto else '1'

    conn.execute("""
        CREATE TABLE apparecchi_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            divisione_id INTEGER NOT NULL,
            descrizione TEXT,
            matricola TEXT UNIQUE NOT NULL,
            numero_inventario TEXT,
            marca TEXT NOT NULL, modello TEXT NOT NULL,
            anno_fabbricazione INTEGER,
            classificazione TEXT CHECK(classificazione IN ('I','IIa','IIb','III')),
            ubicazione TEXT,
            stato TEXT DEFAULT 'funzionante'
                CHECK(stato IN ('funzionante','in_manutenzione','dismesso','da_sostituire')),
            connesso_rete INTEGER DEFAULT 0,
            ip_address TEXT, mac_address TEXT, hostname TEXT, porta INTEGER,
            protocollo TEXT, url_interfaccia TEXT, fornitore TEXT, codice_fornitore TEXT,
            garanzia_scadenza DATE, contratto_manutenzione TEXT, note TEXT,
            soggetto_verifica INTEGER DEFAULT 1,
            foto_path TEXT, created_by INTEGER, updated_by INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (divisione_id) REFERENCES divisioni(id),
            FOREIGN KEY (created_by) REFERENCES utenti(id),
            FOREIGN KEY (updated_by) REFERENCES utenti(id)
        )
    """)
    conn.execute(f"""
        INSERT INTO apparecchi_new
            (id, divisione_id, descrizione, matricola, numero_inventario,
             marca, modello, anno_fabbricazione, classificazione, ubicazione,
             stato, connesso_rete, ip_address, mac_address, hostname, porta,
             protocollo, url_interfaccia, fornitore, codice_fornitore,
             garanzia_scadenza, contratto_manutenzione, note, soggetto_verifica,
             foto_path, created_by, updated_by, created_at, updated_at)
        SELECT id, divisione_id, {source_col}, matricola, numero_inventario,
               marca, modello, anno_fabbricazione, classificazione, ubicazione,
               stato, connesso_rete, ip_address, mac_address, hostname, porta,
               protocollo, url_interfaccia, fornitore, codice_fornitore,
               garanzia_scadenza, contratto_manutenzione, note, {soggetto_sel},
               foto_path, created_by, updated_by, created_at, updated_at
        FROM apparecchi
    """)

    conn.execute("DROP VIEW IF EXISTS prossime_scadenze")
    conn.execute("DROP TABLE apparecchi")
    conn.execute("ALTER TABLE apparecchi_new RENAME TO apparecchi")

    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_apparecchi_divisione ON apparecchi(divisione_id)",
        "CREATE INDEX IF NOT EXISTS idx_apparecchi_descrizione ON apparecchi(descrizione)",
        "CREATE INDEX IF NOT EXISTS idx_apparecchi_matricola ON apparecchi(matricola)",
        "CREATE INDEX IF NOT EXISTS idx_apparecchi_stato ON apparecchi(stato)",
        "CREATE INDEX IF NOT EXISTS idx_apparecchi_marca ON apparecchi(marca)",
    ]:
        conn.execute(idx)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS accessori (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            apparecchio_id INTEGER NOT NULL,
            descrizione TEXT NOT NULL,
            produttore TEXT, modello TEXT, matricola TEXT,
            created_by INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (apparecchio_id) REFERENCES apparecchi(id) ON DELETE CASCADE,
            FOREIGN KEY (created_by) REFERENCES utenti(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_accessori_apparecchio ON accessori(apparecchio_id)")

    conn.execute("PRAGMA legacy_alter_table = OFF")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    ok(f'  {total} apparecchi migrati (codice_interno → descrizione)')


# ============================================================================
# v1.3 — verbale_path in manutenzioni
# ============================================================================

def _applied_v1_3(conn):
    return column_exists(conn, 'manutenzioni', 'verbale_path')

def _apply_v1_3(conn, config):
    if not _applied_v1_3(conn):
        conn.execute("ALTER TABLE manutenzioni ADD COLUMN verbale_path TEXT")
        conn.commit()
        ok("  colonna verbale_path aggiunta a manutenzioni")

        # Crea cartella uploads/verbali
        base_dir = os.path.dirname(os.path.abspath(__file__))
        verbali_dir = os.path.join(base_dir, 'uploads', 'verbali')
        os.makedirs(verbali_dir, exist_ok=True)
        ok(f'  directory uploads/verbali creata')
    else:
        skip('  verbale_path già presente in manutenzioni')


# ============================================================================
# v1.3.2 — import_history CHECK con verbale_manutenzione
# ============================================================================

def _applied_v1_3_2(conn):
    sql = table_sql(conn, 'import_history')
    return sql and 'verbale_manutenzione' in sql

def _apply_v1_3_2(conn, config):
    if _applied_v1_3_2(conn):
        skip("  import_history già ha 'verbale_manutenzione' nel CHECK")
        return

    conn.executescript("""
ALTER TABLE import_history RENAME TO _import_history_old;
CREATE TABLE import_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo_import TEXT NOT NULL CHECK(tipo_import IN
        ('inventario', 'verbale_email', 'verbale_manutenzione', 'verifica_elettrica')),
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
INSERT INTO import_history SELECT * FROM _import_history_old;
DROP TABLE _import_history_old;
CREATE INDEX IF NOT EXISTS idx_import_history_tipo ON import_history(tipo_import);
CREATE INDEX IF NOT EXISTS idx_import_history_stato ON import_history(stato);
CREATE INDEX IF NOT EXISTS idx_import_history_created ON import_history(created_at);
    """)
    conn.commit()
    ok("  import_history aggiornato con 'verbale_manutenzione'")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    for subdir in ['import', 'verbali', 'verifiche']:
        path = os.path.join(base_dir, 'uploads', subdir)
        os.makedirs(path, exist_ok=True)


# ============================================================================
# v1.4 — UNIQUE(modello, matricola) su apparecchi
# ============================================================================

def _has_single_unique_matricola(conn):
    for idx in conn.execute("PRAGMA index_list(apparecchi)").fetchall():
        if not idx[2]:  # not unique
            continue
        cols = conn.execute(f"PRAGMA index_info({idx[1]})").fetchall()
        if len(cols) == 1 and cols[0][2] == 'matricola':
            return True
    return False

def _applied_v1_4(conn):
    # Applicata se user_version >= 143 o se non c'è più UNIQUE singolo su matricola
    if get_user_version(conn) >= 143:
        return True
    return not _has_single_unique_matricola(conn)

def _apply_v1_4(conn, config):
    if _applied_v1_4(conn):
        skip('  UNIQUE(modello, matricola) già presente')
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE IF EXISTS apparecchi_new")

    total = conn.execute("SELECT COUNT(*) FROM apparecchi").fetchone()[0]
    has_soggetto = column_exists(conn, 'apparecchi', 'soggetto_verifica')
    soggetto_def = "\n                soggetto_verifica INTEGER DEFAULT 1," if has_soggetto else ""
    soggetto_sel = "soggetto_verifica," if has_soggetto else ""
    soggetto_ins = "soggetto_verifica," if has_soggetto else ""

    conn.execute(f"""
        CREATE TABLE apparecchi_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            divisione_id INTEGER NOT NULL,
            descrizione TEXT,
            matricola TEXT NOT NULL,
            numero_inventario TEXT,
            marca TEXT NOT NULL, modello TEXT NOT NULL,
            anno_fabbricazione INTEGER,
            classificazione TEXT CHECK(classificazione IN ('I','IIa','IIb','III')),
            ubicazione TEXT,
            stato TEXT DEFAULT 'funzionante'
                CHECK(stato IN ('funzionante','in_manutenzione','dismesso','da_sostituire')),
            connesso_rete INTEGER DEFAULT 0,
            ip_address TEXT, mac_address TEXT, hostname TEXT, porta INTEGER,
            protocollo TEXT, url_interfaccia TEXT, fornitore TEXT, codice_fornitore TEXT,
            garanzia_scadenza DATE, contratto_manutenzione TEXT, note TEXT,{soggetto_def}
            foto_path TEXT, created_by INTEGER, updated_by INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(modello, matricola),
            FOREIGN KEY (divisione_id) REFERENCES divisioni(id),
            FOREIGN KEY (created_by) REFERENCES utenti(id),
            FOREIGN KEY (updated_by) REFERENCES utenti(id)
        )
    """)
    conn.execute(f"""
        INSERT INTO apparecchi_new
            (id, divisione_id, descrizione, matricola, numero_inventario,
             marca, modello, anno_fabbricazione, classificazione, ubicazione,
             stato, connesso_rete, ip_address, mac_address, hostname, porta,
             protocollo, url_interfaccia, fornitore, codice_fornitore,
             garanzia_scadenza, contratto_manutenzione, note, {soggetto_ins}
             foto_path, created_by, updated_by, created_at, updated_at)
        SELECT id, divisione_id, descrizione, matricola, numero_inventario,
               marca, modello, anno_fabbricazione, classificazione, ubicazione,
               stato, connesso_rete, ip_address, mac_address, hostname, porta,
               protocollo, url_interfaccia, fornitore, codice_fornitore,
               garanzia_scadenza, contratto_manutenzione, note, {soggetto_sel}
               foto_path, created_by, updated_by, created_at, updated_at
        FROM apparecchi
    """)

    conn.execute("DROP VIEW IF EXISTS prossime_scadenze")
    conn.execute("DROP TABLE apparecchi")
    conn.execute("ALTER TABLE apparecchi_new RENAME TO apparecchi")

    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_apparecchi_divisione ON apparecchi(divisione_id)",
        "CREATE INDEX IF NOT EXISTS idx_apparecchi_descrizione ON apparecchi(descrizione)",
        "CREATE INDEX IF NOT EXISTS idx_apparecchi_matricola ON apparecchi(matricola)",
        "CREATE INDEX IF NOT EXISTS idx_apparecchi_stato ON apparecchi(stato)",
        "CREATE INDEX IF NOT EXISTS idx_apparecchi_marca ON apparecchi(marca)",
    ]:
        conn.execute(idx)

    # Ricrea vista
    conn.executescript("""
DROP VIEW IF EXISTS prossime_scadenze;
CREATE VIEW prossime_scadenze AS
SELECT a.id AS apparecchio_id, a.divisione_id,
       a.descrizione, a.marca, a.modello, a.matricola, a.ubicazione,
       m.id AS manutenzione_id, m.id AS record_id,
       'manutenzione' AS tipo_record, m.tipo AS tipo_manutenzione, m.prossima_scadenza,
       CAST((julianday(m.prossima_scadenza) - julianday('now')) AS INTEGER) AS giorni_rimasti,
       CASE WHEN julianday(m.prossima_scadenza) - julianday('now') < 0  THEN 'scaduto'
            WHEN julianday(m.prossima_scadenza) - julianday('now') <= 7  THEN 'urgente'
            WHEN julianday(m.prossima_scadenza) - julianday('now') <= 15 THEN 'attenzione'
            WHEN julianday(m.prossima_scadenza) - julianday('now') <= 30 THEN 'avviso'
            ELSE 'ok' END AS priorita
FROM apparecchi a
INNER JOIN manutenzioni m ON a.id = m.apparecchio_id
WHERE a.stato != 'dismesso' AND m.prossima_scadenza IS NOT NULL
  AND m.id = (SELECT m2.id FROM manutenzioni m2
              WHERE m2.apparecchio_id = m.apparecchio_id AND m2.tipo = m.tipo
              ORDER BY m2.data_intervento DESC, m2.id DESC LIMIT 1)
UNION ALL
SELECT a.id AS apparecchio_id, a.divisione_id,
       a.descrizione, a.marca, a.modello, a.matricola, a.ubicazione,
       NULL AS manutenzione_id, v.id AS record_id,
       'verifica' AS tipo_record, 'verifica_elettrica' AS tipo_manutenzione, v.prossima_scadenza,
       CAST((julianday(v.prossima_scadenza) - julianday('now')) AS INTEGER) AS giorni_rimasti,
       CASE WHEN julianday(v.prossima_scadenza) - julianday('now') < 0  THEN 'scaduto'
            WHEN julianday(v.prossima_scadenza) - julianday('now') <= 7  THEN 'urgente'
            WHEN julianday(v.prossima_scadenza) - julianday('now') <= 15 THEN 'attenzione'
            WHEN julianday(v.prossima_scadenza) - julianday('now') <= 30 THEN 'avviso'
            ELSE 'ok' END AS priorita
FROM apparecchi a
INNER JOIN verifiche v ON a.id = v.apparecchio_id
WHERE a.stato != 'dismesso' AND v.prossima_scadenza IS NOT NULL
  AND v.id = (SELECT v2.id FROM verifiche v2
              WHERE v2.apparecchio_id = v.apparecchio_id
              ORDER BY v2.data_verifica DESC, v2.id DESC LIMIT 1)
ORDER BY prossima_scadenza ASC;
    """)

    conn.execute("PRAGMA legacy_alter_table = OFF")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA user_version = 143")
    conn.commit()
    ok(f'  {total} apparecchi migrati (UNIQUE matricola → UNIQUE(modello,matricola))')


# ============================================================================
# v2.0 — Multi-struttura: strutture, strutture_config, struttura_id ovunque
# ============================================================================

def _applied_v2_0(conn):
    return (table_exists(conn, 'strutture') and
            column_exists(conn, 'log_attivita', 'struttura_id') and
            get_user_version(conn) >= 200)

def _apply_v2_0(conn, config):
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA legacy_alter_table = ON")

    # Tabelle nuove
    for sql, desc in [
        ("""CREATE TABLE IF NOT EXISTS strutture (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          nome TEXT NOT NULL, codice TEXT UNIQUE NOT NULL, descrizione TEXT,
          tipo TEXT DEFAULT 'altro'
              CHECK(tipo IN ('ospedale','clinica_privata','rsa','ambulatorio',
                             'poliambulatorio','laboratorio','altro')),
          indirizzo TEXT, telefono TEXT, email_notifiche TEXT, pec TEXT,
          responsabile TEXT, email_responsabile TEXT,
          codice_fiscale TEXT, partita_iva TEXT,
          data_attivazione DATE, scadenza_contratto DATE, note TEXT,
          modalita TEXT NOT NULL DEFAULT 'standard'
              CHECK(modalita IN ('standard','avanzata')),
          attiva INTEGER DEFAULT 1,
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""", 'strutture'),
        ("""CREATE TABLE IF NOT EXISTS strutture_config (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          struttura_id INTEGER NOT NULL,
          chiave TEXT NOT NULL, valore TEXT,
          UNIQUE(struttura_id, chiave),
          FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE
        )""", 'strutture_config'),
        ("""CREATE TABLE IF NOT EXISTS api_tokens (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          struttura_id INTEGER NOT NULL,
          nome TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
          scopes TEXT DEFAULT 'read',
          ultimo_utilizzo DATETIME, scadenza DATE,
          attivo INTEGER DEFAULT 1, created_by INTEGER,
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE,
          FOREIGN KEY (created_by) REFERENCES utenti(id)
        )""", 'api_tokens'),
        ("""CREATE TABLE IF NOT EXISTS login_attempts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ip_address TEXT NOT NULL, email TEXT,
          esito TEXT NOT NULL CHECK(esito IN ('fallito','bloccato','riuscito')),
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""", 'login_attempts'),
    ]:
        run_sql(conn, sql, desc)

    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_strutture_codice ON strutture(codice)",
        "CREATE INDEX IF NOT EXISTS idx_strutture_attiva ON strutture(attiva)",
        "CREATE INDEX IF NOT EXISTS idx_strutture_config_struttura ON strutture_config(struttura_id)",
        "CREATE INDEX IF NOT EXISTS idx_api_tokens_struttura ON api_tokens(struttura_id)",
        "CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON api_tokens(token_hash)",
        "CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip_address, created_at)",
    ]:
        run_sql(conn, idx)

    conn.commit()

    # Struttura di default
    # structure_name esiste quasi sempre nei config reali, ma vuoto: con
    # config.get(chiave, fallback) il fallback non scatterebbe mai e la
    # struttura verrebbe creata senza nome.
    struttura_nome = ((config.get('structure_name') or '').strip()
                      or (config.get('app_name') or '').strip()
                      or 'Struttura Principale')
    if not conn.execute("SELECT id FROM strutture LIMIT 1").fetchone():
        conn.execute(
            "INSERT INTO strutture (nome, codice, modalita) VALUES (?, ?, 'avanzata')",
            (struttura_nome, 'DEFAULT')
        )
        conn.commit()
        ok(f"  struttura '{struttura_nome}' creata")
    else:
        skip('  struttura di default già presente')

    struttura_id = conn.execute("SELECT id FROM strutture ORDER BY id LIMIT 1").fetchone()[0]

    # struttura_id nelle tabelle
    for table in ('divisioni', 'apparecchi', 'log_attivita'):
        if not column_exists(conn, table, 'struttura_id'):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN struttura_id INTEGER")
            conn.execute(f"UPDATE {table} SET struttura_id = ?", (struttura_id,))
            conn.commit()
            ok(f'  struttura_id aggiunto a {table}')
        else:
            skip(f'  struttura_id già presente in {table}')

    if not column_exists(conn, 'utenti', 'struttura_id'):
        conn.execute("ALTER TABLE utenti ADD COLUMN struttura_id INTEGER")
        conn.execute(
            "UPDATE utenti SET struttura_id = ? WHERE ruolo != 'superadmin'", (struttura_id,)
        )
        conn.commit()
        ok('  struttura_id aggiunto a utenti')
    else:
        skip('  struttura_id già presente in utenti')

    # CHECK ruolo utenti (aggiunge superadmin)
    utenti_sql = table_sql(conn, 'utenti')
    if 'superadmin' not in utenti_sql:
        cols_old = [r[1] for r in conn.execute("PRAGMA table_info(utenti)").fetchall()]
        conn.execute("ALTER TABLE utenti RENAME TO _utenti_old")
        conn.execute("""CREATE TABLE utenti (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
          nome TEXT NOT NULL, cognome TEXT NOT NULL,
          ruolo TEXT NOT NULL CHECK(ruolo IN ('superadmin','admin','utente','tecnico')),
          struttura_id INTEGER, divisione_default_id INTEGER,
          attivo INTEGER DEFAULT 1, primo_accesso INTEGER DEFAULT 1,
          ultimo_accesso DATETIME,
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY (struttura_id) REFERENCES strutture(id),
          FOREIGN KEY (divisione_default_id) REFERENCES divisioni(id)
        )""")
        col_list = ', '.join(c for c in cols_old)
        conn.execute(f"INSERT INTO utenti SELECT {col_list} FROM _utenti_old")
        conn.execute("DROP TABLE _utenti_old")
        for idx in [
            "CREATE INDEX IF NOT EXISTS idx_utenti_email ON utenti(email)",
            "CREATE INDEX IF NOT EXISTS idx_utenti_ruolo ON utenti(ruolo)",
            "CREATE INDEX IF NOT EXISTS idx_utenti_attivo ON utenti(attivo)",
        ]:
            conn.execute(idx)
        conn.commit()
        ok('  tabella utenti ricreata con CHECK ruolo aggiornato')

    # UNIQUE(struttura_id, modello, matricola) su apparecchi
    app_sql = re.sub(r'\s+', ' ', table_sql(conn, 'apparecchi'))
    if ('struttura_id, modello, matricola' not in app_sql and
            'struttura_id,modello,matricola' not in app_sql):
        cols = [r[1] for r in conn.execute("PRAGMA table_info(apparecchi)").fetchall()]
        col_list = ', '.join(cols)
        conn.execute("ALTER TABLE apparecchi RENAME TO _apparecchi_old")
        struttura_col_def = "struttura_id INTEGER NOT NULL," if 'struttura_id' not in cols else ""
        conn.execute(f"""
            CREATE TABLE apparecchi (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              struttura_id INTEGER NOT NULL,
              divisione_id INTEGER NOT NULL,
              descrizione TEXT, matricola TEXT NOT NULL,
              numero_inventario TEXT, marca TEXT NOT NULL, modello TEXT NOT NULL,
              anno_fabbricazione INTEGER,
              classificazione TEXT CHECK(classificazione IN ('I','IIa','IIb','III')),
              ubicazione TEXT,
              stato TEXT DEFAULT 'funzionante'
                   CHECK(stato IN ('funzionante','in_manutenzione','dismesso','da_sostituire')),
              connesso_rete INTEGER DEFAULT 0,
              ip_address TEXT, mac_address TEXT, hostname TEXT, porta INTEGER,
              protocollo TEXT, url_interfaccia TEXT, fornitore TEXT, codice_fornitore TEXT,
              garanzia_scadenza DATE, contratto_manutenzione TEXT, note TEXT,
              foto_path TEXT, soggetto_verifica INTEGER NOT NULL DEFAULT 1,
              created_by INTEGER, updated_by INTEGER,
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(struttura_id, modello, matricola),
              FOREIGN KEY (struttura_id) REFERENCES strutture(id),
              FOREIGN KEY (divisione_id) REFERENCES divisioni(id),
              FOREIGN KEY (created_by) REFERENCES utenti(id),
              FOREIGN KEY (updated_by) REFERENCES utenti(id)
            )
        """)
        # Mappa colonne, gestisce struttura_id assente nel vecchio schema
        src_struttura = 'struttura_id' if 'struttura_id' in cols else str(struttura_id)
        src_soggetto  = 'soggetto_verifica' if 'soggetto_verifica' in cols else '1'
        target_cols = [
            'id','struttura_id','divisione_id','descrizione','matricola',
            'numero_inventario','marca','modello','anno_fabbricazione','classificazione',
            'ubicazione','stato','connesso_rete','ip_address','mac_address','hostname',
            'porta','protocollo','url_interfaccia','fornitore','codice_fornitore',
            'garanzia_scadenza','contratto_manutenzione','note','foto_path',
            'soggetto_verifica','created_by','updated_by','created_at','updated_at'
        ]
        src_exprs = []
        for c in target_cols:
            if c == 'struttura_id':
                src_exprs.append(src_struttura)
            elif c == 'soggetto_verifica':
                src_exprs.append(src_soggetto)
            elif c in cols:
                src_exprs.append(c)
            else:
                src_exprs.append('NULL')
        conn.execute(
            f"INSERT INTO apparecchi ({', '.join(target_cols)}) "
            f"SELECT {', '.join(src_exprs)} FROM _apparecchi_old"
        )
        conn.execute("DROP TABLE _apparecchi_old")
        for idx in [
            "CREATE INDEX IF NOT EXISTS idx_apparecchi_divisione ON apparecchi(divisione_id)",
            "CREATE INDEX IF NOT EXISTS idx_apparecchi_struttura ON apparecchi(struttura_id)",
            "CREATE INDEX IF NOT EXISTS idx_apparecchi_matricola ON apparecchi(matricola)",
        ]:
            conn.execute(idx)
        conn.commit()
        ok('  apparecchi: UNIQUE(struttura_id, modello, matricola)')

    # Modalita: rinomina ingegneria_clinica → avanzata
    strutture_sql = table_sql(conn, 'strutture')
    if 'ingegneria_clinica' in strutture_sql:
        # Ricrea strutture con CHECK aggiornato
        cols_s = [r[1] for r in conn.execute("PRAGMA table_info(strutture)").fetchall()]
        conn.execute("ALTER TABLE strutture RENAME TO _strutture_old")
        conn.execute("""CREATE TABLE strutture (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          nome TEXT NOT NULL, codice TEXT UNIQUE NOT NULL, descrizione TEXT,
          tipo TEXT DEFAULT 'altro'
              CHECK(tipo IN ('ospedale','clinica_privata','rsa','ambulatorio',
                             'poliambulatorio','laboratorio','altro')),
          indirizzo TEXT, telefono TEXT, email_notifiche TEXT, pec TEXT,
          responsabile TEXT, email_responsabile TEXT,
          codice_fiscale TEXT, partita_iva TEXT,
          data_attivazione DATE, scadenza_contratto DATE, note TEXT,
          modalita TEXT NOT NULL DEFAULT 'standard'
              CHECK(modalita IN ('standard','avanzata')),
          attiva INTEGER DEFAULT 1,
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        cols_target = [
            'id','nome','codice','descrizione','tipo','indirizzo','telefono',
            'email_notifiche','pec','responsabile','email_responsabile',
            'codice_fiscale','partita_iva','data_attivazione','scadenza_contratto','note',
            'modalita','attiva','created_at','updated_at'
        ]
        src = []
        for c in cols_target:
            if c == 'modalita':
                if 'modalita' in cols_s:
                    src.append("CASE WHEN modalita='ingegneria_clinica' THEN 'avanzata' ELSE modalita END")
                else:
                    src.append("'standard'")
            elif c in cols_s:
                src.append(c)
            else:
                src.append('NULL')
        conn.execute(
            f"INSERT INTO strutture ({', '.join(cols_target)}) "
            f"SELECT {', '.join(src)} FROM _strutture_old"
        )
        conn.execute("DROP TABLE _strutture_old")
        conn.commit()
        ok("  modalita 'ingegneria_clinica' rinominata in 'avanzata'")

    # Colonne aggiuntive strutture (idempotente)
    for col, col_def in [
        ('tipo',               "TEXT DEFAULT 'altro'"),
        ('telefono',           'TEXT'), ('pec', 'TEXT'),
        ('responsabile',       'TEXT'), ('email_responsabile', 'TEXT'),
        ('codice_fiscale',     'TEXT'), ('partita_iva', 'TEXT'),
        ('data_attivazione',   'DATE'), ('scadenza_contratto', 'DATE'),
        ('note',               'TEXT'),
    ]:
        if not column_exists(conn, 'strutture', col):
            conn.execute(f"ALTER TABLE strutture ADD COLUMN {col} {col_def}")
    conn.commit()

    # Config locale
    base_dir = os.path.dirname(os.path.abspath(__file__))
    local_cfg_path = os.path.join(base_dir, 'config.local.json')
    if os.path.exists(local_cfg_path):
        with open(local_cfg_path, 'r', encoding='utf-8') as f:
            lcfg = json.load(f)
        if 'single_struttura' not in lcfg:
            lcfg['single_struttura'] = True
            with open(local_cfg_path, 'w', encoding='utf-8') as f:
                json.dump(lcfg, f, indent=2, ensure_ascii=False)
            ok('  single_struttura: true aggiunto a config.local.json')

    # Sentinella versione
    data_dir = os.path.join(base_dir, 'data')
    if os.path.isdir(data_dir):
        notice = os.path.join(data_dir, '.version_notice')
        if not os.path.exists(notice):
            with open(notice, 'w', encoding='utf-8') as f:
                json.dump({'old_version': '1.x', 'new_version': '2.0.0',
                           'upgraded_at': datetime.now().strftime('%d/%m/%Y %H:%M')}, f)

    conn.execute("PRAGMA user_version = 200")
    conn.execute("PRAGMA legacy_alter_table = OFF")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA legacy_alter_table = OFF")
    conn.commit()


# ============================================================================
# v2.1 — divisioni e apparecchi: struttura_id NOT NULL, schema corretto
# ============================================================================

def _applied_v2_1(conn):
    # Controlliamo che divisioni abbia NOT NULL su struttura_id
    sql = table_sql(conn, 'divisioni')
    return sql and 'struttura_id INTEGER NOT NULL' in sql.replace('\n', ' ')

def _apply_v2_1(conn, config):
    conn.execute("PRAGMA foreign_keys = OFF")
    # legacy_alter_table=ON: senza questo SQLite 3.26+ riscrive le FK delle
    # tabelle figlie quando si rinomina un padre, facendole puntare a
    # <tabella>_old; dopo il DROP restano FK verso una tabella inesistente
    # e ogni INSERT fallisce con "no such table".
    conn.execute("PRAGMA legacy_alter_table = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    # DIVISIONI
    if not _applied_v2_1(conn):
        conn.execute("ALTER TABLE divisioni RENAME TO divisioni_old")
        conn.execute("""
            CREATE TABLE divisioni (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              nome TEXT NOT NULL, codice TEXT NOT NULL,
              colore TEXT DEFAULT '#0ea5e9', descrizione TEXT,
              attiva INTEGER DEFAULT 1,
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              struttura_id INTEGER NOT NULL,
              UNIQUE(struttura_id, nome),
              UNIQUE(struttura_id, codice),
              FOREIGN KEY (struttura_id) REFERENCES strutture(id)
            )
        """)
        conn.execute("""
            INSERT INTO divisioni
            SELECT id, nome, codice, colore, descrizione, attiva,
                   created_at, updated_at, struttura_id
            FROM divisioni_old
        """)
        conn.execute("DROP TABLE divisioni_old")
        conn.commit()
        ok('  divisioni: struttura_id NOT NULL applicato')
    else:
        skip('  divisioni: schema già aggiornato')

    # APPARECCHI — ricrea con schema v2.1 corretto se necessario
    app_sql = table_sql(conn, 'apparecchi')
    if "struttura_id INTEGER NOT NULL" not in app_sql.replace('\n', ' '):
        cols = [r[1] for r in conn.execute("PRAGMA table_info(apparecchi)").fetchall()]
        col_list = ', '.join(cols)
        conn.execute("ALTER TABLE apparecchi RENAME TO apparecchi_old")
        conn.execute("""
            CREATE TABLE apparecchi (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              divisione_id INTEGER,
              struttura_id INTEGER NOT NULL,
              descrizione TEXT, matricola TEXT NOT NULL,
              numero_inventario TEXT, marca TEXT NOT NULL, modello TEXT NOT NULL,
              anno_fabbricazione INTEGER,
              classificazione TEXT CHECK(classificazione IN ('I','IIa','IIb','III')),
              ubicazione TEXT,
              stato TEXT NOT NULL DEFAULT 'funzionante'
                     CHECK(stato IN ('funzionante','in_manutenzione','da_sostituire','dismesso')),
              soggetto_verifica INTEGER DEFAULT 1, connesso_rete INTEGER DEFAULT 0,
              ip_address TEXT, mac_address TEXT, hostname TEXT, porta INTEGER,
              protocollo TEXT DEFAULT 'http', url_interfaccia TEXT,
              fornitore TEXT, codice_fornitore TEXT, garanzia_scadenza TEXT,
              contratto_manutenzione TEXT, note TEXT, foto_path TEXT,
              created_by INTEGER, updated_by INTEGER,
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(struttura_id, modello, matricola),
              FOREIGN KEY (divisione_id) REFERENCES divisioni(id),
              FOREIGN KEY (struttura_id) REFERENCES strutture(id),
              FOREIGN KEY (created_by) REFERENCES utenti(id),
              FOREIGN KEY (updated_by) REFERENCES utenti(id)
            )
        """)
        conn.execute(f"INSERT INTO apparecchi SELECT {col_list} FROM apparecchi_old")
        conn.execute("DROP TABLE apparecchi_old")
        conn.commit()
        ok('  apparecchi: schema v2.1 applicato')
    else:
        skip('  apparecchi: schema già aggiornato')

    conn.execute("PRAGMA legacy_alter_table = OFF")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


# ============================================================================
# v2.2 — ruolo tecnico + tabella tecnici_strutture
# ============================================================================

def _applied_v2_2(conn):
    return (table_exists(conn, 'tecnici_strutture') and
            'tecnico' in table_sql(conn, 'utenti'))

def _apply_v2_2(conn, config):
    conn.execute("PRAGMA foreign_keys = OFF")
    # legacy_alter_table=ON: senza questo SQLite 3.26+ riscrive le FK delle
    # tabelle figlie quando si rinomina un padre, facendole puntare a
    # <tabella>_old; dopo il DROP restano FK verso una tabella inesistente
    # e ogni INSERT fallisce con "no such table".
    conn.execute("PRAGMA legacy_alter_table = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    # Aggiorna CHECK ruolo utenti per aggiungere 'tecnico'
    utenti_sql = table_sql(conn, 'utenti')
    if "'tecnico'" not in utenti_sql:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(utenti)").fetchall()]
        col_list = ', '.join(cols)
        conn.execute("ALTER TABLE utenti RENAME TO utenti_old")
        conn.execute("""
            CREATE TABLE utenti (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
              nome TEXT NOT NULL, cognome TEXT NOT NULL,
              ruolo TEXT NOT NULL CHECK(ruolo IN ('superadmin','admin','utente','tecnico')),
              divisione_default_id INTEGER,
              attivo INTEGER DEFAULT 1, primo_accesso INTEGER DEFAULT 1,
              ultimo_accesso DATETIME,
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              struttura_id INTEGER,
              FOREIGN KEY (struttura_id) REFERENCES strutture(id),
              FOREIGN KEY (divisione_default_id) REFERENCES divisioni(id)
            )
        """)
        conn.execute(f"INSERT INTO utenti SELECT {col_list} FROM utenti_old")
        conn.execute("DROP TABLE utenti_old")
        conn.commit()
        ok("  utenti: ruolo 'tecnico' aggiunto al CHECK")
    else:
        skip("  utenti: ruolo 'tecnico' già presente")

    if not table_exists(conn, 'tecnici_strutture'):
        conn.execute("""
            CREATE TABLE tecnici_strutture (
              tecnico_id   INTEGER NOT NULL,
              struttura_id INTEGER NOT NULL,
              PRIMARY KEY (tecnico_id, struttura_id),
              FOREIGN KEY (tecnico_id)   REFERENCES utenti(id) ON DELETE CASCADE,
              FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tecnici_strutture_tecnico ON tecnici_strutture(tecnico_id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tecnici_strutture_struttura ON tecnici_strutture(struttura_id)")
        conn.commit()
        ok('  tabella tecnici_strutture creata')
    else:
        skip('  tecnici_strutture già esistente')

    conn.execute("PRAGMA legacy_alter_table = OFF")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


# ============================================================================
# v2.3 — FK ON DELETE CASCADE/SET NULL su divisioni, utenti, email_config
# ============================================================================

def _applied_v2_3(conn):
    div_sql   = table_sql(conn, 'divisioni')   or ''
    utenti_sql = table_sql(conn, 'utenti')     or ''
    email_sql  = table_sql(conn, 'email_config') or ''
    return (
        'ON DELETE CASCADE'  in div_sql   and
        'ON DELETE SET NULL' in utenti_sql and
        'ON DELETE SET NULL' in email_sql
    )


def _apply_v2_3(conn, config):
    conn.execute("PRAGMA foreign_keys = OFF")
    # legacy_alter_table=ON: senza questo SQLite 3.26+ riscrive le FK delle
    # tabelle figlie quando si rinomina un padre, facendole puntare a
    # <tabella>_old; dopo il DROP restano FK verso una tabella inesistente
    # e ogni INSERT fallisce con "no such table".
    conn.execute("PRAGMA legacy_alter_table = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    # DIVISIONI — struttura_id ON DELETE CASCADE
    div_sql = table_sql(conn, 'divisioni') or ''
    if 'ON DELETE CASCADE' not in div_sql:
        conn.execute("ALTER TABLE divisioni RENAME TO divisioni_old")
        conn.execute("""
            CREATE TABLE divisioni (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              nome TEXT NOT NULL, codice TEXT NOT NULL,
              colore TEXT DEFAULT '#0ea5e9', descrizione TEXT,
              attiva INTEGER DEFAULT 1,
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              struttura_id INTEGER NOT NULL,
              UNIQUE(struttura_id, nome),
              UNIQUE(struttura_id, codice),
              FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            INSERT INTO divisioni
            SELECT id, nome, codice, colore, descrizione, attiva,
                   created_at, updated_at, struttura_id
            FROM divisioni_old
        """)
        conn.execute("DROP TABLE divisioni_old")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_divisioni_codice ON divisioni(codice)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_divisioni_attiva ON divisioni(attiva)")
        conn.commit()
        ok('  divisioni: struttura_id ON DELETE CASCADE aggiunto')
    else:
        skip('  divisioni: FK CASCADE già presente')

    # UTENTI — struttura_id + divisione_default_id ON DELETE SET NULL
    utenti_sql = table_sql(conn, 'utenti') or ''
    if 'ON DELETE SET NULL' not in utenti_sql:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(utenti)").fetchall()]
        col_list = ', '.join(cols)
        conn.execute("ALTER TABLE utenti RENAME TO utenti_old")
        conn.execute("""
            CREATE TABLE utenti (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
              nome TEXT NOT NULL, cognome TEXT NOT NULL,
              ruolo TEXT NOT NULL CHECK(ruolo IN ('superadmin','admin','utente','tecnico')),
              divisione_default_id INTEGER,
              attivo INTEGER DEFAULT 1, primo_accesso INTEGER DEFAULT 1,
              ultimo_accesso DATETIME,
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              struttura_id INTEGER,
              FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE SET NULL,
              FOREIGN KEY (divisione_default_id) REFERENCES divisioni(id) ON DELETE SET NULL
            )
        """)
        conn.execute(f"INSERT INTO utenti SELECT {col_list} FROM utenti_old")
        conn.execute("DROP TABLE utenti_old")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_utenti_email ON utenti(email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_utenti_ruolo ON utenti(ruolo)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_utenti_attivo ON utenti(attivo)")
        conn.commit()
        ok('  utenti: struttura_id/divisione_default_id ON DELETE SET NULL aggiunto')
    else:
        skip('  utenti: FK SET NULL già presente')

    # EMAIL_CONFIG — divisione_id ON DELETE SET NULL
    email_sql = table_sql(conn, 'email_config') or ''
    if 'ON DELETE SET NULL' not in email_sql:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(email_config)").fetchall()]
        col_list = ', '.join(cols)
        conn.execute("ALTER TABLE email_config RENAME TO email_config_old")
        conn.execute("""
            CREATE TABLE email_config (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              divisione_id INTEGER,
              email_account TEXT NOT NULL,
              email_password_encrypted TEXT NOT NULL,
              imap_server TEXT NOT NULL,
              imap_port INTEGER DEFAULT 993,
              check_interval_minutes INTEGER DEFAULT 15,
              attivo INTEGER DEFAULT 1,
              ultima_verifica DATETIME,
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY (divisione_id) REFERENCES divisioni(id) ON DELETE SET NULL
            )
        """)
        conn.execute(f"INSERT INTO email_config SELECT {col_list} FROM email_config_old")
        conn.execute("DROP TABLE email_config_old")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_email_config_divisione ON email_config(divisione_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_email_config_attivo ON email_config(attivo)")
        conn.commit()
        ok('  email_config: divisione_id ON DELETE SET NULL aggiunto')
    else:
        skip('  email_config: FK SET NULL già presente')

    conn.execute("PRAGMA legacy_alter_table = OFF")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


# ============================================================================
# v2.7 — Gestione impianti: anagrafica, componenti, scadenze, interventi
# ============================================================================

def _applied_v2_7(conn):
    if not tabelle_impianti_presenti(conn):
        return False
    viste = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'view'"
    ).fetchall()}
    return 'prossime_scadenze_impianti' in viste


def _apply_v2_7(conn, config):
    # Le istruzioni sono idempotenti: quelle gia' applicate (tipicamente le
    # ALTER TABLE su divisioni, su un database gia' avviato con la 2.7)
    # sollevano e vengono saltate, esattamente come in
    # models.apply_schema_updates() a ogni avvio dell'applicazione.
    applicate = saltate = 0
    for sql in DDL_IMPIANTI:
        try:
            conn.execute(sql)
            applicate += 1
        except sqlite3.Error:
            saltate += 1
    conn.commit()
    ok(f'  Impianti: {applicate} istruzioni applicate, {saltate} già presenti')


# ---------------------------------------------------------------------------
# Registro migrazioni in ordine
# ---------------------------------------------------------------------------

MIGRATIONS = [
    Migration('v1.1',   110, 'Tabella verifiche; aggiornamento import_history; vista prossime_scadenze', _applied_v1_1,   _apply_v1_1),
    Migration('v1.2',   120, 'Rinomina codice_interno→descrizione; stato da_sostituire; tabella accessori',_applied_v1_2,   _apply_v1_2),
    Migration('v1.3',   130, 'Colonna verbale_path in manutenzioni',                                       _applied_v1_3,   _apply_v1_3),
    Migration('v1.3.2', 132, "import_history CHECK aggiunto 'verbale_manutenzione'",                       _applied_v1_3_2, _apply_v1_3_2),
    Migration('v1.4',   140, 'UNIQUE(modello, matricola) su apparecchi',                                   _applied_v1_4,   _apply_v1_4),
    Migration('v2.0',   200, 'Multi-struttura: strutture, strutture_config, struttura_id in tutte le tabelle', _applied_v2_0, _apply_v2_0),
    Migration('v2.1',   210, 'divisioni e apparecchi: struttura_id NOT NULL, schema corretto',             _applied_v2_1,   _apply_v2_1),
    Migration('v2.2',   220, "Ruolo 'tecnico' e tabella tecnici_strutture",                                _applied_v2_2,   _apply_v2_2),
    Migration('v2.3',   230, 'FK ON DELETE CASCADE/SET NULL su divisioni, utenti, email_config',          _applied_v2_3,   _apply_v2_3),
    Migration('v2.7',   SCHEMA_VERSION_IMPIANTI,
                             'Impianti: manutentori, impianti e tabelle figlie, vista prossime_scadenze_impianti',
                                                                                                          _applied_v2_7,   _apply_v2_7),
]

# ---------------------------------------------------------------------------
# Analisi database
# ---------------------------------------------------------------------------

def describe_version(conn):
    """Restituisce una descrizione della versione rilevata dal database."""
    uv = get_user_version(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    if 'strutture' in tables and uv >= 200:
        if _applied_v2_3(conn) and _applied_v2_7(conn):
            return 'v2.7 (ultima)', uv
        if _applied_v2_3(conn):
            return 'v2.3', uv
        if _applied_v2_2(conn) and _applied_v2_1(conn):
            return 'v2.2', uv
        if _applied_v2_1(conn):
            return 'v2.1', uv
        return 'v2.0', uv
    if uv >= 143:
        return 'v1.4', uv
    if 'verifiche' in tables:
        if column_exists(conn, 'manutenzioni', 'verbale_path'):
            sql = table_sql(conn, 'import_history')
            if sql and 'verbale_manutenzione' in sql:
                return 'v1.3.2', uv
            return 'v1.3', uv
        return 'v1.2' if not column_exists(conn, 'apparecchi', 'codice_interno') else 'v1.1', uv
    return 'v1.0 (originale)', uv


def analyze(conn):
    """Restituisce (versione_str, user_version, pending_list)."""
    version_str, uv = describe_version(conn)
    pending = [m for m in MIGRATIONS if not m.applied(conn)]
    return version_str, uv, pending


def print_analysis(conn, db_path):
    version_str, uv, pending = analyze(conn)

    header('Analisi database')
    print(f'  Percorso  : {db_path}')
    size_mb = os.path.getsize(db_path) / (1024 * 1024)
    print(f'  Dimensione: {size_mb:.2f} MB')
    print(f'  user_version: {uv}')
    if _supports_color():
        print(f'  Versione rilevata: {BOLD}{version_str}{RESET}')
    else:
        print(f'  Versione rilevata: {version_str}')

    header('Stato migrazioni')
    for m in MIGRATIONS:
        applied = m.applied(conn)
        if _supports_color():
            status = f'{GREEN}applicata{RESET}' if applied else f'{YELLOW}PENDING{RESET}'
        else:
            status = 'applicata' if applied else 'PENDING'
        print(f'  [{m.id:6}] {status:30}  {m.desc}')

    if pending:
        if _supports_color():
            print(f'\n  {BOLD}{len(pending)} migrazione/i da applicare.{RESET}')
        else:
            print(f'\n  {len(pending)} migrazione/i da applicare.')
    else:
        if _supports_color():
            print(f'\n  {GREEN}{BOLD}Database aggiornato — nessuna migrazione necessaria.{RESET}')
        else:
            print('\n  Database aggiornato — nessuna migrazione necessaria.')

    return pending


# ---------------------------------------------------------------------------
# Applicazione migrazioni
# ---------------------------------------------------------------------------

def apply_all(conn, db_path, config, pending):
    if not pending:
        return True

    # Backup unico prima di tutto
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = db_path + f'.bak_migrate_{ts}'
    shutil.copy2(db_path, backup)
    ok(f'Backup creato: {backup}')

    header('Applicazione migrazioni')
    for m in pending:
        if _supports_color():
            print(f'\n  Migrazione {BOLD}{m.id}{RESET}: {m.desc}')
        else:
            print(f'\n  Migrazione {m.id}: {m.desc}')
        try:
            m.apply(conn, config)
            # Ogni migrazione porta il proprio codice (v2.3 → 230), ma solo v1.4
            # e v2.0 lo scrivevano: un database portato a v2.3 continuava a
            # dichiararsi 200. Allineiamo user_version alla migrazione applicata.
            if get_user_version(conn) < m.version:
                conn.execute(f"PRAGMA user_version = {m.version}")
                conn.commit()
            ok(f'  Migrazione {m.id} completata')
        except Exception as e:
            err(f'  Migrazione {m.id} FALLITA: {e}')
            err(f'  Ripristino dal backup: {backup}')
            conn.close()
            shutil.copy2(backup, db_path)
            return False

    # Integrity check finale
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if result != 'ok':
        err(f'Integrity check fallito: {result}')
        err(f'Ripristina dal backup: {backup}')
        return False

    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Strumento di migrazione MedInventory — analizza e aggiorna il database.'
    )
    parser.add_argument('--check', action='store_true',
                        help='Solo analisi, nessuna modifica')
    parser.add_argument('--yes', '-y', action='store_true',
                        help='Applica senza chiedere conferma')
    parser.add_argument('--db', metavar='PATH',
                        help='Percorso esplicito al database SQLite')
    args = parser.parse_args()

    db_path = load_db_path(args.db)
    config  = load_config()

    if _supports_color():
        print(f'\n{BOLD}MedInventory — Strumento di migrazione{RESET}')
    else:
        print('\nMedInventory — Strumento di migrazione')
    separator()

    if not os.path.exists(db_path):
        err(f'Database non trovato: {db_path}')
        err("Eseguire 'python seed.py' per una nuova installazione.")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        pending = print_analysis(conn, db_path)
    except Exception as e:
        err(f'Errore durante l\'analisi: {e}')
        conn.close()
        sys.exit(1)

    if args.check:
        conn.close()
        sys.exit(0 if not pending else 1)

    if not pending:
        conn.close()
        sys.exit(0)

    separator()

    if not args.yes:
        try:
            risposta = input('\nApplicare le migrazioni? [s/N] ').strip().lower()
        except (KeyboardInterrupt, EOFError):
            print('\nAnnullato.')
            conn.close()
            sys.exit(0)
        if risposta not in ('s', 'si', 'sì', 'y', 'yes'):
            print('Annullato.')
            conn.close()
            sys.exit(0)

    success = apply_all(conn, db_path, config, pending)
    conn.close()

    separator()
    if success:
        header('Risultato finale')
        # Riapre per mostrare lo stato aggiornato
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        version_str, uv, remaining = analyze(conn2)
        conn2.close()
        ok(f'Migrazione completata. Versione database: {version_str}')
        if remaining:
            warn(f'Attenzione: {len(remaining)} migrazione/i ancora pendenti.')
        else:
            ok('Tutte le migrazioni applicate correttamente.')
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
