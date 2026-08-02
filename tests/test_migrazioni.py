"""Migrazioni di schema applicate all'avvio da models.apply_schema_updates().

I test partono da uno schema vecchio ricostruito a mano e verificano che dopo
la migrazione i dati ci siano ancora e le chiavi esterne siano intatte.
"""


def test_migrazione_v22_su_schema_v1_senza_struttura_id_non_perde_utenti(app):
    """Il database reale del progetto e' su questo schema: una utenti di prima
    della v2.0, senza struttura_id e con il CHECK a due soli ruoli. La v2.2
    faceva 'INSERT INTO utenti SELECT col_list FROM utenti_old_v22' senza
    nominare le colonne di destinazione: con una tabella vecchia che ha meno
    colonne della nuova (qui manca struttura_id), SQLite pretende comunque
    che i conteggi coincidano e l'INSERT falliva. Il guaio non e' l'eccezione
    in se': RENAME e CREATE TABLE sono DDL gia' in autocommit, quindi il
    rollback dell'except non li annulla, e restava una utenti vuota con i
    dati reali intrappolati in utenti_old_v22 — l'app si avvia ma nessuno
    entra piu'."""
    from models import get_db, execute, query_one, query_all, apply_schema_updates
    with app.app_context():
        db = get_db()
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("DROP TABLE utenti")
        db.execute("""
            CREATE TABLE utenti (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              email TEXT UNIQUE NOT NULL,
              password_hash TEXT NOT NULL,
              nome TEXT NOT NULL,
              cognome TEXT NOT NULL,
              ruolo TEXT NOT NULL CHECK(ruolo IN ('admin', 'utente')),
              divisione_default_id INTEGER,
              attivo INTEGER DEFAULT 1,
              primo_accesso INTEGER DEFAULT 1,
              ultimo_accesso DATETIME,
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY (divisione_default_id) REFERENCES divisioni(id)
            )
        """)
        db.commit()

        s = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Vecchia','V1',1)").lastrowid
        d = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Div','D1',?)", (s,)).lastrowid
        admin_id = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo) "
            "VALUES ('admin@v1.it','x','A','A','admin')"
        ).lastrowid
        utente_id = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo) "
            "VALUES ('utente@v1.it','x','U','U','utente')"
        ).lastrowid
        execute(
            "INSERT INTO sessioni (utente_id,token,expires_at) VALUES (?, 'tok-v1', datetime('now','+1 day'))",
            (admin_id,)
        )
        execute(
            "INSERT INTO utenti_divisioni (utente_id,divisione_id,ruolo_divisione) VALUES (?, ?, 'utente')",
            (utente_id, d)
        )

        apply_schema_updates()

        assert query_one("SELECT COUNT(*) AS n FROM utenti")['n'] == 2
        assert query_all("SELECT name FROM sqlite_master WHERE name LIKE '%utenti_old%'") == []
        db.execute("PRAGMA foreign_keys = ON")
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        riga = query_one(
            "SELECT s.token, u.email FROM sessioni s JOIN utenti u ON u.id = s.utente_id "
            "WHERE s.token = 'tok-v1'"
        )
        assert riga is not None and riga['email'] == 'admin@v1.it'


def test_migrazione_v22_scarta_colonne_assenti_nella_nuova_tabella(app):
    """cols_vecchie viene da PRAGMA table_info sulla utenti precedente: puo'
    contenere colonne che la nuova tabella non conosce (installazione
    personalizzata, o colonna rimossa fra le versioni). L'INSERT deve
    scartarle, non fallire: perdere una colonna extra e' accettabile,
    un'app che non si avvia piu' no.

    Qui il conteggio delle colonne coincide per caso (la vecchia ha una colonna
    in piu' che la nuova non ha, la nuova ha struttura_id che la vecchia non
    ha), quindi senza l'elenco delle colonne di destinazione l'INSERT riesce
    ma disallinea i valori: 'da scartare' finisce in struttura_id. E' il caso
    peggiore, perche' non lascia traccia di errore."""
    from models import get_db, execute, query_one, query_all, apply_schema_updates
    with app.app_context():
        db = get_db()
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("DROP TABLE utenti")
        db.execute("""
            CREATE TABLE utenti (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              email TEXT UNIQUE NOT NULL,
              password_hash TEXT NOT NULL,
              nome TEXT NOT NULL,
              cognome TEXT NOT NULL,
              ruolo TEXT NOT NULL CHECK(ruolo IN ('admin', 'utente')),
              divisione_default_id INTEGER,
              attivo INTEGER DEFAULT 1,
              primo_accesso INTEGER DEFAULT 1,
              ultimo_accesso DATETIME,
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              nota_personalizzata TEXT,
              FOREIGN KEY (divisione_default_id) REFERENCES divisioni(id)
            )
        """)
        db.commit()

        execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,nota_personalizzata) "
            "VALUES ('extra@v1.it','x','E','E','admin','da scartare')"
        )

        apply_schema_updates()

        assert query_one("SELECT COUNT(*) AS n FROM utenti")['n'] == 1
        riga = query_one("SELECT email, ruolo, struttura_id FROM utenti")
        assert riga['email'] == 'extra@v1.it'
        assert riga['ruolo'] == 'admin'
        assert riga['struttura_id'] is None
        assert query_all("SELECT name FROM sqlite_master WHERE name LIKE '%utenti_old%'") == []
        colonne = [r[1] for r in db.execute("PRAGMA table_info(utenti)").fetchall()]
        assert 'nota_personalizzata' not in colonne
        assert 'struttura_id' in colonne


def test_avvio_su_database_non_migrato_dice_di_eseguire_migrate(app, tmp_path):
    """Un'installazione che non ha ancora ricevuto le migrazioni autonome muore
    in init_db(), perche' schema.sql crea un indice su apparecchi.descrizione,
    che migrate_v1_2.py deve ancora ottenere rinominando codice_interno.
    L'errore di SQLite - "no such column: descrizione" - non dice all'operatore
    cosa fare, e l'applicazione non parte affatto.

    Le migrazioni NON vanno applicate qui: fanno un backup, rinominano colonne
    e sono una scelta dell'operatore. Indovinarle rischierebbe di aggiungere una
    descrizione vuota accanto a un codice_interno popolato, e di lasciare quei
    dati dove il rinomino non li cerchera' piu'. Quello che si deve pretendere
    e' che il messaggio dica cosa fare."""
    import pytest
    from models import get_db, init_db
    with app.app_context():
        db = get_db()
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("DROP INDEX IF EXISTS idx_apparecchi_descrizione")
        db.execute("ALTER TABLE apparecchi RENAME COLUMN descrizione TO codice_interno")
        db.commit()

        with pytest.raises(RuntimeError) as errore:
            init_db()

        messaggio = str(errore.value)
        assert 'migrate.py --check' in messaggio
        assert 'no such column' in messaggio
        assert 'README' in messaggio


def test_un_errore_di_schema_diverso_non_viene_mascherato(app):
    """Il messaggio nuovo copre un caso preciso: una colonna che le migrazioni
    autonome devono ancora portare. Qualunque altro errore in schema.sql e' un
    guasto da far vedere com'e', non da tradurre in un consiglio sbagliato."""
    import sqlite3
    import pytest
    from models import get_db, init_db
    with app.app_context():
        db = get_db()
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("DROP TABLE apparecchi")
        # Una vista al posto della tabella: CREATE TABLE IF NOT EXISTS la vede
        # e non fa nulla, poi il primo CREATE INDEX su di essa fallisce con
        # "views may not be indexed", che non e' "no such column".
        db.execute("CREATE VIEW apparecchi AS SELECT 1 AS id, 1 AS divisione_id")
        db.commit()

        with pytest.raises(sqlite3.OperationalError) as errore:
            init_db()
        assert 'no such column' not in str(errore.value)
        assert 'migrate.py' not in str(errore.value)


def test_la_colonna_eliminato_il_arriva_anche_su_un_database_esistente(app):
    """La colonna sta in schema.sql per le installazioni nuove, ma
    un'installazione gia' in servizio non riesegue schema.sql sulle tabelle che
    esistono gia' (sono tutte CREATE TABLE IF NOT EXISTS): serve la migrazione
    incrementale, che gira a ogni avvio."""
    from models import get_db, apply_schema_updates
    with app.app_context():
        db = get_db()
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("ALTER TABLE utenti DROP COLUMN eliminato_il")
        db.commit()
        assert 'eliminato_il' not in [r[1] for r in db.execute("PRAGMA table_info(utenti)")]

        apply_schema_updates()

        colonne = [r[1] for r in db.execute("PRAGMA table_info(utenti)")]
        assert 'eliminato_il' in colonne
        # E gli utenti esistenti non risultano cancellati.
        assert db.execute(
            "SELECT COUNT(*) FROM utenti WHERE eliminato_il IS NOT NULL").fetchone()[0] == 0
