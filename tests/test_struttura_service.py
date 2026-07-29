"""La primitiva che cancella una struttura, provata su database temporanei.

Non serve l'applicazione: rimuovi_strutture riceve una connessione. E' la
ragione per cui e' scritta cosi'.
"""
import os
import sqlite3

import pytest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def conn(tmp_path):
    """Due strutture popolate. La B esiste per un motivo solo: dimostrare che
    non viene toccata. Un test che cancella A e conta le righe di A verifica
    'ha cancellato', non 'ha cancellato la cosa giusta'."""
    percorso = str(tmp_path / 'prova.db')
    con = sqlite3.connect(percorso)
    con.row_factory = sqlite3.Row
    with open(os.path.join(RADICE, 'schema.sql'), encoding='utf-8') as f:
        con.executescript(f.read())
    con.execute("PRAGMA foreign_keys = ON")

    ids = {}
    for etichetta, nome, codice in (('a', 'Clinica A', 'A'), ('b', 'Clinica B', 'B')):
        s = con.execute("INSERT INTO strutture (nome,codice,attiva) VALUES (?,?,1)",
                        (nome, codice)).lastrowid
        d = con.execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES (?,?,?)",
                        (f'Div {codice}', f'D{codice}', s)).lastrowid
        u = con.execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id) "
            "VALUES (?,'x','N','C','admin',?)", (f'admin@{codice}.it', s)).lastrowid
        ap = con.execute(
            "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato,created_by) "
            "VALUES (?,?,?,'M','MOD','funzionante',?)", (d, s, f'{codice}-1', u)).lastrowid
        con.execute("INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento,created_by) "
                    "VALUES (?,'preventiva','2026-01-01',?)", (ap, u))
        con.execute("INSERT INTO verifiche (apparecchio_id,data_verifica,esito,created_by) "
                    "VALUES (?,'2026-01-01','positivo',?)", (ap, u))
        con.execute("INSERT INTO documenti (apparecchio_id,tipo,filename,filepath,uploaded_by) "
                    "VALUES (?,'report','d.pdf','x/d.pdf',?)", (ap, u))
        con.execute("INSERT INTO accessori (apparecchio_id,descrizione,created_by) VALUES (?,'Sonda',?)", (ap, u))
        con.execute("INSERT INTO import_history (struttura_id,divisione_id,tipo_import,filename,filepath,imported_by) "
                    "VALUES (?,?,'inventario','x.xlsx','x/x.xlsx',?)", (s, d, u))
        con.execute("INSERT INTO strutture_config (struttura_id,chiave,valore) VALUES (?,'ai_provider','x')", (s,))
        con.execute("INSERT INTO log_attivita (utente_id,azione,entita,struttura_id,dettagli) "
                    "VALUES (?,'creazione','apparecchi',?,'nota')", (u, s))
        ids[etichetta] = {'struttura': s, 'divisione': d, 'utente': u, 'apparecchio': ap}

    # Un tecnico assegnato a entrambe: struttura_id NULL, come li crea admin.py
    tec = con.execute(
        "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id) "
        "VALUES ('tec@x.it','x','T','T','tecnico',NULL)").lastrowid
    for etichetta in ('a', 'b'):
        con.execute("INSERT INTO tecnici_strutture (tecnico_id,struttura_id) VALUES (?,?)",
                    (tec, ids[etichetta]['struttura']))
    ids['tecnico'] = tec
    con.commit()
    return con, ids


def conta(con, tabella, dove='', params=()):
    return con.execute(f"SELECT COUNT(*) FROM {tabella} {dove}", params).fetchone()[0]


def test_cancella_la_struttura_indicata(conn):
    from struttura_service import rimuovi_strutture
    con, ids = conn
    conteggi = rimuovi_strutture(con, [ids['a']['struttura']])
    con.commit()

    assert conta(con, 'strutture', 'WHERE id=?', (ids['a']['struttura'],)) == 0
    assert conta(con, 'apparecchi', 'WHERE struttura_id=?', (ids['a']['struttura'],)) == 0
    assert conta(con, 'divisioni', 'WHERE struttura_id=?', (ids['a']['struttura'],)) == 0
    assert conta(con, 'utenti', 'WHERE email=?', ('admin@A.it',)) == 0
    assert conteggi['apparecchi'] == 1
    assert conteggi['utenti'] == 1


def test_la_struttura_b_resta_intatta(conn):
    """L'asserzione che distingue 'ha cancellato la cosa giusta' da
    'ha cancellato'."""
    from struttura_service import rimuovi_strutture
    con, ids = conn
    rimuovi_strutture(con, [ids['a']['struttura']])
    con.commit()

    b = ids['b']['struttura']
    assert conta(con, 'strutture', 'WHERE id=?', (b,)) == 1
    assert conta(con, 'apparecchi', 'WHERE struttura_id=?', (b,)) == 1
    assert conta(con, 'divisioni', 'WHERE struttura_id=?', (b,)) == 1
    assert conta(con, 'utenti', 'WHERE struttura_id=?', (b,)) == 1
    for tabella in ('manutenzioni', 'verifiche', 'documenti', 'accessori'):
        assert conta(con, tabella, 'WHERE apparecchio_id=?', (ids['b']['apparecchio'],)) == 1


def test_i_figli_dell_apparecchio_spariscono(conn):
    from struttura_service import rimuovi_strutture
    con, ids = conn
    rimuovi_strutture(con, [ids['a']['struttura']])
    con.commit()
    for tabella in ('manutenzioni', 'verifiche', 'documenti', 'accessori'):
        assert conta(con, tabella, 'WHERE apparecchio_id=?', (ids['a']['apparecchio'],)) == 0


def test_il_tecnico_sopravvive_e_perde_solo_l_assegnazione(conn):
    """Garanzia che dipende da una convenzione: i tecnici hanno struttura_id
    NULL, quindi 'DELETE FROM utenti WHERE struttura_id = ?' li lascia fuori.
    Le convenzioni vanno inchiodate da un test, altrimenti la prima persona
    che aggiunge un tecnico con la struttura valorizzata li cancella tutti."""
    from struttura_service import rimuovi_strutture
    con, ids = conn
    rimuovi_strutture(con, [ids['a']['struttura']])
    con.commit()

    assert conta(con, 'utenti', 'WHERE id=?', (ids['tecnico'],)) == 1
    assert conta(con, 'tecnici_strutture', 'WHERE tecnico_id=?', (ids['tecnico'],)) == 1
    resta = con.execute("SELECT struttura_id FROM tecnici_strutture WHERE tecnico_id=?",
                        (ids['tecnico'],)).fetchone()[0]
    assert resta == ids['b']['struttura']


def test_il_registro_sopravvive_slegato_dalla_struttura(conn):
    from struttura_service import rimuovi_strutture
    con, ids = conn
    rimuovi_strutture(con, [ids['a']['struttura']])
    con.commit()

    righe = con.execute("SELECT struttura_id, utente_id, dettagli FROM log_attivita "
                        "WHERE dettagli LIKE 'nota%'").fetchall()
    orfana = [r for r in righe if r['struttura_id'] is None]
    assert len(orfana) == 1
    assert orfana[0]['utente_id'] is None
    assert 'admin@A.it' in orfana[0]['dettagli']


def test_un_apparecchio_altrui_creato_da_un_utente_cancellato_non_blocca(conn):
    """apparecchi.created_by non ha ON DELETE: se un utente della struttura A
    ha creato un apparecchio nella B (un superadmin puo' farlo), cancellarlo
    fa fallire l'intera operazione con un FOREIGN KEY constraint failed."""
    from struttura_service import rimuovi_strutture
    con, ids = conn
    con.execute("UPDATE apparecchi SET created_by=? WHERE id=?",
                (ids['a']['utente'], ids['b']['apparecchio']))
    con.commit()

    rimuovi_strutture(con, [ids['a']['struttura']])
    con.commit()

    riga = con.execute("SELECT created_by FROM apparecchi WHERE id=?",
                       (ids['b']['apparecchio'],)).fetchone()
    assert riga['created_by'] is None
    assert conta(con, 'apparecchi', 'WHERE id=?', (ids['b']['apparecchio'],)) == 1


def test_elenco_vuoto_non_fa_nulla(conn):
    from struttura_service import rimuovi_strutture
    con, ids = conn
    conteggi = rimuovi_strutture(con, [])
    con.commit()
    assert conta(con, 'strutture') == 2
    assert conteggi['strutture'] == 0


def test_import_preview_di_un_altra_struttura_non_blocca_la_cancellazione(conn):
    """import_preview.apparecchio_match_id non ha ON DELETE verso apparecchi.
    Una riga di import_preview puo' appartenere a un import_history della
    struttura B (sopravvissuta) e puntare, come suggerimento di match, a un
    apparecchio della struttura A che stiamo cancellando: e' il ramo di
    import_bp._match_apparecchi che cerca un match senza scope di struttura.
    Una preview creata dentro l'import di A non proverebbe nulla: sparirebbe
    gia' in cascata insieme al suo import_history. Qui la preview e' di B, non
    va toccata come riga, ma il suo collegamento all'apparecchio di A deve
    essere azzerato, altrimenti la DELETE FROM apparecchi va in FK error."""
    from struttura_service import rimuovi_strutture
    con, ids = conn
    import_id = con.execute(
        "INSERT INTO import_history (struttura_id,divisione_id,tipo_import,filename,filepath,imported_by) "
        "VALUES (?,?,'inventario','y.xlsx','y/y.xlsx',?)",
        (ids['b']['struttura'], ids['b']['divisione'], ids['b']['utente'])).lastrowid
    preview_id = con.execute(
        "INSERT INTO import_preview (import_id,apparecchio_match_id,stato) VALUES (?,?,'pending')",
        (import_id, ids['a']['apparecchio'])).lastrowid
    con.commit()

    rimuovi_strutture(con, [ids['a']['struttura']])
    con.commit()

    riga = con.execute("SELECT apparecchio_match_id FROM import_preview WHERE id=?",
                       (preview_id,)).fetchone()
    assert riga is not None  # e' roba di B: la riga resta, solo il link sparisce
    assert riga['apparecchio_match_id'] is None
    assert conta(con, 'import_history', 'WHERE id=?', (import_id,)) == 1


def test_config_e_token_vengono_cancellati_solo_per_la_struttura_indicata(conn):
    """strutture_config e api_tokens vanno in cascata sulla FK verso strutture:
    qui verifichiamo che il confine di tenant sia rispettato anche per loro,
    non solo per apparecchi/divisioni/utenti."""
    from struttura_service import rimuovi_strutture
    con, ids = conn
    con.execute("INSERT INTO api_tokens (struttura_id,nome,token_hash,created_by) VALUES (?,?,?,?)",
                (ids['a']['struttura'], 'Token A', 'hash-a', ids['a']['utente']))
    con.execute("INSERT INTO api_tokens (struttura_id,nome,token_hash,created_by) VALUES (?,?,?,?)",
                (ids['b']['struttura'], 'Token B', 'hash-b', ids['b']['utente']))
    con.commit()

    rimuovi_strutture(con, [ids['a']['struttura']])
    con.commit()

    assert conta(con, 'strutture_config', 'WHERE struttura_id=?', (ids['a']['struttura'],)) == 0
    assert conta(con, 'strutture_config', 'WHERE struttura_id=?', (ids['b']['struttura'],)) == 1
    assert conta(con, 'api_tokens', 'WHERE struttura_id=?', (ids['a']['struttura'],)) == 0
    assert conta(con, 'api_tokens', 'WHERE struttura_id=?', (ids['b']['struttura'],)) == 1


def test_contenuto_conta_dati_utenti_e_tecnici(conn, tmp_path):
    from struttura_service import contenuto_struttura
    con, ids = conn
    cartella = tmp_path / 'uploads' / 'strutture' / str(ids['a']['struttura']) / 'foto'
    cartella.mkdir(parents=True)
    (cartella / 'x.jpg').write_bytes(b'0' * 1234)

    c = contenuto_struttura(con, ids['a']['struttura'], str(tmp_path / 'uploads'))
    assert c['apparecchi'] == 1
    assert c['manutenzioni'] == 1
    assert c['verifiche'] == 1
    assert c['utenti'] == 1
    assert c['tecnici'] == 1      # assegnato, non di proprieta'
    assert c['file'] == 1
    assert c['byte'] == 1234


def test_contenuto_senza_cartella_uploads(conn, tmp_path):
    """Una struttura senza allegati non deve far fallire la scheda."""
    from struttura_service import contenuto_struttura
    con, ids = conn
    c = contenuto_struttura(con, ids['b']['struttura'], str(tmp_path / 'inesistente'))
    assert c['file'] == 0
    assert c['byte'] == 0


def test_contenuto_in_modalita_single_struttura(conn, tmp_path):
    """In single-struttura upload_subdir mette i file sotto uploads_base/<tipo>/,
    senza il prefisso strutture/<id>/: contenuto_struttura deve guardare li',
    non nel percorso multi-struttura. Nessuna cartella strutture/<id>/ esiste
    qui: un'implementazione che ignorasse il flag e cercasse comunque nel
    percorso multi troverebbe zero file e il test fallirebbe."""
    from struttura_service import contenuto_struttura
    con, ids = conn
    cartella = tmp_path / 'uploads' / 'foto'
    cartella.mkdir(parents=True)
    (cartella / 'y.jpg').write_bytes(b'0' * 500)

    c = contenuto_struttura(con, ids['a']['struttura'], str(tmp_path / 'uploads'),
                            single_struttura=True)
    assert c['file'] == 1
    assert c['byte'] == 500


def test_contenuto_di_default_resta_in_modalita_multi(conn, tmp_path):
    """Senza specificare single_struttura, il comportamento resta quello
    multi-struttura preesistente: i file vanno cercati sotto
    uploads_base/strutture/<id>/<tipo>/."""
    from struttura_service import contenuto_struttura
    con, ids = conn
    cartella = tmp_path / 'uploads' / 'strutture' / str(ids['a']['struttura']) / 'foto'
    cartella.mkdir(parents=True)
    (cartella / 'z.jpg').write_bytes(b'0' * 777)

    c = contenuto_struttura(con, ids['a']['struttura'], str(tmp_path / 'uploads'))
    assert c['file'] == 1
    assert c['byte'] == 777
