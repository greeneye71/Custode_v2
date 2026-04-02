# Guida di migrazione MedInventory v1.x → v2.0

Questo documento descrive come aggiornare un'installazione esistente di MedInventory v1.x
alla versione v2.0, che introduce l'architettura multi-struttura (multi-tenant) e la REST API.

---

## 1. Prerequisiti

- Python 3.10 o superiore
- Accesso in scrittura alla directory dell'applicazione e al file `data/database.sqlite`
- **Backup del database prima di qualsiasi operazione** (vedi passo 1 dei passaggi)
- Connessione a internet (se si usano provider AI Anthropic) oppure server locale attivo
  (Ollama / LM Studio)

---

## 2. Passaggi di migrazione

```bash
# 1. Backup manuale del database (obbligatorio)
cp data/database.sqlite data/database_backup_pre_v2.sqlite

# 2. Aggiorna il codice (git pull o sostituisci i file manualmente)
git pull origin main

# 3. Installa le nuove dipendenze
pip install -r requirements.txt

# 4. Esegui la migrazione del database
python migrate_v2_0.py

# 5. Configura single_struttura (per installazioni esistenti v1.x)
# In config.json (o config.local.json se presente) aggiungere o verificare:
#   "single_struttura": true
# Lo script migrate_v2_0.py lo aggiunge automaticamente se config.local.json esiste.

# 6. Avvia l'applicazione
python run_production.py
```

> **Nota:** `migrate_v2_0.py` crea automaticamente un backup aggiuntivo con timestamp
> (es. `data/database.sqlite.bak_pre_v2_20260403_120000`) prima di applicare qualsiasi
> modifica. In caso di errore imprevisto il database originale viene ripristinato
> automaticamente da questo backup.

---

## 3. Cosa fa `migrate_v2_0.py`

Lo script è **idempotente**: può essere eseguito più volte senza effetti collaterali.
Verifica `PRAGMA user_version` e salta la migrazione se il database è già alla versione 200.

Le operazioni eseguite in ordine sono:

### 3.1 Backup preventivo automatico
Prima di aprire il database, copia `database.sqlite` in un file con timestamp
(`database.sqlite.bak_pre_v2_YYYYMMDD_HHMMSS`).

### 3.2 Creazione nuove tabelle
- **`strutture`** — strutture/clienti con codice univoco, modalità operativa
  (`standard` / `ingegneria_clinica`), indirizzo, email notifiche e flag attiva.
- **`strutture_config`** — coppie chiave/valore di configurazione per-struttura
  (AI provider, SMTP, frequenza report). Vincolo `UNIQUE(struttura_id, chiave)`.
- **`api_tokens`** — token Bearer per la REST API, con hash SHA-256, scope
  (`read` / `read write`), scadenza opzionale e log dell'ultimo utilizzo.
- **`login_attempts`** — registrazione di ogni tentativo di login con IP, email
  ed esito (`fallito` / `bloccato` / `riuscito`), usata per il rate limiting.

Vengono creati anche gli indici necessari su tutte le nuove tabelle.

### 3.3 Struttura di default
Se non esiste ancora alcuna struttura, ne viene creata una con:
- `nome` — letto da `structure_name` o `app_name` in `config.json`
  (default: `Struttura Principale`)
- `codice` — `DEFAULT`
- `modalita` — `ingegneria_clinica`

### 3.4 Aggiunta di `struttura_id` alle tabelle esistenti
La colonna `struttura_id INTEGER` viene aggiunta (se assente) a:
- `divisioni` — tutti i record esistenti vengono assegnati alla struttura di default.
- `apparecchi` — idem.
- `log_attivita` — idem.
- `utenti` — aggiunta e popolata per tutti gli utenti con ruolo diverso da `superadmin`.

### 3.5 Aggiornamento CHECK ruolo su `utenti`
Se la tabella `utenti` non include ancora `superadmin` nel vincolo `CHECK(ruolo IN (...))`,
la tabella viene ricreata (rename → crea nuova → copia dati → drop vecchia) con il vincolo
aggiornato a `('superadmin', 'admin', 'utente')`.

### 3.6 Aggiornamento UNIQUE su `apparecchi`
Il vincolo di unicità viene aggiornato da `UNIQUE(modello, matricola)` a
`UNIQUE(struttura_id, modello, matricola)`, permettendo che apparecchi con stesso
modello e matricola esistano in strutture diverse. Anche questa operazione usa
il pattern rename → ricrea → copia → drop.

### 3.7 Aggiornamento `config.local.json`
Se `config.local.json` esiste e non contiene già la chiave `single_struttura`,
lo script vi aggiunge `"single_struttura": true` per garantire la compatibilità
backward con la configurazione esistente.

### 3.8 File sentinella versione
Crea `data/.version_notice` con le versioni pre/post migrazione e il timestamp,
usato dall'applicazione per mostrare un avviso di avvenuto aggiornamento al primo avvio.

### 3.9 PRAGMA user_version
Imposta `PRAGMA user_version = 200` per segnare il database come migrato. Le
esecuzioni successive dello script si fermano immediatamente a questo controllo.

---

## 4. Modalità compatibilità `single_struttura: true`

Impostando `"single_struttura": true` in `config.json` (o `config.local.json`):

**Cosa rimane uguale rispetto a v1.x:**
- Tutte le funzionalità esistenti (apparecchi, manutenzioni, import AI, email monitor,
  export Excel/PDF, backup).
- Il flusso di login e la gestione degli utenti admin/utente.
- L'interfaccia grafica principale (nessun selettore di struttura visibile).

**Cosa cambia:**
- La navbar non mostra il menu "Strutture".
- Le route `/strutture/...` non sono accessibili agli utenti normali.
- `inject_globals()` espone `single_struttura = True` ai template, che nascondono
  i controlli multi-struttura.
- Il context processor popola `g_struttura` e `g_struttura_modalita` automaticamente
  dalla struttura di default (nessuna scelta richiesta all'utente).
- Il ruolo `superadmin` non è necessario; il ruolo `admin` continua a funzionare
  come in v1.x.

---

## 5. Modalità multi-struttura `single_struttura: false`

Per abilitare la gestione di più strutture/clienti indipendenti è disponibile
lo script `toggle_modalita.py` che automatizza il cambio di configurazione.

### 5.1 Impostare la modalità multi-struttura

```bash
python toggle_modalita.py --multi
```

Lo script imposta `"single_struttura": false` in `config.local.json` e avvisa
se non esiste ancora un superadmin. In alternativa, modificare manualmente
`config.local.json`:

```json
{
  "single_struttura": false
}
```

### 5.2 Creare un utente superadmin

```bash
python crea_superadmin.py
```

Lo script chiede interattivamente email e password (minimo 8 caratteri, una
maiuscola, un numero) e crea l'utente superadmin nel database. Se il superadmin
esiste già, offre di reimpostare la password.

> Cambiare la password al primo accesso se si usa la password di default.

### 5.3 Riavviare l'applicazione

```bash
python run_production.py
```

### 5.4 Creare strutture aggiuntive

Accedere con il superadmin e navigare in **Strutture → Nuova struttura**.
Compilare nome, codice univoco e modalità operativa (`standard` o
`ingegneria_clinica`).

### 5.5 Impersonazione struttura

Il superadmin può selezionare una struttura dal menu a tendina in navbar;
un banner colorato indica la struttura attiva. Tutte le operazioni successive
(lettura/scrittura apparecchi, manutenzioni, utenti) avvengono nel contesto
di quella struttura.

### 5.6 Tornare alla modalità single-struttura

```bash
python toggle_modalita.py --single
```

---

## 6. Rollback a v1.x

Se la migrazione produce problemi inattesi:

```bash
# 1. Fermare l'applicazione

# 2. Ripristinare il backup del database
#    (usare il backup manuale o quello automatico con timestamp)
cp data/database_backup_pre_v2.sqlite data/database.sqlite

# 3. Ripristinare il codice v1.x
git checkout v1.4.3
# oppure, se non si usa git:
# sostituire i file con quelli della versione precedente

# 4. Riavviare l'applicazione v1.x
python run_production.py
```

> Il backup automatico con timestamp creato da `migrate_v2_0.py` si trova nella
> stessa directory del database, con nome
> `database.sqlite.bak_pre_v2_YYYYMMDD_HHMMSS`.

---

## 7. Verifiche post-migrazione

Dopo aver completato la migrazione e avviato l'applicazione, verificare:

- [ ] Il login con le credenziali esistenti funziona correttamente.
- [ ] La pagina **Apparecchi** elenca tutti i dispositivi presenti prima della migrazione.
- [ ] La pagina **Manutenzioni / Scadenzario** mostra le scadenze con le priorità corrette.
- [ ] Il backup automatico schedulato è attivo (verificare in **Admin → Backup**).
- [ ] L'email monitor (se configurato) riprende il polling senza errori nei log.
- [ ] L'import AI funziona su un file di test.
- [ ] Il file `data/.version_notice` è presente (conferma che la migrazione è avvenuta).
- [ ] `PRAGMA user_version` restituisce `200`:
  ```bash
  python -c "import sqlite3; db=sqlite3.connect('data/database.sqlite'); print(db.execute('PRAGMA user_version').fetchone()[0])"
  ```
- [ ] (Modalità multi-struttura) Il menu **Strutture** è visibile in navbar per il superadmin.
- [ ] (REST API) Un token API creato dalla UI restituisce dati da `GET /api/v1/apparecchi`
  con header `Authorization: Bearer <token>`.
