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
    'matricola_distanza_uno': 'stesso modello e ubicazione, matricole che '
                              'differiscono per un carattere',
}

# Sotto questa lunghezza il contenimento non si applica: '1' e' contenuto in
# '12', '13', '104' e cosi' via, e su un parco con matricole corte l'elenco
# proporrebbe tutto con tutto.
LUNGHEZZA_MINIMA_CONTENIMENTO = 4


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

    if (len(ma) >= LUNGHEZZA_MINIMA_CONTENIMENTO
            and len(mb) >= LUNGHEZZA_MINIMA_CONTENIMENTO
            and (ma in mb or mb in ma)):
        return 'matricola_contenuta'

    # Il criterio piu' debole, e per questo il piu' vincolato: senza stesso
    # modello e stessa ubicazione proporrebbe ogni coppia di matricole
    # consecutive del parco. Inoltre, su matricole corte diventerebbe
    # indiscriminato (1 e 12, 1 e 13, ecc.), quindi applica il vincolo
    # sulla lunghezza minima.
    if (len(ma) >= LUNGHEZZA_MINIMA_CONTENIMENTO
            and len(mb) >= LUNGHEZZA_MINIMA_CONTENIMENTO
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
