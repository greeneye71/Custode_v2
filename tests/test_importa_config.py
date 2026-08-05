"""Importatore di un'altra installazione: cosa finisce in strutture_config.

Dalla 2.6.2 il server di posta e' unico e vive nella configurazione di
sistema. L'importatore pero' copiava dentro strutture_config anche host,
porta, utente, mittente, TLS e la password SMTP della sorgente, ricifrata con
la encryption_key del target — chiavi che da questa versione non legge piu'
nessuno e che la migrazione cancella al primo avvio. Peggio dell'inutilita':
scriveva una credenziale nel database e, quando non riusciva a cifrarla,
diceva all'operatore di reinserirla "dal pannello di configurazione", che
quel campo non ce l'ha piu'.
"""
import os
import sqlite3
import sys
import types

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RADICE not in sys.path:
    sys.path.insert(0, RADICE)


def _importatore_finto(config_sorgente, conn, struttura_id):
    """Un Importatore con le sole parti che importa_config() usa davvero.

    Costruirlo per intero richiederebbe un'installazione sorgente su disco:
    qui interessa una cosa sola, cosa scrive quel metodo in strutture_config.
    """
    from importa_installazione import Importatore, Report

    imp = object.__new__(Importatore)
    imp.src = types.SimpleNamespace(config=config_sorgente)
    imp.opz = types.SimpleNamespace(con_config=True, target_config={'encryption_key': 'k'})
    imp.conn = conn
    imp.rep = Report()
    imp.struttura_id = struttura_id
    return imp


def _database_minimo(tmp_path):
    conn = sqlite3.connect(str(tmp_path / 'target.sqlite'))
    conn.execute("""CREATE TABLE strutture_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        struttura_id INTEGER NOT NULL,
        chiave TEXT NOT NULL,
        valore TEXT,
        UNIQUE(struttura_id, chiave))""")
    return conn


def test_il_server_di_posta_della_sorgente_non_finisce_in_strutture_config(tmp_path):
    conn = _database_minimo(tmp_path)
    imp = _importatore_finto({
        'smtp_host': 'smtp.vecchia.it', 'smtp_port': 587,
        'smtp_user': 'posta@vecchia.it', 'smtp_from': 'noreply@vecchia.it',
        'smtp_use_tls': True, 'smtp_password': 'segretissima',
        'ai_provider': 'anthropic', 'anthropic_api_key': 'sk-ant-xxx',
    }, conn, 7)

    imp.importa_config()

    righe = conn.execute("SELECT chiave, valore FROM strutture_config").fetchall()
    chiavi = [c for c, _v in righe]
    assert not any(c.startswith('smtp_') for c in chiavi)
    # E la password non e' finita nel database sotto nessun altro nome, ne' in
    # chiaro ne' cifrata (la forma cifrata cambia a ogni esecuzione, quindi si
    # controlla che non ci sia alcun valore oltre a quelli attesi).
    assert 'segretissima' not in [v for _c, v in righe]
    # Le impostazioni AI restano per struttura: la cancellazione e' mirata.
    assert 'ai_provider' in chiavi
    assert 'anthropic_api_key' in chiavi


def test_l_operatore_viene_avvisato_che_la_posta_va_riconfigurata(tmp_path):
    """Il silenzio farebbe credere che gli avvisi di scadenza partiranno da
    soli. La sorgente aveva un suo server; il target ne ha uno di sistema che
    potrebbe non essere ancora impostato."""
    conn = _database_minimo(tmp_path)
    imp = _importatore_finto({'smtp_host': 'smtp.vecchia.it',
                              'smtp_user': 'posta@vecchia.it'}, conn, 7)

    imp.importa_config()

    unito = " ".join(imp.rep.avvisi).lower()
    assert 'server di posta' in unito
    assert 'configurazione di sistema' in unito
    # Il vecchio avviso mandava l'operatore in un pannello che quel campo non
    # ce l'ha piu': quell'istruzione non deve ricomparire.
    assert 'reinseriscila' not in unito


def test_senza_posta_nella_sorgente_non_si_avvisa_di_niente(tmp_path):
    """Un avviso che compare sempre e' rumore: chi importa un'installazione
    che la posta non l'aveva configurata non ha niente da ricontrollare."""
    conn = _database_minimo(tmp_path)
    imp = _importatore_finto({'ai_provider': 'anthropic'}, conn, 7)

    imp.importa_config()

    assert imp.rep.avvisi == []
