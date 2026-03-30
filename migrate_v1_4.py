"""
MedInventory - Migration v1.4.3
Rimuove il vincolo UNIQUE sulla sola colonna 'matricola' della tabella apparecchi
e lo sostituisce con un vincolo composito UNIQUE(modello, matricola).

Motivazione: apparecchi di modelli diversi possono condividere la stessa matricola
(numero di serie assegnato dal produttore, non globalmente univoco).

Esegui PRIMA di avviare la nuova versione dell'applicazione:
    python migrate_v1_4.py
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
    backup_path = f"{db_path}.bak_v1_4_{timestamp}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def column_exists(cursor, table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def has_single_unique_on_matricola(cursor):
    """Ritorna True se esiste ancora un indice UNIQUE sulla sola colonna matricola."""
    cursor.execute("PRAGMA index_list(apparecchi)")
    indexes = cursor.fetchall()
    for idx in indexes:
        idx_name = idx[1]
        is_unique = idx[2]
        if not is_unique:
            continue
        cursor.execute(f"PRAGMA index_info({idx_name})")
        cols = cursor.fetchall()
        if len(cols) == 1 and cols[0][2] == 'matricola':
            return True
    return False


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
        if not has_single_unique_on_matricola(cur):
            print("Migrazione già applicata (nessun indice UNIQUE su sola colonna 'matricola').")
            conn.close()
            return True

        cur.execute("PRAGMA foreign_keys = OFF")

        # Clean up any leftover temp table from a previous failed run
        cur.execute("DROP TABLE IF EXISTS apparecchi_new")

        # Count current records
        cur.execute("SELECT COUNT(*) FROM apparecchi")
        total = cur.fetchone()[0]
        print(f"Apparecchi da migrare: {total}")

        # soggetto_verifica è stato aggiunto in v1.2 — gestiamo entrambi i casi
        has_soggetto = column_exists(cur, 'apparecchi', 'soggetto_verifica')
        soggetto_col_def = "\n                soggetto_verifica INTEGER DEFAULT 1," if has_soggetto else ""
        soggetto_col_sel = "soggetto_verifica," if has_soggetto else ""
        soggetto_col_ins = "soggetto_verifica," if has_soggetto else ""

        # Crea nuova tabella: matricola NOT NULL (no UNIQUE), vincolo composito UNIQUE(modello, matricola)
        cur.execute(f"""
            CREATE TABLE apparecchi_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                divisione_id INTEGER NOT NULL,
                descrizione TEXT,
                matricola TEXT NOT NULL,
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
                note TEXT,{soggetto_col_def}
                foto_path TEXT,
                created_by INTEGER,
                updated_by INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(modello, matricola),
                FOREIGN KEY (divisione_id) REFERENCES divisioni(id),
                FOREIGN KEY (created_by) REFERENCES utenti(id),
                FOREIGN KEY (updated_by) REFERENCES utenti(id)
            )
        """)

        # Copia dati
        cur.execute(f"""
            INSERT INTO apparecchi_new
                (id, divisione_id, descrizione, matricola, numero_inventario,
                 marca, modello, anno_fabbricazione, classificazione,
                 ubicazione, stato, connesso_rete, ip_address, mac_address,
                 hostname, porta, protocollo, url_interfaccia,
                 fornitore, codice_fornitore, garanzia_scadenza,
                 contratto_manutenzione, note, {soggetto_col_ins}
                 foto_path, created_by, updated_by, created_at, updated_at)
            SELECT
                id, divisione_id, descrizione, matricola, numero_inventario,
                marca, modello, anno_fabbricazione, classificazione,
                ubicazione, stato, connesso_rete, ip_address, mac_address,
                hostname, porta, protocollo, url_interfaccia,
                fornitore, codice_fornitore, garanzia_scadenza,
                contratto_manutenzione, note, {soggetto_col_sel}
                foto_path, created_by, updated_by, created_at, updated_at
            FROM apparecchi
        """)

        # Elimina vista prima di rinominare la tabella (SQLite la valida)
        cur.execute("DROP VIEW IF EXISTS prossime_scadenze")

        cur.execute("DROP TABLE apparecchi")
        cur.execute("ALTER TABLE apparecchi_new RENAME TO apparecchi")

        # Ricrea indici
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apparecchi_divisione ON apparecchi(divisione_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apparecchi_descrizione ON apparecchi(descrizione)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apparecchi_matricola ON apparecchi(matricola)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apparecchi_stato ON apparecchi(stato)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apparecchi_marca ON apparecchi(marca)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apparecchi_ubicazione ON apparecchi(ubicazione)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apparecchi_ip ON apparecchi(ip_address)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apparecchi_numero_inventario ON apparecchi(numero_inventario)")

        # Ricrea vista prossime_scadenze
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
              AND m.id = (
                SELECT m2.id FROM manutenzioni m2
                WHERE m2.apparecchio_id = m.apparecchio_id
                  AND m2.tipo = m.tipo
                ORDER BY m2.data_intervento DESC, m2.id DESC
                LIMIT 1
              )

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
              AND v.id = (
                SELECT v2.id FROM verifiche v2
                WHERE v2.apparecchio_id = v.apparecchio_id
                ORDER BY v2.data_verifica DESC, v2.id DESC
                LIMIT 1
              )

            ORDER BY prossima_scadenza ASC
        """)

        cur.execute("PRAGMA foreign_keys = ON")
        cur.execute("PRAGMA user_version = 143")
        conn.commit()

        # Integrity check
        cur.execute("PRAGMA integrity_check")
        result = cur.fetchone()[0]
        if result != 'ok':
            raise Exception(f"Integrity check fallito: {result}")

        # Verifica conteggio
        cur.execute("SELECT COUNT(*) FROM apparecchi")
        migrated = cur.fetchone()[0]

        conn.close()

        print(f"\nMigrazione completata con successo!")
        print(f"  Record migrati:  {migrated}/{total}")
        print(f"  Backup salvato:  {backup_path}")
        print(f"  Integrity check: OK")
        print(f"  Modifica:        UNIQUE(matricola) → UNIQUE(modello, matricola)")
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
