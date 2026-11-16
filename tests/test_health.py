import logging


def test_pronto_senza_login(client):
    risposta = client.get('/health/ready')
    assert risposta.status_code == 200
    assert risposta.get_json() == {'status': 'ok'}


def test_non_reindirizza_al_login(client):
    risposta = client.get('/health/ready', follow_redirects=False)
    assert risposta.status_code not in (301, 302, 303, 307, 308)


def test_503_se_il_database_non_si_apre(client, monkeypatch, caplog):
    def esplode(*args, **kwargs):
        raise RuntimeError('database non raggiungibile: /percorso/segreto')
    monkeypatch.setattr('models.get_db', esplode)
    with caplog.at_level(logging.WARNING, logger='medinventory'):
        risposta = client.get('/health/ready')
    assert risposta.status_code == 503
    assert risposta.get_json() == {'status': 'errore'}
    assert b'segreto' not in risposta.data
    # Il 503 deve venire dalla route (e non da un hook precedente): il
    # dettaglio resta solo nel log.
    assert any('segreto' in r.getMessage() for r in caplog.records)


def test_api_health_invariata(client):
    risposta = client.get('/api/health')
    assert risposta.status_code == 200
    assert risposta.get_json()['status'] == 'ok'
