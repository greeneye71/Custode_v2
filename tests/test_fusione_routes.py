"""Le rotte della fusione: chi puo' arrivarci e cosa vede."""
import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def dati(app):
    """Una struttura con due schede duplicate e un utente per ogni ruolo che
    conta. La struttura B esiste per dimostrare l'isolamento."""
    from models import execute
    with app.app_context():
        a = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        b = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica B','B',1)").lastrowid
        da = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Oculistica','OCU',?)",
                     (a,)).lastrowid
        db_ = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Cardiologia','CAR',?)",
                      (b,)).lastrowid
        hash_pw = generate_password_hash('Passw0rd!')
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
                "VALUES ('admin@a.it',?,'A','A','admin',?,0)", (hash_pw, a))
        semplice = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('utente@a.it',?,'U','U','utente',?,0)", (hash_pw, a)).lastrowid
        execute("INSERT INTO utenti_divisioni (utente_id,divisione_id,ruolo_divisione) "
                "VALUES (?,?,'utente')", (semplice, da))

        uno = execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,"
                      "stato,ubicazione) VALUES (?,?,'R-00015','REXXAM','OZY','funzionante','Sala 1')",
                      (da, a)).lastrowid
        due = execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,"
                      "stato,ubicazione) VALUES (?,?,'R00015','REXXAM','OZY','funzionante','Sala 1')",
                      (da, a)).lastrowid
        estraneo = execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,"
                           "modello,stato) VALUES (?,?,'SEGRETO-B','SIEMENS','Y1','funzionante')",
                           (db_, b)).lastrowid
    return {'a': a, 'b': b, 'div_a': da, 'div_b': db_,
            'uno': uno, 'due': due, 'estraneo': estraneo}


def entra(client, email):
    client.post('/login', data={'email': email, 'password': 'Passw0rd!'})


def test_l_elenco_propone_la_coppia_duplicata(client, dati):
    entra(client, 'admin@a.it')
    risposta = client.get('/apparecchi/duplicati')
    assert risposta.status_code == 200
    testo = risposta.get_data(as_text=True)
    assert 'R-00015' in testo
    assert 'R00015' in testo


def test_l_elenco_non_mostra_apparecchi_di_altre_strutture(client, app, dati):
    """L'elenco confronta gli apparecchi fra loro: senza filtro di struttura
    proporrebbe coppie fra tenant diversi, e la pagina stessa mostrerebbe
    matricole altrui."""
    from models import execute
    with app.app_context():
        # Una matricola equivalente a 'SEGRETO-B' (stessa normalizzazione,
        # letterale diverso per non violare UNIQUE(struttura_id, modello,
        # matricola) contro l'apparecchio 'estraneo' della fixture): se il
        # filtro di struttura manca, la coppia compare di sicuro.
        execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,"
                "stato,ubicazione) VALUES (?,?,'SEGRETO B','SIEMENS','Y1','funzionante','X')",
                (dati['div_b'], dati['b']))
    entra(client, 'admin@a.it')
    testo = client.get('/apparecchi/duplicati').get_data(as_text=True)
    assert 'SEGRETO-B' not in testo


def test_l_elenco_e_negato_a_un_utente_semplice(client, dati):
    """La fusione cancella una scheda, e un utente non puo' nemmeno
    dismetterne una.

    Il test resta ancorato al comportamento della ROTTA (redirect, non 200
    sulla pagina) e non al contenuto della pagina di atterraggio: seguire il
    redirect e controllare solo l'assenza della matricola li' non
    distinguerebbe un accesso negato da un accesso concesso per errore ma
    atterrato su una vista che semplicemente non aveva nulla da mostrare -
    esattamente cio' che e' successo quando l'utente semplice era stato
    spostato in una divisione senza apparecchi per aggirare un falso
    negativo diverso."""
    entra(client, 'utente@a.it')
    risposta = client.get('/apparecchi/duplicati', follow_redirects=False)
    assert risposta.status_code == 302
    assert 'R00015' not in risposta.get_data(as_text=True)


def test_l_elenco_dice_perche_propone_la_coppia(client, dati):
    """Chi guarda deve sapere quale criterio l'ha proposta: e' cio' che
    distingue una corrispondenza certa da una somiglianza da verificare."""
    entra(client, 'admin@a.it')
    testo = client.get('/apparecchi/duplicati').get_data(as_text=True)
    assert 'matricola identica a meno di trattini' in testo


def test_la_fusione_dalla_rotta_somma_gli_interventi(client, app, dati):
    from models import execute, query_one
    with app.app_context():
        for ap in (dati['uno'], dati['due']):
            execute("INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento) "
                    "VALUES (?,'preventiva','2026-03-12')", (ap,))
    entra(client, 'admin@a.it')
    risposta = client.post(
        f"/apparecchi/{dati['uno']}/fondi/{dati['due']}",
        data={'principale': dati['uno']}, follow_redirects=True)
    assert risposta.status_code == 200
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM manutenzioni WHERE apparecchio_id=?",
                         (dati['uno'],))['n'] == 2
        assert query_one("SELECT id FROM apparecchi WHERE id=?", (dati['due'],)) is None


def test_la_fusione_e_negata_fra_strutture_diverse(client, app, dati):
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/apparecchi/{dati['uno']}/fondi/{dati['estraneo']}",
                data={'principale': dati['uno']}, follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT id FROM apparecchi WHERE id=?", (dati['estraneo'],)) is not None


def test_la_fusione_e_negata_a_un_utente_semplice(client, app, dati):
    from models import query_one
    entra(client, 'utente@a.it')
    client.post(f"/apparecchi/{dati['uno']}/fondi/{dati['due']}",
                data={'principale': dati['uno']}, follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT id FROM apparecchi WHERE id=?", (dati['due'],)) is not None


def test_il_registro_conserva_la_scheda_scartata(client, app, dati):
    """La fusione e' definitiva: senza i campi nel registro, ricostruire a
    mano la scheda cancellata diventa impossibile."""
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/apparecchi/{dati['uno']}/fondi/{dati['due']}",
                data={'principale': dati['uno']}, follow_redirects=True)
    with app.app_context():
        voce = query_one("SELECT dettagli FROM log_attivita WHERE azione='fusione'")
        assert voce is not None
        # 'R00015' e 'OZY' compaiono anche nel riassunto iniziale del
        # messaggio ("Fusi ... in id ..."): da soli non proverebbero che il
        # blocco "Scheda scartata: campo=valore ..." campo per campo esiste
        # davvero. ubicazione non compare altrove nel messaggio, quindi la
        # sua presenza dimostra che quel blocco e' stato scritto.
        assert "Scheda scartata: matricola='R00015'" in voce['dettagli']
        assert "ubicazione='Sala 1'" in voce['dettagli']


def test_la_pagina_di_confronto_mostra_i_campi_diversi(client, dati):
    entra(client, 'admin@a.it')
    testo = client.get(f"/apparecchi/{dati['uno']}/fondi/{dati['due']}").get_data(as_text=True)
    assert 'R-00015' in testo
    assert 'R00015' in testo
    assert 'Matricola' in testo


def test_la_collisione_con_un_terzo_viene_spiegata_non_lanciata(client, app, dati):
    from models import execute, query_one
    with app.app_context():
        execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato) "
                "VALUES (?,?,'COLLIDE','REXXAM','OZY','funzionante')",
                (dati['div_a'], dati['a']))
    entra(client, 'admin@a.it')
    risposta = client.post(
        f"/apparecchi/{dati['uno']}/fondi/{dati['due']}",
        data={'principale': dati['uno'], 'campo_matricola': 'COLLIDE'},
        follow_redirects=True)
    assert risposta.status_code == 200
    assert 'Esiste gia' in risposta.get_data(as_text=True)
    with app.app_context():
        assert query_one("SELECT id FROM apparecchi WHERE id=?", (dati['due'],)) is not None


def test_una_fusione_fallita_non_lascia_scritture_durevoli(client, app, dati, monkeypatch):
    """I sei test precedenti provano i casi che riescono e i rifiuti che
    avvengono PRIMA di scrivere (FusioneRifiutataError, FusioneCollisioneError):
    nessuno prova il guasto a meta', quando fondi_apparecchi ha gia' scritto
    qualcosa sulla connessione della richiesta e poi solleva un'eccezione
    imprevista. Qui si simula quel guasto e si controlla che non resti nulla
    di quella scrittura: un update parziale sopravvissuto sarebbe
    indistinguibile da una fusione riuscita a meta', e l'operatore non
    avrebbe modo di saperlo dalla sola interfaccia.

    ATTENZIONE al nome: NON e' un test del db.rollback() esplicito nel ramo
    `except Exception` di esegui_fusione. Con quella riga tolta, questo test
    resta verde lo stesso (verificato: rimuovendola a mano e rilanciando
    solo questo test, passa comunque). Il motivo e' in models.get_db/close_db:
    la connessione della richiesta viene chiusa a fine richiesta con
    db.close(), MAI preceduta da un commit implicito; e nel percorso di
    guasto qui esercitato il codice non raggiunge mai db.commit() (sta dopo
    la chiamata a fondi_apparecchi nel try, e l'eccezione la salta). Senza un
    commit di mezzo, l'UPDATE fatto dentro fondi_apparecchi_a_meta non e' mai
    diventato durevole: sparisce alla chiusura della connessione a
    prescindere dal rollback esplicito. Quella riga quindi OGGI non e'
    coperta da nessun test - ne' questo ne' altri - e chi la leggesse
    protetta da questo test la toglierebbe credendo di non rompere nulla.
    Lo diventerebbe se in futuro la rotta eseguisse un commit intermedio
    prima di chiamare fondi_apparecchi (per esempio un'altra scrittura sulla
    stessa richiesta): in quel momento un rollback mancante lascerebbe quella
    scrittura precedente durevole mentre la fusione fallisce, e servirebbe
    un test dedicato a quello scenario - che oggi non si puo' scrivere
    perche' descriverebbe codice che non esiste.

    Simula il guasto con un monkeypatch su fusione_service.fondi_apparecchi
    che scrive davvero (sposta la manutenzione di 'due' su 'uno') e poi
    solleva RuntimeError senza mai arrivare al commit della rotta.

    Il monkeypatch funziona perche' la rotta fa `from fusione_service import
    fondi_apparecchi` DENTRO la funzione esegui_fusione, non in cima al file:
    il nome viene risolto al momento della chiamata, quindi sostituire
    l'attributo sul modulo prima della POST e' sufficiente. Se qualcuno
    "semplificasse" spostando l'import a livello di modulo, il monkeypatch
    smetterebbe di avere effetto e questo test non proverebbe piu' nulla
    (patcherebbe una copia del nome gia' legata nel namespace di apparecchi.py).
    """
    from models import execute, query_one
    import fusione_service

    with app.app_context():
        m = execute("INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento) "
                    "VALUES (?,'preventiva','2026-01-01')", (dati['due'],)).lastrowid

    def fondi_apparecchi_a_meta(conn, id_principale, id_scartato, valori=None,
                                interventi_scartati=()):
        conn.execute("UPDATE manutenzioni SET apparecchio_id = ? WHERE apparecchio_id = ?",
                     (id_principale, id_scartato))
        raise RuntimeError("guasto simulato a meta' operazione")

    monkeypatch.setattr(fusione_service, 'fondi_apparecchi', fondi_apparecchi_a_meta)

    entra(client, 'admin@a.it')
    risposta = client.post(
        f"/apparecchi/{dati['uno']}/fondi/{dati['due']}",
        data={'principale': dati['uno']}, follow_redirects=True)

    assert risposta.status_code == 200
    assert 'Fusione fallita' in risposta.get_data(as_text=True)

    with app.app_context():
        # Se il rollback ha funzionato, la manutenzione e' ancora sulla
        # scheda 'due' (non spostata), e 'due' esiste ancora.
        riga = query_one("SELECT apparecchio_id FROM manutenzioni WHERE id=?", (m,))
        assert riga['apparecchio_id'] == dati['due']
        assert query_one("SELECT id FROM apparecchi WHERE id=?", (dati['due'],)) is not None
