"""
Recovery script: ripristina import_history e import_preview
dopo una migrazione interrotta di migrate_v1_3_2.py.

Problema noto: SQLite >= 3.26 aggiorna automaticamente i riferimenti FK
quando si rinomina una tabella. La migrazione ha rinominato import_history
in _import_history_old, e SQLite ha aggiornato la FK in import_preview
per puntare a _import_history_old. Il recovery precedente ha ricreato
import_history ma ha eliminato _import_history_old, lasciando import_preview
con una FK verso una tabella inesistente.

Eseguire una sola volta:
    python recover_import_history.py
"""
import sqlite3
import sys
import os
import json

# Su Windows la console non è UTF-8: senza questo, stampare accenti o
# caratteri di riquadro fa fallire lo script con UnicodeEncodeError
# (succede appena l'output viene rediretto su file o log).
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def recover(db_path):
    if not os.path.exists(db_path):
        print(f"Database non trovato: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Verifica stato tabelle
        def table_exists(name):
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
            return cursor.fetchone() is not None

        def get_table_sql(name):
            cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,))
            row = cursor.fetchone()
            return row[0] if row else None

        has_ih = table_exists('import_history')
        has_old = table_exists('_import_history_old')
        ip_sql = get_table_sql('import_preview')
        ip_fk_broken = ip_sql and '_import_history_old' in ip_sql

        print(f"Stato DB:")
        print(f"  import_history:      {'OK' if has_ih else 'MANCANTE'}")
        print(f"  _import_history_old: {'presente' if has_old else 'assente'}")
        print(f"  import_preview FK:   {'ROTTA (_import_history_old)' if ip_fk_broken else 'OK'}")

        if has_ih and not has_old and not ip_fk_broken:
            print("\nNessun recupero necessario.")
            return

        # Usa PRAGMA legacy_alter_table per evitare nuovi aggiornamenti FK automatici
        cursor.execute("PRAGMA legacy_alter_table = ON")

        # --- Step 1: assicura che import_history esista ---
        if not has_ih:
            print("\nCreazione import_history...")
            cursor.execute("BEGIN")
            cursor.execute("""
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
                )
            """)
            if has_old:
                cursor.execute("SELECT COUNT(*) FROM _import_history_old")
                count = cursor.fetchone()[0]
                if count > 0:
                    cursor.execute(
                        "INSERT INTO import_history SELECT * FROM _import_history_old")
                    print(f"  Copiate {count} righe da _import_history_old.")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_import_history_tipo ON import_history(tipo_import)")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_import_history_stato ON import_history(stato)")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_import_history_created ON import_history(created_at)")
            conn.commit()
            print("  OK - import_history creata.")

        # --- Step 2: elimina _import_history_old se ancora presente ---
        if table_exists('_import_history_old'):
            print("\nEliminazione _import_history_old residua...")
            cursor.execute("DROP TABLE _import_history_old")
            conn.commit()
            print("  OK.")

        # --- Step 3: ricrea import_preview se la FK è rotta ---
        ip_sql = get_table_sql('import_preview')
        ip_fk_broken = ip_sql and '_import_history_old' in ip_sql
        if ip_fk_broken:
            print("\nRicreazione import_preview (FK rotta verso _import_history_old)...")
            cursor.execute("BEGIN")
            cursor.execute(
                "ALTER TABLE import_preview RENAME TO _import_preview_old")
            cursor.execute("""
                CREATE TABLE import_preview (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_id INTEGER NOT NULL,
                    riga_numero INTEGER,
                    dati_estratti TEXT,
                    apparecchio_match_id INTEGER,
                    match_confidence DECIMAL(3,2),
                    stato TEXT CHECK(stato IN ('pending', 'approved', 'rejected', 'imported')),
                    note_revisione TEXT,
                    FOREIGN KEY (import_id) REFERENCES import_history(id) ON DELETE CASCADE,
                    FOREIGN KEY (apparecchio_match_id) REFERENCES apparecchi(id)
                )
            """)
            cursor.execute(
                "INSERT INTO import_preview SELECT * FROM _import_preview_old")
            cursor.execute("DROP TABLE _import_preview_old")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_import_preview_import ON import_preview(import_id)")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_import_preview_stato ON import_preview(stato)")
            conn.commit()
            print("  OK - import_preview ricreata con FK corretta.")

        print("\nRecupero completato con successo.")

    except Exception as e:
        conn.rollback()
        print(f"\nERRORE durante il recupero: {e}")
        sys.exit(1)
    finally:
        cursor.execute("PRAGMA legacy_alter_table = OFF")
        conn.close()


if __name__ == '__main__':
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
    with open(config_path, 'r') as f:
        config = json.load(f)
    db_path = config.get('database_path', 'data/database.sqlite')
    recover(db_path)
