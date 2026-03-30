# MedInventory v2 — Multi-Struttura · Fase 1 · Design Spec

**Data:** 2026-03-30
**Versione target:** 2.0.0
**Autore:** Studio Bergamaschi
**Stato:** Approvato

---

## 1. Contesto e obiettivo

MedInventory v1.x gestisce apparecchi elettromedicali per una singola struttura sanitaria.
La v2 introduce il **multi-tenancy**: un'unica istanza centralizzata serve più strutture
(ospedali, cliniche), ciascuna con propri utenti, divisioni, apparecchi e manutenzioni.

La v1.x rimane in uso per il cliente storico tramite il flag `single_struttura: true`.
Le due versioni condividono un unico codebase (nessun branch separato).

Questa spec copre la **Fase 1** — le fondamenta multi-struttura.
Le Fasi 2 (entità normative) e 3 (analytics e patrimonio) saranno specificate separatamente.

---

## 2. Strategia codebase

- **Un solo repository**, nessun fork.
- Flag `single_struttura` in `config.local.json`:
  - `true` → UI identica alla v1.x, nessuna voce "struttura" visibile, modalità forzata a `ingegneria_clinica`
  - `false` → modalità multi-struttura completa (default per nuove installazioni)
- Tutti i bugfix e le migliorie si applicano automaticamente a entrambi i prodotti.
- Le route `/strutture/*` e `/api/v1` esistono sempre ma non sono linkate dalla UI in modalità `single_struttura`.

---

## 3. Schema del database

### 3.1 Nuove tabelle

#### `strutture`
```sql
CREATE TABLE strutture (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  nome        TEXT NOT NULL,
  codice      TEXT UNIQUE NOT NULL,
  descrizione TEXT,
  indirizzo   TEXT,
  email_notifiche TEXT,
  modalita    TEXT NOT NULL DEFAULT 'standard'
              CHECK(modalita IN ('standard', 'ingegneria_clinica')),
  attiva      INTEGER DEFAULT 1,
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

#### `strutture_config`
Configurazione per-struttura. Se una chiave manca, si usa il default globale di `config.local.json`.
```sql
CREATE TABLE strutture_config (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  struttura_id INTEGER NOT NULL REFERENCES strutture(id) ON DELETE CASCADE,
  chiave       TEXT NOT NULL,
  valore       TEXT,
  UNIQUE(struttura_id, chiave)
);
-- Chiavi previste:
--   ai_provider, anthropic_api_key, ai_import_model, ai_email_model,
--   ai_local_base_url, ai_local_model,
--   smtp_host, smtp_port, smtp_user, smtp_password_encrypted, smtp_from,
--   report_frequenza  ('giornaliero' | 'settimanale' | 'mensile')
--   report_schedulato_attivo ('1' | '0')
```

#### `api_tokens`
```sql
CREATE TABLE api_tokens (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  struttura_id   INTEGER NOT NULL REFERENCES strutture(id) ON DELETE CASCADE,
  nome           TEXT NOT NULL,
  token_hash     TEXT UNIQUE NOT NULL,
  scopes         TEXT DEFAULT 'read',   -- 'read', 'read,write'
  ultimo_utilizzo DATETIME,
  scadenza       DATE,
  attivo         INTEGER DEFAULT 1,
  created_by     INTEGER REFERENCES utenti(id),
  created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

#### `login_attempts`
```sql
CREATE TABLE login_attempts (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ip_address TEXT NOT NULL,
  email      TEXT,
  esito      TEXT NOT NULL CHECK(esito IN ('fallito', 'bloccato', 'riuscito')),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_login_attempts_ip    ON login_attempts(ip_address, created_at);
CREATE INDEX idx_login_attempts_email ON login_attempts(email, created_at);
```

### 3.2 Modifiche alle tabelle esistenti

| Tabella | Colonna aggiunta | Note |
|---|---|---|
| `divisioni` | `struttura_id INTEGER NOT NULL REFERENCES strutture(id)` | — |
| `utenti` | `struttura_id INTEGER REFERENCES strutture(id)` | NULL per superadmin |
| `utenti` | `ruolo` CHECK esteso | `('superadmin','admin','utente')` |
| `apparecchi` | `struttura_id INTEGER NOT NULL REFERENCES strutture(id)` | Denormalizzato per performance e UNIQUE |
| `log_attivita` | `struttura_id INTEGER REFERENCES strutture(id)` | Denormalizzato per filtri cross-struttura |

#### Vincolo UNIQUE `apparecchi`
Il vincolo esistente `UNIQUE(modello, matricola)` diventa `UNIQUE(struttura_id, modello, matricola)`:
due strutture diverse possono avere lo stesso apparecchio; all'interno della stessa struttura
la coppia modello+matricola rimane univoca.

### 3.3 Nessuna modifica a
`manutenzioni`, `verifiche`, `documenti`, `accessori`, `sessioni`, `import_history`,
`import_preview` — scoped transitivamente tramite `apparecchio_id` o `divisione_id`.

`email_config` non viene modificata: contiene le credenziali **IMAP** (polling in entrata)
ed è già scoped per divisione → struttura transitivamente (`email_config.divisione_id →
divisioni.struttura_id`). Le credenziali **SMTP** per le notifiche in uscita (digest,
report schedulati) vanno invece in `strutture_config` (chiavi `smtp_host`, `smtp_port`,
`smtp_user`, `smtp_password_encrypted`, `smtp_from`).

---

## 4. Ruoli e autenticazione

### 4.1 Gerarchia ruoli

| Ruolo | Scope | Capacità |
|---|---|---|
| `superadmin` | Globale | Crea/gestisce strutture, vede tutto, `struttura_id = NULL` |
| `admin` | Struttura | Gestisce utenti, divisioni, config della propria struttura |
| `utente` | Struttura | Lettura e operazioni base (come oggi) |

### 4.2 Decoratori

- `login_required` — invariato
- `admin_required` — esteso rispetto alla v1.x: controlla `ruolo IN ('admin','superadmin')`.
  In v1.x ammetteva solo `admin`; il superadmin deve poter accedere a tutte le route admin.
  Cambiamento intenzionale, non rompe la v1.x (in `single_struttura: true` non esiste alcun superadmin).
- `superadmin_required` — nuovo, controlla `ruolo = 'superadmin'`
- `admin_struttura_required` — nuovo, ammette admin o superadmin con struttura nel contesto

### 4.3 Sessione

La sessione aggiunge:
- `struttura_id` — struttura corrente dell'utente (o quella impersonata dal superadmin)
- `struttura_nome` — per visualizzazione nel breadcrumb
- `struttura_modalita` — `'standard'` | `'ingegneria_clinica'`

### 4.4 Impersonation superadmin

Il superadmin può "entrare" in una struttura tramite il switcher nel menu.
Un breadcrumb permanente mostra `[Superadmin > Nome Struttura]`.
La sessione conserva il ruolo `superadmin`; i filtri query usano la `struttura_id` impersonata.

### 4.5 Filtri query

- `_get_divisione_filter()` — invariato
- `_get_struttura_filter()` — nuovo; aggiunge `WHERE apparecchi.struttura_id = ?`
  Il superadmin senza struttura impersonata non applica il filtro (vede tutto).

---

## 5. Modalità Standard / Ingegneria Clinica

Configurabile per struttura dal superadmin (`strutture.modalita`).
L'admin della struttura non può cambiarla.

### 5.1 Modalità Standard
Per operatori, capi reparto, tecnici di reparto.

| Area | Funzioni incluse |
|---|---|
| Apparecchi | CRUD completo, foto, accessori, stati |
| Manutenzioni & verifiche | Registrazione, scadenzario, upload verbale |
| Import AI | Excel/PDF/CSV via AI (semplifica l'inserimento per utenti non tecnici) |
| AI suggerimenti | Analisi storico manutenzioni, raccomandazioni intervallo |
| Export | Excel e PDF base |
| Utenti & divisioni | Gestione della propria struttura |
| Dashboard | Scadenzario, contatori stato apparecchi |
| Digest email | Riepilogo scadenze (frequenza configurabile) |

### 5.2 Modalità Ingegneria Clinica
Tutto lo Standard più:

| Area | Funzioni incluse |
|---|---|
| QR code | Generazione PNG on-the-fly, stampabile dalla scheda apparecchio |
| API REST | Endpoint `/api/v1` con token Bearer |
| Email monitor IMAP | Polling, parsing verbali AI, coda revisione |
| Report schedulati | PDF/Excel automatici inviati via email alla struttura |
| Audit log avanzato | Filtri per utente/periodo/entità, export CSV |
| *Fase 2* | Collaudi, contratti, recall, consumabili, firma verbali |
| *Fase 3* | TCO, MTBF, registro cespiti, checklist, export accreditamento |

### 5.3 Implementazione

- Il context processor `inject_globals()` inietta `g.struttura_modalita` in ogni template.
- Le route avanzate controllano `g.struttura_modalita == 'ingegneria_clinica'`, altrimenti `abort(403)`.
- Il menu di navigazione mostra/nasconde le voci dinamicamente via Jinja2.
- In `single_struttura: true` la modalità è forzata a `ingegneria_clinica`.

---

## 6. Nuovi blueprint e modifiche

### 6.1 Nuovi blueprint

| File | Prefix | Responsabilità |
|---|---|---|
| `strutture_bp.py` | `/strutture` | CRUD strutture (superadmin only): lista, crea, modifica, disattiva, config per-struttura |
| `api_bp.py` | `/api/v1` | REST API autenticata con token Bearer |

### 6.2 Modifiche ai blueprint esistenti

| Blueprint | Modifica |
|---|---|
| `auth.py` | Rate limiting login, logout globale, `superadmin_required`, `admin_struttura_required`, impersonation switcher |
| `admin.py` | Gestione utenti e divisioni scoped per struttura; sezione token API; config AI/SMTP per struttura; nuova sub-page `/admin/sicurezza` (sblocco IP/utenti bloccati) |
| `scheduler.py` | Itera su tutte le strutture attive per digest email e report schedulati |
| `apparecchi.py` | Aggiunge generazione QR code (libreria `qrcode`) |
| `export_bp.py` | Aggiunge report schedulati PDF/Excel |
| `ai_service.py` | Legge config AI da `strutture_config` con fallback a `config.local.json` |
| `email_monitor.py` | Scoped per struttura; usa SMTP/IMAP da `strutture_config` con fallback globale |
| `app.py` | Registra i nuovi blueprint; aggiorna `inject_globals()` |

---

## 7. Dashboard e notifiche

### 7.1 Dashboard superadmin (`/dashboard` con struttura_id = NULL)

- Tabella strutture: totale apparecchi, scadenze critiche (scaduto + urgente), stato IMAP, modalità
- Grafici: apparecchi per stato aggregati, top-5 strutture con più scadenze arretrate
- Accesso rapido impersonation per struttura

### 7.2 Dashboard per struttura

Identica alla v1.x. In modalità Ingegneria Clinica aggiunge:
- Widget stato email monitor (ultima sincronizzazione, messaggi in coda)
- Widget report schedulati (ultimo invio, prossimo invio)

### 7.3 Digest email scadenze

Lo scheduler itera su tutte le strutture attive con `email_notifiche` configurata.
Ogni struttura riceve una mail separata — mai aggregate tra strutture diverse.

Frequenza da `strutture_config.report_frequenza`: `giornaliero` / `settimanale` / `mensile`.

Contenuto email:
- Scaduti (priorità `scaduto`)
- Urgenti (≤7 giorni, priorità `urgente`)
- In scadenza (≤30 giorni, priorità `attenzione` + `avviso`)
- Raggruppati per divisione

SMTP: usa quello specifico della struttura (`strutture_config`) se presente, altrimenti globale.

---

## 8. Sicurezza

### 8.1 Rate limiting login

- Tabella `login_attempts` (ip, email, timestamp, esito)
- Soglia: 5 tentativi falliti in 10 minuti → blocco 15 minuti
- Messaggio esplicito all'utente con minuti residui
- Il superadmin può sbloccare manualmente da `/admin/sicurezza`
- Il contatore si azzera al login riuscito

### 8.2 Logout da tutti i dispositivi

- Disponibile dalla pagina profilo utente
- Cancella tutte le righe di `sessioni` per quell'`utente_id` (o tutte tranne la corrente)
- Il superadmin può forzarlo su qualsiasi utente da `/admin/utenti/<id>`

### 8.3 API REST `/api/v1`

Solo in modalità Ingegneria Clinica. Autenticazione: header `Authorization: Bearer <token>`.

I token sono gestiti dall'admin struttura in `/admin/api-tokens`:
- Creazione con nome, scopo (`read` / `read,write`), scadenza opzionale
- Revoca immediata
- Log ultimo utilizzo

Endpoint Fase 1:

| Metodo | Path | Scope richiesto | Descrizione |
|---|---|---|---|
| `GET` | `/api/v1/apparecchi` | `read` | Lista apparecchi della struttura |
| `GET` | `/api/v1/apparecchi/<id>` | `read` | Dettaglio singolo apparecchio |
| `GET` | `/api/v1/scadenze` | `read` | Scadenze attive con priorità |
| `GET` | `/api/v1/manutenzioni` | `read` | Lista manutenzioni |
| `POST` | `/api/v1/manutenzioni` | `write` | Crea nuova manutenzione |

Tutti gli endpoint sono scoped alla struttura del token. Risposta: JSON con paginazione (`page`, `per_page`, `total`).

---

## 9. Compatibilità v1.x e migrazione

### 9.1 Flag `single_struttura`

In `config.local.json`:
```json
"single_struttura": true
```

Effetti con `true`:
- UI non mostra mai il concetto di struttura
- Login porta direttamente all'applicazione (nessuno switcher struttura)
- `admin_required` funziona come nella v1.x
- Modalità forzata a `ingegneria_clinica`
- Route `/strutture/*` disponibili ma non linkate

### 9.2 Script `migrate_v2_0.py`

Script idempotente (sicuro da rieseguire). Passi:

1. Crea le nuove tabelle (`strutture`, `strutture_config`, `api_tokens`, `login_attempts`)
2. Aggiunge le colonne mancanti alle tabelle esistenti (con `ALTER TABLE IF NOT EXISTS`)
3. Crea una struttura di default leggendo `structure_name` da `config.local.json`
4. Associa tutte le divisioni, utenti e apparecchi esistenti alla struttura di default
5. Aggiorna il vincolo UNIQUE su `apparecchi` (ricrea la tabella se necessario — SQLite non supporta `ALTER TABLE DROP CONSTRAINT`)
6. Mantiene il ruolo `admin` esistente invariato (`admin` in `single_struttura: true` equivale a pieno controllo)
7. Imposta `single_struttura: true` in `config.local.json` se non già presente
8. Scrive `data/.version_notice` con `old_version`/`new_version` per il flash informativo all'admin

---

## 10. Dipendenze aggiuntive

| Libreria | Uso | Già presente |
|---|---|---|
| `qrcode[pil]` | Generazione QR code PNG | No — da aggiungere a `requirements.txt` |

Tutte le altre funzionalità (rate limiting, API REST, strutture) usano librerie già presenti
(Flask, SQLite, Werkzeug, FPDF2, openpyxl).

---

## 11. Fuori scope (Fase 1)

Le seguenti funzionalità sono previste ma rimandate alle fasi successive:

**Fase 2 — Entità normative:**
Collaudi di accettazione con generazione verbale e workflow approvazione, storico spostamenti,
gestione recall/avvisi di sicurezza, contratti di manutenzione, scadenzario consumabili,
firma/presa visione verbali con hash SHA-256, export per accreditamento regionale.

**Fase 3 — Analytics e patrimonio:**
Scheda rischio clinico per apparecchio, registro cespiti (valore acquisto, ammortamento),
dashboard TCO, calcolo MTBF e disponibilità, checklist manutenzione configurabile per marca/modello.

---

## 12. Ordine di implementazione suggerito

1. `migrate_v2_0.py` + aggiornamento `schema.sql`
2. `strutture_bp.py` — CRUD strutture (superadmin)
3. Modifiche auth: `superadmin_required`, sessione arricchita, impersonation, filtri query
4. Modifiche `admin.py` — scoping per struttura
5. Flag `single_struttura` nel context processor e nei template
6. Modalità standard/ingegneria_clinica — guard nelle route e nel menu
7. Rate limiting login (`login_attempts`)
8. Logout da tutti i dispositivi
9. Dashboard superadmin
10. Digest email scadenze nello scheduler
11. Config per-struttura AI e SMTP (`strutture_config`)
12. QR code (`apparecchi.py` + `qrcode`)
13. `api_bp.py` — token e endpoint REST
14. Report schedulati (`export_bp.py` + scheduler)
15. Audit log avanzato
