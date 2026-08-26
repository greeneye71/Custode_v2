"""
MedInventory - Ciclo di vita di una struttura

Volutamente estraneo a Flask: riceve connessioni e percorsi dal chiamante,
non li va a cercare in current_app. Cosi' la stessa funzione lavora sul
database vivo dentro una richiesta e su una copia temporanea dentro un test.
"""

import json
import os
import shutil
import sqlite3
from datetime import datetime


# Colonne che referenziano un utente senza ON DELETE: vanno liberate prima
# di cancellare l'utente, altrimenti la FK rifiuta l'intera operazione.
# Sono tutte nullable. Nomi letterali, mai da input.
RIFERIMENTI_UTENTE = (
    ('apparecchi', 'created_by'),
    ('apparecchi', 'updated_by'),
    ('manutenzioni', 'created_by'),
    ('verifiche', 'created_by'),
    ('accessori', 'created_by'),
    ('documenti', 'uploaded_by'),
    ('import_history', 'imported_by'),
    ('api_tokens', 'created_by'),
)

CHIAVI_CONTEGGIO = ('apparecchi', 'manutenzioni', 'verifiche', 'documenti',
                    'accessori', 'import', 'divisioni', 'utenti', 'strutture')

# Tabelle che importa_installazione.py dichiara esplicitamente di non
# importare (vedi CLAUDE.md): appartengono al deployment sorgente (sessioni
# attive, tentativi di login, token API, configurazione email) o sono dati
# di lavorazione di un import mai concluso (import_preview). Il criterio e'
# lo stesso in entrambe le direzioni: se l'importatore non le legge,
# l'esportatore non deve portarle in un archivio consegnabile.
TABELLE_DEPLOYMENT_SORGENTE = ('sessioni', 'login_attempts', 'api_tokens',
                              'email_config', 'import_preview')

# Colonna che contiene un percorso relativo di un allegato, per tabella, con
# la condizione che la lega alla struttura. Serve sia a contenuto_struttura
# (conteggio file/byte) sia a esporta_struttura (selezione degli allegati in
# modalita' single, dove non esiste un sottoalbero uploads/ per struttura da
# isolare con un copytree).
COLONNE_ALLEGATI = (
    ('apparecchi', 'foto_path', 'struttura_id = ?'),
    ('documenti', 'filepath',
     'apparecchio_id IN (SELECT id FROM apparecchi WHERE struttura_id = ?)'),
    ('manutenzioni', 'verbale_path',
     'apparecchio_id IN (SELECT id FROM apparecchi WHERE struttura_id = ?)'),
    ('verifiche', 'documento_path',
     'apparecchio_id IN (SELECT id FROM apparecchi WHERE struttura_id = ?)'),
    ('import_history', 'filepath', 'struttura_id = ?'),
    ('impianti_documenti', 'filepath',
     'impianto_id IN (SELECT id FROM impianti WHERE struttura_id = ?)'),
    ('impianti_interventi', 'verbale_path',
     'impianto_id IN (SELECT id FROM impianti WHERE struttura_id = ?)'),
    # strutture non ha una colonna struttura_id: E' la struttura, la
    # condizione e' sulla sua stessa chiave primaria.
    ('strutture', 'logo_path', 'id = ?'),
)

# Frammenti che, nel NOME di una chiave di strutture_config, la segnalano come
# un segreto da non mettere in un archivio consegnabile a terzi: chiavi AI
# (strutture_bp.nuova() semina qui, per ogni struttura creata dall'interfaccia,
# le chiavi globali lette da config.local.json: sono dell'operatore del
# deployment, non della struttura, anche se la riga e' formalmente sua) e
# credenziali SMTP. Il criterio e' per schema del nome, non un elenco delle
# chiavi note (anthropic_api_key/gemini_api_key/openai_api_key): una chiave
# futura che contenga uno di questi frammenti (un nuovo provider AI, un nuovo
# servizio con password o token) rientra da sola, senza bisogno che qualcuno
# se ne ricordi qui. smtp_password_encrypted ci rientra anche se, da sola,
# non e' sfruttabile senza encryption_key (che l'archivio non porta): resta
# un segreto per nome, ed e' il criterio - non la sfruttabilita' odierna -
# a dover restare valido quando la cifratura cambiera'.
FRAMMENTI_CONFIG_SENSIBILE = ('api_key', 'password', 'token', 'secret')


class InstallazioneNonMigrataError(Exception):
    """Sollevata da esporta_struttura quando la struttura ha allegati che
    esistono su disco ma fuori dallo schema multi-struttura
    (uploads_base/strutture/<id>/...): sintomo di un'installazione promossa
    da single a multi con toggle_modalita.py, che cambia solo il flag e non
    sposta i file (vedi CHANGELOG). Fermare qui evita di produrre un archivio
    vuoto o incompleto - e di far precedere quell'archivio a una
    cancellazione che si crede coperta da un backup che non lo e'."""


def _percorsi_allegati(conn, struttura_id):
    """Percorsi relativi (colonne *_path) referenziati dalle righe di una
    struttura. L'elenco delle colonne e' in COLONNE_ALLEGATI, non qui: questo
    docstring non ne ripete i nomi apposta, per non poter piu' divergere da
    quella tupla come e' successo con import_history.filepath e
    strutture.logo_path (mancanti nel giro 1, aggiunti nel giro 2).

    Unico punto che sa quali colonne contengono un percorso di allegato:
    chi ne aggiunge una nuova la aggiunge in COLONNE_ALLEGATI, non altrove.
    Una colonna dimenticata qui non manca solo dall'archivio in modalita'
    single: se un domani un'operazione di cancellazione usasse questa
    funzione per sapere quali file sono ancora referenziati prima di
    ripulire il disco, quella colonna dimenticata diventerebbe un file
    orfano cancellato per errore.
    """
    percorsi = set()
    for tabella, colonna, dove in COLONNE_ALLEGATI:
        righe = conn.execute(
            f"SELECT {colonna} FROM {tabella} WHERE {dove} "
            f"AND {colonna} IS NOT NULL AND {colonna} != ''",
            (struttura_id,))
        percorsi.update(r[0] for r in righe)
    return percorsi


def _rimuovi_utenti(conn, ids_utenti, annota_email=True):
    """Libera ogni riferimento (RIFERIMENTI_UTENTE) verso gli utenti indicati,
    slega la loro identita' dal registro attivita' invece di lasciare un
    utente_id orfano, poi cancella le righe.

    Isolata da rimuovi_strutture perche' serve anche a esporta_struttura, su
    un insieme di utenti diverso: rimuovi_strutture la usa per gli utenti di
    UNA struttura (struttura_id = quella cancellata), esporta_struttura la
    usa in piu' per gli utenti senza struttura (superadmin e tecnici), che
    rimuovi_strutture lascia intenzionalmente fuori perche' nella direzione
    cancellazione quegli account non devono sparire (vedi il commento al
    passo 6 qui sotto).

    annota_email decide se scrivere l'email nel testo della voce di registro,
    ed e' l'unica differenza fra le due direzioni. Nella CANCELLAZIONE serve:
    il registro resta nel deployment, e su un registro di apparecchi
    elettromedicali sapere chi ha fatto cosa e' proprio la cosa che non deve
    sparire con l'account. Nell'ESPORTAZIONE no: la potatura di log_attivita
    lascia nell'archivio le voci della struttura esportata, e annotarle qui
    ci scriverebbe dentro le email del superadmin del deployment e dei
    tecnici esterni - rimettendo nell'archivio consegnabile esattamente le
    identita' che toglierle dalla tabella utenti voleva tenerne fuori.
    Riordinare le due operazioni non basterebbe: l'annotazione seleziona per
    utente_id, non per struttura, quindi tocca le voci superstiti in
    qualunque ordine."""
    if not ids_utenti:
        return
    seg = ','.join('?' * len(ids_utenti))
    utenti = conn.execute(
        f"SELECT id, email FROM utenti WHERE id IN ({seg})", ids_utenti).fetchall()
    for riga in utenti:
        uid, email = riga[0], riga[1]
        if annota_email:
            conn.execute(
                "UPDATE log_attivita SET utente_id = NULL, "
                "dettagli = COALESCE(dettagli, '') || ' [utente eliminato: ' || ? || ']' "
                "WHERE utente_id = ?", (email, uid))
        else:
            conn.execute(
                "UPDATE log_attivita SET utente_id = NULL WHERE utente_id = ?", (uid,))
        for tabella, colonna in RIFERIMENTI_UTENTE:
            conn.execute(f"UPDATE {tabella} SET {colonna} = NULL WHERE {colonna} = ?", (uid,))
    conn.execute(f"DELETE FROM utenti WHERE id IN ({seg})", ids_utenti)


def rimuovi_strutture(conn, ids):
    """Cancella dal database tutto cio' che appartiene alle strutture indicate.

    Opera su una sqlite3.Connection qualunque, ed e' il motivo per cui questa
    funzione esiste da sola: cancellare una struttura significa chiamarla sul
    database vivo con [questa], esportarne una significa chiamarla su una copia
    con [tutte le altre]. Un solo codice per le due operazioni, quindi il
    percorso di cancellazione viene esercitato da ogni esportazione invece che
    una volta all'anno.

    Il chiamante apre e chiude la transazione e decide se abilitare le FK.

    Restituisce un dizionario di conteggi (le righe che c'erano prima).
    """
    conteggi = {chiave: 0 for chiave in CHIAVI_CONTEGGIO}
    if not ids:
        return conteggi

    seg = ','.join('?' * len(ids))
    ids = list(ids)

    def conta(sql, params=None):
        # Accesso per indice: il chiamante potrebbe non aver impostato
        # row_factory, quindi il risultato non e' garantito indicizzabile per nome.
        return conn.execute(sql, params if params is not None else ids).fetchone()[0]

    figli = (f"SELECT id FROM apparecchi WHERE struttura_id IN ({seg})")
    # I conteggi vanno presi ora, prima di ogni DELETE: dopo le cancellazioni
    # sarebbero tutti zero e il dizionario restituito sarebbe inutile.
    conteggi['apparecchi'] = conta(f"SELECT COUNT(*) FROM apparecchi WHERE struttura_id IN ({seg})")
    for chiave, tabella in (('manutenzioni', 'manutenzioni'), ('verifiche', 'verifiche'),
                            ('documenti', 'documenti'), ('accessori', 'accessori')):
        conteggi[chiave] = conta(
            f"SELECT COUNT(*) FROM {tabella} WHERE apparecchio_id IN ({figli})")
    conteggi['import'] = conta(f"SELECT COUNT(*) FROM import_history WHERE struttura_id IN ({seg})")
    conteggi['divisioni'] = conta(f"SELECT COUNT(*) FROM divisioni WHERE struttura_id IN ({seg})")
    conteggi['utenti'] = conta(f"SELECT COUNT(*) FROM utenti WHERE struttura_id IN ({seg})")
    conteggi['strutture'] = conta(f"SELECT COUNT(*) FROM strutture WHERE id IN ({seg})")

    # 1. import_history: la FK verso strutture non ha ON DELETE e bloccherebbe.
    #    import_preview va in cascata (import_id -> import_history ON DELETE CASCADE).
    conn.execute(f"DELETE FROM import_history WHERE struttura_id IN ({seg})", ids)

    # 2. import_preview.apparecchio_match_id non ha ON DELETE verso apparecchi,
    #    e puo' puntare a un apparecchio delle strutture in cancellazione anche
    #    da una riga che appartiene all'import_history di UN'ALTRA struttura,
    #    sopravvissuta: e' il caso di import_bp._match_apparecchi quando cerca
    #    un match senza scope di struttura. Quella riga non e' nostra - e' di
    #    un tenant che non c'entra - quindi non la cancelliamo: si azzera solo
    #    il collegamento, a righe cancellate la referenza non avrebbe piu' senso.
    conn.execute(
        f"UPDATE import_preview SET apparecchio_match_id = NULL "
        f"WHERE apparecchio_match_id IN ({figli})", ids)

    # 3. apparecchi: manutenzioni, verifiche, documenti e accessori in cascata.
    conn.execute(f"DELETE FROM apparecchi WHERE struttura_id IN ({seg})", ids)

    # 4. email_config: la FK verso divisioni e' SET NULL, le righe resterebbero
    #    orfane con le credenziali di una struttura che non esiste piu'.
    conn.execute(
        f"DELETE FROM email_config WHERE divisione_id IN "
        f"(SELECT id FROM divisioni WHERE struttura_id IN ({seg}))", ids)

    # 5. Il registro sopravvive, slegato dalla struttura: su un registro di
    #    apparecchi elettromedicali la traccia di chi ha fatto cosa e' proprio
    #    la cosa che non deve sparire insieme ai dati.
    conn.execute(f"UPDATE log_attivita SET struttura_id = NULL WHERE struttura_id IN ({seg})", ids)

    # 6. Utenti. _rimuovi_utenti libera ogni riferimento che sopravvive a
    #    loro e conserva la loro identita' nel registro in forma testuale.
    #    I tecnici hanno struttura_id NULL (admin.py li crea cosi', e cosi'
    #    ha superadmin): restano fuori da questa selezione per costruzione,
    #    non per un controllo dimenticabile - qui l'account non deve sparire.
    ids_utenti = [r[0] for r in conn.execute(
        f"SELECT id FROM utenti WHERE struttura_id IN ({seg})", ids).fetchall()]
    _rimuovi_utenti(conn, ids_utenti)

    # 7. La struttura: divisioni, strutture_config, api_tokens e
    #    tecnici_strutture vanno in cascata. Il tecnico perde l'assegnazione,
    #    non l'account.
    conn.execute(f"DELETE FROM strutture WHERE id IN ({seg})", ids)

    return conteggi


def cartella_struttura(uploads_base, struttura_id, single_struttura=False):
    """Sottoalbero uploads/strutture/<id>/ di UNA struttura, in modalita' multi.

    Le due funzioni devono restare d'accordo: upload_subdir() decide dove un
    upload viene *scritto* (uploads_base/strutture/<id>/<subdir> in
    multi-struttura), questa decide dove lo si va a *cercare*. Chi cambia
    l'una deve controllare l'altra.

    In modalita' single-struttura solleva un'eccezione invece di restituire
    un percorso: in single upload_subdir() scrive sotto uploads_base/<subdir>/
    SENZA alcun prefisso per struttura (vedi models.upload_subdir), quindi
    quella cartella e' condivisa da ogni struttura del database, non isola
    nulla. Fino al giro di correzioni 2 questa funzione restituiva
    uploads_base stessa in quel caso: un valore che sembra "la cartella
    della struttura" ma e' l'intera cartella allegati del deployment, e che
    un chiamante ignaro (una futura cancellazione con shutil.rmtree, per
    esempio) userebbe per cancellare i file di TUTTE le strutture credendo
    di cancellarne una sola. Chi ha bisogno degli allegati di UNA struttura
    in modalita' single deve usare _percorsi_allegati(), che seleziona per
    riferimento (le colonne *_path), non per cartella.
    """
    if single_struttura:
        raise ValueError(
            "cartella_struttura non e' utilizzabile in modalita' single-struttura "
            "(non esiste un sottoalbero da isolare: uploads_base e' condivisa da "
            "tutte le strutture). Usa _percorsi_allegati() per selezionare gli "
            "allegati di una struttura per riferimento.")
    return os.path.join(uploads_base, 'strutture', str(struttura_id))


def contenuto_struttura(conn, struttura_id, uploads_base, single_struttura=False):
    """Cosa contiene una struttura: conteggi, file e spazio occupato.

    Serve alla scheda, alla pagina di conferma della cancellazione e a
    ESPORTAZIONE.txt. I 'tecnici' sono quelli assegnati, che alla
    cancellazione sopravvivono: contarli insieme agli utenti li farebbe
    sembrare in pericolo.

    Limite noto: se un'installazione e' stata promossa da single a multi
    struttura, gli allegati caricati prima della promozione restano nel
    vecchio percorso (uploads_base/<subdir>/) mentre single_struttura qui
    arriva gia' valorizzato a False dal chiamante: il conteggio guardera'
    nel percorso multi-struttura e non li trovera'. toggle_modalita.py cambia
    solo il flag di configurazione, non sposta i file. Non e' un problema che
    questa funzione possa risolvere da sola: e' un travaso di dati, non una
    lettura; chi legge 'file: 0' su una struttura del genere non se ne deve
    fidare senza controllare a mano.
    """
    def conta(sql):
        return conn.execute(sql, (struttura_id,)).fetchone()[0]

    figli = "SELECT id FROM apparecchi WHERE struttura_id = ?"
    contenuto = {
        'apparecchi': conta("SELECT COUNT(*) FROM apparecchi WHERE struttura_id = ?"),
        'manutenzioni': conta(f"SELECT COUNT(*) FROM manutenzioni WHERE apparecchio_id IN ({figli})"),
        'verifiche': conta(f"SELECT COUNT(*) FROM verifiche WHERE apparecchio_id IN ({figli})"),
        'documenti': conta(f"SELECT COUNT(*) FROM documenti WHERE apparecchio_id IN ({figli})"),
        'accessori': conta(f"SELECT COUNT(*) FROM accessori WHERE apparecchio_id IN ({figli})"),
        'import': conta("SELECT COUNT(*) FROM import_history WHERE struttura_id = ?"),
        'divisioni': conta("SELECT COUNT(*) FROM divisioni WHERE struttura_id = ?"),
        'utenti': conta("SELECT COUNT(*) FROM utenti WHERE struttura_id = ? "
                        "AND eliminato_il IS NULL"),
        'tecnici': conta("SELECT COUNT(*) FROM tecnici_strutture WHERE struttura_id = ?"),
    }

    numero, byte = 0, 0
    if single_struttura:
        # In modalita' single non esiste un sottoalbero per struttura da
        # isolare con un os.walk: i file stanno direttamente sotto
        # uploads/<tipo>/. Per questo cartella_struttura, che qui dovrebbe
        # restituire l'intera cartella uploads, in single rifiuta invece di
        # rispondere: un percorso del genere non e' isolabile e chi lo
        # cancellasse porterebbe via gli allegati dell'intero deployment.
        # Se il database contenesse piu' strutture nonostante il flag single
        # (installazione promossa, o due importazioni successive su un
        # target single privo di guardia) un os.walk conterebbe anche i
        # file di un'altra struttura. Si selezionano invece i soli file
        # referenziati dalle righe di QUESTA struttura.
        for relativo in _percorsi_allegati(conn, struttura_id):
            percorso = os.path.join(uploads_base, relativo.replace('/', os.sep))
            try:
                byte += os.path.getsize(percorso)
                numero += 1
            except OSError:
                pass
    else:
        radice = cartella_struttura(uploads_base, struttura_id, single_struttura)
        for cartella, _sotto, file_presenti in os.walk(radice):
            for nome in file_presenti:
                numero += 1
                try:
                    byte += os.path.getsize(os.path.join(cartella, nome))
                except OSError:
                    pass
    contenuto['file'] = numero
    contenuto['byte'] = byte
    return contenuto


def radice_allegati(uploads_base, struttura_id, single_struttura=False):
    """Cartella oltre la quale un percorso di allegato non deve uscire.

    In multi e' il sottoalbero della struttura; in single e' uploads_base,
    perche' li' l'albero e' condiviso e non esiste un confine per struttura.
    """
    if single_struttura:
        return os.path.realpath(uploads_base)
    return os.path.realpath(cartella_struttura(uploads_base, struttura_id, False))


def _allegato_nel_perimetro(uploads_base, radice, relativo):
    """Percorso assoluto dell'allegato se e' un file dentro la radice, o None.

    I valori delle colonne *_path arrivano dal database, non da chi chiama, e
    la cancellazione li usa per decidere cosa togliere dal disco: prima di
    cancellare vanno risolti e verificati, non composti e usati. Un valore con
    una risalita ('strutture/1/foto/../../2/foto/B.jpg') porterebbe la
    cancellazione della struttura 1 a portarsi via un allegato della 2, e la
    riga della 2 resterebbe a puntare a un file che non c'e' piu'. Un valore
    che punta a una cartella invece che a un file la farebbe conteggiare fra
    i file da cancellare senza che venga mai cancellata.

    Nessuno scrittore nel codice puo' produrre questi valori: l'applicazione
    compone i percorsi con secure_filename e importa_installazione.py li
    ricompone dai loro pezzi invece di riusarli. Serve un valore scritto a
    mano nel database. Ma questa e' l'unica operazione irreversibile del
    programma, e verificare costa una realpath.
    """
    assoluto = os.path.realpath(os.path.join(uploads_base, relativo.replace('/', os.sep)))
    if _fuori_dal_perimetro(radice, assoluto):
        return None
    if not os.path.isfile(assoluto):
        return None
    return assoluto


def _fuori_dal_perimetro(radice, assoluto):
    """True se il percorso assoluto (gia' risolto con realpath) non ricade
    dentro radice. Estratta da _allegato_nel_perimetro perche' serve anche a
    _percorsi_legacy_fuori_multi, con una radice diversa (l'intero albero
    uploads_base/strutture/, non il sottoalbero di una singola struttura)."""
    return assoluto != radice and not assoluto.startswith(radice + os.sep)


def _percorsi_legacy_fuori_multi(uploads_base, struttura_id, percorsi):
    """Fra i percorsi passati (quelli di _percorsi_allegati), quelli che
    esistono davvero su disco ma non ricadono ne' nel perimetro della
    struttura ne', piu' in generale, nell'intero schema multi-struttura
    (uploads_base/strutture/...).

    Non e' lo stesso controllo di _allegato_nel_perimetro/_fuori_dal_perimetro
    da solo: un percorso che esce dal perimetro della struttura ma resta
    dentro uploads_base/strutture/ (una risalita verso un'altra struttura, o
    un riferimento incrociato scritto per errore) e' un'anomalia isolata di
    UNA riga - _allegato_nel_perimetro la rifiuta gia' e la cancellazione la
    segnala in 'file_non_rimossi' senza toccarla, comportamento verificato e
    voluto (vedi test_un_percorso_con_risalita_non_porta_via_un_file_di_un_
    altra_struttura). Qui invece si cerca il sintomo opposto: un file che
    esiste, appartiene a questa struttura (una riga la referenzia), ma non e'
    MAI stato scritto secondo lo schema multi-struttura - il segno di
    un'installazione promossa da single a multi con toggle_modalita.py, che
    cambia solo il flag di configurazione e non sposta i file (vedi
    CHANGELOG). Continuare comunque produrrebbe un archivio vuoto o
    incompleto, silenziosamente."""
    radice = os.path.realpath(cartella_struttura(uploads_base, struttura_id, False))
    radice_multi = os.path.realpath(os.path.join(uploads_base, 'strutture'))
    fuori = []
    for relativo in sorted(percorsi):
        grezzo = os.path.join(uploads_base, relativo.replace('/', os.sep))
        if not os.path.exists(grezzo):
            continue
        assoluto = os.path.realpath(grezzo)
        if _fuori_dal_perimetro(radice, assoluto) and _fuori_dal_perimetro(radice_multi, assoluto):
            fuori.append(relativo)
    return fuori


def percorsi_installazione_non_migrata(conn, uploads_base, struttura_id):
    """Percorsi referenziati dalla struttura che _percorsi_legacy_fuori_multi
    giudica un sintomo di installazione promossa da single a multi senza
    travaso dei file.

    Non filtra da se' la modalita': in single restituisce comunque i percorsi
    che non stanno sotto uploads/strutture/, cioe' in pratica tutti. Sono i
    CHIAMANTI a interrogarla solo in modalita' multi, dove la domanda ha senso
    — in single non esiste un perimetro per struttura da cui uscire restando
    dentro uploads_base. Chi la usasse altrove senza quella guardia otterrebbe
    un rifiuto su ogni installazione single sana.

    Uso: esporta_struttura si rifiuta (InstallazioneNonMigrataError) se
    questo elenco non e' vuoto, PRIMA di scrivere qualunque cosa su disco;
    la rotta della scheda/pagina di conferma lo interroga per lo stesso
    motivo, prima di mostrare conteggi che sarebbero silenziosamente
    sbagliati (vedi strutture_bp.conferma_eliminazione)."""
    return _percorsi_legacy_fuori_multi(
        uploads_base, struttura_id, _percorsi_allegati(conn, struttura_id))


def _azzera_config_sensibile(conn, struttura_id):
    """Rimuove dalla copia le chiavi di strutture_config che sembrano un
    segreto per il NOME (vedi FRAMMENTI_CONFIG_SENSIBILE), non un elenco
    delle chiavi note. Restituisce le chiavi rimosse, solo per un eventuale
    log del chiamante: ESPORTAZIONE.txt si limita a dire che la
    configurazione sensibile non e' stata inclusa, senza elencarla."""
    condizioni = ' OR '.join('lower(chiave) LIKE ?' for _ in FRAMMENTI_CONFIG_SENSIBILE)
    parametri = [f'%{frammento}%' for frammento in FRAMMENTI_CONFIG_SENSIBILE]
    rimosse = [r[0] for r in conn.execute(
        f"SELECT chiave FROM strutture_config WHERE struttura_id = ? AND ({condizioni})",
        [struttura_id] + parametri).fetchall()]
    conn.execute(
        f"DELETE FROM strutture_config WHERE struttura_id = ? AND ({condizioni})",
        [struttura_id] + parametri)
    return rimosse


def anteprima_cancellazione_file(conn, struttura_id, uploads_base,
                                 single_struttura=False):
    """Quanti file (e byte) la cancellazione di questa struttura liberera'
    DAVVERO: solo quelli referenziati da una riga (_percorsi_allegati), gli
    stessi che elimina_struttura elenca per cancellarli uno per uno.

    Non e' lo stesso numero di contenuto_struttura['file']: in modalita'
    multi quella conta l'intero sottoalbero uploads/strutture/<id>/ con un
    os.walk (utile per sapere quanto spazio occupa la struttura sul disco,
    orfani compresi), mentre la cancellazione - per non rischiare di
    portare via un file non suo in un database che non rispetta le
    assunzioni della modalita' (vedi _percorsi_allegati) - cancella solo
    cio' che una riga referenzia ancora. La pagina di conferma deve
    mostrare QUESTO numero come "verra' cancellato": usare quello di
    contenuto_struttura in multi promette la cancellazione di file che in
    realta' sopravvivono (gli orfani), e chi legge quel numero prima di
    un'operazione irreversibile fa un controllo di plausibilita' su un
    dato sbagliato.

    In modalita' single i due numeri coincidono sempre: contenuto_struttura
    usa gia' questa stessa selezione in quella modalita', perche' li' non
    esiste un sottoalbero da isolare con un os.walk.

    Conta solo i percorsi che elimina_struttura cancellera' davvero, con lo
    stesso criterio (_allegato_nel_perimetro): un percorso che esce dalla
    cartella della struttura, o che punta a una cartella, non viene cancellato
    e quindi non va promesso.
    """
    radice = radice_allegati(uploads_base, struttura_id, single_struttura)
    numero, byte = 0, 0
    for relativo in _percorsi_allegati(conn, struttura_id):
        assoluto = _allegato_nel_perimetro(uploads_base, radice, relativo)
        if assoluto is None:
            continue
        try:
            byte += os.path.getsize(assoluto)
            numero += 1
        except OSError:
            pass
    return {'file': numero, 'byte': byte}


def _nome_cartella(nome_struttura):
    """Nome parlante e ordinabile, con l'ora: due esportazioni nello stesso
    giorno non si sovrascrivono."""
    pulito = ''.join(c if c.isalnum() else '-' for c in nome_struttura.lower())
    pulito = '-'.join(p for p in pulito.split('-') if p)[:40] or 'struttura'
    return f"{pulito}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def _cartella_esportazione_libera(cartella_archivi, nome_struttura):
    """Nome di cartella non ancora usato dentro cartella_archivi.

    _nome_cartella ha risoluzione al secondo: due esportazioni della stessa
    struttura fatte nello stesso secondo (un database di prova si copia in
    pochi millisecondi) produrrebbero altrimenti lo stesso nome, e la seconda
    sovrascriverebbe silenziosamente la prima invece di finire in un archivio
    proprio. Qui si controlla l'esistenza e si accoda un contatore finche' il
    nome non e' libero.
    """
    base = _nome_cartella(nome_struttura)
    candidato = base
    n = 2
    while os.path.exists(os.path.join(cartella_archivi, candidato)):
        candidato = f"{base}-{n}"
        n += 1
    return candidato


def esporta_struttura(db_path, uploads_base, struttura_id, cartella_archivi,
                       single_struttura=False):
    """Scrive un archivio della struttura e ne restituisce il percorso.

    L'archivio ha la forma di un'installazione MedInventory, quindi si
    reimporta con lo strumento che esiste gia' (importa_installazione.py):
    nessun formato nuovo da mantenere, e nessun lettore nuovo da scrivere.
    Il nome del database ('data/database.sqlite') e il config.json con
    database_path/uploads_path/structure_name sono la forma che
    importa_installazione.Sorgente si aspetta di trovare senza bisogno di
    --db esplicito: senza structure_name, nome_struttura_suggerito() ricade
    sul nome della cartella (timestamp incluso).

    Lo snapshot si prende con sqlite3.backup(), che e' coerente anche con
    l'applicazione in esercizio. La copia viene poi svuotata di TUTTE LE ALTRE
    strutture con la stessa primitiva che cancella: il predicato e' invertito,
    il codice e' lo stesso. Le tabelle che importa_installazione.py dichiara
    di non importare (TABELLE_DEPLOYMENT_SORGENTE) appartengono al deployment
    sorgente, non a una struttura: rimuovi_strutture(altre) non le tocca se
    riguardano la struttura esportata, quindi si svuotano qui esplicitamente.
    Il registro attivita' viene ridotto alle sole righe della struttura
    esportata, per lo stesso motivo.

    single_struttura deve rispecchiare la modalita' dell'installazione
    sorgente: importa_installazione.py, in fase di reimporto, ricostruisce il
    percorso di ogni allegato unendo la propria cartella uploads al percorso
    relativo salvato nel database (documenti.file_path e affini), che in
    single-struttura non ha il prefisso 'strutture/<id>/'. L'archivio deve
    quindi mettere i file esattamente li' dove quel percorso relativo li va a
    cercare, non sotto 'uploads/strutture/<id>/' a prescindere dalla modalita'.
    In single-struttura cartella_struttura restituisce l'INTERA cartella
    uploads (non isola un sottoalbero per struttura): un copytree di quella
    cartella travaserebbe anche gli allegati di altre strutture eventualmente
    presenti nello stesso database (installazione promossa, o due importazioni
    successive su un target single, che non ha guardie contro il caso). Si
    copiano quindi i soli file referenziati dalle righe della struttura.

    In modalita' multi, se la struttura ha allegati che percorsi_installazione_
    non_migrata giudica scritti secondo il vecchio schema single-struttura (il
    caso vero: un'installazione promossa con toggle_modalita.py, che cambia
    solo il flag e non sposta i file), la funzione si RIFIUTA sollevando
    InstallazioneNonMigrataError, prima di scrivere qualunque cosa su disco.
    Senza questo controllo l'archivio uscirebbe privo di quegli allegati (il
    copytree del sottoalbero uploads/strutture/<id>/ non li trova, perche' non
    sono li'): un archivio incompleto consegnato come se fosse buono, e se
    l'esportazione precede una cancellazione (elimina_struttura la chiama per
    prima) quei file diventerebbero orfani cancellabili da pulisci_uploads.py
    senza che ne esista copia da nessuna parte. Un singolo riferimento
    incrociato a un'altra struttura (una risalita, o un percorso scritto per
    errore) non basta a far scattare il rifiuto: resta dentro lo schema
    multi-struttura (uploads/strutture/...), e' un'anomalia isolata di UNA
    riga, ed e' gia' gestita da _allegato_nel_perimetro/file_non_rimossi senza
    bisogno di bloccare l'intera operazione (vedi il docstring di
    _percorsi_legacy_fuori_multi).

    Se l'esportazione fallisce a qualunque passo dopo la creazione della
    cartella di destinazione, quella cartella (che a quel punto puo' contenere
    un backup INTEGRALE di tutti i tenant, prima ancora di essere ripulita)
    viene rimossa: non deve restare sul disco a tempo indeterminato.
    """
    sorgente = sqlite3.connect(db_path)
    try:
        nome = sorgente.execute("SELECT nome FROM strutture WHERE id = ?",
                                (struttura_id,)).fetchone()
        if nome is None:
            raise ValueError(f"Struttura {struttura_id} inesistente")
        nome = nome[0]
        altre = [r[0] for r in sorgente.execute(
            "SELECT id FROM strutture WHERE id != ?", (struttura_id,))]

        if not single_struttura:
            non_migrati = percorsi_installazione_non_migrata(sorgente, uploads_base, struttura_id)
            if non_migrati:
                elenco = ', '.join(non_migrati[:10])
                if len(non_migrati) > 10:
                    elenco += f", ... (+{len(non_migrati) - 10})"
                raise InstallazioneNonMigrataError(
                    f'La struttura "{nome}" ha {len(non_migrati)} allegati che esistono su '
                    "disco ma fuori dallo schema multi-struttura (uploads/strutture/"
                    f"{struttura_id}/...): {elenco}. E' il segno di un'installazione promossa "
                    "da single a multi con toggle_modalita.py, che cambia solo il flag e non "
                    "sposta i file. Esportazione e cancellazione si fermano qui per non "
                    "produrre un archivio incompleto: sposta manualmente questi file sotto "
                    f"uploads/strutture/{struttura_id}/<tipo>/ (facendo corrispondere i "
                    "percorsi salvati nel database), poi riprova. Tornare in modalita' "
                    "single-struttura sblocca la sola esportazione: la cancellazione di "
                    "una struttura in quella modalita' non e' disponibile.")

        destinazione = os.path.join(
            cartella_archivi, _cartella_esportazione_libera(cartella_archivi, nome))
        try:
            os.makedirs(os.path.join(destinazione, 'data'), exist_ok=True)
            percorso_copia = os.path.join(destinazione, 'data', 'database.sqlite')

            copia = sqlite3.connect(percorso_copia)
            try:
                sorgente.backup(copia)
                copia.execute("PRAGMA foreign_keys = ON")
                rimuovi_strutture(copia, altre)
                for tabella in TABELLE_DEPLOYMENT_SORGENTE:
                    copia.execute(f"DELETE FROM {tabella}")
                copia.execute(
                    "DELETE FROM log_attivita WHERE struttura_id IS NULL OR struttura_id != ?",
                    (struttura_id,))
                # rimuovi_strutture(altre) lascia intenzionalmente fuori chi ha
                # struttura_id NULL (superadmin e tecnici): nella direzione
                # cancellazione quell'account non deve sparire (vedi il passo
                # 6 di rimuovi_strutture). Ma questo e' un archivio che si
                # CONSEGNA a terzi: un account che non appartiene alla
                # struttura esportata - hash della password compreso - non ci
                # deve stare, a prescindere da un'eventuale assegnazione in
                # tecnici_strutture. Quell'assegnazione e' un rapporto
                # disponibile, non di proprieta': la cancellazione stessa la
                # tratta cosi' ("il tecnico perde l'assegnazione, non
                # l'account"), quindi non e' un motivo per far uscire
                # l'account dal deployment insieme all'archivio.
                # tecnici_strutture segue in cascata (ON DELETE CASCADE su
                # tecnico_id): non serve azzerarla a parte.
                non_del_deployment = [r[0] for r in copia.execute(
                    "SELECT id FROM utenti WHERE struttura_id IS NULL").fetchall()]
                _rimuovi_utenti(copia, non_del_deployment, annota_email=False)
                # Le chiavi AI globali e le credenziali SMTP non sono della
                # struttura anche se la riga e' formalmente sua (vedi
                # FRAMMENTI_CONFIG_SENSIBILE): un archivio consegnabile non
                # deve farle uscire dal deployment in chiaro.
                _azzera_config_sensibile(copia, struttura_id)
                copia.commit()
                contenuto = contenuto_struttura(copia, struttura_id, uploads_base,
                                                single_struttura)
                percorsi_allegati = _percorsi_allegati(copia, struttura_id)
                copia.execute("VACUUM")
                copia.commit()
            finally:
                copia.close()

            if single_struttura:
                # Nessun sottoalbero per struttura da travasare con
                # copytree: si copiano i soli file che le righe della
                # struttura referenziano davvero.
                for relativo in percorsi_allegati:
                    origine = os.path.join(uploads_base, relativo.replace('/', os.sep))
                    if os.path.isfile(origine):
                        dest = os.path.join(destinazione, 'uploads', relativo.replace('/', os.sep))
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        shutil.copy2(origine, dest)
            else:
                origine_file = cartella_struttura(uploads_base, struttura_id, single_struttura)
                if os.path.isdir(origine_file):
                    destinazione_file = os.path.join(
                        destinazione, 'uploads', 'strutture', str(struttura_id))
                    shutil.copytree(origine_file, destinazione_file)

            with open(os.path.join(destinazione, 'config.json'), 'w', encoding='utf-8') as f:
                json.dump({
                    'database_path': 'data/database.sqlite',
                    'uploads_path': 'uploads',
                    'structure_name': nome,
                }, f, ensure_ascii=False, indent=2)

            with open(os.path.join(destinazione, 'ESPORTAZIONE.txt'), 'w', encoding='utf-8') as f:
                f.write("MedInventory - archivio di una struttura\n\n")
                f.write(f"Struttura:  {nome} (id {struttura_id})\n")
                f.write(f"Esportata:  {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n")
                for chiave in ('apparecchi', 'manutenzioni', 'verifiche', 'documenti',
                               'accessori', 'import', 'divisioni', 'utenti', 'tecnici', 'file'):
                    f.write(f"  {chiave:14} {contenuto[chiave]}\n")
                f.write(f"  {'byte':14} {contenuto['byte']}\n\n")
                f.write(
                    "Non incluso in questo archivio: il superadmin del deployment e i\n"
                    "tecnici (sono account condivisi con altre strutture, non di proprieta'\n"
                    "di questa, anche quando risultano assegnati). Dopo il reimporto vanno\n"
                    "ricreati e riassegnati a mano, se servono. Non inclusa nemmeno la\n"
                    "configurazione sensibile della struttura (chiavi delle API AI, password\n"
                    "SMTP): va reinserita a mano, se serve.\n\n"
                )
                f.write("Per reimportare questo archivio in un'installazione MedInventory:\n\n")
                f.write(f"  python importa_installazione.py \"{destinazione}\"\n")

            return destinazione
        except Exception:
            shutil.rmtree(destinazione, ignore_errors=True)
            raise
    finally:
        sorgente.close()


def _cartella_senza_file(cartella):
    """True se la cartella non contiene alcun file, a qualunque profondita'.

    Cartelle vuote annidate non contano come 'con contenuto': possono
    restare (un rmtree le porta via comunque), ma un solo file dentro basta
    a rifiutare la rimozione dell'albero."""
    for _radice, _sotto, file_presenti in os.walk(cartella):
        if file_presenti:
            return False
    return True


def elimina_struttura(db_path, uploads_base, struttura_id, cartella_archivi,
                      single_struttura=False):
    """Elimina una struttura: archivio, database, file. In quest'ordine.

    L'archivio per primo perche' se fallisce non si deve cancellare nulla:
    esporta_struttura non tocca ne' il database vivo ne' uploads_base, quindi
    un suo fallimento lascia tutto esattamente com'era.

    Il database prima dei file perche' l'errore inverso e' peggiore: righe
    che puntano ad allegati inesistenti sono un guasto silenzioso che si
    scopre mesi dopo aprendo un verbale, mentre file senza riga sono solo
    spazio sprecato - e pulisci_uploads.py (Task 9) e' fatto apposta per
    trovarli.

    I file vengono cancellati elencandoli da _percorsi_allegati, MAI con uno
    shutil.rmtree di un intero sottoalbero: in modalita' single-struttura
    quel sottoalbero non esiste (cartella_struttura solleva ValueError), e in
    modalita' multi conterrebbe anche eventuali file orfani non referenziati
    da nessuna riga, che non e' compito di questa funzione decidere di
    buttare via. Solo in multi, se dopo la cancellazione file per file la
    cartella della struttura resta vuota, viene rimossa per pulizia: non e'
    un rmtree cieco, e' un rmtree su una cartella gia' verificata senza
    contenuto.

    Un file che non si riesce a cancellare (bloccato da un altro processo,
    permessi) non fa fallire l'operazione - a quel punto database e archivio
    sono gia' a posto - ma finisce nella chiave 'file_non_rimossi' del
    risultato, cosi' il chiamante puo' loggarlo invece di perderlo come
    farebbe ignore_errors=True. Ci finisce anche cio' che si e' scelto di non
    toccare perche' fuori perimetro (vedi _allegato_nel_perimetro) ed e'
    ancora sul disco: dal punto di vista di chi legge sono la stessa cosa,
    file che la cancellazione ha lasciato indietro.

    Restituisce i conteggi di rimuovi_strutture piu' 'nome', 'archivio'
    (percorso dell'archivio) e 'file_non_rimossi' (elenco di percorsi
    relativi).
    """
    archivio = esporta_struttura(db_path, uploads_base, struttura_id, cartella_archivi,
                                 single_struttura=single_struttura)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        nome = conn.execute("SELECT nome FROM strutture WHERE id = ?",
                            (struttura_id,)).fetchone()[0]
        # Va preso ORA: dopo rimuovi_strutture le righe che lo popolano non
        # ci sono piu' e la funzione non troverebbe piu' nulla da restituire.
        percorsi = _percorsi_allegati(conn, struttura_id)
        conteggi = rimuovi_strutture(conn, [struttura_id])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    radice = radice_allegati(uploads_base, struttura_id, single_struttura)
    file_non_rimossi = []
    for relativo in percorsi:
        assoluto = _allegato_nel_perimetro(uploads_base, radice, relativo)
        if assoluto is None:
            # Fuori dalla cartella della struttura, oppure non e' un file.
            # Se qualcosa esiste ancora a quel percorso va segnalato: e'
            # rimasto sul disco e non lo abbiamo toccato per scelta. Se non
            # esiste, era gia' assente e non c'e' nulla da dire.
            grezzo = os.path.join(uploads_base, relativo.replace('/', os.sep))
            if os.path.exists(grezzo):
                file_non_rimossi.append(relativo)
            continue
        try:
            os.remove(assoluto)
        except OSError:
            file_non_rimossi.append(relativo)

    if not single_struttura:
        cartella = cartella_struttura(uploads_base, struttura_id, single_struttura)
        if os.path.isdir(cartella) and _cartella_senza_file(cartella):
            shutil.rmtree(cartella, ignore_errors=True)

    conteggi['nome'] = nome
    conteggi['archivio'] = archivio
    conteggi['file_non_rimossi'] = file_non_rimossi
    return conteggi
