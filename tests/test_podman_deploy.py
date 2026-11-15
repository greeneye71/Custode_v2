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


def _bash(*args, **kwargs):
    """Esegue `bash <args>`; salta il test se non c'è una bash vera."""
    if BASH is None:
        pytest.skip("bash non disponibile")
    return subprocess.run([BASH, *args], capture_output=True, text=True, **kwargs)


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


ESTENSIONI_UNITA = {".pod", ".container", ".volume", ".conf", ".service", ".timer"}


def _unita():
    return sorted(p for p in (RADICE / PODMAN).glob("*") if p.suffix in ESTENSIONI_UNITA)


def _righe(relativo):
    # Righe utili: senza vuote e senza commenti.
    return [r.strip() for r in _leggi(relativo).splitlines()
            if r.strip() and not r.strip().startswith("#")]


def test_pod_pubblica_solo_la_443():
    righe = _righe(PODMAN + "custode.pod")
    assert "PodName=custode" in righe
    assert [r for r in righe if r.startswith("PublishPort=")] == ["PublishPort=443:443"]
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
    assert "ExecCondition=/usr/bin/podman container exists custode-web" in righe
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
