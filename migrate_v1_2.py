"""
MedInventory - Migration v1.2.0
Rinomina codice_interno -> descrizione (rimuove UNIQUE),
aggiunge stato 'da_sostituire', aggiorna vista prossime_scadenze.

Esegui PRIMA di avviare la nuova versione dell'applicazione:
    python migrate_v1_2.py
"""

import json
import os
import shutil
import sqlite3
from datetime import datetime


def load_db_path():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, 'config.json')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return os.path.join(base_dir, config.get('database_path', 'data/database.sqlite'))
    return os.path.join(base_dir, 'data/database.sqlite')


def backup_database(db_path):
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = f"{db_path}.bak_v1_2_{timestamp}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def column_exists(cursor, table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def migrate(db_path):
    print(f"Database: {db_path}")

    if not os.path.exists(db_path):
        print(f"ERRORE: database non trovato in '{db_path}'")
        return False

    # Backup
    backup_path = backup_database(db_path)
    print(f"Backup creato: {backup_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        # Check if migration already applied
        if column_exists(cur, 'apparecchi', 'descrizione') and \
           not column_exists(cur, 'apparecchi', 'codice_interno'):
            print("Migrazione già applicata (colonna 'descrizione' presente, 'codice_interno' assente).")
            conn.close()
            return True

        cur.execute("PRAGMA foreign_keys = OFF")

        # Clean up any leftover temp table from a previous failed run
        cur.execute("DROP TABLE IF EXISTS apparecchi_new")

        # Count current records
        cur.execute("SELECT COUNT(*) FROM apparecchi")
        total = cur.fetchone()[0]
        print(f"Apparecchi da migrare: {total}")

        # Create new table with updated schema
        cur.execute("""
            CREATE TABLE apparecchi_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                divisione_id INTEGER NOT NULL,
                descrizione TEXT,
                matricola TEXT UNIQUE NOT NULL,
                numero_inventario TEXT,
                marca TEXT NOT NULL,
                modello TEXT NOT NULL,
                anno_fabbricazione INTEGER,
                classificazione TEXT CHECK(classificazione IN ('I', 'IIa', 'IIb', 'III')),
                ubicazione TEXT,
                stato TEXT DEFAULT 'funzionante'
                    CHECK(stato IN ('funzionante', 'in_manutenzione', 'dismesso', 'da_sostituire')),
                connesso_rete INTEGER DEFAULT 0,
                ip_address TEXT,
                mac_address TEXT,
                hostname TEXT,
                porta INTEGER,
                protocollo TEXT,
                url_interfaccia TEXT,
                fornitore TEXT,
                codice_fornitore TEXT,
                garanzia_scadenza DATE,
                contratto_manutenzione TEXT,
                note TEXT,
                soggetto_verifica INTEGER DEFAULT 1,
                foto_path TEXT,
                created_by INTEGER,
                updated_by INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (divisione_id) REFERENCES divisioni(id),
                FOREIGN KEY (created_by) REFERENCES utenti(id),
                FOREIGN KEY (updated_by) REFERENCES utenti(id)
            )
        """)

        # Copy data, mapping codice_interno -> descrizione
        # Check if soggetto_verifica exists in old table
        has_soggetto = column_exists(cur, 'apparecchi', 'soggetto_verifica')
        soggetto_col = "soggetto_verifica" if has_soggetto else "1"

        # Build SELECT dynamically based on old schema
        has_codice = column_exists(cur, 'apparecchi', 'codice_interno')
        source_col = "codice_interno" if has_codice else "descrizione"

        cur.execute(f"""
            INSERT INTO apparecchi_new
                (id, divisione_id, descrizione, matricola, numero_inventario,
                 marca, modello, anno_fabbricazione, classificazione,
                 ubicazione, stato, connesso_rete, ip_address, mac_address,
                 hostname, porta, protocollo, url_interfaccia,
                 fornitore, codice_fornitore, garanzia_scadenza,
                 contratto_manutenzione, note, soggetto_verifica, foto_path,
                 created_by, updated_by, created_at, updated_at)
            SELECT
                id, divisione_id, {source_col} AS descrizione, matricola, numero_inventario,
                marca, modello, anno_fabbricazione, classificazione,
                ubicazione, stato, connesso_rete, ip_address, mac_address,
                hostname, porta, protocollo, url_interfaccia,
                fornitore, codice_fornitore, garanzia_scadenza,
                contratto_manutenzione, note, {soggetto_col}, foto_path,
                created_by, updated_by, created_at, updated_at
            FROM apparecchi
        """)

        # Drop view BEFORE dropping table (SQLite validates views on table rename)
        cur.execute("DROP VIEW IF EXISTS prossime_scadenze")

        cur.execute("DROP TABLE apparecchi")
        cur.execute("ALTER TABLE apparecchi_new RENAME TO apparecchi")

        # Recreate indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apparecchi_divisione ON apparecchi(divisione_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apparecchi_descrizione ON apparecchi(descrizione)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apparecchi_matricola ON apparecchi(matricola)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apparecchi_stato ON apparecchi(stato)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apparecchi_marca ON apparecchi(marca)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apparecchi_ubicazione ON apparecchi(ubicazione)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apparecchi_ip ON apparecchi(ip_address)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apparecchi_numero_inventario ON apparecchi(numero_inventario)")

        # Recreate view with 'descrizione' column
        cur.execute("DROP VIEW IF EXISTS prossime_scadenze")  # safety (already dropped above)
        cur.execute("""
            CREATE VIEW prossime_scadenze AS
            SELECT
              a.id AS apparecchio_id,
              a.divisione_id,
              a.descrizione,
              a.marca,
              a.modello,
              a.matricola,
              a.ubicazione,
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
            WHERE a.stato != 'dismesso'
              AND m.prossima_scadenza IS NOT NULL

            UNION ALL

            SELECT
              a.id AS apparecchio_id,
              a.divisione_id,
              a.descrizione,
              a.marca,
              a.modello,
              a.matricola,
              a.ubicazione,
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
            WHERE a.stato != 'dismesso'
              AND v.prossima_scadenza IS NOT NULL

            ORDER BY prossima_scadenza ASC
        """)

        # Create accessori table if not exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS accessori (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                apparecchio_id INTEGER NOT NULL,
                descrizione TEXT NOT NULL,
                produttore TEXT,
                modello TEXT,
                matricola TEXT,
                created_by INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (apparecchio_id) REFERENCES apparecchi(id) ON DELETE CASCADE,
                FOREIGN KEY (created_by) REFERENCES utenti(id)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_accessori_apparecchio ON accessori(apparecchio_id)")

        cur.execute("PRAGMA foreign_keys = ON")
        conn.commit()

        # Integrity check
        cur.execute("PRAGMA integrity_check")
        result = cur.fetchone()[0]
        if result != 'ok':
            raise Exception(f"Integrity check fallito: {result}")

        # Verify count
        cur.execute("SELECT COUNT(*) FROM apparecchi")
        migrated = cur.fetchone()[0]

        conn.close()

        print(f"\nMigrazione completata con successo!")
        print(f"  Record migrati:  {migrated}/{total}")
        print(f"  Backup salvato:  {backup_path}")
        print(f"  Integrity check: OK")
        return True

    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"\nERRORE durante la migrazione: {e}")
        print(f"Database non modificato. Backup disponibile: {backup_path}")
        return False


if __name__ == '__main__':
    db_path = load_db_path()
    success = migrate(db_path)
    exit(0 if success else 1)
