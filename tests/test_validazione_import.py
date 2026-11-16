"""M14 - l'import AI scriveva in archivio dati che il form avrebbe rifiutato.

Le tre rotte di esecuzione dell'import e l'auto-import via email costruivano
da soli le proprie regole, piu' permissive di quelle dei form: una data
illeggibile finiva in tabella cosi' com'era, una periodicita' assurda pure,
un tipo di manutenzione sconosciuto diventava 'preventiva', e — il caso
peggiore — un esito di verifica assente o incomprensibile diventava
'positivo'. Un apparecchio mai verificato risultava quindi a norma.

Adesso le regole stanno in un solo posto, `validazione_dominio.py`, e sia gli
esecutori di import_bp sia email_monitor le applicano prima di scrivere. Una
riga incoerente viene respinta (o messa in coda) con il motivo, non
normalizzata a un valore plausibile.
"""
import email.message
import json
import os
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

from validazione_dominio import (TIPO_NON_CLASSIFICATO, valida_apparecchio,
                                 valida_manutenzione, valida_verifica)


def entra(client, indirizzo):
    client.post('/login', data={'email': indirizzo, 'password': 'Passw0rd!'})


# ---------------------------------------------------------------------------
# Il validatore condiviso
# ---------------------------------------------------------------------------

def test_esito_verifica_assente_non_diventa_positivo():
    """M14: il default ottimistico e' il difetto centrale del rilievo."""
    puliti, errori = valida_verifica({'data_verifica': '2026-01-01'})
    assert errori == ["Esito della verifica assente"]
    assert puliti['esito'] is None


def test_esito_verifica_incomprensibile_e_un_errore():
    """M14: 'boh' non e' 'positivo'."""
    _, errori = valida_verifica({'data_verifica': '2026-01-01', 'esito': 'boh'})
    assert errori == ["Esito della verifica non riconosciuto: boh"]


def test_verifica_valida_prende_la_periodicita_predefinita():
    """La periodicita' assente resta a due anni: non afferma nulla
    sull'esito, ed e' il comportamento storico dell'import."""
    puliti, errori = valida_verifica(
        {'data_verifica': '01/02/2026', 'esito': 'Con Riserva'})
    assert errori == []
    assert puliti['data_verifica'] == '2026-02-01'
    assert puliti['esito'] == 'con_riserva'
    assert puliti['periodicita_giorni'] == 730


def test_data_illeggibile_respinta_invece_che_scritta_grezza():
    """M14: prima 'primavera 2026' finiva in manutenzioni.data_intervento."""
    _, errori = valida_manutenzione({'data_intervento': 'primavera 2026'})
    assert errori == ["Data dell'intervento non valida"]


def test_periodicita_assurda_respinta():
    """M14: 100000 giorni sposta la scadenza oltre l'anno 2200."""
    _, errori = valida_manutenzione(
        {'data_intervento': '2026-01-01', 'periodicita_giorni': 100000})
    assert len(errori) == 1
    assert 'eriodicita' in errori[0]


def test_tipo_manutenzione_sconosciuto_non_diventa_preventiva():
    """M14: un tipo dichiarato ma irriconoscibile e' un errore; un tipo
    assente resta 'preventiva' come nel resto del programma."""
    _, errori = valida_manutenzione(
        {'data_intervento': '2026-01-01', 'tipo': 'straordinaria urgente'})
    assert errori == ["Tipo di manutenzione non riconosciuto: straordinaria urgente"]

    puliti, errori = valida_manutenzione({'data_intervento': '2026-01-01'})
    assert errori == []
    assert puliti['tipo'] == 'preventiva'


def test_apparecchio_senza_identificativi_respinto():
    """M14: il form li pretende, l'import li lasciava vuoti."""
    _, errori = valida_apparecchio({'descrizione': 'un apparecchio qualunque'})
    assert sorted(errori) == [
        "Campo obbligatorio assente: marca",
        "Campo obbligatorio assente: matricola",
        "Campo obbligatorio assente: modello",
    ]
    # In aggiornamento gli identificativi non vengono riscritti: nessun errore.
    _, errori = valida_apparecchio({'descrizione': 'x'}, richiedi_identificativi=False)
    assert errori == []


def test_chiavi_estranee_sopravvivono_alla_validazione():
    """Gli esecutori rileggono `_page_file` dal dizionario ripulito: se il
    validatore ricostruisse il dizionario da zero, l'allegato della pagina
    sparirebbe senza che nessun test se ne accorga."""
    puliti, errori = valida_verifica(
        {'data_verifica': '2026-01-01', 'esito': 'positivo',
         '_page_file': 'pagina_3.pdf'})
    assert errori == []
    assert puliti['_page_file'] == 'pagina_3.pdf'


# ---------------------------------------------------------------------------
# Gli esecutori di import_bp
# ---------------------------------------------------------------------------

@pytest.fixture
def scenario(app):
    """Una struttura con divisione, admin e un apparecchio abbinabile."""
    from models import execute
    with app.app_context():
        s = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica M','IM',1)").lastrowid
        d = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Div M','DVM',?)", (s,)).lastrowid
        pw = generate_password_hash('Passw0rd!')
        admin = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('adminm@i.it',?,'A','M','admin',?,0)", (pw, s)
        ).lastrowid
        a = execute(
            "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato) "
            "VALUES (?,?,'MAT-M1','Acme','Uno','funzionante')", (d, s)
        ).lastrowid
    return {'struttura': s, 'divisione': d, 'admin': admin, 'apparecchio': a}


def _prepara_import(app, scenario, tipo_import, dati, match=None):
    from models import execute
    with app.app_context():
        import_id = execute(
            """INSERT INTO import_history
               (tipo_import, filename, filepath, divisione_id, struttura_id, stato, imported_by)
               VALUES (?,'f.pdf','x/f.pdf',?,?,'completed',?)""",
            (tipo_import, scenario['divisione'], scenario['struttura'], scenario['admin'])
        ).lastrowid
        preview_id = execute(
            """INSERT INTO import_preview
               (import_id, riga_numero, dati_estratti, apparecchio_match_id, match_confidence, stato)
               VALUES (?, 1, ?, ?, 1.0, 'pending')""",
            (import_id, json.dumps(dati), match)
        ).lastrowid
    return import_id, preview_id


def test_esegui_verifica_senza_esito_non_scrive_positivo(client, app, scenario):
    """M14: la riga viene respinta col motivo, non archiviata come positiva."""
    from models import query_one
    entra(client, 'adminm@i.it')
    import_id, preview_id = _prepara_import(
        app, scenario, 'verifica_elettrica',
        {'matricola': 'MAT-M1', 'data_verifica': '2026-01-01'},
        match=scenario['apparecchio'])

    client.post(f'/import/{import_id}/esegui', data={'selected': [str(preview_id)]})

    with app.app_context():
        verifica = query_one("SELECT id FROM verifiche WHERE apparecchio_id=?",
                             (scenario['apparecchio'],))
        riga = query_one("SELECT stato, note_revisione FROM import_preview WHERE id=?",
                         (preview_id,))
    assert verifica is None
    assert riga['stato'] == 'rejected'
    assert 'Esito' in riga['note_revisione']


def test_esegui_verifica_incoerente_non_crea_apparecchi(client, app, scenario):
    """M14: la validazione precede la creazione della scheda, altrimenti una
    riga da scartare lascia comunque un apparecchio nuovo in inventario."""
    from models import query_one
    entra(client, 'adminm@i.it')
    import_id, preview_id = _prepara_import(
        app, scenario, 'verifica_elettrica',
        {'data_verifica': '2026-01-01', 'esito': 'forse'})

    client.post(f'/import/{import_id}/esegui', data={
        'selected': [str(preview_id)],
        f'crea_nuovo_{preview_id}': '1',
        f'nuovo_marca_{preview_id}': 'Beta',
        f'nuovo_modello_{preview_id}': 'B1',
        f'nuovo_matricola_{preview_id}': 'MAT-NUOVA',
        f'nuovo_divisione_id_{preview_id}': str(scenario['divisione']),
    })

    with app.app_context():
        creato = query_one("SELECT id FROM apparecchi WHERE matricola='MAT-NUOVA'")
        riga = query_one("SELECT stato FROM import_preview WHERE id=?", (preview_id,))
    assert creato is None
    assert riga['stato'] == 'rejected'


def test_esegui_verbale_con_data_illeggibile_respinto(client, app, scenario):
    """M14: 'primavera 2026' non deve entrare in manutenzioni."""
    from models import query_one
    entra(client, 'adminm@i.it')
    import_id, preview_id = _prepara_import(
        app, scenario, 'verbale_manutenzione',
        {'matricola': 'MAT-M1', 'tipo': 'preventiva',
         'data_intervento': 'primavera 2026'},
        match=scenario['apparecchio'])

    client.post(f'/import/{import_id}/esegui', data={'selected': [str(preview_id)]})

    with app.app_context():
        manutenzione = query_one("SELECT id FROM manutenzioni WHERE apparecchio_id=?",
                                 (scenario['apparecchio'],))
        riga = query_one("SELECT stato, note_revisione FROM import_preview WHERE id=?",
                         (preview_id,))
    assert manutenzione is None
    assert riga['stato'] == 'rejected'
    assert 'Data' in riga['note_revisione']


def test_esegui_inventario_senza_identificativi_respinto(client, app, scenario):
    """M14: l'import creava schede senza marca, modello ne' matricola, che
    nessuna ricerca ritrova e nessun verbale sa abbinare."""
    from models import query_one
    entra(client, 'adminm@i.it')
    import_id, preview_id = _prepara_import(
        app, scenario, 'inventario',
        {'descrizione': 'Apparecchio senza dati identificativi'})

    client.post(f'/import/{import_id}/esegui', data={'selected': [str(preview_id)]})

    with app.app_context():
        creato = query_one(
            "SELECT id FROM apparecchi WHERE descrizione='Apparecchio senza dati identificativi'")
        riga = query_one("SELECT stato, note_revisione FROM import_preview WHERE id=?",
                         (preview_id,))
    assert creato is None
    assert riga['stato'] == 'rejected'
    assert 'obbligatorio' in riga['note_revisione']


# ---------------------------------------------------------------------------
# L'auto-import via email
# ---------------------------------------------------------------------------

class FintaCasella:
    """Il minimo che _process_email chiede a una connessione IMAP."""

    def __init__(self, messaggio_bytes):
        self._raw = messaggio_bytes

    def fetch(self, msg_id, parti):
        return 'OK', [(b'1 (BODY[] {1})', self._raw)]


def _email_con_pdf():
    msg = email.message.EmailMessage()
    msg['Subject'] = 'Documento'
    msg['From'] = 'assistenza@ditta.it'
    msg.set_content('In allegato.')
    msg.add_attachment(b'%PDF-1.4 finto', maintype='application',
                       subtype='pdf', filename='documento.pdf')
    return msg.as_bytes()


def _lancia_monitor(app, scenario, monkeypatch, tipo_documento, elementi):
    import ai_service
    import email_monitor

    monkeypatch.setattr(ai_service, 'extract_from_pdf',
                        lambda percorso: 'testo del documento sufficientemente lungo')
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
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    try:
        rec = conn.execute("SELECT * FROM import_history ORDER BY id DESC LIMIT 1").fetchone()
        righe = conn.execute(
            "SELECT * FROM import_preview WHERE import_id = ? ORDER BY riga_numero",
            (rec['id'],)).fetchall()
        manutenzioni = conn.execute("SELECT COUNT(*) c FROM manutenzioni").fetchone()['c']
        verifiche = conn.execute("SELECT COUNT(*) c FROM verifiche").fetchone()['c']
        return rec, righe, manutenzioni, verifiche
    finally:
        conn.close()


def test_documento_non_classificato_finisce_in_coda(app, scenario, monkeypatch):
    """M14: quando l'AI non riconosce il tipo, il documento veniva importato
    come verbale di manutenzione 'per default'. Adesso resta in coda."""
    _lancia_monitor(app, scenario, monkeypatch, TIPO_NON_CLASSIFICATO, [
        {'matricola': 'MAT-M1', 'tipo': 'preventiva', 'data_intervento': '2026-03-01'},
    ])
    rec, righe, manutenzioni, _ = _ultimo_import(app)

    assert rec['stato'] == 'pending'
    assert manutenzioni == 0
    assert righe == []
    assert 'non riconosciuto' in rec['errori_dettaglio']


def test_verifica_email_senza_esito_resta_in_coda(app, scenario, monkeypatch):
    """M14: l'auto-import via email aveva lo stesso default ottimistico
    dell'import manuale, e nessun operatore lo vedeva mai."""
    _lancia_monitor(app, scenario, monkeypatch, 'verifica_elettrica', [
        {'matricola': 'MAT-M1', 'data_verifica': '2026-03-01'},
    ])
    rec, righe, _, verifiche = _ultimo_import(app)

    assert verifiche == 0
    assert rec['stato'] == 'pending'
    assert len(righe) == 1
    assert righe[0]['stato'] == 'pending'
    assert 'Esito' in righe[0]['note_revisione']


def test_verbale_email_con_periodicita_assurda_resta_in_coda(app, scenario, monkeypatch):
    """M14: una periodicita' di 100000 giorni non deve produrre una scadenza
    nell'anno 2300 nello scadenzario."""
    _lancia_monitor(app, scenario, monkeypatch, 'verbale', [
        {'matricola': 'MAT-M1', 'tipo': 'preventiva',
         'data_intervento': '2026-03-01', 'periodicita_giorni': 100000},
    ])
    rec, righe, manutenzioni, _ = _ultimo_import(app)

    assert manutenzioni == 0
    assert rec['stato'] == 'pending'
    assert righe[0]['stato'] == 'pending'
    assert 'eriodicita' in righe[0]['note_revisione']
