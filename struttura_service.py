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
    #    import_preview va in cascata.
    conn.execute(f"DELETE FROM import_history WHERE struttura_id IN ({seg})", ids)

    # 2. apparecchi: manutenzioni, verifiche, documenti e accessori in cascata.
    conn.execute(f"DELETE FROM apparecchi WHERE struttura_id IN ({seg})", ids)

    # 3. email_config: la FK verso divisioni e' SET NULL, le righe resterebbero
    #    orfane con le credenziali di una struttura che non esiste piu'.
    conn.execute(
        f"DELETE FROM email_config WHERE divisione_id IN "
        f"(SELECT id FROM divisioni WHERE struttura_id IN ({seg}))", ids)

    # 4. Il registro sopravvive, slegato dalla struttura: su un registro di
    #    apparecchi elettromedicali la traccia di chi ha fatto cosa e' proprio
    #    la cosa che non deve sparire insieme ai dati.
    conn.execute(f"UPDATE log_attivita SET struttura_id = NULL WHERE struttura_id IN ({seg})", ids)

    # 5. Utenti. Prima si libera ogni riferimento che sopravvive a loro, poi si
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

    # 6. La struttura: divisioni, strutture_config, api_tokens e
    #    tecnici_strutture vanno in cascata. Il tecnico perde l'assegnazione,
    #    non l'account.
    conn.execute(f"DELETE FROM strutture WHERE id IN ({seg})", ids)

    return conteggi
