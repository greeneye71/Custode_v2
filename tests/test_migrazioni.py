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


def test_chi_riceveva_il_digest_lo_riceve_ancora_dopo_la_migrazione(app):
    """L'asserzione che conta piu' di tutte. Prima della 2.6.2 il digest di
    testo si accendeva con report_schedulato_attivo; quella chiave sparisce, e
    se la migrazione non la convertisse, un parco di elettromedicali
    smetterebbe di ricevere gli avvisi di scadenza senza che nessuno se ne
    accorga — il modo peggiore di consegnare questa modifica."""
    from models import get_db, execute, query_one, apply_schema_updates
    with app.app_context():
        s = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        execute("INSERT INTO strutture_config (struttura_id,chiave,valore) "
                "VALUES (?,'report_schedulato_attivo','1')", (s,))
        execute("INSERT INTO strutture_config (struttura_id,chiave,valore) "
                "VALUES (?,'report_frequenza','settimanale')", (s,))

        apply_schema_updates()

        attivi = query_one("SELECT valore FROM strutture_config "
                           "WHERE struttura_id=? AND chiave='avvisi_scadenza_attivi'", (s,))
        formato = query_one("SELECT valore FROM strutture_config "
                            "WHERE struttura_id=? AND chiave='avvisi_scadenza_formato'", (s,))
        assert attivi is not None and attivi['valore'] == '1'
        # Testo, non PDF: chi riceveva un digest di testo deve continuare a
        # ricevere quello. Cambiargli il formato sarebbe una sorpresa.
        assert formato is not None and formato['valore'] == 'testo'
        # La frequenza scelta non si perde per strada.
        assert query_one("SELECT valore FROM strutture_config "
                         "WHERE struttura_id=? AND chiave='report_frequenza'",
                         (s,))['valore'] == 'settimanale'


def test_chi_non_riceveva_il_digest_non_inizia_a_riceverlo(app):
    """Il verso opposto, che il test precedente da solo non copre: una
    migrazione che accendesse tutti sarebbe verde li' sopra e sbagliata qui.
    Una struttura con l'interruttore a zero — o senza alcuna riga, il caso
    normale — non deve trovarsi gli avvisi accesi dopo un aggiornamento."""
    from models import execute, query_one, apply_schema_updates
    with app.app_context():
        spenta = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Spenta','SP',1)").lastrowid
        muta = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Muta','MU',1)").lastrowid
        execute("INSERT INTO strutture_config (struttura_id,chiave,valore) "
                "VALUES (?,'report_schedulato_attivo','')", (spenta,))

        apply_schema_updates()

        for sid in (spenta, muta):
            assert query_one("SELECT valore FROM strutture_config "
                             "WHERE struttura_id=? AND chiave='avvisi_scadenza_attivi'",
                             (sid,)) is None


def test_le_chiavi_del_server_spariscono_password_cifrata_compresa(app):
    """Un server di posta e' infrastruttura del deployment, non un dato della
    clinica. Lasciare le righe significherebbe tenere configurazione morta che
    sembra viva, con dentro una credenziale cifrata che finirebbe in ogni
    archivio esportato."""
    from models import execute, query_all, apply_schema_updates
    with app.app_context():
        s = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        for chiave, valore in [
            ('smtp_host', 'smtp.clinica.it'), ('smtp_port', '587'),
            ('smtp_user', 'posta@clinica.it'), ('smtp_from', 'noreply@clinica.it'),
            ('smtp_use_tls', '1'), ('smtp_password_encrypted', 'gAAAAABmSEGRETO='),
            ('report_pdf_attivo', '1'),
        ]:
            execute("INSERT INTO strutture_config (struttura_id,chiave,valore) VALUES (?,?,?)",
                    (s, chiave, valore))
        # Una chiave che NON va toccata, per provare che la cancellazione e'
        # mirata e non una pulizia a tappeto della configurazione.
        execute("INSERT INTO strutture_config (struttura_id,chiave,valore) "
                "VALUES (?,'anthropic_api_key','sk-ant-xxx')", (s,))

        apply_schema_updates()

        rimaste = [r['chiave'] for r in query_all(
            "SELECT chiave FROM strutture_config WHERE struttura_id=?", (s,))]
        for sparita in ('smtp_host', 'smtp_port', 'smtp_user', 'smtp_from',
                        'smtp_use_tls', 'smtp_password_encrypted',
                        'report_schedulato_attivo', 'report_pdf_attivo'):
            assert sparita not in rimaste
        assert 'anthropic_api_key' in rimaste
        # E il segreto non e' rimasto da nessuna parte sotto un altro nome.
        valori = [r['valore'] for r in query_all(
            "SELECT valore FROM strutture_config WHERE struttura_id=?", (s,))]
        assert 'gAAAAABmSEGRETO=' not in valori


def test_la_migrazione_non_riaccende_avvisi_spenti_dall_operatore(app):
    """apply_schema_updates() gira a OGNI avvio, non una volta sola: e' il
    punto in cui una migrazione di dati puo' fare danno. Se la conversione
    restasse ripetibile, il primo riavvio dopo che l'operatore ha tolto la
    spunta agli avvisi glieli riaccenderebbe, e non capirebbe mai perche'.
    L'idempotenza qui non e' un dettaglio di eleganza."""
    from models import execute, query_one, query_all, apply_schema_updates
    with app.app_context():
        s = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        execute("INSERT INTO strutture_config (struttura_id,chiave,valore) "
                "VALUES (?,'report_schedulato_attivo','1')", (s,))

        apply_schema_updates()
        # L'operatore ci ripensa e spegne gli avvisi (il modulo cancella la riga).
        execute("DELETE FROM strutture_config "
                "WHERE struttura_id=? AND chiave='avvisi_scadenza_attivi'", (s,))
        # Riavvio.
        apply_schema_updates()

        assert query_one("SELECT valore FROM strutture_config "
                         "WHERE struttura_id=? AND chiave='avvisi_scadenza_attivi'",
                         (s,)) is None
        # E la seconda esecuzione non ha nemmeno duplicato il formato.
        formati = query_all("SELECT valore FROM strutture_config "
                            "WHERE struttura_id=? AND chiave='avvisi_scadenza_formato'", (s,))
        assert len(formati) == 1


def test_le_colonne_del_reset_arrivano_su_un_database_esistente(app):
    """Come per eliminato_il: schema.sql non rifa' le tabelle che esistono
    gia', quindi senza la migrazione incrementale un'installazione in servizio
    resterebbe senza le due colonne e il reset esploderebbe al primo uso."""
    from models import get_db, apply_schema_updates
    with app.app_context():
        db = get_db()
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("ALTER TABLE utenti DROP COLUMN reset_hash")
        db.execute("ALTER TABLE utenti DROP COLUMN reset_scadenza")
        db.commit()

        apply_schema_updates()

        colonne = [r[1] for r in db.execute("PRAGMA table_info(utenti)")]
        assert 'reset_hash' in colonne
        assert 'reset_scadenza' in colonne


def test_login_attempts_impara_l_esito_reset_senza_perdere_le_righe(app):
    """Il CHECK di login_attempts non conosceva 'reset' e SQLite non permette
    di allargarlo se non ricostruendo la tabella. La ricostruzione deve
    conservare le righe: sono i contatori del blocco anti-forza-bruta, e
    perderli significherebbe azzerare i blocchi in corso a ogni aggiornamento."""
    from models import get_db, apply_schema_updates
    with app.app_context():
        db = get_db()
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("DROP TABLE login_attempts")
        db.execute("""
            CREATE TABLE login_attempts (
              id         INTEGER PRIMARY KEY AUTOINCREMENT,
              ip_address TEXT NOT NULL,
              email      TEXT,
              esito      TEXT NOT NULL CHECK(esito IN ('fallito', 'bloccato', 'riuscito')),
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("INSERT INTO login_attempts (ip_address, email, esito) "
                   "VALUES ('10.0.0.9', 'tizio@x.it', 'fallito')")
        db.commit()

        apply_schema_updates()

        # La riga di prima e' ancora li', con il suo indirizzo.
        riga = db.execute("SELECT ip_address, email, esito FROM login_attempts").fetchone()
        assert riga is not None
        assert (riga[0], riga[1], riga[2]) == ('10.0.0.9', 'tizio@x.it', 'fallito')
        # E adesso 'reset' e' un esito ammesso.
        db.execute("INSERT INTO login_attempts (ip_address, email, esito) "
                   "VALUES ('10.0.0.9', 'tizio@x.it', 'reset')")
        db.commit()
        assert db.execute("SELECT COUNT(*) FROM login_attempts "
                          "WHERE esito='reset'").fetchone()[0] == 1
        # Nessuna tabella di appoggio dimenticata per strada.
        assert db.execute("SELECT name FROM sqlite_master "
                          "WHERE name LIKE '%login_attempts_old%'").fetchall() == []


def test_la_ricostruzione_di_login_attempts_e_idempotente(app):
    """apply_schema_updates() gira a ogni avvio: due esecuzioni non devono
    duplicare le righe, lasciare tabelle di appoggio o rompere le chiavi.

    Quello che questo test NON prova e' la guardia che salta la ricostruzione
    quando e' gia' stata fatta: togliendola, la tabella verrebbe rifatta tutte
    le volte conservando comunque le righe, e la suite resterebbe verde
    (verificato). La guardia e' un risparmio, non una correttezza — vedi il
    commento in models.apply_schema_updates()."""
    from models import get_db, apply_schema_updates
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO login_attempts (ip_address, email, esito) "
                   "VALUES ('10.0.0.9', 'tizio@x.it', 'reset')")
        db.commit()

        apply_schema_updates()
        apply_schema_updates()

        assert db.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0] == 1
        assert db.execute("SELECT name FROM sqlite_master "
                          "WHERE name LIKE '%login_attempts_old%'").fetchall() == []
        db.execute("PRAGMA foreign_keys = ON")
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
