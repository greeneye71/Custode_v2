"""Regole di dominio degli impianti.

Qui sta il calcolo, non la presentazione: nessun import di request o
render_template. Il blueprint valida i form e chiama queste funzioni; lo
scheduler chiama avvisi_da_inviare() e destinatari().
"""

import calendar
import logging
from datetime import date, datetime

from models import execute, query_all, query_one, transazione
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
    # M03: intervento e avanzamento del piano sono una cosa sola. Committarli
    # separatamente lascerebbe, se la seconda scrittura fallisce, un intervento
    # registrato su una scadenza ferma: la verifica risulta fatta e insieme
    # ancora dovuta, e nessuno se ne accorge finche' l'avviso non riparte.
    with transazione():
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


#: Soglie in ordine di gravita' crescente. Sono cumulative: una scadenza a 3
#: giorni ha superato sia 'anticipo' sia 'imminente'. Si invia solo la piu'
#: grave non ancora registrata, ma si registrano tutte quelle raggiunte —
#: altrimenti la soglia saltata partirebbe al giro dopo, fuori tempo massimo.
SOGLIE = ('anticipo', 'imminente', 'scaduto')


def _soglie_raggiunte(giorni_rimasti, giorni_anticipo):
    """Le soglie superate da una scadenza, dalla piu' lieve alla piu' grave.

    'scaduto' scatta a giorni_rimasti <= 0, mentre la vista classifica
    'scaduto' con < 0: il giorno stesso della scadenza la vista dice ancora
    'urgente', ma un avviso che parte il giorno dopo arriva tardi. La
    divergenza e' voluta.
    """
    raggiunte = []
    if giorni_rimasti <= giorni_anticipo:
        raggiunte.append('anticipo')
    if giorni_rimasti <= 7:
        raggiunte.append('imminente')
    if giorni_rimasti <= 0:
        raggiunte.append('scaduto')
        # Solleciti ogni 30 giorni finche' la verifica non viene registrata.
        # Trenta giorni e non un mese di calendario: il numero nella soglia
        # ('sollecito_2') e' un contatore di solleciti, non un conteggio di
        # mesi, e serve solo a distinguere un sollecito dal precedente dentro
        # impianti_avvisi_inviati.
        mesi = int(-giorni_rimasti) // 30
        if mesi >= 1:
            raggiunte.append(f'sollecito_{mesi}')
    return raggiunte


def avvisi_da_inviare(struttura_id):
    """Le scadenze della struttura che hanno un avviso da spedire.

    Un elemento per scadenza, con la soglia piu' grave non ancora registrata in
    impianti_avvisi_inviati. Le scadenze sospese e gli impianti dismessi sono
    gia' esclusi dalla vista.
    """
    righe = query_all(
        """SELECT v.*, d.nome as divisione_nome, d.email as divisione_email,
                  s.email_extra, s.avvisa_manutentore,
                  m.email as manutentore_email,
                  (SELECT MAX(i.data_intervento) FROM impianti_interventi i
                    WHERE i.scadenza_id = v.scadenza_id) as ultimo_intervento
           FROM prossime_scadenze_impianti v
           JOIN impianti_scadenze s ON s.id = v.scadenza_id
           JOIN impianti imp ON imp.id = v.impianto_id
           LEFT JOIN divisioni d ON d.id = v.divisione_id
           LEFT JOIN manutentori m ON m.id = imp.manutentore_id
           WHERE v.struttura_id = ?""",
        (struttura_id,)
    )

    avvisi = []
    for r in righe:
        raggiunte = _soglie_raggiunte(r['giorni_rimasti'], r['giorni_anticipo'])
        if not raggiunte:
            continue
        gia_inviate = {x['soglia'] for x in query_all(
            "SELECT soglia FROM impianti_avvisi_inviati"
            " WHERE scadenza_id = ? AND scadenza_target = ?",
            (r['scadenza_id'], r['prossima_scadenza']))}
        da_fare = [s for s in raggiunte if s not in gia_inviate]
        if not da_fare:
            continue
        avviso = dict(r)
        avviso['soglia'] = da_fare[-1]          # la piu' grave
        avviso['soglie_coperte'] = da_fare      # tutte quelle da registrare
        avvisi.append(avviso)
    return avvisi


def destinatari(struttura, avviso):
    """Gli indirizzi a cui spedire l'avviso, in cascata e senza doppioni.

    1) responsabile della struttura (o, in mancanza, l'indirizzo di notifica)
    2) email della divisione
    3) indirizzi extra della riga di piano (elenco separato da virgole)
    4) manutentore dell'impianto, se la riga lo prevede
    """
    elenco = []
    responsabile = (struttura['email_responsabile']
                    or struttura['email_notifiche'])
    for candidato in (responsabile, avviso.get('divisione_email')):
        if candidato:
            elenco.append(candidato)
    for pezzo in (avviso.get('email_extra') or '').split(','):
        if pezzo.strip():
            elenco.append(pezzo.strip())
    if avviso.get('avvisa_manutentore') and avviso.get('manutentore_email'):
        elenco.append(avviso['manutentore_email'])

    visti, puliti = set(), []
    for indirizzo in elenco:
        chiave = indirizzo.strip().lower()
        if chiave and chiave not in visti:
            visti.add(chiave)
            puliti.append(indirizzo.strip())
    return puliti


ETICHETTE_SOGLIA = {
    'anticipo': 'in scadenza',
    'imminente': 'in scadenza imminente',
    'scaduto': 'SCADUTA',
}


def corpo_avviso(struttura, avviso):
    """Oggetto e testo dell'avviso. L'oggetto nomina la struttura: il mittente
    e' lo stesso per tutte le strutture del deployment."""
    soglia = avviso['soglia']
    etichetta = (ETICHETTE_SOGLIA.get(soglia)
                 or f"SCADUTA — sollecito n. {soglia.rsplit('_', 1)[-1]}")
    oggetto = (f"[{struttura['nome']}] {avviso['impianto_nome']}: "
               f"{avviso['scadenza_nome']} {etichetta}")

    giorni = avviso['giorni_rimasti']
    quando = (f"mancano {giorni} giorni" if giorni > 0
              else f"scaduta da {-giorni} giorni" if giorni < 0
              else 'scade oggi')
    righe = [
        f"Struttura: {struttura['nome']}",
        f"Divisione: {avviso.get('divisione_nome') or '-'}",
        f"Impianto: {avviso['impianto_nome']}"
        + (f" ({avviso['ubicazione']})" if avviso.get('ubicazione') else ''),
        f"Verifica: {avviso['scadenza_nome']}",
        f"Riferimento: {avviso.get('riferimento_normativo') or '-'}",
        f"Scadenza: {avviso['prossima_scadenza']} ({quando})",
        f"Ultimo intervento registrato: {avviso.get('ultimo_intervento') or 'nessuno'}",
        "",
        "Messaggio automatico di MedInventory.",
    ]
    return oggetto, "\n".join(righe)


def registra_avviso(scadenza_id, soglia, scadenza_target, indirizzi):
    """Segna una soglia come inviata. Scritta solo dopo un invio riuscito:
    una riga scritta in anticipo trasformerebbe un errore SMTP in un avviso
    perso per sempre."""
    execute(
        """INSERT OR IGNORE INTO impianti_avvisi_inviati
           (scadenza_id, soglia, scadenza_target, destinatari)
           VALUES (?, ?, ?, ?)""",
        (scadenza_id, soglia, scadenza_target, ', '.join(indirizzi))
    )


def applica_catalogo(impianto_id, tipo, nomi_scelti, partenza):
    """Crea le righe di piano per le voci di catalogo scelte.

    'partenza' e' la data da cui contare la prima scadenza (di norma la data di
    creazione dell'impianto). Le voci non presenti in catalogo sono ignorate.
    Restituisce il numero di righe create.
    """
    scelti = {(n or '').strip().lower() for n in nomi_scelti}
    # Le voci gia' a piano si saltano qui, non solo nella rotta che chiama:
    # fino alla 2.7.1 un doppio invio del modulo (o due schede aperte)
    # duplicava le righe di scadenza, e da li' in poi ogni avviso partiva due
    # volte e il piano mostrava la stessa verifica ripetuta.
    gia_presenti = {(r['nome'] or '').strip().lower() for r in query_all(
        "SELECT nome FROM impianti_scadenze WHERE impianto_id = ?", (impianto_id,))}
    creati = 0
    for voce in voci_per_tipo(tipo):
        nome_norm = voce['nome'].strip().lower()
        if nome_norm not in scelti or nome_norm in gia_presenti:
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
