"""File statici: versione nell'URL e cache lunga, solo per gli URL versionati.

Dietro il tunnel Cloudflare ogni file statico senza cache costa un giro fino
al server. Gli URL prodotti da url_for('static') portano ?v=<impronta del
contenuto> e quelli, e solo quelli, si dichiarano immutabili per un anno. I
download privati (/uploads, verbali, documenti) non devono mai cambiare
comportamento: un proxy condiviso li servirebbe a chiunque.
"""
import os
import re
from urllib.parse import parse_qs, urlparse

import pytest
from flask import url_for
from werkzeug.security import generate_password_hash

import app as modulo_app

CACHE_LUNGA = 'public, max-age=31536000, immutable'


def _versione(url):
    return parse_qs(urlparse(url).query).get('v', [None])[0]


@pytest.fixture
def loggato(app, client):
    from models import execute
    with app.app_context():
        sid = execute("INSERT INTO strutture (nome,codice,attiva)"
                      " VALUES ('Clinica S','ICS',1)").lastrowid
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,"
                "struttura_id,primo_accesso) VALUES ('admins@i.it',?,'A','S',"
                "'admin',?,0)", (generate_password_hash('Passw0rd!'), sid))
    client.post('/login', data={'email': 'admins@i.it',
                                'password': 'Passw0rd!'})
    return sid


# ---------------------------------------------------------------------------
# URL versionati
# ---------------------------------------------------------------------------

def test_url_for_aggiunge_la_versione(app):
    with app.test_request_context():
        url = url_for('static', filename='css/style.css')
    assert url.startswith('/static/css/style.css?v=')
    assert re.fullmatch(r'[0-9a-f]{12}', _versione(url))


def test_v_esplicito_non_viene_sovrascritto(app):
    with app.test_request_context():
        url = url_for('static', filename='css/style.css', v='mio')
    assert _versione(url) == 'mio'


def test_file_mancante_niente_versione(app):
    with app.test_request_context():
        url = url_for('static', filename='non/esiste.css')
    assert url == '/static/non/esiste.css'


def test_percorso_fuori_cartella_niente_versione(app):
    with app.test_request_context():
        url = url_for('static', filename='../app.py')
    assert 'v=' not in url


def test_la_versione_cambia_col_contenuto(app, tmp_path):
    cartella = tmp_path / 'statici'
    cartella.mkdir()
    file = cartella / 'prova.css'
    file.write_text('body { color: red; }')
    app.static_folder = str(cartella)

    with app.test_request_context():
        prima = _versione(url_for('static', filename='prova.css'))
        di_nuovo = _versione(url_for('static', filename='prova.css'))
    assert prima and prima == di_nuovo

    file.write_text('body { color: blue; background: white; }')
    st = os.stat(file)
    os.utime(file, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    with app.test_request_context():
        dopo = _versione(url_for('static', filename='prova.css'))
    assert dopo and dopo != prima


def test_la_pagina_usa_url_versionati(client):
    html = client.get('/login').get_data(as_text=True)
    assert re.search(r'/static/[^"\']+\?v=[0-9a-f]{12}', html)


# ---------------------------------------------------------------------------
# Cache-Control
# ---------------------------------------------------------------------------

def test_statico_versionato_cache_lunga(app, client):
    with app.test_request_context():
        url = url_for('static', filename='css/style.css')
    risposta = client.get(url)
    assert risposta.status_code == 200
    assert risposta.headers['Cache-Control'] == CACHE_LUNGA
    # Gli header di sicurezza restano: l'ordine degli hook non li cancella.
    assert risposta.headers['X-Content-Type-Options'] == 'nosniff'
    risposta.close()


def test_statico_versionato_304_cache_lunga(app, client):
    with app.test_request_context():
        url = url_for('static', filename='css/style.css')
    prima = client.get(url)
    etag = prima.headers['ETag']
    prima.close()
    risposta = client.get(url, headers={'If-None-Match': etag})
    assert risposta.status_code == 304
    assert risposta.headers['Cache-Control'] == CACHE_LUNGA


def test_statico_senza_versione_invariato(client):
    risposta = client.get('/static/css/style.css')
    assert risposta.status_code == 200
    cc = risposta.headers.get('Cache-Control', '')
    assert 'public' not in cc
    assert 'max-age=31536000' not in cc
    assert 'immutable' not in cc
    assert 'no-cache' in cc
    risposta.close()


def test_statico_mancante_con_versione_niente_cache_lunga(client):
    risposta = client.get('/static/non/esiste.css?v=abc')
    assert risposta.status_code == 404
    assert risposta.headers.get('Cache-Control') != CACHE_LUNGA


def test_upload_privato_non_diventa_pubblico(app, client, loggato):
    cartella = os.path.join(app.config['UPLOADS_PATH'], 'strutture',
                            str(loggato), 'documenti')
    os.makedirs(cartella)
    with open(os.path.join(cartella, 'verbale.pdf'), 'wb') as f:
        f.write(b'%PDF-1.4 privato')

    url = f'/uploads/strutture/{loggato}/documenti/verbale.pdf'
    for indirizzo in (url, url + '?v=abc'):
        risposta = client.get(indirizzo)
        assert risposta.status_code == 200
        cc = risposta.headers.get('Cache-Control', '')
        assert 'public' not in cc
        assert 'immutable' not in cc
        assert 'max-age=31536000' not in cc
        risposta.close()


def test_send_file_max_age_non_impostato(app):
    """Se qualcuno lo impostasse, /uploads diventerebbe cacheabile."""
    assert app.config.get('SEND_FILE_MAX_AGE_DEFAULT') is None


# ---------------------------------------------------------------------------
# Niente cookie sui file statici (Cloudflare non metterebbe in cache)
# ---------------------------------------------------------------------------

def test_statico_senza_set_cookie_anonimo(app, client):
    with app.test_request_context():
        url = url_for('static', filename='css/style.css')
    risposta = client.get(url)
    assert 'Set-Cookie' not in risposta.headers
    assert 'Cookie' not in risposta.headers.get('Vary', '')
    risposta.close()


def test_statico_senza_set_cookie_da_loggato(app, client, loggato):
    with app.test_request_context():
        url = url_for('static', filename='css/style.css')
    risposta = client.get(url)
    assert risposta.status_code == 200
    assert 'Set-Cookie' not in risposta.headers
    assert 'Cookie' not in risposta.headers.get('Vary', '')
    risposta.close()


def test_le_pagine_rinnovano_ancora_il_cookie(client, loggato):
    """La sessione scorrevole resta: solo i file statici non la rinnovano."""
    risposta = client.get('/')
    assert 'Set-Cookie' in risposta.headers
