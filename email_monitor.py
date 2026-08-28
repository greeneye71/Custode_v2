"""
MedInventory - Email Monitor
IMAP email monitoring for automatic maintenance report (verbale) import.
Checks configured email accounts for PDF attachments, parses them with AI,
and creates maintenance records or queues them for manual review.
"""

import os
import email
import imaplib
import base64
import hashlib
import json
import tempfile
import logging
import uuid
import traceback
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename

logger = logging.getLogger('medinventory.email')


def _get_fernet(encryption_key):
    """Deriva la chiave Fernet da encryption_key usando SHA-256 (compatibile con scheduler.py)."""
    from cryptography.fernet import Fernet
    fernet_key = base64.urlsafe_b64encode(hashlib.sha256(encryption_key.encode()).digest())
    return Fernet(fernet_key)


def get_fernet(config):
    """Get Fernet cipher from app config."""
    key = config.get('encryption_key', config['secret_key'])
    return _get_fernet(key)


def decrypt_password(encrypted_password, config):
    """Decrypt an IMAP password stored in the database."""
    f = get_fernet(config)
    return f.decrypt(encrypted_password.encode()).decode()


def _struttura_unica(db_path):
    """Id dell'unica struttura attiva, o None se ce n'è più di una (o nessuna)."""
    import sqlite3
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            rows = conn.execute(
                "SELECT id FROM strutture WHERE attiva = 1 LIMIT 2").fetchall()
        finally:
            conn.close()
        return rows[0][0] if len(rows) == 1 else None
    except Exception as e:
        logger.warning(f"Impossibile determinare la struttura per l'import email: {e}")
        return None


def check_emails_for_division(email_cfg, app_config, db_path):
    """
    Check IMAP mailbox for a single division's email config.
    Downloads PDF attachments and processes them with AI.

    Args:
        email_cfg: dict from email_config table
        app_config: application config dict
        db_path: path to the SQLite database
    """
    import sqlite3
    from ai_service import extract_text_from_file, parse_verbale_with_ai

    account = email_cfg['email_account']
    server = email_cfg['imap_server']
    port = email_cfg.get('imap_port', 993)
    divisione_id = email_cfg.get('divisione_id')

    # Password: either plaintext (from config.json) or encrypted (legacy DB record)
    if 'password' in email_cfg:
        password = email_cfg['password']
    else:
        try:
            password = decrypt_password(email_cfg['email_password_encrypted'], app_config)
        except Exception as e:
            logger.error(f"Errore decrittazione password per {account}: {e}")
            return

    from ai_service import get_ai_config
    struttura_id = email_cfg.get('struttura_id')
    ai_cfg = get_ai_config(struttura_id=struttura_id, config=app_config)
    api_key = ai_cfg['api_key']
    ai_model = ai_cfg['model_email']

    if not api_key:
        logger.warning("Chiave API Anthropic non configurata, skip analisi email.")
        return

    # Connect to IMAP
    mail = None
    try:
        use_ssl = email_cfg.get('imap_ssl', True)
        if use_ssl:
            mail = imaplib.IMAP4_SSL(server, port, timeout=30)
        else:
            mail = imaplib.IMAP4(server, port, timeout=30)
            mail.starttls()
        mail.login(account, password)
        mail.select('INBOX')

        # Search for UNSEEN emails
        status, messages = mail.search(None, 'UNSEEN')
        if status != 'OK' or not messages[0]:
            logger.info(f"Nessuna nuova email per {account}")
            if email_cfg.get('id'):
                _update_ultima_verifica(db_path, email_cfg['id'])
            return

        msg_ids = messages[0].split()
        logger.info(f"Trovate {len(msg_ids)} nuove email per {account}")

        # Gli allegati sostano sotto uploads/strutture/<id>/email/ come tutto
        # il resto: uploads/email/ era fuori dal perimetro del tenant, e
        # /uploads/<path> isola soltanto i percorsi che iniziano per strutture/.
        from models import upload_subdir as _upload_subdir
        _uploads_base = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(db_path)), '..', 'uploads')
        )
        uploads_dir, uploads_rel = _upload_subdir(
            'email', struttura_id,
            uploads_base=_uploads_base,
            single_struttura=(app_config or {}).get('single_struttura', False)
        )

        for msg_id in msg_ids:
            try:
                _process_email(
                    mail, msg_id, divisione_id, api_key, ai_model,
                    uploads_dir, db_path, account, app_config=app_config,
                    struttura_id=struttura_id, uploads_rel=uploads_rel
                )
            except Exception as e:
                # Il messaggio resta UNSEEN e viene ritentato al giro dopo:
                # la ricerca e' su UNSEEN, e con il vecchio fetch RFC822 —
                # che segna \Seen da solo — un errore qui perdeva il verbale.
                logger.error(f"Errore processando email {msg_id} per {account}: {e}")
                continue
            _segna_letta(mail, msg_id, account)

        if email_cfg.get('id'):
            _update_ultima_verifica(db_path, email_cfg['id'])

    except imaplib.IMAP4.error as e:
        logger.error(f"Errore IMAP per {account}: {e}")
    except Exception as e:
        logger.error(f"Errore generico email per {account}: {e}")
    finally:
        if mail:
            try:
                mail.close()
                mail.logout()
            except Exception:
                pass


def _segna_letta(mail, msg_id, account):
    """Marca il messaggio come letto dopo che e' stato elaborato."""
    try:
        mail.store(msg_id, '+FLAGS', r'\Seen')
    except Exception as e:
        logger.warning(f"Impossibile segnare come letta l'email {msg_id} di {account}: {e}")


def _process_email(mail, msg_id, divisione_id, api_key, ai_model, uploads_dir, db_path, account, app_config=None, struttura_id=None, uploads_rel='email'):
    """Process a single email message: extract PDF attachments and analyze."""
    import sqlite3
    from ai_service import parse_verbale_with_ai, classify_email_document_type, analyze_verifiche_with_ai

    # BODY.PEEK[] non tocca il flag \Seen: e' chi chiama a segnare il
    # messaggio come letto, e solo se l'elaborazione e' arrivata in fondo.
    status, data = mail.fetch(msg_id, '(BODY.PEEK[])')
    # Un errore qui deve *sollevare*: chi chiama segna il messaggio come letto
    # subito dopo il ritorno, e un return silenzioso su un FETCH fallito
    # bruciava il messaggio senza averlo mai letto davvero.
    if status != 'OK':
        raise RuntimeError(f"FETCH non riuscito per l'email {msg_id}: {status}")
    if not data or not data[0] or not isinstance(data[0], (tuple, list)) or len(data[0]) < 2:
        raise RuntimeError(f"Risposta FETCH incompleta per l'email {msg_id}")

    raw_email = data[0][1]
    msg = email.message_from_bytes(raw_email)

    subject = _decode_header(msg['Subject']) or '(nessun oggetto)'
    sender = _decode_header(msg['From']) or '(sconosciuto)'
    date_str = msg['Date'] or ''

    logger.info(f"Processando email: {subject} da {sender}")

    # Find PDF attachments
    pdf_attachments = []
    for part in msg.walk():
        content_type = part.get_content_type()
        filename = part.get_filename()

        if filename:
            filename = _decode_header(filename)

        if content_type == 'application/pdf' or (filename and filename.lower().endswith('.pdf')):
            pdf_data = part.get_payload(decode=True)
            if pdf_data:
                pdf_attachments.append({
                    'filename': filename or 'allegato.pdf',
                    'data': pdf_data
                })

    if not pdf_attachments:
        logger.info(f"Nessun PDF allegato in: {subject}")
        return

    # Process each PDF
    for idx, att in enumerate(pdf_attachments):
        safe_name = None
        try:
            # Save PDF to disk con nome sicuro (previene path traversal)
            safe_base = secure_filename(att.get('filename', '') or 'allegato.pdf')
            if not safe_base or safe_base == '.pdf':
                safe_base = 'allegato.pdf'
            # Token casuale e non solo timestamp+indice: due allegati con lo
            # stesso nome arrivati nello stesso secondo si sovrascrivevano, e
            # il primo verbale spariva dal disco restando citato a database.
            safe_name = f"{int(datetime.now().timestamp())}_{uuid.uuid4().hex[:8]}_{safe_base}"
            pdf_path = os.path.join(uploads_dir, safe_name)
            with open(pdf_path, 'wb') as f:
                f.write(att['data'])

            # Extract text from PDF (may return empty for scanned/image PDFs or on encoding errors)
            from ai_service import extract_from_pdf
            try:
                pdf_text = extract_from_pdf(pdf_path)
            except Exception as pdfplumber_err:
                err_msg = str(pdfplumber_err).encode('ascii', errors='replace').decode('ascii')
                logger.warning(f"pdfplumber error su {att['filename']}: {err_msg}")
                pdf_text = ''
            scanned_pdf = not pdf_text or len(pdf_text.strip()) < 20

            if scanned_pdf:
                logger.info(f"PDF scansionato rilevato: {att['filename']} - uso API documento Claude")

            # Classifica tipo documento
            if scanned_pdf:
                from ai_service import classify_email_document_type_from_pdf_document
                doc_type = classify_email_document_type_from_pdf_document(pdf_path, api_key, ai_model, config=app_config, struttura_id=struttura_id)
            else:
                doc_type = classify_email_document_type(pdf_text, api_key, ai_model, config=app_config, struttura_id=struttura_id)
            logger.info(f"Tipo documento rilevato: {doc_type} per {att['filename']}")

            if doc_type == 'verifica_elettrica':
                # Branch verifiche di sicurezza elettrica
                if scanned_pdf:
                    from ai_service import analyze_verifiche_from_pdf_document
                    items, ai_response = analyze_verifiche_from_pdf_document(pdf_path, api_key, ai_model, config=app_config, struttura_id=struttura_id)
                else:
                    items, ai_response = analyze_verifiche_with_ai(pdf_text, api_key, ai_model, config=app_config, struttura_id=struttura_id)
                apparecchio_id = None
                tipo_import_value = 'verifica_elettrica'
                righe_preview = []
                verifiche_importate = 0

                conn = sqlite3.connect(db_path, timeout=10)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
                try:
                    for item in items:
                        matricola = (item.get('matricola') or '').strip()
                        item_app_id = _find_apparecchio(
                            conn, matricola, divisione_id, struttura_id=struttura_id,
                            modello=item.get('modello'), marca=item.get('marca'))

                        if not item_app_id:
                            righe_preview.append(_riga_preview(
                                item, None, False,
                                'Apparecchio non individuato dalla matricola'))
                            continue
                        if not item.get('data_verifica'):
                            righe_preview.append(_riga_preview(
                                item, item_app_id, False,
                                'Data della verifica assente'))
                            continue
                        try:
                            # Default 730 giorni (2 anni) se non indicato
                            periodicita_v = int(item.get('periodicita_giorni') or 730)
                            prossima = item.get('prossima_scadenza') or None
                            if not prossima:
                                try:
                                    d = datetime.strptime(item['data_verifica'], '%Y-%m-%d')
                                    d += timedelta(days=periodicita_v)
                                    prossima = d.strftime('%Y-%m-%d')
                                except ValueError:
                                    pass
                            esito_v = (item.get('esito') or 'positivo').strip().lower()
                            if esito_v not in ('positivo', 'negativo', 'con_riserva'):
                                esito_v = 'positivo'

                            conn.execute(
                                """INSERT INTO verifiche
                                   (apparecchio_id, data_verifica, prossima_scadenza,
                                    periodicita_giorni, esito, tecnico_ditta, note)
                                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    item_app_id,
                                    item.get('data_verifica'),
                                    prossima,
                                    periodicita_v,
                                    esito_v,
                                    item.get('tecnico_ditta'),
                                    item.get('note'),
                                )
                            )
                            verifiche_importate += 1
                            apparecchio_id = item_app_id
                            righe_preview.append(_riga_preview(item, item_app_id, True))
                            logger.info(f"Auto-importata verifica per apparecchio {matricola}")
                        except Exception as e:
                            logger.error(f"Errore auto-import verifica: {e}")
                            righe_preview.append(_riga_preview(
                                item, item_app_id, False,
                                f"Errore in fase di inserimento: {e}"))
                    conn.commit()
                finally:
                    conn.close()

                parsed_data_str = json.dumps(items)
                totale = len(items)
                righe_imp = verifiche_importate

            else:
                # Branch manutenzioni — il documento può contenere più interventi
                tipo_import_value = 'verbale_email'
                if scanned_pdf:
                    from ai_service import parse_verbale_from_pdf_document
                    parsed_items, ai_response = parse_verbale_from_pdf_document(pdf_path, api_key, ai_model, config=app_config, struttura_id=struttura_id)
                else:
                    parsed_items, ai_response = parse_verbale_with_ai(pdf_text, api_key, ai_model, config=app_config, struttura_id=struttura_id)

                imported_count = 0
                last_apparecchio_id = None
                righe_preview = []

                # Copy PDF to verbali folder for attachment to manutenzioni
                from models import upload_subdir as _upload_subdir
                _uploads_base = os.path.normpath(
                    os.path.join(os.path.dirname(os.path.abspath(db_path)), '..', 'uploads')
                )
                verbali_dir, verbale_rel_prefix = _upload_subdir(
                    'verbali', struttura_id,
                    uploads_base=_uploads_base,
                    single_struttura=(app_config or {}).get('single_struttura', False)
                )
                verbale_name = f"{int(datetime.now().timestamp())}_{uuid.uuid4().hex[:8]}_{safe_base}"
                verbale_dest = os.path.join(verbali_dir, verbale_name)
                import shutil
                shutil.copy2(pdf_path, verbale_dest)
                verbale_rel_path = f"{verbale_rel_prefix}/{verbale_name}"

                conn = sqlite3.connect(db_path, timeout=10)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
                try:
                    for parsed_data in parsed_items:
                        matricola = (parsed_data.get('matricola') or '').strip()
                        apparecchio_id = _find_apparecchio(
                            conn, matricola, divisione_id, struttura_id=struttura_id,
                            modello=parsed_data.get('modello'), marca=parsed_data.get('marca'))

                        if apparecchio_id:
                            last_apparecchio_id = apparecchio_id
                        else:
                            righe_preview.append(_riga_preview(
                                parsed_data, None, False,
                                'Apparecchio non individuato dalla matricola'))
                            continue
                        if not parsed_data.get('data_intervento'):
                            righe_preview.append(_riga_preview(
                                parsed_data, apparecchio_id, False,
                                'Data intervento assente'))
                            continue
                        try:
                            tipo_m = (parsed_data.get('tipo') or 'preventiva').strip().lower()
                            if tipo_m not in ('preventiva', 'correttiva', 'verifica', 'calibrazione'):
                                tipo_m = 'preventiva'
                            conn.execute(
                                """INSERT INTO manutenzioni
                                   (apparecchio_id, tipo, data_intervento, prossima_scadenza,
                                    periodicita_giorni, tecnico_ditta, descrizione, esito, costo,
                                    verbale_path)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    apparecchio_id,
                                    tipo_m,
                                    parsed_data.get('data_intervento'),
                                    parsed_data.get('prossima_scadenza') or None,
                                    parsed_data.get('periodicita_giorni'),
                                    parsed_data.get('tecnico_ditta'),
                                    parsed_data.get('descrizione'),
                                    parsed_data.get('esito'),
                                    parsed_data.get('costo'),
                                    verbale_rel_path
                                )
                            )
                            imported_count += 1
                            righe_preview.append(_riga_preview(parsed_data, apparecchio_id, True))
                            logger.info(f"Auto-importata manutenzione per apparecchio {matricola} con verbale allegato")
                        except Exception as e:
                            logger.error(f"Errore auto-import manutenzione {matricola}: {e}")
                            righe_preview.append(_riga_preview(
                                parsed_data, apparecchio_id, False,
                                f"Errore in fase di inserimento: {e}"))
                    conn.commit()
                finally:
                    conn.close()

                apparecchio_id = last_apparecchio_id
                parsed_data_str = json.dumps(parsed_items)
                totale = len(parsed_items)
                righe_imp = imported_count

            # Save import record.
            # 'completed' solo se *ogni* elemento estratto e' stato importato:
            # fino alla 2.8.0 bastava un intervento su dieci, e gli altri nove
            # sparivano dalla coda sopravvivendo solo dentro il JSON grezzo.
            completato = totale > 0 and righe_imp >= totale
            _save_email_import(
                db_path, divisione_id, att['filename'],
                f"{uploads_rel}/{safe_name}", sender, subject,
                tipo_import_value=tipo_import_value,
                stato='completed' if completato else 'pending',
                ai_prompt=f"[System prompt + PDF text ({len(pdf_text)} chars)]",
                ai_response=ai_response,
                parsed_data=parsed_data_str,
                apparecchio_id=apparecchio_id,
                errori=None if completato else 'In attesa di revisione manuale',
                totale_righe=totale,
                righe_importate=righe_imp,
                struttura_id=struttura_id,
                righe=righe_preview
            )

        except Exception as e:
            tb = traceback.format_exc()
            err_safe = str(e).encode('ascii', errors='replace').decode('ascii')
            logger.error(f"Errore processando PDF {att['filename']}: {err_safe}\n{tb}")
            _save_email_import(
                db_path, divisione_id, att['filename'],
                f"{uploads_rel}/{safe_name}" if safe_name else '',
                sender, subject,
                stato='failed', errori=str(e),
                struttura_id=struttura_id
            )


def _find_apparecchio(conn, matricola, divisione_id=None, struttura_id=None,
                      modello=None, marca=None):
    """Trova l'apparecchio di una matricola dentro lo scope indicato.

    Senza struttura_id né divisione_id non si cerca: la matricola non è unica
    fra strutture diverse e fino alla 2.7.1 il fallback senza scope poteva
    restituire l'apparecchio di un altro tenant, sul quale poi venivano scritte
    manutenzioni e verifiche. Nessuno scope significa nessun risultato.

    Nemmeno dentro lo scope la matricola è una chiave: UNIQUE è su
    struttura+modello+matricola. Se più apparecchi la portano e il documento
    non dice quale, si restituisce None e il verbale finisce in coda per la
    scelta manuale: scrivere una manutenzione sull'apparecchio sbagliato è
    peggio che non scriverla.
    """
    if not matricola:
        return None
    from models import scegli_apparecchio as _scegli

    def _decidi(righe):
        riga, _motivo = _scegli(righe, modello=modello, marca=marca)
        return riga['id'] if riga else None

    # Priorità: filtra per struttura_id se disponibile
    if struttura_id:
        righe = conn.execute(
            "SELECT id, marca, modello FROM apparecchi "
            "WHERE matricola = ? AND struttura_id = ? AND stato != 'dismesso'",
            (matricola, struttura_id)
        ).fetchall()
        if righe:
            return _decidi(righe)
        if not divisione_id:
            return None
    # Fallback: filtra per divisione_id
    if not divisione_id:
        return None
    righe = conn.execute(
        "SELECT id, marca, modello FROM apparecchi "
        "WHERE matricola = ? AND divisione_id = ? AND stato != 'dismesso'",
        (matricola, divisione_id)
    ).fetchall()
    return _decidi(righe)


def _decode_header(value):
    """Decode an email header value."""
    if not value:
        return ''
    decoded_parts = email.header.decode_header(value)
    parts = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            parts.append(part.decode(charset or 'utf-8', errors='replace'))
        else:
            parts.append(part)
    return ' '.join(parts)


def _riga_preview(dati, apparecchio_id, importata, nota=None):
    """Descrive l'esito di un singolo elemento estratto dal documento.

    'imported' e' definitivo, 'pending' significa che l'elemento aspetta la
    revisione manuale: e' il motivo per cui la nota accompagna sempre la riga.
    """
    return {
        'dati': dati,
        'apparecchio_id': apparecchio_id,
        'confidenza': 1.0 if apparecchio_id else None,
        'stato': 'imported' if importata else 'pending',
        'nota': nota
    }


def _save_email_import(db_path, divisione_id, filename, filepath, email_from, email_subject,
                       tipo_import_value='verbale_email', stato='pending', ai_prompt=None,
                       ai_response=None, parsed_data=None, apparecchio_id=None, errori=None,
                       totale_righe=1, righe_importate=None, struttura_id=None, righe=None):
    """Salva il record di import e una riga import_preview per ogni elemento.

    Le righe sono l'unico posto in cui un elemento non importato resta
    raggiungibile: senza di esse la revisione manuale vedeva solo il primo
    intervento del PDF e gli altri erano leggibili solo nel JSON grezzo.
    Restituisce l'id del record creato.
    """
    if righe_importate is None:
        righe_importate = 1 if stato == 'completed' else 0
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        cur = conn.execute(
            """INSERT INTO import_history
               (tipo_import, filename, filepath, divisione_id, struttura_id,
                email_from, email_subject,
                totale_righe, righe_importate, stato, ai_prompt, ai_response, errori_dettaglio)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tipo_import_value, filename, filepath, divisione_id, struttura_id,
                email_from, email_subject,
                totale_righe, righe_importate,
                stato, ai_prompt, ai_response,
                errori
            )
        )
        import_id = cur.lastrowid
        for numero, riga in enumerate(righe or [], start=1):
            conn.execute(
                """INSERT INTO import_preview
                   (import_id, riga_numero, dati_estratti, apparecchio_match_id,
                    match_confidence, stato, note_revisione)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    import_id, numero,
                    json.dumps(riga.get('dati') or {}),
                    riga.get('apparecchio_id'),
                    riga.get('confidenza'),
                    riga.get('stato') or 'pending',
                    riga.get('nota')
                )
            )
        conn.commit()
        return import_id
    finally:
        conn.close()


def _update_ultima_verifica(db_path, config_id):
    """Update the last check timestamp for an email config."""
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute(
            "UPDATE email_config SET ultima_verifica = datetime('now') WHERE id = ?",
            (config_id,)
        )
        conn.commit()
    finally:
        conn.close()


def check_all_emails(app):
    """
    Check configured IMAP mailbox.
    Called by the scheduler. Reads IMAP settings from app config (config.json).
    """
    app_config = app.config['APP_CONFIG']
    db_path = app.config['DATABASE_PATH']

    if not app_config.get('imap_enabled'):
        logger.debug("Monitoraggio email IMAP non abilitato.")
        return

    imap_account = app_config.get('imap_account', '').strip()
    imap_server = app_config.get('imap_server', '').strip()

    if not imap_account or not imap_server:
        logger.warning("IMAP abilitato ma account o server non configurati.")
        return

    # L'account IMAP è configurato a livello globale, quindi non porta con sé una
    # struttura. Se il deployment ne ha una sola, i verbali sono suoi: senza questa
    # attribuzione i record finirebbero con struttura_id NULL e la coda email
    # resterebbe invisibile a ogni utente (che filtra per struttura).
    struttura_id = _struttura_unica(db_path)
    if struttura_id is None:
        # Fino alla 2.7.1 qui si proseguiva con struttura_id None: i verbali
        # finivano senza attribuzione e _find_apparecchio() cercava la matricola
        # su tutto il database, scrivendo manutenzioni e verifiche
        # sull'apparecchio di un'altra struttura. Meglio non importare nulla.
        logger.error(
            "IMAP globale con più strutture attive (o nessuna): import email "
            "sospeso. I verbali non sarebbero attribuibili a una struttura e "
            "potrebbero finire sugli apparecchi di un'altra. Configurare "
            "l'import per struttura."
        )
        return

    email_cfg = {
        'id': None,
        'email_account': imap_account,
        'password': app_config.get('imap_password', ''),
        'imap_server': imap_server,
        'imap_port': app_config.get('imap_port', 993),
        'imap_ssl': app_config.get('imap_ssl', True),
        'divisione_id': None,
        'struttura_id': struttura_id,
    }

    logger.info(f"Controllo account email: {imap_account}")
    try:
        check_emails_for_division(email_cfg, app_config, db_path)
    except Exception as e:
        logger.error(f"Errore controllo email per {imap_account}: {e}")
