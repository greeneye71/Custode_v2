"""pulisci_uploads.py decide per differenza: cio' che non compare fra i
percorsi che il database referenzia e' considerato orfano. Il rischio non e'
mancare un file davvero orfano - e' l'opposto, dichiarare orfano (e con
--elimina cancellare) un allegato ancora in uso. I test qui provano proprio
quello, non solo "funziona".

Gira su database temporanei costruiti a mano (schema.sql, come
test_struttura_service.py): non serve l'applicazione Flask, lo script non ne
dipende.
"""
import os
import sqlite3

import pytest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import sys
sys.path.insert(0, RADICE)

import pulisci_uploads
from struttura_service import COLONNE_ALLEGATI


# ---------------------------------------------------------------------------
# Rete di sicurezza: senza --target lo script guarda data/database.sqlite e
# uploads/ DENTRO la cartella dello script, cioe' il repository vero se lo si
# esegue da qui senza argomenti. Ogni test di questo file passa --target
# esplicito verso una cartella temporanea; questa fixture si accorge di
# qualunque test che, per errore, lo dimentichi e finisca per leggere o
# scrivere nell'installazione reale (stesso schema di sicurezza di
# tests/test_round_trip.py, che protegge importa_installazione.py per lo
# stesso motivo).
# ---------------------------------------------------------------------------

def _stato_repository_reale():
    db_reale = os.path.join(RADICE, 'data', 'database.sqlite')
    uploads_reale = os.path.join(RADICE, 'uploads')

    conteggio = None
    if os.path.exists(db_reale):
        con = sqlite3.connect(f'file:{db_reale}?mode=ro', uri=True)
        try:
            conteggio = con.execute("SELECT COUNT(*) FROM strutture").fetchone()[0]
        except sqlite3.Error:
            conteggio = 'illeggibile'
        finally:
            con.close()

    voci_uploads = None
    if os.path.isdir(uploads_reale):
        voci_uploads = sorted(
            os.path.relpath(os.path.join(cartella, nome), uploads_reale)
            for cartella, _sotto, file_presenti in os.walk(uploads_reale)
            for nome in file_presenti
        )
    return conteggio, voci_uploads


@pytest.fixture(autouse=True)
def _il_repository_reale_non_viene_toccato():
    prima = _stato_repository_reale()
    yield
    dopo = _stato_repository_reale()
    assert dopo == prima, (
        "il database o la cartella uploads REALI del repository sono cambiati "
        "durante un test di pulisci_uploads.py: manca --target esplicito.")


# ---------------------------------------------------------------------------
# Fixture: un'installazione temporanea (database + uploads), popolata a mano.
# ---------------------------------------------------------------------------

def _installazione_vuota(tmp_path):
    """Cartella con lo schema corrente e nessuna riga, uploads/ vuota."""
    (tmp_path / 'data').mkdir()
    (tmp_path / 'uploads').mkdir()
    db_path = tmp_path / 'data' / 'database.sqlite'
    con = sqlite3.connect(str(db_path))
    with open(os.path.join(RADICE, 'schema.sql'), encoding='utf-8') as f:
        con.executescript(f.read())
    con.commit()
    con.close()
    return tmp_path


def _popola_una_struttura_con_verbale(tmp_path):
    """Una struttura, un apparecchio, una manutenzione con un verbale VERO
    (referenziato), piu' un file orfano vero e proprio sotto uploads/."""
    db_path = tmp_path / 'data' / 'database.sqlite'
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON")
    s = con.execute("INSERT INTO strutture (nome, codice, attiva) VALUES ('Clinica A','A',1)").lastrowid
    d = con.execute("INSERT INTO divisioni (nome, codice, struttura_id) VALUES ('Div','D',?)", (s,)).lastrowid
    u = con.execute(
        "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id) "
        "VALUES ('a@a.it','x','N','C','admin',?)", (s,)).lastrowid
    ap = con.execute(
        "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato,created_by) "
        "VALUES (?,?,?,'M','MOD','funzionante',?)", (d, s, 'M-1', u)).lastrowid
    con.execute(
        "INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento,created_by,verbale_path) "
        "VALUES (?,'preventiva','2026-01-01',?,'strutture/1/verbali/vero.pdf')", (ap, u))
    con.commit()
    con.close()

    # Il verbale VERO, referenziato dalla riga appena inserita.
    (tmp_path / 'uploads' / 'strutture' / '1' / 'verbali').mkdir(parents=True)
    (tmp_path / 'uploads' / 'strutture' / '1' / 'verbali' / 'vero.pdf').write_bytes(b'verbale vero')

    # Un file davvero orfano, in nessuna riga.
    (tmp_path / 'uploads' / 'strutture' / '1' / 'verbali' / 'orfano.pdf').write_bytes(b'orfano')


# ---------------------------------------------------------------------------
# 1) La garanzia che interessa di piu': un allegato referenziato non e' MAI
#    elencato come orfano, e un orfano vero lo e'.
# ---------------------------------------------------------------------------

def test_un_allegato_referenziato_non_e_mai_elencato_come_orfano(tmp_path):
    _installazione_vuota(tmp_path)
    _popola_una_struttura_con_verbale(tmp_path)

    codice = pulisci_uploads.main(['--target', str(tmp_path)])

    assert codice == 0
    # Non basta guardare l'output: verifichiamo anche i file rimasti su disco,
    # perche' senza --elimina main() non cancella nulla - la prova vera che
    # "vero.pdf" non e' MAI stato considerato orfano va fatta sulla funzione
    # che decide, non solo sul comportamento di default.
    db = sqlite3.connect(str(tmp_path / 'data' / 'database.sqlite'))
    referenziati = pulisci_uploads.percorsi_referenziati(db)
    db.close()
    assert 'strutture\\1\\verbali\\vero.pdf' in referenziati or \
           os.path.normpath('strutture/1/verbali/vero.pdf').lower() in referenziati

    orfani, _byte = pulisci_uploads.trova_orfani(str(tmp_path / 'uploads'), referenziati)
    relativi = {os.path.relpath(p, str(tmp_path / 'uploads')) for p in orfani}
    assert os.path.join('strutture', '1', 'verbali', 'orfano.pdf') in relativi
    assert os.path.join('strutture', '1', 'verbali', 'vero.pdf') not in relativi


def test_prova_di_sensibilita_un_verbale_referenziato_sparirebbe_se_si_saltasse_la_colonna(tmp_path, monkeypatch):
    """Riproduce il difetto del piano: 'saltare' la colonna verbale_path
    (invece di fermarsi) dichiara orfano il verbale vero. Non tocchiamo il
    file sorgente per dimostrarlo: simuliamo lo stesso effetto rimuovendo la
    colonna da COLONNE_PERCORSO per la durata del test."""
    _installazione_vuota(tmp_path)
    _popola_una_struttura_con_verbale(tmp_path)

    difettoso = tuple(
        (t, c) for t, c in pulisci_uploads.COLONNE_PERCORSO if not (t == 'manutenzioni' and c == 'verbale_path')
    )
    monkeypatch.setattr(pulisci_uploads, 'COLONNE_PERCORSO', difettoso)

    db = sqlite3.connect(str(tmp_path / 'data' / 'database.sqlite'))
    referenziati = pulisci_uploads.percorsi_referenziati(db)
    db.close()
    orfani, _byte = pulisci_uploads.trova_orfani(str(tmp_path / 'uploads'), referenziati)
    relativi = {os.path.relpath(p, str(tmp_path / 'uploads')) for p in orfani}

    # Col difetto reintrodotto il verbale vero finisce fra gli orfani: e'
    # esattamente la falla che la funzione reale (senza monkeypatch) non ha.
    assert os.path.join('strutture', '1', 'verbali', 'vero.pdf') in relativi


# ---------------------------------------------------------------------------
# 2) Una colonna mancante dallo schema FERMA lo script (non lo salta).
# ---------------------------------------------------------------------------

def _installazione_con_schema_vecchio_senza_verbale_path(tmp_path):
    """Schema pre-migrazione: manutenzioni senza verbale_path. Riproduce
    esattamente il caso reale (vedi rapporto): il database di sviluppo del
    repository e' su questo schema."""
    (tmp_path / 'data').mkdir()
    (tmp_path / 'uploads').mkdir()
    db_path = tmp_path / 'data' / 'database.sqlite'
    con = sqlite3.connect(str(db_path))
    con.executescript("""
        CREATE TABLE strutture (id INTEGER PRIMARY KEY, nome TEXT, codice TEXT, attiva INTEGER);
        CREATE TABLE divisioni (id INTEGER PRIMARY KEY, nome TEXT, codice TEXT, struttura_id INTEGER);
        CREATE TABLE utenti (id INTEGER PRIMARY KEY, email TEXT, password_hash TEXT, nome TEXT,
            cognome TEXT, ruolo TEXT, struttura_id INTEGER);
        CREATE TABLE apparecchi (id INTEGER PRIMARY KEY, divisione_id INTEGER, struttura_id INTEGER,
            matricola TEXT, marca TEXT, modello TEXT, stato TEXT, created_by INTEGER, foto_path TEXT);
        CREATE TABLE manutenzioni (id INTEGER PRIMARY KEY, apparecchio_id INTEGER, tipo TEXT,
            data_intervento TEXT, created_by INTEGER);
        CREATE TABLE verifiche (id INTEGER PRIMARY KEY, apparecchio_id INTEGER, data_verifica TEXT,
            esito TEXT, created_by INTEGER, documento_path TEXT);
        CREATE TABLE documenti (id INTEGER PRIMARY KEY, apparecchio_id INTEGER, filepath TEXT);
        CREATE TABLE import_history (id INTEGER PRIMARY KEY, struttura_id INTEGER, filepath TEXT);
    """)
    con.commit()
    con.close()
    return tmp_path


def test_una_colonna_mancante_ferma_lo_script_invece_di_saltarla(tmp_path, capsys):
    _installazione_con_schema_vecchio_senza_verbale_path(tmp_path)
    # Un file che, con lo schema corrente, sarebbe un verbale referenziato:
    # qui la colonna che lo referenzierebbe non esiste proprio.
    (tmp_path / 'uploads' / 'verbali').mkdir(parents=True)
    (tmp_path / 'uploads' / 'verbali' / 'sarebbe_vero.pdf').write_bytes(b'verbale')

    codice = pulisci_uploads.main(['--target', str(tmp_path)])

    assert codice == 1
    output = capsys.readouterr().out
    assert 'manutenzioni.verbale_path' in output
    # Non deve MAI arrivare a stampare l'elenco (la riga "N file orfani, ... MB:"
    # che main() stampa solo dopo aver calcolato trova_orfani): si e' fermato
    # prima, nel calcolo dei referenziati.
    assert 'file orfani' not in output.lower()
    # E soprattutto: il file e' ancora li'.
    assert (tmp_path / 'uploads' / 'verbali' / 'sarebbe_vero.pdf').exists()


def test_percorsi_referenziati_solleva_sulla_colonna_mancante_senza_catturarla(tmp_path):
    _installazione_con_schema_vecchio_senza_verbale_path(tmp_path)
    con = sqlite3.connect(str(tmp_path / 'data' / 'database.sqlite'))
    with pytest.raises(pulisci_uploads.ColonnaMancante, match='manutenzioni.verbale_path'):
        pulisci_uploads.percorsi_referenziati(con)
    con.close()


def test_prova_di_sensibilita_il_continue_del_piano_avrebbe_nascosto_lerrore(tmp_path):
    """Riproduce l'except-continue del piano (righe 2014-2022): con quello,
    la colonna mancante non ferma nulla e il file che SAREBBE stato un
    verbale finisce fra gli orfani."""
    _installazione_con_schema_vecchio_senza_verbale_path(tmp_path)
    (tmp_path / 'uploads' / 'verbali').mkdir(parents=True)
    (tmp_path / 'uploads' / 'verbali' / 'sarebbe_vero.pdf').write_bytes(b'verbale')

    con = sqlite3.connect(str(tmp_path / 'data' / 'database.sqlite'))
    referenziati = set()
    for tabella, colonna in pulisci_uploads.COLONNE_PERCORSO:
        try:
            righe = con.execute(
                f"SELECT {colonna} FROM {tabella} WHERE {colonna} IS NOT NULL AND {colonna} != ''")
        except sqlite3.OperationalError:
            continue  # il difetto del piano
        for (valore,) in righe:
            if valore:
                referenziati.add(os.path.normpath(valore.replace('/', os.sep)).lower())
    con.close()

    orfani, _byte = pulisci_uploads.trova_orfani(str(tmp_path / 'uploads'), referenziati)
    relativi = {os.path.relpath(p, str(tmp_path / 'uploads')) for p in orfani}
    assert os.path.join('verbali', 'sarebbe_vero.pdf') in relativi  # la falla, riprodotta


# ---------------------------------------------------------------------------
# 3) COLONNE_PERCORSO deriva da COLONNE_ALLEGATI, non e' un elenco separato.
# ---------------------------------------------------------------------------

def test_colonne_percorso_deriva_da_colonne_allegati():
    atteso = tuple((t, c) for t, c, _dove in COLONNE_ALLEGATI)
    assert pulisci_uploads.COLONNE_PERCORSO == atteso
    # E per costruzione include le colonne vere (non 'file_path' inventato
    # dal piano per documenti/import_history).
    assert ('documenti', 'filepath') in pulisci_uploads.COLONNE_PERCORSO
    assert ('import_history', 'filepath') in pulisci_uploads.COLONNE_PERCORSO
    assert ('documenti', 'file_path') not in pulisci_uploads.COLONNE_PERCORSO
    assert ('import_history', 'file_path') not in pulisci_uploads.COLONNE_PERCORSO


# ---------------------------------------------------------------------------
# 4) Il database predefinito e' data/database.sqlite, e non viene creato se
#    manca.
# ---------------------------------------------------------------------------

def test_database_assente_ferma_lo_script_e_non_lo_crea(tmp_path, capsys):
    # La cartella 'data' esiste (come in un'installazione vera), ma il file
    # database.sqlite no: e' esattamente il caso in cui sqlite3.connect lo
    # creerebbe vuoto se non venisse verificato prima.
    (tmp_path / 'data').mkdir()
    (tmp_path / 'uploads').mkdir()

    codice = pulisci_uploads.main(['--target', str(tmp_path)])

    assert codice == 1
    assert not (tmp_path / 'data' / 'database.sqlite').exists()
    output = capsys.readouterr().out
    assert 'Database non trovato' in output


def test_prova_di_sensibilita_senza_la_verifica_sqlite_crea_il_file_vuoto(tmp_path):
    """Dimostra perche' serve il controllo: sqlite3.connect su un percorso
    inesistente lo crea vuoto, e da un database vuoto NESSUN percorso risulta
    referenziato - esattamente il difetto del piano (config.get con un nome
    di file sbagliato produce lo stesso effetto)."""
    percorso_inesistente = str(tmp_path / 'data' / 'non_esiste.sqlite')
    assert not os.path.exists(percorso_inesistente)
    os.makedirs(tmp_path / 'data')
    con = sqlite3.connect(percorso_inesistente)  # lo crea, vuoto
    con.close()
    assert os.path.exists(percorso_inesistente)  # la falla, riprodotta


def test_database_path_predefinito_e_quello_vero(tmp_path):
    _installazione_vuota(tmp_path)
    # Nessun config.json/config.local.json in tmp_path: si usa il predefinito.
    config = pulisci_uploads.carica_config(str(tmp_path))
    db_path = os.path.join(str(tmp_path), config.get('database_path', pulisci_uploads.DB_PATH_PREDEFINITO))
    assert os.path.normpath(db_path) == os.path.normpath(os.path.join(str(tmp_path), 'data', 'database.sqlite'))
    assert os.path.isfile(db_path)


# ---------------------------------------------------------------------------
# 5) Il freno su --elimina: insieme referenziato vuoto ma file presenti.
# ---------------------------------------------------------------------------

def test_elimina_si_rifiuta_se_il_database_non_referenzia_nulla_ma_ci_sono_file(tmp_path, capsys):
    _installazione_vuota(tmp_path)  # schema corretto, ZERO righe
    (tmp_path / 'uploads' / 'foto').mkdir(parents=True)
    (tmp_path / 'uploads' / 'foto' / 'x.jpg').write_bytes(b'x')

    codice = pulisci_uploads.main(['--target', str(tmp_path), '--elimina', '--yes'])

    assert codice == 1
    assert (tmp_path / 'uploads' / 'foto' / 'x.jpg').exists()  # NON cancellato
    output = capsys.readouterr().out
    assert 'rifiuto' in output.lower() or 'mi rifiuto' in output.lower()


def test_prova_di_sensibilita_senza_il_freno_elimina_lintera_cartella(tmp_path):
    """Senza il freno, un database vuoto (o un percorso sbagliato che ne apre
    uno vuoto) farebbe considerare orfano OGNI file: e' il difetto 3 del
    piano (database_path sbagliato) portato alle sue conseguenze su
    --elimina."""
    _installazione_vuota(tmp_path)
    (tmp_path / 'uploads' / 'foto').mkdir(parents=True)
    (tmp_path / 'uploads' / 'foto' / 'x.jpg').write_bytes(b'x')

    orfani, _byte = pulisci_uploads.trova_orfani(str(tmp_path / 'uploads'), set())
    assert len(orfani) == 1  # 'x.jpg' e' fra i "da cancellare": la falla, senza il freno a monte


# ---------------------------------------------------------------------------
# 6) Percorso felice: --elimina rimuove solo l'orfano, l'allegato vero resta.
# ---------------------------------------------------------------------------

def test_elimina_cancella_solo_i_file_non_referenziati(tmp_path, capsys):
    _installazione_vuota(tmp_path)
    _popola_una_struttura_con_verbale(tmp_path)

    codice = pulisci_uploads.main(['--target', str(tmp_path), '--elimina', '--yes'])

    assert codice == 0
    assert not (tmp_path / 'uploads' / 'strutture' / '1' / 'verbali' / 'orfano.pdf').exists()
    assert (tmp_path / 'uploads' / 'strutture' / '1' / 'verbali' / 'vero.pdf').exists()
    output = capsys.readouterr().out
    assert '1 file rimossi' in output


def test_senza_elimina_non_cancella_nulla(tmp_path):
    _installazione_vuota(tmp_path)
    _popola_una_struttura_con_verbale(tmp_path)

    codice = pulisci_uploads.main(['--target', str(tmp_path)])

    assert codice == 0
    assert (tmp_path / 'uploads' / 'strutture' / '1' / 'verbali' / 'orfano.pdf').exists()
    assert (tmp_path / 'uploads' / 'strutture' / '1' / 'verbali' / 'vero.pdf').exists()


def test_elimina_chiede_conferma_e_rispetta_il_rifiuto(tmp_path, monkeypatch):
    _installazione_vuota(tmp_path)
    _popola_una_struttura_con_verbale(tmp_path)

    monkeypatch.setattr('builtins.input', lambda *_: 'n')
    codice = pulisci_uploads.main(['--target', str(tmp_path), '--elimina'])  # niente --yes

    assert codice == 0
    # Rifiutata la conferma: l'orfano e' ancora li'.
    assert (tmp_path / 'uploads' / 'strutture' / '1' / 'verbali' / 'orfano.pdf').exists()


def test_nessun_file_orfano_non_richiede_conferma(tmp_path, capsys):
    _installazione_vuota(tmp_path)
    # Nessun file sotto uploads/: nulla da elencare o cancellare.
    codice = pulisci_uploads.main(['--target', str(tmp_path), '--elimina', '--yes'])
    assert codice == 0
    assert 'Nessun file orfano.' in capsys.readouterr().out
