"""Test delle rotte di stampa: contano soprattutto i confini di visibilita'."""
import io

import pytest
from pypdf import PdfReader
from werkzeug.security import generate_password_hash


def testo_di(pdf_bytes):
    """Estrae il testo di tutte le pagine di un PDF (vedi test_report_service.py)."""
    lettore = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(pagina.extract_text() for pagina in lettore.pages)


def _assert_rifiuto_pulito(client, url, messaggio):
    """Verifica che l'accesso fuori scope sia un redirect esplicito verso
    /stampe con un flash che spiega il motivo, non un 404/500 mascherato da
    un body che 'per caso' non comincia con %PDF."""
    risposta = client.get(url)
    assert risposta.status_code == 302
    assert risposta.headers['Location'] in ('/stampe', '/stampe/')

    pagina = client.get(url, follow_redirects=True)
    assert messaggio in pagina.get_data(as_text=True)


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
        execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('admin@a.it',?,'A','A','admin',?,0)", (hash_pw, s1))
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


def test_la_pagina_stampe_risponde_anche_con_slash_finale(client, dati):
    entra(client, 'admin@a.it')
    risposta = client.get('/stampe/')
    assert risposta.status_code == 200


def test_admin_ottiene_l_inventario_di_struttura(client, dati):
    entra(client, 'admin@a.it')
    risposta = client.get('/stampe/inventario?divisione_id=tutte')
    assert risposta.status_code == 200
    assert risposta.data.startswith(b'%PDF')


def test_utente_non_ottiene_una_divisione_non_sua(client, dati):
    entra(client, 'utente@a.it')
    _assert_rifiuto_pulito(client, f"/stampe/inventario?divisione_id={dati['d2']}",
                            'Divisione non disponibile.')


def test_nessuno_ottiene_una_divisione_di_un_altra_struttura(client, dati):
    entra(client, 'admin@a.it')
    _assert_rifiuto_pulito(client, f"/stampe/inventario?divisione_id={dati['dx']}",
                            'Divisione non disponibile.')


def test_divisione_inesistente_non_produce_un_pdf(client, dati):
    entra(client, 'admin@a.it')
    _assert_rifiuto_pulito(client, '/stampe/inventario?divisione_id=999999',
                            'Divisione non disponibile.')


def test_utente_vede_solo_la_propria_divisione_nell_inventario_generale(client, dati):
    """Garanzia centrale: per il ruolo 'utente', 'tutte le divisioni' significa
    solo le sue, non l'intera struttura."""
    entra(client, 'utente@a.it')
    risposta = client.get('/stampe/inventario?divisione_id=tutte')
    assert risposta.status_code == 200
    testo = testo_di(risposta.data)
    assert 'OCU-1' in testo
    assert 'CAR-1' not in testo


def test_admin_vede_tutte_le_divisioni_nell_inventario_generale(client, dati):
    """Simmetrico al precedente: per admin/tecnico/superadmin 'tutte le
    divisioni' significa l'intera struttura."""
    entra(client, 'admin@a.it')
    risposta = client.get('/stampe/inventario?divisione_id=tutte')
    assert risposta.status_code == 200
    testo = testo_di(risposta.data)
    assert 'OCU-1' in testo
    assert 'CAR-1' in testo


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

    _assert_rifiuto_pulito(client, '/stampe/inventario?divisione_id=tutte',
                            'Nessuna divisione accessibile.')
