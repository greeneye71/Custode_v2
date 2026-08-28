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


def restore_backup(backup_path, db_path):
    """
    Restore a database from a backup file.

    Args:
        backup_path: Path to the backup file
        db_path: Path to the current database (will be overwritten)
    """
    if not os.path.exists(backup_path):
        raise FileNotFoundError(f"Backup non trovato: {backup_path}")

    # Create a safety copy of the current database before restoring
    safety_path = db_path + '.pre_restore'
    if os.path.exists(db_path):
        shutil.copy2(db_path, safety_path)

    try:
        # Restore using SQLite backup API
        import sqlite3
        source = sqlite3.connect(backup_path)
        dest = sqlite3.connect(db_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()

        logger.info(f"Database ripristinato da: {os.path.basename(backup_path)}")

        # Remove safety copy on success
        if os.path.exists(safety_path):
            os.remove(safety_path)

    except Exception as e:
        # Restore safety copy if something went wrong
        if os.path.exists(safety_path):
            shutil.copy2(safety_path, db_path)
            os.remove(safety_path)
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
