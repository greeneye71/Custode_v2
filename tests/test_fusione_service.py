"""La fusione di apparecchi duplicati.

candidati_duplicati e' una funzione pura su una lista di dizionari: si prova
con dieci righe in memoria invece che con un database popolato.
"""
import os
import sqlite3

import pytest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def riga(id, matricola, marca='REXXAM', modello='OZY', ubicazione='Sala 1'):
    return {'id': id, 'matricola': matricola, 'marca': marca,
            'modello': modello, 'ubicazione': ubicazione}


def test_matricola_equivalente_una_volta_normalizzata():
    from fusione_service import candidati_duplicati
    coppie = candidati_duplicati([riga(1, 'R-00015'), riga(2, 'r00015')])
    assert len(coppie) == 1
    assert {coppie[0].a['id'], coppie[0].b['id']} == {1, 2}
    assert coppie[0].criterio == 'matricola_equivalente'


def test_una_matricola_contenuta_nell_altra():
    from fusione_service import candidati_duplicati
    coppie = candidati_duplicati([riga(1, 'MON-1'), riga(2, 'MON-1/A')])
    assert len(coppie) == 1
    assert coppie[0].criterio == 'matricola_contenuta'


def test_matricole_a_distanza_uno_con_stesso_modello_e_ubicazione():
    from fusione_service import candidati_duplicati
    coppie = candidati_duplicati([riga(1, 'MON-1'), riga(2, 'MON-l')])
    assert len(coppie) == 1
    assert coppie[0].criterio == 'matricola_distanza_uno'


def test_distanza_uno_non_basta_se_il_modello_e_diverso():
    """Il criterio piu' debole dei tre e' anche quello che sbaglia piu'
    facilmente: 'MON-1' e 'MON-2' sono due monitor consecutivi, non un
    duplicato. Il modello e l'ubicazione uguali sono cio' che lo rende
    utilizzabile, e senza di essi non deve proporre nulla."""
    from fusione_service import candidati_duplicati
    assert candidati_duplicati([riga(1, 'MON-1', modello='OZY'),
                                riga(2, 'MON-2', modello='ALTRO')]) == []
    assert candidati_duplicati([riga(1, 'MON-1', ubicazione='Sala 1'),
                                riga(2, 'MON-2', ubicazione='Sala 2')]) == []


def test_due_apparecchi_consecutivi_nella_stessa_sala_sono_proposti_ma_e_il_caso_da_guardare():
    """Onesta' sul limite: stesso modello, stessa sala, matricole a distanza 1
    e' esattamente la forma di due macchine gemelle acquistate insieme
    ('MON-1' e 'MON-2' nella stessa sala). Il criterio le propone, ed e'
    voluto - non c'e' modo di distinguerle da un errore di battitura senza
    guardarle. La difesa non e' nel criterio ma nell'interfaccia: la coppia
    viene PROPOSTA, non fusa, e l'etichetta dice perche'."""
    from fusione_service import candidati_duplicati
    coppie = candidati_duplicati([riga(1, 'MON-1'), riga(2, 'MON-2')])
    assert len(coppie) == 1
    assert coppie[0].criterio == 'matricola_distanza_uno'


def test_matricole_corte_non_si_propongono_per_contenimento():
    """'1' e' contenuto in '12', '13', '104'... Su matricole corte il
    contenimento propone tutto con tutto e l'elenco diventa inutilizzabile,
    che e' il modo in cui questa funzione smette di essere usata."""
    from fusione_service import candidati_duplicati
    coppie = candidati_duplicati([riga(1, '1'), riga(2, '12'), riga(3, '13')])
    assert coppie == []


def test_una_coppia_non_viene_proposta_due_volte_ne_con_se_stessa():
    from fusione_service import candidati_duplicati
    coppie = candidati_duplicati([riga(1, 'R-00015'), riga(2, 'r00015'), riga(3, 'R 00015')])
    assert len(coppie) == 3          # 1-2, 1-3, 2-3: ogni coppia una volta sola
    viste = {frozenset((c.a['id'], c.b['id'])) for c in coppie}
    assert len(viste) == 3
    assert all(c.a['id'] != c.b['id'] for c in coppie)


def test_il_criterio_piu_forte_vince():
    """Due righe possono soddisfare piu' criteri: l'etichetta deve essere
    quella piu' forte, altrimenti l'elenco declassa una corrispondenza certa
    a somiglianza vaga e chi guarda si fida meno di quanto potrebbe."""
    from fusione_service import candidati_duplicati
    coppie = candidati_duplicati([riga(1, 'MON1'), riga(2, 'mon-1')])
    assert coppie[0].criterio == 'matricola_equivalente'


def test_matricola_vuota_non_propone_nulla():
    """Una matricola assente non e' una matricola uguale a un'altra assente."""
    from fusione_service import candidati_duplicati
    assert candidati_duplicati([riga(1, ''), riga(2, ''), riga(3, None)]) == []


def test_elenco_vuoto_o_singolo():
    from fusione_service import candidati_duplicati
    assert candidati_duplicati([]) == []
    assert candidati_duplicati([riga(1, 'R-1')]) == []


def test_matricole_cortissime_non_si_propongono_nemmeno_a_parita_di_modello():
    """Il vincolo di lunghezza governa anche il criterio piu' debole, non solo
    il contenimento: 'A-1' e 'A-2' nella stessa sala sono quasi sempre due
    macchine numerate di seguito, non la stessa registrata due volte. Il
    criterio che le distinguerebbe non esiste, e proporle tutte rende
    l'elenco inutilizzabile - che e' il modo in cui questa funzione smette
    di essere usata."""
    from fusione_service import candidati_duplicati
    assert candidati_duplicati([riga(1, 'A-1'), riga(2, 'A-2')]) == []
