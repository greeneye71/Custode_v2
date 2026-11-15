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
    if trovata and not re.search(r"[\/](System32|WindowsApps)[\/]", trovata, re.I):
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
