"""
migrate_v2_1.py — Rimuove DEFAULT 1 da struttura_id in divisioni e apparecchi.

SQLite non permette ALTER COLUMN, quindi si ricreano le tabelle via rename+create+copy.
I dati esistenti vengono preservati. Se la struttura_id = 1 esiste nel database, i record
senza struttura esplicita sono già corretti; se non esiste, lo script avverte.

Uso:
    python migrate_v2_1.py [path/to/database.sqlite]
"""

import sqlite3
import sys
import os

# Su Windows la console non è UTF-8: senza questo, stampare accenti o
# caratteri di riquadro fa fallire lo script con UnicodeEncodeError
# (succede appena l'output viene rediretto su file o log).
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

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
        # Verifica che struttura con id=1 esista (per DB migrati da v1)
        struttura_1 = conn.execute("SELECT id FROM strutture WHERE id=1").fetchone()
        if not struttura_1:
            print("ATTENZIONE: nessuna struttura con id=1. "
                  "I record con struttura_id=1 saranno in violazione di FK dopo la migrazione.")

        # --- DIVISIONI ---
        print("Migrazione tabella divisioni...")
        # Vedi migrate.py: senza legacy_alter_table le FK delle tabelle
        # figlie finirebbero per puntare a <tabella>_old, poi eliminata.
        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.execute("ALTER TABLE divisioni RENAME TO divisioni_old")
        conn.execute("""
            CREATE TABLE divisioni (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              nome TEXT NOT NULL,
              codice TEXT NOT NULL,
              colore TEXT DEFAULT '#0ea5e9',
              descrizione TEXT,
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
        print("  OK — divisioni migrate.")

        # --- APPARECCHI ---
        # Leggi le colonne attuali di apparecchi
        cols = [row[1] for row in conn.execute("PRAGMA table_info(apparecchi)").fetchall()]
        print(f"  Colonne apparecchi: {cols}")

        if 'struttura_id' not in cols:
            print("  struttura_id non presente in apparecchi — skip (già aggiornata o non necessaria).")
        else:
            print("Migrazione tabella apparecchi...")
            # Crea nuova tabella senza DEFAULT 1
            conn.execute("ALTER TABLE apparecchi RENAME TO apparecchi_old")

            # Ricrea la tabella leggendo la definizione originale meno il DEFAULT 1
            # Usiamo le stesse colonne mantenendo l'ordine
            col_list = ', '.join(cols)
            conn.execute(f"""
                CREATE TABLE apparecchi AS
                SELECT {col_list} FROM apparecchi_old WHERE 0
            """)
            conn.execute("DROP TABLE apparecchi")

            # Crea la tabella con la definizione corretta
            conn.execute("""
                CREATE TABLE apparecchi (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  divisione_id INTEGER,
                  struttura_id INTEGER NOT NULL,
                  descrizione TEXT,
                  matricola TEXT NOT NULL,
                  numero_inventario TEXT,
                  marca TEXT NOT NULL,
                  modello TEXT NOT NULL,
                  anno_fabbricazione INTEGER,
                  classificazione TEXT CHECK(classificazione IN ('I', 'IIa', 'IIb', 'III')),
                  ubicazione TEXT,
                  stato TEXT NOT NULL DEFAULT 'funzionante'
                         CHECK(stato IN ('funzionante', 'in_manutenzione', 'da_sostituire', 'dismesso')),
                  soggetto_verifica INTEGER DEFAULT 1,
                  connesso_rete INTEGER DEFAULT 0,
                  ip_address TEXT,
                  mac_address TEXT,
                  hostname TEXT,
                  porta INTEGER,
                  protocollo TEXT DEFAULT 'http',
                  url_interfaccia TEXT,
                  fornitore TEXT,
                  codice_fornitore TEXT,
                  garanzia_scadenza TEXT,
                  contratto_manutenzione TEXT,
                  note TEXT,
                  foto_path TEXT,
                  created_by INTEGER,
                  updated_by INTEGER,
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
            print("  OK — apparecchi migrati.")

        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
        print("\nMigrazione v2.1 completata con successo.")

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
