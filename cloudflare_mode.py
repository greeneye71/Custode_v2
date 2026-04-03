"""
cloudflare_mode.py — Attiva, disattiva, interroga e testa la modalità Cloudflare Tunnel.

Uso:
    python cloudflare_mode.py             # mostra stato attuale e chiede conferma
    python cloudflare_mode.py --status    # mostra solo lo stato senza modificare
    python cloudflare_mode.py --on        # attiva la modalità Cloudflare
    python cloudflare_mode.py --off       # disattiva la modalità Cloudflare
    python cloudflare_mode.py --test      # esegue diagnostica completa

In modalità Cloudflare attiva vengono impostati:
    - cloudflare_mode: true   → Waitress ascolta su 127.0.0.1 (default)
    - force_https: true       → cookie Secure, HSTS, redirect HTTP→HTTPS
    - host: "127.0.0.1"       → blocca connessioni dirette che bypassano il tunnel

In modalità Cloudflare disattiva vengono ripristinati:
    - cloudflare_mode: false
    - force_https: false
    - host: "0.0.0.0"         → accessibile su tutta la LAN
"""

import json
import os
import sys
import argparse
import platform
import subprocess
import shutil
import socket
import ssl
import time
import urllib.request
import urllib.error
import urllib.parse

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_LOCAL   = os.path.join(BASE_DIR, 'config.local.json')
CONFIG_EXAMPLE = os.path.join(BASE_DIR, 'config.local.example.json')

# Percorsi standard del file di configurazione cloudflared
_CF_CONFIG_PATHS = [
    os.path.expanduser('~/.cloudflared/config.yml'),
    os.path.expanduser('~/.cloudflared/config.yaml'),
    r'C:\Windows\System32\config\systemprofile\.cloudflared\config.yml',
    r'C:\Windows\System32\config\systemprofile\.cloudflared\config.yaml',
    '/etc/cloudflared/config.yml',
    '/etc/cloudflared/config.yaml',
]

# ─────────────────────────────────────────────────────────────────────────────
# Helpers config
# ─────────────────────────────────────────────────────────────────────────────

def leggi_config():
    if os.path.exists(CONFIG_LOCAL):
        with open(CONFIG_LOCAL, 'r', encoding='utf-8') as f:
            return json.load(f)
    if os.path.exists(CONFIG_EXAMPLE):
        with open(CONFIG_EXAMPLE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def scrivi_config(config):
    with open(CONFIG_LOCAL, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def cloudflare_attivo(config):
    return bool(config.get('cloudflare_mode', False))


def descrivi_stato(attivo, config):
    host        = config.get('host', '127.0.0.1' if attivo else '0.0.0.0')
    force_https = config.get('force_https', attivo)
    if attivo:
        return (
            "CLOUDFLARE TUNNEL ATTIVO\n"
            f"  - host             : {host}\n"
            f"  - force_https      : {force_https}\n"
            "  - Cookie Secure    : sì\n"
            "  - HSTS             : sì (12 mesi)\n"
            "  - Redirect HTTP→HTTPS: sì (via X-Forwarded-Proto)\n"
            "  - Accesso diretto  : bloccato (solo tunnel)"
        )
    else:
        return (
            "CLOUDFLARE TUNNEL DISATTIVO  (modalità LAN locale)\n"
            f"  - host             : {host}\n"
            f"  - force_https      : {force_https}\n"
            "  - Cookie Secure    : no\n"
            "  - HSTS             : no\n"
            "  - Accesso diretto  : sì (tutta la LAN)"
        )


def attiva(config):
    config['cloudflare_mode'] = True
    config['force_https']     = True
    if config.get('host', '0.0.0.0') == '0.0.0.0':
        config['host'] = '127.0.0.1'


def disattiva(config):
    config['cloudflare_mode'] = False
    config['force_https']     = False
    if config.get('host', '127.0.0.1') == '127.0.0.1':
        config['host'] = '0.0.0.0'


# ─────────────────────────────────────────────────────────────────────────────
# Diagnostica
# ─────────────────────────────────────────────────────────────────────────────

OK   = '  [OK]  '
WARN = '  [!!]  '
FAIL = '  [XX]  '
INFO = '  [--]  '


def _print_check(esito, msg):
    prefisso = {True: OK, False: FAIL, None: WARN, 'info': INFO}[esito]
    print(prefisso + msg)


def _trova_cloudflared():
    """Restituisce il percorso dell'eseguibile cloudflared o None."""
    trovato = shutil.which('cloudflared')
    if trovato:
        return trovato
    # Percorsi di fallback per installazioni non nel PATH
    if platform.system() == 'Windows':
        candidati = [
            r'C:\Program Files\cloudflared\cloudflared.exe',
            r'C:\ProgramData\cloudflared\cloudflared.exe',
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'cloudflared', 'cloudflared.exe'),
        ]
    else:
        candidati = [
            '/usr/local/bin/cloudflared',
            '/usr/bin/cloudflared',
            '/opt/cloudflared/cloudflared',
        ]
    for c in candidati:
        if os.path.isfile(c):
            return c
    return None


def _versione_cloudflared(exe):
    """Restituisce la stringa di versione oppure None."""
    try:
        out = subprocess.check_output(
            [exe, '--version'], stderr=subprocess.STDOUT, timeout=5
        ).decode(errors='replace').strip()
        # "cloudflared version 2024.x.y ..."
        return out.split('\n')[0]
    except Exception:
        return None


def _servizio_cloudflared_attivo():
    """
    Controlla se il servizio/processo cloudflared è in esecuzione.
    Ritorna (bool, str) → (attivo, descrizione).
    """
    sistema = platform.system()
    try:
        if sistema == 'Windows':
            out = subprocess.check_output(
                ['sc', 'query', 'cloudflared'],
                stderr=subprocess.DEVNULL, timeout=5
            ).decode(errors='replace')
            if 'RUNNING' in out:
                return True, 'servizio Windows in esecuzione'
            if 'STOPPED' in out:
                return False, 'servizio Windows fermato'
            return False, 'servizio Windows non trovato'
        else:
            # systemd
            ret = subprocess.call(
                ['systemctl', 'is-active', '--quiet', 'cloudflared'],
                timeout=5
            )
            if ret == 0:
                return True, 'systemd service attivo'
            # Fallback: cerca processo
            out = subprocess.check_output(
                ['pgrep', '-x', 'cloudflared'],
                stderr=subprocess.DEVNULL, timeout=5
            ).decode().strip()
            if out:
                return True, f'processo in esecuzione (PID {out.split()[0]})'
            return False, 'nessun processo trovato'
    except subprocess.TimeoutExpired:
        return None, 'timeout controllo stato'
    except FileNotFoundError:
        # pgrep / sc non disponibili
        return None, 'impossibile verificare lo stato'
    except subprocess.CalledProcessError:
        return False, 'nessun processo trovato'


def _leggi_config_cloudflared():
    """
    Cerca e parsa il file config.yml di cloudflared.
    Restituisce dict con 'tunnel', 'hostname', 'service', 'config_path' o {}.
    """
    import re
    for path in _CF_CONFIG_PATHS:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                testo = f.read()
            risultato = {'config_path': path}
            # Estrazione basilare senza dipendenza da PyYAML
            m = re.search(r'^tunnel:\s*(\S+)', testo, re.MULTILINE)
            if m:
                risultato['tunnel'] = m.group(1)
            m = re.search(r'hostname:\s*(\S+)', testo)
            if m:
                risultato['hostname'] = m.group(1)
            m = re.search(r'service:\s*(https?://\S+)', testo)
            if m:
                risultato['service'] = m.group(1)
            return risultato
        except Exception:
            continue
    return {}


def _test_server_locale(host, porta, timeout=5):
    """
    Testa la raggiungibilità del server MedInventory locale.
    Ritorna (bool, str, float|None) → (raggiungibile, dettaglio, ms).
    """
    try:
        t0 = time.monotonic()
        url = f'http://{host}:{porta}/login'
        req = urllib.request.Request(url, headers={'User-Agent': 'MedInventory-Diag/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ms = (time.monotonic() - t0) * 1000
            return True, f'HTTP {r.status}', ms
    except urllib.error.HTTPError as e:
        ms = (time.monotonic() - t0) * 1000
        return True, f'HTTP {e.code} (atteso per /login)', ms
    except urllib.error.URLError as e:
        return False, str(e.reason), None
    except Exception as e:
        return False, str(e), None


def _test_url_https(url, timeout=10):
    """
    Testa la raggiungibilità di un URL HTTPS.
    Ritorna dict con: ok, status, ms, headers, errore.
    """
    ctx = ssl.create_default_context()
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'MedInventory-Diag/1.0'})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            ms = (time.monotonic() - t0) * 1000
            return {
                'ok': True, 'status': r.status, 'ms': ms,
                'headers': dict(r.headers), 'errore': None,
            }
    except urllib.error.HTTPError as e:
        ms = (time.monotonic() - t0) * 1000
        return {
            'ok': True, 'status': e.code, 'ms': ms,
            'headers': dict(e.headers), 'errore': None,
        }
    except Exception as e:
        return {'ok': False, 'status': None, 'ms': None, 'headers': {}, 'errore': str(e)}


def _test_redirect_http(hostname, timeout=8):
    """
    Testa che http://hostname reindrizzi a https://.
    Usa un opener senza redirect automatico.
    Ritorna (bool, str) → (reindirizzato_a_https, dettaglio).
    """
    url = f'http://{hostname}/'
    try:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        req = urllib.request.Request(url, headers={'User-Agent': 'MedInventory-Diag/1.0'})
        try:
            with opener.open(req, timeout=timeout) as r:
                return False, f'Nessun redirect (HTTP {r.status})'
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 307, 308):
                location = e.headers.get('Location', '')
                if location.startswith('https://'):
                    return True, f'Redirect {e.code} → {location}'
                return False, f'Redirect {e.code} → {location} (non HTTPS)'
            return False, f'HTTP {e.code}'
    except Exception as e:
        return None, f'Errore connessione: {e}'


def _controlla_headers_sicurezza(headers):
    """
    Verifica la presenza degli header di sicurezza HTTP.
    Ritorna lista di (bool, nome, valore|None, nota).
    """
    attesi = [
        ('strict-transport-security', 'HSTS',
         'Forza HTTPS per i prossimi 12 mesi'),
        ('x-frame-options', 'X-Frame-Options',
         'Protezione clickjacking'),
        ('x-content-type-options', 'X-Content-Type-Options',
         'Previene MIME-type sniffing'),
        ('referrer-policy', 'Referrer-Policy',
         'Limita dati nel Referer'),
        ('permissions-policy', 'Permissions-Policy',
         'Disabilita funzionalità browser non necessarie'),
    ]
    headers_lower = {k.lower(): v for k, v in headers.items()}
    risultati = []
    for chiave, nome, nota in attesi:
        valore = headers_lower.get(chiave)
        risultati.append((valore is not None, nome, valore, nota))
    return risultati


def _controlla_cookie_login(url_base, timeout=10):
    """
    Fa una POST fittizia al form di login e analizza i flag del cookie di sessione.
    Ritorna dict: {secure, httponly, samesite, trovato}.
    """
    ctx = ssl.create_default_context()
    url = url_base.rstrip('/') + '/login'
    dati = urllib.parse.urlencode({'email': 'test@test.it', 'password': 'test'}).encode()
    try:
        req = urllib.request.Request(
            url, data=dati, method='POST',
            headers={
                'User-Agent': 'MedInventory-Diag/1.0',
                'Content-Type': 'application/x-www-form-urlencoded',
            }
        )
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ctx))
        try:
            r = opener.open(req, timeout=timeout)
            raw_cookies = r.headers.get_all('Set-Cookie') or []
        except urllib.error.HTTPError as e:
            raw_cookies = e.headers.get_all('Set-Cookie') or []
    except Exception:
        return {'trovato': False, 'secure': None, 'httponly': None, 'samesite': None}

    if not raw_cookies:
        return {'trovato': False, 'secure': None, 'httponly': None, 'samesite': None}

    # Cerca il cookie di sessione Flask (di solito "session=...")
    cookie_str = ' '.join(raw_cookies).lower()
    return {
        'trovato':  True,
        'secure':   'secure'   in cookie_str,
        'httponly': 'httponly' in cookie_str,
        'samesite': 'samesite' in cookie_str,
    }


def esegui_test(config):
    """
    Diagnostica completa dell'integrazione Cloudflare Tunnel.
    """
    print()
    print("=" * 60)
    print("  Diagnostica Cloudflare Tunnel — MedInventory")
    print("=" * 60)

    host   = config.get('host', '127.0.0.1')
    porta  = config.get('port', 5000)
    cf_on  = cloudflare_attivo(config)
    fhttps = config.get('force_https', False)

    # ── Sezione 1: configurazione MedInventory ───────────────────────────────
    print("\n[1] Configurazione MedInventory")
    _print_check('info', f"cloudflare_mode : {cf_on}")
    _print_check('info', f"force_https     : {fhttps}")
    _print_check('info', f"host            : {host}  porta: {porta}")

    if cf_on and host == '0.0.0.0':
        _print_check(False, "host='0.0.0.0' con cloudflare_mode=true espone il "
                            "server HTTP direttamente. Usa host='127.0.0.1'.")
    elif cf_on and host == '127.0.0.1':
        _print_check(True, "host='127.0.0.1' — server non raggiungibile direttamente")

    if cf_on and not fhttps:
        _print_check(None, "cloudflare_mode=true ma force_https=false — "
                           "cookie Secure e HSTS non attivi")

    # ── Sezione 2: cloudflared installato ────────────────────────────────────
    print("\n[2] Installazione cloudflared")
    exe = _trova_cloudflared()
    if exe:
        versione = _versione_cloudflared(exe)
        _print_check(True, f"Trovato: {exe}")
        if versione:
            _print_check('info', versione)
    else:
        _print_check(False, "cloudflared non trovato nel PATH né nelle posizioni standard")
        if platform.system() == 'Windows':
            _print_check('info', "Installare con: winget install --id Cloudflare.cloudflared")
        else:
            _print_check('info', "Installare con: sudo apt install cloudflared")
            _print_check('info', "Oppure (binario diretto): sudo curl -fsSL "
                                 "https://github.com/cloudflare/cloudflared/releases/latest"
                                 "/download/cloudflared-linux-amd64 "
                                 "-o /usr/local/bin/cloudflared && sudo chmod +x /usr/local/bin/cloudflared")

    # ── Sezione 3: processo/servizio cloudflared ─────────────────────────────
    print("\n[3] Stato processo/servizio cloudflared")
    attivo_proc, dettaglio_proc = _servizio_cloudflared_attivo()
    if attivo_proc is True:
        _print_check(True, f"In esecuzione — {dettaglio_proc}")
    elif attivo_proc is False:
        _print_check(False, f"Non in esecuzione — {dettaglio_proc}")
        if platform.system() == 'Windows':
            _print_check('info', "Avvia con: cloudflared tunnel run <nome-tunnel>  "
                                 "oppure: sc start cloudflared  (se installato come servizio)")
        else:
            _print_check('info', "Avvia con: cloudflared tunnel run <nome-tunnel>  "
                                 "oppure: sudo systemctl start cloudflared  (se installato come servizio)")
    else:
        _print_check(None, f"Stato non determinabile — {dettaglio_proc}")

    # ── Sezione 4: file di configurazione cloudflared ────────────────────────
    print("\n[4] File di configurazione cloudflared")
    cf_cfg = _leggi_config_cloudflared()
    if cf_cfg:
        _print_check(True, f"Trovato: {cf_cfg['config_path']}")
        if cf_cfg.get('tunnel'):
            _print_check('info', f"Tunnel UUID  : {cf_cfg['tunnel']}")
        if cf_cfg.get('hostname'):
            _print_check('info', f"Hostname     : {cf_cfg['hostname']}")
        if cf_cfg.get('service'):
            _print_check('info', f"Service locale: {cf_cfg['service']}")
    else:
        _print_check(None, "File config.yml non trovato nelle posizioni standard")
        _print_check('info', "Consultare: CLOUDFLARE_TUNNEL.md — Parte 4")

    hostname_tunnel = cf_cfg.get('hostname')

    # ── Sezione 5: server locale MedInventory ────────────────────────────────
    print("\n[5] Server MedInventory locale")
    # Prova sia sull'host configurato sia su 127.0.0.1 (per il caso in cui il
    # server ascolti su localhost ma il test giri dallo stesso server)
    host_prova = '127.0.0.1' if host == '127.0.0.1' else host
    ok_locale, det_locale, ms_locale = _test_server_locale(host_prova, porta)
    if ok_locale:
        ms_str = f'{ms_locale:.0f} ms' if ms_locale is not None else ''
        _print_check(True, f"Raggiungibile su http://{host_prova}:{porta}  {ms_str}")
        _print_check('info', det_locale)
    else:
        _print_check(False, f"Non raggiungibile su http://{host_prova}:{porta}")
        _print_check('info', det_locale)
        _print_check('info', "Avviare MedInventory con: python run_production.py")

    # ── Sezione 6: tunnel HTTPS (se hostname disponibile) ────────────────────
    if hostname_tunnel:
        print(f"\n[6] Tunnel HTTPS — https://{hostname_tunnel}")

        url_https = f'https://{hostname_tunnel}'
        ris = _test_url_https(url_https + '/login')
        if ris['ok']:
            ms_str = f'{ris["ms"]:.0f} ms' if ris['ms'] else ''
            _print_check(True, f"Raggiungibile via HTTPS  {ms_str}")
            _print_check('info', f"HTTP {ris['status']}")
        else:
            _print_check(False, f"Non raggiungibile: {ris['errore']}")
            if attivo_proc is False:
                _print_check('info', "Verifica che cloudflared sia in esecuzione (sezione 3)")

        # ── Sezione 7: header di sicurezza ───────────────────────────────────
        if ris['ok'] and ris['headers']:
            print(f"\n[7] Header di sicurezza HTTP")
            checks = _controlla_headers_sicurezza(ris['headers'])
            for presente, nome, valore, nota in checks:
                if presente:
                    _print_check(True, f"{nome}: {valore}")
                else:
                    _print_check(False, f"{nome} assente — {nota}")

        # ── Sezione 8: cookie di sessione ────────────────────────────────────
        print(f"\n[8] Flag cookie di sessione")
        cookie_info = _controlla_cookie_login(url_https)
        if cookie_info['trovato']:
            s  = cookie_info['secure']
            h  = cookie_info['httponly']
            sa = cookie_info['samesite']
            _print_check(s,  f"Secure   {'attivo' if s else 'NON attivo — cookie inviato anche su HTTP'}")
            _print_check(h,  f"HttpOnly {'attivo' if h else 'NON attivo — cookie accessibile da JavaScript'}")
            _print_check(sa, f"SameSite {'attivo' if sa else 'NON attivo — protezione CSRF assente'}")
            if not s and fhttps:
                _print_check(None, "force_https=true ma Secure non rilevato — riavvia l'applicazione?")
        else:
            _print_check(None, "Nessun cookie di sessione rilevato nella risposta al login "
                               "(normale se il server non risponde al POST di test)")

        # ── Sezione 9: redirect HTTP→HTTPS ───────────────────────────────────
        print(f"\n[9] Redirect HTTP → HTTPS")
        if fhttps:
            reindirizzato, det_redir = _test_redirect_http(hostname_tunnel)
            if reindirizzato is True:
                _print_check(True, det_redir)
            elif reindirizzato is False:
                _print_check(False, f"Nessun redirect HTTPS rilevato — {det_redir}")
                _print_check('info', "Abilitare 'Always Use HTTPS' nel pannello Cloudflare "
                                     "oppure verificare che force_https=true sia attivo")
            else:
                _print_check(None, det_redir)
        else:
            _print_check('info', "force_https=false — redirect HTTP→HTTPS non attivo")
            _print_check('info', "Per abilitarlo: python cloudflare_mode.py --on")

        # ── Sezione 10: DNS resolution ───────────────────────────────────────
        print(f"\n[10] Risoluzione DNS")
        try:
            ip = socket.gethostbyname(hostname_tunnel)
            _print_check(True, f"{hostname_tunnel} → {ip}")
            # Gli IP di Cloudflare sono nel range 104.x / 172.x / 198.x
            cf_ranges = ('104.', '172.64.', '172.65.', '172.66.', '172.67.',
                         '198.41.', '141.', '162.', '103.')
            is_cf = any(ip.startswith(r) for r in cf_ranges)
            if is_cf:
                _print_check(True, "IP appartenente alla rete Cloudflare")
            else:
                _print_check(None, f"IP {ip} non riconosciuto come Cloudflare — "
                                   "verifica il record CNAME nel pannello DNS")
        except socket.gaierror as e:
            _print_check(False, f"DNS non risolve {hostname_tunnel}: {e}")
            _print_check('info', "Verifica il record CNAME in Cloudflare Dashboard → DNS")

    else:
        print(f"\n[6–10] Saltati — hostname tunnel non trovato in config.yml")
        _print_check('info', "Configura il tunnel e poi riesegui il test")

    # ── Riepilogo ─────────────────────────────────────────────────────────────
    print()
    print("─" * 60)
    print("  Fine diagnostica.")
    if not cf_on:
        print("  NOTA: cloudflare_mode=false — per attivare: "
              "python cloudflare_mode.py --on")
    print("─" * 60)
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Gestisce la modalità Cloudflare Tunnel di MedInventory'
    )
    gruppo = parser.add_mutually_exclusive_group()
    gruppo.add_argument('--status', action='store_true',
                        help='Mostra solo lo stato attuale senza modificare nulla')
    gruppo.add_argument('--on',   action='store_true', help='Attiva la modalità Cloudflare')
    gruppo.add_argument('--off',  action='store_true', help='Disattiva la modalità Cloudflare')
    gruppo.add_argument('--test', action='store_true', help='Diagnostica completa del tunnel')
    args = parser.parse_args()

    config  = leggi_config()
    corrente = cloudflare_attivo(config)

    if args.test:
        esegui_test(config)
        return

    print("=" * 55)
    print("  MedInventory — Gestione modalità Cloudflare Tunnel")
    print("=" * 55)
    print(f"\nStato attuale:\n  {descrivi_stato(corrente, config)}\n")

    if args.status:
        return

    if args.on:
        nuovo = True
    elif args.off:
        nuovo = False
    else:
        nuovo = not corrente
        print(f"Nuovo stato:\n  {descrivi_stato(nuovo, config)}\n")
        risposta = input("Confermi il cambio? [s/N] ").strip().lower()
        if risposta not in ('s', 'si', 'sì', 'y', 'yes'):
            print("Operazione annullata.")
            return

    if nuovo == corrente:
        print("Nessuna modifica — la modalità è già quella richiesta.")
        return

    if not os.path.exists(CONFIG_LOCAL):
        print("AVVISO: config.local.json non trovato — verrà creato.")

    if nuovo:
        attiva(config)
    else:
        disattiva(config)

    scrivi_config(config)

    print(f"\n✓ Modalità impostata:\n  {descrivi_stato(nuovo, config)}")
    print("\nRiavvia l'applicazione per applicare le modifiche:")
    print("  python run_production.py")

    if nuovo:
        print("\nPassaggi successivi consigliati:")
        print("  1. Assicurati che cloudflared sia configurato e in esecuzione")
        print("  2. Cloudflare Dashboard → SSL/TLS → 'Full' o 'Full (strict)'")
        print("  3. Cloudflare Dashboard → SSL/TLS → abilita 'Always Use HTTPS'")
        print("  4. Verifica la guida: CLOUDFLARE_TUNNEL.md")
        print("  5. Testa con: python cloudflare_mode.py --test")
    else:
        print("\nL'applicazione tornerà accessibile su tutta la LAN locale.")


if __name__ == '__main__':
    main()
