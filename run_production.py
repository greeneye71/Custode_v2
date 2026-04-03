"""
MedInventory - Production Server
Runs the Flask application with Waitress WSGI server.
Used for production deployment and Windows service.
"""

import sys
import os
import logging
from logging.handlers import RotatingFileHandler

# Force UTF-8 I/O on Windows (avoids 'ascii' codec errors with Italian text / Anthropic SDK)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Ensure the app directory is in sys.path
app_dir = os.path.dirname(os.path.abspath(__file__))
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

from app import create_app


def setup_logging(log_dir):
    """Configure production logging with rotation."""
    os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Main application log
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'medinventory.log'),
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    # Error-only log
    error_handler = RotatingFileHandler(
        os.path.join(log_dir, 'errors.log'),
        maxBytes=2 * 1024 * 1024,  # 2 MB
        backupCount=3,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    # Apply to root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(error_handler)

    # Also log to console
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)


def main():
    """Start the production server."""
    app = create_app()
    config = app.config['APP_CONFIG']

    # Setup logging
    log_dir = os.path.join(app_dir, 'logs')
    setup_logging(log_dir)

    logger = logging.getLogger('medinventory')
    logger.info("=" * 60)
    logger.info(f"  {config.get('app_name', 'MedInventory')} - Avvio in produzione")
    logger.info(f"  by {config.get('organization', 'Studio Bergamaschi')}")
    logger.info("=" * 60)

    # Start background scheduler
    from scheduler import init_scheduler
    scheduler = init_scheduler(app)
    logger.info(f"Scheduler avviato (email check ogni {config.get('email_check_interval_minutes', 15)} min)")

    cloudflare_mode = config.get('cloudflare_mode', False)

    # In modalità Cloudflare Tunnel il server deve ascoltare solo su localhost:
    # cloudflared si connette a 127.0.0.1 e nessuna porta va aperta sul router.
    # Ascoltare su 0.0.0.0 esporrebbe il server HTTP (non cifrato) anche
    # all'esterno, permettendo di aggirare il tunnel.
    if cloudflare_mode:
        host_default = '127.0.0.1'
    else:
        host_default = '0.0.0.0'

    host = config.get('host', host_default)
    port = config.get('port', 5000)

    if cloudflare_mode and host == '0.0.0.0':
        logger.warning(
            "ATTENZIONE: cloudflare_mode=true ma host='0.0.0.0'. "
            "Il server è raggiungibile su tutte le interfacce, incluse quelle "
            "esterne al tunnel. Impostare host='127.0.0.1' per una configurazione sicura."
        )

    # url_scheme informa Waitress del protocollo percepito dai client.
    # Con ProxyFix(x_proto=1) questo valore è già sovrascritto da X-Forwarded-Proto,
    # ma impostarlo correttamente evita artefatti nei log di Waitress.
    url_scheme = 'https' if config.get('force_https', False) else 'http'

    proto_label = 'https' if cloudflare_mode else 'http'
    logger.info(f"Server in ascolto su {proto_label}://{host}:{port}")
    if cloudflare_mode:
        logger.info("Modalità Cloudflare Tunnel attiva — accesso esterno via tunnel cifrato")

    try:
        from waitress import serve
        serve(
            app,
            host=host,
            port=port,
            threads=8,
            channel_timeout=120,
            url_scheme=url_scheme,
        )
    except KeyboardInterrupt:
        logger.info("Arresto richiesto dall'utente.")
    except Exception as e:
        logger.error(f"Errore server: {e}")
    finally:
        from scheduler import stop_scheduler
        stop_scheduler()
        logger.info("Server arrestato.")


if __name__ == '__main__':
    main()
