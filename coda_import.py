"""Tetto ai lavori di analisi AI in background (M07).

Ogni upload di documento faceva partire un thread daemon senza alcun limite:
niente massimo globale, niente quota per struttura. Bastavano pochi upload
ripetuti — o un utente che riprova perche' la pagina sembra ferma — per avere
decine di analisi contemporanee, con altrettante chiamate al provider AI e
altrettanti PDF spezzati in memoria.

Qui sta il contatore che manca. Non e' una coda durevole: i lavori restano
in RAM e un riavvio li perde ancora (l'import resta in stato 'processing' e
va ripetuto). E' il limite di ammissione, cioe' la parte che protegge il
processo, tenuta separata perche' possa essere sostituita da una vera coda
senza toccare le rotte.

Uso:

    if not coda_import.prenota(struttura_id, config):
        # rifiuta la richiesta, niente file salvato
    ...
    coda_import.avvia(struttura_id, funzione, args, nome='import-12')

`avvia()` rilascia lo slot alla fine del lavoro, comunque vada. Se fra la
prenotazione e l'avvio qualcosa fallisce, il chiamante deve chiamare
`rilascia()` da se': lo slot prenotato e mai avviato resterebbe occupato
per sempre.
"""

import logging
import threading

logger = logging.getLogger('medinventory.coda_import')

#: Analisi contemporanee su tutto il deployment.
MAX_GLOBALI = 4
#: Analisi contemporanee di una singola struttura: una struttura sola non
#: deve poter occupare tutti gli slot e lasciare le altre in attesa.
MAX_PER_STRUTTURA = 3

_lock = threading.Lock()
_totale = 0
_per_struttura = {}


def limiti(config=None):
    """I due tetti, con l'eventuale override dalla configurazione globale.

    Sono politica di sistema, non di struttura: si leggono dal config
    globale e mai da strutture_config, altrimenti un admin di struttura
    potrebbe alzarsi da solo la propria quota.
    """
    config = config or {}

    def _intero(chiave, predefinito):
        try:
            valore = int(config.get(chiave, predefinito))
        except (TypeError, ValueError):
            return predefinito
        return valore if valore >= 1 else predefinito

    return (_intero('import_max_analisi', MAX_GLOBALI),
            _intero('import_max_analisi_struttura', MAX_PER_STRUTTURA))


def _chiave(struttura_id):
    # Un import senza struttura (superadmin che non impersona) non deve
    # scavalcare la quota: finisce in un secchio suo, con lo stesso tetto.
    return struttura_id if struttura_id is not None else 0


def prenota(struttura_id, config=None):
    """Riserva uno slot. False se i tetti sono gia' raggiunti.

    Controllo e occupazione avvengono sotto lo stesso lock: due upload
    simultanei non possono passare entrambi sull'ultimo slot libero.
    """
    global _totale
    globali, per_struttura = limiti(config)
    chiave = _chiave(struttura_id)
    with _lock:
        if _totale >= globali:
            logger.warning('Analisi AI rifiutata: %d lavori globali in corso', _totale)
            return False
        if _per_struttura.get(chiave, 0) >= per_struttura:
            logger.warning('Analisi AI rifiutata: struttura %s ha gia\' %d lavori',
                           chiave, _per_struttura.get(chiave, 0))
            return False
        _totale += 1
        _per_struttura[chiave] = _per_struttura.get(chiave, 0) + 1
        return True


def rilascia(struttura_id):
    """Libera uno slot prenotato. Idempotente sotto zero: un doppio rilascio
    non deve mai far scendere i contatori sotto lo zero, o il tetto si
    trasformerebbe col tempo in un limite piu' alto di quello configurato."""
    global _totale
    chiave = _chiave(struttura_id)
    with _lock:
        if _totale > 0:
            _totale -= 1
        rimasti = _per_struttura.get(chiave, 0) - 1
        if rimasti > 0:
            _per_struttura[chiave] = rimasti
        else:
            _per_struttura.pop(chiave, None)


def avvia(struttura_id, target, args=(), nome=None):
    """Fa partire il lavoro gia' prenotato e ne rilascia lo slot alla fine.

    Il rilascio sta in un finally: un'analisi che esplode deve liberare il
    posto come una riuscita, altrimenti dopo qualche errore il deployment
    non accetta piu' import.
    """
    def _esegui():
        try:
            target(*args)
        except Exception:
            # Un thread che muore per un'eccezione non ha nessuno che la
            # raccolga: senza questo log l'analisi sparirebbe in silenzio.
            logger.exception('Analisi di import terminata con errore')
        finally:
            rilascia(struttura_id)

    t = threading.Thread(target=_esegui, daemon=True, name=nome)
    t.start()
    return t


def stato():
    """(totale, {struttura: lavori}). Serve alla diagnostica e ai test."""
    with _lock:
        return _totale, dict(_per_struttura)


def azzera():
    """Solo per i test: riporta i contatori a zero."""
    global _totale
    with _lock:
        _totale = 0
        _per_struttura.clear()
