"""A07 - ripristino e azzeramento del database mentre l'applicazione e' attiva
(audit del 28/08/2026).

Il rilievo: /admin/backup/<file>/ripristina e /admin/reset riscrivevano il file
SQLite vivo chiudendo soltanto la connessione della *propria* richiesta. Gli
altri thread di Waitress e lo scheduler continuavano a leggere e scrivere
mentre il file, il WAL e lo SHM venivano sostituiti. Il backup non veniva
validato prima dell'uso, la copia di sicurezza .pre_restore spariva subito, e
il log_attivita() scritto dentro il database appena ripristinato poteva fallire
facendo apparire come errore un ripristino riuscito.

Qui si verifica il meccanismo introdotto per chiuderlo:

- manutenzione_globale: barriera con conteggio dei lavori in corso, drenaggio e
  rientranza per thread (chi tiene l'esclusiva non aspetta se stesso);
- 503 con Retry-After alle richieste nuove, JSON sulle rotte /api/;
- task dello scheduler rimandati, senza consumare il turno;
- backup_service.verifica_database() prima e dopo la scrittura;
- .pre_restore conservata anche quando il ripristino riesce;
- traccia dell'operazione nel log applicativo, non solo nel database.
"""
import os
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager

import pytest
from werkzeug.security import generate_password_hash

import backup_service
import manutenzione_globale
import scheduler as modulo_scheduler
from schema_impianti import SCHEMA_VERSION_IMPIANTI


@pytest.fixture(autouse=True)
def barriera_pulita():
    """Lo stato del modulo e' globale: va azzerato prima e dopo ogni test,
    altrimenti una barriera lasciata alzata blocca tutta la suite."""
    manutenzione_globale.azzera()
    yield
    manutenzione_globale.azzera()


@contextmanager
def esclusiva_in_thread(motivo='prova', attesa=2.0):
    """Tiene un'operazione esclusiva in un thread separato.

    Serve a mettere il thread del test *fuori* dall'operazione, che e' la
    posizione di una richiesta HTTP qualsiasi mentre il ripristino e' in corso.
    """
    dentro = threading.Event()
    rilascia = threading.Event()
    errori = []

    def corpo():
        try:
            with manutenzione_globale.operazione_esclusiva(motivo, attesa=attesa):
                dentro.set()
                rilascia.wait(10)
        except Exception as e:      # riportato al test
            errori.append(e)
            dentro.set()

    t = threading.Thread(target=corpo, daemon=True)
    t.start()
    assert dentro.wait(10), "l'operazione esclusiva non e' mai partita"
    try:
        if errori:
            raise errori[0]
        yield
    finally:
        rilascia.set()
        t.join(10)


# ---------------------------------------------------------------------------
# La barriera
# ---------------------------------------------------------------------------

def test_un_lavoro_si_registra_e_si_toglie():
    assert manutenzione_globale.entra() is True
    assert manutenzione_globale.partecipanti() == 1
    manutenzione_globale.esci()
    assert manutenzione_globale.partecipanti() == 0


def test_uscire_senza_essere_entrati_non_scala_il_contatore():
    """teardown_request gira anche per le richieste respinte con 503: se
    quell'uscita scalasse il contatore, il drenaggio vedrebbe numeri negativi e
    l'esclusiva partirebbe con lavori ancora dentro."""
    manutenzione_globale.esci()
    assert manutenzione_globale.partecipanti() == 0


def test_con_la_barriera_alzata_un_lavoro_nuovo_non_entra():
    with esclusiva_in_thread('ripristino'):
        assert manutenzione_globale.attiva() is True
        assert manutenzione_globale.descrizione() == 'ripristino'
        assert manutenzione_globale.entra() is False
    assert manutenzione_globale.entra() is True
    manutenzione_globale.esci()


def test_chi_e_gia_dentro_puo_rientrare():
    """Mentre l'esclusiva aspetta il drenaggio, chi era gia' dentro deve poter
    fare una chiamata annidata (un task che ne chiama un altro): altrimenti si
    troverebbe fuori da un lavoro che ha gia' iniziato. I thread nuovi, invece,
    restano fuori."""
    assert manutenzione_globale.entra() is True      # lavoro gia' in corso
    finita = threading.Event()

    def esclusiva():
        with manutenzione_globale.operazione_esclusiva('ripristino', attesa=10.0):
            finita.set()

    t = threading.Thread(target=esclusiva, daemon=True)
    t.start()
    scadenza = time.monotonic() + 5
    while not manutenzione_globale.attiva() and time.monotonic() < scadenza:
        time.sleep(0.01)
    assert manutenzione_globale.attiva(), "la barriera non si e' alzata"

    assert manutenzione_globale.entra() is True      # rientro dello stesso thread
    rifiutato = []
    nuovo = threading.Thread(
        target=lambda: rifiutato.append(manutenzione_globale.entra()))
    nuovo.start()
    nuovo.join(5)
    assert rifiutato == [False]

    manutenzione_globale.esci()
    manutenzione_globale.esci()
    assert finita.wait(10), "il drenaggio non si e' completato"
    t.join(10)
    assert manutenzione_globale.partecipanti() == 0


def test_l_esclusiva_non_aspetta_se_stessa():
    """La richiesta che chiede il ripristino e' a sua volta un lavoro
    registrato: se il drenaggio la contasse, aspetterebbe se stessa fino al
    timeout e il ripristino non partirebbe mai."""
    assert manutenzione_globale.entra() is True
    with manutenzione_globale.operazione_esclusiva('ripristino', attesa=0.5):
        assert manutenzione_globale.partecipanti() == 0
    # La partecipazione del chiamante viene restituita all'uscita.
    assert manutenzione_globale.partecipanti() == 1
    manutenzione_globale.esci()


def test_due_operazioni_esclusive_insieme_sono_rifiutate():
    with esclusiva_in_thread('ripristino'):
        with pytest.raises(manutenzione_globale.ManutenzioneInCorso) as e:
            with manutenzione_globale.operazione_esclusiva('azzeramento',
                                                           attesa=0.2):
                pass
        assert 'ripristino' in str(e.value)


def test_se_il_traffico_non_si_drena_l_operazione_non_parte():
    """E' il punto del rilievo: meglio rifiutare il ripristino che sostituire
    il file mentre qualcun altro ci sta scrivendo dentro."""
    fermo = threading.Event()
    rilascia = threading.Event()

    def occupante():
        with manutenzione_globale.lavoro() as ammesso:
            assert ammesso
            fermo.set()
            rilascia.wait(10)

    t = threading.Thread(target=occupante, daemon=True)
    t.start()
    assert fermo.wait(10)
    try:
        with pytest.raises(manutenzione_globale.ManutenzioneInCorso) as e:
            with manutenzione_globale.operazione_esclusiva('ripristino',
                                                           attesa=0.3):
                pytest.fail('il corpo non doveva essere eseguito')
        assert 'riprova' in str(e.value)
        # La barriera va riabbassata: un tentativo fallito non deve lasciare
        # l'applicazione in manutenzione per sempre.
        assert manutenzione_globale.attiva() is False
        assert manutenzione_globale.entra() is True
        manutenzione_globale.esci()
    finally:
        rilascia.set()
        t.join(10)


def test_un_errore_dentro_l_esclusiva_riabbassa_la_barriera():
    with pytest.raises(ZeroDivisionError):
        with manutenzione_globale.operazione_esclusiva('ripristino'):
            1 / 0
    assert manutenzione_globale.attiva() is False


# ---------------------------------------------------------------------------
# Le richieste HTTP
# ---------------------------------------------------------------------------

def test_le_richieste_ricevono_503_durante_la_manutenzione(client):
    with esclusiva_in_thread('ripristino del backup'):
        risposta = client.get('/')
    assert risposta.status_code == 503
    assert risposta.headers.get('Retry-After') == '30'
    assert 'Manutenzione in corso' in risposta.get_data(as_text=True)


def test_le_rotte_api_ricevono_json_e_non_la_pagina_html(client):
    """Un client REST non sa cosa farsene di una pagina: la 503 deve restare
    leggibile come JSON."""
    with esclusiva_in_thread('ripristino del backup'):
        risposta = client.get('/api/v1/apparecchi')
    assert risposta.status_code == 503
    assert risposta.headers.get('Retry-After') == '30'
    assert 'Manutenzione' in risposta.get_json()['errore']


def test_finita_la_manutenzione_le_richieste_ripartono(client):
    with esclusiva_in_thread('ripristino'):
        assert client.get('/').status_code == 503
    assert client.get('/').status_code in (200, 302)
    assert manutenzione_globale.partecipanti() == 0


# ---------------------------------------------------------------------------
# Lo scheduler
# ---------------------------------------------------------------------------

class _FintoTempo:
    """time senza attese: lo scheduler dorme 10 secondi prima di partire."""
    sleep = staticmethod(lambda _s: None)
    time = staticmethod(time.time)


def _scheduler_a_un_giro(app, monkeypatch, eseguiti):
    """Uno scheduler con un solo task scaduto, che si ferma dopo un giro."""
    sched = modulo_scheduler.BackgroundScheduler(app)
    sched._tasks = [{'name': 'finto', 'func': lambda: eseguiti.append(1),
                     'interval': 0, 'last_run': 0}]
    monkeypatch.setattr(modulo_scheduler, 'time', _FintoTempo)

    def ferma(timeout=None):
        sched._stop_event.set()
        return True

    monkeypatch.setattr(sched._stop_event, 'wait', ferma)
    return sched


def test_lo_scheduler_esegue_i_task_quando_non_c_e_manutenzione(app, monkeypatch):
    eseguiti = []
    _scheduler_a_un_giro(app, monkeypatch, eseguiti)._run()
    assert eseguiti == [1]


def test_lo_scheduler_rimanda_i_task_durante_la_manutenzione(app, monkeypatch):
    """Il task non parte e il turno non viene consumato: last_run resta a zero,
    cosi' il task riparte al primo giro dopo la manutenzione invece di
    aspettare un intero intervallo."""
    eseguiti = []
    sched = _scheduler_a_un_giro(app, monkeypatch, eseguiti)
    with esclusiva_in_thread('azzeramento'):
        sched._run()
    assert eseguiti == []
    assert sched._tasks[0]['last_run'] == 0


# ---------------------------------------------------------------------------
# La validazione del backup
# ---------------------------------------------------------------------------

def test_il_database_dell_applicazione_e_valido(app):
    assert backup_service.verifica_database(app.config['DATABASE_PATH']) == []


def test_un_file_che_non_e_un_database_viene_rifiutato(tmp_path):
    finto = tmp_path / 'medinventory_backup_finto.sqlite'
    finto.write_text('non sono un database', encoding='utf-8')
    assert backup_service.verifica_database(str(finto))


def test_un_file_inesistente_viene_rifiutato(tmp_path):
    problemi = backup_service.verifica_database(str(tmp_path / 'assente.sqlite'))
    assert problemi and 'non trovato' in problemi[0]


def test_un_database_sqlite_estraneo_viene_rifiutato(tmp_path):
    """Un database SQLite integro ma di un altro programma passerebbe
    quick_check: senza il controllo sulle tabelle sostituirebbe comunque i dati
    di MedInventory."""
    estraneo = tmp_path / 'altro.sqlite'
    conn = sqlite3.connect(str(estraneo))
    conn.execute('CREATE TABLE ricette (id INTEGER PRIMARY KEY)')
    conn.commit()
    conn.close()
    problemi = backup_service.verifica_database(str(estraneo))
    assert any('MedInventory' in p for p in problemi)


def test_uno_schema_piu_recente_viene_rifiutato(app, tmp_path):
    """Un backup preso da un'installazione aggiornata non si puo' migrare
    all'indietro."""
    futuro = tmp_path / 'futuro.sqlite'
    shutil.copy2(app.config['DATABASE_PATH'], futuro)
    conn = sqlite3.connect(str(futuro))
    conn.execute(f'PRAGMA user_version = {SCHEMA_VERSION_IMPIANTI + 10}')
    conn.commit()
    conn.close()
    problemi = backup_service.verifica_database(str(futuro))
    assert any('recente' in p for p in problemi)


def test_la_verifica_non_modifica_il_database(app, tmp_path):
    """Viene aperto in sola lettura: validare un backup non deve migrarlo."""
    copia = tmp_path / 'copia.sqlite'
    shutil.copy2(app.config['DATABASE_PATH'], copia)
    prima = copia.read_bytes()
    assert backup_service.verifica_database(str(copia)) == []
    assert copia.read_bytes() == prima


# ---------------------------------------------------------------------------
# Il ripristino
# ---------------------------------------------------------------------------

@pytest.fixture
def coppia(app, tmp_path):
    """Un database 'vivo' e un backup valido con un dato riconoscibile."""
    vivo = tmp_path / 'vivo.sqlite'
    shutil.copy2(app.config['DATABASE_PATH'], vivo)
    backup = tmp_path / 'medinventory_backup_20260101_000000_abcdef12.sqlite'
    shutil.copy2(app.config['DATABASE_PATH'], backup)
    conn = sqlite3.connect(str(backup))
    conn.execute("INSERT INTO strutture (nome, codice, attiva) "
                 "VALUES ('Dal backup', 'BK', 1)")
    conn.commit()
    conn.close()
    return {'vivo': str(vivo), 'backup': str(backup)}


def _strutture(percorso):
    conn = sqlite3.connect(percorso)
    try:
        return [r[0] for r in conn.execute('SELECT nome FROM strutture')]
    finally:
        conn.close()


def test_il_ripristino_scrive_i_dati_del_backup(coppia):
    backup_service.restore_backup(coppia['backup'], coppia['vivo'])
    assert 'Dal backup' in _strutture(coppia['vivo'])


def test_il_ripristino_conserva_la_copia_di_sicurezza(coppia):
    """Prima veniva cancellata subito: chi si accorgeva dopo il riavvio di aver
    ripristinato il backup sbagliato non aveva piu' niente a cui tornare."""
    copia = backup_service.restore_backup(coppia['backup'], coppia['vivo'])
    assert copia == coppia['vivo'] + '.pre_restore'
    assert os.path.exists(copia)
    assert 'Dal backup' not in _strutture(copia)


def test_un_backup_non_valido_non_tocca_il_database(coppia, tmp_path):
    rotto = tmp_path / 'medinventory_backup_rotto.sqlite'
    rotto.write_text('spazzatura', encoding='utf-8')
    with open(coppia['vivo'], 'rb') as f:
        prima = f.read()
    with pytest.raises(ValueError) as e:
        backup_service.restore_backup(str(rotto), coppia['vivo'])
    assert 'non ripristinabile' in str(e.value)
    with open(coppia['vivo'], 'rb') as f:
        assert f.read() == prima
    assert not os.path.exists(coppia['vivo'] + '.pre_restore')


def test_i_file_laterali_del_database_precedente_vengono_rimossi(coppia):
    """WAL e SHM appartengono al database sostituito: lasciarli in giro
    significa lasciare transazioni di un altro database accanto a questo."""
    for estensione in ('-wal', '-shm'):
        with open(coppia['vivo'] + estensione, 'wb') as f:
            f.write(b'')
    backup_service.restore_backup(coppia['backup'], coppia['vivo'])
    residui = [e for e in ('-wal', '-shm')
               if os.path.exists(coppia['vivo'] + e)
               and os.path.getsize(coppia['vivo'] + e) > 0]
    assert residui == []


# ---------------------------------------------------------------------------
# La rotta di ripristino
# ---------------------------------------------------------------------------

def entra(client, email):
    client.post('/login', data={'email': email, 'password': 'Passw0rd!'})


@pytest.fixture
def superadmin(app):
    from models import execute
    with app.app_context():
        pw = generate_password_hash('Passw0rd!')
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,"
                "struttura_id,primo_accesso) "
                "VALUES ('super@g.it',?,'Super','G','superadmin',NULL,0)", (pw,))
    return 'super@g.it'


def crea_backup(app):
    """Un backup vero, prodotto dal servizio, dentro BACKUPS_PATH.

    Va preso *dopo* il login: la sessione vive in una tabella del database, e
    un backup precedente riporterebbe indietro anche quella, disconnettendo
    l'operatore proprio mentre legge l'esito.
    """
    return backup_service.create_backup(app.config['DATABASE_PATH'],
                                        app.config['BACKUPS_PATH'])['filename']


def test_il_ripristino_dalla_rotta_riesce_e_indica_la_copia(client, app,
                                                            superadmin):
    entra(client, superadmin)
    backup_pronto = crea_backup(app)
    risposta = client.post(f'/admin/backup/{backup_pronto}/ripristina',
                           follow_redirects=True)
    testo = risposta.get_data(as_text=True)
    assert 'Errore durante il ripristino' not in testo
    assert 'pre_restore' in testo
    assert os.path.exists(app.config['DATABASE_PATH'] + '.pre_restore')


def test_il_ripristino_riuscito_non_e_un_errore_se_il_log_fallisce(
        client, app, superadmin, monkeypatch):
    """Il rilievo lo cita per nome: log_attivita() scrive nel database appena
    ripristinato, dove l'autore puo' non esistere piu' e la foreign key
    rifiuta la riga. Il ripristino e' comunque riuscito."""
    import admin as modulo_admin

    def esplode(*args, **kwargs):
        raise sqlite3.IntegrityError('FOREIGN KEY constraint failed')

    entra(client, superadmin)
    backup_pronto = crea_backup(app)
    monkeypatch.setattr(modulo_admin, 'log_attivita', esplode)
    risposta = client.post(f'/admin/backup/{backup_pronto}/ripristina',
                           follow_redirects=True)
    testo = risposta.get_data(as_text=True)
    assert 'Errore durante il ripristino' not in testo
    assert 'Database ripristinato' in testo


def test_il_ripristino_e_fermato_se_c_e_gia_un_operazione_in_corso(
        client, app, superadmin):
    """Due operazioni globali insieme sono il caso peggiore: due processi che
    riscrivono lo stesso file."""
    entra(client, superadmin)
    backup_pronto = crea_backup(app)
    with esclusiva_in_thread('azzeramento del database', attesa=2.0):
        risposta = client.post(f'/admin/backup/{backup_pronto}/ripristina',
                               follow_redirects=True)
    assert risposta.status_code == 503
    assert not os.path.exists(app.config['DATABASE_PATH'] + '.pre_restore')


def test_il_ripristino_di_un_file_estraneo_e_rifiutato(client, app, superadmin):
    """Il nome supera i controlli di percorso ma il contenuto non e' un
    database: prima veniva copiato addosso a quello vivo lo stesso."""
    os.makedirs(app.config['BACKUPS_PATH'], exist_ok=True)
    nome = 'medinventory_backup_20260101_000000_deadbeef.sqlite'
    with open(os.path.join(app.config['BACKUPS_PATH'], nome), 'w',
              encoding='utf-8') as f:
        f.write('non sono un database')
    entra(client, superadmin)
    risposta = client.post(f'/admin/backup/{nome}/ripristina',
                           follow_redirects=True)
    assert 'Errore durante il ripristino' in risposta.get_data(as_text=True)
    # Il database vivo non e' stato toccato: e' ancora valido e non e' stata
    # nemmeno creata la copia di sicurezza, segno che la scrittura non e' mai
    # iniziata.
    assert backup_service.verifica_database(app.config['DATABASE_PATH']) == []
    assert not os.path.exists(app.config['DATABASE_PATH'] + '.pre_restore')
