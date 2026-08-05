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
                # Controlla ogni ora, non ogni 24: _is_digest_due richiede
                # l'ora esatta (7:00). Con interval=86400 il task si allineava
                # all'ora di avvio dell'app e la finestra non veniva mai colpita.
                'func': self._send_deadline_alerts,
                'interval': 3600,
                'last_run': 0,
            },
            {
                'name': 'cleanup_login_attempts',
                'func': self._cleanup_login_attempts,
                'interval': 86400,  # una volta al giorno
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
        """Rimuove le sessioni scadute dal database."""
        db_path = self.app.config['DATABASE_PATH']
        try:
            conn = sqlite3.connect(db_path)
            try:
                cursor = conn.execute(
                    "DELETE FROM sessioni WHERE expires_at < datetime('now')"
                )
                conn.commit()
                deleted = cursor.rowcount
                if deleted > 0:
                    logger.info(f"Eliminate {deleted} sessioni scadute.")
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Errore pulizia sessioni: {e}")

    def _cleanup_login_attempts(self):
        """Elimina i tentativi di login più vecchi di 24 ore.

        Senza questo cleanup la tabella login_attempts cresce indefinitamente
        perché l'app inserisce un record per ogni tentativo (riuscito o fallito)
        ma non li elimina mai in modo sistematico (rimuove solo quelli per IP
        dopo un login riuscito).
        """
        db_path = self.app.config['DATABASE_PATH']
        try:
            conn = sqlite3.connect(db_path)
            try:
                cursor = conn.execute(
                    "DELETE FROM login_attempts WHERE created_at < datetime('now', '-1 day')"
                )
                conn.commit()
                deleted = cursor.rowcount
                if deleted > 0:
                    logger.info(f"Eliminati {deleted} tentativi di login obsoleti (>24h).")
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Errore pulizia login_attempts: {e}")

    def _send_deadline_alerts(self):
        """Avvisi di scadenza alle strutture che li hanno chiesti.

        Un interruttore e un formato al posto dei due percorsi separati della
        2.6.1 (report_schedulato_attivo per il testo, report_pdf_attivo per il
        PDF): la seconda chiave non veniva scritta da nessuna parte, quindi il
        report PDF non e' mai stato raggiungibile.
        """
        with self.app.app_context():
            from models import query_all, get_struttura_config
            strutture = query_all(
                "SELECT * FROM strutture WHERE attiva=1 AND email_notifiche IS NOT NULL"
            )

            for struttura in strutture:
                sid = struttura['id']
                if get_struttura_config(sid, 'avvisi_scadenza_attivi', '') != '1':
                    continue
                if not self._is_digest_due(get_struttura_config(sid, 'report_frequenza',
                                                                'settimanale')):
                    continue

                formato = get_struttura_config(sid, 'avvisi_scadenza_formato', 'testo')
                try:
                    if formato == 'pdf':
                        self._invia_report_pdf(struttura)
                    else:
                        self._invia_digest(struttura)
                except Exception as e:
                    # Gira in un thread di fondo: un'eccezione qui fermerebbe
                    # gli avvisi di tutte le strutture successive, e nessuno la
                    # vedrebbe se non nel log.
                    logger.error(f"Errore avvisi struttura {struttura['nome']}: {e}")

    def _config_smtp(self):
        """I parametri del server di posta, solo di sistema.

        Fino alla 2.6.1 ognuno di questi valori aveva un gemello in
        strutture_config e vinceva quello. Un server di posta pero' e'
        infrastruttura del deployment, non un dato della clinica: averlo per
        struttura moltiplicava le credenziali da tenere aggiornate e se le
        portava dietro negli archivi esportati.

        smtp_use_tls arriva dal JSON globale come booleano (true), non come la
        stringa '1' che si usava in strutture_config: si accettano entrambi.
        """
        cfg = self.app.config.get('APP_CONFIG') or {}
        tls = cfg.get('smtp_use_tls', True)
        return {
            'host': cfg.get('smtp_host', ''),
            'porta': int(cfg.get('smtp_port') or 587),
            'utente': cfg.get('smtp_user', ''),
            'password': cfg.get('smtp_password', ''),
            # Il mittente e' unico per tutte le strutture: chi riceve capisce
            # di quale si tratta dal messaggio, non dall'indirizzo.
            'mittente': cfg.get('smtp_user', ''),
            'usa_tls': str(tls).lower() not in ('0', 'false', ''),
        }

    def _invia(self, struttura, messaggio):
        """Spedisce un messaggio gia' pronto. True se e' partito."""
        import smtplib

        smtp = self._config_smtp()
        if not smtp['host'] or not smtp['utente']:
            logger.warning("SMTP di sistema non configurato: avviso non inviato "
                           f"a {struttura['nome']}.")
            return False

        messaggio['From'] = smtp['mittente']
        messaggio['To'] = struttura['email_notifiche']
        try:
            with smtplib.SMTP(smtp['host'], smtp['porta'], timeout=15) as server:
                if smtp['usa_tls']:
                    server.starttls()
                if smtp['utente'] and smtp['password']:
                    server.login(smtp['utente'], smtp['password'])
                server.sendmail(smtp['mittente'], struttura['email_notifiche'],
                                messaggio.as_string())
            logger.info(f"Avviso inviato a {struttura['email_notifiche']} "
                        f"({struttura['nome']})")
            return True
        except Exception as e:
            logger.error(f"Errore invio avviso {struttura['nome']}: {e}")
            return False

    def _invia_report_pdf(self, struttura):
        """Genera il report PDF delle scadenze e lo allega."""
        import os
        import tempfile
        from email.mime.application import MIMEApplication
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from export_service import genera_report_scadenze_pdf

        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            percorso = tmp.name
        try:
            genera_report_scadenze_pdf(struttura_id=struttura['id'], output_path=percorso)

            msg = MIMEMultipart()
            msg['Subject'] = (f"Report scadenze {struttura['nome']} — "
                              f"{datetime.now().strftime('%d/%m/%Y')}")
            # Il corpo nomina la struttura: il mittente e' lo stesso per tutte,
            # e senza aprire l'allegato non ci sarebbe altro modo di capirlo.
            msg.attach(MIMEText(
                f"In allegato il report periodico delle scadenze di {struttura['nome']}.",
                'plain', 'utf-8'))
            with open(percorso, 'rb') as f:
                allegato = MIMEApplication(f.read(), _subtype='pdf')
            allegato.add_header('Content-Disposition', 'attachment',
                                filename=f"scadenze_{struttura['codice']}.pdf")
            msg.attach(allegato)
            self._invia(struttura, msg)
        finally:
            if os.path.exists(percorso):
                os.remove(percorso)

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

    def _invia_digest(self, struttura):
        """Il digest di testo delle scadenze della struttura.

        Ogni riga porta la divisione, e l'intestazione porta la struttura: un
        avviso di scadenza attraversa piu' divisioni, quindi nominarne una sola
        nell'oggetto sarebbe falso, ma il destinatario deve comunque poter
        capire di chi si parla — il mittente non glielo dice piu'.
        """
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from models import query_all

        scadenze = query_all("""
            SELECT ps.*, a.matricola, a.marca, a.modello, a.descrizione,
                   d.nome as divisione_nome
            FROM prossime_scadenze ps
            JOIN apparecchi a ON a.id = ps.apparecchio_id
            JOIN divisioni d ON d.id = a.divisione_id
            WHERE a.struttura_id = ?
            AND ps.priorita IN ('scaduto', 'urgente', 'attenzione', 'avviso')
            ORDER BY ps.priorita, ps.prossima_scadenza
        """, (struttura['id'],))
        if not scadenze:
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

        msg = MIMEMultipart()
        msg['Subject'] = (f"Scadenzario {struttura['nome']} — "
                          f"{datetime.now().strftime('%d/%m/%Y')}")
        msg.attach(MIMEText("\n".join(righe), 'plain', 'utf-8'))
        self._invia(struttura, msg)

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
