"""cloudflare_mode.py --test dentro il container (Task 10).

Nel container non esistono cloudflared dell'host, systemctl o config.yml:
le sezioni che li guardano vanno saltate dicendo dove guardare. Nessun test
qui apre una connessione o lancia un processo: rete e sottoprocessi sono
sostituiti.
"""
import pytest

import cloudflare_mode

CONFIG = {
    'host': '127.0.0.1',
    'port': 5000,
    'cloudflare_mode': True,
    'force_https': True,
}

FUNZIONI_HOST = (
    '_trova_cloudflared',
    '_versione_cloudflared',
    '_servizio_cloudflared_attivo',
    '_leggi_config_cloudflared',
)


@pytest.fixture
def rete_finta(monkeypatch):
    """Sostituisce ogni funzione che esce sulla rete e registra cosa le
    viene chiesto."""
    chiamate = {'https': [], 'cookie': [], 'redirect': [], 'dns': []}

    def server_locale(host, porta, timeout=5):
        return True, 'HTTP 200', 3.0

    def url_https(url, timeout=10):
        chiamate['https'].append(url)
        return {'ok': True, 'ms': 12.0, 'status': 200,
                'errore': None, 'headers': {}}

    def cookie_login(url_base, timeout=10):
        chiamate['cookie'].append(url_base)
        return {'trovato': True, 'secure': True,
                'httponly': True, 'samesite': True}

    def redirect_http(hostname, timeout=8):
        chiamate['redirect'].append(hostname)
        return True, 'HTTP 301 -> https'

    def risolvi(nome):
        chiamate['dns'].append(nome)
        return '104.16.0.1'

    monkeypatch.setattr(cloudflare_mode, '_test_server_locale', server_locale)
    monkeypatch.setattr(cloudflare_mode, '_test_url_https', url_https)
    monkeypatch.setattr(cloudflare_mode, '_controlla_cookie_login', cookie_login)
    monkeypatch.setattr(cloudflare_mode, '_test_redirect_http', redirect_http)
    monkeypatch.setattr(cloudflare_mode.socket, 'gethostbyname', risolvi)
    return chiamate


@pytest.fixture
def host_osservato(monkeypatch):
    """Sostituisce le quattro funzioni che guardano l'host e registra quali
    vengono chiamate."""
    chiamate = []

    def trova():
        chiamate.append('_trova_cloudflared')
        return '/usr/bin/cloudflared'

    def versione(exe):
        chiamate.append('_versione_cloudflared')
        return 'cloudflared version 2026.11.0'

    def servizio():
        chiamate.append('_servizio_cloudflared_attivo')
        return True, 'servizio attivo'

    def config_yml():
        chiamate.append('_leggi_config_cloudflared')
        return {'config_path': '/etc/cloudflared/config.yml',
                'tunnel': 'uuid-di-prova',
                'hostname': 'vecchio.example.it',
                'service': 'http://127.0.0.1:5000'}

    monkeypatch.setattr(cloudflare_mode, '_trova_cloudflared', trova)
    monkeypatch.setattr(cloudflare_mode, '_versione_cloudflared', versione)
    monkeypatch.setattr(cloudflare_mode, '_servizio_cloudflared_attivo', servizio)
    monkeypatch.setattr(cloudflare_mode, '_leggi_config_cloudflared', config_yml)
    return chiamate


@pytest.fixture
def nel_container(monkeypatch, tmp_path):
    monkeypatch.setenv('MEDINVENTORY_RADICE', str(tmp_path))


@pytest.fixture
def fuori_dal_container(monkeypatch):
    monkeypatch.delenv('MEDINVENTORY_RADICE', raising=False)


def test_nel_container_le_sezioni_host_sono_saltate(
        nel_container, rete_finta, host_osservato, capsys):
    cloudflare_mode.esegui_test(CONFIG)
    uscita = capsys.readouterr().out
    assert host_osservato == []
    assert 'journalctl CONTAINER_NAME=custode-tunnel' in uscita
    assert 'configura.sh stato' in uscita


def test_fuori_dal_container_le_sezioni_host_restano(
        fuori_dal_container, rete_finta, host_osservato, capsys):
    cloudflare_mode.esegui_test(CONFIG)
    uscita = capsys.readouterr().out
    assert set(host_osservato) == set(FUNZIONI_HOST)
    assert '[2] Installazione cloudflared' in uscita
    assert '[3] Stato processo/servizio cloudflared' in uscita
    assert '[4] File di configurazione cloudflared' in uscita
    assert 'CONTAINER_NAME' not in uscita


def test_fuori_dal_container_l_hostname_viene_da_config_yml(
        fuori_dal_container, rete_finta, host_osservato):
    cloudflare_mode.esegui_test(CONFIG)
    assert rete_finta['https'] == ['https://vecchio.example.it/login']


def test_hostname_usato_per_l_url_pubblico(
        nel_container, rete_finta, host_osservato):
    cloudflare_mode.esegui_test(CONFIG, hostname='custode.example.it')
    assert rete_finta['https'] == ['https://custode.example.it/login']
    assert rete_finta['dns'] == ['custode.example.it']
    assert all('custode.example.it' in voce
               for voce in rete_finta['cookie'] + rete_finta['redirect'])


def test_nel_container_senza_hostname_suggerisce_l_opzione(
        nel_container, rete_finta, host_osservato, capsys):
    cloudflare_mode.esegui_test(CONFIG)
    uscita = capsys.readouterr().out
    assert rete_finta['https'] == []
    assert '--hostname' in uscita
    assert 'config.yml' not in uscita


def test_nel_container_nessun_sottoprocesso_verso_l_host(
        nel_container, rete_finta, monkeypatch):
    """Le funzioni vere restano al loro posto: se una viene raggiunta, il
    primo sottoprocesso o la prima ricerca nel PATH fa fallire il test."""
    def vietato(*args, **kwargs):
        raise AssertionError(f'chiamata verso l\'host: {args!r}')

    for nome in ('call', 'check_output', 'check_call', 'run', 'Popen'):
        monkeypatch.setattr(cloudflare_mode.subprocess, nome, vietato)
    monkeypatch.setattr(cloudflare_mode.shutil, 'which', vietato)

    cloudflare_mode.esegui_test(CONFIG, hostname='custode.example.it')


def test_hostname_senza_test_e_rifiutato(capsys):
    for argomenti in (['--hostname', 'custode.example.it'],
                      ['--status', '--hostname', 'custode.example.it'],
                      ['--on', '--hostname', 'custode.example.it']):
        with pytest.raises(SystemExit) as uscita:
            cloudflare_mode.main(argomenti)
        assert uscita.value.code == 2
    assert '--hostname' in capsys.readouterr().err


def test_hostname_che_non_e_un_nome_e_rifiutato(capsys):
    for valore in ('https://custode.example.it', 'custode.example.it/login',
                   'custode.example.it:8443', 'a b', ''):
        with pytest.raises(SystemExit) as uscita:
            cloudflare_mode.main(['--test', '--hostname', valore])
        assert uscita.value.code == 2


def test_main_passa_l_hostname_al_test(monkeypatch):
    ricevuto = {}

    def finto_test(config, hostname=None):
        ricevuto['config'] = config
        ricevuto['hostname'] = hostname

    monkeypatch.setattr(cloudflare_mode, 'leggi_config', lambda: dict(CONFIG))
    monkeypatch.setattr(cloudflare_mode, 'esegui_test', finto_test)
    cloudflare_mode.main(['--test', '--hostname', 'custode.example.it'])
    assert ricevuto['hostname'] == 'custode.example.it'
    assert ricevuto['config']['port'] == 5000
