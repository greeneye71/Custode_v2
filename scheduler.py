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
        """Send daily email alert for expired or urgent deadlines."""
        import smtplib
        from email.mime.text import MIMEText

        config = self.app.config['APP_CONFIG']

        if not config.get('alert_email_enabled'):
            return

        alert_to = config.get('alert_email_to', '').strip()
        smtp_host = config.get('smtp_host', '').strip()
        if not alert_to or not smtp_host:
            logger.warning("Alert email abilitata ma SMTP non configurato.")
            return

        db_path = self.app.config['DATABASE_PATH']
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """SELECT ps.*, d.nome as divisione_nome
                   FROM prossime_scadenze ps
                   LEFT JOIN divisioni d ON ps.divisione_id = d.id
                   WHERE ps.priorita IN ('scaduto', 'urgente')
                   ORDER BY ps.prossima_scadenza ASC"""
            )
            scadenze = cursor.fetchall()
            conn.close()
        except Exception as e:
            logger.error(f"Errore query scadenze per alert: {e}")
            return

        if not scadenze:
            logger.debug("Nessuna scadenza urgente, alert non inviata.")
            return

        app_name = config.get('app_name', 'MedInventory')
        structure = config.get('structure_name', '')
        header = f"{app_name}{' - ' + structure if structure else ''}"
        now_str = datetime.now().strftime('%d/%m/%Y %H:%M')

        lines = [
            f"{header}",
            f"Riepilogo scadenze urgenti del {now_str}",
            "=" * 50,
            "",
            f"Trovate {len(scadenze)} scadenze critiche:",
            "",
        ]
        for s in scadenze:
            tipo_label = "Manutenzione" if s['tipo_record'] == 'manutenzione' else "Verifica el."
            div_label = s['divisione_nome'] or 'N.D.'
            lines.append(
                f"  [{s['priorita'].upper()}] {s['marca']} {s['modello']} ({s['matricola']})"
            )
            lines.append(
                f"         {tipo_label} - Scadenza: {s['prossima_scadenza']} "
                f"({s['giorni_rimasti']}gg) - Div.: {div_label}"
            )
            lines.append("")

        body = "\n".join(lines)
        subject = f"[{app_name}] {len(scadenze)} scadenze urgenti"

        smtp_port = config.get('smtp_port', 587)
        smtp_user = config.get('smtp_user', '').strip()
        smtp_password = config.get('smtp_password', '').strip()
        smtp_use_tls = config.get('smtp_use_tls', True)
        from_addr = smtp_user if smtp_user else f"noreply@{smtp_host}"

        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = from_addr
        msg['To'] = alert_to

        try:
            if smtp_use_tls:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15)

            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)

            server.sendmail(from_addr, [alert_to], msg.as_string())
            server.quit()
            logger.info(f"Alert email inviata a {alert_to}: {len(scadenze)} scadenze.")
        except Exception as e:
            logger.error(f"Errore invio alert email: {e}")

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
