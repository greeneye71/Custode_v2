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
    riga sbagliata" e' che la pagina dica di chi si tratta."""
    entra(client, 'admin@a.it')
    testo = client.get(f"/admin/utenti/{dati['mario']}/elimina").get_data(as_text=True)
    assert 'mario@a.it' in testo
    assert 'Rossi' in testo


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
