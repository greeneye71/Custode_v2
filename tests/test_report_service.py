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
        {'marca': 'REXXAM', 'modello': 'OZY', 'descrizione': 'Autorefrattometro',
         'matricola': 'R-00015', 'ubicazione': 'Sala visite 1',
         'divisione_nome': 'Oculistica'},
        {'marca': 'TOMEY', 'modello': 'RC-800', 'descrizione': 'Topografo',
         'matricola': '576806', 'ubicazione': 'Sala visite 1',
         'divisione_nome': 'Oculistica'},
        {'marca': 'GE', 'modello': 'B40', 'descrizione': 'Monitor multiparametrico',
         'matricola': 'MON-1', 'ubicazione': 'Ambulatorio',
         'divisione_nome': 'Cardiologia'},
    ]


def test_percorso_logo_rifiuta_un_percorso_fuori_da_uploads(app):
    """logo_path arriva dal database. Oggi l'unico scrittore passa da
    upload_subdir e secure_filename, ma la funzione non deve dipendere dal
    fatto che nessuno scriva mai in quella colonna in altro modo: gli altri
    punti del progetto che compongono percorsi da dati (app.py, manutenzioni.py,
    verifiche.py) il controllo lo fanno."""
    from models import percorso_logo_struttura
    with app.app_context():
        assert percorso_logo_struttura({'logo_path': '../../etc/passwd'}) is None
        assert percorso_logo_struttura({'logo_path': 'strutture/1/loghi/l.png'}) is not None


def test_percorso_logo_non_rifiuta_la_radice_stessa(app):
    """Stesso caso limite di struttura_service._allegato_nel_perimetro: un
    controllo scritto come 'startswith(radice + separatore)' da solo tratta
    come 'fuori' anche il percorso che COINCIDE con la radice, perche' la
    stringa uguale non inizia con se stessa piu' un separatore."""
    from models import percorso_logo_struttura
    with app.app_context():
        assert percorso_logo_struttura({'logo_path': '.'}) is not None


def test_il_logo_resta_entro_i_due_tetti_mantenendo_le_proporzioni(tmp_path):
    """La specifica pone due limiti, 15 mm di altezza e 40 di larghezza.
    Vincolare la sola altezza lascia passare un logo a banda largo 150 mm, che
    invade la fascia in cui la testata scrive il nome della struttura."""
    from PIL import Image
    from report_service import misura_logo

    banda = str(tmp_path / 'banda.png')
    Image.new('RGB', (1200, 120), color=(30, 80, 200)).save(banda)
    larghezza, altezza = misura_logo(banda)
    assert larghezza <= 40 and altezza <= 15
    assert abs(larghezza / altezza - 10) < 0.01   # proporzioni intatte

    colonna = str(tmp_path / 'colonna.png')
    Image.new('RGB', (100, 400), color=(30, 80, 200)).save(colonna)
    larghezza, altezza = misura_logo(colonna)
    assert larghezza <= 40 and altezza <= 15
    assert abs(altezza / larghezza - 4) < 0.01


def test_le_colonne_inventario_sommano_alla_larghezza_utile():
    assert sum(larghezza for _, larghezza, _ in COLONNE_INVENTARIO) == 180


def test_il_corpo_a_8_pt_fa_stare_quaranta_righe_in_una_pagina():
    """Il carattere piu' piccolo serve a stampare meno fogli, quindi il test
    misura proprio quello: quaranta apparecchi devono stare in una pagina
    sola. Con il corpo e l'altezza riga precedenti (9 pt su righe da 6 mm) ne
    entravano trentasette, e questa stessa lista ne avrebbe occupate due."""
    righe = [{'marca': 'M', 'modello': 'MM', 'descrizione': 'D',
              'matricola': f'S{i}', 'ubicazione': 'U', 'divisione_nome': 'Div'}
             for i in range(40)]
    pdf = stampa_inventario(righe, contesto_minimo(raggruppa=False))
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 1


def test_inventario_elenca_marca_modello_descrizione_matricola_e_ubicazione():
    pdf = stampa_inventario(righe_inventario(), contesto_minimo(raggruppa=False))
    testo = testo_di(pdf)
    assert 'REXXAM' in testo
    assert 'RC-800' in testo
    assert 'Autorefrattometro' in testo
    assert 'R-00015' in testo
    assert 'Sala visite 1' in testo


def test_una_descrizione_lunga_non_sfonda_la_sua_colonna():
    """La colonna Descrizione e' larga 42 mm: un testo che non ci sta va
    troncato con i puntini, non mandato a capo ne' lasciato debordare sulla
    colonna Matricola. Una riga alta il doppio spezza la griglia e rende la
    tabella illeggibile in diagonale."""
    lunga = 'Elettrocardiografo multicanale con carrello e stampante integrata'
    righe = [dict(righe_inventario()[0], descrizione=lunga)]
    testo = testo_di(stampa_inventario(righe, contesto_minimo(raggruppa=False)))
    assert lunga not in testo
    assert '...' in testo
    # La matricola resta al suo posto: la descrizione non l'ha spinta fuori.
    assert 'R-00015' in testo


def test_inventario_riporta_il_totale():
    pdf = stampa_inventario(righe_inventario(), contesto_minimo(raggruppa=False))
    testo = testo_di(pdf)
    assert 'Totale apparecchi: 3' in testo


def test_inventario_raggruppato_apre_una_sezione_per_divisione():
    pdf = stampa_inventario(righe_inventario(), contesto_minimo(raggruppa=True))
    testo = testo_di(pdf)
    assert 'Oculistica' in testo
    assert 'Cardiologia' in testo

    # Non basta che i due conteggi compaiano da qualche parte nel testo:
    # devono comparire nella sezione della divisione giusta. Le sezioni sono
    # ordinate alfabeticamente, quindi 'Cardiologia' precede 'Oculistica' nel
    # testo estratto: isoliamo i due segmenti e controlliamo il conteggio
    # dentro ciascuno, cosi' uno scambio fra le due divisioni fa fallire
    # il test invece di passare inosservato.
    posizione_cardiologia = testo.index('Cardiologia')
    posizione_oculistica = testo.index('Oculistica')
    assert posizione_cardiologia < posizione_oculistica

    segmento_cardiologia = testo[posizione_cardiologia:posizione_oculistica]
    segmento_oculistica = testo[posizione_oculistica:]

    # Un apparecchio in Cardiologia, due in Oculistica (vedi righe_inventario)
    assert 'Totale divisione: 1' in segmento_cardiologia
    assert 'Totale divisione: 2' in segmento_oculistica
    assert 'Totale divisione: 2' not in segmento_cardiologia
    assert 'Totale divisione: 1' not in segmento_oculistica


def test_inventario_raggruppato_gestisce_la_sezione_senza_divisione():
    """Le righe prive di divisione_nome (assente o stringa vuota) devono
    confluire nella sezione 'Senza divisione' (vedi il fallback in
    stampa_inventario) senza far fallire la generazione del PDF."""
    righe = righe_inventario() + [
        {'marca': 'ACME', 'modello': 'Z1', 'matricola': 'Z-001',
         'ubicazione': 'Magazzino', 'divisione_nome': ''},
    ]
    pdf = stampa_inventario(righe, contesto_minimo(raggruppa=True))
    testo = testo_di(pdf)
    assert 'Senza divisione' in testo

    # 'Senza divisione' e' alfabeticamente successiva alle altre sezioni:
    # il suo conteggio deve comparire dopo il titolo della sezione stessa.
    posizione_senza_divisione = testo.index('Senza divisione')
    segmento = testo[posizione_senza_divisione:]
    assert 'Totale divisione: 1' in segmento


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


from report_service import (COLONNE_SCADENZE, COLONNE_SCADENZE_SPUNTA,
                            COLONNE_SCADENZE_TIPO, stampa_scadenze)


def righe_scadenze():
    return [
        {'marca': 'REXXAM', 'modello': 'OZY', 'matricola': 'R-00015',
         'ubicazione': 'Sala visite 1', 'divisione_nome': 'Oculistica',
         'prossima_scadenza': '2026-08-15', 'giorni_rimasti': 18},
    ]


def righe_scadute():
    return [
        {'marca': 'GE', 'modello': 'B40', 'matricola': 'MON-1',
         'ubicazione': 'Ambulatorio', 'divisione_nome': 'Cardiologia',
         'prossima_scadenza': '2026-05-01', 'giorni_rimasti': -87},
    ]


def contesto_scadenze(**extra):
    # I default vanno sovrascritti da extra, non passati insieme: altrimenti
    # contesto_scadenze(mostra_spunta=True) passerebbe due volte lo stesso
    # argomento e solleverebbe TypeError.
    base = {'titolo': 'Scadenze manutenzioni',
            'fine_periodo': '31/12/2026',
            'mostra_spunta': False}
    base.update(extra)
    return contesto_minimo(**base)


def test_le_colonne_scadenze_sommano_alla_larghezza_utile():
    assert sum(larghezza for _, larghezza, _ in COLONNE_SCADENZE) == 180
    assert sum(larghezza for _, larghezza, _ in COLONNE_SCADENZE_SPUNTA) == 180
    assert sum(larghezza for _, larghezza, _ in COLONNE_SCADENZE_TIPO) == 180


def test_la_colonna_tipo_riporta_la_natura_della_scadenza():
    """Il prospetto misto (solo il report email) deve dire di ogni riga se e'
    una manutenzione e di che genere, o una verifica elettrica: e' l'unico
    dato che distingue due righe dello stesso apparecchio."""
    righe = [
        dict(righe_scadenze()[0], tipo_manutenzione='preventiva'),
        dict(righe_scadenze()[0], tipo_manutenzione='verifica_elettrica'),
    ]
    testo = testo_di(stampa_scadenze([], righe, contesto_scadenze(mostra_tipo=True)))
    assert 'Preventiva' in testo
    assert 'Verifica elettrica' in testo


def test_scadenze_mostra_prima_le_scadute_poi_il_periodo():
    pdf = stampa_scadenze(righe_scadute(), righe_scadenze(), contesto_scadenze())
    testo = testo_di(pdf)
    assert 'Scadute' in testo
    assert 'In scadenza entro il 31/12/2026' in testo
    assert testo.index('Scadute') < testo.index('In scadenza entro')


def test_scadenze_riporta_apparecchio_matricola_e_scadenza():
    pdf = stampa_scadenze([], righe_scadenze(), contesto_scadenze())
    testo = testo_di(pdf)
    assert 'REXXAM' in testo
    assert 'R-00015' in testo
    assert '15/08/2026' in testo


def test_scadenze_senza_nulla_produce_il_foglio_nessun_dato():
    pdf = stampa_scadenze([], [], contesto_scadenze())
    assert 'Nessuna scadenza nel periodo indicato' in testo_di(pdf)


def test_la_spunta_aggiunge_la_colonna_senza_sfondare():
    pdf = stampa_scadenze([], righe_scadenze(), contesto_scadenze(mostra_spunta=True))
    assert pdf.startswith(b'%PDF')
    assert 'REXXAM' in testo_di(pdf)
