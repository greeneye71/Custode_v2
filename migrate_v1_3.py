"""
MedInventory - Migration v1.3.0
Aggiunge colonna verbale_path alla tabella manutenzioni
per supportare l'allegato del verbale PDF.

Esegui PRIMA di avviare la nuova versione dell'applicazione:
    python migrate_v1_3.py
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
    backup_path = f"{db_path}.bak_v1_3_{timestamp}"
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
    cur = conn.cursor()

    try:
        # Check if migration already applied
        if column_exists(cur, 'manutenzioni', 'verbale_path'):
            print("Migrazione già applicata (colonna 'verbale_path' già presente in manutenzioni).")
            conn.close()
            return True

        # Add verbale_path column
        cur.execute("ALTER TABLE manutenzioni ADD COLUMN verbale_path TEXT")
        print("Aggiunta colonna 'verbale_path' alla tabella 'manutenzioni'.")

        conn.commit()

        # Integrity check
        cur.execute("PRAGMA integrity_check")
        result = cur.fetchone()[0]
        if result != 'ok':
            raise Exception(f"Integrity check fallito: {result}")

        conn.close()

        # Create verbali uploads directory
        base_dir = os.path.dirname(os.path.abspath(db_path))
        verbali_dir = os.path.join(base_dir, '..', 'uploads', 'verbali')
        os.makedirs(verbali_dir, exist_ok=True)
        print(f"Cartella uploads/verbali creata.")

        print(f"\nMigrazione completata con successo!")
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
