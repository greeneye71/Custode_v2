# Changelog

Tutte le modifiche rilevanti al progetto sono documentate in questo file.
Formato basato su [Keep a Changelog](https://keepachangelog.com/it/1.0.0/).
Versioning basato su [Semantic Versioning](https://semver.org/lang/it/).

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
