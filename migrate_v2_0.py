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
        else:
            logger.info(f"  struttura_id già presente in {table}")

    if not column_exists(db, 'utenti', 'struttura_id'):
        db.execute("ALTER TABLE utenti ADD COLUMN struttura_id INTEGER")
        db.execute(
            "UPDATE utenti SET struttura_id = ? WHERE ruolo != 'superadmin'",
            (struttura_id,)
        )
        db.commit()
        logger.info("  struttura_id aggiunto a utenti")
    else:
        logger.info("  struttura_id già presente in utenti")

    # ----------------------------------------------------------------
    # 4. Estensione CHECK ruolo utenti
    # ----------------------------------------------------------------
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
        else:
            logger.info("  single_struttura già presente in config.local.json")
    else:
        logger.info("  config.local.json non trovato, salto.")

    # ----------------------------------------------------------------
    # 7. File sentinella versione
    # ----------------------------------------------------------------
    logger.info("7. File sentinella versione...")
    data_dir = os.path.join(BASE_DIR, 'data')
    notice_path = os.path.join(data_dir, '.version_notice')
    if os.path.exists(data_dir) and not os.path.exists(notice_path):
        with open(notice_path, 'w', encoding='utf-8') as f:
            json.dump({
                'old_version': '1.x',
                'new_version': '2.0.0',
                'upgraded_at': datetime.now().strftime('%d/%m/%Y %H:%M'),
            }, f)
        logger.info("  File sentinella creato.")
    else:
        logger.info("  File sentinella già presente o directory data/ assente.")

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

    # Colonne obbligatorie della nuova tabella con fallback per quelle assenti nel vecchio schema
    # Formato: (nome_col_nuova, expr_se_presente, expr_se_assente)
    col_mapping = [
        ('id',                    'id',                    None),          # sempre presente
        ('struttura_id',          'struttura_id',          str(struttura_id)),
        ('divisione_id',          'divisione_id',          None),          # sempre presente
        ('descrizione',           'descrizione',           'NULL'),
        ('matricola',             'matricola',             None),          # sempre presente
        ('numero_inventario',     'numero_inventario',     'NULL'),
        ('marca',                 'marca',                 None),          # sempre presente
        ('modello',               'modello',               None),          # sempre presente
        ('anno_fabbricazione',    'anno_fabbricazione',    'NULL'),
        ('classificazione',       'classificazione',       'NULL'),
        ('ubicazione',            'ubicazione',            'NULL'),
        ('stato',                 'stato',                 "'funzionante'"),
        ('connesso_rete',         'connesso_rete',         '0'),
        ('ip_address',            'ip_address',            'NULL'),
        ('mac_address',           'mac_address',           'NULL'),
        ('hostname',              'hostname',              'NULL'),
        ('porta',                 'porta',                 'NULL'),
        ('protocollo',            'protocollo',            'NULL'),
        ('url_interfaccia',       'url_interfaccia',       'NULL'),
        ('fornitore',             'fornitore',             'NULL'),
        ('codice_fornitore',      'codice_fornitore',      'NULL'),
        ('garanzia_scadenza',     'garanzia_scadenza',     'NULL'),
        ('contratto_manutenzione','contratto_manutenzione','NULL'),
        ('note',                  'note',                  'NULL'),
        ('foto_path',             'foto_path',             'NULL'),
        ('soggetto_verifica',     'soggetto_verifica',     '1'),
        ('created_by',            'created_by',            'NULL'),
        ('updated_by',            'updated_by',            'NULL'),
        ('created_at',            'created_at',            'CURRENT_TIMESTAMP'),
        ('updated_at',            'updated_at',            'CURRENT_TIMESTAMP'),
    ]

    select_exprs = []
    for col_new, col_old_expr, fallback in col_mapping:
        if col_old_expr in cols_old:
            select_exprs.append(col_old_expr)
        else:
            select_exprs.append(f"{fallback} AS {col_new}")

    select_clause = ", ".join(select_exprs)
    db.execute(f"INSERT INTO apparecchi SELECT {select_clause} FROM _apparecchi_old")
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
