"""Esporta una struttura (o l'archivio prodotto dalla sua cancellazione) e
reimportala: il giro completo che rende vera la promessa scritta nella
pagina di conferma della cancellazione ("l'archivio si reimporta"). Finche'
nessuno lo prova con un import vero, e' un file che nessuno ha mai riaperto.

E' il test piu' lento della suite: lancia importa_installazione.py come
sottoprocesso reale (non solo la libreria), una volta per giro.
"""
import os
import sqlite3
import subprocess
import sys

import pytest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPORTATORE = os.path.join(RADICE, 'importa_installazione.py')


# ---------------------------------------------------------------------------
# Rete di sicurezza: il database e la cartella uploads REALI del repository
# non devono mai essere toccati da questo file.
#
# Il piano originale del Task 8 chiamava importa_installazione.py con
# cwd=destinazione, credendo che bastasse a dirigere l'import li'. Non e'
# cosi': --target ha default BASE_DIR (importa_installazione.py:33 e :1155),
# cioe' la cartella DELLO SCRIPT, sempre la stessa indipendentemente dalla
# cwd del sottoprocesso. Un test che dimenticasse --target scriverebbe nel
# database di sviluppo del repository a ogni esecuzione della suite - non
# c'e' un cwd che eviti questo, l'unico modo e' passare --target esplicito.
#
# Non si puo' dimostrare questo difetto eseguendolo davvero (nemmeno da una
# cwd finta): eseguirlo senza --target scriverebbe per davvero nel database
# reale, cioe' il disastro che questa nota descrive. La prova e' quindi
# duplice: lettura del codice (fatta, vedi sopra) e questa fixture, che gira
# PRIMA e DOPO ogni test del file e si accorge di qualunque scrittura,
# qualunque test l'abbia causata - anche uno aggiunto in futuro che
# dimenticasse --target.
# ---------------------------------------------------------------------------

def _stato_repository_reale():
    db_reale = os.path.join(RADICE, 'data', 'database.sqlite')
    uploads_reale = os.path.join(RADICE, 'uploads')

    # Su un clone appena fatto, o in integrazione continua, il database di
    # sviluppo non esiste: senza questa guardia tutti i test del file
    # andrebbero in errore nel setup, per un motivo che non c'entra nulla con
    # cio' che provano. Nessun database significa nulla da proteggere.
    conteggio = None
    if os.path.exists(db_reale):
        con = sqlite3.connect(f'file:{db_reale}?mode=ro', uri=True)
        try:
            conteggio = con.execute("SELECT COUNT(*) FROM strutture").fetchone()[0]
        except sqlite3.Error:
            conteggio = 'illeggibile'
        finally:
            con.close()

    # os.walk e non os.listdir: un import copia gli allegati DENTRO le
    # sottocartelle (uploads/<tipo>/ quando il target e' in modalita' single,
    # uploads/strutture/<id>/<tipo>/ in multi), che qui esistono gia'. Un
    # confronto sul solo primo livello non vedrebbe nessuna di quelle
    # scritture, cioe' proprio quelle che questa rete esiste per intercettare.
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
        "durante questo test: un import ha scritto li' invece che nella "
        "destinazione di prova. E' il difetto GRAVE segnalato nel brief del "
        "Task 8 - controllare che ogni chiamata a importa_installazione.py "
        "passi --target esplicito.")


# ---------------------------------------------------------------------------
# Costruzione di installazioni di prova
# ---------------------------------------------------------------------------

def _installazione_vuota(app, tmp_path, nome='destinazione'):
    """Una installazione di destinazione con lo schema corrente, vuota: e'
    li' che il giro reimporta.

    'Schema corrente' significa schema.sql PIU' le migrazioni incrementali
    di models.apply_schema_updates(), non schema.sql da solo:
    apparecchi.soggetto_verifica, per esempio, esiste solo dopo la
    migrazione (models.py, non schema.sql), e importa_installazione.py la
    valorizza incondizionatamente a ogni INSERT in apparecchi. Un database
    di destinazione costruito con un semplice executescript(schema.sql), come
    fanno i test di struttura_service.py per i loro database SORGENTE (che
    l'importatore legge per introspezione e non pretende colonne precise),
    qui fa fallire l'INSERT con 'no such column: soggetto_verifica' -
    scoperto eseguendo davvero il sottoprocesso, non a tavolino.

    La fixture 'app' di conftest.py fa gia' girare create_app(), che a sua
    volta chiama init_db() + apply_schema_updates() sul proprio database
    temporaneo (vedi app.py:249-252): e' esattamente lo stesso principio del
    test preesistente test_ogni_colonna_di_percorso_dello_schema_e_registrata
    in test_struttura_service.py, che per lo stesso motivo gira 'sul database
    che l'applicazione costruisce davvero all'avvio'. Da li' si prende una
    copia (sqlite3.backup(), coerente qualunque sia lo stato del WAL) sotto il
    nome che importa_installazione.py cerca di default senza --db esplicito:
    'data/database.sqlite' - il nome sbagliato ('data/medinventory.db') era
    il secondo difetto del piano.
    """
    destinazione = tmp_path / nome
    (destinazione / 'data').mkdir(parents=True)
    (destinazione / 'uploads').mkdir()
    sorgente = sqlite3.connect(app.config['DATABASE_PATH'])
    copia = sqlite3.connect(str(destinazione / 'data' / 'database.sqlite'))
    try:
        sorgente.backup(copia)
    finally:
        copia.close()
        sorgente.close()
    return destinazione


def _reimporta(archivio, destinazione, extra=()):
    """Lancia lo strumento di importazione reale come sottoprocesso, contro
    l'installazione di destinazione indicata ESPLICITAMENTE con --target
    (vedi la nota in cima al file sul perche' e' obbligatorio). --yes evita
    il prompt interattivo, --no-backup evita un passo irrilevante per questo
    test (la destinazione e' comunque una cartella temporanea)."""
    return subprocess.run(
        [sys.executable, IMPORTATORE, str(archivio),
         '--target', str(destinazione), '--yes', '--no-backup', *extra],
        capture_output=True, text=True, timeout=300,
    )


def _costruisci_installazione(tmp_path, single_struttura, sottocartella='sorgente'):
    """Un database con due strutture e il suo albero di uploads, nella forma
    che quella modalita' produce davvero (vedi models.upload_subdir): in
    multi ogni allegato sta sotto uploads/strutture/<id>/<tipo>/, in single
    sotto uploads/<tipo>/ senza alcun prefisso - condiviso da entrambe le
    strutture, perche' e' cosi' che upload_subdir lo scriverebbe per davvero
    in quella modalita'.

    'Clinica Beta' non viene mai esportata: la sua matricola e il suo
    allegato (chiamato 'beta.pdf', cosi' il nome basta a riconoscerlo ovunque
    finisca) servono a dimostrare che il giro non porta via nulla di suo -
    anche quando, in single, condivide la stessa cartella fisica di Alfa.
    """
    radice = tmp_path / sottocartella
    radice.mkdir(parents=True, exist_ok=True)
    percorso_db = str(radice / 'origine.db')
    con = sqlite3.connect(percorso_db)
    with open(os.path.join(RADICE, 'schema.sql'), encoding='utf-8') as f:
        con.executescript(f.read())
    con.execute("PRAGMA foreign_keys = ON")

    s = con.execute(
        "INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica Alfa','ALF',1)").lastrowid
    altra = con.execute(
        "INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica Beta','BET',1)").lastrowid
    d = con.execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Oculistica','OCU',?)", (s,)).lastrowid
    da = con.execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Altrui','ALT',?)", (altra,)).lastrowid
    con.execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id) "
                "VALUES ('admin@alfa.it','x','A','Alfa','admin',?)", (s,))

    def percorso(tipo, struttura_id, nome_file):
        if single_struttura:
            return f'{tipo}/{nome_file}'
        return f'strutture/{struttura_id}/{tipo}/{nome_file}'

    percorsi_alfa = []
    for n in range(3):
        ap = con.execute(
            "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato,ubicazione) "
            "VALUES (?,?,?,'REXXAM','OZY','funzionante','Sala 1')", (d, s, f'ALF-{n}')).lastrowid
        verbale = percorso('verbali', s, f'v{n}.pdf')
        verifica = percorso('verifiche', s, f'e{n}.pdf')
        con.execute("INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento,prossima_scadenza,verbale_path) "
                    "VALUES (?,'preventiva','2026-01-01','2027-01-01',?)", (ap, verbale))
        con.execute("INSERT INTO verifiche (apparecchio_id,data_verifica,prossima_scadenza,esito,documento_path) "
                    "VALUES (?,'2026-01-01','2027-01-01','positivo',?)", (ap, verifica))
        percorsi_alfa += [verbale, verifica]

    ap_beta = con.execute(
        "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato) "
        "VALUES (?,?,'BETA-1','SIEMENS','Y1','funzionante')", (da, altra)).lastrowid
    verbale_beta = percorso('verbali', altra, 'beta.pdf')
    con.execute("INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento,verbale_path) "
                "VALUES (?,'preventiva','2026-01-01',?)", (ap_beta, verbale_beta))
    con.commit()
    con.close()

    uploads = radice / 'uploads'
    for relativo in percorsi_alfa + [verbale_beta]:
        p = uploads / relativo.replace('/', os.sep)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'%PDF-1.4 contenuto ' + relativo.encode())

    return {
        'db': percorso_db, 'uploads': str(uploads),
        'struttura': s, 'altra': altra,
        'percorsi_alfa': percorsi_alfa, 'percorso_beta': verbale_beta,
    }


def _verifica_giro_completo(archivio, destinazione):
    """Le asserzioni comuni a ogni giro completo riuscito: apparecchi,
    manutenzioni, verifiche, matricole, nome vero della struttura, isolamento
    da Beta, e - il punto per cui questo test esiste - gli allegati PRESENTI
    SUL DISCO ai percorsi che il database dichiara, non solo la colonna
    valorizzata."""
    # L'ARCHIVIO per primo, prima di guardare la destinazione. L'isolamento
    # della destinazione non dimostra quello dell'archivio: importa_installazione
    # copia solo i file referenziati dalle righe che importa, quindi un allegato
    # estraneo nell'archivio non arriverebbe mai a destinazione qualunque cosa
    # ci sia dentro. Ma l'archivio e' l'artefatto che si consegna a terzi, ed e'
    # li' che un file di un altro tenant fa danno.
    file_archivio = {f for _, _, presenti in os.walk(os.path.join(archivio, 'uploads'))
                     for f in presenti}
    assert 'beta.pdf' not in file_archivio, (
        f"l'archivio contiene un allegato di un'altra struttura: {sorted(file_archivio)}")

    esito = _reimporta(archivio, destinazione)
    assert esito.returncode == 0, f"import fallito:\n{esito.stdout}\n{esito.stderr}"

    db_dest = str(destinazione / 'data' / 'database.sqlite')
    con = sqlite3.connect(db_dest)
    try:
        assert con.execute("SELECT COUNT(*) FROM apparecchi").fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM manutenzioni").fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM verifiche").fetchone()[0] == 3

        matricole = {r[0] for r in con.execute("SELECT matricola FROM apparecchi")}
        assert matricole == {'ALF-0', 'ALF-1', 'ALF-2'}
        assert 'BETA-1' not in matricole

        # Il nome vero della struttura, non il nome della cartella
        # dell'archivio (che porta il timestamp dell'esportazione): lo
        # garantisce config.json/structure_name scritto da esporta_struttura
        # e letto da Sorgente.nome_struttura_suggerito().
        nomi = [r[0] for r in con.execute("SELECT nome FROM strutture")]
        assert nomi == ['Clinica Alfa']

        percorsi_manutenzioni = [r[0] for r in con.execute(
            "SELECT verbale_path FROM manutenzioni WHERE verbale_path IS NOT NULL")]
        percorsi_verifiche = [r[0] for r in con.execute(
            "SELECT documento_path FROM verifiche WHERE documento_path IS NOT NULL")]
        assert len(percorsi_manutenzioni) == 3
        assert len(percorsi_verifiche) == 3
    finally:
        con.close()

    # Nota sul valore di questa asserzione: NON e' falsificabile rompendo
    # l'esportazione. Se l'archivio esce senza allegati, importa_installazione
    # azzera anche le colonne, quindi cade prima len(percorsi_*) == 3 e questo
    # ciclo non viene raggiunto. Uno stato "colonna valorizzata ma file
    # assente" lo puo' produrre solo l'importatore, che decide le due cose
    # insieme. Vale quindi come guardia contro una regressione DI
    # importa_installazione.py, non dell'esportazione.
    uploads_dest = str(destinazione / 'uploads')
    for rel in percorsi_manutenzioni + percorsi_verifiche:
        assoluto = os.path.join(uploads_dest, rel.replace('/', os.sep))
        assert os.path.isfile(assoluto), f"allegato non ricreato al percorso dichiarato: {rel}"

    # E nemmeno nella destinazione, sotto nessun nome rimappato. Questa meta'
    # e' piu' debole di quella sull'archivio (vedi sopra): la destinazione non
    # puo' ricevere un file estraneo per costruzione, quindi da sola non
    # dimostrerebbe l'isolamento.
    nomi_file_dest = {f for _, _, file_presenti in os.walk(uploads_dest) for f in file_presenti}
    assert 'beta.pdf' not in nomi_file_dest
    assert len(nomi_file_dest) == 6  # 3 verbali + 3 verifiche di Alfa, nient'altro


# ---------------------------------------------------------------------------
# Il giro completo, in entrambe le modalita'
# ---------------------------------------------------------------------------

def test_esporta_e_reimporta_in_modalita_multi(app, tmp_path):
    from struttura_service import esporta_struttura
    installazione = _costruisci_installazione(tmp_path, single_struttura=False)

    archivio = esporta_struttura(installazione['db'], installazione['uploads'],
                                 installazione['struttura'], str(tmp_path / 'archivi'),
                                 single_struttura=False)
    destinazione = _installazione_vuota(app, tmp_path)
    _verifica_giro_completo(archivio, destinazione)


def test_esporta_e_reimporta_in_modalita_single(app, tmp_path):
    """Stessa garanzia, ma con gli allegati nella forma che la modalita'
    single produce davvero: senza il prefisso strutture/<id>/, condivisi con
    Beta nella stessa cartella fisica. E' la forma che l'importatore deve
    comunque saper leggere (terzo difetto del piano: il test non provava
    affatto questa modalita')."""
    from struttura_service import esporta_struttura
    installazione = _costruisci_installazione(tmp_path, single_struttura=True)

    archivio = esporta_struttura(installazione['db'], installazione['uploads'],
                                 installazione['struttura'], str(tmp_path / 'archivi'),
                                 single_struttura=True)
    destinazione = _installazione_vuota(app, tmp_path)
    _verifica_giro_completo(archivio, destinazione)


# ---------------------------------------------------------------------------
# L'archivio prodotto dalla CANCELLAZIONE, non da un'esportazione manuale
# ---------------------------------------------------------------------------

def test_larchivio_della_cancellazione_e_reimportabile(app, tmp_path):
    """La promessa che la pagina di conferma scrive all'operatore poco prima
    di un'operazione irreversibile: l'archivio prodotto da elimina_struttura
    si reimporta e riporta indietro i dati che la cancellazione ha tolto
    dalla sorgente.

    elimina_struttura svuota la sorgente: il confronto e' quindi fra 'cosa
    c'era prima' (preso PRIMA di cancellare) e 'cosa c'e' nella destinazione
    DOPO' il reimporto - non fra due letture della stessa sorgente, che dopo
    la cancellazione sarebbe vuota e renderebbe il confronto vuoto contro
    vuoto."""
    from struttura_service import elimina_struttura

    installazione = _costruisci_installazione(tmp_path, single_struttura=False,
                                              sottocartella='da-cancellare')
    con = sqlite3.connect(installazione['db'])
    prima_apparecchi = con.execute(
        "SELECT COUNT(*) FROM apparecchi WHERE struttura_id=?", (installazione['struttura'],)).fetchone()[0]
    prima_matricole = {r[0] for r in con.execute(
        "SELECT matricola FROM apparecchi WHERE struttura_id=?", (installazione['struttura'],))}
    con.close()
    assert prima_apparecchi == 3  # se la fixture cambiasse, deve accorgersene questo, non un 0==0 a valle

    esito = elimina_struttura(installazione['db'], installazione['uploads'],
                              installazione['struttura'], str(tmp_path / 'archivi-cancellazione'))
    archivio = esito['archivio']

    con = sqlite3.connect(installazione['db'])
    try:
        assert con.execute("SELECT COUNT(*) FROM strutture WHERE id=?",
                           (installazione['struttura'],)).fetchone()[0] == 0
        # Beta non e' stata toccata dalla cancellazione di Alfa.
        assert con.execute("SELECT COUNT(*) FROM strutture WHERE id=?",
                           (installazione['altra'],)).fetchone()[0] == 1
    finally:
        con.close()

    destinazione = _installazione_vuota(app, tmp_path, 'destinazione-cancellazione')
    esito_import = _reimporta(archivio, destinazione)
    assert esito_import.returncode == 0, f"import fallito:\n{esito_import.stdout}\n{esito_import.stderr}"

    con = sqlite3.connect(str(destinazione / 'data' / 'database.sqlite'))
    try:
        dopo_apparecchi = con.execute("SELECT COUNT(*) FROM apparecchi").fetchone()[0]
        dopo_matricole = {r[0] for r in con.execute("SELECT matricola FROM apparecchi")}
        assert dopo_apparecchi == prima_apparecchi
        assert dopo_matricole == prima_matricole
        assert con.execute("SELECT nome FROM strutture").fetchone()[0] == 'Clinica Alfa'
        verbale = con.execute(
            "SELECT verbale_path FROM manutenzioni WHERE verbale_path IS NOT NULL LIMIT 1").fetchone()[0]
    finally:
        con.close()
    assoluto = os.path.join(str(destinazione / 'uploads'), verbale.replace('/', os.sep))
    assert os.path.isfile(assoluto), f"allegato non ricreato al percorso dichiarato: {verbale}"


# ---------------------------------------------------------------------------
# Il limite noto dell'importatore: da inchiodare, non da nascondere
# ---------------------------------------------------------------------------

def test_il_reimporto_non_riporta_il_logo_ne_lo_storico_import(app, tmp_path):
    """Limite noto e documentato, non nascosto: importa_installazione.py non
    riporta indietro il logo della struttura ne' lo storico degli import AI.

    NON e' una perdita di dati: entrambi restano nell'ARCHIVIO (prima meta'
    del test, che legge l'archivio direttamente e li trova), semplicemente
    non vengono ricreati nella destinazione senza un intervento in piu':

    - COLONNE_FILE di importa_installazione.py non elenca 'strutture':
      {'logo_path': ...} e crea_struttura() non valorizza logo_path
      sull'INSERT, quindi ne' il file ne' la colonna vengono riportati;
    - import_history e' dietro il flag opt-in --con-import-history, qui non
      passato.

    Se in futuro l'importatore verra' completato per riportare anche questi
    due, questo test cadra' e andra' aggiornato: e' il comportamento voluto,
    non un test da tenere verde a tutti i costi (vedi task-8-brief.md).
    """
    from struttura_service import esporta_struttura

    radice = tmp_path / 'con-logo'
    radice.mkdir()
    percorso_db = str(radice / 'origine.db')
    con = sqlite3.connect(percorso_db)
    with open(os.path.join(RADICE, 'schema.sql'), encoding='utf-8') as f:
        con.executescript(f.read())
    con.execute("PRAGMA foreign_keys = ON")
    s = con.execute(
        "INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica Alfa','ALF',1)").lastrowid
    con.execute("UPDATE strutture SET logo_path=? WHERE id=?",
               (f'strutture/{s}/loghi/logo.png', s))
    d = con.execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Oculistica','OCU',?)", (s,)).lastrowid
    con.execute(
        "INSERT INTO import_history (struttura_id,divisione_id,tipo_import,filename,filepath) "
        "VALUES (?,?,'inventario','vecchio.xlsx','import/vecchio.xlsx')", (s, d))
    con.commit()
    con.close()

    uploads = radice / 'uploads'
    logo = uploads / 'strutture' / str(s) / 'loghi' / 'logo.png'
    logo.parent.mkdir(parents=True)
    logo.write_bytes(b'PNG finto')

    archivio = esporta_struttura(percorso_db, str(uploads), s, str(tmp_path / 'archivi'))

    copia = sqlite3.connect(os.path.join(archivio, 'data', 'database.sqlite'))
    try:
        logo_in_archivio = copia.execute("SELECT logo_path FROM strutture").fetchone()[0]
        import_history_in_archivio = copia.execute("SELECT COUNT(*) FROM import_history").fetchone()[0]
    finally:
        copia.close()
    assert logo_in_archivio is not None
    assert import_history_in_archivio == 1
    assert os.path.isfile(os.path.join(archivio, 'uploads', 'strutture', str(s), 'loghi', 'logo.png'))

    destinazione = _installazione_vuota(app, tmp_path)
    esito = _reimporta(archivio, destinazione)
    assert esito.returncode == 0, f"import fallito:\n{esito.stdout}\n{esito.stderr}"

    con = sqlite3.connect(str(destinazione / 'data' / 'database.sqlite'))
    try:
        logo_in_target = con.execute("SELECT logo_path FROM strutture").fetchone()[0]
        import_history_in_target = con.execute("SELECT COUNT(*) FROM import_history").fetchone()[0]
    finally:
        con.close()
    assert logo_in_target is None
    assert import_history_in_target == 0
