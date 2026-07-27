"""
MedInventory - Database seeder
Run once to create default admin user and sample divisions.

Usage: python seed.py
"""

import os
import sys
import shutil

# Add project root to path
_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE)


def _ensure_config():
    """Crea config.json da config.example.json se mancante (prima installazione)."""
    config_path = os.path.join(_BASE, 'config.json')
    example_path = os.path.join(_BASE, 'config.example.json')
    if not os.path.exists(config_path):
        if not os.path.exists(example_path):
            print("ERRORE: config.example.json non trovato. Il repository potrebbe essere incompleto.")
            sys.exit(1)
        shutil.copy2(example_path, config_path)
        print(f"Creato config.json da config.example.json")
        print("  -> Le chiavi crittografiche verranno generate automaticamente.")
        print("  -> Modificare config.local.json per personalizzare host, porta e API key AI.\n")

    # Crea la directory data/ se mancante
    data_dir = os.path.join(_BASE, 'data')
    os.makedirs(data_dir, exist_ok=True)


from app import create_app
from models import query_one, query_all, execute
from werkzeug.security import generate_password_hash

# Su Windows la console non è UTF-8: senza questo, stampare accenti o
# caratteri di riquadro fa fallire lo script con UnicodeEncodeError
# (succede appena l'output viene rediretto su file o log).
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def seed_database():
    """Populate the database with initial data."""
    _ensure_config()
    app = create_app()

    with app.app_context():
        # ------------------------------------------------------------------
        # Struttura di default (v2: necessaria prima di divisioni e utenti)
        # ------------------------------------------------------------------
        struttura = query_one("SELECT id FROM strutture LIMIT 1")
        if not struttura:
            print("Creazione struttura di default...")
            cur = execute(
                """INSERT INTO strutture (nome, codice, descrizione, modalita, attiva)
                   VALUES (?, ?, ?, ?, ?)""",
                ('Struttura Principale', 'DEFAULT',
                 'Struttura predefinita (rinominare da Amministrazione → Strutture)',
                 'avanzata', 1)
            )
            struttura_id = cur.lastrowid
            print(f"  -> Struttura creata (id={struttura_id})")
        else:
            struttura_id = struttura['id']
            print(f"Struttura esistente (id={struttura_id}), skip.")

        # ------------------------------------------------------------------
        # Divisioni (only if none exist)
        # ------------------------------------------------------------------
        existing = query_all("SELECT id FROM divisioni")
        if not existing:
            print("Creazione divisioni di esempio...")
            execute(
                """INSERT INTO divisioni (nome, codice, colore, descrizione, struttura_id)
                   VALUES (?, ?, ?, ?, ?)""",
                ('Divisione 1', 'DIV1', '#0ea5e9',
                 'Prima divisione (rinominare da pannello admin)', struttura_id)
            )
            execute(
                """INSERT INTO divisioni (nome, codice, colore, descrizione, struttura_id)
                   VALUES (?, ?, ?, ?, ?)""",
                ('Divisione 2', 'DIV2', '#10b981',
                 'Seconda divisione (rinominare da pannello admin)', struttura_id)
            )
            print("  -> 2 divisioni create")
        else:
            print(f"Divisioni esistenti: {len(existing)}, skip.")

        # ------------------------------------------------------------------
        # Admin user (only if no users exist)
        # ------------------------------------------------------------------
        existing_users = query_all("SELECT id FROM utenti")
        if not existing_users:
            print("Creazione utente admin predefinito...")
            password_hash = generate_password_hash('admin123')
            cursor = execute(
                """INSERT INTO utenti (email, password_hash, nome, cognome, ruolo, struttura_id, primo_accesso)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ('admin@medinventory.local', password_hash,
                 'Amministratore', 'Sistema', 'admin', struttura_id, 1)
            )
            admin_id = cursor.lastrowid

            # Assign admin to all divisions
            divisioni = query_all("SELECT id FROM divisioni WHERE attiva = 1")
            for div in divisioni:
                execute(
                    """INSERT INTO utenti_divisioni (utente_id, divisione_id, ruolo_divisione)
                       VALUES (?, ?, ?)""",
                    (admin_id, div['id'], 'admin')
                )

            print(f"  -> Admin creato: admin@medinventory.local / admin123")
            print(f"  -> Assegnato a {len(divisioni)} divisioni")
            print("  -> IMPORTANTE: cambiare la password al primo accesso!")
        else:
            print(f"Utenti esistenti: {len(existing_users)}, skip.")

        print("\nSeed completato.")


if __name__ == '__main__':
    seed_database()
