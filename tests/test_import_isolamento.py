"""Il difetto gemello di Critico 1 in import_bp.py.

Tre punti che si concatenano: (1) analizza() salta il controllo di divisione
per il ruolo 'admin', (2) il thread di analisi riceve g.struttura_id invece
del valore autoritativo gia' usato per la INSERT in import_history, e come
conseguenza _match_apparecchi cerca senza filtro di struttura quando
g.struttura_id e' None; (3) _execute_verbali/_execute_verifiche si fidano di
apparecchio_match_id (il risultato di quel match automatico) senza
riverificarlo, mentre l'override manuale accanto passa gia' da
apparecchio_accessibile().
"""
import io
import time

import pytest
from werkzeug.security import generate_password_hash


def entra(client, email):
    client.post('/login', data={'email': email, 'password': 'Passw0rd!'})


@pytest.fixture
def strutture_import(app):
    """Struttura B: dove vive l'import legittimo (admin + divisione + un
    apparecchio con marca distintiva). Struttura Z: estranea, non collegata
    all'import in alcun modo, con un apparecchio di matricola identica a uno
    scenario di test — e' il bersaglio che nessuna delle due rotte deve poter
    raggiungere."""
    from models import execute
    with app.app_context():
        b = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica B','IB',1)").lastrowid
        z = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica Z','IZ',1)").lastrowid
        db_ = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Div B','DVB',?)", (b,)).lastrowid
        dz = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Div Z','DVZ',?)", (z,)).lastrowid
        hash_pw = generate_password_hash('Passw0rd!')
        admin_b = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('adminb@i.it',?,'A','B','admin',?,0)", (hash_pw, b)
        ).lastrowid
        utente_b = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('utenteb@i.it',?,'U','B','utente',?,0)", (hash_pw, b)
        ).lastrowid
        execute(
            "INSERT INTO utenti_divisioni (utente_id,divisione_id,ruolo_divisione) VALUES (?,?,'utente')",
            (utente_b, db_)
        )
        app_z = execute(
            "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato) "
            "VALUES (?,?,'DUP-001','SBAGLIATO','Z1','funzionante')", (dz, z)
        ).lastrowid
    return {
        'b': b, 'z': z, 'divisione_b': db_, 'divisione_z': dz,
        'admin_b_id': admin_b, 'utente_b_id': utente_b, 'app_z': app_z,
    }


def _configura_ai_globale(app):
    """Fa passare il controllo 'AI configurata' senza chiamare davvero un
    provider: le funzioni che parlano con l'AI vengono comunque sostituite
    nei singoli test con monkeypatch."""
    app.config['APP_CONFIG']['ai_provider'] = 'anthropic'
    app.config['APP_CONFIG']['anthropic_api_key'] = 'chiave-finta-di-test'


def _attendi_import(app, import_id, timeout=5.0):
    """Il thread di analisi gira in background: aspetta che finisca (o
    fallisca) invece di indovinare quanto ci mette."""
    from models import query_one
    scaduto = time.time() + timeout
    while time.time() < scaduto:
        with app.app_context():
            rec = query_one("SELECT stato FROM import_history WHERE id=?", (import_id,))
        if rec and rec['stato'] in ('completed', 'failed'):
            return rec['stato']
        time.sleep(0.05)
    raise AssertionError("il thread di analisi non ha finito entro il timeout")


# ---------------------------------------------------------------------------
# Punto 1: analizza() salta il controllo di divisione per l'admin
# ---------------------------------------------------------------------------

def test_admin_non_puo_importare_su_divisione_di_altra_struttura(client, app, strutture_import):
    """Un admin della struttura B non deve poter attribuire un import a una
    divisione della struttura Z, indovinandone l'id."""
    _configura_ai_globale(app)
    entra(client, 'adminb@i.it')
    dati = {
        'file': (io.BytesIO(b'colonna1,colonna2\nabc,def\n'), 'test.csv'),
        'divisione_id': str(strutture_import['divisione_z']),
    }
    risposta = client.post('/import/analizza', data=dati,
                           content_type='multipart/form-data', follow_redirects=True)
    testo = risposta.data.decode('utf-8', errors='replace')
    assert 'Divisione non accessibile' in testo


# ---------------------------------------------------------------------------
# Punto 2: il thread riceve g.struttura_id invece di _struttura_import
# ---------------------------------------------------------------------------

def test_match_automatico_non_trova_apparecchi_di_altre_strutture(client, app, monkeypatch, strutture_import):
    """Uno scenario legittimo, non anomalo: un utente della struttura B, la
    cui struttura viene disattivata mentre lui e' collegato, resta assegnato
    alla sua divisione
    (utenti_divisioni non dipende dallo stato della struttura) e supera quindi
    il controllo di divisione — ma g.struttura_id e' None. L'unico apparecchio
    con quella matricola vive nella struttura Z, estranea: se il match
    automatico usa g.struttura_id (None) invece della struttura autoritativa
    dell'import, lo trova comunque. Deve restare senza match."""
    from models import execute, query_one
    _configura_ai_globale(app)

    import ai_service
    monkeypatch.setattr(ai_service, 'extract_text_from_file',
                        lambda filepath, filetype: 'testo estratto di prova, non vuoto')
    monkeypatch.setattr(ai_service, 'classify_document_type',
                        lambda text, api_key, model, config=None, struttura_id=None: 'verbale_manutenzione')
    monkeypatch.setattr(
        ai_service, 'parse_verbale_with_ai',
        lambda pdf_text, api_key, model='x', config=None, struttura_id=None: (
            [{'matricola': 'DUP-001', 'tipo': 'preventiva', 'data_intervento': '2026-01-01'}],
            'risposta finta'
        )
    )

    entra(client, 'utenteb@i.it')
    # La struttura si disattiva a sessione gia' aperta: dalla 2.8.0 il login su
    # una struttura non attiva viene rifiutato, ma una sessione aperta prima
    # resta valida ed e' esattamente da li' che nasce g.struttura_id a None.
    with app.app_context():
        execute("UPDATE strutture SET attiva = 0 WHERE id = ?", (strutture_import['b'],))

    dati = {
        'file': (io.BytesIO(b'testo finto'), 'verbale.csv'),
        'divisione_id': str(strutture_import['divisione_b']),
    }
    risposta = client.post('/import/analizza', data=dati, content_type='multipart/form-data')
    assert risposta.status_code == 302

    with app.app_context():
        import_id = query_one(
            "SELECT id FROM import_history WHERE struttura_id=? ORDER BY id DESC LIMIT 1",
            (strutture_import['b'],)
        )['id']

    stato = _attendi_import(app, import_id)
    assert stato == 'completed'

    with app.app_context():
        match = query_one(
            "SELECT apparecchio_match_id FROM import_preview WHERE import_id=?", (import_id,)
        )
    assert match['apparecchio_match_id'] is None


# ---------------------------------------------------------------------------
# Punto 3: _execute_verbali / _execute_verifiche non riverificano il match
# automatico prima di scriverlo
# ---------------------------------------------------------------------------

def test_esegui_verbale_non_scrive_su_apparecchio_di_altra_struttura(client, app, strutture_import):
    """Simula un import_preview gia' analizzato il cui match automatico punta
    a un apparecchio della struttura Z (come accadrebbe col difetto del
    Punto 2, o per qualunque altra ragione): l'esecuzione non deve creare la
    manutenzione, l'override automatico va riverificato come quello manuale."""
    from models import execute, query_one
    entra(client, 'adminb@i.it')
    with app.app_context():
        import_id = execute(
            """INSERT INTO import_history
               (tipo_import, filename, filepath, divisione_id, struttura_id, stato, imported_by)
               VALUES ('verbale_manutenzione','f.pdf','x/f.pdf',?,?,'completed',?)""",
            (strutture_import['divisione_b'], strutture_import['b'], strutture_import['admin_b_id'])
        ).lastrowid
        preview_id = execute(
            """INSERT INTO import_preview
               (import_id, riga_numero, dati_estratti, apparecchio_match_id, match_confidence, stato)
               VALUES (?, 1, ?, ?, 1.0, 'pending')""",
            (import_id,
             '{"matricola":"DUP-001","tipo":"preventiva","data_intervento":"2026-01-01"}',
             strutture_import['app_z'])
        ).lastrowid

    client.post(f'/import/{import_id}/esegui', data={'selected': [str(preview_id)]})

    with app.app_context():
        manutenzione = query_one(
            "SELECT id FROM manutenzioni WHERE apparecchio_id=?", (strutture_import['app_z'],)
        )
        riga = query_one("SELECT stato FROM import_preview WHERE id=?", (preview_id,))
    assert manutenzione is None
    assert riga['stato'] == 'rejected'


def test_esegui_verifica_non_scrive_su_apparecchio_di_altra_struttura(client, app, strutture_import):
    """Stessa forma della precedente, file gemello _execute_verifiche."""
    from models import execute, query_one
    entra(client, 'adminb@i.it')
    with app.app_context():
        import_id = execute(
            """INSERT INTO import_history
               (tipo_import, filename, filepath, divisione_id, struttura_id, stato, imported_by)
               VALUES ('verifica_elettrica','f.pdf','x/f.pdf',?,?,'completed',?)""",
            (strutture_import['divisione_b'], strutture_import['b'], strutture_import['admin_b_id'])
        ).lastrowid
        preview_id = execute(
            """INSERT INTO import_preview
               (import_id, riga_numero, dati_estratti, apparecchio_match_id, match_confidence, stato)
               VALUES (?, 1, ?, ?, 1.0, 'pending')""",
            (import_id,
             '{"matricola":"DUP-001","data_verifica":"2026-01-01","esito":"positivo"}',
             strutture_import['app_z'])
        ).lastrowid

    client.post(f'/import/{import_id}/esegui', data={'selected': [str(preview_id)]})

    with app.app_context():
        verifica = query_one(
            "SELECT id FROM verifiche WHERE apparecchio_id=?", (strutture_import['app_z'],)
        )
        riga = query_one("SELECT stato FROM import_preview WHERE id=?", (preview_id,))
    assert verifica is None
    assert riga['stato'] == 'rejected'
