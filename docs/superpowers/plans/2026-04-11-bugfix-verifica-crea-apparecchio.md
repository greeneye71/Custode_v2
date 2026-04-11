# Bugfix Sicurezza + Crea Apparecchio da Verifica — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correggere 3 bug dal Codex review (TIPI_VALIDI, reset_database, scheduler doppia email) e aggiungere la possibilità di creare un nuovo apparecchio direttamente dalla preview import verifiche elettriche, con campi pre-compilati dall'AI.

**Architecture:** Modifiche chirurgiche a file Python esistenti (import_bp.py, admin.py, scheduler.py, ai_service.py) e al template preview.html. Nessuna nuova tabella DB, nessun nuovo file. Il backend crea l'apparecchio prima di inserire la verifica nella stessa transazione della riga.

**Tech Stack:** Flask 3.x, SQLite3, HTMX, Bootstrap 5, JavaScript vanilla (show/hide inline form)

---

## File Modificati

| File | Righe coinvolte | Modifica |
|------|----------------|---------|
| `import_bp.py` | 1113 | Fix `TIPI_VALIDI` |
| `admin.py` | 838–862 | Fix `reset_database()` seed struttura |
| `scheduler.py` | 192–212 | Fix chiave config `_send_scheduled_reports` |
| `schema.sql` | 54 | Aggiorna commento chiavi valide |
| `ai_service.py` | 94–113 | Estende `VERIFICA_BATCH_SYSTEM_PROMPT` |
| `import_bp.py` | 534–575 | Aggiunge `divisioni_list` alla preview route |
| `import_bp.py` | 836–857 | Aggiunge branch `crea_nuovo` in `_execute_verifiche` |
| `templates/import/preview.html` | 143–173 | Aggiunge radio + inline form nella cella Apparecchio |

---

## Task 1: Fix TIPI_VALIDI — allineamento al CHECK constraint DB

**Files:**
- Modify: `import_bp.py:1113`

La costante `TIPI_VALIDI` nella conferma email contiene tipi non esistenti nel DB (`straordinaria`, `verifica_elettrica`, `collaudo`) e manca di `verifica` e `calibrazione` che sono validi nel DB. Il CHECK constraint su `manutenzioni.tipo` è: `('preventiva', 'correttiva', 'verifica', 'calibrazione')`.

- [ ] **Step 1: Modifica TIPI_VALIDI**

In `import_bp.py` sostituire la riga 1113:

```python
# PRIMA (sbagliato):
TIPI_VALIDI = ('preventiva', 'correttiva', 'straordinaria', 'verifica_elettrica', 'collaudo')

# DOPO (corretto — corrisponde al CHECK nel DB manutenzioni.tipo):
TIPI_VALIDI = ('preventiva', 'correttiva', 'verifica', 'calibrazione')
```

- [ ] **Step 2: Verifica visiva**

Aprire `schema.sql` riga 240 e confermare che il CHECK recita:
```sql
tipo TEXT NOT NULL CHECK(tipo IN ('preventiva', 'correttiva', 'verifica', 'calibrazione')),
```
I valori in `TIPI_VALIDI` devono corrispondere esattamente.

- [ ] **Step 3: Commit**

```bash
git add import_bp.py
git commit -m "fix: TIPI_VALIDI allineato al CHECK constraint manutenzioni.tipo"
```

---

## Task 2: Fix reset_database() — seed struttura prima delle divisioni

**Files:**
- Modify: `admin.py:838–862`

`reset_database()` inserisce divisioni e utente admin senza `struttura_id`, ma il campo è `NOT NULL` nella tabella `divisioni` e dovrebbe essere impostato anche per gli admin. Questo causa un errore FK/NOT NULL al momento del reset. Il pattern corretto è in `seed.py`.

- [ ] **Step 1: Sostituire il blocco seed in reset_database()**

In `admin.py`, sostituire le righe 838–862 (dal commento `# 5. Seed:` fino a `admin_id = c.lastrowid` + il ciclo `for div_id`) con:

```python
        # 5. Seed: struttura di default + 2 divisioni + utente admin predefinito
        c = db_execute(
            """INSERT INTO strutture (nome, codice, descrizione, modalita, attiva)
               VALUES (?,?,?,?,?)""",
            ('Struttura Principale', 'DEFAULT',
             'Struttura predefinita (rinominare da Amministrazione → Strutture)',
             'avanzata', 1)
        )
        struttura_id = c.lastrowid

        c = db_execute(
            """INSERT INTO divisioni (nome, codice, colore, descrizione, struttura_id)
               VALUES (?,?,?,?,?)""",
            ('Divisione 1', 'DIV1', '#0ea5e9',
             'Prima divisione (rinominare da pannello admin)', struttura_id)
        )
        div1_id = c.lastrowid
        c = db_execute(
            """INSERT INTO divisioni (nome, codice, colore, descrizione, struttura_id)
               VALUES (?,?,?,?,?)""",
            ('Divisione 2', 'DIV2', '#10b981',
             'Seconda divisione (rinominare da pannello admin)', struttura_id)
        )
        div2_id = c.lastrowid

        password_hash = generate_password_hash('admin123')
        c = db_execute(
            """INSERT INTO utenti (email, password_hash, nome, cognome, ruolo, struttura_id, primo_accesso)
               VALUES (?,?,?,?,?,?,1)""",
            ('admin@medinventory.local', password_hash, 'Amministratore', 'Sistema', 'admin',
             struttura_id)
        )
        admin_id = c.lastrowid

        for div_id in (div1_id, div2_id):
            db_execute(
                "INSERT INTO utenti_divisioni (utente_id, divisione_id, ruolo_divisione) VALUES (?,?,?)",
                (admin_id, div_id, 'admin')
            )
```

- [ ] **Step 2: Verifica visiva**

Confermare che le righe successive (commento `# 6. Invalida la sessione`) siano rimaste invariate.

- [ ] **Step 3: Commit**

```bash
git add admin.py
git commit -m "fix: reset_database crea struttura di default prima del seed divisioni/utente"
```

---

## Task 3: Fix scheduler — doppia email con stesso toggle

**Files:**
- Modify: `scheduler.py:192–212`
- Modify: `schema.sql:54`

`_send_deadline_alerts()` e `_send_scheduled_reports()` usano entrambe `report_schedulato_attivo`, inviando due email separate. Si introduce la chiave `report_pdf_attivo` per il PDF periodico; il digest testuale rimane su `report_schedulato_attivo`.

- [ ] **Step 1: Aggiorna _send_scheduled_reports in scheduler.py**

Sostituire le righe 192–212 di `scheduler.py`:

```python
    def _send_scheduled_reports(self):
        """Invia report periodici PDF alle strutture con report_pdf_attivo=1."""
        with self.app.app_context():
            from models import query_all, get_struttura_config
            strutture = query_all("SELECT * FROM strutture WHERE attiva=1")
            global_cfg = self.app.config.get('APP_CONFIG', {})

            for struttura in strutture:
                sid = struttura['id']
                if get_struttura_config(sid, 'report_pdf_attivo', '0') != '1':
                    continue
                frequenza = get_struttura_config(sid, 'report_frequenza', 'settimanale')
                if not self._is_digest_due(frequenza):
                    continue
                if not struttura.get('email_notifiche'):
                    continue

                try:
                    self._genera_e_invia_report(struttura, global_cfg)
                except Exception as e:
                    logger.error(f"Errore report struttura {struttura['nome']}: {e}")
```

- [ ] **Step 2: Aggiorna commento chiavi in schema.sql**

In `schema.sql` riga 54 sostituire:
```sql
-- smtp_use_tls, report_frequenza, report_schedulato_attivo
```
con:
```sql
-- smtp_use_tls, report_frequenza, report_schedulato_attivo, report_pdf_attivo
```

- [ ] **Step 3: Commit**

```bash
git add scheduler.py schema.sql
git commit -m "fix: scheduler PDF usa chiave report_pdf_attivo separata per evitare doppia email"
```

---

## Task 4: Estende AI prompt — estrai campi apparecchio dalle verifiche

**Files:**
- Modify: `ai_service.py:94–113`

Il prompt attuale estrae solo dati della verifica (data, esito, tecnico). Si aggiungono tre campi opzionali: `marca`, `modello`, `descrizione`, usati per pre-compilare il form "Crea nuovo apparecchio".

- [ ] **Step 1: Aggiorna VERIFICA_BATCH_SYSTEM_PROMPT**

In `ai_service.py`, sostituire le righe 94–113 con:

```python
VERIFICA_BATCH_SYSTEM_PROMPT = """Sei un assistente specializzato nell'analisi di rapporti di verifica di sicurezza elettrica per apparecchi elettromedicali.
Ti verrà fornito il testo estratto da un documento (PDF, Excel o CSV) che può contenere una o più verifiche di sicurezza elettrica.

Devi estrarre i dati e restituire un array JSON. Ogni elemento dell'array rappresenta una verifica:
- matricola (il numero di serie/matricola dell'apparecchio verificato - fondamentale)
- data_verifica (formato YYYY-MM-DD - obbligatorio)
- prossima_scadenza (formato YYYY-MM-DD - opzionale, calcolata dalla periodicità se non esplicitata)
- periodicita_giorni (365 per annuale, 730 per biennale - default 730 se non specificato)
- esito (uno tra: positivo, negativo, con_riserva - obbligatorio)
- tecnico_ditta (nome del tecnico e/o ditta che ha eseguito la verifica - opzionale)
- note (osservazioni o note del rapporto - opzionale)
- marca (produttore dell'apparecchio verificato - opzionale, se presente nel documento)
- modello (modello dell'apparecchio verificato - opzionale, se presente nel documento)
- descrizione (tipo/descrizione apparecchio, es. "Elettrobisturi", "Monitor multiparametrico" - opzionale, se presente nel documento)

REGOLE:
- Restituisci SOLO un array JSON valido, senza altro testo
- Se il documento contiene più apparecchi/verifiche, restituisci un elemento per ognuno
- La matricola è il campo più importante per identificare l'apparecchio
- Per l'esito: "pass", "OK", "conforme", "idoneo" → positivo; "fail", "KO", "non conforme", "non idoneo" → negativo; "con riserva", "condizionale" → con_riserva
- Se la data non è in formato standard, convertila in YYYY-MM-DD
- Se il documento contiene informazioni sull'apparecchio (marca, modello, tipo/descrizione), includile nei campi opzionali marca/modello/descrizione
- Keywords che identificano verifiche elettriche: sicurezza elettrica, corrente di dispersione, IEC 62353, messa a terra, CEI, VSE
"""
```

- [ ] **Step 2: Commit**

```bash
git add ai_service.py
git commit -m "feat: VERIFICA_BATCH_SYSTEM_PROMPT estrae marca/modello/descrizione apparecchio"
```

---

## Task 5: Preview route — aggiungi divisioni_list al contesto

**Files:**
- Modify: `import_bp.py:534–575`

La route `/import/<id>` deve passare al template la lista di divisioni accessibili, necessaria per il select "Divisione" nel form "Crea nuovo apparecchio".

- [ ] **Step 1: Aggiorna la preview route**

In `import_bp.py`, sostituire le righe 534–575 (da `# For verbali/verifiche:` fino a `return render_template(...)`) con:

```python
    # For verbali/verifiche: provide apparecchi list for manual selection
    apparecchi_list = []
    divisioni_list = []
    tipo = import_rec['tipo_import']
    if tipo in ('verbale_manutenzione', 'verifica_elettrica'):
        div = getattr(g, 'divisione_attiva', None)
        if div and div.get('id') != 'tutte':
            apparecchi_list = query_all(
                """SELECT a.id, a.matricola, a.marca, a.modello, d.nome as divisione_nome
                   FROM apparecchi a
                   LEFT JOIN divisioni d ON a.divisione_id = d.id
                   WHERE a.stato != 'dismesso' AND a.divisione_id = ?
                   ORDER BY a.matricola""",
                [div['id']]
            )
        elif getattr(g, 'user', {}).get('ruolo') == 'admin':
            apparecchi_list = query_all(
                """SELECT a.id, a.matricola, a.marca, a.modello, d.nome as divisione_nome
                   FROM apparecchi a
                   LEFT JOIN divisioni d ON a.divisione_id = d.id
                   WHERE a.stato != 'dismesso'
                   ORDER BY a.matricola"""
            )
        else:
            ids = [d['id'] for d in getattr(g, 'divisioni', [])]
            if ids:
                ph = ','.join('?' * len(ids))
                apparecchi_list = query_all(
                    f"""SELECT a.id, a.matricola, a.marca, a.modello, d.nome as divisione_nome
                       FROM apparecchi a
                       LEFT JOIN divisioni d ON a.divisione_id = d.id
                       WHERE a.stato != 'dismesso' AND a.divisione_id IN ({ph})
                       ORDER BY a.matricola""",
                    ids
                )

        # Divisioni accessibili per il form "crea nuovo apparecchio"
        if tipo == 'verifica_elettrica':
            struttura_id = getattr(g, 'struttura_id', None) or g.user.get('struttura_id')
            if not struttura_id and import_rec.get('divisione_id'):
                div_row = query_one(
                    "SELECT struttura_id FROM divisioni WHERE id=?",
                    (import_rec['divisione_id'],)
                )
                if div_row:
                    struttura_id = div_row['struttura_id']
            if struttura_id:
                divisioni_list = query_all(
                    "SELECT id, nome, colore FROM divisioni WHERE attiva=1 AND struttura_id=? ORDER BY nome",
                    (struttura_id,)
                )

    tipo_label = DOC_TYPE_LABELS.get(tipo, tipo)

    # Divisione attiva corrente (per preselezionare nel form "crea nuovo")
    divisione_attiva_id = None
    div_attiva = getattr(g, 'divisione_attiva', None)
    if div_attiva and div_attiva.get('id') != 'tutte':
        divisione_attiva_id = div_attiva.get('id')

    return render_template('import/preview.html',
                           import_rec=import_rec, rows=rows,
                           nuovi=nuovi, trovati=trovati,
                           tipo_label=tipo_label,
                           apparecchi_list=apparecchi_list,
                           divisioni_list=divisioni_list,
                           divisione_attiva_id=divisione_attiva_id)
```

- [ ] **Step 2: Commit**

```bash
git add import_bp.py
git commit -m "feat: preview route aggiunge divisioni_list per form crea nuovo apparecchio"
```

---

## Task 6: Backend — branch crea_nuovo in _execute_verifiche

**Files:**
- Modify: `import_bp.py:836–857`

Nella funzione `_execute_verifiche`, prima di risolvere `apparecchio_id` tramite override/match, si verifica se l'utente ha scelto di creare un nuovo apparecchio. In caso affermativo: valida i campi, verifica la divisione, inserisce l'apparecchio e usa il `lastrowid` come `apparecchio_id` per la verifica.

- [ ] **Step 1: Sostituire il blocco di risoluzione apparecchio_id**

In `import_bp.py`, sostituire le righe 836–857 (da `app_override = request.form.get(...)` fino a `continue` del `if not apparecchio_id:`) con:

```python
            # Risoluzione apparecchio: crea nuovo, override manuale, o match AI
            crea_nuovo = request.form.get(f'crea_nuovo_{row_id}') == '1'

            if crea_nuovo:
                n_marca = request.form.get(f'nuovo_marca_{row_id}', '').strip()
                n_modello = request.form.get(f'nuovo_modello_{row_id}', '').strip()
                n_matricola = request.form.get(f'nuovo_matricola_{row_id}', '').strip()
                n_descrizione = request.form.get(f'nuovo_descrizione_{row_id}', '').strip()
                n_divisione_id = request.form.get(f'nuovo_divisione_id_{row_id}', type=int)

                if not (n_marca and n_modello and n_matricola and n_divisione_id):
                    raise ValueError(
                        "Marca, modello, matricola e divisione sono obbligatori "
                        "per creare un nuovo apparecchio"
                    )

                struttura_id_user = getattr(g, 'struttura_id', None) or g.user.get('struttura_id')
                div_check = query_one(
                    "SELECT struttura_id FROM divisioni WHERE id=? AND attiva=1",
                    (n_divisione_id,)
                )
                if not div_check or (struttura_id_user and div_check['struttura_id'] != struttura_id_user):
                    raise ValueError("Divisione non accessibile")

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
                             request.remote_addr,
                             struttura_id=div_check['struttura_id'])
            else:
                app_override = request.form.get(f'apparecchio_id_{row_id}')
                if app_override:
                    try:
                        apparecchio_id = int(app_override)
                    except (ValueError, TypeError):
                        apparecchio_id = None
                    # validate override is within accessible divisions
                    if apparecchio_id and g.user['ruolo'] not in ('admin', 'superadmin'):
                        accessible_ids = [d['id'] for d in g.divisioni]
                        app_rec = query_one(
                            "SELECT divisione_id FROM apparecchi WHERE id = ?", (apparecchio_id,))
                        if not app_rec or app_rec['divisione_id'] not in accessible_ids:
                            apparecchio_id = None
                else:
                    apparecchio_id = row['apparecchio_match_id']

            if not apparecchio_id:
                execute("UPDATE import_preview SET stato = 'rejected', "
                        "note_revisione = 'Nessun apparecchio associato' WHERE id = ?",
                        (int(row_id),))
                errors += 1
                continue
```

- [ ] **Step 2: Verifica che le righe successive siano intatte**

Le righe dopo il blocco modificato iniziano con:
```python
            # Bug E: validate required date field
            data_verifica = (data.get('data_verifica') or '').strip()
```
Devono essere rimaste invariate.

- [ ] **Step 3: Commit**

```bash
git add import_bp.py
git commit -m "feat: _execute_verifiche supporta creazione nuovo apparecchio da form preview"
```

---

## Task 7: Template — form inline "Crea nuovo apparecchio" nella cella verifica

**Files:**
- Modify: `templates/import/preview.html:143–173`

La cella "Apparecchio" per righe senza match nella sezione `verifica_elettrica` mostra oggi solo il dropdown. Si sostituisce con un radio toggle + due sezioni condizionali: dropdown esistente e mini-form creazione.

- [ ] **Step 1: Sostituire la cella Apparecchio per verifica_elettrica senza match**

In `templates/import/preview.html`, sostituire le righe 158–173 (il blocco `{% if row.apparecchio_match_id %}` dentro la sezione `verifica_elettrica`):

```html
                            <td>
                                {% if row.apparecchio_match_id %}
                                <span class="badge bg-success">
                                    {{ row.match_marca }} {{ row.match_modello }}
                                </span>
                                <br><small class="text-muted">{{ row.match_matricola }}</small>
                                {% else %}
                                <div class="mb-1">
                                    <div class="form-check form-check-inline">
                                        <input class="form-check-input apparecchio-mode"
                                               type="radio"
                                               name="modo_apparecchio_{{ row.id }}"
                                               id="sel_exist_{{ row.id }}"
                                               value="seleziona"
                                               checked
                                               onchange="toggleApparecchioMode({{ row.id }})">
                                        <label class="form-check-label small" for="sel_exist_{{ row.id }}">
                                            Seleziona esistente
                                        </label>
                                    </div>
                                    <div class="form-check form-check-inline">
                                        <input class="form-check-input apparecchio-mode"
                                               type="radio"
                                               name="modo_apparecchio_{{ row.id }}"
                                               id="crea_new_{{ row.id }}"
                                               value="crea"
                                               onchange="toggleApparecchioMode({{ row.id }})">
                                        <label class="form-check-label small" for="crea_new_{{ row.id }}">
                                            Crea nuovo
                                        </label>
                                    </div>
                                </div>

                                {# Sezione: seleziona esistente #}
                                <div id="sect_exist_{{ row.id }}">
                                    <select class="form-select form-select-sm"
                                            name="apparecchio_id_{{ row.id }}"
                                            style="min-width: 200px; font-size: 0.75rem;">
                                        <option value="">-- Seleziona --</option>
                                        {% for a in apparecchi_list %}
                                        <option value="{{ a.id }}">{{ a.matricola }} - {{ a.marca }} {{ a.modello }}</option>
                                        {% endfor %}
                                    </select>
                                </div>

                                {# Sezione: crea nuovo apparecchio #}
                                <div id="sect_crea_{{ row.id }}" style="display:none;">
                                    <input type="hidden" name="crea_nuovo_{{ row.id }}" value="0"
                                           id="flag_crea_{{ row.id }}">
                                    <div class="row g-1 mt-1" style="min-width: 260px;">
                                        <div class="col-6">
                                            <input type="text" class="form-control form-control-sm"
                                                   name="nuovo_marca_{{ row.id }}"
                                                   placeholder="Marca *"
                                                   value="{{ d.get('marca', '') }}">
                                        </div>
                                        <div class="col-6">
                                            <input type="text" class="form-control form-control-sm"
                                                   name="nuovo_modello_{{ row.id }}"
                                                   placeholder="Modello *"
                                                   value="{{ d.get('modello', '') }}">
                                        </div>
                                        <div class="col-6">
                                            <input type="text" class="form-control form-control-sm"
                                                   name="nuovo_matricola_{{ row.id }}"
                                                   placeholder="Matricola *"
                                                   value="{{ d.get('matricola', '') }}">
                                        </div>
                                        <div class="col-6">
                                            <input type="text" class="form-control form-control-sm"
                                                   name="nuovo_descrizione_{{ row.id }}"
                                                   placeholder="Descrizione"
                                                   value="{{ d.get('descrizione', '') }}">
                                        </div>
                                        <div class="col-12">
                                            <select class="form-select form-select-sm"
                                                    name="nuovo_divisione_id_{{ row.id }}">
                                                <option value="">-- Divisione * --</option>
                                                {% for div in divisioni_list %}
                                                <option value="{{ div.id }}"
                                                    {% if divisione_attiva_id and div.id == divisione_attiva_id %}selected{% endif %}>
                                                    {{ div.nome }}
                                                </option>
                                                {% endfor %}
                                            </select>
                                        </div>
                                    </div>
                                </div>
                                {% endif %}
                            </td>
```

- [ ] **Step 2: Aggiungere la funzione JS in `{% block scripts_extra %}`**

In fondo al file, dentro il blocco `{% block scripts_extra %}`, aggiungere dopo la funzione `toggleAll`:

```javascript
function toggleApparecchioMode(rowId) {
    const isCrea = document.getElementById('crea_new_' + rowId).checked;
    document.getElementById('sect_exist_' + rowId).style.display = isCrea ? 'none' : '';
    document.getElementById('sect_crea_' + rowId).style.display = isCrea ? '' : 'none';
    document.getElementById('flag_crea_' + rowId).value = isCrea ? '1' : '0';
}
```

- [ ] **Step 3: Commit**

```bash
git add templates/import/preview.html
git commit -m "feat: preview verifica aggiunge radio + inline form crea nuovo apparecchio"
```

---

## Task 8: Verifica manuale end-to-end

**Files:** nessuno (solo verifica)

- [ ] **Step 1: Avviare l'app**

```bash
python app.py
```

Aprire `http://localhost:5000` nel browser.

- [ ] **Step 2: Verificare Bug 1 (TIPI_VALIDI)**

Navigare in **Importa → Coda Email**. Se presente una bozza email in attesa, aprirla e verificare che il campo "Tipo" mostri solo: `preventiva`, `correttiva`, `verifica`, `calibrazione` (e non `straordinaria` o simili). Confermare una bozza con tipo `verifica` e verificare che venga salvata correttamente in `manutenzioni` senza errori.

- [ ] **Step 3: Verificare Bug 2 (reset_database)**

Navigare in **Admin → Configurazione → Reset Database**. Eseguire il reset. Verificare che:
- la pagina reindirizza al login
- è possibile accedere con `admin@medinventory.local` / `admin123`
- il pannello mostra la struttura "Struttura Principale" creata

- [ ] **Step 4: Verificare Bug 3 (scheduler)**

Aprire la configurazione struttura e confermare che `report_schedulato_attivo` e `report_pdf_attivo` siano chiavi distinte. Con `report_schedulato_attivo=1` e `report_pdf_attivo=0` (default), il job del scheduler deve inviare solo il digest testuale. Con entrambe a `1` deve inviare entrambe le email. *(Questo può essere verificato anche ispezionando i log con `python run_production.py` e attendendo il trigger del job.)*

- [ ] **Step 5: Verificare Feature — AI prompt esteso**

Importare un PDF di verifica elettrica che contiene informazioni sull'apparecchio (marca, modello visibili nel documento). Nella preview verificare che i campi `marca`/`modello`/`descrizione` siano presenti nel `parsed_data` della riga (visibile in Developer Tools oppure stampando temporaneamente `{{ d }}`).

- [ ] **Step 6: Verificare Feature — form crea nuovo**

Importare un PDF di verifica con una matricola inesistente nel DB. Nella preview:
1. La riga deve mostrare i radio "Seleziona esistente" / "Crea nuovo"
2. Selezionare "Crea nuovo" → il form inline appare con i campi pre-compilati
3. Selezionare "Seleziona esistente" → il dropdown torna visibile, il form scompare
4. Selezionare "Crea nuovo", compilare i campi, scegliere una divisione, cliccare "Importa Selezionati"
5. Verificare che l'apparecchio appaia in **Apparecchi** e la verifica in **Verifiche** per quell'apparecchio

- [ ] **Step 7: Commit finale (se tutto ok)**

```bash
git log --oneline -8
```

Verificare che tutti i commit dei task 1–7 siano presenti.

---

## Checklist spec coverage

| Requisito spec | Task |
|---------------|------|
| Fix TIPI_VALIDI | Task 1 |
| Fix reset_database seed struttura | Task 2 |
| Fix scheduler doppia email | Task 3 |
| Estendi AI prompt marca/modello/descrizione | Task 4 |
| divisioni_list nella preview route | Task 5 |
| Backend crea_nuovo in _execute_verifiche | Task 6 |
| Template radio + inline form | Task 7 |
| Verifica manuale e2e | Task 8 |
