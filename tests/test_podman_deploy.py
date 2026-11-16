"""Test statici del deploy Podman: leggono i file, non richiedono Podman né root."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parent.parent
PODMAN = "deploy/podman/"


def _trova_bash():
    """Una bash vera: Git Bash se c'è, altrimenti quella nel PATH.

    Lo stub WSL di System32 (e gli alias di WindowsApps) non è una bash utilizzabile.
    """
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.exists():
        return str(git_bash)
    trovata = shutil.which("bash")
    if trovata and not re.search(r"[\\/](System32|WindowsApps)[\\/]", trovata, re.I):
        return trovata
    return None


BASH = _trova_bash()


def _leggi(relativo: str) -> str:
    return (RADICE / relativo).read_text(encoding="utf-8")




def test_containerfile_parte_da_fedora_e_non_fissa_il_comando():
    testo = _leggi("Containerfile")
    assert testo.startswith("FROM registry.fedoraproject.org/fedora:42")
    assert "ENTRYPOINT" not in testo
    assert "CMD" not in testo
    assert "MEDINVENTORY_RADICE=/stato" in testo
    assert "EXPOSE 5000" in testo


def test_containerfile_installa_il_lock_senza_risolvere():
    testo = _leggi("Containerfile")
    assert "--no-deps" in testo
    assert "pip check" in testo
    assert "--require-hashes" not in testo   # il lock non ha hash
    assert "requirements.txt" not in testo   # mai gli intervalli


def test_containerfile_scarta_pystray():
    assert "grep -vi '^pystray=='" in _leggi("Containerfile")


def test_pystray_solo_nel_launcher():
    # Se un modulo dell'app importasse pystray, l'immagine partirebbe rotta.
    colpevoli = [p.name for p in RADICE.glob("*.py")
                 if re.search(r"^\s*(import|from)\s+pystray", p.read_text(encoding="utf-8"), re.M)]
    assert colpevoli == []


def test_containerignore_esclude_stato_e_segreti():
    righe = _leggi(".containerignore").splitlines()
    for voce in ("config.json", "config.local.json", "config.json.bak_*", "data/",
                 "uploads/", "backups/", "logs/", ".git/", "venv/", "migliorie/",
                 "tests/", "launcher.pyw", "*.bat"):
        assert voce in righe
    assert "config.example.json" not in righe
    assert "deploy/" not in righe
    for voce in ("**/*.sqlite*", "**/*.bak_*", "*.key", "*.pem", ".env"):
        assert voce in righe
    assert "schema.sql" not in righe


ESTENSIONI_UNITA = {".pod", ".container", ".volume", ".conf", ".service", ".timer"}


def _unita():
    return sorted(p for p in (RADICE / PODMAN).glob("*") if p.suffix in ESTENSIONI_UNITA)


def _righe(relativo):
    # Righe utili: senza vuote e senza commenti.
    return [r.strip() for r in _leggi(relativo).splitlines()
            if r.strip() and not r.strip().startswith("#")]


def test_pod_pubblica_solo_la_8443():
    righe = _righe(PODMAN + "custode.pod")
    assert "PodName=custode" in righe
    assert [r for r in righe if r.startswith("PublishPort=")] == ["PublishPort=8443:443"]
    assert "PublishPort=443:443" not in righe
    assert "WantedBy=default.target" in righe
    # La 5000 resta dentro il pod: nessun container pubblica porte per conto suo.
    for nome in ("custode-web", "custode-tunnel", "custode-lan"):
        assert not [r for r in _righe(PODMAN + f"{nome}.container")
                    if r.startswith("PublishPort=")]


def test_contenitori_nel_pod_con_riavvio_e_avvio_al_boot():
    for nome in ("custode-web", "custode-tunnel", "custode-lan"):
        righe = _righe(PODMAN + f"{nome}.container")
        assert "Pod=custode.pod" in righe
        assert f"ContainerName={nome}" in righe
        assert "Restart=always" in righe
        assert "WantedBy=default.target" in righe
    for nome in ("custode-tunnel", "custode-lan"):
        righe = _righe(PODMAN + f"{nome}.container")
        assert "After=custode-web.service" in righe
        assert "Requires=custode-web.service" in righe


def test_web_avviato_solo_quando_risponde_health_ready():
    righe = _righe(PODMAN + "custode-web.container")
    assert "Image=localhost/custode:latest" in righe
    assert "Exec=python3 /app/run_production.py" in righe
    assert "Environment=MEDINVENTORY_RADICE=/stato" in righe
    assert "Notify=healthy" in righe
    salute = [r for r in righe if r.startswith("HealthCmd=")]
    assert len(salute) == 1
    assert "http://127.0.0.1:5000/health/ready" in salute[0]
    # Durante un ripristino la rotta risponde 503 di proposito: niente riavvio automatico.
    assert not [r for r in righe if r.startswith("HealthOnFailure=")]
    volumi = [r for r in righe if r.startswith("Volume=")]
    assert volumi == ["Volume=custode-stato.volume:/stato",
                      "Volume=/var/backups/custode:/stato/backups:Z"]


def test_web_usa_il_fuso_dell_host():
    assert "Timezone=local" in _righe(PODMAN + "custode-web.container")


def test_volume_di_stato_ha_nome_esplicito():
    assert "VolumeName=custode-stato" in _righe(PODMAN + "custode-stato.volume")


def test_immagini_esterne_fissate():
    fissate = {
        "custode-tunnel.container": "Image=docker.io/cloudflare/cloudflared:2026.11.0",
        "custode-lan.container": "Image=docker.io/library/nginx:1.28.0-alpine",
    }
    for nome, riga in fissate.items():
        assert riga in _righe(PODMAN + nome)
    contenitori = sorted((RADICE / PODMAN).glob("*.container"))
    assert len(contenitori) >= 3
    for percorso in contenitori:
        immagini = [r for r in _righe(PODMAN + percorso.name) if r.startswith("Image=")]
        assert len(immagini) == 1, percorso.name
        if immagini[0].startswith("Image=localhost/"):
            continue   # l'immagine costruita qui: l'etichetta la decide la build
        ultimo = immagini[0].rsplit("/", 1)[-1]
        assert ":" in ultimo and not ultimo.endswith(":latest"), percorso.name


def test_tunnel_legge_il_token_solo_dal_secret():
    testo = _leggi(PODMAN + "custode-tunnel.container")
    righe = _righe(PODMAN + "custode-tunnel.container")
    assert "Secret=custode-tunnel-token,type=env,target=TUNNEL_TOKEN" in righe
    assert "Exec=tunnel --no-autoupdate run" in righe
    assert [r for r in righe if "TUNNEL_TOKEN" in r] == [
        "Secret=custode-tunnel-token,type=env,target=TUNNEL_TOKEN"]
    assert "--token" not in testo
    assert not [r for r in righe if r.startswith(("Environment=", "EnvironmentFile=", "Volume="))]


def test_relabel_solo_sulle_cartelle_di_custode():
    ammessi = {
        "custode-web.container": ["Volume=/var/backups/custode:/stato/backups:Z"],
        "custode-lan.container": ["Volume=%h/lan/conf.d:/etc/nginx/conf.d:ro,Z",
                                  "Volume=%h/lan/certs:/etc/nginx/certs:ro,Z"],
    }
    unita = _unita()
    assert set(ammessi) <= {p.name for p in unita}
    for percorso in unita:
        con_relabel = [r for r in _righe(PODMAN + percorso.name)
                       if re.search(r"[:,][zZ]$", r)]
        assert con_relabel == ammessi.get(percorso.name, []), percorso.name


def test_nginx_sovrascrive_le_intestazioni_inoltrate():
    testo = _leggi(PODMAN + "custode-lan.conf")
    righe = _righe(PODMAN + "custode-lan.conf")
    # L'app si fida di un solo salto: nginx scrive ciò che ha visto, non accoda.
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in righe
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in righe
    assert "proxy_set_header X-Forwarded-Host $host;" in righe
    assert "$proxy_add_x_forwarded_for" not in testo
    assert "$http_x_forwarded" not in testo
    for intestazione in ("X-Forwarded-For", "X-Forwarded-Proto", "X-Forwarded-Host"):
        assert len([r for r in righe if r.startswith(f"proxy_set_header {intestazione} ")]) == 1


def test_nginx_termina_tls_e_inoltra_solo_all_app():
    righe = _righe(PODMAN + "custode-lan.conf")
    assert [r for r in righe if r.startswith("listen ")] == ["listen 443 ssl;"]
    assert "ssl_certificate /etc/nginx/certs/custode.crt;" in righe
    assert "ssl_certificate_key /etc/nginx/certs/custode.key;" in righe
    assert [r for r in righe if r.startswith("proxy_pass ")] == [
        "proxy_pass http://127.0.0.1:5000;"]
    # Lo stesso limite dell'app (MAX_CONTENT_LENGTH, 32 MB).
    assert "client_max_body_size 32m;" in righe


def test_nessun_quadlet_nomina_la_ca():
    unita = _unita()
    assert len(unita) >= 8
    for percorso in unita:
        testo = percorso.read_text(encoding="utf-8")
        for vietato in ("ca.key", "minventory-ca", "/etc/pki"):
            assert vietato not in testo, f"{percorso.name}: {vietato}"


def test_diagnosi_non_riavvia_un_app_ferma():
    righe = _righe(PODMAN + "custode-diagnosi.service")
    # After= ordina soltanto; Requires=, Wants= e simili farebbero ripartire
    # un'app fermata di proposito.
    for chiave in ("Requires=", "Wants=", "BindsTo=", "Upholds=", "PartOf=", "OnFailure="):
        assert not [r for r in righe if r.startswith(chiave)], chiave
    assert "After=custode-web.service" in righe
    assert "Type=oneshot" in righe
    # A container assente l'esecuzione viene saltata, non fallisce.
    # ... e anche se esiste ma è fermo ($$ = $ letterale per systemd).
    cond = [r for r in righe if r.startswith("ExecCondition=")]
    assert len(cond) == 1
    assert "{{.State.Running}} custode-web" in cond[0]
    assert "= true ]" in cond[0] and "$$(" in cond[0]
    assert [r for r in righe if r.startswith("ExecStart=")] == [
        "ExecStart=/usr/bin/podman exec custode-web python3 /app/manutenzione.py diagnosi"]
    for testo in (_leggi(PODMAN + "custode-diagnosi.service"),
                  _leggi(PODMAN + "custode-diagnosi.timer")):
        assert "systemctl" not in testo
        assert "podman start" not in testo


def test_timer_diagnosi_giornaliero():
    righe = _righe(PODMAN + "custode-diagnosi.timer")
    assert "OnCalendar=*-*-* 06:30:00" in righe
    assert "Persistent=true" in righe
    assert "WantedBy=timers.target" in righe
    assert not [r for r in righe if "custode-web" in r]


# --- Task 8: wrapper `custode` -------------------------------------------------

WRAPPER = PODMAN + "custode"

SCRIPT_D7 = (
    "manutenzione.py", "migrate.py", "toggle_modalita.py", "pulisci_uploads.py",
    "importa_installazione.py", "crea_superadmin.py", "seed.py",
    "cloudflare_mode.py", "recover_import_history.py",
)


def _bash(script, chiamata, *argomenti):
    """Carica uno script con --solo-funzioni e chiama una sua funzione pura.

    Gli argomenti arrivano come $1, $2...: mai interpolati nel testo del comando.
    Percorso completo di bash: su Windows "bash" nudo risolve lo stub WSL.
    """
    import os
    ambiente = dict(os.environ, MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
    return subprocess.run(
        [BASH, "-c", f". ./{script} --solo-funzioni; {chiamata}", "_", *argomenti],
        capture_output=True, text=True, cwd=RADICE, env=ambiente,
    )


def _corpo(testo, nome):
    """Corpo di una funzione bash scritta come `nome() {` ... `}` a inizio riga."""
    trovato = re.search(rf"^{nome}\(\) \{{\n(.*?)^\}}", testo, re.S | re.M)
    assert trovato, f"funzione {nome} non trovata"
    return trovato.group(1)


@pytest.mark.skipif(BASH is None, reason="bash non disponibile")
def test_wrapper_e_bash_valido():
    subprocess.run([BASH, "-n", WRAPPER], check=True, cwd=RADICE)


def test_wrapper_lista_chiusa_come_d7():
    testo = _leggi(WRAPPER)
    riga = re.search(r'^SCRIPT_AMMESSI="([^"]*)"$', testo, re.M)
    assert riga, "SCRIPT_AMMESSI deve stare su una riga sola"
    assert sorted(riga.group(1).split()) == sorted(SCRIPT_D7)
    for nome in SCRIPT_D7:
        assert (RADICE / nome).is_file(), f"{nome} non esiste nel repository"


@pytest.mark.skipif(BASH is None, reason="bash non disponibile")
@pytest.mark.parametrize("nome", SCRIPT_D7)
def test_wrapper_script_ammesso_accetta(nome):
    assert _bash(WRAPPER, 'script_ammesso "$1"', nome).returncode == 0


@pytest.mark.skipif(BASH is None, reason="bash non disponibile")
@pytest.mark.parametrize("nome", [
    "app.py", "run_production.py", "../manutenzione.py", "/app/manutenzione.py",
    "manutenzione", "", "manutenzione.py; id", "MANUTENZIONE.PY",
])
def test_wrapper_script_ammesso_rifiuta(nome):
    # 1 esatto: 127 vorrebbe dire "funzione non definita", non "rifiutato".
    assert _bash(WRAPPER, 'script_ammesso "$1"', nome).returncode == 1


@pytest.mark.skipif(BASH is None, reason="bash non disponibile")
@pytest.mark.parametrize("argomenti, riavvia", [
    (["toggle_modalita.py"], True),
    (["toggle_modalita.py", "--single"], True),
    (["toggle_modalita.py", "--multi"], True),
    (["toggle_modalita.py", "--status"], False),
    (["cloudflare_mode.py", "--on"], True),
    (["cloudflare_mode.py", "--off"], True),
    (["cloudflare_mode.py", "--status"], False),
    (["cloudflare_mode.py", "--test"], False),
    (["cloudflare_mode.py", "--test", "--hostname", "custode.example.it"], False),
    (["manutenzione.py", "stato"], False),
    (["migrate.py", "--yes"], False),
])
def test_wrapper_richiede_riavvio(argomenti, riavvia):
    esito = _bash(WRAPPER, 'richiede_riavvio "$@"', *argomenti)
    assert esito.returncode == (0 if riavvia else 1), esito.stderr


@pytest.mark.skipif(BASH is None, reason="bash non disponibile")
def test_esterna_non_rietichetta_mai():
    esito = _bash(WRAPPER, 'argomenti_esterna "$1"', "/srv/altra-installazione")
    assert esito.returncode == 0, esito.stderr
    argomenti = esito.stdout.splitlines()
    assert argomenti[:2] == ["--security-opt", "label=disable"]
    montaggi = [argomenti[i + 1] for i, a in enumerate(argomenti) if a == "-v"]
    assert montaggi == [
        "custode-stato:/stato",
        "/var/backups/custode:/stato/backups",
        "/srv/altra-installazione:/esterna:ro",
    ]
    for montaggio in montaggi:
        opzioni = ",".join(montaggio.split(":")[2:]).split(",")
        assert "z" not in opzioni and "Z" not in opzioni, montaggio
    # E nel sorgente: il solo relabel del wrapper è quello di `fermo`, a pod fermo.
    testo = _leggi(WRAPPER)
    assert ":z" not in testo
    assert testo.count(":Z") == 1
    assert ":Z" in _corpo(testo, "esegui_a_parte")
    assert "label=disable" not in _corpo(testo, "esegui_a_parte")


@pytest.mark.skipif(BASH is None, reason="bash non disponibile")
@pytest.mark.parametrize("cartella", ["relativa/dir", "/srv/a:Z", "/srv/a,b", "", "/"])
def test_wrapper_esterna_rifiuta_cartelle_ambigue(cartella):
    # ":" e "," aggiungerebbero opzioni al montaggio: è da lì che entrerebbe un relabel.
    esito = _bash(WRAPPER, 'argomenti_esterna "$1"', cartella)
    assert esito.returncode == 1 and esito.stdout == ""


@pytest.mark.skipif(BASH is None, reason="bash non disponibile")
@pytest.mark.parametrize("argomenti, scrive", [
    (["importa_installazione.py", "/esterna"], True),
    (["importa_installazione.py", "/esterna", "--struttura-nome", "Clinica"], True),
    (["importa_installazione.py", "/esterna", "--dry-run"], False),
    (["importa_installazione.py", "/esterna", "--struttura-nome", "Clinica", "--dry-run"], False),
    (["manutenzione.py", "--db", "/esterna/data/database.sqlite", "stato"], False),
])
def test_wrapper_esterna_scrive(argomenti, scrive):
    esito = _bash(WRAPPER, 'esterna_scrive "$@"', *argomenti)
    assert esito.returncode == (0 if scrive else 1), esito.stderr


def test_wrapper_non_nomina_la_ca_e_non_traccia():
    testo = _leggi(WRAPPER)
    for vietato in ("ca.key", "minventory-ca", "set -x", "xtrace", "TUNNEL_TOKEN"):
        assert vietato not in testo, vietato


def test_wrapper_riaccende_sempre_il_pod():
    corpo = _corpo(_leggi(WRAPPER), "con_pod_fermo")
    # Il trap si arma prima di fermare: se anche lo stop fallisce, il pod riparte.
    assert corpo.index("trap riaccendi_pod EXIT") < corpo.index('stop "$POD_SERVICE"')
    assert '"$@" || rc=$?' in corpo
    assert 'return "$rc"' in corpo


def test_wrapper_tty_deciso_fuori_dalla_sostituzione_di_comando():
    # In $(...) fd 1 e' la pipe di cattura: [ -t 1 ] sarebbe sempre falso e
    # getpass (utenti azzera, menu) girerebbe senza pty, facendo eco alla password.
    testo = _leggi(WRAPPER)
    assert "opzione_tty" not in testo
    assert "$(opzione_tty" not in testo
    assert "TTY=(-t)" in testo
    assert '"${TTY[@]}"' in testo
    assert "shellcheck disable=SC2046" not in testo


def test_wrapper_tty_vuoto_senza_terminale():
    # Caricato con --solo-funzioni, fuori da un terminale, TTY e' un array vuoto
    # e la sua espansione non rompe set -u.
    esito = _bash(WRAPPER, 'echo "n=${#TTY[@]}"; echo x "${TTY[@]}" y')
    assert esito.returncode == 0, esito.stderr
    assert "n=0" in esito.stdout
    assert "x y" in esito.stdout


# --- Task 9: configura.sh ------------------------------------------------------

CONFIGURA = PODMAN + "configura.sh"
TOKEN_FINTO = "eyJh" + "A" * 60


@pytest.mark.skipif(BASH is None, reason="bash non disponibile")
def test_configura_e_bash_valido():
    subprocess.run([BASH, "-n", CONFIGURA], check=True, cwd=RADICE)


def test_configura_offre_ogni_comando():
    testo = _leggi(CONFIGURA)
    for comando in ("installa", "token", "certificato", "stato", "aggiorna",
                    "disinstalla", "nuovo", "importa", "avvia", "tunnel-off"):
        assert f"{comando})" in testo, comando


@pytest.mark.skipif(BASH is None, reason="bash non disponibile")
@pytest.mark.parametrize("incollato", [
    TOKEN_FINTO,
    "sudo cloudflared service install " + TOKEN_FINTO,
    "  " + TOKEN_FINTO + "  ",
])
def test_configura_normalizza_token_estrae(incollato):
    esito = _bash(CONFIGURA, 'normalizza_token "$1"', incollato)
    assert esito.returncode == 0 and esito.stdout.strip() == TOKEN_FINTO


@pytest.mark.skipif(BASH is None, reason="bash non disponibile")
@pytest.mark.parametrize("incollato", ["", "   ", "corto", "token con caratteri ;rm"])
def test_configura_normalizza_token_rifiuta(incollato):
    esito = _bash(CONFIGURA, 'normalizza_token "$1"', incollato)
    assert esito.returncode == 1 and esito.stdout == ""


def test_configura_non_espone_mai_il_token():
    testo = _leggi(CONFIGURA)
    assert "read -rs" in testo
    assert "podman secret create --replace custode-tunnel-token -" in testo
    assert "set -x" not in testo and "xtrace" not in testo
    assert "TUNNEL_TOKEN=" not in testo
    # Il token esce dalla variabile in un solo punto: stdin di `podman secret create`.
    consegna = "printf '%s' \"$token\" | as_custode podman secret create"
    for riga in testo.splitlines():
        if "$token" in riga or "${token" in riga:
            assert riga.strip().startswith(consegna), riga
    # Il testo incollato passa solo a normalizza_token (funzione di shell, non un processo).
    for riga in testo.splitlines():
        if "$incollato" in riga:
            assert riga.strip() == 'if ! token=$(normalizza_token "$incollato"); then', riga


def test_chiave_della_ca_mai_verso_il_container():
    # Righe logiche: le continuazioni con "\" fanno parte dello stesso comando.
    righe = [r.strip() for r in _leggi(CONFIGURA).replace("\\\n", " ").splitlines()]
    ammessi = (
        "#",                              # commenti
        'CA_KEY="$CA_DIR/ca.key"',        # la definizione
        'if [ ! -f "$CA_KEY" ]',          # il controllo di esistenza
        'if [ -f "$CA_KEY" ]',
        "openssl req -x509 ",             # creazione della CA
        "openssl x509 -req ",             # firma del certificato del server
        'chmod 600 "$CA_KEY"',
        "echo ", "err ",                  # messaggi all'operatore
    )
    citazioni = [r for r in righe if "ca.key" in r or "CA_KEY" in r]
    assert citazioni, "configura.sh deve firmare con la CA"
    for riga in citazioni:
        assert riga.startswith(ammessi), riga
        for vietato in ("podman", "as_custode", "CERT_DIR", "lan/certs", "-v ",
                        "Volume", "cp ", "install ", "mv ", "tar ", "rsync"):
            assert vietato not in riga, riga
    # Verso ~custode/lan/certs viaggiano solo custode.crt e custode.key, uno per riga.
    copie = [r for r in righe if "$CERT_DIR" in r and r.startswith(("install ", "cp ", "mv "))]
    assert len(copie) == 2
    for riga in copie:
        assert re.search(r'"\$CERT_DIR/custode\.(crt|key)"$', riga), riga
        assert "*" not in riga and "$CA_DIR/." not in riga and " -r" not in riga, riga
    # Nessuna copia a tappeto della cartella della CA.
    for riga in righe:
        assert '"$CA_DIR"/*' not in riga and "$CA_DIR/*" not in riga, riga


def test_configura_subuid_diversi_da_mcorsi():
    testo = _leggi(CONFIGURA)
    assert "SUBID=300000-365535" in testo
    assert '--add-subuids "$SUBID" --add-subgids "$SUBID"' in testo
    assert "200000" not in testo and "265535" not in testo


def test_configura_non_tocca_le_porte_privilegiate():
    testo = _leggi(CONFIGURA)
    assert "PORTA_LAN=8443" in testo
    # Nessun valore scritto per la soglia, tranne il ripristino a 1024.
    valori = re.findall(r"ip_unprivileged_port_start=(\d+)", testo)
    assert valori == ["1024"]
    assert "SYSCTL_FILE" not in testo and "apri_porta_443" not in testo
    # La migrazione rimuove solo il file di Custode, in installa, prima delle unità.
    assert "SYSCTL_VECCHIO=/etc/sysctl.d/90-custode-porta-443.conf" in testo
    assert 'rm -f "$SYSCTL_VECCHIO"' in testo
    installa = _corpo(testo, "cmd_installa")
    assert installa.index("ripristina_soglia_porte") < installa.index("installa_unita")
    # Firewall: la porta, non il servizio https (che può servire ad altro).
    assert '--add-port="$PORTA_LAN/tcp"' in testo
    assert "--add-service=https" not in testo
    # Il pod parte in un punto solo.
    assert len(re.findall(r'(?:re)?start "\$POD_SERVICE"', testo)) == 1


def test_configura_prepara_utente_e_host():
    testo = _leggi(CONFIGURA)
    for atteso in (
        'useradd --system --create-home --shell /sbin/nologin "$APP_USER"',
        'loginctl enable-linger "$APP_USER"',
        'install -d -o "$APP_USER" -g "$APP_USER" -m 0750 "$BACKUP_DIR"',
        "podman build --network=host",
        'podman save "$IMAGE" | as_custode podman load',
        'install -m 0755 "$SCRIPT_DIR/custode" /usr/local/bin/custode',
    ):
        assert atteso in testo, atteso


def test_configura_log_dal_journal_di_sistema():
    # "journalctl --user -M utente@" fallisce da root: si legge per CONTAINER_NAME.
    testo = _leggi(CONFIGURA)
    assert "journalctl --user" not in testo
    stato = _corpo(testo, "cmd_stato")
    for atteso in ("CONTAINER_NAME=", "custode-web", "custode-tunnel", "custode-lan",
                   "podman ps --pod", '"$TIMER"'):
        assert atteso in stato, atteso


def test_configura_importa_adegua_e_si_ferma():
    corpo = _corpo(_leggi(CONFIGURA), "cmd_importa")
    for voce in ("data", "uploads", "backups", "config.json", "config.local.json"):
        assert voce in corpo, voce
    prova = corpo.index("config_container.py adegua --radice /sorgente --prova")
    scrittura = corpo.index("rm -rf /stato/data /stato/uploads")
    assert prova < scrittura, "la prova di adegua precede ogni scrittura nel volume"
    assert corpo.index("config_container.py adegua;") > scrittura
    assert "avvia_pod" not in corpo, "importa non avvia: prima token e vecchie unità"
    assert 'is-active --quiet "$VECCHIO_SERVIZIO"' in corpo


def test_configura_importa_non_copia_il_contesto_selinux():
    corpo = _corpo(_leggi(CONFIGURA), "cmd_importa")
    riga = next(r for r in corpo.splitlines() if '"/sorgente/$voce"' in r and "cp " in r)
    assert "cp -a" not in riga, "cp -a porta l'etichetta MCS privata del mount :Z nel volume"
    assert "--preserve=mode,ownership,timestamps" in riga


def test_configura_messaggi_della_migrazione():
    testo = _leggi(CONFIGURA)
    for atteso in (
        "due processi cloudflared, uno è di mCorsi",
        "(porta 5100): non va toccato",
        "ps -eo pid,user,unit,args | grep '[c]loudflared' | sed 's/--token [^ ]*/--token ***/'",
        "due repliche dello stesso tunnel",
        "502",
        'NON usare "Refresh token"',
        "Zero Trust -> Networks -> Tunnels",
        "systemctl stop $VECCHIO_SERVIZIO",
        "systemctl disable --now",
        "encryption_key",
    ):
        assert atteso in testo, atteso


@pytest.mark.skipif(BASH is None, reason="bash non disponibile")
@pytest.mark.parametrize("nome, valido", [
    ("custode.studio.lan", True),
    ("srv-01", True),
    ("", False),
    ("nome con spazi", False),
    ("x;rm -rf", False),
    ("custode.lan\nIP.2 = 10.0.0.1", False),
])
def test_configura_nome_host_valido(nome, valido):
    # Nome e IP finiscono in san.cnf e in -subj: un a capo aggiungerebbe un SAN.
    esito = _bash(CONFIGURA, 'nome_host_valido "$1"', nome)
    assert esito.returncode == (0 if valido else 1), esito.stderr


@pytest.mark.skipif(BASH is None, reason="bash non disponibile")
@pytest.mark.parametrize("ip, valido", [
    ("192.168.1.215", True),
    ("999.1.1.1", False),
    ("1.2.3", False),
    ("1.2.3.4\nDNS.2 = esempio.it", False),
    ("", False),
])
def test_configura_ip_valido(ip, valido):
    esito = _bash(CONFIGURA, 'ip_valido "$1"', ip)
    assert esito.returncode == (0 if valido else 1), esito.stderr


# Verdetti del controllore (F1, F6, F10, F11)

def test_configura_indirizzo_interno_mai_localhost():
    # F1: solo il nome dell'immagine puo' contenere "localhost".
    testo = _leggi(CONFIGURA).replace("localhost/custode:latest", "")
    assert "localhost" not in testo


def test_configura_eseguibile_nell_immagine_e_in_git():
    # F6: il bit di esecuzione nel Containerfile e fine riga LF forzati.
    assert "deploy/podman/configura.sh" in _leggi("Containerfile")
    chmod = re.search(r"RUN chmod \+x ([^&]*?)\s*&&", _leggi("Containerfile"), re.S)
    assert chmod and "deploy/podman/configura.sh" in chmod.group(1)
    assert "|| true" not in chmod.group(0) and "2>/dev/null" not in chmod.group(0)
    assert "deploy/podman/configura.sh" in _leggi(".gitattributes")
    assert b"\r\n" not in (RADICE / CONFIGURA).read_bytes()


def test_configura_importa_pulisce_la_copia_di_lavoro_su_ogni_uscita():
    # F10: un trap RETURN non scatta a set -e ne' su un errore del chiamante.
    testo = _leggi(CONFIGURA)
    assert "RETURN" not in testo
    corpo = _corpo(testo, "cmd_importa")
    trap = [r for r in corpo.splitlines() if r.strip().startswith("trap ")]
    assert any("EXIT" in r and "rm -rf" in r for r in trap), trap
    # La copia viene creata dentro lo stesso blocco che installa il trap.
    assert corpo.index("trap ") > corpo.index("mktemp -d /var/tmp/custode-importa")
    assert corpo.index("trap ") < corpo.index("cp -a")


def test_configura_nuovo_non_monta_i_backup():
    # F11: seed.py non scrive in backups: nessuna superficie :Z in piu'.
    corpo = _corpo(_leggi(CONFIGURA), "cmd_nuovo")
    assert "BACKUP_DIR" not in corpo and ":Z" not in corpo
    assert "seed.py" in corpo and "config_container.py adegua" in corpo
    assert "avvia_pod" not in corpo


def test_configura_non_tocca_altri_cloudflared():
    testo = _leggi(CONFIGURA)
    for vietato in ("pkill", "killall", "kill ", "systemctl stop cloudflared",
                    "systemctl disable cloudflared"):
        assert vietato not in testo, vietato


@pytest.mark.skipif(BASH is None, reason="bash non disponibile")
def test_configura_menu_errexit_attivo_e_fallimento_segnalato():
    """Un passo fallito dal menu ferma il comando, e il menu lo dice e prosegue."""
    import os
    ambiente = dict(os.environ, MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
    copione = (
        ". ./deploy/podman/configura.sh --solo-funzioni; "
        "esegui() { false; echo PASSO_DOPO_IL_FALLIMENTO; }; "
        "menu"
    )
    esito = subprocess.run(
        [BASH, "-c", copione], input=b"1\n4\n0\n", capture_output=True,
        cwd=RADICE, env=ambiente,
    )
    # byte e non testo: su Windows il testo trasformerebbe \n in \r\n e `read` sbaglierebbe scelta
    fuori = esito.stdout.decode("utf-8", "replace")
    errori = esito.stderr.decode("utf-8", "replace")
    assert esito.returncode == 0, errori
    assert "PASSO_DOPO_IL_FALLIMENTO" not in fuori
    assert "non è andato a buon fine (codice 1)" in errori
    # il menu è andato avanti: la scelta 4 è stata eseguita (errore ripetuto) e 0 ha chiuso
    assert errori.count("non è andato a buon fine") == 2


def test_configura_giorni_validita_esclude_zero():
    sh = _leggi(PODMAN + "configura.sh")
    assert '"$giorni" =~ ^[1-9][0-9]{0,3}$' in sh
    assert "^[0-9]{1,4}$" not in sh


def test_importa_toglie_la_config_vecchia_prima_di_copiare():
    sh = _leggi(PODMAN + "configura.sh")
    pulizia = sh.index("rm -f /stato/config.json /stato/config.local.json")
    copia = sh.index('cp -R --preserve=mode,ownership,timestamps "/sorgente/$voce" /stato/')
    assert pulizia < copia
