"""
MedInventory - Database seeder
Run once to create default admin user and sample divisions.

Usage: python seed.py
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from models import query_one, query_all, execute
from werkzeug.security import generate_password_hash


def seed_database():
    """Populate the database with initial data."""
    app = create_app()

    with app.app_context():
        # ------------------------------------------------------------------
        # Divisioni (only if none exist)
        # ------------------------------------------------------------------
        existing = query_all("SELECT id FROM divisioni")
        if not existing:
            print("Creazione divisioni di esempio...")
            execute(
                """INSERT INTO divisioni (nome, codice, colore, descrizione)
                   VALUES (?, ?, ?, ?)""",
                ('Divisione 1', 'DIV1', '#0ea5e9',
                 'Prima divisione (rinominare da pannello admin)')
            )
            execute(
                """INSERT INTO divisioni (nome, codice, colore, descrizione)
                   VALUES (?, ?, ?, ?)""",
                ('Divisione 2', 'DIV2', '#10b981',
                 'Seconda divisione (rinominare da pannello admin)')
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
                """INSERT INTO utenti (email, password_hash, nome, cognome, ruolo, primo_accesso)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ('admin@medinventory.local', password_hash,
                 'Amministratore', 'Sistema', 'admin', 1)
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
