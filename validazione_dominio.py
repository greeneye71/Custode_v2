"""
MedInventory - Validazione di dominio condivisa.

L'audit M14 rilevava che i percorsi AI (import unificato e posta) duplicavano
la logica di dominio con regole piu' permissive di quelle applicate a form e
API: date non ISO che raggiungevano SQLite, periodicita' nulle o negative,
inventari con marca/modello/matricola vuoti, esiti sconosciuti convertiti
ottimisticamente in 'positivo' e classificazioni incerte trasformate in un
tipo di documento plausibile ma arbitrario.

Questo modulo e' l'unico posto in cui quelle regole vivono. Resta senza
dipendenze da Flask, cosi' da poter essere usato anche dal monitor della
posta, che gira in un thread di sfondo senza contesto applicativo.

Convenzione delle funzioni `valida_*`: restituiscono `(dati, errori)` dove
`errori` e' una lista di messaggi in italiano gia' presentabili all'utente.
Lista vuota significa che i dati sono scrivibili cosi' come sono.
"""

from datetime import datetime

# Valori ammessi dallo schema (CHECK di schema.sql).
TIPI_MANUTENZIONE = ('preventiva', 'correttiva', 'verifica', 'calibrazione')
ESITI_VERIFICA = ('positivo', 'negativo', 'con_riserva')
CLASSIFICAZIONI_APPARECCHIO = ('I', 'IIa', 'IIb', 'III')

# Periodicita' plausibili: un giorno di intervallo e' gia' poco credibile,
# oltre i dieci anni non e' piu' una periodicita'.
PERIODICITA_MIN = 1
PERIODICITA_MAX = 3650
PERIODICITA_VERIFICA_DEFAULT = 730  # due anni

# Anni di fabbricazione accettabili.
ANNO_MIN = 1900
ANNO_MAX = 2100

# Tipo restituito quando la classificazione non e' riconosciuta: il documento
# va rivisto a mano, non trasformato nel tipo piu' probabile.
TIPO_NON_CLASSIFICATO = 'da_classificare'

# Formati che i verbali usano davvero. L'ordine conta: '%d/%m/%Y' e' presente
# e '%m/%d/%Y' no, perche' 03/04/2026 in un documento italiano e' il 3 aprile.
_FORMATI_DATA = (
    '%Y-%m-%d',
    '%d/%m/%Y',
    '%d-%m-%Y',
    '%d.%m.%Y',
    '%Y/%m/%d',
    '%d/%m/%y',
    '%d-%m-%y',
)


def normalizza_data(valore):
    """Riporta una data a 'YYYY-MM-DD'. None se non e' una data valida.

    Accetta i formati che compaiono nei verbali, ma non inventa nulla: una
    stringa incomprensibile o un 31/02 restituiscono None, e chi chiama
    decide se e' un errore bloccante.
    """
    if valore is None:
        return None
    testo = str(valore).strip()
    if not testo:
        return None
    # Le date ISO possono arrivare con l'ora attaccata.
    if 'T' in testo:
        testo = testo.split('T', 1)[0]
    elif ' ' in testo and len(testo) > 10:
        testo = testo.split(' ', 1)[0]
    for formato in _FORMATI_DATA:
        try:
            return datetime.strptime(testo, formato).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


def normalizza_periodicita(valore):
    """Periodicita' in giorni come intero plausibile, oppure None.

    Gestisce i float che l'AI restituisce ("365.0") e rifiuta zero, valori
    negativi e intervalli oltre i dieci anni.
    """
    if valore is None:
        return None
    testo = str(valore).strip().replace(',', '.')
    if not testo:
        return None
    try:
        giorni = int(float(testo))
    except (TypeError, ValueError):
        return None
    if giorni < PERIODICITA_MIN or giorni > PERIODICITA_MAX:
        return None
    return giorni


def normalizza_costo(valore):
    """Importo come float non negativo, oppure None."""
    if valore is None:
        return None
    testo = str(valore).strip()
    if not testo:
        return None
    # "1.234,56 EUR" -> "1234.56"
    testo = testo.replace('€', '').replace('EUR', '').replace('eur', '').strip()
    if ',' in testo and '.' in testo:
        testo = testo.replace('.', '').replace(',', '.')
    else:
        testo = testo.replace(',', '.')
    try:
        importo = float(testo)
    except (TypeError, ValueError):
        return None
    if importo < 0:
        return None
    return importo


def normalizza_anno(valore):
    """Anno di fabbricazione plausibile, oppure None."""
    if valore is None:
        return None
    testo = str(valore).strip()
    if not testo:
        return None
    try:
        anno = int(float(testo))
    except (TypeError, ValueError):
        return None
    if anno < ANNO_MIN or anno > ANNO_MAX:
        return None
    return anno


def normalizza_classificazione(valore):
    """Classe CEI dell'apparecchio, oppure None se non riconosciuta."""
    if valore is None:
        return None
    testo = str(valore).strip()
    if not testo:
        return None
    for classe in CLASSIFICAZIONI_APPARECCHIO:
        if testo.lower() == classe.lower():
            return classe
    return None


def normalizza_tipo_manutenzione(valore):
    """Tipo di manutenzione fra quelli ammessi, oppure None.

    Tollera le forme discorsive dei verbali ("Manutenzione preventiva
    programmata"), ma un tipo che non si riconduce a nessuno dei quattro
    valori non diventa 'preventiva': restituisce None.
    """
    if valore is None:
        return None
    testo = str(valore).strip().lower()
    if not testo:
        return None
    if testo in TIPI_MANUTENZIONE:
        return testo
    radici = (
        ('correttiv', 'correttiva'),
        ('preventiv', 'preventiva'),
        ('calibraz', 'calibrazione'),
        ('taratur', 'calibrazione'),
        ('verific', 'verifica'),
        ('program', 'preventiva'),
        ('riparaz', 'correttiva'),
        ('guast', 'correttiva'),
    )
    for radice, tipo in radici:
        if radice in testo:
            return tipo
    return None


def normalizza_esito_verifica(valore):
    """Esito di una verifica elettrica, oppure None se non riconosciuto.

    Non esiste un esito predefinito: prima della 2.8.4 un esito assente o
    incomprensibile diventava 'positivo', cioe' una verifica superata che
    nessuno aveva dichiarato tale.
    """
    if valore is None:
        return None
    testo = str(valore).strip().lower().replace(' ', '_')
    if testo in ESITI_VERIFICA:
        return testo
    equivalenti = {
        'positiva': 'positivo', 'superato': 'positivo', 'superata': 'positivo',
        'conforme': 'positivo', 'ok': 'positivo', 'idoneo': 'positivo',
        'negativa': 'negativo', 'non_superato': 'negativo',
        'non_conforme': 'negativo', 'non_idoneo': 'negativo',
        'fallito': 'negativo',
        'riserva': 'con_riserva', 'con_riserve': 'con_riserva',
        'parzialmente_conforme': 'con_riserva',
    }
    return equivalenti.get(testo)


def _testo(dati, chiave):
    valore = dati.get(chiave)
    if valore is None:
        return ''
    return str(valore).strip()


def valida_apparecchio(dati, richiedi_identificativi=True):
    """Regole del form apparecchi applicate ai dati che arrivano dall'AI.

    Marca, modello e matricola sono obbligatori esattamente come nel form: un
    inventario importato non deve poter creare schede senza identificativi,
    che poi nessuna ricerca ritrova e nessun verbale sa abbinare.

    `richiedi_identificativi=False` serve all'aggiornamento di una scheda
    gia' esistente, dove quei tre campi non vengono riscritti e quindi non
    hanno senso come obbligatori: gli altri controlli restano.
    """
    errori = []
    puliti = dict(dati)

    for campo in ('marca', 'modello', 'matricola'):
        valore = _testo(dati, campo)
        if richiedi_identificativi and not valore:
            errori.append("Campo obbligatorio assente: " + campo)
        puliti[campo] = valore

    if _testo(dati, 'anno_fabbricazione'):
        anno = normalizza_anno(dati.get('anno_fabbricazione'))
        if anno is None:
            errori.append("Anno di fabbricazione non plausibile")
        puliti['anno_fabbricazione'] = anno
    else:
        puliti['anno_fabbricazione'] = None

    if _testo(dati, 'classificazione'):
        classe = normalizza_classificazione(dati.get('classificazione'))
        if classe is None:
            errori.append("Classificazione non riconosciuta (attese I, IIa, IIb, III)")
        puliti['classificazione'] = classe
    else:
        puliti['classificazione'] = None

    for campo, etichetta in (('data_acquisto', "Data di acquisto"),
                             ('garanzia_scadenza', "Data di scadenza garanzia")):
        if _testo(dati, campo):
            data = normalizza_data(dati.get(campo))
            if data is None:
                errori.append(etichetta + " non valida")
            puliti[campo] = data
        else:
            puliti[campo] = None

    return puliti, errori


def valida_manutenzione(dati):
    """Regole di `manutenzioni._validate_manutenzione` applicate ai dati AI."""
    errori = []
    puliti = dict(dati)

    data_intervento = normalizza_data(dati.get('data_intervento'))
    if not _testo(dati, 'data_intervento'):
        errori.append("Data dell'intervento assente")
    elif data_intervento is None:
        errori.append("Data dell'intervento non valida")
    puliti['data_intervento'] = data_intervento

    if _testo(dati, 'prossima_scadenza'):
        prossima = normalizza_data(dati.get('prossima_scadenza'))
        if prossima is None:
            errori.append("Data della prossima scadenza non valida")
        puliti['prossima_scadenza'] = prossima
    else:
        puliti['prossima_scadenza'] = None

    # Il tipo assente resta 'preventiva' come nel resto del programma; un tipo
    # dichiarato ma irriconoscibile e' invece un errore.
    if _testo(dati, 'tipo'):
        tipo = normalizza_tipo_manutenzione(dati.get('tipo'))
        if tipo is None:
            errori.append("Tipo di manutenzione non riconosciuto: " + _testo(dati, 'tipo'))
        puliti['tipo'] = tipo
    else:
        puliti['tipo'] = 'preventiva'

    if _testo(dati, 'periodicita_giorni'):
        periodicita = normalizza_periodicita(dati.get('periodicita_giorni'))
        if periodicita is None:
            errori.append(_errore_periodicita())
        puliti['periodicita_giorni'] = periodicita
    else:
        puliti['periodicita_giorni'] = None

    if _testo(dati, 'costo'):
        costo = normalizza_costo(dati.get('costo'))
        if costo is None:
            errori.append("Importo non valido")
        puliti['costo'] = costo
    else:
        puliti['costo'] = None

    return puliti, errori


def valida_verifica(dati):
    """Regole di `verifiche._validate_verifica` applicate ai dati AI.

    A differenza del form, la periodicita' assente prende il valore
    predefinito di due anni: e' il comportamento storico dell'import ed e'
    innocuo, perche' non afferma nulla sull'esito della verifica.
    """
    errori = []
    puliti = dict(dati)

    data_verifica = normalizza_data(dati.get('data_verifica'))
    if not _testo(dati, 'data_verifica'):
        errori.append("Data della verifica assente")
    elif data_verifica is None:
        errori.append("Data della verifica non valida")
    puliti['data_verifica'] = data_verifica

    esito = normalizza_esito_verifica(dati.get('esito'))
    if not _testo(dati, 'esito'):
        errori.append("Esito della verifica assente")
    elif esito is None:
        errori.append("Esito della verifica non riconosciuto: " + _testo(dati, 'esito'))
    puliti['esito'] = esito

    if _testo(dati, 'periodicita_giorni'):
        periodicita = normalizza_periodicita(dati.get('periodicita_giorni'))
        if periodicita is None:
            errori.append(_errore_periodicita())
        puliti['periodicita_giorni'] = periodicita
    else:
        puliti['periodicita_giorni'] = PERIODICITA_VERIFICA_DEFAULT

    if _testo(dati, 'prossima_scadenza'):
        prossima = normalizza_data(dati.get('prossima_scadenza'))
        if prossima is None:
            errori.append("Data della prossima scadenza non valida")
        puliti['prossima_scadenza'] = prossima
    else:
        puliti['prossima_scadenza'] = None

    return puliti, errori


def _errore_periodicita():
    return ("Periodicita' non plausibile (attesi da %d a %d giorni)"
            % (PERIODICITA_MIN, PERIODICITA_MAX))


def messaggio_errori(errori):
    """Riunisce gli errori in una riga sola per note_revisione e log."""
    return '; '.join(errori)
