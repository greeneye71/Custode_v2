# MedInventory - Documentazione

**Gestione Apparecchi Elettromedicali**
*Versione 2.6.2 - by Studio Bergamaschi*

---

## Novità v1.4.2

### Robustezza e affidabilità
- **Pagine di errore HTTP** — errori 404, 403, 413 e 500 mostrano ora una pagina grafica chiara invece di un'eccezione Flask grezza o una pagina bianca.
- **Durata sessione corretta** — `PERMANENT_SESSION_LIFETIME` è ora derivato da `session_lifetime_hours` (default 8h). In precedenza Flask applicava 31 giorni indipendentemente dalla configurazione.
- **Logging su file** — tutti i log applicativi vengono scritti in `logs/medinventory.log` con rotazione automatica (5 MB × 5 file). Facilita la diagnosi di problemi in produzione.
- **Errori DB registrati** — le funzioni `query_one`, `query_all`, `execute` registrano ora l'errore e la query SQL prima di rilanciarla, rendendo diagnosticabili gli errori di database.
- **Timeout connessioni DB nell'email monitor** — le connessioni dirette SQLite nel thread di monitoraggio email hanno ora `timeout=10`. Senza timeout un lock DB prolungato bloccava il thread scheduler.

---

## Novità v1.4.1

### Import AI asincrono
- **Nessun blocco durante l'analisi** — caricare un file non occupa più il server per 15–50 secondi. L'elaborazione AI avviene in background; il browser mostra una pagina di attesa con aggiornamento automatico e viene reindirizzato alla preview al termine.
- **Più utenti simultanei** — i thread del server sono stati portati da 4 a 8. Con l'import asincrono, un'analisi AI in corso non rallenta più gli altri utenti.

---

## Novità v1.4.0

### Sicurezza
- **Protezione path traversal nei download** — il download di documenti allegati agli apparecchi ora verifica che il percorso risolto sia dentro la cartella `uploads/`, impedendo accessi a file arbitrari.
- **Validazione backup più rigorosa** — le operazioni di scarica, ripristino ed eliminazione backup rifiutano filename con separatori di percorso o sequenze `..`.
- **Limite dimensione upload** — i caricamenti di file sono ora limitati a 32 MB; file più grandi vengono rifiutati prima di essere salvati.

### Validazione input
- **Porta di rete** — valori fuori dall'intervallo 1–65535 vengono ora rifiutati con messaggio di errore.
- **Indirizzo IP** — validazione sostituita con `ipaddress.ip_address()` che controlla correttamente gli ottetti (precedentemente accettava `256.999.0.1`).
- **Date intervento e verifica** — il formato `YYYY-MM-DD` è ora verificato esplicitamente; date malformate producono un messaggio di errore invece di essere inserite in DB.

### Email e scheduler
- **Thread scheduler bloccato su IMAP** — aggiunto timeout di 30 secondi sulla connessione IMAP. In precedenza un server IMAP non raggiungibile bloccava il thread del scheduler indefinitamente.

### Backup
- **Backup di sicurezza automatico prima del ripristino** — l'operazione "Ripristina" crea ora un backup datato del database corrente prima di sovrascriverlo, permettendo un recupero in caso di ripristino errato.

### Stabilità
- **Riparazione automatica schema import** — l'avvio rileva e corregge automaticamente database con la tabella `import_history` mancante o con la foreign key di `import_preview` corrotta (problema lasciato dalla migrazione v1.3.2 su SQLite ≥ 3.26).

---

## Novità v1.3.4

### Sicurezza
- **Accesso ai file protetto da autenticazione** — la route `/uploads/` richiedeva ora il login; in precedenza qualunque utente non autenticato poteva scaricare verbali, foto e PDF indovinando il percorso.
- **Controllo divisione su modifica/eliminazione** — le pagine di modifica ed eliminazione di apparecchi, manutenzioni e verifiche ora verificano che l'utente abbia accesso alla divisione del record. In precedenza un utente non-admin poteva modificare o cancellare qualsiasi record indovinando l'ID nell'URL.

### Scadenzario
- **Calcolo scadenze corretto** — la vista `prossime_scadenze` considerava tutte le manutenzioni/verifiche invece dell'ultima per tipo, producendo scadenze duplicate e date errate. Ora viene considerato solo il record più recente. La correzione viene applicata automaticamente al primo avvio senza migrazione manuale.

### Import AI
- **Verbali e verifiche analizzati con il modello corretto** — ora viene usato `ai_import_model` (Sonnet) invece del modello email (Haiku).
- **Matching matricola case-insensitive** — trova corrispondenze indipendentemente da maiuscole/minuscole.
- **Validazione dati AI più robusta** — date obbligatorie, tipo intervento e esito verifica vengono ora validati e normalizzati prima dell'inserimento; valori non validi non causano più errori DB.
- **Default periodicità verifiche corretto** — il default è ora 730 giorni (2 anni) come previsto da IEC 62353, non 365.
- **Scadenza verifiche auto-calcolata** — anche via email, la `prossima_scadenza` viene sempre calcolata se mancante.
- **Import inventario più completo** — l'AI estrae ora anche `codice_fornitore`, `garanzia_scadenza` e `contratto_manutenzione`. Per gli apparecchi già presenti, i campi vuoti vengono integrati senza sovrascrivere i dati esistenti.

---

## Novità v1.3.3

- **Bug salvataggio provider AI** — le chiavi `ai_provider`, `ai_local_base_url` e `ai_local_model` non venivano salvate correttamente in `config.local.json`.
- **Miglioramenti efficienza** — query batch per matching matricole, consolidamento contatori email, singola connessione DB nei loop di auto-import.

---

## Novità v1.3.2

- **Import unificato con classificazione AI** — il sistema di import ora classifica automaticamente il tipo di documento caricato (inventario, verbale di manutenzione, verifica di sicurezza elettrica) e lo analizza con il prompt appropriato.
- **Splitting PDF multipagina** — per verbali e verifiche, ogni pagina del PDF viene trattata come un documento separato e analizzata singolarmente dall'AI. Le singole pagine vengono salvate come allegati ai record importati.
- **Import verbali di manutenzione** — possibilità di importare verbali di manutenzione direttamente dal flusso unificato, con creazione automatica dei record manutenzione e allegamento del PDF.
- **Import verifiche elettriche dal flusso unificato** — anche le verifiche di sicurezza elettrica sono importabili dal punto di ingresso unico.
- **Preview multi-tipo** — la pagina di anteprima si adatta al tipo di documento mostrando le colonne pertinenti e la possibilità di associare manualmente gli apparecchi non matchati.
- **Dipendenza `pypdf`** — aggiunta per lo splitting dei PDF.
- **Migrazione**: eseguire `python migrate_v1_3_2.py` per aggiornare il database.

## Novità v1.3.1

- **Supporto modelli AI locali** — oltre a Claude (Anthropic), ora è possibile utilizzare modelli
  AI locali tramite Ollama, LM Studio o qualsiasi server OpenAI-compatibile. Configurabile dalla
  pagina Configurazione.
- **Selezione provider AI** — dropdown nel pannello admin con visibilità condizionale dei campi:
  chiave API per Anthropic, URL server e nome modello per provider locali.
- **Caricamento modelli disponibili** — pulsante nel pannello Configurazione che interroga il
  server AI locale per mostrare i modelli installati.
- **Limitazione PDF scansionati** — i provider locali non supportano l'analisi diretta di PDF
  scansionati (immagine). Per questi documenti è necessario Anthropic Claude.

## Novità v1.3.0

- **Verbale PDF allegato alle manutenzioni** — ogni manutenzione può ora avere un file PDF
  del verbale dell'intervento. Il campo upload è disponibile nel form di creazione e modifica.
  Il verbale è scaricabile dalla scheda dettaglio apparecchio (colonna "Verb." nella tabella
  manutenzioni) e dalla pagina modifica.
- **Allegato automatico da email** — quando il monitor IMAP riceve un verbale di manutenzione
  via email, il PDF originale viene automaticamente allegato alla manutenzione creata, oltre a
  essere analizzato dall'AI per l'estrazione dei dati.
- **Migrazione** — eseguire `python migrate_v1_3.py` per aggiungere la colonna `verbale_path`
  alla tabella `manutenzioni`.

## Novità v1.2.0

- **Campo "Descrizione" libero** — il campo `codice_interno` (con vincolo UNIQUE) è stato
  sostituito da `descrizione`, testo libero senza vincoli di unicità. È possibile inserire la
  stessa descrizione su più apparecchi.
- **Nuovo stato "Da programmare sostituzione"** — aggiunto il valore `da_sostituire` alla lista
  stati degli apparecchi, con badge arancio dedicato. Gli apparecchi in questo stato continuano
  a comparire nello scadenzario.
- **Toggle "Dismessi" nella lista** — nella barra filtri della lista apparecchi è presente un
  interruttore Bootstrap che mostra/nasconde gli apparecchi dismessi senza perdere gli altri
  filtri attivi. Di default i dismessi sono nascosti.
- **Ricerca estesa a "Descrizione"** — il campo di ricerca libera include ora anche il campo
  descrizione oltre a matricola, marca, modello, ubicazione e fornitore.
- **Accessori** — ogni apparecchio può avere zero o più accessori associati (es. cavi,
  adattatori, sensori). Ogni accessorio ha: descrizione (obbligatoria), produttore, modello,
  matricola. Gli accessori si gestiscono direttamente nel form dell'apparecchio con righe
  aggiungibili/rimovibili. Sono visualizzati nella scheda dettaglio e vengono eliminati
  automaticamente con l'apparecchio.
- **Migrazione database** — eseguire `python migrate_v1_2.py` prima del primo avvio. Lo script
  crea automaticamente un backup, rinomina la colonna, aggiorna il CHECK, ricrea la vista
  `prossime_scadenze` e crea la tabella `accessori`.

### Migrazione da v1.1.x

```bash
# Eseguire UNA VOLTA prima dell'avvio v1.2
python migrate_v1_2.py
```

---

## Novità v1.1.6

- **Separazione configurazione utente/sistema** — le impostazioni personalizzabili (chiave API,
  modelli AI, credenziali IMAP/SMTP, chiavi crittografiche) sono ora in `config.local.json`,
  mai sovrascritto dagli aggiornamenti. `config.json` contiene solo i default di sistema.
- **`.gitignore`** — creato con esclusione di `config.local.json` e delle cartelle dati/log.
- **Migrazione automatica** — al primo avvio dopo l'aggiornamento, `config.local.json` viene
  creato automaticamente con i valori presenti nel vecchio `config.json`.

## Novità v1.1.5

- **Fix paginazione HTMX** — i pulsanti di navigazione tra pagine in Apparecchi, Manutenzioni e
  Verifiche ora aggiornano correttamente anche il blocco paginazione (numero pagina corrente,
  frecce precedente/successivo). Fix tramite HTMX Out-of-Band swap.
- **Paginazione completa in Manutenzioni e Verifiche** — aggiunte frecce «/», ellissi per
  liste lunghe e contatore totale risultati, in linea con Apparecchi.
- **Import AI migliorato** — risolto troncamento JSON su inventari grandi (max_tokens 4096→8192),
  gestione markdown code fences nella risposta Claude, messaggio errore diagnostico.
- **Mappatura colonna "Seriale"** — il prompt AI ora riconosce correttamente tutte le varianti
  italiane/inglesi del numero di serie (Seriale, S/N, Serial Number, ecc.) evitando la
  confusione con la colonna "Codice Interno".
- **`setup.sh` su Python 3.13** — fix bootstrap pip per ambienti Linux dove `ensurepip` non è
  disponibile nel virtual environment.

## Novità v1.1.4

- **Accesso remoto via Cloudflare Tunnel** — MedInventory è ora accessibile da internet in modo
  sicuro senza aprire porte sul router e senza IP pubblico fisso. Guida completa in
  [`CLOUDFLARE_TUNNEL.md`](./CLOUDFLARE_TUNNEL.md).
- **`ProxyFix` middleware** — `request.remote_addr` ora restituisce l'IP reale del client anche
  quando l'app è raggiunta tramite Cloudflare Tunnel, Nginx o qualsiasi reverse proxy.

## Novità v1.1.3

- **`setup.sh`** e **`install_service.sh`** — nuovi script equivalenti per distribuzione Linux,
  con gestione del servizio tramite systemd.

## Novità v1.1.2

- **Reset database completo** — nuovo pulsante nella pagina Configurazione che cancella l'intero
  database, crea un backup automatico, ricrea l'utente admin di default e reindirizza al login.
- **Reset database parziale** — cancella solo i dati di inventario (apparecchi, manutenzioni,
  verifiche, documenti, log) mantenendo utenti e divisioni. Crea anch'esso un backup automatico.
- **`install_service.bat` riscritto e corretto** — fix del path `AppDirectory` (trailing backslash),
  fix della logica di rilevamento NSSM, aggiunto controllo `waitress`, configurazione account di
  servizio (LocalSystem vs utente specifico), verifica stato reale dopo l'avvio tramite `sc query`,
  opzione per visualizzare i log di errore direttamente dal menu.

---

## Novità v1.1.0

- **Verifiche di Sicurezza Elettrica** — nuovo modulo dedicato (IEC 62353/CEI) parallelo alle manutenzioni, con import AI batch, scadenzario unificato e allegati PDF.
- **Launcher Windows con System Tray** — `launcher.pyw` avvia e monitora il server con icona nella barra di sistema.
- **Selezione modelli AI** — dropdown precompilato nel pannello configurazione per scegliere il modello Claude per import e email.
- **Versione dinamica** — la versione visualizzata nel footer è letta da `config.json`.

### Migrazione da v1.0.0

```bash
# Eseguire UNA VOLTA prima dell'avvio v1.1
python migrate_v1_1.py
```

Lo script crea la tabella `verifiche`, aggiorna il CHECK in `import_history` e ricrea la vista `prossime_scadenze` con UNION manutenzioni + verifiche. Il database originale viene copiato automaticamente come backup.

---

## Indice

1. [Panoramica](#1-panoramica)
2. [Requisiti di sistema](#2-requisiti-di-sistema)
3. [Installazione](#3-installazione)
4. [Configurazione](#4-configurazione)
5. [Primo avvio](#5-primo-avvio)
6. [Guida utente](#6-guida-utente)
7. [Verifiche di Sicurezza Elettrica](#7-verifiche-di-sicurezza-elettrica)
8. [Pannello amministrazione](#8-pannello-amministrazione)
9. [Funzionalità AI](#9-funzionalità-ai)
10. [Monitoraggio email](#10-monitoraggio-email)
11. [Launcher Windows](#11-launcher-windows)
12. [Export e report](#12-export-e-report)
13. [Backup e ripristino](#13-backup-e-ripristino)
14. [Installazione come servizio Windows/Linux](#14-installazione-come-servizio-windows)
15. [Accesso remoto — Cloudflare Tunnel](#15-accesso-remoto--cloudflare-tunnel)
16. [Architettura tecnica](#15-architettura-tecnica)
17. [Schema database](#16-schema-database)
18. [API e route](#17-api-e-route)
19. [Risoluzione problemi](#18-risoluzione-problemi)

---

## 1. Panoramica

MedInventory è un'applicazione web per la gestione completa degli apparecchi elettromedicali. Pensata per strutture sanitarie come poliambulatori e centri medici, permette di:

- **Inventariare** tutti gli apparecchi elettromedicali con dati tecnici, classificazione e informazioni di rete
- **Registrare** gli interventi di manutenzione (preventiva, correttiva, verifica, calibrazione)
- **Monitorare le scadenze** con un sistema a 5 livelli di priorità e badge visivi
- **Importare inventari** da file Excel/PDF/CSV con analisi automatica tramite AI (Claude)
- **Ricevere verbali di manutenzione** via email con parsing automatico dei PDF allegati
- **Esportare report** in formato Excel e PDF
- **Gestire più divisioni** con controllo accessi per ruolo

L'applicazione è accessibile da qualsiasi dispositivo sulla rete locale tramite browser web (PC, tablet, smartphone).

### Caratteristiche principali

| Funzionalità | Descrizione |
|---|---|
| Multi-divisione | Gestione separata per divisioni/reparti con codifica colore |
| Ruoli utente | Amministratore (gestione completa) e Utente (solo dati) |
| Dashboard interattiva | Grafici, statistiche, scadenze imminenti |
| Import AI | Upload Excel/PDF/CSV con analisi Claude Sonnet |
| Email IMAP | Ricezione automatica verbali PDF via email |
| Scadenzario | Monitoraggio scadenze con 5 livelli di urgenza |
| Export | Report Excel e PDF per apparecchi e scadenzario |
| Backup automatico | Backup settimanale con politica di retention |
| Servizio Windows | Installabile come servizio per avvio automatico |

---

## 2. Requisiti di sistema

### Server (macchina che ospita l'applicazione)

| Requisito | Minimo | Consigliato |
|---|---|---|
| Sistema operativo | Windows 10 64-bit | Windows 10/11 64-bit |
| Python | 3.10 | 3.11 o 3.12 |
| RAM | 512 MB liberi | 1 GB liberi |
| Disco | 200 MB + spazio dati | 1 GB |
| Rete | Connessione LAN | LAN Gigabit |

### Client (macchine che accedono all'applicazione)

- Qualsiasi dispositivo con browser web moderno (Chrome, Firefox, Edge, Safari)
- Connessione alla stessa rete locale del server
- Risoluzione schermo minima: 1024x768 (ottimizzato per 1920x1080)

### Dipendenze Python

Installate automaticamente da `requirements.txt`:

| Pacchetto | Versione | Utilizzo |
|---|---|---|
| flask | >= 3.0 | Framework web |
| anthropic | >= 0.40 | API Claude (AI) |
| openpyxl | >= 3.1 | Lettura/scrittura Excel |
| fpdf2 | >= 2.8 | Generazione PDF |
| pdfplumber | >= 0.11 | Estrazione testo da PDF |
| cryptography | >= 42.0 | Cifratura credenziali IMAP |
| waitress | >= 3.0 | Server WSGI di produzione |

---

## 3. Installazione

### Installazione rapida

1. **Copiare** la cartella `Custode_v2` sul server (es. `C:\MedInventory`)

2. **Eseguire** `setup.bat` (doppio clic)
   - Crea il virtual environment Python
   - Installa le dipendenze
   - Inizializza il database
   - Mostra le istruzioni per il primo avvio

### Installazione manuale

```batch
cd C:\MedInventory

:: Creare il virtual environment
python -m venv venv

:: Attivare il virtual environment
venv\Scripts\activate

:: Installare le dipendenze
pip install -r requirements.txt

:: Inizializzare il database
python seed.py

:: Avviare l'applicazione
python app.py
```

### Verifica dell'installazione

Dopo l'avvio, aprire nel browser:
- **Dalla stessa macchina:** `http://localhost:5000`
- **Da un'altra macchina in rete:** `http://<IP-del-server>:5000`

---

## 4. Configurazione

La configurazione dell'applicazione è memorizzata nel file `config.json`, creato automaticamente al primo avvio a partire da `config.example.json`.

### Parametri di configurazione

| Parametro | Default | Descrizione |
|---|---|---|
| `app_name` | MedInventory | Nome dell'applicazione (visibile nell'interfaccia) |
| `organization` | Studio Bergamaschi | Nome dell'organizzazione sviluppatrice |
| `structure_name` | *(vuoto)* | Nome della struttura sanitaria (es. "Poliambulatorio XYZ") |
| `host` | 0.0.0.0 | Indirizzo di ascolto (0.0.0.0 = tutte le interfacce) |
| `port` | 5000 | Porta TCP del server web |
| `debug` | false | Modalità debug (solo per sviluppo) |
| `database_path` | data/database.sqlite | Percorso del database SQLite |
| `uploads_path` | uploads | Cartella per file caricati |
| `backups_path` | backups | Cartella per i backup |
| `session_lifetime_hours` | 8 | Durata della sessione utente in ore |
| `backup_retention` | 4 | Numero di backup da conservare |
| `anthropic_api_key` | *(vuoto)* | Chiave API Anthropic per Claude |
| `ai_import_model` | claude-sonnet-4-20250514 | Modello AI per import inventario |
| `ai_email_model` | claude-haiku-4-5-20251001 | Modello AI per parsing email |
| `email_check_interval_minutes` | 15 | Intervallo controllo email IMAP (minuti) |

### Chiavi segrete

Al primo avvio vengono generate automaticamente:
- `secret_key` - Chiave per la cifratura delle sessioni Flask
- `encryption_key` - Chiave per la cifratura delle password IMAP

**IMPORTANTE:** Non modificare queste chiavi dopo il primo avvio, altrimenti le sessioni attive e le password IMAP cifrate diventeranno invalide.

### Configurazione da interfaccia

L'amministratore può modificare la maggior parte dei parametri direttamente dal pannello *Configurazione* nell'interfaccia web, senza dover modificare manualmente il file JSON.

---

## 5. Primo avvio

### Credenziali predefinite

| Campo | Valore |
|---|---|
| Email | `admin@medinventory.local` |
| Password | `admin123` |

Al primo accesso verrà obbligatoriamente richiesto di cambiare la password.

### Passi consigliati dopo il primo accesso

1. **Cambiare la password** dell'amministratore (obbligatorio al primo accesso)
2. **Configurare il nome della struttura** (Amministrazione > Configurazione)
3. **Rinominare le divisioni** predefinite (Amministrazione > Divisioni)
4. **Inserire la chiave API Anthropic** se si intende usare l'import AI (Amministrazione > Configurazione)
5. **Configurare le caselle email** per il monitoraggio IMAP (Amministrazione > Config Email)
6. **Creare gli utenti** necessari (Amministrazione > Utenti)

### Regole password

Per tutti gli utenti, la password deve rispettare:
- Minimo **8 caratteri**
- Almeno **1 lettera maiuscola**
- Almeno **1 numero**

---

## 6. Guida utente

### 6.1 Dashboard

La pagina principale mostra:
- **4 schede statistiche:** totale apparecchi, scadenze attive, manutenzioni del mese, costi del mese
- **Scadenze imminenti:** le 10 scadenze più urgenti
- **Ultimi interventi:** le 10 manutenzioni più recenti
- **Grafici:** distribuzione per classificazione, costi mensili (12 mesi), interventi per tipo

Tutti i dati sono filtrati in base alla **divisione attiva** selezionata nella barra superiore.

### 6.2 Apparecchi

#### Lista apparecchi
- Ricerca per matricola, marca, modello, ubicazione
- Filtri per classificazione, stato e ubicazione
- Aggiornamento in tempo reale tramite HTMX (senza ricaricare la pagina)
- Pulsante export Excel/PDF

#### Scheda apparecchio
Ogni apparecchio contiene:

| Sezione | Campi |
|---|---|
| **Identificazione** | Matricola (univoca), descrizione (testo libero), numero inventario |
| **Accessori** | Lista accessori: descrizione, produttore, modello, matricola (righe multiple) |
| **Dispositivo** | Marca, modello, anno fabbricazione, classificazione (I, IIa, IIb, III) |
| **Collocazione** | Divisione, ubicazione, stato (funzionante / in manutenzione / da programmare sostituzione / dismesso) |
| **Rete** | Connesso a rete (si/no), IP, MAC, hostname, porta, protocollo, URL interfaccia |
| **Fornitore** | Fornitore, codice fornitore, scadenza garanzia, contratto manutenzione |
| **Documenti** | Upload di manuali, certificati, foto, report |
| **Note** | Campo testo libero |

#### Classificazione apparecchi

Secondo la Direttiva Dispositivi Medici:

| Classe | Rischio | Esempi |
|---|---|---|
| **I** | Basso | Letti, sedie a rotelle, stetoscopi |
| **IIa** | Medio-basso | ECG, ecografi, lampade chirurgiche |
| **IIb** | Medio-alto | Ventilatori, defibrillatori, pompe infusione |
| **III** | Alto | Protesi impiantabili, valvole cardiache |

#### Dismettere un apparecchio

La dismissione è un'operazione **soft-delete**: l'apparecchio non viene cancellato ma il suo stato diventa "dismesso". Gli apparecchi dismessi non compaiono nelle liste standard ma restano nel database per storico.

### 6.3 Manutenzioni

#### Tipi di manutenzione

| Tipo | Descrizione |
|---|---|
| **Preventiva** | Manutenzione programmata periodica |
| **Correttiva** | Riparazione a seguito di guasto |
| **Verifica** | Controllo di sicurezza/funzionamento |
| **Calibrazione** | Taratura strumenti di misura |

#### Registrare una manutenzione

Campi del form:
- **Apparecchio** (seleziona da elenco)
- **Tipo** intervento
- **Data intervento**
- **Tecnico/Ditta** esecutrice
- **Descrizione** dell'intervento
- **Esito** (positivo, negativo, con riserva...)
- **Costo** in euro
- **Verbale PDF** — allegato opzionale del verbale dell'intervento (solo PDF)
- **Prossima scadenza** (data)
- **Periodicità** (giorni, per calcolo automatico scadenze future)

### 6.4 Scadenzario

Lo scadenzario mostra tutte le manutenzioni con una prossima scadenza impostata. Il sistema calcola automaticamente la priorità:

| Priorità | Condizione | Colore | Badge |
|---|---|---|---|
| **Scaduto** | Data passata | Rosso (pulsante) | Animato |
| **Urgente** | Entro 7 giorni | Rosso | Fisso |
| **Attenzione** | 7-15 giorni | Arancione | Fisso |
| **Avviso** | 15-30 giorni | Giallo | Fisso |
| **OK** | Oltre 30 giorni | Verde | Fisso |

Il contatore delle scadenze critiche (scadute + urgenti) appare come badge rosso nella barra di navigazione.

### 6.5 Selezione divisione

La barra superiore contiene il selettore della divisione attiva:
- **Utenti:** vedono solo le divisioni a cui sono assegnati
- **Amministratori:** vedono tutte le divisioni + l'opzione "Tutte le divisioni"
- La divisione attiva filtra **tutti** i dati visualizzati (dashboard, liste, scadenzario)

---

## 7. Verifiche di Sicurezza Elettrica

Il modulo Verifiche gestisce i collaudi periodici di sicurezza elettrica degli apparecchi elettromedicali (norma IEC 62353 / CEI 62-148).

### Accesso

Menu laterale → **Verifiche** (icona scudo).

### CRUD manuale

- **Lista**: filtri per esito, date, ricerca testuale. Supporto HTMX per filtri in tempo reale.
- **Nuova verifica**: seleziona apparecchio, inserisci data, esito (positivo/negativo/con_riserva), tecnico, periodicità e opzionalmente allega il PDF del rapporto.
- **Modifica/Elimina**: accessibili dalla lista o dal dettaglio apparecchio.
- **Download documento**: il PDF allegato è scaricabile direttamente.

### Import AI batch

1. Vai a **Verifiche → Import AI** (`/verifiche/import`).
2. Carica un PDF (o Excel/CSV) con i dati di una o più verifiche.
3. Claude analizza il documento ed estrae: matricola, data, esito, tecnico, prossima scadenza.
4. Nella pagina di preview puoi abbinare manualmente gli apparecchi non riconosciuti e selezionare le righe da importare.
5. Clicca **Importa Selezionate** per inserire le verifiche nel database.

### Scadenzario unificato

La vista **Scadenzario** mostra manutenzioni e verifiche insieme, con badge distinto "Verifica Elettrica" (icona scudo blu). Il filtro **Tipo** include ora anche "Verifica Elettrica".

### Email automatica

Il monitor email rileva automaticamente i PDF di verifica (keyword: "sicurezza elettrica", "IEC 62353", ecc.) e li processa separatamente dalle manutenzioni, inserendo il record in `verifiche` se l'apparecchio è trovato per matricola, altrimenti accodando per revisione manuale.

---

## 8. Pannello amministrazione

Accessibile solo agli utenti con ruolo **admin**.

### 7.1 Gestione utenti

| Operazione | Descrizione |
|---|---|
| Nuovo utente | Crea utente con email, nome, cognome, ruolo, divisioni assegnate |
| Modifica utente | Aggiorna dati, ruolo, divisioni e stato attivo/disattivo |
| Elimina | **v2.6.2** — Cancella l'account con doppia conferma (vedi sotto) |
| Reset password | Genera password temporanea e forza il cambio al prossimo accesso |

#### Cancellazione di un utente (v2.6.2)

Sostituisce la vecchia disattivazione come modo di togliere di mezzo un account; la
disattivazione resta, ma dentro il modulo di modifica, per i blocchi temporanei.

La pagina di conferma dice **chi** si sta cancellando — nome, email, ruolo, struttura — e
**cosa resta a suo nome**: apparecchi inseriti, manutenzioni, verifiche, documenti, import.

Cosa succede: password resa inutilizzabile, indirizzo liberato (si puo' ricreare un account
con la stessa email), sessioni chiuse, assegnazioni a divisioni e strutture rimosse. **La
riga resta**, con `eliminato_il` valorizzata: otto colonne di altre tabelle referenziano
`utenti(id)`, e su un registro di elettromedicali «chi ha inserito questo apparecchio» non
deve sparire. L'utente cancellato non compare in nessun elenco e non puo' piu' accedere.

L'operazione **non e' reversibile** e non restituisce la password.

**Rifiuti.** Non si puo' cancellare se stessi, ne' un utente di un'altra struttura, ne' un
tecnico (si gestiscono dalla loro pagina). L'ultimo amministratore **attivo** di una
struttura non si puo' cancellare, disattivare ne' declassare a utente semplice, e lo stesso
vale per l'ultimo superamministratore attivo: senza, la struttura resterebbe senza nessuno
in grado di amministrarla. Nessuno puo' inoltre disattivarsi o declassarsi da solo.

**Ruoli:**
- `admin` - Accesso completo: gestione utenti, divisioni, configurazione, backup, log
- `utente` - Solo gestione apparecchi, manutenzioni, import, export

### 7.2 Gestione divisioni

Le divisioni sono le unità organizzative (reparti, sedi, aree):
- Ogni divisione ha: **nome**, **codice** (univoco, abbreviazione), **colore** (per identificazione visiva), **descrizione**
- Si possono attivare/disattivare
- Il conteggio apparecchi e utenti per divisione è mostrato nella lista

### 7.3 Configurazione

Editor web per i parametri di `config.json`:
- Nome applicazione e struttura
- Porta del server
- Chiave API Anthropic (mascherata per sicurezza)
- Modelli AI
- Intervallo check email
- Durata sessione
- Retention backup

**Nota:** alcune modifiche (es. porta) richiedono il riavvio dell'applicazione.

#### Zona Pericolosa

In fondo alla pagina Configurazione è presente la sezione **Zona Pericolosa** con due operazioni
irreversibili. Entrambe richiedono di digitare `RESET` nel modal di conferma prima di procedere
e creano automaticamente un backup nella cartella `backups/`.

| Pulsante | Cosa cancella | Cosa mantiene |
|---|---|---|
| **Reset Parziale** | Apparecchi, manutenzioni, verifiche, documenti, import, configurazioni email, log | Utenti, divisioni, sessioni attive |
| **Azzera Database** | Tutto il database | — (ricrea admin di default e 2 divisioni vuote) |

Dopo l'*Azzera Database* la sessione viene invalidata e l'utente viene reindirizzato al login.
Le credenziali di accesso tornano a `admin@medinventory.local` / `admin123`.

### 7.4 Configurazione email

Impostazione delle caselle IMAP per il monitoraggio automatico dei verbali:
- Una configurazione per divisione (o globale)
- Campi: account email, password (cifrata), server IMAP, porta
- Attivazione/disattivazione individuale
- Indicazione dell'ultima verifica effettuata

### 7.5 Backup

Vedi [sezione 11](#11-backup-e-ripristino).

### 7.6 Log attività

Registro cronologico di tutte le azioni degli utenti:
- Login/logout
- Creazione, modifica, eliminazione di record
- Import e analisi AI
- Backup e ripristini
- Filtrabile per: testo, entità, intervallo date
- Paginato (50 voci per pagina)

---

## 8. Funzionalità AI

MedInventory utilizza l'API Claude di Anthropic per due funzionalità:

### 8.1 Import inventario (Claude Sonnet)

**Flusso operativo:**

1. **Upload** - L'utente carica un file Excel (.xlsx), PDF o CSV contenente un elenco di apparecchi
2. **Estrazione testo** - Il sistema estrae il contenuto testuale dal file
3. **Analisi AI** - Claude Sonnet analizza il testo e lo struttura in JSON con i campi: matricola, marca, modello, classificazione, ubicazione, ecc.
4. **Ricerca duplicati** - Il sistema cerca corrispondenze nel database:
   - Match esatto per matricola
   - Match esatto per descrizione
   - Match fuzzy per marca + modello (70% confidence)
5. **Anteprima** - L'utente visualizza una tabella con tutti gli elementi estratti, contrassegnati come "nuovo" o "duplicato"
6. **Selezione** - L'utente seleziona quali elementi importare
7. **Esecuzione** - Gli elementi selezionati vengono inseriti/aggiornati nel database

### 8.2 Parsing verbali email (Claude Haiku)

**Flusso automatico:**

1. Il sistema controlla le caselle IMAP configurate (ogni N minuti)
2. Per ogni email non letta con allegato PDF:
   - Salva il PDF su disco
   - Estrae il testo con pdfplumber
   - Invia a Claude Haiku per estrarre: matricola, tipo intervento, data, tecnico, descrizione, esito, costo, prossima scadenza
3. Se trova un apparecchio corrispondente (per matricola): crea automaticamente la manutenzione
4. Se non trova corrispondenza: accoda per revisione manuale

### Requisiti

- Chiave API Anthropic valida (inseribile da Configurazione)
- Connessione internet per raggiungere l'API Anthropic
- I modelli AI possono essere personalizzati nella configurazione

---

## 9. Monitoraggio email

### Come funziona

Un processo background (scheduler) controlla periodicamente le caselle email IMAP configurate. Quando trova email con allegati PDF, li scarica e li analizza con AI per estrarre dati di manutenzione.

### Coda email

La pagina **Coda Email** (menu laterale) mostra:
- **In attesa di revisione** - Verbali che non sono stati importati automaticamente (apparecchio non trovato o dati incompleti)
- **Auto-importati** - Verbali processati e importati con successo
- **Errori** - Verbali che hanno generato errori nel processing

### Revisione manuale

Per i verbali in coda:
1. Cliccare **Revisiona**
2. Verificare/correggere i dati estratti dall'AI
3. Se l'apparecchio non è stato riconosciuto, selezionarlo manualmente dall'elenco
4. **Conferma e Importa** oppure **Scarta**

### Configurazione IMAP

Da *Amministrazione > Config Email*:

| Campo | Esempio | Note |
|---|---|---|
| Divisione | Divisione 1 | La divisione a cui associare i verbali ricevuti |
| Account email | manutenzione@azienda.it | Indirizzo completo |
| Password | ******** | Cifrata nel database con Fernet |
| Server IMAP | imap.azienda.it | Server di posta in arrivo |
| Porta | 993 | Standard per IMAP SSL |

---

## 10. Monitoraggio email

*(sezione invariata dalla v1.0 — vedi capitolo 9 per i dettagli)*

Il monitor email v1.1 classifica automaticamente gli allegati PDF in **verbale manutenzione** o **verifica di sicurezza elettrica** prima di invocare il parser AI appropriato.

---

## 11. Launcher Windows

`launcher.pyw` è un'applicazione standalone che:
- Avvia `run_production.py` in background (nessuna finestra console).
- Mostra un'icona nella system tray: **verde** se il server è raggiungibile, **rossa** altrimenti.
- Aggiorna lo stato ogni 10 secondi.

### Installazione

```bash
pip install pystray Pillow
```

### Uso

Doppio-click su `launcher.pyw` (richiede che `pythonw.exe` sia associato ai file `.pyw`).

### Menu TNA

| Voce | Azione |
|------|--------|
| Apri MedInventory | Apre il browser sull'app |
| Riavvia server | Ferma e riavvia `run_production.py` |
| Visualizza log | Apre la cartella `logs/` in Explorer |
| Esci | Ferma il server e chiude il launcher |

### Avvio automatico con Windows

Crea un collegamento a `launcher.pyw` nella cartella:
```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
```
Non richiede diritti di amministratore (porta 5000 > 1024).

### Guard doppio avvio

Se la porta è già in uso all'avvio, il launcher mostra l'icona verde adottando il server esistente senza avviare un secondo processo.

---

## 12. Export e report

### Formati disponibili

| Sezione | Excel (.xlsx) | PDF |
|---|---|---|
| Apparecchi | Si | Si |
| Manutenzioni | Si | - |
| Scadenzario | Si | Si |

### Come esportare

In ogni pagina lista, utilizzare il pulsante **Esporta** (icona download) nella barra superiore. Il file viene scaricato dal browser.

### Contenuto dei report

**Report Apparecchi:**
- Intestazione con nome struttura e data generazione
- Tabella completa: matricola, codice, marca, modello, anno, classificazione, ubicazione, stato, fornitore, IP, divisione
- Filtro automatico Excel
- Totale apparecchi

**Report Scadenzario:**
- Righe colorate per priorità (rosso scaduto, arancione attenzione, giallo avviso, verde ok)
- Conteggi riepilogativi (scaduti, urgenti, totale)

I report rispettano sempre il **filtro divisione attiva**: se si è selezionata una divisione specifica, il report conterrà solo i dati di quella divisione.

---

## 11. Backup e ripristino

### Backup automatico

Il sistema esegue un backup automatico del database ogni **domenica alle ore 03:00**. Il numero di backup conservati è configurabile (default: 4).

### Backup manuale

Da *Amministrazione > Backup*:
1. Cliccare **Crea Backup Ora**
2. Il backup viene salvato nella cartella `backups/`
3. Nome file: `medinventory_backup_YYYYMMDD_HHMMSS.sqlite`

### Operazioni disponibili

| Azione | Descrizione |
|---|---|
| **Crea** | Genera un nuovo backup immediato |
| **Scarica** | Download del file di backup |
| **Ripristina** | Sovrascrive il database attuale con il backup selezionato |
| **Elimina** | Cancella un backup specifico |

### Ripristino

**ATTENZIONE:** Il ripristino sovrascrive il database corrente. Prima del ripristino:
- Viene creata una copia di sicurezza automatica (`database.sqlite.pre_restore`)
- Se il ripristino fallisce, la copia di sicurezza viene automaticamente ripristinata
- Dopo il ripristino, è consigliato **riavviare l'applicazione**

### Politica di retention

Quando il numero di backup supera il limite configurato (`backup_retention`), i backup più vecchi vengono automaticamente eliminati. Questo avviene sia dopo un backup manuale che dopo quello automatico.

---

## 12. Installazione come servizio Windows

L'installazione come servizio consente l'avvio automatico di MedInventory all'accensione del server, senza necessità di login.

### Prerequisiti

1. **NSSM** (Non-Sucking Service Manager) - Scaricabile gratuitamente da https://nssm.cc/download
2. Copiare `nssm.exe` nella cartella dell'applicazione oppure aggiungerlo al PATH di sistema

### Procedura

1. **Eseguire** `install_service.bat` **come Amministratore** (tasto destro > Esegui come amministratore)
2. Scegliere **1. Installa il servizio**
3. Confermare l'avvio immediato

### Gestione del servizio

Lo script `install_service.bat` offre le seguenti opzioni:

| Opzione | Descrizione |
|---|---|
| 1 | Installa il servizio (avvio automatico con Windows) |
| 2 | Rimuovi il servizio |
| 3 | Avvia il servizio |
| 4 | Ferma il servizio |
| 5 | Riavvia il servizio |
| 6 | Mostra lo stato del servizio |
| 7 | Mostra log errori (`logs\service_stderr.log`) |

### Account di esecuzione

Durante l'installazione viene richiesto con quale account eseguire il servizio:

| Scelta | Account | Quando usarlo |
|---|---|---|
| 1 | `LocalSystem` | App in `C:\MedInventory` o cartella accessibile a SYSTEM |
| 2 | Account utente specifico | App in cartella utente, unità di rete mappata (es. `Z:\`) o share UNC |

Per l'account utente inserire `.\NomeUtente` per un account locale o `DOMINIO\utente` per un account di dominio.

### Diagnostica in caso di errore

Se il servizio si installa ma non si avvia:

1. Scegliere l'opzione **7** per leggere il log errori direttamente dal menu
2. Oppure aprire manualmente `logs\service_stderr.log`
3. Cause più frequenti:
   - Account SYSTEM senza permessi sulla cartella (→ usare account utente specifico)
   - `waitress` non installato nel venv (→ eseguire `setup.bat`)
   - Porta 5000 già in uso (→ modificare `port` in `config.json`)
   - `config.example.json` mancante (→ necessario per la creazione automatica di `config.json`)

### Server di produzione

In produzione, l'applicazione utilizza **Waitress** (server WSGI) invece del server di sviluppo Flask:
- Multi-thread (4 thread)
- Logging con rotazione automatica dei file (max 5 MB, 5 file)
- File di log in `logs/medinventory.log` e `logs/errors.log`

### Modalità di avvio

| Modalità | Comando | Uso |
|---|---|---|
| Sviluppo | `python app.py` | Test e debug (con reload automatico) |
| Produzione | `python run_production.py` | Uso quotidiano con logging |
| Servizio | `install_service.bat` | Avvio automatico senza login |

### Installazione su Linux

#### Setup iniziale

```bash
# Rende gli script eseguibili
chmod +x setup.sh install_service.sh

# Esegue il setup (non richiede root)
bash setup.sh
```

#### Installazione come servizio systemd

```bash
sudo bash install_service.sh
# Scegliere opzione 1 → selezionare l'utente di esecuzione → avviare
```

Lo script crea il file `/etc/systemd/system/medinventory.service` con avvio automatico.

#### Gestione del servizio (Linux)

| Opzione script | Equivalente manuale |
|---|---|
| 1 — Installa | `systemctl enable medinventory` |
| 3 — Avvia | `sudo systemctl start medinventory` |
| 4 — Ferma | `sudo systemctl stop medinventory` |
| 5 — Riavvia | `sudo systemctl restart medinventory` |
| 6 — Stato | `systemctl status medinventory` |
| 7 — Log | `journalctl -u medinventory -n 50` |

#### Account di esecuzione (Linux)

| Scelta | Quando usarla |
|---|---|
| Utente corrente | App in `/home/utente/` o cartella di proprietà dell'utente |
| root | Sconsigliato — solo se strettamente necessario |
| Utente specifico | App in `/opt/medinventory/` con utente dedicato |

#### File unit generato

```ini
[Unit]
Description=MedInventory - Gestione Apparecchi Elettromedicali
After=network.target

[Service]
Type=simple
User=<utente scelto>
WorkingDirectory=/path/to/app
ExecStart=/path/to/venv/bin/python run_production.py
Restart=on-failure
RestartSec=5
StandardOutput=append:/path/to/logs/service_stdout.log
StandardError=append:/path/to/logs/service_stderr.log

[Install]
WantedBy=multi-user.target
```

---

## 15. Accesso remoto — Cloudflare Tunnel

Per rendere MedInventory accessibile **da fuori dalla rete locale** (es. da casa, da mobile,
da un'altra sede) senza aprire porte sul router e senza un IP pubblico fisso, è possibile
usare **Cloudflare Tunnel**.

### Come funziona

```
Browser remoto → HTTPS → Cloudflare → tunnel cifrato → cloudflared → localhost:5000
```

`cloudflared` apre una connessione uscente verso Cloudflare: nessuna porta da aprire,
nessun IP fisso necessario. HTTPS e certificato SSL sono gestiti automaticamente.

### Prerequisiti

- Account Cloudflare gratuito
- Un dominio gestito da Cloudflare (es. `tuodominio.it`)
- MedInventory in esecuzione su `localhost:5000`

### Documentazione completa

Tutte le istruzioni passo-passo (installazione, configurazione, avvio come servizio
su Windows e Linux, autenticazione aggiuntiva con Cloudflare Access, manutenzione
e risoluzione problemi) si trovano nel file dedicato:

📄 **[CLOUDFLARE_TUNNEL.md](./CLOUDFLARE_TUNNEL.md)**

### Opzioni a confronto

| Soluzione | Complessità | Sicurezza | IP fisso richiesto |
|---|---|---|---|
| **Cloudflare Tunnel** | Bassa | Alta (HTTPS automatico) | No |
| **WireGuard VPN** | Media | Molto alta (traffico locale) | Sì (o DDNS) |
| Port forwarding diretto | Molto bassa | Bassa (HTTP esposto) | Sì |

> Per dati sanitari si consiglia di abilitare anche **Cloudflare Access**
> (descritto in `CLOUDFLARE_TUNNEL.md`, Parte 8) per aggiungere un layer
> di autenticazione prima della pagina di login di MedInventory.

---

## 13. Architettura tecnica

### Stack tecnologico

```
Browser (qualsiasi)
    |
    | HTTP (porta 5000)
    |
Flask + Waitress (WSGI)
    |
    +-- Jinja2 (template HTML)
    +-- Bootstrap 5 (CSS via CDN)
    +-- HTMX (aggiornamenti parziali)
    +-- Chart.js (grafici dashboard)
    |
SQLite (database file)
    |
    +-- data/database.sqlite
```

### Struttura del progetto

```
MedInventory/
|
|-- app.py                    # Punto di ingresso, factory Flask
|-- auth.py                   # Autenticazione e sessioni
|-- models.py                 # Accesso al database
|-- schema.sql                # Schema del database
|-- seed.py                   # Dati iniziali
|
|-- apparecchi.py             # CRUD apparecchi
|-- manutenzioni.py           # CRUD manutenzioni + scadenzario
|-- admin.py                  # Pannello amministrazione
|-- import_bp.py              # Import inventario + coda email
|-- export_bp.py              # Export Excel/PDF
|
|-- ai_service.py             # Integrazione Claude API
|-- email_monitor.py          # Monitoraggio IMAP
|-- scheduler.py              # Task periodici background
|-- backup_service.py         # Gestione backup
|-- export_service.py         # Generazione report
|
|-- run_production.py         # Server produzione (Waitress)
|-- setup.bat                 # Setup iniziale (Windows)
|-- setup.sh                  # Setup iniziale (Linux)
|-- install_service.bat       # Installazione servizio Windows (NSSM)
|-- install_service.sh        # Installazione servizio Linux (systemd)
|-- requirements.txt          # Dipendenze Python
|-- config.example.json       # Template configurazione
|
|-- templates/                # Template HTML (Jinja2)
|   |-- base.html             # Layout master (navbar, sidebar, footer)
|   |-- login.html            # Pagina di login
|   |-- dashboard.html        # Dashboard con grafici
|   |-- cambio_password.html  # Cambio password obbligatorio
|   |-- apparecchi/           # Pagine apparecchi (lista, form, dettaglio)
|   |-- manutenzioni/         # Pagine manutenzioni (lista, form, scadenzario)
|   |-- admin/                # Pagine admin (utenti, divisioni, config, backup, log)
|   |-- import/               # Pagine import (upload, preview, storico, coda email)
|   +-- partials/             # Fragment HTMX (tabelle per aggiornamento parziale)
|
|-- static/
|   |-- css/style.css         # Stili personalizzati
|   +-- js/app.js             # JavaScript minimale
|
|-- data/                     # Database (creato automaticamente)
|-- uploads/                  # File caricati (foto, documenti, import)
|-- backups/                  # Backup del database
+-- logs/                     # Log applicazione (in produzione)
```

### Design pattern

| Pattern | Implementazione |
|---|---|
| **Blueprint** | Ogni area funzionale e' un Blueprint Flask separato |
| **Decorator** | `@login_required` e `@admin_required` per il controllo accessi |
| **Context Processor** | Iniezione automatica di utente, divisione, config in tutti i template |
| **Division Filter** | Helper `_get_divisione_filter()` riutilizzato in tutti i blueprint |
| **HTMX Partial** | Le route verificano `request.args.get('partial')` per restituire solo la tabella |
| **Soft Delete** | Dismissione apparecchi (stato='dismesso') senza cancellazione fisica |

### Risorse esterne (via CDN)

| Risorsa | Versione | URL |
|---|---|---|
| Bootstrap CSS | 5.3.3 | cdn.jsdelivr.net |
| Bootstrap JS | 5.3.3 | cdn.jsdelivr.net |
| Bootstrap Icons | 1.11.3 | cdn.jsdelivr.net |
| HTMX | 2.0.4 | unpkg.com |
| Chart.js | 4.x | cdn.jsdelivr.net |
| Font Inter | variable | fonts.googleapis.com |

**Nota:** Le risorse CDN richiedono connessione internet al primo caricamento; i browser le memorizzano poi nella cache locale.

---

## 14. Schema database

### Tabelle principali

#### divisioni
Unita organizzative (reparti, sedi).

| Colonna | Tipo | Descrizione |
|---|---|---|
| id | INTEGER PK | Identificativo |
| nome | TEXT UNIQUE | Nome divisione |
| codice | TEXT UNIQUE | Codice abbreviato (es. DIV1) |
| colore | TEXT | Colore esadecimale (es. #0ea5e9) |
| descrizione | TEXT | Descrizione libera |
| attiva | INTEGER | 1=attiva, 0=disattivata |

#### utenti
Account utente.

| Colonna | Tipo | Descrizione |
|---|---|---|
| id | INTEGER PK | Identificativo |
| email | TEXT UNIQUE | Email (usata come username) |
| password_hash | TEXT | Hash bcrypt della password |
| nome, cognome | TEXT | Dati anagrafici |
| ruolo | TEXT | 'superadmin', 'admin', 'tecnico' o 'utente' |
| primo_accesso | INTEGER | 1=deve cambiare password |
| attivo | INTEGER | 1=attivo, 0=bloccato |
| struttura_id | INTEGER | Struttura di appartenenza (NULL per superadmin e tecnici) |
| eliminato_il | DATETIME | **v2.6.2** — Valorizzata = account cancellato. La riga resta come voce storica: otto colonne di altre tabelle referenziano `utenti(id)` |
| reset_hash | TEXT | **v2.6.2** — Impronta della password temporanea del reset. Vale **accanto** a `password_hash`, non al suo posto |
| reset_scadenza | DATETIME | **v2.6.2** — Scadenza della temporanea (30 minuti), scritta e confrontata con l'orologio del database |

#### utenti_divisioni
Associazione N:M utenti-divisioni.

| Colonna | Tipo | Descrizione |
|---|---|---|
| utente_id | INTEGER FK | Riferimento utente |
| divisione_id | INTEGER FK | Riferimento divisione |
| ruolo_divisione | TEXT | 'admin' o 'utente' per questa divisione |

#### apparecchi
Apparecchi elettromedicali.

| Colonna | Tipo | Descrizione |
|---|---|---|
| id | INTEGER PK | Identificativo |
| divisione_id | INTEGER FK | Divisione di appartenenza |
| matricola | TEXT UNIQUE | Numero di serie (identificativo principale) |
| descrizione | TEXT | Descrizione libera (nessun vincolo di unicità) |
| numero_inventario | TEXT | Numero inventario patrimoniale |
| marca | TEXT | Produttore |
| modello | TEXT | Modello del dispositivo |
| anno_fabbricazione | INTEGER | Anno di produzione |
| classificazione | TEXT | Classe: I, IIa, IIb, III |
| ubicazione | TEXT | Luogo fisico (stanza, piano...) |
| stato | TEXT | funzionante / in_manutenzione / da_sostituire / dismesso |
| connesso_rete | INTEGER | 1=connesso alla rete |
| ip_address | TEXT | Indirizzo IP |
| mac_address | TEXT | Indirizzo MAC |
| hostname | TEXT | Nome host di rete |
| fornitore | TEXT | Fornitore/distributore |
| garanzia_scadenza | DATE | Scadenza garanzia |
| contratto_manutenzione | TEXT | Riferimento contratto |
| foto_path | TEXT | Percorso foto del dispositivo |
| note | TEXT | Note libere |

#### accessori
Accessori associati agli apparecchi. Eliminazione a cascata con l'apparecchio.

| Colonna | Tipo | Descrizione |
|---|---|---|
| id | INTEGER PK | Identificativo |
| apparecchio_id | INTEGER FK | Apparecchio proprietario (CASCADE DELETE) |
| descrizione | TEXT NOT NULL | Nome/tipo accessorio |
| produttore | TEXT | Produttore dell'accessorio |
| modello | TEXT | Modello dell'accessorio |
| matricola | TEXT | Numero di serie dell'accessorio |
| created_by | INTEGER FK | Utente che ha inserito il record |
| created_at | DATETIME | Data inserimento |

#### manutenzioni
Interventi di manutenzione.

| Colonna | Tipo | Descrizione |
|---|---|---|
| id | INTEGER PK | Identificativo |
| apparecchio_id | INTEGER FK | Apparecchio interessato |
| tipo | TEXT | preventiva / correttiva / verifica / calibrazione |
| data_intervento | DATE | Data dell'intervento |
| prossima_scadenza | DATE | Data prossimo intervento previsto |
| periodicita_giorni | INTEGER | Frequenza in giorni |
| tecnico_ditta | TEXT | Chi ha eseguito l'intervento |
| descrizione | TEXT | Descrizione dell'intervento |
| esito | TEXT | Risultato dell'intervento |
| costo | DECIMAL | Costo in euro |
| verbale_path | TEXT | Percorso relativo al PDF del verbale |

### Vista: prossime_scadenze

Vista SQL precalcolata che mostra tutte le scadenze future con priorita automatica:

| Colonna calcolata | Descrizione |
|---|---|
| giorni_rimasti | Differenza in giorni tra scadenza e oggi |
| priorita | scaduto / urgente / attenzione / avviso / ok |

---

## 15. API e route

### Elenco completo delle route (54 endpoint)

#### Autenticazione
| Metodo | URL | Descrizione |
|---|---|---|
| GET/POST | `/login` | Pagina di login |
| GET | `/logout` | Disconnessione |
| GET/POST | `/cambio-password` | Cambio password |
| GET/POST | `/password-dimenticata` | **v2.6.2** — Richiesta di password temporanea via email |
| GET | `/divisione/<id>` | Cambio divisione attiva |

#### Dashboard
| Metodo | URL | Descrizione |
|---|---|---|
| GET | `/` | Dashboard principale |
| GET | `/api/health` | Health check (JSON) |

#### Apparecchi
| Metodo | URL | Descrizione |
|---|---|---|
| GET | `/apparecchi` | Lista apparecchi |
| GET/POST | `/apparecchi/nuovo` | Nuovo apparecchio |
| GET | `/apparecchi/<id>` | Dettaglio apparecchio |
| GET/POST | `/apparecchi/<id>/modifica` | Modifica apparecchio |
| POST | `/apparecchi/<id>/dismetti` | Dismissione |
| GET | `/apparecchi/duplicati` | **v2.6.1** — Elenco delle coppie sospette (admin, tecnico, superadmin) |
| GET | `/apparecchi/<id>/fondi` | **v2.6.2** — Ricerca dell'altra scheda da fondere |
| GET/POST | `/apparecchi/<id>/fondi/<altro_id>` | **v2.6.1** — Confronto ed esecuzione della fusione |
| POST | `/apparecchi/<id>/foto` | Upload foto |
| POST | `/apparecchi/<id>/documento` | Upload documento |
| GET | `/apparecchi/<id>/documento/<doc_id>/scarica` | Download documento |

#### Manutenzioni
| Metodo | URL | Descrizione |
|---|---|---|
| GET | `/manutenzioni` | Lista manutenzioni |
| GET/POST | `/manutenzioni/nuova` | Nuova manutenzione |
| GET/POST | `/manutenzioni/<id>/modifica` | Modifica |
| POST | `/manutenzioni/<id>/elimina` | Eliminazione |
| GET | `/manutenzioni/<id>/verbale` | Download verbale PDF |
| GET | `/scadenzario` | Vista scadenzario |

#### Import
| Metodo | URL | Descrizione |
|---|---|---|
| GET | `/import` | Pagina upload |
| POST | `/import/analizza` | Analisi AI del file |
| GET | `/import/<id>/preview` | Anteprima risultati |
| POST | `/import/<id>/esegui` | Esecuzione import |
| GET | `/import/storico` | Storico importazioni |
| GET | `/import/email` | Coda email verbali |
| GET | `/import/email/<id>` | Dettaglio verbale |
| POST | `/import/email/<id>/conferma` | Conferma verbale |
| GET | `/import/email/<id>/scarta` | Scarta verbale |

#### Export
| Metodo | URL | Descrizione |
|---|---|---|
| GET | `/export/apparecchi/excel` | Export apparecchi Excel |
| GET | `/export/apparecchi/pdf` | Export apparecchi PDF |
| GET | `/export/manutenzioni/excel` | Export manutenzioni Excel |
| GET | `/export/scadenzario/excel` | Export scadenzario Excel |
| GET | `/export/scadenzario/pdf` | Export scadenzario PDF |

#### Amministrazione
| Metodo | URL | Descrizione |
|---|---|---|
| GET | `/admin/utenti` | Lista utenti |
| GET/POST | `/admin/utenti/nuovo` | Nuovo utente |
| GET/POST | `/admin/utenti/<id>/modifica` | Modifica utente (include lo stato attivo/disattivo) |
| GET | `/admin/utenti/<id>/elimina` | Conferma cancellazione utente |
| POST | `/admin/utenti/<id>/elimina` | Cancella utente |
| POST | `/admin/utenti/<id>/reset-password` | Reset password |
| GET | `/admin/divisioni` | Lista divisioni |
| POST | `/admin/divisioni/nuova` | Nuova divisione |
| POST | `/admin/divisioni/<id>/modifica` | Modifica divisione |
| POST | `/admin/divisioni/<id>/toggle` | Attiva/disattiva divisione |
| GET/POST | `/admin/configurazione` | Editor configurazione |
| GET | `/admin/email-config` | Config email IMAP |
| POST | `/admin/email-config/nuova` | Nuova config email |
| POST | `/admin/email-config/<id>/toggle` | Attiva/disattiva email |
| GET | `/admin/backup` | Gestione backup |
| POST | `/admin/backup/crea` | Crea backup |
| GET | `/admin/backup/<filename>/scarica` | Scarica backup |
| POST | `/admin/backup/<filename>/ripristina` | Ripristina backup |
| POST | `/admin/backup/<filename>/elimina` | Elimina backup |
| GET | `/admin/log-attivita` | Log attivita |
| POST | `/admin/reset-database` | Azzera database completo (con backup automatico) |
| POST | `/admin/reset-parziale` | Reset parziale: cancella inventario, mantiene utenti |

---

## 16. Risoluzione problemi

### L'applicazione non si avvia

**Errore "Address already in use":**
La porta 5000 e' gia' in uso. Modificare la porta in `config.json` oppure terminare il processo che la occupa:
```batch
netstat -ano | findstr :5000
taskkill /PID <numero_PID> /F
```

**Errore "ModuleNotFoundError":**
Le dipendenze non sono installate. Eseguire:
```batch
venv\Scripts\pip install -r requirements.txt
```

### Non riesco ad accedere da altri PC

1. Verificare che `host` in `config.json` sia `"0.0.0.0"` (non `"127.0.0.1"`)
2. Verificare che il firewall di Windows consenta le connessioni sulla porta 5000:
   - Pannello di controllo > Windows Defender Firewall > Impostazioni avanzate
   - Regola in entrata > Nuova regola > Porta > TCP 5000 > Consenti
3. Usare l'indirizzo IP del server (es. `http://192.168.1.100:5000`), non `localhost`

### L'import AI non funziona

1. Verificare che la chiave API Anthropic sia configurata (Amministrazione > Configurazione)
2. Verificare la connessione internet dal server
3. Verificare che il file caricato contenga dati leggibili (non immagini scannerizzate senza OCR)

### Le email non vengono controllate

1. Verificare la configurazione IMAP (Amministrazione > Config Email)
2. Verificare che la configurazione sia **attiva** (interruttore verde)
3. Verificare server IMAP, porta (993 per SSL), email e password
4. Alcuni provider richiedono una "password per le app" specifica (es. Gmail)
5. Controllare i log in `logs/medinventory.log`

### Password dimenticata

L'amministratore puo' resettare la password di qualsiasi utente da *Amministrazione > Utenti > Reset Password*. Viene generata una password temporanea e l'utente dovra' cambiarla al prossimo accesso.

Se la password dell'unico amministratore e' persa:

```batch
cd C:\MedInventory
venv\Scripts\python manutenzione.py utenti password admin@medinventory.local
```

Lo strumento chiede la nuova password, la valida e riattiva l'account.

Per capire prima *perche'* l'accesso non funziona:

```batch
venv\Scripts\python manutenzione.py diagnosi
```

La diagnosi distingue i casi che la schermata di accesso riassume tutti in
"credenziali non valide": indirizzo inesistente, utente disattivato, password
diversa, blocco per tentativi ripetuti. Segnala anche le password salvate in un
formato che le versioni recenti non sanno piu' verificare -- capita sulle
installazioni migrate da molto lontano, e in quel caso l'accesso risponde con un
errore del server invece che con il rifiuto.

### Manutenzione da riga di comando

```batch
cd C:\MedInventory
venv\Scripts\python manutenzione.py
```

Senza argomenti mostra lo stato dell'installazione, l'esito dei controlli e un
menu. Ogni voce del menu ha il subcomando corrispondente, utilizzabile senza
presidio: `stato`, `diagnosi`, `migra`, `utenti`, `uploads`, `modalita`,
`backup`. `--db PERCORSO` fa lavorare lo strumento su un'altra installazione,
anche se ferma su una versione vecchia dello schema.

**Azzerare gli utenti conservando tutto il resto:**

```batch
venv\Scripts\python manutenzione.py utenti azzera --nuovo-admin nuovo@struttura.it
```

Apparecchi, manutenzioni, verifiche e documenti restano. Gli account vengono
distrutti ma le righe sopravvivono come voci storiche, perche' le schede
continuino a dire chi ha inserito cosa; con `--definitivo` spariscono anche
quelle e i riferimenti si azzerano. Il nuovo accesso viene creato nella stessa
transazione dell'azzeramento, cosi' l'operazione non puo' chiudere fuori dalla
porta. Un backup del database viene fatto sempre, prima di scrivere.

### Il database e' corrotto

1. Andare in *Amministrazione > Backup*
2. Selezionare l'ultimo backup funzionante
3. Cliccare **Ripristina**
4. Riavviare l'applicazione

Se nessun backup e' disponibile, è possibile reinizializzare il database direttamente
dall'interfaccia tramite *Amministrazione > Configurazione > Zona Pericolosa > Azzera Database*
(crea un backup automatico prima di procedere).

In alternativa, da riga di comando (ATTENZIONE: tutti i dati verranno persi):
```batch
cd C:\MedInventory
del data\database.sqlite
venv\Scripts\python seed.py
```

---

*Documentazione MedInventory v2.7.0*
*Studio Bergamaschi*
