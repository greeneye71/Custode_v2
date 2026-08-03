"""Le rotte della cancellazione utenti: chi puo', cosa si rifiuta, cosa resta."""
import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def dati(app):
    from models import execute
    with app.app_context():
        a = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        b = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica B','B',1)").lastrowid
        da = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Ocu','OCU',?)",
                     (a,)).lastrowid
        h = generate_password_hash('Passw0rd!')
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,primo_accesso) "
                "VALUES ('super@x.it',?,'S','S','superadmin',0)", (h,))
        admin_a = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('admin@a.it',?,'A','A','admin',?,0)", (h, a)).lastrowid
        secondo = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('admin2@a.it',?,'A','Due','admin',?,0)", (h, a)).lastrowid
        mario = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('mario@a.it',?,'M','Rossi','utente',?,0)", (h, a)).lastrowid
        altrui = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('utente@b.it',?,'U','B','utente',?,0)", (h, b)).lastrowid
        tec = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('tec@x.it',?,'T','T','tecnico',NULL,0)", (h,)).lastrowid
        execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,"
                "stato,created_by) VALUES (?,?,'M-1','REXXAM','OZY','funzionante',?)",
                (da, a, mario))
    return {'a': a, 'admin_a': admin_a, 'secondo': secondo, 'mario': mario,
            'altrui': altrui, 'tec': tec}


def entra(client, email):
    client.post('/login', data={'email': email, 'password': 'Passw0rd!'})


def test_la_pagina_di_conferma_dice_chi_si_sta_cancellando(client, dati):
    """Non si digita nulla per confermare: l'unica difesa contro "ho cliccato la
    riga sbagliata" e' che la pagina dica di chi si tratta — nome, email e
    struttura. Senza l'ultima asserzione, ripristinare la query letterale del
    brief (senza il LEFT JOIN su strutture) farebbe comunque passare tutti gli
    altri test: e' questa a difendere la deviazione."""
    entra(client, 'admin@a.it')
    testo = client.get(f"/admin/utenti/{dati['mario']}/elimina").get_data(as_text=True)
    assert 'mario@a.it' in testo
    assert 'Rossi' in testo
    assert 'Clinica A' in testo


def test_la_pagina_di_conferma_dice_cosa_resta(client, dati):
    """I tre punti che la pagina deve dire prima del pulsante: che non si torna
    indietro, che il nome resta sulle schede inserite, che l'indirizzo si
    libera. Le tre asserzioni separate, cosi' se ne sparisce una si sa quale."""
    entra(client, 'admin@a.it')
    testo = client.get(f"/admin/utenti/{dati['mario']}/elimina").get_data(as_text=True)
    assert 'reversibile' in testo.lower()
    assert 'resta' in testo.lower()
    assert 'libero' in testo.lower()


def test_la_cancellazione_riuscita(client, app, dati):
    from models import query_one
    entra(client, 'admin@a.it')
    r = client.post(f"/admin/utenti/{dati['mario']}/elimina", follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        riga = query_one("SELECT email, eliminato_il FROM utenti WHERE id=?", (dati['mario'],))
        assert riga['eliminato_il'] is not None
        assert riga['email'] != 'mario@a.it'


def test_l_ultimo_admin_non_si_cancella_dalla_rotta(client, app, dati):
    from models import query_one
    entra(client, 'super@x.it')
    client.post(f"/admin/utenti/{dati['secondo']}/elimina", follow_redirects=True)
    r = client.post(f"/admin/utenti/{dati['admin_a']}/elimina", follow_redirects=True)
    assert 'amministratore' in r.get_data(as_text=True).lower()
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['admin_a'],))['eliminato_il'] is None


def test_nessuno_cancella_se_stesso(client, app, dati):
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['admin_a']}/elimina", follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['admin_a'],))['eliminato_il'] is None


def test_un_admin_non_cancella_utenti_di_altre_strutture(client, app, dati):
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['altrui']}/elimina", follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['altrui'],))['eliminato_il'] is None


def test_un_admin_non_cancella_un_tecnico(client, app, dati):
    """Un tecnico e' un account condiviso fra strutture, non proprieta' di una."""
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['tec']}/elimina", follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['tec'],))['eliminato_il'] is None


def test_il_registro_conserva_l_email_originale(client, app, dati):
    """Dopo la cancellazione nel database c'e' solo la forma spostata."""
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['mario']}/elimina", follow_redirects=True)
    with app.app_context():
        voce = query_one("SELECT dettagli, struttura_id FROM log_attivita "
                         "WHERE azione='eliminazione' AND entita='utenti'")
        assert voce is not None
        assert 'mario@a.it' in voce['dettagli']
        assert voce['struttura_id'] == dati['a']


def test_un_utente_cancellato_non_entra_piu(client, app, dati):
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['mario']}/elimina", follow_redirects=True)
    client.get('/logout')
    r = client.post('/login', data={'email': 'mario@a.it', 'password': 'Passw0rd!'},
                    follow_redirects=True)
    assert 'dashboard' not in r.get_data(as_text=True).lower()


def test_un_superadmin_non_cancella_un_tecnico(client, app, dati):
    """Per un admin, il tecnico e' gia' fermato da _check_utente_scope (il suo
    struttura_id nullo non coincide con nessuna struttura). Per un superadmin
    _check_utente_scope restituisce sempre True: qui il controllo esplicito
    sul ruolo 'tecnico' in _utente_cancellabile e' l'UNICA difesa, e questo
    test e' l'unico a coprirla."""
    from models import query_one
    entra(client, 'super@x.it')
    client.post(f"/admin/utenti/{dati['tec']}/elimina", follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['tec'],))['eliminato_il'] is None


def test_un_guasto_dopo_la_registrazione_non_dice_che_nulla_e_cambiato(client, app, dati, monkeypatch):
    """Il vero punto di non ritorno e' dentro log_attivita: passa da
    models.execute(), che fa gia' db.commit() sulla stessa connessione. Un
    guasto DOPO quella chiamata (qui simulato facendo esplodere log_attivita
    subito dopo aver fatto il suo lavoro vero) trova l'utente gia' cancellato
    in modo durevole: il messaggio all'operatore deve dirlo, non deve
    affermare che "nulla e' stato modificato"."""
    import admin as modulo_admin
    from models import query_one

    log_vero = modulo_admin.log_attivita

    def registra_e_poi_esplodi(*args, **kwargs):
        log_vero(*args, **kwargs)
        raise RuntimeError('guasto simulato dopo la registrazione')

    monkeypatch.setattr(modulo_admin, 'log_attivita', registra_e_poi_esplodi)

    entra(client, 'admin@a.it')
    r = client.post(f"/admin/utenti/{dati['mario']}/elimina", follow_redirects=True)
    testo = r.get_data(as_text=True)

    # La cancellazione e' davvero avvenuta (la registrazione l'ha resa
    # durevole prima del guasto simulato) ...
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['mario'],))['eliminato_il'] is not None
    # ... quindi il messaggio mostrato non puo' essere quello per il caso in
    # cui non e' successo nulla (severita' 'danger', frase "fallita, nulla") ...
    assert 'alert-danger' not in testo
    assert 'fallita, nulla' not in testo
    # ... deve invece avvisare con severita' 'warning' che l'operazione e'
    # avvenuta ma qualcosa e' andato storto dopo.
    assert 'alert-warning' in testo
    assert 'cancellato' in testo.lower()
    assert 'subito dopo' in testo.lower()


def _cancella(client, id):
    client.post(f"/admin/utenti/{id}/elimina", follow_redirects=True)


def test_non_compare_nell_elenco_utenti_dell_admin(client, dati):
    entra(client, 'admin@a.it')
    _cancella(client, dati['mario'])
    assert 'mario@a.it' not in client.get('/admin/utenti').get_data(as_text=True)


def test_non_compare_nell_elenco_utenti_del_superadmin(client, dati):
    entra(client, 'admin@a.it')
    _cancella(client, dati['mario'])
    client.get('/logout')
    entra(client, 'super@x.it')
    assert 'mario@a.it' not in client.get('/admin/utenti').get_data(as_text=True)


def test_un_tecnico_cancellato_non_compare_nell_elenco_tecnici(client, app, dati):
    """La rotta /admin/utenti/<id>/elimina rifiuta SEMPRE un tecnico, anche per
    un superadmin (e' l'unica difesa verificata da
    test_un_superadmin_non_cancella_un_tecnico in questo stesso file): non e'
    la sua via di cancellazione, quindi _cancella() qui non produrrebbe nulla
    da filtrare. Cio' che questo test deve coprire e' il filtro della lista
    tecnici, non l'autorizzazione (gia' coperta altrove): si usa percio' la
    primitiva cancella_utente direttamente, che a differenza della rotta non
    discrimina per ruolo."""
    from utente_service import cancella_utente
    from models import get_db
    entra(client, 'super@x.it')
    with app.app_context():
        conn = get_db()
        cancella_utente(conn, dati['tec'])
        conn.commit()
    assert 'tec@x.it' not in client.get('/admin/tecnici').get_data(as_text=True)


def test_non_compare_nella_scheda_della_struttura(client, dati):
    entra(client, 'admin@a.it')
    _cancella(client, dati['mario'])
    client.get('/logout')
    entra(client, 'super@x.it')
    assert 'mario@a.it' not in client.get(f"/strutture/{dati['a']}").get_data(as_text=True)


def test_non_e_contato_fra_gli_utenti_della_struttura(client, app, dati):
    """contenuto_struttura conta gli utenti prima di cancellare una struttura:
    contarne di cancellati direbbe all'operatore un numero che non esiste."""
    from struttura_service import contenuto_struttura
    from models import get_db
    entra(client, 'admin@a.it')
    with app.app_context():
        prima = contenuto_struttura(get_db(), dati['a'], '/tmp/x')['utenti']
    _cancella(client, dati['mario'])
    with app.app_context():
        dopo = contenuto_struttura(get_db(), dati['a'], '/tmp/x')['utenti']
    assert dopo == prima - 1
