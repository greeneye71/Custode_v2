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


def test_la_voce_di_registro_e_visibile_a_chi_ha_fuso(client, app, dati):
    """log_attivita chiamata senza struttura_id (settimo parametro) nasce con
    struttura_id NULL: admin.py filtra 'l.struttura_id = ?' per chiunque non
    sia superadmin, e NULL = ? non e' mai vero. L'admin che ha eseguito la
    fusione non vedrebbe la propria voce - l'unica traccia rimasta di una
    scheda cancellata in modo definitivo."""
    entra(client, 'admin@a.it')
    client.post(f"/apparecchi/{dati['uno']}/fondi/{dati['due']}",
                data={'principale': dati['uno']}, follow_redirects=True)
    risposta = client.get('/admin/log-attivita')
    assert risposta.status_code == 200
    testo = risposta.get_data(as_text=True)
    assert 'fusione' in testo


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


def test_la_get_di_confronto_rifiuta_lo_stesso_id_due_volte(client, dati):
    """Senza guardia la GET renderebbe una pagina 'funzionante' (bottone di
    fusione, due radio 'principale' con lo stesso value e lo stesso id HTML
    duplicato) che rimanda a se stessa: la POST rifiuta gia' id == altro_id,
    ma il redirect di quel rifiuto punta proprio a questa pagina, un anello.
    La GET deve chiuderlo per prima."""
    entra(client, 'admin@a.it')
    risposta = client.get(f"/apparecchi/{dati['uno']}/fondi/{dati['uno']}",
                          follow_redirects=False)
    assert risposta.status_code == 302


def test_la_get_di_confronto_e_negata_fra_strutture_diverse(client, app, dati):
    """_due_schede_fondibili e' l'UNICO presidio della GET (nessun'altra
    query filtrata la protegge): usa apparecchio_accessibile per entrambi
    gli id, esattamente il controllo che la 2.6.0 ha reso obbligatorio su
    nove rotte dopo che una lettura per id nuda ci era passata sotto silenzio
    per mesi. Se qualcuno sostituisse apparecchio_accessibile con una SELECT
    diretta per id, la POST rifiuterebbe comunque (fondi_apparecchi controlla
    la struttura), ma la GET renderebbe la pagina di confronto con dentro i
    dati riservati dell'altra struttura - motivo per cui qui si controlla
    l'EFFETTO e lo STATUS, non solo l'assenza di una stringa nella pagina di
    atterraggio dopo un redirect."""
    from models import execute
    with app.app_context():
        execute("UPDATE apparecchi SET ubicazione='Bunker riservato', "
                "note='Contratto 4.500 EUR' WHERE id=?", (dati['estraneo'],))
    entra(client, 'admin@a.it')

    negata = client.get(f"/apparecchi/{dati['uno']}/fondi/{dati['estraneo']}",
                        follow_redirects=False)
    assert negata.status_code == 302

    atterraggio = client.get(f"/apparecchi/{dati['uno']}/fondi/{dati['estraneo']}",
                             follow_redirects=True)
    testo = atterraggio.get_data(as_text=True)
    assert 'Bunker riservato' not in testo
    assert 'Contratto 4.500 EUR' not in testo
    assert 'SEGRETO-B' not in testo


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


def test_un_guasto_reale_dopo_la_delete_non_lascia_la_scheda_cancellata(client, app, dati):
    """test_una_fusione_fallita_non_lascia_scritture_durevoli sostituisce
    fondi_apparecchi per intero con un monkeypatch: non esercita mai
    l'ordinamento vero della funzione ne' un guasto che il database stesso
    solleva. Qui il guasto e' reale e passa dal codice di produzione fino in
    fondo: 'rottamato' supera la validazione della rotta (CAMPI_FONDIBILI
    ammette 'stato') ma non CHECK(stato IN ('funzionante', 'in_manutenzione',
    'dismesso', 'da_sostituire')) dello schema. fondi_apparecchi applica i
    valori scelti SOLO dopo la DELETE della scheda scartata (altrimenti
    UNIQUE(struttura_id, modello, matricola) rifiuterebbe l'UPDATE mentre la
    scartata esiste ancora - vedi test_la_principale_puo_prendere_la_
    matricola_della_scartata): l'IntegrityError del CHECK arriva quindi
    QUANDO la scheda scartata e' gia' cancellata e le sue manutenzioni gia'
    spostate. E' il percorso piu' pericoloso della funzione - quello in cui
    un rollback mancante lascerebbe una scheda cancellata per sempre - e
    nessun test lo esercitava con codice vero."""
    from models import execute, query_one
    with app.app_context():
        m = execute("INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento) "
                    "VALUES (?,'preventiva','2026-01-01')", (dati['due'],)).lastrowid

    entra(client, 'admin@a.it')
    risposta = client.post(
        f"/apparecchi/{dati['uno']}/fondi/{dati['due']}",
        data={'principale': dati['uno'], 'campo_stato': 'rottamato'},
        follow_redirects=True)

    assert risposta.status_code == 200
    assert 'Fusione fallita' in risposta.get_data(as_text=True)

    with app.app_context():
        # La scheda 'due' torna, con la sua manutenzione al suo posto (non
        # spostata su 'uno'), e nessuna voce di registro: il rollback ha
        # annullato tutto cio' che fondi_apparecchi aveva gia' scritto,
        # DELETE della scartata compresa.
        assert query_one("SELECT id FROM apparecchi WHERE id=?", (dati['due'],)) is not None
        riga = query_one("SELECT apparecchio_id FROM manutenzioni WHERE id=?", (m,))
        assert riga['apparecchio_id'] == dati['due']
        assert query_one(
            "SELECT COUNT(*) AS n FROM log_attivita WHERE azione='fusione'")['n'] == 0


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


# ---------------------------------------------------------------------------
# I due punti d'ingresso (2.6.2)
# ---------------------------------------------------------------------------

def test_il_pulsante_dei_duplicati_non_e_piu_nella_scheda(client, dati):
    """Riguarda tutti gli apparecchi della struttura, non quello aperto: stava
    nel posto sbagliato per un errore del piano della 2.6.1."""
    entra(client, 'admin@a.it')
    pagina = client.get(f"/apparecchi/{dati['uno']}").get_data(as_text=True)
    assert '/apparecchi/duplicati' not in pagina


def test_il_pulsante_dei_duplicati_e_nell_elenco(client, dati):
    entra(client, 'admin@a.it')
    pagina = client.get('/apparecchi').get_data(as_text=True)
    assert '/apparecchi/duplicati' in pagina


def test_l_utente_semplice_non_vede_il_pulsante_dei_duplicati(client, dati):
    """La condizione di ruolo c'era in dettaglio.html: spostando il pulsante non
    va persa, o l'utente semplice clicca per ricevere un rifiuto."""
    entra(client, 'utente@a.it')
    pagina = client.get('/apparecchi').get_data(as_text=True)
    assert '/apparecchi/duplicati' not in pagina


def test_la_ricerca_trova_un_duplicato_che_l_algoritmo_non_propone(client, app, dati):
    """L'intero motivo per cui questa via esiste. Le due matricole sono cosi'
    diverse che nessuno dei tre criteri le accosta: usare una coppia
    proponibile non proverebbe niente."""
    from models import execute
    with app.app_context():
        altro = execute(
            "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,"
            "stato,ubicazione) VALUES (?,?,'INV/2019/887','REXXAM','OZY','funzionante','Sala 1')",
            (dati['div_a'], dati['a'])).lastrowid
    entra(client, 'admin@a.it')

    # L'elenco automatico non la propone...
    automatico = client.get('/apparecchi/duplicati').get_data(as_text=True)
    assert 'INV/2019/887' not in automatico

    # ...ma la ricerca manuale la trova.
    pagina = client.get(f"/apparecchi/{dati['uno']}/fondi?cerca=INV/2019").get_data(as_text=True)
    assert 'INV/2019/887' in pagina
    assert f"/apparecchi/{dati['uno']}/fondi/{altro}" in pagina


def test_la_ricerca_non_esce_dalla_struttura(client, dati):
    """Nemmeno cercando la matricola esatta di un apparecchio di un'altra
    struttura.

    Si cerca l'assenza del COLLEGAMENTO a quella scheda, non della stringa: il
    modulo di ricerca rimanda il termine cercato dentro il campo 'value', per
    non farlo riscrivere a chi affina la ricerca. Cercare 'SEGRETO-B' nella
    pagina lo trova sempre — quella prima versione del test falliva su un
    codice corretto, e con una matricola non riflessa sarebbe passata
    qualunque cosa la rotta restituisse."""
    entra(client, 'admin@a.it')
    pagina = client.get(f"/apparecchi/{dati['uno']}/fondi?cerca=SEGRETO-B").get_data(as_text=True)
    assert f"/fondi/{dati['estraneo']}" not in pagina
    assert 'SIEMENS' not in pagina


def test_la_ricerca_non_restituisce_la_scheda_di_partenza(client, dati):
    """Fondere un apparecchio con se stesso e' un anello che la pagina di
    confronto rifiuta gia': non va nemmeno offerto."""
    entra(client, 'admin@a.it')
    pagina = client.get(f"/apparecchi/{dati['uno']}/fondi?cerca=REXXAM").get_data(as_text=True)
    assert f"/apparecchi/{dati['uno']}/fondi/{dati['uno']}" not in pagina
    assert f"/apparecchi/{dati['uno']}/fondi/{dati['due']}" in pagina


def test_senza_testo_cercato_non_elenca_niente(client, dati):
    """Un invito a cercare, non l'intero parco: su una struttura con migliaia
    di apparecchi un elenco completo non aiuta a trovare il duplicato e costa
    una pagina lenta.

    Il freno e' doppio e i due strati sono ridondanti: la rotta non interroga
    il database senza testo, e il template non stampa la tabella senza testo.
    Rompendone uno solo questo test resta verde (verificato per entrambi):
    cade quando si rompono tutti e due. E' quindi una prova della coppia, non
    di ciascun pezzo — chi ne toglie uno non se ne accorge da qui."""
    entra(client, 'admin@a.it')
    pagina = client.get(f"/apparecchi/{dati['uno']}/fondi").get_data(as_text=True)
    assert f"/apparecchi/{dati['uno']}/fondi/{dati['due']}" not in pagina


def test_la_ricerca_trova_un_dismesso_e_lo_segnala(client, app, dati):
    """Divergenza voluta rispetto all'elenco automatico, ed e' quella che
    qualcuno «uniformera'» per sbaglio: chi si accorge del doppione e invece di
    fondere DISMETTE una delle due schede lascia una macchina con lo storico
    spezzato, meta' del quale su una scheda che l'algoritmo non propone."""
    from models import execute
    with app.app_context():
        execute("UPDATE apparecchi SET stato='dismesso' WHERE id=?", (dati['due'],))
    entra(client, 'admin@a.it')

    pagina = client.get(f"/apparecchi/{dati['uno']}/fondi?cerca=R00015").get_data(as_text=True)
    assert 'R00015' in pagina
    assert 'dismesso' in pagina.lower()

    # E l'elenco automatico continua a non proporlo.
    automatico = client.get('/apparecchi/duplicati').get_data(as_text=True)
    assert 'R00015' not in automatico


def test_la_ricerca_e_negata_a_un_utente_semplice(client, dati):
    """Si guarda lo stato della rotta, non il contenuto della pagina di
    atterraggio: un redirect verso l'elenco e' comunque un rifiuto."""
    entra(client, 'utente@a.it')
    risposta = client.get(f"/apparecchi/{dati['uno']}/fondi?cerca=REXXAM")
    assert risposta.status_code == 302
    assert '/apparecchi' in risposta.headers['Location']


def test_dalla_ricerca_si_arriva_alla_fusione_e_si_conclude(client, app, dati):
    """Il giro completo: due meta' che funzionano da sole non fanno una
    funzione che funziona."""
    from models import query_one
    entra(client, 'admin@a.it')
    pagina = client.get(f"/apparecchi/{dati['uno']}/fondi?cerca=R00015").get_data(as_text=True)
    assert f"/apparecchi/{dati['uno']}/fondi/{dati['due']}" in pagina

    confronto = client.get(f"/apparecchi/{dati['uno']}/fondi/{dati['due']}")
    assert confronto.status_code == 200

    client.post(f"/apparecchi/{dati['uno']}/fondi/{dati['due']}",
                data={'principale': str(dati['uno'])}, follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT id FROM apparecchi WHERE id=?", (dati['due'],)) is None
        assert query_one("SELECT id FROM apparecchi WHERE id=?", (dati['uno'],)) is not None
