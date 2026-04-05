# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

**MedInventory v1.4.3** (Custode_v2) — Italian-language web application for managing medical devices (*apparecchi elettromedicali*) in healthcare facilities. Built for Windows LAN deployment by Studio Bergamaschi.

**Stack:** Flask 3.x + SQLite3 + HTMX + Bootstrap 5 + AI (Anthropic Codex / Ollama / LM Studio)

## Running the Application

```bash
# Development (auto-reload, debug mode)
python app.py

# Production (Waitress WSGI, 4 threads, rotating logs)
python run_production.py

# First-time setup: initialize database schema + default admin user
python seed.py
# Default credentials: admin@medinventory.local / admin123
```

No build step needed. No test suite exists.

## Configuration

All runtime config lives in `config.json` (auto-generated from `config.example.json`). Key fields:

- `ai_provider` — `anthropic` (default), `ollama`, `lmstudio`, or `openai_compatible`
- `anthropic_api_key` — required when using Anthropic Codex
- `ai_import_model` — currently `Codex-sonnet-4-20250514` (Anthropic)
- `ai_email_model` — currently `Codex-haiku-4-5-20251001` (Anthropic)
- `ai_local_base_url` — URL for local AI server (Ollama/LM Studio)
- `ai_local_model` — model name for local provider
- `database_path` — defaults to `data/database.sqlite`
- `encryption_key` — Fernet key for IMAP password encryption (auto-generated)

## Architecture

### Flask Application Factory
`app.py` — `create_app()` registers all blueprints, starts the background scheduler, and injects globals into all templates via `inject_globals()` context processor.

### Blueprints
| File | Prefix | Responsibility |
|------|--------|---------------|
| `auth.py` | (root) | Login, logout, sessions, `@login_required` / `@admin_required` decorators |
| `apparecchi.py` | `/apparecchi` | Medical device CRUD, file uploads, soft-delete (dismissione) |
| `manutenzioni.py` | `/manutenzioni` | Maintenance records, deadlines, scadenzario view, verbale PDF upload/download |
| `admin.py` | `/admin` | Users, divisions, config editor, backup, activity log |
| `import_bp.py` | `/import` | AI-powered inventory import + email queue review |
| `export_bp.py` | `/export` | Excel/PDF report generation |

### Services
| File | Responsibility |
|------|---------------|
| `ai_service.py` | AI provider abstraction (Anthropic/Ollama/LM Studio): text extraction + structured JSON parsing |
| `email_monitor.py` | IMAP polling, PDF extraction, Codex parsing of maintenance reports |
| `scheduler.py` | Background daemon: email checks, session cleanup, auto-backups |
| `backup_service.py` | SQLite backup/restore lifecycle |
| `export_service.py` | Report generation logic (openpyxl, fpdf2) |
| `models.py` | Thin DB helper: `get_db()` returns a sqlite3 connection |

### Database
SQLite with WAL mode and foreign keys enabled. Schema defined in `schema.sql`; seed data in `seed.py`. Key tables: `divisioni`, `utenti`, `utenti_divisioni`, `sessioni`, `apparecchi`, `manutenzioni`, `documenti`, `email_config`, `coda_email`, `log_attivita`. The view `prossime_scadenze` pre-calculates deadlines with 5-priority classification (scaduto / urgente / attenzione / avviso / ok).

**v1.2.0 schema changes:** `apparecchi.codice_interno` (UNIQUE) renamed to `descrizione` (no UNIQUE constraint). `stato` CHECK extended with `'da_sostituire'`. New `accessori` table (FK CASCADE on `apparecchi.id`) with columns: `id`, `apparecchio_id`, `descrizione`, `produttore`, `modello`, `matricola`, `created_by`, `created_at`. Run `migrate_v1_2.py` to apply to existing databases.

**v1.3.0 schema changes:** New `manutenzioni.verbale_path TEXT` column for PDF verbale attachment. Run `migrate_v1_3.py` to apply to existing databases.

### Sessions & Auth
Sessions use UUID tokens stored in the `sessioni` table (not Flask cookies alone). Two roles: `admin` (full access) and `utente` (read-only). Division scoping is enforced throughout via `_get_divisione_filter()` helpers in each blueprint.

### HTMX Pattern
Routes check `request.args.get('partial')` to return only a table fragment (from `templates/partials/`) for in-place updates, or the full page otherwise.

## Key Conventions

- **Language:** All UI text, comments, variable names, and database values are in Italian.
- **Soft delete:** Devices are never physically deleted — `stato='dismesso'` marks them retired. Stati validi: `funzionante`, `in_manutenzione`, `da_sostituire`, `dismesso`.
- **Parameterized SQL:** All queries use `?` placeholders; never f-string SQL.
- **Activity logging:** Every significant action must call `log_attivita()` from `auth.py`.
- **Division filter:** Always apply `_get_divisione_filter()` when querying `apparecchi` or `manutenzioni` to respect the user's active division scope.
- **File uploads:** Uploaded photos/documents go to `uploads/` with `secure_filename()`; extensions are whitelisted per type.

## AI Features

`ai_service.py` wraps the AI workflows with multi-provider support (Anthropic Codex / Ollama / LM Studio / OpenAI-compatible):
1. **Unified document import** (`import_bp.py`): Upload Excel/PDF/CSV → classify document type (inventario / verbale manutenzione / verifica elettrica) via keyword heuristics + AI fallback → for multi-page PDFs, split into individual pages (`pypdf`) → analyze each page with type-specific prompt → preview with apparecchio matching → batch insert into `apparecchi`, `manutenzioni`, or `verifiche`.
2. **Email maintenance parsing** (`email_monitor.py`): IMAP polling → PDF attachment extraction → AI parses maintenance report → auto-creates `manutenzioni` record with PDF verbale attached (`verbale_path`) if device found, else queues for manual review in `coda_email`.
