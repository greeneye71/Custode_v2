# La posta: un server solo, preferenze per struttura — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Togliere il server di posta per struttura — resta solo quello di sistema — e lasciare alla struttura una preferenza sola: gli avvisi di scadenza li vuoi, in che formato (testo o PDF) e con che frequenza.

**Architecture:** Tre strati, un task per strato. (1) Una migrazione dati in `models.apply_schema_updates()` che cancella le chiavi del server da `strutture_config` e converte il vecchio interruttore `report_schedulato_attivo` nella coppia nuova `avvisi_scadenza_attivi` + `avvisi_scadenza_formato`. (2) `scheduler.py`, dove i due percorsi separati (digest di testo e report PDF) diventano un percorso solo con un formato, e i parametri SMTP si leggono solo dalla configurazione globale. (3) Il modulo di configurazione della struttura, che perde i campi del server e guadagna l'interruttore, il formato e il controllo — disabilitato in permanenza — del monitoraggio casella.

**Tech Stack:** Flask 3.x, SQLite3, Jinja2, `smtplib`/`email.mime` della standard library, pytest, `pypdf` (già in uso nei test del report).

**Specifica di riferimento:** `docs/superpowers/specs/2026-08-02-posta-configurazione-design.md`. Leggila prima di iniziare: contiene il perché di ogni scelta e i due difetti trovati durante la progettazione (`report_pdf_attivo` che nessuno poteva scrivere, e l'etichetta «Report schedulato attivo» che accende il digest di testo).

## Global Constraints

- **Lingua italiana** per interfaccia, commenti, nomi di variabili, valori a database e messaggi di commit. È la convenzione del progetto (`CLAUDE.md`).
- **Nomi delle chiavi nuove, esatti:** `avvisi_scadenza_attivi` (valori `'1'` oppure riga assente) e `avvisi_scadenza_formato` (valori `'testo'` oppure `'pdf'`). `report_frequenza` resta com'è (`giornaliero` / `settimanale` / `mensile`).
- **Chiavi cancellate da `strutture_config`, esatte:** `smtp_host`, `smtp_port`, `smtp_user`, `smtp_from`, `smtp_use_tls`, `smtp_password_encrypted`, `report_schedulato_attivo`, `report_pdf_attivo`.
- **Nessuna chiave per il monitoraggio casella.** Il controllo nel modulo è disabilitato e basta. Un valore che niente può scrivere è esattamente il difetto `report_pdf_attivo`.
- **Chi oggi riceve il digest deve continuare a riceverlo.** È l'asserzione che conta più di tutte: un aggiornamento che spegne in silenzio gli avvisi di scadenza di un parco di elettromedicali sarebbe il peggior modo di consegnare questa modifica.
- **Ogni test va provato sensibile**: si reintroduce il difetto e si verifica che il test cada, poi si ripristina. Il sabotaggio va eseguito facendo girare il **file di test intero**, mai con `-k` o col singolo nome: in questo progetto undici test si sono rivelati ciechi, e più di uno era mascherato proprio da un secondo freno che scattava per un motivo diverso.
- **Non si tocca** `struttura_service._azzera_config_sensibile`: dopo questa modifica non troverà più nulla di SMTP da azzerare, ma continua a servire per le chiavi AI, che restano per struttura.
- **Fuori ambito** (dalla specifica): il monitoraggio casella per struttura, `email_monitor._find_apparecchio`, la tabella legacy `email_config`.
- **Fuori ambito, e da non correggere di iniziativa:** oggi il POST del modulo di configurazione è riservato al `superadmin` (`strutture_bp.py`, `if is_admin_only: return redirect(...)`); un `admin` la pagina la vede ma non la salva. Questa modifica non cambia chi può salvare. Se durante il lavoro la cosa sembra sbagliata, **va segnalata nel rapporto, non corretta**: chi può configurare cosa è una decisione dell'utente, non di questo piano.

---

## Struttura dei file

| File | Responsabilità in questa modifica |
|---|---|
| `models.py` (`apply_schema_updates`, lista `migrations`) | Migrazione dati: converte il vecchio interruttore, cancella le chiavi del server e le chiavi morte. Gira a ogni avvio, quindi dev'essere idempotente. |
| `schema.sql` (commento righe 52-55) | L'elenco delle chiavi valide di `strutture_config` è documentazione: va aggiornato o mente. |
| `scheduler.py` | Un percorso solo per gli avvisi, con un formato. SMTP solo di sistema. Il corpo dell'email col PDF nomina la struttura. |
| `strutture_bp.py` (rotta `config`) | Salva solo le preferenze. Niente più campi del server, niente più cifratura Fernet della password SMTP. |
| `templates/strutture/config.html` | Via la sezione «SMTP / Notifiche». La sezione «Report schedulati» diventa «Avvisi di scadenza», con interruttore, formato, frequenza e il controllo disabilitato del monitoraggio casella. |
| `tests/test_migrazioni.py` | Test della migrazione dati (Task 1). |
| `tests/test_avvisi_scadenza.py` (nuovo) | Test dell'invio: formato testo, formato PDF, server unico, corpo che nomina la struttura (Task 2). |
| `tests/test_strutture_routes.py` | Test del modulo di configurazione (Task 3). |

---

## Task 1: La migrazione dei dati

**Files:**
- Modify: `models.py` — in fondo alla lista `migrations` dentro `apply_schema_updates()` (la lista inizia a `models.py:375`; il ciclo che la applica è a `models.py:487`, con `try/except` per ogni voce)
- Modify: `schema.sql:52-55` (il commento con l'elenco delle chiavi valide)
- Test: `tests/test_migrazioni.py`

**Interfaces:**
- Consumes: niente dai task precedenti.
- Produces: le due chiavi `avvisi_scadenza_attivi` (`'1'`) e `avvisi_scadenza_formato` (`'testo'` / `'pdf'`) in `strutture_config`. Il Task 2 le legge con `get_struttura_config`, il Task 3 le scrive con `set_struttura_config`.

**Contesto che serve sapere:**
- `strutture_config` è `(id, struttura_id, chiave, valore)` con `UNIQUE(struttura_id, chiave)` (`schema.sql:41-48`). L'unicità è quello che rende sicuro l'`INSERT OR IGNORE` qui sotto.
- Il ciclo delle migrazioni fa `db.execute(sql)` dentro un `try/except` che logga e prosegue: ogni voce dev'essere **una sola istruzione SQL**, senza `;` multipli.
- L'ordine delle voci nella lista è l'ordine di esecuzione. Le due conversioni vanno **prima** della cancellazione, altrimenti convertono il nulla.
- L'idempotenza qui non viene da `INSERT OR IGNORE`, ma dal fatto che la cancellazione toglie la riga sorgente: al secondo avvio non c'è più niente da convertire. È anche quello che impedisce di **resuscitare** una preferenza che l'operatore ha spento dopo l'aggiornamento.

- [ ] **Step 1: Scrivi i test che falliscono**

In fondo a `tests/test_migrazioni.py`:

```python
def test_chi_riceveva_il_digest_lo_riceve_ancora_dopo_la_migrazione(app):
    """L'asserzione che conta piu' di tutte. Prima della 2.6.2 il digest di
    testo si accendeva con report_schedulato_attivo; quella chiave sparisce, e
    se la migrazione non la convertisse, un parco di elettromedicali
    smetterebbe di ricevere gli avvisi di scadenza senza che nessuno se ne
    accorga — il modo peggiore di consegnare questa modifica."""
    from models import get_db, execute, query_one, apply_schema_updates
    with app.app_context():
        s = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        execute("INSERT INTO strutture_config (struttura_id,chiave,valore) "
                "VALUES (?,'report_schedulato_attivo','1')", (s,))
        execute("INSERT INTO strutture_config (struttura_id,chiave,valore) "
                "VALUES (?,'report_frequenza','settimanale')", (s,))

        apply_schema_updates()

        attivi = query_one("SELECT valore FROM strutture_config "
                           "WHERE struttura_id=? AND chiave='avvisi_scadenza_attivi'", (s,))
        formato = query_one("SELECT valore FROM strutture_config "
                            "WHERE struttura_id=? AND chiave='avvisi_scadenza_formato'", (s,))
        assert attivi is not None and attivi['valore'] == '1'
        # Testo, non PDF: chi riceveva un digest di testo deve continuare a
        # ricevere quello. Cambiargli il formato sarebbe una sorpresa.
        assert formato is not None and formato['valore'] == 'testo'
        # La frequenza scelta non si perde per strada.
        assert query_one("SELECT valore FROM strutture_config "
                         "WHERE struttura_id=? AND chiave='report_frequenza'",
                         (s,))['valore'] == 'settimanale'


def test_chi_non_riceveva_il_digest_non_inizia_a_riceverlo(app):
    """Il verso opposto, che il test precedente da solo non copre: una
    migrazione che accendesse tutti sarebbe verde li' sopra e sbagliata qui.
    Una struttura con l'interruttore a zero — o senza alcuna riga, il caso
    normale — non deve trovarsi gli avvisi accesi dopo un aggiornamento."""
    from models import execute, query_one, apply_schema_updates
    with app.app_context():
        spenta = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Spenta','SP',1)").lastrowid
        muta = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Muta','MU',1)").lastrowid
        execute("INSERT INTO strutture_config (struttura_id,chiave,valore) "
                "VALUES (?,'report_schedulato_attivo','')", (spenta,))

        apply_schema_updates()

        for sid in (spenta, muta):
            assert query_one("SELECT valore FROM strutture_config "
                             "WHERE struttura_id=? AND chiave='avvisi_scadenza_attivi'",
                             (sid,)) is None


def test_le_chiavi_del_server_spariscono_password_cifrata_compresa(app):
    """Un server di posta e' infrastruttura del deployment, non un dato della
    clinica. Lasciare le righe significherebbe tenere configurazione morta che
    sembra viva, con dentro una credenziale cifrata che finirebbe in ogni
    archivio esportato."""
    from models import execute, query_all, apply_schema_updates
    with app.app_context():
        s = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        for chiave, valore in [
            ('smtp_host', 'smtp.clinica.it'), ('smtp_port', '587'),
            ('smtp_user', 'posta@clinica.it'), ('smtp_from', 'noreply@clinica.it'),
            ('smtp_use_tls', '1'), ('smtp_password_encrypted', 'gAAAAABmSEGRETO='),
            ('report_pdf_attivo', '1'),
        ]:
            execute("INSERT INTO strutture_config (struttura_id,chiave,valore) VALUES (?,?,?)",
                    (s, chiave, valore))
        # Una chiave che NON va toccata, per provare che la cancellazione e'
        # mirata e non una pulizia a tappeto della configurazione.
        execute("INSERT INTO strutture_config (struttura_id,chiave,valore) "
                "VALUES (?,'anthropic_api_key','sk-ant-xxx')", (s,))

        apply_schema_updates()

        rimaste = [r['chiave'] for r in query_all(
            "SELECT chiave FROM strutture_config WHERE struttura_id=?", (s,))]
        for sparita in ('smtp_host', 'smtp_port', 'smtp_user', 'smtp_from',
                        'smtp_use_tls', 'smtp_password_encrypted',
                        'report_schedulato_attivo', 'report_pdf_attivo'):
            assert sparita not in rimaste
        assert 'anthropic_api_key' in rimaste
        # E il segreto non e' rimasto da nessuna parte sotto un altro nome.
        valori = [r['valore'] for r in query_all(
            "SELECT valore FROM strutture_config WHERE struttura_id=?", (s,))]
        assert 'gAAAAABmSEGRETO=' not in valori


def test_la_migrazione_non_riaccende_avvisi_spenti_dall_operatore(app):
    """apply_schema_updates() gira a OGNI avvio, non una volta sola: e' il
    punto in cui una migrazione di dati puo' fare danno. Se la conversione
    restasse ripetibile, il primo riavvio dopo che l'operatore ha tolto la
    spunta agli avvisi glieli riaccenderebbe, e non capirebbe mai perche'.
    L'idempotenza qui non e' un dettaglio di eleganza."""
    from models import execute, query_one, query_all, apply_schema_updates
    with app.app_context():
        s = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        execute("INSERT INTO strutture_config (struttura_id,chiave,valore) "
                "VALUES (?,'report_schedulato_attivo','1')", (s,))

        apply_schema_updates()
        # L'operatore ci ripensa e spegne gli avvisi (il modulo cancella la riga).
        execute("DELETE FROM strutture_config "
                "WHERE struttura_id=? AND chiave='avvisi_scadenza_attivi'", (s,))
        # Riavvio.
        apply_schema_updates()

        assert query_one("SELECT valore FROM strutture_config "
                         "WHERE struttura_id=? AND chiave='avvisi_scadenza_attivi'",
                         (s,)) is None
        # E la seconda esecuzione non ha nemmeno duplicato il formato.
        formati = query_all("SELECT valore FROM strutture_config "
                            "WHERE struttura_id=? AND chiave='avvisi_scadenza_formato'", (s,))
        assert len(formati) == 1
```

- [ ] **Step 2: Esegui i test e verifica che falliscano**

Run: `python -m pytest tests/test_migrazioni.py -q`
Expected: FAIL — i primi tre falliscono perché `avvisi_scadenza_attivi` non esiste e le chiavi `smtp_*` restano; il quarto fallisce sul primo `assert`.

- [ ] **Step 3: Aggiungi la migrazione**

In `models.py`, in fondo alla lista `migrations` (dopo l'ultima voce esistente, prima della parentesi quadra di chiusura):

```python
        # --- 2.6.2: la posta ha un server solo, quello di sistema ---
        # Prima si converte il vecchio interruttore nella coppia nuova, poi si
        # cancella la riga sorgente. L'ordine e' quello che rende la migrazione
        # idempotente: apply_schema_updates() gira a ogni avvio, e senza la
        # cancellazione il primo riavvio dopo che l'operatore ha spento gli
        # avvisi glieli riaccenderebbe. Formato 'testo' perche' e' quello che
        # quell'interruttore accendeva davvero (scheduler._invia_digest):
        # chi riceve un digest di testo deve continuare a ricevere quello.
        """INSERT OR IGNORE INTO strutture_config (struttura_id, chiave, valore)
           SELECT struttura_id, 'avvisi_scadenza_attivi', '1' FROM strutture_config
           WHERE chiave = 'report_schedulato_attivo' AND valore = '1'""",
        """INSERT OR IGNORE INTO strutture_config (struttura_id, chiave, valore)
           SELECT struttura_id, 'avvisi_scadenza_formato', 'testo' FROM strutture_config
           WHERE chiave = 'report_schedulato_attivo' AND valore = '1'""",
        # Le chiavi del server non le legge piu' nessuno: lasciarle significa
        # tenere configurazione morta che sembra viva, con dentro una
        # credenziale cifrata che finirebbe in ogni archivio esportato.
        # report_pdf_attivo esce di scena senza conversione: nessun modulo e
        # nessun template l'ha mai scritta, quindi non c'e' niente da salvare.
        """DELETE FROM strutture_config WHERE chiave IN (
               'smtp_host', 'smtp_port', 'smtp_user', 'smtp_from', 'smtp_use_tls',
               'smtp_password_encrypted', 'report_schedulato_attivo', 'report_pdf_attivo')""",
```

In `schema.sql`, sostituisci il commento delle righe 52-55 con:

```sql
-- Chiavi valide: ai_provider, anthropic_api_key, ai_import_model,
-- ai_email_model, ai_local_base_url, ai_local_model,
-- report_frequenza, avvisi_scadenza_attivi, avvisi_scadenza_formato
-- Dalla 2.6.2 il server di posta e' solo di sistema: nessuna chiave smtp_*
-- vive qui. La migrazione in models.apply_schema_updates() le cancella.
```

- [ ] **Step 4: Esegui i test e verifica che passino**

Run: `python -m pytest tests/test_migrazioni.py -q`
Expected: PASS (tutti)

- [ ] **Step 5: Prova che i test sono sensibili**

Uno alla volta, e ogni volta eseguendo **tutto il file**:

1. Togli le due voci `INSERT OR IGNORE`. Run: `python -m pytest tests/test_migrazioni.py -q` → deve cadere `test_chi_riceveva_il_digest_lo_riceve_ancora_dopo_la_migrazione`. Ripristina.
2. Cambia `'testo'` in `'pdf'` nella seconda `INSERT OR IGNORE`. Run: stesso comando → deve cadere lo stesso test, sull'asserzione del formato. Ripristina.
3. Togli `AND valore = '1'` dalle due `INSERT OR IGNORE`. Run: stesso comando → deve cadere `test_chi_non_riceveva_il_digest_non_inizia_a_riceverlo`. Ripristina.
4. Togli la voce `DELETE`. Run: stesso comando → devono cadere `test_le_chiavi_del_server_spariscono_password_cifrata_compresa` **e** `test_la_migrazione_non_riaccende_avvisi_spenti_dall_operatore`. Ripristina.

Se un sabotaggio non fa cadere il test che dovrebbe, il test è cieco: si corregge il test, non si prosegue.

- [ ] **Step 6: Esegui la suite intera e committa**

```bash
python -m pytest tests/ -q
git add models.py schema.sql tests/test_migrazioni.py
git commit -m "feat(posta): la configurazione di struttura perde il server e guadagna gli avvisi"
```

---

## Task 2: Un percorso solo per gli avvisi, un server solo per la posta

**Files:**
- Modify: `scheduler.py` — registrazione dei task in `start()`, `_send_deadline_alerts`, `_send_scheduled_reports` (da togliere), `_decrypt_smtp_password` (da togliere), `_invia_pdf_allegato`, `_invia_digest`
- Test: `tests/test_avvisi_scadenza.py` (nuovo)

**Interfaces:**
- Consumes: le chiavi `avvisi_scadenza_attivi` e `avvisi_scadenza_formato` del Task 1, lette con `models.get_struttura_config(struttura_id, chiave, default)`.
- Produces:
  - `BackgroundScheduler._config_smtp() -> dict` con le chiavi `host` (str), `porta` (int), `utente` (str), `password` (str), `mittente` (str), `usa_tls` (bool).
  - `BackgroundScheduler._invia(struttura, messaggio) -> bool` — apre la connessione, spedisce, logga; `False` se il server non è configurato o l'invio fallisce.
  - `BackgroundScheduler._send_deadline_alerts()` resta il solo punto d'ingresso periodico degli avvisi.

**Contesto che serve sapere:**
- `get_struttura_config(sid, chiave, default)` ripiega sulla configurazione **globale** quando la riga non c'è (`models.py:813`). Per `avvisi_scadenza_attivi` questo non fa danno (nessuna chiave globale con quel nome), ma è il motivo per cui il default va passato esplicito.
- `_is_digest_due(frequenza)` guarda l'orologio (le 7:00, lunedì, primo del mese). Nei test va neutralizzata, altrimenti il test passa o fallisce a seconda dell'ora in cui gira.
- `smtplib` viene importato **dentro** le funzioni: `monkeypatch.setattr('smtplib.SMTP', ...)` funziona lo stesso, perché la ricerca dell'attributo avviene alla chiamata.
- La configurazione globale nei test arriva dal `config.local.json` dello sviluppatore: **non si può dare per scontato** che `smtp_host` sia vuoto o pieno. Ogni test imposta `app.config['APP_CONFIG']` per la parte che gli interessa.
- Non esiste un `smtp_from` globale (`config.local.example.json` ha solo `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `smtp_use_tls`): il mittente è `smtp_user`. È esattamente il senso di «il mittente è unico».
- `smtp_use_tls` globale è un **booleano JSON** (`true`), non la stringa `'1'` che si usava in `strutture_config`. Il codice nuovo deve reggere entrambi.

- [ ] **Step 1: Scrivi i test che falliscono**

Crea `tests/test_avvisi_scadenza.py`:

```python
"""Gli avvisi di scadenza: un interruttore, un formato, un solo server di posta.

Fino alla 2.6.1 c'erano due percorsi separati — un digest di testo acceso da
report_schedulato_attivo e un report PDF acceso da report_pdf_attivo, chiave
che nessun modulo ha mai scritto — e ogni struttura poteva avere un proprio
server SMTP. Ora il percorso e' uno e il server e' quello di sistema.
"""
import email
import io

import pytest
from pypdf import PdfReader


class SMTPFinto:
    """Sostituto di smtplib.SMTP che registra i messaggi invece di spedirli.

    Registra anche host/porta/credenziali: servono a provare che il server
    usato e' quello di sistema e non uno per struttura.
    """
    inviati = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.utente = None
        self.tls = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        self.tls = True

    def login(self, utente, password):
        self.utente = utente
        self.password = password

    def sendmail(self, mittente, destinatario, testo):
        SMTPFinto.inviati.append({
            'host': self.host, 'porta': self.port, 'utente': self.utente,
            'tls': self.tls, 'mittente': mittente, 'destinatario': destinatario,
            'messaggio': email.message_from_string(testo),
        })


def corpo_testo(messaggio):
    """Il testo del primo pezzo text/plain di un messaggio MIME."""
    for parte in messaggio.walk():
        if parte.get_content_type() == 'text/plain':
            return parte.get_payload(decode=True).decode('utf-8')
    return ''


def allegati_pdf(messaggio):
    """(nome, testo estratto) di ogni allegato PDF."""
    trovati = []
    for parte in messaggio.walk():
        if parte.get_content_type() == 'application/pdf':
            dati = parte.get_payload(decode=True)
            lettore = PdfReader(io.BytesIO(dati))
            testo = "\n".join(p.extract_text() for p in lettore.pages)
            trovati.append((parte.get_filename(), testo))
    return trovati


@pytest.fixture
def posta(app, monkeypatch):
    """Scheduler pronto a inviare: SMTP finto, orologio neutralizzato,
    server di sistema configurato."""
    from scheduler import BackgroundScheduler
    SMTPFinto.inviati = []
    monkeypatch.setattr('smtplib.SMTP', SMTPFinto)
    app.config['APP_CONFIG'] = dict(app.config.get('APP_CONFIG') or {})
    app.config['APP_CONFIG'].update({
        'smtp_host': 'smtp.sistema.it', 'smtp_port': 2525,
        'smtp_user': 'sistema@sistema.it', 'smtp_password': 'segreta',
        'smtp_use_tls': True,
    })
    scheduler = BackgroundScheduler(app)
    monkeypatch.setattr(scheduler, '_is_digest_due', lambda frequenza: True)
    return scheduler


@pytest.fixture
def struttura_con_scadenza(app):
    """Una struttura con un destinatario e una manutenzione scaduta."""
    from models import execute
    with app.app_context():
        s = execute("INSERT INTO strutture (nome,codice,attiva,email_notifiche) "
                    "VALUES ('Clinica Alfa','ALF',1,'direzione@alfa.it')").lastrowid
        d = execute("INSERT INTO divisioni (nome,codice,struttura_id) "
                    "VALUES ('Oculistica','OCU',?)", (s,)).lastrowid
        a = execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,"
                    "modello,stato,ubicazione) VALUES (?,?,'R-00015','REXXAM','OZY',"
                    "'funzionante','Sala 1')", (d, s)).lastrowid
        execute("INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento,prossima_scadenza) "
                "VALUES (?,'preventiva',date('now','-1 year'),date('now','-10 days'))", (a,))
    return s


def accendi(app, struttura_id, formato):
    from models import set_struttura_config
    with app.app_context():
        set_struttura_config(struttura_id, 'avvisi_scadenza_attivi', '1')
        set_struttura_config(struttura_id, 'avvisi_scadenza_formato', formato)


def test_il_formato_testo_manda_il_digest(app, posta, struttura_con_scadenza):
    """Il digest continua a nominare struttura e divisione: e' il modo in cui
    chi riceve capisce di chi si parla, ora che il mittente e' unico per tutte
    le strutture e non dice piu' niente."""
    accendi(app, struttura_con_scadenza, 'testo')
    posta._send_deadline_alerts()

    assert len(SMTPFinto.inviati) == 1
    inviata = SMTPFinto.inviati[0]
    assert inviata['destinatario'] == 'direzione@alfa.it'
    assert 'Clinica Alfa' in inviata['messaggio']['Subject']
    corpo = corpo_testo(inviata['messaggio'])
    assert 'Clinica Alfa' in corpo
    assert 'Oculistica' in corpo
    assert 'R-00015' in corpo
    assert allegati_pdf(inviata['messaggio']) == []


def test_il_formato_pdf_manda_il_report_allegato(app, posta, struttura_con_scadenza):
    """Percorso mai stato raggiungibile prima: report_pdf_attivo, la chiave che
    scheduler._send_scheduled_reports leggeva, non veniva scritta da nessun
    modulo e da nessun template. Il codice del report c'era, funzionante, dalla
    2.5, e restava a zero per sempre."""
    accendi(app, struttura_con_scadenza, 'pdf')
    posta._send_deadline_alerts()

    assert len(SMTPFinto.inviati) == 1
    allegati = allegati_pdf(SMTPFinto.inviati[0]['messaggio'])
    assert len(allegati) == 1
    nome, testo_pdf = allegati[0]
    assert nome.endswith('.pdf')
    assert 'R-00015' in testo_pdf


def test_il_corpo_dell_email_col_pdf_nomina_la_struttura(app, posta, struttura_con_scadenza):
    """Era «In allegato il report periodico delle scadenze», e basta: nessuna
    struttura, in un deployment che ne ospita diverse e ora spedisce tutte
    dallo stesso indirizzo. Con l'allegato aperto si capisce, ma il messaggio
    da solo no."""
    accendi(app, struttura_con_scadenza, 'pdf')
    posta._send_deadline_alerts()

    corpo = corpo_testo(SMTPFinto.inviati[0]['messaggio'])
    assert 'Clinica Alfa' in corpo


def test_una_struttura_che_aveva_un_server_proprio_usa_ora_quello_di_sistema(
        app, posta, struttura_con_scadenza):
    """Righe come queste sopravvivono solo in un database che non ha ancora
    ricevuto la migrazione del Task 1 (per esempio un archivio importato da
    un'installazione piu' vecchia). Non devono dirottare la posta: se lo
    facessero, l'invio si fermerebbe contro un server che non esiste."""
    from models import set_struttura_config
    accendi(app, struttura_con_scadenza, 'testo')
    with app.app_context():
        set_struttura_config(struttura_con_scadenza, 'smtp_host', 'smtp.inesistente.local')
        set_struttura_config(struttura_con_scadenza, 'smtp_user', 'vecchio@clinica.it')
        set_struttura_config(struttura_con_scadenza, 'smtp_from', 'vecchio@clinica.it')

    posta._send_deadline_alerts()

    assert len(SMTPFinto.inviati) == 1
    inviata = SMTPFinto.inviati[0]
    assert inviata['host'] == 'smtp.sistema.it'
    assert inviata['porta'] == 2525
    assert inviata['utente'] == 'sistema@sistema.it'
    assert inviata['mittente'] == 'sistema@sistema.it'


def test_senza_interruttore_non_parte_niente(app, posta, struttura_con_scadenza):
    """Una struttura che non ha chiesto gli avvisi non ne riceve, anche se ha
    scadenze e un destinatario configurato."""
    posta._send_deadline_alerts()
    assert SMTPFinto.inviati == []


def test_senza_server_di_sistema_non_si_spedisce_e_non_si_esplode(
        app, posta, struttura_con_scadenza):
    """Un'installazione che non ha ancora configurato la posta e' normale. Il
    task periodico deve saltarla e proseguire, non sollevare: gira dentro un
    thread di fondo dove un'eccezione non la vede nessuno."""
    accendi(app, struttura_con_scadenza, 'testo')
    app.config['APP_CONFIG']['smtp_host'] = ''

    posta._send_deadline_alerts()

    assert SMTPFinto.inviati == []
```

- [ ] **Step 2: Esegui i test e verifica che falliscano**

Run: `python -m pytest tests/test_avvisi_scadenza.py -q`
Expected: FAIL — nessun invio parte, perché `_send_deadline_alerts` legge ancora `report_schedulato_attivo` e chiede un `smtp_host` per struttura.

- [ ] **Step 3: Riscrivi la parte di posta dello scheduler**

In `scheduler.py`:

**(a)** In `start()`, togli la voce del task `report_schedulati` dalla lista `self._tasks` (quella con `'func': self._send_scheduled_reports`). Gli avvisi hanno un percorso solo, ed è `deadline_alerts`.

**(b)** Sostituisci `_send_deadline_alerts` per intero con:

```python
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
```

**(c)** Togli `_send_scheduled_reports` e `_decrypt_smtp_password`. La seconda decifrava una password che ora non esiste più in `strutture_config`; la chiave globale è in chiaro nel file di configurazione.

**(d)** Sostituisci `_genera_e_invia_report` e `_invia_pdf_allegato` con:

```python
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
```

**(e)** Sostituisci `_invia_digest` con la versione che non cerca più le scadenze fuori e non apre più la connessione da sé:

```python
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
```

**(f)** Il report PDF ora parte solo se ci sono scadenze da segnalare? No: `genera_report_scadenze_pdf` regge già una struttura senza scadenze (`tests/test_report_scheduler.py::test_il_report_dello_scheduler_regge_una_struttura_senza_scadenze`) e il comportamento precedente era di inviarlo comunque. **Non cambiarlo**: è fuori dalla specifica.

- [ ] **Step 4: Esegui i test e verifica che passino**

Run: `python -m pytest tests/test_avvisi_scadenza.py tests/test_report_scheduler.py -q`
Expected: PASS (tutti)

- [ ] **Step 5: Prova che i test sono sensibili**

Uno alla volta, eseguendo ogni volta **tutto** `tests/test_avvisi_scadenza.py`:

1. In `_send_deadline_alerts`, rimetti `'report_schedulato_attivo'` al posto di `'avvisi_scadenza_attivi'` → devono cadere i test del testo e del PDF. Ripristina.
2. Forza `formato = 'testo'` ignorando la configurazione → deve cadere `test_il_formato_pdf_manda_il_report_allegato`. Ripristina.
3. In `_invia_report_pdf`, rimetti il corpo vecchio `"In allegato il report periodico delle scadenze."` → deve cadere `test_il_corpo_dell_email_col_pdf_nomina_la_struttura` **e nessun altro** (se cadesse anche quello del PDF, quel test starebbe misurando la cosa sbagliata). Ripristina.
4. In `_config_smtp`, fai vincere `get_struttura_config(sid, 'smtp_host')` sul globale → deve cadere `test_una_struttura_che_aveva_un_server_proprio_usa_ora_quello_di_sistema`. Ripristina.
5. In `_invia`, togli il controllo `if not smtp['host']` → deve cadere `test_senza_server_di_sistema_non_si_spedisce_e_non_si_esplode`. Ripristina.

- [ ] **Step 6: Cerca riferimenti rimasti alle funzioni tolte**

```bash
grep -rn "_send_scheduled_reports\|_decrypt_smtp_password\|_genera_e_invia_report\|_invia_pdf_allegato\|report_pdf_attivo" --include=*.py --include=*.html --include=*.md .
```
Attesi: solo le occorrenze nella specifica e nel piano (documentazione storica). Qualunque riferimento in codice o template è un pezzo rimasto indietro e va sistemato.

- [ ] **Step 7: Esegui la suite intera e committa**

```bash
python -m pytest tests/ -q
git add scheduler.py tests/test_avvisi_scadenza.py
git commit -m "feat(posta): un solo percorso per gli avvisi e un solo server di posta"
```

---

## Task 3: Il modulo di configurazione della struttura

**Files:**
- Modify: `strutture_bp.py` — rotta `config`, ramo `POST` (la lista `chiavi_smtp_report`, l'insieme `CHECKBOX_KEYS` e il blocco che cifra `smtp_password` con Fernet)
- Modify: `templates/strutture/config.html:141-236` (le sezioni «SMTP / Notifiche» e «Report schedulati»)
- Test: `tests/test_strutture_routes.py`

**Interfaces:**
- Consumes: le chiavi del Task 1, scritte con `models.set_struttura_config(struttura_id, chiave, valore)` e cancellate con un `DELETE` diretto (è il modo in cui la rotta già gestisce i campi vuoti).
- Produces: niente per i task successivi.

**Contesto che serve sapere:**
- Il `POST` è riservato al `superadmin`: `if is_admin_only: return redirect(...)`. **Non cambiarlo** (vedi Global Constraints). I test del modulo entrano come `superadmin`.
- `tests/test_strutture_routes.py` ha già la fixture `dati` e l'helper `entra(client, email)`: si riusano.
- Gli import `base64`, `hashlib` e `Fernet` in cima a `strutture_bp.py` potrebbero servire ancora ad altro (chiavi AI): **verifica con grep prima di toglierli**, e se servono ancora lasciali stare.
- La casella del monitoraggio va resa non attivabile in modo che regga anche un POST costruito a mano: `disabled` da solo impedisce al browser di inviare il campo, ma la vera garanzia è che la rotta non conosca nessuna chiave corrispondente. Sono due difese diverse e servono entrambe — il test le prova separatamente.

- [ ] **Step 1: Scrivi i test che falliscono**

In fondo a `tests/test_strutture_routes.py`. Il file ha già la fixture `dati` (che crea la struttura `dati['s']`, un `superadmin` `super@x.it` e un `admin` `admin@a.it`, tutti con password `Passw0rd!`) e l'helper `entra(client, email)`: si riusano, non se ne creano di nuovi.

```python
def test_il_modulo_di_configurazione_non_ha_piu_i_campi_del_server(client, dati):
    """Il server di posta e' infrastruttura del deployment: si configura una
    volta sola, nella configurazione di sistema. Lasciare i campi qui
    inviterebbe a compilarli, e da questa versione nessuno li leggerebbe."""
    entra(client, 'super@x.it')

    pagina = client.get(f"/strutture/{dati['s']}/config").get_data(as_text=True)

    for campo in ('name="smtp_host"', 'name="smtp_port"', 'name="smtp_user"',
                  'name="smtp_from"', 'name="smtp_password"', 'name="smtp_use_tls"'):
        assert campo not in pagina


def test_si_accendono_gli_avvisi_scegliendo_il_formato(client, app, dati):
    from models import query_one
    struttura_id = dati['s']
    entra(client, 'super@x.it')

    client.post(f"/strutture/{struttura_id}/config",
                data={'avvisi_scadenza_attivi': '1',
                      'avvisi_scadenza_formato': 'pdf',
                      'report_frequenza': 'mensile'},
                follow_redirects=True)

    with app.app_context():
        def valore(chiave):
            riga = query_one("SELECT valore FROM strutture_config "
                             "WHERE struttura_id=? AND chiave=?", (struttura_id, chiave))
            return riga['valore'] if riga else None
        assert valore('avvisi_scadenza_attivi') == '1'
        assert valore('avvisi_scadenza_formato') == 'pdf'
        assert valore('report_frequenza') == 'mensile'


def test_togliere_la_spunta_spegne_davvero_gli_avvisi(client, app, dati):
    """Una casella non spuntata non compare affatto nel POST. Se la rotta si
    limitasse a scrivere i campi presenti, l'interruttore si potrebbe accendere
    e mai piu' spegnere dall'interfaccia."""
    from models import query_one, set_struttura_config
    struttura_id = dati['s']
    with app.app_context():
        set_struttura_config(struttura_id, 'avvisi_scadenza_attivi', '1')
    entra(client, 'super@x.it')

    client.post(f"/strutture/{struttura_id}/config",
                data={'avvisi_scadenza_formato': 'testo',
                      'report_frequenza': 'settimanale'},
                follow_redirects=True)

    with app.app_context():
        assert query_one("SELECT valore FROM strutture_config "
                         "WHERE struttura_id=? AND chiave='avvisi_scadenza_attivi'",
                         (struttura_id,)) is None


def test_il_monitoraggio_casella_si_vede_ma_non_si_attiva(client, app, dati):
    """Compare in pagina perche' e' una funzione annunciata, disabilitata in
    attesa di un'implementazione migliore. Non ha una chiave di configurazione:
    un valore che niente puo' scrivere e' esattamente il difetto trovato con
    report_pdf_attivo, che sembrava configurazione viva e non lo era."""
    from models import query_all
    struttura_id = dati['s']
    entra(client, 'super@x.it')

    pagina = client.get(f"/strutture/{struttura_id}/config").get_data(as_text=True)
    assert 'monitora' in pagina.lower()
    assert 'disabled' in pagina

    # E anche forzando un POST a mano non nasce nessuna riga.
    client.post(f"/strutture/{struttura_id}/config",
                data={'monitoraggio_casella_attivo': '1',
                      'report_frequenza': 'settimanale'},
                follow_redirects=True)
    with app.app_context():
        chiavi = [r['chiave'] for r in query_all(
            "SELECT chiave FROM strutture_config WHERE struttura_id=?", (struttura_id,))]
    assert not any('monitoraggio' in c or 'imap' in c for c in chiavi)


def test_il_modulo_non_scrive_piu_chiavi_del_server(client, app, dati):
    """Anche con un POST che le contiene: la rotta deve conoscere solo le
    preferenze, non il server."""
    from models import query_all
    struttura_id = dati['s']
    entra(client, 'super@x.it')

    client.post(f"/strutture/{struttura_id}/config",
                data={'smtp_host': 'smtp.intruso.it', 'smtp_user': 'intruso@x.it',
                      'smtp_password': 'segreta', 'smtp_from': 'intruso@x.it',
                      'report_frequenza': 'settimanale'},
                follow_redirects=True)

    with app.app_context():
        righe = query_all("SELECT chiave, valore FROM strutture_config "
                          "WHERE struttura_id=?", (struttura_id,))
    chiavi = [r['chiave'] for r in righe]
    assert not any(c.startswith('smtp_') for c in chiavi)
    assert 'segreta' not in [r['valore'] for r in righe]
```

- [ ] **Step 2: Esegui i test e verifica che falliscano**

Run: `python -m pytest tests/test_strutture_routes.py -q`
Expected: FAIL — i campi del server sono ancora in pagina e la rotta li salva ancora.

- [ ] **Step 3: Riscrivi il ramo POST della rotta**

In `strutture_bp.py`, dentro `config`, sostituisci il blocco che va da `# Superadmin only: save SMTP + report fields` fino a prima del `flash('Configurazione salvata.', 'success')` con:

```python
        # Solo le preferenze: dalla 2.6.2 il server di posta e' unico e vive
        # nella configurazione di sistema. Le chiavi smtp_* qui non si scrivono
        # piu' — nessuno le leggerebbe — e la migrazione in
        # models.apply_schema_updates() ha gia' cancellato quelle esistenti,
        # password cifrata compresa.
        CHIAVI_PREFERENZE = ['avvisi_scadenza_attivi', 'avvisi_scadenza_formato',
                             'report_frequenza']
        CASELLE = {'avvisi_scadenza_attivi'}
        for chiave in CHIAVI_PREFERENZE:
            if chiave in CASELLE:
                valore = '1' if request.form.get(chiave) else ''
            else:
                valore = request.form.get(chiave, '').strip()
            if valore:
                set_struttura_config(struttura_id, chiave, valore)
            else:
                # Una casella non spuntata non compare affatto nel POST: senza
                # questa cancellazione l'interruttore si accenderebbe e non si
                # potrebbe piu' spegnere dall'interfaccia.
                execute(
                    "DELETE FROM strutture_config WHERE struttura_id=? AND chiave=?",
                    (struttura_id, chiave)
                )
```

e cambia la voce di registro subito sotto:

```python
        log_attivita(g.user['id'], 'modifica', 'strutture_config', struttura_id,
                     'Preferenze avvisi di scadenza salvate', request.remote_addr)
```

Poi verifica se `base64`, `hashlib` e `Fernet` servono ancora in quel file:

```bash
grep -n "base64\|hashlib\|Fernet" strutture_bp.py
```
Se restano usati (chiavi AI), lascia gli import. Se non li usa più nessuno, toglili.

- [ ] **Step 4: Riscrivi le due sezioni del modulo**

In `templates/strutture/config.html`, sostituisci **entrambe** le sezioni «SMTP / Notifiche» e «Report schedulati» (dal commento `{# Sezione: SMTP / Notifiche #}` fino alla chiusura della card dei report) con:

```html
    {# ------------------------------------------------------------------ #}
    {# Sezione: Avvisi di scadenza                                         #}
    {# ------------------------------------------------------------------ #}
    <div class="col-12">
      <div class="card">
        <div class="card-header">
          <i class="bi bi-bell me-1"></i> Avvisi di scadenza
        </div>
        <div class="card-body">
          <p class="text-muted small">
            Gli avvisi partono dal server di posta di sistema, uguale per tutte le
            strutture. Il destinatario e' l'indirizzo per le notifiche indicato
            nella scheda della struttura.
          </p>
          <div class="row g-3">

            <div class="col-12">
              <div class="form-check">
                <input class="form-check-input" type="checkbox" name="avvisi_scadenza_attivi"
                       id="avvisi_scadenza_attivi" value="1"
                       {% if cfg.get('avvisi_scadenza_attivi') == '1' %}checked{% endif %}>
                <label class="form-check-label" for="avvisi_scadenza_attivi">
                  Invia gli avvisi di scadenza
                </label>
              </div>
            </div>

            <div class="col-md-4">
              <label class="form-label">Formato</label>
              <select class="form-select" name="avvisi_scadenza_formato">
                <option value="testo" {% if cfg.get('avvisi_scadenza_formato', 'testo') != 'pdf' %}selected{% endif %}>Elenco nel corpo dell'email</option>
                <option value="pdf"   {% if cfg.get('avvisi_scadenza_formato') == 'pdf' %}selected{% endif %}>Report PDF allegato</option>
              </select>
            </div>

            <div class="col-md-4">
              <label class="form-label">Frequenza</label>
              <select class="form-select" name="report_frequenza">
                <option value="" {% if not cfg.get('report_frequenza') %}selected{% endif %}>— usa default globale —</option>
                <option value="giornaliero" {% if cfg.get('report_frequenza') == 'giornaliero' %}selected{% endif %}>Giornaliero</option>
                <option value="settimanale" {% if cfg.get('report_frequenza') == 'settimanale' %}selected{% endif %}>Settimanale</option>
                <option value="mensile"     {% if cfg.get('report_frequenza') == 'mensile' %}selected{% endif %}>Mensile</option>
              </select>
            </div>

            <div class="col-12">
              <div class="form-check">
                <input class="form-check-input" type="checkbox"
                       id="monitoraggio_casella" disabled>
                <label class="form-check-label text-muted" for="monitoraggio_casella">
                  Monitora una casella di posta per i verbali di manutenzione
                </label>
                <div class="form-text">
                  Disponibile in una versione successiva: oggi il monitoraggio della
                  posta in arrivo e' unico per l'installazione e si configura nella
                  configurazione di sistema.
                </div>
              </div>
            </div>

          </div>
        </div>
      </div>
    </div>
```

Nota: la casella disabilitata **non ha `name`**. Senza `name` non finisce nel POST nemmeno se qualcuno toglie `disabled` dagli strumenti del browser, e non esiste una chiave che possa rappresentarla.

- [ ] **Step 5: Esegui i test e verifica che passino**

Run: `python -m pytest tests/test_strutture_routes.py -q`
Expected: PASS (tutti)

- [ ] **Step 6: Prova che i test sono sensibili**

Uno alla volta, eseguendo ogni volta **tutto** `tests/test_strutture_routes.py`:

1. Rimetti `'smtp_host'` in `CHIAVI_PREFERENZE` → deve cadere `test_il_modulo_non_scrive_piu_chiavi_del_server`. Ripristina.
2. Togli il ramo `else` con il `DELETE` → deve cadere `test_togliere_la_spunta_spegne_davvero_gli_avvisi`. Ripristina.
3. Rimetti nel template un solo campo `<input name="smtp_host">` → deve cadere `test_il_modulo_di_configurazione_non_ha_piu_i_campi_del_server`. Ripristina.
4. Togli `disabled` dalla casella del monitoraggio → deve cadere `test_il_monitoraggio_casella_si_vede_ma_non_si_attiva`. Ripristina.

- [ ] **Step 7: Prova a mano il giro completo**

```bash
python app.py
```
Entra come superadmin, apri la configurazione di una struttura, accendi gli avvisi con formato PDF, salva, riapri la pagina: la spunta e il formato devono essere quelli salvati. Togli la spunta, salva, riapri: deve risultare spenta. Ferma l'applicazione.

- [ ] **Step 8: Esegui la suite intera e committa**

```bash
python -m pytest tests/ -q
git add strutture_bp.py templates/strutture/config.html tests/test_strutture_routes.py
git commit -m "feat(posta): il modulo di struttura configura gli avvisi, non il server"
```

---

## Verifica finale del piano

- [ ] `python -m pytest tests/ -q` — tutta la suite verde.
- [ ] `grep -rn "report_schedulato_attivo\|report_pdf_attivo\|smtp_password_encrypted" --include=*.py --include=*.html .` — nessuna occorrenza in codice o template (solo specifica, piano e migrazione, che le nominano per cancellarle).
- [ ] Il `CHANGELOG.md` **non** si tocca qui: la voce della 2.6.2 e il numero di versione si scrivono nell'ultimo piano della release, quando si sa cosa è entrato davvero.
- [ ] Nel rapporto finale, segnala esplicitamente se durante il lavoro è emerso che il POST del modulo resta riservato al `superadmin` mentre un `admin` la pagina la vede soltanto: è fuori ambito per questo piano, ma è una decisione che l'utente deve poter prendere.
