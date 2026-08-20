"""Adattatori verso gli script che gia' fanno il lavoro.

Nessuna logica nuova: migrate.py, pulisci_uploads.py, toggle_modalita.py e
backup_service.py restano l'autorita' sulle rispettive operazioni, e i loro
test restano quelli che le proteggono. Questo modulo serve solo a dare loro
un'unica interfaccia e un unico modo di trovare il database.
"""
import os
import sqlite3
from datetime import datetime

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def radice():
    return RADICE


def percorso_database(percorso_esplicito=None):
    import migrate
    return migrate.load_db_path(percorso_esplicito)


def carica_config():
    import migrate
    return migrate.load_config()


def apri(percorso_db):
    if not os.path.exists(percorso_db):
        raise FileNotFoundError(percorso_db)
    conn = sqlite3.connect(percorso_db)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def backup_di_sicurezza(percorso_db, etichetta='manutenzione'):
    """Copia accanto al database, la rete dell'operazione che sta per partire.

    Sta accanto e non in backups/ di proposito: e' la rete di questa
    esecuzione, non un backup di esercizio, e non deve entrare nella
    rotazione che ne conserva solo quattro.

    Non e' una copia del file. Il database gira in WAL, e le transazioni
    piu' recenti vivono nel file -wal accanto: copiarlo con shutil ne
    produrrebbe uno indietro di tutto cio' che non e' ancora stato
    riversato - proprio i dati che l'operatore teme di perdere.
    sqlite3.backup() legge invece un'istantanea coerente, ed e' la stessa
    scelta di importa_installazione.py.
    """
    marca = datetime.now().strftime('%Y%m%d_%H%M%S')
    copia = f'{percorso_db}.bak_{etichetta}_{marca}'
    sorgente = sqlite3.connect(percorso_db)
    destinazione = sqlite3.connect(copia)
    try:
        with destinazione:
            sorgente.backup(destinazione)
    finally:
        destinazione.close()
        sorgente.close()
    return copia


def migrazioni_pendenti(conn):
    import migrate
    _versione, _uv, pendenti = migrate.analyze(conn)
    return pendenti


def applica_migrazioni(conn, percorso_db, config, pendenti):
    import migrate
    return migrate.apply_all(conn, percorso_db, config, pendenti)


def percorso_uploads(config):
    percorso = config.get('uploads_path', 'uploads')
    if not os.path.isabs(percorso):
        percorso = os.path.join(RADICE, percorso)
    return percorso


def percorso_backup(config):
    percorso = config.get('backups_path', 'backups')
    if not os.path.isabs(percorso):
        percorso = os.path.join(RADICE, percorso)
    return percorso


def orfani(conn, cartella_uploads):
    import pulisci_uploads
    referenziati = pulisci_uploads.percorsi_referenziati(conn)
    return pulisci_uploads.trova_orfani(cartella_uploads, referenziati)


def elimina_orfani(percorsi):
    import pulisci_uploads
    return pulisci_uploads.elimina_file(percorsi)


def modalita_attuale(config):
    import toggle_modalita
    return toggle_modalita.stato_attuale(config)


def imposta_modalita(single):
    import toggle_modalita
    config = toggle_modalita.leggi_config()
    config['single_struttura'] = bool(single)
    toggle_modalita.scrivi_config(config)
    return bool(single)


def crea_backup(percorso_db, cartella_backup):
    import backup_service
    return backup_service.create_backup(percorso_db, cartella_backup)


def elenca_backup(cartella_backup):
    import backup_service
    if not os.path.isdir(cartella_backup):
        return []
    return backup_service.list_backups(cartella_backup)


def ripristina_backup(file_backup, percorso_db):
    import backup_service
    return backup_service.restore_backup(file_backup, percorso_db)
