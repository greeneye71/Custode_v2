"""Riscrive requirements.lock.txt dalle versioni installate nell'ambiente.

B03: requirements.txt dichiara gli intervalli, il lock dichiara le versioni
provate. Questo script parte dalle dipendenze dirette di requirements.txt,
segue le loro dipendenze con i metadati dei pacchetti installati e scrive un
pin esatto per ognuna. Non interroga la rete e non installa niente: fotografa
l'ambiente corrente, che e' quello su cui la suite ha appena girato.

    python aggiorna_lock.py            # riscrive il lock
    python aggiorna_lock.py --verifica # esce 1 se il lock e' disallineato

Cosa non fa: risolvere le dipendenze per una piattaforma diversa da questa.
Il prodotto e' un'installazione Windows LAN, il lock nasce li'.
"""
import argparse
import importlib.metadata as md
import os
import sys

try:
    from packaging.requirements import Requirement
except ImportError:  # packaging arriva con pip, ma non diamolo per scontato
    Requirement = None

RADICE = os.path.dirname(os.path.abspath(__file__))
REQUISITI = os.path.join(RADICE, 'requirements.txt')
LOCK = os.path.join(RADICE, 'requirements.lock.txt')

INTESTAZIONE = """# MedInventory - versioni bloccate (B03)
# by Studio Bergamaschi
#
# File generato da aggiorna_lock.py: non modificarlo a mano.
#
# requirements.txt dichiara gli intervalli ammessi; questo file dichiara le
# versioni con cui il programma e' stato effettivamente provato, dipendenze
# indirette comprese. In esercizio si installa da qui:
#
#     pip install -r requirements.lock.txt
#
# Aggiornamento controllato:
#   1. pip install -U -r requirements.txt
#   2. python -m pytest tests/ -q       (deve essere verde)
#   3. python aggiorna_lock.py          (riscrive questo file)
#   4. commit del lock insieme all'esito dei test
#
# Ogni riga e' un pin esatto: nessun '>=' qui dentro, altrimenti il file non
# blocca niente. tests/test_dipendenze.py lo verifica.

"""


def normalizza(nome):
    return nome.lower().replace('_', '-')


def dipendenze_dirette(percorso=REQUISITI):
    """Nomi e extra dichiarati in requirements.txt, commenti esclusi."""
    dirette = []
    with open(percorso, encoding='utf-8') as f:
        for riga in f:
            riga = riga.split('#')[0].strip()
            if not riga:
                continue
            req = Requirement(riga)
            dirette.append((req.name, set(req.extras)))
    return dirette


def chiusura(dirette):
    """Dalle dirette alle indirette, seguendo i metadati installati."""
    versioni = {}
    mancanti = []

    def visita(nome, extras):
        chiave = normalizza(nome)
        if chiave in versioni:
            return
        try:
            dist = md.distribution(nome)
        except md.PackageNotFoundError:
            mancanti.append(chiave)
            return
        versioni[chiave] = dist.version
        for grezza in (dist.requires or []):
            req = Requirement(grezza)
            if req.marker is not None:
                ambienti = [{'extra': e} for e in (set(extras) | {''})]
                if not any(req.marker.evaluate(a) for a in ambienti):
                    continue
            visita(req.name, set(req.extras))

    for nome, extras in dirette:
        visita(nome, extras)
    return versioni, mancanti


def contenuto_lock(versioni):
    righe = [f'{nome}=={versioni[nome]}' for nome in sorted(versioni)]
    return INTESTAZIONE + '\n'.join(righe) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verifica', action='store_true',
                        help='non scrive: esce 1 se il lock e\' disallineato')
    argomenti = parser.parse_args()

    if Requirement is None:
        print('Serve il pacchetto packaging: pip install packaging')
        return 2

    versioni, mancanti = chiusura(dipendenze_dirette())
    if mancanti:
        print('Pacchetti dichiarati ma non installati: '
              + ', '.join(sorted(mancanti)))
        print('Installa l\'ambiente completo prima di rigenerare il lock.')
        return 2

    atteso = contenuto_lock(versioni)
    if argomenti.verifica:
        corrente = ''
        if os.path.exists(LOCK):
            with open(LOCK, encoding='utf-8') as f:
                corrente = f.read()
        if corrente == atteso:
            print(f'Lock allineato: {len(versioni)} pacchetti.')
            return 0
        print('Lock disallineato rispetto all\'ambiente. '
              'Esegui: python aggiorna_lock.py')
        return 1

    with open(LOCK, 'w', encoding='utf-8', newline='\n') as f:
        f.write(atteso)
    print(f'Scritto {LOCK}: {len(versioni)} pacchetti.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
