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


def test_esportazione_dalla_scheda(client, app, dati):
    import os
    entra(client, 'super@x.it')
    risposta = client.post(f"/strutture/{dati['s']}/esporta", follow_redirects=True)
    assert risposta.status_code == 200
    with app.app_context():
        from flask import current_app
        radice = os.path.join(current_app.config['BACKUPS_PATH'], 'strutture')
        assert os.path.isdir(radice)
        assert len(os.listdir(radice)) == 1


def test_esportazione_negata_a_un_admin(client, app, dati):
    """Come le altre operazioni sull'intero ciclo di vita di una struttura,
    l'esportazione e' riservata al superadmin: un admin vede solo la propria
    struttura e non deve poter produrre un archivio scaricabile."""
    import os
    entra(client, 'admin@a.it')
    client.post(f"/strutture/{dati['s']}/esporta", follow_redirects=True)
    with app.app_context():
        from flask import current_app
        radice = os.path.join(current_app.config['BACKUPS_PATH'], 'strutture')
        assert not os.path.isdir(radice) or len(os.listdir(radice)) == 0


# ---------------------------------------------------------------------------
# Cancellazione
# ---------------------------------------------------------------------------

def test_la_pagina_di_conferma_richiede_la_struttura_disattivata(client, dati):
    """Il freno vale anche sul GET: non basta bloccare la POST se la pagina
    di conferma restasse comunque raggiungibile su una struttura attiva."""
    entra(client, 'super@x.it')
    risposta = client.get(f"/strutture/{dati['s']}/elimina", follow_redirects=True)
    testo = risposta.get_data(as_text=True)
    assert 'disattiva' in testo.lower()
    assert 'Cancella definitivamente' not in testo


def test_la_pagina_di_conferma_mostra_il_contenuto(client, dati):
    entra(client, 'super@x.it')
    risposta = client.get(f"/strutture/{dati['spenta']}/elimina")
    assert risposta.status_code == 200
    testo = risposta.get_data(as_text=True)
    assert 'Chiusa' in testo
    assert 'Cancella definitivamente' in testo


def test_la_pagina_di_conferma_e_negata_a_un_admin(client, dati):
    entra(client, 'admin@a.it')
    risposta = client.get(f"/strutture/{dati['spenta']}/elimina", follow_redirects=True)
    assert 'Cancella definitivamente' not in risposta.get_data(as_text=True)


def test_la_cancellazione_richiede_la_struttura_disattivata(client, app, dati):
    from models import query_one
    entra(client, 'super@x.it')
    risposta = client.post(f"/strutture/{dati['s']}/elimina",
                           data={'conferma_nome': 'Clinica A'}, follow_redirects=True)
    assert 'disattiva' in risposta.get_data(as_text=True).lower()
    with app.app_context():
        assert query_one("SELECT id FROM strutture WHERE id=?", (dati['s'],)) is not None


def test_la_cancellazione_richiede_il_nome_esatto(client, app, dati):
    from models import execute, query_one
    entra(client, 'super@x.it')
    with app.app_context():
        execute("UPDATE strutture SET attiva=0 WHERE id=?", (dati['s'],))
    risposta = client.post(f"/strutture/{dati['s']}/elimina",
                           data={'conferma_nome': 'clinica'}, follow_redirects=True)
    assert 'nome' in risposta.get_data(as_text=True).lower()
    with app.app_context():
        assert query_one("SELECT id FROM strutture WHERE id=?", (dati['s'],)) is not None


def test_la_cancellazione_riuscita(client, app, dati):
    from models import execute, query_one
    entra(client, 'super@x.it')
    with app.app_context():
        execute("UPDATE strutture SET attiva=0 WHERE id=?", (dati['s'],))
    risposta = client.post(f"/strutture/{dati['s']}/elimina",
                           data={'conferma_nome': 'Clinica A'}, follow_redirects=True)
    assert risposta.status_code == 200
    with app.app_context():
        assert query_one("SELECT id FROM strutture WHERE id=?", (dati['s'],)) is None
        assert query_one("SELECT id FROM utenti WHERE email='admin@a.it'") is None
        assert query_one("SELECT id FROM utenti WHERE email='super@x.it'") is not None


def test_la_cancellazione_e_negata_a_un_admin(client, app, dati):
    from models import execute, query_one
    with app.app_context():
        execute("UPDATE strutture SET attiva=0 WHERE id=?", (dati['s'],))
    entra(client, 'admin@a.it')
    client.post(f"/strutture/{dati['s']}/elimina",
                data={'conferma_nome': 'Clinica A'}, follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT id FROM strutture WHERE id=?", (dati['s'],)) is not None


def test_la_scheda_di_una_struttura_attiva_non_mostra_il_pulsante_elimina(client, dati):
    """Il pulsante Elimina compare solo per una struttura gia' disattivata:
    su una attiva la scheda deve spiegare perche' non c'e', non nasconderlo
    e basta (altrimenti un ramo del template smesso di rendersi passerebbe
    inosservato)."""
    entra(client, 'super@x.it')
    risposta = client.get(f"/strutture/{dati['s']}")
    testo = risposta.get_data(as_text=True)
    assert f"/strutture/{dati['s']}/elimina" not in testo
    assert 'disattivala prima' in testo.lower()


def test_la_scheda_di_una_struttura_disattivata_mostra_il_pulsante_elimina(client, dati):
    entra(client, 'super@x.it')
    risposta = client.get(f"/strutture/{dati['spenta']}")
    testo = risposta.get_data(as_text=True)
    assert f"/strutture/{dati['spenta']}/elimina" in testo
    assert 'Elimina' in testo


def test_la_scheda_nasconde_il_pulsante_elimina_in_modalita_single(client, app, dati):
    """single_struttura arriva alla scheda dalla config dell'installazione
    (Task 5): in quella modalita' il piano nasconde il pulsante Elimina
    anche su una struttura disattivata."""
    app.config['APP_CONFIG']['single_struttura'] = True
    entra(client, 'super@x.it')
    risposta = client.get(f"/strutture/{dati['spenta']}")
    testo = risposta.get_data(as_text=True)
    assert f"/strutture/{dati['spenta']}/elimina" not in testo
