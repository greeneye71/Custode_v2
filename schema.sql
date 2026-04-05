PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

-- ============================================
-- STRUTTURE
-- ============================================
CREATE TABLE IF NOT EXISTS strutture (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  nome                TEXT NOT NULL,
  codice              TEXT UNIQUE NOT NULL,
  descrizione         TEXT,
  tipo                TEXT DEFAULT 'altro'
                      CHECK(tipo IN ('ospedale','clinica_privata','rsa','ambulatorio',
                                     'poliambulatorio','laboratorio','altro')),
  indirizzo           TEXT,
  telefono            TEXT,
  email_notifiche     TEXT,
  pec                 TEXT,
  responsabile        TEXT,
  email_responsabile  TEXT,
  codice_fiscale      TEXT,
  partita_iva         TEXT,
  data_attivazione    DATE,
  scadenza_contratto  DATE,
  note                TEXT,
  modalita            TEXT NOT NULL DEFAULT 'standard'
                      CHECK(modalita IN ('standard', 'avanzata')),
  attiva              INTEGER DEFAULT 1,
  created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_strutture_codice ON strutture(codice);
CREATE INDEX IF NOT EXISTS idx_strutture_attiva ON strutture(attiva);

-- ============================================
-- STRUTTURE_CONFIG (configurazione per-struttura)
-- ============================================
CREATE TABLE IF NOT EXISTS strutture_config (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  struttura_id INTEGER NOT NULL,
  chiave       TEXT NOT NULL,
  valore       TEXT,
  UNIQUE(struttura_id, chiave),
  FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_strutture_config_struttura ON strutture_config(struttura_id);

-- Chiavi valide: ai_provider, anthropic_api_key, ai_import_model,
-- ai_email_model, ai_local_base_url, ai_local_model,
-- smtp_host, smtp_port, smtp_user, smtp_password_encrypted, smtp_from,
-- smtp_use_tls, report_frequenza, report_schedulato_attivo

-- ============================================
-- API_TOKENS
-- ============================================
CREATE TABLE IF NOT EXISTS api_tokens (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  struttura_id    INTEGER NOT NULL,
  nome            TEXT NOT NULL,
  token_hash      TEXT UNIQUE NOT NULL,
  scopes          TEXT DEFAULT 'read',
  ultimo_utilizzo DATETIME,
  scadenza        DATE,
  attivo          INTEGER DEFAULT 1,
  -- FK verso utenti (definita più avanti nel file - SQLite valida FK solo a runtime DML)
  created_by      INTEGER,
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE,
  FOREIGN KEY (created_by) REFERENCES utenti(id)
);

CREATE INDEX IF NOT EXISTS idx_api_tokens_struttura ON api_tokens(struttura_id);
CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON api_tokens(token_hash);

-- ============================================
-- LOGIN_ATTEMPTS (rate limiting)
-- ============================================
CREATE TABLE IF NOT EXISTS login_attempts (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ip_address TEXT NOT NULL,
  email      TEXT,
  esito      TEXT NOT NULL CHECK(esito IN ('fallito', 'bloccato', 'riuscito')),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_login_attempts_ip    ON login_attempts(ip_address, created_at);
CREATE INDEX IF NOT EXISTS idx_login_attempts_email ON login_attempts(email, created_at);

-- ============================================
-- DIVISIONI
-- ============================================
CREATE TABLE IF NOT EXISTS divisioni (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nome TEXT NOT NULL,
  codice TEXT NOT NULL,
  colore TEXT DEFAULT '#0ea5e9',
  descrizione TEXT,
  attiva INTEGER DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  struttura_id INTEGER NOT NULL,
  UNIQUE(struttura_id, nome),
  UNIQUE(struttura_id, codice),
  FOREIGN KEY (struttura_id) REFERENCES strutture(id)
);

CREATE INDEX IF NOT EXISTS idx_divisioni_codice ON divisioni(codice);
CREATE INDEX IF NOT EXISTS idx_divisioni_attiva ON divisioni(attiva);

-- ============================================
-- UTENTI
-- ============================================
CREATE TABLE IF NOT EXISTS utenti (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  nome TEXT NOT NULL,
  cognome TEXT NOT NULL,
  ruolo TEXT NOT NULL CHECK(ruolo IN ('superadmin', 'admin', 'utente')),
  divisione_default_id INTEGER,
  attivo INTEGER DEFAULT 1,
  primo_accesso INTEGER DEFAULT 1,
  ultimo_accesso DATETIME,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  struttura_id INTEGER,   -- NULL per superadmin
  FOREIGN KEY (struttura_id) REFERENCES strutture(id),
  FOREIGN KEY (divisione_default_id) REFERENCES divisioni(id)
);

CREATE INDEX IF NOT EXISTS idx_utenti_email ON utenti(email);
CREATE INDEX IF NOT EXISTS idx_utenti_ruolo ON utenti(ruolo);
CREATE INDEX IF NOT EXISTS idx_utenti_attivo ON utenti(attivo);

-- ============================================
-- UTENTI_DIVISIONI (associazione N:M)
-- ============================================
CREATE TABLE IF NOT EXISTS utenti_divisioni (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  utente_id INTEGER NOT NULL,
  divisione_id INTEGER NOT NULL,
  ruolo_divisione TEXT NOT NULL CHECK(ruolo_divisione IN ('admin', 'utente')),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (utente_id) REFERENCES utenti(id) ON DELETE CASCADE,
  FOREIGN KEY (divisione_id) REFERENCES divisioni(id) ON DELETE CASCADE,
  UNIQUE(utente_id, divisione_id)
);

CREATE INDEX IF NOT EXISTS idx_utenti_divisioni_utente ON utenti_divisioni(utente_id);
CREATE INDEX IF NOT EXISTS idx_utenti_divisioni_divisione ON utenti_divisioni(divisione_id);

-- ============================================
-- SESSIONI
-- ============================================
CREATE TABLE IF NOT EXISTS sessioni (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  utente_id INTEGER NOT NULL,
  token TEXT UNIQUE NOT NULL,
  expires_at DATETIME NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (utente_id) REFERENCES utenti(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessioni_token ON sessioni(token);
CREATE INDEX IF NOT EXISTS idx_sessioni_utente ON sessioni(utente_id);
CREATE INDEX IF NOT EXISTS idx_sessioni_expires ON sessioni(expires_at);

-- ============================================
-- APPARECCHI
-- ============================================
CREATE TABLE IF NOT EXISTS apparecchi (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  divisione_id INTEGER NOT NULL,
  descrizione TEXT,
  matricola TEXT NOT NULL,
  numero_inventario TEXT,
  marca TEXT NOT NULL,
  modello TEXT NOT NULL,
  anno_fabbricazione INTEGER,
  classificazione TEXT CHECK(classificazione IN ('I', 'IIa', 'IIb', 'III')),
  ubicazione TEXT,
  stato TEXT DEFAULT 'funzionante' CHECK(stato IN ('funzionante', 'in_manutenzione', 'dismesso', 'da_sostituire')),
  connesso_rete INTEGER DEFAULT 0,
  ip_address TEXT,
  mac_address TEXT,
  hostname TEXT,
  porta INTEGER,
  protocollo TEXT,
  url_interfaccia TEXT,
  fornitore TEXT,
  codice_fornitore TEXT,
  garanzia_scadenza DATE,
  contratto_manutenzione TEXT,
  note TEXT,
  foto_path TEXT,
  created_by INTEGER,
  updated_by INTEGER,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  struttura_id INTEGER NOT NULL,
  UNIQUE(struttura_id, modello, matricola),
  FOREIGN KEY (struttura_id) REFERENCES strutture(id),
  FOREIGN KEY (divisione_id) REFERENCES divisioni(id),
  FOREIGN KEY (created_by) REFERENCES utenti(id),
  FOREIGN KEY (updated_by) REFERENCES utenti(id)
);

CREATE INDEX IF NOT EXISTS idx_apparecchi_divisione ON apparecchi(divisione_id);
CREATE INDEX IF NOT EXISTS idx_apparecchi_descrizione ON apparecchi(descrizione);
CREATE INDEX IF NOT EXISTS idx_apparecchi_matricola ON apparecchi(matricola);
CREATE INDEX IF NOT EXISTS idx_apparecchi_stato ON apparecchi(stato);
CREATE INDEX IF NOT EXISTS idx_apparecchi_marca ON apparecchi(marca);
CREATE INDEX IF NOT EXISTS idx_apparecchi_ubicazione ON apparecchi(ubicazione);
CREATE INDEX IF NOT EXISTS idx_apparecchi_ip ON apparecchi(ip_address);
CREATE INDEX IF NOT EXISTS idx_apparecchi_numero_inventario ON apparecchi(numero_inventario);

-- ============================================
-- MANUTENZIONI
-- ============================================
CREATE TABLE IF NOT EXISTS manutenzioni (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  apparecchio_id INTEGER NOT NULL,
  tipo TEXT NOT NULL CHECK(tipo IN ('preventiva', 'correttiva', 'verifica', 'calibrazione')),
  data_intervento DATE NOT NULL,
  prossima_scadenza DATE,
  periodicita_giorni INTEGER,
  tecnico_ditta TEXT,
  descrizione TEXT,
  esito TEXT,
  costo DECIMAL(10,2),
  verbale_path TEXT,
  created_by INTEGER,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (apparecchio_id) REFERENCES apparecchi(id) ON DELETE CASCADE,
  FOREIGN KEY (created_by) REFERENCES utenti(id)
);

CREATE INDEX IF NOT EXISTS idx_manutenzioni_apparecchio ON manutenzioni(apparecchio_id);
CREATE INDEX IF NOT EXISTS idx_manutenzioni_tipo ON manutenzioni(tipo);
CREATE INDEX IF NOT EXISTS idx_manutenzioni_scadenza ON manutenzioni(prossima_scadenza);
CREATE INDEX IF NOT EXISTS idx_manutenzioni_data ON manutenzioni(data_intervento);

-- ============================================
-- DOCUMENTI
-- ============================================
CREATE TABLE IF NOT EXISTS documenti (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  apparecchio_id INTEGER NOT NULL,
  tipo TEXT NOT NULL CHECK(tipo IN ('manuale', 'certificato', 'foto', 'report')),
  filename TEXT NOT NULL,
  filepath TEXT NOT NULL,
  filesize INTEGER,
  uploaded_by INTEGER,
  uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (apparecchio_id) REFERENCES apparecchi(id) ON DELETE CASCADE,
  FOREIGN KEY (uploaded_by) REFERENCES utenti(id)
);

CREATE INDEX IF NOT EXISTS idx_documenti_apparecchio ON documenti(apparecchio_id);
CREATE INDEX IF NOT EXISTS idx_documenti_tipo ON documenti(tipo);

-- ============================================
-- ACCESSORI
-- ============================================
CREATE TABLE IF NOT EXISTS accessori (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  apparecchio_id INTEGER NOT NULL,
  descrizione TEXT NOT NULL,
  produttore TEXT,
  modello TEXT,
  matricola TEXT,
  created_by INTEGER,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (apparecchio_id) REFERENCES apparecchi(id) ON DELETE CASCADE,
  FOREIGN KEY (created_by) REFERENCES utenti(id)
);

CREATE INDEX IF NOT EXISTS idx_accessori_apparecchio ON accessori(apparecchio_id);

-- ============================================
-- IMPORT_HISTORY
-- ============================================
CREATE TABLE IF NOT EXISTS verifiche (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  apparecchio_id INTEGER NOT NULL,
  data_verifica DATE NOT NULL,
  prossima_scadenza DATE,
  periodicita_giorni INTEGER DEFAULT 365,
  esito TEXT NOT NULL CHECK(esito IN ('positivo', 'negativo', 'con_riserva')),
  tecnico_ditta TEXT,
  note TEXT,
  documento_path TEXT,
  created_by INTEGER,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (apparecchio_id) REFERENCES apparecchi(id) ON DELETE CASCADE,
  FOREIGN KEY (created_by) REFERENCES utenti(id)
);

CREATE INDEX IF NOT EXISTS idx_verifiche_apparecchio ON verifiche(apparecchio_id);
CREATE INDEX IF NOT EXISTS idx_verifiche_scadenza ON verifiche(prossima_scadenza);
CREATE INDEX IF NOT EXISTS idx_verifiche_data ON verifiche(data_verifica);

CREATE TABLE IF NOT EXISTS import_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tipo_import TEXT NOT NULL CHECK(tipo_import IN ('inventario', 'verbale_email', 'verbale_manutenzione', 'verifica_elettrica')),
  filename TEXT NOT NULL,
  filepath TEXT NOT NULL,
  tipo_documento TEXT,
  divisione_id INTEGER,
  email_from TEXT,
  email_subject TEXT,
  totale_righe INTEGER,
  righe_importate INTEGER,
  righe_errori INTEGER,
  stato TEXT CHECK(stato IN ('pending', 'processing', 'completed', 'failed')),
  ai_prompt TEXT,
  ai_response TEXT,
  errori_dettaglio TEXT,
  imported_by INTEGER,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  completed_at DATETIME,
  FOREIGN KEY (divisione_id) REFERENCES divisioni(id),
  FOREIGN KEY (imported_by) REFERENCES utenti(id)
);

CREATE INDEX IF NOT EXISTS idx_import_history_tipo ON import_history(tipo_import);
CREATE INDEX IF NOT EXISTS idx_import_history_stato ON import_history(stato);
CREATE INDEX IF NOT EXISTS idx_import_history_created ON import_history(created_at);

-- ============================================
-- IMPORT_PREVIEW
-- ============================================
CREATE TABLE IF NOT EXISTS import_preview (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  import_id INTEGER NOT NULL,
  riga_numero INTEGER,
  dati_estratti TEXT,
  apparecchio_match_id INTEGER,
  match_confidence DECIMAL(3,2),
  stato TEXT CHECK(stato IN ('pending', 'approved', 'rejected', 'imported')),
  note_revisione TEXT,
  FOREIGN KEY (import_id) REFERENCES import_history(id) ON DELETE CASCADE,
  FOREIGN KEY (apparecchio_match_id) REFERENCES apparecchi(id)
);

CREATE INDEX IF NOT EXISTS idx_import_preview_import ON import_preview(import_id);
CREATE INDEX IF NOT EXISTS idx_import_preview_stato ON import_preview(stato);

-- ============================================
-- EMAIL_CONFIG
-- ============================================
CREATE TABLE IF NOT EXISTS email_config (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  divisione_id INTEGER,
  email_account TEXT NOT NULL,
  email_password_encrypted TEXT NOT NULL,
  imap_server TEXT NOT NULL,
  imap_port INTEGER DEFAULT 993,
  check_interval_minutes INTEGER DEFAULT 15,
  attivo INTEGER DEFAULT 1,
  ultima_verifica DATETIME,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (divisione_id) REFERENCES divisioni(id)
);

CREATE INDEX IF NOT EXISTS idx_email_config_divisione ON email_config(divisione_id);
CREATE INDEX IF NOT EXISTS idx_email_config_attivo ON email_config(attivo);

-- ============================================
-- LOG_ATTIVITA
-- ============================================
CREATE TABLE IF NOT EXISTS log_attivita (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  utente_id INTEGER,
  azione TEXT NOT NULL,
  entita TEXT NOT NULL,
  entita_id INTEGER,
  dettagli TEXT,
  ip_address TEXT,
  struttura_id INTEGER,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (struttura_id) REFERENCES strutture(id),
  FOREIGN KEY (utente_id) REFERENCES utenti(id)
);

CREATE INDEX IF NOT EXISTS idx_log_utente ON log_attivita(utente_id);
CREATE INDEX IF NOT EXISTS idx_log_entita ON log_attivita(entita, entita_id);
CREATE INDEX IF NOT EXISTS idx_log_data ON log_attivita(created_at);
CREATE INDEX IF NOT EXISTS idx_log_struttura ON log_attivita(struttura_id);
CREATE INDEX IF NOT EXISTS idx_apparecchi_struttura ON apparecchi(struttura_id);

-- ============================================
-- VISTA: prossime_scadenze (manutenzioni + verifiche)
-- ============================================
CREATE VIEW IF NOT EXISTS prossime_scadenze AS
SELECT
  a.id AS apparecchio_id,
  a.divisione_id,
  a.descrizione,
  a.marca,
  a.modello,
  a.matricola,
  a.ubicazione,
  m.id AS manutenzione_id,
  m.id AS record_id,
  'manutenzione' AS tipo_record,
  m.tipo AS tipo_manutenzione,
  m.prossima_scadenza,
  CAST((julianday(m.prossima_scadenza) - julianday('now')) AS INTEGER) AS giorni_rimasti,
  CASE
    WHEN julianday(m.prossima_scadenza) - julianday('now') < 0  THEN 'scaduto'
    WHEN julianday(m.prossima_scadenza) - julianday('now') <= 7  THEN 'urgente'
    WHEN julianday(m.prossima_scadenza) - julianday('now') <= 15 THEN 'attenzione'
    WHEN julianday(m.prossima_scadenza) - julianday('now') <= 30 THEN 'avviso'
    ELSE 'ok'
  END AS priorita
FROM apparecchi a
INNER JOIN manutenzioni m ON a.id = m.apparecchio_id
WHERE a.stato != 'dismesso'
  AND m.prossima_scadenza IS NOT NULL
  AND m.id = (
    SELECT m2.id FROM manutenzioni m2
    WHERE m2.apparecchio_id = m.apparecchio_id
      AND m2.tipo = m.tipo
    ORDER BY m2.data_intervento DESC, m2.id DESC
    LIMIT 1
  )

UNION ALL

SELECT
  a.id AS apparecchio_id,
  a.divisione_id,
  a.descrizione,
  a.marca,
  a.modello,
  a.matricola,
  a.ubicazione,
  NULL AS manutenzione_id,
  v.id AS record_id,
  'verifica' AS tipo_record,
  'verifica_elettrica' AS tipo_manutenzione,
  v.prossima_scadenza,
  CAST((julianday(v.prossima_scadenza) - julianday('now')) AS INTEGER) AS giorni_rimasti,
  CASE
    WHEN julianday(v.prossima_scadenza) - julianday('now') < 0  THEN 'scaduto'
    WHEN julianday(v.prossima_scadenza) - julianday('now') <= 7  THEN 'urgente'
    WHEN julianday(v.prossima_scadenza) - julianday('now') <= 15 THEN 'attenzione'
    WHEN julianday(v.prossima_scadenza) - julianday('now') <= 30 THEN 'avviso'
    ELSE 'ok'
  END AS priorita
FROM apparecchi a
INNER JOIN verifiche v ON a.id = v.apparecchio_id
WHERE a.stato != 'dismesso'
  AND v.prossima_scadenza IS NOT NULL
  AND v.id = (
    SELECT v2.id FROM verifiche v2
    WHERE v2.apparecchio_id = v.apparecchio_id
    ORDER BY v2.data_verifica DESC, v2.id DESC
    LIMIT 1
  )

ORDER BY prossima_scadenza ASC;

-- ============================================
-- VERSIONE SCHEMA
-- ============================================
PRAGMA user_version = 200;
