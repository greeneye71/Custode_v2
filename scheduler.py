"""
MedInventory - Background Scheduler
Runs periodic tasks in a background thread:
- Email monitoring (every N minutes, configurable)
- Expired session cleanup (every hour)
- Automatic backup (weekly, Sunday 03:00)
"""

import threading
import time
import logging
import sqlite3
from datetime import datetime

logger = logging.getLogger('medinventory.scheduler')


class BackgroundScheduler:
    """Simple interval-based scheduler running in a daemon thread."""

    def __init__(self, app):
        self.app = app
        self._stop_event = threading.Event()
        self._thread = None
        self._tasks = []

    def start(self):
        """Start the scheduler in a background thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Scheduler già in esecuzione.")
            return

        config = self.app.config['APP_CONFIG']
        email_interval = config.get('email_check_interval_minutes', 15) * 60  # in seconds

        # Register tasks with intervals (seconds)
        self._tasks = [
            {
                'name': 'email_check',
                'func': self._check_emails,
                'interval': email_interval,
                'last_run': 0,
            },
            {
                'name': 'session_cleanup',
                'func': self._cleanup_sessions,
                'interval': 3600,  # every hour
                'last_run': 0,
            },
            {
                'name': 'backup_check',
                'func': self._check_backup,
                'interval': 3600,  # check every hour, run weekly
                'last_run': 0,
            },
            {
                'name': 'deadline_alerts',
                'func': self._send_deadline_alerts,
                'interval': 86400,  # once a day
                'last_run': 0,
            },
        ]

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Scheduler avviato con successo.")

    def stop(self):
        """Stop the scheduler."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Scheduler fermato.")

    def _run(self):
        """Main scheduler loop."""
        # Wait a bit before starting (let the app fully initialize)
        time.sleep(10)

        while not self._stop_event.is_set():
            now = time.time()

            for task in self._tasks:
                if now - task['last_run'] >= task['interval']:
                    try:
                        logger.debug(f"Esecuzione task: {task['name']}")
                        task['func']()
                        task['last_run'] = now
                    except Exception as e:
                        logger.error(f"Errore nel task {task['name']}: {e}")

            # Sleep for 30 seconds between checks
            self._stop_event.wait(30)

    def _check_emails(self):
        """Run email check for all active configurations."""
        from email_monitor import check_all_emails
        try:
            check_all_emails(self.app)
        except Exception as e:
            logger.error(f"Errore controllo email: {e}")

    def _cleanup_sessions(self):
        """Remove expired sessions from the database."""
        db_path = self.app.config['DATABASE_PATH']
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.execute(
                "DELETE FROM sessioni WHERE expires_at < datetime('now')"
            )
            conn.commit()
            deleted = cursor.rowcount
            conn.close()
            if deleted > 0:
                logger.info(f"Eliminate {deleted} sessioni scadute.")
        except Exception as e:
            logger.error(f"Errore pulizia sessioni: {e}")

    def _send_deadline_alerts(self):
        """Invia digest email scadenze a ogni struttura attiva con email_notifiche configurata."""
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        with self.app.app_context():
            from models import query_all, get_struttura_config
            strutture = query_all(
                "SELECT * FROM strutture WHERE attiva=1 AND email_notifiche IS NOT NULL"
            )
            global_cfg = self.app.config.get('APP_CONFIG', {})

            for struttura in strutture:
                sid = struttura['id']
                frequenza = get_struttura_config(sid, 'report_frequenza') or 'settimanale'
                attivo = get_struttura_config(sid, 'report_schedulato_attivo') or '1'
                if attivo != '1':
                    continue
                if not self._is_digest_due(frequenza):
                    continue

                scadenze = query_all("""
                    SELECT ps.*, a.matricola, a.marca, a.modello, a.descrizione,
                           d.nome as divisione_nome
                    FROM prossime_scadenze ps
                    JOIN apparecchi a ON a.id = ps.apparecchio_id
                    JOIN divisioni d ON d.id = a.divisione_id
                    WHERE a.struttura_id = ?
                    AND ps.priorita IN ('scaduto', 'urgente', 'attenzione', 'avviso')
                    ORDER BY ps.priorita, ps.prossima_scadenza
                """, (sid,))

                if not scadenze:
                    continue

                self._invia_digest(struttura, scadenze, global_cfg)

    def _is_digest_due(self, frequenza):
        """Controlla se è il momento giusto per inviare il digest."""
        now = datetime.now()
        if frequenza == 'giornaliero':
            return now.hour == 7
        elif frequenza == 'settimanale':
            return now.weekday() == 0 and now.hour == 7  # lunedì alle 7:00
        elif frequenza == 'mensile':
            return now.day == 1 and now.hour == 7  # primo del mese alle 7:00
        return False

    def _invia_digest(self, struttura, scadenze, global_cfg):
        """Costruisce e invia l'email digest delle scadenze per una struttura."""
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from models import get_struttura_config

        sid = struttura['id']

        smtp_host = get_struttura_config(sid, 'smtp_host') or global_cfg.get('smtp_host', '')
        smtp_port = int(get_struttura_config(sid, 'smtp_port') or global_cfg.get('smtp_port', 587))
        smtp_user = get_struttura_config(sid, 'smtp_user') or global_cfg.get('smtp_user', '')
        smtp_pass_enc = get_struttura_config(sid, 'smtp_password_encrypted')
        smtp_pass = ''
        if smtp_pass_enc:
            try:
                import base64, hashlib
                from cryptography.fernet import Fernet
                key = global_cfg.get('encryption_key', '')
                if key:
                    fernet_key = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
                    smtp_pass = Fernet(fernet_key).decrypt(smtp_pass_enc.encode()).decode()
            except Exception as e:
                logger.warning(f"Impossibile decifrare smtp_password per struttura {struttura['nome']}: {e}")
        if not smtp_pass:
            smtp_pass = global_cfg.get('smtp_password', '')

        smtp_from = get_struttura_config(sid, 'smtp_from') or smtp_user
        use_tls = (get_struttura_config(sid, 'smtp_use_tls') or '1') == '1'

        if not smtp_host or not smtp_user:
            logger.warning(f"SMTP non configurato per struttura {struttura['nome']}, digest non inviato.")
            return

        priorita_labels = {
            'scaduto':    'SCADUTO',
            'urgente':    'URGENTE (<=7gg)',
            'attenzione': 'ATTENZIONE (<=15gg)',
            'avviso':     'AVVISO (<=30gg)',
        }
        righe = [f"Scadenzario — {struttura['nome']}", "=" * 40, ""]
        for priorita, label in priorita_labels.items():
            gruppo = [s for s in scadenze if s['priorita'] == priorita]
            if gruppo:
                righe.append(f"\n{label}")
                righe.append("-" * 30)
                for s in gruppo:
                    nome_app = s['descrizione'] or f"{s['marca']} {s['modello']}"
                    righe.append(
                        f"  {nome_app} (mat. {s['matricola']}) — {s['divisione_nome']} — "
                        f"scade: {s['prossima_scadenza']} ({s['giorni_rimasti']} gg)"
                    )
        corpo = "\n".join(righe)

        try:
            msg = MIMEMultipart()
            msg['From'] = smtp_from
            msg['To'] = struttura['email_notifiche']
            msg['Subject'] = f"Scadenzario {struttura['nome']} — {datetime.now().strftime('%d/%m/%Y')}"
            msg.attach(MIMEText(corpo, 'plain', 'utf-8'))

            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                if use_tls:
                    server.starttls()
                if smtp_user and smtp_pass:
                    server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_from, struttura['email_notifiche'], msg.as_string())
            logger.info(f"Digest inviato a {struttura['email_notifiche']} ({struttura['nome']})")
        except Exception as e:
            logger.error(f"Errore invio digest {struttura['nome']}: {e}")

    def _check_backup(self):
        """Check if a weekly backup is needed (Sunday 03:00)."""
        now = datetime.now()
        # Only run on Sunday (weekday 6) between 03:00 and 03:59
        if now.weekday() == 6 and now.hour == 3:
            try:
                from backup_service import create_backup
                config = self.app.config['APP_CONFIG']
                db_path = self.app.config['DATABASE_PATH']
                backups_path = self.app.config['BACKUPS_PATH']
                retention = config.get('backup_retention', 4)

                create_backup(db_path, backups_path, retention)
                logger.info("Backup settimanale completato.")
            except Exception as e:
                logger.error(f"Errore backup settimanale: {e}")


# Global scheduler instance
_scheduler = None


def init_scheduler(app):
    """Initialize and start the background scheduler."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()

    _scheduler = BackgroundScheduler(app)
    _scheduler.start()
    return _scheduler


def stop_scheduler():
    """Stop the background scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.stop()
        _scheduler = None
