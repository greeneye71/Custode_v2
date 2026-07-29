"""Rotte di gestione delle strutture. Tutte riservate al superadmin."""
import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def dati(app):
    from models import execute
    with app.app_context():
        s = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        spenta = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Chiusa','CH',0)").lastrowid
        d = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Oculistica','OCU',?)", (s,)).lastrowid
        execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato) "
                "VALUES (?,?,'OCU-1','REXXAM','OZY','funzionante')", (d, s))
        hash_pw = generate_password_hash('Passw0rd!')
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,primo_accesso) "
                "VALUES ('super@x.it',?,'S','S','superadmin',0)", (hash_pw,))
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
                "VALUES ('admin@a.it',?,'A','A','admin',?,0)", (hash_pw, s))
    return {'s': s, 'spenta': spenta}


def entra(client, email):
    client.post('/login', data={'email': email, 'password': 'Passw0rd!'})


def test_la_scheda_mostra_il_contenuto(client, dati):
    entra(client, 'super@x.it')
    risposta = client.get(f"/strutture/{dati['s']}")
    assert risposta.status_code == 200
    testo = risposta.get_data(as_text=True)
    assert 'Clinica A' in testo
    assert 'Oculistica' in testo


def test_la_scheda_e_negata_a_un_admin(client, dati):
    # Non si puo' verificare l'assenza cercando 'Oculistica': l'admin ha quella
    # divisione come propria, e il menu di base.html la mostra in ogni pagina
    # (compresa la dashboard di redirect), a prescindere dall'accesso alla
    # scheda. Si verifica invece l'assenza di un testo che compare solo dentro
    # scheda.html, cosi' un decoratore indebolito che lasciasse passare
    # l'admin verrebbe comunque rilevato.
    entra(client, 'admin@a.it')
    risposta = client.get(f"/strutture/{dati['s']}", follow_redirects=True)
    assert 'Tecnici assegnati' not in risposta.get_data(as_text=True)


def test_riattivazione(client, app, dati):
    from models import query_one
    entra(client, 'super@x.it')
    risposta = client.post(f"/strutture/{dati['spenta']}/riattiva", follow_redirects=True)
    assert risposta.status_code == 200
    with app.app_context():
        assert query_one("SELECT attiva FROM strutture WHERE id=?", (dati['spenta'],))['attiva'] == 1


def test_riattivazione_negata_a_un_admin(client, app, dati):
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/strutture/{dati['spenta']}/riattiva", follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT attiva FROM strutture WHERE id=?", (dati['spenta'],))['attiva'] == 0
