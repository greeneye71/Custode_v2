"""M10 — SSRF attraverso l'URL del server AI locale.

L'indirizzo del server AI (Ollama, LM Studio, endpoint OpenAI-compatibile) lo
sceglie l'admin di una struttura, ma la richiesta HTTP parte dal server: senza
controlli e' una sonda verso la LAN, verso localhost e verso gli indirizzi di
metadata delle installazioni in cloud.

Qui si verifica sia la funzione `sicurezza_url.valida_url_ai_locale()` sia le
due rotte che salvano quell'indirizzo, perche' il rifiuto deve arrivare prima
della scrittura in configurazione, non solo al momento della chiamata uscente.
"""
import socket

import pytest
from werkzeug.security import generate_password_hash

from models import execute, query_one
from sicurezza_url import leggi_allowlist, valida_url_ai_locale


# ---------------------------------------------------------------------------
# Schema, credenziali, forma dell'URL
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('url', [
    'ftp://192.168.1.10/',
    'file:///c:/windows/win.ini',
    'gopher://192.168.1.10:70/',
    '192.168.1.10:11434',
])
def test_solo_http_e_https(url):
    """Schemi diversi da HTTP(S) raggiungono servizi che non sono un server AI."""
    with pytest.raises(ValueError) as errore:
        valida_url_ai_locale(url)
    assert 'http://' in str(errore.value)


@pytest.mark.parametrize('url', ['', '   ', None])
def test_url_vuoto_rifiutato(url):
    with pytest.raises(ValueError):
        valida_url_ai_locale(url)


def test_credenziali_nell_url_rifiutate():
    """Le credenziali in chiaro servono a farsi autenticare da un servizio
    interno, non a parlare con Ollama."""
    with pytest.raises(ValueError) as errore:
        valida_url_ai_locale('http://utente:parola@192.168.1.10:11434')
    assert 'credenziali' in str(errore.value)


def test_host_mancante_rifiutato():
    with pytest.raises(ValueError) as errore:
        valida_url_ai_locale('http:///v1/models')
    assert 'nome host' in str(errore.value)


def test_porta_non_numerica_rifiutata():
    with pytest.raises(ValueError) as errore:
        valida_url_ai_locale('http://192.168.1.10:abc')
    assert 'Porta non valida' in str(errore.value)


# ---------------------------------------------------------------------------
# Reti vietate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('url', [
    'http://169.254.169.254/latest/meta-data/',      # metadata AWS/Azure
    'http://169.254.169.254:80/',
    'http://[::ffff:169.254.169.254]:8080/',         # lo stesso indirizzo mascherato da IPv6
    'http://[fe80::1]:8080/',                        # link-local IPv6
    'http://224.0.0.1:8080/',                        # multicast
    'http://240.0.0.1:8080/',                        # riservata
    'http://0.0.0.0:8080/',                          # "questa rete"
    'http://255.255.255.255:8080/',                  # broadcast
    'http://[::]:8080/',                             # non specificato
])
def test_reti_di_sistema_rifiutate(url):
    """Nessun server AI vive su queste reti; sono i bersagli tipici di una SSRF."""
    with pytest.raises(ValueError) as errore:
        valida_url_ai_locale(url)
    assert 'rete di sistema' in str(errore.value)


@pytest.mark.parametrize('url,atteso', [
    ('http://127.0.0.1:11434',        'http://127.0.0.1:11434'),
    ('http://127.0.0.1:11434/',       'http://127.0.0.1:11434'),
    ('http://localhost:1234/v1/',     'http://localhost:1234/v1'),
    ('http://192.168.1.50:8080',      'http://192.168.1.50:8080'),
    ('https://10.0.0.5/api',          'https://10.0.0.5/api'),
    ('http://172.16.3.4:11434/?x=1',  'http://172.16.3.4:11434'),
])
def test_loopback_e_lan_ammessi_e_normalizzati(url, atteso):
    """Loopback e reti private restano raggiungibili: e' li' che sta il server
    AI di un'installazione LAN. Query e barra finale spariscono."""
    assert valida_url_ai_locale(url) == atteso


def test_nome_non_risolvibile_rifiutato(monkeypatch):
    def esplodi(*args, **kwargs):
        raise socket.gaierror('nome sconosciuto')
    monkeypatch.setattr(socket, 'getaddrinfo', esplodi)
    with pytest.raises(ValueError) as errore:
        valida_url_ai_locale('http://server-che-non-esiste:11434')
    assert 'non risolvibile' in str(errore.value)


def test_nome_che_risolve_sui_metadata_rifiutato(monkeypatch):
    """Il controllo guarda l'indirizzo risolto, non il nome: un dominio
    pubblico puntato su 169.254.169.254 non passa."""
    def risolvi(host, porta, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('169.254.169.254', porta))]
    monkeypatch.setattr(socket, 'getaddrinfo', risolvi)
    with pytest.raises(ValueError) as errore:
        valida_url_ai_locale('http://ai.esempio.it:11434')
    assert 'rete di sistema' in str(errore.value)


def test_basta_un_indirizzo_vietato_per_rifiutare(monkeypatch):
    """Un nome con piu' record A passa solo se nessuno dei suoi indirizzi e'
    vietato: bastasse il primo buono, il secondo deciderebbe la connessione."""
    def risolvi(host, porta, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.10', porta)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('169.254.169.254', porta)),
        ]
    monkeypatch.setattr(socket, 'getaddrinfo', risolvi)
    with pytest.raises(ValueError):
        valida_url_ai_locale('http://ai.esempio.it:11434')


# ---------------------------------------------------------------------------
# Porte di sistema
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('porta', [22, 23, 25, 139, 445])
def test_porte_di_sistema_rifiutate(porta):
    """Sotto la 1024 ci sono SSH, SMTP, SMB: non un server AI."""
    with pytest.raises(ValueError) as errore:
        valida_url_ai_locale('http://192.168.1.10:%d' % porta)
    assert 'Porta non ammessa' in str(errore.value)


@pytest.mark.parametrize('url', [
    'http://192.168.1.10',        # 80 implicita
    'https://192.168.1.10',       # 443 implicita
    'http://192.168.1.10:443',
    'http://192.168.1.10:11434',
    'http://192.168.1.10:1234',
    'http://192.168.1.10:8080',
])
def test_porte_ammesse(url):
    assert valida_url_ai_locale(url).startswith('http')


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('config,atteso', [
    ({}, []),
    ({'ai_local_url_allowlist': []}, []),
    ({'ai_local_url_allowlist': ['127.0.0.1:11434']}, ['127.0.0.1:11434']),
    ({'ai_local_url_allowlist': '127.0.0.1:11434, 10.0.0.0/8'}, ['127.0.0.1:11434', '10.0.0.0/8']),
    ({'ai_local_url_allowlist': '127.0.0.1 10.0.0.0/8'}, ['127.0.0.1', '10.0.0.0/8']),
    (None, []),
])
def test_lettura_dell_allowlist(config, atteso):
    """L'operatore puo' scriverla come lista o come stringa."""
    assert leggi_allowlist(config) == atteso


@pytest.mark.parametrize('voce,url', [
    ('192.168.1.10',            'http://192.168.1.10:11434'),
    ('192.168.1.10:11434',      'http://192.168.1.10:11434'),
    ('192.168.1.0/24',          'http://192.168.1.10:11434'),
    ('192.168.1.0/24:11434',    'http://192.168.1.10:11434'),
    ('localhost:1234',          'http://localhost:1234/v1'),
    ('[::1]',                   'http://[::1]:11434'),
])
def test_allowlist_consente_le_voci_elencate(voce, url):
    assert valida_url_ai_locale(url, [voce])


@pytest.mark.parametrize('voce,url', [
    ('192.168.1.10',         'http://192.168.1.11:11434'),   # altro host
    ('192.168.1.10:11434',   'http://192.168.1.10:8080'),    # altra porta
    ('192.168.1.0/24',       'http://10.0.0.5:11434'),       # altra rete
    ('10.0.0.0/8:11434',     'http://10.0.0.5:8080'),        # rete giusta, porta no
])
def test_allowlist_rifiuta_tutto_il_resto(voce, url):
    with pytest.raises(ValueError) as errore:
        valida_url_ai_locale(url, [voce])
    assert 'ai_local_url_allowlist' in str(errore.value)


def test_allowlist_dal_dizionario_di_configurazione():
    """La rotta passa direttamente la configurazione globale."""
    config = {'ai_local_url_allowlist': ['192.168.1.0/24']}
    assert valida_url_ai_locale('http://192.168.1.10:11434', config)
    with pytest.raises(ValueError):
        valida_url_ai_locale('http://10.0.0.5:11434', config)


def test_allowlist_puo_ammettere_una_porta_di_sistema():
    """Elencare un indirizzo esplicitamente e' la deroga prevista: la regola
    sulle porte sotto la 1024 vale quando l'allowlist e' vuota."""
    assert valida_url_ai_locale('http://192.168.1.10:8000', ['192.168.1.10:8000'])
    with pytest.raises(ValueError):
        valida_url_ai_locale('http://192.168.1.10:22')


def test_allowlist_non_supera_le_reti_vietate():
    """Nemmeno un'allowlist scritta male riapre i metadata."""
    with pytest.raises(ValueError) as errore:
        valida_url_ai_locale('http://169.254.169.254/', ['169.254.0.0/16'])
    assert 'rete di sistema' in str(errore.value)


def test_voce_cidr_soddisfatta_solo_se_tutti_gli_indirizzi_sono_dentro(monkeypatch):
    def risolvi(host, porta, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.5', porta)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.9.9', porta)),
        ]
    monkeypatch.setattr(socket, 'getaddrinfo', risolvi)
    with pytest.raises(ValueError):
        valida_url_ai_locale('http://ai.esempio.it:11434', ['10.0.0.0/8'])


# ---------------------------------------------------------------------------
# Le rotte: il rifiuto arriva prima della scrittura
# ---------------------------------------------------------------------------

@pytest.fixture
def struttura_con_admin(app):
    with app.app_context():
        sid = execute(
            "INSERT INTO strutture (nome, codice, attiva) VALUES ('Clinica S', 'CLS', 1)"
        ).lastrowid
        execute(
            "INSERT INTO utenti (struttura_id, nome, cognome, email, password_hash,"
            " ruolo, attivo) VALUES (?, 'Admin', 'S', 'admin.s@test.it', ?, 'admin', 1)",
            (sid, generate_password_hash('Passw0rd!')))
        execute(
            "INSERT INTO utenti (struttura_id, nome, cognome, email, password_hash,"
            " ruolo, attivo) VALUES (NULL, 'Super', 'S', 'super.s@test.it', ?, 'superadmin', 1)",
            (generate_password_hash('Passw0rd!'),))
    return sid


def entra(client, email):
    return client.post('/login', data={'email': email, 'password': 'Passw0rd!'},
                       follow_redirects=True)


@pytest.mark.parametrize('url_ostile', [
    'http://169.254.169.254/latest/meta-data/',
    'http://192.168.1.10:22',
    'file:///c:/windows/win.ini',
])
def test_la_rotta_di_struttura_rifiuta_un_url_ostile(app, client, struttura_con_admin, url_ostile):
    """L'admin di tenant riceve 400 e in strutture_config non resta nulla."""
    entra(client, 'admin.s@test.it')
    risposta = client.post(
        '/strutture/%d/config/test-ai' % struttura_con_admin,
        json={'provider': 'ollama', 'local_base_url': url_ostile})
    assert risposta.status_code == 400
    assert risposta.get_json()['ok'] is False
    with app.app_context():
        riga = query_one(
            "SELECT valore FROM strutture_config WHERE struttura_id=? AND chiave='ai_local_base_url'",
            (struttura_con_admin,))
    assert riga is None


def test_la_rotta_di_struttura_registra_il_rifiuto(app, client, struttura_con_admin):
    entra(client, 'admin.s@test.it')
    client.post('/strutture/%d/config/test-ai' % struttura_con_admin,
                json={'provider': 'ollama', 'local_base_url': 'http://169.254.169.254/'})
    with app.app_context():
        riga = query_one(
            "SELECT dettagli FROM log_attivita WHERE entita='strutture_config'"
            " ORDER BY id DESC LIMIT 1")
    assert riga and 'URL locale rifiutato' in riga['dettagli']


def test_la_rotta_di_struttura_salva_l_url_normalizzato(app, client, struttura_con_admin, monkeypatch):
    """Un indirizzo LAN legittimo passa e viene salvato senza barra finale."""
    import strutture_bp
    monkeypatch.setattr(strutture_bp, '_fetch_local_models', lambda url, allowlist=None: ['modello'])
    entra(client, 'admin.s@test.it')
    risposta = client.post(
        '/strutture/%d/config/test-ai' % struttura_con_admin,
        json={'provider': 'ollama', 'local_base_url': 'http://192.168.1.50:11434/'})
    assert risposta.status_code == 200
    with app.app_context():
        riga = query_one(
            "SELECT valore FROM strutture_config WHERE struttura_id=? AND chiave='ai_local_base_url'",
            (struttura_con_admin,))
    assert riga['valore'] == 'http://192.168.1.50:11434'


def test_l_allowlist_di_sistema_vincola_l_admin_di_struttura(app, client, struttura_con_admin):
    """L'allowlist sta nella configurazione globale: l'admin di tenant non ha
    modo di allargarla, e un indirizzo fuori lista viene respinto."""
    app.config['APP_CONFIG']['ai_local_url_allowlist'] = ['192.168.1.50:11434']
    try:
        entra(client, 'admin.s@test.it')
        risposta = client.post(
            '/strutture/%d/config/test-ai' % struttura_con_admin,
            json={'provider': 'ollama', 'local_base_url': 'http://192.168.1.99:11434'})
    finally:
        app.config['APP_CONFIG'].pop('ai_local_url_allowlist', None)
    assert risposta.status_code == 400
    assert 'ai_local_url_allowlist' in risposta.get_json()['message']


def test_la_rotta_globale_rifiuta_un_url_ostile(app, client, struttura_con_admin):
    """Stessa difesa nella configurazione di sistema, dove entra il superadmin.
    Il 400 arriva prima di save_config(): non tocca config.local.json."""
    entra(client, 'super.s@test.it')
    risposta = client.post('/admin/configurazione/test-ai',
                           json={'provider': 'lmstudio',
                                 'local_base_url': 'http://169.254.169.254/'})
    assert risposta.status_code == 400
    assert 'rete di sistema' in risposta.get_json()['message']


def test_check_ai_configured_rifiuta_un_url_locale_non_valido(app):
    """La validazione si ripete al momento dell'uso: un indirizzo finito in
    configurazione per altre vie non diventa una chiamata uscente."""
    from ai_service import check_ai_configured
    with app.app_context():
        config = {'ai_provider': 'ollama',
                  'ai_local_base_url': 'http://169.254.169.254/',
                  'ai_local_model': 'llama3'}
        ok, messaggio = check_ai_configured(config)
    assert ok is False
    assert 'rete di sistema' in messaggio
