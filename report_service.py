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
        logo = self.contesto.get('logo_path')
        if logo and os.path.exists(logo):
            try:
                self.image(logo, x=15, y=alto_iniziale, h=15)
            except Exception:
                pass  # un logo illeggibile non deve impedire la stampa

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

        self.set_draw_color(180, 180, 180)
        self.line(15, self.get_y(), 195, self.get_y())
        self.ln(3)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.cell(0, 10, f'Pagina {self.page_no()} di {{nb}}', align='C')

    def intestazione_tabella(self, colonne):
        """colonne: lista di (etichetta, larghezza_mm, allineamento)."""
        self.set_font('Helvetica', 'B', 9)
        self.set_fill_color(226, 232, 240)
        self.set_draw_color(180, 180, 180)
        for etichetta, larghezza, _allineamento in colonne:
            self.cell(larghezza, 7, testo_sicuro(etichetta), border='B',
                      align='C', fill=True)
        self.ln()

    def riga_tabella(self, valori, colonne, alternata=False):
        self.set_font('Helvetica', '', 9)
        if alternata:
            self.set_fill_color(*GRIGIO_RIGA)
        for valore, (_etichetta, larghezza, allineamento) in zip(valori, colonne):
            self.cell(larghezza, 6, tronca(self, valore, larghezza), border=0,
                      align=allineamento, fill=alternata)
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
    ('N.', 10, 'C'),
    ('Marca', 38, 'L'),
    ('Modello', 42, 'L'),
    ('Matricola', 38, 'L'),
    ('Ubicazione', 52, 'L'),
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
             riga.get('matricola'), riga.get('ubicazione')],
            COLONNE_INVENTARIO,
            alternata=(indice % 2 == 0),
        )
