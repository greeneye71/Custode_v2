"""M13: dipendenze frontend da CDN esterni senza SRI, e nessuna CSP.

Bootstrap, bootstrap-icons, htmx, Chart.js e il font Inter arrivavano da
jsdelivr, unpkg e Google Fonts senza integrity: chi controlla (o dirotta) una
di quelle origini esegue codice nel browser di ogni utente, con la sessione
aperta. E su una LAN senza uscita verso Internet — la condizione normale di
questo prodotto — l'interfaccia si presentava semplicemente senza stile e
senza htmx.

Gli asset sono ora versionati in static/vendor/ e serviti dall'applicazione,
e la risposta porta una Content-Security-Policy che vieta le origini esterne.
Questi test inchiodano le due proprieta': nessun template torna a un CDN, e
la CSP resta.
"""
import glob
import os
import re

import pytest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(RADICE, 'templates')
VENDOR = os.path.join(RADICE, 'static', 'vendor')


def _template_html():
    return glob.glob(os.path.join(TEMPLATES, '**', '*.html'), recursive=True)


# ---------------------------------------------------------------------------
# Nessun asset esterno
# ---------------------------------------------------------------------------

def test_nessun_template_carica_script_o_css_da_internet():
    """Il difetto era esattamente questo: un tag che punta fuori. Se qualcuno
    reintroduce un CDN (anche 'solo per una libreria') il test lo ferma."""
    esterni = []
    modello = re.compile(r'<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*'
                         r'["\'](https?:)?//[^"\']+["\']', re.IGNORECASE)
    for percorso in _template_html():
        with open(percorso, encoding='utf-8') as f:
            for numero, riga in enumerate(f, 1):
                if modello.search(riga):
                    esterni.append(f'{os.path.relpath(percorso, RADICE)}:{numero}')
    assert esterni == [], f'asset caricati da origini esterne: {esterni}'


def test_ogni_asset_vendor_citato_dai_template_esiste_su_disco():
    """Un url_for verso un file mancante non e' un errore a build time: la
    pagina si carica e basta, senza stile. Meglio accorgersene qui."""
    citati = set()
    for percorso in _template_html():
        with open(percorso, encoding='utf-8') as f:
            citati.update(re.findall(r"filename=['\"](vendor/[^'\"]+)['\"]", f.read()))
    assert citati, 'nessun asset vendor citato: i template sono cambiati?'
    mancanti = [c for c in citati
                if not os.path.isfile(os.path.join(RADICE, 'static', *c.split('/')))]
    assert mancanti == [], f'asset citati ma assenti: {mancanti}'


@pytest.mark.parametrize('relativo', [
    'bootstrap/bootstrap.min.css',
    'bootstrap/bootstrap.bundle.min.js',
    'bootstrap-icons/bootstrap-icons.min.css',
    'bootstrap-icons/fonts/bootstrap-icons.woff2',
    'htmx/htmx.min.js',
    'chartjs/chart.umd.min.js',
    'inter/inter.css',
])
def test_l_asset_e_presente_e_non_e_un_segnaposto(relativo):
    percorso = os.path.join(VENDOR, *relativo.split('/'))
    assert os.path.isfile(percorso), f'{relativo} non e\' stato distribuito'
    assert os.path.getsize(percorso) > 1024, f'{relativo} sembra troncato'


def test_i_css_vendor_non_rimandano_a_origini_esterne():
    """Un CSS locale che si tira dietro i font da Google avrebbe rimesso
    dentro la dipendenza esterna dalla porta di servizio."""
    fuori = []
    for percorso in glob.glob(os.path.join(VENDOR, '**', '*.css'), recursive=True):
        with open(percorso, encoding='utf-8') as f:
            for url in re.findall(r'url\(([^)]+)\)', f.read()):
                url = url.strip('\'" ')
                # Le icone SVG inline di Bootstrap sono data: URI e contengono
                # l'URL dello schema xmlns: non e' una risorsa da scaricare.
                if url.startswith('data:'):
                    continue
                if '//' in url:
                    fuori.append((os.path.relpath(percorso, RADICE), url))
    assert fuori == [], f'CSS vendor con riferimenti esterni: {fuori}'


def test_i_font_citati_dai_css_vendor_esistono():
    mancanti = []
    for percorso in glob.glob(os.path.join(VENDOR, '**', '*.css'), recursive=True):
        cartella = os.path.dirname(percorso)
        with open(percorso, encoding='utf-8') as f:
            for url in re.findall(r'url\(["\']?([^"\')?]+)', f.read()):
                if url.startswith('data:'):
                    continue
                if not os.path.isfile(os.path.join(cartella, url.replace('/', os.sep))):
                    mancanti.append((os.path.relpath(percorso, RADICE), url))
    assert mancanti == [], f'font citati ma assenti: {mancanti}'


def test_l_applicazione_serve_davvero_un_asset_vendor(client):
    """Controllo positivo: i test qui sopra passerebbero anche se la cartella
    non fosse raggiungibile via HTTP."""
    risposta = client.get('/static/vendor/htmx/htmx.min.js')
    assert risposta.status_code == 200
    assert len(risposta.data) > 1024


# ---------------------------------------------------------------------------
# Content-Security-Policy
# ---------------------------------------------------------------------------

def test_la_csp_e_presente_su_una_pagina(client):
    csp = client.get('/login').headers.get('Content-Security-Policy')
    assert csp, 'nessuna Content-Security-Policy nella risposta'


@pytest.mark.parametrize('direttiva', [
    "default-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'self'",
    "connect-src 'self'",
])
def test_la_csp_contiene_le_direttive_che_contano(client, direttiva):
    csp = client.get('/login').headers['Content-Security-Policy']
    assert direttiva in csp


def test_la_csp_non_riammette_origini_esterne(client):
    """'unsafe-inline' e' un compromesso noto e documentato per gli script
    inline dei template; un https:// nella policy sarebbe invece il ritorno
    del difetto."""
    csp = client.get('/login').headers['Content-Security-Policy']
    assert 'http://' not in csp and 'https://' not in csp
    assert '*' not in csp


def test_la_csp_c_e_anche_su_redirect_ed_errori(client):
    """Se la policy dipendesse dalla rotta o dallo status, basterebbe la
    pagina sbagliata per non averla dove serve."""
    for risposta in (client.get('/'), client.get('/rotta-inesistente')):
        assert risposta.headers.get('Content-Security-Policy'), risposta.status
