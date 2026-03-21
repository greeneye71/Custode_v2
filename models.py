"""
MedInventory - Database access layer
Provides connection management and query helpers for SQLite.
"""

import sqlite3
import os
from flask import g, current_app


def get_db():
    """Get or create a database connection for the current request."""
    if 'db' not in g:
        db_path = current_app.config['DATABASE_PATH']
        g.db = sqlite3.connect(db_path)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 5000")
    return g.db


def close_db(e=None):
    """Close the database connection at the end of the request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    """Initialize the database from schema.sql."""
    db = get_db()
    schema_path = os.path.join(current_app.root_path, 'schema.sql')
    with open(schema_path, 'r', encoding='utf-8') as f:
        # Split by semicolons and execute each statement
        # (needed because executescript doesn't support PRAGMA in all cases)
        sql = f.read()
    db.executescript(sql)
    db.commit()


def query_one(sql, params=()):
    """Execute a query and return one row as dict, or None."""
    db = get_db()
    row = db.execute(sql, params).fetchone()
    if row is None:
        return None
    return dict(row)


def query_all(sql, params=()):
    """Execute a query and return all rows as list of dicts."""
    db = get_db()
    rows = db.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def execute(sql, params=()):
    """Execute an INSERT/UPDATE/DELETE and return the cursor.
    Use cursor.lastrowid for inserts, cursor.rowcount for updates/deletes.
    """
    db = get_db()
    cursor = db.execute(sql, params)
    db.commit()
    return cursor


def apply_schema_updates():
    """Applica aggiornamenti incrementali allo schema (idempotente) all'avvio."""
    db = get_db()
    migrations = [
        "ALTER TABLE apparecchi ADD COLUMN soggetto_verifica INTEGER NOT NULL DEFAULT 1",
        """CREATE TABLE IF NOT EXISTS accessori (
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
        )""",
        "CREATE INDEX IF NOT EXISTS idx_accessori_apparecchio ON accessori(apparecchio_id)",
    ]
    for sql in migrations:
        try:
            db.execute(sql)
        except Exception:
            pass
    db.commit()


def log_attivita(utente_id, azione, entita, entita_id=None, dettagli=None, ip_address=None):
    """Log an activity to the log_attivita table."""
    execute(
        """INSERT INTO log_attivita (utente_id, azione, entita, entita_id, dettagli, ip_address)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (utente_id, azione, entita, entita_id, dettagli, ip_address)
    )
