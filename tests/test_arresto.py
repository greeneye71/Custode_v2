import signal
import types

import pytest

import run_production


@pytest.fixture
def gestore_ripristinato():
    precedente = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, precedente)


def test_sigterm_ha_un_gestore(gestore_ripristinato):
    run_production.installa_gestore_sigterm()
    assert signal.getsignal(signal.SIGTERM) is run_production._termina


def test_il_gestore_esce_in_modo_pulito():
    with pytest.raises(SystemExit) as uscita:
        run_production._termina(signal.SIGTERM, None)
    assert uscita.value.code == 0


def test_main_installa_il_gestore_prima_di_servire(
        monkeypatch, tmp_path, gestore_ripristinato):
    """Al momento in cui main() chiama serve(), SIGTERM ha gia' il gestore."""
    import scheduler
    import waitress

    app_finta = types.SimpleNamespace(config={'APP_CONFIG': {}})
    visto = {}
    fermato = []

    def serve_finto(*args, **kwargs):
        visto['gestore'] = signal.getsignal(signal.SIGTERM)

    monkeypatch.setattr(run_production, 'create_app', lambda: app_finta)
    monkeypatch.setattr(run_production.percorsi, 'percorso',
                        lambda *parti: str(tmp_path.joinpath(*parti)))
    monkeypatch.setattr(run_production, 'setup_logging', lambda log_dir: None)
    monkeypatch.setattr(scheduler, 'init_scheduler', lambda app: object())
    monkeypatch.setattr(scheduler, 'stop_scheduler',
                        lambda: fermato.append(True))
    monkeypatch.setattr(waitress, 'serve', serve_finto)

    run_production.main()

    assert visto['gestore'] is run_production._termina
    assert fermato == [True]
