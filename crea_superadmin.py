"""
crea_superadmin.py — Crea o reimposta il superadmin di MedInventory.

Uso:
    python crea_superadmin.py
"""

import os
import sys
import getpass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def valida_password(password):
    errori = []
    if len(password) < 8:
        errori.append("almeno 8 caratteri")
    if not any(c.isupper() for c in password):
        errori.append("almeno una lettera maiuscola")
    if not any(c.isdigit() for c in password):
        errori.append("almeno un numero")
    return errori


def main():
    from app import create_app
    from models import query_one, execute
    from werkzeug.security import generate_password_hash, check_password_hash

    print("=" * 55)
    print("  MedInventory — Creazione superadmin")
    print("=" * 55)

    app = create_app()
    with app.app_context():
        # Verifica superadmin esistente
        esistente = query_one(
            "SELECT id, email, attivo FROM utenti WHERE ruolo = 'superadmin'"
        )

        if esistente:
            print(f"\nSuperadmin esistente: {esistente['email']}")
            print(f"Stato: {'attivo' if esistente['attivo'] else 'disattivato'}")
            scelta = input("\nVuoi reimpostare la password? [s/N] ").strip().lower()
            if scelta not in ('s', 'si', 'sì', 'y', 'yes'):
                print("Operazione annullata.")
                return

            # Reimposta password
            while True:
                nuova = getpass.getpass("Nuova password: ")
                errori = valida_password(nuova)
                if errori:
                    print(f"Password non valida: {', '.join(errori)}.")
                    continue
                conferma = getpass.getpass("Conferma password: ")
                if nuova != conferma:
                    print("Le password non coincidono.")
                    continue
                break

            execute(
                "UPDATE utenti SET password_hash = ?, attivo = 1, primo_accesso = 0, updated_at = datetime('now') WHERE id = ?",
                (generate_password_hash(nuova), esistente['id'])
            )
            print(f"\n✓ Password superadmin aggiornata: {esistente['email']}")

        else:
            print("\nNessun superadmin trovato. Creazione nuovo superadmin.")

            # Email
            while True:
                email = input("Email superadmin [superadmin@medinventory.local]: ").strip()
                if not email:
                    email = 'superadmin@medinventory.local'
                conflitto = query_one("SELECT id FROM utenti WHERE email = ?", (email,))
                if conflitto:
                    print(f"Email già in uso da un altro utente.")
                    continue
                break

            # Password
            while True:
                password = getpass.getpass("Password (min 8 car., 1 maiuscola, 1 numero): ")
                errori = valida_password(password)
                if errori:
                    print(f"Password non valida: {', '.join(errori)}.")
                    continue
                conferma = getpass.getpass("Conferma password: ")
                if password != conferma:
                    print("Le password non coincidono.")
                    continue
                break

            execute(
                """INSERT INTO utenti
                       (email, password_hash, nome, cognome, ruolo, struttura_id, primo_accesso, attivo)
                   VALUES (?, ?, ?, ?, 'superadmin', NULL, 0, 1)""",
                (email, generate_password_hash(password), 'Super', 'Admin')
            )
            print(f"\n✓ Superadmin creato: {email}")

        print("\nRiavvia l'applicazione e accedi con le credenziali superadmin.")


if __name__ == '__main__':
    main()
