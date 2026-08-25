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
    base = current_app.config['UPLOADS_PATH']
    percorso = os.path.join(base, struttura['logo_path'].replace('/', os.sep))
    radice = os.path.realpath(base)
    assoluto = os.path.realpath(percorso)
    # Stesso controllo di struttura_service._allegato_nel_perimetro (Task 7):
    # un percorso composto da un dato del database va verificato prima di
    # essere usato, non solo composto. Il confronto tiene conto del caso in
    # cui il risolto COINCIDE con la radice: un semplice
    # startswith(radice + os.sep) da solo tratterebbe quel caso come "fuori",
    # perche' la stringa uguale non inizia con se stessa piu' un separatore.
    if assoluto != radice and not assoluto.startswith(radice + os.sep):
        return None
    return percorso


def init_db():
    """Initialize the database from schema.sql."""
    db = get_db()
    schema_path = os.path.join(current_app.root_path, 'schema.sql')
    with open(schema_path, 'r', encoding='utf-8') as f:
        # Split by semicolons and execute each statement
        # (needed because executescript doesn't support PRAGMA in all cases)
        sql = f.read()
    try:
        db.executescript(sql)
    except sqlite3.OperationalError as e:
        # Un database che non ha ancora ricevuto le migrazioni autonome muore
        # qui, perche' schema.sql crea indici su colonne che quelle migrazioni
        # devono ancora aggiungere o rinominare (per esempio descrizione, che
        # migrate_v1_2.py ottiene rinominando codice_interno). Il messaggio di
        # SQLite - "no such column: descrizione" - non dice all'operatore cosa
        # fare, e l'applicazione non parte affatto: e' il momento peggiore per
        # un errore criptico. Le migrazioni NON vengono eseguite qui: sono
        # scelte deliberate dell'operatore, fanno un backup e possono
        # rinominare colonne, quindi indovinarle rischierebbe di lasciare i
        # dati dove nessuno li cerchera' piu'.
        if 'no such column' in str(e):
            raise RuntimeError(
                f"Il database non e' aggiornato allo schema di questa versione "
                f"({e}). Le migrazioni non vengono applicate automaticamente. "
                f"Esegui prima:\n"
                f"    python migrate.py --check    (analizza, non modifica nulla)\n"
                f"    python migrate.py            (applica, con backup)\n"
                f"Vedi la sezione \"Migrazioni\" del README."
            ) from e
        raise
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
        # Cancellazione degli utenti (2.6.2): la riga sopravvive come voce
        # storica, questa colonna la distingue da un utente normale.
        "ALTER TABLE utenti ADD COLUMN eliminato_il DATETIME",
        # --- 2.6.2: la posta ha un server solo, quello di sistema ---
        # Prima si converte il vecchio interruttore nella coppia nuova, poi si
        # cancella la riga sorgente. L'ordine e' quello che rende la migrazione
        # idempotente: apply_schema_updates() gira a ogni avvio, e senza la
        # cancellazione il primo riavvio dopo che l'operatore ha spento gli
        # avvisi glieli riaccenderebbe. Formato 'testo' perche' e' quello che
        # quell'interruttore accendeva davvero (scheduler._invia_digest):
        # chi riceve un digest di testo deve continuare a ricevere quello.
        """INSERT OR IGNORE INTO strutture_config (struttura_id, chiave, valore)
           SELECT struttura_id, 'avvisi_scadenza_attivi', '1' FROM strutture_config
           WHERE chiave = 'report_schedulato_attivo' AND valore = '1'""",
        """INSERT OR IGNORE INTO strutture_config (struttura_id, chiave, valore)
           SELECT struttura_id, 'avvisi_scadenza_formato', 'testo' FROM strutture_config
           WHERE chiave = 'report_schedulato_attivo' AND valore = '1'""",
        # Le chiavi del server non le legge piu' nessuno: lasciarle significa
        # tenere configurazione morta che sembra viva, con dentro una
        # credenziale cifrata che finirebbe in ogni archivio esportato.
        # report_pdf_attivo esce di scena senza conversione: nessun modulo e
        # nessun template l'ha mai scritta, quindi non c'e' niente da salvare.
        """DELETE FROM strutture_config WHERE chiave IN (
               'smtp_host', 'smtp_port', 'smtp_user', 'smtp_from', 'smtp_use_tls',
               'smtp_password_encrypted', 'report_schedulato_attivo', 'report_pdf_attivo')""",
        # Reset della password dalla schermata di accesso (2.6.2). La
        # temporanea vale accanto a password_hash, non al suo posto.
        "ALTER TABLE utenti ADD COLUMN reset_hash TEXT",
        "ALTER TABLE utenti ADD COLUMN reset_scadenza DATETIME",
    ]
    for sql in migrations:
        try:
            db.execute(sql)
        except Exception as e:
            logger.warning(f"Migration step skipped (may already be applied): {e}")
    db.commit()

    # login_attempts.esito accetta anche 'reset' (2.6.2): il CHECK si cambia
    # solo ricostruendo la tabella. Nessuna chiave esterna la referenzia, e le
    # righe si conservano nominando le colonne di destinazione — senza elenco,
    # una tabella con una colonna in piu' o in meno farebbe fallire l'INSERT
    # dopo che RENAME e CREATE, che sono DDL gia' in autocommit, hanno gia'
    # avuto effetto (la lezione della migrazione v2.2 di utenti, poco sotto).
    #
    # La guardia su "'reset' non c'e' ancora" e' un risparmio, non una
    # correttezza: nessun test la copre, e non per dimenticanza — togliendola,
    # la ricostruzione girerebbe a ogni avvio conservando comunque le righe e
    # senza lasciare residui, quindi non c'e' niente di osservabile che possa
    # cadere. Resta perche' rifare la tabella a ogni riavvio e' lavoro inutile
    # e una finestra, per quanto breve, in cui i contatori del blocco
    # anti-forza-bruta non ci sono.
    try:
        riga = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='login_attempts'"
        ).fetchone()
        if riga and "'reset'" not in riga[0]:
            logger.info("Migrazione 2.6.2: login_attempts accetta esito 'reset'...")
            db.execute("ALTER TABLE login_attempts RENAME TO login_attempts_old_262")
            db.execute("""
                CREATE TABLE login_attempts (
                  id         INTEGER PRIMARY KEY AUTOINCREMENT,
                  ip_address TEXT NOT NULL,
                  email      TEXT,
                  esito      TEXT NOT NULL CHECK(esito IN ('fallito', 'bloccato', 'riuscito', 'reset')),
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute(
                "INSERT INTO login_attempts (id, ip_address, email, esito, created_at) "
                "SELECT id, ip_address, email, esito, created_at FROM login_attempts_old_262"
            )
            db.execute("DROP TABLE login_attempts_old_262")
            db.execute("CREATE INDEX IF NOT EXISTS idx_login_attempts_ip    "
                       "ON login_attempts(ip_address, created_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_login_attempts_email "
                       "ON login_attempts(email, created_at)")
            db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"Migrazione login_attempts non applicata: {e}")

    # Aggiunge 'tecnico' al CHECK ruolo di utenti se non già presente (v2.2)
    try:
        row = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='utenti'"
        ).fetchone()
        if row and "'tecnico'" not in row[0]:
            logger.info("Migrazione v2.2: aggiunta ruolo tecnico a tabella utenti...")
            cols_vecchie = [r[1] for r in db.execute("PRAGMA table_info(utenti)").fetchall()]
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
            # Colonne di destinazione nominate esplicitamente: senza elenco,
            # SQLite pretende che il numero di colonne selezionate coincida con
            # quello della tabella nuova. Una utenti di uno schema v1.x (senza
            # struttura_id) ne ha di meno e l'INSERT fallirebbe — nel modo
            # peggiore, perché RENAME e CREATE TABLE sono DDL già in autocommit
            # e il rollback dell'except sotto non li annulla: resterebbe una
            # utenti vuota con i dati intrappolati in utenti_old_v22, e l'app
            # si avvia comunque, senza che nessuno riesca più ad accedere.
            # cols_vecchie può anche contenere colonne che la tabella nuova non
            # conosce (installazione personalizzata, o colonna rimossa fra le
            # versioni): si scartano e si segnalano, perché un dato che sparisce
            # in silenzio è peggio di un errore.
            cols_nuove = [r[1] for r in db.execute("PRAGMA table_info(utenti)").fetchall()]
            cols_comuni = [c for c in cols_vecchie if c in cols_nuove]
            cols_scartate = [c for c in cols_vecchie if c not in cols_nuove]
            if cols_scartate:
                logger.warning(
                    f"Migrazione v2.2: colonne di utenti_old_v22 assenti nella "
                    f"nuova tabella utenti, scartate: {cols_scartate}"
                )
            col_list = ', '.join(cols_comuni)
            db.execute(f"INSERT INTO utenti ({col_list}) SELECT {col_list} FROM utenti_old_v22")
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

    # Utenti rimasti senza struttura: adottali, astienti o disattivali a
    # seconda di quante strutture esistono. Ci si arriva eliminando una
    # struttura (utenti.struttura_id era ON DELETE SET NULL fino alla 2.6),
    # importando dati incompleti, oppure aggiornando un'installazione che
    # non conosceva ancora struttura_id (schema v1.x, prima di
    # migrate_v2_0.py): in quel caso la migrazione v2.2 appena eseguita ha
    # lasciato struttura_id NULL su ogni utente esistente, e disattivarli
    # tutti incondizionatamente bloccherebbe fuori un'installazione che ha
    # aggiornato senza che nessuno abbia sbagliato niente. Questo blocco
    # esiste per chiudere una falla (un account senza scope che, prima del
    # Task 1, vedeva comunque dati altrui), non per murare fuori chi
    # aggiorna: i tre rami sotto lo distinguono da un'ambiguita' reale.
    # superadmin e tecnico sono esclusi in ogni ramo: hanno struttura_id
    # NULL per progetto.
    try:
        orfani = db.execute(
            "SELECT id, email FROM utenti "
            "WHERE struttura_id IS NULL AND attivo = 1 "
            "  AND ruolo IN ('admin', 'utente')"
        ).fetchall()
        if orfani:
            strutture_attive = db.execute(
                "SELECT id, nome FROM strutture WHERE attiva = 1"
            ).fetchall()
            if len(strutture_attive) == 1:
                # Adozione: stessa logica del precedente su import_history
                # (poco sopra, "Import storici senza divisione..."): con
                # un'unica struttura attiva non c'e' ambiguita' su dove
                # inquadrarli, ed e' il caso piu' comune (installazione
                # monostruttura che aggiorna).
                struttura = strutture_attive[0]
                db.execute(
                    "UPDATE utenti SET struttura_id = ? "
                    "WHERE struttura_id IS NULL AND attivo = 1 "
                    "  AND ruolo IN ('admin', 'utente')",
                    (struttura['id'],)
                )
                db.commit()
                logger.warning(
                    f"{len(orfani)} utenti senza struttura assegnati "
                    f"all'unica struttura attiva '{struttura['nome']}' "
                    f"(id {struttura['id']}): "
                    + ', '.join(r['email'] for r in orfani)
                )
            elif len(strutture_attive) == 0:
                # Astensione: non esiste ancora una struttura in cui
                # inquadrarli (es. installazione mai passata da
                # migrate_v2_0.py). Disattivarli qui li renderebbe
                # inaccessibili senza che nessuno abbia un posto dove
                # riassegnarli: si lasciano attivi e si segnala soltanto.
                logger.warning(
                    f"{len(orfani)} utenti senza struttura e nessuna struttura "
                    "attiva nel database: lasciati attivi in attesa che venga "
                    "creata almeno una struttura. Utenti: "
                    + ', '.join(r['email'] for r in orfani)
                )
            else:
                # Disattivazione: con due o piu' strutture attive
                # l'ambiguita' e' reale, nessuno puo' indovinare a quale
                # appartenesse l'utente. Comportamento invariato.
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
        logger.error(f"Gestione utenti orfani fallita: {e}", exc_info=True)

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
            cols_vecchie = [r[1] for r in db.execute("PRAGMA table_info(utenti)").fetchall()]
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
            # Colonne di destinazione nominate esplicitamente (vedi stesso
            # commento nella migrazione v2.2 sopra): un utente che viene da
            # uno schema senza struttura_id deve ritrovarsi struttura_id NULL
            # (valore predefinito), non far fallire l'INSERT. cols_vecchie
            # viene da PRAGMA table_info sulla tabella vecchia e puo' contenere
            # colonne che la tabella nuova non ha (installazione personalizzata
            # o colonna rimossa fra le versioni): si scartano e si segnalano,
            # perché un dato che sparisce in silenzio è peggio di un errore.
            cols_nuove = [r[1] for r in db.execute("PRAGMA table_info(utenti)").fetchall()]
            cols_comuni = [c for c in cols_vecchie if c in cols_nuove]
            cols_scartate = [c for c in cols_vecchie if c not in cols_nuove]
            if cols_scartate:
                logger.warning(
                    f"Migrazione v2.6: colonne di utenti_old_26 assenti nella "
                    f"nuova tabella utenti, scartate: {cols_scartate}"
                )
            col_list = ', '.join(cols_comuni)
            db.execute(f"INSERT INTO utenti ({col_list}) SELECT {col_list} FROM utenti_old_26")
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


#: Sentinella per distinguere "struttura non indicata" (da dedurre dalla
#: richiesta in corso) da "operazione deliberatamente globale" (None esplicito).
STRUTTURA_AUTO = object()


def struttura_corrente():
    """La struttura attiva della richiesta in corso, o None.

    Fuori da un contesto applicativo (thread di background, script) l'accesso
    a `g` solleva RuntimeError: getattr non la cattura, quindi va intercettata.
    """
    try:
        return getattr(g, 'struttura_id', None)
    except RuntimeError:
        return None


def log_attivita(utente_id, azione, entita, entita_id=None, dettagli=None,
                 ip_address=None, struttura_id=STRUTTURA_AUTO):
    """Registra un'attivita' in log_attivita.

    Se `struttura_id` non viene passato si usa la struttura attiva della
    richiesta: senza questo default le righe restano con struttura_id NULL e
    l'admin di struttura non le vede piu' in /admin/log-attivita. Le operazioni
    davvero globali (backup, restore, config di sistema, tecnici) passano
    `struttura_id=None` in modo esplicito.
    """
    if struttura_id is STRUTTURA_AUTO:
        struttura_id = struttura_corrente()
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
