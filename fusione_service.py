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
    return _criterio_coppia_normalizzate(
        a, b, normalizza_matricola(a.get('matricola')), normalizza_matricola(b.get('matricola')))


def _criterio_coppia_normalizzate(a, b, ma, mb):
    """Come _criterio_coppia, ma con le matricole gia' normalizzate dal
    chiamante: candidati_duplicati le calcola una sola volta per riga invece
    che due volte per ogni coppia in cui la riga compare."""
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
    distanza fra stringhe. NON e' trascurabile su un parco di qualche
    migliaio di apparecchi: rimane quadratico (matricola_contenuta e
    matricola_distanza_uno lo richiedono, vedi sotto), quindi 4x le righe
    restano circa 4x il tempo, non 16x come nella versione precedente di
    questa funzione, ma la crescita e' comunque quadratica, non lineare.
    Misurato sulla rotta vera dopo questa ottimizzazione: 500 apparecchi
    ~0.3s, 1500 ~1.8s, 3000 ~5.9s (contro gli ~0.3s / ~2.4s / ~9.5s di prima
    - circa 2 volte piu' veloce a parita' di righe, non un cambio di classe
    di complessita'). run_production.py serve con Waitress a 4 thread: su un
    parco grande una sola apertura della pagina puo' ancora bloccarne uno
    per diversi secondi.

    matricola_equivalente (il criterio piu' forte, un confronto ESATTO fra
    matricole normalizzate) e' raggruppato per chiave e costa quindi lineare,
    non quadratico. matricola_contenuta e matricola_distanza_uno restano
    quadratici - richiedono di confrontare matricole DIVERSE fra loro, che
    una chiave esatta non puo' raggruppare - ma le matricole sono
    normalizzate una sola volta per riga invece che due volte per ogni
    coppia, quindi il costo per coppia e' comunque piu' basso.

    Propone, non decide: due macchine gemelle comprate insieme ('MON-1' e
    'MON-2' nella stessa sala) hanno la stessa forma di un errore di
    battitura, e nessun criterio automatico puo' distinguerle. Per questo
    ogni coppia porta il criterio che l'ha proposta.
    """
    normalizzate = [normalizza_matricola(r.get('matricola')) for r in righe]

    gruppi = {}
    for i, m in enumerate(normalizzate):
        if m:
            gruppi.setdefault(m, []).append(i)

    trovate = []
    equivalenti = set()
    for indici in gruppi.values():
        if len(indici) < 2:
            continue
        for x in range(len(indici)):
            for y in range(x + 1, len(indici)):
                i, j = indici[x], indici[y]
                trovate.append(Coppia(righe[i], righe[j], 'matricola_equivalente'))
                equivalenti.add((i, j))

    for i in range(len(righe)):
        for j in range(i + 1, len(righe)):
            if (i, j) in equivalenti:
                continue
            criterio = _criterio_coppia_normalizzate(
                righe[i], righe[j], normalizzate[i], normalizzate[j])
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


# I campi che l'interfaccia puo' far scegliere. Elenco chiuso e non derivato
# dallo schema: i nomi finiscono in una f-string di UPDATE e arrivano da un
# form. struttura_id, divisione_id, id e le colonne di audit non ci sono
# apposta - spostare un apparecchio di struttura e' un'altra funzione, con i
# suoi casi limite e i suoi file da spostare.
CAMPI_FONDIBILI = (
    'descrizione', 'matricola', 'numero_inventario', 'marca', 'modello',
    'anno_fabbricazione', 'classificazione', 'ubicazione', 'stato',
    'connesso_rete', 'ip_address', 'mac_address', 'hostname', 'porta',
    'protocollo', 'url_interfaccia', 'fornitore', 'codice_fornitore',
    'garanzia_scadenza', 'contratto_manutenzione', 'note', 'foto_path',
)

# Le due tabelle di interventi che la pagina di conferma puo' far scartare.
TABELLE_INTERVENTO = {'manutenzione': 'manutenzioni', 'verifica': 'verifiche'}


class FusioneRifiutataError(Exception):
    """La fusione non puo' essere eseguita: le due schede non sono fondibili
    (stessa scheda, struttura diversa, una delle due non esiste)."""


class FusioneCollisioneError(FusioneRifiutataError):
    """I valori scelti farebbero collidere la scheda risultante con un TERZO
    apparecchio, per UNIQUE(struttura_id, modello, matricola). L'attributo
    .altro contiene quell'apparecchio, perche' il messaggio possa nominarlo
    invece di riportare un errore di database."""

    def __init__(self, messaggio, altro):
        super().__init__(messaggio)
        self.altro = altro


def valori_predefiniti(principale, scartato):
    """Per ogni campo in cui le due schede differiscono, il valore da
    preselezionare: quello della principale, TRANNE dove la principale e'
    vuota e la scartata ha un valore.

    Nel caso comune basta confermare, e non si perde il dato che solo la
    scheda scartata aveva - che e' il motivo per cui la fusione esiste.
    """
    predefiniti = {}
    for campo in CAMPI_FONDIBILI:
        a, b = principale.get(campo), scartato.get(campo)
        if a == b:
            continue
        vuoto_a = a is None or a == ''
        predefiniti[campo] = b if vuoto_a else a
    return predefiniti


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

    valori e' un dizionario {campo: valore} limitato a CAMPI_FONDIBILI: per
    ogni campo presente, il valore vince su quello della scheda principale.
    Viene applicato DOPO la cancellazione della scheda scartata, non prima
    (vedi sotto). interventi_scartati e' una lista di coppie
    ('manutenzione'|'verifica', id): quegli interventi vengono cancellati
    invece di confluire sulla scheda principale.
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

    valori = dict(valori or {})
    sconosciuti = [c for c in valori if c not in CAMPI_FONDIBILI]
    if sconosciuti:
        raise FusioneRifiutataError(
            f"Campi non fondibili: {', '.join(sorted(sconosciuti))}.")

    for tipo, _id in interventi_scartati:
        if tipo not in TABELLE_INTERVENTO:
            raise FusioneRifiutataError(f"Tipo di intervento sconosciuto: {tipo}.")

    # Gli interventi da scartare devono appartenere a una delle due schede in
    # fusione: l'elenco arriva da un form, e un id qualunque non deve poter
    # cancellare l'intervento di un apparecchio che non c'entra.
    for tipo, id_intervento in interventi_scartati:
        tabella = TABELLE_INTERVENTO[tipo]
        proprietario = conn.execute(
            f"SELECT apparecchio_id FROM {tabella} WHERE id = ?",
            (id_intervento,)).fetchone()
        if proprietario is None or proprietario[0] not in (id_principale, id_scartato):
            raise FusioneRifiutataError(
                f"L'intervento {tipo} {id_intervento} non appartiene a nessuna "
                f"delle due schede in fusione.")

    # Collisione con un TERZO apparecchio, verificata prima di scrivere
    # qualunque cosa: un rifiuto a meta' lascerebbe le due schede in uno stato
    # peggiore di quello di partenza.
    modello_finale = valori.get('modello', principale['modello'])
    matricola_finale = valori.get('matricola', principale['matricola'])
    altro = conn.execute(
        "SELECT * FROM apparecchi WHERE struttura_id = ? AND modello = ? "
        "AND matricola = ? AND id NOT IN (?, ?)",
        (principale['struttura_id'], modello_finale, matricola_finale,
         id_principale, id_scartato)).fetchone()
    if altro is not None:
        raise FusioneCollisioneError(
            f"Esiste gia' un altro apparecchio con modello \"{modello_finale}\" e "
            f"matricola \"{matricola_finale}\" in questa struttura "
            f"(id {altro['id']}). La fusione non e' stata eseguita.",
            dict(altro))

    esito = {'manutenzioni': 0, 'verifiche': 0, 'documenti': 0, 'accessori': 0,
             'preview': 0, 'interventi_scartati': 0, 'valori_scelti': [],
             'valori_precedenti': {}}

    for tipo, id_intervento in interventi_scartati:
        conn.execute(f"DELETE FROM {TABELLE_INTERVENTO[tipo]} WHERE id = ?",
                     (id_intervento,))
        esito['interventi_scartati'] += 1

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

    # 4. Ora la cancellazione non porta via nulla: non ha piu' figli.
    conn.execute("DELETE FROM apparecchi WHERE id = ?", (id_scartato,))

    # 5. scartato e' una sqlite3.Row gia' letta in memoria in validazione
    # (righe 204-207, prima di qualunque scrittura): dict() non e' sensibile
    # all'ordine e darebbe lo stesso risultato ovunque nella funzione. Sta
    # qui, dopo la DELETE, solo per leggibilita' - raggruppata vicino alla
    # cancellazione a cui si riferisce concettualmente. L'ordine che conta
    # davvero in questa funzione e' quello di UPDATE (sposta i figli) prima
    # di DELETE (cancella la scheda), non questa riga.
    esito['scartato'] = dict(scartato)

    # Dopo la cancellazione, non prima: se la principale prende la matricola
    # della scartata mentre la scartata esiste ancora,
    # UNIQUE(struttura_id, modello, matricola) rifiuta l'UPDATE.
    if valori:
        # Il registro e' l'unica rete rimasta anche per la scheda superstite:
        # questi valori vengono SOVRASCRITTI (non solo i nomi dei campi, che
        # da soli non bastano a ricostruire nulla), quindi vanno catturati
        # ORA, dalla riga letta prima di qualunque scrittura (`principale`,
        # righe 204-205), non dopo l'UPDATE che li rimpiazza. Solo i campi il
        # cui valore cambia davvero: se l'operatore conferma il valore gia'
        # presente sulla principale, non c'e' nulla da ricostruire.
        esito['valori_precedenti'] = {
            campo: principale[campo] for campo, valore in valori.items()
            if principale[campo] != valore
        }
        assegnazioni = ', '.join(f"{c} = ?" for c in valori)
        conn.execute(
            f"UPDATE apparecchi SET {assegnazioni} WHERE id = ?",
            list(valori.values()) + [id_principale])
        esito['valori_scelti'] = list(valori)

    return esito
