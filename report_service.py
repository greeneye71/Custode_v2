"""
MedInventory - Motore di stampa

Genera i prospetti PDF pensati per la carta: A4 verticale, testata ripetuta,
tabelle a larghezza fissa che sommano ai 180 mm utili del foglio.

Volutamente estraneo a Flask e al database: riceve righe gia' filtrate e
restituisce byte. Cosi' le stesse funzioni servono sia la stampa a mano sia il
PDF che lo scheduler allega alle email, e sono testabili senza applicazione.
"""

import os

from fpdf import FPDF
from fpdf.enums import XPos, YPos

# Larghezza utile: A4 (210 mm) meno i margini laterali da 15 mm.
LARGHEZZA_UTILE = 180

# Grigio molto tenue per le righe alternate: su una laser d'ufficio un fondo
# pieno colorato esce sporco e consuma inutilmente.
GRIGIO_RIGA = (245, 247, 250)

# Corpo delle tabelle. A 8 pt ci stanno circa quattro righe in piu' per pagina
# e resta spazio per la colonna Descrizione senza stringere le altre oltre il
# leggibile; sotto gli 8 un elenco lungo diventa faticoso da scorrere in
# diagonale su una stampa laser. Le altezze seguono il corpo: una riga troppo
# alta per il carattere che contiene fa sembrare la tabella slabbrata.
CORPO_PT = 8
ALTEZZA_RIGA = 5.5
ALTEZZA_INTESTAZIONE = 6.5

# Caratteri tipografici che arrivano dai copia-incolla e che i font incorporati
# di fpdf2 (latin-1) non sanno rappresentare.
SOSTITUZIONI = {
    '‘': "'", '’': "'",
    '“': '"', '”': '"',
    '–': '-', '—': '-',
    '…': '...',
    ' ': ' ',
}


def testo_sicuro(valore):
    """Rende un valore stampabile con i font incorporati di fpdf2.

    fpdf2 con i font di base gestisce latin-1: gli accenti italiani passano, ma
    virgolette curve, trattini lunghi e puntini di sospensione tipografici no, e
    farebbero fallire la generazione con un errore di codifica. Meglio un
    carattere approssimato che un PDF che non esce.
    """
    if valore is None:
        return ''
    testo = str(valore)
    for originale, sostituto in SOSTITUZIONI.items():
        testo = testo.replace(originale, sostituto)
    return testo.encode('latin-1', errors='replace').decode('latin-1')


def tronca(pdf, testo, larghezza):
    """Accorcia il testo con i puntini finche' entra nella colonna.

    Si tronca invece di andare a capo: una riga alta il doppio spezza la griglia
    e rende la tabella illeggibile in diagonale. Il dato completo resta a video.
    """
    testo = testo_sicuro(testo)
    disponibile = larghezza - 2
    if pdf.get_string_width(testo) <= disponibile:
        return testo
    while testo and pdf.get_string_width(testo + '...') > disponibile:
        testo = testo[:-1]
    return testo + '...'


LOGO_ALTEZZA_MAX = 15
LOGO_LARGHEZZA_MAX = 40


def misura_logo(percorso):
    """Dimensioni in mm del logo, entro i due tetti e in proporzione.

    Vincolare la sola altezza non basta: un logo a banda (10:1) resterebbe
    alto 15 mm ma largo 150, cioe' occuperebbe la stessa fascia verticale in
    cui la testata scrive il nome della struttura, e i due si sovrapporrebbero.
    """
    from PIL import Image
    with Image.open(percorso) as immagine:
        larghezza_px, altezza_px = immagine.size
    if not larghezza_px or not altezza_px:
        raise ValueError('immagine senza dimensioni')
    scala = min(LOGO_LARGHEZZA_MAX / larghezza_px, LOGO_ALTEZZA_MAX / altezza_px)
    return larghezza_px * scala, altezza_px * scala


class ReportPDF(FPDF):
    """Base comune a tutti i prospetti: testata, pie' di pagina, elementi di tabella."""

    def __init__(self, contesto):
        super().__init__(orientation='P', unit='mm', format='A4')
        self.contesto = contesto
        self.set_auto_page_break(auto=True, margin=20)
        self.set_margins(15, 12, 15)
        self.alias_nb_pages()

    def header(self):
        # Ripetuta su ogni pagina: i fogli si separano, e la pagina 4 trovata da
        # sola su una scrivania deve poter dire cos'e'.
        alto_iniziale = self.get_y()
        margine_testo = 15
        logo = self.contesto.get('logo_path')
        if logo and os.path.exists(logo):
            try:
                larghezza, altezza = misura_logo(logo)
                self.image(logo, x=15, y=alto_iniziale, w=larghezza, h=altezza)
                # Il blocco di testo parte dopo il logo: centrato sullo spazio
                # che resta, non sul foglio intero, altrimenti tornerebbe a
                # finirgli sopra.
                margine_testo = 15 + larghezza + 4
            except Exception:
                pass  # un logo illeggibile non deve impedire la stampa

        self.set_left_margin(margine_testo)
        self.set_x(margine_testo)

        self.set_font('Helvetica', 'B', 15)
        self.cell(0, 8, testo_sicuro(self.contesto['struttura_nome']),
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')

        self.set_font('Helvetica', '', 10)
        self.cell(0, 5, testo_sicuro(self.contesto['titolo']),
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')

        ambito = self.contesto.get('ambito')
        if ambito:
            self.set_font('Helvetica', 'B', 9)
            self.cell(0, 5, testo_sicuro(ambito),
                      new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')

        from datetime import datetime
        self.set_font('Helvetica', 'I', 8)
        self.cell(0, 5, f"generato il {datetime.now().strftime('%d/%m/%Y')}",
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')

        # Il corpo del prospetto torna a partire dal margine del foglio: lo
        # spostamento vale solo per la testata.
        self.set_left_margin(15)
        self.set_x(15)

        self.set_draw_color(180, 180, 180)
        self.line(15, self.get_y(), 195, self.get_y())
        self.ln(3)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.cell(0, 10, f'Pagina {self.page_no()} di {{nb}}', align='C')

    def intestazione_tabella(self, colonne):
        """colonne: lista di (etichetta, larghezza_mm, allineamento)."""
        self.set_font('Helvetica', 'B', CORPO_PT)
        self.set_fill_color(226, 232, 240)
        self.set_draw_color(180, 180, 180)
        for etichetta, larghezza, _allineamento in colonne:
            self.cell(larghezza, ALTEZZA_INTESTAZIONE, testo_sicuro(etichetta),
                      border='B', align='C', fill=True)
        self.ln()

    def riga_tabella(self, valori, colonne, alternata=False):
        self.set_font('Helvetica', '', CORPO_PT)
        if alternata:
            self.set_fill_color(*GRIGIO_RIGA)
        for valore, (_etichetta, larghezza, allineamento) in zip(valori, colonne):
            self.cell(larghezza, ALTEZZA_RIGA, tronca(self, valore, larghezza),
                      border=0, align=allineamento, fill=alternata)
        self.ln()

    def messaggio_vuoto(self, testo):
        """Un foglio che dice 'non c'e' nulla' e' un documento valido: dimostra
        di aver controllato. Un file vuoto o un errore no."""
        self.ln(20)
        self.set_font('Helvetica', 'I', 11)
        self.set_text_color(110, 110, 110)
        self.cell(0, 10, testo_sicuro(testo), new_x=XPos.LMARGIN,
                  new_y=YPos.NEXT, align='C')
        self.set_text_color(0, 0, 0)

    def blocco_firma(self):
        if not self.contesto.get('mostra_firma'):
            return
        self.ln(12)
        self.set_font('Helvetica', '', 10)
        self.cell(0, 8, 'Data ____________________     '
                        'Firma ______________________________',
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)


# (etichetta, larghezza in mm, allineamento) — la somma deve fare 180
COLONNE_INVENTARIO = [
    ('N.', 8, 'C'),
    ('Marca', 30, 'L'),
    ('Modello', 34, 'L'),
    ('Descrizione', 42, 'L'),
    ('Matricola', 30, 'L'),
    ('Ubicazione', 36, 'L'),
]


def stampa_inventario(righe, contesto):
    """Prospetto inventario. Con contesto['raggruppa'] apre una sezione per
    divisione, con il conteggio in coda a ciascuna."""
    pdf = ReportPDF(contesto)
    pdf.add_page()

    if not righe:
        pdf.messaggio_vuoto('Nessun apparecchio corrisponde ai criteri selezionati')
        return bytes(pdf.output())

    if contesto.get('raggruppa'):
        gruppi = {}
        for riga in righe:
            gruppi.setdefault(riga.get('divisione_nome') or 'Senza divisione', []).append(riga)
        for divisione in sorted(gruppi):
            pdf.set_font('Helvetica', 'B', 11)
            pdf.ln(2)
            pdf.cell(0, 7, testo_sicuro(divisione), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            _corpo_inventario(pdf, gruppi[divisione])
            pdf.set_font('Helvetica', 'I', 9)
            pdf.cell(0, 6, f'Totale divisione: {len(gruppi[divisione])}',
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
    else:
        _corpo_inventario(pdf, righe)

    pdf.ln(2)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(0, 7, f'Totale apparecchi: {len(righe)}',
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.blocco_firma()
    return bytes(pdf.output())


def _corpo_inventario(pdf, righe):
    pdf.intestazione_tabella(COLONNE_INVENTARIO)
    for indice, riga in enumerate(righe, start=1):
        pdf.riga_tabella(
            [indice, riga.get('marca'), riga.get('modello'),
             riga.get('descrizione'), riga.get('matricola'),
             riga.get('ubicazione')],
            COLONNE_INVENTARIO,
            alternata=(indice % 2 == 0),
        )


COLONNE_SCADENZE = [
    ('Apparecchio', 48, 'L'),
    ('Matricola', 30, 'L'),
    ('Ubicazione', 40, 'L'),
    ('Divisione', 26, 'L'),
    ('Scadenza', 22, 'C'),
    ('Giorni', 14, 'C'),
]

# La colonna di spunta non allarga la tabella: le altre si stringono per
# farle posto, cosi' il prospetto resta dentro il foglio in entrambi i casi.
COLONNE_SCADENZE_SPUNTA = [
    ('', 6, 'C'),
    ('Apparecchio', 46, 'L'),
    ('Matricola', 29, 'L'),
    ('Ubicazione', 38, 'L'),
    ('Divisione', 25, 'L'),
    ('Scadenza', 22, 'C'),
    ('Giorni', 14, 'C'),
]

# Solo per il prospetto misto (il report email dello scheduler), l'unico che
# elenca manutenzioni e verifiche insieme: senza la colonna Tipo due righe
# dello stesso apparecchio sarebbero identiche tranne che nella data, e chi
# riceve il PDF non saprebbe a chi girare quale. Le stampe manuali sono gia'
# separate per tipo e la colonna sarebbe una ripetizione del titolo.
COLONNE_SCADENZE_TIPO = [
    ('Apparecchio', 38, 'L'),
    ('Matricola', 26, 'L'),
    ('Ubicazione', 30, 'L'),
    ('Divisione', 20, 'L'),
    # 30 mm: 'Verifica elettrica' e' l'etichetta piu' lunga che questa colonna
    # deve contenere e a 24 usciva troncata, cioe' proprio il dato che la
    # colonna esiste per portare.
    ('Tipo', 30, 'L'),
    ('Scadenza', 22, 'C'),
    ('Giorni', 14, 'C'),
]


def _etichetta_tipo(valore):
    """Da 'verifica_elettrica' a 'Verifica elettrica'."""
    return testo_sicuro(valore).replace('_', ' ').capitalize()


def _data_italiana(valore):
    """Da 2026-08-15 a 15/08/2026. Lascia intatto cio' che non riconosce."""
    testo = testo_sicuro(valore)
    parti = testo.split('-')
    if len(parti) == 3 and len(parti[0]) == 4:
        return f'{parti[2]}/{parti[1]}/{parti[0]}'
    return testo


def stampa_scadenze(scadute, in_scadenza, contesto):
    """Prospetto scadenze: prima cio' che e' gia' in ritardo, poi il periodo."""
    pdf = ReportPDF(contesto)
    pdf.add_page()

    if not scadute and not in_scadenza:
        pdf.messaggio_vuoto('Nessuna scadenza nel periodo indicato')
        return bytes(pdf.output())

    if contesto.get('mostra_tipo'):
        colonne = COLONNE_SCADENZE_TIPO
    elif contesto.get('mostra_spunta'):
        colonne = COLONNE_SCADENZE_SPUNTA
    else:
        colonne = COLONNE_SCADENZE

    if scadute:
        _sezione_scadenze(pdf, 'Scadute', scadute, colonne, contesto)
    if in_scadenza:
        _sezione_scadenze(
            pdf, f"In scadenza entro il {contesto.get('fine_periodo', '')}",
            in_scadenza, colonne, contesto)

    pdf.ln(2)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(0, 7, f'Totale: {len(scadute)} scadute, {len(in_scadenza)} in scadenza',
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.blocco_firma()
    return bytes(pdf.output())


def _sezione_scadenze(pdf, titolo, righe, colonne, contesto):
    pdf.ln(2)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(0, 7, testo_sicuro(f'{titolo} ({len(righe)})'),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.intestazione_tabella(colonne)
    for indice, riga in enumerate(righe, start=1):
        apparecchio = f"{riga.get('marca') or ''} {riga.get('modello') or ''}".strip()
        valori = [
            apparecchio,
            riga.get('matricola'),
            riga.get('ubicazione'),
            riga.get('divisione_nome'),
            _data_italiana(riga.get('prossima_scadenza')),
            riga.get('giorni_rimasti'),
        ]
        if contesto.get('mostra_tipo'):
            valori.insert(4, _etichetta_tipo(riga.get('tipo_manutenzione')))
        elif contesto.get('mostra_spunta'):
            valori.insert(0, '[  ]')
        pdf.riga_tabella(valori, colonne, alternata=(indice % 2 == 0))
