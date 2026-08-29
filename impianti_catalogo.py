"""Periodicità standard proposte alla creazione di un impianto.

Una costante, non una tabella: sono valori di legge o di norma tecnica, uguali
per tutte le strutture, e vanno aggiornati con il codice. Il catalogo è una
*proposta*: le voci scelte diventano righe di impianti_scadenze, e da quel
momento vivono di vita propria. Modificare CATALOGO non riscrive nessun piano
già esistente.
"""

CATALOGO = {
    'elettrico': [
        {'nome': 'Verifica impianto di terra', 'mesi': 24,
         'riferimento': 'DPR 462/01'},
        {'nome': 'Prova interruttori differenziali', 'mesi': 6,
         'riferimento': 'CEI 64-8'},
    ],
    'idraulico': [
        {'nome': 'Analisi legionella', 'mesi': 12,
         'riferimento': 'Linee guida 07/05/2015'},
    ],
    'riscaldamento': [
        {'nome': 'Manutenzione e controllo fumi', 'mesi': 12,
         'riferimento': 'DPR 74/2013'},
    ],
    'climatizzazione': [
        {'nome': 'Pulizia filtri e batterie', 'mesi': 6, 'riferimento': ''},
        {'nome': 'Controllo perdite F-gas', 'mesi': 12,
         'riferimento': 'Reg. UE 517/2014'},
    ],
    'antincendio': [
        {'nome': 'Controllo estintori', 'mesi': 6, 'riferimento': 'UNI 9994-1'},
        {'nome': 'Controllo idranti', 'mesi': 6, 'riferimento': 'UNI 10779'},
        {'nome': 'Verifica rivelazione incendi', 'mesi': 6,
         'riferimento': 'UNI 11224'},
    ],
    'gas_medicali': [
        {'nome': 'Verifica periodica impianto', 'mesi': 12,
         'riferimento': 'UNI EN ISO 7396-1'},
    ],
    'ascensori': [
        {'nome': 'Verifica periodica', 'mesi': 24, 'riferimento': 'DPR 162/99'},
        {'nome': 'Manutenzione ordinaria', 'mesi': 6, 'riferimento': 'DPR 162/99'},
    ],
    'rete_dati': [],
    'altro': [],
}


def voci_per_tipo(tipo):
    """Le voci di catalogo di un tipo di impianto. Lista vuota se sconosciuto."""
    return list(CATALOGO.get(tipo, []))


def voci_mancanti(tipo, nomi_presenti):
    """Le voci di catalogo non ancora nel piano dell'impianto.

    Il confronto è sul nome normalizzato (senza spazi ai bordi, minuscolo):
    riproporre una voce già inserita a mano con la stessa dicitura sarebbe un
    doppione che l'utente deve poi cancellare.
    """
    presenti = {(n or '').strip().lower() for n in nomi_presenti}
    return [v for v in voci_per_tipo(tipo)
            if v['nome'].strip().lower() not in presenti]
