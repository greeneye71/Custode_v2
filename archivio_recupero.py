"""
MedInventory - Archivio di ripristino completo.

Il backup ordinario (`backup_service.py`) contiene soltanto il file SQLite: e'
la copia che serve per tornare indietro di qualche giorno sulla stessa
installazione, e da sola non basta a rimettere in piedi il programma su una
macchina nuova. Mancano gli allegati (`uploads/`, logo delle strutture
compreso), la configurazione locale e la chiave con cui sono cifrate le
password di posta: dopo un ripristino del solo database, molti record puntano
a file che non esistono piu' o che appartengono a un'altra epoca.

Questo modulo produce l'archivio completo: database consistente, allegati,
configurazione, manifest con le versioni e le impronte SHA-256 di ogni file, e
le istruzioni di ripristino. `verifica_archivio()` rilegge l'archivio,
ricontrolla le impronte ed estrae il database per validarlo davvero: e' la
prova di ripristino periodica, non la semplice constatazione che il file
esiste.

Il modulo non importa Flask: si puo' usare dagli script di manutenzione.

ATTENZIONE: l'archivio contiene la configurazione locale, quindi la chiave di
cifratura e le chiavi API. Va custodito come una credenziale.
"""

import os
import json
import glob
import uuid
import shutil
import hashlib
import logging
import sqlite3
import tempfile
import zipfile
from datetime import datetime

logger = logging.getLogger('medinventory.archivio')

PREFISSO = 'medinventory_recupero_'
ESTENSIONE = '.zip'

# Nomi usati dentro l'archivio: chi ripristina a mano deve ritrovarli uguali
# alle istruzioni.
NOME_DATABASE = 'database/database.sqlite'
NOME_MANIFEST = 'MANIFEST.json'
NOME_ISTRUZIONI = 'ISTRUZIONI_RIPRISTINO.txt'
CARTELLA_UPLOADS = 'uploads/'
CARTELLA_CONFIG = 'config/'

# Conteggi riportati nel manifest: servono a riconoscere a colpo d'occhio
# l'epoca dei dati senza aprire il database.
TABELLE_CONTEGGIO = ('strutture', 'divisioni', 'utenti', 'apparecchi',
                     'manutenzioni', 'verifiche', 'impianti')

ISTRUZIONI = """RIPRISTINO DI MEDINVENTORY DA ARCHIVIO COMPLETO
================================================

Questo archivio contiene tutto quello che serve a rimettere in servizio
l'installazione su una macchina nuova. Ripristinarlo e' un'operazione manuale:
il programma non lo fa da solo, perche' sovrascriverebbe anche allegati e
configurazione dell'installazione di destinazione.

Prima di iniziare
-----------------
1. Ferma il programma (chiudi il launcher o il servizio Waitress).
2. Metti da parte la cartella attuale dell'installazione: se qualcosa va
   storto e' l'unico modo per tornare indietro.
3. Installa la stessa versione di MedInventory indicata in MANIFEST.json
   ("versione_applicazione"). Una versione piu' vecchia non sa leggere lo
   schema del database contenuto qui.

Ripristino
----------
4. Estrai l'archivio in una cartella di lavoro.
5. Copia database/database.sqlite su data/database.sqlite
   dell'installazione, cancellando gli eventuali file
   database.sqlite-wal e database.sqlite-shm rimasti accanto.
6. Copia il contenuto di uploads/ dentro la cartella uploads/
   dell'installazione, al posto di quella esistente.
7. Copia i file di config/ nella cartella del programma. Rivedi
   config.local.json: percorsi, porta e impostazioni di posta si riferiscono
   alla macchina di origine.
8. Riavvia il programma e controlla: numero di strutture e apparecchi
   (confrontali con "conteggi" nel manifest), apertura di un allegato,
   invio di una email di prova.

Verifica delle impronte
-----------------------
MANIFEST.json elenca ogni file con la sua impronta SHA-256. La stessa verifica
la fa il programma, dalla pagina Backup, con il pulsante "Verifica"
sull'archivio.

Contenuto sensibile
-------------------
config/config.local.json contiene la chiave di cifratura delle password di
posta e le chiavi API dei servizi AI. Conserva l'archivio come conserveresti
una password, e cancellalo dalle postazioni dove non serve piu'.
"""


def _impronta(percorso):
    """SHA-256 di un file, letto a blocchi: gli allegati possono essere grossi."""
    digest = hashlib.sha256()
    with open(percorso, 'rb') as f:
        for blocco in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(blocco)
    return digest.hexdigest()


def _conteggi(db_path):
    """Righe delle tabelle principali, per riconoscere l'epoca dei dati."""
    conteggi = {}
    conn = sqlite3.connect(db_path)
    try:
        presenti = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        for tabella in TABELLE_CONTEGGIO:
            if tabella in presenti:
                conteggi[tabella] = conn.execute(
                    f"SELECT COUNT(*) FROM {tabella}").fetchone()[0]
        versione_schema = conn.execute('PRAGMA user_version').fetchone()[0]
    finally:
        conn.close()
    return conteggi, versione_schema


def crea_archivio(db_path, uploads_path, config_paths, archivi_path,
                  versione_app='sconosciuta', includi_uploads=True,
                  retention=0):
    """Crea l'archivio di ripristino completo e ne ritorna le informazioni.

    `config_paths` e' la lista dei file di configurazione da includere: quelli
    che non esistono vengono saltati senza errore.

    Il database viene copiato con l'API `backup()` di SQLite, quindi e'
    consistente anche se il programma sta lavorando. Gli allegati, invece,
    vengono letti mentre l'applicazione gira: un file caricato durante la copia
    puo' finirci dentro o no, ma nessuno viene troncato.
    """
    os.makedirs(archivi_path, exist_ok=True)

    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database non trovato: {db_path}")

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    nome = f"{PREFISSO}{timestamp}_{uuid.uuid4().hex[:8]}{ESTENSIONE}"
    percorso = os.path.join(archivi_path, nome)
    # Si scrive su un nome temporaneo: un archivio interrotto a meta' non deve
    # comparire nell'elenco come se fosse ripristinabile.
    parziale = percorso + '.parziale'

    cartella_lavoro = tempfile.mkdtemp(prefix='medinv_archivio_')
    try:
        istantanea = os.path.join(cartella_lavoro, 'database.sqlite')
        source = sqlite3.connect(db_path)
        dest = sqlite3.connect(istantanea)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()

        conteggi, versione_schema = _conteggi(istantanea)
        elenco = []
        allegati = 0

        with zipfile.ZipFile(parziale, 'w', zipfile.ZIP_DEFLATED) as zip_file:

            def aggiungi(origine, nome_interno):
                zip_file.write(origine, nome_interno)
                elenco.append({
                    'nome': nome_interno,
                    'dimensione': os.path.getsize(origine),
                    'sha256': _impronta(origine),
                })

            aggiungi(istantanea, NOME_DATABASE)

            if includi_uploads and uploads_path and os.path.isdir(uploads_path):
                for radice, _cartelle, file_presenti in os.walk(uploads_path):
                    for nome_file in sorted(file_presenti):
                        completo = os.path.join(radice, nome_file)
                        relativo = os.path.relpath(completo, uploads_path)
                        aggiungi(completo,
                                 CARTELLA_UPLOADS + relativo.replace(os.sep, '/'))
                        allegati += 1

            # Due voci possono puntare allo stesso file (o avere lo stesso
            # nome): nell'archivio deve restarne una sola.
            nomi_config = set()
            for config_path in config_paths or []:
                if not config_path or not os.path.exists(config_path):
                    continue
                nome = CARTELLA_CONFIG + os.path.basename(config_path)
                if nome in nomi_config:
                    continue
                nomi_config.add(nome)
                aggiungi(config_path, nome)

            manifest = {
                'prodotto': 'MedInventory',
                'versione_applicazione': versione_app,
                'versione_schema': versione_schema,
                'creato_il': datetime.now().isoformat(timespec='seconds'),
                'database': NOME_DATABASE,
                'conteggi': conteggi,
                'allegati_inclusi': bool(includi_uploads),
                'numero_allegati': allegati,
                'contiene_segreti': True,
                'avviso': ("L'archivio contiene la configurazione locale: chiave "
                           "di cifratura delle password di posta e chiavi API. "
                           "Custodirlo come una credenziale."),
                'file': elenco,
            }
            zip_file.writestr(NOME_MANIFEST,
                              json.dumps(manifest, indent=2, ensure_ascii=False))
            zip_file.writestr(NOME_ISTRUZIONI, ISTRUZIONI)

        os.replace(parziale, percorso)
    finally:
        shutil.rmtree(cartella_lavoro, ignore_errors=True)
        if os.path.exists(parziale):
            os.remove(parziale)

    dimensione = os.path.getsize(percorso)
    logger.info("Archivio di ripristino creato: %s (%.1f KB, %d allegati)",
                nome, dimensione / 1024, allegati)

    _applica_retention(archivi_path, retention)

    return {
        'filename': nome,
        'path': percorso,
        'size': dimensione,
        'size_display': _formato_dimensione(dimensione),
        'manifest': manifest,
    }


def _applica_retention(archivi_path, retention):
    """Tiene solo gli ultimi `retention` archivi. 0 o meno: nessuna pulizia."""
    if retention <= 0:
        return
    archivi = sorted(
        glob.glob(os.path.join(archivi_path, PREFISSO + '*' + ESTENSIONE)),
        key=os.path.getmtime, reverse=True)
    for vecchio in archivi[retention:]:
        try:
            os.remove(vecchio)
            logger.info("Archivio eliminato (retention): %s",
                        os.path.basename(vecchio))
        except OSError as e:
            logger.error("Errore eliminazione archivio %s: %s", vecchio, e)


def elenca_archivi(archivi_path):
    """Archivi disponibili, dal piu' recente."""
    if not os.path.exists(archivi_path):
        return []

    archivi = []
    for percorso in glob.glob(os.path.join(archivi_path,
                                           PREFISSO + '*' + ESTENSIONE)):
        stat = os.stat(percorso)
        archivi.append({
            'filename': os.path.basename(percorso),
            'path': percorso,
            'size': stat.st_size,
            'size_display': _formato_dimensione(stat.st_size),
            'created_at': datetime.fromtimestamp(stat.st_mtime).strftime(
                '%d/%m/%Y %H:%M:%S'),
            'mtime': stat.st_mtime,
        })

    archivi.sort(key=lambda a: a['mtime'], reverse=True)
    return archivi


def leggi_manifest(percorso):
    """Manifest di un archivio, o None se non c'e' o non si legge."""
    try:
        with zipfile.ZipFile(percorso) as zip_file:
            return json.loads(zip_file.read(NOME_MANIFEST).decode('utf-8'))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None


def verifica_archivio(percorso):
    """Ricontrolla un archivio e ritorna la lista dei problemi (vuota se va bene).

    Non si limita a guardare il manifest: ricalcola le impronte di ogni file ed
    estrae il database per validarlo con gli stessi controlli del ripristino.
    E' la prova di ripristino che l'audit chiede di fare periodicamente, invece
    di fidarsi del fatto che il file esiste.
    """
    from backup_service import verifica_database

    if not os.path.exists(percorso):
        return [f"Archivio non trovato: {percorso}"]

    problemi = []
    try:
        with zipfile.ZipFile(percorso) as zip_file:
            nomi = set(zip_file.namelist())
            if NOME_MANIFEST not in nomi:
                return ["Manca il manifest: l'archivio non e' stato prodotto da "
                        "MedInventory o e' incompleto."]
            try:
                manifest = json.loads(zip_file.read(NOME_MANIFEST).decode('utf-8'))
            except (ValueError, UnicodeDecodeError) as e:
                return [f"Manifest illeggibile: {e}"]

            if manifest.get('prodotto') != 'MedInventory':
                problemi.append("Il manifest non dichiara un archivio MedInventory.")

            for voce in manifest.get('file', []):
                nome = voce.get('nome')
                if nome not in nomi:
                    problemi.append(f"File mancante nell'archivio: {nome}")
                    continue
                digest = hashlib.sha256()
                with zip_file.open(nome) as f:
                    for blocco in iter(lambda: f.read(1024 * 1024), b''):
                        digest.update(blocco)
                if digest.hexdigest() != voce.get('sha256'):
                    problemi.append(f"Impronta diversa da quella dichiarata: {nome}")

            nome_db = manifest.get('database', NOME_DATABASE)
            if nome_db not in nomi:
                problemi.append("L'archivio non contiene il database.")
            else:
                cartella = tempfile.mkdtemp(prefix='medinv_verifica_')
                try:
                    estratto = os.path.join(cartella, 'database.sqlite')
                    with zip_file.open(nome_db) as sorgente, \
                            open(estratto, 'wb') as uscita:
                        shutil.copyfileobj(sorgente, uscita)
                    problemi.extend(verifica_database(estratto))
                finally:
                    shutil.rmtree(cartella, ignore_errors=True)
    except zipfile.BadZipFile as e:
        return [f"Archivio non leggibile: {e}"]

    return problemi


def elimina_archivio(percorso):
    """Cancella un archivio."""
    if os.path.exists(percorso):
        os.remove(percorso)
        logger.info("Archivio eliminato: %s", os.path.basename(percorso))


def _formato_dimensione(byte):
    if byte < 1024:
        return f"{byte} B"
    if byte < 1024 * 1024:
        return f"{byte / 1024:.1f} KB"
    return f"{byte / (1024 * 1024):.1f} MB"
