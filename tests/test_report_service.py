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
