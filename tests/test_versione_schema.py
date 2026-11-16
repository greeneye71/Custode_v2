"""B05 - stato del database locale da chiarire prima dell'esercizio.

Un database con PRAGMA user_version = 0 costringe ogni strumento a dedurre
la versione dalla forma delle tabelle, ed e' cosi' che un file gia'
aggiornato si e' fatto riconoscere come v1.1 con nove migrazioni pendenti.
La diagnosi ora lo dice, con il rimedio: backup verificato, poi migrazione.
"""
import pytest

from manutenzione_lib import diagnosi


def fotografia(user_version, pendenti=(), disponibile=True):
    return {'schema': {'disponibile': disponibile,
                       'versione': '2.8', 'user_version': user_version,
                       'pendenti': list(pendenti)}}


def test_user_version_a_zero_e_un_avviso():
    esito = diagnosi.controllo_versione_dichiarata(
        None, {}, fotografia(0))
    assert esito is not None
    assert esito.gravita == 'avviso'
    assert 'user_version' in esito.dettaglio


def test_il_rimedio_nomina_backup_e_migrazione():
    """Chi legge la diagnosi su un'installazione altrui non deve indovinare
    il seguito, e soprattutto non deve migrare senza backup."""
    esito = diagnosi.controllo_versione_dichiarata(None, {}, fotografia(0))
    assert 'backup' in esito.rimedio
    assert 'migra' in esito.rimedio


def test_una_versione_dichiarata_non_produce_nulla():
    assert diagnosi.controllo_versione_dichiarata(
        None, {}, fotografia(282)) is None


def test_con_migrazioni_pendenti_tace():
    """Le migrazioni pendenti sono gia' un errore di controllo_migrazioni:
    due righe sullo stesso fatto rendono la diagnosi meno leggibile."""
    assert diagnosi.controllo_versione_dichiarata(
        None, {}, fotografia(0, pendenti=['v2_7'])) is None


def test_schema_non_leggibile_non_produce_nulla():
    assert diagnosi.controllo_versione_dichiarata(
        None, {}, fotografia(0, disponibile=False)) is None
    assert diagnosi.controllo_versione_dichiarata(None, {}, {}) is None


def test_il_controllo_e_nell_elenco_eseguito():
    assert diagnosi.controllo_versione_dichiarata in diagnosi.CONTROLLI


@pytest.mark.parametrize('user_version', [0, 143, 282])
def test_esegui_non_esplode_con_qualsiasi_versione(user_version):
    esiti = diagnosi.esegui(None, {}, fotografia(user_version))
    assert all(e.gravita in ('errore', 'avviso') for e in esiti)
