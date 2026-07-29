"""
MedInventory - Database access layer
Provides connection management and query helpers for SQLite.
"""

import sqlite3
import os
import logging
from flask import g, current_app

logger = logging.getLogger('medinventory.models')


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


def upload_subdir(subdir, struttura_id=None, uploads_base=None, single_struttura=None):
    """
    Restituisce (abs_path, rel_prefix) per i file di upload.

    Modalità single-struttura  → uploads/{subdir}/         rel: {subdir}
    Modalità multi-struttura   → uploads/strutture/{id}/{subdir}/  rel: strutture/{id}/{subdir}

    Può essere usata sia in contesto Flask (senza parametri extra) sia fuori Flask
    (passando uploads_base e single_struttura esplicitamente, es. in email_monitor).
    """
    if uploads_base is None:
        uploads_base = current_app.config['UPLOADS_PATH']
    if single_struttura is None:
        single_struttura = current_app.config.get('APP_CONFIG', {}).get('single_struttura', False)

    if single_struttura or struttura_id is None:
        abs_path = os.path.join(uploads_base, subdir)
        rel_prefix = subdir
    else:
        abs_path = os.path.join(uploads_base, 'strutture', str(struttura_id), subdir)
        rel_prefix = f"strutture/{struttura_id}/{subdir}"

    os.makedirs(abs_path, exist_ok=True)
    return abs_path, rel_prefix


def percorso_logo_struttura(struttura):
    """Percorso assoluto del logo di una struttura, o None se non ne ha uno.

    Condivisa fra le stampe manuali (dentro una request, via g.struttura) e il
    report dello scheduler (fuori da una request, dentro un app_context aperto
    a mano): in entrambi i casi current_app e' disponibile, ma viene toccato
    solo quando la struttura ha davvero un logo_path, cosi' l'helper resta
    innocuo per chi importa models.py fuori da Flask e non lo invoca mai con
    una struttura che ne ha uno.

    Il chiamante deve comunque verificare l'esistenza del file (lo fa gia'
    ReportPDF.header()): un logo cancellato da disco non deve impedire la
    stampa.
    """
    if not struttura or not struttura.get('logo_path'):
        return None
    return os.path.join(current_app.config['UPLOADS_PATH'],
                        struttura['logo_path'].replace('/', os.sep))


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
    try:
        db = get_db()
        row = db.execute(sql, params).fetchone()
        if row is None:
            return None
        return dict(row)
    except Exception as e:
        logger.error(f"query_one failed: {e} | SQL: {sql[:100]!r}")
        raise


def query_all(sql, params=()):
    """Execute a query and return all rows as list of dicts."""
    try:
        db = get_db()
        rows = db.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"query_all failed: {e} | SQL: {sql[:100]!r}")
        raise


def execute(sql, params=()):
    """Execute an INSERT/UPDATE/DELETE and return the cursor.
    Use cursor.lastrowid for inserts, cursor.rowcount for updates/deletes.
    """
    try:
        db = get_db()
        cursor = db.execute(sql, params)
        db.commit()
        return cursor
    except Exception as e:
        logger.error(f"execute failed: {e} | SQL: {sql[:100]!r}")
        raise


def _fix_import_tables():
    """Ripara import_history e import_preview se danneggiate dalla migrazione v1.3.2.

    SQLite >= 3.26 aggiorna automaticamente le FK dei figli quando si rinomina
    una tabella padre.  migrate_v1_3_2.py rinominava import_history in
    _import_history_old e poi la ricreava; SQLite aggiornava la FK in
    import_preview per puntare a _import_history_old.  Se la migrazione veniva
    interrotta o il recovery precedente eliminava _import_history_old senza
    ricreare import_preview, l'app smetteva di funzionare con
    "no such table: main._import_history_old".
    """
    db = get_db()

    def table_exists(name):
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    def get_table_sql(name):
        row = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row[0] if row else None

    try:
        # Abilita legacy_alter_table per evitare nuovi aggiornamenti FK automatici
        db.execute("PRAGMA legacy_alter_table = ON")

        # 1. Crea import_history se mancante
        if not table_exists('import_history'):
            logger.warning("import_history mancante — creazione automatica")
            db.execute("""CREATE TABLE import_history (
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
            )""")
            if table_exists('_import_history_old'):
                db.execute(
                    "INSERT INTO import_history SELECT * FROM _import_history_old")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_import_history_tipo ON import_history(tipo_import)")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_import_history_stato ON import_history(stato)")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_import_history_created ON import_history(created_at)")
            db.commit()

        # 2. Rimuovi _import_history_old residua
        if table_exists('_import_history_old'):
            db.execute("DROP TABLE _import_history_old")
            db.commit()

        # 3. Ricrea import_preview se la FK punta a _import_history_old
        ip_sql = get_table_sql('import_preview')
        if ip_sql and '_import_history_old' in ip_sql:
            logger.warning("import_preview ha FK rotta — ricreazione automatica")
            db.execute(
                "ALTER TABLE import_preview RENAME TO _import_preview_old")
            db.execute("""CREATE TABLE import_preview (
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
            )""")
            db.execute(
                "INSERT INTO import_preview SELECT * FROM _import_preview_old")
            db.execute("DROP TABLE _import_preview_old")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_import_preview_import ON import_preview(import_id)")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_import_preview_stato ON import_preview(stato)")
            db.commit()

    except Exception as e:
        logger.error(f"Errore in _fix_import_tables: {e}")
    finally:
        try:
            db.execute("PRAGMA legacy_alter_table = OFF")
        except Exception:
            pass


def _ripara_fk_orfane():
    """Ripara le FK che puntano a tabelle '<nome>_old' non più esistenti.

    SQLite >= 3.26 riscrive i riferimenti FK delle tabelle figlie quando si
    rinomina la tabella padre. Le migrazioni che rinominavano utenti/divisioni
    senza 'PRAGMA legacy_alter_table = ON' lasciavano quindi sessioni,
    utenti_divisioni, log_attivita e apparecchi a puntare a utenti_old /
    divisioni_old, subito dopo eliminate. Con foreign_keys=ON ogni INSERT su
    quelle tabelle fallisce con "no such table: main.utenti_old": il login
    diventa impossibile e l'applicazione inutilizzabile.

    Qui si corregge solo il testo della clausola REFERENCES: nessun dato viene
    spostato e gli indici restano al loro posto.
    """
    import re
    db = get_db()
    tabelle = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

    riparazioni = []   # (tabella, nome_errato, nome_corretto)
    for nome, sql in db.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"):
        for riferita in set(re.findall(r'REFERENCES\s+"?(\w+)"?', sql, re.I)):
            if riferita in tabelle:
                continue
            m = re.match(r'^_?(?P<base>.+?)_old(?:_\w+)?$', riferita)
            if m and m.group('base') in tabelle:
                riparazioni.append((nome, riferita, m.group('base')))

    if not riparazioni:
        return

    for tabella, errato, corretto in riparazioni:
        logger.warning(
            f"FK danneggiata: {tabella} -> {errato} (inesistente), correggo in {corretto}")

    try:
        versione = db.execute("PRAGMA schema_version").fetchone()[0]
        db.execute("PRAGMA writable_schema = ON")
        for tabella, errato, corretto in riparazioni:
            for vecchio, nuovo in ((f'"{errato}"', f'"{corretto}"'),
                                   (f'REFERENCES {errato}', f'REFERENCES {corretto}')):
                db.execute(
                    "UPDATE sqlite_master SET sql = replace(sql, ?, ?) "
                    "WHERE type = 'table' AND name = ?",
                    (vecchio, nuovo, tabella))
        db.execute(f"PRAGMA schema_version = {versione + 1}")
        db.execute("PRAGMA writable_schema = OFF")
        db.commit()
    except Exception as e:
        logger.error(f"Riparazione FK fallita: {e}")
        try:
            db.execute("PRAGMA writable_schema = OFF")
        except Exception:
            pass
        return

    # Lo schema riscritto è visibile solo a una connessione nuova.
    try:
        db.close()
    except Exception:
        pass
    g.pop('db', None)
    nuovo_db = get_db()
    problemi = nuovo_db.execute("PRAGMA integrity_check").fetchone()[0]
    if problemi != 'ok':
        logger.error(f"integrity_check dopo la riparazione FK: {problemi}")
    else:
        logger.info(f"Riparate {len(riparazioni)} FK orfane; integrità del database confermata.")


def get_schema_version():
    """Ritorna la versione dello schema DB (PRAGMA user_version).
    Convenzione: major*100 + minor*10 + patch  →  v1.4.3 = 143.
    Restituisce 0 per DB creati prima dell'introduzione del versioning.
    """
    db = get_db()
    return db.execute("PRAGMA user_version").fetchone()[0]


def _matricola_unique_solo(db):
    """Ritorna True se esiste ancora un indice UNIQUE sulla sola colonna matricola
    (schema pre-v1.4.3). Usato per rilevare se migrate_v1_4.py è stato eseguito."""
    for idx in db.execute("PRAGMA index_list(apparecchi)").fetchall():
        if not idx[2]:  # not unique
            continue
        cols = db.execute(f"PRAGMA index_info({idx[1]})").fetchall()
        if len(cols) == 1 and cols[0][2] == 'matricola':
            return True
    return False


def apply_schema_updates():
    """Applica aggiornamenti incrementali allo schema (idempotente) all'avvio."""
    _fix_import_tables()
    _ripara_fk_orfane()
    db = get_db()
    # Mark any import jobs left in 'processing' state by a previous server run as failed.
    # Background threads are killed on shutdown, so 'processing' records are stale.
    try:
        db.execute(
            "UPDATE import_history SET stato='failed', errori_dettaglio='Interrotto al riavvio del server' "
            "WHERE stato='processing'"
        )
        db.commit()
    except Exception as e:
        logger.warning(f"Cleanup import_history processing: {e}")
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
        """CREATE TABLE IF NOT EXISTS tecnici_strutture (
            tecnico_id   INTEGER NOT NULL,
            struttura_id INTEGER NOT NULL,
            PRIMARY KEY (tecnico_id, struttura_id),
            FOREIGN KEY (tecnico_id)   REFERENCES utenti(id) ON DELETE CASCADE,
            FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE
        )""",
        "CREATE INDEX IF NOT EXISTS idx_tecnici_strutture_tecnico   ON tecnici_strutture(tecnico_id)",
        "CREATE INDEX IF NOT EXISTS idx_tecnici_strutture_struttura ON tecnici_strutture(struttura_id)",
        # struttura_id esplicita su import_history: l'isolamento multi-tenant non
        # può dipendere da divisione_id, che è NULL per gli import da email.
        "ALTER TABLE import_history ADD COLUMN struttura_id INTEGER REFERENCES strutture(id)",
        "CREATE INDEX IF NOT EXISTS idx_import_history_struttura ON import_history(struttura_id)",
        """UPDATE import_history SET struttura_id = (
               SELECT d.struttura_id FROM divisioni d WHERE d.id = import_history.divisione_id
           ) WHERE struttura_id IS NULL AND divisione_id IS NOT NULL""",
        # Import storici senza divisione (es. code email) in installazioni con una
        # sola struttura: assegnali a quella struttura, altrimenti resterebbero
        # invisibili a tutti.
        """UPDATE import_history SET struttura_id = (SELECT id FROM strutture WHERE attiva = 1)
           WHERE struttura_id IS NULL
             AND (SELECT COUNT(*) FROM strutture WHERE attiva = 1) = 1""",
        # Ricrea la vista prossime_scadenze per mostrare solo l'ultima manutenzione/verifica
        # per ogni (apparecchio, tipo) invece di tutti i record storici
        "DROP VIEW IF EXISTS prossime_scadenze",
        """CREATE VIEW prossime_scadenze AS
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
ORDER BY prossima_scadenza ASC""",
        # Logo mostrato nella testata dei prospetti stampati.
        "ALTER TABLE strutture ADD COLUMN logo_path TEXT",
    ]
    for sql in migrations:
        try:
            db.execute(sql)
        except Exception as e:
            logger.warning(f"Migration step skipped (may already be applied): {e}")
    db.commit()

    # Aggiunge 'tecnico' al CHECK ruolo di utenti se non già presente (v2.2)
    try:
        row = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='utenti'"
        ).fetchone()
        if row and "'tecnico'" not in row[0]:
            logger.info("Migrazione v2.2: aggiunta ruolo tecnico a tabella utenti...")
            cols = [r[1] for r in db.execute("PRAGMA table_info(utenti)").fetchall()]
            col_list = ', '.join(cols)
            # legacy_alter_table=ON: impedisce a SQLite 3.26+ di aggiornare
            # automaticamente i riferimenti FK nelle tabelle figlie durante il RENAME,
            # altrimenti sessioni/utenti_divisioni punterebbero a utenti_old_v22.
            db.execute("PRAGMA legacy_alter_table = ON")
            db.execute("PRAGMA foreign_keys = OFF")
            db.execute("ALTER TABLE utenti RENAME TO utenti_old_v22")
            db.execute("""
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
            db.execute(f"INSERT INTO utenti SELECT {col_list} FROM utenti_old_v22")
            db.execute("DROP TABLE utenti_old_v22")
            db.execute("PRAGMA legacy_alter_table = OFF")
            db.execute("PRAGMA foreign_keys = ON")
            db.commit()
            logger.info("Migrazione v2.2 completata.")
    except Exception as e:
        logger.error(f"Migrazione v2.2 utenti (ruolo tecnico) FALLITA: {e}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        try:
            db.execute("PRAGMA legacy_alter_table = OFF")
            db.execute("PRAGMA foreign_keys = ON")
        except Exception:
            pass

    # Utenti rimasti senza struttura: li disattiva e li segnala.
    # Ci si arriva eliminando una struttura (utenti.struttura_id era
    # ON DELETE SET NULL fino alla 2.6) oppure importando dati incompleti.
    # Dal Task 1 un account cosi' non vede piu' alcun dato, ma continua ad
    # autenticarsi: disattivarlo lo rende visibile a chi amministra, invece
    # di lasciarlo come un accesso silenzioso che nessuno controlla.
    # superadmin e tecnico sono esclusi: hanno struttura_id NULL per progetto.
    try:
        orfani = db.execute(
            "SELECT id, email FROM utenti "
            "WHERE struttura_id IS NULL AND attivo = 1 "
            "  AND ruolo IN ('admin', 'utente')"
        ).fetchall()
        if orfani:
            db.execute(
                "UPDATE utenti SET attivo = 0 "
                "WHERE struttura_id IS NULL AND attivo = 1 "
                "  AND ruolo IN ('admin', 'utente')"
            )
            db.commit()
            for riga in orfani:
                logger.warning(
                    f"Utente senza struttura disattivato: {riga['email']} (id {riga['id']}). "
                    "Riassegnalo a una struttura per riabilitarlo."
                )
    except Exception as e:
        logger.error(f"Disattivazione utenti orfani fallita: {e}", exc_info=True)

    # v2.6: utenti.struttura_id da ON DELETE SET NULL a RESTRICT.
    # SQLite non sa cambiare una FK con ALTER TABLE: va ricostruita la tabella.
    # legacy_alter_table = ON e' obbligatorio: da SQLite 3.26 il RENAME
    # riscrive da solo le FK delle tabelle figlie, e sessioni e
    # utenti_divisioni finirebbero a puntare a utenti_old_26 lasciando
    # l'applicazione senza login. Stessa forma della migrazione v2.2 sopra.
    try:
        row = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='utenti'"
        ).fetchone()
        if row and 'ON DELETE RESTRICT' not in row[0]:
            logger.info("Migrazione v2.6: struttura_id di utenti a ON DELETE RESTRICT...")
            cols = [r[1] for r in db.execute("PRAGMA table_info(utenti)").fetchall()]
            col_list = ', '.join(cols)
            db.execute("PRAGMA legacy_alter_table = ON")
            db.execute("PRAGMA foreign_keys = OFF")
            db.execute("ALTER TABLE utenti RENAME TO utenti_old_26")
            db.execute("""
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
                  FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE RESTRICT,
                  FOREIGN KEY (divisione_default_id) REFERENCES divisioni(id) ON DELETE SET NULL
                )
            """)
            db.execute(f"INSERT INTO utenti SELECT {col_list} FROM utenti_old_26")
            db.execute("DROP TABLE utenti_old_26")
            db.execute("PRAGMA legacy_alter_table = OFF")
            db.execute("PRAGMA foreign_keys = ON")
            db.commit()
            logger.info("Migrazione v2.6 completata.")
    except Exception as e:
        logger.error(f"Migrazione v2.6 (FK utenti.struttura_id) FALLITA: {e}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        try:
            db.execute("PRAGMA legacy_alter_table = OFF")
            db.execute("PRAGMA foreign_keys = ON")
        except Exception:
            pass

    # Versioning schema DB tramite PRAGMA user_version
    # Convenzione: major*100 + minor*10 + patch  (v1.4.3 → 143)
    schema_ver = db.execute("PRAGMA user_version").fetchone()[0]
    if schema_ver == 0:
        # DB precedente all'introduzione del versioning: rileva lo stato reale
        if not _matricola_unique_solo(db):
            # Schema già allineato a v1.4.3 (o installazione fresca)
            db.execute("PRAGMA user_version = 143")
            db.commit()
            logger.info("Schema DB: versione impostata a 143 (v1.4.3)")
        else:
            # migrate_v1_4.py non ancora eseguito
            logger.warning(
                "Schema DB non aggiornato: eseguire 'python migrate_v1_4.py' "
                "per rimuovere il vincolo UNIQUE su matricola (richiesto da v1.4.3)"
            )
    else:
        logger.debug(f"Schema DB versione {schema_ver}")


def filtro_divisione(table_alias='a'):
    """Clausola WHERE e parametri per limitare una query allo scope dell'utente.

    Unico punto del progetto che decide cosa un utente puo' vedere. Fino alla
    2.5 ne esistevano quattro copie divergenti, una per blueprint, e tutte
    contenevano lo stesso difetto: per un admin o tecnico senza struttura
    attiva restituivano ("", []), cioe' NESSUN filtro, e la query tornava gli
    apparecchi di tutte le strutture. Lo stato si raggiunge sia eliminando la
    struttura (utenti.struttura_id e' ON DELETE SET NULL) sia semplicemente
    disattivandola, perche' auth.py porta g.struttura_id a None quando la
    struttura non e' attiva.

    In un'applicazione multi-tenant l'assenza di scope significa "nessun
    dato", mai "tutti i dati".

    Il superadmin che non impersona alcuna struttura ricade anche lui in
    "AND 1=0": e' gia' il comportamento odierno di tre blueprint su quattro,
    e la via per vedere i dati di una struttura e' entrarci.

    Restituisce (clausola, parametri). La clausola inizia sempre con "AND ".
    """
    div = getattr(g, 'divisione_attiva', None)
    if div and div.get('id') != 'tutte':
        return f"AND {table_alias}.divisione_id = ?", [div['id']]

    if getattr(g, 'user', {}).get('ruolo') in ('admin', 'tecnico', 'superadmin'):
        struttura_id = getattr(g, 'struttura_id', None)
        if struttura_id:
            return f"AND {table_alias}.struttura_id = ?", [struttura_id]
        return "AND 1=0", []

    ids = [d['id'] for d in getattr(g, 'divisioni', [])]
    if not ids:
        return "AND 1=0", []
    segnaposto = ','.join('?' * len(ids))
    return f"AND {table_alias}.divisione_id IN ({segnaposto})", ids


def apparecchio_accessibile(apparecchio_id):
    """Verifica che l'apparecchio sia nello scope dell'utente corrente.

    Controlla sia la struttura (isolamento multi-tenant) sia, per i ruoli non
    amministrativi, l'appartenenza a una divisione accessibile.
    Restituisce la riga dell'apparecchio, oppure None se non accessibile.
    """
    struttura_id = getattr(g, 'struttura_id', None)
    if struttura_id:
        app_row = query_one(
            "SELECT * FROM apparecchi WHERE id = ? AND struttura_id = ?",
            (apparecchio_id, struttura_id)
        )
    elif g.user['ruolo'] == 'superadmin':
        # Stato normale del superadmin che non sta impersonando nessuno.
        app_row = query_one("SELECT * FROM apparecchi WHERE id = ?", (apparecchio_id,))
    else:
        # Admin, tecnico o utente senza struttura attiva: nessun apparecchio.
        # La condizione precedente era "struttura_id = ? OR ? IS NULL", che
        # con struttura_id None accettava qualunque riga.
        return None
    if not app_row:
        return None
    if g.user['ruolo'] not in ('admin', 'superadmin', 'tecnico'):
        accessible_ids = [d['id'] for d in getattr(g, 'divisioni', [])]
        if app_row['divisione_id'] not in accessible_ids:
            return None
    return app_row


def log_attivita(utente_id, azione, entita, entita_id=None, dettagli=None,
                 ip_address=None, struttura_id=None):
    """Log an activity to the log_attivita table."""
    execute(
        """INSERT INTO log_attivita
               (utente_id, azione, entita, entita_id, dettagli, ip_address, struttura_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (utente_id, azione, entita, entita_id, dettagli, ip_address, struttura_id)
    )


# ---------------------------------------------------------------------------
# Strutture config helpers
# ---------------------------------------------------------------------------

def get_struttura_config(struttura_id, chiave, default=None):
    """Legge un valore di configurazione per-struttura.
    Se non presente, restituisce il valore globale da APP_CONFIG o il default.
    """
    row = query_one(
        "SELECT valore FROM strutture_config WHERE struttura_id = ? AND chiave = ?",
        (struttura_id, chiave)
    )
    if row and row['valore'] is not None:
        return row['valore']
    # Fallback al config globale
    try:
        return (current_app.config.get('APP_CONFIG') or {}).get(chiave, default)
    except RuntimeError:
        return default


def set_struttura_config(struttura_id, chiave, valore):
    """Inserisce o aggiorna un valore di configurazione per-struttura."""
    execute(
        """INSERT INTO strutture_config (struttura_id, chiave, valore)
           VALUES (?, ?, ?)
           ON CONFLICT(struttura_id, chiave) DO UPDATE SET valore = excluded.valore""",
        (struttura_id, chiave, valore)
    )


def get_struttura_config_all(struttura_id):
    """Restituisce tutti i valori di configurazione per-struttura come dict."""
    rows = query_all(
        "SELECT chiave, valore FROM strutture_config WHERE struttura_id = ?",
        (struttura_id,)
    )
    return {r['chiave']: r['valore'] for r in rows}
