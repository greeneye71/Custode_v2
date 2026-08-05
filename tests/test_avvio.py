"""Due superfici che nessun altro test tocca: la pagina della richiesta di
reset quando la posta e' configurata, e la registrazione dei task dello
scheduler. Sono i due punti in cui un errore non si vede dalla suite ma si
vede al primo avvio."""


def test_la_pagina_di_richiesta_reset_si_apre(client, app):
    """Gli altri test della richiesta o fanno POST, o passano dal caso «SMTP
    non configurato», che risponde con un redirect senza rendere il template:
    un errore nel template non lo vedrebbe nessuno."""
    app.config['APP_CONFIG'].update({'smtp_host': 'h', 'smtp_user': 'u@x.it'})
    risposta = client.get('/password-dimenticata')
    assert risposta.status_code == 200
    testo = risposta.get_data(as_text=True)
    assert 'name="email"' in testo
    assert 'csrf_token' in testo


def test_lo_scheduler_registra_i_suoi_task(app):
    """La 2.6.2 ha tolto il task 'report_schedulati', fuso dentro
    'deadline_alerts'. Nessun test avviava lo scheduler: un riferimento a un
    metodo cancellato sarebbe esploso solo in produzione, dentro un thread di
    fondo dove l'eccezione la vede solo il log."""
    from scheduler import BackgroundScheduler
    scheduler = BackgroundScheduler(app)
    scheduler.start()
    try:
        nomi = [t['name'] for t in scheduler._tasks]
    finally:
        scheduler.stop()
    assert 'deadline_alerts' in nomi
    assert 'report_schedulati' not in nomi
    for task in scheduler._tasks:
        assert callable(task['func'])
