"""Rotte di gestione delle strutture. Tutte riservate al superadmin."""
import pytest
from werkzeug.security import generate_password_hash

# I loghi di prova devono cominciare come un PNG vero: da 2.8.4 le rotte di
# upload verificano il contenuto e non solo l'estensione (M05).
PNG = b'\x89PNG\r\n\x1a\n'


@pytest.fixture
def dati(app):
    from models import execute
    with app.app_context():
        s = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        spenta = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Chiusa','CH',0)").lastrowid
        d = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Oculistica','OCU',?)", (s,)).lastrowid
        execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato) "
                "VALUES (?,?,'OCU-1','REXXAM','OZY','funzionante')", (d, s))
        hash_pw = generate_password_hash('Passw0rd!')
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,primo_accesso) "
                "VALUES ('super@x.it',?,'S','S','superadmin',0)", (hash_pw,))
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
                "VALUES ('admin@a.it',?,'A','A','admin',?,0)", (hash_pw, s))
    return {'s': s, 'spenta': spenta}


def entra(client, email):
    client.post('/login', data={'email': email, 'password': 'Passw0rd!'})


def test_la_scheda_mostra_il_contenuto(client, dati):
    entra(client, 'super@x.it')
    risposta = client.get(f"/strutture/{dati['s']}")
    assert risposta.status_code == 200
    testo = risposta.get_data(as_text=True)
    assert 'Clinica A' in testo
    assert 'Oculistica' in testo


def test_la_scheda_e_negata_a_un_admin(client, dati):
    # Non si puo' verificare l'assenza cercando 'Oculistica': l'admin ha quella
    # divisione come propria, e il menu di base.html la mostra in ogni pagina
    # (compresa la dashboard di redirect), a prescindere dall'accesso alla
    # scheda. Si verifica invece l'assenza di un testo che compare solo dentro
    # scheda.html, cosi' un decoratore indebolito che lasciasse passare
    # l'admin verrebbe comunque rilevato.
    entra(client, 'admin@a.it')
    risposta = client.get(f"/strutture/{dati['s']}", follow_redirects=True)
    assert 'Tecnici assegnati' not in risposta.get_data(as_text=True)


def test_riattivazione(client, app, dati):
    from models import query_one
    entra(client, 'super@x.it')
    risposta = client.post(f"/strutture/{dati['spenta']}/riattiva", follow_redirects=True)
    assert risposta.status_code == 200
    with app.app_context():
        assert query_one("SELECT attiva FROM strutture WHERE id=?", (dati['spenta'],))['attiva'] == 1


def test_riattivazione_negata_a_un_admin(client, app, dati):
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/strutture/{dati['spenta']}/riattiva", follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT attiva FROM strutture WHERE id=?", (dati['spenta'],))['attiva'] == 0


def test_esportazione_dalla_scheda(client, app, dati):
    import os
    entra(client, 'super@x.it')
    risposta = client.post(f"/strutture/{dati['s']}/esporta", follow_redirects=True)
    assert risposta.status_code == 200
    with app.app_context():
        from flask import current_app
        radice = os.path.join(current_app.config['BACKUPS_PATH'], 'strutture')
        assert os.path.isdir(radice)
        assert len(os.listdir(radice)) == 1


def test_esportazione_negata_a_un_admin(client, app, dati):
    """Come le altre operazioni sull'intero ciclo di vita di una struttura,
    l'esportazione e' riservata al superadmin: un admin vede solo la propria
    struttura e non deve poter produrre un archivio scaricabile."""
    import os
    entra(client, 'admin@a.it')
    client.post(f"/strutture/{dati['s']}/esporta", follow_redirects=True)
    with app.app_context():
        from flask import current_app
        radice = os.path.join(current_app.config['BACKUPS_PATH'], 'strutture')
        assert not os.path.isdir(radice) or len(os.listdir(radice)) == 0


# ---------------------------------------------------------------------------
# Cancellazione
# ---------------------------------------------------------------------------

def test_la_pagina_di_conferma_richiede_la_struttura_disattivata(client, dati):
    """Il freno vale anche sul GET: non basta bloccare la POST se la pagina
    di conferma restasse comunque raggiungibile su una struttura attiva."""
    entra(client, 'super@x.it')
    risposta = client.get(f"/strutture/{dati['s']}/elimina", follow_redirects=True)
    testo = risposta.get_data(as_text=True)
    assert 'disattiva' in testo.lower()
    assert 'Cancella definitivamente' not in testo


def test_la_pagina_di_conferma_mostra_il_contenuto(client, dati):
    entra(client, 'super@x.it')
    risposta = client.get(f"/strutture/{dati['spenta']}/elimina")
    assert risposta.status_code == 200
    testo = risposta.get_data(as_text=True)
    assert 'Chiusa' in testo
    assert 'Cancella definitivamente' in testo


def test_la_pagina_di_conferma_e_negata_a_un_admin(client, dati):
    entra(client, 'admin@a.it')
    risposta = client.get(f"/strutture/{dati['spenta']}/elimina", follow_redirects=True)
    assert 'Cancella definitivamente' not in risposta.get_data(as_text=True)


def test_la_cancellazione_richiede_la_struttura_disattivata(client, app, dati):
    from models import query_one
    entra(client, 'super@x.it')
    risposta = client.post(f"/strutture/{dati['s']}/elimina",
                           data={'conferma_nome': 'Clinica A'}, follow_redirects=True)
    assert 'disattiva' in risposta.get_data(as_text=True).lower()
    with app.app_context():
        assert query_one("SELECT id FROM strutture WHERE id=?", (dati['s'],)) is not None


def test_la_cancellazione_richiede_il_nome_esatto(client, app, dati):
    from models import execute, query_one
    entra(client, 'super@x.it')
    with app.app_context():
        execute("UPDATE strutture SET attiva=0 WHERE id=?", (dati['s'],))
    risposta = client.post(f"/strutture/{dati['s']}/elimina",
                           data={'conferma_nome': 'clinica'}, follow_redirects=True)
    assert 'nome' in risposta.get_data(as_text=True).lower()
    with app.app_context():
        assert query_one("SELECT id FROM strutture WHERE id=?", (dati['s'],)) is not None


def test_la_cancellazione_riuscita(client, app, dati):
    from models import execute, query_one
    entra(client, 'super@x.it')
    with app.app_context():
        execute("UPDATE strutture SET attiva=0 WHERE id=?", (dati['s'],))
    risposta = client.post(f"/strutture/{dati['s']}/elimina",
                           data={'conferma_nome': 'Clinica A'}, follow_redirects=True)
    assert risposta.status_code == 200
    with app.app_context():
        assert query_one("SELECT id FROM strutture WHERE id=?", (dati['s'],)) is None
        assert query_one("SELECT id FROM utenti WHERE email='admin@a.it'") is None
        assert query_one("SELECT id FROM utenti WHERE email='super@x.it'") is not None


def test_la_cancellazione_e_negata_a_un_admin(client, app, dati):
    from models import execute, query_one
    with app.app_context():
        execute("UPDATE strutture SET attiva=0 WHERE id=?", (dati['s'],))
    entra(client, 'admin@a.it')
    client.post(f"/strutture/{dati['s']}/elimina",
                data={'conferma_nome': 'Clinica A'}, follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT id FROM strutture WHERE id=?", (dati['s'],)) is not None


def test_la_scheda_di_una_struttura_attiva_non_mostra_il_pulsante_elimina(client, dati):
    """Il pulsante Elimina compare solo per una struttura gia' disattivata:
    su una attiva la scheda deve spiegare perche' non c'e', non nasconderlo
    e basta (altrimenti un ramo del template smesso di rendersi passerebbe
    inosservato)."""
    entra(client, 'super@x.it')
    risposta = client.get(f"/strutture/{dati['s']}")
    testo = risposta.get_data(as_text=True)
    assert f"/strutture/{dati['s']}/elimina" not in testo
    assert 'disattivala prima' in testo.lower()


def test_la_scheda_di_una_struttura_disattivata_mostra_il_pulsante_elimina(client, dati):
    entra(client, 'super@x.it')
    risposta = client.get(f"/strutture/{dati['spenta']}")
    testo = risposta.get_data(as_text=True)
    assert f"/strutture/{dati['spenta']}/elimina" in testo
    assert 'Elimina' in testo


def test_la_scheda_nasconde_il_pulsante_elimina_in_modalita_single(client, app, dati):
    """single_struttura arriva alla scheda dalla config dell'installazione
    (Task 5): in quella modalita' il piano nasconde il pulsante Elimina
    anche su una struttura disattivata."""
    app.config['APP_CONFIG']['single_struttura'] = True
    entra(client, 'super@x.it')
    risposta = client.get(f"/strutture/{dati['spenta']}")
    testo = risposta.get_data(as_text=True)
    assert f"/strutture/{dati['spenta']}/elimina" not in testo


# ---------------------------------------------------------------------------
# Giro di correzioni 1 (rilievi Minori della revisione)
# ---------------------------------------------------------------------------

def _prepara_struttura_disattivata_con_foto(app, struttura_id, nome_apparecchio_file, contenuto_file):
    """Disattiva la struttura, referenzia un file di foto sotto
    uploads/strutture/<id>/foto/<nome> e lo scrive davvero su disco.
    Restituisce il percorso assoluto del file creato."""
    import os
    from models import execute, query_one
    with app.app_context():
        execute("UPDATE strutture SET attiva=0 WHERE id=?", (struttura_id,))
        apparecchio_id = query_one(
            "SELECT id FROM apparecchi WHERE struttura_id=?", (struttura_id,))['id']
        relativo = f"strutture/{struttura_id}/foto/{nome_apparecchio_file}"
        execute("UPDATE apparecchi SET foto_path=? WHERE id=?", (relativo, apparecchio_id))

    cartella = os.path.join(app.config['UPLOADS_PATH'], 'strutture', str(struttura_id), 'foto')
    os.makedirs(cartella, exist_ok=True)
    percorso = os.path.join(cartella, nome_apparecchio_file)
    with open(percorso, 'wb') as f:
        f.write(contenuto_file)
    return percorso


def test_la_pagina_di_conferma_distingue_i_file_che_verranno_cancellati_dagli_orfani(client, app, dati):
    """Minore: in multi la pagina usava contenuto.file (l'intero
    sottoalbero, orfani compresi) mentre la cancellazione passa dalle righe
    (_percorsi_allegati): un operatore vedeva 'verranno cancellati N file'
    quando in realta' solo alcuni lo sarebbero stati davvero, gli altri
    restando sul disco senza preavviso."""
    import os
    _prepara_struttura_disattivata_con_foto(app, dati['s'], 'referenziato.jpg', b'0' * 100)
    cartella_orfano = os.path.join(app.config['UPLOADS_PATH'], 'strutture', str(dati['s']), 'foto')
    with open(os.path.join(cartella_orfano, 'orfano.jpg'), 'wb') as f:
        f.write(b'0' * 999)

    entra(client, 'super@x.it')
    risposta = client.get(f"/strutture/{dati['s']}/elimina")
    testo = risposta.get_data(as_text=True)
    assert '1 file allegati saranno cancellati' in testo
    assert 'pulisci_uploads.py' in testo
    assert '1 file presenti nella cartella' in testo


def test_la_cancellazione_con_un_file_bloccato_avvisa_l_operatore(client, app, dati):
    """Minore: il file non rimosso finiva solo nel log applicativo e nel
    registro attivita', ma la pagina che l'operatore vede restava un flash
    verde di successo pieno. Su una struttura sanitaria cancellata un
    allegato sopravvissuto va saputo subito, non ritrovato mesi dopo."""
    import os
    percorso = _prepara_struttura_disattivata_con_foto(app, dati['s'], 'bloccato.jpg', b'x')

    entra(client, 'super@x.it')
    # Un handle ancora aperto (anche solo in lettura) impedisce a os.remove
    # di cancellare il file su Windows: e' cosi' che si simula un file
    # bloccato da un altro processo senza doverne avviare uno davvero.
    handle = open(percorso, 'rb')
    try:
        risposta = client.post(f"/strutture/{dati['s']}/elimina",
                               data={'conferma_nome': 'Clinica A'}, follow_redirects=True)
    finally:
        handle.close()
    os.remove(percorso)  # ripulito ora che l'handle e' chiuso

    testo = risposta.get_data(as_text=True)
    assert 'alert-warning' in testo
    assert 'non sono stati' in testo.lower() and 'rimoss' in testo.lower()
    assert 'alert-success' not in testo


def test_la_cancellazione_registra_l_operazione_anche_se_fallisce_dopo_il_commit(client, app, dati, monkeypatch):
    """Minore: se elimina_struttura fallisce DOPO aver gia' cancellato le
    righe (qui: un errore non-OSError durante la rimozione di un file, che
    il codice non cattura perche' cattura solo OSError), il database e'
    gia' stato modificato. Il messaggio 'nulla e' stato cancellato' sarebbe
    falso, e l'operazione - irreversibile - deve comunque finire nel
    registro attivita'.

    Scenario costruito apposta (il revisore ha confermato che non esiste un
    innesco naturale con il codice attuale): si inietta un'eccezione
    non-OSError in os.remove dentro struttura_service, cosa che nessun
    percorso reale del codice provoca oggi."""
    import struttura_service
    from models import query_one
    _prepara_struttura_disattivata_con_foto(app, dati['s'], 'x.jpg', b'x')

    def esplode(*a, **k):
        raise RuntimeError('errore non-OSError iniettato per il test')
    monkeypatch.setattr(struttura_service.os, 'remove', esplode)

    entra(client, 'super@x.it')
    risposta = client.post(f"/strutture/{dati['s']}/elimina",
                           data={'conferma_nome': 'Clinica A'}, follow_redirects=True)
    testo = risposta.get_data(as_text=True)

    assert 'nulla' not in testo.lower()
    assert 'alert-warning' in testo

    with app.app_context():
        assert query_one("SELECT id FROM strutture WHERE id=?", (dati['s'],)) is None
        riga_log = query_one(
            "SELECT * FROM log_attivita WHERE azione='eliminazione' AND entita_id=?",
            (dati['s'],))
        assert riga_log is not None


# ============================================================================
# LOGO DELLA STRUTTURA
# ============================================================================

def test_caricare_un_nuovo_logo_cancella_il_precedente(client, app, dati):
    """Senza questo, ogni sostituzione del logo lascia un orfano su disco -
    esattamente cio' che pulisci_uploads.py esiste per ripulire dopo che e'
    gia' successo. Qui si verifica che non succeda affatto."""
    import io
    import os
    from models import percorso_logo_struttura, query_one

    entra(client, 'super@x.it')

    primo = client.post(f"/strutture/{dati['s']}/logo",
                         data={'logo': (io.BytesIO(PNG + b'primo logo'), 'primo.png')},
                         content_type='multipart/form-data', follow_redirects=True)
    assert primo.status_code == 200

    with app.app_context():
        struttura = query_one("SELECT logo_path FROM strutture WHERE id=?", (dati['s'],))
        percorso_primo = percorso_logo_struttura(struttura)
    assert percorso_primo is not None
    assert os.path.isfile(percorso_primo)

    secondo = client.post(f"/strutture/{dati['s']}/logo",
                          data={'logo': (io.BytesIO(PNG + b'secondo logo'), 'secondo.png')},
                          content_type='multipart/form-data', follow_redirects=True)
    assert secondo.status_code == 200

    with app.app_context():
        struttura = query_one("SELECT logo_path FROM strutture WHERE id=?", (dati['s'],))
        percorso_secondo = percorso_logo_struttura(struttura)
    assert percorso_secondo is not None
    assert percorso_secondo != percorso_primo
    assert os.path.isfile(percorso_secondo)
    # Il file del logo precedente non deve essere rimasto sul disco.
    assert not os.path.exists(percorso_primo)


def test_caricare_il_primo_logo_non_tenta_di_cancellare_nulla(client, app, dati):
    """Non c'e' un logo precedente: il ramo di cancellazione non deve essere
    imboccato (ne' fallire cercando di rimuovere un percorso inesistente)."""
    import io

    entra(client, 'super@x.it')
    risposta = client.post(f"/strutture/{dati['s']}/logo",
                           data={'logo': (io.BytesIO(PNG + b'unico logo'), 'unico.png')},
                           content_type='multipart/form-data', follow_redirects=True)
    assert risposta.status_code == 200
    assert 'Logo aggiornato' in risposta.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Giro finale: Critico 2 (installazione promossa) e Minore (single/rotta)
# ---------------------------------------------------------------------------

def _prepara_struttura_disattivata_con_foto_legacy(app, struttura_id, nome_file, contenuto_file):
    """Come _prepara_struttura_disattivata_con_foto, ma scrive il percorso
    SENZA il prefisso strutture/<id>/ (uploads/foto/<nome>): lo schema che
    upload_subdir usa in modalita' single. Riproduce un'installazione
    promossa a multi con toggle_modalita.py senza travasare gli allegati."""
    import os
    from models import execute, query_one
    with app.app_context():
        execute("UPDATE strutture SET attiva=0 WHERE id=?", (struttura_id,))
        apparecchio_id = query_one(
            "SELECT id FROM apparecchi WHERE struttura_id=?", (struttura_id,))['id']
        execute("UPDATE apparecchi SET foto_path=? WHERE id=?",
                (f"foto/{nome_file}", apparecchio_id))

    cartella = os.path.join(app.config['UPLOADS_PATH'], 'foto')
    os.makedirs(cartella, exist_ok=True)
    percorso = os.path.join(cartella, nome_file)
    with open(percorso, 'wb') as f:
        f.write(contenuto_file)
    return percorso


def test_la_pagina_di_conferma_si_rifiuta_su_un_installazione_non_migrata(client, app, dati):
    """Critico 2: senza questo controllo la pagina mostrava '0 file allegati
    saranno cancellati' pur avendo un allegato vero sul disco, e il messaggio
    finale della cancellazione avrebbe indirizzato a pulisci_uploads.py, che
    lo avrebbe cancellato senza che ne esistesse copia in nessun archivio."""
    _prepara_struttura_disattivata_con_foto_legacy(app, dati['s'], 'vecchia.jpg', b'x' * 10)

    entra(client, 'super@x.it')
    risposta = client.get(f"/strutture/{dati['s']}/elimina", follow_redirects=True)
    testo = risposta.get_data(as_text=True)
    assert 'Cancella definitivamente' not in testo
    assert 'schema multi-struttura' in testo.lower()


def test_l_esportazione_si_rifiuta_su_un_installazione_non_migrata(client, app, dati):
    """Stessa garanzia sulla sola esportazione (senza cancellazione): non
    deve uscire un archivio vuoto spacciato per buono."""
    import os
    _prepara_struttura_disattivata_con_foto_legacy(app, dati['s'], 'vecchia.jpg', b'x' * 10)

    entra(client, 'super@x.it')
    risposta = client.post(f"/strutture/{dati['s']}/esporta", follow_redirects=True)
    testo = risposta.get_data(as_text=True)
    assert 'schema multi-struttura' in testo.lower()
    with app.app_context():
        from flask import current_app
        radice = os.path.join(current_app.config['BACKUPS_PATH'], 'strutture')
        assert not os.path.isdir(radice) or len(os.listdir(radice)) == 0


def test_la_cancellazione_si_rifiuta_su_un_installazione_non_migrata(client, app, dati):
    """La POST che cancella davvero non deve nemmeno tentare: elimina_struttura
    chiama esporta_struttura per prima, che si rifiuta prima di toccare il
    database. La struttura deve restare intatta."""
    from models import query_one
    _prepara_struttura_disattivata_con_foto_legacy(app, dati['s'], 'vecchia.jpg', b'x' * 10)

    entra(client, 'super@x.it')
    risposta = client.post(f"/strutture/{dati['s']}/elimina",
                           data={'conferma_nome': 'Clinica A'}, follow_redirects=True)
    testo = risposta.get_data(as_text=True)
    assert 'schema multi-struttura' in testo.lower()
    with app.app_context():
        assert query_one("SELECT id FROM strutture WHERE id=?", (dati['s'],)) is not None


def test_la_pagina_di_conferma_si_rifiuta_in_modalita_single(client, app, dati):
    """Minore: scheda.html nasconde il pulsante Elimina in modalita' single,
    ma la rotta restava raggiungibile lo stesso (GET diretta). Interfaccia e
    rotta devono concordare."""
    app.config['APP_CONFIG']['single_struttura'] = True
    entra(client, 'super@x.it')
    risposta = client.get(f"/strutture/{dati['spenta']}/elimina", follow_redirects=True)
    testo = risposta.get_data(as_text=True)
    assert 'Cancella definitivamente' not in testo
    assert 'non' in testo.lower() and 'single-struttura' in testo.lower()


def test_la_cancellazione_si_rifiuta_in_modalita_single(client, app, dati):
    """Stessa guardia sulla POST diretta: senza, la rotta rimarrebbe
    raggiungibile aggirando il pulsante nascosto dalla scheda."""
    from models import execute, query_one
    with app.app_context():
        execute("UPDATE strutture SET attiva=0 WHERE id=?", (dati['s'],))
    app.config['APP_CONFIG']['single_struttura'] = True

    entra(client, 'super@x.it')
    risposta = client.post(f"/strutture/{dati['s']}/elimina",
                           data={'conferma_nome': 'Clinica A'}, follow_redirects=True)
    assert 'single-struttura' in risposta.get_data(as_text=True).lower()
    with app.app_context():
        assert query_one("SELECT id FROM strutture WHERE id=?", (dati['s'],)) is not None


def test_il_modulo_di_configurazione_non_ha_piu_i_campi_del_server(client, dati):
    """Il server di posta e' infrastruttura del deployment: si configura una
    volta sola, nella configurazione di sistema. Lasciare i campi qui
    inviterebbe a compilarli, e da questa versione nessuno li leggerebbe."""
    entra(client, 'super@x.it')

    pagina = client.get(f"/strutture/{dati['s']}/config").get_data(as_text=True)

    for campo in ('name="smtp_host"', 'name="smtp_port"', 'name="smtp_user"',
                  'name="smtp_from"', 'name="smtp_password"', 'name="smtp_use_tls"'):
        assert campo not in pagina


def test_si_accendono_gli_avvisi_scegliendo_il_formato(client, app, dati):
    from models import query_one
    struttura_id = dati['s']
    entra(client, 'super@x.it')

    client.post(f"/strutture/{struttura_id}/config",
                data={'avvisi_scadenza_attivi': '1',
                      'avvisi_scadenza_formato': 'pdf',
                      'report_frequenza': 'mensile'},
                follow_redirects=True)

    with app.app_context():
        def valore(chiave):
            riga = query_one("SELECT valore FROM strutture_config "
                             "WHERE struttura_id=? AND chiave=?", (struttura_id, chiave))
            return riga['valore'] if riga else None
        assert valore('avvisi_scadenza_attivi') == '1'
        assert valore('avvisi_scadenza_formato') == 'pdf'
        assert valore('report_frequenza') == 'mensile'


def test_togliere_la_spunta_spegne_davvero_gli_avvisi(client, app, dati):
    """Una casella non spuntata non compare affatto nel POST. Se la rotta si
    limitasse a scrivere i campi presenti, l'interruttore si potrebbe accendere
    e mai piu' spegnere dall'interfaccia."""
    from models import query_one, set_struttura_config
    struttura_id = dati['s']
    with app.app_context():
        set_struttura_config(struttura_id, 'avvisi_scadenza_attivi', '1')
    entra(client, 'super@x.it')

    client.post(f"/strutture/{struttura_id}/config",
                data={'avvisi_scadenza_formato': 'testo',
                      'report_frequenza': 'settimanale'},
                follow_redirects=True)

    with app.app_context():
        assert query_one("SELECT valore FROM strutture_config "
                         "WHERE struttura_id=? AND chiave='avvisi_scadenza_attivi'",
                         (struttura_id,)) is None


def test_il_monitoraggio_casella_si_vede_ma_non_si_attiva(client, app, dati):
    """Compare in pagina perche' e' una funzione annunciata, disabilitata in
    attesa di un'implementazione migliore. Non ha una chiave di configurazione:
    un valore che niente puo' scrivere e' esattamente il difetto trovato con
    report_pdf_attivo, che sembrava configurazione viva e non lo era."""
    from models import query_all
    struttura_id = dati['s']
    entra(client, 'super@x.it')

    import re
    pagina = client.get(f"/strutture/{struttura_id}/config").get_data(as_text=True)
    assert 'monitora' in pagina.lower()
    # Si isola il tag della casella prima di cercarci 'disabled': cercarlo in
    # tutta la pagina non distingue niente, perche' 'disabled' compare gia'
    # altrove nel modulo — con quella versione il test restava verde anche
    # togliendo l'attributo proprio da questa casella.
    tag = re.search(r'<input[^>]*id="monitoraggio_casella"[^>]*>', pagina)
    assert tag is not None
    assert 'disabled' in tag.group(0)
    # E non ha un name: senza, non entra nel POST nemmeno se qualcuno toglie
    # 'disabled' dagli strumenti del browser.
    assert 'name=' not in tag.group(0)

    # E anche forzando un POST a mano non nasce nessuna riga.
    client.post(f"/strutture/{struttura_id}/config",
                data={'monitoraggio_casella_attivo': '1',
                      'report_frequenza': 'settimanale'},
                follow_redirects=True)
    with app.app_context():
        chiavi = [r['chiave'] for r in query_all(
            "SELECT chiave FROM strutture_config WHERE struttura_id=?", (struttura_id,))]
    assert not any('monitoraggio' in c or 'imap' in c for c in chiavi)


def test_il_modulo_non_scrive_piu_chiavi_del_server(client, app, dati):
    """Anche con un POST che le contiene: la rotta deve conoscere solo le
    preferenze, non il server."""
    from models import query_all
    struttura_id = dati['s']
    entra(client, 'super@x.it')

    client.post(f"/strutture/{struttura_id}/config",
                data={'smtp_host': 'smtp.intruso.it', 'smtp_user': 'intruso@x.it',
                      'smtp_password': 'segreta', 'smtp_from': 'intruso@x.it',
                      'report_frequenza': 'settimanale'},
                follow_redirects=True)

    with app.app_context():
        righe = query_all("SELECT chiave, valore FROM strutture_config "
                          "WHERE struttura_id=?", (struttura_id,))
    chiavi = [r['chiave'] for r in righe]
    assert not any(c.startswith('smtp_') for c in chiavi)
    assert 'segreta' not in [r['valore'] for r in righe]
