# AGENTS.md

This file provides guidance to coding agents working in this repository.

## Project Overview

**MedInventory v2.6.3** (Custode_v2) — Italian-language web application for managing medical devices (*apparecchi elettromedicali*) in healthcare facilities. Multi-tenant: one deployment hosts several *strutture* (facilities), each with its own divisions, users, data and AI configuration. Built for Windows LAN deployment by Studio Bergamaschi.

**Stack:** Flask 3.x + SQLite3 + HTMX + Bootstrap 5 + AI (Anthropic Claude / Google Gemini / OpenAI / Ollama / LM Studio)

## Running the Application

```bash
# Development (auto-reload, debug mode)
python app.py

# Production (Waitress WSGI, 4 threads, rotating logs)
python run_production.py

# First-time setup: initialize database schema + default admin user
python seed.py
# Default credentials: admin@medinventory.local / admin123

# Multi-tenant deployments also need a superadmin (seed.py only creates an admin)
python crea_superadmin.py

# Switch between single- and multi-struttura mode
python toggle_modalita.py --status

# Unified maintenance tool: status report, diagnostics, repairs
python manutenzione.py                  # status + diagnostics + interactive menu
python manutenzione.py diagnosi         # checks only; exit 1 on errors
python manutenzione.py utenti elenca    # users, with the state of each password hash
python manutenzione.py utenti azzera --nuovo-admin admin@example.it
python manutenzione.py --db OTHER/data/database.sqlite stato

# Import another installation's data as a new struttura (see below)
python importa_installazione.py <source-install-dir> --dry-run
```

No build step needed. No test suite exists.

## Importing another installation

`importa_installazione.py` absorbs a separate MedInventory installation — typically
a single-facility one already in service — into this deployment as a new struttura,
attachments included. The source is never modified: the tool reads a consistent
snapshot taken with `sqlite3.backup()`, so it works even against a running install.

The source may be on an older schema. Columns are resolved by introspection plus a
map of known renames (`RINOMINI`), missing columns get defaults, unknown ones are
reported and ignored; values outside the target's CHECK constraints are normalized
and listed. Add new renames to `RINOMINI` rather than writing per-version readers.

Behaviour worth knowing before changing it:
- `--dry-run` reports everything and writes nothing. The target DB is backed up
  automatically before any write, and the whole import is one transaction — files
  copied during a failed run are removed on rollback.
- Re-running is safe: each entity has a natural key (`apparecchi` by
  struttura+modello+matricola, `manutenzioni` by apparecchio+tipo+data, …) and a
  second run with `--in-struttura` skips everything. Creating a struttura whose
  name already exists is refused, with instructions.
- `utenti.email` is globally UNIQUE, so an already-present user is skipped and its
  references are remapped onto the existing account. A source `superadmin` is
  imported as `admin` of the new struttura — it must not gain global powers here.
- An `apparecchi.stato` the target does not know (e.g. `rottamato` from a customized
  install) falls back to the schema default, `funzionante` — an optimistic conversion
  that would silently put a retired device back among the active ones, deadlines
  included. The tool warns about it twice, before and after the import, and lists the
  affected devices by matricola; change the fallback in `VINCOLI` if that is wrong for
  a given migration.
- Not imported: `sessioni`, `login_attempts`, `api_tokens`, `email_config`,
  `import_preview`. They belong to the source deployment or are transient.

## Configuration

Config is split in two files, merged at startup (`load_config()` in `app.py`, local wins):

- `config.json` — system defaults only (`version`, `database_path`, `uploads_path`, `backups_path`). Tracked in git, overwritten by updates.
- `config.local.json` — everything the operator customizes. Auto-created from `config.local.example.json`; never touched by updates. `save_config()` only writes keys listed in `LOCAL_CONFIG_KEYS`.

Key fields (all in `config.local.json`):

- `single_struttura` — `true` for a single-facility install, `false` for multi-tenant
- `default_ai_provider` — `anthropic`, `gemini`, `openai`, `ollama`, `lmstudio`, or `openai_compatible`
- `default_anthropic_api_key` / `default_gemini_api_key` / `default_openai_api_key` — global fallback keys
- `default_ai_import_model`, `default_ai_email_model`, `default_ai_local_base_url`, `default_ai_local_model`
- `imap_*`, `smtp_*` — global mail settings
- `encryption_key` — derives the Fernet key for IMAP/SMTP password encryption (auto-generated)
- `force_https`, `cloudflare_mode` — deployment behind a tunnel/reverse proxy

**Per-struttura config** lives in the `strutture_config` table (`get_struttura_config()` / `set_struttura_config()` in `models.py`), and falls back to the global values above. AI keys, models and SMTP can all be overridden per facility.

## Architecture

### Flask Application Factory
`app.py` — `create_app()` registers all blueprints, sets security headers and cookie flags, enables CSRF protection, and injects globals into all templates via `inject_globals()`. The background scheduler is started by the entry points (`app.py __main__`, `run_production.py`), not by the factory.

### Blueprints
| File | Prefix | Responsibility |
|------|--------|---------------|
| `auth.py` | (root) | Login, logout, sessions, struttura impersonation, authorization decorators |
| `apparecchi.py` | `/apparecchi` | Medical device CRUD, accessories, file uploads, QR code, soft-delete (dismissione) |
| `manutenzioni.py` | `/manutenzioni` | Maintenance records, deadlines, scadenzario view, verbale PDF upload/download |
| `verifiche.py` | `/verifiche` | Electrical safety checks (verifiche di sicurezza elettrica) + AI bulk import |
| `admin.py` | `/admin` | Users, divisions, global config, backup, activity log, security, tecnici |
| `strutture_bp.py` | `/strutture` | Facility CRUD, per-facility config + AI test, API tokens |
| `import_bp.py` | `/import` | AI-powered unified document import + email queue review |
| `export_bp.py` | `/export` | Excel/PDF report generation |
| `api_bp.py` | `/api/v1` | REST API, Bearer-token auth, scoped to the token's struttura (CSRF-exempt) |

### Services
| File | Responsibility |
|------|---------------|
| `ai_service.py` | AI provider abstraction: text extraction + structured JSON parsing, per-struttura config resolution |
| `email_monitor.py` | IMAP polling, PDF extraction, AI parsing of maintenance reports |
| `scheduler.py` | Background daemon: email checks, session cleanup, auto-backups, deadline digests, scheduled PDF reports |
| `backup_service.py` | SQLite backup/restore lifecycle |
| `export_service.py` | Report generation logic (openpyxl, fpdf2) |
| `cloudflare_mode.py` | Cloudflare Tunnel setup helper |
| `models.py` | DB helpers: `get_db()`, query wrappers, scope helpers, incremental schema updates |
| `manutenzione_lib/stato.py` | Installation snapshot: paths, schema version, counts. Never includes secrets |
| `manutenzione_lib/diagnosi.py` | Checks, each a function returning an `Esito` whose remedy is the command to run |
| `manutenzione_lib/utenti.py` | Account operations outside Flask: hash inspection, password reset, wipe |

### Database
SQLite with WAL mode and foreign keys enabled. Schema in `schema.sql`; seed data in `seed.py`. `models.apply_schema_updates()` applies idempotent incremental migrations at every startup — put new schema changes there, not only in standalone `migrate_*.py` scripts.

Key tables: `strutture`, `strutture_config`, `api_tokens`, `divisioni`, `utenti`, `utenti_divisioni`, `tecnici_strutture`, `sessioni`, `login_attempts`, `apparecchi`, `accessori`, `manutenzioni`, `verifiche`, `documenti`, `import_history`, `import_preview`, `email_config`, `log_attivita`. The view `prossime_scadenze` merges maintenance and electrical-check deadlines, keeping only the latest record per (apparecchio, tipo), with a 5-priority classification (scaduto / urgente / attenzione / avviso / ok).

`import_history.struttura_id` is the authoritative tenant column for imports — `divisione_id` is NULL for email imports and must not be used for isolation.

Note: `email_config` is legacy. IMAP settings are read from the global config by `email_monitor.check_all_emails()`; rows in that table are not polled.

### Sessions & Auth
Sessions use UUID tokens stored in the `sessioni` table (not Flask cookies alone). Login is rate-limited per IP and per email via `login_attempts`.

Four roles:

| Role | Scope |
|------|-------|
| `superadmin` | All facilities. Can impersonate a struttura; global operations (backup, reset, global config). |
| `admin` | One struttura: its users, divisions, per-facility config, and all its data. |
| `tecnico` | The facilities assigned via `tecnici_strutture`; selects the active one at login. |
| `utente` | The divisions assigned via `utenti_divisioni`. Can create and edit records there, but not delete or dismiss. |

Decorators in `auth.py`: `login_required`, `admin_required`, `superadmin_required`, `tecnico_o_admin_required`, `tecnico_o_superadmin_required`, `admin_struttura_required`, `operazione_globale_required`.

### HTMX Pattern
Routes check `request.args.get('partial')` to return only a table fragment (from `templates/partials/`) for in-place updates, or the full page otherwise.

## Maintenance tool

`manutenzione.py` is the entry point; the logic lives in `manutenzione_lib/`
(`tui.py` presents and knows no domain, `stato.py` collects and never judges,
`diagnosi.py` judges and never prints, `utenti.py` operates on a raw
`sqlite3.Connection`, `menu.py` is the only caller of `input()`). It never
imports Flask, which is what lets `--db` point at a *different* installation —
including one on an older schema. `migrate.py`, `toggle_modalita.py` and
`pulisci_uploads.py` are unchanged and still work standalone; the tool calls
into them through `manutenzione_lib/operazioni.py`.

Diagnosing a login nobody can pass: `check_password_hash` **raises** on a hash
whose method Werkzeug 3 dropped (the old `sha256$…`), and `auth.py:422` does
not catch it — so such an installation answers 500, not "credenziali non
valide". `manutenzione.py diagnosi` separates that case from the ones that
really do produce the rejection message: no row with that email, `attivo = 0`,
a genuinely wrong password, or a lockout in `login_attempts`.

Checks read columns that migrations added, so each one asks
`stato.colonna_esiste()` first: a check that raises on a v1.x database says
nothing, and the remedy there is `manutenzione.py migra`, not a bug report.

`manutenzione.py utenti azzera` wipes users while keeping every other row. It
reuses `utente_service.cancella_utente()` by default (tombstone rows, `*_by`
untouched) and `struttura_service._rimuovi_utenti()` under `--definitivo`. The
replacement login is created inside the same transaction, and the command
refuses to leave a database nobody can log into.

## Key Conventions

- **Language:** All UI text, comments, variable names, and database values are in Italian.
- **Tenant isolation:** Every query touching `apparecchi`, `manutenzioni`, `verifiche`, `documenti` or `import_history` must be scoped to the caller's struttura. Use `models.apparecchio_accessibile()` before serving or writing anything tied to a device (it checks struttura *and* division), and `import_bp.get_import_in_scope()` for import records. Reaching a row by id alone is never sufficient.
- **Global vs per-facility operations:** Anything acting on the whole database (backup, restore, reset, global config) goes behind `@operazione_globale_required`, not `@admin_required` — a facility admin must not be able to dump or wipe other tenants' data.
- **Soft delete:** Devices are never physically deleted — `stato='dismesso'` marks them retired. Stati validi: `funzionante`, `in_manutenzione`, `da_sostituire`, `dismesso`.
- **Parameterized SQL:** All queries use `?` placeholders; never f-string user input. When a query *is* built with an f-string (division filters), remember Python does not consume `%%` — write `strftime('%Y-%m', ...)`, not the doubled form.
- **CSRF:** `CSRFProtect` is global. Every POST form needs a hidden `csrf_token` field rendered with `{{ csrf_token() }}` — including empty JS-driven forms. `fetch()` and HTMX get the header automatically from the wrapper in `base.html`.
- **Activity logging:** Every significant action must call `log_attivita()` from `models.py`.
- **Division filter:** Always apply `_get_divisione_filter()` when querying `apparecchi`, `manutenzioni` or `verifiche` to respect the user's active division scope.
- **File uploads:** Go through `models.upload_subdir()`, which places files under `uploads/strutture/<id>/<tipo>/` in multi-tenant mode — the only path prefix `/uploads/<path>` knows how to isolate. Always `secure_filename()`; extensions are whitelisted per type.

## AI Features

`ai_service.py` wraps the AI workflows with multi-provider support (Anthropic Claude / Gemini / OpenAI / Ollama / LM Studio / OpenAI-compatible), resolving provider, key and model per struttura with fallback to the global defaults:
1. **Unified document import** (`import_bp.py`): Upload Excel/PDF/CSV → classify document type (inventario / verbale manutenzione / verifica elettrica) via keyword heuristics + AI fallback → for multi-page PDFs, split into individual pages (`pypdf`) → analyze each page with type-specific prompt → preview with apparecchio matching → batch insert into `apparecchi`, `manutenzioni`, or `verifiche`. Analysis runs in a background thread; `import_history.stato` tracks progress.
2. **Email maintenance parsing** (`email_monitor.py`): IMAP polling → PDF attachment extraction → AI parses maintenance report → auto-creates `manutenzioni` record with PDF verbale attached (`verbale_path`) if device found, else queues for manual review in the email queue (`import_history` with `tipo_import='verbale_email'`).
## Configuration

Config is split in two files, merged at startup (`load_config()` in `app.py`, local wins):

- `config.json` — system defaults only (`version`, `database_path`, `uploads_path`, `backups_path`). Tracked in git, overwritten by updates.
- `config.local.json` — everything the operator customizes. Auto-created from `config.local.example.json`; never touched by updates. `save_config()` only writes keys listed in `LOCAL_CONFIG_KEYS`.

Key fields (all in `config.local.json`):

- `single_struttura` — `true` for a single-facility install, `false` for multi-tenant
- `default_ai_provider` — `anthropic`, `gemini`, `openai`, `ollama`, `lmstudio`, or `openai_compatible`
- `default_anthropic_api_key` / `default_gemini_api_key` / `default_openai_api_key` — global fallback keys
- `default_ai_import_model`, `default_ai_email_model`, `default_ai_local_base_url`, `default_ai_local_model`
- `imap_*`, `smtp_*` — global mail settings
- `encryption_key` — derives the Fernet key for IMAP/SMTP password encryption (auto-generated)
- `force_https`, `cloudflare_mode` — deployment behind a tunnel/reverse proxy

**Per-struttura config** lives in the `strutture_config` table (`get_struttura_config()` / `set_struttura_config()` in `models.py`), and falls back to the global values above. AI keys, models and SMTP can all be overridden per facility.

## Architecture

### Flask Application Factory
`app.py` — `create_app()` registers all blueprints, sets security headers and cookie flags, enables CSRF protection, and injects globals into all templates via `inject_globals()`. The background scheduler is started by the entry points (`app.py __main__`, `run_production.py`), not by the factory.

### Blueprints
| File | Prefix | Responsibility |
|------|--------|---------------|
| `auth.py` | (root) | Login, logout, sessions, struttura impersonation, authorization decorators |
| `apparecchi.py` | `/apparecchi` | Medical device CRUD, accessories, file uploads, QR code, soft-delete (dismissione) |
| `manutenzioni.py` | `/manutenzioni` | Maintenance records, deadlines, scadenzario view, verbale PDF upload/download |
| `verifiche.py` | `/verifiche` | Electrical safety checks (verifiche di sicurezza elettrica) + AI bulk import |
| `admin.py` | `/admin` | Users, divisions, global config, backup, activity log, security, tecnici |
| `strutture_bp.py` | `/strutture` | Facility CRUD, per-facility config + AI test, API tokens |
| `import_bp.py` | `/import` | AI-powered unified document import + email queue review |
| `export_bp.py` | `/export` | Excel/PDF report generation |
| `api_bp.py` | `/api/v1` | REST API, Bearer-token auth, scoped to the token's struttura (CSRF-exempt) |

### Services
| File | Responsibility |
|------|---------------|
| `ai_service.py` | AI provider abstraction: text extraction + structured JSON parsing, per-struttura config resolution |
| `email_monitor.py` | IMAP polling, PDF extraction, AI parsing of maintenance reports |
| `scheduler.py` | Background daemon: email checks, session cleanup, auto-backups, deadline digests, scheduled PDF reports |
| `backup_service.py` | SQLite backup/restore lifecycle |
| `export_service.py` | Report generation logic (openpyxl, fpdf2) |
| `cloudflare_mode.py` | Cloudflare Tunnel setup helper |
| `models.py` | DB helpers: `get_db()`, query wrappers, scope helpers, incremental schema updates |

### Database
SQLite with WAL mode and foreign keys enabled. Schema in `schema.sql`; seed data in `seed.py`. `models.apply_schema_updates()` applies idempotent incremental migrations at every startup — put new schema changes there, not only in standalone `migrate_*.py` scripts.

Key tables: `strutture`, `strutture_config`, `api_tokens`, `divisioni`, `utenti`, `utenti_divisioni`, `tecnici_strutture`, `sessioni`, `login_attempts`, `apparecchi`, `accessori`, `manutenzioni`, `verifiche`, `documenti`, `import_history`, `import_preview`, `email_config`, `log_attivita`. The view `prossime_scadenze` merges maintenance and electrical-check deadlines, keeping only the latest record per (apparecchio, tipo), with a 5-priority classification (scaduto / urgente / attenzione / avviso / ok).

`import_history.struttura_id` is the authoritative tenant column for imports — `divisione_id` is NULL for email imports and must not be used for isolation.

Note: `email_config` is legacy. IMAP settings are read from the global config by `email_monitor.check_all_emails()`; rows in that table are not polled.

### Sessions & Auth
Sessions use UUID tokens stored in the `sessioni` table (not Flask cookies alone). Login is rate-limited per IP and per email via `login_attempts`.

Four roles:

| Role | Scope |
|------|-------|
| `superadmin` | All facilities. Can impersonate a struttura; global operations (backup, reset, global config). |
| `admin` | One struttura: its users, divisions, per-facility config, and all its data. |
| `tecnico` | The facilities assigned via `tecnici_strutture`; selects the active one at login. |
| `utente` | The divisions assigned via `utenti_divisioni`. Can create and edit records there, but not delete or dismiss. |

Decorators in `auth.py`: `login_required`, `admin_required`, `superadmin_required`, `tecnico_o_admin_required`, `tecnico_o_superadmin_required`, `admin_struttura_required`, `operazione_globale_required`.

### HTMX Pattern
Routes check `request.args.get('partial')` to return only a table fragment (from `templates/partials/`) for in-place updates, or the full page otherwise.

## Key Conventions

- **Language:** All UI text, comments, variable names, and database values are in Italian.
- **Tenant isolation:** Every query touching `apparecchi`, `manutenzioni`, `verifiche`, `documenti` or `import_history` must be scoped to the caller's struttura. Use `models.apparecchio_accessibile()` before serving or writing anything tied to a device (it checks struttura *and* division), and `import_bp.get_import_in_scope()` for import records. Reaching a row by id alone is never sufficient.
- **Global vs per-facility operations:** Anything acting on the whole database (backup, restore, reset, global config) goes behind `@operazione_globale_required`, not `@admin_required` — a facility admin must not be able to dump or wipe other tenants' data.
- **Soft delete:** Devices are never physically deleted — `stato='dismesso'` marks them retired. Stati validi: `funzionante`, `in_manutenzione`, `da_sostituire`, `dismesso`.
- **Parameterized SQL:** All queries use `?` placeholders; never f-string user input. When a query *is* built with an f-string (division filters), remember Python does not consume `%%` — write `strftime('%Y-%m', ...)`, not the doubled form.
- **CSRF:** `CSRFProtect` is global. Every POST form needs a hidden `csrf_token` field rendered with `{{ csrf_token() }}` — including empty JS-driven forms. `fetch()` and HTMX get the header automatically from the wrapper in `base.html`.
- **Activity logging:** Every significant action must call `log_attivita()` from `models.py`.
- **Division filter:** Always apply `_get_divisione_filter()` when querying `apparecchi`, `manutenzioni` or `verifiche` to respect the user's active division scope.
- **File uploads:** Go through `models.upload_subdir()`, which places files under `uploads/strutture/<id>/<tipo>/` in multi-tenant mode — the only path prefix `/uploads/<path>` knows how to isolate. Always `secure_filename()`; extensions are whitelisted per type.

## AI Features

`ai_service.py` wraps the AI workflows with multi-provider support (Anthropic Claude / Gemini / OpenAI / Ollama / LM Studio / OpenAI-compatible), resolving provider, key and model per struttura with fallback to the global defaults:
1. **Unified document import** (`import_bp.py`): Upload Excel/PDF/CSV → classify document type (inventario / verbale manutenzione / verifica elettrica) via keyword heuristics + AI fallback → for multi-page PDFs, split into individual pages (`pypdf`) → analyze each page with type-specific prompt → preview with apparecchio matching → batch insert into `apparecchi`, `manutenzioni`, or `verifiche`. Analysis runs in a background thread; `import_history.stato` tracks progress.
2. **Email maintenance parsing** (`email_monitor.py`): IMAP polling → PDF attachment extraction → AI parses maintenance report → auto-creates `manutenzioni` record with PDF verbale attached (`verbale_path`) if device found, else queues for manual review in the email queue (`import_history` with `tipo_import='verbale_email'`).
