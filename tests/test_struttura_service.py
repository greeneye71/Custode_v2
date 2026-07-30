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


def test_cartella_struttura_rifiuta_la_modalita_single(tmp_path):
    """In single-struttura non esiste un sottoalbero uploads/ per struttura
    da isolare: upload_subdir() scrive tutto sotto uploads_base/<subdir>/,
    condivisa da ogni struttura del database. cartella_struttura non deve
    fingere che ne esista uno restituendo uploads_base stessa: quel valore,
    passato per errore a un futuro shutil.rmtree (es. dal Task 7), cancella
    gli allegati dell'intero deployment invece di una struttura sola."""
    from struttura_service import cartella_struttura
    with pytest.raises(ValueError, match='_percorsi_allegati'):
        cartella_struttura(str(tmp_path / 'uploads'), 1, single_struttura=True)


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
    percorso multi troverebbe zero file e il test fallirebbe.

    Nella cartella c'e' anche un secondo file NON referenziato da nessuna
    riga (orfano/di un'altra struttura): serve a distinguere la selezione
    per riferimento (conta solo y.jpg) da un os.walk cieco sull'intera
    cartella (conterebbe anche l'orfano). Senza di esso questo test non
    intercetta la reintroduzione dell'os.walk cieco: la cartella
    conterrebbe comunque un solo file, e le due implementazioni
    darebbero lo stesso risultato per coincidenza."""
    from struttura_service import contenuto_struttura
    con, ids = conn
    con.execute("UPDATE apparecchi SET foto_path=? WHERE id=?",
               ('foto/y.jpg', ids['a']['apparecchio']))
    con.commit()
    cartella = tmp_path / 'uploads' / 'foto'
    cartella.mkdir(parents=True)
    (cartella / 'y.jpg').write_bytes(b'0' * 500)
    (cartella / 'non-referenziato.jpg').write_bytes(b'0' * 999)

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


# ---------------------------------------------------------------------------
# esporta_struttura
# ---------------------------------------------------------------------------

def test_esportazione_contiene_solo_la_struttura_richiesta(conn, tmp_path):
    """L'archivio deve essere una installazione con dentro una struttura sola:
    se ci finisse anche la B, consegnandolo si consegnerebbero i dati di
    un'altra struttura. Verifica sia il database (righe) sia il filesystem
    (l'intero sottoalbero di B non deve esistere nell'archivio) sia il
    registro attivita' e i tentativi di login, che non hanno una FK di
    struttura e potrebbero sopravvivere per l'intero deployment."""
    import sqlite3
    from struttura_service import esporta_struttura
    con, ids = conn
    con.execute("INSERT INTO login_attempts (ip_address,email,esito) VALUES ('9.9.9.9','b@b.it','riuscito')")
    con.commit()
    percorso_db = con.execute("PRAGMA database_list").fetchone()[2]
    con.close()

    uploads = tmp_path / 'uploads'
    cartella_a = uploads / 'strutture' / str(ids['a']['struttura']) / 'foto'
    cartella_a.mkdir(parents=True)
    (cartella_a / 'foto.jpg').write_bytes(b'immagine')
    cartella_b = uploads / 'strutture' / str(ids['b']['struttura']) / 'foto'
    cartella_b.mkdir(parents=True)
    (cartella_b / 'foto-b.jpg').write_bytes(b'immagine-b')

    destinazione = esporta_struttura(percorso_db, str(uploads),
                                     ids['a']['struttura'], str(tmp_path / 'archivi'))

    copia = sqlite3.connect(os.path.join(destinazione, 'data', 'database.sqlite'))
    nomi = [r[0] for r in copia.execute("SELECT nome FROM strutture")]
    assert nomi == ['Clinica A']
    assert copia.execute("SELECT COUNT(*) FROM apparecchi").fetchone()[0] == 1
    assert copia.execute("SELECT matricola FROM apparecchi").fetchone()[0] == 'A-1'
    assert copia.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0] == 0
    righe_log = copia.execute("SELECT struttura_id FROM log_attivita").fetchall()
    assert all(r[0] == ids['a']['struttura'] for r in righe_log)
    copia.close()

    atteso = os.path.join(destinazione, 'uploads', 'strutture',
                          str(ids['a']['struttura']), 'foto', 'foto.jpg')
    assert os.path.exists(atteso)
    non_atteso = os.path.join(destinazione, 'uploads', 'strutture', str(ids['b']['struttura']))
    assert not os.path.exists(non_atteso)
    assert os.path.exists(os.path.join(destinazione, 'ESPORTAZIONE.txt'))


def test_esportazione_non_tocca_il_database_vivo(conn, tmp_path):
    """rimuovi_strutture gira sulla copia. Se per un errore girasse
    sull'originale, l'esportazione cancellerebbe tutte le altre strutture."""
    from struttura_service import esporta_struttura
    con, ids = conn
    percorso_db = con.execute("PRAGMA database_list").fetchone()[2]

    esporta_struttura(percorso_db, str(tmp_path / 'uploads'),
                      ids['a']['struttura'], str(tmp_path / 'archivi'))

    assert con.execute("SELECT COUNT(*) FROM strutture").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM apparecchi").fetchone()[0] == 2


def test_due_esportazioni_non_si_sovrascrivono(conn, tmp_path):
    from struttura_service import esporta_struttura
    con, ids = conn
    percorso_db = con.execute("PRAGMA database_list").fetchone()[2]
    uno = esporta_struttura(percorso_db, str(tmp_path / 'u'), ids['a']['struttura'], str(tmp_path / 'ar'))
    due = esporta_struttura(percorso_db, str(tmp_path / 'u'), ids['a']['struttura'], str(tmp_path / 'ar'))
    assert uno != due
    assert os.path.exists(uno) and os.path.exists(due)


def test_due_esportazioni_nello_stesso_secondo_non_si_sovrascrivono(conn, tmp_path, monkeypatch):
    """_nome_cartella ha risoluzione al secondo: se due esportazioni cadono
    nello stesso secondo (capita facilmente su un database di prova, piccolo
    e veloce da copiare), senza un controllo di esistenza la seconda
    sovrascriverebbe silenziosamente la prima invece di fallire o accodarsi.
    Qui si blocca l'orologio per rendere la collisione certa, non probabile."""
    import struttura_service as svc

    class OrologioFermo(svc.datetime):
        @classmethod
        def now(cls):
            return svc.datetime(2026, 1, 1, 12, 0, 0)

    monkeypatch.setattr(svc, 'datetime', OrologioFermo)

    con, ids = conn
    percorso_db = con.execute("PRAGMA database_list").fetchone()[2]
    uno = svc.esporta_struttura(percorso_db, str(tmp_path / 'u'), ids['a']['struttura'], str(tmp_path / 'ar'))
    due = svc.esporta_struttura(percorso_db, str(tmp_path / 'u'), ids['a']['struttura'], str(tmp_path / 'ar'))
    assert uno != due
    assert os.path.isdir(uno) and os.path.isdir(due)
    # Ognuna deve contenere davvero la propria copia, non un archivio a metà
    # sovrascritto dall'altra esportazione.
    for cartella in (uno, due):
        assert os.path.exists(os.path.join(cartella, 'ESPORTAZIONE.txt'))


def test_esportazione_in_modalita_single_struttura_include_gli_allegati(conn, tmp_path):
    """In single-struttura gli allegati stanno sotto uploads_base/<tipo>/,
    senza il prefisso strutture/<id>/ (vedi contenuto_struttura). Se
    esporta_struttura non propagasse single_struttura a cartella_struttura,
    cercherebbe gli allegati nel posto sbagliato e l'archivio uscirebbe senza
    foto pur avendone una sul disco.

    La cartella contiene anche un file NON referenziato da nessuna riga:
    senza di esso il test non distingue la selezione per riferimento da un
    copytree cieco dell'intera cartella, perche' un copytree copierebbe
    comunque l'unico file presente per coincidenza (difetto scoperto dal
    revisore nel giro di correzioni 2, sul test gemello di
    contenuto_struttura - lo stesso vale qui)."""
    from struttura_service import esporta_struttura
    con, ids = conn
    con.execute("UPDATE apparecchi SET foto_path=? WHERE id=?",
               ('foto/unica.jpg', ids['a']['apparecchio']))
    con.commit()
    percorso_db = con.execute("PRAGMA database_list").fetchone()[2]

    uploads = tmp_path / 'uploads'
    cartella = uploads / 'foto'
    cartella.mkdir(parents=True)
    (cartella / 'unica.jpg').write_bytes(b'immagine-single')
    (cartella / 'non-referenziato.jpg').write_bytes(b'altro-file')

    destinazione = esporta_struttura(percorso_db, str(uploads), ids['a']['struttura'],
                                     str(tmp_path / 'archivi'), single_struttura=True)

    atteso = os.path.join(destinazione, 'uploads', 'foto', 'unica.jpg')
    assert os.path.exists(atteso)
    assert not os.path.exists(os.path.join(destinazione, 'uploads', 'foto', 'non-referenziato.jpg'))


def test_esportazione_in_modalita_single_non_include_gli_allegati_di_altre_strutture(conn, tmp_path):
    """Riproduce lo scenario del rilievo di revisione: flag single_struttura
    ma piu' strutture nel database (es. due importa_installazione.py di
    fila su un target single, che non ha alcuna guardia contro il caso).
    In single-struttura cartella_struttura restituisce l'INTERA cartella
    uploads, perche' non esiste un sottoalbero per struttura da isolare: un
    copytree di quella cartella travasa anche gli allegati della clinica B
    nell'archivio della clinica A. La selezione corretta e' sui percorsi
    referenziati dalle righe della struttura (apparecchi.foto_path,
    documenti.filepath, manutenzioni.verbale_path, verifiche.documento_path),
    non su un albero."""
    from struttura_service import esporta_struttura
    con, ids = conn
    con.execute("UPDATE apparecchi SET foto_path=? WHERE id=?",
               ('foto/A.jpg', ids['a']['apparecchio']))
    con.execute("UPDATE apparecchi SET foto_path=? WHERE id=?",
               ('foto/B.jpg', ids['b']['apparecchio']))
    con.commit()
    percorso_db = con.execute("PRAGMA database_list").fetchone()[2]

    uploads = tmp_path / 'uploads'
    cartella = uploads / 'foto'
    cartella.mkdir(parents=True)
    (cartella / 'A.jpg').write_bytes(b'immagine-a')
    (cartella / 'B.jpg').write_bytes(b'immagine-b')

    destinazione = esporta_struttura(percorso_db, str(uploads), ids['a']['struttura'],
                                     str(tmp_path / 'archivi'), single_struttura=True)

    assert os.path.exists(os.path.join(destinazione, 'uploads', 'foto', 'A.jpg'))
    assert not os.path.exists(os.path.join(destinazione, 'uploads', 'foto', 'B.jpg'))


def test_contenuto_in_modalita_single_non_conta_i_file_di_altre_strutture(conn, tmp_path):
    """Stesso difetto della copia (vedi test sopra), visto dal lato dei
    conteggi che finiscono in ESPORTAZIONE.txt: un os.walk cieco sull'intera
    cartella uploads conterebbe anche i file della clinica B."""
    from struttura_service import contenuto_struttura
    con, ids = conn
    con.execute("UPDATE apparecchi SET foto_path=? WHERE id=?",
               ('foto/A.jpg', ids['a']['apparecchio']))
    con.execute("UPDATE apparecchi SET foto_path=? WHERE id=?",
               ('foto/B.jpg', ids['b']['apparecchio']))
    con.commit()

    uploads = tmp_path / 'uploads'
    cartella = uploads / 'foto'
    cartella.mkdir(parents=True)
    (cartella / 'A.jpg').write_bytes(b'0' * 100)
    (cartella / 'B.jpg').write_bytes(b'0' * 250)

    c = contenuto_struttura(con, ids['a']['struttura'], str(uploads), single_struttura=True)
    assert c['file'] == 1
    assert c['byte'] == 100


# ---------------------------------------------------------------------------
# Isolamento dei dati del deployment sorgente (rilievi 1/5 della revisione)
# ---------------------------------------------------------------------------

def test_esportazione_svuota_le_tabelle_del_deployment_sorgente(conn, tmp_path):
    """importa_installazione.py dichiara esplicitamente (vedi CLAUDE.md) di
    non importare sessioni, login_attempts, api_tokens, email_config e
    import_preview: appartengono al deployment sorgente o sono transitorie.
    rimuovi_strutture(copia, altre), da sola, non le tocca affatto se
    riguardano la struttura ESPORTATA (non e' 'un'altra struttura'), e
    login_attempts non ha nemmeno una FK verso strutture. L'archivio non deve
    portarsele dietro: nessuno le reimportera' mai."""
    import sqlite3
    from struttura_service import esporta_struttura
    con, ids = conn

    con.execute("INSERT INTO sessioni (utente_id,token,expires_at) VALUES (?,?,datetime('now','+1 day'))",
               (ids['a']['utente'], 'tok-a'))
    con.execute("INSERT INTO login_attempts (ip_address,email,esito) VALUES ('1.2.3.4','x@x.it','riuscito')")
    con.execute("INSERT INTO api_tokens (struttura_id,nome,token_hash,created_by) VALUES (?,?,?,?)",
               (ids['a']['struttura'], 'Token A', 'hash-a', ids['a']['utente']))
    con.execute("INSERT INTO email_config (divisione_id,email_account,email_password_encrypted,imap_server) "
               "VALUES (NULL,'imap@x.it','cifrato','imap.x.it')")
    import_id = con.execute(
        "INSERT INTO import_history (struttura_id,divisione_id,tipo_import,filename,filepath,imported_by) "
        "VALUES (?,?,'inventario','y.xlsx','y/y.xlsx',?)",
        (ids['a']['struttura'], ids['a']['divisione'], ids['a']['utente'])).lastrowid
    con.execute("INSERT INTO import_preview (import_id,stato) VALUES (?,'pending')", (import_id,))
    con.commit()

    percorso_db = con.execute("PRAGMA database_list").fetchone()[2]
    destinazione = esporta_struttura(percorso_db, str(tmp_path / 'uploads'),
                                     ids['a']['struttura'], str(tmp_path / 'archivi'))

    copia = sqlite3.connect(os.path.join(destinazione, 'data', 'database.sqlite'))
    for tabella in ('sessioni', 'login_attempts', 'api_tokens', 'email_config', 'import_preview'):
        assert copia.execute(f"SELECT COUNT(*) FROM {tabella}").fetchone()[0] == 0, tabella
    # Il registro resta, ma solo per la struttura esportata: non deve
    # contenere la voce di B, ne' l'email di B travestita da voce "globale".
    righe = copia.execute("SELECT struttura_id FROM log_attivita").fetchall()
    assert len(righe) == 1
    assert righe[0][0] == ids['a']['struttura']
    copia.close()


# ---------------------------------------------------------------------------
# Reimportabilita' dell'archivio (rilievo 2 della revisione)
# ---------------------------------------------------------------------------

def test_esportazione_e_reimportabile_dallo_strumento_di_importazione(conn, tmp_path):
    """L'archivio deve avere la forma che importa_installazione.py si
    aspetta senza bisogno di --db esplicito: nome del database e config.json
    con database_path/uploads_path/nome struttura."""
    from struttura_service import esporta_struttura
    from importa_installazione import Sorgente
    con, ids = conn
    percorso_db = con.execute("PRAGMA database_list").fetchone()[2]

    destinazione = esporta_struttura(percorso_db, str(tmp_path / 'uploads'),
                                     ids['a']['struttura'], str(tmp_path / 'archivi'))

    sorgente = Sorgente(destinazione)
    assert os.path.normpath(sorgente.db_path) == os.path.join(destinazione, 'data', 'database.sqlite')
    assert os.path.exists(sorgente.db_path)
    assert sorgente.nome_struttura_suggerito() == 'Clinica A'
    sorgente.chiudi()


# ---------------------------------------------------------------------------
# Pulizia dopo un'esportazione fallita (rilievo 4 della revisione)
# ---------------------------------------------------------------------------

def test_esportazione_fallita_non_lascia_una_copia_integrale_su_disco(conn, tmp_path, monkeypatch):
    """Se l'esportazione fallisce a meta' (qui: rimuovi_strutture solleva),
    sul disco resta gia' una cartella con dentro il backup COMPLETO di tutti
    i tenant (rimuovi_strutture non e' ancora girata). Deve essere rimossa,
    non lasciata a tempo indeterminato indistinguibile da un archivio buono
    tranne che per l'assenza di ESPORTAZIONE.txt."""
    import struttura_service as svc
    con, ids = conn
    percorso_db = con.execute("PRAGMA database_list").fetchone()[2]

    class OrologioFermo(svc.datetime):
        @classmethod
        def now(cls):
            return svc.datetime(2026, 2, 2, 10, 0, 0)
    monkeypatch.setattr(svc, 'datetime', OrologioFermo)

    def rompi(*a, **k):
        raise RuntimeError('backup interrotto')
    monkeypatch.setattr(svc, 'rimuovi_strutture', rompi)

    cartella_archivi = str(tmp_path / 'archivi')
    atteso = os.path.join(cartella_archivi, svc._nome_cartella('Clinica A'))

    with pytest.raises(RuntimeError):
        svc.esporta_struttura(percorso_db, str(tmp_path / 'uploads'), ids['a']['struttura'],
                              cartella_archivi)

    assert not os.path.exists(atteso)


# ---------------------------------------------------------------------------
# ESPORTAZIONE.txt completo (rilievo 6 della revisione)
# ---------------------------------------------------------------------------

def test_esportazione_txt_elenca_tutte_le_chiavi_di_contenuto_struttura(conn, tmp_path):
    """contenuto_struttura restituisce 10 chiavi (piu' 'byte'): ESPORTAZIONE.txt
    ne elencava solo 8, mancavano 'import' e 'tecnici'."""
    from struttura_service import esporta_struttura
    con, ids = conn
    percorso_db = con.execute("PRAGMA database_list").fetchone()[2]

    destinazione = esporta_struttura(percorso_db, str(tmp_path / 'uploads'),
                                     ids['a']['struttura'], str(tmp_path / 'archivi'))

    with open(os.path.join(destinazione, 'ESPORTAZIONE.txt'), encoding='utf-8') as f:
        testo = f.read()
    for chiave in ('apparecchi', 'manutenzioni', 'verifiche', 'documenti', 'accessori',
                  'import', 'divisioni', 'utenti', 'tecnici', 'file', 'byte'):
        assert f"  {chiave:14}" in testo, chiave


# ---------------------------------------------------------------------------
# Parita' single/multi degli allegati (rilievo 1 del giro di correzioni 2)
# ---------------------------------------------------------------------------

def _leggi_conteggi_file_byte(destinazione):
    """'file' e 'byte' da ESPORTAZIONE.txt, per confrontare due archivi senza
    ripetere il parsing in ogni test."""
    with open(os.path.join(destinazione, 'ESPORTAZIONE.txt'), encoding='utf-8') as f:
        testo = f.read()
    valori = {}
    for riga in testo.splitlines():
        parti = riga.split()
        if len(parti) == 2 and parti[0] in ('file', 'byte'):
            valori[parti[0]] = int(parti[1])
    return valori


def test_esportazione_single_e_multi_producono_gli_stessi_allegati(conn, tmp_path):
    """La stessa struttura, con un percorso valorizzato per OGNI colonna di
    COLONNE_ALLEGATI, esportata prima in multi e poi in single: l'insieme
    degli allegati (a meno del prefisso strutture/<id>/) e i conteggi
    file/byte di ESPORTAZIONE.txt devono coincidere. Una colonna presente
    in una modalita' e dimenticata nell'altra (il difetto del giro 1 per
    import_history.filepath e strutture.logo_path) fa divergere gli insiemi
    senza che nessuno dei due test 'a colonna singola' se ne accorga da
    solo: qui la stessa struttura produce due archivi che devono essere
    lo stesso archivio."""
    from struttura_service import esporta_struttura
    con, ids = conn
    a = ids['a']['struttura']
    apparecchio = ids['a']['apparecchio']

    manutenzione_id = con.execute(
        "SELECT id FROM manutenzioni WHERE apparecchio_id=?", (apparecchio,)).fetchone()[0]
    verifica_id = con.execute(
        "SELECT id FROM verifiche WHERE apparecchio_id=?", (apparecchio,)).fetchone()[0]
    import_id = con.execute(
        "SELECT id FROM import_history WHERE struttura_id=?", (a,)).fetchone()[0]

    # Un percorso per ciascuna riga di COLONNE_ALLEGATI: se una viene
    # dimenticata in futuro, questo test smette di essere vuoto per quella
    # colonna e la differenza emerge nel confronto finale.
    percorsi = {
        ('apparecchi', 'foto_path', apparecchio): 'foto/A.jpg',
        ('documenti', 'filepath', apparecchio): 'documenti/A.pdf',
        ('manutenzioni', 'verbale_path', manutenzione_id): 'verbali/A.pdf',
        ('verifiche', 'documento_path', verifica_id): 'verifiche/A.pdf',
        ('import_history', 'filepath', import_id): 'import/A.xlsx',
        ('strutture', 'logo_path', a): 'loghi/A.png',
    }
    for (tabella, colonna, riga_id), relativo in percorsi.items():
        con.execute(f"UPDATE {tabella} SET {colonna}=? WHERE id=?", (relativo, riga_id))
    con.commit()
    percorso_db = con.execute("PRAGMA database_list").fetchone()[2]

    relativi = set(percorsi.values())

    uploads_multi = tmp_path / 'multi'
    for rel in relativi:
        p = uploads_multi / 'strutture' / str(a) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'contenuto-' + rel.encode())
    dest_multi = esporta_struttura(percorso_db, str(uploads_multi), a, str(tmp_path / 'archivi-multi'))

    uploads_single = tmp_path / 'single'
    for rel in relativi:
        p = uploads_single / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'contenuto-' + rel.encode())
    dest_single = esporta_struttura(percorso_db, str(uploads_single), a,
                                    str(tmp_path / 'archivi-single'), single_struttura=True)

    radice_multi = os.path.join(dest_multi, 'uploads', 'strutture', str(a))
    trovati_multi = {
        os.path.relpath(os.path.join(dirpath, f), radice_multi).replace(os.sep, '/')
        for dirpath, _sotto, file_presenti in os.walk(radice_multi)
        for f in file_presenti
    }
    radice_single = os.path.join(dest_single, 'uploads')
    trovati_single = {
        os.path.relpath(os.path.join(dirpath, f), radice_single).replace(os.sep, '/')
        for dirpath, _sotto, file_presenti in os.walk(radice_single)
        for f in file_presenti
    }

    assert trovati_multi == relativi
    assert trovati_single == relativi
    assert _leggi_conteggi_file_byte(dest_multi) == _leggi_conteggi_file_byte(dest_single)


# COLONNE_ALLEGATI e' l'elenco delle colonne che contengono un percorso di
# allegato. Il test qui sotto lo confronta con lo schema per introspezione,
# invece di ripetere a mano un elenco che divergerebbe alla prima colonna
# nuova: e' esattamente cosi' che import_history.filepath e strutture.logo_path
# sono rimasti fuori. Una colonna di percorso non registrata resta fuori
# dall'archivio in modalita' single mentre la riga che la referenzia ci entra,
# e la cancellazione di una struttura la lascia orfana sul disco dopo aver
# dichiarato di averla salvata.
#
# Se una colonna con 'path' nel nome NON e' un allegato della struttura, va
# messa qui con la sua ragione: una riga in piu' in un test e' il prezzo per
# non poter piu' dimenticare quella importante.
COLONNE_PATH_NON_ALLEGATI = {
    # (tabella, colonna): perche' non e' un allegato di una struttura
}


def test_ogni_colonna_di_percorso_dello_schema_e_registrata(app):
    """Introspezione dello schema migrato, non un elenco letterale.

    Gira sul database che l'applicazione costruisce davvero all'avvio
    (schema.sql piu' le migrazioni incrementali di apply_schema_updates), non
    su schema.sql da solo: logo_path, una delle due colonne dimenticate, e'
    aggiunta da una migrazione oltre che presente nello schema, e una colonna
    introdotta solo da una migrazione futura non comparirebbe altrimenti.
    """
    from struttura_service import COLONNE_ALLEGATI
    from models import get_db

    with app.app_context():
        db = get_db()
        tabelle = [r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'").fetchall()]
        colonne_schema = {
            (tabella, r[1])
            for tabella in tabelle
            for r in db.execute(f"PRAGMA table_info({tabella})").fetchall()
        }

    candidate = {(t, c) for t, c in colonne_schema if 'path' in c.lower()}
    registrate = {(t, c) for t, c, _dove in COLONNE_ALLEGATI}

    dimenticate = candidate - registrate - set(COLONNE_PATH_NON_ALLEGATI)
    assert not dimenticate, (
        f"colonne di percorso non registrate in COLONNE_ALLEGATI: "
        f"{sorted(dimenticate)}. Se non sono allegati di una struttura, "
        f"aggiungile a COLONNE_PATH_NON_ALLEGATI con la ragione."
    )

    # E il verso opposto: una riga di COLONNE_ALLEGATI che nomina una colonna
    # inesistente non fallisce, viene ignorata dal try/except di
    # _percorsi_allegati. Sarebbe una copertura solo apparente.
    inesistenti = registrate - colonne_schema
    assert not inesistenti, (
        f"COLONNE_ALLEGATI nomina colonne che lo schema non ha: "
        f"{sorted(inesistenti)}"
    )
