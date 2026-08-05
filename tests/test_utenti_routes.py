"""Le rotte della cancellazione utenti: chi puo', cosa si rifiuta, cosa resta."""
import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def dati(app):
    from models import execute
    with app.app_context():
        a = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        b = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica B','B',1)").lastrowid
        da = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Ocu','OCU',?)",
                     (a,)).lastrowid
        h = generate_password_hash('Passw0rd!')
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,primo_accesso) "
                "VALUES ('super@x.it',?,'S','S','superadmin',0)", (h,))
        admin_a = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('admin@a.it',?,'A','A','admin',?,0)", (h, a)).lastrowid
        secondo = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('admin2@a.it',?,'A','Due','admin',?,0)", (h, a)).lastrowid
        mario = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('mario@a.it',?,'M','Rossi','utente',?,0)", (h, a)).lastrowid
        altrui = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('utente@b.it',?,'U','B','utente',?,0)", (h, b)).lastrowid
        tec = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('tec@x.it',?,'T','T','tecnico',NULL,0)", (h,)).lastrowid
        execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,"
                "stato,created_by) VALUES (?,?,'M-1','REXXAM','OZY','funzionante',?)",
                (da, a, mario))
    return {'a': a, 'admin_a': admin_a, 'secondo': secondo, 'mario': mario,
            'altrui': altrui, 'tec': tec}


def entra(client, email):
    client.post('/login', data={'email': email, 'password': 'Passw0rd!'})


def test_la_pagina_di_conferma_dice_chi_si_sta_cancellando(client, dati):
    """Non si digita nulla per confermare: l'unica difesa contro "ho cliccato la
    riga sbagliata" e' che la pagina dica di chi si tratta — nome, email e
    struttura. Senza l'ultima asserzione, ripristinare la query letterale del
    brief (senza il LEFT JOIN su strutture) farebbe comunque passare tutti gli
    altri test: e' questa a difendere la deviazione."""
    entra(client, 'admin@a.it')
    testo = client.get(f"/admin/utenti/{dati['mario']}/elimina").get_data(as_text=True)
    assert 'mario@a.it' in testo
    assert 'Rossi' in testo
    assert 'Clinica A' in testo


def test_la_pagina_di_conferma_dice_cosa_resta(client, dati):
    """I tre punti che la pagina deve dire prima del pulsante: che non si torna
    indietro, che il nome resta sulle schede inserite, che l'indirizzo si
    libera. Le tre asserzioni separate, cosi' se ne sparisce una si sa quale."""
    entra(client, 'admin@a.it')
    testo = client.get(f"/admin/utenti/{dati['mario']}/elimina").get_data(as_text=True)
    assert 'reversibile' in testo.lower()
    assert 'resta' in testo.lower()
    assert 'libero' in testo.lower()


def test_la_cancellazione_riuscita(client, app, dati):
    from models import query_one
    entra(client, 'admin@a.it')
    r = client.post(f"/admin/utenti/{dati['mario']}/elimina", follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        riga = query_one("SELECT email, eliminato_il FROM utenti WHERE id=?", (dati['mario'],))
        assert riga['eliminato_il'] is not None
        assert riga['email'] != 'mario@a.it'


def test_l_ultimo_admin_non_si_cancella_dalla_rotta(client, app, dati):
    from models import query_one
    entra(client, 'super@x.it')
    client.post(f"/admin/utenti/{dati['secondo']}/elimina", follow_redirects=True)
    r = client.post(f"/admin/utenti/{dati['admin_a']}/elimina", follow_redirects=True)
    assert 'amministratore' in r.get_data(as_text=True).lower()
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['admin_a'],))['eliminato_il'] is None


def test_nessuno_cancella_se_stesso(client, app, dati):
    """Guardava solo il database: passerebbe anche se il messaggio di
    rifiuto fosse muto o dicesse un'altra cosa. Qui si asserisce anche il
    testo esatto scritto in _utente_cancellabile."""
    from models import query_one
    entra(client, 'admin@a.it')
    r = client.post(f"/admin/utenti/{dati['admin_a']}/elimina", follow_redirects=True)
    assert 'non puoi cancellare il tuo account' in r.get_data(as_text=True).lower()
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['admin_a'],))['eliminato_il'] is None


def test_un_admin_non_cancella_utenti_di_altre_strutture(client, app, dati):
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['altrui']}/elimina", follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['altrui'],))['eliminato_il'] is None


def test_un_admin_non_cancella_un_tecnico(client, app, dati):
    """Un tecnico e' un account condiviso fra strutture, non proprieta' di una."""
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['tec']}/elimina", follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['tec'],))['eliminato_il'] is None


def test_il_registro_conserva_l_email_originale(client, app, dati):
    """Dopo la cancellazione nel database c'e' solo la forma spostata."""
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['mario']}/elimina", follow_redirects=True)
    with app.app_context():
        voce = query_one("SELECT dettagli, struttura_id FROM log_attivita "
                         "WHERE azione='eliminazione' AND entita='utenti'")
        assert voce is not None
        assert 'mario@a.it' in voce['dettagli']
        assert voce['struttura_id'] == dati['a']


def test_un_utente_cancellato_non_entra_piu(client, app, dati):
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['mario']}/elimina", follow_redirects=True)
    client.get('/logout')
    r = client.post('/login', data={'email': 'mario@a.it', 'password': 'Passw0rd!'},
                    follow_redirects=True)
    assert 'dashboard' not in r.get_data(as_text=True).lower()


def test_un_superadmin_non_cancella_un_tecnico(client, app, dati):
    """Per un admin, il tecnico e' gia' fermato da _check_utente_scope (il suo
    struttura_id nullo non coincide con nessuna struttura). Per un superadmin
    _check_utente_scope restituisce sempre True: qui il controllo esplicito
    sul ruolo 'tecnico' in _utente_cancellabile e' l'UNICA difesa, e questo
    test e' l'unico a coprirla."""
    from models import query_one
    entra(client, 'super@x.it')
    client.post(f"/admin/utenti/{dati['tec']}/elimina", follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['tec'],))['eliminato_il'] is None


def test_un_guasto_dopo_la_registrazione_non_dice_che_nulla_e_cambiato(client, app, dati, monkeypatch):
    """Il vero punto di non ritorno e' dentro log_attivita: passa da
    models.execute(), che fa gia' db.commit() sulla stessa connessione. Un
    guasto DOPO quella chiamata (qui simulato facendo esplodere log_attivita
    subito dopo aver fatto il suo lavoro vero) trova l'utente gia' cancellato
    in modo durevole: il messaggio all'operatore deve dirlo, non deve
    affermare che "nulla e' stato modificato"."""
    import admin as modulo_admin
    from models import query_one

    log_vero = modulo_admin.log_attivita

    def registra_e_poi_esplodi(*args, **kwargs):
        log_vero(*args, **kwargs)
        raise RuntimeError('guasto simulato dopo la registrazione')

    monkeypatch.setattr(modulo_admin, 'log_attivita', registra_e_poi_esplodi)

    entra(client, 'admin@a.it')
    r = client.post(f"/admin/utenti/{dati['mario']}/elimina", follow_redirects=True)
    testo = r.get_data(as_text=True)

    # La cancellazione e' davvero avvenuta (la registrazione l'ha resa
    # durevole prima del guasto simulato) ...
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['mario'],))['eliminato_il'] is not None
    # ... quindi il messaggio mostrato non puo' essere quello per il caso in
    # cui non e' successo nulla (severita' 'danger', frase "fallita, nulla") ...
    assert 'alert-danger' not in testo
    assert 'fallita, nulla' not in testo
    # ... deve invece avvisare con severita' 'warning' che l'operazione e'
    # avvenuta ma qualcosa e' andato storto dopo.
    assert 'alert-warning' in testo
    assert 'cancellato' in testo.lower()
    assert 'subito dopo' in testo.lower()


def _cancella(client, id):
    client.post(f"/admin/utenti/{id}/elimina", follow_redirects=True)


def test_non_compare_nell_elenco_utenti_dell_admin(client, dati):
    entra(client, 'admin@a.it')
    _cancella(client, dati['mario'])
    assert 'mario@a.it' not in client.get('/admin/utenti').get_data(as_text=True)


def test_non_compare_nell_elenco_utenti_del_superadmin(client, dati):
    entra(client, 'admin@a.it')
    _cancella(client, dati['mario'])
    client.get('/logout')
    entra(client, 'super@x.it')
    assert 'mario@a.it' not in client.get('/admin/utenti').get_data(as_text=True)


def test_un_tecnico_cancellato_non_compare_nell_elenco_tecnici(client, app, dati):
    """La rotta /admin/utenti/<id>/elimina rifiuta SEMPRE un tecnico, anche per
    un superadmin (e' l'unica difesa verificata da
    test_un_superadmin_non_cancella_un_tecnico in questo stesso file): non e'
    la sua via di cancellazione, quindi _cancella() qui non produrrebbe nulla
    da filtrare. Cio' che questo test deve coprire e' il filtro della lista
    tecnici, non l'autorizzazione (gia' coperta altrove): si usa percio' la
    primitiva cancella_utente direttamente, che a differenza della rotta non
    discrimina per ruolo."""
    from utente_service import cancella_utente
    from models import get_db
    entra(client, 'super@x.it')
    with app.app_context():
        conn = get_db()
        cancella_utente(conn, dati['tec'])
        conn.commit()
    assert 'tec@x.it' not in client.get('/admin/tecnici').get_data(as_text=True)


def test_non_compare_nella_scheda_della_struttura(client, dati):
    entra(client, 'admin@a.it')
    _cancella(client, dati['mario'])
    client.get('/logout')
    entra(client, 'super@x.it')
    assert 'mario@a.it' not in client.get(f"/strutture/{dati['a']}").get_data(as_text=True)


def test_un_tecnico_cancellato_non_compare_nella_scheda_della_struttura(client, app, dati):
    """Cio' che questo test dimostra e' vero e vale la pena difenderlo: un
    tecnico cancellato non compare nella scheda della sua struttura. Lo si
    assegna davvero via tecnici_strutture e si verifica PRIMA che compaia
    (senza questa meta' un test che controlla solo l'assenza passerebbe anche
    se il tecnico non fosse mai stato li'), poi lo si cancella con la
    primitiva -- la rotta rifiuta sempre i tecnici, di proposito -- e si
    verifica che sparisca.

    Ma oggi e' vero per un motivo che NON e' il filtro eliminato_il di
    strutture_bp.py:392: cancella_utente toglie la riga di tecnici_strutture,
    quindi il JOIN di quella query non trova piu' il tecnico a prescindere dal
    filtro (vedi il commento li' accanto). Questo test protegge il
    comportamento visibile, non quella riga di SQL: se il filtro sparisse, la
    suite resterebbe verde, e sarebbe corretto che lo fosse."""
    from models import execute, get_db
    from utente_service import cancella_utente
    entra(client, 'super@x.it')
    with app.app_context():
        execute("INSERT INTO tecnici_strutture (tecnico_id, struttura_id) VALUES (?, ?)",
                (dati['tec'], dati['a']))
    prima = client.get(f"/strutture/{dati['a']}").get_data(as_text=True)
    assert 'tec@x.it' in prima
    with app.app_context():
        conn = get_db()
        cancella_utente(conn, dati['tec'])
        conn.commit()
    dopo = client.get(f"/strutture/{dati['a']}").get_data(as_text=True)
    assert 'tec@x.it' not in dopo


def test_non_e_contato_fra_gli_utenti_della_struttura(client, app, dati):
    """contenuto_struttura conta gli utenti prima di cancellare una struttura:
    contarne di cancellati direbbe all'operatore un numero che non esiste."""
    from struttura_service import contenuto_struttura
    from models import get_db
    entra(client, 'admin@a.it')
    with app.app_context():
        prima = contenuto_struttura(get_db(), dati['a'], '/tmp/x')['utenti']
    _cancella(client, dati['mario'])
    with app.app_context():
        dopo = contenuto_struttura(get_db(), dati['a'], '/tmp/x')['utenti']
    assert dopo == prima - 1


def test_un_tecnico_non_compare_nell_elenco_utenti(client, dati):
    """I tecnici hanno la loro pagina, che sa gestire le assegnazioni alle
    strutture; il modulo generico no."""
    entra(client, 'super@x.it')
    assert 'tec@x.it' not in client.get('/admin/utenti').get_data(as_text=True)


def test_il_modulo_generico_rifiuta_un_tecnico(client, app, dati):
    """Un URL scritto a mano non deve poter declassare nessuno.

    Il payload usa ruolo='utente', non 'tecnico': il <select> del modulo
    generico offre solo 'admin' e 'utente', quindi 'utente' e' cio' che una
    sottomissione reale del modulo produrrebbe per un tecnico -- ed e' esattamente
    il declassamento descritto nel difetto. Con ruolo='tecnico' il test sarebbe
    cieco alla guardia di testa rimossa: quel valore viene comunque respinto
    dal ramo 'ruolo non ammesso' della validazione generica (che non lo
    riconosce), quindi il test passerebbe anche senza il controllo esplicito
    sui tecnici che questa route deve avere."""
    from models import query_one
    entra(client, 'super@x.it')
    client.post(f"/admin/utenti/{dati['tec']}/modifica",
                data={'nome': 'T', 'cognome': 'T', 'email': 'tec@x.it',
                      'ruolo': 'utente', 'struttura_id': dati['a']},
                follow_redirects=True)
    with app.app_context():
        u = query_one("SELECT ruolo, struttura_id FROM utenti WHERE id=?", (dati['tec'],))
        assert u['ruolo'] == 'tecnico'
        assert u['struttura_id'] is None


def test_salvare_un_superadmin_non_lo_declassa(client, app, dati):
    """Il caso peggiore: l'unico superadmin salva la propria scheda e il
    deployment resta senza superadmin. Misurato prima della correzione:
    ruolo 'utente', zero superadmin, /admin/backup 302."""
    from models import query_one
    entra(client, 'super@x.it')
    with app.app_context():
        sa = query_one("SELECT id FROM utenti WHERE email='super@x.it'")['id']
    client.post(f"/admin/utenti/{sa}/modifica",
                data={'nome': 'S', 'cognome': 'S', 'email': 'super@x.it',
                      'ruolo': 'superadmin', 'struttura_id': dati['a']},
                follow_redirects=True)
    with app.app_context():
        u = query_one("SELECT ruolo, struttura_id FROM utenti WHERE id=?", (sa,))
        assert u['ruolo'] == 'superadmin'
        assert u['struttura_id'] is None
        assert query_one("SELECT COUNT(*) AS n FROM utenti "
                         "WHERE ruolo='superadmin'")['n'] == 1


def test_un_superadmin_puo_ancora_correggersi_il_nome(client, app, dati):
    """Non esiste una pagina di profilo: /cambio-password fa solo la password.
    Quella scheda e' l'unico posto dove un superadmin puo' correggersi."""
    from models import query_one
    entra(client, 'super@x.it')
    with app.app_context():
        sa = query_one("SELECT id FROM utenti WHERE email='super@x.it'")['id']
    client.post(f"/admin/utenti/{sa}/modifica",
                data={'nome': 'Giovanni', 'cognome': 'Bergamaschi',
                      'email': 'super@x.it', 'ruolo': 'superadmin'},
                follow_redirects=True)
    with app.app_context():
        u = query_one("SELECT nome, ruolo FROM utenti WHERE id=?", (sa,))
        assert u['nome'] == 'Giovanni'
        assert u['ruolo'] == 'superadmin'


def test_un_ruolo_non_ammesso_su_un_utente_normale_e_un_errore(client, app, dati):
    """Non piu' una riscrittura muta: chi manda un valore che non esiste deve
    vedere un errore, non ritrovarsi l'utente declassato in silenzio.

    Mario e' gia' 'utente': la vecchia riscrittura muta (ruolo = 'utente')
    porta esattamente allo stesso valore finale, quindi il solo controllo sul
    ruolo salvato non distinguerebbe correzione e difetto -- e' cieco. Il
    segno che distingue i due casi e' che la correzione NON esegue affatto
    l'UPDATE e ripresenta il modulo (200, nessun redirect, nessun messaggio
    di successo), mentre la riscrittura muta salva e reindirizza."""
    from models import query_one
    entra(client, 'admin@a.it')
    r = client.post(f"/admin/utenti/{dati['mario']}/modifica",
                    data={'nome': 'M', 'cognome': 'Rossi', 'email': 'mario@a.it',
                          'ruolo': 'superadmin'},
                    follow_redirects=True)
    assert not r.history
    assert 'aggiornato' not in r.get_data(as_text=True).lower()
    with app.app_context():
        assert query_one("SELECT ruolo FROM utenti WHERE id=?",
                         (dati['mario'],))['ruolo'] == 'utente'


def test_il_ruolo_non_ammesso_si_vede_nella_pagina(client, dati):
    """Un rifiuto che non si spiega e' indistinguibile da un guasto: non
    basta che il salvataggio sia bloccato (gia' coperto dal test sopra), il
    modulo ripresentato deve mostrare perche'."""
    entra(client, 'admin@a.it')
    r = client.post(f"/admin/utenti/{dati['mario']}/modifica",
                    data={'nome': 'M', 'cognome': 'Rossi', 'email': 'mario@a.it',
                          'ruolo': 'superadmin'},
                    follow_redirects=True)
    assert 'ruolo non ammesso' in r.get_data(as_text=True).lower()


def test_un_utente_cancellato_non_e_modificabile(client, app, dati):
    """La specifica vieta di modificare un utente gia' cancellato: il modulo
    generico deve rifiutare, non riscrivere una riga che non rappresenta piu'
    nessuno."""
    from models import query_one
    entra(client, 'admin@a.it')
    _cancella(client, dati['mario'])
    r = client.post(f"/admin/utenti/{dati['mario']}/modifica",
                    data={'nome': 'Altro', 'cognome': 'Nome',
                          'email': 'nuovo@a.it', 'ruolo': 'utente'},
                    follow_redirects=True)
    assert 'stato cancellato' in r.get_data(as_text=True).lower()
    with app.app_context():
        u = query_one("SELECT nome, email FROM utenti WHERE id=?", (dati['mario'],))
        assert u['nome'] == 'M'
        assert u['email'] != 'nuovo@a.it'


def test_la_password_di_un_utente_cancellato_non_si_resetta(client, app, dati):
    """Idem per il reset password: un account gia' cancellato ha gia' una
    password inutilizzabile, resettarla sarebbe una via di rientro."""
    from models import query_one
    entra(client, 'admin@a.it')
    _cancella(client, dati['mario'])
    with app.app_context():
        prima = query_one("SELECT password_hash FROM utenti WHERE id=?",
                          (dati['mario'],))['password_hash']
    r = client.post(f"/admin/utenti/{dati['mario']}/reset-password",
                    follow_redirects=True)
    assert 'stato cancellato' in r.get_data(as_text=True).lower()
    with app.app_context():
        dopo = query_one("SELECT password_hash FROM utenti WHERE id=?",
                         (dati['mario'],))['password_hash']
    assert dopo == prima


def test_un_utente_disattivato_si_riattiva_dal_modulo(client, app, dati):
    """Il sistema disattiva da solo gli utenti rimasti senza struttura: senza
    questa casella resterebbero disattivati per sempre."""
    from models import execute, query_one
    with app.app_context():
        execute("UPDATE utenti SET attivo = 0 WHERE id = ?", (dati['mario'],))
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['mario']}/modifica",
                data={'nome': 'M', 'cognome': 'Rossi', 'email': 'mario@a.it',
                      'ruolo': 'utente', 'attivo': '1'},
                follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT attivo FROM utenti WHERE id=?", (dati['mario'],))['attivo'] == 1


def test_si_puo_disattivare_dal_modulo(client, app, dati):
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['mario']}/modifica",
                data={'nome': 'M', 'cognome': 'Rossi', 'email': 'mario@a.it',
                      'ruolo': 'utente'},
                follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT attivo FROM utenti WHERE id=?", (dati['mario'],))['attivo'] == 0


def test_l_elenco_offre_elimina_e_non_disattiva(client, dati):
    entra(client, 'admin@a.it')
    testo = client.get('/admin/utenti').get_data(as_text=True)
    assert f"/admin/utenti/{dati['mario']}/elimina" in testo
    assert '/toggle' not in testo


def test_non_ci_si_puo_disattivare_da_soli(client, app, dati):
    """Il freno esplicito della vecchia rotta /toggle va portato nel modulo
    generico: senza, bastava salvare la propria scheda senza spuntare la
    casella per restare fuori per sempre (auth.py pretende attivo=1 sia in
    sessione sia al login, e dall'applicazione non si potrebbe piu' rimediare).
    Il resto della scheda deve pero' continuare a salvarsi: qui il nome cambia
    davvero, solo 'attivo' resta a 1. Il vecchio /toggle diceva esplicitamente
    "Non puoi disattivare il tuo account": qui il freno e' silenzioso sul
    valore ma non deve esserlo sul messaggio, quindi si asserisce anche il
    flash che dice cosa e' stato ignorato."""
    from models import query_one
    entra(client, 'admin@a.it')
    r = client.post(f"/admin/utenti/{dati['admin_a']}/modifica",
                data={'nome': 'Nuovo', 'cognome': 'A', 'email': 'admin@a.it',
                      'ruolo': 'admin'},
                follow_redirects=True)
    assert 'disattivare il tuo account' in r.get_data(as_text=True).lower()
    with app.app_context():
        u = query_one("SELECT nome, attivo FROM utenti WHERE id=?", (dati['admin_a'],))
        assert u['attivo'] == 1
        assert u['nome'] == 'Nuovo'


def test_non_ci_si_puo_declassare_da_soli(client, app, dati):
    """Simmetrico al freno sull'autodisattivazione, e per la stessa ragione:
    un admin che apre la propria scheda e sceglie 'Utente' nel menu Ruolo si
    autodeclassa con un solo POST. Se e' l'ultimo admin della struttura (o
    l'unico admin di un'installazione single-struttura senza superadmin, il
    caso reale di seed.py) il deployment resta senza nessuno che possa
    amministrare quella struttura, con la stessa conseguenza e lo stesso
    rimedio da riga di comando (crea_superadmin.py) del difetto gia' chiuso
    per 'attivo'. Il resto della scheda deve continuare a salvarsi."""
    from models import query_one
    entra(client, 'admin@a.it')
    r = client.post(f"/admin/utenti/{dati['admin_a']}/modifica",
                data={'nome': 'Nuovo', 'cognome': 'A', 'email': 'admin@a.it',
                      'ruolo': 'utente'},
                follow_redirects=True)
    assert 'cambiare il tuo ruolo' in r.get_data(as_text=True).lower()
    with app.app_context():
        u = query_one("SELECT nome, ruolo FROM utenti WHERE id=?", (dati['admin_a'],))
        assert u['ruolo'] == 'admin'
        assert u['nome'] == 'Nuovo'


def test_l_ultimo_admin_non_si_declassa_dal_modulo(client, app, dati):
    """Stessa conseguenza che la cancellazione vieta gia' per l'ultimo admin,
    e che il modulo vieta gia' per la disattivazione: declassarlo ad
    'utente' lascerebbe comunque la struttura senza nessuno che possa
    gestirla. 'secondo' viene prima cancellato cosi' 'admin_a' e' davvero
    l'ultimo; l'operatore e' il superadmin, cosi' non e' il freno da soli a
    scattare qui.

    Il payload manda esplicitamente attivo='1': senza, il form omesso
    varrebbe 0 e farebbe scattare INSIEME anche il freno sulla
    disattivazione dell'ultimo admin, mascherando se sia quello o il freno
    sul ruolo a bloccare il salvataggio -- e' esattamente il modo in cui la
    prima versione di questo test si e' rivelata cieca al freno che dice di
    coprire: rimuovendo SOLO quello sul ruolo la suite restava verde, perche'
    il freno sull'attivo bloccava comunque per un motivo diverso."""
    from models import query_one
    entra(client, 'super@x.it')
    client.post(f"/admin/utenti/{dati['secondo']}/elimina", follow_redirects=True)
    r = client.post(f"/admin/utenti/{dati['admin_a']}/modifica",
                    data={'nome': 'A', 'cognome': 'A', 'email': 'admin@a.it',
                          'ruolo': 'utente', 'attivo': '1'},
                    follow_redirects=True)
    assert 'amministratore' in r.get_data(as_text=True).lower()
    with app.app_context():
        assert query_one("SELECT ruolo FROM utenti WHERE id=?",
                         (dati['admin_a'],))['ruolo'] == 'admin'


def test_si_puo_promuovere_un_utente_ad_admin(client, app, dati):
    """La correzione del rilievo sull'autodeclassamento non deve ostacolare
    l'altro verso: un admin che promuove un utente della propria struttura ad
    admin deve continuare a funzionare senza che nessun freno pensato per
    l'ultimo amministratore si metta di traverso (la transizione qui e'
    utente -> admin, non admin -> utente)."""
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/admin/utenti/{dati['mario']}/modifica",
                data={'nome': 'M', 'cognome': 'Rossi', 'email': 'mario@a.it',
                      'ruolo': 'admin'},
                follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT ruolo FROM utenti WHERE id=?",
                         (dati['mario'],))['ruolo'] == 'admin'


def test_non_si_disattiva_l_ultimo_admin_di_una_struttura(client, app, dati):
    """Stessa conseguenza che la cancellazione vieta gia' per l'ultimo admin:
    disattivarlo lascerebbe comunque la struttura senza nessuno che possa
    gestirla. L'operatore e' il superadmin, cosi' non e' il freno da soli a
    scattare qui.

    'secondo' viene DISATTIVATO, non cancellato: e' il caso che distingue le
    due domande di motivo_rifiuto. Cancellarlo lo farebbe sparire anche dal
    conteggio "tutti gli admin esistenti" della cancellazione, quindi non
    direbbe niente sulla disattivazione, che deve invece contare solo gli
    admin ATTIVI. Con la versione precedente di questo test (che cancellava
    'secondo') il difetto era invisibile: motivo_rifiuto usato senza
    distinzione contava 'secondo' come amministratore superstite (la sua
    riga esiste ancora, solo attivo=0) e lasciava disattivare admin_a,
    svuotando la struttura di amministratori funzionanti senza che nessun
    test se ne accorgesse."""
    from models import query_one, execute
    entra(client, 'super@x.it')
    with app.app_context():
        execute("UPDATE utenti SET attivo = 0 WHERE id = ?", (dati['secondo'],))
    r = client.post(f"/admin/utenti/{dati['admin_a']}/modifica",
                    data={'nome': 'A', 'cognome': 'A', 'email': 'admin@a.it',
                          'ruolo': 'admin'},
                    follow_redirects=True)
    assert 'amministratore' in r.get_data(as_text=True).lower()
    with app.app_context():
        assert query_one("SELECT attivo FROM utenti WHERE id=?",
                         (dati['admin_a'],))['attivo'] == 1


def test_non_si_declassa_l_ultimo_admin_attivo_se_l_altro_e_disattivato(client, app, dati):
    """Le tre operazioni portano allo stesso stato finale — la struttura senza
    nessun amministratore in grado di entrare — quindi devono contare allo
    stesso modo. Con il conteggio precedente (tutti gli admin esistenti, anche
    disattivati) questo POST passava: 'secondo' e' solo disattivato, contava
    come superstite, e la struttura restava con zero admin attivi. Solo il
    superadmin puo' arrivarci — un admin della struttura e' per forza attivo,
    quindi resterebbe lui — ma e' proprio l'operatore a cui la disattivazione
    dello stesso admin viene gia' vietata.

    Il payload manda attivo='1' cosi' non e' il freno sulla disattivazione a
    scattare al posto di quello sul ruolo."""
    from models import query_one, execute
    entra(client, 'super@x.it')
    with app.app_context():
        execute("UPDATE utenti SET attivo = 0 WHERE id = ?", (dati['secondo'],))
    r = client.post(f"/admin/utenti/{dati['admin_a']}/modifica",
                    data={'nome': 'A', 'cognome': 'A', 'email': 'admin@a.it',
                          'ruolo': 'utente', 'attivo': '1'},
                    follow_redirects=True)
    assert 'riattiva quello disattivato' in r.get_data(as_text=True)
    with app.app_context():
        assert query_one("SELECT ruolo FROM utenti WHERE id=?",
                         (dati['admin_a'],))['ruolo'] == 'admin'


def test_non_si_cancella_l_ultimo_admin_attivo_se_l_altro_e_disattivato(client, app, dati):
    """Stesso stato finale del declassamento qui sopra, e per la cancellazione
    e' pure irreversibile: la riga di admin_a diventa una lapide. Con il
    conteggio precedente 'secondo' (disattivato) bastava a far passare la
    cancellazione, e in quella struttura non restava nessuno capace nemmeno di
    riattivarlo."""
    from models import query_one, execute
    entra(client, 'super@x.it')
    with app.app_context():
        execute("UPDATE utenti SET attivo = 0 WHERE id = ?", (dati['secondo'],))
    r = client.post(f"/admin/utenti/{dati['admin_a']}/elimina", follow_redirects=True)
    assert 'riattiva quello disattivato' in r.get_data(as_text=True)
    with app.app_context():
        riga = query_one("SELECT eliminato_il, email FROM utenti WHERE id=?",
                         (dati['admin_a'],))
        assert riga['eliminato_il'] is None
        assert riga['email'] == 'admin@a.it'


def test_cancellare_un_tecnico_non_restituisce_piu_500(client, app, dati):
    """Prima della correzione: HTTP 500 e tecnico ancora presente, perche' la
    rotta azzerava manutenzioni.updated_by e verifiche.updated_by, colonne che
    non esistono."""
    from models import execute, query_one
    with app.app_context():
        div = query_one("SELECT id FROM divisioni WHERE struttura_id=?", (dati['a'],))['id']
        execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,"
                "stato,created_by) VALUES (?,?,'T-1','A','B','funzionante',?)",
                (div, dati['a'], dati['tec']))
    entra(client, 'super@x.it')
    r = client.post(f"/admin/tecnici/{dati['tec']}/elimina", follow_redirects=False)
    assert r.status_code != 500
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['tec'],))['eliminato_il'] is not None


def test_cancellando_un_tecnico_l_autore_resta(client, app, dati):
    """Con la primitiva nuova il nome resta: un miglioramento, non solo una
    riparazione."""
    from models import execute, query_one
    with app.app_context():
        div = query_one("SELECT id FROM divisioni WHERE struttura_id=?", (dati['a'],))['id']
        ap = execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,"
                     "modello,stato,created_by) VALUES (?,?,'T-2','A','B','funzionante',?)",
                     (div, dati['a'], dati['tec'])).lastrowid
    entra(client, 'super@x.it')
    client.post(f"/admin/tecnici/{dati['tec']}/elimina", follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT created_by FROM apparecchi WHERE id=?",
                         (ap,))['created_by'] == dati['tec']


def test_un_guasto_dopo_la_registrazione_di_un_tecnico_non_dice_che_nulla_e_cambiato(
        client, app, dati, monkeypatch):
    """Stesso schema di test_un_guasto_dopo_la_registrazione_non_dice_che_nulla_e_cambiato
    (Task 4), qui per tecnico_elimina: il vero punto di non ritorno e' dentro
    log_attivita, che passa da models.execute() e committa gia' sulla stessa
    connessione. Un guasto DOPO quella chiamata (qui simulato facendo esplodere
    log_attivita subito dopo aver fatto il suo lavoro vero) trova il tecnico
    gia' cancellato in modo durevole: il messaggio all'operatore deve dirlo,
    non deve affermare che "nulla e' stato modificato"."""
    import admin as modulo_admin
    from models import query_one

    log_vero = modulo_admin.log_attivita

    def registra_e_poi_esplodi(*args, **kwargs):
        log_vero(*args, **kwargs)
        raise RuntimeError('guasto simulato dopo la registrazione')

    monkeypatch.setattr(modulo_admin, 'log_attivita', registra_e_poi_esplodi)

    entra(client, 'super@x.it')
    r = client.post(f"/admin/tecnici/{dati['tec']}/elimina", follow_redirects=True)
    testo = r.get_data(as_text=True)

    # La cancellazione e' davvero avvenuta (la registrazione l'ha resa
    # durevole prima del guasto simulato) ...
    with app.app_context():
        assert query_one("SELECT eliminato_il FROM utenti WHERE id=?",
                         (dati['tec'],))['eliminato_il'] is not None
    # ... quindi il messaggio mostrato non puo' essere quello per il caso in
    # cui non e' successo nulla (severita' 'danger', frase "fallita, nulla") ...
    assert 'alert-danger' not in testo
    assert 'fallita, nulla' not in testo
    # ... deve invece avvisare con severita' 'warning' che l'operazione e'
    # avvenuta ma qualcosa e' andato storto dopo.
    assert 'alert-warning' in testo
    assert 'cancellato' in testo.lower()
    assert 'subito dopo' in testo.lower()


def test_tecnico_modifica_rifiuta_un_tecnico_cancellato(client, app, dati):
    """tecnico_modifica e' una delle sei rotte per id che devono rifiutare un
    utente con eliminato_il valorizzato, ma era rimasta fuori: raggiungibile
    con un URL scritto a mano (l'elenco tecnici la filtra correttamente, ma
    la rotta stessa no). Senza la guardia, salvare il modulo su un tecnico
    gia' cancellato riscrive l'email originale sopra alla forma spostata --
    riprendendosi l'indirizzo che la cancellazione aveva liberato, in modo
    invisibile in ogni schermata -- e ricrea le assegnazioni in
    tecnici_strutture su un account che non esiste piu'."""
    from models import query_one, query_all, get_db
    from utente_service import cancella_utente
    entra(client, 'super@x.it')
    with app.app_context():
        conn = get_db()
        cancella_utente(conn, dati['tec'])
        conn.commit()
        email_spostata = query_one("SELECT email FROM utenti WHERE id=?",
                                   (dati['tec'],))['email']

    r_get = client.get(f"/admin/tecnici/{dati['tec']}/modifica")
    assert r_get.status_code in (301, 302)

    r_post = client.post(f"/admin/tecnici/{dati['tec']}/modifica",
                data={'nome': 'RESUSCITATO', 'cognome': 'T', 'email': 'tec@x.it',
                      'strutture': [str(dati['a'])]},
                follow_redirects=True)
    assert r_post.status_code == 200

    with app.app_context():
        u = query_one("SELECT nome, email, eliminato_il FROM utenti WHERE id=?",
                      (dati['tec'],))
        assert u['eliminato_il'] is not None
        assert u['nome'] != 'RESUSCITATO'
        assert u['email'] == email_spostata
        assert u['email'] != 'tec@x.it'
        assegnazioni = query_all(
            "SELECT * FROM tecnici_strutture WHERE tecnico_id=?", (dati['tec'],))
        assert assegnazioni == []


def test_utente_reset_password_rifiuta_un_tecnico(client, app, dati):
    """L'unica delle sei rotte per id senza questa guardia. In pratica passa
    solo il superadmin (un admin e' gia' fermato da _check_utente_scope, il
    cui struttura_id nullo non coincide con nessuna struttura), quindi non ci
    sono conseguenze sfruttabili -- ma senza la guardia le sessioni di un
    tecnico al lavoro verrebbero chiuse da una pagina che non e' la sua."""
    from models import query_one
    entra(client, 'super@x.it')
    with app.app_context():
        prima = query_one("SELECT password_hash FROM utenti WHERE id=?",
                          (dati['tec'],))['password_hash']
    r = client.post(f"/admin/utenti/{dati['tec']}/reset-password",
                    follow_redirects=True)
    assert 'loro pagina' in r.get_data(as_text=True).lower()
    with app.app_context():
        dopo = query_one("SELECT password_hash FROM utenti WHERE id=?",
                         (dati['tec'],))['password_hash']
    assert dopo == prima
