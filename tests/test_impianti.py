"""Impianti: schema, isolamento, piano di manutenzione, avvisi."""
import pytest
from werkzeug.security import generate_password_hash

from models import execute, query_one, query_all


@pytest.fixture
def ambiente(app):
    """Due strutture con una divisione e un admin ciascuna.

    Modellata su tests/test_isolamento.py: le righe si inseriscono con
    execute() dentro un app_context, senza passare dalle rotte.
    """
    with app.app_context():
        dati = {}
        for chiave, nome, codice, email in (
            ('a', 'Clinica A', 'CLA', 'admin.a@test.it'),
            ('b', 'Clinica B', 'CLB', 'admin.b@test.it'),
        ):
            sid = execute(
                "INSERT INTO strutture (nome, codice, attiva, email_notifiche,"
                " email_responsabile) VALUES (?, ?, 1, ?, ?)",
                (nome, codice, f'notifiche.{chiave}@test.it',
                 f'responsabile.{chiave}@test.it')
            ).lastrowid
            did = execute(
                "INSERT INTO divisioni (struttura_id, nome, codice, email)"
                " VALUES (?, ?, ?, ?)",
                (sid, f'Divisione {chiave.upper()}', f'DIV-{chiave.upper()}',
                 f'divisione.{chiave}@test.it')
            ).lastrowid
            uid = execute(
                "INSERT INTO utenti (struttura_id, nome, cognome, email,"
                " password_hash, ruolo, attivo) VALUES (?, ?, ?, ?, ?, 'admin', 1)",
                (sid, 'Admin', chiave.upper(), email,
                 generate_password_hash('Passw0rd!'))
            ).lastrowid
            dati[chiave] = {'struttura': sid, 'divisione': did,
                            'utente': uid, 'email': email}
        return dati


def entra(client, email):
    """Login con la password della fixture."""
    return client.post('/login', data={'email': email, 'password': 'Passw0rd!'},
                       follow_redirects=True)


def test_schema_impianti_creato(app, ambiente):
    """Le tabelle e la vista esistono dopo apply_schema_updates()."""
    attese = {'manutentori', 'impianti', 'impianti_componenti',
              'impianti_documenti', 'impianti_scadenze', 'impianti_interventi',
              'impianti_avvisi_inviati'}
    with app.app_context():
        nomi = {r['name'] for r in query_all(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert attese <= nomi
        viste = {r['name'] for r in query_all(
            "SELECT name FROM sqlite_master WHERE type='view'")}
        assert 'prossime_scadenze_impianti' in viste
        colonne = {r['name'] for r in query_all("PRAGMA table_info(divisioni)")}
        assert {'indirizzo', 'email', 'telefono', 'responsabile'} <= colonne


def test_vista_impianti_classifica_e_esclude_dismessi(app, ambiente):
    """La vista dà la priorità giusta e salta gli impianti dismessi."""
    with app.app_context():
        a = ambiente['a']
        attivo = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Cabina elettrica', 'elettrico')",
            (a['struttura'], a['divisione'])
        ).lastrowid
        dismesso = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo, stato)"
            " VALUES (?, ?, 'Vecchia centrale', 'riscaldamento', 'dismesso')",
            (a['struttura'], a['divisione'])
        ).lastrowid
        for impianto, giorni in ((attivo, -3), (attivo, 5), (attivo, 200),
                                 (dismesso, 1)):
            execute(
                "INSERT INTO impianti_scadenze (impianto_id, nome,"
                " periodicita_mesi, prossima_scadenza)"
                " VALUES (?, 'Verifica', 24, date('now', ?))",
                (impianto, f'{giorni} days')
            )
        righe = query_all(
            "SELECT priorita FROM prossime_scadenze_impianti WHERE impianto_id = ?",
            (attivo,))
        assert [r['priorita'] for r in righe] == ['scaduto', 'urgente', 'ok']
        assert query_all(
            "SELECT 1 FROM prossime_scadenze_impianti WHERE impianto_id = ?",
            (dismesso,)) == []


def test_impianto_accessibile_isola_le_strutture(app, ambiente):
    """Un admin non raggiunge l'impianto dell'altra struttura, nemmeno per id."""
    from flask import g
    from models import impianto_accessibile

    with app.app_context():
        b = ambiente['b']
        impianto_b = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Impianto segreto B', 'idraulico')",
            (b['struttura'], b['divisione'])
        ).lastrowid

    with app.test_request_context():
        g.user = {'id': ambiente['a']['utente'], 'ruolo': 'admin'}
        g.struttura_id = ambiente['a']['struttura']
        g.divisioni = []
        assert impianto_accessibile(impianto_b) is None

        g.struttura_id = ambiente['b']['struttura']
        riga = impianto_accessibile(impianto_b)
        assert riga is not None and riga['nome'] == 'Impianto segreto B'


def test_impianto_accessibile_rispetta_le_divisioni(app, ambiente):
    """Un utente semplice vede solo gli impianti delle sue divisioni."""
    from flask import g
    from models import impianto_accessibile

    with app.app_context():
        a = ambiente['a']
        altra_div = execute(
            "INSERT INTO divisioni (struttura_id, nome, codice) VALUES (?, 'Altra', 'ALT')",
            (a['struttura'],)).lastrowid
        impianto = execute(
            "INSERT INTO impianti (struttura_id, divisione_id, nome, tipo)"
            " VALUES (?, ?, 'Quadro Altra', 'elettrico')",
            (a['struttura'], altra_div)).lastrowid

    with app.test_request_context():
        g.user = {'id': 99, 'ruolo': 'utente'}
        g.struttura_id = ambiente['a']['struttura']
        g.divisioni = [{'id': ambiente['a']['divisione']}]
        assert impianto_accessibile(impianto) is None
        g.divisioni = [{'id': altra_div}]
        assert impianto_accessibile(impianto) is not None
