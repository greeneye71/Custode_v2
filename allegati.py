"""Validazione centralizzata degli allegati caricati (M05).

Fino alla 2.8.2 ogni rotta guardava solo l'estensione del nome del file:
un eseguibile rinominato in .pdf veniva salvato, elencato e riservito come
documento, e un .xlsx poteva essere un archivio che in decompressione occupa
gigabyte. Il controllo del contenuto sta qui, in un modulo senza Flask, e le
rotte lo chiamano PRIMA di scrivere su disco: un file rifiutato non deve
lasciare tracce.

Non e' un antivirus. Riconosce il tipo reale dai primi byte, rifiuta cio' che
non corrisponde all'estensione dichiarata e ferma gli archivi che si espandono
in modo sproporzionato. Un PDF malevolo ma sintatticamente PDF passa: per
quello serve un motore di scansione, che resta un'aggiunta possibile qui
dentro senza toccare le rotte.

Il contenimento dei percorsi in lettura (realpath sotto UPLOADS_PATH) e' l'altra
meta' dello stesso rilievo, ed e' gia' nei blueprint: apparecchi, manutenzioni,
verifiche e impianti passano tutti da un helper che lo verifica.
"""
import zipfile

# Byte letti dall'inizio del file per riconoscerne il tipo. Le firme note
# stanno nei primi 12, il resto serve al controllo "sembra testo".
_TESTA = 4096

# Estensione -> prefissi ammessi. Chi non compare qui (nessuna firma stabile)
# passa il controllo di contenuto: meglio nessun controllo che uno sbagliato.
FIRME = {
    'pdf': (b'%PDF-',),
    'jpg': (b'\xff\xd8\xff',),
    'jpeg': (b'\xff\xd8\xff',),
    'png': (b'\x89PNG\r\n\x1a\n',),
    'gif': (b'GIF87a', b'GIF89a'),
    # docx/xlsx sono archivi zip: PK\x03\x04 e' l'unico inizio valido per un
    # archivio non vuoto (\x05\x06 e' lo zip vuoto, che non e' un documento).
    'docx': (b'PK\x03\x04',),
    'xlsx': (b'PK\x03\x04',),
    # doc/xls storici: contenitore OLE2.
    'doc': (b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1',),
    'xls': (b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1',),
}

# webp non ha un prefisso unico: RIFF, quattro byte di lunghezza, poi WEBP.
ESTENSIONI_RIFF = {'webp'}

# csv e txt non hanno firma: si controlla solo che non siano binari.
ESTENSIONI_TESTUALI = {'csv', 'txt'}

# Estensioni da aprire come archivio per il controllo di decompressione.
ESTENSIONI_ARCHIVIO = {'docx', 'xlsx'}

# Un xlsx e' XML: comprime molto, e un rapporto alto non e' di per se' sospetto.
# Si guarda il rapporto solo sopra una dimensione decompressa che nessun foglio
# di lavoro normale raggiunge.
RAPPORTO_MAX = 200
SOGLIA_RAPPORTO = 8 * 1024 * 1024
ESPANSIONE_MAX = 512 * 1024 * 1024

MESSAGGIO_CONTENUTO = ("Il contenuto del file non corrisponde all'estensione "
                       "dichiarata: caricamento rifiutato.")
MESSAGGIO_ARCHIVIO = ("Il file e' un archivio che in decompressione supera i "
                      "limiti previsti: caricamento rifiutato.")
MESSAGGIO_VUOTO = 'Il file e\' vuoto.'


def estensione(nome):
    """Estensione in minuscolo, senza punto. Stringa vuota se non ce n'e'."""
    if not nome or '.' not in nome:
        return ''
    return nome.rsplit('.', 1)[-1].lower()


def _flusso(file_obj):
    """Il flusso leggibile di un FileStorage, o l'oggetto stesso nei test."""
    return getattr(file_obj, 'stream', file_obj)


def _testa(file_obj):
    """Primi byte del file, lasciando il flusso riavvolto per chi salva dopo.

    Un flusso non riavvolgibile qui non esiste: werkzeug tiene l'upload in
    memoria o in un file temporaneo, entrambi seekable. Se un chiamante
    passasse altro, l'eccezione e' preferibile a un salvataggio non verificato.
    """
    flusso = _flusso(file_obj)
    flusso.seek(0)
    testa = flusso.read(_TESTA)
    flusso.seek(0)
    return testa


def sembra_testo(testa):
    """Vero se i primi byte non sono quelli di un binario.

    Il byte nullo e' il segnale piu' affidabile; in piu' si rifiuta cio' che
    inizia con una firma binaria nota, perche' un .csv che e' in realta' un
    eseguibile o un PDF non e' un errore di battitura dell'utente.
    """
    if b'\x00' in testa:
        return False
    binarie = [f for firme in FIRME.values() for f in firme]
    binarie += [b'MZ', b'\x7fELF', b'PK\x03\x04', b'RIFF', b'\x1f\x8b']
    return not any(testa.startswith(f) for f in binarie)


def contenuto_coerente(testa, ext):
    """Vero se i primi byte sono compatibili con l'estensione dichiarata."""
    if not testa:
        return False
    if ext in ESTENSIONI_TESTUALI:
        return sembra_testo(testa)
    if ext in ESTENSIONI_RIFF:
        return testa[:4] == b'RIFF' and testa[8:12] == b'WEBP'
    firme = FIRME.get(ext)
    if firme is None:
        return True
    return any(testa.startswith(f) for f in firme)


def archivio_sproporzionato(file_obj):
    """Vero se l'archivio si espande oltre i limiti, o non e' leggibile.

    Il rapporto si guarda per voce, non sul totale: una bomba classica e' un
    singolo membro minuscolo che esplode. Il totale copre invece l'archivio
    fatto di mille membri onesti ma enormi.
    """
    flusso = _flusso(file_obj)
    flusso.seek(0)
    try:
        with zipfile.ZipFile(flusso) as archivio:
            totale = 0
            for voce in archivio.infolist():
                totale += voce.file_size
                if totale > ESPANSIONE_MAX:
                    return True
                if (voce.compress_size and voce.file_size > SOGLIA_RAPPORTO
                        and voce.file_size / voce.compress_size > RAPPORTO_MAX):
                    return True
    except (zipfile.BadZipFile, OSError, ValueError):
        # Inizia per PK ma non e' un archivio apribile: non e' un documento
        # Office, e openpyxl o python-docx ci sbatterebbero contro piu' tardi.
        return True
    finally:
        flusso.seek(0)
    return False


def verifica(file_obj, estensioni_ammesse,
             messaggio_estensione='Formato file non supportato.'):
    """Controlla un upload. Restituisce None se va bene, altrimenti il
    messaggio (in italiano, gia' pronto per flash) del motivo del rifiuto.

    Il messaggio dell'estensione lo passa il chiamante, perche' ogni rotta ha
    il suo elenco di formati da citare all'utente.
    """
    if file_obj is None or not getattr(file_obj, 'filename', ''):
        return 'Nessun file selezionato.'
    ext = estensione(file_obj.filename)
    if ext not in estensioni_ammesse:
        return messaggio_estensione
    testa = _testa(file_obj)
    if not testa:
        return MESSAGGIO_VUOTO
    if not contenuto_coerente(testa, ext):
        return MESSAGGIO_CONTENUTO
    if ext in ESTENSIONI_ARCHIVIO and archivio_sproporzionato(file_obj):
        return MESSAGGIO_ARCHIVIO
    return None
