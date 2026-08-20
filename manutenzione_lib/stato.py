"""Fotografia di un'installazione: percorsi, versioni, conteggi.

Raccoglie e basta. Non giudica (quello e' diagnosi.py) e non stampa (quello
e' tui.py). Il risultato e' un dizionario, cosi' '--json' puo' emetterlo
identico e la TUI formattarlo.

Nessun segreto entra nel dizionario: delle chiavi API e delle password si
riporta solo se ci sono. Il dizionario finisce nei log e negli incolla di
chi chiede assistenza.
"""
import os
import sqlite3

TABELLE_DATI = ('apparecchi', 'manutenzioni', 'verifiche', 'documenti', 'accessori')
PROVIDER_CHIAVI = {
    'anthropic': 'default_anthropic_api_key',
    'gemini': 'default_gemini_api_key',
    'openai': 'default_openai_api_key',
}


def tabella_esiste(conn, nome):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (nome,)).fetchone() is not None


def colonna_esiste(conn, tabella, colonna):
    """Le installazioni vecchie hanno tabelle giuste e colonne mancanti.

    Chi interroga una colonna aggiunta da una migrazione deve chiederlo prima:
    su un database a schema v1.x la query esplode, e un controllo che esplode
    e' un controllo che non dice nulla.
    """
    if not tabella_esiste(conn, tabella):
        return False
    return colonna in {r[1] for r in conn.execute(f'PRAGMA table_info({tabella})')}


def _non_disponibile(motivo):
    return {'disponibile': False, 'motivo': motivo}


def _sezione_database(conn):
    percorso = None
    for _seq, nome, file in conn.execute('PRAGMA database_list'):
        if nome == 'main':
            percorso = file
    return {
        'disponibile': True,
        'percorso': percorso,
        'dimensione_byte': (os.path.getsize(percorso)
                            if percorso and os.path.exists(percorso) else 0),
        'journal_mode': conn.execute('PRAGMA journal_mode').fetchone()[0],
        'integrity_check': conn.execute('PRAGMA integrity_check').fetchone()[0],
        'foreign_key_check': [tuple(r) for r in
                              conn.execute('PRAGMA foreign_key_check').fetchall()],
    }


def _sezione_schema(conn):
    import migrate
    versione, uv = migrate.describe_version(conn)
    pendenti = [m.id for m in migrate.MIGRATIONS if not m.applied(conn)]
    return {'disponibile': True, 'versione': versione,
            'user_version': uv, 'pendenti': pendenti}


def _sezione_modalita(conn, config):
    strutture = []
    if tabella_esiste(conn, 'strutture'):
        strutture = [{'id': r[0], 'nome': r[1]} for r in
                     conn.execute('SELECT id, nome FROM strutture ORDER BY id')]
    return {'disponibile': True,
            'single_struttura': bool(config.get('single_struttura', False)),
            'strutture': len(strutture),
            'elenco': strutture}


def _sezione_utenti(conn):
    if not tabella_esiste(conn, 'utenti'):
        return _non_disponibile("la tabella 'utenti' non esiste")
    colonne = {r[1] for r in conn.execute('PRAGMA table_info(utenti)')}
    cancellati = 0
    if 'eliminato_il' in colonne:
        cancellati = conn.execute(
            'SELECT COUNT(*) FROM utenti WHERE eliminato_il IS NOT NULL').fetchone()[0]
    per_ruolo = {r[0]: r[1] for r in conn.execute(
        'SELECT ruolo, COUNT(*) FROM utenti WHERE attivo = 1 GROUP BY ruolo')}
    return {
        'disponibile': True,
        'totale': conn.execute('SELECT COUNT(*) FROM utenti').fetchone()[0],
        'totale_attivi': conn.execute(
            'SELECT COUNT(*) FROM utenti WHERE attivo = 1').fetchone()[0],
        'disattivati': conn.execute(
            'SELECT COUNT(*) FROM utenti WHERE attivo = 0').fetchone()[0],
        'cancellati': cancellati,
        'per_ruolo': per_ruolo,
    }


def _sezione_dati(conn):
    conteggi = {}
    for tabella in TABELLE_DATI:
        if not tabella_esiste(conn, tabella):
            return _non_disponibile(f"la tabella '{tabella}' non esiste")
        conteggi[tabella] = conn.execute(
            f'SELECT COUNT(*) FROM {tabella}').fetchone()[0]
    conteggi['disponibile'] = True
    return conteggi


def _sezione_uploads(conn, config, radice):
    percorso = config.get('uploads_path', 'uploads')
    if not os.path.isabs(percorso):
        percorso = os.path.join(radice, percorso)
    if not os.path.isdir(percorso):
        return {'disponibile': False, 'percorso': percorso,
                'motivo': 'la cartella non esiste'}

    file_presenti, byte_totali = 0, 0
    for cartella, _sotto, nomi in os.walk(percorso):
        for nome in nomi:
            file_presenti += 1
            try:
                byte_totali += os.path.getsize(os.path.join(cartella, nome))
            except OSError:
                pass

    sezione = {'disponibile': True, 'percorso': percorso,
               'file': file_presenti, 'byte': byte_totali}
    try:
        import pulisci_uploads
        referenziati = pulisci_uploads.percorsi_referenziati(conn)
        orfani, byte_orfani = pulisci_uploads.trova_orfani(percorso, referenziati)
        sezione['orfani'] = len(orfani)
        sezione['byte_orfani'] = byte_orfani
        sezione['mancanti'] = sorted(
            r for r in referenziati
            if not os.path.exists(os.path.join(percorso, r)))
    except Exception as e:
        # ColonnaMancante su schema vecchio: il conteggio dei file resta
        # valido, l'analisi degli orfani no. Dichiararla impossibile e'
        # l'unica risposta onesta - vedi il docstring di pulisci_uploads.
        sezione['orfani'] = None
        sezione['motivo_orfani'] = str(e)
    return sezione


def _sezione_ai(config):
    return {
        'disponibile': True,
        'provider': config.get('default_ai_provider'),
        'chiavi': {nome: bool(config.get(chiave))
                   for nome, chiave in PROVIDER_CHIAVI.items()},
        'base_url_locale': config.get('default_ai_local_base_url'),
        'modello_import': config.get('default_ai_import_model'),
    }


def _sezione_posta(config):
    return {
        'disponibile': True,
        'smtp_host': config.get('smtp_host'),
        'smtp_port': config.get('smtp_port'),
        'smtp_password_presente': bool(config.get('smtp_password')),
        'imap_host': config.get('imap_host'),
        'imap_password_presente': bool(config.get('imap_password')),
    }


def _sezione_backup(config, radice):
    percorso = config.get('backups_path', 'backups')
    if not os.path.isabs(percorso):
        percorso = os.path.join(radice, percorso)
    if not os.path.isdir(percorso):
        return {'disponibile': False, 'percorso': percorso,
                'motivo': 'la cartella non esiste'}
    try:
        import backup_service
        elenco = backup_service.list_backups(percorso)
    except Exception as e:
        return {'disponibile': False, 'percorso': percorso, 'motivo': str(e)}
    return {'disponibile': True, 'percorso': percorso, 'numero': len(elenco),
            'ultimo': elenco[0]['filename'] if elenco else None}


def raccogli(conn, config, radice):
    """Fotografia completa. Ogni sezione fallisce per conto suo.

    Una sezione che non si puo' calcolare vale {'disponibile': False,
    'motivo': ...} e la raccolta prosegue: un database a schema vecchio deve
    restare ispezionabile, e' il motivo per cui esiste questo strumento.
    """
    fotografia = {}
    sezioni = (
        ('database', lambda: _sezione_database(conn)),
        ('schema',   lambda: _sezione_schema(conn)),
        ('modalita', lambda: _sezione_modalita(conn, config)),
        ('utenti',   lambda: _sezione_utenti(conn)),
        ('dati',     lambda: _sezione_dati(conn)),
        ('uploads',  lambda: _sezione_uploads(conn, config, radice)),
        ('ai',       lambda: _sezione_ai(config)),
        ('posta',    lambda: _sezione_posta(config)),
        ('backup',   lambda: _sezione_backup(config, radice)),
    )
    for nome, calcola in sezioni:
        try:
            fotografia[nome] = calcola()
        except (sqlite3.Error, OSError, ImportError) as e:
            fotografia[nome] = _non_disponibile(str(e))
    return fotografia
