"""M12 - il backup non bastava per un disaster recovery (audit del 28/08/2026).

Il rilievo: il "backup" conteneva soltanto il file SQLite. Niente allegati,
niente configurazione locale, niente logo, niente chiave di cifratura, nessun
manifest. Dopo un ripristino su una macchina nuova i record restavano ma i loro
allegati no, e nessuno aveva mai *provato* un ripristino: si verificava soltanto
che il file fosse stato creato.

Qui si verifica quello che chiude il rilievo:

- archivio_recupero.crea_archivio(): database in copia consistente, uploads/,
  configurazione, MANIFEST.json con impronta SHA-256 di ogni file e istruzioni;
- verifica_archivio(): la prova di ripristino, che rilegge le impronte e valida
  il database estratto, e che si accorge di un archivio manomesso o estraneo;
- l'archivio e' scritto in modo atomico e rispetta la retention;
- le rotte stanno dietro @operazione_globale_required, rifiutano nomi file
  fuori dalla cartella e lasciano traccia in log_attivita;
- la pagina backup dice a chiare lettere cosa il backup del database NON
  contiene.
"""
import json
import os
import sqlite3
import zipfile

import pytest
from werkzeug.security import generate_password_hash

import archivio_recupero


RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _installazione(tmp_path, allegati=('verbali/v1.pdf', 'foto/a.jpg')):
    """Una finta installazione: database di schema, uploads, config locale."""
    dati = tmp_path / 'data'
    dati.mkdir(exist_ok=True)
    db = dati / 'database.sqlite'
    conn = sqlite3.connect(str(db))
    with open(os.path.join(RADICE, 'schema.sql'), encoding='utf-8') as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

    uploads = tmp_path / 'uploads'
    for relativo in allegati:
        percorso = uploads / relativo
        percorso.parent.mkdir(parents=True, exist_ok=True)
        percorso.write_bytes(b'contenuto di ' + relativo.encode())

    config = tmp_path / 'config.local.json'
    config.write_text(json.dumps({'encryption_key': 'segretissima'}),
                      encoding='utf-8')

    return {
        'db': str(db),
        'uploads': str(uploads),
        'config': [str(config)],
        'archivi': str(tmp_path / 'backups'),
    }


def _crea(tmp_path, **extra):
    inst = _installazione(tmp_path)
    return inst, archivio_recupero.crea_archivio(
        db_path=inst['db'], uploads_path=inst['uploads'],
        config_paths=inst['config'], archivi_path=inst['archivi'],
        versione_app='2.8.4', **extra)


# ---------------------------------------------------------------------------
# Contenuto dell'archivio
# ---------------------------------------------------------------------------

def test_l_archivio_contiene_database_allegati_configurazione_e_istruzioni(tmp_path):
    """Il rilievo elencava esattamente cosa mancava al backup: ora c'e' tutto
    dentro un file solo."""
    _, esito = _crea(tmp_path)

    with zipfile.ZipFile(esito['path']) as z:
        nomi = set(z.namelist())

    assert archivio_recupero.NOME_DATABASE in nomi
    assert archivio_recupero.NOME_MANIFEST in nomi
    assert archivio_recupero.NOME_ISTRUZIONI in nomi
    assert 'uploads/verbali/v1.pdf' in nomi
    assert 'uploads/foto/a.jpg' in nomi
    assert 'config/config.local.json' in nomi


def test_il_manifest_dichiara_versione_conteggi_e_impronta_di_ogni_file(tmp_path):
    """Senza manifest non si sa da quale epoca del database vengono gli
    allegati, ne' con quale versione del programma si ripristina."""
    _, esito = _crea(tmp_path)
    manifest = esito['manifest']

    assert manifest['versione_applicazione'] == '2.8.4'
    assert 'apparecchi' in manifest['conteggi']
    assert manifest['numero_allegati'] == 2

    for voce in manifest['file']:
        assert len(voce['sha256']) == 64
        assert voce['dimensione'] >= 0

    nomi = {v['nome'] for v in manifest['file']}
    assert archivio_recupero.NOME_DATABASE in nomi
    assert 'uploads/verbali/v1.pdf' in nomi


def test_l_archivio_avverte_che_contiene_segreti(tmp_path):
    """Dentro c'e' config.local.json, cioe' la chiave di cifratura e le chiavi
    API: chi lo copia su una chiavetta deve saperlo."""
    _, esito = _crea(tmp_path)

    assert esito['manifest']['contiene_segreti'] is True
    with zipfile.ZipFile(esito['path']) as z:
        istruzioni = z.read(archivio_recupero.NOME_ISTRUZIONI).decode('utf-8')
    assert 'chiave di cifratura' in istruzioni.lower()


def test_il_database_nell_archivio_e_ripristinabile(tmp_path):
    """Non basta che il file ci sia: deve superare gli stessi controlli di un
    backup prima del ripristino."""
    from backup_service import verifica_database

    _, esito = _crea(tmp_path)
    with zipfile.ZipFile(esito['path']) as z:
        z.extract(archivio_recupero.NOME_DATABASE, str(tmp_path / 'estratto'))

    estratto = tmp_path / 'estratto' / archivio_recupero.NOME_DATABASE
    assert verifica_database(str(estratto)) == []


def test_senza_allegati_l_archivio_lo_dichiara(tmp_path):
    """L'operatore puo' escludere gli uploads (archivio piu' leggero), ma il
    manifest deve dirlo: e' la differenza fra un ripristino completo e uno no."""
    _, esito = _crea(tmp_path, includi_uploads=False)

    assert esito['manifest']['allegati_inclusi'] is False
    assert esito['manifest']['numero_allegati'] == 0
    with zipfile.ZipFile(esito['path']) as z:
        assert not [n for n in z.namelist() if n.startswith('uploads/')]


def test_non_resta_nessun_file_parziale(tmp_path):
    """L'archivio si scrive con un nome temporaneo e si rinomina alla fine:
    un archivio incompleto non deve mai comparire nell'elenco."""
    inst, esito = _crea(tmp_path)

    residui = [n for n in os.listdir(inst['archivi']) if n.endswith('.parziale')]
    assert residui == []
    assert os.path.basename(esito['path']) in os.listdir(inst['archivi'])


def test_la_retention_tiene_solo_gli_ultimi_archivi(tmp_path):
    """Gli archivi sono grossi (contengono gli allegati): senza retention
    riempiono il disco."""
    inst = _installazione(tmp_path)
    for _ in range(3):
        archivio_recupero.crea_archivio(
            db_path=inst['db'], uploads_path=inst['uploads'],
            config_paths=inst['config'], archivi_path=inst['archivi'],
            versione_app='2.8.4', retention=2)

    assert len(archivio_recupero.elenca_archivi(inst['archivi'])) == 2


# ---------------------------------------------------------------------------
# La prova di ripristino
# ---------------------------------------------------------------------------

def test_la_verifica_di_un_archivio_intatto_non_trova_problemi(tmp_path):
    _, esito = _crea(tmp_path)
    assert archivio_recupero.verifica_archivio(esito['path']) == []


def test_la_verifica_si_accorge_di_un_allegato_manomesso(tmp_path):
    """L'impronta serve a questo: un file corrotto dalla copia su rete o dal
    supporto non deve passare per buono."""
    _, esito = _crea(tmp_path)

    manomesso = str(tmp_path / 'manomesso.zip')
    with zipfile.ZipFile(esito['path']) as origine, \
            zipfile.ZipFile(manomesso, 'w') as destinazione:
        for voce in origine.infolist():
            dati = origine.read(voce.filename)
            if voce.filename == 'uploads/verbali/v1.pdf':
                dati = b'altro contenuto'
            destinazione.writestr(voce.filename, dati)

    problemi = archivio_recupero.verifica_archivio(manomesso)
    assert any('v1.pdf' in p for p in problemi)


def test_la_verifica_si_accorge_di_un_file_mancante(tmp_path):
    _, esito = _crea(tmp_path)

    monco = str(tmp_path / 'monco.zip')
    with zipfile.ZipFile(esito['path']) as origine, \
            zipfile.ZipFile(monco, 'w') as destinazione:
        for voce in origine.infolist():
            if voce.filename == archivio_recupero.NOME_DATABASE:
                continue
            destinazione.writestr(voce.filename, origine.read(voce.filename))

    assert archivio_recupero.verifica_archivio(monco)


def test_un_zip_estraneo_viene_rifiutato(tmp_path):
    """Un archivio qualsiasi non e' un archivio di ripristino: va detto prima
    del giorno in cui serve, non durante l'emergenza."""
    estraneo = str(tmp_path / 'estraneo.zip')
    with zipfile.ZipFile(estraneo, 'w') as z:
        z.writestr('lettera.txt', 'niente a che vedere')

    assert archivio_recupero.verifica_archivio(estraneo)


def test_un_file_che_non_e_uno_zip_viene_rifiutato(tmp_path):
    finto = tmp_path / 'finto.zip'
    finto.write_bytes(b'non sono uno zip')
    assert archivio_recupero.verifica_archivio(str(finto))


# ---------------------------------------------------------------------------
# Le rotte
# ---------------------------------------------------------------------------

def entra(client, email):
    client.post('/login', data={'email': email, 'password': 'Passw0rd!'})


@pytest.fixture
def utenti(app, tmp_path, monkeypatch):
    """Un superadmin e un admin di struttura, con la configurazione locale
    dirottata su un file finto: i test non devono impacchettare le chiavi vere
    dello sviluppatore."""
    import app as modulo_app
    from models import execute

    finta = tmp_path / 'config.local.json'
    finta.write_text(json.dumps({'encryption_key': 'segretissima'}),
                     encoding='utf-8')
    monkeypatch.setattr(modulo_app, 'LOCAL_CONFIG_PATH', str(finta))
    monkeypatch.setattr(modulo_app, 'CONFIG_PATH', str(finta))

    with app.app_context():
        pw = generate_password_hash('Passw0rd!')
        prima = execute("INSERT INTO strutture (nome,codice,attiva) "
                        "VALUES ('Clinica G','GG',1)").lastrowid
        execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica H','HH',1)")
        execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('anna@g.it',?,'Anna','G','admin',?,0)", (pw, prima))
        execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('super@g.it',?,'Super','G','superadmin',NULL,0)", (pw,))
    return {'struttura': prima}


def test_il_superadmin_crea_verifica_e_scarica_l_archivio(client, app, utenti):
    """Il giro completo dalla pagina: creazione, prova di ripristino, download."""
    entra(client, 'super@g.it')

    risposta = client.post('/admin/backup/archivio/crea', follow_redirects=True)
    assert risposta.status_code == 200

    archivi = archivio_recupero.elenca_archivi(app.config['BACKUPS_PATH'])
    assert len(archivi) == 1
    nome = archivi[0]['filename']

    verifica = client.post(f'/admin/backup/archivio/{nome}/verifica', follow_redirects=True)
    assert 'completo' in verifica.get_data(as_text=True)

    scarico = client.get(f'/admin/backup/archivio/{nome}/scarica')
    assert scarico.status_code == 200


def test_l_admin_di_struttura_non_tocca_gli_archivi(client, utenti):
    """L'archivio contiene i dati di tutti i tenant e la configurazione del
    deployment: e' un'operazione globale, non di struttura."""
    entra(client, 'anna@g.it')

    assert client.post('/admin/backup/archivio/crea').status_code == 302
    assert client.get('/admin/backup/archivio/x.zip/scarica').status_code == 302
    assert client.post('/admin/backup/archivio/x.zip/verifica').status_code == 302


def test_senza_login_le_rotte_dell_archivio_non_rispondono(client, utenti):
    assert client.post('/admin/backup/archivio/crea').status_code == 302
    assert client.get('/admin/backup/archivio/x.zip/scarica').status_code == 302


@pytest.mark.parametrize('nome', [
    '../../config.local.json',
    'medinventory_recupero_../fuori.zip',
    'medinventory_recupero_..\fuori.zip',
    'altro.zip',
    'medinventory_recupero_x.txt',
])
def test_un_nome_di_archivio_fuori_dalla_cartella_e_rifiutato(nome):
    """Il nome arriva dall'URL: se passasse, /backup/archivio/<nome>/scarica
    diventerebbe una lettura di file arbitrari."""
    from admin import _nome_archivio_valido
    assert _nome_archivio_valido(nome) is False


@pytest.mark.parametrize('nome', [
    '../../data/database.sqlite',
    'medinventory_backup_../fuori.sqlite',
    'altro.sqlite',
])
def test_un_nome_di_backup_fuori_dalla_cartella_e_rifiutato(nome):
    from admin import _nome_backup_valido
    assert _nome_backup_valido(nome) is False


def test_la_rotta_rifiuta_un_nome_non_valido(client, utenti):
    """Stessa difesa vista dalla rotta: nessun file servito, solo il messaggio."""
    entra(client, 'super@g.it')

    risposta = client.get('/admin/backup/archivio/altro.zip/scarica',
                          follow_redirects=True)
    assert 'Nome file non valido' in risposta.get_data(as_text=True)


def test_la_creazione_dell_archivio_finisce_nel_log(client, app, utenti):
    """Portare via una copia completa dei dati e' un'operazione da tracciare."""
    from models import query_one

    entra(client, 'super@g.it')
    client.post('/admin/backup/archivio/crea', follow_redirects=True)

    with app.app_context():
        riga = query_one("SELECT azione, struttura_id FROM log_attivita "
                         "WHERE azione = 'archivio_creazione'")
    assert riga is not None
    assert riga['struttura_id'] is None


# ---------------------------------------------------------------------------
# La prova di ripristino di un backup normale
# ---------------------------------------------------------------------------

def test_il_backup_normale_si_puo_provare_senza_ripristinarlo(client, app, utenti):
    """L'audit chiedeva prove periodiche di restore: il pulsante Verifica apre
    il backup in sola lettura e lo valida, senza toccare il database in uso."""
    entra(client, 'super@g.it')
    client.post('/admin/backup/crea', follow_redirects=True)

    from backup_service import list_backups
    backup = list_backups(app.config['BACKUPS_PATH'])[0]

    risposta = client.post(f"/admin/backup/{backup['filename']}/verifica",
                           follow_redirects=True)
    assert 'integro e ripristinabile' in risposta.get_data(as_text=True)


def test_un_backup_corrotto_non_passa_la_prova(client, app, utenti):
    """Meglio scoprirlo con il pulsante che durante l'emergenza."""
    entra(client, 'super@g.it')

    os.makedirs(app.config['BACKUPS_PATH'], exist_ok=True)
    nome = 'medinventory_backup_20260101_000000_deadbeef.sqlite'
    with open(os.path.join(app.config['BACKUPS_PATH'], nome), 'wb') as f:
        f.write(b'non sono un database')

    risposta = client.post(f'/admin/backup/{nome}/verifica', follow_redirects=True)
    assert 'NON e' in risposta.get_data(as_text=True)


def test_la_pagina_backup_dice_cosa_il_backup_non_contiene(client, utenti):
    """Il rilievo chiedeva di non chiamare "backup" quello che e' il solo
    database: la pagina ora lo scrive."""
    entra(client, 'super@g.it')
    testo = client.get('/admin/backup').get_data(as_text=True)

    assert 'soltanto il file SQLite' in testo
    assert 'Archivio di ripristino completo' in testo
