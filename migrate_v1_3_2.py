"""
Migration v1.3.2
- Update import_history CHECK constraint to include 'verbale_manutenzione'
- Create uploads/import/ directory structure
"""
import sqlite3
import sys
import os
import json


def migrate(db_path):
    if not os.path.exists(db_path):
        print(f"Database non trovato: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        print("Aggiornamento tabella import_history (CHECK constraint)...")

        cursor.execute("BEGIN")

        # 1. Rename old table
        cursor.execute("ALTER TABLE import_history RENAME TO _import_history_old")

        # 2. Create new table with updated CHECK
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

        # 3. Copy data
        cursor.execute("INSERT INTO import_history SELECT * FROM _import_history_old")

        # 4. Drop old table
        cursor.execute("DROP TABLE _import_history_old")

        # 5. Recreate indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_import_history_tipo ON import_history(tipo_import)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_import_history_stato ON import_history(stato)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_import_history_created ON import_history(created_at)")

        conn.commit()
        print("  OK - Tabella import_history aggiornata.")

    except Exception as e:
        conn.rollback()
        print(f"  ERRORE: {e}")
        sys.exit(1)
    finally:
        conn.close()

    # Create directories
    uploads_base = os.path.join(os.path.dirname(db_path), '..', 'uploads')
    for subdir in ['import', 'verbali', 'verifiche']:
        path = os.path.join(uploads_base, subdir)
        os.makedirs(path, exist_ok=True)
        print(f"  Directory: {path}")

    print("\nMigrazione v1.3.2 completata con successo.")


if __name__ == '__main__':
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
    with open(config_path, 'r') as f:
        config = json.load(f)
    db_path = config.get('database_path', 'data/database.sqlite')
    migrate(db_path)
