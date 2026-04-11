# Design: Fix bug sicurezza + "Crea apparecchio da verifica"

**Data:** 2026-04-11  
**Branch:** v2-multi-struttura  
**Scope:** Correzione 3 bug dal Codex review + nuova feature import verifiche elettriche

---

## Contesto

Il Codex review ha identificato 5 potenziali problemi nel branch. Dopo analisi del codice, 2 punti (scoping divisioni e insert divisione_nuova) risultano già corretti nel codice attuale. I 3 bug reali sono descritti sotto.

La nuova feature aggiunge la possibilità di creare un nuovo apparecchio direttamente dalla schermata di preview import verifiche di sicurezza elettrica, quando la matricola non viene riconosciuta.

---

## Bug 1 — TIPI_VALIDI non allineati allo schema DB (P1)

**File:** `import_bp.py` circa riga 1113  
**Problema:** La lista di validazione usata nella conferma email usa tipi non esistenti nel DB (`straordinaria`, `verifica_elettrica`, `collaudo`), e mancano i tipi validi (`verifica`, `calibrazione`). Il CHECK constraint su `manutenzioni.tipo` ammette solo: `preventiva`, `correttiva`, `verifica`, `calibrazione`. Confermare un'email con tipo `verifica` la riscrive in `preventiva`; tipi non validi fanno fallire la INSERT.

**Fix:** Sostituire `TIPI_VALIDI` con i valori che rispecchiano il CHECK nel DB:
```python
TIPI_VALIDI = ('preventiva', 'correttiva', 'verifica', 'calibrazione')
```

---

## Bug 2 — reset_database() non crea la struttura prima delle divisioni (P2)

**File:** `admin.py` circa riga 839  
**Problema:** Dopo `init_db()` la tabella `strutture` è vuota. La funzione inserisce immediatamente divisioni senza `struttura_id` (`NOT NULL` nel schema) e l'utente admin senza `struttura_id`. Questo causa errori FK/NOT NULL al momento del reset.

**Fix:** Prima dei seed, inserire una struttura di default e usarne l'id in tutti gli insert successivi, replicando il pattern di `seed.py`:

```python
# 5a. Struttura di default
c = db_execute(
    "INSERT INTO strutture (nome, codice, descrizione, modalita, attiva) VALUES (?,?,?,?,?)",
    ('Struttura Principale', 'DEFAULT',
     'Struttura predefinita (rinominare da Amministrazione → Strutture)',
     'avanzata', 1)
)
struttura_id = c.lastrowid

# 5b. Divisioni con struttura_id
c = db_execute(
    "INSERT INTO divisioni (nome, codice, colore, descrizione, struttura_id) VALUES (?,?,?,?,?)",
    ('Divisione 1', 'DIV1', '#0ea5e9', '...', struttura_id)
)
# ... idem Divisione 2

# 5c. Utente admin con struttura_id
c = db_execute(
    """INSERT INTO utenti (email, password_hash, nome, cognome, ruolo, struttura_id, primo_accesso)
       VALUES (?,?,?,?,?,?,1)""",
    ('admin@medinventory.local', password_hash, 'Amministratore', 'Sistema', 'admin', struttura_id)
)
```

---

## Bug 3 — Scheduler invia doppia email (P2)

**File:** `scheduler.py` circa riga 158–205  
**Problema:** `_send_deadline_alerts()` (digest testuale scadenze) e `_send_scheduled_reports()` (PDF periodico) usano entrambe la stessa chiave di configurazione `report_schedulato_attivo`. Quando una struttura abilita i report, riceve due email distinte ad ogni run del job.

**Fix:** Introdurre una chiave separata `report_pdf_attivo` per il PDF periodico. Il digest testuale continua ad usare `report_schedulato_attivo`. Nessuna modifica UI necessaria (la chiave è configurabile tramite il pannello strutture_config come le altre).

```python
# _send_deadline_alerts — invariato, usa report_schedulato_attivo
attivo = get_struttura_config(sid, 'report_schedulato_attivo', '0')

# _send_scheduled_reports — usa nuova chiave dedicata
if get_struttura_config(sid, 'report_pdf_attivo', '0') != '1':
    continue
```

Aggiornare il commento nel schema.sql (riga 54) per documentare la nuova chiave.

---

## Feature — Crea nuovo apparecchio da verifica non riconosciuta

### Obiettivo

Nella preview import verifiche elettriche, quando una matricola non viene trovata nel DB, l'utente può oggi solo selezionare un apparecchio esistente dal dropdown. Si aggiunge la possibilità di creare un nuovo apparecchio usando i dati estratti dal documento dalla verifica stessa.

### Approccio UI (A — Inline form nel preview table)

La cella "Apparecchio" per le righe senza match mostra un radio toggle + form condizionale:

```
[○ Seleziona esistente]  [● Crea nuovo apparecchio]

Quando "Seleziona esistente":
  [ dropdown apparecchi esistenti — come oggi ]

Quando "Crea nuovo apparecchio":
  Marca*:      [ input — pre-compilato da AI se estratto ]
  Modello*:    [ input — pre-compilato da AI se estratto ]
  Matricola*:  [ input — pre-compilato dalla matricola del doc ]
  Descrizione: [ input — pre-compilato da AI se estratto ]
  Divisione*:  [ select divisioni accessibili all'utente ]
```

Il form è inline nella cella, visibile solo quando il radio "Crea nuovo" è selezionato. JS puro (no dipendenze extra) gestisce show/hide scoped alla singola riga (ogni riga ha il proprio radio indipendente). Default: radio "Seleziona esistente" preselezionato. Il select divisione preseleziona la divisione attiva dell'utente se disponibile.

### Estensione AI prompt (`ai_service.py`)

Aggiungere al `VERIFICA_BATCH_SYSTEM_PROMPT` i campi opzionali per l'apparecchio:

```
- marca (produttore dell'apparecchio verificato — opzionale, se presente nel documento)
- modello (modello dell'apparecchio verificato — opzionale)
- descrizione (tipo/descrizione apparecchio, es. "Elettrobisturi", "Monitor multiparametrico" — opzionale)
```

Aggiungere nelle REGOLE: "Se il documento contiene informazioni sull'apparecchio (marca, modello, tipo), includile nei campi opzionali."

### Preview route (`import_bp.py`)

Nel blocco condizionale che costruisce `apparecchi_list` per verbali/verifiche, aggiungere anche `divisioni_list` per la struttura corrente:

```python
if tipo in ('verbale_manutenzione', 'verifica_elettrica'):
    # ... logica apparecchi_list esistente ...
    
    # Aggiungi divisioni accessibili per "crea nuovo"
    # Per superadmin senza struttura_id attivo, deriva la struttura dall'import record
    struttura_id = getattr(g, 'struttura_id', None) or g.user.get('struttura_id')
    if not struttura_id and import_rec.get('divisione_id'):
        div_row = query_one("SELECT struttura_id FROM divisioni WHERE id=?",
                            (import_rec['divisione_id'],))
        if div_row:
            struttura_id = div_row['struttura_id']
    if struttura_id:
        divisioni_list = query_all(
            "SELECT id, nome, colore FROM divisioni WHERE attiva=1 AND struttura_id=? ORDER BY nome",
            (struttura_id,)
        )
    else:
        divisioni_list = []  # superadmin globale senza contesto struttura: crea nuovo non disponibile
```

Passare `divisioni_list` al `render_template`.

### Backend `_execute_verifiche` (`import_bp.py`)

Prima della logica di risoluzione `apparecchio_id` esistente, aggiungere il branch "crea nuovo":

```python
crea_nuovo = request.form.get(f'crea_nuovo_{row_id}') == '1'

if crea_nuovo:
    # Estrai e valida campi
    n_marca = request.form.get(f'nuovo_marca_{row_id}', '').strip()
    n_modello = request.form.get(f'nuovo_modello_{row_id}', '').strip()
    n_matricola = request.form.get(f'nuovo_matricola_{row_id}', '').strip()
    n_descrizione = request.form.get(f'nuovo_descrizione_{row_id}', '').strip()
    n_divisione_id = request.form.get(f'nuovo_divisione_id_{row_id}', type=int)

    if not (n_marca and n_modello and n_matricola and n_divisione_id):
        raise ValueError("Marca, modello, matricola e divisione sono obbligatori per creare un nuovo apparecchio")

    # Verifica che la divisione appartenga alla struttura dell'utente
    struttura_id = getattr(g, 'struttura_id', None)
    div_check = query_one(
        "SELECT struttura_id FROM divisioni WHERE id=? AND attiva=1",
        (n_divisione_id,)
    )
    if not div_check or (struttura_id and div_check['struttura_id'] != struttura_id):
        raise ValueError("Divisione non accessibile")

    # Inserisce il nuovo apparecchio
    cur = execute(
        """INSERT INTO apparecchi
           (divisione_id, struttura_id, matricola, marca, modello, descrizione, created_by)
           VALUES (?,?,?,?,?,?,?)""",
        (n_divisione_id, div_check['struttura_id'],
         n_matricola, n_marca, n_modello, n_descrizione or None, g.user['id'])
    )
    apparecchio_id = cur.lastrowid
    log_attivita(g.user['id'], 'creazione', 'apparecchi', apparecchio_id,
                 f"Creato da import verifica: {n_marca} {n_modello} ({n_matricola})",
                 request.remote_addr)
else:
    # Logica esistente: override manuale o match AI
    app_override = request.form.get(f'apparecchio_id_{row_id}')
    ...
```

### Sicurezza

- La divisione scelta viene verificata contro `struttura_id` dell'utente prima dell'insert.
- I campi testo usano `?` placeholder (no f-string SQL).
- `log_attivita` traccia la creazione del nuovo apparecchio.

### Flusso completo

```
Upload PDF/Excel verifica
  → AI estrae: matricola, data, esito, tecnico, [marca, modello, descrizione]
  → match DB per matricola
    → trovato → badge verde, riga pre-selezionata
    → non trovato → cella con radio "Seleziona / Crea nuovo"
      → "Seleziona": dropdown esistente (come ora)
      → "Crea nuovo": mini-form pre-compilato AI + divisione
  → Submit
    → per ogni riga selezionata:
      → se crea_nuovo: INSERT apparecchi → usa lastrowid
      → INSERT verifiche con apparecchio_id
```

---

## File Modificati

| File | Modifica |
|------|---------|
| `import_bp.py` | Fix `TIPI_VALIDI`; aggiunge `divisioni_list` alla preview route; branch `crea_nuovo` in `_execute_verifiche` |
| `admin.py` | Fix `reset_database()` aggiungendo seed struttura |
| `scheduler.py` | `_send_scheduled_reports` usa `report_pdf_attivo` invece di `report_schedulato_attivo` |
| `ai_service.py` | Estende `VERIFICA_BATCH_SYSTEM_PROMPT` con campi opzionali apparecchio |
| `templates/import/preview.html` | Aggiunge radio + inline form "Crea nuovo" per righe verifica senza match |
| `schema.sql` | Aggiorna commento chiavi valide per includere `report_pdf_attivo` |

---

## Non incluso in scope

- Modifica UI per esporre `report_pdf_attivo` nel pannello configurazione struttura (può essere aggiunto in un secondo step).
- Estensione della feature "Crea nuovo" ai verbali manutenzione (solo verifiche elettriche per ora).
- Aggiornamento `migrate_*.py` (nessuna modifica schema DB, solo comportamento runtime e config).
