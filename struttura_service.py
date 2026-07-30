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
    # strutture non ha una colonna struttura_id: E' la struttura, la
    # condizione e' sulla sua stessa chiave primaria.
    ('strutture', 'logo_path', 'id = ?'),
)


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

    # 6. Utenti. Prima si libera ogni riferimento che sopravvive a loro, poi si
    #    conserva la loro identita' nel registro in forma testuale.
    utenti = conn.execute(
        f"SELECT id, email FROM utenti WHERE struttura_id IN ({seg})", ids).fetchall()
    for riga in utenti:
        uid, email = riga[0], riga[1]
        conn.execute(
            "UPDATE log_attivita SET utente_id = NULL, "
            "dettagli = COALESCE(dettagli, '') || ' [utente eliminato: ' || ? || ']' "
            "WHERE utente_id = ?", (email, uid))
        for tabella, colonna in RIFERIMENTI_UTENTE:
            conn.execute(f"UPDATE {tabella} SET {colonna} = NULL WHERE {colonna} = ?", (uid,))
    # I tecnici hanno struttura_id NULL (admin.py li crea cosi'): restano fuori
    # da questa DELETE per costruzione, non per un controllo dimenticabile.
    conn.execute(f"DELETE FROM utenti WHERE struttura_id IN ({seg})", ids)

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
        'utenti': conta("SELECT COUNT(*) FROM utenti WHERE struttura_id = ?"),
        'tecnici': conta("SELECT COUNT(*) FROM tecnici_strutture WHERE struttura_id = ?"),
    }

    numero, byte = 0, 0
    if single_struttura:
        # cartella_struttura restituisce qui l'INTERA cartella uploads: non
        # esiste un sottoalbero per struttura da isolare con un os.walk.
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
                f.write("Per reimportare questo archivio in un'installazione MedInventory:\n\n")
                f.write(f"  python importa_installazione.py \"{destinazione}\"\n")

            return destinazione
        except Exception:
            shutil.rmtree(destinazione, ignore_errors=True)
            raise
    finally:
        sorgente.close()
