"""M07: i thread di analisi AI non avevano alcun tetto.

Ogni upload faceva partire un daemon thread: nessun massimo globale, nessuna
quota per struttura. Pochi documenti — o un utente che riprova perche' la
pagina sembra ferma — bastavano per avere decine di analisi contemporanee,
altrettante chiamate al provider e altrettanti PDF aperti in memoria.

coda_import e' il limite di ammissione. Questi test inchiodano le proprieta'
che contano: il tetto e' rispettato, una struttura non puo' occupare tutti gli
slot, lo slot torna libero comunque vada il lavoro, e il rifiuto avviene prima
di scrivere qualsiasi cosa (nessun file salvato, nessun import 'processing'
che non partira' mai).
"""
import io
import os
import threading

import pytest
from werkzeug.security import generate_password_hash

import coda_import


@pytest.fixture(autouse=True)
def contatori_puliti():
    """I contatori sono di modulo: senza azzerarli un test si porterebbe
    dietro gli slot occupati dal precedente."""
    coda_import.azzera()
    yield
    coda_import.azzera()


# ---------------------------------------------------------------------------
# I tetti
# ---------------------------------------------------------------------------

def test_i_limiti_predefiniti_valgono_senza_configurazione():
    assert coda_import.limiti() == (coda_import.MAX_GLOBALI,
                                    coda_import.MAX_PER_STRUTTURA)


def test_i_limiti_si_leggono_dalla_configurazione_globale():
    globali, per_struttura = coda_import.limiti({
        'import_max_analisi': 2, 'import_max_analisi_struttura': 1})
    assert (globali, per_struttura) == (2, 1)


def test_un_limite_assurdo_non_disattiva_il_tetto():
    """Zero, negativo o non numerico non devono valere 'nessun limite': si
    torna al predefinito, altrimenti un errore di battitura in config
    riaprirebbe il difetto."""
    for valore in (0, -3, 'molti', None):
        globali, _ = coda_import.limiti({'import_max_analisi': valore})
        assert globali == coda_import.MAX_GLOBALI


def test_il_tetto_globale_ferma_le_prenotazioni():
    config = {'import_max_analisi': 2, 'import_max_analisi_struttura': 2}
    assert coda_import.prenota(1, config) is True
    assert coda_import.prenota(2, config) is True
    assert coda_import.prenota(3, config) is False
    assert coda_import.stato()[0] == 2


def test_una_struttura_non_occupa_tutti_gli_slot():
    """La quota per struttura e' il motivo per cui esiste: senza, un tenant
    che carica dieci documenti lascia gli altri fuori."""
    config = {'import_max_analisi': 4, 'import_max_analisi_struttura': 2}
    assert coda_import.prenota(7, config) is True
    assert coda_import.prenota(7, config) is True
    assert coda_import.prenota(7, config) is False
    # un'altra struttura passa lo stesso: il tetto globale non e' raggiunto
    assert coda_import.prenota(8, config) is True


def test_l_import_senza_struttura_ha_un_secchio_proprio():
    """Un superadmin che non impersona non deve scavalcare la quota ne'
    consumare quella di una struttura vera."""
    config = {'import_max_analisi': 4, 'import_max_analisi_struttura': 1}
    assert coda_import.prenota(None, config) is True
    assert coda_import.prenota(None, config) is False
    assert coda_import.prenota(5, config) is True


def test_rilascia_libera_lo_slot():
    config = {'import_max_analisi': 1, 'import_max_analisi_struttura': 1}
    assert coda_import.prenota(1, config) is True
    assert coda_import.prenota(1, config) is False
    coda_import.rilascia(1)
    assert coda_import.stato() == (0, {})
    assert coda_import.prenota(1, config) is True


def test_un_rilascio_di_troppo_non_alza_il_tetto():
    """Se i contatori scendessero sotto zero, ogni doppio rilascio
    regalerebbe uno slot in piu' rispetto a quello configurato."""
    coda_import.rilascia(1)
    coda_import.rilascia(1)
    assert coda_import.stato() == (0, {})
    config = {'import_max_analisi': 1, 'import_max_analisi_struttura': 1}
    assert coda_import.prenota(1, config) is True
    assert coda_import.prenota(1, config) is False


# ---------------------------------------------------------------------------
# avvia(): lo slot torna libero comunque vada
# ---------------------------------------------------------------------------

def test_avvia_rilascia_lo_slot_a_lavoro_finito():
    fatto = threading.Event()
    assert coda_import.prenota(3) is True
    coda_import.avvia(3, fatto.set).join(timeout=5)
    assert fatto.is_set()
    assert coda_import.stato() == (0, {})


def test_avvia_rilascia_lo_slot_anche_se_il_lavoro_esplode():
    """Un'analisi che fallisce deve liberare il posto come una riuscita:
    altrimenti dopo qualche errore il deployment non accetta piu' import."""
    def esplode():
        raise RuntimeError('analisi fallita')

    assert coda_import.prenota(3) is True
    coda_import.avvia(3, esplode).join(timeout=5)
    assert coda_import.stato() == (0, {})


def test_avvia_passa_gli_argomenti_al_lavoro():
    ricevuti = []
    assert coda_import.prenota(3) is True
    coda_import.avvia(3, lambda *a: ricevuti.append(a), args=(1, 'due')).join(timeout=5)
    assert ricevuti == [(1, 'due')]


# ---------------------------------------------------------------------------
# La rotta: il rifiuto arriva prima di scrivere
# ---------------------------------------------------------------------------

@pytest.fixture
def struttura_con_admin(app):
    from models import execute
    with app.app_context():
        sid = execute("INSERT INTO strutture (nome,codice,attiva)"
                      " VALUES ('Clinica C','ICC',1)").lastrowid
        did = execute("INSERT INTO divisioni (nome,codice,struttura_id)"
                      " VALUES ('Div C','DVC',?)", (sid,)).lastrowid
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,"
                "struttura_id,primo_accesso) VALUES ('adminc@i.it',?,'A','C',"
                "'admin',?,0)", (generate_password_hash('Passw0rd!'), sid))
    app.config['APP_CONFIG']['ai_provider'] = 'anthropic'
    app.config['APP_CONFIG']['anthropic_api_key'] = 'chiave-finta-di-test'
    return {'struttura': sid, 'divisione': did}


def _file_caricati(app):
    radice = app.config['UPLOADS_PATH']
    return [f for _, _, files in os.walk(radice) for f in files]


def test_la_rotta_rifiuta_quando_gli_slot_sono_esauriti(client, app,
                                                        struttura_con_admin):
    """Il rifiuto non deve lasciare tracce: un file salvato e una riga
    'processing' che nessun thread portera' avanti sarebbero peggio del
    rifiuto stesso."""
    from models import query_one
    app.config['APP_CONFIG']['import_max_analisi'] = 1
    assert coda_import.prenota(999, app.config['APP_CONFIG']) is True

    client.post('/login', data={'email': 'adminc@i.it', 'password': 'Passw0rd!'})
    risposta = client.post('/import/analizza', data={
        'file': (io.BytesIO(b'colonna1,colonna2\nabc,def\n'), 'test.csv'),
        'divisione_id': str(struttura_con_admin['divisione']),
    }, content_type='multipart/form-data', follow_redirects=True)

    assert 'troppe analisi AI in corso' in risposta.data.decode('utf-8',
                                                                errors='replace')
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM import_history")['n'] == 0
    assert _file_caricati(app) == []


def test_uno_slot_rifiutato_non_resta_occupato(client, app, struttura_con_admin):
    """Il rifiuto non prenota niente: il contatore deve restare quello di
    prima, o bastarebbero pochi tentativi respinti per chiudere gli import a
    tutti."""
    app.config['APP_CONFIG']['import_max_analisi'] = 1
    coda_import.prenota(999, app.config['APP_CONFIG'])
    prima = coda_import.stato()

    client.post('/login', data={'email': 'adminc@i.it', 'password': 'Passw0rd!'})
    client.post('/import/analizza', data={
        'file': (io.BytesIO(b'colonna1,colonna2\nabc,def\n'), 'test.csv'),
        'divisione_id': str(struttura_con_admin['divisione']),
    }, content_type='multipart/form-data', follow_redirects=True)

    assert coda_import.stato() == prima


def test_con_uno_slot_libero_la_rotta_accetta(client, app, struttura_con_admin,
                                              monkeypatch):
    """Controllo positivo: senza di esso i due test qui sopra passerebbero
    anche se la rotta rifiutasse sempre, per un motivo qualsiasi."""
    import import_bp
    from models import query_one
    monkeypatch.setattr(import_bp, '_run_import_async', lambda *a, **k: None)

    client.post('/login', data={'email': 'adminc@i.it', 'password': 'Passw0rd!'})
    client.post('/import/analizza', data={
        'file': (io.BytesIO(b'colonna1,colonna2\nabc,def\n'), 'test.csv'),
        'divisione_id': str(struttura_con_admin['divisione']),
    }, content_type='multipart/form-data', follow_redirects=True)

    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM import_history")['n'] == 1
    assert _file_caricati(app) != []
