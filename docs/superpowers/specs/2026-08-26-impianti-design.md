# Gestione impianti — MedInventory 2.7.0

- **Data:** 2026-08-26
- **Versione bersaglio:** 2.7.0
- **Stato:** approvato in brainstorming, da pianificare

## 1. Obiettivo

Affiancare alla gestione degli apparecchi elettromedicali la gestione degli
**impianti** delle strutture: anagrafica, documentazione iniziale, piano di
manutenzione e verifica periodica, storico degli interventi e avvisi di scadenza
via email.

Gli impianti condividono con gli apparecchi il problema che l'applicazione già
risolve — una scadenza che nessuno ricorda finché non è passata — ma non il
modello dati: un apparecchio ha una scadenza perché ha avuto un intervento,
mentre un impianto ha un piano di verifiche che esiste prima del primo
intervento e sopravvive a ognuno di essi. Da qui la scelta di tabelle proprie
invece del riuso di `manutenzioni`.

## 2. Requisiti dell'utente

1. Le strutture possono avere impianti, di tipo predefinito (elettrico,
   idraulico, riscaldamento, climatizzazione, …) o di un tipo custom la cui
   descrizione è impostabile.
2. Ogni impianto può avere documentazione iniziale caricabile — progetti,
   dichiarazioni di conformità, collaudi — con descrizione, data e soggetto
   emittente (ragione sociale, indirizzo, telefono, email).
3. Oltre alla documentazione iniziale, ogni impianto ha un piano di
   manutenzione/verifica, con periodicità standard (es. verifica di terra ogni
   due anni) o decise volta per volta.
4. Il sistema avvisa via email il responsabile della struttura 30 giorni prima
   della scadenza, con anticipo configurabile, ed eventualmente un secondo
   indirizzo (per esempio il manutentore).
5. Funzionalità aggiuntive utili suggerite in fase di analisi e accolte:
   scadenzario unificato apparecchi + impianti, libretto impianto in PDF,
   componenti dell'impianto.

## 3. Decisioni di impostazione

Le scelte qui sotto sono state discusse e approvate; le motivazioni restano
perché ognuna ha un'alternativa ragionevole che è stata scartata.

**Gli impianti appartengono alla divisione, non alla struttura.** Portano
`divisione_id NOT NULL` e `struttura_id` denormalizzato. La denormalizzazione
serve al filtro di isolamento, che deve poter scartare le righe di un'altra
struttura senza passare da `divisioni` in ogni query; è la stessa scelta già
fatta per `apparecchi`. Di conseguenza `divisioni` guadagna quattro colonne
opzionali (indirizzo, email, telefono, responsabile): se l'impianto è della
divisione, l'avviso deve poter raggiungere la divisione.

**Piano e storico sono tabelle distinte.** `impianti_scadenze` è il piano — che
cosa va verificato, ogni quanto, con quale anticipo avvisare, a chi. La riga
esiste anche quando nessun intervento è ancora stato fatto.
`impianti_interventi` è lo storico. Un intervento *può* riferirsi a una riga di
piano, e in quel caso ne fa avanzare la scadenza, ma può anche essere una
riparazione fuori piano.

**Nessun riuso polimorfico di `manutenzioni`/`verifiche`.** Aggiungere un
`impianto_id` nullable avrebbe richiesto di rendere nullable `apparecchio_id` —
oggi NOT NULL — e di rivedere ogni query di isolamento esistente. Un rischio
sull'isolamento fra tenant per risparmiare una tabella non vale il cambio.

**`periodicita_mesi` NULL significa una tantum.** Registrare un intervento su
una riga periodica sposta `prossima_scadenza` di N mesi *dalla data
dell'intervento*, non dalla scadenza precedente: se la verifica è stata fatta in
ritardo, il ciclo successivo parte da quando è stata fatta davvero. Registrare
un intervento su una riga una tantum la chiude (`attiva = 0`).

**La chiave anti-duplicato include `scadenza_target`.** Senza di essa, un
`UNIQUE(scadenza_id, soglia)` sopprimerebbe per sempre l'avviso del ciclo
successivo: la stessa riga di piano, rinnovata, non avviserebbe mai più.

**Il catalogo delle periodicità standard è una costante Python**, non una
tabella. Un aggiornamento normativo viaggia col codice e non richiede migrazione
dei dati né un'interfaccia di amministrazione che nessuno userebbe più di una
volta l'anno.

**`prossime_scadenze` non si tocca.** La vista è usata da `export_service`,
dallo scheduler e dalle API; ne viene aggiunta una gemella,
`prossime_scadenze_impianti`, e le pagine che mostrano entrambe fanno UNION.

## 4. Modello dati

### 4.1 Colonne aggiuntive su `divisioni`

Aggiunte da `apply_schema_updates()`, tutte opzionali:

```sql
ALTER TABLE divisioni ADD COLUMN indirizzo TEXT;
ALTER TABLE divisioni ADD COLUMN email TEXT;
ALTER TABLE divisioni ADD COLUMN telefono TEXT;
ALTER TABLE divisioni ADD COLUMN responsabile TEXT;
```

### 4.2 `manutentori`

Anagrafica riusabile delle ditte manutentrici, per struttura. Non serve ai
documenti (l'emittente di una dichiarazione di conformità si inserisce una volta
sola e non torna) ma alle manutenzioni, dove la stessa ditta ricorre.

```sql
CREATE TABLE IF NOT EXISTS manutentori (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  struttura_id INTEGER NOT NULL,
  ragione_sociale TEXT NOT NULL,
  indirizzo TEXT,
  telefono TEXT,
  email TEXT,
  partita_iva TEXT,
  note TEXT,
  attivo INTEGER DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE,
  UNIQUE(struttura_id, ragione_sociale)
);

CREATE INDEX IF NOT EXISTS idx_manutentori_struttura ON manutentori(struttura_id);
CREATE INDEX IF NOT EXISTS idx_manutentori_attivo ON manutentori(attivo);
```

### 4.3 `impianti`

```sql
CREATE TABLE IF NOT EXISTS impianti (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  struttura_id INTEGER NOT NULL,
  divisione_id INTEGER NOT NULL,
  nome TEXT NOT NULL,
  tipo TEXT NOT NULL DEFAULT 'altro' CHECK(tipo IN (
    'elettrico', 'idraulico', 'riscaldamento', 'climatizzazione',
    'antincendio', 'gas_medicali', 'ascensori', 'rete_dati', 'altro')),
  tipo_custom TEXT,
  descrizione TEXT,
  ubicazione TEXT,
  anno_installazione INTEGER,
  identificativo TEXT,
  stato TEXT NOT NULL DEFAULT 'attivo' CHECK(stato IN (
    'attivo', 'in_manutenzione', 'fuori_servizio', 'dismesso')),
  manutentore_id INTEGER,
  note TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  created_by INTEGER,
  updated_by INTEGER,
  FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE,
  FOREIGN KEY (divisione_id) REFERENCES divisioni(id) ON DELETE RESTRICT,
  FOREIGN KEY (manutentore_id) REFERENCES manutentori(id) ON DELETE SET NULL,
  FOREIGN KEY (created_by) REFERENCES utenti(id),
  FOREIGN KEY (updated_by) REFERENCES utenti(id),
  UNIQUE(struttura_id, nome)
);

CREATE INDEX IF NOT EXISTS idx_impianti_struttura ON impianti(struttura_id);
CREATE INDEX IF NOT EXISTS idx_impianti_divisione ON impianti(divisione_id);
CREATE INDEX IF NOT EXISTS idx_impianti_tipo ON impianti(tipo);
CREATE INDEX IF NOT EXISTS idx_impianti_stato ON impianti(stato);
```

`tipo_custom` si compila solo con `tipo = 'altro'`; è la descrizione mostrata al
posto dell'etichetta generica. Il vincolo è applicato in validazione, non in
CHECK: un CHECK condizionale renderebbe più difficile aggiungere tipi in
seguito, e il costo di una riga incoerente è un'etichetta ignorata.

`stato = 'dismesso'` è la cancellazione logica, come per gli apparecchi: un
impianto non si cancella mai fisicamente, perché la sua documentazione ha valore
anche dopo.

### 4.4 `impianti_componenti`

```sql
CREATE TABLE IF NOT EXISTS impianti_componenti (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  impianto_id INTEGER NOT NULL,
  descrizione TEXT NOT NULL,
  marca TEXT,
  modello TEXT,
  matricola TEXT,
  ubicazione TEXT,
  note TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (impianto_id) REFERENCES impianti(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_impianti_componenti_impianto
  ON impianti_componenti(impianto_id);
```

I componenti servono a dire *che cosa* si verifica quando la verifica non
riguarda l'impianto intero — il singolo quadro elettrico, l'estintore, la UTA.
Restano volutamente poveri: non sono apparecchi e non hanno un ciclo di vita
proprio.

### 4.5 `impianti_documenti`

```sql
CREATE TABLE IF NOT EXISTS impianti_documenti (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  impianto_id INTEGER NOT NULL,
  tipo TEXT NOT NULL DEFAULT 'altro' CHECK(tipo IN (
    'progetto', 'dichiarazione_conformita', 'collaudo', 'certificato',
    'libretto', 'planimetria', 'verbale', 'altro')),
  descrizione TEXT,
  data_documento DATE,
  emittente_ragione_sociale TEXT,
  emittente_indirizzo TEXT,
  emittente_telefono TEXT,
  emittente_email TEXT,
  filename TEXT NOT NULL,
  filepath TEXT NOT NULL,
  filesize INTEGER,
  uploaded_by INTEGER,
  uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (impianto_id) REFERENCES impianti(id) ON DELETE CASCADE,
  FOREIGN KEY (uploaded_by) REFERENCES utenti(id)
);

CREATE INDEX IF NOT EXISTS idx_impianti_documenti_impianto
  ON impianti_documenti(impianto_id);
CREATE INDEX IF NOT EXISTS idx_impianti_documenti_tipo
  ON impianti_documenti(tipo);
```

I dati dell'emittente sono testo libero sulla riga del documento, non una FK a
`manutentori`: le ditte che emettono i documenti iniziali sono quasi sempre
diverse fra loro e si inseriscono una volta sola.

### 4.6 `impianti_scadenze` — il piano

```sql
CREATE TABLE IF NOT EXISTS impianti_scadenze (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  impianto_id INTEGER NOT NULL,
  componente_id INTEGER,
  nome TEXT NOT NULL,
  riferimento_normativo TEXT,
  periodicita_mesi INTEGER,
  prossima_scadenza DATE NOT NULL,
  giorni_anticipo INTEGER NOT NULL DEFAULT 30,
  email_extra TEXT,
  avvisa_manutentore INTEGER NOT NULL DEFAULT 1,
  attiva INTEGER NOT NULL DEFAULT 1,
  note TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (impianto_id) REFERENCES impianti(id) ON DELETE CASCADE,
  FOREIGN KEY (componente_id) REFERENCES impianti_componenti(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_impianti_scadenze_impianto
  ON impianti_scadenze(impianto_id);
CREATE INDEX IF NOT EXISTS idx_impianti_scadenze_prossima
  ON impianti_scadenze(prossima_scadenza);
CREATE INDEX IF NOT EXISTS idx_impianti_scadenze_attiva
  ON impianti_scadenze(attiva);
```

`email_extra` è una lista di indirizzi separati da virgola, per riga di piano:
la verifica di terra e la manutenzione della caldaia raramente interessano le
stesse persone.

### 4.7 `impianti_interventi` — lo storico

```sql
CREATE TABLE IF NOT EXISTS impianti_interventi (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  impianto_id INTEGER NOT NULL,
  scadenza_id INTEGER,
  componente_id INTEGER,
  tipo TEXT NOT NULL DEFAULT 'ordinaria' CHECK(tipo IN (
    'verifica', 'ordinaria', 'straordinaria', 'riparazione')),
  data_intervento DATE NOT NULL,
  esito TEXT CHECK(esito IN ('positivo', 'negativo', 'con_riserva')),
  manutentore_id INTEGER,
  tecnico_ditta TEXT,
  descrizione TEXT,
  costo REAL,
  verbale_path TEXT,
  note TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  created_by INTEGER,
  FOREIGN KEY (impianto_id) REFERENCES impianti(id) ON DELETE CASCADE,
  FOREIGN KEY (scadenza_id) REFERENCES impianti_scadenze(id) ON DELETE SET NULL,
  FOREIGN KEY (componente_id) REFERENCES impianti_componenti(id) ON DELETE SET NULL,
  FOREIGN KEY (manutentore_id) REFERENCES manutentori(id) ON DELETE SET NULL,
  FOREIGN KEY (created_by) REFERENCES utenti(id)
);

CREATE INDEX IF NOT EXISTS idx_impianti_interventi_impianto
  ON impianti_interventi(impianto_id);
CREATE INDEX IF NOT EXISTS idx_impianti_interventi_scadenza
  ON impianti_interventi(scadenza_id);
CREATE INDEX IF NOT EXISTS idx_impianti_interventi_data
  ON impianti_interventi(data_intervento);
```

`scadenza_id` è `ON DELETE SET NULL` e non CASCADE: cancellare una riga di piano
non deve cancellare la prova che quelle verifiche sono state fatte.

Un esito `negativo` **non** fa avanzare `prossima_scadenza` e non chiude la
riga: la verifica va rifatta. Un esito `con_riserva` la fa avanzare come un
esito positivo, perché la riserva riguarda l'impianto, non la scadenza.

### 4.8 `impianti_avvisi_inviati`

```sql
CREATE TABLE IF NOT EXISTS impianti_avvisi_inviati (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scadenza_id INTEGER NOT NULL,
  soglia TEXT NOT NULL,
  scadenza_target DATE NOT NULL,
  destinatari TEXT,
  inviato_il DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (scadenza_id) REFERENCES impianti_scadenze(id) ON DELETE CASCADE,
  UNIQUE(scadenza_id, soglia, scadenza_target)
);

CREATE INDEX IF NOT EXISTS idx_impianti_avvisi_scadenza
  ON impianti_avvisi_inviati(scadenza_id);
```

## 5. Vista `prossime_scadenze_impianti`

Stessa classificazione a cinque priorità di `prossime_scadenze`, in modo che le
due sorgenti si possano unire senza tradurre nulla.

```sql
CREATE VIEW IF NOT EXISTS prossime_scadenze_impianti AS
SELECT
  i.id AS impianto_id,
  i.struttura_id,
  i.divisione_id,
  i.nome AS impianto_nome,
  i.tipo,
  i.tipo_custom,
  i.ubicazione,
  s.id AS scadenza_id,
  s.nome AS scadenza_nome,
  s.riferimento_normativo,
  s.periodicita_mesi,
  s.giorni_anticipo,
  c.descrizione AS componente_descrizione,
  s.prossima_scadenza,
  CAST((julianday(s.prossima_scadenza) - julianday('now')) AS INTEGER) AS giorni_rimasti,
  CASE
    WHEN julianday(s.prossima_scadenza) - julianday('now') < 0  THEN 'scaduto'
    WHEN julianday(s.prossima_scadenza) - julianday('now') <= 7  THEN 'urgente'
    WHEN julianday(s.prossima_scadenza) - julianday('now') <= 15 THEN 'attenzione'
    WHEN julianday(s.prossima_scadenza) - julianday('now') <= 30 THEN 'avviso'
    ELSE 'ok'
  END AS priorita
FROM impianti i
INNER JOIN impianti_scadenze s ON i.id = s.impianto_id
LEFT JOIN impianti_componenti c ON c.id = s.componente_id
WHERE i.stato != 'dismesso'
  AND s.attiva = 1
ORDER BY s.prossima_scadenza ASC;
```

La vista non ha bisogno della sottoquery "solo l'ultimo record" che
`prossime_scadenze` usa su `manutenzioni` e `verifiche`: il piano contiene già
una sola riga per verifica, ed è quella la scadenza corrente.

## 6. Route, permessi e interfaccia

### 6.1 Blueprint

Nuovo `impianti_bp = Blueprint('impianti', __name__, url_prefix='/impianti')` in
`impianti.py`, registrato in `create_app()`. La logica di dominio sta fuori dal
blueprint: `impianti_service.py` (ricalcolo delle scadenze, applicazione del
catalogo, risoluzione dei destinatari, selezione degli avvisi da inviare) e
`impianti_catalogo.py` (la costante).

### 6.2 Rotte

| Rotta | Metodi | Permesso |
|---|---|---|
| `/impianti` | GET | `login_required` |
| `/impianti/nuovo` | GET POST | `tecnico_o_admin_required` |
| `/impianti/<id>` | GET | `login_required` |
| `/impianti/<id>/modifica` | GET POST | `tecnico_o_admin_required` |
| `/impianti/<id>/dismetti` | POST | `tecnico_o_admin_required` |
| `/impianti/<id>/componenti` | POST | `tecnico_o_admin_required` |
| `/impianti/<id>/componenti/<cid>/elimina` | POST | `tecnico_o_admin_required` |
| `/impianti/<id>/documenti` | POST | `login_required` |
| `/impianti/<id>/documenti/<did>` | GET | `login_required` |
| `/impianti/<id>/documenti/<did>/elimina` | POST | `tecnico_o_admin_required` |
| `/impianti/<id>/piano/nuova` | GET POST | `tecnico_o_admin_required` |
| `/impianti/<id>/piano/<sid>/modifica` | GET POST | `tecnico_o_admin_required` |
| `/impianti/<id>/piano/<sid>/sospendi` | POST | `tecnico_o_admin_required` |
| `/impianti/<id>/piano/catalogo` | GET POST | `tecnico_o_admin_required` |
| `/impianti/<id>/interventi/nuovo` | GET POST | `login_required` |
| `/impianti/interventi/<iid>/verbale` | GET | `login_required` |
| `/impianti/<id>/libretto.pdf` | GET | `login_required` |
| `/impianti/manutentori` | GET | `tecnico_o_admin_required` |
| `/impianti/manutentori/nuovo` | GET POST | `tecnico_o_admin_required` |
| `/impianti/manutentori/<mid>/modifica` | GET POST | `tecnico_o_admin_required` |
| `/impianti/manutentori/<mid>/elimina` | POST | `tecnico_o_admin_required` |

Ripartizione dei permessi: l'anagrafica dell'impianto e il piano si toccano solo
da admin o tecnico; l'utente della divisione legge l'impianto, registra
interventi e carica documenti. Chi lavora sull'impianto deve poter dire che cosa
ha fatto senza poter cambiare ogni quanto va fatto.

La lista risponde a `?partial=1` con il solo frammento di tabella, come le altre
liste dell'applicazione.

### 6.3 Guardia di isolamento

Nuova `models.impianto_accessibile(impianto_id)`, gemella di
`apparecchio_accessibile()`: verifica struttura **e** divisione, e viene chiamata
da ogni rotta che riceve un `<id>`, prima di qualunque lettura o scrittura.

Le tabelle figlie non portano `struttura_id`: ogni query su documenti,
componenti, piano e interventi fa JOIN su `impianti` con il filtro di struttura.
`WHERE impianto_id = ?` da solo non è mai sufficiente, esattamente come per gli
apparecchi raggiunti per id.

Gli elenchi passano dal filtro di divisione (`_get_divisione_filter()`) con
l'alias della tabella impianti.

### 6.4 Scadenzario unificato

Non nasce una pagina scadenze separata per gli impianti. La
`/manutenzioni/scadenzario` esistente guadagna:

- un filtro `origine` con valori `tutto` (predefinito), `apparecchi`, `impianti`;
- una colonna "oggetto" che mostra l'apparecchio o l'impianto;
- una query in UNION fra `prossime_scadenze` e `prossime_scadenze_impianti`,
  normalizzata sulle colonne comuni (`origine`, `oggetto`, `dettaglio`,
  `divisione`, `prossima_scadenza`, `giorni_rimasti`, `priorita`).

I contatori per priorità della dashboard sommano le due origini.

### 6.5 Template e navigazione

Nuovi: `templates/impianti/lista.html`, `form.html`, `dettaglio.html`,
`manutentori.html`, più `templates/partials/impianti_table.html` per gli
aggiornamenti HTMX.

`dettaglio.html` è a schede: anagrafica, componenti, documenti, piano,
interventi.

Voce di menu "Impianti" accanto ad "Apparecchi" in `templates/base.html`, in
**due** punti: la barra desktop (intorno alla riga 68) e il menu ridotto
(intorno alla riga 239).

### 6.6 Libretto impianto

`/impianti/<id>/libretto.pdf` genera, riusando la classe PDF già in uso per le
stampe, un documento con: anagrafica, componenti, elenco dei documenti allegati
(descrizione, data, emittente — non i file), piano di manutenzione con le
scadenze correnti e storico completo degli interventi. È il fascicolo che si
consegna all'organo di vigilanza.

## 7. Avvisi

### 7.1 Task dello scheduler

Nuovo task in `scheduler.py`:

```python
{
    'name': 'impianti_alerts',
    'func': self._send_impianti_alerts,
    'interval': 3600,
    'last_run': 0,
},
```

Il task gira ogni ora ma non fa nulla prima delle 7 del mattino. Non c'è un gate
sull'ora *esatta*: la tabella anti-duplicato impedisce i doppioni, quindi le ore
successive sono ritentativi gratuiti per gli invii falliti, e nessun avviso
parte alle tre di notte.

Come `_send_deadline_alerts`, il ciclo per struttura cattura le eccezioni riga
per riga: un errore su una struttura non deve fermare le successive, e in un
thread di fondo nessuno lo vedrebbe se non nel log.

### 7.2 Selezione degli avvisi

In `impianti_service.avvisi_da_inviare()` — niente SQL nello scheduler, che
resta orchestrazione. La query parte da `impianti_scadenze` con `attiva = 1`,
unisce `impianti` (`stato != 'dismesso'`), `divisioni` e `strutture`
(`attiva = 1`), e calcola
`giorni_rimasti = julianday(prossima_scadenza) - julianday('now')`.

Soglie raggiunte da una riga:

| soglia | condizione |
|---|---|
| `anticipo` | `giorni_rimasti <= giorni_anticipo` |
| `imminente` | `giorni_rimasti <= 7` |
| `scaduto` | `giorni_rimasti <= 0` |
| `sollecito_N` | scaduta, con `N = giorni_scaduti // 30`, `N >= 1` |

La soglia `scaduto` scatta a `giorni_rimasti <= 0`, cioè già il giorno stesso
della scadenza, mentre la vista classifica quel giorno come `urgente` e passa a
`scaduto` solo dal giorno dopo. La differenza è voluta: la vista descrive uno
stato, l'avviso deve partire mentre la verifica è ancora in tempo.

Le soglie sono cumulative: una riga a meno quaranta giorni le ha raggiunte
tutte. Si invia **solo la più alta non ancora presente in
`impianti_avvisi_inviati`**, e si registrano come inviate tutte quelle
raggiunte. Senza questa regola un impianto inserito già scaduto sparerebbe
quattro email di seguito.

### 7.3 Destinatari

In cascata, per ogni riga di piano:

1. `strutture.email_responsabile`, con fallback su `strutture.email_notifiche` —
   sempre presente;
2. `divisioni.email`, se valorizzata;
3. `impianti_scadenze.email_extra`, lista separata da virgola;
4. l'email del manutentore dell'impianto (`impianti.manutentore_id` →
   `manutentori.email`), solo se `avvisa_manutentore = 1`.

Gli indirizzi vuoti o duplicati si scartano. Se dopo la cascata non resta alcun
destinatario, l'avviso non parte e la riga finisce nel log come configurazione
incompleta — non come errore.

### 7.4 Invio

Una email per riga di piano, non un digest raggruppato: i destinatari cambiano
riga per riga e gli impianti sono pochi (l'ordine di grandezza atteso è dieci
per divisione).

Contenuto: struttura, divisione, impianto, voce di piano, riferimento normativo,
data di scadenza, giorni mancanti o di ritardo, data dell'ultimo intervento
registrato. Oggetto che nomina la struttura, perché il mittente è lo stesso per
tutte.

L'invio passa da `posta.invia()`, unico punto da cui esce la posta.

La riga in `impianti_avvisi_inviati` si scrive **solo a invio riuscito**: un
fallimento SMTP viene ritentato nell'ora successiva invece di perdere l'avviso.

### 7.5 Interruttore per struttura

Nuova chiave `avvisi_impianti_attivi` in `strutture_config`, predefinita a
`'1'`. L'avviso puntuale è il motivo per cui la funzione esiste; chi non lo
vuole lo spegne. La chiave si affianca a `avvisi_scadenza_attivi`, che continua
a governare solo il digest degli apparecchi.

### 7.6 Digest e report PDF esistenti

`_invia_digest` guadagna una sezione "IMPIANTI" alimentata da
`prossime_scadenze_impianti`, e `export_service.genera_report_scadenze_pdf` una
sezione gemella. Entrambe compaiono solo se ci sono righe, e rispettano
`avvisi_impianti_attivi`.

## 8. Catalogo delle periodicità standard

`impianti_catalogo.py`, costante indicizzata per tipo di impianto:

```python
CATALOGO = {
    'elettrico': [
        {'nome': 'Verifica impianto di terra', 'mesi': 24, 'riferimento': 'DPR 462/01'},
        {'nome': 'Prova interruttori differenziali', 'mesi': 6, 'riferimento': 'CEI 64-8'},
    ],
    'antincendio': [
        {'nome': 'Controllo estintori', 'mesi': 6, 'riferimento': 'UNI 9994-1'},
        {'nome': 'Controllo idranti', 'mesi': 6, 'riferimento': 'UNI 10779'},
        {'nome': 'Verifica rivelazione incendi', 'mesi': 6, 'riferimento': 'UNI 11224'},
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
```

Alla creazione di un impianto, le voci del suo tipo sono proposte come caselle da
spuntare; le voci scelte diventano righe di `impianti_scadenze` con
`periodicita_mesi` e `riferimento_normativo` precompilati e una prima
`prossima_scadenza` da confermare. Da quel momento ogni riga è modificabile
singolarmente, e il catalogo non la tocca più: aggiornare la costante non
riscrive i piani esistenti.

La stessa proposta è richiamabile in seguito da `/impianti/<id>/piano/catalogo`,
che mostra solo le voci non ancora presenti nel piano.

**Le periodicità e i riferimenti normativi qui sopra vanno confermati prima
dell'implementazione** (vedi § 13).

## 9. Isolamento e migrazioni

### 9.1 Isolamento

- `impianti` porta `struttura_id`; ogni elenco lo filtra e applica il filtro di
  divisione.
- Ogni rotta con un `<id>` chiama `models.impianto_accessibile()` prima di
  leggere o scrivere.
- Le query sulle tabelle figlie fanno sempre JOIN su `impianti`.
- Gli allegati (documenti e verbali) passano da
  `models.upload_subdir('impianti')`, cioè `uploads/strutture/<id>/impianti/`,
  l'unico prefisso che `/uploads/<path>` sa isolare. Nome del file sempre da
  `secure_filename()`, estensioni in whitelist.
- Ogni operazione significativa chiama `log_attivita()`.

### 9.2 Migrazioni

Tutto in `models.apply_schema_updates()`, idempotente:

1. i sette `CREATE TABLE IF NOT EXISTS` e i relativi indici;
2. le quattro `ALTER TABLE divisioni`, ciascuna protetta dal controllo di
   esistenza della colonna già usato in `apply_schema_updates()`;
3. `DROP VIEW IF EXISTS prossime_scadenze_impianti` seguito dal `CREATE VIEW`,
   così un aggiornamento della vista si propaga senza intervento manuale;
4. `PRAGMA user_version = 270`.

Le stesse definizioni vanno anche in `schema.sql`, per le installazioni nuove.

### 9.3 Importazione di un'altra installazione

`importa_installazione.py` deve imparare le nuove tabelle, altrimenti importare
una 2.7 perde tutti gli impianti in silenzio. Serve:

- chiavi naturali: `manutentori` per struttura + ragione sociale, `impianti` per
  struttura + nome, `impianti_componenti` per impianto + descrizione,
  `impianti_documenti` per impianto + filename, `impianti_scadenze` per impianto
  + nome, `impianti_interventi` per impianto + scadenza + data;
- copia degli allegati di `impianti_documenti.filepath` e
  `impianti_interventi.verbale_path` nel perimetro della nuova struttura;
- `impianti_avvisi_inviati` **non** si importa: è storia dello scheduler di
  origine, e reimportarla sopprimerebbe avvisi legittimi nel bersaglio.

Va aggiornato anche il perimetro allegati di `struttura_service.py`, che governa
export e cancellazione di una struttura.

## 10. Impatto sui file esistenti

| File | Modifica |
|---|---|
| `app.py` | registra `impianti_bp` in `create_app()` |
| `models.py` | `impianto_accessibile()`; nuove tabelle, indici, vista e colonne di `divisioni` in `apply_schema_updates()`; `upload_subdir` accetta `'impianti'` |
| `schema.sql` | le stesse definizioni per le installazioni nuove; `PRAGMA user_version = 270` |
| `scheduler.py` | task `impianti_alerts` e `_send_impianti_alerts`; sezione IMPIANTI in `_invia_digest` |
| `export_service.py` | sezione impianti nel report PDF delle scadenze; libretto impianto |
| `manutenzioni.py` | filtro `origine` e UNION nello scadenzario |
| `templates/base.html` | voce di menu, barra desktop e menu ridotto |
| `templates/partials/` | frammento delle scadenze: colonna "oggetto", righe di entrambe le origini |
| `strutture_bp.py` | interruttore `avvisi_impianti_attivi` nella configurazione della struttura |
| `admin.py` | campi nuovi delle divisioni nel form di divisione |
| `importa_installazione.py` | nuove tabelle, chiavi naturali, allegati |
| `struttura_service.py` | perimetro allegati esteso a `uploads/strutture/<id>/impianti/` |
| `config.example.json` | `version` a 2.7.0 |
| `CLAUDE.md` | tabella dei blueprint, elenco dei servizi, tabelle chiave |

## 11. Test

Nuovo `tests/test_impianti.py`:

- **Isolamento fra strutture:** l'admin della struttura A non vede né raggiunge
  gli impianti di B — elenco, dettaglio, upload di un documento, download di un
  documento, riga di piano, intervento. Ogni tentativo per id diretto risponde
  403 o 404, mai il contenuto.
- **Isolamento fra divisioni:** l'utente assegnato alla divisione X non vede gli
  impianti della divisione Y della stessa struttura.
- **Permessi:** l'utente legge l'impianto della sua divisione, registra un
  intervento e carica un documento; non modifica anagrafica né piano, non
  dismette, non cancella documenti.
- **Ricalcolo della scadenza:** intervento con esito positivo su riga periodica →
  `prossima_scadenza` avanza di N mesi *dalla data dell'intervento*; su riga una
  tantum → `attiva = 0`; esito negativo → nessun avanzamento.
- **Avvisi:** la stessa soglia non si ripete; il rinnovo della scadenza riarma
  l'avviso del ciclo nuovo; su una riga già scaduta da mesi parte solo la soglia
  più alta; un fallimento di invio non scrive la riga in
  `impianti_avvisi_inviati`.
- **Destinatari:** cascata completa, e cascata con `divisioni.email` assente,
  `email_extra` assente, `avvisa_manutentore = 0`; nessun destinatario → nessun
  invio, nessuna eccezione.
- **Vista:** le priorità di `prossime_scadenze_impianti` coincidono con quelle di
  `prossime_scadenze` a parità di giorni rimasti; gli impianti dismessi e le
  righe con `attiva = 0` non compaiono.
- **Scadenzario unificato:** il filtro `origine` seleziona le righe attese e il
  totale di `tutto` è la somma delle due origini.

## 12. Fuori scope per la 2.7.0

Esplicitamente non si fa, per tenere la versione implementabile:

- import AI di documenti di impianto (progetti, verbali) — l'import unificato
  resta agli apparecchi;
- lettura dei verbali di manutenzione impianto dalla casella IMAP;
- esposizione degli impianti nelle API REST `/api/v1`;
- QR code per impianto;
- fusione di impianti duplicati;
- gestione dei costi come budget o consuntivo: `costo` si registra e si mostra
  nello storico, non alimenta report;
- allegati sui componenti;
- scadenze calcolate su contatori (ore di funzionamento, cicli) invece che su
  date.

## 13. Questioni aperte

**Le periodicità e i riferimenti normativi del catalogo (§ 8) sono una proposta
da confermare** prima di scrivere `impianti_catalogo.py`. Non sono una
consulenza normativa: vanno verificati con chi risponde della conformità negli
impianti gestiti, tipo per tipo. Il resto del design non dipende dai valori — il
catalogo è una costante e le righe di piano sono modificabili una per una —
quindi la conferma può arrivare durante l'implementazione, purché arrivi prima
del rilascio.
