"""
MedInventory - Fusione di apparecchi duplicati

Volutamente estraneo a Flask, come struttura_service.py: riceve connessioni e
valori dal chiamante. candidati_duplicati e' per giunta una funzione pura su
una lista di dizionari, quindi si prova con dieci righe in memoria invece che
con un database popolato.
"""

from collections import namedtuple


Coppia = namedtuple('Coppia', 'a b criterio')

# Dal piu' forte al piu' debole: l'ordine e' significativo, _criterio_coppia
# restituisce il primo che si applica.
CRITERI = {
    'matricola_equivalente': 'matricola identica a meno di trattini e maiuscole',
    'matricola_contenuta': 'una matricola e\' contenuta nell\'altra',
    'matricola_distanza_uno': 'stesso modello e ubicazione, matricole di '
                              'lunghezza sufficiente che differiscono per un carattere',
}

# Sotto questa lunghezza il confronto fra matricole non distingue piu' un
# duplicato da due macchine numerate di seguito: '1'/'12' e 'A-1'/'A-2'
# potrebbero essere due registrazioni dello stesso apparecchio o due macchine
# gemelle. Su un parco con matricole corte, qualunque criterio porterebbe a
# proposte indiscriminate e un elenco che nessuno usa. La costante governa sia
# il criterio di contenimento che quello di distanza uno.
LUNGHEZZA_MINIMA_MATRICOLA = 4


def normalizza_matricola(valore):
    """Solo lettere e cifre, in maiuscolo. 'R-00015' e 'r 00015' diventano
    la stessa cosa, che e' il modo in cui lo stesso apparecchio viene
    trascritto due volte da due documenti diversi."""
    if not valore:
        return ''
    return ''.join(c for c in str(valore) if c.isalnum()).upper()


def _differisce_di_un_carattere(a, b):
    """True se una sola sostituzione, inserimento o cancellazione trasforma a
    in b. Non serve una distanza di Levenshtein completa: interessa solo il
    caso 1, e fermarsi li' rende la funzione leggibile e veloce."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        differenze = sum(1 for x, y in zip(a, b) if x != y)
        return differenze == 1
    lunga, corta = (a, b) if len(a) > len(b) else (b, a)
    for i in range(len(lunga)):
        if lunga[:i] + lunga[i + 1:] == corta:
            return True
    return False


def _criterio_coppia(a, b):
    """Il criterio piu' forte che si applica a due righe, o None."""
    ma = normalizza_matricola(a.get('matricola'))
    mb = normalizza_matricola(b.get('matricola'))
    if not ma or not mb:
        return None

    if ma == mb:
        return 'matricola_equivalente'

    if (len(ma) >= LUNGHEZZA_MINIMA_MATRICOLA
            and len(mb) >= LUNGHEZZA_MINIMA_MATRICOLA
            and (ma in mb or mb in ma)):
        return 'matricola_contenuta'

    # Il criterio piu' debole, e per questo il piu' vincolato: senza stesso
    # modello e stessa ubicazione proporrebbe ogni coppia di matricole
    # consecutive del parco. Inoltre, su matricole corte diventerebbe
    # indiscriminato (1 e 12, 1 e 13, ecc.), quindi applica il vincolo
    # sulla lunghezza minima.
    if (len(ma) >= LUNGHEZZA_MINIMA_MATRICOLA
            and len(mb) >= LUNGHEZZA_MINIMA_MATRICOLA
            and a.get('modello') == b.get('modello')
            and a.get('ubicazione') == b.get('ubicazione')
            and _differisce_di_un_carattere(ma, mb)):
        return 'matricola_distanza_uno'

    return None


def candidati_duplicati(righe):
    """Coppie di righe che potrebbero descrivere lo stesso apparecchio.

    Funzione pura: `righe` e' una lista di dizionari con almeno id, matricola,
    marca, modello e ubicazione. Chi chiama decide cosa passarle - la rotta
    esclude gli apparecchi dismessi.

    Il confronto gira in Python e non in SQL perche' SQLite non ha una
    distanza fra stringhe; su qualche migliaio di apparecchi il costo e'
    trascurabile.

    Propone, non decide: due macchine gemelle comprate insieme ('MON-1' e
    'MON-2' nella stessa sala) hanno la stessa forma di un errore di
    battitura, e nessun criterio automatico puo' distinguerle. Per questo
    ogni coppia porta il criterio che l'ha proposta.
    """
    trovate = []
    for i in range(len(righe)):
        for j in range(i + 1, len(righe)):
            criterio = _criterio_coppia(righe[i], righe[j])
            if criterio:
                trovate.append(Coppia(righe[i], righe[j], criterio))
    return trovate


# Tabelle figlie di apparecchi con ON DELETE CASCADE: se non si spostano
# PRIMA di cancellare la scheda scartata, la cascata le cancella insieme a lei.
TABELLE_FIGLIE = (
    ('manutenzioni', 'apparecchio_id'),
    ('verifiche', 'apparecchio_id'),
    ('documenti', 'apparecchio_id'),
    ('accessori', 'apparecchio_id'),
)


class FusioneRifiutataError(Exception):
    """La fusione non puo' essere eseguita: le due schede non sono fondibili
    (stessa scheda, struttura diversa, una delle due non esiste)."""


def fondi_apparecchi(conn, id_principale, id_scartato, valori=None,
                     interventi_scartati=()):
    """Fonde la scheda scartata dentro la principale, che conserva il proprio id.

    Il chiamante apre e chiude la transazione, come per
    struttura_service.rimuovi_strutture: la rotta la vuole tutta in una, e un
    test la vuole poter annullare.

    L'ORDINE delle operazioni non e' una preferenza di stile.
    manutenzioni, verifiche, documenti e accessori hanno ON DELETE CASCADE
    verso apparecchi: se si cancella la scheda scartata prima di spostarli, la
    cascata porta via proprio i dati che la fusione doveva salvare, e
    l'operazione riesce senza errori. E import_preview.apparecchio_match_id non
    ha ON DELETE affatto, quindi bloccherebbe la cancellazione.

    Restituisce i conteggi di cio' che ha spostato, la scheda scartata per
    intero (il registro la conserva campo per campo: la fusione e' definitiva)
    e l'elenco dei valori scelti dalla scartata.

    valori e interventi_scartati sono accettati ma non ancora usati: li
    implementera' un task successivo.
    """
    if id_principale == id_scartato:
        raise FusioneRifiutataError(
            "La scheda principale e quella da fondere sono la stessa.")

    principale = conn.execute(
        "SELECT * FROM apparecchi WHERE id = ?", (id_principale,)).fetchone()
    scartato = conn.execute(
        "SELECT * FROM apparecchi WHERE id = ?", (id_scartato,)).fetchone()
    if principale is None or scartato is None:
        raise FusioneRifiutataError("Una delle due schede non esiste.")
    if principale['struttura_id'] != scartato['struttura_id']:
        raise FusioneRifiutataError(
            "Le due schede appartengono a strutture diverse.")

    esito = {'manutenzioni': 0, 'verifiche': 0, 'documenti': 0, 'accessori': 0,
             'preview': 0, 'interventi_scartati': 0, 'valori_scelti': []}

    # 1-2. I figli si spostano PRIMA di qualunque cancellazione.
    for tabella, colonna in TABELLE_FIGLIE:
        cur = conn.execute(
            f"UPDATE {tabella} SET {colonna} = ? WHERE {colonna} = ?",
            (id_principale, id_scartato))
        esito[tabella] = cur.rowcount

    # 3. import_preview: nessun ON DELETE, bloccherebbe la cancellazione.
    cur = conn.execute(
        "UPDATE import_preview SET apparecchio_match_id = ? "
        "WHERE apparecchio_match_id = ?", (id_principale, id_scartato))
    esito['preview'] = cur.rowcount

    # 4. La scheda scartata va letta finche' esiste.
    esito['scartato'] = dict(scartato)

    # 5. Ora la cancellazione non porta via nulla: non ha piu' figli.
    conn.execute("DELETE FROM apparecchi WHERE id = ?", (id_scartato,))

    return esito
