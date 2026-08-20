"""
MedInventory - Gestione Apparecchi Elettromedicali
Main application entry point.

by Studio Bergamaschi
"""

import sys
import os
import json
import shutil
import secrets
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta

# Force UTF-8 I/O on Windows (avoids 'ascii' codec errors with Italian text / Anthropic SDK)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
from flask import (Flask, g, session, redirect, url_for, render_template,
                   request, current_app)
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_wtf.csrf import CSRFProtect

from models import close_db, init_db, get_db, query_all
from auth import login_required as auth_login_required

# ---------------------------------------------------------------------------
# Version (source of truth — config.json is auto-updated at startup)
# ---------------------------------------------------------------------------

APP_VERSION = "2.6.3"

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(log_dir):
    """Configure rotating file logging for the entire application."""
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'medinventory.log')
    handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding='utf-8'
    )
    handler.setFormatter(
        logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    )
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(logging.INFO)
        root.addHandler(handler)
    else:
        root.addHandler(handler)


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_setup_logging(os.path.join(_BASE_DIR, 'logs'))

CONFIG_PATH         = os.path.join(_BASE_DIR, 'config.json')
LOCAL_CONFIG_PATH   = os.path.join(_BASE_DIR, 'config.local.json')
LOCAL_EXAMPLE_PATH  = os.path.join(_BASE_DIR, 'config.local.example.json')
EXAMPLE_CONFIG_PATH = os.path.join(_BASE_DIR, 'config.example.json')

# Campi che appartengono alla configurazione locale (mai sovrascritta dagli aggiornamenti).
# config.json contiene solo i default di sistema (version, paths).
# config.local.json contiene tutto ciò che l'utente personalizza.
LOCAL_CONFIG_KEYS = frozenset({
    'app_name', 'organization', 'structure_name',
    'host', 'port', 'debug',
    'secret_key', 'encryption_key',
    'session_lifetime_hours', 'backup_retention',
    'ai_provider', 'anthropic_api_key', 'gemini_api_key', 'openai_api_key',
    'ai_import_model', 'ai_email_model', 'ai_verifiche_model',
    'ai_local_base_url', 'ai_local_model',
    'default_ai_provider',
    'default_anthropic_api_key', 'default_gemini_api_key', 'default_openai_api_key',
    'default_ai_local_base_url', 'default_ai_local_model',
    'default_ai_import_model', 'default_ai_email_model',
    'email_check_interval_minutes',
    'imap_enabled', 'imap_account', 'imap_password', 'imap_server', 'imap_port', 'imap_ssl',
    'alert_email_enabled', 'alert_email_to',
    'smtp_host', 'smtp_port', 'smtp_user', 'smtp_password', 'smtp_use_tls',
    'single_struttura', 'force_https', 'cloudflare_mode',
})


def _create_local_config(base_config):
    """Crea config.local.json da template, migrando i campi già presenti in config.json."""
    if os.path.exists(LOCAL_EXAMPLE_PATH):
        with open(LOCAL_EXAMPLE_PATH, 'r', encoding='utf-8') as f:
            local = json.load(f)
    else:
        local = {}

    # Migra i campi utente già presenti nel vecchio config.json (backward compatibility)
    for key in LOCAL_CONFIG_KEYS:
        if key in base_config:
            local[key] = base_config[key]

    # Genera chiavi crittografiche se assenti
    if not local.get('secret_key'):
        local['secret_key'] = secrets.token_hex(32)
    if not local.get('encryption_key'):
        local['encryption_key'] = secrets.token_hex(32)

    with open(LOCAL_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(local, f, indent=2, ensure_ascii=False)


def load_config():
    """Carica config.json (base) e lo fonde con config.local.json (impostazioni utente).
    Se config.local.json non esiste viene creato automaticamente.
    """
    # Carica il config di base (default di sistema)
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)

    # Crea il config locale se non esiste (primo avvio o aggiornamento da versione precedente)
    if not os.path.exists(LOCAL_CONFIG_PATH):
        _create_local_config(config)

    # Fonde il config locale sopra il base (local ha priorità)
    with open(LOCAL_CONFIG_PATH, 'r', encoding='utf-8') as f:
        local = json.load(f)
    config.update(local)

    return config


def save_config(config):
    """Salva le impostazioni utente in config.local.json. Non modifica mai config.json."""
    local = {k: v for k, v in config.items() if k in LOCAL_CONFIG_KEYS}
    with open(LOCAL_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(local, f, indent=2, ensure_ascii=False)


def check_version_update(config):
    """Confronta la versione nel config con APP_VERSION.
    Se diversa: fa il backup di config.json, aggiorna la versione e scrive
    il file sentinella data/.version_notice per notificare il primo admin.
    """
    config_version = config.get('version', '')
    if config_version == APP_VERSION:
        return

    # Backup config.json
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = CONFIG_PATH + f'.bak_{timestamp}'
    shutil.copy2(CONFIG_PATH, backup_path)

    # Aggiorna versione in config.json
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        base = json.load(f)
    base['version'] = APP_VERSION
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(base, f, indent=2, ensure_ascii=False)

    # Aggiorna anche il config in memoria
    config['version'] = APP_VERSION

    # Scrivi file sentinella per notificare il primo admin al login
    notice_path = os.path.join(_BASE_DIR, 'data', '.version_notice')
    os.makedirs(os.path.dirname(notice_path), exist_ok=True)
    with open(notice_path, 'w', encoding='utf-8') as f:
        json.dump({
            'old_version': config_version or 'sconosciuta',
            'new_version': APP_VERSION,
            'upgraded_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'config_backup': backup_path,
        }, f, indent=2)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app():
    """Create and configure the Flask application."""
    config = load_config()
    check_version_update(config)

    app = Flask(
        __name__,
        static_folder='static',
        template_folder='templates'
    )

    # Flask config
    app.secret_key = config['secret_key']

    # CSRF protection
    csrf = CSRFProtect()
    csrf.init_app(app)
    app.config['WTF_CSRF_TIME_LIMIT'] = 3600  # 1 ora

    app.config['DATABASE_PATH'] = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        config.get('database_path', 'data/database.sqlite')
    )
    app.config['UPLOADS_PATH'] = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        config.get('uploads_path', 'uploads')
    )
    app.config['BACKUPS_PATH'] = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        config.get('backups_path', 'backups')
    )
    app.config['APP_CONFIG'] = config
    app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32 MB upload limit
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(
        hours=config.get('session_lifetime_hours', 8)
    )

    # ProxyFix: corregge request.remote_addr e scheme quando l'app è dietro
    # un reverse proxy o tunnel (Cloudflare Tunnel, Nginx, ecc.).
    # x_for=1 si fida di un solo hop proxy (cloudflared → app).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # ---------------------------------------------------------------------------
    # Cookie security
    # ---------------------------------------------------------------------------
    # HttpOnly: impedisce l'accesso al cookie via JavaScript (sempre attivo).
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    # SameSite=Lax: blocca l'invio del cookie in richieste cross-site di terze parti
    # (protezione CSRF di base), compatibile sia con HTTP che HTTPS.
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    # Secure: il cookie viene trasmesso solo su HTTPS.
    # Abilitare SOLO quando l'app è accessibile esclusivamente via HTTPS
    # (es. con Cloudflare Tunnel). Disabilitarlo in accesso LAN puro via HTTP.
    if config.get('force_https', False):
        app.config['SESSION_COOKIE_SECURE'] = True

    # Ensure data directories exist
    os.makedirs(os.path.dirname(app.config['DATABASE_PATH']), exist_ok=True)
    os.makedirs(app.config['UPLOADS_PATH'], exist_ok=True)
    os.makedirs(app.config['BACKUPS_PATH'], exist_ok=True)

    # ---------------------------------------------------------------------------
    # Database lifecycle
    # ---------------------------------------------------------------------------
    app.teardown_appcontext(close_db)

    with app.app_context():
        init_db()
        from models import apply_schema_updates
        apply_schema_updates()

    # ---------------------------------------------------------------------------
    # Context processor: inject common variables into all templates
    # ---------------------------------------------------------------------------
    @app.context_processor
    def inject_globals():
        """Make config, user, and division data available in all templates."""
        from posta import smtp_configurato

        single_struttura = config.get('single_struttura', False)

        struttura = getattr(g, 'struttura', None)
        is_superadmin_impersonating = getattr(g, 'is_superadmin_impersonating', False)

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

        # Installazione a struttura singola: l'admin conserva l'accesso alle
        # operazioni globali (backup, config) perché non c'è nulla da isolare.
        # Stesso criterio usato da auth.operazione_globale_required.
        installazione_singola = single_struttura
        if not installazione_singola and getattr(g, 'user', None):
            from auth import _installazione_singola_struttura
            installazione_singola = _installazione_singola_struttura()

        ctx = {
            'app_config': config,
            'installazione_singola': installazione_singola,
            'app_name': config.get('app_name', 'MedInventory'),
            'organization': config.get('organization', 'Studio Bergamaschi'),
            'structure_name': config.get('structure_name', ''),
            'app_version': APP_VERSION,
            'single_struttura': single_struttura,
            'g_struttura': struttura,
            'g_is_superadmin_impersonating': is_superadmin_impersonating,
            'strutture_list': strutture_list,
            # La schermata di accesso mostra «Password dimenticata?» solo se
            # c'e' un server di posta da cui spedire: un pulsante che accetta
            # la richiesta e non manda niente e' peggio che non averlo, perche'
            # l'utente aspetta invano. Sta qui e non nella rotta perche'
            # login.html viene reso da sei punti diversi di auth.py, e passarla
            # a mano da ognuno e' il modo di dimenticarsene in uno.
            'smtp_configurato': smtp_configurato(
                current_app.config.get('APP_CONFIG') or config),
        }

        # Add user-related context if authenticated
        if hasattr(g, 'user') and g.user:
            ctx['current_user'] = g.user
            ctx['g_user_id'] = g.user.get('id')
            ctx['divisioni_accessibili'] = getattr(g, 'divisioni', [])
            ctx['divisione_attiva'] = getattr(g, 'divisione_attiva', None)
            ctx['scadenze_alert_count'] = getattr(g, 'scadenze_alert_count', 0)

        return ctx

    # ---------------------------------------------------------------------------
    # Security headers (aggiunti a ogni risposta)
    # ---------------------------------------------------------------------------
    @app.after_request
    def add_security_headers(response):
        # Previene MIME-type sniffing (es. eseguire uno script travestito da immagine)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # Blocca embedding in iframe di altri siti (clickjacking)
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        # Limita le informazioni nel Referer header inviato ai siti terzi
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # Disabilita funzionalità browser non necessarie (geolocation, camera, ecc.)
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        # HSTS: forza il browser a usare HTTPS per i prossimi 12 mesi.
        # Inviato solo quando la connessione è (o sembra) HTTPS, per non
        # bloccare l'accesso LAN su HTTP puro.
        if (request.scheme == 'https'
                or request.headers.get('X-Forwarded-Proto') == 'https'):
            response.headers['Strict-Transport-Security'] = (
                'max-age=31536000; includeSubDomains'
            )
        return response

    # ---------------------------------------------------------------------------
    # HTTPS redirect (opzionale — attivo solo con force_https=true in config)
    # ---------------------------------------------------------------------------
    if config.get('force_https', False):
        @app.before_request
        def enforce_https():
            # Scatta solo quando il proxy ci dice che il client ha usato HTTP
            # (X-Forwarded-Proto: http). Se l'header non c'è, siamo in accesso
            # LAN diretto senza proxy e non forziamo il redirect.
            if request.headers.get('X-Forwarded-Proto') == 'http':
                url = request.url.replace('http://', 'https://', 1)
                return redirect(url, 301)

    # ---------------------------------------------------------------------------
    # Register blueprints
    # ---------------------------------------------------------------------------
    from auth import auth_bp
    app.register_blueprint(auth_bp)

    from apparecchi import apparecchi_bp
    app.register_blueprint(apparecchi_bp)

    from manutenzioni import manutenzioni_bp
    app.register_blueprint(manutenzioni_bp)

    from admin import admin_bp
    app.register_blueprint(admin_bp)

    from import_bp import import_bp
    app.register_blueprint(import_bp)

    from export_bp import export_bp
    app.register_blueprint(export_bp)

    from stampe_bp import stampe_bp
    app.register_blueprint(stampe_bp)

    from verifiche import verifiche_bp
    app.register_blueprint(verifiche_bp)

    from strutture_bp import strutture_bp
    app.register_blueprint(strutture_bp)

    from api_bp import api_bp
    app.register_blueprint(api_bp)
    # API REST: autenticazione via Bearer token (non cookie), quindi non
    # soggetta a CSRF. Senza questa esenzione i POST a /api/v1/* verrebbero
    # rifiutati da CSRFProtect con 400 (token mancante).
    csrf.exempt(api_bp)

    # ---------------------------------------------------------------------------
    # Root route -> Dashboard
    # ---------------------------------------------------------------------------
    @app.route('/')
    @auth_login_required
    def index():
        from flask import redirect, url_for
        from models import query_one, query_all

        # Tecnico senza struttura selezionata → pagina di selezione
        if getattr(g, 'user', {}).get('ruolo') == 'tecnico' and not getattr(g, 'struttura_id', None):
            return redirect(url_for('auth.tecnico_seleziona_struttura_page'))

        # Division filter
        div = getattr(g, 'divisione_attiva', None)
        struttura_id = getattr(g, 'struttura_id', None)
        if div and div.get('id') != 'tutte':
            div_clause = "AND a.divisione_id = ?"
            div_params = [div['id']]
            div_clause_m = "AND a.divisione_id = ?"
        elif getattr(g, 'user', {}).get('ruolo') in ('admin', 'tecnico'):
            if struttura_id:
                div_clause = "AND a.struttura_id = ?"
                div_params = [struttura_id]
                div_clause_m = "AND a.struttura_id = ?"
            else:
                div_clause = ""
                div_params = []
                div_clause_m = ""
        else:
            ids = [d['id'] for d in getattr(g, 'divisioni', [])]
            if ids:
                ph = ','.join('?' * len(ids))
                div_clause = f"AND a.divisione_id IN ({ph})"
                div_params = ids
                div_clause_m = div_clause
            else:
                div_clause = "AND 1=0"
                div_params = []
                div_clause_m = "AND 1=0"

        # Stat 1: Total apparecchi (non dismessi)
        r = query_one(
            f"SELECT COUNT(*) as cnt FROM apparecchi a WHERE a.stato != 'dismesso' {div_clause}",
            div_params
        )
        totale_apparecchi = r['cnt'] if r else 0

        # Stat 2: Active alerts (deadlines <= 30 days)
        if div and div.get('id') != 'tutte':
            r = query_one(
                "SELECT COUNT(*) as cnt FROM prossime_scadenze WHERE divisione_id = ? AND priorita IN ('scaduto','urgente','attenzione','avviso')",
                [div['id']]
            )
        elif getattr(g, 'user', {}).get('ruolo') in ('admin', 'tecnico'):
            if struttura_id:
                r = query_one(
                    """SELECT COUNT(*) as cnt FROM prossime_scadenze ps
                       JOIN apparecchi a ON a.id = ps.apparecchio_id
                       WHERE a.struttura_id = ? AND ps.priorita IN ('scaduto','urgente','attenzione','avviso')""",
                    [struttura_id]
                )
            else:
                r = query_one(
                    "SELECT COUNT(*) as cnt FROM prossime_scadenze WHERE priorita IN ('scaduto','urgente','attenzione','avviso')"
                )
        else:
            ids = [d['id'] for d in getattr(g, 'divisioni', [])]
            if ids:
                ph = ','.join('?' * len(ids))
                r = query_one(
                    f"SELECT COUNT(*) as cnt FROM prossime_scadenze WHERE divisione_id IN ({ph}) AND priorita IN ('scaduto','urgente','attenzione','avviso')",
                    ids
                )
            else:
                r = None
        scadenze_attive = r['cnt'] if r else 0

        # Stat 3: Manutenzioni this month
        r = query_one(
            f"""SELECT COUNT(*) as cnt FROM manutenzioni m
                JOIN apparecchi a ON m.apparecchio_id = a.id
                WHERE strftime('%Y-%m', m.data_intervento) = strftime('%Y-%m', 'now')
                {div_clause_m}""",
            div_params
        )
        manutenzioni_mese = r['cnt'] if r else 0

        # Stat 4: Costs this month
        r = query_one(
            f"""SELECT COALESCE(SUM(m.costo), 0) as tot FROM manutenzioni m
                JOIN apparecchi a ON m.apparecchio_id = a.id
                WHERE strftime('%Y-%m', m.data_intervento) = strftime('%Y-%m', 'now')
                {div_clause_m}""",
            div_params
        )
        costi_mese = r['tot'] if r else 0

        # Upcoming deadlines (top 10)
        if div and div.get('id') != 'tutte':
            scadenze_imminenti = query_all(
                """SELECT ps.*, d.nome as divisione_nome, d.colore as divisione_colore
                   FROM prossime_scadenze ps
                   LEFT JOIN divisioni d ON ps.divisione_id = d.id
                   WHERE ps.divisione_id = ?
                   ORDER BY ps.prossima_scadenza ASC LIMIT 10""",
                [div['id']]
            )
        elif struttura_id:
            scadenze_imminenti = query_all(
                """SELECT ps.*, d.nome as divisione_nome, d.colore as divisione_colore
                   FROM prossime_scadenze ps
                   LEFT JOIN divisioni d ON ps.divisione_id = d.id
                   JOIN apparecchi a ON a.id = ps.apparecchio_id
                   WHERE a.struttura_id = ?
                   ORDER BY ps.prossima_scadenza ASC LIMIT 10""",
                [struttura_id]
            )
        else:
            scadenze_imminenti = query_all(
                """SELECT ps.*, d.nome as divisione_nome, d.colore as divisione_colore
                   FROM prossime_scadenze ps
                   LEFT JOIN divisioni d ON ps.divisione_id = d.id
                   ORDER BY ps.prossima_scadenza ASC LIMIT 10"""
            )

        # Recent interventions (last 10)
        ultimi_interventi = query_all(
            f"""SELECT m.*, a.marca, a.modello, a.matricola,
                       d.nome as divisione_nome, d.colore as divisione_colore
                FROM manutenzioni m
                JOIN apparecchi a ON m.apparecchio_id = a.id
                LEFT JOIN divisioni d ON a.divisione_id = d.id
                WHERE 1=1 {div_clause_m}
                ORDER BY m.data_intervento DESC LIMIT 10""",
            div_params
        )

        # Chart data: apparecchi per classificazione
        chart_classificazione = query_all(
            f"""SELECT classificazione, COUNT(*) as cnt
                FROM apparecchi a
                WHERE a.stato != 'dismesso' AND a.classificazione IS NOT NULL {div_clause}
                GROUP BY classificazione ORDER BY classificazione""",
            div_params
        )

        # Chart data: costi mensili (last 12 months)
        chart_costi = query_all(
            f"""SELECT strftime('%Y-%m', m.data_intervento) as mese,
                       COALESCE(SUM(m.costo), 0) as totale
                FROM manutenzioni m
                JOIN apparecchi a ON m.apparecchio_id = a.id
                WHERE m.data_intervento >= date('now', '-12 months')
                {div_clause_m}
                GROUP BY mese ORDER BY mese""",
            div_params
        )

        # Chart data: interventi per tipo
        chart_tipi = query_all(
            f"""SELECT m.tipo, COUNT(*) as cnt
                FROM manutenzioni m
                JOIN apparecchi a ON m.apparecchio_id = a.id
                WHERE 1=1 {div_clause_m}
                GROUP BY m.tipo ORDER BY cnt DESC""",
            div_params
        )

        # Stat 5: Verifiche scadute
        r = query_one(
            f"""SELECT COUNT(*) as cnt FROM apparecchi a
                WHERE a.stato != 'dismesso' AND a.soggetto_verifica = 1 {div_clause}
                AND EXISTS (
                    SELECT 1 FROM verifiche v WHERE v.apparecchio_id = a.id
                    AND v.prossima_scadenza < date('now')
                    AND v.id = (SELECT id FROM verifiche
                                WHERE apparecchio_id = a.id
                                ORDER BY data_verifica DESC, id DESC LIMIT 1)
                )""",
            div_params
        )
        verifiche_scadute = r['cnt'] if r else 0

        # Stat 6: Verifiche in scadenza entro 30gg
        r = query_one(
            f"""SELECT COUNT(*) as cnt FROM apparecchi a
                WHERE a.stato != 'dismesso' AND a.soggetto_verifica = 1 {div_clause}
                AND EXISTS (
                    SELECT 1 FROM verifiche v WHERE v.apparecchio_id = a.id
                    AND v.prossima_scadenza >= date('now')
                    AND v.prossima_scadenza <= date('now', '+30 days')
                    AND v.id = (SELECT id FROM verifiche
                                WHERE apparecchio_id = a.id
                                ORDER BY data_verifica DESC, id DESC LIMIT 1)
                )""",
            div_params
        )
        verifiche_in_scadenza = r['cnt'] if r else 0

        # Stat 7: Apparecchi soggetti senza nessuna verifica
        r = query_one(
            f"""SELECT COUNT(*) as cnt FROM apparecchi a
                WHERE a.stato != 'dismesso' AND a.soggetto_verifica = 1 {div_clause}
                AND NOT EXISTS (SELECT 1 FROM verifiche WHERE apparecchio_id = a.id)""",
            div_params
        )
        apparecchi_senza_verifica = r['cnt'] if r else 0

        # Chart data: stato verifiche elettriche
        chart_verifiche = query_all(
            f"""SELECT
                    CASE
                        WHEN a.soggetto_verifica = 0 THEN 'Esente'
                        WHEN uv.ultima IS NULL THEN 'Nessuna'
                        WHEN uv.scadenza IS NULL OR uv.scadenza >= date('now') THEN 'OK'
                        ELSE 'Scaduta'
                    END as stato,
                    COUNT(*) as cnt
                FROM apparecchi a
                LEFT JOIN (
                    SELECT v.apparecchio_id,
                           v.data_verifica as ultima,
                           v.prossima_scadenza as scadenza
                    FROM verifiche v
                    WHERE v.id = (SELECT id FROM verifiche
                                  WHERE apparecchio_id = v.apparecchio_id
                                  ORDER BY data_verifica DESC, id DESC LIMIT 1)
                ) uv ON uv.apparecchio_id = a.id
                WHERE a.stato != 'dismesso' {div_clause}
                GROUP BY 1""",
            div_params
        )

        import json
        return render_template('dashboard.html',
                               totale_apparecchi=totale_apparecchi,
                               scadenze_attive=scadenze_attive,
                               manutenzioni_mese=manutenzioni_mese,
                               costi_mese=costi_mese,
                               verifiche_scadute=verifiche_scadute,
                               verifiche_in_scadenza=verifiche_in_scadenza,
                               apparecchi_senza_verifica=apparecchi_senza_verifica,
                               scadenze_imminenti=scadenze_imminenti,
                               ultimi_interventi=ultimi_interventi,
                               chart_classificazione_json=json.dumps(chart_classificazione),
                               chart_costi_json=json.dumps(chart_costi),
                               chart_tipi_json=json.dumps(chart_tipi),
                               chart_verifiche_json=json.dumps(chart_verifiche))

    # ---------------------------------------------------------------------------
    # HTTP error handlers
    # ---------------------------------------------------------------------------
    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/error.html', code=404,
                               title='Pagina non trovata',
                               message='La risorsa richiesta non esiste.'), 404

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('errors/error.html', code=403,
                               title='Accesso negato',
                               message='Non hai i permessi per accedere a questa risorsa.'), 403

    @app.errorhandler(413)
    def too_large(e):
        return render_template('errors/error.html', code=413,
                               title='File troppo grande',
                               message='La dimensione massima consentita è 32 MB.'), 413

    @app.errorhandler(500)
    def server_error(e):
        logging.getLogger('medinventory').error(f'Errore 500: {e}')
        return render_template('errors/error.html', code=500,
                               title='Errore interno del server',
                               message='Si è verificato un errore imprevisto. Contatta l\'amministratore.'), 500

    # ---------------------------------------------------------------------------
    # Health check endpoint
    # ---------------------------------------------------------------------------
    @app.route('/api/health')
    def health():
        return {'status': 'ok', 'app': config.get('app_name', 'MedInventory')}

    # ---------------------------------------------------------------------------
    # Serve uploaded files
    # ---------------------------------------------------------------------------
    @app.route('/uploads/<path:filename>')
    @auth_login_required
    def uploaded_file(filename):
        import re
        from flask import send_from_directory, abort as _abort
        uploads_path = app.config['UPLOADS_PATH']
        resolved = os.path.realpath(os.path.join(uploads_path, filename))
        if not resolved.startswith(os.path.realpath(uploads_path) + os.sep):
            _abort(403)
        # Multi-tenant: verify caller has access to the struttura owning this file
        m = re.match(r'^strutture/(\d+)/', filename)
        if m:
            file_struttura_id = int(m.group(1))
            ruolo = g.user.get('ruolo')
            if ruolo != 'superadmin':
                user_struttura_id = getattr(g, 'struttura_id', None) or g.user.get('struttura_id')
                if user_struttura_id != file_struttura_id:
                    _abort(403)
        return send_from_directory(uploads_path, filename)

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    app = create_app()
    config = app.config['APP_CONFIG']

    # Start background scheduler (email monitoring, session cleanup, backups)
    from scheduler import init_scheduler
    scheduler = init_scheduler(app)

    print(f"\n  {config.get('app_name', 'MedInventory')} avviato")
    print(f"  http://{config.get('host', '0.0.0.0')}:{config.get('port', 5000)}")
    print(f"  by {config.get('organization', 'Studio Bergamaschi')}")
    print(f"  Scheduler attivo (email check ogni {config.get('email_check_interval_minutes', 15)} min)\n")

    try:
        app.run(
            host=config.get('host', '0.0.0.0'),
            port=config.get('port', 5000),
            debug=config.get('debug', False)
        )
    finally:
        from scheduler import stop_scheduler
        stop_scheduler()
