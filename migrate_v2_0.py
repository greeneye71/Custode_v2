#!/usr/bin/env python3
"""
migrate_v2_0.py — Migrazione MedInventory v1.x → v2.0
Script idempotente. Sicuro da eseguire più volte.

Operazioni eseguite:
  - Crea le nuove tabelle v2 (strutture, strutture_config, api_tokens, login_attempts)
  - Crea la struttura di default e assegna i record esistenti
  - Aggiunge struttura_id a divisioni, apparecchi, log_attivita, utenti
  - Ricrea utenti con CHECK ruolo aggiornato (include superadmin)
  - Ricrea apparecchi con UNIQUE(struttura_id, modello, matricola)
  - Aggiunge le nuove colonne a strutture (telefono, responsabile, ecc.)
  - Rinomina la modalita 'ingegneria_clinica' in 'avanzata'
  - Aggiorna config.local.json con single_struttura: true
  - Crea il file sentinella data/.version_notice
  - Imposta PRAGMA user_version = 200

Eseguire PRIMA di avviare l'app v2.0:
    python migrate_v2_0.py
"""

import re
import sqlite3
import json
import os
import shutil
import logging
from datetime import datetime
import sys

# Su Windows la console non è UTF-8: senza questo, stampare accenti o
# caratteri di riquadro fa fallire lo script con UnicodeEncodeError
# (succede appena l'output viene rediretto su file o log).
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

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

    current_version = db.execute("PRAGMA user_version").fetchone()[0]

    # ----------------------------------------------------------------
    # FASE A: migrazione strutturale v1 → v2 (salta se già completata)
    # ----------------------------------------------------------------
    if current_version < 200:
        _migrate_v1_to_v2(db, config)
    else:
        logger.info("Fase A: già a versione 200, salto migrazione strutturale.")

    # ----------------------------------------------------------------
    # FASE B: raffinamenti schema v2.0 (sempre eseguita, idempotente)
    # ----------------------------------------------------------------
    logger.info("Fase B: raffinamenti schema strutture...")
    _add_strutture_columns(db)
    _rename_modalita_avanzata(db)

    # ----------------------------------------------------------------
    # FASE C: config, sentinella, user_version
    # ----------------------------------------------------------------
    _update_local_config()
    _write_version_notice()

    db.execute("PRAGMA user_version = 200")
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA legacy_alter_table = OFF")
    logger.info("Migrazione completata. PRAGMA user_version = 200")


# ============================================================================
# FASE A: migrazione strutturale v1 → v2
# ============================================================================

def _migrate_v1_to_v2(db, config):
    logger.info("Fase A: migrazione strutturale v1 → v2...")

    # ---- A1. Nuove tabelle ----
    logger.info("  A1. Creazione nuove tabelle...")

    run_safe(db, """CREATE TABLE IF NOT EXISTS strutture (
      id                  INTEGER PRIMARY KEY AUTOINCREMENT,
      nome                TEXT NOT NULL,
      codice              TEXT UNIQUE NOT NULL,
      descrizione         TEXT,
      tipo                TEXT DEFAULT 'altro'
                          CHECK(tipo IN ('ospedale','clinica_privata','rsa','ambulatorio',
                                         'poliambulatorio','laboratorio','altro')),
      indirizzo           TEXT,
      telefono            TEXT,
      email_notifiche     TEXT,
      pec                 TEXT,
      responsabile        TEXT,
      email_responsabile  TEXT,
      codice_fiscale      TEXT,
      partita_iva         TEXT,
      data_attivazione    DATE,
      scadenza_contratto  DATE,
      note                TEXT,
      modalita            TEXT NOT NULL DEFAULT 'standard'
                          CHECK(modalita IN ('standard', 'avanzata')),
      attiva              INTEGER DEFAULT 1,
      created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
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

    # ---- A2. Struttura di default ----
    logger.info("  A2. Struttura di default...")
    # structure_name esiste quasi sempre nei config reali, ma vuoto: con
    # config.get(chiave, fallback) il fallback non scatterebbe mai e la
    # struttura verrebbe creata senza nome.
    struttura_nome = ((config.get('structure_name') or '').strip()
                      or (config.get('app_name') or '').strip()
                      or 'Struttura Principale')
    struttura_exists = db.execute("SELECT id FROM strutture LIMIT 1").fetchone()
    if not struttura_exists:
        db.execute(
            "INSERT INTO strutture (nome, codice, modalita) VALUES (?, ?, 'avanzata')",
            (struttura_nome, 'DEFAULT')
        )
        db.commit()
        logger.info(f"  Struttura '{struttura_nome}' creata.")
    else:
        logger.info("  Struttura di default già presente.")

    struttura_id = db.execute("SELECT id FROM strutture ORDER BY id LIMIT 1").fetchone()[0]

    # ---- A3. Colonne struttura_id nelle tabelle esistenti ----
    logger.info("  A3. Aggiunta colonne struttura_id...")
    for table in ('divisioni', 'apparecchi', 'log_attivita'):
        if not column_exists(db, table, 'struttura_id'):
            db.execute(f"ALTER TABLE {table} ADD COLUMN struttura_id INTEGER")
            db.execute(f"UPDATE {table} SET struttura_id = ?", (struttura_id,))
            db.commit()
            logger.info(f"    struttura_id aggiunto a {table}")
        else:
            logger.info(f"    struttura_id già presente in {table}")

    if not column_exists(db, 'utenti', 'struttura_id'):
        db.execute("ALTER TABLE utenti ADD COLUMN struttura_id INTEGER")
        db.execute(
            "UPDATE utenti SET struttura_id = ? WHERE ruolo != 'superadmin'",
            (struttura_id,)
        )
        db.commit()
        logger.info("    struttura_id aggiunto a utenti")
    else:
        logger.info("    struttura_id già presente in utenti")

    # ---- A4. Estensione CHECK ruolo utenti ----
    logger.info("  A4. Verifica CHECK ruolo utenti...")
    utenti_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='utenti'"
    ).fetchone()[0]
    if 'superadmin' not in utenti_sql:
        logger.info("    Ricreazione tabella utenti per aggiornare CHECK ruolo...")
        _recreate_utenti(db)
    else:
        logger.info("    CHECK ruolo già aggiornato.")

    # ---- A5. Aggiornamento UNIQUE su apparecchi ----
    logger.info("  A5. Aggiornamento UNIQUE su apparecchi...")
    app_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='apparecchi'"
    ).fetchone()[0]
    app_sql_normalized = re.sub(r'\s+', ' ', app_sql)
    if ('struttura_id, modello, matricola' not in app_sql_normalized
            and 'struttura_id,modello,matricola' not in app_sql_normalized):
        logger.info("    Ricreazione tabella apparecchi per aggiornare UNIQUE...")
        _recreate_apparecchi(db, struttura_id)
    else:
        logger.info("    UNIQUE su apparecchi già aggiornato.")

    db.commit()


# ============================================================================
# FASE B: raffinamenti schema strutture
# ============================================================================

def _add_strutture_columns(db):
    """Aggiunge le nuove colonne a strutture se non presenti (idempotente)."""
    nuove_colonne = [
        ('tipo',               "TEXT DEFAULT 'altro'"),
        ('telefono',           'TEXT'),
        ('pec',                'TEXT'),
        ('responsabile',       'TEXT'),
        ('email_responsabile', 'TEXT'),
        ('codice_fiscale',     'TEXT'),
        ('partita_iva',        'TEXT'),
        ('data_attivazione',   'DATE'),
        ('scadenza_contratto', 'DATE'),
        ('note',               'TEXT'),
    ]
    for col, col_def in nuove_colonne:
        if not column_exists(db, 'strutture', col):
            db.execute(f"ALTER TABLE strutture ADD COLUMN {col} {col_def}")
            logger.info(f"  strutture.{col} aggiunta")
    db.commit()


def _rename_modalita_avanzata(db):
    """
    Rinomina la modalita 'ingegneria_clinica' in 'avanzata'.
    Richiede la ricreazione della tabella strutture per aggiornare il CHECK.
    """
    strutture_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='strutture'"
    ).fetchone()
    if strutture_sql is None:
        return  # tabella non ancora creata (non dovrebbe accadere)

    sql_text = strutture_sql[0]

    # Controlla se il CHECK usa ancora 'ingegneria_clinica'
    needs_recreate = 'ingegneria_clinica' in sql_text
    # Controlla se ci sono righe con la vecchia modalita
    has_old_value = db.execute(
        "SELECT COUNT(*) FROM strutture WHERE modalita = 'ingegneria_clinica'"
    ).fetchone()[0] > 0

    if not needs_recreate and not has_old_value:
        logger.info("  Modalita: nessun riferimento a 'ingegneria_clinica', niente da fare.")
        return

    if needs_recreate:
        logger.info("  Ricreazione tabella strutture per aggiornare CHECK modalita...")
        _recreate_strutture(db)
    elif has_old_value:
        # CHECK non limita piu, aggiorna solo i dati
        db.execute(
            "UPDATE strutture SET modalita = 'avanzata' WHERE modalita = 'ingegneria_clinica'"
        )
        db.commit()
        logger.info("  Dati strutture: 'ingegneria_clinica' rinominato in 'avanzata'.")


def _recreate_strutture(db):
    """Ricrea strutture con CHECK modalita aggiornato e nuove colonne."""
    cols_old = [row[1] for row in db.execute("PRAGMA table_info(strutture)").fetchall()]

    db.execute("ALTER TABLE strutture RENAME TO _strutture_old")
    db.execute("""CREATE TABLE strutture (
      id                  INTEGER PRIMARY KEY AUTOINCREMENT,
      nome                TEXT NOT NULL,
      codice              TEXT UNIQUE NOT NULL,
      descrizione         TEXT,
      tipo                TEXT DEFAULT 'altro'
                          CHECK(tipo IN ('ospedale','clinica_privata','rsa','ambulatorio',
                                         'poliambulatorio','laboratorio','altro')),
      indirizzo           TEXT,
      telefono            TEXT,
      email_notifiche     TEXT,
      pec                 TEXT,
      responsabile        TEXT,
      email_responsabile  TEXT,
      codice_fiscale      TEXT,
      partita_iva         TEXT,
      data_attivazione    DATE,
      scadenza_contratto  DATE,
      note                TEXT,
      modalita            TEXT NOT NULL DEFAULT 'standard'
                          CHECK(modalita IN ('standard', 'avanzata')),
      attiva              INTEGER DEFAULT 1,
      created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # Colonne della nuova tabella con fallback per quelle assenti nel vecchio schema
    # (nome_nuovo, expr_se_presente, expr_se_assente)
    col_mapping = [
        ('id',                  'id',                  None),
        ('nome',                'nome',                None),
        ('codice',              'codice',              None),
        ('descrizione',         'descrizione',         'NULL'),
        ('tipo',                'tipo',                "'altro'"),
        ('indirizzo',           'indirizzo',           'NULL'),
        ('telefono',            'telefono',            'NULL'),
        ('email_notifiche',     'email_notifiche',     'NULL'),
        ('pec',                 'pec',                 'NULL'),
        ('responsabile',        'responsabile',        'NULL'),
        ('email_responsabile',  'email_responsabile',  'NULL'),
        ('codice_fiscale',      'codice_fiscale',      'NULL'),
        ('partita_iva',         'partita_iva',         'NULL'),
        ('data_attivazione',    'data_attivazione',    'NULL'),
        ('scadenza_contratto',  'scadenza_contratto',  'NULL'),
        ('note',                'note',                'NULL'),
        # Rinomina ingegneria_clinica → avanzata
        ('modalita',            None,                  None),   # gestito separatamente
        ('attiva',              'attiva',              '1'),
        ('created_at',          'created_at',          'CURRENT_TIMESTAMP'),
        ('updated_at',          'updated_at',          'CURRENT_TIMESTAMP'),
    ]

    select_exprs = []
    for col_new, col_old, fallback in col_mapping:
        if col_new == 'modalita':
            if 'modalita' in cols_old:
                select_exprs.append(
                    "CASE WHEN modalita = 'ingegneria_clinica' THEN 'avanzata' "
                    "ELSE modalita END AS modalita"
                )
            else:
                select_exprs.append("'standard' AS modalita")
        elif col_old in cols_old:
            select_exprs.append(col_old)
        elif fallback is not None:
            select_exprs.append(f"{fallback} AS {col_new}")
        else:
            logger.warning(f"  Colonna attesa '{col_new}' non trovata, uso NULL")
            select_exprs.append(f"NULL AS {col_new}")

    select_clause = ", ".join(select_exprs)
    db.execute(f"INSERT INTO strutture SELECT {select_clause} FROM _strutture_old")
    db.execute("DROP TABLE _strutture_old")

    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_strutture_codice ON strutture(codice)",
        "CREATE INDEX IF NOT EXISTS idx_strutture_attiva ON strutture(attiva)",
    ]:
        db.execute(idx)

    db.commit()
    logger.info("  Tabella strutture ricreata con CHECK 'avanzata' e nuove colonne.")


# ============================================================================
# Helper: ricreazione utenti e apparecchi
# ============================================================================

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

    col_mapping = [
        ('id',                     'id',                     None),
        ('struttura_id',           'struttura_id',           str(struttura_id)),
        ('divisione_id',           'divisione_id',           None),
        ('descrizione',            'descrizione',            'NULL'),
        ('matricola',              'matricola',              None),
        ('numero_inventario',      'numero_inventario',      'NULL'),
        ('marca',                  'marca',                  None),
        ('modello',                'modello',                None),
        ('anno_fabbricazione',     'anno_fabbricazione',     'NULL'),
        ('classificazione',        'classificazione',        'NULL'),
        ('ubicazione',             'ubicazione',             'NULL'),
        ('stato',                  'stato',                  "'funzionante'"),
        ('connesso_rete',          'connesso_rete',          '0'),
        ('ip_address',             'ip_address',             'NULL'),
        ('mac_address',            'mac_address',            'NULL'),
        ('hostname',               'hostname',               'NULL'),
        ('porta',                  'porta',                  'NULL'),
        ('protocollo',             'protocollo',             'NULL'),
        ('url_interfaccia',        'url_interfaccia',        'NULL'),
        ('fornitore',              'fornitore',              'NULL'),
        ('codice_fornitore',       'codice_fornitore',       'NULL'),
        ('garanzia_scadenza',      'garanzia_scadenza',      'NULL'),
        ('contratto_manutenzione', 'contratto_manutenzione', 'NULL'),
        ('note',                   'note',                   'NULL'),
        ('foto_path',              'foto_path',              'NULL'),
        ('soggetto_verifica',      'soggetto_verifica',      '1'),
        ('created_by',             'created_by',             'NULL'),
        ('updated_by',             'updated_by',             'NULL'),
        ('created_at',             'created_at',             'CURRENT_TIMESTAMP'),
        ('updated_at',             'updated_at',             'CURRENT_TIMESTAMP'),
    ]

    select_exprs = []
    for col_new, col_old_expr, fallback in col_mapping:
        if col_old_expr in cols_old:
            select_exprs.append(col_old_expr)
        elif fallback is not None:
            select_exprs.append(f"{fallback} AS {col_new}")
        else:
            logger.warning(f"  Colonna attesa '{col_new}' non trovata in _apparecchi_old, uso NULL")
            select_exprs.append(f"NULL AS {col_new}")

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


# ============================================================================
# FASE C: config e sentinella
# ============================================================================

def _update_local_config():
    """Aggiunge single_struttura: true a config.local.json se non presente."""
    if not os.path.exists(CONFIG_PATH):
        logger.info("  config.local.json non trovato, salto.")
        return
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        local_cfg = json.load(f)
    if 'single_struttura' not in local_cfg:
        local_cfg['single_struttura'] = True
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(local_cfg, f, indent=2, ensure_ascii=False)
        logger.info("  single_struttura: true aggiunto a config.local.json")
    else:
        logger.info("  single_struttura già presente in config.local.json")


def _write_version_notice():
    """Crea data/.version_notice (file sentinella per l'app)."""
    data_dir = os.path.join(BASE_DIR, 'data')
    notice_path = os.path.join(data_dir, '.version_notice')
    if not os.path.exists(data_dir):
        logger.warning("  Directory data/ non trovata — file sentinella non creato.")
        return
    if os.path.exists(notice_path):
        logger.info("  File sentinella già presente.")
        return
    with open(notice_path, 'w', encoding='utf-8') as f:
        json.dump({
            'old_version': '1.x',
            'new_version': '2.0.0',
            'upgraded_at': datetime.now().strftime('%d/%m/%Y %H:%M'),
        }, f)
    logger.info("  File sentinella creato.")


# ============================================================================
# MAIN
# ============================================================================

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
    else:
        db.close()
        logger.info("=" * 55)
        logger.info("Migrazione v2.0 completata con successo.")
        logger.info(f"Backup pre-migrazione: {backup_path}")
        logger.info("Avviare l'applicazione con: python run_production.py")


if __name__ == '__main__':
    main()
