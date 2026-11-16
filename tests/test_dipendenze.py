"""B03 - dipendenze non riproducibili.

requirements.txt usava solo limiti inferiori e non esisteva un lock: una
installazione futura poteva risolvere versioni mai provate. Qui si verificano
le tre proprieta' che rendono l'installazione ripetibile: ogni dipendenza
diretta ha un minimo e un massimo, il lock e' fatto solo di pin esatti, e il
lock copre tutte le dirette con una versione compresa nell'intervallo.
"""
import os

import pytest

from packaging.requirements import Requirement
from packaging.version import Version

import aggiorna_lock

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUISITI = os.path.join(RADICE, 'requirements.txt')
LOCK = os.path.join(RADICE, 'requirements.lock.txt')


def righe_utili(percorso):
    with open(percorso, encoding='utf-8') as f:
        for riga in f:
            riga = riga.split('#')[0].strip()
            if riga:
                yield riga


@pytest.fixture(scope='module')
def dirette():
    return [Requirement(r) for r in righe_utili(REQUISITI)]


@pytest.fixture(scope='module')
def bloccate():
    pin = {}
    for riga in righe_utili(LOCK):
        req = Requirement(riga)
        pin[aggiorna_lock.normalizza(req.name)] = req
    return pin


def test_ogni_dipendenza_diretta_ha_minimo_e_massimo(dirette):
    assert dirette, 'requirements.txt vuoto: il parser non sta leggendo niente'
    for req in dirette:
        operatori = {s.operator for s in req.specifier}
        assert {'>=', '>'} & operatori, f'{req.name} senza versione minima'
        assert {'<', '<=', '==', '~='} & operatori, \
            f'{req.name} senza limite superiore: puo\' tirarsi dentro una major'


def test_il_lock_contiene_solo_pin_esatti(bloccate):
    assert bloccate, 'requirements.lock.txt vuoto'
    for nome, req in bloccate.items():
        operatori = {s.operator for s in req.specifier}
        assert operatori == {'=='}, f'{nome} non e\' bloccato a una versione'


def test_il_lock_copre_tutte_le_dipendenze_diritte(dirette, bloccate):
    for req in dirette:
        assert aggiorna_lock.normalizza(req.name) in bloccate, \
            f'{req.name} dichiarata ma assente dal lock'


def test_la_versione_bloccata_rispetta_l_intervallo(dirette, bloccate):
    for req in dirette:
        pin = bloccate[aggiorna_lock.normalizza(req.name)]
        versione = str(next(iter(pin.specifier)).version)
        assert req.specifier.contains(Version(versione), prereleases=True), \
            (f'{req.name}: il lock blocca {versione}, fuori da '
             f'{req.specifier}')


def test_il_lock_include_anche_le_indirette(bloccate):
    """Un lock delle sole dirette non blocca niente: werkzeug e jinja2
    arrivano da flask e sono quelli che cambiano comportamento sotto i piedi."""
    for indiretta in ('werkzeug', 'jinja2', 'markupsafe', 'itsdangerous'):
        assert indiretta in bloccate


def test_il_lock_e_allineato_all_ambiente():
    """Se qualcuno aggiorna un pacchetto e dimentica di rigenerare il lock,
    il file smette di descrivere cio' su cui i test sono verdi."""
    versioni, mancanti = aggiorna_lock.chiusura(
        aggiorna_lock.dipendenze_dirette())
    if mancanti:
        pytest.skip('ambiente incompleto: ' + ', '.join(sorted(mancanti)))
    with open(LOCK, encoding='utf-8') as f:
        corrente = f.read()
    assert corrente == aggiorna_lock.contenuto_lock(versioni), \
        'lock disallineato: esegui python aggiorna_lock.py'
