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


def test_l_elenco_propone_duplicati_fra_divisioni_diverse(client, app, dati):
    """auth.py mette un admin appena entrato (nessuna scelta di divisione in
    sessione) sulla PRIMA divisione della struttura (g.divisioni[0]), non su
    'tutte' - quello e' il default del solo tecnico. Se l'elenco confrontasse
    con filtro_divisione() (che onora g.divisione_attiva), un duplicato che
    vive in due reparti diversi non verrebbe mai proposto: e' proprio il caso
    che il CHANGELOG cita per primo ("import da documenti diversi o un
    inserimento manuale in due reparti").

    Qui la struttura ha DUE divisioni ('Cardiologia' e 'Oculistica', ordinate
    per nome: Cardiologia prima); l'admin finisce quindi bloccato su
    Cardiologia. I due apparecchi duplicati vivono uno in Cardiologia e uno
    in Oculistica: solo confrontando l'intera struttura la coppia emerge."""
    from models import execute
    with app.app_context():
        div_secondaria = execute(
            "INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Cardiologia','CAR',?)",
            (dati['a'],)).lastrowid
        # Sposto 'due' nella nuova divisione: 'uno' resta in Oculistica
        # (dati['div_a']), quindi la coppia duplicata attraversa i reparti.
        execute("UPDATE apparecchi SET divisione_id=? WHERE id=?",
                (div_secondaria, dati['due']))
    entra(client, 'admin@a.it')  # primo accesso: nessuna divisione ancora scelta in sessione
    risposta = client.get('/apparecchi/duplicati')
    assert risposta.status_code == 200
    testo = risposta.get_data(as_text=True)
    assert 'Nessuna coppia sospetta' not in testo
    assert 'R-00015' in testo
    assert 'R00015' in testo


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
        # blocco "Scheda scartata: campo=valore ..." esiste davvero. Il
        # blocco itera su dict(scartato) (tutte le colonne, non solo
        # CAMPI_FONDIBILI), quindi l'ordine dei campi segue quello delle
        # colonne della tabella e non e' garantito: si ancora al formato
        # "campo='valore'" con le virgolette, che nel riassunto iniziale non
        # compare mai, invece che a una posizione fissa dopo "Scheda
        # scartata:".
        assert 'Scheda scartata:' in voce['dettagli']
        assert "matricola='R00015'" in voce['dettagli']
        assert "ubicazione='Sala 1'" in voce['dettagli']


def test_il_registro_conserva_i_valori_precedenti_della_superstite(client, app, dati):
    """La scheda scartata non e' l'unica a cambiare: i valori scelti dal
    form possono sovrascrivere anche la scheda SUPERSTITE, e quel valore
    vecchio esiste solo per un istante - nel database resta solo la forma
    nuova. Senza di esso nel registro, ricostruire lo stato precedente e'
    impossibile per meta' dei dati toccati dalla fusione."""
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/apparecchi/{dati['uno']}/fondi/{dati['due']}",
                data={'principale': dati['uno'], 'campo_matricola': 'R00015'},
                follow_redirects=True)
    with app.app_context():
        voce = query_one("SELECT dettagli FROM log_attivita WHERE azione='fusione'")
        assert voce is not None
        assert ("Valori precedenti sulla scheda superstite (sovrascritti): "
                "matricola='R-00015'") in voce['dettagli']


def test_la_pagina_di_confronto_mostra_i_campi_diversi(client, dati):
    entra(client, 'admin@a.it')
    testo = client.get(f"/apparecchi/{dati['uno']}/fondi/{dati['due']}").get_data(as_text=True)
    assert 'R-00015' in testo
    assert 'R00015' in testo
    assert 'Matricola' in testo


def test_la_pagina_di_confronto_marca_il_proprietario_di_ogni_valore(client, dati):
    """Il predefinito deve poter seguire la scelta di quale scheda
    sopravvive: lo script della pagina si affida a data-owner per sapere a
    quale scheda (uno o due) appartiene ciascun valore proposto per un campo
    diverso. Senza questi attributi il predefinito resterebbe sempre quello
    calcolato per 'uno', a prescindere da quale principale l'operatore
    sceglie."""
    entra(client, 'admin@a.it')
    testo = client.get(f"/apparecchi/{dati['uno']}/fondi/{dati['due']}").get_data(as_text=True)
    assert f'data-owner="{dati["uno"]}"' in testo
    assert f'data-owner="{dati["due"]}"' in testo


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


def test_il_registro_conserva_anche_i_campi_non_scegibili_dal_form(client, app, dati):
    """CAMPI_FONDIBILI governa cosa il FORM puo' far scegliere ed esclude di
    proposito divisione_id e le colonne di audit: spostare un apparecchio di
    divisione o di struttura e' un'altra funzione, con altri controlli. Ma il
    registro non e' il form: e' l'unica traccia che resta della scheda
    scartata, e divisione_id e' NOT NULL nello schema - senza, ricostruire a
    mano la scheda cancellata non e' possibile. Il messaggio deve quindi
    riversare la riga INTERA (dict(scartato) via esito['scartato']), non
    filtrata da CAMPI_FONDIBILI."""
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/apparecchi/{dati['uno']}/fondi/{dati['due']}",
                data={'principale': dati['uno']}, follow_redirects=True)
    with app.app_context():
        voce = query_one("SELECT dettagli FROM log_attivita WHERE azione='fusione'")
        assert voce is not None
        assert f"divisione_id={dati['div_a']}" in voce['dettagli']


def test_se_la_registrazione_fallisce_la_fusione_non_risulta_avvenuta(client, app, dati, monkeypatch):
    """Prima di questa correzione log_attivita veniva chiamata DOPO il commit
    della fusione: un guasto proprio li' dentro lasciava una scheda gia'
    cancellata senza alcuna voce di registro a spiegare cosa conteneva - la
    fusione era gia' avvenuta, ma diventata irrintracciabile, ed e'
    esattamente lo scenario che l'unica rete rimasta (il registro) doveva
    escludere. Spostando log_attivita dentro il try, prima del commit, un
    guasto nella scrittura del registro fa fallire (e annullare) tutto
    insieme: meglio nessuna fusione che una fusione senza traccia.

    apparecchi.py importa log_attivita a livello di modulo
    (`from models import (..., log_attivita, ...)`), non dentro la funzione
    come fa invece per fusione_service: il nome e' gia' legato nel namespace
    di apparecchi al momento dell'import, quindi il monkeypatch deve colpire
    apparecchi.log_attivita, non models.log_attivita - patchare quest'ultimo
    non avrebbe alcun effetto sulla rotta.
    """
    from models import query_one
    import apparecchi

    def log_attivita_che_esplode(*args, **kwargs):
        raise RuntimeError("guasto simulato nella scrittura del registro")

    monkeypatch.setattr(apparecchi, 'log_attivita', log_attivita_che_esplode)

    entra(client, 'admin@a.it')
    risposta = client.post(
        f"/apparecchi/{dati['uno']}/fondi/{dati['due']}",
        data={'principale': dati['uno']}, follow_redirects=True)

    assert risposta.status_code == 200
    assert 'Fusione fallita' in risposta.get_data(as_text=True)

    with app.app_context():
        assert query_one("SELECT id FROM apparecchi WHERE id=?", (dati['due'],)) is not None
        assert query_one(
            "SELECT COUNT(*) AS n FROM log_attivita WHERE azione='fusione'")['n'] == 0
