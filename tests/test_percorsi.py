import os
import pytest
from pathlib import Path

import percorsi

SORGENTI = str(Path(__file__).resolve().parents[1])


def test_senza_variabile_la_radice_sono_i_sorgenti(monkeypatch):
    monkeypatch.delenv(percorsi.VARIABILE, raising=False)
    assert percorsi.radice() == SORGENTI
    assert percorsi.sorgenti() == SORGENTI
    assert percorsi.stato_separato() is False


def test_la_variabile_sposta_la_radice(monkeypatch, tmp_path):
    monkeypatch.setenv(percorsi.VARIABILE, str(tmp_path))
    assert percorsi.radice() == str(tmp_path)
    assert percorsi.stato_separato() is True
    assert percorsi.percorso('data', 'database.sqlite') == os.path.join(str(tmp_path), 'data', 'database.sqlite')


def test_stringa_vuota_vale_come_assente(monkeypatch):
    monkeypatch.setenv(percorsi.VARIABILE, '')
    assert percorsi.radice() == SORGENTI
    assert percorsi.stato_separato() is False


def test_radice_relativa_diventa_assoluta(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(percorsi.VARIABILE, 'stato')
    assert percorsi.radice() == os.path.join(str(tmp_path), 'stato')


def test_i_modelli_restano_nei_sorgenti(monkeypatch, tmp_path):
    monkeypatch.setenv(percorsi.VARIABILE, str(tmp_path))
    assert percorsi.modello('config.example.json') == os.path.join(SORGENTI, 'config.example.json')
    assert os.path.isfile(percorsi.modello('config.local.example.json'))


def test_la_radice_non_e_in_cache(monkeypatch, tmp_path):
    monkeypatch.setenv(percorsi.VARIABILE, str(tmp_path / 'a'))
    prima = percorsi.radice()
    monkeypatch.setenv(percorsi.VARIABILE, str(tmp_path / 'b'))
    assert percorsi.radice() != prima


def test_modulo_senza_flask():
    testo = Path(SORGENTI, 'percorsi.py').read_text(encoding='utf-8')
    assert 'flask' not in testo.lower()


# --- Task 2: i moduli che calcolavano la radice da soli ---------------------

import subprocess
import sys


def _importa_in_sottoprocesso(tmp_path, codice, radice=True):
    ambiente = dict(os.environ)
    ambiente.pop(percorsi.VARIABILE, None)
    if radice:
        ambiente[percorsi.VARIABILE] = str(tmp_path)
    esito = subprocess.run([sys.executable, '-c', codice], capture_output=True,
                           text=True, cwd=SORGENTI, env=ambiente)
    assert esito.returncode == 0, esito.stderr
    return esito.stdout.strip().splitlines()


@pytest.mark.parametrize('modulo, stato, modello_attr', [
    ('app', 'CONFIG_PATH', 'EXAMPLE_CONFIG_PATH'),
    ('app', 'LOCAL_CONFIG_PATH', 'LOCAL_EXAMPLE_PATH'),
    ('cloudflare_mode', 'CONFIG_LOCAL', 'CONFIG_EXAMPLE'),
    ('toggle_modalita', 'CONFIG_LOCAL', 'CONFIG_EXAMPLE'),
])
def test_stato_nella_radice_modelli_nei_sorgenti(tmp_path, modulo, stato, modello_attr):
    righe = _importa_in_sottoprocesso(
        tmp_path, f'import {modulo} as m; print(m.{stato}); print(m.{modello_attr})')
    assert righe[-2].startswith(str(tmp_path))
    assert righe[-1].startswith(SORGENTI)


@pytest.mark.parametrize('modulo, stato', [
    ('app', 'CONFIG_PATH'), ('app', 'LOCAL_CONFIG_PATH'),
    ('cloudflare_mode', 'CONFIG_LOCAL'), ('toggle_modalita', 'CONFIG_LOCAL'),
])
def test_senza_variabile_lo_stato_resta_nei_sorgenti(tmp_path, modulo, stato):
    righe = _importa_in_sottoprocesso(
        tmp_path, f'import {modulo} as m; print(m.{stato})', radice=False)
    assert righe[-1].startswith(SORGENTI)


def test_i_log_finiscono_nella_radice(tmp_path):
    _importa_in_sottoprocesso(tmp_path, 'import app')
    assert (tmp_path / 'logs').is_dir()


def test_operazioni_segue_la_radice(monkeypatch, tmp_path):
    from manutenzione_lib import operazioni
    monkeypatch.setenv(percorsi.VARIABILE, str(tmp_path))
    assert operazioni.radice() == str(tmp_path)
    assert operazioni.percorso_uploads({'uploads_path': 'uploads'}) == os.path.join(str(tmp_path), 'uploads')
    assert operazioni.percorso_backup({'backups_path': 'backups'}) == os.path.join(str(tmp_path), 'backups')
    monkeypatch.delenv(percorsi.VARIABILE)
    assert operazioni.radice() == SORGENTI


def test_migrate_segue_la_radice(monkeypatch, tmp_path):
    import migrate
    monkeypatch.setenv(percorsi.VARIABILE, str(tmp_path))
    assert migrate.load_db_path() == os.path.join(str(tmp_path), 'data', 'database.sqlite')


def test_create_app_percorsi_nella_radice(tmp_path):
    righe = _importa_in_sottoprocesso(
        tmp_path,
        "import app; a = app.create_app(); "
        "print(a.config['DATABASE_PATH']); print(a.config['UPLOADS_PATH']); "
        "print(a.config['BACKUPS_PATH'])")
    assert all(r.startswith(str(tmp_path)) for r in righe[-3:])


FILE_SOSTITUITI = ('app.py', 'auth.py', 'run_production.py', 'seed.py', 'toggle_modalita.py',
                   'cloudflare_mode.py', 'migrate.py', 'pulisci_uploads.py',
                   'recover_import_history.py', 'importa_installazione.py',
                   'manutenzione_lib/operazioni.py')


@pytest.mark.parametrize('nome', FILE_SOSTITUITI)
def test_ogni_file_passa_da_percorsi(nome):
    testo = Path(SORGENTI, nome).read_text(encoding='utf-8')
    assert 'percorsi.' in testo
