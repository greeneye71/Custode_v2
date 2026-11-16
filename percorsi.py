"""Percorsi dello stato di MedInventory, separati dal codice.

Due famiglie di percorsi, da non confondere:

- *stato*: database, uploads, backup, log, config.local.json. Vive nella radice,
  che è la cartella dei sorgenti a meno che la variabile MEDINVENTORY_RADICE non
  la sposti altrove (container, installazione con dati su un altro disco).
- *modelli*: file del codice che restano sempre nei sorgenti, come
  config.example.json e config.local.example.json.

La radice non è in cache: viene letta dall'ambiente a ogni chiamata. I test e il
wrapper del container cambiano la variabile a processo già avviato, quindi una
cache memorizzerebbe il valore sbagliato.
"""

import os

VARIABILE = 'MEDINVENTORY_RADICE'


def sorgenti() -> str:
    """Cartella assoluta del codice (quella di questo file)."""
    return os.path.dirname(os.path.abspath(__file__))


def radice() -> str:
    """Radice dello stato: la variabile se impostata e non vuota, altrimenti i sorgenti."""
    valore = os.environ.get(VARIABILE, '')
    if valore:
        return os.path.abspath(valore)
    return sorgenti()


def stato_separato() -> bool:
    """True se lo stato è stato spostato fuori dai sorgenti."""
    return bool(os.environ.get(VARIABILE, ''))


def percorso(*parti: str) -> str:
    """Percorso dentro la radice dello stato."""
    return os.path.join(radice(), *parti)


def modello(nome: str) -> str:
    """Percorso di un file modello, che resta sempre nei sorgenti."""
    return os.path.join(sorgenti(), nome)
