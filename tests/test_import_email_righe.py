"""Un verbale email con piu' interventi non deve perderne per strada.

Fino alla 2.8.0 il PDF arrivato via IMAP produceva un solo record in
import_history: se l'AI ne estraeva dieci interventi e uno solo veniva
importato in automatico, il record passava a 'completed' e gli altri nove
uscivano dalla coda senza essere mai stati lavorati. Sopravvivevano solo
dentro il JSON grezzo della risposta AI, che nessuna pagina mostrava, e la
revisione manuale faceva vedere comunque il primo elemento e basta.

Adesso ogni elemento estratto ha la sua riga in import_preview, con il motivo
per cui non e' entrato, e import_history resta 'pending' finche' una riga e'
in attesa. Questi test coprono le due meta': cosa scrive email_monitor e cosa
ne fanno le rotte di revisione.
"""
import email.message
import json
import os
import sqlite3

import pytest
from werkzeug.security import generate_password_hash


def entra(client, indirizzo):
    client.post('/login', data={'email': indirizzo, 'password': 'Passw0rd!'})


def _conn(app):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def scenario(app):
    """Una struttura con una divisione e due apparecchi di matricola nota."""
    from models import execute
    with app.app_context():
        s = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica E','IE',1)").lastrowid
        d = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Div E','DVE',?)", (s,)).lastrowid
        pw = generate_password_hash('Passw0rd!')
        execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('admine@i.it',?,'A','E','admin',?,0)", (pw, s)
        )
        a1 = execute(
            "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato) "
            "VALUES (?,?,'MAT-001','Acme','Uno','funzionante')", (d, s)
        ).lastrowid
        a2 = execute(
            "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato) "
            "VALUES (?,?,'MAT-002','Acme','Due','funzionante')", (d, s)
        ).lastrowid
    return {'struttura': s, 'divisione': d, 'app1': a1, 'app2': a2}


# ---------------------------------------------------------------------------
# email_monitor: una riga import_preview per ogni elemento estratto
# ---------------------------------------------------------------------------

class FintaCasella:
    """Il minimo che _process_email chiede a una connessione IMAP."""

    def __init__(self, messaggio_bytes):
        self._raw = messaggio_bytes

    def fetch(self, msg_id, parti):
        return 'OK', [(b'1 (BODY[] {1})', self._raw)]


def _email_con_pdf():
    msg = email.message.EmailMessage()
    msg['Subject'] = 'Verbale manutenzione'
    msg['From'] = 'assistenza@ditta.it'
    msg.set_content('In allegato il verbale.')
    msg.add_attachment(b'%PDF-1.4 finto', maintype='application',
                       subtype='pdf', filename='verbale.pdf')
    return msg.as_bytes()


def _lancia_monitor(app, scenario, monkeypatch, tipo_documento, elementi):
    """Esegue _process_email con l'AI sostituita da risposte fisse."""
    import ai_service
    import email_monitor

    monkeypatch.setattr(ai_service, 'extract_from_pdf',
                        lambda percorso: 'testo del verbale sufficientemente lungo')
    monkeypatch.setattr(ai_service, 'classify_email_document_type',
                        lambda *a, **k: tipo_documento)
    risposta = json.dumps(elementi)
    monkeypatch.setattr(ai_service, 'parse_verbale_with_ai',
                        lambda *a, **k: (elementi, risposta))
    monkeypatch.setattr(ai_service, 'analyze_verifiche_with_ai',
                        lambda *a, **k: (elementi, risposta))

    uploads_dir = os.path.join(app.config['UPLOADS_PATH'], 'email')
    os.makedirs(uploads_dir, exist_ok=True)
    email_monitor._process_email(
        FintaCasella(_email_con_pdf()), b'1', scenario['divisione'],
        'chiave-finta', 'modello-finto', uploads_dir,
        app.config['DATABASE_PATH'], {},
        app_config={'single_struttura': False},
        struttura_id=scenario['struttura'])


def _ultimo_import(app):
    conn = _conn(app)
    try:
        rec = conn.execute("SELECT * FROM import_history ORDER BY id DESC LIMIT 1").fetchone()
        righe = conn.execute(
            "SELECT * FROM import_preview WHERE import_id = ? ORDER BY riga_numero",
            (rec['id'],)
        ).fetchall()
        return rec, righe
    finally:
        conn.close()


def test_verbale_parzialmente_importato_resta_in_coda(app, scenario, monkeypatch):
    """Tre interventi, uno solo importabile: il record non e' 'completed' e le
    due righe rimaste sono raggiungibili una per una, con il loro motivo."""
    _lancia_monitor(app, scenario, monkeypatch, 'verbale', [
        {'matricola': 'MAT-001', 'tipo': 'preventiva', 'data_intervento': '2026-03-01'},
        {'matricola': 'SCONOSCIUTA', 'tipo': 'preventiva', 'data_intervento': '2026-03-01'},
        {'matricola': 'MAT-002', 'tipo': 'preventiva'},
    ])
    rec, righe = _ultimo_import(app)

    assert rec['stato'] == 'pending'
    assert rec['totale_righe'] == 3
    assert rec['righe_importate'] == 1
    assert [r['stato'] for r in righe] == ['imported', 'pending', 'pending']
    assert righe[1]['note_revisione'] == 'Apparecchio non individuato dalla matricola'
    assert righe[2]['note_revisione'] == "Data dell'intervento assente"
    assert righe[2]['apparecchio_match_id'] == scenario['app2']
    assert [json.loads(r['dati_estratti'])['matricola'] for r in righe] == \
        ['MAT-001', 'SCONOSCIUTA', 'MAT-002']

    conn = _conn(app)
    try:
        assert conn.execute("SELECT COUNT(*) FROM manutenzioni").fetchone()[0] == 1
    finally:
        conn.close()


def test_verbale_tutto_importato_e_completed(app, scenario, monkeypatch):
    """Quando ogni elemento entra, il record esce dalla coda."""
    _lancia_monitor(app, scenario, monkeypatch, 'verbale', [
        {'matricola': 'MAT-001', 'tipo': 'preventiva', 'data_intervento': '2026-03-01'},
        {'matricola': 'MAT-002', 'tipo': 'correttiva', 'data_intervento': '2026-03-02'},
    ])
    rec, righe = _ultimo_import(app)

    assert rec['stato'] == 'completed'
    assert rec['righe_importate'] == 2
    assert rec['errori_dettaglio'] is None
    assert [r['stato'] for r in righe] == ['imported', 'imported']


def test_verifiche_contano_solo_quelle_davvero_inserite(app, scenario, monkeypatch):
    """righe_importate contava gli elementi estratti, non gli INSERT riusciti:
    una verifica senza data risultava importata pur non esistendo."""
    _lancia_monitor(app, scenario, monkeypatch, 'verifica_elettrica', [
        {'matricola': 'MAT-001', 'data_verifica': '2026-03-01', 'esito': 'positivo'},
        {'matricola': 'MAT-002', 'esito': 'positivo'},
    ])
    rec, righe = _ultimo_import(app)

    assert rec['tipo_import'] == 'verifica_elettrica'
    assert rec['stato'] == 'pending'
    assert rec['totale_righe'] == 2
    assert rec['righe_importate'] == 1
    assert [r['stato'] for r in righe] == ['imported', 'pending']
    assert righe[1]['note_revisione'] == 'Data della verifica assente'

    conn = _conn(app)
    try:
        assert conn.execute("SELECT COUNT(*) FROM verifiche").fetchone()[0] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Rotte di revisione: una riga alla volta
# ---------------------------------------------------------------------------

def _crea_import(app, scenario, righe, stato='pending'):
    """Un import email con le righe indicate, come lo scriverebbe il monitor."""
    from models import execute
    with app.app_context():
        import_id = execute(
            """INSERT INTO import_history
               (tipo_import, filename, filepath, divisione_id, struttura_id,
                email_from, email_subject, totale_righe, righe_importate, stato, ai_response)
               VALUES ('verbale_email','verbale.pdf','email/verbale.pdf',?,?,
                       'assistenza@ditta.it','Verbale',?,0,?,?)""",
            (scenario['divisione'], scenario['struttura'], len(righe), stato,
             json.dumps([r[0] for r in righe]))
        ).lastrowid
        for numero, (dati, stato_riga) in enumerate(righe, start=1):
            execute(
                """INSERT INTO import_preview
                   (import_id, riga_numero, dati_estratti, stato, note_revisione)
                   VALUES (?, ?, ?, ?, 'Apparecchio non individuato dalla matricola')""",
                (import_id, numero, json.dumps(dati), stato_riga)
            )
    return import_id


DUE_RIGHE = [
    ({'matricola': 'MAT-001', 'tipo': 'preventiva', 'data_intervento': '2026-03-01'}, 'pending'),
    ({'matricola': 'MAT-002', 'tipo': 'correttiva', 'data_intervento': '2026-03-02'}, 'pending'),
]


def _stati_righe(app, import_id):
    conn = _conn(app)
    try:
        return [(r['id'], r['stato']) for r in conn.execute(
            "SELECT id, stato FROM import_preview WHERE import_id=? ORDER BY riga_numero",
            (import_id,)).fetchall()]
    finally:
        conn.close()


def _stato_import(app, import_id):
    conn = _conn(app)
    try:
        return conn.execute("SELECT stato, righe_importate FROM import_history WHERE id=?",
                            (import_id,)).fetchone()
    finally:
        conn.close()


def test_dettaglio_mostra_tutte_le_righe_pendenti(client, app, scenario):
    """La pagina di revisione mostrava solo il primo elemento del PDF."""
    import_id = _crea_import(app, scenario, DUE_RIGHE)
    entra(client, 'admine@i.it')
    risposta = client.get(f'/import/email/{import_id}')
    corpo = risposta.get_data(as_text=True)

    assert risposta.status_code == 200
    assert 'MAT-001' in corpo
    assert 'MAT-002' in corpo


def test_conferma_di_una_riga_lascia_il_record_in_coda(client, app, scenario):
    """La prima conferma chiudeva l'intero record: la seconda riga spariva."""
    import_id = _crea_import(app, scenario, DUE_RIGHE)
    righe = _stati_righe(app, import_id)
    entra(client, 'admine@i.it')

    client.post(f'/import/email/{import_id}/conferma', data={
        'preview_id': str(righe[0][0]),
        'apparecchio_id': str(scenario['app1']),
        'tipo': 'preventiva',
        'data_intervento': '2026-03-01',
    }, follow_redirects=True)

    assert _stati_righe(app, import_id) == [(righe[0][0], 'imported'), (righe[1][0], 'pending')]
    rec = _stato_import(app, import_id)
    assert rec['stato'] == 'pending'
    assert rec['righe_importate'] == 1


def test_ultima_conferma_chiude_il_record(client, app, scenario):
    import_id = _crea_import(app, scenario, DUE_RIGHE)
    righe = _stati_righe(app, import_id)
    entra(client, 'admine@i.it')

    for preview_id, apparecchio in ((righe[0][0], scenario['app1']),
                                    (righe[1][0], scenario['app2'])):
        client.post(f'/import/email/{import_id}/conferma', data={
            'preview_id': str(preview_id),
            'apparecchio_id': str(apparecchio),
            'tipo': 'preventiva',
            'data_intervento': '2026-03-01',
        }, follow_redirects=True)

    assert [s for _, s in _stati_righe(app, import_id)] == ['imported', 'imported']
    rec = _stato_import(app, import_id)
    assert rec['stato'] == 'completed'
    assert rec['righe_importate'] == 2

    conn = _conn(app)
    try:
        assert conn.execute("SELECT COUNT(*) FROM manutenzioni").fetchone()[0] == 2
    finally:
        conn.close()


def test_riga_gia_lavorata_non_si_conferma_due_volte(client, app, scenario):
    import_id = _crea_import(app, scenario, [
        DUE_RIGHE[0], ({'matricola': 'MAT-002'}, 'rejected'),
    ])
    righe = _stati_righe(app, import_id)
    entra(client, 'admine@i.it')

    client.post(f'/import/email/{import_id}/conferma', data={
        'preview_id': str(righe[1][0]),
        'apparecchio_id': str(scenario['app2']),
        'tipo': 'preventiva',
        'data_intervento': '2026-03-01',
    }, follow_redirects=True)

    assert _stati_righe(app, import_id) == [(righe[0][0], 'pending'), (righe[1][0], 'rejected')]
    conn = _conn(app)
    try:
        assert conn.execute("SELECT COUNT(*) FROM manutenzioni").fetchone()[0] == 0
    finally:
        conn.close()


def test_riga_di_un_altro_import_viene_rifiutata(client, app, scenario):
    """preview_id e' un id globale: senza il vincolo import_id si confermava
    la riga di un altro verbale passando il suo numero."""
    primo = _crea_import(app, scenario, DUE_RIGHE)
    secondo = _crea_import(app, scenario, DUE_RIGHE)
    righe_secondo = _stati_righe(app, secondo)
    entra(client, 'admine@i.it')

    client.post(f'/import/email/{primo}/conferma', data={
        'preview_id': str(righe_secondo[0][0]),
        'apparecchio_id': str(scenario['app1']),
        'tipo': 'preventiva',
        'data_intervento': '2026-03-01',
    }, follow_redirects=True)

    assert [s for _, s in _stati_righe(app, secondo)] == ['pending', 'pending']
    assert _stato_import(app, secondo)['stato'] == 'pending'
    conn = _conn(app)
    try:
        assert conn.execute("SELECT COUNT(*) FROM manutenzioni").fetchone()[0] == 0
    finally:
        conn.close()


def test_scarta_di_una_riga_non_tocca_le_altre(client, app, scenario):
    import_id = _crea_import(app, scenario, DUE_RIGHE)
    righe = _stati_righe(app, import_id)
    entra(client, 'admine@i.it')

    client.post(f'/import/email/{import_id}/scarta',
                data={'preview_id': str(righe[0][0])}, follow_redirects=True)

    assert _stati_righe(app, import_id) == [(righe[0][0], 'rejected'), (righe[1][0], 'pending')]
    assert _stato_import(app, import_id)['stato'] == 'pending'


def test_scarta_del_verbale_chiude_ogni_riga_pendente(client, app, scenario):
    """Le righe restavano 'pending' per sempre dopo lo scarto del record."""
    import_id = _crea_import(app, scenario, DUE_RIGHE)
    entra(client, 'admine@i.it')

    client.post(f'/import/email/{import_id}/scarta', data={}, follow_redirects=True)

    assert [s for _, s in _stati_righe(app, import_id)] == ['rejected', 'rejected']
    assert _stato_import(app, import_id)['stato'] == 'failed'
