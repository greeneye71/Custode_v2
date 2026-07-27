"""Fixture condivise: applicazione Flask su un database temporaneo.

Ogni test riceve un database vuoto e una cartella uploads propri, così i test
non si influenzano fra loro e non toccano i dati reali dello sviluppatore.
"""
import os
import sys

import pytest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)


@pytest.fixture
def app(tmp_path):
    os.makedirs(tmp_path / 'data', exist_ok=True)
    os.makedirs(tmp_path / 'uploads', exist_ok=True)

    import app as modulo_app

    config = modulo_app.load_config()
    config['database_path'] = str(tmp_path / 'data' / 'test.sqlite')
    config['uploads_path'] = str(tmp_path / 'uploads')
    config['single_struttura'] = False

    # create_app() rilegge la configurazione da disco e aggiorna config.json
    # quando la versione cambia: entrambe le cose vanno neutralizzate nei test.
    modulo_app.load_config = lambda: config
    modulo_app.check_version_update = lambda _config: None

    applicazione = modulo_app.create_app()
    applicazione.config['DATABASE_PATH'] = str(tmp_path / 'data' / 'test.sqlite')
    applicazione.config['UPLOADS_PATH'] = str(tmp_path / 'uploads')
    applicazione.config['WTF_CSRF_ENABLED'] = False
    return applicazione


@pytest.fixture
def client(app):
    return app.test_client()
