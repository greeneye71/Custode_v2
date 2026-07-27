"""Test del motore di stampa. Sono funzioni pure: dati in ingresso, byte in uscita."""
import io

import pytest
from pypdf import PdfReader

from report_service import ReportPDF, testo_sicuro


def testo_di(pdf_bytes):
    """Estrae il testo di tutte le pagine di un PDF."""
    lettore = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(pagina.extract_text() for pagina in lettore.pages)


def contesto_minimo(**extra):
    base = {
        'struttura_nome': 'Casa di Cura Sant Anna',
        'titolo': 'Inventario apparecchi elettromedicali',
        'ambito': '',
        'logo_path': None,
        'mostra_firma': False,
    }
    base.update(extra)
    return base


def test_testo_sicuro_converte_i_caratteri_tipografici():
    assert testo_sicuro('Sala ’Ecografia’') == "Sala 'Ecografia'"
    assert testo_sicuro('Reparto — Piano 2') == 'Reparto - Piano 2'
    assert testo_sicuro('Segue…') == 'Segue...'


def test_testo_sicuro_sostituisce_i_caratteri_fuori_latin1():
    # Un carattere cinese non e' rappresentabile: non deve far fallire il PDF.
    assert testo_sicuro('Sala 中') == 'Sala ?'


def test_testo_sicuro_conserva_gli_accenti_italiani():
    assert testo_sicuro('Perchè più città') == 'Perchè più città'


def test_testo_sicuro_gestisce_none_e_numeri():
    assert testo_sicuro(None) == ''
    assert testo_sicuro(42) == '42'


def test_il_pdf_ha_la_firma_corretta_e_la_testata():
    pdf = ReportPDF(contesto_minimo())
    pdf.add_page()
    risultato = bytes(pdf.output())

    assert risultato.startswith(b'%PDF')
    testo = testo_di(risultato)
    assert 'Casa di Cura Sant Anna' in testo
    assert 'Inventario apparecchi elettromedicali' in testo


def test_l_ambito_compare_nella_testata_quando_valorizzato():
    pdf = ReportPDF(contesto_minimo(ambito='Divisione: Oculistica'))
    pdf.add_page()
    assert 'Divisione: Oculistica' in testo_di(bytes(pdf.output()))


def test_messaggio_vuoto_produce_un_documento_valido():
    pdf = ReportPDF(contesto_minimo())
    pdf.add_page()
    pdf.messaggio_vuoto('Nessun apparecchio corrisponde ai criteri selezionati')
    testo = testo_di(bytes(pdf.output()))
    assert 'Nessun apparecchio corrisponde' in testo


def test_blocco_firma_compare_solo_se_richiesto():
    senza = ReportPDF(contesto_minimo(mostra_firma=False))
    senza.add_page()
    senza.blocco_firma()
    assert 'Firma' not in testo_di(bytes(senza.output()))

    con = ReportPDF(contesto_minimo(mostra_firma=True))
    con.add_page()
    con.blocco_firma()
    assert 'Firma' in testo_di(bytes(con.output()))


from report_service import COLONNE_INVENTARIO, stampa_inventario


def righe_inventario():
    return [
        {'marca': 'REXXAM', 'modello': 'OZY', 'matricola': 'R-00015',
         'ubicazione': 'Sala visite 1', 'divisione_nome': 'Oculistica'},
        {'marca': 'TOMEY', 'modello': 'RC-800', 'matricola': '576806',
         'ubicazione': 'Sala visite 1', 'divisione_nome': 'Oculistica'},
        {'marca': 'GE', 'modello': 'B40', 'matricola': 'MON-1',
         'ubicazione': 'Ambulatorio', 'divisione_nome': 'Cardiologia'},
    ]


def test_le_colonne_inventario_sommano_alla_larghezza_utile():
    assert sum(larghezza for _, larghezza, _ in COLONNE_INVENTARIO) == 180


def test_inventario_elenca_marca_modello_matricola_e_ubicazione():
    pdf = stampa_inventario(righe_inventario(), contesto_minimo(raggruppa=False))
    testo = testo_di(pdf)
    assert 'REXXAM' in testo
    assert 'RC-800' in testo
    assert 'R-00015' in testo
    assert 'Sala visite 1' in testo


def test_inventario_riporta_il_totale():
    pdf = stampa_inventario(righe_inventario(), contesto_minimo(raggruppa=False))
    testo = testo_di(pdf)
    assert 'Totale apparecchi: 3' in testo


def test_inventario_raggruppato_apre_una_sezione_per_divisione():
    pdf = stampa_inventario(righe_inventario(), contesto_minimo(raggruppa=True))
    testo = testo_di(pdf)
    assert 'Oculistica' in testo
    assert 'Cardiologia' in testo
    # Due apparecchi in Oculistica, uno in Cardiologia
    assert 'Totale divisione: 2' in testo
    assert 'Totale divisione: 1' in testo


def test_inventario_vuoto_produce_il_foglio_nessun_dato():
    pdf = stampa_inventario([], contesto_minimo(raggruppa=False))
    assert 'Nessun apparecchio corrisponde ai criteri selezionati' in testo_di(pdf)


def test_inventario_regge_i_caratteri_tipografici():
    righe = [{'marca': 'ACME', 'modello': 'X—1', 'matricola': 'A1',
              'ubicazione': 'Sala ’Rossa’', 'divisione_nome': 'Reparto'}]
    pdf = stampa_inventario(righe, contesto_minimo(raggruppa=False))
    assert pdf.startswith(b'%PDF')


def test_un_inventario_lungo_va_a_capo_pagina():
    # ~35 righe stanno in una pagina, 200 no: serve a verificare che
    # l'interruzione automatica funzioni e che la testata si ripeta.
    righe = [{'marca': f'MARCA {n}', 'modello': f'MOD-{n}',
              'matricola': f'M{n:04d}', 'ubicazione': 'Sala 1',
              'divisione_nome': 'Reparto'} for n in range(200)]
    pdf = stampa_inventario(righe, contesto_minimo(raggruppa=False))

    lettore = PdfReader(io.BytesIO(pdf))
    assert len(lettore.pages) > 1
    # La testata si ripete: il nome della struttura compare su ogni pagina
    for pagina in lettore.pages:
        assert 'Casa di Cura Sant Anna' in pagina.extract_text()
