"""Presentazione a terminale: colori, tabelle, prompt.

Non importa nulla del dominio, e nessuna funzione stampa: tutte
restituiscono stringhe. Cosi' i test non devono catturare stdout, e il
menu resta l'unico posto che parla con l'utente.
"""
import sys

COLORI = {
    'verde':     '\033[92m',
    'giallo':    '\033[93m',
    'rosso':     '\033[91m',
    'ciano':     '\033[96m',
    'grassetto': '\033[1m',
}
AZZERA = '\033[0m'

# Marcatori testuali usati quando il colore non e' disponibile. Stessa scelta
# di migrate.py, cosi' i due strumenti si leggono allo stesso modo.
MARCATORI = {'ok': '[OK]', 'avviso': '[!!]', 'errore': '[ERR]'}
COLORE_GRAVITA = {'ok': 'verde', 'avviso': 'giallo', 'errore': 'rosso'}


def supporta_colore():
    return hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()


def colora(testo, colore):
    if not supporta_colore() or colore not in COLORI:
        return testo
    return f'{COLORI[colore]}{testo}{AZZERA}'


def titolo(testo):
    if supporta_colore():
        return colora(testo, 'grassetto')
    return f'=== {testo} ==='


def riga_esito(gravita, testo):
    return f'{MARCATORI[gravita]} {colora(testo, COLORE_GRAVITA[gravita])}'


def campo(etichetta, valore, larghezza=12):
    return f'  {etichetta.ljust(larghezza)} {valore}'


def separatore(larghezza=60):
    return '-' * larghezza


def tabella(intestazioni, righe):
    """Colonne allineate sulla cella piu' larga di ciascuna.

    Le celle arrivano anche come int (una colonna di conteggi): si convertono
    qui, invece di chiedere al chiamante di ricordarselo ogni volta.
    """
    celle = [[str(c) for c in riga] for riga in righe]
    larghezze = [len(str(t)) for t in intestazioni]
    for riga in celle:
        for i, valore in enumerate(riga):
            if i < len(larghezze):
                larghezze[i] = max(larghezze[i], len(valore))

    def formatta(valori):
        return '  ' + '  '.join(
            str(v).ljust(larghezze[i]) for i, v in enumerate(valori))

    linee = [formatta(intestazioni),
             '  ' + '  '.join('-' * l for l in larghezze)]
    linee.extend(formatta(riga) for riga in celle)
    return '\n'.join(linee)
