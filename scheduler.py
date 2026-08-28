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

# A livello di modulo (non dentro il metodo, come le altre import locali di
# questo file) cosi' i test possono sostituirla con monkeypatch.setattr
# facendo scheduler.invia = ... invece di rimettere mano a posta.py, che e'
# codice di invio condiviso con altri flussi.
from posta import invia

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
                'name': 'impianti_alerts',
                # Ogni ora, senza finestra oraria fissa: la tabella
                # impianti_avvisi_inviati impedisce i doppioni, quindi ogni ora
                # successiva e' un tentativo ripetuto gratis se l'SMTP era giu'.
                'func': self._send_impianti_alerts,
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
                    # last_run si aggiorna comunque: fino alla 2.7.1 stava
                    # dentro il try, dopo la chiamata, quindi un task che
                    # sollevava veniva ritentato ogni 30 secondi per sempre —
                    # riempiendo il log e, per il controllo email, ribussando
                    # all'IMAP di continuo.
                    task['last_run'] = now
                    try:
                        logger.debug(f"Esecuzione task: {task['name']}")
                        task['func']()
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
            from models import query_all, get_struttura_config, set_struttura_config
            strutture = query_all(
                "SELECT * FROM strutture WHERE attiva=1 AND email_notifiche IS NOT NULL"
            )

            for struttura in strutture:
                sid = struttura['id']
                if get_struttura_config(sid, 'avvisi_scadenza_attivi', '') != '1':
                    continue
                frequenza = get_struttura_config(sid, 'report_frequenza', 'settimanale')
                periodo = self._periodo_digest(frequenza)
                if periodo is None:
                    continue
                # Registrato in strutture_config e non in memoria: un riavvio
                # dell'applicazione non deve far ripartire gli avvisi già
                # inviati per questo periodo.
                if get_struttura_config(sid, 'ultimo_avviso_scadenze', '') == periodo:
                    continue

                formato = get_struttura_config(sid, 'avvisi_scadenza_formato', 'testo')
                try:
                    if formato == 'pdf':
                        esito = self._invia_report_pdf(struttura)
                    else:
                        esito = self._invia_digest(struttura)
                    # Il periodo si segna solo se l'avviso e' davvero partito
                    # (o se non c'era nulla da mandare). Fino alla 2.8.0 lo si
                    # segnava comunque: un SMTP irraggiungibile per un'ora
                    # bruciava silenziosamente l'avviso di tutto il periodo,
                    # senza che nessuno se ne accorgesse.
                    if esito in ('inviato', 'niente_da_inviare'):
                        set_struttura_config(sid, 'ultimo_avviso_scadenze', periodo)
                    else:
                        logger.error(
                            f"Avviso scadenze non partito per {struttura['nome']}: "
                            f"il periodo {periodo} resta da inviare.")
                except Exception as e:
                    # Gira in un thread di fondo: un'eccezione qui fermerebbe
                    # gli avvisi di tutte le strutture successive, e nessuno la
                    # vedrebbe se non nel log.
                    logger.error(f"Errore avvisi struttura {struttura['nome']}: {e}")

    def _send_impianti_alerts(self):
        """Avvisi di scadenza degli impianti, un invio per indirizzo.

        Non c'e' digest: ogni verifica ha destinatari propri (il manutentore
        della riga, l'indirizzo extra del perito) e un messaggio unico non
        potrebbe rispettarli. Un invio per indirizzo e non un unico invio con
        i destinatari uniti da virgola: smtplib.SMTP.sendmail (dentro
        posta.invia) tratta una stringa come UN SOLO destinatario di envelope,
        quindi "a@x.it, b@y.it" diventa un RCPT TO invalido per RFC 5321 e i
        server veri lo rifiutano.
        """
        with self.app.app_context():
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            from models import query_all, get_struttura_config
            import impianti_service

            if datetime.now().hour < 7:
                return
            if not self._config_smtp()['host']:
                logger.warning("SMTP di sistema non configurato: avvisi "
                               "impianti non inviati.")
                return

            strutture = query_all("SELECT * FROM strutture WHERE attiva = 1")
            for struttura in strutture:
                sid = struttura['id']
                if get_struttura_config(sid, 'avvisi_impianti_attivi', '1') != '1':
                    continue
                try:
                    for avviso in impianti_service.avvisi_da_inviare(sid):
                        indirizzi = impianti_service.destinatari(struttura, avviso)
                        if not indirizzi:
                            # Configurazione incompleta, non un guasto: la
                            # struttura non ha indicato nessun destinatario.
                            logger.info(
                                f"Nessun destinatario per la scadenza "
                                f"{avviso['scadenza_id']} ({struttura['nome']})")
                            continue
                        oggetto, testo = impianti_service.corpo_avviso(
                            struttura, avviso)

                        # Un messaggio nuovo per indirizzo: posta.invia()
                        # scrive dentro From/To, e Message.__setitem__
                        # accumula invece di sostituire, quindi riusare lo
                        # stesso MIMEMultipart fra piu' invii duplicherebbe
                        # gli header dal secondo invio in poi.
                        falliti = []
                        raggiunti = []
                        for indirizzo in indirizzi:
                            msg = MIMEMultipart()
                            msg['Subject'] = oggetto
                            msg.attach(MIMEText(testo, 'plain', 'utf-8'))
                            if invia(self.app.config.get('APP_CONFIG'),
                                     indirizzo, msg):
                                raggiunti.append(indirizzo)
                            else:
                                falliti.append(indirizzo)

                        if falliti:
                            logger.error(
                                f"Avviso impianto non partito per "
                                f"{falliti} (scadenza {avviso['scadenza_id']}, "
                                f"{struttura['nome']})")

                        # Si registra se almeno un invio e' partito: un
                        # indirizzo permanentemente rotto non deve bloccare
                        # per sempre il riavviso a quelli buoni (altrimenti il
                        # ciclo orario ritenta all'infinito), al prezzo che
                        # quell'indirizzo perda la notifica in silenzio — per
                        # questo il fallimento e' loggato sopra a livello
                        # ERROR, cosi' l'operatore vede quale indirizzo e'
                        # rotto. Si registra e si logga chi e' stato
                        # raggiunto davvero, non la lista di partenza: con un
                        # invio parzialmente fallito la colonna 'destinatari'
                        # e' l'unica traccia di chi ha ricevuto l'avviso.
                        if raggiunti:
                            for soglia in avviso['soglie_coperte']:
                                impianti_service.registra_avviso(
                                    avviso['scadenza_id'], soglia,
                                    avviso['prossima_scadenza'], raggiunti)
                            logger.info(f"Avviso impianto inviato a "
                                        f"{raggiunti} ({struttura['nome']})")
                except Exception as e:
                    logger.error(f"Errore avvisi impianti struttura "
                                 f"{struttura['nome']}: {e}")

    def _config_smtp(self):
        """I parametri del server di posta, solo di sistema (vedi posta.py)."""
        from posta import parametri
        return parametri(self.app.config.get('APP_CONFIG'))

    def _invia(self, struttura, messaggio):
        """Spedisce un messaggio gia' pronto. True se e' partito.

        L'invio vero sta in posta.py, unico posto da cui parte la posta; qui
        restano le righe di log, che nominano la struttura.
        """
        from posta import invia

        if not self._config_smtp()['host']:
            logger.warning("SMTP di sistema non configurato: avviso non inviato "
                           f"a {struttura['nome']}.")
            return False

        if invia(self.app.config.get('APP_CONFIG'), struttura['email_notifiche'],
                 messaggio):
            logger.info(f"Avviso inviato a {struttura['email_notifiche']} "
                        f"({struttura['nome']})")
            return True
        logger.error(f"Avviso non partito per {struttura['nome']}.")
        return False

    def _invia_report_pdf(self, struttura):
        """Genera il report PDF delle scadenze e lo allega.

        Restituisce 'inviato' o 'fallito': chi chiama segna il periodo come
        gia' avvisato solo nel primo caso.
        """
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
            return 'inviato' if self._invia(struttura, msg) else 'fallito'
        finally:
            if os.path.exists(percorso):
                os.remove(percorso)

    def _periodo_digest(self, frequenza, now=None):
        """La chiave del periodo corrente, o None se il momento non e' ancora passato.

        Fino alla 2.7.1 qui si confrontava l'ora esatta (now.hour == 7) su un
        task che gira ogni 3600 secondi *di orologio*: il timer deriva, quindi
        due controlli potevano cadere nella stessa ora (digest doppio) o
        saltarla del tutto (digest mai inviato per quel giorno). Ora si
        risponde a una domanda che non dipende dall'istante del controllo:
        "il momento di questo periodo e' passato?". Chi chiama confronta la
        chiave con l'ultimo invio registrato e manda una volta sola.
        """
        now = now or datetime.now()
        if frequenza == 'giornaliero':
            if now.hour < 7:
                return None
            return now.strftime('%Y-%m-%d')
        if frequenza == 'settimanale':
            # Lunedì alle 7:00. Se l'applicazione era ferma quel lunedì,
            # l'invio si recupera nei giorni successivi della stessa settimana
            # invece di essere perso.
            if now.weekday() == 0 and now.hour < 7:
                return None
            anno, settimana, _ = now.isocalendar()
            return f'{anno}-W{settimana:02d}'
        if frequenza == 'mensile':
            if now.day == 1 and now.hour < 7:
                return None
            return now.strftime('%Y-%m')
        return None

    def _invia_digest(self, struttura):
        """Il digest di testo delle scadenze della struttura.

        Ogni riga porta la divisione, e l'intestazione porta la struttura: un
        avviso di scadenza attraversa piu' divisioni, quindi nominarne una sola
        nell'oggetto sarebbe falso, ma il destinatario deve comunque poter
        capire di chi si parla — il mittente non glielo dice piu'.

        Restituisce 'inviato', 'niente_da_inviare' oppure 'fallito'.
        """
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from models import query_all, get_struttura_config

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

        # Interrogato prima del return anticipato: una struttura puo' non
        # avere apparecchi in scadenza ma avere impianti, e in quel caso il
        # digest va comunque mandato.
        impianti = []
        if get_struttura_config(struttura['id'], 'avvisi_impianti_attivi',
                                '1') == '1':
            impianti = query_all("""
                SELECT v.*, d.nome as divisione_nome
                FROM prossime_scadenze_impianti v
                LEFT JOIN divisioni d ON d.id = v.divisione_id
                WHERE v.struttura_id = ?
                  AND v.priorita IN ('scaduto', 'urgente', 'attenzione', 'avviso')
                ORDER BY v.prossima_scadenza
            """, (struttura['id'],))

        if not scadenze and not impianti:
            return 'niente_da_inviare'

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

        if impianti:
            righe.append("\nIMPIANTI")
            righe.append("-" * 30)
            for i in impianti:
                righe.append(
                    f"  {i['impianto_nome']} — {i['scadenza_nome']} — "
                    f"{i['divisione_nome'] or '-'} — scade: "
                    f"{i['prossima_scadenza']} ({i['giorni_rimasti']} gg)")

        msg = MIMEMultipart()
        msg['Subject'] = (f"Scadenzario {struttura['nome']} — "
                          f"{datetime.now().strftime('%d/%m/%Y')}")
        msg.attach(MIMEText("\n".join(righe), 'plain', 'utf-8'))
        return 'inviato' if self._invia(struttura, msg) else 'fallito'

    def _eta_ultimo_backup(self, backups_path):
        """Giorni trascorsi dal backup piu' recente, o None se non ce ne sono."""
        import os
        try:
            file_backup = [
                os.path.join(backups_path, n) for n in os.listdir(backups_path)
                if n.startswith('medinventory_backup_') and n.endswith('.sqlite')
            ]
        except OSError:
            return None
        if not file_backup:
            return None
        piu_recente = max(os.path.getmtime(f) for f in file_backup)
        return (time.time() - piu_recente) / 86400

    def _check_backup(self):
        """Backup settimanale, la domenica dalle 03:00.

        Fino alla 2.7.1 la condizione era now.hour == 3 su un task che gira
        ogni 3600 secondi *di orologio*: il timer deriva, quindi la finestra
        poteva essere saltata (nessun backup per una settimana intera) o
        colpita due volte. Ora conta l'eta' del backup piu' recente sul disco,
        che sopravvive anche a un riavvio e tiene conto dei backup manuali.
        """
        now = datetime.now()
        backups_path = self.app.config['BACKUPS_PATH']
        eta = self._eta_ultimo_backup(backups_path)

        if eta is not None:
            if eta < 6:
                return
            # Fuori dalla finestra della domenica si aspetta ancora un po':
            # oltre gli otto giorni pero' si recupera comunque, altrimenti
            # un'applicazione spenta la domenica resterebbe senza backup.
            if not (now.weekday() == 6 and now.hour >= 3) and eta < 8:
                return

        try:
            from backup_service import create_backup
            config = self.app.config['APP_CONFIG']
            db_path = self.app.config['DATABASE_PATH']
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
