import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


RADICE = Path(__file__).resolve().parents[1]
PERCORSO = RADICE / "deploy" / "podman" / "config_container.py"


def _carica(nome, percorso):
    spec = importlib.util.spec_from_file_location(nome, percorso)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


cc = _carica("config_container", PERCORSO)


def _base(**oltre):
    dati = {"version": "2.8.4", "database_path": "data/database.sqlite",
            "uploads_path": "uploads", "backups_path": "backups"}
    dati.update(oltre)
    return dati


def _locale(**oltre):
    dati = {"host": "0.0.0.0", "port": 5000, "debug": False,
            "secret_key": "segreto-di-prova", "encryption_key": "chiave-di-prova",
            "smtp_host": "posta.example.it",
            "cloudflare_mode": False, "force_https": False, "behind_proxy": False}
    dati.update(oltre)
    return dati


def test_adegua_rifiuta_percorso_assoluto_posix():
    with pytest.raises(cc.ConfigNonPortabile) as errore:
        cc.adegua(_base(database_path="/srv/medinventory/data/database.sqlite"), _locale())
    assert "database_path" in str(errore.value)


@pytest.mark.parametrize("valore", [
    "C:\\MedInventory\\data\\database.sqlite",
    "C:/MedInventory/data/database.sqlite",
    "\\\\server\\condivisa\\database.sqlite",
    "D:database.sqlite",
])
def test_adegua_rifiuta_percorso_assoluto_windows(valore):
    # Su Linux os.path.isabs non li riconosce: il controllo è esplicito.
    with pytest.raises(cc.ConfigNonPortabile) as errore:
        cc.adegua(_base(database_path=valore), _locale())
    assert "database_path" in str(errore.value)


@pytest.mark.parametrize("valore", ["../data/database.sqlite", "data\\..\\..\\database.sqlite"])
def test_adegua_rifiuta_risalita(valore):
    with pytest.raises(cc.ConfigNonPortabile) as errore:
        cc.adegua(_base(database_path=valore), _locale())
    assert ".." in str(errore.value)


def test_adegua_rifiuta_uploads_fuori_layout():
    with pytest.raises(cc.ConfigNonPortabile) as errore:
        cc.adegua(_base(uploads_path="allegati"), _locale())
    assert "uploads_path" in str(errore.value)


def test_adegua_rifiuta_percorso_nel_config_locale():
    # load_config() fa vincere config.local.json: un percorso lì dentro conta.
    with pytest.raises(cc.ConfigNonPortabile) as errore:
        cc.adegua(_base(), _locale(backups_path="/mnt/nas/backups"))
    assert "config.local.json" in str(errore.value)


def test_adegua_forza_ascolto_locale():
    _, locale, modifiche = cc.adegua(_base(), _locale(host="0.0.0.0", port=8080))
    assert locale["host"] == "127.0.0.1"
    assert locale["port"] == 5000
    assert any("host" in m for m in modifiche)
    assert any("port" in m for m in modifiche)


def test_adegua_forza_modalita_proxy():
    _, locale, _ = cc.adegua(_base(), {"encryption_key": "chiave-di-prova"})
    assert locale["cloudflare_mode"] is True
    assert locale["force_https"] is True
    assert locale["host"] == "127.0.0.1"
    assert locale["port"] == 5000


def test_adegua_non_tocca_encryption_key_ne_le_altre_chiavi():
    prima = _locale()
    config, locale, modifiche = cc.adegua(_base(), prima)
    forzate = set(cc.VALORI_FORZATI)
    assert locale["encryption_key"] == "chiave-di-prova"
    assert {k: v for k, v in locale.items() if k not in forzate} == \
           {k: v for k, v in prima.items() if k not in forzate}
    assert config == _base()
    assert not any("encryption_key" in m or "secret_key" in m for m in modifiche)


def test_adegua_non_modifica_gli_argomenti():
    config, locale = _base(uploads_path="./uploads/"), _locale()
    copia_config, copia_locale = json.dumps(config), json.dumps(locale)
    nuovo, nuovo_locale, _ = cc.adegua(config, locale)
    assert json.dumps(config) == copia_config
    assert json.dumps(locale) == copia_locale
    assert nuovo is not config and nuovo_locale is not locale


def test_adegua_normalizza_la_forma_equivalente():
    config, _, modifiche = cc.adegua(
        _base(database_path="./data/database.sqlite", uploads_path="uploads/"), _locale())
    assert config["database_path"] == "data/database.sqlite"
    assert config["uploads_path"] == "uploads"
    assert any("uploads_path" in m for m in modifiche)


def test_adegua_e_idempotente():
    config, locale, _ = cc.adegua(_base(), _locale())
    ancora, ancora_locale, modifiche = cc.adegua(config, locale)
    assert modifiche == []
    assert (ancora, ancora_locale) == (config, locale)


def _prepara(cartella, config, locale):
    (cartella / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    (cartella / "config.local.json").write_text(json.dumps(locale, indent=2), encoding="utf-8")


def _cli(*argomenti, ambiente=None):
    env = {k: v for k, v in os.environ.items() if k != cc.VARIABILE}
    env.update(ambiente or {})
    return subprocess.run([sys.executable, str(PERCORSO), *argomenti],
                          capture_output=True, text=True, env=env)


def test_cli_esce_1_senza_scrivere(tmp_path):
    _prepara(tmp_path, _base(database_path="/srv/dati/database.sqlite"), _locale())
    prima = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    esito = _cli("adegua", "--radice", str(tmp_path))
    assert esito.returncode == 1
    assert "database_path" in esito.stderr
    assert esito.stdout == ""
    # Nemmeno config.local.json, che da solo sarebbe stato adeguabile.
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == prima


def test_cli_prova_non_scrive(tmp_path):
    _prepara(tmp_path, _base(), _locale())
    prima = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    esito = _cli("adegua", "--radice", str(tmp_path), "--prova")
    assert esito.returncode == 0
    assert "host" in esito.stdout
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == prima


def test_cli_scrive_e_stampa_le_modifiche(tmp_path):
    _prepara(tmp_path, _base(), _locale())
    # Senza --radice vale la variabile d'ambiente.
    esito = _cli("adegua", ambiente={cc.VARIABILE: str(tmp_path)})
    assert esito.returncode == 0
    assert "host" in esito.stdout
    locale = json.loads((tmp_path / "config.local.json").read_text(encoding="utf-8"))
    assert locale["host"] == "127.0.0.1"
    assert locale["cloudflare_mode"] is True and locale["force_https"] is True
    assert locale["encryption_key"] == "chiave-di-prova"
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8")) == _base()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json", "config.local.json"]


def test_cli_senza_file_esce_1(tmp_path):
    esito = _cli("adegua", "--radice", str(tmp_path))
    assert esito.returncode == 1
    assert "config.json" in esito.stderr
    assert list(tmp_path.iterdir()) == []


def test_variabile_coincide_con_percorsi():
    percorsi = _carica("percorsi_per_confronto", RADICE / "percorsi.py")
    assert cc.VARIABILE == percorsi.VARIABILE


def test_modulo_usa_solo_la_libreria_standard():
    # Gira anche fuori dall'app e prima che l'app sia configurata: niente Flask.
    albero = ast.parse(PERCORSO.read_text(encoding="utf-8"))
    importati = set()
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Import):
            importati.update(a.name.split(".")[0] for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom):
            importati.add((nodo.module or "").split(".")[0])
    assert importati <= {"argparse", "copy", "json", "os", "re", "shutil", "sys"}
