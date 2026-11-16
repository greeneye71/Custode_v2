"""A03: la matricola non e' una chiave.

Lo schema impone UNIQUE(struttura_id, modello, matricola): due apparecchi di
modello diverso possono portare la stessa matricola, e nel parco installato
succede (produttori diversi, numerazioni che ripartono). Fino alla 2.8.0 ogni
matcher faceva fetchone() sulla sola matricola: con due omonimi la scelta era
arbitraria e dipendeva dall'ordine di inserimento, quindi un verbale poteva
essere scritto sull'apparecchio sbagliato.

Ogni test su una matricola condivisa gira nei due ordini di inserimento: e'
l'ordine a rivelare la scelta arbitraria, un ordine solo passerebbe per meta'
dei bug.
"""
import sqlite3

import pytest


ORDINI = [('DRAGER', 'Evita 4', 'MINDRAY', 'SV300'),
          ('MINDRAY', 'SV300', 'DRAGER', 'Evita 4')]


@pytest.fixture
def omonimi(app):
    """Due apparecchi con la stessa matricola, modelli diversi, stesso reparto."""
    from models import execute
    with app.app_context():
        s = execute("INSERT INTO strutture (nome,codice,attiva) "
                    "VALUES ('Clinica A','CA',1)").lastrowid
        d = execute("INSERT INTO divisioni (nome,codice,struttura_id,attiva) "
                    "VALUES ('Rianimazione','RIA',?,1)", (s,)).lastrowid

    def crea(marca, modello):
        return execute(
            "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,"
            "modello,stato) VALUES (?,?,'SN-7788',?,?,'funzionante')",
            (d, s, marca, modello)).lastrowid

    return {'app': app, 'struttura': s, 'divisione': d, 'crea': crea}


def _coppia(omonimi, ordine):
    """Inserisce i due omonimi nell'ordine dato, restituisce {modello: id}."""
    marca1, mod1, marca2, mod2 = ordine
    with omonimi['app'].app_context():
        primo = omonimi['crea'](marca1, mod1)
        secondo = omonimi['crea'](marca2, mod2)
    return {mod1: primo, mod2: secondo}


# ---------------------------------------------------------------------------
# models.scegli_apparecchio() — la decisione, isolata
# ---------------------------------------------------------------------------

def _riga(id_, marca, modello):
    return {'id': id_, 'marca': marca, 'modello': modello}


def test_nessun_candidato():
    from models import scegli_apparecchio
    assert scegli_apparecchio([]) == (None, 'nessuno')
    assert scegli_apparecchio(None) == (None, 'nessuno')


def test_candidato_unico_passa_anche_senza_modello():
    """Il caso normale: una matricola, un apparecchio. Non deve essere stato
    reso piu' severo insieme al resto."""
    from models import scegli_apparecchio
    riga, motivo = scegli_apparecchio([_riga(1, 'DRAGER', 'Evita 4')])
    assert (riga['id'], motivo) == (1, 'matricola')


def test_due_omonimi_senza_modello_non_si_sceglie():
    from models import scegli_apparecchio
    candidati = [_riga(1, 'DRAGER', 'Evita 4'), _riga(2, 'MINDRAY', 'SV300')]
    assert scegli_apparecchio(candidati) == (None, 'ambiguo')
    assert scegli_apparecchio(candidati, modello='  ') == (None, 'ambiguo')


def test_il_modello_scioglie_l_ambiguita_in_entrambi_gli_ordini():
    from models import scegli_apparecchio
    a, b = _riga(1, 'DRAGER', 'Evita 4'), _riga(2, 'MINDRAY', 'SV300')
    for candidati in ([a, b], [b, a]):
        riga, motivo = scegli_apparecchio(candidati, modello='sv300')
        assert (riga['id'], motivo) == (2, 'matricola+modello')


def test_un_modello_che_non_combacia_non_sceglie_a_caso():
    """Il documento parla di un terzo apparecchio: nessuno dei due e' quello."""
    from models import scegli_apparecchio
    candidati = [_riga(1, 'DRAGER', 'Evita 4'), _riga(2, 'MINDRAY', 'SV300')]
    assert scegli_apparecchio(candidati, modello='Servo-i') == (None, 'ambiguo')


def test_la_marca_distingue_due_apparecchi_di_modello_omonimo():
    from models import scegli_apparecchio
    candidati = [_riga(1, 'DRAGER', 'Monitor'), _riga(2, 'MINDRAY', 'Monitor')]
    riga, motivo = scegli_apparecchio(candidati, modello='monitor', marca='mindray')
    assert (riga['id'], motivo) == (2, 'matricola+modello')


# ---------------------------------------------------------------------------
# import_bp._match_apparecchi() — preview di verbali e verifiche
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('ordine', ORDINI)
def test_match_preview_lascia_ambigua_la_riga_senza_modello(omonimi, ordine):
    from import_bp import _match_apparecchi
    ids = _coppia(omonimi, ordine)
    items = [{'matricola': 'SN-7788'}]
    with omonimi['app'].app_context():
        _match_apparecchi(items, struttura_id=omonimi['struttura'])
    assert items[0]['_match_id'] is None
    assert items[0]['_match_confidenza'] == 0.0
    assert sorted(c['id'] for c in items[0]['_match_ambiguo']) == sorted(ids.values())


@pytest.mark.parametrize('ordine', ORDINI)
def test_match_preview_col_modello_prende_l_apparecchio_giusto(omonimi, ordine):
    from import_bp import _match_apparecchi
    ids = _coppia(omonimi, ordine)
    items = [{'matricola': 'SN-7788', 'modello': 'SV300'},
             {'matricola': 'SN-7788', 'modello': 'Evita 4'}]
    with omonimi['app'].app_context():
        _match_apparecchi(items, struttura_id=omonimi['struttura'])
    assert items[0]['_match_id'] == ids['SV300']
    assert items[1]['_match_id'] == ids['Evita 4']
    assert '_match_ambiguo' not in items[0]


# ---------------------------------------------------------------------------
# email_monitor._find_apparecchio() — il verbale arrivato per posta
# ---------------------------------------------------------------------------

def _conn(app):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    return conn


@pytest.mark.parametrize('ordine', ORDINI)
def test_verbale_email_ambiguo_non_viene_associato(omonimi, ordine):
    """Meglio in coda per la scelta manuale che una manutenzione scritta sul
    dispositivo sbagliato."""
    from email_monitor import _find_apparecchio
    _coppia(omonimi, ordine)
    conn = _conn(omonimi['app'])
    try:
        assert _find_apparecchio(conn, 'SN-7788',
                                 struttura_id=omonimi['struttura']) is None
        assert _find_apparecchio(conn, 'SN-7788',
                                 divisione_id=omonimi['divisione']) is None
    finally:
        conn.close()


@pytest.mark.parametrize('ordine', ORDINI)
def test_verbale_email_col_modello_trova_l_apparecchio(omonimi, ordine):
    from email_monitor import _find_apparecchio
    ids = _coppia(omonimi, ordine)
    conn = _conn(omonimi['app'])
    try:
        assert _find_apparecchio(conn, 'SN-7788', struttura_id=omonimi['struttura'],
                                 modello='Evita 4') == ids['Evita 4']
        assert _find_apparecchio(conn, 'SN-7788', omonimi['divisione'],
                                 modello='SV300') == ids['SV300']
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ai_service.find_duplicates() — inventario
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('ordine', ORDINI)
def test_inventario_ambiguo_non_e_ne_nuovo_ne_duplicato(omonimi, ordine):
    """Dichiararlo 'nuovo' creerebbe un terzo apparecchio con la stessa
    matricola; dichiararlo duplicato di uno dei due sarebbe una scelta a caso."""
    from ai_service import find_duplicates
    _coppia(omonimi, ordine)
    items = [{'matricola': 'SN-7788', 'marca': None, 'modello': None}]
    with omonimi['app'].app_context():
        esiti = find_duplicates(items, omonimi['divisione'],
                                struttura_id=omonimi['struttura'])
    assert esiti[0]['match_type'] == 'ambiguo'
    assert esiti[0]['match_id'] is None
    assert len(esiti[0]['data']['_match_ambiguo']) == 2


@pytest.mark.parametrize('ordine', ORDINI)
def test_inventario_col_modello_riconosce_il_duplicato(omonimi, ordine):
    from ai_service import find_duplicates
    ids = _coppia(omonimi, ordine)
    items = [{'matricola': 'SN-7788', 'marca': 'MINDRAY', 'modello': 'SV300'}]
    with omonimi['app'].app_context():
        esiti = find_duplicates(items, omonimi['divisione'],
                                struttura_id=omonimi['struttura'])
    assert esiti[0]['match_type'] == 'esatto'
    assert esiti[0]['match_id'] == ids['SV300']
