"""Test dello strumento di manutenzione a riga di comando."""
import json as _json
import os
import sqlite3
import sys

import pytest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)

from manutenzione_lib import stato, tui
from manutenzione_lib import utenti as mutenti


@pytest.fixture
def conn(app):
    """Connessione grezza al database di prova.

    Passa dalla fixture 'app' perche' e' create_app() ad applicare lo schema,
    ma poi lavora in sqlite3 puro: i moduli di manutenzione_lib/ non conoscono
    Flask, e i test devono esercitarli come li esercita manutenzione.py.
    """
    c = sqlite3.connect(app.config['DATABASE_PATH'])
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys = ON')
    yield c
    c.close()


def _struttura(conn, nome='Casa di Cura Alfa'):
    codice = nome.lower().replace(' ', '-')[:20]
    cur = conn.execute("INSERT INTO strutture (nome, codice) VALUES (?, ?)",
                       (nome, codice))
    conn.commit()
    return cur.lastrowid


def _divisione(conn, struttura_id, nome='Rianimazione'):
    cur = conn.execute(
        "INSERT INTO divisioni (nome, codice, struttura_id) VALUES (?, ?, ?)",
        (nome, nome[:3].upper(), struttura_id))
    conn.commit()
    return cur.lastrowid


def _utente(conn, email, ruolo='admin', struttura_id=None, attivo=1,
            password='Password1', password_hash=None):
    from werkzeug.security import generate_password_hash
    cur = conn.execute(
        """INSERT INTO utenti (email, password_hash, nome, cognome, ruolo,
                               struttura_id, attivo)
           VALUES (?, ?, 'Nome', 'Cognome', ?, ?, ?)""",
        (email, password_hash or generate_password_hash(password),
         ruolo, struttura_id, attivo))
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# tui
# ---------------------------------------------------------------------------

def test_senza_terminale_nessuna_sequenza_di_escape(monkeypatch):
    """L'output rediretto su file deve restare leggibile: niente ANSI.

    E' il caso di 'manutenzione.py stato > rapporto.txt', e anche quello di
    pytest, che cattura stdout.
    """
    monkeypatch.setattr(tui, 'supporta_colore', lambda: False)
    assert '\033' not in tui.colora('ciao', 'verde')
    assert '\033' not in tui.riga_esito('errore', 'guasto')
    assert '\033' not in tui.titolo('Stato')


def test_le_righe_di_esito_restano_distinguibili_senza_colore(monkeypatch):
    monkeypatch.setattr(tui, 'supporta_colore', lambda: False)
    assert tui.riga_esito('ok', 'tutto bene').startswith('[OK]')
    assert tui.riga_esito('avviso', 'occhio').startswith('[!!]')
    assert tui.riga_esito('errore', 'guasto').startswith('[ERR]')


def test_la_tabella_allinea_sulle_celle_piu_larghe(monkeypatch):
    monkeypatch.setattr(tui, 'supporta_colore', lambda: False)
    reso = tui.tabella(['Email', 'Ruolo'],
                       [['a@b.it', 'admin'], ['lunghissimo@esempio.it', 'utente']])
    righe = reso.splitlines()
    assert len(righe) == 4
    # Tutte le righe finiscono alla stessa colonna: e' cio' che rende
    # leggibile un elenco di utenti su una console stretta.
    assert len({len(r.rstrip()) for r in righe}) <= 2
    assert 'lunghissimo@esempio.it' in reso


def test_la_tabella_vuota_non_esplode(monkeypatch):
    monkeypatch.setattr(tui, 'supporta_colore', lambda: False)
    assert tui.tabella(['Email'], []) != ''


# ---------------------------------------------------------------------------
# stato
# ---------------------------------------------------------------------------

def test_lo_stato_riporta_database_schema_e_utenti(conn, tmp_path):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    _utente(conn, 'super@alfa.it', 'superadmin', None)

    fotografia = stato.raccogli(conn, {'single_struttura': False}, str(tmp_path))

    assert fotografia['database']['integrity_check'] == 'ok'
    assert fotografia['schema']['user_version'] >= 0
    assert fotografia['modalita']['single_struttura'] is False
    assert fotografia['modalita']['strutture'] == 1
    assert fotografia['utenti']['totale_attivi'] == 2
    assert fotografia['utenti']['per_ruolo']['admin'] == 1
    assert fotografia['utenti']['per_ruolo']['superadmin'] == 1


def test_lo_stato_non_espone_mai_le_chiavi(conn, tmp_path):
    config = {
        'default_ai_provider': 'anthropic',
        'default_anthropic_api_key': 'sk-ant-segretissima',
        'smtp_host': 'smtp.esempio.it',
        'smtp_password': 'password-in-chiaro',
    }
    fotografia = stato.raccogli(conn, config, str(tmp_path))

    reso = _json.dumps(fotografia, default=str)
    assert 'sk-ant-segretissima' not in reso
    assert 'password-in-chiaro' not in reso
    assert fotografia['ai']['chiavi']['anthropic'] is True
    assert fotografia['posta']['smtp_host'] == 'smtp.esempio.it'


def test_lo_stato_sopravvive_a_uno_schema_incompleto(conn, tmp_path):
    """L'installazione vecchia e' esattamente il caso da ispezionare.

    Se una tabella manca, la sezione si dichiara non disponibile e la
    raccolta prosegue: fermarsi qui vorrebbe dire non poter guardare proprio
    i database che hanno bisogno dello strumento.
    """
    conn.execute('DROP TABLE verifiche')
    conn.commit()

    fotografia = stato.raccogli(conn, {}, str(tmp_path))

    assert fotografia['dati']['disponibile'] is False
    assert 'verifiche' in fotografia['dati']['motivo']
    assert fotografia['database']['integrity_check'] == 'ok'


# ---------------------------------------------------------------------------
# utenti: lettura e impronte
# ---------------------------------------------------------------------------

def test_riconosce_le_impronte_che_werkzeug_sa_verificare():
    from werkzeug.security import generate_password_hash
    assert mutenti.stato_impronta(generate_password_hash('Password1')) == 'ok'
    assert mutenti.stato_impronta(
        generate_password_hash('Password1', method='pbkdf2:sha256')) == 'ok'


def test_riconosce_l_impronta_che_fa_esplodere_il_login():
    """Il caso dell'installazione migrata da werkzeug 2.

    check_password_hash SOLLEVA ValueError su un metodo che non conosce piu',
    non restituisce False: auth.py:422 non la cattura, quindi il login
    risponde 500 invece di rifiutare le credenziali. E' la ragione per cui
    questo controllo esiste.
    """
    from werkzeug.security import check_password_hash
    vecchia = 'sha256$abcdef$0123456789'
    assert mutenti.stato_impronta(vecchia) == 'metodo_sconosciuto'
    with pytest.raises(ValueError):
        check_password_hash(vecchia, 'qualunque')


def test_riconosce_l_impronta_senza_forma():
    """Il sentinella di utente_service non ha la forma metodo$sale$impronta.

    check_password_hash torna False senza sollevare: e' voluto, un account
    distrutto deve rifiutare, non esplodere.
    """
    from werkzeug.security import check_password_hash
    from utente_service import PASSWORD_INUTILIZZABILE
    assert mutenti.stato_impronta(PASSWORD_INUTILIZZABILE) == 'malformata'
    assert check_password_hash(PASSWORD_INUTILIZZABILE, 'qualunque') is False


def test_elenco_riporta_lo_stato_di_ogni_impronta(conn):
    sid = _struttura(conn)
    _utente(conn, 'buono@alfa.it', 'admin', sid)
    _utente(conn, 'vecchio@alfa.it', 'utente', sid,
            password_hash='sha256$sale$impronta')

    righe = {r['email']: r for r in mutenti.elenco(conn)}
    assert righe['buono@alfa.it']['impronta'] == 'ok'
    assert righe['vecchio@alfa.it']['impronta'] == 'metodo_sconosciuto'


def test_elenco_ristretto_a_una_struttura(conn):
    alfa = _struttura(conn, 'Alfa')
    beta = _struttura(conn, 'Beta')
    _utente(conn, 'a@alfa.it', 'admin', alfa)
    _utente(conn, 'b@beta.it', 'admin', beta)
    _utente(conn, 'super@x.it', 'superadmin', None)

    email = {r['email'] for r in mutenti.elenco(conn, struttura_id=alfa)}
    assert email == {'a@alfa.it'}


def test_imposta_password_rende_verificabile_una_impronta_rotta(conn):
    from werkzeug.security import check_password_hash
    sid = _struttura(conn)
    _utente(conn, 'vecchio@alfa.it', 'admin', sid,
            password_hash='sha256$sale$impronta')

    mutenti.imposta_password(conn, 'vecchio@alfa.it', 'NuovaPassword1')
    conn.commit()

    riga = conn.execute("SELECT password_hash, attivo, primo_accesso "
                        "FROM utenti WHERE email = ?", ('vecchio@alfa.it',)).fetchone()
    assert check_password_hash(riga['password_hash'], 'NuovaPassword1')
    assert riga['attivo'] == 1
    assert riga['primo_accesso'] == 1


def test_imposta_password_rifiuta_un_indirizzo_inesistente(conn):
    with pytest.raises(mutenti.UtenteInesistente):
        mutenti.imposta_password(conn, 'nessuno@alfa.it', 'NuovaPassword1')


def test_password_debole_rifiutata(conn):
    assert mutenti.valida_password('corta') != []
    assert mutenti.valida_password('tuttominuscolo1') != []
    assert mutenti.valida_password('SenzaNumeri') != []
    assert mutenti.valida_password('Password1') == []
    with pytest.raises(mutenti.PasswordDebole):
        mutenti.crea_accesso(conn, 'nuovo@alfa.it', 'corta', 'superadmin')


def test_crea_accesso_rifiuta_una_email_gia_presente(conn):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    with pytest.raises(mutenti.EmailGiaInUso):
        mutenti.crea_accesso(conn, 'admin@alfa.it', 'Password1', 'admin', sid)


# ---------------------------------------------------------------------------
# utenti: azzeramento
# ---------------------------------------------------------------------------

def test_azzeramento_conservativo_lascia_i_dati_e_la_tracciabilita(conn):
    sid = _struttura(conn)
    uid = _utente(conn, 'admin@alfa.it', 'admin', sid)
    div = _divisione(conn, sid)
    conn.execute("INSERT INTO apparecchi (struttura_id, divisione_id, marca, modello, "
                 "matricola, created_by) "
                 "VALUES (?, ?, 'Philips', 'Defibrillatore', 'MAT-001', ?)",
                 (sid, div, uid))
    conn.commit()

    esito = mutenti.azzera(conn, rimpiazzo=mutenti.Rimpiazzo(
        email='nuovo@alfa.it', password='Password1', ruolo='admin',
        struttura_id=sid))
    conn.commit()

    # L'apparecchio non si tocca, e continua a dire chi l'ha inserito.
    riga = conn.execute('SELECT modello, created_by FROM apparecchi').fetchone()
    assert riga['modello'] == 'Defibrillatore'
    assert riga['created_by'] == uid
    # L'account e' distrutto ma la riga resta come voce storica.
    vecchio = conn.execute('SELECT email, attivo, eliminato_il FROM utenti '
                           'WHERE id = ?', (uid,)).fetchone()
    assert vecchio['email'] == f'admin@alfa.it#eliminato-{uid}'
    assert vecchio['attivo'] == 0
    assert vecchio['eliminato_il'] is not None
    assert 'admin@alfa.it' in esito['coinvolti']


def test_azzeramento_definitivo_rimuove_le_righe_e_libera_i_riferimenti(conn):
    sid = _struttura(conn)
    uid = _utente(conn, 'admin@alfa.it', 'admin', sid)
    div = _divisione(conn, sid)
    conn.execute("INSERT INTO apparecchi (struttura_id, divisione_id, marca, modello, "
                 "matricola, created_by) "
                 "VALUES (?, ?, 'Philips', 'Defibrillatore', 'MAT-001', ?)",
                 (sid, div, uid))
    conn.commit()

    mutenti.azzera(conn, definitivo=True, rimpiazzo=mutenti.Rimpiazzo(
        email='nuovo@alfa.it', password='Password1', ruolo='admin',
        struttura_id=sid))
    conn.commit()

    assert conn.execute('SELECT COUNT(*) FROM utenti WHERE id = ?',
                        (uid,)).fetchone()[0] == 0
    riga = conn.execute('SELECT modello, created_by FROM apparecchi').fetchone()
    assert riga['modello'] == 'Defibrillatore'
    assert riga['created_by'] is None


def test_azzerare_senza_rimpiazzo_e_rifiutato(conn):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)

    with pytest.raises(mutenti.AccessoNonGarantito):
        mutenti.azzera(conn)


def test_dopo_il_rifiuto_nulla_e_stato_scritto(conn):
    """Il rifiuto arriva DOPO le cancellazioni: e' il chiamante ad annullare
    la transazione. Qui si verifica che annullarla basti davvero."""
    sid = _struttura(conn)
    uid = _utente(conn, 'admin@alfa.it', 'admin', sid)

    with pytest.raises(mutenti.AccessoNonGarantito):
        mutenti.azzera(conn)
    conn.rollback()

    riga = conn.execute('SELECT email, attivo FROM utenti WHERE id = ?',
                        (uid,)).fetchone()
    assert riga['email'] == 'admin@alfa.it'
    assert riga['attivo'] == 1


def test_azzeramento_ristretto_a_una_struttura(conn):
    alfa = _struttura(conn, 'Alfa')
    beta = _struttura(conn, 'Beta')
    _utente(conn, 'a@alfa.it', 'admin', alfa)
    id_beta = _utente(conn, 'b@beta.it', 'admin', beta)
    id_super = _utente(conn, 'super@x.it', 'superadmin', None)

    mutenti.azzera(conn, struttura_id=alfa, rimpiazzo=mutenti.Rimpiazzo(
        email='nuovo@alfa.it', password='Password1', ruolo='admin',
        struttura_id=alfa))
    conn.commit()

    assert conn.execute('SELECT email FROM utenti WHERE id = ?',
                        (id_beta,)).fetchone()['email'] == 'b@beta.it'
    assert conn.execute('SELECT email FROM utenti WHERE id = ?',
                        (id_super,)).fetchone()['email'] == 'super@x.it'


def test_su_una_struttura_un_superadmin_globale_basta_come_accesso(conn):
    alfa = _struttura(conn, 'Alfa')
    _utente(conn, 'a@alfa.it', 'admin', alfa)
    _utente(conn, 'super@x.it', 'superadmin', None)

    esito = mutenti.azzera(conn, struttura_id=alfa)
    conn.commit()

    assert esito['rimpiazzo_id'] is None
    assert esito['coinvolti'] == ['a@alfa.it']


def test_l_azzeramento_lascia_una_voce_nel_registro(conn):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)

    mutenti.azzera(conn, rimpiazzo=mutenti.Rimpiazzo(
        email='nuovo@alfa.it', password='Password1', ruolo='admin',
        struttura_id=sid))
    conn.commit()

    voce = conn.execute(
        "SELECT utente_id, azione, dettagli FROM log_attivita "
        "WHERE azione = 'azzeramento_utenti'").fetchone()
    assert voce is not None
    assert voce['utente_id'] is None
    assert 'manutenzione.py' in voce['dettagli']


def test_le_sessioni_aperte_non_sopravvivono_all_azzeramento(conn):
    sid = _struttura(conn)
    uid = _utente(conn, 'admin@alfa.it', 'admin', sid)
    conn.execute("INSERT INTO sessioni (utente_id, token, expires_at) "
                 "VALUES (?, 'token-vivo', datetime('now', '+8 hours'))", (uid,))
    conn.commit()

    mutenti.azzera(conn, rimpiazzo=mutenti.Rimpiazzo(
        email='nuovo@alfa.it', password='Password1', ruolo='admin',
        struttura_id=sid))
    conn.commit()

    assert conn.execute('SELECT COUNT(*) FROM sessioni').fetchone()[0] == 0


# ---------------------------------------------------------------------------
# diagnosi
# ---------------------------------------------------------------------------

from manutenzione_lib import diagnosi, operazioni  # noqa: E402


def _diagnostica(conn, tmp_path, config=None):
    config = config or {}
    fotografia = stato.raccogli(conn, config, str(tmp_path))
    return diagnosi.esegui(conn, config, fotografia)


def _titoli(esiti):
    return {e.titolo for e in esiti}


def test_un_database_sano_con_un_admin_non_produce_errori(conn, tmp_path):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    (tmp_path / 'uploads').mkdir(exist_ok=True)
    esiti = _diagnostica(conn, tmp_path, {'uploads_path': str(tmp_path / 'uploads')})
    assert not diagnosi.ci_sono_errori(esiti), [
        (e.gravita, e.titolo, e.dettaglio) for e in esiti if e.gravita == 'errore']


def test_nessun_utente_attivo_e_un_errore(conn, tmp_path):
    _struttura(conn)
    esiti = _diagnostica(conn, tmp_path)
    assert 'Nessun utente attivo' in _titoli(esiti)
    assert diagnosi.ci_sono_errori(esiti)


def test_struttura_senza_amministratore_attivo(conn, tmp_path):
    sid = _struttura(conn, 'Alfa')
    _utente(conn, 'super@x.it', 'superadmin', None)
    _utente(conn, 'utente@alfa.it', 'utente', sid)
    esiti = _diagnostica(conn, tmp_path)
    assert 'Struttura senza amministratore attivo' in _titoli(esiti)


def test_impronta_non_verificabile_e_un_errore_con_il_rimedio_giusto(conn, tmp_path):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    _utente(conn, 'vecchio@alfa.it', 'utente', sid,
            password_hash='sha256$sale$impronta')

    esiti = _diagnostica(conn, tmp_path)
    guasto = [e for e in esiti if e.titolo == 'Password non verificabile']
    assert len(guasto) == 1
    assert guasto[0].gravita == 'errore'
    assert 'vecchio@alfa.it' in guasto[0].dettaglio
    assert 'utenti password' in guasto[0].rimedio


def test_utente_disattivato_e_un_avviso_che_spiega_il_messaggio_di_login(conn, tmp_path):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    _utente(conn, 'spento@alfa.it', 'utente', sid, attivo=0)
    (tmp_path / 'uploads').mkdir(exist_ok=True)

    esiti = _diagnostica(conn, tmp_path, {'uploads_path': str(tmp_path / 'uploads')})
    avviso = [e for e in esiti if e.titolo == 'Utenti disattivati']
    assert avviso and avviso[0].gravita == 'avviso'
    assert 'spento@alfa.it' in avviso[0].dettaglio
    # Un avviso non deve alterare il codice di uscita.
    assert not diagnosi.ci_sono_errori(esiti)


def test_blocco_per_tentativi_ripetuti_segnalato(conn, tmp_path):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    for _ in range(5):
        conn.execute("INSERT INTO login_attempts (ip_address, email, esito) "
                     "VALUES ('10.0.0.1', 'admin@alfa.it', 'fallito')")
    conn.commit()

    esiti = _diagnostica(conn, tmp_path)
    assert 'Accessi bloccati per tentativi ripetuti' in _titoli(esiti)


def test_migrazioni_pendenti_sono_un_errore(conn, tmp_path):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    fotografia = stato.raccogli(conn, {}, str(tmp_path))
    fotografia['schema']['pendenti'] = ['v2.3']

    esiti = diagnosi.esegui(conn, {}, fotografia)
    pendenti = [e for e in esiti if e.titolo == 'Migrazioni non applicate']
    assert pendenti and pendenti[0].gravita == 'errore'
    assert 'migra' in pendenti[0].rimedio


def test_modalita_incoerente_col_numero_di_strutture(conn, tmp_path):
    sid = _struttura(conn, 'Alfa')
    _struttura(conn, 'Beta')
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    _utente(conn, 'admin@beta.it', 'admin', 2)

    esiti = _diagnostica(conn, tmp_path, config={'single_struttura': True})
    assert "Modalita' incoerente" in _titoli(esiti)


def test_un_controllo_che_esplode_non_ferma_gli_altri(conn, tmp_path, monkeypatch):
    def esplode(conn, config, fotografia):
        raise sqlite3.OperationalError('no such table: fantasma')

    monkeypatch.setattr(diagnosi, 'CONTROLLI',
                        (esplode, diagnosi.controllo_nessun_utente_attivo))
    esiti = _diagnostica(conn, tmp_path)
    titoli = _titoli(esiti)
    assert 'Controllo fallito: esplode' in titoli
    assert 'Nessun utente attivo' in titoli


# ---------------------------------------------------------------------------
# operazioni
# ---------------------------------------------------------------------------

def test_su_uno_schema_vecchio_i_controlli_degradano_invece_di_esplodere(conn,
                                                                          tmp_path):
    """Il database di un'installazione v1.x e' il caso da ispezionare, non un
    caso limite. Un controllo che esplode li' non dice nulla, e il rimedio
    non e' una segnalazione: e' la migrazione."""
    conn.execute('ALTER TABLE utenti DROP COLUMN eliminato_il')
    conn.commit()
    _struttura(conn, 'Alfa')
    _utente(conn, 'spento@alfa.it', 'admin', 1, attivo=0)

    fotografia = stato.raccogli(conn, {}, str(tmp_path))
    fotografia['schema']['pendenti'] = ['v2.3']
    esiti = diagnosi.esegui(conn, {}, fotografia)

    falliti = [e for e in esiti if e.titolo.startswith('Controllo fallito')]
    assert falliti == [], [e.dettaglio for e in falliti]
    # E il controllo che poteva ancora dire qualcosa lo dice.
    assert 'Utenti disattivati' in _titoli(esiti)


def test_un_controllo_che_esplode_su_schema_vecchio_rimanda_alla_migrazione(
        conn, tmp_path, monkeypatch):
    def esplode(conn, config, fotografia):
        raise sqlite3.OperationalError('no such column: fantasma')

    monkeypatch.setattr(diagnosi, 'CONTROLLI', (esplode,))
    fotografia = stato.raccogli(conn, {}, str(tmp_path))
    fotografia['schema']['pendenti'] = ['v2.3']

    esito = diagnosi.esegui(conn, {}, fotografia)[0]
    assert esito.rimedio == 'python manutenzione.py migra'


def test_il_backup_di_sicurezza_e_una_copia_apribile(conn, app):
    _struttura(conn, 'Alfa')
    conn.commit()

    copia = operazioni.backup_di_sicurezza(app.config['DATABASE_PATH'])

    assert os.path.exists(copia)
    assert 'bak_manutenzione_' in os.path.basename(copia)
    altra = sqlite3.connect(copia)
    assert altra.execute('SELECT nome FROM strutture').fetchone()[0] == 'Alfa'
    altra.close()


def test_gli_adattatori_riusano_gli_script_esistenti():
    """Se qualcuno rinomina una funzione negli script, deve rompersi qui e
    non a runtime davanti all'operatore."""
    import backup_service
    import migrate
    import pulisci_uploads
    import toggle_modalita

    for modulo, nome in (
        (migrate, 'analyze'), (migrate, 'apply_all'), (migrate, 'describe_version'),
        (migrate, 'load_db_path'), (migrate, 'load_config'), (migrate, 'MIGRATIONS'),
        (pulisci_uploads, 'percorsi_referenziati'), (pulisci_uploads, 'trova_orfani'),
        (pulisci_uploads, 'elimina_file'),
        (toggle_modalita, 'stato_attuale'), (toggle_modalita, 'scrivi_config'),
        (toggle_modalita, 'leggi_config'),
        (backup_service, 'create_backup'), (backup_service, 'list_backups'),
        (backup_service, 'restore_backup'),
    ):
        assert hasattr(modulo, nome), f"{modulo.__name__}.{nome} non esiste piu'"


# ---------------------------------------------------------------------------
# entry point e subcomandi
# ---------------------------------------------------------------------------

import manutenzione as cli  # noqa: E402


@pytest.fixture
def cli_config(tmp_path, monkeypatch):
    """Isola i comandi dalla configurazione reale dello sviluppatore."""
    (tmp_path / 'uploads').mkdir(exist_ok=True)
    config = {'uploads_path': str(tmp_path / 'uploads'),
              'backups_path': str(tmp_path / 'backups')}
    monkeypatch.setattr(cli.operazioni, 'carica_config', lambda: config)
    return config


def test_stato_json_e_leggibile_da_una_macchina(conn, app, capsys, cli_config):
    _struttura(conn, 'Alfa')

    codice = cli.main(['--db', app.config['DATABASE_PATH'], 'stato', '--json'])

    assert codice == 0
    reso = _json.loads(capsys.readouterr().out)
    assert reso['modalita']['strutture'] == 1


def test_diagnosi_esce_con_uno_se_c_e_un_errore(conn, app, capsys, cli_config):
    _struttura(conn, 'Alfa')  # nessun utente: errore

    codice = cli.main(['--db', app.config['DATABASE_PATH'], 'diagnosi'])

    assert codice == 1
    assert 'Nessun utente attivo' in capsys.readouterr().out


def test_diagnosi_esce_con_zero_quando_ci_sono_solo_avvisi(conn, app, capsys,
                                                           cli_config):
    sid = _struttura(conn, 'Alfa')
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    _utente(conn, 'spento@alfa.it', 'utente', sid, attivo=0)

    assert cli.main(['--db', app.config['DATABASE_PATH'], 'diagnosi']) == 0


def test_un_database_inesistente_non_produce_traceback(capsys, tmp_path, cli_config):
    codice = cli.main(['--db', str(tmp_path / 'non-esiste.sqlite'), 'stato'])
    assert codice == 1
    assert 'seed.py' in capsys.readouterr().out


def test_utenti_elenca_mostra_lo_stato_delle_impronte(conn, app, capsys, cli_config):
    sid = _struttura(conn, 'Alfa')
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    _utente(conn, 'vecchio@alfa.it', 'utente', sid,
            password_hash='sha256$sale$impronta')

    assert cli.main(['--db', app.config['DATABASE_PATH'], 'utenti', 'elenca']) == 0
    uscita = capsys.readouterr().out
    assert 'vecchio@alfa.it' in uscita
    assert 'metodo_sconosciuto' in uscita


def test_utenti_azzera_senza_rimpiazzo_rifiuta_e_non_scrive(conn, app, cli_config):
    sid = _struttura(conn, 'Alfa')
    _utente(conn, 'admin@alfa.it', 'admin', sid)

    codice = cli.main(['--db', app.config['DATABASE_PATH'],
                       'utenti', 'azzera', '-y'])

    assert codice == 1
    assert conn.execute('SELECT email FROM utenti').fetchone()['email'] == 'admin@alfa.it'


def test_utenti_azzera_con_rimpiazzo_funziona_senza_domande(conn, app, cli_config,
                                                            monkeypatch):
    sid = _struttura(conn, 'Alfa')
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    monkeypatch.setattr(cli, 'chiedi_password', lambda _e: 'Password1')

    codice = cli.main(['--db', app.config['DATABASE_PATH'], 'utenti', 'azzera',
                       '-y', '--nuovo-admin', 'nuovo@alfa.it'])

    assert codice == 0
    righe = {r['email'] for r in conn.execute('SELECT email FROM utenti')}
    assert 'nuovo@alfa.it' in righe
    assert 'admin@alfa.it' not in righe


def test_utenti_password_reimposta_e_riattiva(conn, app, cli_config, monkeypatch):
    from werkzeug.security import check_password_hash
    sid = _struttura(conn, 'Alfa')
    _utente(conn, 'spento@alfa.it', 'admin', sid, attivo=0)
    monkeypatch.setattr(cli, 'chiedi_password', lambda _e: 'NuovaPassword1')

    codice = cli.main(['--db', app.config['DATABASE_PATH'],
                       'utenti', 'password', 'spento@alfa.it'])

    assert codice == 0
    riga = conn.execute('SELECT password_hash, attivo FROM utenti '
                        'WHERE email = ?', ('spento@alfa.it',)).fetchone()
    assert riga['attivo'] == 1
    assert check_password_hash(riga['password_hash'], 'NuovaPassword1')


def test_utenti_password_senza_indirizzo_e_un_errore_d_uso(capsys, cli_config):
    assert cli.main(['utenti', 'password']) == 2
    assert 'indirizzo' in capsys.readouterr().out


# ---------------------------------------------------------------------------
# menu
# ---------------------------------------------------------------------------

def test_il_menu_mostra_stato_diagnosi_e_voci_ed_esce_con_q(conn, app, capsys,
                                                            cli_config, monkeypatch):
    sid = _struttura(conn, 'Alfa')
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    monkeypatch.setattr('builtins.input', lambda *_a: 'q')

    codice = cli.main(['--db', app.config['DATABASE_PATH']])

    uscita = capsys.readouterr().out
    assert codice == 0
    assert 'Stato installazione' in uscita
    assert 'Diagnosi' in uscita
    assert 'Utenti e accessi' in uscita


def test_il_menu_esce_pulito_su_interruzione(conn, app, capsys, cli_config,
                                             monkeypatch):
    _struttura(conn, 'Alfa')

    def interrompi(*_a):
        raise KeyboardInterrupt

    monkeypatch.setattr('builtins.input', interrompi)
    assert cli.main(['--db', app.config['DATABASE_PATH']]) == 0
    assert 'Traceback' not in capsys.readouterr().out


def test_una_scelta_ignota_non_chiude_il_menu(conn, app, capsys, cli_config,
                                              monkeypatch):
    _struttura(conn, 'Alfa')
    risposte = iter(['zzz', 'q'])
    monkeypatch.setattr('builtins.input', lambda *_a: next(risposte))

    assert cli.main(['--db', app.config['DATABASE_PATH']]) == 0
    assert 'Scelta non riconosciuta' in capsys.readouterr().out


# ---------------------------------------------------------------------------
# crea_superadmin
# ---------------------------------------------------------------------------

def test_crea_superadmin_delega_allo_strumento_unificato(monkeypatch):
    """Lo script storico resta, ma smette di avere una logica sua.

    Due implementazioni della stessa cosa divergono: era gia' successo con la
    validazione della password, che qui c'era e altrove no.
    """
    import crea_superadmin
    chiamate = []
    monkeypatch.setattr(crea_superadmin, '_esegui',
                        lambda argv: chiamate.append(argv) or 0)

    assert crea_superadmin.main() == 0
    assert chiamate == [['utenti', 'superadmin']]


def test_crea_superadmin_conserva_valida_password():
    import crea_superadmin
    assert crea_superadmin.valida_password('corta') != []
    assert crea_superadmin.valida_password('Password1') == []


def test_la_versione_e_coerente_ovunque():
    """config.json e APP_VERSION devono dire la stessa cosa.

    Sono due file letti da percorsi diversi: quando divergono, l'interfaccia
    mostra una versione e il controllo aggiornamenti un'altra.
    """
    import app as modulo_app
    with open(os.path.join(RADICE, 'config.json'), encoding='utf-8') as f:
        config = _json.load(f)
    assert config['version'] == modulo_app.APP_VERSION == '2.6.3'
