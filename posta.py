"""
MedInventory - Invio della posta

Un solo posto da cui parte la posta. Dalla 2.6.2 il server SMTP e' unico e di
sistema (vedi docs/superpowers/specs/2026-08-02-posta-configurazione-design.md),
e da questa versione serve anche fuori dallo scheduler — la password
temporanea del reset parte da qui. Duplicare la risoluzione dei parametri e
l'invio avrebbe significato due posti da correggere ogni volta.

Volutamente estraneo a Flask, come struttura_service.py e utente_service.py:
riceve il dizionario di configurazione, non lo va a cercare.
"""

import logging
import smtplib

logger = logging.getLogger('medinventory.posta')


def parametri(cfg):
    """I parametri del server di posta di sistema.

    ``smtp_use_tls`` arriva dal JSON globale come booleano (``true``), non come
    la stringa ``'1'`` che si usava in strutture_config prima della 2.6.2: si
    accettano entrambi.

    Il mittente e' ``smtp_user`` e non ha un gemello per struttura: chi riceve
    capisce di quale struttura si tratta dal messaggio, non dall'indirizzo.
    """
    cfg = cfg or {}
    tls = cfg.get('smtp_use_tls', True)
    return {
        'host': cfg.get('smtp_host', ''),
        'porta': int(cfg.get('smtp_port') or 587),
        'utente': cfg.get('smtp_user', ''),
        'password': cfg.get('smtp_password', ''),
        'mittente': cfg.get('smtp_user', ''),
        'usa_tls': str(tls).lower() not in ('0', 'false', ''),
    }


def smtp_configurato(cfg):
    """Se il deployment ha un server di posta da cui spedire.

    Serve anche fuori dall'invio: la schermata di accesso mostra «Password
    dimenticata?» solo quando la risposta e' vera, perche' un pulsante che
    accetta la richiesta e non manda niente e' peggio che non averlo.
    """
    p = parametri(cfg)
    return bool(p['host'] and p['utente'])


def invia(cfg, destinatario, messaggio):
    """Spedisce un messaggio MIME gia' composto. True se e' partito.

    Non solleva: i chiamanti sono un thread di fondo e una rotta che non deve
    dire a chi passa se un indirizzo esiste. Il perche' di un fallimento resta
    nel log.
    """
    p = parametri(cfg)
    if not p['host'] or not p['utente']:
        logger.warning("SMTP di sistema non configurato: niente da spedire a %s.",
                       destinatario)
        return False

    messaggio['From'] = p['mittente']
    messaggio['To'] = destinatario
    try:
        with smtplib.SMTP(p['host'], p['porta'], timeout=15) as server:
            if p['usa_tls']:
                server.starttls()
            if p['utente'] and p['password']:
                server.login(p['utente'], p['password'])
            server.sendmail(p['mittente'], destinatario, messaggio.as_string())
        return True
    except Exception as e:
        logger.error("Errore invio a %s: %s", destinatario, e)
        return False
