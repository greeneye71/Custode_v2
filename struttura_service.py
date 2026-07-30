"""
MedInventory - Ciclo di vita di una struttura

Volutamente estraneo a Flask: riceve connessioni e percorsi dal chiamante,
non li va a cercare in current_app. Cosi' la stessa funzione lavora sul
database vivo dentro una richiesta e su una copia temporanea dentro un test.
"""

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
    """Albero dei file di una struttura, come lo compone models.upload_subdir.

    Le due funzioni devono restare d'accordo: upload_subdir() decide dove un
    upload viene *scritto* (uploads_base/<subdir> in single-struttura,
    uploads_base/strutture/<id>/<subdir> in multi-struttura), questa decide
    dove lo si va a *cercare*. Chi cambia l'una deve controllare l'altra.
    """
    if single_struttura:
        return uploads_base
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

    Lo snapshot si prende con sqlite3.backup(), che e' coerente anche con
    l'applicazione in esercizio. La copia viene poi svuotata di TUTTE LE ALTRE
    strutture con la stessa primitiva che cancella: il predicato e' invertito,
    il codice e' lo stesso.

    single_struttura deve rispecchiare la modalita' dell'installazione
    sorgente: importa_installazione.py, in fase di reimporto, ricostruisce il
    percorso di ogni allegato unendo la propria cartella uploads al percorso
    relativo salvato nel database (documenti.file_path e affini), che in
    single-struttura non ha il prefisso 'strutture/<id>/'. L'archivio deve
    quindi mettere i file esattamente li' dove quel percorso relativo li va a
    cercare, non sotto 'uploads/strutture/<id>/' a prescindere dalla modalita'.
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
        os.makedirs(os.path.join(destinazione, 'data'), exist_ok=True)
        percorso_copia = os.path.join(destinazione, 'data', 'medinventory.db')

        copia = sqlite3.connect(percorso_copia)
        try:
            sorgente.backup(copia)
            copia.execute("PRAGMA foreign_keys = ON")
            rimuovi_strutture(copia, altre)
            copia.commit()
            contenuto = contenuto_struttura(copia, struttura_id, uploads_base,
                                            single_struttura)
            copia.execute("VACUUM")
            copia.commit()
        finally:
            copia.close()
    finally:
        sorgente.close()

    origine_file = cartella_struttura(uploads_base, struttura_id, single_struttura)
    if os.path.isdir(origine_file):
        if single_struttura:
            # cartella_struttura restituisce gia' uploads_base: e' l'intera
            # cartella (non c'e' un sottoalbero per-struttura da isolare), va
            # travasata cosi' com'e' sotto 'uploads/'.
            destinazione_file = os.path.join(destinazione, 'uploads')
        else:
            destinazione_file = os.path.join(
                destinazione, 'uploads', 'strutture', str(struttura_id))
        shutil.copytree(origine_file, destinazione_file)

    with open(os.path.join(destinazione, 'ESPORTAZIONE.txt'), 'w', encoding='utf-8') as f:
        f.write("MedInventory - archivio di una struttura\n\n")
        f.write(f"Struttura:  {nome} (id {struttura_id})\n")
        f.write(f"Esportata:  {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n")
        for chiave in ('apparecchi', 'manutenzioni', 'verifiche', 'documenti',
                       'accessori', 'divisioni', 'utenti', 'file'):
            f.write(f"  {chiave:14} {contenuto[chiave]}\n")
        f.write(f"  {'byte':14} {contenuto['byte']}\n\n")
        f.write("Per reimportare questo archivio in un'installazione MedInventory:\n\n")
        f.write(f"  python importa_installazione.py \"{destinazione}\"\n")

    return destinazione
