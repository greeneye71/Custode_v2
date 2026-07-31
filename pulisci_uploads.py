"""
MedInventory - File orfani sotto uploads/

Elenca i file che nessuna riga del database referenzia. Si accumulano da
import interrotti, sostituzioni di logo e cancellazioni parziali (la
cancellazione di una struttura toglie le righe dal database ma lascia sul
disco i file che risultano fuori dal suo perimetro, vedi
struttura_service._allegato_nel_perimetro).

Uso:
    python pulisci_uploads.py              # elenca soltanto (predefinito)
    python pulisci_uploads.py --elimina    # chiede conferma, poi rimuove

Questo script decide per differenza: cio' che non compare fra i percorsi
referenziati e' considerato orfano. Due conseguenze:

- una colonna che lo schema non ha FERMA lo script invece di essere saltata:
  saltarla dichiarerebbe orfani tutti i file che quella colonna referenzia
  (su manutenzioni.verbale_path, i verbali di manutenzione veri);
- un database vuoto o sbagliato produce zero percorsi referenziati, quindi
  "sono tutti orfani" per QUALUNQUE file sotto uploads/: --elimina si rifiuta
  in quel caso (vedi _verifica_insieme_plausibile).
"""
import argparse
import json
import os
import sqlite3
import sys

from struttura_service import COLONNE_ALLEGATI

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Derivato da struttura_service.COLONNE_ALLEGATI, non riscritto qui: quella
# tupla ha anche la condizione per struttura (non serve a questo script, che
# guarda l'intero database), ma tabella e colonna sono le stesse. Cosi' il
# test di introspezione che tiene COLONNE_ALLEGATI allineato allo schema
# (test_ogni_colonna_di_percorso_dello_schema_e_registrata) protegge anche
# questo elenco, invece di proteggere solo l'originale mentre una copia
# scritta a mano qui diverge in silenzio - e' successo gia' una volta con
# import_history.filepath e strutture.logo_path.
COLONNE_PERCORSO = tuple((tabella, colonna) for tabella, colonna, _dove in COLONNE_ALLEGATI)

DB_PATH_PREDEFINITO = 'data/database.sqlite'
UPLOADS_PATH_PREDEFINITO = 'uploads'


class ColonnaMancante(RuntimeError):
    """Una colonna di COLONNE_PERCORSO non esiste sullo schema del database
    aperto. Ferma lo script: vedi il docstring del modulo sul perche' non va
    ignorata."""


def carica_config(radice):
    """Merge di config.json e config.local.json, il secondo vince - stessa
    regola di app.load_config(). Nessuno dei due e' obbligatorio: senza
    entrambi si usano i percorsi predefiniti."""
    config = {}
    for nome in ('config.json', 'config.local.json'):
        percorso = os.path.join(radice, nome)
        if os.path.exists(percorso):
            with open(percorso, encoding='utf-8') as f:
                config.update(json.load(f))
    return config


def percorsi_referenziati(conn):
    """Insieme dei percorsi che il database cita, normalizzati (separatore
    dell'OS, minuscolo, senza './' iniziale) cosi' da poter essere confrontati
    con cio' che os.walk trova su disco indipendentemente da maiuscole o
    separatore usati quando sono stati scritti.

    Solleva ColonnaMancante, senza catturare nulla, se una tabella o colonna
    di COLONNE_PERCORSO non esiste sullo schema aperto: vedi il docstring del
    modulo."""
    referenziati = set()
    for tabella, colonna in COLONNE_PERCORSO:
        try:
            righe = conn.execute(
                f"SELECT {colonna} FROM {tabella} "
                f"WHERE {colonna} IS NOT NULL AND {colonna} != ''").fetchall()
        except sqlite3.OperationalError as e:
            raise ColonnaMancante(
                f"'{tabella}.{colonna}' non esiste su questo database ({e}). "
                f"Lo schema non e' aggiornato: avvia l'applicazione (applica le "
                f"migrazioni all'avvio) e ripeti. Nessun file e' stato considerato "
                f"orfano.") from e
        for (valore,) in righe:
            if valore:
                referenziati.add(os.path.normpath(valore.replace('/', os.sep)).lower())
    return referenziati


def trova_orfani(uploads, referenziati):
    """(percorsi assoluti ordinati, byte totali) dei file sotto uploads/ il
    cui percorso relativo (stessa normalizzazione di percorsi_referenziati)
    non compare nell'insieme referenziato."""
    orfani, byte_totali = [], 0
    for cartella, _sotto, file_presenti in os.walk(uploads):
        for nome in file_presenti:
            assoluto = os.path.join(cartella, nome)
            relativo = os.path.normpath(os.path.relpath(assoluto, uploads)).lower()
            if relativo not in referenziati:
                orfani.append(assoluto)
                try:
                    byte_totali += os.path.getsize(assoluto)
                except OSError:
                    pass
    orfani.sort()
    return orfani, byte_totali


def elimina_file(percorsi):
    """Rimuove i file indicati. Ritorna (rimossi, falliti); falliti e' una
    lista di (percorso, errore) - i fallimenti non fermano gli altri."""
    rimossi = 0
    falliti = []
    for percorso in percorsi:
        try:
            os.remove(percorso)
            rimossi += 1
        except OSError as e:
            falliti.append((percorso, str(e)))
    return rimossi, falliti


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Elenca (o rimuove) i file sotto uploads/ che nessuna riga referenzia.")
    p.add_argument('--elimina', action='store_true',
                    help='rimuove i file trovati (senza, elenca soltanto)')
    p.add_argument('-y', '--yes', action='store_true',
                    help='non chiedere conferma prima di --elimina')
    p.add_argument('--target', metavar='DIR',
                    help="cartella dell'installazione, se diversa da quella dello script "
                         "(uso nei test)")
    args = p.parse_args(argv)

    radice = os.path.abspath(args.target) if args.target else \
        os.path.dirname(os.path.abspath(__file__))
    config = carica_config(radice)

    db_path = os.path.join(radice, config.get('database_path', DB_PATH_PREDEFINITO))
    # sqlite3.connect crea un file vuoto se il percorso non esiste: da un
    # database vuoto ogni file sotto uploads/ risulterebbe orfano.
    # Verificare PRIMA di aprire e' l'unico modo per evitarlo.
    if not os.path.isfile(db_path):
        print(f"Database non trovato: {db_path}")
        print("Verifica 'database_path' in config.json / config.local.json. "
              "Non e' stato aperto (e non e' stato creato) nulla.")
        return 1

    uploads = os.path.join(radice, config.get('uploads_path', UPLOADS_PATH_PREDEFINITO))

    conn = sqlite3.connect(db_path)
    try:
        try:
            referenziati = percorsi_referenziati(conn)
        except ColonnaMancante as e:
            print(str(e))
            return 1
    finally:
        conn.close()

    orfani, byte_totali = trova_orfani(uploads, referenziati)

    if not orfani:
        print("Nessun file orfano.")
        return 0

    print(f"{len(orfani)} file orfani, {byte_totali / 1048576:.1f} MB:\n")
    for percorso in orfani:
        print(f"  {os.path.relpath(percorso, uploads)}")

    if not args.elimina:
        print("\nNessun file rimosso. Per rimuoverli davvero: --elimina")
        return 0

    # Nessun percorso referenziato ma dei file sotto uploads/ esistono: quasi
    # certamente il database aperto e' vuoto o sbagliato (percorso errato,
    # database appena creato), non un'installazione senza allegati. In quel
    # caso "sono tutti orfani" varrebbe per l'intera cartella: rifiuto, non
    # e' un dato su cui agire.
    if not referenziati:
        print("\nNessun percorso risulta referenziato dal database, ma la cartella "
              "uploads/ contiene file: e' quasi certamente un database vuoto o un "
              "percorso sbagliato, non un'installazione senza allegati. Mi rifiuto "
              "di cancellare - verifica 'database_path' e riprova.")
        return 1

    print(f"\nSta per cancellare {len(orfani)} file, {byte_totali / 1048576:.1f} MB.")
    if not args.yes:
        risposta = input("Procedere? [s/N] ").strip().lower()
        if risposta not in ('s', 'si', 'sì'):
            print("Annullato: nessun file rimosso.")
            return 0

    rimossi, falliti = elimina_file(orfani)
    print(f"\n{rimossi} file rimossi.")
    for percorso, errore in falliti:
        print(f"  non rimosso: {percorso} ({errore})")
    return 0 if not falliti else 1


if __name__ == '__main__':
    sys.exit(main())
