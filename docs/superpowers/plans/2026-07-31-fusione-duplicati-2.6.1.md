# Fusione di apparecchi duplicati — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fondere due schede che descrivono lo stesso apparecchio fisico, preservando manutenzioni, verifiche, documenti e accessori di entrambe.

**Architecture:** Un modulo `fusione_service.py` senza Flask, come `struttura_service.py` e `report_service.py`: una funzione pura che propone le coppie sospette e una primitiva transazionale che esegue la fusione su una `sqlite3.Connection`. Le rotte in `apparecchi.py` non contengono logica di fusione, solo autorizzazione, raccolta delle scelte e registrazione.

**Tech Stack:** Python 3.13, Flask 3.x, SQLite3, Jinja2, Bootstrap 5, pytest.

**Spec di riferimento:** `docs/superpowers/specs/2026-07-29-fusione-duplicati-2.7-design.md`

## Global Constraints

- **Punto di partenza:** `main` a `7988faa` (MedInventory 2.6.0), **194 test verdi**. Verificare con `python -m pytest tests/ -q` prima di iniziare.
- **Versione di rilascio: 2.6.1**, per scelta esplicita dell'utente. Nota lasciata a verbale: il progetto dichiara «Versioning basato su Semantic Versioning» e questa e' una funzione nuova con rotte nuove, quindi per convenzione sarebbe una 2.7.0. E' una riga da cambiare al Task 6 se l'utente cambia idea.
- **Lingua italiana** per interfaccia, commenti, nomi di variabili, valori di database e messaggi di commit.
- **`fusione_service.py` non importa Flask** e non deve mai importarlo: riceve connessioni e valori dal chiamante.
- **SQL parametrizzato**: sempre `?`. I nomi di colonna che finiscono in una f-string devono provenire da una costante del modulo, mai dal form.
- **CSRF**: ogni form POST porta `{{ csrf_token() }}`.
- **`log_attivita()`** da `models.py` per ogni fusione eseguita.
- **Isolamento fra strutture**: entrambe le schede devono passare `models.apparecchio_accessibile()`. Non esiste altro modo accettabile di raggiungere un apparecchio per id — la 2.6.0 e' uscita per chiudere nove rotte che ne avevano una copia inline.
- **MAI PowerShell `Get-Content`/`Set-Content` su file sorgente**: corrompe i caratteri tipografici e aggiunge un BOM. Solo gli strumenti di modifica dell'editor.
- **Ogni modifica a un test preesistente va dichiarata** nel rapporto di task, con il perche'.
- Non scrivere nel database, in `uploads/` o in `backups/` reali del repository durante le prove.

## Struttura dei file

| File | Responsabilita' |
|---|---|
| `fusione_service.py` (nuovo) | `candidati_duplicati()` (pura), `fondi_apparecchi()` (transazionale), le costanti dei campi e le eccezioni. Nessun Flask. |
| `apparecchi.py` (modifica) | Quattro rotte: elenco candidati, ricerca dell'altro apparecchio, pagina di confronto, esecuzione. Autorizzazione e registro. |
| `templates/apparecchi/duplicati.html` (nuovo) | Elenco delle coppie sospette, con il criterio che le ha proposte. |
| `templates/apparecchi/fondi.html` (nuovo) | Scelta della scheda che sopravvive, dei valori campo per campo, degli interventi da scartare. |
| `templates/apparecchi/dettaglio.html` (modifica) | Voce "Fondi con..." |
| `tests/test_fusione_service.py` (nuovo) | La funzione pura e la primitiva, su database temporanei. |
| `tests/test_fusione_routes.py` (nuovo) | Autorizzazione, isolamento, giro completo dalle rotte. |

## Nota sull'ordine delle operazioni — leggere prima di scrivere codice

`manutenzioni`, `verifiche`, `documenti` e `accessori` hanno
`FOREIGN KEY (apparecchio_id) REFERENCES apparecchi(id) **ON DELETE CASCADE**`
(`schema.sql:256, 277, 296, 317`). `import_preview.apparecchio_match_id`
(`schema.sql:367`) **non** ha `ON DELETE`, quindi blocca la cancellazione.

Due conseguenze che decidono l'ordine, e sbagliarlo distrugge dati in silenzio:

1. **I figli vanno spostati PRIMA di cancellare la scheda scartata.** Se si cancella
   prima, la cascata porta via proprio le manutenzioni e le verifiche che la fusione
   doveva salvare, e l'operazione riesce senza errori.
2. **I valori scelti vanno applicati DOPO la cancellazione della scartata.** Se la
   scheda che sopravvive prende la matricola dell'altra mentre l'altra esiste ancora,
   `UNIQUE(struttura_id, modello, matricola)` (`schema.sql:223`) rifiuta l'UPDATE.

Ordine corretto, ed e' quello che il Task 2 e il Task 3 costruiscono:

```
0. rifiuti (accessibilita', stessa struttura, collisione con un TERZO apparecchio)
1. DELETE degli interventi scartati esplicitamente
2. UPDATE dei figli: apparecchio_id -> principale
3. UPDATE import_preview.apparecchio_match_id -> principale
4. lettura completa della scheda scartata (per il registro), finche' esiste
5. DELETE della scheda scartata
6. UPDATE della scheda principale con i valori scelti
```

---

## Task 1: `candidati_duplicati`, la funzione pura

**Files:**
- Create: `fusione_service.py`
- Test: `tests/test_fusione_service.py`

**Interfaces:**
- Consumes: niente.
- Produces:
  - `fusione_service.normalizza_matricola(valore) -> str`
  - `fusione_service.Coppia` — `namedtuple('Coppia', 'a b criterio')`, dove `a` e `b` sono i dizionari ricevuti in ingresso e `criterio` e' una delle stringhe di `CRITERI`.
  - `fusione_service.CRITERI` — dizionario `chiave -> etichetta italiana`.
  - `fusione_service.candidati_duplicati(righe) -> list[Coppia]`

`righe` e' una lista di dizionari con almeno le chiavi `id`, `matricola`, `marca`,
`modello`, `ubicazione`. E' una funzione **pura**: nessuna query, nessun Flask. Chi la
chiama decide quali righe passarle (la rotta del Task 4 esclude i dismessi).

- [ ] **Step 1: Scrivere i test che falliscono**

Crea `tests/test_fusione_service.py`:

```python
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
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `python -m pytest tests/test_fusione_service.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'fusione_service'`.

- [ ] **Step 3: Scrivere `fusione_service.py`**

```python
"""
MedInventory - Fusione di apparecchi duplicati

Volutamente estraneo a Flask, come struttura_service.py: riceve connessioni e
valori dal chiamante. candidati_duplicati e' per giunta una funzione pura su
una lista di dizionari, quindi si prova con dieci righe in memoria invece che
con un database popolato.
"""

from collections import namedtuple


Coppia = namedtuple('Coppia', 'a b criterio')

# Dal piu' forte al piu' debole: l'ordine e' significativo, _criterio_coppia
# restituisce il primo che si applica.
CRITERI = {
    'matricola_equivalente': 'matricola identica a meno di trattini e maiuscole',
    'matricola_contenuta': 'una matricola e\' contenuta nell\'altra',
    'matricola_distanza_uno': 'stesso modello e ubicazione, matricole che '
                              'differiscono per un carattere',
}

# Sotto questa lunghezza il contenimento non si applica: '1' e' contenuto in
# '12', '13', '104' e cosi' via, e su un parco con matricole corte l'elenco
# proporrebbe tutto con tutto.
LUNGHEZZA_MINIMA_CONTENIMENTO = 4


def normalizza_matricola(valore):
    """Solo lettere e cifre, in maiuscolo. 'R-00015' e 'r 00015' diventano
    la stessa cosa, che e' il modo in cui lo stesso apparecchio viene
    trascritto due volte da due documenti diversi."""
    if not valore:
        return ''
    return ''.join(c for c in str(valore) if c.isalnum()).upper()


def _differisce_di_un_carattere(a, b):
    """True se una sola sostituzione, inserimento o cancellazione trasforma a
    in b. Non serve una distanza di Levenshtein completa: interessa solo il
    caso 1, e fermarsi li' rende la funzione leggibile e veloce."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        differenze = sum(1 for x, y in zip(a, b) if x != y)
        return differenze == 1
    lunga, corta = (a, b) if len(a) > len(b) else (b, a)
    for i in range(len(lunga)):
        if lunga[:i] + lunga[i + 1:] == corta:
            return True
    return False


def _criterio_coppia(a, b):
    """Il criterio piu' forte che si applica a due righe, o None."""
    ma = normalizza_matricola(a.get('matricola'))
    mb = normalizza_matricola(b.get('matricola'))
    if not ma or not mb:
        return None

    if ma == mb:
        return 'matricola_equivalente'

    if (len(ma) >= LUNGHEZZA_MINIMA_CONTENIMENTO
            and len(mb) >= LUNGHEZZA_MINIMA_CONTENIMENTO
            and (ma in mb or mb in ma)):
        return 'matricola_contenuta'

    # Il criterio piu' debole, e per questo il piu' vincolato: senza stesso
    # modello e stessa ubicazione proporrebbe ogni coppia di matricole
    # consecutive del parco.
    if (a.get('modello') == b.get('modello')
            and a.get('ubicazione') == b.get('ubicazione')
            and _differisce_di_un_carattere(ma, mb)):
        return 'matricola_distanza_uno'

    return None


def candidati_duplicati(righe):
    """Coppie di righe che potrebbero descrivere lo stesso apparecchio.

    Funzione pura: `righe` e' una lista di dizionari con almeno id, matricola,
    marca, modello e ubicazione. Chi chiama decide cosa passarle - la rotta
    esclude gli apparecchi dismessi.

    Il confronto gira in Python e non in SQL perche' SQLite non ha una
    distanza fra stringhe; su qualche migliaio di apparecchi il costo e'
    trascurabile.

    Propone, non decide: due macchine gemelle comprate insieme ('MON-1' e
    'MON-2' nella stessa sala) hanno la stessa forma di un errore di
    battitura, e nessun criterio automatico puo' distinguerle. Per questo
    ogni coppia porta il criterio che l'ha proposta.
    """
    trovate = []
    for i in range(len(righe)):
        for j in range(i + 1, len(righe)):
            criterio = _criterio_coppia(righe[i], righe[j])
            if criterio:
                trovate.append(Coppia(righe[i], righe[j], criterio))
    return trovate
```

- [ ] **Step 4: Eseguire i test**

Run: `python -m pytest tests/test_fusione_service.py -q`
Expected: PASS, 10 test.

- [ ] **Step 5: Provare la sensibilita' del vincolo sul criterio debole**

In `_criterio_coppia`, togli temporaneamente le due condizioni
`a.get('modello') == b.get('modello')` e
`a.get('ubicazione') == b.get('ubicazione')`, poi esegui
`python -m pytest tests/test_fusione_service.py -q`: deve fallire
`test_distanza_uno_non_basta_se_il_modello_e_diverso`. Rimetti a posto e
verifica con `git diff` che il file sia tornato identico.

Poi la stessa cosa con `LUNGHEZZA_MINIMA_CONTENIMENTO = 1`: deve fallire
`test_matricole_corte_non_si_propongono_per_contenimento`.

- [ ] **Step 6: Commit**

```bash
git add fusione_service.py tests/test_fusione_service.py
git commit -m "feat(fusione): criteri per proporre apparecchi duplicati"
```

---

## Task 2: `fondi_apparecchi`, il trasferimento dei figli

**Files:**
- Modify: `fusione_service.py`
- Test: `tests/test_fusione_service.py`

**Interfaces:**
- Consumes: niente del Task 1 (stesso file, funzioni indipendenti).
- Produces:
  - `fusione_service.TABELLE_FIGLIE` — `(('manutenzioni', 'apparecchio_id'), ('verifiche', 'apparecchio_id'), ('documenti', 'apparecchio_id'), ('accessori', 'apparecchio_id'))`
  - `fusione_service.fondi_apparecchi(conn, id_principale, id_scartato, valori=None, interventi_scartati=()) -> dict`
    Chiavi del risultato: `manutenzioni`, `verifiche`, `documenti`, `accessori`,
    `preview`, `interventi_scartati`, `scartato` (dizionario completo della riga
    cancellata), `valori_scelti` (lista di nomi di colonna).

Il chiamante apre e chiude la transazione, come fa `struttura_service.rimuovi_strutture`.
In questo task `valori` e `interventi_scartati` sono accettati ma non usati: li
implementa il Task 3. **Non anticiparli.**

- [ ] **Step 1: Scrivere i test che falliscono**

Aggiungi in coda a `tests/test_fusione_service.py`:

```python
@pytest.fixture
def conn(tmp_path):
    """Due apparecchi duplicati della stessa struttura, ciascuno con la sua
    storia, piu' un terzo apparecchio che non deve essere toccato."""
    percorso = str(tmp_path / 'prova.db')
    con = sqlite3.connect(percorso)
    con.row_factory = sqlite3.Row
    with open(os.path.join(RADICE, 'schema.sql'), encoding='utf-8') as f:
        con.executescript(f.read())
    con.execute("PRAGMA foreign_keys = ON")

    s = con.execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
    d = con.execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Oculistica','OCU',?)",
                    (s,)).lastrowid
    ids = {}
    for etichetta, matricola, anno, note in (
            ('principale', 'R-00015', None, None),
            ('scartato', 'R00015', 2019, 'Rev. 2024'),
            ('terzo', 'ALTRO-1', None, None)):
        ids[etichetta] = con.execute(
            "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,"
            "stato,ubicazione,anno_fabbricazione,note) "
            "VALUES (?,?,?,'REXXAM','OZY','funzionante','Sala 1',?,?)",
            (d, s, matricola, anno, note)).lastrowid

    for etichetta, quante in (('principale', 3), ('scartato', 1)):
        for n in range(quante):
            con.execute(
                "INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento,verbale_path) "
                "VALUES (?,'preventiva','2026-03-12',?)",
                (ids[etichetta], f'strutture/{s}/verbali/{etichetta}{n}.pdf'))
    for etichetta, quante in (('principale', 1), ('scartato', 2)):
        for n in range(quante):
            con.execute(
                "INSERT INTO verifiche (apparecchio_id,data_verifica,esito,documento_path) "
                "VALUES (?,'2025-11-08','positivo',?)",
                (ids[etichetta], f'strutture/{s}/verifiche/{etichetta}{n}.pdf'))
    con.execute("INSERT INTO documenti (apparecchio_id,tipo,filename,filepath) "
                "VALUES (?,'manuale','m.pdf','x/m.pdf')", (ids['scartato'],))
    con.execute("INSERT INTO accessori (apparecchio_id,descrizione) VALUES (?,'Sonda')",
                (ids['scartato'],))
    # Un apparecchio del terzo, per dimostrare che non viene toccato
    con.execute("INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento) "
                "VALUES (?,'correttiva','2026-01-01')", (ids['terzo'],))
    con.commit()
    return con, ids, s


def conta(con, tabella, apparecchio_id):
    return con.execute(
        f"SELECT COUNT(*) FROM {tabella} WHERE apparecchio_id = ?",
        (apparecchio_id,)).fetchone()[0]


def test_la_fusione_somma_gli_interventi_delle_due_schede(conn):
    """L'unica asserzione che distingue "ha fuso" da "ha fuso senza perdere
    niente". I figli hanno ON DELETE CASCADE: cancellare la scheda scartata
    prima di spostarli li porta via, e l'operazione riesce lo stesso."""
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    esito = fondi_apparecchi(con, ids['principale'], ids['scartato'])
    con.commit()

    assert conta(con, 'manutenzioni', ids['principale']) == 4   # 3 + 1
    assert conta(con, 'verifiche', ids['principale']) == 3      # 1 + 2
    assert conta(con, 'documenti', ids['principale']) == 1
    assert conta(con, 'accessori', ids['principale']) == 1
    assert esito['manutenzioni'] == 1    # quante ne ha SPOSTATE
    assert esito['verifiche'] == 2
    assert esito['documenti'] == 1
    assert esito['accessori'] == 1


def test_la_scheda_scartata_sparisce_e_la_principale_conserva_il_proprio_id(conn):
    """L'id della principale non cambia: i QR code stampati e attaccati
    sull'apparecchio restano validi. E' il motivo per cui la scelta di quale
    scheda sopravvive non e' indifferente."""
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    fondi_apparecchi(con, ids['principale'], ids['scartato'])
    con.commit()

    assert con.execute("SELECT COUNT(*) FROM apparecchi WHERE id=?",
                       (ids['scartato'],)).fetchone()[0] == 0
    riga = con.execute("SELECT id, matricola FROM apparecchi WHERE id=?",
                       (ids['principale'],)).fetchone()
    assert riga is not None and riga['matricola'] == 'R-00015'


def test_un_terzo_apparecchio_non_viene_toccato(conn):
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    fondi_apparecchi(con, ids['principale'], ids['scartato'])
    con.commit()
    assert conta(con, 'manutenzioni', ids['terzo']) == 1
    assert con.execute("SELECT COUNT(*) FROM apparecchi WHERE id=?",
                       (ids['terzo'],)).fetchone()[0] == 1


def test_nessun_allegato_resta_orfano(conn):
    """Nessun file si sposta: gli allegati stanno in
    uploads/strutture/<id>/<tipo>/, non in cartelle per apparecchio, quindi
    fondere due schede della stessa struttura cambia solo la riga che li
    referenzia. Il test lo inchioda: i percorsi devono essere ancora tutti
    li', invariati."""
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    prima = {r[0] for r in con.execute("SELECT verbale_path FROM manutenzioni")}
    prima |= {r[0] for r in con.execute("SELECT documento_path FROM verifiche")}

    fondi_apparecchi(con, ids['principale'], ids['scartato'])
    con.commit()

    dopo = {r[0] for r in con.execute("SELECT verbale_path FROM manutenzioni")}
    dopo |= {r[0] for r in con.execute("SELECT documento_path FROM verifiche")}
    assert dopo == prima


def test_i_riferimenti_di_import_preview_seguono_la_scheda_principale(conn):
    """import_preview.apparecchio_match_id non ha ON DELETE: se non lo si
    sposta, la cancellazione della scheda scartata fallisce con un errore di
    chiave esterna e l'intera fusione si annulla."""
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    imp = con.execute(
        "INSERT INTO import_history (struttura_id,tipo_import,filename,filepath) "
        "VALUES (?,'inventario','x.xlsx','x/x.xlsx')", (_s,)).lastrowid
    con.execute("INSERT INTO import_preview (import_id,riga_numero,dati_estratti,"
                "apparecchio_match_id) VALUES (?,1,'{}',?)", (imp, ids['scartato']))
    con.commit()

    esito = fondi_apparecchi(con, ids['principale'], ids['scartato'])
    con.commit()

    assert esito['preview'] == 1
    assert con.execute("SELECT apparecchio_match_id FROM import_preview").fetchone()[0] \
        == ids['principale']


def test_la_scheda_scartata_viene_restituita_per_intero(conn):
    """Il registro deve poter contenere la scheda cancellata campo per campo,
    cosi' ricostruirla a mano resta possibile: la fusione e' definitiva."""
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    esito = fondi_apparecchi(con, ids['principale'], ids['scartato'])
    con.commit()

    scartato = esito['scartato']
    assert scartato['matricola'] == 'R00015'
    assert scartato['anno_fabbricazione'] == 2019
    assert scartato['note'] == 'Rev. 2024'
    assert scartato['modello'] == 'OZY'


def test_fondere_una_scheda_con_se_stessa_e_rifiutato(conn):
    from fusione_service import fondi_apparecchi, FusioneRifiutataError
    con, ids, _s = conn
    with pytest.raises(FusioneRifiutataError):
        fondi_apparecchi(con, ids['principale'], ids['principale'])


def test_fondere_fra_strutture_diverse_e_rifiutato(conn):
    """La rotta controlla gia' l'accessibilita' di entrambe, ma la primitiva
    non deve dipendere dal fatto che il suo unico chiamante odierno lo faccia:
    e' l'ultima difesa dell'isolamento fra strutture."""
    from fusione_service import fondi_apparecchi, FusioneRifiutataError
    con, ids, _s = conn
    altra = con.execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica B','B',1)").lastrowid
    div = con.execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Cardio','CAR',?)",
                      (altra,)).lastrowid
    estraneo = con.execute(
        "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato) "
        "VALUES (?,?,'B-1','SIEMENS','Y1','funzionante')", (div, altra)).lastrowid
    con.commit()

    with pytest.raises(FusioneRifiutataError):
        fondi_apparecchi(con, ids['principale'], estraneo)
    assert con.execute("SELECT COUNT(*) FROM apparecchi WHERE id=?",
                       (estraneo,)).fetchone()[0] == 1
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `python -m pytest tests/test_fusione_service.py -q`
Expected: FAIL, `ImportError: cannot import name 'fondi_apparecchi'`.

- [ ] **Step 3: Implementare `fondi_apparecchi`**

Aggiungi in coda a `fusione_service.py`:

```python
TABELLE_FIGLIE = (
    ('manutenzioni', 'apparecchio_id'),
    ('verifiche', 'apparecchio_id'),
    ('documenti', 'apparecchio_id'),
    ('accessori', 'apparecchio_id'),
)


class FusioneRifiutataError(Exception):
    """La fusione non puo' essere eseguita: le due schede non sono fondibili
    (stessa scheda, struttura diversa, una delle due non esiste)."""


def fondi_apparecchi(conn, id_principale, id_scartato, valori=None,
                     interventi_scartati=()):
    """Fonde la scheda scartata dentro la principale, che conserva il proprio id.

    Il chiamante apre e chiude la transazione, come per
    struttura_service.rimuovi_strutture: la rotta la vuole tutta in una, e un
    test la vuole poter annullare.

    L'ORDINE delle operazioni non e' una preferenza di stile.
    manutenzioni, verifiche, documenti e accessori hanno ON DELETE CASCADE
    verso apparecchi: se si cancella la scheda scartata prima di spostarli, la
    cascata porta via proprio i dati che la fusione doveva salvare, e
    l'operazione riesce senza errori. E import_preview.apparecchio_match_id non
    ha ON DELETE affatto, quindi bloccherebbe la cancellazione.

    Restituisce i conteggi di cio' che ha spostato, la scheda scartata per
    intero (il registro la conserva campo per campo: la fusione e' definitiva)
    e l'elenco dei valori scelti dalla scartata.
    """
    if id_principale == id_scartato:
        raise FusioneRifiutataError(
            "La scheda principale e quella da fondere sono la stessa.")

    principale = conn.execute(
        "SELECT * FROM apparecchi WHERE id = ?", (id_principale,)).fetchone()
    scartato = conn.execute(
        "SELECT * FROM apparecchi WHERE id = ?", (id_scartato,)).fetchone()
    if principale is None or scartato is None:
        raise FusioneRifiutataError("Una delle due schede non esiste.")
    if principale['struttura_id'] != scartato['struttura_id']:
        raise FusioneRifiutataError(
            "Le due schede appartengono a strutture diverse.")

    esito = {'manutenzioni': 0, 'verifiche': 0, 'documenti': 0, 'accessori': 0,
             'preview': 0, 'interventi_scartati': 0, 'valori_scelti': []}

    # 1-2. I figli si spostano PRIMA di qualunque cancellazione.
    for tabella, colonna in TABELLE_FIGLIE:
        cur = conn.execute(
            f"UPDATE {tabella} SET {colonna} = ? WHERE {colonna} = ?",
            (id_principale, id_scartato))
        esito[tabella] = cur.rowcount

    # 3. import_preview: nessun ON DELETE, bloccherebbe la cancellazione.
    cur = conn.execute(
        "UPDATE import_preview SET apparecchio_match_id = ? "
        "WHERE apparecchio_match_id = ?", (id_principale, id_scartato))
    esito['preview'] = cur.rowcount

    # 4. La scheda scartata va letta finche' esiste.
    esito['scartato'] = dict(scartato)

    # 5. Ora la cancellazione non porta via nulla: non ha piu' figli.
    conn.execute("DELETE FROM apparecchi WHERE id = ?", (id_scartato,))

    return esito
```

- [ ] **Step 4: Eseguire i test**

Run: `python -m pytest tests/test_fusione_service.py -q`
Expected: PASS, 18 test.

- [ ] **Step 5: Provare la sensibilita' dell'ordine, che e' il punto del task**

Sposta la riga `conn.execute("DELETE FROM apparecchi WHERE id = ?", ...)` **prima**
del ciclo su `TABELLE_FIGLIE`, poi esegui
`python -m pytest tests/test_fusione_service.py -q`.

Deve fallire `test_la_fusione_somma_gli_interventi_delle_due_schede` con un
conteggio di 3 manutenzioni invece di 4: e' la cascata che ha portato via
l'intervento della scheda scartata. Riporta la riga al suo posto e verifica con
`git diff` che il file sia tornato identico.

Se il test **non** fallisce, fermati e segnalalo: significa che
`PRAGMA foreign_keys` non e' attivo nella fixture e il test non sta provando
quello che dichiara.

- [ ] **Step 6: Commit**

```bash
git add fusione_service.py tests/test_fusione_service.py
git commit -m "feat(fusione): trasferimento degli interventi fra due schede"
```

---

## Task 3: valori campo per campo, interventi scartati, rifiuto su collisione

**Files:**
- Modify: `fusione_service.py`
- Test: `tests/test_fusione_service.py`

**Interfaces:**
- Consumes: `fondi_apparecchi` del Task 2.
- Produces:
  - `fusione_service.CAMPI_FONDIBILI` — tupla dei nomi di colonna che l'interfaccia
    puo' far scegliere.
  - `fusione_service.FusioneCollisioneError(FusioneRifiutataError)` con attributo
    `.altro` (dizionario dell'apparecchio che collide).
  - `fusione_service.valori_predefiniti(principale, scartato) -> dict` — per ogni campo
    diverso fra le due schede, il valore da preselezionare.
  - `fondi_apparecchi(..., valori=dict, interventi_scartati=[('manutenzione'|'verifica', id)])`
    ora onora entrambi i parametri.

- [ ] **Step 1: Scrivere i test che falliscono**

Aggiungi in coda a `tests/test_fusione_service.py`:

```python
def test_i_valori_scelti_finiscono_sulla_scheda_principale(conn):
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    esito = fondi_apparecchi(con, ids['principale'], ids['scartato'],
                             valori={'anno_fabbricazione': 2019, 'note': 'Rev. 2024'})
    con.commit()

    riga = con.execute("SELECT anno_fabbricazione, note, matricola FROM apparecchi WHERE id=?",
                       (ids['principale'],)).fetchone()
    assert riga['anno_fabbricazione'] == 2019
    assert riga['note'] == 'Rev. 2024'
    assert riga['matricola'] == 'R-00015'          # non scelta: resta la sua
    assert sorted(esito['valori_scelti']) == ['anno_fabbricazione', 'note']


def test_la_principale_puo_prendere_la_matricola_della_scartata(conn):
    """UNIQUE(struttura_id, modello, matricola) rifiuterebbe l'UPDATE finche'
    la scheda scartata esiste: i valori vanno applicati DOPO la cancellazione.
    E' il caso ordinario in cui si tiene la scheda con lo storico piu' lungo
    ma la matricola corretta e' quella dell'altra."""
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    fondi_apparecchi(con, ids['principale'], ids['scartato'],
                     valori={'matricola': 'R00015'})
    con.commit()
    assert con.execute("SELECT matricola FROM apparecchi WHERE id=?",
                       (ids['principale'],)).fetchone()[0] == 'R00015'


def test_un_campo_non_fondibile_viene_rifiutato(conn):
    """valori arriva da un form. I nomi di colonna finiscono in una f-string,
    quindi l'elenco dei campi ammessi e' una costante del modulo e non
    qualcosa che il chiamante puo' estendere."""
    from fusione_service import fondi_apparecchi, FusioneRifiutataError
    con, ids, _s = conn
    with pytest.raises(FusioneRifiutataError):
        fondi_apparecchi(con, ids['principale'], ids['scartato'],
                         valori={'struttura_id': 999})
    with pytest.raises(FusioneRifiutataError):
        fondi_apparecchi(con, ids['principale'], ids['scartato'],
                         valori={'id': 1})


def test_collisione_con_un_terzo_apparecchio_rifiuta_e_lo_nomina(conn):
    """Meglio un rifiuto che nomina il terzo apparecchio di un errore di
    database: chi legge deve capire che esiste gia' una scheda con quella
    matricola, e quale."""
    from fusione_service import fondi_apparecchi, FusioneCollisioneError
    con, ids, _s = conn
    con.execute("UPDATE apparecchi SET matricola='COLLIDE' WHERE id=?", (ids['terzo'],))
    con.commit()

    with pytest.raises(FusioneCollisioneError) as errore:
        fondi_apparecchi(con, ids['principale'], ids['scartato'],
                         valori={'matricola': 'COLLIDE'})
    assert errore.value.altro['id'] == ids['terzo']
    # E niente e' stato toccato: il rifiuto precede ogni scrittura.
    assert con.execute("SELECT COUNT(*) FROM apparecchi WHERE id=?",
                       (ids['scartato'],)).fetchone()[0] == 1
    assert conta(con, 'manutenzioni', ids['scartato']) == 1


def test_un_intervento_scartato_non_confluisce(conn):
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    da_scartare = con.execute(
        "SELECT id FROM verifiche WHERE apparecchio_id=? LIMIT 1",
        (ids['scartato'],)).fetchone()[0]

    esito = fondi_apparecchi(con, ids['principale'], ids['scartato'],
                             interventi_scartati=[('verifica', da_scartare)])
    con.commit()

    assert esito['interventi_scartati'] == 1
    assert conta(con, 'verifiche', ids['principale']) == 2      # 1 + 2 - 1
    assert con.execute("SELECT COUNT(*) FROM verifiche WHERE id=?",
                       (da_scartare,)).fetchone()[0] == 0


def test_si_puo_scartare_anche_un_intervento_della_scheda_principale(conn):
    """La coppia duplicata ha una copia su ciascuna scheda: chi conferma
    sceglie quale delle due buttare, e puo' benissimo essere quella della
    scheda che sopravvive."""
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    da_scartare = con.execute(
        "SELECT id FROM manutenzioni WHERE apparecchio_id=? LIMIT 1",
        (ids['principale'],)).fetchone()[0]

    fondi_apparecchi(con, ids['principale'], ids['scartato'],
                     interventi_scartati=[('manutenzione', da_scartare)])
    con.commit()
    assert conta(con, 'manutenzioni', ids['principale']) == 3    # 3 + 1 - 1


def test_un_intervento_di_un_terzo_apparecchio_non_si_puo_scartare(conn):
    """interventi_scartati arriva da un form: un id qualunque non deve poter
    cancellare l'intervento di un apparecchio che non c'entra."""
    from fusione_service import fondi_apparecchi, FusioneRifiutataError
    con, ids, _s = conn
    estraneo = con.execute(
        "SELECT id FROM manutenzioni WHERE apparecchio_id=?",
        (ids['terzo'],)).fetchone()[0]

    with pytest.raises(FusioneRifiutataError):
        fondi_apparecchi(con, ids['principale'], ids['scartato'],
                         interventi_scartati=[('manutenzione', estraneo)])
    assert conta(con, 'manutenzioni', ids['terzo']) == 1


def test_valori_predefiniti_tiene_la_principale_tranne_dove_e_vuota(conn):
    """Nel caso comune basta confermare, e non si perde il dato che solo la
    scheda scartata aveva."""
    from fusione_service import valori_predefiniti
    con, ids, _s = conn
    p = dict(con.execute("SELECT * FROM apparecchi WHERE id=?", (ids['principale'],)).fetchone())
    s = dict(con.execute("SELECT * FROM apparecchi WHERE id=?", (ids['scartato'],)).fetchone())

    predefiniti = valori_predefiniti(p, s)
    assert predefiniti['matricola'] == 'R-00015'      # la principale ha un valore
    assert predefiniti['anno_fabbricazione'] == 2019  # la principale e' vuota
    assert predefiniti['note'] == 'Rev. 2024'         # idem
    assert 'marca' not in predefiniti                 # identici: non si sceglie
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `python -m pytest tests/test_fusione_service.py -q`
Expected: FAIL, `ImportError: cannot import name 'valori_predefiniti'`.

- [ ] **Step 3: Implementare**

In `fusione_service.py`, aggiungi dopo `TABELLE_FIGLIE`:

```python
# I campi che l'interfaccia puo' far scegliere. Elenco chiuso e non derivato
# dallo schema: i nomi finiscono in una f-string di UPDATE e arrivano da un
# form. struttura_id, divisione_id, id e le colonne di audit non ci sono
# apposta - spostare un apparecchio di struttura e' un'altra funzione, con i
# suoi casi limite e i suoi file da spostare.
CAMPI_FONDIBILI = (
    'descrizione', 'matricola', 'numero_inventario', 'marca', 'modello',
    'anno_fabbricazione', 'classificazione', 'ubicazione', 'stato',
    'connesso_rete', 'ip_address', 'mac_address', 'hostname', 'porta',
    'protocollo', 'url_interfaccia', 'fornitore', 'codice_fornitore',
    'garanzia_scadenza', 'contratto_manutenzione', 'note', 'foto_path',
)

# Le due tabelle di interventi che la pagina di conferma puo' far scartare.
TABELLE_INTERVENTO = {'manutenzione': 'manutenzioni', 'verifica': 'verifiche'}
```

e dopo `FusioneRifiutataError`:

```python
class FusioneCollisioneError(FusioneRifiutataError):
    """I valori scelti farebbero collidere la scheda risultante con un TERZO
    apparecchio, per UNIQUE(struttura_id, modello, matricola). L'attributo
    .altro contiene quell'apparecchio, perche' il messaggio possa nominarlo
    invece di riportare un errore di database."""

    def __init__(self, messaggio, altro):
        super().__init__(messaggio)
        self.altro = altro


def valori_predefiniti(principale, scartato):
    """Per ogni campo in cui le due schede differiscono, il valore da
    preselezionare: quello della principale, TRANNE dove la principale e'
    vuota e la scartata ha un valore.

    Nel caso comune basta confermare, e non si perde il dato che solo la
    scheda scartata aveva - che e' il motivo per cui la fusione esiste.
    """
    predefiniti = {}
    for campo in CAMPI_FONDIBILI:
        a, b = principale.get(campo), scartato.get(campo)
        if a == b:
            continue
        vuoto_a = a is None or a == ''
        predefiniti[campo] = b if vuoto_a else a
    return predefiniti
```

Poi, dentro `fondi_apparecchi`, **subito dopo** il controllo sulla struttura e
**prima** di qualunque scrittura:

```python
    valori = dict(valori or {})
    sconosciuti = [c for c in valori if c not in CAMPI_FONDIBILI]
    if sconosciuti:
        raise FusioneRifiutataError(
            f"Campi non fondibili: {', '.join(sorted(sconosciuti))}.")

    for tipo, _id in interventi_scartati:
        if tipo not in TABELLE_INTERVENTO:
            raise FusioneRifiutataError(f"Tipo di intervento sconosciuto: {tipo}.")

    # Gli interventi da scartare devono appartenere a una delle due schede in
    # fusione: l'elenco arriva da un form, e un id qualunque non deve poter
    # cancellare l'intervento di un apparecchio che non c'entra.
    for tipo, id_intervento in interventi_scartati:
        tabella = TABELLE_INTERVENTO[tipo]
        proprietario = conn.execute(
            f"SELECT apparecchio_id FROM {tabella} WHERE id = ?",
            (id_intervento,)).fetchone()
        if proprietario is None or proprietario[0] not in (id_principale, id_scartato):
            raise FusioneRifiutataError(
                f"L'intervento {tipo} {id_intervento} non appartiene a nessuna "
                f"delle due schede in fusione.")

    # Collisione con un TERZO apparecchio, verificata prima di scrivere
    # qualunque cosa: un rifiuto a meta' lascerebbe le due schede in uno stato
    # peggiore di quello di partenza.
    modello_finale = valori.get('modello', principale['modello'])
    matricola_finale = valori.get('matricola', principale['matricola'])
    altro = conn.execute(
        "SELECT * FROM apparecchi WHERE struttura_id = ? AND modello = ? "
        "AND matricola = ? AND id NOT IN (?, ?)",
        (principale['struttura_id'], modello_finale, matricola_finale,
         id_principale, id_scartato)).fetchone()
    if altro is not None:
        raise FusioneCollisioneError(
            f"Esiste gia' un altro apparecchio con modello \"{modello_finale}\" e "
            f"matricola \"{matricola_finale}\" in questa struttura "
            f"(id {altro['id']}). La fusione non e' stata eseguita.",
            dict(altro))
```

Poi, **prima** del ciclo su `TABELLE_FIGLIE`, la cancellazione degli interventi scelti:

```python
    for tipo, id_intervento in interventi_scartati:
        conn.execute(f"DELETE FROM {TABELLE_INTERVENTO[tipo]} WHERE id = ?",
                     (id_intervento,))
        esito['interventi_scartati'] += 1
```

Infine, **dopo** `DELETE FROM apparecchi`, l'applicazione dei valori:

```python
    # Dopo la cancellazione, non prima: se la principale prende la matricola
    # della scartata mentre la scartata esiste ancora,
    # UNIQUE(struttura_id, modello, matricola) rifiuta l'UPDATE.
    if valori:
        assegnazioni = ', '.join(f"{c} = ?" for c in valori)
        conn.execute(
            f"UPDATE apparecchi SET {assegnazioni} WHERE id = ?",
            list(valori.values()) + [id_principale])
        esito['valori_scelti'] = list(valori)
```

- [ ] **Step 4: Eseguire i test**

Run: `python -m pytest tests/test_fusione_service.py -q`
Expected: PASS, 26 test.

- [ ] **Step 5: Provare la sensibilita' dei due rifiuti e dell'ordine dei valori**

Tre prove, ognuna seguita da `git checkout -- fusione_service.py` e da un
`git diff` che deve risultare vuoto:

1. Togli il controllo sui campi sconosciuti: deve fallire
   `test_un_campo_non_fondibile_viene_rifiutato`.
2. Togli il controllo di proprieta' degli interventi: deve fallire
   `test_un_intervento_di_un_terzo_apparecchio_non_si_puo_scartare`.
3. Sposta l'UPDATE dei valori **prima** del `DELETE FROM apparecchi`: deve
   fallire `test_la_principale_puo_prendere_la_matricola_della_scartata` con un
   `IntegrityError: UNIQUE constraint failed`.

- [ ] **Step 6: Commit**

```bash
git add fusione_service.py tests/test_fusione_service.py
git commit -m "feat(fusione): valori campo per campo, interventi scartati e rifiuti"
```

---

## Task 4: l'elenco dei candidati

**Files:**
- Modify: `apparecchi.py`
- Create: `templates/apparecchi/duplicati.html`
- Test: `tests/test_fusione_routes.py`

**Interfaces:**
- Consumes: `fusione_service.candidati_duplicati`, `fusione_service.CRITERI`.
- Produces: la rotta `apparecchi.duplicati` (`GET /apparecchi/duplicati`).

- [ ] **Step 1: Scrivere i test che falliscono**

Crea `tests/test_fusione_routes.py`:

```python
"""Le rotte della fusione: chi puo' arrivarci e cosa vede."""
import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def dati(app):
    """Una struttura con due schede duplicate e un utente per ogni ruolo che
    conta. La struttura B esiste per dimostrare l'isolamento."""
    from models import execute
    with app.app_context():
        a = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        b = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica B','B',1)").lastrowid
        da = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Oculistica','OCU',?)",
                     (a,)).lastrowid
        db_ = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Cardiologia','CAR',?)",
                      (b,)).lastrowid
        hash_pw = generate_password_hash('Passw0rd!')
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
                "VALUES ('admin@a.it',?,'A','A','admin',?,0)", (hash_pw, a))
        semplice = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('utente@a.it',?,'U','U','utente',?,0)", (hash_pw, a)).lastrowid
        execute("INSERT INTO utenti_divisioni (utente_id,divisione_id,ruolo_divisione) "
                "VALUES (?,?,'utente')", (semplice, da))

        uno = execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,"
                      "stato,ubicazione) VALUES (?,?,'R-00015','REXXAM','OZY','funzionante','Sala 1')",
                      (da, a)).lastrowid
        due = execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,"
                      "stato,ubicazione) VALUES (?,?,'R00015','REXXAM','OZY','funzionante','Sala 1')",
                      (da, a)).lastrowid
        estraneo = execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,"
                           "modello,stato) VALUES (?,?,'SEGRETO-B','SIEMENS','Y1','funzionante')",
                           (db_, b)).lastrowid
    return {'a': a, 'b': b, 'div_a': da, 'div_b': db_,
            'uno': uno, 'due': due, 'estraneo': estraneo}


def entra(client, email):
    client.post('/login', data={'email': email, 'password': 'Passw0rd!'})


def test_l_elenco_propone_la_coppia_duplicata(client, dati):
    entra(client, 'admin@a.it')
    risposta = client.get('/apparecchi/duplicati')
    assert risposta.status_code == 200
    testo = risposta.get_data(as_text=True)
    assert 'R-00015' in testo
    assert 'R00015' in testo


def test_l_elenco_non_mostra_apparecchi_di_altre_strutture(client, app, dati):
    """L'elenco confronta gli apparecchi fra loro: senza filtro di struttura
    proporrebbe coppie fra tenant diversi, e la pagina stessa mostrerebbe
    matricole altrui."""
    from models import execute
    with app.app_context():
        # Un duplicato PERFETTO nell'altra struttura: se il filtro manca,
        # la coppia compare di sicuro.
        execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,"
                "stato,ubicazione) VALUES (?,?,'SEGRETO-B','SIEMENS','Y1','funzionante','X')",
                (dati['div_b'], dati['b']))
    entra(client, 'admin@a.it')
    testo = client.get('/apparecchi/duplicati').get_data(as_text=True)
    assert 'SEGRETO-B' not in testo


def test_l_elenco_e_negato_a_un_utente_semplice(client, dati):
    """La fusione cancella una scheda, e un utente non puo' nemmeno
    dismetterne una."""
    entra(client, 'utente@a.it')
    risposta = client.get('/apparecchi/duplicati', follow_redirects=True)
    assert 'R00015' not in risposta.get_data(as_text=True)


def test_l_elenco_dice_perche_propone_la_coppia(client, dati):
    """Chi guarda deve sapere quale criterio l'ha proposta: e' cio' che
    distingue una corrispondenza certa da una somiglianza da verificare."""
    entra(client, 'admin@a.it')
    testo = client.get('/apparecchi/duplicati').get_data(as_text=True)
    assert 'matricola identica a meno di trattini' in testo
```

**Nota per l'implementatore:** se un test di questo file dovesse fallire, la
regola e' **correggere la fixture, non l'asserzione**. In questo progetto tre
test si sono gia' rivelati ciechi perche' qualcuno aveva adattato
l'aspettativa al comportamento invece del contrario.

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `python -m pytest tests/test_fusione_routes.py -q`
Expected: FAIL, 404 sulla rotta inesistente.

- [ ] **Step 3: Aggiungere la rotta**

In `apparecchi.py`, aggiungi `from fusione_service import ...` **dentro** la funzione
(come fanno le rotte di `strutture_bp.py`), e la rotta dopo `lista()`:

```python
@apparecchi_bp.route('/apparecchi/duplicati')
@login_required
def duplicati():
    """Coppie di schede che potrebbero descrivere lo stesso apparecchio."""
    from fusione_service import candidati_duplicati, CRITERI

    if g.user['ruolo'] not in ('admin', 'superadmin', 'tecnico'):
        flash('Non autorizzato.', 'danger')
        return redirect(url_for('apparecchi.lista'))

    div_clause, div_params = filtro_divisione()
    righe = query_all(
        f"""SELECT a.id, a.matricola, a.marca, a.modello, a.ubicazione, a.descrizione,
                   (SELECT COUNT(*) FROM manutenzioni m WHERE m.apparecchio_id = a.id) AS n_manut,
                   (SELECT COUNT(*) FROM verifiche v WHERE v.apparecchio_id = a.id) AS n_verif
            FROM apparecchi a
            WHERE a.stato != 'dismesso' AND {div_clause}
            ORDER BY a.marca, a.modello, a.matricola""",
        div_params)

    coppie = candidati_duplicati(righe)
    return render_template('apparecchi/duplicati.html', coppie=coppie, criteri=CRITERI)
```

- [ ] **Step 4: Creare `templates/apparecchi/duplicati.html`**

```html
{% extends "base.html" %}
{% block title %}Possibili duplicati{% endblock %}
{% block content %}
<div class="d-flex justify-content-between align-items-center mb-4">
  <h2><i class="bi bi-files me-2"></i>Possibili duplicati</h2>
  <span class="badge bg-secondary">{{ coppie|length }} coppie</span>
</div>

{% if not coppie %}
<div class="alert alert-success">
  <i class="bi bi-check-circle me-1"></i>
  Nessuna coppia sospetta fra gli apparecchi visibili.
</div>
{% else %}
<p class="text-muted">
  Sono proposte, non certezze: due macchine gemelle acquistate insieme hanno la
  stessa forma di un errore di battitura. Confronta prima di fondere.
</p>
{% for c in coppie %}
<div class="card shadow-sm mb-3">
  <div class="card-body">
    <div class="row">
      {% for scheda in (c.a, c.b) %}
      <div class="col-md-5">
        <strong>{{ scheda.marca }} {{ scheda.modello }}</strong>
        <code class="ms-1">{{ scheda.matricola }}</code><br>
        <span class="text-muted small">
          {{ scheda.ubicazione or '—' }} ·
          {{ scheda.n_manut }} manut. · {{ scheda.n_verif }} verif.
        </span>
      </div>
      {% if not loop.last %}<div class="col-md-1 text-center align-self-center">↔</div>{% endif %}
      {% endfor %}
      {# Il pulsante "Confronta" arriva col Task 5, insieme alla rotta che
         apre: un collegamento a una rotta inesistente farebbe sollevare
         BuildError, e un segnaposto che non porta da nessuna parte e' peggio
         di un pulsante che ancora non c'e'. #}
    </div>
    <div class="mt-2 small text-secondary">
      <i class="bi bi-info-circle me-1"></i>{{ criteri[c.criterio] }}
    </div>
  </div>
</div>
{% endfor %}
{% endif %}
{% endblock %}
```

**Attenzione:** in questo task il template non ha ancora il pulsante "Confronta",
perche' la rotta `apparecchi.fondi` che aprirebbe nasce solo al Task 5 e un
`url_for` verso una rotta inesistente fa sollevare `BuildError` sulla pagina
intera. Il Task 5 aggiunge rotta e pulsante insieme. Niente segnaposto.

- [ ] **Step 5: Eseguire i test**

Run: `python -m pytest tests/test_fusione_routes.py -q`
Expected: PASS, 4 test.

- [ ] **Step 6: Provare la sensibilita' del filtro di struttura**

Togli `AND {div_clause}` dalla query (e `div_params` dai parametri): deve fallire
`test_l_elenco_non_mostra_apparecchi_di_altre_strutture`. Rimetti a posto.

- [ ] **Step 7: Commit**

```bash
git add apparecchi.py templates/apparecchi/duplicati.html tests/test_fusione_routes.py
git commit -m "feat(fusione): elenco delle coppie sospette"
```

---

## Task 5: confronto, conferma ed esecuzione

**Files:**
- Modify: `apparecchi.py`, `templates/apparecchi/dettaglio.html`, `templates/apparecchi/duplicati.html`
- Create: `templates/apparecchi/fondi.html`
- Test: `tests/test_fusione_routes.py`

**Interfaces:**
- Consumes: `fondi_apparecchi`, `valori_predefiniti`, `CAMPI_FONDIBILI`,
  `FusioneRifiutataError`, `FusioneCollisioneError` dai Task 2-3.
- Produces: le rotte `apparecchi.fondi` (`GET`) e `apparecchi.esegui_fusione`
  (`POST /apparecchi/<id>/fondi/<altro_id>`).

- [ ] **Step 1: Scrivere i test che falliscono**

Aggiungi in coda a `tests/test_fusione_routes.py`:

```python
def test_la_fusione_dalla_rotta_somma_gli_interventi(client, app, dati):
    from models import execute, query_one
    with app.app_context():
        for ap in (dati['uno'], dati['due']):
            execute("INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento) "
                    "VALUES (?,'preventiva','2026-03-12')", (ap,))
    entra(client, 'admin@a.it')
    risposta = client.post(
        f"/apparecchi/{dati['uno']}/fondi/{dati['due']}",
        data={'principale': dati['uno']}, follow_redirects=True)
    assert risposta.status_code == 200
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM manutenzioni WHERE apparecchio_id=?",
                         (dati['uno'],))['n'] == 2
        assert query_one("SELECT id FROM apparecchi WHERE id=?", (dati['due'],)) is None


def test_la_fusione_e_negata_fra_strutture_diverse(client, app, dati):
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/apparecchi/{dati['uno']}/fondi/{dati['estraneo']}",
                data={'principale': dati['uno']}, follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT id FROM apparecchi WHERE id=?", (dati['estraneo'],)) is not None


def test_la_fusione_e_negata_a_un_utente_semplice(client, app, dati):
    from models import query_one
    entra(client, 'utente@a.it')
    client.post(f"/apparecchi/{dati['uno']}/fondi/{dati['due']}",
                data={'principale': dati['uno']}, follow_redirects=True)
    with app.app_context():
        assert query_one("SELECT id FROM apparecchi WHERE id=?", (dati['due'],)) is not None


def test_il_registro_conserva_la_scheda_scartata(client, app, dati):
    """La fusione e' definitiva: senza i campi nel registro, ricostruire a
    mano la scheda cancellata diventa impossibile."""
    from models import query_one
    entra(client, 'admin@a.it')
    client.post(f"/apparecchi/{dati['uno']}/fondi/{dati['due']}",
                data={'principale': dati['uno']}, follow_redirects=True)
    with app.app_context():
        voce = query_one("SELECT dettagli FROM log_attivita WHERE azione='fusione'")
        assert voce is not None
        assert 'R00015' in voce['dettagli']
        assert 'OZY' in voce['dettagli']


def test_la_pagina_di_confronto_mostra_i_campi_diversi(client, dati):
    entra(client, 'admin@a.it')
    testo = client.get(f"/apparecchi/{dati['uno']}/fondi/{dati['due']}").get_data(as_text=True)
    assert 'R-00015' in testo
    assert 'R00015' in testo
    assert 'Matricola' in testo


def test_la_collisione_con_un_terzo_viene_spiegata_non_lanciata(client, app, dati):
    from models import execute, query_one
    with app.app_context():
        execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato) "
                "VALUES (?,?,'COLLIDE','REXXAM','OZY','funzionante')",
                (dati['div_a'], dati['a']))
    entra(client, 'admin@a.it')
    risposta = client.post(
        f"/apparecchi/{dati['uno']}/fondi/{dati['due']}",
        data={'principale': dati['uno'], 'campo_matricola': 'COLLIDE'},
        follow_redirects=True)
    assert risposta.status_code == 200
    assert 'Esiste gia' in risposta.get_data(as_text=True)
    with app.app_context():
        assert query_one("SELECT id FROM apparecchi WHERE id=?", (dati['due'],)) is not None
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `python -m pytest tests/test_fusione_routes.py -q`
Expected: FAIL, 404.

- [ ] **Step 3: Aggiungere le due rotte**

In `apparecchi.py`, dopo `duplicati()`:

```python
def _due_schede_fondibili(id, altro_id):
    """Le due schede, se l'utente puo' agire su entrambe. Altrimenti (None, None).

    apparecchio_accessibile controlla struttura E divisione: e' l'unico modo
    accettabile di raggiungere un apparecchio per id.
    """
    if g.user['ruolo'] not in ('admin', 'superadmin', 'tecnico'):
        return None, None
    return apparecchio_accessibile(id), apparecchio_accessibile(altro_id)


@apparecchi_bp.route('/apparecchi/<int:id>/fondi/<int:altro_id>')
@login_required
def fondi(id, altro_id):
    """Confronto delle due schede prima della fusione."""
    from fusione_service import CAMPI_FONDIBILI, valori_predefiniti

    uno, due = _due_schede_fondibili(id, altro_id)
    if not uno or not due:
        flash('Apparecchio non trovato o non autorizzato.', 'danger')
        return redirect(url_for('apparecchi.lista'))

    differenze = [
        {'campo': c, 'a': uno.get(c), 'b': due.get(c)}
        for c in CAMPI_FONDIBILI if uno.get(c) != due.get(c)
    ]
    interventi = query_all(
        """SELECT 'manutenzione' AS tipo, id, data_intervento AS data, tipo AS sottotipo,
                  verbale_path AS allegato, apparecchio_id
           FROM manutenzioni WHERE apparecchio_id IN (?, ?)
           UNION ALL
           SELECT 'verifica', id, data_verifica, esito, documento_path, apparecchio_id
           FROM verifiche WHERE apparecchio_id IN (?, ?)
           ORDER BY data DESC""",
        (id, altro_id, id, altro_id))

    return render_template('apparecchi/fondi.html', uno=uno, due=due,
                           differenze=differenze, interventi=interventi,
                           predefiniti=valori_predefiniti(uno, due))


@apparecchi_bp.route('/apparecchi/<int:id>/fondi/<int:altro_id>', methods=['POST'])
@login_required
def esegui_fusione(id, altro_id):
    """Esegue la fusione. Tutto in una transazione sola."""
    from fusione_service import (fondi_apparecchi, CAMPI_FONDIBILI,
                                 FusioneCollisioneError, FusioneRifiutataError)

    uno, due = _due_schede_fondibili(id, altro_id)
    if not uno or not due:
        flash('Apparecchio non trovato o non autorizzato.', 'danger')
        return redirect(url_for('apparecchi.lista'))

    try:
        id_principale = int(request.form.get('principale', 0))
    except (TypeError, ValueError):
        id_principale = 0
    if id_principale not in (id, altro_id):
        flash('Scegli quale scheda deve sopravvivere.', 'warning')
        return redirect(url_for('apparecchi.fondi', id=id, altro_id=altro_id))
    id_scartato = altro_id if id_principale == id else id

    valori = {}
    for campo in CAMPI_FONDIBILI:
        if f'campo_{campo}' in request.form:
            valore = request.form.get(f'campo_{campo}')
            valori[campo] = valore if valore != '' else None

    interventi_scartati = []
    for chiave in request.form.getlist('scarta'):
        tipo, _sep, id_int = chiave.partition(':')
        if id_int.isdigit():
            interventi_scartati.append((tipo, int(id_int)))

    db = get_db()
    try:
        esito = fondi_apparecchi(db, id_principale, id_scartato, valori,
                                 interventi_scartati)
        db.commit()
    except FusioneCollisioneError as e:
        db.rollback()
        flash(str(e), 'danger')
        return redirect(url_for('apparecchi.fondi', id=id, altro_id=altro_id))
    except FusioneRifiutataError as e:
        db.rollback()
        flash(f'Fusione non eseguita: {e}', 'danger')
        return redirect(url_for('apparecchi.fondi', id=id, altro_id=altro_id))
    except Exception as e:
        db.rollback()
        current_app.logger.error(f'Fusione {id_principale}<-{id_scartato} fallita: {e}',
                                 exc_info=True)
        flash('Fusione fallita, nulla e\' stato modificato. Controlla il log.', 'danger')
        return redirect(url_for('apparecchi.fondi', id=id, altro_id=altro_id))

    scartato = esito['scartato']
    campi = ' '.join(
        f"{c}={scartato.get(c)!r}" for c in CAMPI_FONDIBILI
        if scartato.get(c) not in (None, ''))
    log_attivita(
        g.user['id'], 'fusione', 'apparecchi', id_principale,
        f"Fusi \"{scartato['marca']} {scartato['modello']} {scartato['matricola']}\" "
        f"(id {id_scartato}) in id {id_principale}. Scheda scartata: {campi}. "
        f"Spostati: {esito['manutenzioni']} manutenzioni, {esito['verifiche']} verifiche, "
        f"{esito['documenti']} documenti, {esito['accessori']} accessori. "
        f"Scartati: {esito['interventi_scartati']} interventi. "
        f"Valori scelti: {', '.join(esito['valori_scelti']) or 'nessuno'}",
        request.remote_addr)

    flash(f"Schede fuse: {esito['manutenzioni']} manutenzioni e "
          f"{esito['verifiche']} verifiche trasferite.", 'success')
    return redirect(url_for('apparecchi.dettaglio', id=id_principale))
```

Verifica che `get_db` e `current_app` siano fra gli import di `apparecchi.py`; se
mancano, aggiungili alla riga `from models import (...)` e a quella di `flask`.

- [ ] **Step 4: Creare `templates/apparecchi/fondi.html`**

```html
{% extends "base.html" %}
{% block title %}Fondi apparecchi{% endblock %}
{% block content %}
<h2 class="mb-4"><i class="bi bi-union me-2"></i>Fondi due schede</h2>

<form method="post" action="{{ url_for('apparecchi.esegui_fusione', id=uno.id, altro_id=due.id) }}">
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">

  <div class="card shadow-sm mb-4">
    <div class="card-header">Quale scheda sopravvive</div>
    <div class="card-body">
      <p class="text-muted small">
        La scheda scelta conserva il proprio id: i QR code gia' stampati e
        attaccati sull'apparecchio restano validi. L'altra viene cancellata.
      </p>
      {% for s in (uno, due) %}
      <div class="form-check">
        <input class="form-check-input" type="radio" name="principale"
               value="{{ s.id }}" id="p{{ s.id }}" {% if loop.first %}checked{% endif %}>
        <label class="form-check-label" for="p{{ s.id }}">
          {{ s.marca }} {{ s.modello }} <code>{{ s.matricola }}</code>
          <span class="text-muted">— id {{ s.id }}</span>
        </label>
      </div>
      {% endfor %}
    </div>
  </div>

  {% if differenze %}
  <div class="card shadow-sm mb-4">
    <div class="card-header">Campi diversi</div>
    <div class="table-responsive">
      <table class="table mb-0">
        <thead class="table-light">
          <tr><th>Campo</th><th>{{ uno.matricola }}</th><th>{{ due.matricola }}</th></tr>
        </thead>
        <tbody>
          {% for d in differenze %}
          <tr>
            <td class="text-capitalize">{{ d.campo.replace('_', ' ') }}</td>
            {% for valore in (d.a, d.b) %}
            <td>
              <div class="form-check">
                <input class="form-check-input" type="radio"
                       name="campo_{{ d.campo }}" value="{{ valore if valore is not none }}"
                       {% if predefiniti.get(d.campo) == valore %}checked{% endif %}>
                <label class="form-check-label">
                  {% if valore in (none, '') %}<em class="text-muted">vuoto</em>
                  {% else %}{{ valore }}{% endif %}
                </label>
              </div>
            </td>
            {% endfor %}
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  {% endif %}

  <div class="card shadow-sm mb-4">
    <div class="card-header">Interventi</div>
    <div class="card-body">
      <p class="text-muted small">
        Confluiscono tutti sulla scheda che sopravvive. Spunta solo cio' che
        vuoi buttare: due interventi nello stesso giorno possono essere
        legittimamente due.
      </p>
      {% for i in interventi %}
      <div class="form-check">
        <input class="form-check-input" type="checkbox" name="scarta"
               value="{{ i.tipo }}:{{ i.id }}" id="s{{ i.tipo }}{{ i.id }}">
        <label class="form-check-label" for="s{{ i.tipo }}{{ i.id }}">
          {{ i.tipo|capitalize }} {{ i.sottotipo }} — {{ i.data }}
          <span class="text-muted">(scheda {{ i.apparecchio_id }})</span>
          {% if i.allegato %}
            <span class="badge bg-secondary ms-1">con allegato</span>
          {% else %}
            <span class="badge bg-light text-muted ms-1">senza allegato</span>
          {% endif %}
        </label>
      </div>
      {% endfor %}
    </div>
  </div>

  <div class="alert alert-warning">
    <i class="bi bi-exclamation-triangle me-1"></i>
    La fusione e' definitiva e non si annulla dall'interfaccia. La scheda
    scartata viene conservata per intero nel registro attivita'.
  </div>

  <div class="d-flex gap-2">
    <button class="btn btn-primary">Fondi le due schede</button>
    <a href="{{ url_for('apparecchi.dettaglio', id=uno.id) }}"
       class="btn btn-outline-secondary">Annulla</a>
  </div>
</form>
{% endblock %}
```

- [ ] **Step 5: Aggiungere il collegamento nell'elenco e la voce alla scheda**

In `templates/apparecchi/duplicati.html`, al posto del commento lasciato dal
Task 4, il pulsante che ora ha una rotta a cui puntare:

```html
      <div class="col-md-1 text-end align-self-center">
        <a href="{{ url_for('apparecchi.fondi', id=c.a.id, altro_id=c.b.id) }}"
           class="btn btn-sm btn-outline-primary">Confronta</a>
      </div>
```

In `templates/apparecchi/dettaglio.html`, accanto agli altri pulsanti di azione,
dentro il blocco gia' condizionato ai ruoli amministrativi:

```html
<a href="{{ url_for('apparecchi.duplicati') }}" class="btn btn-sm btn-outline-secondary">
  <i class="bi bi-files me-1"></i>Possibili duplicati
</a>
```

- [ ] **Step 6: Eseguire i test**

Run: `python -m pytest tests/ -q`
Expected: PASS. Prima del Task 5 erano 194 + 10 (Task 1) + 8 (Task 2) + 8 (Task 3)
+ 4 (Task 4) = 224; con i 6 di questo task, **230**. Dichiara il numero reale.

- [ ] **Step 7: Provare la sensibilita' dell'autorizzazione e del registro**

Tre prove, ognuna seguita da ripristino e `git diff` vuoto:

1. In `_due_schede_fondibili`, togli il controllo sul ruolo: deve fallire
   `test_la_fusione_e_negata_a_un_utente_semplice`.
2. Sostituisci `apparecchio_accessibile(altro_id)` con
   `query_one("SELECT * FROM apparecchi WHERE id = ?", (altro_id,))`: deve fallire
   `test_la_fusione_e_negata_fra_strutture_diverse`.
3. Togli dal messaggio di `log_attivita` la parte `Scheda scartata: {campi}`: deve
   fallire `test_il_registro_conserva_la_scheda_scartata`.

- [ ] **Step 8: Commit**

```bash
git add apparecchi.py templates/apparecchi/fondi.html templates/apparecchi/duplicati.html templates/apparecchi/dettaglio.html tests/test_fusione_routes.py
git commit -m "feat(fusione): confronto, conferma ed esecuzione della fusione"
```

---

## Task 6: rilascio 2.6.1

**Files:**
- Modify: `app.py:33`, `config.example.json`, `CHANGELOG.md`, `README.md` (×2),
  `DOCUMENTAZIONE.md`, `CLOUDFLARE_TUNNEL.md`, `CLAUDE.md`, `AGENTS.md`

**Interfaces:** nessuna.

- [ ] **Step 1: Allineare i riferimenti di versione**

`app.py:33` → `APP_VERSION = "2.6.1"`
`config.example.json` → `"version": "2.6.1"`
`README.md` righe 1 e 555, `DOCUMENTAZIONE.md:4`, `CLOUDFLARE_TUNNEL.md:533`,
`CLAUDE.md:7`, `AGENTS.md:7` → `2.6.1`

Verifica con:
`git grep -n "2\.6\.0" -- ':!CHANGELOG.md' ':!docs/superpowers'`
Non deve restituire nulla.

**Se l'utente ha nel frattempo scelto 2.7.0**, sostituisci `2.6.1` con `2.7.0`
ovunque in questo task: e' l'unica differenza.

- [ ] **Step 2: Aggiungere la voce di CHANGELOG**

Inserisci sopra `## [2.6.0]`:

```markdown
## [2.6.1] - 2026-07-31

### Aggiunto

- **Fusione di apparecchi duplicati.** Lo stesso apparecchio fisico registrato due
  volte — `R-00015` e `R00015`, `MON-1` e `MON-l` — si incontra dopo un import da
  documenti diversi o un inserimento manuale in due reparti. Tenerne due schede
  spezza lo storico e falsa lo scadenzario, perche' la vista `prossime_scadenze`
  tiene l'ultimo record per apparecchio e ne vede due; cancellarne una perde i dati
  che solo quella aveva. Ora si fondono: manutenzioni, verifiche, documenti e
  accessori confluiscono sulla scheda che sopravvive, che **conserva il proprio id**
  perche' i QR code gia' stampati e attaccati sull'apparecchio restino validi.
- **Elenco dei possibili duplicati** (`/apparecchi/duplicati`), con il criterio che
  ha proposto ogni coppia: matricola identica a meno di trattini e maiuscole, una
  matricola contenuta nell'altra, oppure stesso modello e ubicazione con matricole
  che differiscono per un carattere. Sono proposte, non certezze: due macchine
  gemelle acquistate insieme hanno la stessa forma di un errore di battitura, e
  nessun criterio automatico puo' distinguerle — per questo si confronta prima di
  fondere.
- Nella pagina di confronto si sceglie quale scheda sopravvive e, per ogni campo
  diverso, quale valore tenere. E' preselezionato quello della scheda principale,
  **tranne dove e' vuoto e l'altra ha un valore**: nel caso comune basta confermare,
  e non si perde il dato che solo la scheda scartata aveva. Gli interventi
  confluiscono tutti; scartarne uno e' una scelta esplicita, e la riga dice se solo
  quella copia ha il verbale allegato.
- La fusione e' **definitiva** e non si annulla dall'interfaccia. Nel registro
  attivita' finisce la scheda cancellata campo per campo, cosi' ricostruirla a mano
  resta possibile.

### Note

- Riservata ad `admin`, `tecnico` e `superadmin`: la fusione cancella una scheda, e
  un `utente` non puo' nemmeno dismetterne una. Entrambe le schede devono essere
  accessibili all'operatore — nessuna fusione fra strutture diverse ne' fra divisioni
  non assegnate.
- Se i valori scelti facessero collidere la scheda risultante con un terzo apparecchio
  (`UNIQUE(struttura_id, modello, matricola)`), l'operazione viene rifiutata con un
  messaggio che nomina il terzo, invece di fallire con un errore di database.
- **Nessun file si sposta**: gli allegati stanno in `uploads/strutture/<id>/<tipo>/`,
  non in cartelle per apparecchio, quindi fondere due schede della stessa struttura
  cambia solo la riga che li referenzia.
- Lo **spostamento di un apparecchio fra strutture** resta da progettare: a differenza
  della fusione, li' i file si spostano davvero, perche' cambia il prefisso del
  percorso.

---
```

- [ ] **Step 3: Eseguire la suite completa**

Run: `python -m pytest tests/ -q`
Expected: PASS. Dichiara il numero reale.

- [ ] **Step 4: Commit**

```bash
git add app.py config.example.json CHANGELOG.md README.md DOCUMENTAZIONE.md CLOUDFLARE_TUNNEL.md CLAUDE.md AGENTS.md
git commit -m "chore(release): 2.6.1 — fusione di apparecchi duplicati"
```

---

## Autoverifica del piano

**Copertura della spec.** «Elenco dei candidati» → Task 4. «Fusione manuale dalla
scheda» → Task 5 (voce nella scheda) + Task 5 (rotta di confronto). Tre criteri con
etichetta → Task 1. Scelta campo per campo con preselezione → Task 3
(`valori_predefiniti`) + Task 5 (template). «La scheda principale conserva il proprio
id» → Task 2, test dedicato. «Cosa si sposta», `import_preview` compreso → Task 2.
«Nessun file si sposta» → Task 2, test dedicato. Interventi che sembrano lo stesso →
Task 3 + Task 5. Vincoli e rifiuti (accessibilita', collisione con un terzo, permessi,
transazione unica) → Task 3 e Task 5. Tracciabilita' → Task 5. Tutti i test elencati
nella sezione «Test» della spec hanno un test corrispondente.

**Segnaposto.** Nessun «TBD», nessun «simile al Task N», nessun passo senza il codice
che serve, e nessun segnaposto nell'interfaccia: il pulsante "Confronta" nasce al
Task 5 insieme alla rotta che apre, invece di comparire prima come elemento inerte.

**Coerenza dei tipi.** `Coppia(a, b, criterio)` con `a`/`b` dizionari: usata cosi' nel
Task 1 e nel template del Task 4 (`c.a.id`, `c.a.matricola`). `fondi_apparecchi`
restituisce le stesse chiavi in tutti i task che la consumano. `CAMPI_FONDIBILI` e'
definita nel Task 3 e usata nei Task 3 e 5. `FusioneCollisioneError` eredita da
`FusioneRifiutataError`, quindi nel Task 5 il primo `except` deve precedere il
secondo — ed e' scritto in quell'ordine.

**Un rischio che l'implementatore deve conoscere.** Il Task 5 passa a
`fondi_apparecchi` la connessione della richiesta (`get_db()`), che ha
`PRAGMA foreign_keys = ON` (`models.get_db`). E' quello che serve: senza, la
cancellazione della scheda scartata non verrebbe bloccata da `import_preview` e il
difetto passerebbe inosservato fino a un `foreign_key_check`.
