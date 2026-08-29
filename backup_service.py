"""
MedInventory - Backup Service
Creates SQLite database backups and manages retention policy.
"""

import os
import shutil
import glob
import uuid
import logging
from datetime import datetime

from schema_impianti import SCHEMA_VERSION_IMPIANTI

logger = logging.getLogger('medinventory.backup')


def create_backup(db_path, backups_path, retention=4):
    """
    Create a backup of the SQLite database.

    Args:
        db_path: Path to the SQLite database file
        backups_path: Directory to store backups
        retention: Number of backups to keep (oldest are deleted)

    Returns:
        dict with backup info (filename, size, path) or raises exception
    """
    os.makedirs(backups_path, exist_ok=True)

    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database non trovato: {db_path}")

    # Nome con timestamp piu' un token casuale: due backup avviati nello
    # stesso secondo (backup automatico e backup manuale, o due operatori)
    # avevano lo stesso nome e il secondo sovrascriveva il primo.
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_filename = f"medinventory_backup_{timestamp}_{uuid.uuid4().hex[:8]}.sqlite"
    backup_path = os.path.join(backups_path, backup_filename)

    # Use SQLite backup API via sqlite3
    import sqlite3
    source = sqlite3.connect(db_path)
    dest = sqlite3.connect(backup_path)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()

    # Get backup size
    backup_size = os.path.getsize(backup_path)

    logger.info(f"Backup creato: {backup_filename} ({backup_size / 1024:.1f} KB)")

    # Apply retention policy
    _apply_retention(backups_path, retention)

    return {
        'filename': backup_filename,
        'path': backup_path,
        'size': backup_size,
        'created_at': datetime.now().isoformat(),
    }


def _apply_retention(backups_path, retention):
    """Delete old backups exceeding the retention count."""
    if retention <= 0:
        return

    backups = sorted(
        glob.glob(os.path.join(backups_path, 'medinventory_backup_*.sqlite')),
        key=os.path.getmtime,
        reverse=True  # newest first
    )

    # Delete backups beyond retention limit
    for old_backup in backups[retention:]:
        try:
            os.remove(old_backup)
            logger.info(f"Backup eliminato (retention): {os.path.basename(old_backup)}")
        except OSError as e:
            logger.error(f"Errore eliminazione backup {old_backup}: {e}")


def list_backups(backups_path):
    """
    List all available backups.

    Returns:
        List of dicts with backup info, sorted newest first.
    """
    if not os.path.exists(backups_path):
        return []

    backups = []
    for filepath in glob.glob(os.path.join(backups_path, 'medinventory_backup_*.sqlite')):
        stat = os.stat(filepath)
        filename = os.path.basename(filepath)

        # Parse timestamp from filename
        try:
            ts_str = filename.replace('medinventory_backup_', '').replace('.sqlite', '')
            # Dopo il timestamp puo' esserci il token anticollisione: si legge
            # solo la parte data_ora, i backup piu' vecchi non ce l'hanno.
            ts_str = '_'.join(ts_str.split('_')[:2])
            created = datetime.strptime(ts_str, '%Y%m%d_%H%M%S')
            created_str = created.strftime('%d/%m/%Y %H:%M:%S')
        except ValueError:
            created_str = datetime.fromtimestamp(stat.st_mtime).strftime('%d/%m/%Y %H:%M:%S')

        backups.append({
            'filename': filename,
            'path': filepath,
            'size': stat.st_size,
            'size_display': _format_size(stat.st_size),
            'created_at': created_str,
            'mtime': stat.st_mtime,
        })

    backups.sort(key=lambda x: x['mtime'], reverse=True)
    return backups


# Tabelle senza le quali il file non e' un database di MedInventory: se manca
# una di queste, il ripristino sostituirebbe l'installazione con qualcos'altro.
TABELLE_RICHIESTE = ('strutture', 'utenti', 'divisioni', 'apparecchi',
                     'manutenzioni', 'verifiche', 'log_attivita')


def verifica_database(percorso):
    """Controlla che il file sia un database di MedInventory ripristinabile.

    Ritorna la lista dei problemi trovati, vuota se il file va bene. Il file
    viene aperto in sola lettura (URI `mode=ro`) e non viene mai modificato:
    e' quello che permette di validare un backup *prima* di sovrascrivere il
    database vivo, invece di scoprire il problema dopo.
    """
    import sqlite3

    problemi = []
    if not os.path.exists(percorso):
        return [f"File non trovato: {percorso}"]

    uri = 'file:' + percorso.replace('?', '%3f').replace('#', '%23') + '?mode=ro'
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        return [f"File non apribile come database SQLite: {e}"]

    try:
        try:
            esito = conn.execute('PRAGMA quick_check').fetchone()
        except sqlite3.DatabaseError as e:
            return [f"File non leggibile come database SQLite: {e}"]
        if not esito or esito[0] != 'ok':
            problemi.append(f"Controllo di integrita' fallito: {esito[0] if esito else 'nessun esito'}")

        presenti = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        mancanti = [t for t in TABELLE_RICHIESTE if t not in presenti]
        if mancanti:
            problemi.append("Non sembra un database MedInventory, mancano le "
                            "tabelle: " + ', '.join(mancanti))

        # Uno schema piu' recente di quello che questo codice conosce non si
        # puo' migrare all'indietro: meglio rifiutarlo che ritrovarsi con
        # tabelle e colonne che l'applicazione non sa gestire.
        try:
            versione = conn.execute('PRAGMA user_version').fetchone()[0]
        except sqlite3.DatabaseError:
            versione = 0
        if versione > SCHEMA_VERSION_IMPIANTI:
            problemi.append(
                f"Lo schema del backup (versione {versione}) e' piu' recente di "
                f"quello supportato da questa installazione ({SCHEMA_VERSION_IMPIANTI}): "
                "aggiorna il programma prima di ripristinarlo.")
    finally:
        conn.close()

    return problemi


def _rimuovi_laterali(db_path):
    """Toglie i file -wal e -shm accanto al database."""
    for estensione in ('-wal', '-shm'):
        laterale = db_path + estensione
        if os.path.exists(laterale):
            try:
                os.remove(laterale)
            except OSError:
                logger.warning("Impossibile rimuovere %s", laterale)


def restore_backup(backup_path, db_path):
    """Ripristina il database da un backup, validandolo prima e dopo.

    Il chiamante deve gia' avere fermato il traffico (vedi
    `manutenzione_globale.operazione_esclusiva`): qui si riscrive il file
    SQLite vivo, e finche' altri thread ci scrivono dentro il risultato non e'
    prevedibile.

    Ordine delle operazioni:

    1. il backup viene validato *prima* di toccare qualsiasi cosa;
    2. il database corrente viene copiato in `<db>.pre_restore`;
    3. il ripristino usa l'API `backup()` di SQLite (e non `os.replace`, che
       su Windows fallisce se qualche handle e' ancora aperto);
    4. il risultato viene rivalidato; se non passa, si torna indietro dalla
       copia di sicurezza.

    La copia `.pre_restore` **non** viene cancellata: resta finche' l'operatore
    non ha riavviato e verificato. Ritorna il suo percorso.
    """
    if not os.path.exists(backup_path):
        raise FileNotFoundError(f"Backup non trovato: {backup_path}")

    problemi = verifica_database(backup_path)
    if problemi:
        raise ValueError("Backup non ripristinabile. " + ' '.join(problemi))

    safety_path = db_path + '.pre_restore'
    if os.path.exists(db_path):
        shutil.copy2(db_path, safety_path)

    try:
        import sqlite3
        source = sqlite3.connect(backup_path)
        dest = sqlite3.connect(db_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()

        # I file laterali appartengono al database appena sostituito: lasciarli
        # significa lasciare in giro transazioni del database precedente.
        _rimuovi_laterali(db_path)

        problemi = verifica_database(db_path)
        if problemi:
            raise ValueError("Il database ripristinato non supera i controlli. "
                             + ' '.join(problemi))

        # Aprire il database, anche in sola lettura, ricrea -wal e -shm: la
        # verifica appena fatta ne ha lasciato una coppia vuota. Si tolgono,
        # cosi' il file resta solo come e' stato scritto.
        _rimuovi_laterali(db_path)

        logger.warning("Database ripristinato da %s. Copia di sicurezza: %s",
                       os.path.basename(backup_path), safety_path)
        return safety_path

    except Exception as e:
        # Si torna indietro dalla copia di sicurezza, che resta sul disco.
        if os.path.exists(safety_path):
            shutil.copy2(safety_path, db_path)
        logger.error("Ripristino fallito da %s: %s",
                     os.path.basename(backup_path), e)
        raise e


def delete_backup(backup_path):
    """Delete a specific backup file."""
    if os.path.exists(backup_path):
        os.remove(backup_path)
        logger.info(f"Backup eliminato: {os.path.basename(backup_path)}")


def _format_size(size_bytes):
    """Format file size in human-readable form."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
