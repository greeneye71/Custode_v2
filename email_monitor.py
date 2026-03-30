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
import json
import tempfile
import logging
import traceback
from datetime import datetime, timedelta

logger = logging.getLogger('medinventory.email')


def get_fernet(config):
    """Get Fernet cipher from app config."""
    from cryptography.fernet import Fernet
    key = config.get('encryption_key', config['secret_key'])
    fernet_key = base64.urlsafe_b64encode(key[:32].encode().ljust(32, b'\0'))
    return Fernet(fernet_key)


def decrypt_password(encrypted_password, config):
    """Decrypt an IMAP password stored in the database."""
    f = get_fernet(config)
    return f.decrypt(encrypted_password.encode()).decode()


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

    api_key = app_config.get('anthropic_api_key', '')
    ai_model = app_config.get('ai_email_model', 'claude-haiku-4-5-20251001')

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

        uploads_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), '..', 'uploads', 'email')
        os.makedirs(uploads_dir, exist_ok=True)

        for msg_id in msg_ids:
            try:
                _process_email(
                    mail, msg_id, divisione_id, api_key, ai_model,
                    uploads_dir, db_path, account, app_config=app_config
                )
            except Exception as e:
                logger.error(f"Errore processando email {msg_id} per {account}: {e}")

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


def _process_email(mail, msg_id, divisione_id, api_key, ai_model, uploads_dir, db_path, account, app_config=None):
    """Process a single email message: extract PDF attachments and analyze."""
    import sqlite3
    from ai_service import parse_verbale_with_ai, classify_email_document_type, analyze_verifiche_with_ai

    status, data = mail.fetch(msg_id, '(RFC822)')
    if status != 'OK':
        return

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
    for att in pdf_attachments:
        safe_name = None
        try:
            # Save PDF to disk
            safe_name = f"{int(datetime.now().timestamp())}_{att['filename'].replace(' ', '_')}"
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
                doc_type = classify_email_document_type_from_pdf_document(pdf_path, api_key, ai_model, config=app_config)
            else:
                doc_type = classify_email_document_type(pdf_text, api_key, ai_model, config=app_config)
            logger.info(f"Tipo documento rilevato: {doc_type} per {att['filename']}")

            if doc_type == 'verifica_elettrica':
                # Branch verifiche di sicurezza elettrica
                if scanned_pdf:
                    from ai_service import analyze_verifiche_from_pdf_document
                    items, ai_response = analyze_verifiche_from_pdf_document(pdf_path, api_key, ai_model, config=app_config)
                else:
                    items, ai_response = analyze_verifiche_with_ai(pdf_text, api_key, ai_model, config=app_config)
                auto_imported = False
                apparecchio_id = None
                tipo_import_value = 'verifica_elettrica'

                conn = sqlite3.connect(db_path, timeout=10)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
                try:
                    for item in items:
                        matricola = item.get('matricola', '').strip()
                        item_app_id = _find_apparecchio(conn, matricola, divisione_id)

                        if item_app_id and item.get('data_verifica'):
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
                                auto_imported = True
                                apparecchio_id = item_app_id
                                logger.info(f"Auto-importata verifica per apparecchio {matricola}")
                            except Exception as e:
                                logger.error(f"Errore auto-import verifica: {e}")
                    conn.commit()
                finally:
                    conn.close()

                parsed_data_str = json.dumps(items)

            else:
                # Branch manutenzioni — il documento può contenere più interventi
                tipo_import_value = 'verbale_email'
                if scanned_pdf:
                    from ai_service import parse_verbale_from_pdf_document
                    parsed_items, ai_response = parse_verbale_from_pdf_document(pdf_path, api_key, ai_model, config=app_config)
                else:
                    parsed_items, ai_response = parse_verbale_with_ai(pdf_text, api_key, ai_model, config=app_config)

                imported_count = 0
                last_apparecchio_id = None

                # Copy PDF to verbali folder for attachment to manutenzioni
                verbali_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), '..', 'uploads', 'verbali')
                os.makedirs(verbali_dir, exist_ok=True)
                verbale_name = f"{int(datetime.now().timestamp())}_{att['filename'].replace(' ', '_')}"
                verbale_dest = os.path.join(verbali_dir, verbale_name)
                import shutil
                shutil.copy2(pdf_path, verbale_dest)
                verbale_rel_path = f"verbali/{verbale_name}"

                conn = sqlite3.connect(db_path, timeout=10)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
                try:
                    for parsed_data in parsed_items:
                        matricola = (parsed_data.get('matricola') or '').strip()
                        apparecchio_id = _find_apparecchio(conn, matricola, divisione_id)

                        if apparecchio_id:
                            last_apparecchio_id = apparecchio_id

                        if apparecchio_id and parsed_data.get('data_intervento'):
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
                                logger.info(f"Auto-importata manutenzione per apparecchio {matricola} con verbale allegato")
                            except Exception as e:
                                logger.error(f"Errore auto-import manutenzione {matricola}: {e}")
                    conn.commit()
                finally:
                    conn.close()

                auto_imported = imported_count > 0
                apparecchio_id = last_apparecchio_id
                parsed_data_str = json.dumps(parsed_items)

            # Save import record
            if tipo_import_value == 'verbale_email':
                totale = len(parsed_items)
                righe_imp = imported_count
            else:
                totale = len(items)
                righe_imp = 1 if auto_imported else 0
            _save_email_import(
                db_path, divisione_id, att['filename'],
                f"email/{safe_name}", sender, subject,
                tipo_import_value=tipo_import_value,
                stato='completed' if auto_imported else 'pending',
                ai_prompt=f"[System prompt + PDF text ({len(pdf_text)} chars)]",
                ai_response=ai_response,
                parsed_data=parsed_data_str,
                apparecchio_id=apparecchio_id,
                errori=None if auto_imported else 'In attesa di revisione manuale',
                totale_righe=totale,
                righe_importate=righe_imp
            )

        except Exception as e:
            tb = traceback.format_exc()
            err_safe = str(e).encode('ascii', errors='replace').decode('ascii')
            logger.error(f"Errore processando PDF {att['filename']}: {err_safe}\n{tb}")
            _save_email_import(
                db_path, divisione_id, att['filename'],
                f"email/{safe_name}" if safe_name else '',
                sender, subject,
                stato='failed', errori=str(e)
            )


def _find_apparecchio(conn, matricola, divisione_id):
    """Find apparecchio ID by matricola using an existing connection."""
    if not matricola:
        return None
    if divisione_id:
        row = conn.execute(
            "SELECT id FROM apparecchi WHERE matricola = ? AND divisione_id = ? AND stato != 'dismesso'",
            (matricola, divisione_id)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM apparecchi WHERE matricola = ? AND stato != 'dismesso'",
            (matricola,)
        ).fetchone()
    return row['id'] if row else None


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


def _save_email_import(db_path, divisione_id, filename, filepath, email_from, email_subject,
                       tipo_import_value='verbale_email', stato='pending', ai_prompt=None,
                       ai_response=None, parsed_data=None, apparecchio_id=None, errori=None,
                       totale_righe=1, righe_importate=None):
    """Save an email import record to the database."""
    if righe_importate is None:
        righe_importate = 1 if stato == 'completed' else 0
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute(
            """INSERT INTO import_history
               (tipo_import, filename, filepath, divisione_id, email_from, email_subject,
                totale_righe, righe_importate, stato, ai_prompt, ai_response, errori_dettaglio)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tipo_import_value, filename, filepath, divisione_id,
                email_from, email_subject,
                totale_righe, righe_importate,
                stato, ai_prompt, ai_response,
                errori
            )
        )
        conn.commit()
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

    email_cfg = {
        'id': None,
        'email_account': imap_account,
        'password': app_config.get('imap_password', ''),
        'imap_server': imap_server,
        'imap_port': app_config.get('imap_port', 993),
        'imap_ssl': app_config.get('imap_ssl', True),
        'divisione_id': None,
    }

    logger.info(f"Controllo account email: {imap_account}")
    try:
        check_emails_for_division(email_cfg, app_config, db_path)
    except Exception as e:
        logger.error(f"Errore controllo email per {imap_account}: {e}")
