# Design: Ruolo Tecnico

**Data:** 2026-04-05
**Branch:** v2-multi-struttura
**Stato:** approvato, in attesa di implementazione

---

## Contesto

MedInventory gestisce tre ruoli: `superadmin`, `admin`, `utente`. Manca un ruolo per tecnici
esterni che operano su più strutture clienti senza responsabilità amministrative.

---

## Requisiti

- Il tecnico può accedere a **più strutture** (lista assegnata dal superadmin, modificabile)
- All'interno di ogni struttura vede **tutte le divisioni**
- Può **creare, modificare ed eliminare** apparecchi, manutenzioni e verifiche
- **Non può** gestire utenti, divisioni, configurazione o impostazioni
- Seleziona la struttura attiva al login (stesso flusso del superadmin)
- I tecnici sono **creati e gestiti solo dal superadmin**

---

## Schema DB

### Modifica tabella `utenti`

- `struttura_id` diventa **nullable** (era `NOT NULL`): i tecnici non appartengono a una struttura fissa
- `ruolo` CHECK esteso: `('superadmin', 'admin', 'utente', 'tecnico')`

### Nuova tabella `tecnici_strutture`

```sql
CREATE TABLE tecnici_strutture (
  tecnico_id   INTEGER NOT NULL,
  struttura_id INTEGER NOT NULL,
  PRIMARY KEY (tecnico_id, struttura_id),
  FOREIGN KEY (tecnico_id)   REFERENCES utenti(id) ON DELETE CASCADE,
  FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE
);
CREATE INDEX idx_tecnici_strutture_tecnico ON tecnici_strutture(tecnico_id);
CREATE INDEX idx_tecnici_strutture_struttura ON tecnici_strutture(struttura_id);
```

### Script di migrazione: `migrate_v2_2.py`

Ricrea `utenti` senza `NOT NULL` su `struttura_id` e con CHECK aggiornato (rename+create+copy,
pattern già usato in `migrate_v2_1.py`). Crea `tecnici_strutture`. Preserva tutti i dati.

---

## Flusso di login e sessione

```
POST /login → credenziali OK → controlla ruolo
  ├── superadmin → query tutte le strutture attive     → selettore struttura
  ├── tecnico    → query tecnici_strutture per utente  → selettore struttura (stessa UI)
  ├── admin      → struttura_id da utenti              → entra direttamente
  └── utente     → struttura_id da utenti              → entra direttamente
```

- `seleziona_struttura()` in `auth.py` è invariata: imposta `session['struttura_id']`
- `g.struttura_id` viene letto dalla sessione come sempre
- Il **navbar switcher** struttura (già presente) filtra le strutture disponibili in base al ruolo:
  - superadmin: tutte le strutture attive
  - tecnico: solo quelle in `tecnici_strutture`
- `@login_required` e `inject_globals()` non cambiano
- **Caso limite:** se un tecnico ha 0 strutture assegnate, il login mostra un flash
  di errore "Nessuna struttura assegnata. Contattare l'amministratore." e non procede

---

## Permessi nei blueprint

La regola operativa: ogni check `ruolo not in ('admin', 'superadmin')` che blocca la scrittura
viene aggiornato a `ruolo not in ('admin', 'superadmin', 'tecnico')`.

| Operazione | superadmin | admin | tecnico | utente |
|------------|-----------|-------|---------|--------|
| Apparecchi — lettura | ✓ | ✓ | ✓ | ✓ |
| Apparecchi — crea/modifica/elimina/dismetti | ✓ | ✓ | ✓ | ✗ |
| Manutenzioni — lettura | ✓ | ✓ | ✓ | ✓ |
| Manutenzioni — crea/modifica/elimina | ✓ | ✓ | ✓ | ✗ |
| Verifiche — lettura | ✓ | ✓ | ✓ | ✓ |
| Verifiche — crea/modifica/elimina | ✓ | ✓ | ✓ | ✗ |
| Import documenti AI | ✓ | ✓ | ✓ | ✗ |
| Export Excel/PDF | ✓ | ✓ | ✓ | ✓ |
| Admin — utenti, divisioni, config, backup | ✓ | ✓ | ✗ | ✗ |
| Admin — gestione tecnici | ✓ | ✗ | ✗ | ✗ |

**File da aggiornare:** `apparecchi.py`, `manutenzioni.py`, `verifiche.py`, `import_bp.py`.
I controlli sono inline (`if g.current_user['ruolo'] not in (...)`), nessun nuovo decorator.

Il filtraggio `WHERE struttura_id = ?` esistente funziona senza modifiche: il tecnico ha
`g.struttura_id` impostato dalla sessione esattamente come admin e utente.

---

## UI gestione tecnici (solo superadmin)

### Route in `admin.py` sotto `@superadmin_required`

| Route | Metodo | Descrizione |
|-------|--------|-------------|
| `/admin/tecnici` | GET | Lista tecnici con strutture assegnate |
| `/admin/tecnici/nuovo` | GET/POST | Form creazione tecnico |
| `/admin/tecnici/<id>/modifica` | GET/POST | Form modifica tecnico + strutture |
| `/admin/tecnici/<id>/elimina` | POST | Elimina tecnico |

### Template

- `templates/admin/tecnici.html` — tabella con nome, email, badge strutture, azioni
- `templates/admin/tecnico_form.html` — form con campi anagrafici + checklist strutture attive

### Sidebar

Nuova voce "Tecnici" (icona `bi-person-gear`) nella sezione Amministrazione, visibile
solo a superadmin. File: `templates/base.html`.

### Comportamento del form

- Crea tecnico: `primo_accesso = 1` (cambio password obbligatorio al primo login)
- Strutture assegnate: checkboxes con tutte le strutture attive; la submit cancella e riscrive
  le righe in `tecnici_strutture` per quell'utente (delete+insert in transazione)
- Modifica strutture: disponibile in qualsiasi momento, effetto immediato

---

## File modificati

| File | Tipo modifica |
|------|---------------|
| `schema.sql` | struttura_id nullable in utenti, CHECK aggiornato, nuova tabella |
| `migrate_v2_2.py` | nuovo script di migrazione |
| `auth.py` | login: branch tecnico per selettore struttura; switcher struttura filtrato |
| `admin.py` | 4 nuove route tecnici sotto @superadmin_required |
| `apparecchi.py` | aggiunge 'tecnico' ai check di permesso scrittura |
| `manutenzioni.py` | aggiunge 'tecnico' ai check di permesso scrittura |
| `verifiche.py` | aggiunge 'tecnico' ai check di permesso scrittura |
| `import_bp.py` | aggiunge 'tecnico' ai check di permesso |
| `templates/base.html` | voce Tecnici in sidebar |
| `templates/admin/tecnici.html` | nuovo template lista |
| `templates/admin/tecnico_form.html` | nuovo template form |

---

## Fuori scope

- Permessi granulari per divisione all'interno di una struttura (il tecnico vede tutto)
- Creazione tecnici da parte di admin (solo superadmin)
- Log attività separato per tecnici (usa lo stesso `log_attivita` degli altri ruoli)
