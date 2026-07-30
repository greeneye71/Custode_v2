# Changelog

Tutte le modifiche rilevanti al progetto sono documentate in questo file.
Formato basato su [Keep a Changelog](https://keepachangelog.com/it/1.0.0/).
Versioning basato su [Semantic Versioning](https://semver.org/lang/it/).

---

## [2.6.0] - 2026-07-30

Release dedicata all'isolamento fra strutture e al ciclo di vita di una struttura.

### Corretto

- **Un admin o tecnico senza struttura attiva vedeva gli apparecchi di tutte le
  strutture.** `_get_divisione_filter()` restituiva "nessun filtro" invece di "nessun
  dato", in quattro copie divergenti (apparecchi, manutenzioni, verifiche, export). Lo
  stato si raggiungeva sia eliminando una struttura sia semplicemente disattivandola.
  Il caso piu' grave era l'esportazione: per un superadmin non impersonante
  restituiva davvero "nessun filtro", cioe' i dati di tutte le strutture; le altre tre
  rotte fallivano gia' chiuse (nessun risultato). Ora il filtro e' uno solo, in
  `models.py`, e l'assenza di scope significa nessun dato.
- Lo stesso difetto in `models.apparecchio_accessibile()`, il controllo che protegge i
  download degli allegati.
- **Lo stesso difetto in nove rotte per singolo record (dodici query) — e li' si poteva
  anche scrivere e cancellare.** Le rotte di dettaglio, modifica, dismissione, caricamento
  foto e documenti di un apparecchio, e di modifica ed eliminazione di manutenzioni e
  verifiche, avevano ciascuna la propria copia del controllo, scritta come
  `AND (struttura_id = ? OR ? IS NULL)`: senza struttura attiva la condizione era vera
  per qualunque riga di qualunque struttura, e il controllo di divisione che seguiva
  escludeva esplicitamente i ruoli `admin`, `superadmin` e `tecnico`. Non serviva
  alcuno stato anomalo per arrivarci: **un tecnico assegnato a piu' strutture, nei
  minuti fra il login e la scelta della struttura, poteva leggere e dismettere
  apparecchi di una struttura a cui non era assegnato**, ed eliminare manutenzioni e
  verifiche altrui. Un admin la cui struttura fosse stata disattivata poteva fare
  altrettanto. Ora tutte passano da `apparecchio_accessibile()`, che era gia' l'unico
  posto in cui la regola andava scritta. I due caricamenti — foto e documenti — il
  controllo di divisione non l'avevano affatto, nemmeno per gli utenti semplici: ora
  ce l'hanno, quindi un utente non puo' piu' allegare file a un apparecchio di una
  divisione che non gli e' assegnata, pur restando nella sua struttura.
- La stessa famiglia negli import: `import_bp.analizza()` saltava il controllo di
  divisione per il ruolo `admin` (che poteva cosi' attribuire un import alla divisione
  di un'altra struttura, file compreso), passava al thread di analisi la struttura
  della sessione invece di quella dell'import, e l'apparecchio individuato
  automaticamente dall'AI veniva usato senza verificarne lo scope — mentre
  l'associazione scelta a mano accanto veniva verificata. Il risultato poteva essere
  una manutenzione di una struttura scritta su un apparecchio di un'altra.
- `utenti.struttura_id` passa da `ON DELETE SET NULL` a `RESTRICT`: una cancellazione
  diretta sul database non puo' piu' lasciare un admin o un utente senza struttura.
  Per chi ne aveva gia' una NULL (installazioni aggiornate da uno schema anteriore
  alla 2.6): se esiste esattamente una struttura attiva vengono assegnati a quella;
  se non ce n'e' nessuna restano cosi, con un avviso nel log; con due o piu'
  strutture attive vengono disattivati, segnalati anch'essi nel log.
- **L'archivio di una struttura conteneva il superadmin del deployment ed ogni
  tecnico, hash della password compreso, anche senza alcun rapporto con la
  struttura esportata.** `esporta_struttura` riusa la primitiva di cancellazione
  con il predicato invertito: quella lascia intenzionalmente fuori chi ha
  `struttura_id NULL` (superadmin e tecnici), perche' nella direzione
  cancellazione quell'account non deve sparire. Nella direzione esportazione
  significava che finiva nell'archivio consegnato a terzi. Ora l'esportazione
  toglie anche questi account dalla copia (`tecnici_strutture` segue in
  cascata): un archivio contiene solo gli utenti della struttura esportata,
  tecnici compresi, a prescindere da un'eventuale assegnazione.
- **L'archivio di una struttura conteneva le chiavi delle API AI e le credenziali
  SMTP del deployment in chiaro**, seminate in `strutture_config` da
  `strutture_bp.nuova()` per ogni struttura creata dall'interfaccia (o inserite
  dalla configurazione SMTP): sono dell'operatore del deployment, non della
  struttura, anche se la riga e' formalmente sua. Ora l'esportazione azzera dalla
  copia ogni chiave di `strutture_config` il cui nome contiene `api_key`,
  `password`, `token` o `secret` — per schema del nome, non un elenco delle
  chiavi note, cosi' una chiave futura vi rientra da sola.
- **Un'installazione promossa da single a multi-struttura con `toggle_modalita.py`**
  (che cambia solo il flag di configurazione, senza spostare gli allegati)
  produceva un archivio senza quegli allegati — il sottoalbero
  `uploads/strutture/<id>/` che l'esportazione copia non esiste ancora — e la
  pagina di conferma della cancellazione diceva "0 file allegati saranno
  cancellati" mentre le righe referenziavano ancora i verbali di manutenzione: la
  cancellazione che seguiva li perdeva per sempre, perche' l'unica copia
  (l'archivio) non li aveva mai avuti, e il messaggio finale indirizzava a
  `pulisci_uploads.py`, che li cancellava perche' non piu' referenziati da
  nessuna riga. Ora l'esportazione e la cancellazione si rifiutano (e la scheda
  non arriva nemmeno a mostrare la pagina di conferma) finche' quegli allegati
  non vengono spostati manualmente sotto `uploads/strutture/<id>/`. Un singolo
  riferimento incrociato a un'altra struttura (per esempio una risalita nel
  percorso) resta invece un'anomalia isolata di una riga, segnalata come prima
  senza bloccare l'operazione.
- `toggle_modalita.py` dichiarava "single-struttura" quando la chiave di
  configurazione mancava, mentre il resto del progetto (compreso il punto sopra)
  tratta l'assenza della chiave come "multi": uno strumento e l'applicazione
  potevano descrivere due modalita' diverse per la stessa installazione.

### Aggiunto

- **Scheda della struttura** (`/strutture/<id>`): conteggi, spazio occupato, utenti e
  tecnici assegnati. Riattivazione di una struttura disattivata dall'elenco.
- **Esportazione** di una struttura in un archivio con la forma di un'installazione,
  reimportabile con `importa_installazione.py`. Disponibile anche da sola, non solo
  come passaggio della cancellazione. Limite noto: il reimporto non ripristina il
  logo (`strutture.logo_path` e il relativo file) ne' lo storico import
  (`import_history`, dietro il flag opt-in `--con-import-history`) — i dati restano
  nell'archivio, solo non vengono riscritti automaticamente in questi due punti.
- **Cancellazione completa** di una struttura: apparecchi, manutenzioni, verifiche,
  documenti, accessori, import, divisioni, configurazione, token, utenti e allegati.
  Tre freni in serie: la struttura dev'essere gia' disattivata, l'archivio viene
  prodotto prima di toccare qualsiasi cosa, e il nome va scritto a mano. I tecnici
  sopravvivono, perdendo solo l'assegnazione. Il registro attivita' sopravvive,
  slegato dalla struttura. La cancellazione dei file segue le righe del database, non
  un `rmtree` dell'intero albero: un file gia' fuori dal perimetro della struttura
  (per esempio lasciato da un'importazione interrotta) non viene toccato e resta sul
  disco — per questo `pulisci_uploads.py`, qui sotto, fa parte di questa stessa
  release e non della prossima.
- **`pulisci_uploads.py`**: elenca i file sotto `uploads/` che nessuna riga del
  database referenzia. Elenca soltanto, salvo `--elimina` — che chiede comunque
  conferma e si rifiuta se il database non referenzia alcun percorso pur essendoci
  file sotto `uploads/`: e' il segno di un database vuoto o di un percorso
  configurato male, non di un'installazione senza allegati.

### Modificato

- Il logo precedente viene cancellato quando se ne carica uno nuovo, e
  `percorso_logo_struttura()` verifica che il percorso resti dentro `uploads/`.

- **L'avvio su un database non ancora migrato dice cosa fare.** Le migrazioni
  autonome (`migrate.py` e i singoli `migrate_v*.py`) restano una scelta
  dell'operatore — fanno un backup e possono rinominare colonne, quindi non vengono
  applicate da sole all'avvio. Ma finora, se non erano state eseguite, l'applicazione
  moriva in `init_db()` con `no such column: descrizione`, perche' `schema.sql` crea
  un indice su una colonna che `migrate_v1_2.py` deve ancora ottenere rinominando
  `codice_interno`. Un messaggio che non dice nulla, nel momento peggiore: ora
  l'errore nomina `python migrate.py --check` e rimanda al README. Nessun
  cambiamento su cosa riesce e cosa no.

### Limiti noti

- Il reimporto di un archivio non ripristina logo e storico import (vedi
  l'esportazione qui sopra).
- L'archivio di una struttura non contiene mai il superadmin ne' i tecnici (nemmeno
  quelli effettivamente assegnati): dopo un reimporto le assegnazioni vanno rifatte a
  mano dal nuovo deployment. Scelta deliberata (vedi il punto sopra): un tecnico e' un
  account condiviso fra strutture, non di proprieta' di una sola, e portarne l'hash
  della password fuori dal deployment per il rapporto di assegnazione — disponibile,
  non di proprieta' — non sembra un compromesso ragionevole.
- L'archivio di una struttura non contiene la configurazione sensibile (chiavi delle
  API AI, credenziali SMTP): va reinserita a mano dopo un reimporto.
- **Il travaso degli allegati di un'installazione promossa da single a multi resta da
  fare**: `toggle_modalita.py` cambia solo il flag. Fino a quando gli allegati non
  vengono spostati a mano sotto `uploads/strutture/<id>/`, l'esportazione e la
  cancellazione di quella struttura si rifiutano (vedi sopra) invece di procedere su
  un archivio incompleto.

Dove l'isolamento non arriva ancora — noto, misurato, non corretto in questa release:

- **`pulisci_uploads.py --elimina` cancella le pagine di un import in attesa di
  revisione.** Gli import multipagina salvano le pagine sotto `uploads/import/pages_*`
  e le referenziano da `import_preview.dati_estratti`, che non e' una colonna di
  percorso: lo strumento le dichiara orfane. Se poi si conferma quell'import, la
  manutenzione nasce senza verbale allegato, **senza errore e senza avviso**. Il PDF
  di partenza resta in `import_history`, quindi si recupera rifacendo l'import.
  Conviene chiudere gli import in sospeso prima di usare `--elimina`.
- **`email_monitor` puo' agganciare un verbale ricevuto via email all'apparecchio di
  un'altra struttura.** La ricerca parte con lo scope giusto, ma se dentro la struttura
  non trova nulla prosegue senza filtro; il risultato alimenta un inserimento
  automatico, senza revisione umana. E' il piu' insidioso dei tre perche' non c'e'
  nessuno a guardare.
- **Gli allegati sotto `uploads/import/` non sono isolati per struttura.** `/uploads/`
  sa proteggere solo i percorsi nella forma `strutture/<id>/...`: i documenti caricati
  per un import sono leggibili da qualunque utente autenticato, di qualunque struttura.
- **Un utente spostato da una struttura a un'altra lascia la propria email nel registro
  dell'archivio.** L'esportazione non annota piu' le identita' che rimuove (vedi sopra),
  ma la primitiva di cancellazione, che l'esportazione riusa per togliere le altre
  strutture, continua a farlo: un admin che ha lavorato sulla struttura A e oggi
  appartiene alla B lascia il proprio indirizzo nelle voci di registro della A. Il
  contenuto e' molto piu' debole del caso chiuso in questa release — e' l'indirizzo di
  chi quella struttura l'ha davvero amministrata, non di un estraneo — ma resta un dato
  personale che esce dal deployment.
- **`importa_installazione.py` assegna una struttura anche agli utenti `tecnico` che
  importa**, e non importa `tecnici_strutture`. Quel tecnico non riesce ad accedere
  finche' un admin non lo riassegna, e — avendo una struttura — verrebbe cancellato
  insieme ad essa, contro la garanzia che i tecnici sopravvivono. Non riguarda gli
  archivi prodotti da questa applicazione, che non contengono tecnici; riguarda
  l'assorbimento di un'installazione estranea.

---

## [2.5.2] - 2026-07-30

Release correttiva urgente, un solo difetto. Chi ha gia' installato la 2.5.x su un
database aggiornato non e' interessato; chi aggiorna da una versione anteriore alla
2.2 deve passare da qui.

> **Precisazione aggiunta il 30/07/2026.** Questa correzione riguarda la migrazione
> incrementale che `apply_schema_updates()` applica all'avvio. Non sostituisce le
> migrazioni autonome: un'installazione anteriore alla v2.0 va prima portata avanti
> con `python migrate.py` (che crea un backup e, fra le altre cose, rinomina
> `apparecchi.codice_interno` in `descrizione`), altrimenti l'avvio si interrompe
> prima di arrivare qui. Fino alla 2.6.0 lo faceva con un messaggio criptico.

### Corretto

- **La migrazione v2.2 svuotava la tabella `utenti`** partendo da uno schema anteriore
  alla 2.0. `INSERT INTO utenti SELECT ...` non nominava le colonne di destinazione:
  con una `utenti` vecchia che ha meno colonne della nuova (manca `struttura_id`)
  SQLite rifiuta l'inserimento, e siccome `ALTER TABLE RENAME` e `CREATE TABLE` sono
  DDL gia' in autocommit, il rollback del gestore d'errore non li annullava. Restava
  una `utenti` vuota con i dati reali in `utenti_old_v22`: l'applicazione si avviava
  regolarmente e nessuno riusciva piu' ad accedere, amministratori compresi.
  Quando invece i conteggi coincidevano per caso — una colonna aggiunta a mano
  nell'installazione di partenza al posto di `struttura_id` — l'inserimento riusciva
  disallineando i valori, e il contenuto di quella colonna finiva in `struttura_id`.
  Ora le colonne di destinazione sono nominate esplicitamente e l'elenco e' ridotto
  all'intersezione fra vecchia e nuova tabella, con le colonne scartate segnalate nel
  log invece di essere perse in silenzio.

  Se l'aggiornamento e' gia' stato tentato e la tabella `utenti_old_v22` esiste ancora
  nel database, i dati sono recuperabili: ripristinare il backup precedente
  all'aggiornamento e ripetere l'aggiornamento con questa versione.

---

## [2.5.1] - 2026-07-28

Release di rifinitura delle stampe: correzioni emerse dalla revisione finale del
branch 2.5 e dal primo uso sul campo. Nessuna modifica allo schema del database.

### Aggiunto

- **Descrizione dell'apparecchio** nelle stampe di inventario, PDF ed Excel. Marca,
  modello e matricola da soli non bastano a capire di che macchina si tratta per chi
  non conosce a memoria il parco installato.
- **Colonna Tipo** nel report che lo scheduler allega alle email. E' l'unico prospetto
  che elenca manutenzioni e verifiche insieme: senza, due scadenze dello stesso
  apparecchio risultano identiche tranne che nella data, e chi riceve l'email non puo'
  capire quale girare alla ditta di assistenza e quale allo studio di ingegneria.
- **Workflow GitHub Actions** per la review automatica delle pull request e per le
  menzioni `@claude` in issue e commenti.

### Modificato

- **Corpo delle tabelle a 8 pt** (righe da 5,5 mm) in tutti i prospetti e nel report
  email: quaranta apparecchi stanno in una pagina sola, contro i trentasette di prima,
  e resta larghezza per la colonna Descrizione senza stringere le altre.
- Il **logo della struttura** viene scalato in proporzione entro 40 mm di larghezza e
  15 di altezza, e la testata si sposta a destra. Prima era vincolata la sola altezza:
  un logo a banda usciva largo 150 mm, sopra il nome della struttura.
- La logica che decide l'ambito di una stampa (quale struttura, quali divisioni) vive
  ora in un solo punto, condiviso fra inventario e scadenze.

### Corretto

- Un **intervallo di date libero che parte nel passato** — "cosa scade in tutto il
  2026" — faceva comparire la stessa scadenza sia fra le scadute sia fra quelle in
  arrivo, con il totale in calce che la contava due volte.
- Il **report email** aveva due definizioni di "oggi": l'etichetta del periodo in ora
  locale, il confine delle query in UTC. Nella fascia notturna le due potevano indicare
  giorni diversi.

---

## [2.5.0] - 2026-07-28

### Aggiunto

- **Stampe** — quattro prospetti PDF pensati per il foglio A4: inventario generale,
  inventario di divisione, scadenze manutenzioni e scadenze verifiche. Ogni prospetto
  riporta marca, modello, matricola e ubicazione; l'inventario generale delle strutture
  multi-divisione e' raggruppato per reparto con i conteggi. Per admin, tecnico e
  superadmin l'inventario generale copre l'intera struttura, incluse le divisioni
  disattivate — a differenza dell'elenco a video, che le nasconde.
- **Periodo delle scadenze** a scelte rapide (30 giorni, 90 giorni, entro l'anno in corso
  o il prossimo) oppure con intervallo di date libero, che include entrambi gli estremi
  indicati. Le scadute compaiono sempre in testa, prima di quelle in arrivo.
- **`report_service.py`** — motore di stampa indipendente da Flask e dal database:
  riceve righe gia' filtrate e restituisce byte, quindi serve sia la stampa manuale sia
  il report che lo scheduler allega alle email.
- **Logo della struttura** nella testata dei prospetti, caricabile dalla configurazione.
- **Colonna di spunta** e **spazio per data e firma**, opzionali.
- **Versione Excel** di tutti e quattro i prospetti.
- **Prima rete di test automatici del progetto** (pytest): motore di stampa, normalizzazione
  dei caratteri, confini di visibilita' sulle rotte.

### Modificato

- Il report email dello scheduler ora usa lo stesso motore delle stampe manuali: il
  documento che arriva in posta e' identico a quello generato a mano, logo della
  struttura compreso, e resta un solo generatore da mantenere.
- Nuova colonna `strutture.logo_path` (migrazione idempotente).

---

## [2.4.0] - 2026-07-27

Release dedicata all'isolamento fra strutture, alla migrazione da installazioni
v1.x e a una serie di funzioni che risultavano rotte in esercizio. Tutte le
correzioni sono state verificate eseguendo l'applicazione, non solo per lettura
del codice.

### Aggiunto

- **`importa_installazione.py`** — strumento da riga di comando che assorbe
  un'altra installazione MedInventory in questa, come nuova struttura, allegati
  compresi. Pensato per far confluire un'installazione monostruttura già in
  esercizio dentro un deployment multi-struttura.
  - La sorgente non viene mai modificata: si legge uno snapshot preso con
    `sqlite3.backup()` da connessione read-only, coerente anche a installazione
    accesa.
  - Regge sorgenti di **versioni diverse**: le colonne si risolvono per
    introspezione più una mappa di rinomini noti (`codice_interno` →
    `descrizione`), le assenti prendono un default, le sconosciute vengono
    segnalate, i valori fuori dai vincoli `CHECK` normalizzati ed elencati.
  - `--dry-run` mostra il piano senza scrivere; backup automatico del database
    di destinazione prima della prima scrittura; import in transazione unica,
    con rimozione dei file copiati in caso di rollback.
  - Idempotente: ogni entità ha una chiave naturale, quindi una seconda
    esecuzione con `--in-struttura` non duplica nulla. Creare una struttura con
    un nome già esistente viene rifiutato, indicando le alternative.
  - Opzioni: `--struttura-nome`, `--in-struttura`, `--con-log`, `--con-config`,
    `--con-import-history`, `--senza-file`, `--senza-utenti`,
    `--reset-password`, `--se-esiste`, `--report`, `--target`, `--db`,
    `--uploads`.
- **`models.apparecchio_accessibile()`** — verifica in un punto solo struttura e
  divisione di un apparecchio, da usare prima di servire o scrivere qualsiasi
  cosa a esso collegata.
- **`auth.operazione_globale_required`** — decoratore per le operazioni che
  agiscono sull'intero database. Le installazioni a struttura singola mantengono
  l'accesso da parte dell'admin.
- **`import_history.struttura_id`** — colonna esplicita per l'isolamento degli
  import (migrazione idempotente con backfill).

### Sicurezza — isolamento fra strutture

- **Download accessibili da altre strutture**: documenti apparecchio, verbali di
  manutenzione, documenti di verifica e QR code venivano serviti conoscendo il
  solo id. Ora passano tutti da `apparecchio_accessibile()`. Il download dei
  documenti di verifica era anche privo del controllo di path traversal.
- **Import leggibili ed eseguibili da altre strutture**: le rotte di anteprima,
  stato, esecuzione e coda email non verificavano la proprietà del record. Ora
  passano da `get_import_in_scope()`. L'isolamento non poteva basarsi su
  `divisione_id`, che è NULL per gli import da email: da qui la nuova colonna
  `struttura_id`.
- **Elenchi non filtrati**: divisioni nell'import verifiche, abbinamento per
  matricola, e la tendina apparecchi mostrata all'admin includevano dati di
  tutte le strutture.
- **Override dal form non validati** per admin e superadmin: l'`apparecchio_id`
  scelto a mano ora viene verificato per ogni ruolo.
- **Operazioni globali esposte all'admin di struttura**: backup, download del
  backup, ripristino, azzeramento totale e parziale, configurazione globale e
  chiavi AI di default erano dietro `@admin_required`. Un admin di una struttura
  poteva scaricare o cancellare i dati di tutti gli altri.
- **Scritture fuori dalle divisioni assegnate**: un utente poteva creare o
  spostare record in reparti a cui non aveva accesso.
- **Allegati fuori dall'area isolata**: gli upload di import, verbali e verifiche
  generati dall'AI finivano in cartelle non scoped, mentre `/uploads/<path>` sa
  isolare solo il prefisso `strutture/<id>/`.

### Corretto

- **Migrazione v1.x → v2 che rendeva l'applicazione inutilizzabile.** SQLite
  ≥ 3.26 riscrive le foreign key delle tabelle figlie quando si rinomina il
  padre. Le migrazioni v2.1, v2.2 e v2.3 non usavano
  `PRAGMA legacy_alter_table`, così 12 riferimenti su 9 tabelle finivano a
  puntare a `utenti_old` / `divisioni_old`, eliminate subito dopo. Con
  `foreign_keys=ON` ogni INSERT falliva e il login restituiva 500
  (`no such table: main.utenti_old`). Aggiunta la protezione mancante e, in
  `models._ripara_fk_orfane()`, la riparazione automatica all'avvio per i
  database già migrati.
- **Statistiche mensili della dashboard che contavano tutto lo storico.** In una
  f-string il doppio segno di percentuale non viene consumato e arriva a SQLite
  come formato letterale, rendendo il confronto sempre vero: un mese con un solo
  intervento da 100 € riportava 1.099 €, e il grafico dei costi collassava in
  un'unica barra.
- **Cinque form che restituivano 400** per assenza del token CSRF: azzeramento
  totale e parziale del database, eliminazione manutenzione, eliminazione
  verifica, eliminazione divisione.
- **Digest scadenze mai inviato**: il task girava ogni 86400 secondi mentre la
  condizione richiede le 7:00, quindi si allineava all'ora di avvio
  dell'applicazione senza mai centrare la finestra.
- **Coda email invisibile in multi-struttura**: i verbali importati via IMAP
  venivano salvati senza struttura e non comparivano a nessuno.
- **Script da riga di comando in crash su Windows**: stampavano accenti e
  caratteri di riquadro senza forzare UTF-8 su stdout, con `UnicodeEncodeError`
  sotto cp1252 appena l'output veniva rediretto su file o log.
- **Struttura predefinita creata senza nome** dalla migrazione: le
  configurazioni reali contengono `structure_name` valorizzato a stringa vuota, e
  `config.get(chiave, default)` non ricade sul default in quel caso.
- **`PRAGMA user_version` fermo a 200** anche su database portati a v2.3: ora
  ogni migrazione allinea il proprio codice.
- **Filtro divisioni negli export** esteso a `tecnico` e `superadmin`.
- **`prossima_scadenza`** ora validata come data anche nel form manutenzioni.

### Modificato

- `requirements.txt` dichiara `httpx`, usato da `strutture_bp` e `admin` ma
  presente solo come dipendenza indiretta di `anthropic`.
- Rimosso `modalita_avanzata_required`, diventato inefficace da quando tutte le
  strutture sono in modalità avanzata.
- `.gitignore` esclude l'intera cartella `.claude/`.
- `CLAUDE.md` e `AGENTS.md` riscritti sullo stato reale del progetto: blueprint
  mancanti, i quattro ruoli, la separazione fra `config.json` e
  `config.local.json`, le regole di isolamento e il funzionamento dello
  strumento di importazione.

---

## [2.0.0] - 2026-04-03

### Aggiunto — Architettura multi-struttura (multi-tenant)

- **Tabella `strutture`** — gestione di strutture/clienti indipendenti con codice univoco, modalità operativa (`standard` / `ingegneria_clinica`), email notifiche e flag attiva.
- **Tabella `strutture_config`** — configurazione per-struttura (AI provider, SMTP, report) con fallback al config globale.
- **Ruolo `superadmin`** — accesso globale a tutte le strutture; può impersonare qualsiasi struttura tramite banner di impersonazione.
- **Impersonazione struttura** — il superadmin può operare nel contesto di una struttura specifica senza cambiare account.
- **Divisioni e utenti scoped** — ogni divisione e utente appartiene a una struttura; i dati sono completamente isolati tra strutture.
- **Flag `single_struttura`** in `config.json` — compatibilità backward con installazioni v1.x: disabilita UI multi-struttura e forza modalità `ingegneria_clinica`.

### Aggiunto — REST API

- **Blueprint `api_bp`** su `/api/v1` — 5 endpoint autenticati con Bearer token.
- **Tabella `api_tokens`** — token SHA-256 con scope `read` / `read write`, scadenza opzionale, log ultimo utilizzo.
- **Endpoint**: `GET /apparecchi`, `GET /apparecchi/<id>`, `GET /scadenze`, `GET /manutenzioni`, `POST /manutenzioni`.
- **UI gestione token** in Strutture → Config → Token API (solo superadmin).

### Aggiunto — Sicurezza

- **Rate limiting login** — blocco per IP dopo 5 tentativi falliti in 15 minuti; sbloccabile da admin in Amministrazione → Sicurezza.
- **Tabella `login_attempts`** — registrazione di ogni tentativo con IP, email ed esito.
- **`logout_ovunque`** — invalida tutte le sessioni attive della struttura corrente.
- **`@superadmin_required`**, **`@admin_struttura_required`**, **`@modalita_avanzata_required`** — tre nuovi decoratori di autorizzazione.

### Aggiunto — Modalità ingegneria clinica

- **QR code apparecchi** — generazione e download QR code con URL assoluto alla scheda apparecchio (`/apparecchi/<id>`).
- **Audit log avanzato** — filtri per utente, entità, intervallo date; export CSV; paginazione 50 voci.
- **Report PDF schedulati** — generazione automatica PDF scadenzario per struttura, inviato via email con frequenza configurabile (giornaliera/settimanale/mensile).

### Aggiunto — Scheduler

- **Task `report_schedulati`** — ogni ora verifica se inviare report PDF per-struttura.
- **`_invia_digest`** aggiornato per supporto per-struttura con SMTP e frequenza configurabili.
- **`_decrypt_smtp_password`** — helper centralizzato per decifratura password SMTP con Fernet/SHA-256.

### Modificato

- **`auth.py`**: `admin_required` accetta ora `('admin', 'superadmin')`; sessione arricchita con dati struttura.
- **`ai_service.py`**: `get_ai_config(struttura_id=None)` per config AI per-struttura con fallback globale.
- **`app.py`**: `APP_VERSION = "2.0.0"`; registra `strutture_bp` e `api_bp`; `inject_globals()` espone `g_struttura`, `g_struttura_modalita`, `single_struttura`, `strutture_list`.
- **`admin.py`**: aggiunta dashboard superadmin, pagina sicurezza IP, audit log con filtri struttura.

### Migrazione

- **`migrate_v2_0.py`** — aggiunge tabelle `strutture`, `strutture_config`, `api_tokens`, `login_attempts`; aggiunge colonna `struttura_id` a `divisioni`, `utenti`, `log_attivita`; crea struttura default per installazioni v1.x.

---

## [1.4.3] - 2026-03-29

### Modificato — Schema database

- **Vincolo UNIQUE rimosso dalla sola colonna `matricola`** (`schema.sql`, `migrate_v1_4.py`):
  apparecchi di modelli diversi possono condividere la stessa matricola (numero di serie del
  costruttore, non globalmente univoco). Il vincolo è stato spostato sulla coppia composita
  `UNIQUE(modello, matricola)`, che garantisce comunque l'unicità all'interno di uno stesso modello.
- **Validazione aggiornata** (`apparecchi.py`): il controllo di duplicato nel form ora verifica
  la combinazione `modello + matricola` anziché la sola matricola. Il messaggio di errore è stato
  aggiornato di conseguenza.
- **Migrazione** (`migrate_v1_4.py`): ricrea la tabella `apparecchi` con il nuovo vincolo,
  preservando tutti i dati e gli indici esistenti. Eseguire prima di avviare la nuova versione.

---

## [1.4.2] - 2026-03-28

### Corretto — Robustezza e affidabilità

- **Nessun handler HTTP per 404/403/413/500** (`app.py`): aggiunto set completo di error handler
  che restituisce una pagina standalone (`templates/errors/error.html`) senza dipendenza dal
  context processor. Previene le eccezioni non gestite che mostravano stack trace all'utente.
- **`session.permanent` senza `PERMANENT_SESSION_LIFETIME`** (`app.py`): aggiunto
  `PERMANENT_SESSION_LIFETIME = timedelta(hours=session_lifetime_hours)`. In precedenza Flask
  applicava la sua default (31 giorni) indipendentemente dal valore configurato.
- **Eccezioni DB silenziate in `query_one`, `query_all`, `execute`** (`models.py`): aggiunti
  blocchi `try/except` che registrano l'errore con logger prima di rilanciare l'eccezione.
  Permette di correlare errori di produzione ai log senza perdere il tracciato.
- **`sqlite3.connect()` senza timeout nell'email monitor** (`email_monitor.py`): aggiunti
  `timeout=10` a tutte le connessioni dirette aperte dal thread di monitoraggio email.
  Senza timeout, un lock DB poteva bloccare il thread scheduler indefinitamente.
- **Logging applicativo assente** (`app.py`): aggiunta funzione `_setup_logging()` che configura
  un `RotatingFileHandler` su `logs/medinventory.log` (5 MB × 5 file di backup). L'handler
  si attiva all'import del modulo prima della creazione dell'app.
- **`config.example.json` ancora alla versione 1.1.6**: aggiornato a 1.4.2.

---

## [1.4.1] - 2026-03-28

### Miglioramento — Import AI asincrono

- **Analisi AI non bloccante** (`import_bp.py`): l'operazione di import non occupa più
  un thread HTTP per 15–50 secondi. Il file viene salvato e il record `import_history`
  creato in ~1 secondo; l'elaborazione (classificazione + analisi AI + popolamento preview)
  prosegue in un thread daemon separato. Il browser viene reindirizzato a una pagina di
  attesa che fa polling ogni 2 secondi verso il nuovo endpoint `/import/<id>/stato`.
- **Pagina di attesa** (`templates/import/attendi.html`): nuovo template con spinner
  Bootstrap, barra di progresso animata e redirect automatico alla preview al termine.
  In caso di errore mostra il messaggio e un link per riprovare.
- **Cleanup import bloccati** (`models.py`): al riavvio del server, eventuali record
  `import_history` rimasti in stato `processing` vengono marcati automaticamente come
  `failed` (i thread vengono terminati allo shutdown).

### Miglioramento — Capacità server

- **Thread Waitress** (`run_production.py`): aumentati da 4 a 8. Combinato con l'import
  asincrono, consente di gestire più utenti simultanei senza che le operazioni AI
  monopolizzino il pool di thread HTTP.

---

## [1.4.0] - 2026-03-28

### Corretto — Sicurezza

- **Path traversal nel download documenti apparecchi** (`apparecchi.py`): aggiunta validazione
  `os.path.realpath` che verifica che il percorso risolto sia dentro la cartella `uploads/`
  prima di invocare `send_from_directory`. Previene accesso a file arbitrari dal DB.
- **Path traversal nelle operazioni backup** (`admin.py`): i tre endpoint
  `backup_scarica`, `backup_ripristina`, `backup_elimina` ora rifiutano qualunque filename
  contenga `/`, `\` o `..`, oltre al check `startswith`/`endswith` già presente.
- **Limite dimensione upload mancante** (`app.py`): aggiunto `MAX_CONTENT_LENGTH = 32 MB`
  alla configurazione Flask, che impedisce upload di file enormi.

### Corretto — Validazione input

- **Porta di rete accettava valori fuori range** (`apparecchi.py`): aggiunto controllo
  `1 <= porta <= 65535` dopo la conversione a intero.
- **Indirizzo IP accettava ottetti > 255** (`apparecchi.py`): sostituita regex `\d{1,3}` con
  `ipaddress.ip_address()` che valida correttamente sia IPv4 che IPv6.
- **Data intervento senza validazione formato** (`manutenzioni.py`): aggiunto
  `datetime.strptime(value, '%Y-%m-%d')` con messaggio di errore appropriato.
- **Data verifica senza validazione formato** (`verifiche.py`): stesso fix applicato.

### Corretto — Email e Scheduler

- **Thread scheduler bloccato su IMAP irraggiungibile** (`email_monitor.py`): aggiunto
  `timeout=30` alle istanze `IMAP4_SSL` e `IMAP4`. In precedenza un server IMAP che non
  rispondeva bloccava il thread del scheduler indefinitamente.

### Corretto — Backup

- **Nessun backup di sicurezza prima del ripristino** (`admin.py`): `backup_ripristina`
  ora chiama `create_backup()` sul DB corrente prima di sovrascriverlo con il ripristino.
  Il backup di sicurezza viene contato fuori dal numero di retention configurato.

### Corretto — Recovery schema DB

- **`import_preview` con FK rotta verso `_import_history_old`** (`models.py`): aggiunta
  funzione `_fix_import_tables()` chiamata a ogni avvio da `apply_schema_updates()`.
  Rileva automaticamente database con `import_history` mancante o `import_preview` con FK
  verso la tabella temporanea della migrazione v1.3.2. Corregge entrambe le condizioni
  senza intervento manuale.

---

## [1.3.4] - 2026-03-27

### Corretto — Sicurezza

- **Route `/uploads/` accessibile senza autenticazione**: qualunque utente non autenticato
  poteva scaricare verbali, foto e PDF indovinando il percorso. Aggiunto `@login_required`
  sulla route `uploaded_file` in `app.py`.
- **Controllo divisione mancante nelle route di modifica/eliminazione**: le route
  `apparecchi/modifica`, `manutenzioni/modifica`, `manutenzioni/elimina`,
  `verifiche/modifica`, `verifiche/elimina` non verificavano che l'utente avesse accesso
  alla divisione del record. Un utente non-admin poteva modificare o cancellare qualsiasi
  record indovinando l'ID. Aggiunto controllo `accessible_ids` su tutte le route. Le query
  di `modifica` sono state estese per includere `a.divisione_id`; le query di `elimina`
  ora usano un JOIN su `apparecchi`.

### Corretto — Vista `prossime_scadenze` (scadenzario)

- **Vista che restituiva tutte le manutenzioni invece dell'ultima**: il JOIN sulla vista
  `prossime_scadenze` non aveva un filtro sull'ultima manutenzione/verifica per tipo,
  producendo righe duplicate e date di scadenza errate. Aggiunta subquery correlata
  `ORDER BY data_intervento DESC LIMIT 1` per entrambi i branch (manutenzioni e verifiche).
- **Aggiornamento automatico della vista**: `apply_schema_updates()` in `models.py` esegue
  ora `DROP VIEW IF EXISTS prossime_scadenze` + `CREATE VIEW` al primo avvio, aggiornando
  i database esistenti senza migrazione manuale.

### Corretto — Logica filtro divisione

- **Badge navbar e stat dashboard mancanti per utenti non-admin senza divisione attiva**:
  il contatore delle scadenze in `auth.py` e la stat "scadenze attive" in `app.py` usavano
  una logica binaria (divisione specifica / tutto) che escludeva il caso utente non-admin
  con `divisione_attiva = None`. Corretto con la logica a tre rami coerente con gli altri
  blueprint.
- **Export scadenzario (Excel/PDF) ignorava il filtro divisione** per utenti non-admin con
  divisione attiva nulla. Corretto in `export_bp.py`.

### Corretto — Sistema di import AI

- **Classificazione AI con eccezione silenziosa**: `classify_document_type()` e
  `classify_document_type_from_pdf()` ingoiavano silenziosamente le eccezioni e restituivano
  sempre `'inventario'`, mascherando errori di rete o di configurazione AI. Le eccezioni
  propagano ora al chiamante.
- **Parsing classificazione AI non robusto**: aggiunto `_parse_classification_result()` che
  applica prima corrispondenza esatta poi substring fallback sui tre tipi validi.
- **Modello AI errato per verbali/verifiche**: `_process_verbali()` e `_process_verifiche()`
  usavano `ai_email_model` (Haiku) invece di `ai_import_model` (Sonnet). Corretto.
- **Matching matricola case-sensitive**: `_match_apparecchi()` non trovava corrispondenze
  se la matricola nel documento aveva capitalizzazione diversa dal database. Ora usa
  `LOWER()` su entrambi i lati.
- **`_execute_verbali` e `_execute_verifiche`**: corretti multipli bug di validazione:
  elementi con `_errore=True` ora scartati invece di crashare; `apparecchio_id` da form
  verificato contro le divisioni accessibili; `data_intervento`/`data_verifica` validati
  non vuoti; `tipo` normalizzato su `TIPI_VALIDI_MANUTENZIONE`, `esito` su
  `ESITI_VALIDI_VERIFICA`; `periodicita_giorni` convertito con `int(float(...))` per
  gestire valori come `"365.0"`; `prossima_scadenza` stringa vuota coerced a NULL.
- **`_execute_inventario` — match per `descrizione`**: `find_duplicates()` usava
  `match_type='esatto', confidence=1.0` per il match su `descrizione` nonostante il campo
  non sia più UNIQUE dalla v1.2.0. Corretto in `match_type='fuzzy', confidence=0.5`.
- **`analizza()` — divisione non validata**: `divisione_id` dal form non veniva convertito
  in intero né verificato contro le divisioni accessibili all'utente. Aggiunta validazione.
- **Conversione `costo` non sicura**: `float(data['costo'])` crashava su valori non numerici.
  Aggiunto try/except con fallback a `None`.

### Corretto — Monitor email

- **Default periodicità verifiche 365 invece di 730**: in `email_monitor.py` il valore di
  default per `periodicita_giorni` delle verifiche era 365 giorni (1 anno) invece dei 730
  richiesti (2 anni). Corretto.
- **Auto-calcolo `prossima_scadenza` verifiche non eseguito**: il calcolo automatico della
  data di scadenza era condizionato alla presenza di `periodicita_giorni` nell'output AI.
  Se il campo era null, la scadenza restava NULL anche con una data di verifica valida.
  Ora usa sempre il default 730 se `periodicita_giorni` manca.
- **`tipo` manutenzioni non normalizzato**: valori non validi restituiti dall'AI (es. `"repair"`)
  causavano violazione del CHECK constraint SQLite. Aggiunta normalizzazione verso
  `TIPI_VALIDI_MANUTENZIONE` con fallback `'preventiva'`.
- **`esito` verifiche non normalizzato**: stessa problematica per il campo `esito`. Aggiunta
  normalizzazione verso `ESITI_VALIDI_VERIFICA` con fallback `'positivo'`.

### Migliorato — Import inventario

- **Prompt AI inventario**: aggiunti tre nuovi campi estratti dall'AI: `codice_fornitore`,
  `garanzia_scadenza`, `contratto_manutenzione`.
- **INSERT nuovo apparecchio**: include ora `codice_fornitore`, `garanzia_scadenza`,
  `contratto_manutenzione` oltre ai campi già presenti.
- **UPDATE apparecchio esistente**: ora integra i campi mancanti con `COALESCE(campo, ?)`:
  `descrizione`, `anno_fabbricazione`, `classificazione`, `codice_fornitore`,
  `garanzia_scadenza`, `contratto_manutenzione`. I valori già presenti non vengono
  sovrascritti; quelli NULL vengono completati.

### Migliorato — Default verifiche

- **Default periodicità verifiche 365→730**: il prompt `VERIFICA_BATCH_SYSTEM_PROMPT` e il
  codice di `_execute_verifiche()` usavano 365 giorni (1 anno) come default per la
  periodicità. Allineati a 730 giorni (2 anni) come richiesto dalle normative IEC 62353.

### Migliorato — Affidabilità

- **Logging migrazioni schema**: le eccezioni in `apply_schema_updates()` ora producono un
  `logger.warning` invece di essere ingoiate silenziosamente.

---

## [1.3.3] - 2026-03-08

### Corretto

- **Bug salvataggio provider AI**: le chiavi `ai_provider`, `ai_local_base_url` e `ai_local_model`
  non erano incluse in `LOCAL_CONFIG_KEYS`, quindi venivano scartate al salvataggio di
  `config.local.json`. Selezionare un provider diverso da Anthropic non aveva effetto permanente.
- **Bug variabile `parsed_items` in `email_monitor.py`**: la variabile `parsed_items` era
  referenziata fuori dal branch manutenzioni dove era definita, causando un bug latente
  nel calcolo di `totale_righe` per il branch verifiche.
- **Code smell `'safe_name' in dir()`**: sostituito con inizializzazione esplicita a `None`
  e check diretto.

### Migliorato

- **Efficienza `_match_apparecchi()`**: sostituito N query individuali con una singola query
  batch `WHERE matricola IN (...)` per il matching degli apparecchi durante l'import.
- **Efficienza `email_queue()`**: consolidate 3 query `COUNT(*)` separate in una singola query
  con aggregazione condizionale.
- **Efficienza `email_monitor.py`**: refactoring N+1 connessioni DB nei loop di auto-import
  (verifiche e manutenzioni). Ora una singola connessione per branch con commit finale.
- **Riuso codice Fernet**: `admin.py` ora usa `get_fernet()` da `email_monitor` invece di
  duplicare la logica di derivazione chiave.
- **Riuso estrazione PDF**: `email_monitor.py` ora usa `extract_from_pdf()` da `ai_service`
  (rinominata da `_extract_from_pdf` a API pubblica) invece di reimplementare l'estrazione inline.
- **Helper `_find_apparecchio()`**: estratta funzione dedicata in `email_monitor.py` per
  eliminare la duplicazione della query di lookup matricola tra i due branch.
- **Helper `_parse_email_ai_response()`**: estratta funzione in `import_bp.py` per il parsing
  JSON delle risposte AI email, eliminando duplicazione tra `email_queue()` e `email_dettaglio()`.
- **Collision filename**: `_execute_verbali()` e `_execute_verifiche()` usano ora
  `batch_ts + index` per i nomi file, eliminando il rischio di sovrascrittura.

### Nota

- `APP_VERSION` in `app.py` era rimasto a `1.2.0`; corretto a `1.3.3`.

---

## [1.3.2] - 2026-03-07

### Aggiunto

- **Import unificato con classificazione AI**: il sistema di import ora classifica automaticamente
  il tipo di documento caricato (inventario, verbale di manutenzione, verifica di sicurezza
  elettrica) utilizzando un sistema ibrido euristico + AI.
- **Splitting automatico PDF multipagina**: per i verbali di manutenzione e le verifiche, i PDF
  multipagina vengono divisi automaticamente in singole pagine, ciascuna analizzata come documento
  separato. Le pagine vengono salvate come file individuali per l'allegamento ai record importati.
- **Import verbali di manutenzione**: il flusso di import ora supporta l'importazione diretta di
  verbali di manutenzione, creando record in `manutenzioni` con il PDF allegato come verbale.
- **Import verifiche da import unificato**: anche le verifiche di sicurezza elettrica possono
  essere importate dal flusso unificato, con allegamento automatico del PDF documento.
- **Classificazione per keyword + AI fallback** (`classify_document_type()`): analisi euristica
  con parole chiave specifiche per ogni tipo documento, con fallback alla classificazione AI
  per i casi ambigui.
- **Classificazione PDF scansionati** (`classify_document_type_from_pdf()`): classificazione
  nativa per PDF scansionati tramite Anthropic Claude.
- **Funzioni utilità PDF**: `split_pdf_pages()`, `get_pdf_page_count()`,
  `extract_text_from_pdf_page()` per manipolazione e analisi pagina per pagina.
- **Dipendenza `pypdf`**: aggiunta per la manipolazione (splitting) dei file PDF.
- **Preview multi-tipo**: la pagina di preview si adatta dinamicamente al tipo di documento,
  mostrando colonne specifiche (inventario: marca/modello/classe; verbale: data/tipo/esito;
  verifica: data/esito/periodicità). Per verbali e verifiche non matchati, dropdown per
  selezione manuale dell'apparecchio.
- **`CLASSIFICATION_SYSTEM_PROMPT`**: nuovo prompt di sistema per la classificazione AI.
- **Migrazione `migrate_v1_3_2.py`**: aggiornamento CHECK constraint su `import_history.tipo_import`
  per includere il nuovo valore `verbale_manutenzione`.

### Modificato

- **`import_bp.py`**: refactoring completo del flusso di import. La route `analizza()` ora
  classifica il documento prima dell'analisi, branching su helper dedicati per tipo
  (`_process_inventario()`, `_process_verbali()`, `_process_verifiche()`). La route `esegui()`
  gestisce l'importazione per tutti e tre i tipi (`_execute_inventario()`, `_execute_verbali()`,
  `_execute_verifiche()`).
- **`templates/import/upload.html`**: rinominato da "Import Inventario" a "Import Documenti"
  con descrizione aggiornata per il flusso unificato.
- **`templates/import/preview.html`**: template multi-tipo con layout condizionale per
  inventario, verbale e verifica.
- **`schema.sql`**: aggiornato CHECK constraint `import_history.tipo_import` con
  `verbale_manutenzione`.

---

## [1.3.1] - 2026-03-07

### Aggiunto

- **Supporto multi-provider AI**: oltre a Anthropic Claude, ora è possibile utilizzare modelli
  AI locali tramite **Ollama**, **LM Studio** o qualsiasi server **OpenAI-compatibile**.
  Configurabile dalla pagina Configurazione nel pannello admin.
- **Selezione provider AI** nel pannello Configurazione: dropdown con 4 opzioni (Anthropic Claude,
  Ollama, LM Studio, Altro OpenAI-compatibile). L'interfaccia si adatta dinamicamente mostrando
  i campi pertinenti al provider selezionato.
- **Caricamento modelli disponibili**: pulsante "Carica modelli" nel pannello Configurazione che
  interroga il server AI locale per elencare i modelli installati, con dropdown di selezione rapida.
- **Nuovi campi configurazione**: `ai_provider`, `ai_local_base_url`, `ai_local_model`.
- **`check_ai_configured()`**: funzione helper in `ai_service.py` che verifica la corretta
  configurazione del provider AI (chiave API per Anthropic, URL e modello per provider locali).
- **Fallback automatico PDF scansionati**: per i provider locali che non supportano l'analisi
  diretta di PDF, il sistema estrae prima il testo con pdfplumber e poi lo invia al modello AI.
  I PDF puramente scansionati (immagine) richiedono comunque Anthropic Claude.

### Modificato

- **`ai_service.py`**: refactoring completo con astrazione provider. Tutte le funzioni AI
  (`analyze_inventory_with_ai`, `parse_verbale_with_ai`, `analyze_verifiche_with_ai`, ecc.)
  ora accettano un parametro `config` opzionale e instradano automaticamente le chiamate
  al provider configurato.
- **`import_bp.py`**: usa `check_ai_configured()` al posto del controllo diretto della chiave API.
- **`verifiche.py`**: usa `check_ai_configured()` e passa `config` alle funzioni AI.
- **`email_monitor.py`**: propaga `app_config` a tutte le chiamate AI nella catena di elaborazione.
- **`admin.py`**: aggiunta gestione dei nuovi campi provider nel salvataggio configurazione.
- **`templates/admin/configurazione.html`**: interfaccia AI completamente riscritta con
  visibilità condizionale dei campi in base al provider selezionato, hint contestuali e
  caricamento dinamico dei modelli locali via fetch API.

---

## [1.3.0] - 2026-03-06

### Aggiunto

- **Verbale PDF allegato alle manutenzioni**: ogni manutenzione può ora avere un file PDF
  allegato (verbale dell'intervento). Upload tramite il form di creazione/modifica,
  download dalla scheda dettaglio apparecchio (colonna "Verb." nella tabella manutenzioni)
  e dal form di modifica.
- **Allegato automatico da email**: quando il monitor IMAP riceve un verbale di manutenzione
  via email, il PDF originale viene automaticamente allegato alla manutenzione creata
  (`verbale_path`), oltre a essere analizzato dall'AI per l'estrazione dei dati.
- **Route download verbale** (`GET /manutenzioni/<id>/verbale`): endpoint dedicato per
  scaricare il PDF del verbale allegato.
- **`migrate_v1_3.py`**: script di migrazione che aggiunge la colonna `verbale_path TEXT`
  alla tabella `manutenzioni` e crea la cartella `uploads/verbali/`.

### Schema database

- `manutenzioni.verbale_path TEXT` — percorso relativo al file PDF del verbale (nullable)

### Migrazione

```bash
python migrate_v1_3.py
```

---

## [1.2.0] - 2026-03-04

### Aggiunto

- **Campo `descrizione`**: sostituisce `codice_interno` (UNIQUE rimosso). Testo libero, nessun
  vincolo di unicità — lo stesso valore può essere assegnato a più apparecchi.
- **Stato `da_sostituire`**: nuovo valore nel CHECK di `apparecchi.stato`, con badge arancio
  dedicato (`badge-da_sostituire`). Gli apparecchi in questo stato rimangono visibili nello
  scadenzario (esclusi solo i `dismesso`).
- **Toggle "Dismessi"** nella barra filtri della lista apparecchi: interruttore Bootstrap
  (`mostra_dismessi=1`) che mostra/nasconde i dismessi preservando gli altri filtri attivi.
  Di default nascosti.
- **Accessori**: ogni apparecchio può avere zero o più accessori (tabella `accessori`).
  Ogni accessorio ha: `descrizione` (obbligatoria), `produttore`, `modello`, `matricola`.
  Sezione dedicata nel form di inserimento/modifica con righe aggiungibili/rimovibili
  dinamicamente via JavaScript. Visualizzazione in tabella nella scheda dettaglio.
  Eliminazione a cascata con l'apparecchio (`ON DELETE CASCADE`).
- **`migrate_v1_2.py`**: script di migrazione con backup automatico, rename-copy del schema
  `apparecchi`, ricreazione vista `prossime_scadenze`, creazione tabella `accessori`,
  integrity check post-migrazione.

### Modificato

- **Ricerca** in lista apparecchi: estesa al campo `descrizione` (oltre a matricola, marca,
  modello, ubicazione, fornitore).
- **Dropdown marca** in lista: ora include le marche dei dismessi quando il toggle è attivo.
- **Import AI** (`INVENTORY_SYSTEM_PROMPT`): campo `codice_interno` → `descrizione` nel prompt
  e nella duplicate detection.
- **Export Excel/PDF**: intestazione e dato della colonna `Cod. Interno` → `Descrizione`.
- **`static/css/style.css`**: aggiunta classe `.badge-da_sostituire` (arancio `#f97316`).

### Schema database

- `apparecchi.codice_interno TEXT UNIQUE` → `apparecchi.descrizione TEXT`
- `apparecchi.stato CHECK` esteso con `'da_sostituire'`
- Indice `idx_apparecchi_codice_interno` → `idx_apparecchi_descrizione`
- Vista `prossime_scadenze`: colonna `codice_interno` → `descrizione`
- Nuova tabella `accessori` con FK CASCADE su `apparecchi(id)`

### Migrazione

```bash
python migrate_v1_2.py
```

---

## [1.1.6] - 2026-02-25

### Aggiunto

- **`config.local.json`**: separazione della configurazione utente dal config di sistema.
  Contiene tutte le impostazioni personalizzabili (chiave API Anthropic, modelli AI, credenziali
  IMAP/SMTP, chiavi crittografiche, organizzazione, porta, ecc.). Non viene mai sovrascritto
  durante gli aggiornamenti.
- **`config.local.example.json`**: template per la creazione di `config.local.json` su nuove
  installazioni.
- **`config.json`** ridotto ai soli default di sistema (`version` + path database/upload/backup):
  ora è sicuro sovrascriverlo durante un aggiornamento senza perdere le impostazioni utente.
- **`.gitignore`**: creato con esclusione di `config.local.json` (credenziali), `venv/`,
  `data/`, `uploads/`, `backups/`, `logs/` e cache Python/OS.
- **Migrazione automatica**: al primo avvio dopo l'aggiornamento, se `config.local.json` non
  esiste, `load_config()` lo crea automaticamente migrando i campi utente dal vecchio
  `config.json` e generando le chiavi crittografiche se assenti.

### Modificato

- **`app.py` — `load_config()`**: carica `config.json` (base) e lo fonde con
  `config.local.json` (priorità alle impostazioni locali).
- **`app.py` — `save_config()`**: scrive esclusivamente su `config.local.json`; non modifica
  mai `config.json`.

---

## [1.1.5] - 2026-02-25

### Aggiunto

- **Paginazione HTMX corretta su Apparecchi, Manutenzioni e Verifiche**: i pulsanti di
  navigazione tra pagine (precedente, numerici, successivo) ora aggiornano correttamente
  sia le righe della tabella che il blocco paginazione. Il bug precedente lasciava invariato
  il footer con i numeri di pagina dopo ogni click. Fix tramite HTMX Out-of-Band swap
  (`hx-swap-oob`) nei partial `apparecchi_table.html`, `manutenzioni_table.html` e
  `verifiche_table.html`.
- **Frecce «/» e ellissi nella paginazione di Manutenzioni e Verifiche**: allineamento
  al comportamento già presente in Apparecchi (pulsanti precedente/successivo e `…` per
  liste con molte pagine).
- **Contatore totale risultati nel footer paginazione** di Manutenzioni e Verifiche
  (già presente in Apparecchi).

### Corretto

- **Import AI — risposta JSON troncata**: aumentato `max_tokens` da 4096 a 8192 in
  `analyze_inventory_with_ai()` per evitare troncamenti su inventari con molti apparecchi.
- **Import AI — markdown code fences**: aggiunta gestione esplicita del caso in cui Claude
  restituisce il JSON avvolto in ` ```json ... ``` `. Il parser ora estrae il contenuto
  prima di tentare il parsing.
- **Import AI — messaggio di errore JSON più diagnostico**: in caso di risposta non
  parseable, il messaggio flash include ora la lunghezza della risposta e i primi 200
  caratteri per facilitare il debug.
- **Import AI — mappatura colonna "Seriale" → matricola**: migliorato il system prompt
  di `INVENTORY_SYSTEM_PROMPT` con una sezione dedicata "REGOLE MAPPATURA COLONNE" che
  elenca tutte le varianti italiane/inglesi di "numero di serie" (Seriale, S/N, SN,
  Serial Number, ecc.) e distingue esplicitamente le colonne "Codice"/"Codice Interno"
  (→ `codice_interno`) dalle colonne seriale (→ `matricola`).
- **`setup.sh` — installazione dipendenze con spazi nel path**: il bootstrap pip ora usa
  `python -m pip` invece del binario `pip` direttamente, e aggiunge un controllo esplicito
  con tentativo di recupero via `ensurepip` se pip non è disponibile nel venv (problema
  frequente con Python 3.13 su alcune distribuzioni Ubuntu/Xubuntu).

---

## [1.1.4] - 2026-02-22

### Aggiunto

- **Supporto accesso remoto via Cloudflare Tunnel**: MedInventory è ora accessibile da internet
  in modo sicuro senza aprire porte sul router e senza IP pubblico fisso.
- **`CLOUDFLARE_TUNNEL.md`**: guida completa per la configurazione di Cloudflare Tunnel.
  Copre installazione di `cloudflared` su Windows e Linux, autenticazione, creazione tunnel,
  configurazione `config.yml` specifica per MedInventory (porta 5000), routing DNS,
  installazione come servizio (Windows SCM e Linux systemd), Cloudflare Access per
  autenticazione aggiuntiva, manutenzione e risoluzione problemi.
- **`DOCUMENTAZIONE.md`**: aggiunta sezione 15 "Accesso remoto — Cloudflare Tunnel" con
  schema del flusso, prerequisiti, link a `CLOUDFLARE_TUNNEL.md` e tabella comparativa
  tra le opzioni di accesso remoto disponibili.

### Modificato

- **`app.py` — `ProxyFix` middleware**: aggiunto `werkzeug.middleware.proxy_fix.ProxyFix`
  (`x_for=1, x_proto=1, x_host=1`) per correggere `request.remote_addr`, lo schema e l'host
  quando l'app è raggiunta tramite Cloudflare Tunnel, Nginx o qualsiasi reverse proxy.
  Senza questa modifica tutti gli accessi remoti venivano registrati nel log attività come
  `127.0.0.1` invece dell'IP reale del client.

---

## [1.1.3] - 2026-02-22

### Aggiunto

- **`setup.sh`**: equivalente Linux di `setup.bat`. Controlla Python 3.10+, crea il virtual
  environment, installa le dipendenze, esegue `seed.py` e mostra l'indirizzo IP locale al termine.
- **`install_service.sh`**: equivalente Linux di `install_service.bat`, usa **systemd** al posto
  di NSSM. Menu identico (8 opzioni), scelta dell'utente di esecuzione (utente corrente, root o
  utente specifico), creazione del file unit `/etc/systemd/system/medinventory.service`,
  verifica stato post-avvio con `systemctl is-active`, log tramite `journalctl` e file
  `logs/service_stderr.log`.

---

## [1.1.2] - 2026-02-22

### Aggiunto

- **Reset database completo** (`POST /admin/reset-database`): nuovo pulsante nella pagina
  Configurazione che cancella l'intero database, crea automaticamente un backup di sicurezza,
  ricrea lo schema da `schema.sql`, esegue il seed con utente admin e due divisioni di default,
  invalida la sessione corrente e reindirizza al login.
- **Reset database parziale** (`POST /admin/reset-parziale`): cancella solo i dati di inventario
  (apparecchi, manutenzioni, verifiche elettriche, documenti, storico import, configurazioni
  email, log attività) mantenendo utenti, divisioni e sessioni attive. Crea anch'esso un backup
  automatico prima dell'operazione.
- **Card "Zona Pericolosa"** nella pagina Configurazione con due pulsanti distinti (giallo per il
  reset parziale, rosso per il reset completo), ognuno con la descrizione dell'impatto.
- **Modal di conferma con campo di testo** per entrambe le operazioni: il pulsante di conferma
  rimane disabilitato finché l'utente non digita esattamente `RESET`.
- **Opzione 7 in `install_service.bat`** — "Mostra log errori": visualizza direttamente nel
  terminale il contenuto di `logs\service_stderr.log` senza dover aprire manualmente il file.
- **Verifica `waitress` in `install_service.bat`**: controllo preventivo che il pacchetto
  sia installato nel virtual environment prima di procedere con l'installazione del servizio.
- **Configurazione account di servizio in `install_service.bat`**: durante l'installazione,
  scelta tra `LocalSystem` (default) e un account utente specifico (`.\utente` o `DOMINIO\utente`)
  per risolvere i problemi di permessi su cartelle utente e unità di rete.
- **Verifica stato reale dopo l'avvio in `install_service.bat`**: dopo `nssm start`, il bat
  attende 4 secondi e usa `sc query` per verificare se il servizio è effettivamente in esecuzione,
  mostrando un messaggio di errore con il percorso del log in caso di fallimento.
- **Controllo servizio già installato in `install_service.bat`**: avviso esplicito se il servizio
  è già presente, evitando installazioni duplicate.

### Corretto

- **`install_service.bat` — trailing backslash in `AppDirectory`**: `%~dp0` include sempre
  una `\` finale che in alcune versioni di NSSM causava un path malformato per `AppDirectory`.
  Ora viene rimossa con `if "%APP_DIR:~-1%"=="\" set APP_DIR=%APP_DIR:~0,-1%`.
- **`install_service.bat` — logica di rilevamento NSSM**: la logica precedente non impostava
  correttamente la variabile `%NSSM%` in tutti i casi. Riscritta con `set NSSM=` vuoto e
  assegnazione condizionale tramite `where nssm` e fallback su `nssm.exe` locale.
- **`install_service.bat` — messaggio di avvio sempre positivo**: il messaggio `"Servizio avviato."`
  veniva stampato incondizionatamente anche in caso di errore. Ora il bat controlla `%errorlevel%`
  e lo stato reale del servizio tramite `sc query`.
- **`install_service.bat` — opzione "Mostra stato"**: sostituito `nssm status` con `sc query`
  per una verifica più affidabile e indipendente dalla versione di NSSM installata.

---

## [1.1.0] - 2025-xx-xx

### Aggiunto

- **Modulo Verifiche di Sicurezza Elettrica** (`verifiche.py`): gestione collaudi periodici
  (IEC 62353 / CEI 62-148) parallela alle manutenzioni, con esito positivo/negativo/con_riserva,
  allegato PDF, import AI batch e scadenzario unificato.
- **Launcher Windows con System Tray** (`launcher.pyw`): avvia `run_production.py` in background,
  icona verde/rossa nella system tray, menu contestuale per avvio browser, riavvio e log.
- **Selezione modelli AI** (`ANTHROPIC_MODELS` in `admin.py`): dropdown precompilato nel pannello
  Configurazione per scegliere il modello Claude per import inventario e parsing email/verifiche.
- **Vista `prossime_scadenze` unificata**: la vista SQL usa UNION tra `manutenzioni` e `verifiche`
  per il calcolo delle scadenze in tutto il sistema (dashboard, badge navbar, scadenzario).
- **Script di migrazione** `migrate_v1_1.py`: aggiornamento idempotente da v1.0 a v1.1 con
  backup automatico del database originale.
- **Versione dinamica nel footer**: letta da `config.json` tramite il context processor.
- **Classificazione automatica email**: il monitor IMAP distingue verbali di manutenzione
  da rapporti di verifica elettrica prima di invocare il parser AI appropriato.

### Modificato

- Schema database: aggiunta tabella `verifiche`, aggiunto campo `soggetto_verifica` in
  `apparecchi`, aggiornato `CHECK` in `import_history` per accettare `verifica_elettrica`.
- Dashboard: aggiunte 3 nuove statistiche (verifiche scadute, in scadenza, senza verifica)
  e grafico stato verifiche elettriche.

---

## [1.0.0] - 2025-xx-xx

### Prima versione

- Inventario apparecchi elettromedicali con CRUD completo
- Gestione manutenzioni con scadenzario a 5 livelli di priorità
- Import AI da Excel/PDF/CSV tramite Claude Sonnet
- Monitoraggio email IMAP con parsing PDF tramite Claude Haiku
- Gestione multi-divisione con controllo accessi per ruolo
- Export report Excel e PDF
- Backup automatico settimanale con retention configurabile
- Pannello amministrazione: utenti, divisioni, configurazione, log attività
- Installazione come servizio Windows tramite NSSM
