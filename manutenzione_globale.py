"""Modo manutenzione: ferma il traffico mentre il database viene sostituito.

Il ripristino di un backup e l'azzeramento del database riscrivono il file
SQLite vivo. Fino alla 2.8.3 lo facevano mentre gli altri thread di Waitress e
lo scheduler continuavano a leggere e scrivere: la rotta chiudeva soltanto la
connessione della *propria* richiesta, e il reset cancellava file, WAL e SHM
sotto i piedi di chi era gia' dentro una transazione.

Qui c'e' un solo meccanismo, senza Flask perche' lo usa anche lo scheduler:

- chi sta lavorando si registra con ``entra()`` e si toglie con ``esci()``
  (una richiesta HTTP, un task dello scheduler);
- ``operazione_esclusiva()`` alza la barriera, aspetta che i lavori in corso
  finiscano (*drain*) e solo allora lascia procedere l'operazione;
- mentre la barriera e' alzata ``entra()`` risponde ``False``: le richieste
  nuove ricevono 503 e lo scheduler rimanda i suoi task.

Il conteggio e' per thread e rientrante, cosi' il thread che tiene
l'esclusiva non aspetta se stesso.
"""

import logging
import threading
import time
from contextlib import contextmanager

logger = logging.getLogger('medinventory.manutenzione')

# Testo mostrato a chi bussa mentre l'operazione e' in corso.
MESSAGGIO = ("Manutenzione in corso sul database. "
             "L'applicazione torna disponibile fra qualche istante.")

# Secondi di attesa massima perche' i lavori in corso finiscano.
ATTESA_DRENAGGIO = 15.0

_condizione = threading.Condition()
_descrizione = None      # motivo dell'operazione esclusiva, None se nessuna
_partecipanti = 0        # lavori registrati e non ancora conclusi
_locale = threading.local()


class ManutenzioneInCorso(RuntimeError):
    """L'esclusiva non e' ottenibile: gia' presa, o traffico non drenato."""


def attiva():
    """Vero mentre un'operazione esclusiva e' in corso."""
    return _descrizione is not None


def descrizione():
    """Motivo dell'operazione in corso, o None."""
    return _descrizione


def partecipanti():
    """Quanti lavori risultano in corso (per i test e la diagnostica)."""
    return _partecipanti


def entra():
    """Registra il chiamante fra i lavori in corso.

    Ritorna False se la barriera e' alzata: il chiamante non deve procedere.
    Chi e' gia' dentro puo' rientrare, altrimenti una chiamata annidata
    resterebbe fuori da un'operazione che ha gia' iniziato a lavorare.
    """
    global _partecipanti
    with _condizione:
        if _descrizione is not None and getattr(_locale, 'dentro', 0) == 0:
            return False
        _partecipanti += 1
        _locale.dentro = getattr(_locale, 'dentro', 0) + 1
        return True


def esci():
    """Toglie il chiamante dai lavori in corso. Innocua se non era dentro."""
    global _partecipanti
    with _condizione:
        if getattr(_locale, 'dentro', 0) == 0:
            return
        _locale.dentro -= 1
        _partecipanti -= 1
        _condizione.notify_all()


@contextmanager
def lavoro():
    """``entra()``/``esci()`` come context manager. ``False`` se barriera alzata."""
    ammesso = entra()
    try:
        yield ammesso
    finally:
        if ammesso:
            esci()


@contextmanager
def operazione_esclusiva(motivo, attesa=ATTESA_DRENAGGIO):
    """Alza la barriera, aspetta il drenaggio, esegue, riapre.

    Solleva ManutenzioneInCorso se un'altra operazione esclusiva e' gia' in
    corso o se il traffico non si e' drenato entro ``attesa`` secondi: in
    quel caso il database non viene toccato affatto, che e' il punto.
    """
    global _descrizione, _partecipanti

    with _condizione:
        if _descrizione is not None:
            raise ManutenzioneInCorso(
                f"Un'altra operazione globale e' in corso: {_descrizione}.")
        _descrizione = motivo
        # Il chiamante e' a sua volta un lavoro registrato (la richiesta HTTP
        # che sta eseguendo l'operazione): esce dal conteggio, altrimenti
        # aspetterebbe se stesso fino al timeout.
        propri = getattr(_locale, 'dentro', 0)
        if propri:
            _partecipanti -= propri
            _locale.dentro = 0

        scadenza = time.monotonic() + attesa
        while _partecipanti > 0:
            rimasto = scadenza - time.monotonic()
            if rimasto <= 0:
                break
            _condizione.wait(rimasto)
        drenato = _partecipanti == 0
        if not drenato:
            rimasti = _partecipanti
            _descrizione = None
            _locale.dentro = propri
            _partecipanti += propri
            _condizione.notify_all()

    if not drenato:
        raise ManutenzioneInCorso(
            f"Ci sono ancora {rimasti} operazioni in corso: riprova fra poco.")

    logger.warning("Modo manutenzione attivo: %s", motivo)
    try:
        yield
    finally:
        with _condizione:
            _descrizione = None
            _locale.dentro = propri
            _partecipanti += propri
            _condizione.notify_all()
        logger.warning("Modo manutenzione concluso: %s", motivo)


def azzera():
    """Riporta il modulo allo stato iniziale. Solo per i test."""
    global _descrizione, _partecipanti
    with _condizione:
        _descrizione = None
        _partecipanti = 0
        _locale.dentro = 0
        _condizione.notify_all()
