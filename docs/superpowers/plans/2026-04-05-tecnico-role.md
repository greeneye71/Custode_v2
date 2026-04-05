# Ruolo Tecnico — Piano di Implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aggiungere il ruolo `tecnico` — utente cross-struttura con permessi operativi completi ma senza accesso all'amministrazione.

**Architecture:** Nuova tabella `tecnici_strutture` (N:M tra utenti e strutture). Il tecnico usa lo stesso meccanismo di `struttura_impersonata_id` in sessione già usato dal superadmin. I controlli di permesso nei blueprint vengono estesi inline.

**Tech Stack:** Flask 3.x · SQLite3 · Jinja2 · Bootstrap 5 · HTMX

---

## File coinvolti

| File | Azione |
|------|--------|
| `schema.sql` | Aggiunge `tecnici_strutture`, aggiorna CHECK `ruolo` |
| `migrate_v2_2.py` | Nuovo script di migrazione |
| `auth.py` | `_load_user_from_session`, `login()`, `cambia_divisione()`, 2 nuove route |
| `app.py` | `inject_globals()`: `strutture_list` anche per tecnico |
| `apparecchi.py` | `_get_divisione_filter`, 2 check `ruolo` |
| `manutenzioni.py` | `_get_divisione_filter`, 2 check `ruolo` |
| `verifiche.py` | `_get_divisione_filter` |
| `admin.py` | 4 nuove route gestione tecnici |
| `templates/base.html` | Switcher struttura tecnico, "Tutte" divisioni, sidebar link |
| `templates/partials/struttura_switcher_tecnico.html` | Nuovo partial |
| `templates/auth/seleziona_struttura_tecnico.html` | Nuovo template |
| `templates/admin/tecnici.html` | Nuovo template lista |
| `templates/admin/tecnico_form.html` | Nuovo template form |

---

## Task 1: Schema DB e script di migrazione

**Files:**
- Modify: `schema.sql`
- Create: `migrate_v2_2.py`

- [ ] **Step 1: Aggiorna `schema.sql`**

  Sostituisci il CHECK sul ruolo in `utenti` e aggiungi la nuova tabella dopo `utenti_divisioni`.

  In `schema.sql`, modifica la riga:
  ```sql
  ruolo TEXT NOT NULL CHECK(ruolo IN ('superadmin', 'admin', 'utente')),
  ```
  con:
  ```sql
  ruolo TEXT NOT NULL CHECK(ruolo IN ('superadmin', 'admin', 'utente', 'tecnico')),
  ```

  Poi aggiungi dopo il blocco `UTENTI_DIVISIONI` (dopo l'indice `idx_utenti_divisioni_divisione`):
  ```sql
  -- ============================================
  -- TECNICI_STRUTTURE (strutture accessibili per tecnico)
  -- ============================================
  CREATE TABLE IF NOT EXISTS tecnici_strutture (
    tecnico_id   INTEGER NOT NULL,
    struttura_id INTEGER NOT NULL,
    PRIMARY KEY (tecnico_id, struttura_id),
    FOREIGN KEY (tecnico_id)   REFERENCES utenti(id) ON DELETE CASCADE,
    FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE
  );

  CREATE INDEX IF NOT EXISTS idx_tecnici_strutture_tecnico   ON tecnici_strutture(tecnico_id);
  CREATE INDEX IF NOT EXISTS idx_tecnici_strutture_struttura ON tecnici_strutture(struttura_id);
  ```

- [ ] **Step 2: Crea `migrate_v2_2.py`**

  ```python
  """
  migrate_v2_2.py — Aggiunge ruolo 'tecnico' e tabella tecnici_strutture.

  Ricrea utenti con CHECK aggiornato; crea tecnici_strutture se mancante.
  I dati esistenti vengono preservati.

  Uso:
      python migrate_v2_2.py [path/to/database.sqlite]
  """
  import sqlite3
  import sys
  import os

  DB_PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join('data', 'database.sqlite')


  def run(db_path):
      if not os.path.exists(db_path):
          print(f"ERRORE: database non trovato: {db_path}")
          sys.exit(1)

      conn = sqlite3.connect(db_path)
      conn.row_factory = sqlite3.Row
      conn.execute("PRAGMA foreign_keys = OFF")
      conn.execute("PRAGMA journal_mode = WAL")

      try:
          # --- UTENTI: aggiorna CHECK ruolo ---
          cols = [row[1] for row in conn.execute("PRAGMA table_info(utenti)").fetchall()]
          col_list = ', '.join(cols)

          print("Migrazione tabella utenti (aggiunta ruolo tecnico)...")
          conn.execute("ALTER TABLE utenti RENAME TO utenti_old")
          conn.execute(f"""
              CREATE TABLE utenti (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                nome TEXT NOT NULL,
                cognome TEXT NOT NULL,
                ruolo TEXT NOT NULL CHECK(ruolo IN ('superadmin', 'admin', 'utente', 'tecnico')),
                divisione_default_id INTEGER,
                attivo INTEGER DEFAULT 1,
                primo_accesso INTEGER DEFAULT 1,
                ultimo_accesso DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                struttura_id INTEGER,
                FOREIGN KEY (struttura_id) REFERENCES strutture(id),
                FOREIGN KEY (divisione_default_id) REFERENCES divisioni(id)
              )
          """)
          conn.execute(f"INSERT INTO utenti SELECT {col_list} FROM utenti_old")
          conn.execute("DROP TABLE utenti_old")
          print("  OK — utenti migrati.")

          # --- TECNICI_STRUTTURE ---
          exists = conn.execute(
              "SELECT name FROM sqlite_master WHERE type='table' AND name='tecnici_strutture'"
          ).fetchone()
          if not exists:
              print("Creazione tabella tecnici_strutture...")
              conn.execute("""
                  CREATE TABLE tecnici_strutture (
                    tecnico_id   INTEGER NOT NULL,
                    struttura_id INTEGER NOT NULL,
                    PRIMARY KEY (tecnico_id, struttura_id),
                    FOREIGN KEY (tecnico_id)   REFERENCES utenti(id) ON DELETE CASCADE,
                    FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE
                  )
              """)
              conn.execute(
                  "CREATE INDEX idx_tecnici_strutture_tecnico   ON tecnici_strutture(tecnico_id)")
              conn.execute(
                  "CREATE INDEX idx_tecnici_strutture_struttura ON tecnici_strutture(struttura_id)")
              print("  OK — tecnici_strutture creata.")
          else:
              print("tecnici_strutture già esistente, skip.")

          conn.execute("PRAGMA foreign_keys = ON")
          conn.commit()
          print("\nMigrazione v2.2 completata con successo.")

      except Exception as e:
          conn.rollback()
          print(f"ERRORE durante la migrazione: {e}")
          import traceback
          traceback.print_exc()
          sys.exit(1)
      finally:
          conn.close()


  if __name__ == '__main__':
      run(DB_PATH)
  ```

- [ ] **Step 3: Aggiungi `tecnici_strutture` in `models.py` — `apply_schema_updates()`**

  In `models.py`, dentro `apply_schema_updates()`, aggiungi alla lista `migrations`:
  ```python
  """CREATE TABLE IF NOT EXISTS tecnici_strutture (
      tecnico_id   INTEGER NOT NULL,
      struttura_id INTEGER NOT NULL,
      PRIMARY KEY (tecnico_id, struttura_id),
      FOREIGN KEY (tecnico_id)   REFERENCES utenti(id) ON DELETE CASCADE,
      FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE
  )""",
  "CREATE INDEX IF NOT EXISTS idx_tecnici_strutture_tecnico   ON tecnici_strutture(tecnico_id)",
  "CREATE INDEX IF NOT EXISTS idx_tecnici_strutture_struttura ON tecnici_strutture(struttura_id)",
  ```

- [ ] **Step 4: Verifica sintassi**

  ```bash
  python -m py_compile migrate_v2_2.py models.py
  echo OK
  ```
  Atteso: `OK`

- [ ] **Step 5: Commit**

  ```bash
  git add schema.sql migrate_v2_2.py models.py
  git commit -m "feat: schema tecnici_strutture e migrazione v2.2"
  ```

---

## Task 2: auth.py — sessione tecnico, login, nuove route

**Files:**
- Modify: `auth.py`

- [ ] **Step 1: `_load_user_from_session` — caricamento struttura per tecnico**

  Nella funzione `_load_user_from_session`, il blocco che popola `struttura` è:
  ```python
  if g.user['ruolo'] == 'superadmin':
      struttura_impersonata_id = session.get('struttura_impersonata_id')
      if struttura_impersonata_id:
          struttura = query_one(...)
          if struttura is None:
              session.pop('struttura_impersonata_id', None)
  else:
      struttura_id = g.user.get('struttura_id')
      if struttura_id:
          struttura = query_one(...)
  ```

  Cambia il blocco `else` in:
  ```python
  elif g.user['ruolo'] == 'tecnico':
      struttura_impersonata_id = session.get('struttura_impersonata_id')
      if struttura_impersonata_id:
          allowed = query_one(
              "SELECT struttura_id FROM tecnici_strutture WHERE tecnico_id=? AND struttura_id=?",
              (g.user['id'], struttura_impersonata_id)
          )
          if allowed:
              struttura = query_one(
                  "SELECT * FROM strutture WHERE id=? AND attiva=1",
                  (struttura_impersonata_id,)
              )
          if struttura is None:
              session.pop('struttura_impersonata_id', None)
  else:
      struttura_id = g.user.get('struttura_id')
      if struttura_id:
          struttura = query_one(
              "SELECT * FROM strutture WHERE id=? AND attiva=1",
              (struttura_id,)
          )
  ```

- [ ] **Step 2: `_load_user_from_session` — divisioni per tecnico**

  Il blocco che carica `g.divisioni` ha il ramo `elif g.user['ruolo'] == 'admin':`. Cambia:
  ```python
  elif g.user['ruolo'] == 'admin':
      struttura_id = g.struttura_id
      if struttura_id:
          g.divisioni = query_all(
              "SELECT * FROM divisioni WHERE attiva = 1 AND struttura_id = ? ORDER BY nome",
              (struttura_id,)
          )
      else:
          g.divisioni = query_all(
              "SELECT * FROM divisioni WHERE attiva = 1 ORDER BY nome"
          )
  ```
  in:
  ```python
  elif g.user['ruolo'] in ('admin', 'tecnico'):
      struttura_id = g.struttura_id
      if struttura_id:
          g.divisioni = query_all(
              "SELECT * FROM divisioni WHERE attiva = 1 AND struttura_id = ? ORDER BY nome",
              (struttura_id,)
          )
      else:
          g.divisioni = []
  ```

- [ ] **Step 3: `_load_user_from_session` — divisione_attiva default per tecnico**

  Trova il blocco che imposta `g.divisione_attiva`. Dopo:
  ```python
  elif div_attiva_id == 'tutte' and g.user['ruolo'] in ('admin', 'superadmin'):
      g.divisione_attiva = {'id': 'tutte', 'nome': 'Tutte le divisioni', 'colore': '#6b7280'}
  ```
  Cambia il check in:
  ```python
  elif div_attiva_id == 'tutte' and g.user['ruolo'] in ('admin', 'superadmin', 'tecnico'):
      g.divisione_attiva = {'id': 'tutte', 'nome': 'Tutte le divisioni', 'colore': '#6b7280'}
  ```

  Poi nel ramo finale `elif g.divisioni:`, aggiorna:
  ```python
  elif g.divisioni:
      if g.user['ruolo'] == 'tecnico':
          g.divisione_attiva = {'id': 'tutte', 'nome': 'Tutte le divisioni', 'colore': '#6b7280'}
          session['divisione_attiva_id'] = 'tutte'
      else:
          g.divisione_attiva = g.divisioni[0]
          session['divisione_attiva_id'] = g.divisioni[0]['id']
  ```

- [ ] **Step 4: `_load_user_from_session` — scadenze_alert_count per tecnico**

  Trova le due righe:
  ```python
  elif g.user['ruolo'] in ('admin', 'superadmin') and g.struttura_id:
  ```
  e:
  ```python
  elif g.user['ruolo'] in ('admin', 'superadmin'):
  ```
  Cambia entrambe aggiungendo `'tecnico'`:
  ```python
  elif g.user['ruolo'] in ('admin', 'superadmin', 'tecnico') and g.struttura_id:
  ```
  ```python
  elif g.user['ruolo'] in ('admin', 'superadmin', 'tecnico'):
  ```

- [ ] **Step 5: `cambia_divisione` — abilita 'tutte' per tecnico**

  Trova:
  ```python
  if divisione_id == 'tutte' and g.user['ruolo'] in ('admin', 'superadmin'):
  ```
  Cambia in:
  ```python
  if divisione_id == 'tutte' and g.user['ruolo'] in ('admin', 'superadmin', 'tecnico'):
  ```

- [ ] **Step 6: `login()` — redirect tecnico a selezione struttura**

  Trova, in fondo a `login()`:
  ```python
  if user['primo_accesso']:
      return redirect(url_for('auth.cambio_password'))

  return redirect(url_for('index'))
  ```
  Cambia in:
  ```python
  if user['primo_accesso']:
      return redirect(url_for('auth.cambio_password'))

  if user['ruolo'] == 'tecnico':
      strutture_assegnate = query_all(
          """SELECT s.id FROM strutture s
             JOIN tecnici_strutture ts ON s.id = ts.struttura_id
             WHERE ts.tecnico_id = ? AND s.attiva = 1""",
          (user['id'],)
      )
      if not strutture_assegnate:
          execute("DELETE FROM sessioni WHERE token = ?", (token,))
          session.clear()
          flash('Nessuna struttura assegnata. Contattare l\'amministratore.', 'danger')
          return render_template('login.html', email=email)
      if len(strutture_assegnate) == 1:
          session['struttura_impersonata_id'] = strutture_assegnate[0]['id']
      else:
          return redirect(url_for('auth.tecnico_seleziona_struttura_page'))

  return redirect(url_for('index'))
  ```

- [ ] **Step 7: Aggiungi 2 nuove route in `auth.py`**

  Aggiungi in fondo al file, dopo `esci_impersonazione`:

  ```python
  @auth_bp.route('/tecnico/seleziona-struttura')
  @login_required
  def tecnico_seleziona_struttura_page():
      """Pagina di selezione struttura per tecnico con più strutture assegnate."""
      if g.user['ruolo'] != 'tecnico':
          return redirect(url_for('index'))
      strutture = query_all(
          """SELECT s.id, s.nome FROM strutture s
             JOIN tecnici_strutture ts ON s.id = ts.struttura_id
             WHERE ts.tecnico_id = ? AND s.attiva = 1
             ORDER BY s.nome""",
          (g.user['id'],)
      )
      return render_template('auth/seleziona_struttura_tecnico.html', strutture=strutture)


  @auth_bp.route('/tecnico/struttura/<int:struttura_id>')
  @login_required
  def tecnico_seleziona_struttura(struttura_id):
      """Tecnico imposta la struttura attiva (verifica accesso)."""
      if g.user['ruolo'] != 'tecnico':
          flash('Accesso non autorizzato.', 'danger')
          return redirect(url_for('index'))
      allowed = query_one(
          "SELECT struttura_id FROM tecnici_strutture WHERE tecnico_id = ? AND struttura_id = ?",
          (g.user['id'], struttura_id)
      )
      if not allowed:
          flash('Struttura non assegnata.', 'danger')
          return redirect(url_for('auth.tecnico_seleziona_struttura_page'))
      struttura = query_one(
          "SELECT nome FROM strutture WHERE id = ? AND attiva = 1", (struttura_id,)
      )
      if not struttura:
          flash('Struttura non trovata o non attiva.', 'danger')
          return redirect(url_for('auth.tecnico_seleziona_struttura_page'))
      session['struttura_impersonata_id'] = struttura_id
      return redirect(url_for('index'))
  ```

- [ ] **Step 8: Verifica sintassi**

  ```bash
  python -m py_compile auth.py
  echo OK
  ```
  Atteso: `OK`

- [ ] **Step 9: Commit**

  ```bash
  git add auth.py
  git commit -m "feat: auth — sessione e login per ruolo tecnico"
  ```

---

## Task 3: app.py — strutture_list per tecnico in inject_globals

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Aggiorna il blocco `strutture_list` in `inject_globals()`**

  Trova:
  ```python
  # Lista strutture per il switcher (solo superadmin)
  strutture_list = []
  if getattr(g, 'user', None) and g.user.get('ruolo') == 'superadmin':
      if not hasattr(g, '_strutture_list_cache'):
          g._strutture_list_cache = query_all(
              "SELECT id, nome FROM strutture WHERE attiva=1 ORDER BY nome"
          )
      strutture_list = g._strutture_list_cache
  ```

  Cambia in:
  ```python
  # Lista strutture per il switcher (superadmin: tutte; tecnico: le sue)
  strutture_list = []
  if getattr(g, 'user', None):
      _ruolo = g.user.get('ruolo')
      if _ruolo == 'superadmin':
          if not hasattr(g, '_strutture_list_cache'):
              g._strutture_list_cache = query_all(
                  "SELECT id, nome FROM strutture WHERE attiva=1 ORDER BY nome"
              )
          strutture_list = g._strutture_list_cache
      elif _ruolo == 'tecnico':
          if not hasattr(g, '_strutture_list_cache'):
              g._strutture_list_cache = query_all(
                  """SELECT s.id, s.nome FROM strutture s
                     JOIN tecnici_strutture ts ON s.id = ts.struttura_id
                     WHERE ts.tecnico_id = ? AND s.attiva = 1
                     ORDER BY s.nome""",
                  (g.user['id'],)
              )
          strutture_list = g._strutture_list_cache
  ```

- [ ] **Step 2: Verifica sintassi**

  ```bash
  python -m py_compile app.py
  echo OK
  ```
  Atteso: `OK`

- [ ] **Step 3: Commit**

  ```bash
  git add app.py
  git commit -m "feat: inject_globals — strutture_list anche per tecnico"
  ```

---

## Task 4: Permessi nei blueprint

**Files:**
- Modify: `apparecchi.py`, `manutenzioni.py`, `verifiche.py`

- [ ] **Step 1: `apparecchi.py` — `_get_divisione_filter`**

  Trova:
  ```python
  elif g.user['ruolo'] == 'admin':
      struttura_id = getattr(g, 'struttura_id', None)
      if struttura_id:
          return "AND a.struttura_id = ?", [struttura_id]
      return "", []
  ```
  Cambia in:
  ```python
  elif g.user['ruolo'] in ('admin', 'tecnico'):
      struttura_id = getattr(g, 'struttura_id', None)
      if struttura_id:
          return "AND a.struttura_id = ?", [struttura_id]
      return "", []
  ```

- [ ] **Step 2: `apparecchi.py` — check accesso in `dettaglio()` e `modifica()`**

  Ci sono due occorrenze di:
  ```python
  if g.user['ruolo'] not in ('admin', 'superadmin'):
  ```
  Entrambe vanno cambiate in:
  ```python
  if g.user['ruolo'] not in ('admin', 'superadmin', 'tecnico'):
  ```

- [ ] **Step 3: `manutenzioni.py` — `_get_divisione_filter`**

  Stessa modifica: `elif g.user['ruolo'] == 'admin':` → `elif g.user['ruolo'] in ('admin', 'tecnico'):`

- [ ] **Step 4: `manutenzioni.py` — check accesso in `modifica()` e `elimina()`**

  Due occorrenze di:
  ```python
  if g.user['ruolo'] not in ('admin', 'superadmin'):
  ```
  Cambiare entrambe in:
  ```python
  if g.user['ruolo'] not in ('admin', 'superadmin', 'tecnico'):
  ```

- [ ] **Step 5: `verifiche.py` — `_get_divisione_filter`**

  Stessa modifica: `elif g.user['ruolo'] == 'admin':` → `elif g.user['ruolo'] in ('admin', 'tecnico'):`

- [ ] **Step 6: Verifica sintassi**

  ```bash
  python -m py_compile apparecchi.py manutenzioni.py verifiche.py
  echo OK
  ```
  Atteso: `OK`

- [ ] **Step 7: Commit**

  ```bash
  git add apparecchi.py manutenzioni.py verifiche.py
  git commit -m "feat: permessi — ruolo tecnico abilitato in apparecchi/manutenzioni/verifiche"
  ```

---

## Task 5: admin.py — gestione tecnici

**Files:**
- Modify: `admin.py`

- [ ] **Step 1: Aggiungi 4 route in `admin.py`**

  Aggiungi in fondo al file (prima dell'ultima sezione o alla fine), dopo la sezione log:

  ```python
  # ============================================================================
  # GESTIONE TECNICI (solo superadmin)
  # ============================================================================

  @admin_bp.route('/tecnici')
  @superadmin_required
  def tecnici():
      """Lista tecnici con strutture assegnate."""
      tecnici_list = query_all("""
          SELECT u.id, u.nome, u.cognome, u.email, u.attivo, u.ultimo_accesso,
                 GROUP_CONCAT(s.nome, ', ') as strutture_nomi,
                 COUNT(ts.struttura_id) as num_strutture
          FROM utenti u
          LEFT JOIN tecnici_strutture ts ON u.id = ts.tecnico_id
          LEFT JOIN strutture s ON ts.struttura_id = s.id
          WHERE u.ruolo = 'tecnico'
          GROUP BY u.id
          ORDER BY u.cognome, u.nome
      """)
      return render_template('admin/tecnici.html', tecnici=tecnici_list)


  @admin_bp.route('/tecnici/nuovo', methods=['GET', 'POST'])
  @superadmin_required
  def tecnico_nuovo():
      """Crea un nuovo tecnico."""
      strutture = query_all("SELECT id, nome FROM strutture WHERE attiva=1 ORDER BY nome")

      if request.method == 'GET':
          return render_template('admin/tecnico_form.html',
                                 tecnico=None, errors={},
                                 strutture=strutture, strutture_assegnate=[])

      errors = {}
      nome     = request.form.get('nome', '').strip()
      cognome  = request.form.get('cognome', '').strip()
      email    = request.form.get('email', '').strip().lower()
      password = request.form.get('password', '').strip()
      strutture_sel = request.form.getlist('strutture')

      if not nome:
          errors['nome'] = 'Il nome è obbligatorio.'
      if not cognome:
          errors['cognome'] = 'Il cognome è obbligatorio.'
      if not email:
          errors['email'] = "L'email è obbligatoria."
      elif query_one("SELECT id FROM utenti WHERE email = ?", (email,)):
          errors['email'] = 'Questo indirizzo email è già registrato.'
      if not password or len(password) < 8:
          errors['password'] = 'La password deve essere di almeno 8 caratteri.'

      if errors:
          return render_template('admin/tecnico_form.html',
                                 tecnico=None, errors=errors,
                                 strutture=strutture, strutture_assegnate=strutture_sel)

      password_hash = generate_password_hash(password)
      cursor = execute(
          """INSERT INTO utenti (email, password_hash, nome, cognome, ruolo, primo_accesso, struttura_id)
             VALUES (?, ?, ?, ?, 'tecnico', 1, NULL)""",
          (email, password_hash, nome, cognome)
      )
      tecnico_id = cursor.lastrowid

      for sid in strutture_sel:
          try:
              execute(
                  "INSERT INTO tecnici_strutture (tecnico_id, struttura_id) VALUES (?, ?)",
                  (tecnico_id, int(sid))
              )
          except Exception:
              pass

      log_attivita(g.user['id'], 'creazione', 'utenti', tecnico_id,
                   f"Tecnico creato: {nome} {cognome}", request.remote_addr)
      flash(f"Tecnico {nome} {cognome} creato con successo.", 'success')
      return redirect(url_for('admin.tecnici'))


  @admin_bp.route('/tecnici/<int:id>/modifica', methods=['GET', 'POST'])
  @superadmin_required
  def tecnico_modifica(id):
      """Modifica un tecnico e le strutture assegnate."""
      tecnico = query_one("SELECT * FROM utenti WHERE id = ? AND ruolo = 'tecnico'", (id,))
      if not tecnico:
          flash('Tecnico non trovato.', 'danger')
          return redirect(url_for('admin.tecnici'))

      strutture = query_all("SELECT id, nome FROM strutture WHERE attiva=1 ORDER BY nome")
      strutture_assegnate = [
          r['struttura_id'] for r in
          query_all("SELECT struttura_id FROM tecnici_strutture WHERE tecnico_id = ?", (id,))
      ]

      if request.method == 'GET':
          return render_template('admin/tecnico_form.html',
                                 tecnico=tecnico, errors={},
                                 strutture=strutture, strutture_assegnate=strutture_assegnate)

      errors = {}
      nome          = request.form.get('nome', '').strip()
      cognome       = request.form.get('cognome', '').strip()
      email         = request.form.get('email', '').strip().lower()
      strutture_sel = request.form.getlist('strutture')
      nuova_pw      = request.form.get('password', '').strip()

      if not nome:
          errors['nome'] = 'Il nome è obbligatorio.'
      if not cognome:
          errors['cognome'] = 'Il cognome è obbligatorio.'
      if not email:
          errors['email'] = "L'email è obbligatoria."
      elif query_one("SELECT id FROM utenti WHERE email = ? AND id != ?", (email, id)):
          errors['email'] = 'Email già usata da un altro utente.'
      if nuova_pw and len(nuova_pw) < 8:
          errors['password'] = 'La password deve essere di almeno 8 caratteri.'

      if errors:
          return render_template('admin/tecnico_form.html',
                                 tecnico=tecnico, errors=errors,
                                 strutture=strutture, strutture_assegnate=strutture_sel)

      if nuova_pw:
          execute(
              """UPDATE utenti SET nome=?, cognome=?, email=?, password_hash=?,
                        updated_at=datetime('now') WHERE id=?""",
              (nome, cognome, email, generate_password_hash(nuova_pw), id)
          )
      else:
          execute(
              "UPDATE utenti SET nome=?, cognome=?, email=?, updated_at=datetime('now') WHERE id=?",
              (nome, cognome, email, id)
          )

      execute("DELETE FROM tecnici_strutture WHERE tecnico_id = ?", (id,))
      for sid in strutture_sel:
          try:
              execute(
                  "INSERT INTO tecnici_strutture (tecnico_id, struttura_id) VALUES (?, ?)",
                  (id, int(sid))
              )
          except Exception:
              pass

      log_attivita(g.user['id'], 'modifica', 'utenti', id,
                   f"Tecnico modificato: {nome} {cognome}", request.remote_addr)
      flash(f"Tecnico {nome} {cognome} aggiornato.", 'success')
      return redirect(url_for('admin.tecnici'))


  @admin_bp.route('/tecnici/<int:id>/elimina', methods=['POST'])
  @superadmin_required
  def tecnico_elimina(id):
      """Elimina un tecnico (e le sue assegnazioni strutture per CASCADE)."""
      tecnico = query_one("SELECT * FROM utenti WHERE id = ? AND ruolo = 'tecnico'", (id,))
      if not tecnico:
          flash('Tecnico non trovato.', 'danger')
          return redirect(url_for('admin.tecnici'))

      execute("DELETE FROM utenti WHERE id = ?", (id,))
      log_attivita(g.user['id'], 'eliminazione', 'utenti', id,
                   f"Tecnico eliminato: {tecnico['nome']} {tecnico['cognome']}",
                   request.remote_addr)
      flash(f"Tecnico {tecnico['nome']} {tecnico['cognome']} eliminato.", 'success')
      return redirect(url_for('admin.tecnici'))
  ```

- [ ] **Step 2: Verifica sintassi**

  ```bash
  python -m py_compile admin.py
  echo OK
  ```
  Atteso: `OK`

- [ ] **Step 3: Commit**

  ```bash
  git add admin.py
  git commit -m "feat: admin — CRUD gestione tecnici (superadmin only)"
  ```

---

## Task 6: Template

**Files:**
- Modify: `templates/base.html`
- Create: `templates/partials/struttura_switcher_tecnico.html`
- Create: `templates/auth/seleziona_struttura_tecnico.html`
- Create: `templates/admin/tecnici.html`
- Create: `templates/admin/tecnico_form.html`

- [ ] **Step 1: Crea `templates/partials/struttura_switcher_tecnico.html`**

  ```html
  <li class="nav-item dropdown">
    <a class="nav-link dropdown-toggle" href="#" role="button" data-bs-toggle="dropdown">
      <i class="bi bi-building me-1"></i>
      {% if g_struttura %}{{ g_struttura.nome }}{% else %}Seleziona struttura{% endif %}
    </a>
    <ul class="dropdown-menu dropdown-menu-end">
      {% for s in strutture_list %}
      <li>
        <a class="dropdown-item {% if g_struttura and g_struttura.id == s.id %}active{% endif %}"
           href="{{ url_for('auth.tecnico_seleziona_struttura', struttura_id=s.id) }}">
          <i class="bi bi-building me-2"></i>{{ s.nome }}
        </a>
      </li>
      {% endfor %}
    </ul>
  </li>
  ```

- [ ] **Step 2: Aggiorna `templates/base.html` — struttura switcher**

  Trova:
  ```html
  {% if current_user and current_user.ruolo == 'superadmin' and not single_struttura %}
    {% include 'partials/struttura_switcher.html' %}
  {% endif %}
  ```
  Cambia in:
  ```html
  {% if current_user and not single_struttura %}
    {% if current_user.ruolo == 'superadmin' %}
      {% include 'partials/struttura_switcher.html' %}
    {% elif current_user.ruolo == 'tecnico' and strutture_list|length > 1 %}
      {% include 'partials/struttura_switcher_tecnico.html' %}
    {% endif %}
  {% endif %}
  ```

- [ ] **Step 3: Aggiorna `templates/base.html` — "Tutte le divisioni" per tecnico**

  Trova (nel dropdown divisioni):
  ```html
  {% if current_user is defined and current_user.ruolo == 'admin' %}
  ```
  Cambia in:
  ```html
  {% if current_user is defined and current_user.ruolo in ('admin', 'tecnico') %}
  ```

- [ ] **Step 4: Aggiorna `templates/base.html` — link Tecnici in sidebar**

  Nella sezione amministrazione della sidebar, dopo il link "Strutture" e prima di "Utenti", aggiungi:
  ```html
  {% if current_user.ruolo == 'superadmin' and not single_struttura %}
  <li class="nav-item">
      <a class="nav-link {% if request.endpoint and 'admin.tecnic' in request.endpoint|default('') %}active{% endif %}"
         href="{{ url_for('admin.tecnici') }}">
          <i class="bi bi-person-gear"></i> Tecnici
      </a>
  </li>
  {% endif %}
  ```

- [ ] **Step 5: Crea `templates/auth/seleziona_struttura_tecnico.html`**

  ```html
  <!DOCTYPE html>
  <html lang="it">
  <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Seleziona Struttura — MedInventory</title>
      <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
      <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
  </head>
  <body class="bg-light d-flex align-items-center min-vh-100">
  <div class="container" style="max-width: 480px;">
      <div class="card shadow-sm">
          <div class="card-body p-4">
              <div class="text-center mb-4">
                  <i class="bi bi-building fs-1 text-primary"></i>
                  <h4 class="mt-2">Seleziona Struttura</h4>
                  <p class="text-muted small">Scegli la struttura su cui vuoi lavorare.</p>
              </div>
              {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}
                  {% for category, message in messages %}
                  <div class="alert alert-{{ category }}">{{ message }}</div>
                  {% endfor %}
              {% endif %}
              {% endwith %}
              <div class="list-group">
                  {% for s in strutture %}
                  <a href="{{ url_for('auth.tecnico_seleziona_struttura', struttura_id=s.id) }}"
                     class="list-group-item list-group-item-action d-flex align-items-center gap-3 py-3">
                      <i class="bi bi-building text-primary fs-5"></i>
                      <span class="fw-medium">{{ s.nome }}</span>
                      <i class="bi bi-chevron-right ms-auto text-muted"></i>
                  </a>
                  {% endfor %}
              </div>
              <div class="text-center mt-3">
                  <a href="{{ url_for('auth.logout') }}" class="text-muted small">
                      <i class="bi bi-box-arrow-right me-1"></i>Esci
                  </a>
              </div>
          </div>
      </div>
  </div>
  </body>
  </html>
  ```

- [ ] **Step 6: Crea `templates/admin/tecnici.html`**

  ```html
  {% extends "base.html" %}
  {% block title %}Tecnici — {{ app_name }}{% endblock %}
  {% block content %}
  <div class="page-header d-flex justify-content-between align-items-center mb-4">
      <div>
          <h1 class="h3 mb-0">Tecnici</h1>
          <p class="text-muted mb-0">Utenti tecnici con accesso a più strutture</p>
      </div>
      <a href="{{ url_for('admin.tecnico_nuovo') }}" class="btn btn-primary">
          <i class="bi bi-person-plus me-1"></i> Nuovo Tecnico
      </a>
  </div>

  {% with messages = get_flashed_messages(with_categories=true) %}
  {% if messages %}{% for cat, msg in messages %}
  <div class="alert alert-{{ cat }} alert-dismissible fade show">
      {{ msg }}<button type="button" class="btn-close" data-bs-dismiss="alert"></button>
  </div>
  {% endfor %}{% endif %}{% endwith %}

  <div class="card">
      <div class="card-body p-0">
          <table class="table table-hover mb-0">
              <thead class="table-light">
                  <tr>
                      <th>Nome</th>
                      <th>Email</th>
                      <th>Strutture assegnate</th>
                      <th>Stato</th>
                      <th>Ultimo accesso</th>
                      <th class="text-end">Azioni</th>
                  </tr>
              </thead>
              <tbody>
                  {% for t in tecnici %}
                  <tr>
                      <td class="fw-medium">{{ t.cognome }} {{ t.nome }}</td>
                      <td class="text-muted">{{ t.email }}</td>
                      <td>
                          {% if t.strutture_nomi %}
                              {% for nome in t.strutture_nomi.split(', ') %}
                              <span class="badge bg-secondary me-1">{{ nome }}</span>
                              {% endfor %}
                          {% else %}
                              <span class="text-muted small">Nessuna struttura</span>
                          {% endif %}
                      </td>
                      <td>
                          {% if t.attivo %}
                          <span class="badge bg-success">Attivo</span>
                          {% else %}
                          <span class="badge bg-secondary">Disattivo</span>
                          {% endif %}
                      </td>
                      <td class="text-muted small">
                          {{ t.ultimo_accesso or '—' }}
                      </td>
                      <td class="text-end">
                          <a href="{{ url_for('admin.tecnico_modifica', id=t.id) }}"
                             class="btn btn-sm btn-outline-primary me-1">
                              <i class="bi bi-pencil"></i>
                          </a>
                          <form method="post"
                                action="{{ url_for('admin.tecnico_elimina', id=t.id) }}"
                                class="d-inline"
                                onsubmit="return confirm('Eliminare il tecnico {{ t.nome }} {{ t.cognome }}?')">
                              <button type="submit" class="btn btn-sm btn-outline-danger">
                                  <i class="bi bi-trash"></i>
                              </button>
                          </form>
                      </td>
                  </tr>
                  {% else %}
                  <tr>
                      <td colspan="6" class="text-center text-muted py-4">
                          Nessun tecnico presente. <a href="{{ url_for('admin.tecnico_nuovo') }}">Crea il primo</a>.
                      </td>
                  </tr>
                  {% endfor %}
              </tbody>
          </table>
      </div>
  </div>
  {% endblock %}
  ```

- [ ] **Step 7: Crea `templates/admin/tecnico_form.html`**

  ```html
  {% extends "base.html" %}
  {% block title %}{% if tecnico %}Modifica Tecnico{% else %}Nuovo Tecnico{% endif %} — {{ app_name }}{% endblock %}
  {% block content %}
  <div class="page-header d-flex justify-content-between align-items-center mb-4">
      <div>
          <h1 class="h3 mb-0">{% if tecnico %}Modifica Tecnico{% else %}Nuovo Tecnico{% endif %}</h1>
      </div>
      <a href="{{ url_for('admin.tecnici') }}" class="btn btn-outline-secondary">
          <i class="bi bi-arrow-left me-1"></i> Torna alla lista
      </a>
  </div>

  <div class="row">
      <div class="col-lg-8">
          <form method="post">
              <div class="card mb-4">
                  <div class="card-header fw-semibold">Dati anagrafici</div>
                  <div class="card-body">
                      <div class="row g-3">
                          <div class="col-md-6">
                              <label class="form-label">Nome <span class="text-danger">*</span></label>
                              <input type="text" name="nome" class="form-control {% if errors.nome %}is-invalid{% endif %}"
                                     value="{{ request.form.get('nome', tecnico.nome if tecnico else '') }}" required>
                              {% if errors.nome %}<div class="invalid-feedback">{{ errors.nome }}</div>{% endif %}
                          </div>
                          <div class="col-md-6">
                              <label class="form-label">Cognome <span class="text-danger">*</span></label>
                              <input type="text" name="cognome" class="form-control {% if errors.cognome %}is-invalid{% endif %}"
                                     value="{{ request.form.get('cognome', tecnico.cognome if tecnico else '') }}" required>
                              {% if errors.cognome %}<div class="invalid-feedback">{{ errors.cognome }}</div>{% endif %}
                          </div>
                          <div class="col-md-8">
                              <label class="form-label">Email <span class="text-danger">*</span></label>
                              <input type="email" name="email" class="form-control {% if errors.email %}is-invalid{% endif %}"
                                     value="{{ request.form.get('email', tecnico.email if tecnico else '') }}" required>
                              {% if errors.email %}<div class="invalid-feedback">{{ errors.email }}</div>{% endif %}
                          </div>
                          <div class="col-md-8">
                              <label class="form-label">
                                  Password {% if not tecnico %}<span class="text-danger">*</span>{% else %}<span class="text-muted small">(lascia vuoto per non cambiare)</span>{% endif %}
                              </label>
                              <input type="password" name="password"
                                     class="form-control {% if errors.password %}is-invalid{% endif %}"
                                     {% if not tecnico %}required{% endif %} minlength="8"
                                     placeholder="Minimo 8 caratteri">
                              {% if errors.password %}<div class="invalid-feedback">{{ errors.password }}</div>{% endif %}
                          </div>
                      </div>
                  </div>
              </div>

              <div class="card mb-4">
                  <div class="card-header fw-semibold">Strutture assegnate</div>
                  <div class="card-body">
                      {% if strutture %}
                      <div class="row g-2">
                          {% for s in strutture %}
                          <div class="col-md-6">
                              <div class="form-check">
                                  <input class="form-check-input" type="checkbox"
                                         name="strutture" value="{{ s.id }}" id="s{{ s.id }}"
                                         {% if s.id in strutture_assegnate or s.id|string in strutture_assegnate %}checked{% endif %}>
                                  <label class="form-check-label" for="s{{ s.id }}">
                                      {{ s.nome }}
                                  </label>
                              </div>
                          </div>
                          {% endfor %}
                      </div>
                      {% else %}
                      <p class="text-muted mb-0">Nessuna struttura attiva disponibile.</p>
                      {% endif %}
                  </div>
              </div>

              <div class="d-flex gap-2">
                  <button type="submit" class="btn btn-primary">
                      <i class="bi bi-check-lg me-1"></i>
                      {% if tecnico %}Salva modifiche{% else %}Crea Tecnico{% endif %}
                  </button>
                  <a href="{{ url_for('admin.tecnici') }}" class="btn btn-outline-secondary">Annulla</a>
              </div>
          </form>
      </div>
  </div>
  {% endblock %}
  ```

- [ ] **Step 8: Commit**

  ```bash
  git add templates/base.html \
          templates/partials/struttura_switcher_tecnico.html \
          templates/auth/seleziona_struttura_tecnico.html \
          templates/admin/tecnici.html \
          templates/admin/tecnico_form.html
  git commit -m "feat: template — gestione tecnici e selezione struttura"
  ```

---

## Task 7: Verifica manuale e push finale

- [ ] **Step 1: Esegui la migrazione sul DB di sviluppo**

  ```bash
  python migrate_v2_2.py
  ```
  Atteso: `Migrazione v2.2 completata con successo.`

- [ ] **Step 2: Avvia l'app e verifica login superadmin**

  ```bash
  python app.py
  ```
  - Accedi come superadmin → deve funzionare normalmente
  - Vai in Amministrazione → Tecnici → deve comparire la pagina lista vuota
  - Crea un tecnico con 1 struttura, poi con 2 strutture
  - Verifica che il tecnico con 1 struttura entri direttamente nel dashboard
  - Verifica che il tecnico con 2 strutture veda la pagina di selezione struttura
  - Verifica che il tecnico possa creare, modificare, eliminare apparecchi e manutenzioni
  - Verifica che il tecnico NON veda la sezione Amministrazione nella sidebar
  - Verifica che il tecnico con 2 strutture veda il switcher struttura in navbar

- [ ] **Step 3: Push finale**

  ```bash
  git push origin v2-multi-struttura
  ```
