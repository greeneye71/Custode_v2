"""
migrate_v2_2.py — Aggiunge ruolo 'tecnico' e tabella tecnici_strutture.

Ricrea utenti con CHECK aggiornato; crea tecnici_strutture se mancante.
I dati esistenti vengono preservati.

Uso:
    python migrate_v2_2.py [path/to/database.sqlite]
"""
import sqlite3
import sys
import os

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join('data', 'database.sqlite')


def run(db_path):
    if not os.path.exists(db_path):
        print(f"ERRORE: database non trovato: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA journal_mode = WAL")

    try:
        # --- UTENTI: aggiorna CHECK ruolo ---
        cols = [row[1] for row in conn.execute("PRAGMA table_info(utenti)").fetchall()]
        col_list = ', '.join(cols)

        print("Migrazione tabella utenti (aggiunta ruolo tecnico)...")
        conn.execute("ALTER TABLE utenti RENAME TO utenti_old")
        conn.execute(f"""
            CREATE TABLE utenti (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              email TEXT UNIQUE NOT NULL,
              password_hash TEXT NOT NULL,
              nome TEXT NOT NULL,
              cognome TEXT NOT NULL,
              ruolo TEXT NOT NULL CHECK(ruolo IN ('superadmin', 'admin', 'utente', 'tecnico')),
              divisione_default_id INTEGER,
              attivo INTEGER DEFAULT 1,
              primo_accesso INTEGER DEFAULT 1,
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
        print("  OK — utenti migrati.")

        # --- TECNICI_STRUTTURE ---
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tecnici_strutture'"
        ).fetchone()
        if not exists:
            print("Creazione tabella tecnici_strutture...")
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
                "CREATE INDEX idx_tecnici_strutture_tecnico   ON tecnici_strutture(tecnico_id)")
            conn.execute(
                "CREATE INDEX idx_tecnici_strutture_struttura ON tecnici_strutture(struttura_id)")
            print("  OK — tecnici_strutture creata.")
        else:
            print("tecnici_strutture già esistente, skip.")

        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
        print("\nMigrazione v2.2 completata con successo.")

    except Exception as e:
        conn.rollback()
        print(f"ERRORE durante la migrazione: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == '__main__':
    run(DB_PATH)
