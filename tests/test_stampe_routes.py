"""Test delle rotte di stampa: contano soprattutto i confini di visibilita'."""
import re

import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def dati(app):
    """Due strutture, due divisioni nella prima, un utente assegnato a una sola."""
    from models import execute
    with app.app_context():
        s1 = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        s2 = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica B','B',1)").lastrowid
        d1 = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Oculistica','OCU',?)", (s1,)).lastrowid
        d2 = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Cardiologia','CAR',?)", (s1,)).lastrowid
        dx = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Altrui','ALT',?)", (s2,)).lastrowid
        hash_pw = generate_password_hash('Passw0rd!')
        admin = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('admin@a.it',?,'A','A','admin',?,0)", (hash_pw, s1)).lastrowid
        utente = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('utente@a.it',?,'U','U','utente',?,0)", (hash_pw, s1)).lastrowid
        execute("INSERT INTO utenti_divisioni (utente_id,divisione_id,ruolo_divisione) VALUES (?,?,'utente')",
                (utente, d1))
        execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato,ubicazione) "
                "VALUES (?,?,'OCU-1','REXXAM','OZY','funzionante','Sala 1')", (d1, s1))
        execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato,ubicazione) "
                "VALUES (?,?,'CAR-1','GE','B40','funzionante','Sala 2')", (d2, s1))
    return {'s1': s1, 's2': s2, 'd1': d1, 'd2': d2, 'dx': dx}


def entra(client, email):
    client.post('/login', data={'email': email, 'password': 'Passw0rd!'})


def test_la_pagina_stampe_risponde(client, dati):
    entra(client, 'admin@a.it')
    risposta = client.get('/stampe')
    assert risposta.status_code == 200


def test_admin_ottiene_l_inventario_di_struttura(client, dati):
    entra(client, 'admin@a.it')
    risposta = client.get('/stampe/inventario?divisione_id=tutte')
    assert risposta.status_code == 200
    assert risposta.data.startswith(b'%PDF')


def test_utente_non_ottiene_una_divisione_non_sua(client, dati):
    entra(client, 'utente@a.it')
    risposta = client.get(f"/stampe/inventario?divisione_id={dati['d2']}")
    assert not risposta.data.startswith(b'%PDF')


def test_nessuno_ottiene_una_divisione_di_un_altra_struttura(client, dati):
    entra(client, 'admin@a.it')
    risposta = client.get(f"/stampe/inventario?divisione_id={dati['dx']}")
    assert not risposta.data.startswith(b'%PDF')


def test_divisione_inesistente_non_produce_un_pdf(client, dati):
    entra(client, 'admin@a.it')
    risposta = client.get('/stampe/inventario?divisione_id=999999')
    assert not risposta.data.startswith(b'%PDF')


def test_superadmin_senza_struttura_riceve_una_spiegazione(client, app, dati):
    """Senza struttura impersonata non c'e' un ambito su cui stampare: la pagina
    deve dirlo, non generare un PDF vuoto."""
    from models import execute
    from werkzeug.security import generate_password_hash
    with app.app_context():
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,"
                "struttura_id,primo_accesso) VALUES ('super@a.it',?,'S','S',"
                "'superadmin',NULL,0)", (generate_password_hash('Passw0rd!'),))
    entra(client, 'super@a.it')

    pagina = client.get('/stampe', follow_redirects=True)
    assert 'contesto di una struttura' in pagina.get_data(as_text=True)

    risposta = client.get('/stampe/inventario?divisione_id=tutte')
    assert not risposta.data.startswith(b'%PDF')
