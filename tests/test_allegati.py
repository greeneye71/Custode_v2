"""M05: upload validati quasi solo per estensione.

Il nome del file lo sceglie chi carica: `payload.exe` rinominato `referto.pdf`
passava ogni controllo, finiva in uploads/ ed era poi scaricabile dagli utenti
della struttura. Allo stesso modo un .xlsx poteva essere un archivio costruito
per esplodere in decompressione, e openpyxl lo apriva.

allegati.py e' il controllo unico: firma del contenuto contro estensione
dichiarata, rifiuto dei file vuoti, limite di espansione per gli archivi
Office. Questi test inchiodano il modulo e il fatto che le rotte lo usino
davvero — e, soprattutto, che un rifiuto non lasci il file su disco.
"""
import io
import os
import zipfile

import pytest
from werkzeug.datastructures import FileStorage
from werkzeug.security import generate_password_hash

import allegati

PDF = b'%PDF-1.7\n%\xe2\xe3\xcf\xd3\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32
JPG = b'\xff\xd8\xff\xe0' + b'\x00' * 32
GIF = b'GIF89a' + b'\x00' * 16
WEBP = b'RIFF' + b'\x20\x00\x00\x00' + b'WEBP' + b'\x00' * 16
EXE = b'MZ\x90\x00' + b'\x00' * 32


def _upload(contenuto, nome):
    return FileStorage(stream=io.BytesIO(contenuto), filename=nome)


def _xlsx(voci):
    """Un vero zip, con dentro cio' che gli si passa."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archivio:
        for nome, dati in voci:
            archivio.writestr(nome, dati)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Il modulo
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('contenuto,nome,ammesse', [
    (PDF, 'referto.pdf', {'pdf'}),
    (PNG, 'logo.png', {'png'}),
    (JPG, 'foto.jpg', {'jpg', 'jpeg'}),
    (JPG, 'foto.JPEG', {'jpg', 'jpeg'}),
    (GIF, 'anim.gif', {'gif'}),
    (WEBP, 'foto.webp', {'webp'}),
    (b'matricola;modello\nA1;Alfa\n', 'dati.csv', {'csv'}),
])
def test_un_file_autentico_passa(contenuto, nome, ammesse):
    assert allegati.verifica(_upload(contenuto, nome), ammesse) is None


@pytest.mark.parametrize('contenuto,nome', [
    (EXE, 'referto.pdf'),
    (EXE, 'foto.png'),
    (PDF, 'foglio.xlsx'),
    (PNG, 'referto.pdf'),
    (EXE, 'dati.csv'),
    (b'RIFF\x20\x00\x00\x00AVI ', 'foto.webp'),
])
def test_il_contenuto_che_smentisce_l_estensione_viene_rifiutato(contenuto, nome):
    """E' il difetto in una riga: l'estensione la sceglie chi carica."""
    esito = allegati.verifica(_upload(contenuto, nome), {'pdf', 'png', 'xlsx',
                                                         'csv', 'webp'})
    assert esito == allegati.MESSAGGIO_CONTENUTO


def test_l_estensione_non_ammessa_usa_il_messaggio_del_chiamante():
    """Ogni rotta elenca all'utente i formati che accetta: il modulo non deve
    appiattire quei messaggi su uno solo."""
    esito = allegati.verifica(_upload(PDF, 'referto.pdf'), {'png'},
                              'Usa PNG o JPG.')
    assert esito == 'Usa PNG o JPG.'


def test_il_file_vuoto_viene_rifiutato():
    assert allegati.verifica(_upload(b'', 'referto.pdf'),
                             {'pdf'}) == allegati.MESSAGGIO_VUOTO


def test_nessun_file_e_un_rifiuto_non_un_errore():
    assert allegati.verifica(None, {'pdf'})
    assert allegati.verifica(_upload(PDF, ''), {'pdf'})


def test_il_flusso_resta_riavvolto_dopo_la_verifica():
    """Se la verifica consumasse il flusso, la rotta salverebbe un file vuoto:
    il controllo avrebbe rotto proprio cio' che doveva proteggere."""
    upload = _upload(PDF, 'referto.pdf')
    assert allegati.verifica(upload, {'pdf'}) is None
    assert upload.stream.read() == PDF


def test_un_xlsx_normale_passa():
    contenuto = _xlsx([('[Content_Types].xml', b'<Types/>'),
                       ('xl/worksheets/sheet1.xml', b'<worksheet/>' * 100)])
    assert allegati.verifica(_upload(contenuto, 'inventario.xlsx'),
                             {'xlsx'}) is None


def test_un_archivio_che_esplode_in_decompressione_viene_rifiutato():
    """Il rapporto si guarda per voce: una bomba e' un membro minuscolo che
    si espande, non un archivio grande."""
    contenuto = _xlsx([('[Content_Types].xml', b'<Types/>'),
                       ('xl/bomba.xml', b'\x00'.replace(b'\x00', b'A') * (
                           allegati.SOGLIA_RAPPORTO + 1))])
    assert allegati.verifica(_upload(contenuto, 'inventario.xlsx'),
                             {'xlsx'}) == allegati.MESSAGGIO_ARCHIVIO


def test_un_finto_zip_viene_rifiutato():
    """Firma PK ma archivio illeggibile: openpyxl ci sbatterebbe contro piu'
    tardi, con il file gia' salvato e la riga gia' scritta."""
    esito = allegati.verifica(_upload(b'PK\x03\x04' + b'spazzatura' * 10,
                                      'inventario.xlsx'), {'xlsx'})
    assert esito == allegati.MESSAGGIO_ARCHIVIO


def test_estensione():
    assert allegati.estensione('a/b/Referto.PDF') == 'pdf'
    assert allegati.estensione('senza_punto') == ''
    assert allegati.estensione('') == ''


# ---------------------------------------------------------------------------
# Le rotte lo usano davvero
# ---------------------------------------------------------------------------

@pytest.fixture
def contesto(app):
    """Struttura, divisione, apparecchio, impianto e un admin che li vede."""
    from models import execute
    with app.app_context():
        sid = execute("INSERT INTO strutture (nome,codice,attiva)"
                      " VALUES ('Clinica M','ICM',1)").lastrowid
        did = execute("INSERT INTO divisioni (nome,codice,struttura_id)"
                      " VALUES ('Div M','DVM',?)", (sid,)).lastrowid
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,"
                "struttura_id,primo_accesso) VALUES ('adminm@i.it',?,'A','M',"
                "'admin',?,0)", (generate_password_hash('Passw0rd!'), sid))
        aid = execute("INSERT INTO apparecchi (struttura_id,divisione_id,"
                      "marca,modello,matricola,stato) VALUES (?,?,'ACME',"
                      "'Alfa','M1','funzionante')", (sid, did)).lastrowid
        iid = execute("INSERT INTO impianti (struttura_id,divisione_id,nome,"
                      "tipo,stato) VALUES (?,?,'Gruppo','elettrico',"
                      "'attivo')", (sid, did)).lastrowid
    return {'struttura': sid, 'divisione': did, 'apparecchio': aid,
            'impianto': iid}


@pytest.fixture
def loggato(client, contesto):
    client.post('/login', data={'email': 'adminm@i.it',
                                'password': 'Passw0rd!'})
    return contesto


def _file_caricati(app):
    radice = app.config['UPLOADS_PATH']
    return [f for _, _, files in os.walk(radice) for f in files]


@pytest.mark.parametrize('rotta,campo,nome', [
    ('/apparecchi/{apparecchio}/foto', 'foto', 'foto.png'),
    ('/apparecchi/{apparecchio}/documento', 'documento', 'manuale.pdf'),
    ('/impianti/{impianto}/documenti', 'documento', 'dico.pdf'),
])
def test_la_rotta_rifiuta_un_eseguibile_travestito(client, app, loggato,
                                                   rotta, campo, nome):
    """Il rifiuto deve arrivare prima del salvataggio: un file scartato che
    resta in uploads/ e' esattamente il rischio del rilievo."""
    risposta = client.post(rotta.format(**loggato), data={
        campo: (io.BytesIO(EXE), nome),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert risposta.status_code == 200
    assert _file_caricati(app) == []


def test_l_import_rifiuta_un_csv_binario(client, app, loggato):
    from models import query_one
    app.config['APP_CONFIG']['ai_provider'] = 'anthropic'
    app.config['APP_CONFIG']['anthropic_api_key'] = 'chiave-finta-di-test'
    client.post('/import/analizza', data={
        'file': (io.BytesIO(EXE), 'inventario.csv'),
        'divisione_id': str(loggato['divisione']),
    }, content_type='multipart/form-data', follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM import_history")['n'] == 0
    assert _file_caricati(app) == []


def test_il_logo_rifiuta_un_png_che_non_e_un_png(client, app, loggato):
    client.post(f"/strutture/{loggato['struttura']}/logo", data={
        'logo': (io.BytesIO(b'non sono un png'), 'logo.png'),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert _file_caricati(app) == []


def test_un_documento_autentico_viene_invece_accettato(client, app, loggato):
    """Controllo positivo: senza di esso i test qui sopra passerebbero anche
    se le rotte rifiutassero tutto."""
    client.post(f"/impianti/{loggato['impianto']}/documenti", data={
        'documento': (io.BytesIO(PDF), 'dico.pdf'),
        'tipo': 'dico',
    }, content_type='multipart/form-data', follow_redirects=True)
    assert _file_caricati(app) != []
