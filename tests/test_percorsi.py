import os
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
