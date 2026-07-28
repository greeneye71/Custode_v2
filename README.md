# MedInventory v2.5.1

**Gestione Apparecchi Elettromedicali** — applicazione web per strutture sanitarie
by Studio Bergamaschi

---

**Stack:** Python 3.11+ · Flask 3.x · SQLite3 · HTMX · Bootstrap 5 · AI (Anthropic Claude / Ollama / LM Studio)
**Deployment target:** Windows LAN (Waitress WSGI) · Linux server
**Licenza:** vedere [LICENSE](LICENSE)

---

## Panoramica

MedInventory è un'applicazione web per la gestione del parco *apparecchi elettromedicali* in ambito sanitario. Copre l'intero ciclo di vita dei dispositivi: censimento, scadenzario manutenzioni, verifiche di sicurezza elettrica, import da documenti (Excel/PDF/CSV), reportistica e monitoraggio automatico via IMAP.

**A chi è rivolto:**

- Ingegneri clinici e responsabili tecnici di strutture sanitarie
- Tecnici di manutenzione con accesso in sola lettura
- Aziende sanitarie con più strutture che necessitano di una gestione centralizzata

### Modalità operative

| Modalità | Descrizione |
|---|---|
| **Single-struttura** (legacy v1.x) | Un'unica struttura, tutti gli admin hanno piena visibilità. Attivata impostando `single_struttura: true` in `config.local.json`. |
| **Multi-struttura** (v2.0, default) | Più strutture indipendenti sullo stesso database. Un *superadmin* gestisce il registro delle strutture e può impersonare qualsiasi struttura. Ogni struttura ha i propri utenti, divisioni e apparecchi isolati. |

---

## Prerequisiti

- **Python 3.11** o superiore
- **pip** aggiornato (`pip install --upgrade pip`)
- Nessuna dipendenza di sistema aggiuntiva su Windows
- Su Linux: nessun pacchetto di sistema richiesto (tutte le dipendenze sono Python-pure o includono wheel precompilate)

---

## Installazione rapida (fresh install)

```bash
# 1. Clona il repository o estrai l'archivio
git clone <url-repo> MedInventory
cd MedInventory

# 2. Installa le dipendenze Python
pip install -r requirements.txt

# 3. Crea la configurazione locale (personalizzare prima dell'avvio)
cp config.local.example.json config.local.json

# 4. Inizializza il database e crea l'utente admin predefinito
python seed.py

# 5. Avvia in modalità sviluppo
python app.py
```

Aprire il browser su `http://localhost:5000`

> **Attenzione:** le credenziali predefinite sono `admin@medinventory.local / admin123`.
> Cambiarle immediatamente al primo accesso (vedere la sezione [Credenziali default](#credenziali-default)).

---

## Configurazione

La configurazione è suddivisa in due file:

| File | Scopo |
|---|---|
| `config.json` | Parametri di sistema (versione, percorsi DB/upload). Non modificare manualmente. |
| `config.local.json` | Impostazioni locali dell'installazione (credenziali, AI, SMTP, ecc.). Questo file non viene mai sovrascritto dagli aggiornamenti. |

Creare `config.local.json` copiando `config.local.example.json` e personalizzando i campi necessari.

### Campi di `config.local.json`

| Campo | Tipo | Default | Descrizione |
|---|---|---|---|
| `app_name` | string | `"MedInventory"` | Nome visualizzato nell'interfaccia |
| `organization` | string | `"Studio Bergamaschi"` | Nome dell'organizzazione |
| `structure_name` | string | `""` | Nome della struttura (modalità single) |
| `host` | string | `"0.0.0.0"` | Indirizzo di ascolto del server |
| `port` | integer | `5000` | Porta TCP del server |
| `debug` | boolean | `false` | Modalità debug Flask (solo sviluppo) |
| `secret_key` | string | *(auto-generata)* | Chiave per la firma delle sessioni Flask |
| `encryption_key` | string | *(auto-generata)* | Chiave per la cifratura delle password IMAP |
| `session_lifetime_hours` | integer | `8` | Durata della sessione utente in ore |
| `backup_retention` | integer | `4` | Numero di backup automatici da conservare |
| `ai_provider` | string | `"anthropic"` | Provider AI: `anthropic`, `ollama`, `lmstudio`, `openai_compatible` |
| `anthropic_api_key` | string | `""` | Chiave API Anthropic (obbligatoria se `ai_provider=anthropic`) |
| `ai_import_model` | string | `"claude-sonnet-4-6"` | Modello AI per import documenti |
| `ai_email_model` | string | `"claude-haiku-4-5-20251001"` | Modello AI per parsing email/manutenzioni |
| `ai_verifiche_model` | string | `"claude-haiku-4-5-20251001"` | Modello AI per import verifiche elettriche |
| `ai_local_base_url` | string | `""` | URL base del server AI locale (Ollama/LM Studio) |
| `ai_local_model` | string | `""` | Nome del modello locale |
| `email_check_interval_minutes` | integer | `15` | Intervallo di polling IMAP in minuti |
| `imap_enabled` | boolean | `false` | Abilita monitoraggio email IMAP |
| `imap_account` | string | `""` | Account email per il monitoraggio |
| `imap_password` | string | `""` | Password IMAP (cifrata in DB con `encryption_key`) |
| `imap_server` | string | `""` | Hostname server IMAP |
| `imap_port` | integer | `993` | Porta IMAP |
| `imap_ssl` | boolean | `true` | Usa SSL per la connessione IMAP |
| `alert_email_enabled` | boolean | `false` | Abilita notifiche email per scadenze |
| `alert_email_to` | string | `""` | Destinatario delle notifiche email |
| `smtp_host` | string | `""` | Hostname server SMTP per le notifiche |
| `smtp_port` | integer | `587` | Porta SMTP |
| `smtp_user` | string | `""` | Username SMTP |
| `smtp_password` | string | `""` | Password SMTP |
| `smtp_use_tls` | boolean | `true` | Usa STARTTLS per SMTP |
| `single_struttura` | boolean | `false` | **v2.0** — `true` = modalità legacy single-struttura (upgrade da v1.x) |

---

## Avvio

### Sviluppo

```bash
python app.py
```

Avvia Flask con auto-reload. Utile per sviluppo e test locali. Non adatto alla produzione.

### Produzione (Waitress WSGI)

```bash
python run_production.py
```

Avvia Waitress con 8 thread, logging su file rotante (`logs/medinventory.log` e `logs/errors.log`), scheduler in background per email e backup automatici.

Parametri Waitress configurabili in `config.local.json`:

| Campo | Default | Note |
|---|---|---|
| `host` | `0.0.0.0` | Ascolta su tutte le interfacce |
| `port` | `5000` | Modificare se la porta è occupata |

### Come servizio Windows

```bat
install_service.bat
```

Il file `install_service.bat` registra MedInventory come servizio Windows tramite NSSM o equivalente. Vedere le istruzioni nel file per i dettagli di configurazione del percorso Python.

### Come servizio Linux (systemd)

```bash
bash install_service.sh
```

Il file `install_service.sh` installa una unit systemd e abilita l'avvio automatico.

### Launcher Windows con system tray

```bash
pythonw launcher.pyw
```

Avvia l'applicazione in background con icona nella tray di Windows. Richiede `pystray` e `Pillow` (inclusi in `requirements.txt`).

---

## Architettura

### Flask Application Factory

`app.py` — `create_app()` carica la configurazione, inizializza il database, registra tutti i blueprint e inietta le variabili globali nei template tramite `inject_globals()`.

### Blueprint

| File | Prefisso URL | Responsabilità |
|---|---|---|
| `auth.py` | `/login`, `/logout` | Login, logout, cambio password, sessioni UUID, decorator `@login_required` / `@admin_required` / `@superadmin_required` |
| `apparecchi.py` | `/apparecchi` | CRUD apparecchi elettromedicali, upload foto/documenti, soft-delete (dismissione) |
| `manutenzioni.py` | `/manutenzioni` | Registrazione interventi, scadenzario, upload verbale PDF |
| `verifiche.py` | `/verifiche` | Verifiche di sicurezza elettrica, scadenzario verifiche |
| `admin.py` | `/admin` | Gestione utenti, divisioni, editor configurazione, backup, log attività |
| `import_bp.py` | `/import` | Import AI da Excel/PDF/CSV, coda email in attesa di revisione |
| `export_bp.py` | `/export` | Generazione report Excel e PDF |
| `strutture_bp.py` | `/strutture` | **v2.0** — Gestione strutture (solo superadmin) |
| `api_bp.py` | `/api/v1` | **v2.0** — REST API con autenticazione Bearer token |

### Servizi

| File | Responsabilità |
|---|---|
| `ai_service.py` | Astrazione multi-provider AI: estrazione testo, parsing strutturato JSON |
| `email_monitor.py` | Polling IMAP, estrazione PDF allegati, parsing manutenzioni via AI |
| `scheduler.py` | Daemon in background: controllo email, pulizia sessioni, backup automatici |
| `backup_service.py` | Ciclo di vita backup/restore SQLite |
| `export_service.py` | Logica generazione report (openpyxl, fpdf2) |
| `models.py` | Helper DB: `get_db()`, `query_one()`, `query_all()`, `execute()`, `log_attivita()` |

### Database

SQLite in modalità WAL con foreign key abilitate. Schema definito in `schema.sql`; dati iniziali in `seed.py`.

**Tabelle principali:**

| Tabella | Descrizione |
|---|---|
| `strutture` | **v2.0** — Anagrafica strutture sanitarie (nome, codice, modalità) |
| `strutture_config` | **v2.0** — Configurazione per-struttura (AI, SMTP, ecc.) |
| `api_tokens` | **v2.0** — Token Bearer per REST API, scoped per struttura |
| `login_attempts` | **v2.0** — Rate limiting tentativi di accesso |
| `divisioni` | Reparti/divisioni della struttura, con colore e codice |
| `utenti` | Utenti con ruolo (`superadmin`, `admin`, `utente`) e struttura di appartenenza |
| `utenti_divisioni` | Associazione N:M utenti-divisioni con ruolo di divisione |
| `sessioni` | Token di sessione UUID (non cookie Flask) con scadenza |
| `apparecchi` | Anagrafica dispositivi elettromedicali, con UNIQUE per struttura |
| `manutenzioni` | Interventi di manutenzione con periodicità e verbale PDF |
| `verifiche` | Verifiche di sicurezza elettrica con scadenza |
| `accessori` | Accessori/componenti associati all'apparecchio |
| `documenti` | Documenti allegati (manuali, certificati, foto, report) |
| `email_config` | Configurazione IMAP per-struttura |
| `coda_email` | Email parsate in attesa di revisione manuale |
| `log_attivita` | Audit log di tutte le azioni significative |

**Vista `prossime_scadenze`:** pre-calcola le scadenze manutenzioni con classificazione a 5 priorità: `scaduto`, `urgente`, `attenzione`, `avviso`, `ok`.

### Pattern HTMX

Le route controllano `request.args.get('partial')`: se presente restituiscono solo il frammento tabella da `templates/partials/` per l'aggiornamento in-place; altrimenti restituiscono la pagina completa. Questo consente navigazione fluida senza ricaricare l'intera pagina.

---

## Modalità multi-struttura (v2.0)

### Ruoli utente

| Ruolo | Accesso |
|---|---|
| `superadmin` | Accesso globale a tutte le strutture. Gestisce il registro delle strutture, crea/disattiva strutture, genera token API. Non appartiene a nessuna struttura. |
| `admin` | Amministratore di una struttura specifica. Gestisce utenti, divisioni e configurazione della propria struttura. |
| `utente` | Accesso in lettura/scrittura limitato alle divisioni assegnate della propria struttura. |

### Impersonazione struttura (superadmin)

Il superadmin può entrare nel contesto di una struttura specifica tramite il selettore presente nella navbar. In questo stato (`g.is_superadmin_impersonating = True`) può operare come amministratore della struttura senza modificare il proprio ruolo.

### API Token

Ogni struttura può generare token Bearer per l'accesso all'API REST. I token sono:

- Scoped per struttura (tutti gli endpoint API sono automaticamente filtrati per struttura)
- Associati a un insieme di permessi (`read` o `read write`)
- Opzionalmente limitati nel tempo con una data di scadenza
- Hashati con SHA-256 prima della memorizzazione in database

### Modalità standard vs ingegneria_clinica

Ogni struttura può operare in due modalità configurabili:

| Modalità | Funzionalità disponibili |
|---|---|
| `standard` | CRUD apparecchi, manutenzioni, export. Funzionalità di base. |
| `ingegneria_clinica` | Tutte le funzionalità standard più: verifiche di sicurezza elettrica, import AI avanzato, monitoraggio IMAP, dashboard estesa. |

La modalità si imposta per singola struttura nell'interfaccia superadmin (`/strutture`). In modalità `single_struttura: true`, la modalità è forzata a `ingegneria_clinica`.

---

## Funzionalità AI

`ai_service.py` fornisce un'astrazione multi-provider con supporto per:

| Provider | Configurazione |
|---|---|
| **Anthropic Claude** (default) | `ai_provider: "anthropic"`, `anthropic_api_key: "sk-..."` |
| **Ollama** (locale) | `ai_provider: "ollama"`, `ai_local_base_url: "http://localhost:11434"`, `ai_local_model: "llama3"` |
| **LM Studio** (locale) | `ai_provider: "lmstudio"`, `ai_local_base_url: "http://localhost:1234/v1"`, `ai_local_model: "..."` |
| **OpenAI-compatible** | `ai_provider: "openai_compatible"`, `ai_local_base_url: "..."`, `ai_local_model: "..."` |

### Import documenti (`/import`)

1. Upload di file Excel, PDF o CSV
2. Classificazione automatica del tipo documento: `inventario`, `verbale manutenzione`, `verifica elettrica` (keyword heuristics + AI fallback)
3. Per PDF multi-pagina: split in pagine singole con `pypdf`
4. Analisi di ogni pagina con prompt specifico per tipo documento
5. Preview con matching apparecchi esistenti
6. Inserimento batch in `apparecchi`, `manutenzioni` o `verifiche`

### Monitoraggio email (`email_monitor.py`)

1. Polling IMAP dell'account configurato (intervallo configurabile)
2. Estrazione allegati PDF
3. Parsing del verbale di manutenzione via AI
4. Creazione automatica record `manutenzioni` con `verbale_path` se il dispositivo è trovato
5. In caso di mancato match: accodamento in `coda_email` per revisione manuale dall'interfaccia (`/import/coda`)

---

## REST API

L'API REST v1 è disponibile all'indirizzo `/api/v1`. Tutti gli endpoint richiedono autenticazione Bearer token e restituiscono JSON.

Documentazione completa: [`docs/API.md`](docs/API.md)

Esempio di chiamata:

```bash
curl -H "Authorization: Bearer <token>" http://localhost:5000/api/v1/apparecchi
```

---

## Cambio modalità operativa

Due script semplificano il passaggio tra le due modalità:

### `toggle_modalita.py` — cambia modalità con un comando

```bash
python toggle_modalita.py            # mostra stato attuale e chiede conferma (toggling)
python toggle_modalita.py --status   # mostra solo lo stato senza modificare
python toggle_modalita.py --single   # forza modalità single-struttura (legacy v1.x)
python toggle_modalita.py --multi    # forza modalità multi-struttura (v2.0)
```

Modifica automaticamente `single_struttura` in `config.local.json` e avvisa se manca il superadmin.

### `crea_superadmin.py` — crea o reimposta il superadmin

```bash
python crea_superadmin.py
```

Richiede email e password interattivamente (con validazione). Se il superadmin esiste già, offre di reimpostare la password.

### Flusso consigliato per attivare multi-struttura

```bash
python toggle_modalita.py --multi   # imposta single_struttura: false
python crea_superadmin.py           # crea l'utente superadmin
python run_production.py            # riavvia l'applicazione
```

---

## Migrazione

### Da v1.x a v2.0

**Eseguire prima di avviare l'app per la prima volta in v2.0.**

Lo script è idempotente: sicuro da eseguire più volte.

```bash
python migrate_v2_0.py
```

Lo script esegue le seguenti operazioni:

1. Backup automatico del database (`*.bak_pre_v2_YYYYMMDD_HHMMSS`)
2. Creazione delle nuove tabelle v2: `strutture`, `strutture_config`, `api_tokens`, `login_attempts`
3. Creazione di una struttura di default con il nome della struttura esistente
4. Aggiunta della colonna `struttura_id` a `divisioni`, `apparecchi`, `log_attivita`, `utenti`
5. Aggiornamento del CHECK constraint sul ruolo utenti (aggiunge `superadmin`)
6. Aggiornamento del vincolo UNIQUE su `apparecchi` (ora `UNIQUE(struttura_id, modello, matricola)`)
7. Aggiunta di `single_struttura: true` in `config.local.json` (compatibilità con il comportamento v1.x)
8. Impostazione di `PRAGMA user_version = 200`

In caso di errore: lo script ripristina automaticamente il database dal backup.

### `migrate.py` — strumento unificato (consigliato)

Analizza il database, elenca le migrazioni mancanti e le applica tutte nell'ordine corretto.
Da preferire ai singoli script: un'installazione v1.x ha in genere **più** migrazioni pendenti,
non solo la v2.0.

```bash
python migrate.py --check    # solo analisi, non modifica nulla
python migrate.py            # applica, chiedendo conferma
python migrate.py --yes      # applica senza conferma
python migrate.py --db PATH  # database esplicito
```

Crea un backup prima di scrivere e, se una migrazione fallisce, ripristina automaticamente.

### Migrazioni precedenti (singoli script)

Per installazioni che partono da una versione precedente alla v1.4, applicare le migrazioni in ordine:

```bash
python migrate_v1_1.py   # v1.0 → v1.1
python migrate_v1_2.py   # v1.1 → v1.2 (rinomina codice_interno → descrizione, aggiunge accessori)
python migrate_v1_3.py   # v1.2 → v1.3 (aggiunge verbale_path a manutenzioni)
python migrate_v1_3_2.py # v1.3 → v1.3.2
python migrate_v1_4.py   # v1.3.x → v1.4 (aggiunge verifiche elettriche)
python migrate_v2_0.py   # v1.4.x → v2.0 (multi-struttura)
```

---

## Importare un'altra installazione

`importa_installazione.py` fa confluire un'altra installazione MedInventory in questa, come nuova
struttura, allegati compresi. Serve soprattutto ad assorbire un'installazione monostruttura già in
esercizio dentro un deployment multi-struttura.

L'installazione di origine **non viene mai modificata**: se ne legge uno snapshot coerente, quindi
si può usare anche mentre è in funzione.

```bash
# 1. analisi preliminare: mostra cosa verrebbe importato, senza scrivere nulla
python importa_installazione.py "C:\MedInventory_Ospedale" --dry-run

# 2. importazione vera
python importa_installazione.py "C:\MedInventory_Ospedale" --struttura-nome "Ospedale San Rocco" --con-config --con-log
```

| Opzione | Effetto |
|---|---|
| `--dry-run` | Analizza e mostra il piano, senza scrivere |
| `--struttura-nome NOME` | Nome della struttura da creare |
| `--in-struttura ID` | Importa dentro una struttura esistente |
| `--con-config` | Copia provider e chiavi AI, impostazioni SMTP |
| `--con-log` | Importa anche il registro attività |
| `--con-import-history` | Importa lo storico degli import AI |
| `--senza-file` | Non copiare gli allegati |
| `--senza-utenti` | Non importare gli utenti |
| `--reset-password` | Forza il cambio password al primo accesso |
| `--se-esiste salta\|duplica` | Record già presenti (default: `salta`) |
| `--report FILE.json` | Report dettagliato in JSON |
| `--target DIR` | Installazione di destinazione (default: questa) |
| `--db` / `--uploads` | Percorsi sorgente espliciti |

Note:

- La sorgente può essere di una **versione diversa**: le colonne vengono riconosciute per
  introspezione, quelle assenti prendono un default e i valori non più ammessi vengono
  normalizzati e segnalati.
- L'operazione è **ripetibile**: una seconda esecuzione con `--in-struttura` non duplica nulla.
- Un apparecchio con uno **stato non riconosciuto** (es. `rottamato`) viene importato come
  `funzionante`, quindi risulta attivo e genera scadenze. Lo strumento lo segnala prima e dopo
  l'importazione, elencando le matricole coinvolte.
- Non vengono importati sessioni, tentativi di login, token API e configurazioni email: sono
  legati all'installazione di origine.

---

## Stampe

La voce **Stampe** del menu genera quattro prospetti in PDF, pensati per il foglio A4
verticale, e le corrispondenti versioni in Excel:

| Prospetto | Contenuto |
|---|---|
| Inventario generale | Tutti gli apparecchi, raggruppati per divisione nelle strutture multi-divisione |
| Inventario di divisione | Gli apparecchi di un singolo reparto (non compare nelle strutture mono-divisione) |
| Scadenze manutenzioni | Prima le scadute, poi quelle in arrivo nel periodo scelto |
| Scadenze verifiche | Come sopra, per le verifiche di sicurezza elettrica |

Ogni prospetto riporta marca, modello, matricola e ubicazione. Il periodo si indica con
una scelta rapida (30 giorni, 90 giorni, entro l'anno in corso o il prossimo) oppure con
un intervallo di date libero (entrambi gli estremi indicati sono inclusi).

Opzioni: colonna di spunta da barrare durante il giro di controllo, spazio per data e
firma in calce, inclusione degli apparecchi dismessi.

Il logo della struttura, se caricato da Strutture → Configurazione, compare nella testata.

Per admin, tecnico e superadmin l'inventario generale copre l'intera struttura, incluse le
divisioni disattivate (l'elenco a video, invece, le nasconde).

---

## Credenziali default

| Campo | Valore |
|---|---|
| Email | `admin@medinventory.local` |
| Password | `admin123` |

> **Importante:** cambiare la password immediatamente dopo il primo accesso tramite il menu utente in alto a destra. Non lasciare mai le credenziali predefinite in un ambiente di produzione.

---

## Struttura directory

```
MedInventory/
├── app.py                  # Application factory, entry point sviluppo
├── run_production.py       # Entry point produzione (Waitress)
├── launcher.pyw            # Launcher Windows con system tray
├── seed.py                 # Inizializzazione DB e utente admin
├── schema.sql              # Schema SQL completo
├── config.json             # Parametri di sistema (auto-gestito)
├── config.local.json       # Configurazione locale (da personalizzare)
├── config.example.json     # Template config.json
├── config.local.example.json # Template config.local.json
│
├── auth.py                 # Autenticazione e decorator
├── apparecchi.py           # Blueprint apparecchi
├── manutenzioni.py         # Blueprint manutenzioni
├── verifiche.py            # Blueprint verifiche elettriche
├── admin.py                # Blueprint amministrazione
├── import_bp.py            # Blueprint import AI
├── export_bp.py            # Blueprint export
├── strutture_bp.py         # Blueprint strutture (v2.0)
├── api_bp.py               # Blueprint REST API (v2.0)
│
├── ai_service.py           # Servizio AI multi-provider
├── email_monitor.py        # Monitor IMAP
├── scheduler.py            # Background scheduler
├── backup_service.py       # Gestione backup
├── export_service.py       # Generazione report
├── models.py               # Helpers database SQLite
│
├── migrate_v1_1.py         # Migrazione v1.0 → v1.1
├── migrate_v1_2.py         # Migrazione v1.1 → v1.2
├── migrate_v1_3.py         # Migrazione v1.2 → v1.3
├── migrate_v1_3_2.py       # Migrazione v1.3 → v1.3.2
├── migrate_v1_4.py         # Migrazione v1.3.x → v1.4
├── migrate_v2_0.py         # Migrazione v1.4.x → v2.0
├── migrate.py              # Strumento unificato di migrazione (consigliato)
├── importa_installazione.py # Importa un'altra installazione come nuova struttura
├── toggle_modalita.py      # Cambia modalità single ↔ multi-struttura
├── crea_superadmin.py      # Crea o reimposta il superadmin
│
├── install_service.bat     # Installazione servizio Windows
├── install_service.sh      # Installazione servizio Linux (systemd)
├── setup.bat               # Setup iniziale Windows
├── setup.sh                # Setup iniziale Linux
│
├── templates/              # Template Jinja2
│   ├── partials/           # Frammenti HTMX (aggiornamento parziale)
│   ├── strutture/          # Template gestione strutture (v2.0)
│   └── errors/             # Pagine di errore HTTP
├── static/                 # Asset statici (CSS, JS, immagini)
├── data/                   # Database SQLite (auto-creata)
├── uploads/                # File caricati dagli utenti (auto-creata)
├── backups/                # Backup automatici DB (auto-creata)
├── logs/                   # Log rotanti (auto-creata)
└── docs/                   # Documentazione tecnica
    ├── API.md              # Riferimento REST API
    └── MIGRAZIONE_v2.md    # Guida migrazione da v1.x a v2.0
```

---

## Licenza

Vedere il file [LICENSE](LICENSE) per i termini di licenza.

---

*MedInventory v2.5.1 — by Studio Bergamaschi*
