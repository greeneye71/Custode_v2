"""Regole di dominio degli impianti.

Qui sta il calcolo, non la presentazione: nessun import di request o
render_template. Il blueprint valida i form e chiama queste funzioni; lo
scheduler chiama avvisi_da_inviare() e destinatari().
"""

import calendar
import logging
from datetime import date, datetime

from models import execute, query_one
from impianti_catalogo import voci_per_tipo

logger = logging.getLogger('medinventory.impianti')

#: Esiti che confermano l'esecuzione della verifica e fanno ripartire il ciclo.
#: 'con_riserva' sta qui perche' la verifica e' stata fatta: le riserve sono
#: prescrizioni da chiudere, non un rinvio della scadenza. 'negativo' invece
#: lascia la riga scaduta, che e' esattamente lo stato reale dell'impianto.
ESITI_CHE_RINNOVANO = ('positivo', 'con_riserva')


def aggiungi_mesi(data, mesi):
    """Somma mesi a una data, tagliando il giorno sui mesi corti.

    31 gennaio + 1 mese = 28 (o 29) febbraio: un timedelta di giorni fissi
    sfaserebbe progressivamente le periodicita' lunghe.
    Restituisce una stringa ISO 'YYYY-MM-DD'.
    """
    if isinstance(data, str):
        data = datetime.strptime(data[:10], '%Y-%m-%d').date()
    totale = data.month - 1 + int(mesi)
    anno = data.year + totale // 12
    mese = totale % 12 + 1
    giorno = min(data.day, calendar.monthrange(anno, mese)[1])
    return date(anno, mese, giorno).isoformat()


def registra_intervento(impianto_id, dati, utente_id=None):
    """Registra un intervento e, se serve, fa avanzare la riga di piano.

    Restituisce (intervento_id, nuova_scadenza | None). La nuova scadenza si
    calcola dalla data dell'intervento, non dalla scadenza precedente: se la
    verifica e' stata fatta in ritardo, il ciclo riparte da quando e' stata
    fatta davvero.
    """
    intervento_id = execute(
        """INSERT INTO impianti_interventi
           (impianto_id, scadenza_id, componente_id, tipo, data_intervento,
            esito, manutentore_id, tecnico_ditta, descrizione, costo,
            verbale_path, note, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (impianto_id, dati.get('scadenza_id'), dati.get('componente_id'),
         dati.get('tipo', 'ordinaria'), dati['data_intervento'],
         dati.get('esito'), dati.get('manutentore_id'),
         dati.get('tecnico_ditta'), dati.get('descrizione'), dati.get('costo'),
         dati.get('verbale_path'), dati.get('note'), utente_id)
    ).lastrowid

    scadenza_id = dati.get('scadenza_id')
    if not scadenza_id:
        return intervento_id, None

    riga = query_one(
        "SELECT * FROM impianti_scadenze WHERE id = ? AND impianto_id = ?",
        (scadenza_id, impianto_id)
    )
    if not riga:
        return intervento_id, None

    if dati.get('esito') not in ESITI_CHE_RINNOVANO:
        return intervento_id, None

    if riga['periodicita_mesi']:
        nuova = aggiungi_mesi(dati['data_intervento'], riga['periodicita_mesi'])
        execute(
            "UPDATE impianti_scadenze SET prossima_scadenza = ?,"
            " updated_at = datetime('now') WHERE id = ?",
            (nuova, scadenza_id)
        )
        return intervento_id, nuova

    # Una tantum: eseguita, esce dal piano. Resta nello storico interventi.
    execute(
        "UPDATE impianti_scadenze SET attiva = 0, updated_at = datetime('now')"
        " WHERE id = ?", (scadenza_id,)
    )
    return intervento_id, None


def applica_catalogo(impianto_id, tipo, nomi_scelti, partenza):
    """Crea le righe di piano per le voci di catalogo scelte.

    'partenza' e' la data da cui contare la prima scadenza (di norma la data di
    creazione dell'impianto). Le voci non presenti in catalogo sono ignorate.
    Restituisce il numero di righe create.
    """
    scelti = {(n or '').strip().lower() for n in nomi_scelti}
    creati = 0
    for voce in voci_per_tipo(tipo):
        if voce['nome'].strip().lower() not in scelti:
            continue
        execute(
            """INSERT INTO impianti_scadenze
               (impianto_id, nome, riferimento_normativo, periodicita_mesi,
                prossima_scadenza)
               VALUES (?, ?, ?, ?, ?)""",
            (impianto_id, voce['nome'], voce['riferimento'] or None,
             voce['mesi'], aggiungi_mesi(partenza, voce['mesi']))
        )
        creati += 1
    return creati
