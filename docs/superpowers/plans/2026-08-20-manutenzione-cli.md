# manutenzione.py — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un unico strumento a riga di comando che fotografa un'installazione MedInventory, diagnostica i problemi che la rendono inaccessibile, e permette di azzerare gli utenti conservando tutti gli altri dati.

**Architecture:** Un package `manutenzione_lib/` con separazione stretta — `tui.py` presenta e non conosce il dominio, `stato.py` raccoglie e non giudica, `diagnosi.py` giudica e non stampa, `utenti.py` opera su una `sqlite3.Connection` senza aprire transazioni proprie, `operazioni.py` adatta gli script esistenti, `menu.py` e' l'unico a chiamare `input()`. L'entry point `manutenzione.py` espone gli stessi moduli sia come subcomandi non interattivi sia come menu.

**Tech Stack:** Python 3, libreria standard (argparse, sqlite3, getpass), werkzeug (gia' dipendenza), pytest. **Nessuna dipendenza nuova.**

**Spec:** `docs/superpowers/specs/2026-08-20-manutenzione-cli-design.md`

## Global Constraints

- **Versione di rilascio: 2.6.3.** Si tocca solo nel Task 10.
- **Nessuna dipendenza nuova** in `requirements.txt`. La resa grafica e' ANSI di libreria standard.
- **Lingua italiana** per ogni testo a video, nome di funzione, nome di variabile e commento. E' la convenzione del progetto.
- **Niente Flask.** Ogni modulo di `manutenzione_lib/` opera su una `sqlite3.Connection` passata dal chiamante, come `utente_service.py` e `struttura_service.py`. `create_app()` non e' utilizzabile: lo strumento deve poter aprire un database arbitrario indicato con `--db`.
- **Nessun segreto a video.** Chiavi API e password compaiono come `presente` / `assente`, mai il valore. Vale anche per `stato --json`.
- **Gli script esistenti non si toccano**, con la sola eccezione di `crea_superadmin.py` (Task 9). `migrate.py`, `toggle_modalita.py`, `pulisci_uploads.py` restano identici e i loro test devono restare verdi.
- **Baseline: 395 test verdi** (`python -m pytest tests/ -q`, circa 4 minuti e mezzo). Il numero cresce, non cala.
- **Console Windows.** Ogni entry point riconfigura gli stream come gia' fanno gli script attuali:
  ```python
  if hasattr(sys.stdout, 'reconfigure'):
      sys.stdout.reconfigure(encoding='utf-8', errors='replace')
  if hasattr(sys.stderr, 'reconfigure'):
      sys.stderr.reconfigure(encoding='utf-8', errors='replace')
  ```
- **Commit per task**, messaggi in italiano, formato Conventional Commits, con il trailer:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  ```

## File Structure

| File | Responsabilita' |
|---|---|
| `manutenzione_lib/__init__.py` | vuoto, marca il package |
| `manutenzione_lib/tui.py` | colori, tabelle, righe di esito, prompt, conferme. Non importa nulla del dominio |
| `manutenzione_lib/stato.py` | raccolta dei parametri in un dizionario. Non stampa, non giudica |
| `manutenzione_lib/diagnosi.py` | i controlli, ognuno una funzione. Non stampa, non esce |
| `manutenzione_lib/utenti.py` | elenco, stato delle impronte, creazione accesso, reset password, azzeramento |
| `manutenzione_lib/operazioni.py` | adattatori verso `migrate`, `pulisci_uploads`, `toggle_modalita`, `backup_service` |
| `manutenzione_lib/menu.py` | menu interattivo. Unico a chiamare `input()` |
| `manutenzione.py` | entry point: argparse, subcomandi, avvio del menu |
| `tests/test_manutenzione.py` | tutti i test nuovi |

---

### Task 1: `tui.py` — presentazione

**Files:**
- Create: `manutenzione_lib/__init__.py`
- Create: `manutenzione_lib/tui.py`
- Test: `tests/test_manutenzione.py`

**Interfaces:**
- Consumes: niente.
- Produces:
  - `supporta_colore() -> bool`
  - `colora(testo: str, colore: str) -> str` — `colore` in `'verde' | 'giallo' | 'rosso' | 'ciano' | 'grassetto'`
  - `titolo(testo: str) -> str`
  - `riga_esito(gravita: str, testo: str) -> str` — `gravita` in `'ok' | 'avviso' | 'errore'`
  - `tabella(intestazioni: list[str], righe: list[list[str]]) -> str`
  - `campo(etichetta: str, valore: str, larghezza: int = 12) -> str`
  - `separatore(larghezza: int = 60) -> str`

Tutte restituiscono stringhe, nessuna stampa: e' cio' che le rende testabili senza catturare stdout.

- [ ] **Step 1: Write the failing test**

```python
"""Test dello strumento di manutenzione a riga di comando."""
import os
import sqlite3
import sys

import pytest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)

from manutenzione_lib import tui


def test_senza_terminale_nessuna_sequenza_di_escape(monkeypatch):
    """L'output rediretto su file deve restare leggibile: niente ANSI.

    E' il caso di 'manutenzione.py stato > rapporto.txt', e anche quello di
    pytest, che cattura stdout.
    """
    monkeypatch.setattr(tui, 'supporta_colore', lambda: False)
    assert '\033' not in tui.colora('ciao', 'verde')
    assert '\033' not in tui.riga_esito('errore', 'guasto')
    assert '\033' not in tui.titolo('Stato')


def test_le_righe_di_esito_restano_distinguibili_senza_colore(monkeypatch):
    """Tolto il colore, la gravita' deve restare leggibile dal testo."""
    monkeypatch.setattr(tui, 'supporta_colore', lambda: False)
    assert tui.riga_esito('ok', 'tutto bene').startswith('[OK]')
    assert tui.riga_esito('avviso', 'occhio').startswith('[!!]')
    assert tui.riga_esito('errore', 'guasto').startswith('[ERR]')


def test_la_tabella_allinea_sulle_celle_piu_larghe(monkeypatch):
    monkeypatch.setattr(tui, 'supporta_colore', lambda: False)
    reso = tui.tabella(['Email', 'Ruolo'],
                       [['a@b.it', 'admin'], ['lunghissimo@esempio.it', 'utente']])
    righe = reso.splitlines()
    # Intestazione, separatore, due righe di dati.
    assert len(righe) == 4
    # Tutte le righe finiscono alla stessa colonna: e' cio' che rende
    # leggibile un elenco di utenti su una console stretta.
    assert len({len(r.rstrip()) for r in righe}) <= 2
    assert 'lunghissimo@esempio.it' in reso


def test_la_tabella_vuota_non_esplode(monkeypatch):
    monkeypatch.setattr(tui, 'supporta_colore', lambda: False)
    assert tui.tabella(['Email'], []) != ''
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'manutenzione'`

- [ ] **Step 3: Write minimal implementation**

`manutenzione_lib/__init__.py`:

```python
"""Strumento di manutenzione a riga di comando di MedInventory."""
```

`manutenzione_lib/tui.py`:

```python
"""Presentazione a terminale: colori, tabelle, prompt.

Non importa nulla del dominio, e nessuna funzione stampa: tutte
restituiscono stringhe. Cosi' i test non devono catturare stdout, e il
menu resta l'unico posto che parla con l'utente.
"""
import sys

COLORI = {
    'verde':     '\033[92m',
    'giallo':    '\033[93m',
    'rosso':     '\033[91m',
    'ciano':     '\033[96m',
    'grassetto': '\033[1m',
}
AZZERA = '\033[0m'

# Marcatori testuali usati quando il colore non e' disponibile. Stessa scelta
# di migrate.py, cosi' i due strumenti si leggono allo stesso modo.
MARCATORI = {'ok': '[OK]', 'avviso': '[!!]', 'errore': '[ERR]'}
COLORE_GRAVITA = {'ok': 'verde', 'avviso': 'giallo', 'errore': 'rosso'}


def supporta_colore():
    return hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()


def colora(testo, colore):
    if not supporta_colore() or colore not in COLORI:
        return testo
    return f'{COLORI[colore]}{testo}{AZZERA}'


def titolo(testo):
    if supporta_colore():
        return colora(testo, 'grassetto')
    return f'=== {testo} ==='


def riga_esito(gravita, testo):
    return f'{MARCATORI[gravita]} {colora(testo, COLORE_GRAVITA[gravita])}'


def campo(etichetta, valore, larghezza=12):
    return f'  {etichetta.ljust(larghezza)} {valore}'


def separatore(larghezza=60):
    return '-' * larghezza


def tabella(intestazioni, righe):
    """Colonne allineate sulla cella piu' larga di ciascuna.

    Le celle sono convertite con str() dal chiamante o qui: una colonna di
    conteggi arriva come int e non deve far esplodere len().
    """
    celle = [[str(c) for c in riga] for riga in righe]
    larghezze = [len(str(t)) for t in intestazioni]
    for riga in celle:
        for i, valore in enumerate(riga):
            if i < len(larghezze):
                larghezze[i] = max(larghezze[i], len(valore))

    def formatta(valori):
        return '  ' + '  '.join(
            str(v).ljust(larghezze[i]) for i, v in enumerate(valori))

    linee = [formatta(intestazioni),
             '  ' + '  '.join('-' * l for l in larghezze)]
    linee.extend(formatta(riga) for riga in celle)
    return '\n'.join(linee)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: PASS, 4 test.

- [ ] **Step 5: Commit**

```bash
git add manutenzione_lib/__init__.py manutenzione_lib/tui.py tests/test_manutenzione.py
git commit -m "feat(manutenzione): la presentazione a terminale, senza dominio dentro"
```

---

### Task 2: `stato.py` — raccolta dei parametri

**Files:**
- Create: `manutenzione_lib/stato.py`
- Test: `tests/test_manutenzione.py` (aggiunge)

**Interfaces:**
- Consumes: niente del package.
- Produces:
  - `raccogli(conn, config: dict, radice: str) -> dict` con le chiavi
    `database`, `schema`, `modalita`, `utenti`, `dati`, `uploads`, `ai`, `posta`, `backup`.
    Ogni sezione e' un dizionario; una sezione non calcolabile vale
    `{'disponibile': False, 'motivo': str}`.
  - `tabella_esiste(conn, nome: str) -> bool`

La fixture `conn` sotto serve a tutti i task successivi: va scritta ora.

- [ ] **Step 1: Write the failing test**

Aggiungi in cima al file, dopo gli import esistenti:

```python
from manutenzione_lib import stato


@pytest.fixture
def conn(app):
    """Connessione grezza al database di prova.

    Passa dalla fixture 'app' perche' e' create_app() ad applicare lo schema,
    ma poi lavora in sqlite3 puro: i moduli di manutenzione_lib/ non conoscono
    Flask, e i test devono esercitarli come li esercita manutenzione.py.
    """
    c = sqlite3.connect(app.config['DATABASE_PATH'])
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys = ON')
    yield c
    c.close()


def _struttura(conn, nome='Casa di Cura Alfa'):
    cur = conn.execute("INSERT INTO strutture (nome) VALUES (?)", (nome,))
    conn.commit()
    return cur.lastrowid


def _utente(conn, email, ruolo='admin', struttura_id=None, attivo=1,
            password='Password1', password_hash=None):
    from werkzeug.security import generate_password_hash
    cur = conn.execute(
        """INSERT INTO utenti (email, password_hash, nome, cognome, ruolo,
                               struttura_id, attivo)
           VALUES (?, ?, 'Nome', 'Cognome', ?, ?, ?)""",
        (email, password_hash or generate_password_hash(password),
         ruolo, struttura_id, attivo))
    conn.commit()
    return cur.lastrowid
```

E i test veri e propri:

```python
def test_lo_stato_riporta_database_schema_e_utenti(conn, app, tmp_path):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    _utente(conn, 'super@alfa.it', 'superadmin', None)

    fotografia = stato.raccogli(conn, {'single_struttura': False}, str(tmp_path))

    assert fotografia['database']['integrity_check'] == 'ok'
    assert fotografia['schema']['user_version'] >= 0
    assert fotografia['modalita']['single_struttura'] is False
    assert fotografia['modalita']['strutture'] == 1
    assert fotografia['utenti']['totale_attivi'] == 2
    assert fotografia['utenti']['per_ruolo']['admin'] == 1
    assert fotografia['utenti']['per_ruolo']['superadmin'] == 1


def test_lo_stato_non_espone_mai_le_chiavi(conn, tmp_path):
    config = {
        'default_ai_provider': 'anthropic',
        'default_anthropic_api_key': 'sk-ant-segretissima',
        'smtp_host': 'smtp.esempio.it',
        'smtp_password': 'password-in-chiaro',
    }
    fotografia = stato.raccogli(conn, config, str(tmp_path))

    import json
    reso = json.dumps(fotografia)
    assert 'sk-ant-segretissima' not in reso
    assert 'password-in-chiaro' not in reso
    # Ma deve dire che ci sono.
    assert fotografia['ai']['chiavi']['anthropic'] is True
    assert fotografia['posta']['smtp_host'] == 'smtp.esempio.it'


def test_lo_stato_sopravvive_a_uno_schema_incompleto(conn, tmp_path):
    """L'installazione vecchia e' esattamente il caso da ispezionare.

    Se una tabella manca, la sezione si dichiara non disponibile e la
    raccolta prosegue: fermarsi qui vorrebbe dire non poter guardare proprio
    i database che hanno bisogno dello strumento.
    """
    conn.execute('DROP TABLE verifiche')
    conn.commit()

    fotografia = stato.raccogli(conn, {}, str(tmp_path))

    assert fotografia['dati']['disponibile'] is False
    assert 'verifiche' in fotografia['dati']['motivo']
    # Il resto e' stato raccolto lo stesso.
    assert fotografia['database']['integrity_check'] == 'ok'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: FAIL con `ImportError: cannot import name 'stato'`

- [ ] **Step 3: Write minimal implementation**

`manutenzione_lib/stato.py`:

```python
"""Fotografia di un'installazione: percorsi, versioni, conteggi.

Raccoglie e basta. Non giudica (quello e' diagnosi.py) e non stampa (quello
e' tui.py). Il risultato e' un dizionario, cosi' '--json' puo' emetterlo
identico e la TUI formattarlo.

Nessun segreto entra nel dizionario: delle chiavi API e delle password si
riporta solo se ci sono. Il dizionario finisce nei log e negli incolla di
chi chiede assistenza.
"""
import os
import sqlite3

TABELLE_DATI = ('apparecchi', 'manutenzioni', 'verifiche', 'documenti', 'accessori')
PROVIDER_CHIAVI = {
    'anthropic': 'default_anthropic_api_key',
    'gemini': 'default_gemini_api_key',
    'openai': 'default_openai_api_key',
}


def tabella_esiste(conn, nome):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (nome,)).fetchone() is not None


def _non_disponibile(motivo):
    return {'disponibile': False, 'motivo': motivo}


def _sezione_database(conn):
    percorso = None
    for _seq, nome, file in conn.execute('PRAGMA database_list'):
        if nome == 'main':
            percorso = file
    sezione = {
        'disponibile': True,
        'percorso': percorso,
        'dimensione_byte': os.path.getsize(percorso) if percorso and os.path.exists(percorso) else 0,
        'journal_mode': conn.execute('PRAGMA journal_mode').fetchone()[0],
        'integrity_check': conn.execute('PRAGMA integrity_check').fetchone()[0],
        'foreign_key_check': [tuple(r) for r in conn.execute('PRAGMA foreign_key_check').fetchall()],
    }
    return sezione


def _sezione_schema(conn):
    import migrate
    versione, uv = migrate.describe_version(conn)
    pendenti = [m.id for m in migrate.MIGRATIONS if not m.applied(conn)]
    return {'disponibile': True, 'versione': versione,
            'user_version': uv, 'pendenti': pendenti}


def _sezione_modalita(conn, config):
    strutture = []
    if tabella_esiste(conn, 'strutture'):
        strutture = [{'id': r['id'], 'nome': r['nome']}
                     for r in conn.execute('SELECT id, nome FROM strutture ORDER BY id')]
    return {'disponibile': True,
            'single_struttura': bool(config.get('single_struttura', False)),
            'strutture': len(strutture),
            'elenco': strutture}


def _sezione_utenti(conn):
    if not tabella_esiste(conn, 'utenti'):
        return _non_disponibile("la tabella 'utenti' non esiste")
    colonne = {r[1] for r in conn.execute('PRAGMA table_info(utenti)')}
    cancellati = 0
    if 'eliminato_il' in colonne:
        cancellati = conn.execute(
            'SELECT COUNT(*) FROM utenti WHERE eliminato_il IS NOT NULL').fetchone()[0]
    per_ruolo = {r[0]: r[1] for r in conn.execute(
        'SELECT ruolo, COUNT(*) FROM utenti WHERE attivo = 1 GROUP BY ruolo')}
    return {
        'disponibile': True,
        'totale': conn.execute('SELECT COUNT(*) FROM utenti').fetchone()[0],
        'totale_attivi': conn.execute(
            'SELECT COUNT(*) FROM utenti WHERE attivo = 1').fetchone()[0],
        'disattivati': conn.execute(
            'SELECT COUNT(*) FROM utenti WHERE attivo = 0').fetchone()[0],
        'cancellati': cancellati,
        'per_ruolo': per_ruolo,
    }


def _sezione_dati(conn):
    conteggi = {}
    for tabella in TABELLE_DATI:
        if not tabella_esiste(conn, tabella):
            return _non_disponibile(f"la tabella '{tabella}' non esiste")
        conteggi[tabella] = conn.execute(f'SELECT COUNT(*) FROM {tabella}').fetchone()[0]
    conteggi['disponibile'] = True
    return conteggi


def _sezione_uploads(conn, config, radice):
    percorso = config.get('uploads_path', 'uploads')
    if not os.path.isabs(percorso):
        percorso = os.path.join(radice, percorso)
    if not os.path.isdir(percorso):
        return {'disponibile': False, 'percorso': percorso,
                'motivo': 'la cartella non esiste'}

    file_presenti, byte_totali = 0, 0
    for cartella, _sotto, nomi in os.walk(percorso):
        for nome in nomi:
            file_presenti += 1
            try:
                byte_totali += os.path.getsize(os.path.join(cartella, nome))
            except OSError:
                pass

    sezione = {'disponibile': True, 'percorso': percorso,
               'file': file_presenti, 'byte': byte_totali}
    try:
        import pulisci_uploads
        referenziati = pulisci_uploads.percorsi_referenziati(conn)
        orfani, byte_orfani = pulisci_uploads.trova_orfani(percorso, referenziati)
        sezione['orfani'] = len(orfani)
        sezione['byte_orfani'] = byte_orfani
        sezione['mancanti'] = sorted(
            r for r in referenziati
            if not os.path.exists(os.path.join(percorso, r)))
    except Exception as e:
        # ColonnaMancante su schema vecchio: il conteggio dei file resta
        # valido, l'analisi degli orfani no. Dichiararla impossibile e'
        # l'unica risposta onesta - vedi il docstring di pulisci_uploads.
        sezione['orfani'] = None
        sezione['motivo_orfani'] = str(e)
    return sezione


def _sezione_ai(config):
    return {
        'disponibile': True,
        'provider': config.get('default_ai_provider'),
        'chiavi': {nome: bool(config.get(chiave))
                   for nome, chiave in PROVIDER_CHIAVI.items()},
        'base_url_locale': config.get('default_ai_local_base_url'),
        'modello_import': config.get('default_ai_import_model'),
    }


def _sezione_posta(config):
    return {
        'disponibile': True,
        'smtp_host': config.get('smtp_host'),
        'smtp_port': config.get('smtp_port'),
        'smtp_password_presente': bool(config.get('smtp_password')),
        'imap_host': config.get('imap_host'),
        'imap_password_presente': bool(config.get('imap_password')),
    }


def _sezione_backup(config, radice):
    percorso = config.get('backups_path', 'backups')
    if not os.path.isabs(percorso):
        percorso = os.path.join(radice, percorso)
    if not os.path.isdir(percorso):
        return {'disponibile': False, 'percorso': percorso,
                'motivo': 'la cartella non esiste'}
    try:
        import backup_service
        elenco = backup_service.list_backups(percorso)
    except Exception as e:
        return {'disponibile': False, 'percorso': percorso, 'motivo': str(e)}
    return {'disponibile': True, 'percorso': percorso, 'numero': len(elenco),
            'ultimo': elenco[0]['filename'] if elenco else None}


def raccogli(conn, config, radice):
    """Fotografia completa. Ogni sezione fallisce per conto suo.

    Una sezione che non si puo' calcolare vale {'disponibile': False,
    'motivo': ...} e la raccolta prosegue: un database a schema vecchio deve
    restare ispezionabile, e' il motivo per cui esiste questo strumento.
    """
    fotografia = {}
    sezioni = (
        ('database', lambda: _sezione_database(conn)),
        ('schema',   lambda: _sezione_schema(conn)),
        ('modalita', lambda: _sezione_modalita(conn, config)),
        ('utenti',   lambda: _sezione_utenti(conn)),
        ('dati',     lambda: _sezione_dati(conn)),
        ('uploads',  lambda: _sezione_uploads(conn, config, radice)),
        ('ai',       lambda: _sezione_ai(config)),
        ('posta',    lambda: _sezione_posta(config)),
        ('backup',   lambda: _sezione_backup(config, radice)),
    )
    for nome, calcola in sezioni:
        try:
            fotografia[nome] = calcola()
        except (sqlite3.Error, OSError, ImportError) as e:
            fotografia[nome] = _non_disponibile(str(e))
    return fotografia
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: PASS, 7 test.

- [ ] **Step 5: Commit**

```bash
git add manutenzione_lib/stato.py tests/test_manutenzione.py
git commit -m "feat(manutenzione): la fotografia dell'installazione, segreti esclusi"
```

---

### Task 3: `utenti.py` — lettura e impronte

Prima della diagnosi, perche' due controlli ne dipendono.

**Files:**
- Create: `manutenzione_lib/utenti.py`
- Test: `tests/test_manutenzione.py` (aggiunge)

**Interfaces:**
- Consumes: niente del package.
- Produces:
  - `stato_impronta(password_hash: str) -> str` — `'ok'` | `'metodo_sconosciuto'` | `'malformata'`
  - `elenco(conn, struttura_id=None) -> list[dict]` con chiavi `id`, `email`, `nome`, `cognome`, `ruolo`, `struttura_id`, `attivo`, `eliminato_il`, `impronta`
  - `imposta_password(conn, email: str, password: str) -> int` (id dell'utente)
  - `crea_accesso(conn, email, password, ruolo, struttura_id=None, nome='Nome', cognome='Cognome') -> int`
  - `valida_password(password: str) -> list[str]` (elenco degli errori, vuoto se valida)
  - eccezioni `UtenteInesistente`, `EmailGiaInUso`, `PasswordDebole`

- [ ] **Step 1: Write the failing test**

```python
from manutenzione_lib import utenti as mutenti


def test_riconosce_le_impronte_che_werkzeug_sa_verificare():
    from werkzeug.security import generate_password_hash
    assert mutenti.stato_impronta(generate_password_hash('Password1')) == 'ok'
    assert mutenti.stato_impronta(
        generate_password_hash('Password1', method='pbkdf2:sha256')) == 'ok'


def test_riconosce_l_impronta_che_fa_esplodere_il_login():
    """Il caso dell'installazione migrata da werkzeug 2.

    check_password_hash SOLLEVA ValueError su un metodo che non conosce piu',
    non restituisce False: auth.py:422 non la cattura, quindi il login
    risponde 500 invece di rifiutare le credenziali. E' la ragione per cui
    questo controllo esiste.
    """
    from werkzeug.security import check_password_hash
    vecchia = 'sha256$abcdef$0123456789'
    assert mutenti.stato_impronta(vecchia) == 'metodo_sconosciuto'
    with pytest.raises(ValueError):
        check_password_hash(vecchia, 'qualunque')


def test_riconosce_l_impronta_senza_forma():
    """Il sentinella di utente_service non ha la forma metodo$sale$impronta.

    check_password_hash torna False senza sollevare: e' voluto, un account
    distrutto deve rifiutare, non esplodere.
    """
    from werkzeug.security import check_password_hash
    from utente_service import PASSWORD_INUTILIZZABILE
    assert mutenti.stato_impronta(PASSWORD_INUTILIZZABILE) == 'malformata'
    assert check_password_hash(PASSWORD_INUTILIZZABILE, 'qualunque') is False


def test_elenco_riporta_lo_stato_di_ogni_impronta(conn):
    sid = _struttura(conn)
    _utente(conn, 'buono@alfa.it', 'admin', sid)
    _utente(conn, 'vecchio@alfa.it', 'utente', sid,
            password_hash='sha256$sale$impronta')

    righe = {r['email']: r for r in mutenti.elenco(conn)}
    assert righe['buono@alfa.it']['impronta'] == 'ok'
    assert righe['vecchio@alfa.it']['impronta'] == 'metodo_sconosciuto'


def test_elenco_ristretto_a_una_struttura(conn):
    alfa = _struttura(conn, 'Alfa')
    beta = _struttura(conn, 'Beta')
    _utente(conn, 'a@alfa.it', 'admin', alfa)
    _utente(conn, 'b@beta.it', 'admin', beta)
    _utente(conn, 'super@x.it', 'superadmin', None)

    email = {r['email'] for r in mutenti.elenco(conn, struttura_id=alfa)}
    assert email == {'a@alfa.it'}


def test_imposta_password_rende_verificabile_una_impronta_rotta(conn):
    from werkzeug.security import check_password_hash
    sid = _struttura(conn)
    _utente(conn, 'vecchio@alfa.it', 'admin', sid,
            password_hash='sha256$sale$impronta')

    mutenti.imposta_password(conn, 'vecchio@alfa.it', 'NuovaPassword1')
    conn.commit()

    riga = conn.execute("SELECT password_hash, attivo, primo_accesso "
                        "FROM utenti WHERE email = ?", ('vecchio@alfa.it',)).fetchone()
    assert check_password_hash(riga['password_hash'], 'NuovaPassword1')
    assert riga['attivo'] == 1
    assert riga['primo_accesso'] == 1


def test_imposta_password_rifiuta_un_indirizzo_inesistente(conn):
    with pytest.raises(mutenti.UtenteInesistente):
        mutenti.imposta_password(conn, 'nessuno@alfa.it', 'NuovaPassword1')


def test_password_debole_rifiutata(conn):
    assert mutenti.valida_password('corta') != []
    assert mutenti.valida_password('tuttominuscolo1') != []
    assert mutenti.valida_password('SenzaNumeri') != []
    assert mutenti.valida_password('Password1') == []
    with pytest.raises(mutenti.PasswordDebole):
        mutenti.crea_accesso(conn, 'nuovo@alfa.it', 'corta', 'superadmin')


def test_crea_accesso_rifiuta_una_email_gia_presente(conn):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    with pytest.raises(mutenti.EmailGiaInUso):
        mutenti.crea_accesso(conn, 'admin@alfa.it', 'Password1', 'admin', sid)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: FAIL con `ImportError: cannot import name 'utenti'`

- [ ] **Step 3: Write minimal implementation**

`manutenzione_lib/utenti.py`:

```python
"""Operazioni sugli account, fuori da Flask.

Riceve una sqlite3.Connection e non apre transazioni proprie: le apre e le
chiude il chiamante, come in utente_service.py e struttura_service.py. E'
cio' che permette a manutenzione.py di azzerare gli utenti e creare
l'accesso di rimpiazzo dentro la stessa transazione.
"""
from datetime import datetime

from werkzeug.security import generate_password_hash

# I due soli metodi che werkzeug.security._hash_internal implementa oggi.
# Tutto il resto fa SOLLEVARE check_password_hash, non restituire False:
# vedi stato_impronta.
METODI_VERIFICABILI = ('pbkdf2', 'scrypt')


class UtenteInesistente(ValueError):
    pass


class EmailGiaInUso(ValueError):
    pass


class PasswordDebole(ValueError):
    pass


def valida_password(password):
    """Stesse regole di crea_superadmin.valida_password, che le aveva per primo."""
    errori = []
    if len(password or '') < 8:
        errori.append('almeno 8 caratteri')
    if not any(c.isupper() for c in password or ''):
        errori.append('almeno una lettera maiuscola')
    if not any(c.isdigit() for c in password or ''):
        errori.append('almeno un numero')
    return errori


def stato_impronta(password_hash):
    """Come si comportera' check_password_hash davanti a questa impronta.

    - 'ok'                 la verifica avviene, e dira' vero o falso;
    - 'metodo_sconosciuto' la verifica SOLLEVA ValueError. auth.py:422 non la
                           cattura: il login risponde 500. E' il regalo di una
                           migrazione da werkzeug 2, dove le impronte erano
                           'sha256$sale$impronta';
    - 'malformata'         non ha la forma metodo$sale$impronta, quindi
                           check_password_hash torna False senza sollevare
                           (e' il caso del sentinella '!utente-eliminato').
    """
    parti = (password_hash or '').split('$', 2)
    if len(parti) != 3:
        return 'malformata'
    metodo = parti[0].split(':')[0]
    return 'ok' if metodo in METODI_VERIFICABILI else 'metodo_sconosciuto'


def elenco(conn, struttura_id=None):
    sql = ("SELECT id, email, nome, cognome, ruolo, struttura_id, attivo, "
           "       eliminato_il, password_hash "
           "FROM utenti")
    parametri = ()
    if struttura_id is not None:
        sql += ' WHERE struttura_id = ?'
        parametri = (struttura_id,)
    sql += ' ORDER BY ruolo, email'

    righe = []
    for r in conn.execute(sql, parametri):
        voce = dict(r)
        voce['impronta'] = stato_impronta(voce.pop('password_hash'))
        righe.append(voce)
    return righe


def _adesso():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def imposta_password(conn, email, password):
    """Nuova password per un account esistente, che viene anche riattivato.

    Riattivare fa parte dell'operazione: chi arriva qui lo fa perche' non
    riesce ad entrare, e un attivo = 0 lasciato in piedi restituirebbe
    'credenziali non valide' su una password appena impostata - esattamente
    il vicolo cieco che questo strumento serve a togliere di mezzo.
    """
    errori = valida_password(password)
    if errori:
        raise PasswordDebole(', '.join(errori))
    riga = conn.execute('SELECT id FROM utenti WHERE email = ?', (email,)).fetchone()
    if riga is None:
        raise UtenteInesistente(f"Nessun utente con indirizzo {email}.")
    conn.execute(
        "UPDATE utenti SET password_hash = ?, attivo = 1, primo_accesso = 1, "
        "reset_hash = NULL, reset_scadenza = NULL, updated_at = ? WHERE id = ?",
        (generate_password_hash(password), _adesso(), riga[0]))
    return riga[0]


def crea_accesso(conn, email, password, ruolo, struttura_id=None,
                 nome='Nome', cognome='Cognome'):
    errori = valida_password(password)
    if errori:
        raise PasswordDebole(', '.join(errori))
    if conn.execute('SELECT 1 FROM utenti WHERE email = ?', (email,)).fetchone():
        raise EmailGiaInUso(f"L'indirizzo {email} e' gia' in uso.")
    cur = conn.execute(
        """INSERT INTO utenti (email, password_hash, nome, cognome, ruolo,
                               struttura_id, primo_accesso, attivo)
           VALUES (?, ?, ?, ?, ?, ?, 1, 1)""",
        (email, generate_password_hash(password), nome, cognome, ruolo,
         struttura_id))
    return cur.lastrowid
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: PASS, 16 test.

- [ ] **Step 5: Commit**

```bash
git add manutenzione_lib/utenti.py tests/test_manutenzione.py
git commit -m "feat(manutenzione): gli account, e l'impronta che fa esplodere il login"
```

---

### Task 4: `diagnosi.py` — i controlli

**Files:**
- Create: `manutenzione_lib/diagnosi.py`
- Test: `tests/test_manutenzione.py` (aggiunge)

**Interfaces:**
- Consumes: `stato.raccogli`, `utenti.stato_impronta`.
- Produces:
  - `Esito` — `dataclass` con `gravita: str`, `titolo: str`, `dettaglio: str`, `rimedio: str`
  - `esegui(conn, config: dict, fotografia: dict) -> list[Esito]`
  - `ci_sono_errori(esiti: list[Esito]) -> bool`
  - `CONTROLLI` — tupla delle funzioni di controllo, ognuna `(conn, config, fotografia) -> Esito | None`

- [ ] **Step 1: Write the failing test**

```python
from manutenzione_lib import diagnosi


def _diagnostica(conn, tmp_path, config=None):
    config = config or {}
    fotografia = stato.raccogli(conn, config, str(tmp_path))
    return diagnosi.esegui(conn, config, fotografia)


def _titoli(esiti):
    return {e.titolo for e in esiti}


def test_un_database_sano_con_un_admin_non_produce_errori(conn, tmp_path):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    esiti = _diagnostica(conn, tmp_path)
    assert not diagnosi.ci_sono_errori(esiti), [
        (e.gravita, e.titolo, e.dettaglio) for e in esiti if e.gravita == 'errore']


def test_nessun_utente_attivo_e_un_errore(conn, tmp_path):
    _struttura(conn)
    esiti = _diagnostica(conn, tmp_path)
    assert 'Nessun utente attivo' in _titoli(esiti)
    assert diagnosi.ci_sono_errori(esiti)


def test_struttura_senza_amministratore_attivo(conn, tmp_path):
    sid = _struttura(conn, 'Alfa')
    _utente(conn, 'super@x.it', 'superadmin', None)
    _utente(conn, 'utente@alfa.it', 'utente', sid)
    esiti = _diagnostica(conn, tmp_path)
    assert 'Struttura senza amministratore attivo' in _titoli(esiti)


def test_impronta_non_verificabile_e_un_errore_con_il_rimedio_giusto(conn, tmp_path):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    _utente(conn, 'vecchio@alfa.it', 'utente', sid,
            password_hash='sha256$sale$impronta')

    esiti = _diagnostica(conn, tmp_path)
    guasto = [e for e in esiti if e.titolo == 'Password non verificabile']
    assert len(guasto) == 1
    assert guasto[0].gravita == 'errore'
    assert 'vecchio@alfa.it' in guasto[0].dettaglio
    assert 'utenti password' in guasto[0].rimedio


def test_utente_disattivato_e_un_avviso_che_spiega_il_messaggio_di_login(conn, tmp_path):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    _utente(conn, 'spento@alfa.it', 'utente', sid, attivo=0)

    esiti = _diagnostica(conn, tmp_path)
    avviso = [e for e in esiti if e.titolo == 'Utenti disattivati']
    assert avviso and avviso[0].gravita == 'avviso'
    assert 'spento@alfa.it' in avviso[0].dettaglio
    # Non deve far fallire il codice di uscita.
    assert not diagnosi.ci_sono_errori(esiti)


def test_blocco_per_tentativi_ripetuti_segnalato(conn, tmp_path):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    for _ in range(5):
        conn.execute("INSERT INTO login_attempts (ip_address, email, esito) "
                     "VALUES ('10.0.0.1', 'admin@alfa.it', 'fallito')")
    conn.commit()

    esiti = _diagnostica(conn, tmp_path)
    assert 'Accessi bloccati per tentativi ripetuti' in _titoli(esiti)


def test_migrazioni_pendenti_sono_un_errore(conn, tmp_path, monkeypatch):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    fotografia = stato.raccogli(conn, {}, str(tmp_path))
    fotografia['schema']['pendenti'] = ['v2.3']

    esiti = diagnosi.esegui(conn, {}, fotografia)
    pendenti = [e for e in esiti if e.titolo == 'Migrazioni non applicate']
    assert pendenti and pendenti[0].gravita == 'errore'
    assert 'migra' in pendenti[0].rimedio


def test_modalita_incoerente_col_numero_di_strutture(conn, tmp_path):
    sid = _struttura(conn, 'Alfa')
    _struttura(conn, 'Beta')
    _utente(conn, 'admin@alfa.it', 'admin', sid)

    esiti = _diagnostica(conn, tmp_path, config={'single_struttura': True})
    assert 'Modalita\' incoerente' in _titoli(esiti)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: FAIL con `ImportError: cannot import name 'diagnosi'`

- [ ] **Step 3: Write minimal implementation**

`manutenzione_lib/diagnosi.py`:

```python
"""I controlli su un'installazione.

Ogni controllo e' una funzione (conn, config, fotografia) -> Esito | None:
None quando va tutto bene. Nessuno stampa e nessuno esce - la presentazione
sta in manutenzione.py, cosi' i controlli si testano chiamandoli.

Il rimedio non e' una frase, e' il comando da eseguire: chi legge la
diagnosi su un'installazione altrui non deve dover indovinare il seguito.
"""
from dataclasses import dataclass

from manutenzione_lib import stato as mstato
from manutenzione.utenti import stato_impronta

# Finestra e soglia del blocco per tentativi, le stesse che applica auth.py.
BLOCCO_MINUTI = 30
BLOCCO_TENTATIVI = 5


@dataclass
class Esito:
    gravita: str     # 'errore' | 'avviso'
    titolo: str
    dettaglio: str
    rimedio: str


def _elenco_breve(valori, massimo=5):
    valori = list(valori)
    if len(valori) <= massimo:
        return ', '.join(valori)
    return ', '.join(valori[:massimo]) + f' (e altri {len(valori) - massimo})'


def controllo_integrita(conn, config, fotografia):
    sezione = fotografia.get('database', {})
    if not sezione.get('disponibile'):
        return None
    if sezione.get('integrity_check') != 'ok':
        return Esito('errore', 'Database corrotto',
                     sezione['integrity_check'],
                     'Ripristina un backup: python manutenzione.py backup --elenca')
    return None


def controllo_chiavi_esterne(conn, config, fotografia):
    sezione = fotografia.get('database', {})
    violazioni = sezione.get('foreign_key_check') or []
    if violazioni:
        return Esito('errore', 'Riferimenti pendenti',
                     f'{len(violazioni)} violazioni di chiave esterna, '
                     f'prima fra tutte {violazioni[0]}',
                     'Ripristina un backup: python manutenzione.py backup --elenca')
    return None


def controllo_migrazioni(conn, config, fotografia):
    pendenti = (fotografia.get('schema') or {}).get('pendenti') or []
    if pendenti:
        return Esito('errore', 'Migrazioni non applicate',
                     f"Da applicare: {', '.join(pendenti)}",
                     'python manutenzione.py migra')
    return None


def controllo_nessun_utente_attivo(conn, config, fotografia):
    sezione = fotografia.get('utenti') or {}
    if not sezione.get('disponibile'):
        return None
    if sezione.get('totale_attivi', 0) == 0:
        return Esito('errore', 'Nessun utente attivo',
                     "Nessuno puo' entrare in questa installazione.",
                     'python manutenzione.py utenti superadmin')
    return None


def controllo_strutture_senza_admin(conn, config, fotografia):
    if not mstato.tabella_esiste(conn, 'strutture'):
        return None
    orfane = [r['nome'] for r in conn.execute(
        """SELECT s.nome FROM strutture s
           WHERE NOT EXISTS (
               SELECT 1 FROM utenti u
               WHERE u.struttura_id = s.id AND u.ruolo = 'admin'
                 AND u.attivo = 1 AND u.eliminato_il IS NULL)""")]
    if orfane:
        return Esito('errore', 'Struttura senza amministratore attivo',
                     _elenco_breve(orfane),
                     'python manutenzione.py utenti elenca')
    return None


def controllo_impronte(conn, config, fotografia):
    if not mstato.tabella_esiste(conn, 'utenti'):
        return None
    rotte = [r['email'] for r in conn.execute(
        'SELECT email, password_hash FROM utenti WHERE attivo = 1')
        if stato_impronta(r['password_hash']) == 'metodo_sconosciuto']
    if rotte:
        return Esito(
            'errore', 'Password non verificabile',
            f"Impronta in un formato che werkzeug non sa piu' verificare: "
            f"{_elenco_breve(rotte)}. Il login solleva un'eccezione e "
            f"risponde 500, non 'credenziali non valide'.",
            'python manutenzione.py utenti password <email>')
    return None


def controllo_utenti_disattivati(conn, config, fotografia):
    if not mstato.tabella_esiste(conn, 'utenti'):
        return None
    spenti = [r['email'] for r in conn.execute(
        "SELECT email FROM utenti WHERE attivo = 0 AND eliminato_il IS NULL")]
    if spenti:
        return Esito(
            'avviso', 'Utenti disattivati',
            f"Ricevono 'credenziali non valide' come chi sbaglia password: "
            f"{_elenco_breve(spenti)}",
            'python manutenzione.py utenti password <email>')
    return None


def controllo_blocco_accessi(conn, config, fotografia):
    if not mstato.tabella_esiste(conn, 'login_attempts'):
        return None
    bloccate = [r['email'] for r in conn.execute(
        f"""SELECT email, COUNT(*) AS n FROM login_attempts
            WHERE esito = 'fallito'
              AND created_at > datetime('now', '-{BLOCCO_MINUTI} minutes')
            GROUP BY email HAVING n >= {BLOCCO_TENTATIVI}""")]
    if bloccate:
        return Esito(
            'avviso', 'Accessi bloccati per tentativi ripetuti',
            f'Bloccati per {BLOCCO_MINUTI} minuti: {_elenco_breve(bloccate)}',
            "Attendi la scadenza, oppure svuota login_attempts per quell'indirizzo")
    return None


def controllo_account_cancellati(conn, config, fotografia):
    if not mstato.tabella_esiste(conn, 'utenti'):
        return None
    cancellati = [r['email'] for r in conn.execute(
        "SELECT email FROM utenti WHERE email LIKE '%#eliminato-%'")]
    if cancellati:
        return Esito('avviso', 'Account cancellati',
                     f'Righe storiche, non piu\' utilizzabili per entrare: '
                     f'{_elenco_breve(cancellati)}',
                     'Nessuna azione necessaria')
    return None


def controllo_modalita(conn, config, fotografia):
    sezione = fotografia.get('modalita') or {}
    if not sezione.get('disponibile'):
        return None
    if sezione.get('single_struttura') and sezione.get('strutture', 0) > 1:
        return Esito('avviso', "Modalita' incoerente",
                     f"single_struttura e' attiva ma le strutture sono "
                     f"{sezione['strutture']}",
                     'python manutenzione.py modalita --multi')
    return None


def controllo_uploads(conn, config, fotografia):
    sezione = fotografia.get('uploads') or {}
    if not sezione.get('disponibile'):
        return Esito('errore', 'Cartella uploads assente',
                     f"{sezione.get('percorso')}: {sezione.get('motivo')}",
                     'Crea la cartella o correggi uploads_path in config.local.json')
    if sezione.get('mancanti'):
        return Esito('avviso', 'Allegati mancanti sul disco',
                     f"{len(sezione['mancanti'])} righe puntano a file che non ci sono, "
                     f"la prima e' {sezione['mancanti'][0]}",
                     'Ripristina un backup, o rimuovi i riferimenti dalle schede')
    if sezione.get('orfani'):
        return Esito('avviso', 'File orfani',
                     f"{sezione['orfani']} file che nessuna riga referenzia",
                     'python manutenzione.py uploads --elimina')
    return None


def controllo_chiavi_ai(conn, config, fotografia):
    sezione = fotografia.get('ai') or {}
    provider = sezione.get('provider')
    if provider in ('anthropic', 'gemini', 'openai') and not sezione['chiavi'].get(provider):
        return Esito('avviso', 'Chiave AI assente',
                     f"Il provider predefinito e' {provider} ma la chiave globale manca",
                     'Impostala in config.local.json o per struttura dall\'interfaccia')
    return None


def controllo_posta(conn, config, fotografia):
    sezione = fotografia.get('posta') or {}
    if sezione.get('smtp_host'):
        return None
    if not mstato.tabella_esiste(conn, 'strutture_config'):
        return None
    attivi = conn.execute(
        "SELECT COUNT(*) FROM strutture_config "
        "WHERE chiave = 'avvisi_scadenza_attivi' AND valore IN ('1', 'true')"
    ).fetchone()[0]
    if attivi:
        return Esito('avviso', 'Avvisi attivi senza SMTP',
                     f'{attivi} strutture hanno gli avvisi di scadenza attivi '
                     f"ma il server di posta non e' configurato",
                     'Configura smtp_host in config.local.json')
    return None


def controllo_sessioni_scadute(conn, config, fotografia):
    if not mstato.tabella_esiste(conn, 'sessioni'):
        return None
    scadute = conn.execute(
        "SELECT COUNT(*) FROM sessioni WHERE expires_at <= datetime('now')").fetchone()[0]
    if scadute > 100:
        return Esito('avviso', 'Sessioni scadute accumulate',
                     f'{scadute} righe scadute in sessioni',
                     "Lo scheduler le pulisce all'avvio dell'applicazione")
    return None


CONTROLLI = (
    controllo_integrita,
    controllo_chiavi_esterne,
    controllo_migrazioni,
    controllo_nessun_utente_attivo,
    controllo_strutture_senza_admin,
    controllo_impronte,
    controllo_utenti_disattivati,
    controllo_blocco_accessi,
    controllo_account_cancellati,
    controllo_modalita,
    controllo_uploads,
    controllo_chiavi_ai,
    controllo_posta,
    controllo_sessioni_scadute,
)


def esegui(conn, config, fotografia):
    """Tutti i controlli, errori prima degli avvisi.

    Un controllo che esplode non ferma gli altri: diventa esso stesso un
    errore da mostrare. Su un database malmesso - il caso normale per questo
    strumento - una singola query che fallisce non deve nascondere le
    quattordici diagnosi rimaste.
    """
    esiti = []
    for controllo in CONTROLLI:
        try:
            risultato = controllo(conn, config, fotografia)
        except Exception as e:
            risultato = Esito('errore', f'Controllo fallito: {controllo.__name__}',
                              str(e), 'Segnalalo a Studio Bergamaschi')
        if risultato is not None:
            esiti.append(risultato)
    esiti.sort(key=lambda e: 0 if e.gravita == 'errore' else 1)
    return esiti


def ci_sono_errori(esiti):
    return any(e.gravita == 'errore' for e in esiti)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: PASS, 24 test.

- [ ] **Step 5: Commit**

```bash
git add manutenzione_lib/diagnosi.py tests/test_manutenzione.py
git commit -m "feat(manutenzione): i controlli che dicono perche' non si entra"
```

---

### Task 5: azzeramento degli utenti

**Files:**
- Modify: `manutenzione_lib/utenti.py`
- Test: `tests/test_manutenzione.py` (aggiunge)

**Interfaces:**
- Consumes: `utente_service.cancella_utente`, `utente_service.conteggi_riferimenti`, `struttura_service._rimuovi_utenti`, `struttura_service.RIFERIMENTI_UTENTE`.
- Produces:
  - `esiste_accesso_valido(conn, struttura_id=None) -> bool`
  - `azzera(conn, *, struttura_id=None, definitivo=False, rimpiazzo=None) -> dict` con chiavi `coinvolti` (lista di email originali), `semantica`, `rimpiazzo_id`
  - eccezione `AccessoNonGarantito`
  - `Rimpiazzo` — `dataclass` con `email`, `password`, `ruolo`, `struttura_id`, `nome`, `cognome`

- [ ] **Step 1: Write the failing test**

```python
def test_azzeramento_conservativo_lascia_i_dati_e_la_tracciabilita(conn):
    sid = _struttura(conn)
    uid = _utente(conn, 'admin@alfa.it', 'admin', sid)
    conn.execute("INSERT INTO apparecchi (struttura_id, modello, created_by) "
                 "VALUES (?, 'Defibrillatore', ?)", (sid, uid))
    conn.commit()

    esito = mutenti.azzera(conn, rimpiazzo=mutenti.Rimpiazzo(
        email='nuovo@alfa.it', password='Password1', ruolo='admin',
        struttura_id=sid))
    conn.commit()

    # L'apparecchio non si tocca, e continua a dire chi l'ha inserito.
    riga = conn.execute('SELECT modello, created_by FROM apparecchi').fetchone()
    assert riga['modello'] == 'Defibrillatore'
    assert riga['created_by'] == uid
    # L'account e' distrutto ma la riga resta come voce storica.
    vecchio = conn.execute('SELECT email, attivo, eliminato_il FROM utenti '
                           'WHERE id = ?', (uid,)).fetchone()
    assert vecchio['email'] == f'admin@alfa.it#eliminato-{uid}'
    assert vecchio['attivo'] == 0
    assert vecchio['eliminato_il'] is not None
    assert 'admin@alfa.it' in esito['coinvolti']


def test_azzeramento_definitivo_rimuove_le_righe_e_libera_i_riferimenti(conn):
    sid = _struttura(conn)
    uid = _utente(conn, 'admin@alfa.it', 'admin', sid)
    conn.execute("INSERT INTO apparecchi (struttura_id, modello, created_by) "
                 "VALUES (?, 'Defibrillatore', ?)", (sid, uid))
    conn.commit()

    mutenti.azzera(conn, definitivo=True, rimpiazzo=mutenti.Rimpiazzo(
        email='nuovo@alfa.it', password='Password1', ruolo='admin',
        struttura_id=sid))
    conn.commit()

    assert conn.execute('SELECT COUNT(*) FROM utenti WHERE id = ?',
                        (uid,)).fetchone()[0] == 0
    riga = conn.execute('SELECT modello, created_by FROM apparecchi').fetchone()
    assert riga['modello'] == 'Defibrillatore'
    assert riga['created_by'] is None


def test_azzerare_senza_rimpiazzo_e_rifiutato(conn):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)

    with pytest.raises(mutenti.AccessoNonGarantito):
        mutenti.azzera(conn)


def test_dopo_il_rifiuto_nulla_e_stato_scritto(conn):
    """Il rifiuto arriva DOPO le cancellazioni: e' il chiamante ad annullare
    la transazione. Qui si verifica che annullarla basti davvero."""
    sid = _struttura(conn)
    uid = _utente(conn, 'admin@alfa.it', 'admin', sid)

    with pytest.raises(mutenti.AccessoNonGarantito):
        mutenti.azzera(conn)
    conn.rollback()

    riga = conn.execute('SELECT email, attivo FROM utenti WHERE id = ?',
                        (uid,)).fetchone()
    assert riga['email'] == 'admin@alfa.it'
    assert riga['attivo'] == 1


def test_azzeramento_ristretto_a_una_struttura(conn):
    alfa = _struttura(conn, 'Alfa')
    beta = _struttura(conn, 'Beta')
    _utente(conn, 'a@alfa.it', 'admin', alfa)
    id_beta = _utente(conn, 'b@beta.it', 'admin', beta)
    id_super = _utente(conn, 'super@x.it', 'superadmin', None)

    mutenti.azzera(conn, struttura_id=alfa, rimpiazzo=mutenti.Rimpiazzo(
        email='nuovo@alfa.it', password='Password1', ruolo='admin',
        struttura_id=alfa))
    conn.commit()

    # Beta e il superadmin globale non sono stati toccati.
    assert conn.execute('SELECT email FROM utenti WHERE id = ?',
                        (id_beta,)).fetchone()['email'] == 'b@beta.it'
    assert conn.execute('SELECT email FROM utenti WHERE id = ?',
                        (id_super,)).fetchone()['email'] == 'super@x.it'


def test_su_una_struttura_un_superadmin_globale_basta_come_accesso(conn):
    alfa = _struttura(conn, 'Alfa')
    _utente(conn, 'a@alfa.it', 'admin', alfa)
    _utente(conn, 'super@x.it', 'superadmin', None)

    esito = mutenti.azzera(conn, struttura_id=alfa)
    conn.commit()

    assert esito['rimpiazzo_id'] is None
    assert esito['coinvolti'] == ['a@alfa.it']


def test_l_azzeramento_lascia_una_voce_nel_registro(conn):
    sid = _struttura(conn)
    _utente(conn, 'admin@alfa.it', 'admin', sid)

    mutenti.azzera(conn, rimpiazzo=mutenti.Rimpiazzo(
        email='nuovo@alfa.it', password='Password1', ruolo='admin',
        struttura_id=sid))
    conn.commit()

    voce = conn.execute(
        "SELECT utente_id, azione, dettagli FROM log_attivita "
        "WHERE azione = 'azzeramento_utenti'").fetchone()
    assert voce is not None
    assert voce['utente_id'] is None
    assert 'manutenzione.py' in voce['dettagli']


def test_le_sessioni_aperte_non_sopravvivono_all_azzeramento(conn):
    sid = _struttura(conn)
    uid = _utente(conn, 'admin@alfa.it', 'admin', sid)
    conn.execute("INSERT INTO sessioni (utente_id, token, expires_at) "
                 "VALUES (?, 'token-vivo', datetime('now', '+8 hours'))", (uid,))
    conn.commit()

    mutenti.azzera(conn, rimpiazzo=mutenti.Rimpiazzo(
        email='nuovo@alfa.it', password='Password1', ruolo='admin',
        struttura_id=sid))
    conn.commit()

    assert conn.execute('SELECT COUNT(*) FROM sessioni').fetchone()[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: FAIL con `AttributeError: module 'manutenzione.utenti' has no attribute 'azzera'`

- [ ] **Step 3: Write minimal implementation**

Aggiungi `from dataclasses import dataclass` agli import in cima a `manutenzione_lib/utenti.py`, poi in fondo al file:

```python
class AccessoNonGarantito(RuntimeError):
    """L'azzeramento lascerebbe l'installazione senza nessuno che possa entrare.

    Sollevata DOPO le cancellazioni, di proposito: il controllo interessante
    e' sullo stato finale, non su quello iniziale, ed e' il chiamante ad
    annullare la transazione. Se un giorno il criterio di 'accesso valido'
    cambia, questo resta il posto giusto per esprimerlo.
    """


@dataclass
class Rimpiazzo:
    email: str
    password: str
    ruolo: str = 'superadmin'
    struttura_id: int = None
    nome: str = 'Amministratore'
    cognome: str = 'Sistema'


def esiste_accesso_valido(conn, struttura_id=None):
    """C'e' qualcuno che puo' entrare, adesso?

    Non basta contare le righe: l'utente deve essere attivo, non cancellato,
    e la sua impronta deve essere verificabile - un admin con un
    'sha256$...' addosso non e' un accesso, e' un errore 500.

    Con struttura_id, un superadmin globale conta: amministra tutte le
    strutture, quindi anche quella.
    """
    sql = ("SELECT email, password_hash, ruolo, struttura_id FROM utenti "
           "WHERE attivo = 1 AND eliminato_il IS NULL")
    for riga in conn.execute(sql):
        if stato_impronta(riga['password_hash']) != 'ok':
            continue
        if struttura_id is None:
            return True
        if riga['ruolo'] == 'superadmin':
            return True
        if riga['ruolo'] == 'admin' and riga['struttura_id'] == struttura_id:
            return True
    return False


def azzera(conn, *, struttura_id=None, definitivo=False, rimpiazzo=None):
    """Cancella gli utenti in ambito e conserva tutto il resto.

    Non apre ne' chiude la transazione: e' il chiamante a farlo, ed e' il
    motivo per cui l'accesso di rimpiazzo puo' nascere nello stesso istante
    in cui muoiono gli altri. Se alla fine nessuno puo' entrare, solleva
    AccessoNonGarantito e il chiamante annulla: un'installazione senza
    accesso e' esattamente il guasto che questo strumento ripara, non uno
    che deve saper produrre.

    definitivo=False (predefinito) lascia le righe come voci storiche e non
    tocca le otto colonne *_by: su un registro di elettromedicali 'chi ha
    inserito questo apparecchio' e' tracciabilita'. definitivo=True cancella
    le righe e azzera quei riferimenti.
    """
    from utente_service import cancella_utente
    from struttura_service import _rimuovi_utenti

    sql = 'SELECT id, email FROM utenti WHERE eliminato_il IS NULL'
    parametri = ()
    if struttura_id is not None:
        sql += ' AND struttura_id = ?'
        parametri = (struttura_id,)
    bersagli = conn.execute(sql, parametri).fetchall()
    coinvolti = [r['email'] for r in bersagli]
    ids = [r['id'] for r in bersagli]

    if definitivo:
        # _rimuovi_utenti non tocca sessioni ne' utenti_divisioni: ci pensano
        # le FOREIGN KEY ... ON DELETE CASCADE, ma solo se sono accese.
        conn.execute('PRAGMA foreign_keys = ON')
        _rimuovi_utenti(conn, ids, annota_email=True)
    else:
        for utente_id in ids:
            cancella_utente(conn, utente_id)

    rimpiazzo_id = None
    if rimpiazzo is not None:
        rimpiazzo_id = crea_accesso(
            conn, rimpiazzo.email, rimpiazzo.password, rimpiazzo.ruolo,
            rimpiazzo.struttura_id, rimpiazzo.nome, rimpiazzo.cognome)

    if not esiste_accesso_valido(conn, struttura_id):
        raise AccessoNonGarantito(
            "L'operazione lascerebbe l'installazione senza nessun accesso "
            "valido. Indica un accesso di rimpiazzo (--nuovo-admin EMAIL).")

    ambito = 'struttura {}'.format(struttura_id) if struttura_id else 'tutte le strutture'
    semantica = 'definitivo' if definitivo else 'conservativo'
    conn.execute(
        """INSERT INTO log_attivita (utente_id, azione, entita, dettagli, struttura_id)
           VALUES (NULL, 'azzeramento_utenti', 'utenti', ?, ?)""",
        (f'manutenzione.py: azzeramento {semantica} su {ambito}, '
         f'{len(coinvolti)} utenti', struttura_id))

    return {'coinvolti': coinvolti, 'semantica': semantica,
            'rimpiazzo_id': rimpiazzo_id}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: PASS, 32 test.

- [ ] **Step 5: Commit**

```bash
git add manutenzione_lib/utenti.py tests/test_manutenzione.py
git commit -m "feat(manutenzione): azzerare gli utenti senza restare fuori dalla porta"
```

---

### Task 6: `operazioni.py` — adattatori verso gli script esistenti

**Files:**
- Create: `manutenzione_lib/operazioni.py`
- Test: `tests/test_manutenzione.py` (aggiunge)

**Interfaces:**
- Consumes: `migrate`, `pulisci_uploads`, `toggle_modalita`, `backup_service`.
- Produces:
  - `percorso_database(percorso_esplicito=None) -> str`
  - `carica_config() -> dict`
  - `radice() -> str`
  - `apri(percorso_db) -> sqlite3.Connection`
  - `backup_di_sicurezza(percorso_db, etichetta='manutenzione') -> str`
  - `migrazioni_pendenti(conn) -> list`
  - `applica_migrazioni(conn, percorso_db, config, pendenti) -> bool`
  - `orfani(conn, percorso_uploads) -> tuple[list[str], int]`
  - `elimina_orfani(percorsi) -> tuple[int, list]`
  - `imposta_modalita(single: bool) -> bool`
  - `crea_backup(percorso_db, percorso_backup) -> dict`
  - `elenca_backup(percorso_backup) -> list[dict]`
  - `ripristina_backup(percorso_backup_file, percorso_db) -> dict`

- [ ] **Step 1: Write the failing test**

```python
from manutenzione_lib import operazioni


def test_il_backup_di_sicurezza_e_una_copia_apribile(conn, app, tmp_path):
    sid = _struttura(conn, 'Alfa')
    conn.commit()

    percorso_db = app.config['DATABASE_PATH']
    copia = operazioni.backup_di_sicurezza(percorso_db)

    assert os.path.exists(copia)
    assert 'bak_manutenzione_' in os.path.basename(copia)
    altra = sqlite3.connect(copia)
    assert altra.execute('SELECT nome FROM strutture').fetchone()[0] == 'Alfa'
    altra.close()


def test_gli_adattatori_riusano_gli_script_esistenti():
    """Se qualcuno rinomina una funzione negli script, deve rompersi qui e
    non a runtime davanti all'operatore."""
    import migrate
    import pulisci_uploads
    import toggle_modalita
    import backup_service

    for modulo, nome in (
        (migrate, 'analyze'), (migrate, 'apply_all'), (migrate, 'describe_version'),
        (migrate, 'load_db_path'), (migrate, 'MIGRATIONS'),
        (pulisci_uploads, 'percorsi_referenziati'), (pulisci_uploads, 'trova_orfani'),
        (pulisci_uploads, 'elimina_file'),
        (toggle_modalita, 'stato_attuale'), (toggle_modalita, 'scrivi_config'),
        (backup_service, 'create_backup'), (backup_service, 'list_backups'),
        (backup_service, 'restore_backup'),
    ):
        assert hasattr(modulo, nome), f'{modulo.__name__}.{nome} non esiste piu\''
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: FAIL con `ImportError: cannot import name 'operazioni'`

- [ ] **Step 3: Write minimal implementation**

`manutenzione_lib/operazioni.py`:

```python
"""Adattatori verso gli script che gia' fanno il lavoro.

Nessuna logica nuova: migrate.py, pulisci_uploads.py, toggle_modalita.py e
backup_service.py restano l'autorita' sulle rispettive operazioni, e i loro
test restano quelli che le proteggono. Questo modulo serve solo a dare loro
un'unica interfaccia e un unico modo di trovare il database.
"""
import os
import shutil
import sqlite3
from datetime import datetime

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def radice():
    return RADICE


def percorso_database(percorso_esplicito=None):
    import migrate
    return migrate.load_db_path(percorso_esplicito)


def carica_config():
    import migrate
    return migrate.load_config()


def apri(percorso_db):
    if not os.path.exists(percorso_db):
        raise FileNotFoundError(percorso_db)
    conn = sqlite3.connect(percorso_db)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def backup_di_sicurezza(percorso_db, etichetta='manutenzione'):
    """Copia accanto al database, come fa migrate.apply_all.

    Sta accanto e non in backups/ di proposito: e' la rete di questa
    esecuzione, non un backup di esercizio, e non deve entrare nella
    rotazione che ne conserva solo quattro.
    """
    marca = datetime.now().strftime('%Y%m%d_%H%M%S')
    copia = f'{percorso_db}.bak_{etichetta}_{marca}'
    shutil.copy2(percorso_db, copia)
    return copia


def migrazioni_pendenti(conn):
    import migrate
    _versione, _uv, pendenti = migrate.analyze(conn)
    return pendenti


def applica_migrazioni(conn, percorso_db, config, pendenti):
    import migrate
    return migrate.apply_all(conn, percorso_db, config, pendenti)


def percorso_uploads(config):
    percorso = config.get('uploads_path', 'uploads')
    if not os.path.isabs(percorso):
        percorso = os.path.join(RADICE, percorso)
    return percorso


def percorso_backup(config):
    percorso = config.get('backups_path', 'backups')
    if not os.path.isabs(percorso):
        percorso = os.path.join(RADICE, percorso)
    return percorso


def orfani(conn, cartella_uploads):
    import pulisci_uploads
    referenziati = pulisci_uploads.percorsi_referenziati(conn)
    return pulisci_uploads.trova_orfani(cartella_uploads, referenziati)


def elimina_orfani(percorsi):
    import pulisci_uploads
    return pulisci_uploads.elimina_file(percorsi)


def modalita_attuale(config):
    import toggle_modalita
    return toggle_modalita.stato_attuale(config)


def imposta_modalita(single):
    import toggle_modalita
    config = toggle_modalita.leggi_config()
    config['single_struttura'] = bool(single)
    toggle_modalita.scrivi_config(config)
    return bool(single)


def crea_backup(percorso_db, cartella_backup):
    import backup_service
    return backup_service.create_backup(percorso_db, cartella_backup)


def elenca_backup(cartella_backup):
    import backup_service
    if not os.path.isdir(cartella_backup):
        return []
    return backup_service.list_backups(cartella_backup)


def ripristina_backup(file_backup, percorso_db):
    import backup_service
    return backup_service.restore_backup(file_backup, percorso_db)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: PASS, 34 test.

- [ ] **Step 5: Commit**

```bash
git add manutenzione_lib/operazioni.py tests/test_manutenzione.py
git commit -m "feat(manutenzione): un solo modo di raggiungere gli script esistenti"
```

---

### Task 7: `manutenzione.py` — entry point e subcomandi

**Files:**
- Create: `manutenzione.py`
- Test: `tests/test_manutenzione.py` (aggiunge)

**Interfaces:**
- Consumes: tutto il package.
- Produces: `main(argv=None) -> int`, e le funzioni `comando_stato`, `comando_diagnosi`, `comando_migra`, `comando_utenti`, `comando_uploads`, `comando_modalita`, `comando_backup`, ognuna `(args) -> int`.

- [ ] **Step 1: Write the failing test**

```python
import json as _json

import manutenzione as cli


def test_stato_json_e_leggibile_da_una_macchina(conn, app, capsys, tmp_path,
                                                monkeypatch):
    _struttura(conn, 'Alfa')
    conn.commit()
    monkeypatch.setattr(cli.operazioni, 'carica_config',
                        lambda: {'uploads_path': str(tmp_path / 'uploads')})

    codice = cli.main(['--db', app.config['DATABASE_PATH'], 'stato', '--json'])

    assert codice == 0
    reso = _json.loads(capsys.readouterr().out)
    assert reso['modalita']['strutture'] == 1


def test_diagnosi_esce_con_uno_se_c_e_un_errore(conn, app, capsys, tmp_path,
                                                monkeypatch):
    _struttura(conn, 'Alfa')  # nessun utente: errore
    conn.commit()
    monkeypatch.setattr(cli.operazioni, 'carica_config',
                        lambda: {'uploads_path': str(tmp_path / 'uploads')})

    codice = cli.main(['--db', app.config['DATABASE_PATH'], 'diagnosi'])

    assert codice == 1
    assert 'Nessun utente attivo' in capsys.readouterr().out


def test_diagnosi_esce_con_zero_quando_ci_sono_solo_avvisi(conn, app, capsys,
                                                           tmp_path, monkeypatch):
    sid = _struttura(conn, 'Alfa')
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    _utente(conn, 'spento@alfa.it', 'utente', sid, attivo=0)
    monkeypatch.setattr(cli.operazioni, 'carica_config',
                        lambda: {'uploads_path': str(tmp_path / 'uploads')})

    assert cli.main(['--db', app.config['DATABASE_PATH'], 'diagnosi']) == 0


def test_un_database_inesistente_non_produce_traceback(capsys, tmp_path):
    codice = cli.main(['--db', str(tmp_path / 'non-esiste.sqlite'), 'stato'])
    assert codice == 1
    assert 'seed.py' in capsys.readouterr().out


def test_utenti_elenca_mostra_lo_stato_delle_impronte(conn, app, capsys, tmp_path,
                                                      monkeypatch):
    sid = _struttura(conn, 'Alfa')
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    _utente(conn, 'vecchio@alfa.it', 'utente', sid,
            password_hash='sha256$sale$impronta')
    monkeypatch.setattr(cli.operazioni, 'carica_config',
                        lambda: {'uploads_path': str(tmp_path / 'uploads')})

    assert cli.main(['--db', app.config['DATABASE_PATH'], 'utenti', 'elenca']) == 0
    uscita = capsys.readouterr().out
    assert 'vecchio@alfa.it' in uscita
    assert 'metodo_sconosciuto' in uscita


def test_utenti_azzera_senza_rimpiazzo_rifiuta_e_non_scrive(conn, app, capsys,
                                                            tmp_path, monkeypatch):
    sid = _struttura(conn, 'Alfa')
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    monkeypatch.setattr(cli.operazioni, 'carica_config',
                        lambda: {'uploads_path': str(tmp_path / 'uploads')})

    codice = cli.main(['--db', app.config['DATABASE_PATH'],
                       'utenti', 'azzera', '-y'])

    assert codice == 1
    assert conn.execute("SELECT email FROM utenti").fetchone()['email'] == 'admin@alfa.it'


def test_utenti_azzera_con_rimpiazzo_funziona_senza_domande(conn, app, tmp_path,
                                                            monkeypatch):
    sid = _struttura(conn, 'Alfa')
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    monkeypatch.setattr(cli.operazioni, 'carica_config',
                        lambda: {'uploads_path': str(tmp_path / 'uploads')})
    monkeypatch.setattr(cli, 'chiedi_password', lambda _e: 'Password1')

    codice = cli.main(['--db', app.config['DATABASE_PATH'], 'utenti', 'azzera',
                       '-y', '--nuovo-admin', 'nuovo@alfa.it'])

    assert codice == 0
    righe = {r['email'] for r in conn.execute('SELECT email FROM utenti')}
    assert 'nuovo@alfa.it' in righe
    assert 'admin@alfa.it' not in righe


def test_utenti_password_reimposta_e_riattiva(conn, app, tmp_path, monkeypatch):
    from werkzeug.security import check_password_hash
    sid = _struttura(conn, 'Alfa')
    _utente(conn, 'spento@alfa.it', 'admin', sid, attivo=0)
    monkeypatch.setattr(cli.operazioni, 'carica_config',
                        lambda: {'uploads_path': str(tmp_path / 'uploads')})
    monkeypatch.setattr(cli, 'chiedi_password', lambda _e: 'NuovaPassword1')

    codice = cli.main(['--db', app.config['DATABASE_PATH'],
                       'utenti', 'password', 'spento@alfa.it'])

    assert codice == 0
    riga = conn.execute('SELECT password_hash, attivo FROM utenti '
                        'WHERE email = ?', ('spento@alfa.it',)).fetchone()
    assert riga['attivo'] == 1
    assert check_password_hash(riga['password_hash'], 'NuovaPassword1')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'manutenzione'` sulla riga `import manutenzione as cli`.

> Il package si chiama `manutenzione_lib/` e non `manutenzione/` proprio per questo task: un file `manutenzione.py` e una cartella `manutenzione/` nella stessa radice non coesistono, il package vince sempre e `import manutenzione` non troverebbe mai l'entry point. Il comando che l'operatore digita, `python manutenzione.py`, e' il vincolo; il nome del package cede.

- [ ] **Step 3: Write minimal implementation**

`manutenzione.py`:

```python
#!/usr/bin/env python3
"""
manutenzione.py - Strumento unificato di manutenzione MedInventory

Senza argomenti: fotografa l'installazione, diagnostica i problemi e apre un
menu. Con un subcomando: non interattivo, adatto ai .bat e ai test.

Uso:
    python manutenzione.py                      stato + diagnosi + menu
    python manutenzione.py stato [--json]
    python manutenzione.py diagnosi
    python manutenzione.py migra [--check] [-y]
    python manutenzione.py utenti elenca
    python manutenzione.py utenti azzera [--struttura ID] [--definitivo]
                                         [--nuovo-admin EMAIL] [-y]
    python manutenzione.py utenti password EMAIL
    python manutenzione.py utenti superadmin
    python manutenzione.py uploads [--elimina] [-y]
    python manutenzione.py modalita [--single|--multi]
    python manutenzione.py backup [--crea|--elenca|--ripristina FILE]

--db PERCORSO vale per ogni subcomando: serve a ispezionare un'installazione
diversa da questa.
"""
import argparse
import getpass
import json
import os
import sys

# Su Windows la console non e' UTF-8: senza questo, stampare accenti o
# caratteri di riquadro fa fallire lo script con UnicodeEncodeError
# (succede appena l'output viene rediretto su file o log).
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from manutenzione_lib import diagnosi, operazioni, stato, tui
from manutenzione_lib import utenti as mutenti


def chiedi_password(email):
    """Isolata per poterla sostituire nei test: getpass legge dal terminale."""
    while True:
        password = getpass.getpass(f'Password per {email}: ')
        errori = mutenti.valida_password(password)
        if errori:
            print(f"Password non valida: {', '.join(errori)}.")
            continue
        if password != getpass.getpass('Conferma password: '):
            print('Le password non coincidono.')
            continue
        return password


def _contesto(args):
    """(conn, config, percorso_db) o (None, config, percorso_db) se il database
    non c'e'. Non solleva: l'assenza del database e' un esito da spiegare, non
    un traceback da mostrare all'operatore."""
    percorso_db = operazioni.percorso_database(args.db)
    config = operazioni.carica_config()
    try:
        conn = operazioni.apri(percorso_db)
    except FileNotFoundError:
        print(tui.riga_esito('errore', f'Database non trovato: {percorso_db}'))
        print("  Per una nuova installazione: python seed.py")
        return None, config, percorso_db
    return conn, config, percorso_db


def stampa_stato(fotografia):
    print()
    print(tui.titolo('Stato installazione'))
    db = fotografia['database']
    if db.get('disponibile'):
        print(tui.campo('Database', f"{db['percorso']}  "
                                    f"{db['dimensione_byte'] / (1024*1024):.2f} MB  "
                                    f"{db['integrity_check']}"))
    schema = fotografia['schema']
    if schema.get('disponibile'):
        pendenti = f"{len(schema['pendenti'])} pendenti" if schema['pendenti'] else 'aggiornato'
        print(tui.campo('Schema', f"{schema['versione']}  "
                                  f"user_version {schema['user_version']}  {pendenti}"))
    mod = fotografia['modalita']
    if mod.get('disponibile'):
        nome = 'single-struttura' if mod['single_struttura'] else 'multi-struttura'
        print(tui.campo('Modalita', f"{nome}  {mod['strutture']} strutture"))
    ut = fotografia['utenti']
    if ut.get('disponibile'):
        ruoli = ', '.join(f'{n} {r}' for r, n in sorted(ut['per_ruolo'].items()))
        print(tui.campo('Utenti', f"{ut['totale_attivi']} attivi"
                                  + (f" ({ruoli})" if ruoli else '')
                                  + f", {ut['disattivati']} disattivati"
                                    f", {ut['cancellati']} cancellati"))
    dati = fotografia['dati']
    if dati.get('disponibile'):
        print(tui.campo('Dati', ', '.join(
            f"{n} {t}" for t, n in dati.items() if t != 'disponibile')))
    up = fotografia['uploads']
    if up.get('disponibile'):
        orfani = '' if up.get('orfani') is None else f", {up['orfani']} orfani"
        print(tui.campo('Uploads', f"{up['file']} file, "
                                   f"{up['byte'] / (1024*1024):.1f} MB{orfani}"))
    else:
        print(tui.campo('Uploads', f"non disponibile: {up.get('motivo')}"))
    ai = fotografia['ai']
    chiavi = ', '.join(n for n, presente in ai['chiavi'].items() if presente) or 'nessuna'
    print(tui.campo('AI', f"{ai['provider'] or 'non impostato'}  chiavi: {chiavi}"))
    posta = fotografia['posta']
    print(tui.campo('Posta', f"SMTP {posta['smtp_host'] or 'non configurato'}"))
    bk = fotografia['backup']
    print(tui.campo('Backup', f"{bk['numero']} (ultimo {bk['ultimo']})"
                    if bk.get('disponibile') else f"non disponibile: {bk.get('motivo')}"))


def stampa_diagnosi(esiti):
    print()
    print(tui.titolo('Diagnosi'))
    if not esiti:
        print(tui.riga_esito('ok', 'Nessun problema rilevato.'))
        return
    for e in esiti:
        print(tui.riga_esito(e.gravita, f'{e.titolo}: {e.dettaglio}'))
        print(f'       rimedio: {e.rimedio}')


def comando_stato(args):
    conn, config, _percorso = _contesto(args)
    if conn is None:
        return 1
    try:
        fotografia = stato.raccogli(conn, config, operazioni.radice())
    finally:
        conn.close()
    if args.json:
        print(json.dumps(fotografia, indent=2, ensure_ascii=False, default=str))
    else:
        stampa_stato(fotografia)
    return 0


def comando_diagnosi(args):
    conn, config, _percorso = _contesto(args)
    if conn is None:
        return 1
    try:
        fotografia = stato.raccogli(conn, config, operazioni.radice())
        esiti = diagnosi.esegui(conn, config, fotografia)
    finally:
        conn.close()
    stampa_diagnosi(esiti)
    return 1 if diagnosi.ci_sono_errori(esiti) else 0


def comando_migra(args):
    conn, config, percorso_db = _contesto(args)
    if conn is None:
        return 1
    try:
        pendenti = operazioni.migrazioni_pendenti(conn)
        if not pendenti:
            print(tui.riga_esito('ok', 'Nessuna migrazione da applicare.'))
            return 0
        print(tui.riga_esito('avviso',
                             f'{len(pendenti)} migrazioni da applicare: '
                             + ', '.join(m.id for m in pendenti)))
        if args.check:
            return 1
        if not args.yes and not conferma('Applicare le migrazioni?'):
            return 0
        riuscito = operazioni.applica_migrazioni(conn, percorso_db, config, pendenti)
        return 0 if riuscito else 1
    finally:
        conn.close()


def conferma(domanda):
    try:
        risposta = input(f'{domanda} [s/N] ').strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return False
    return risposta in ('s', 'si', 'sì', 'y', 'yes')


def conferma_distruttiva(parola):
    print(f"  Per procedere digita esattamente: {parola}")
    try:
        return input('  > ').strip() == parola
    except (KeyboardInterrupt, EOFError):
        print()
        return False


def comando_utenti(args):
    conn, config, percorso_db = _contesto(args)
    if conn is None:
        return 1
    try:
        if args.azione == 'elenca':
            righe = mutenti.elenco(conn, args.struttura)
            print(tui.tabella(
                ['id', 'email', 'ruolo', 'struttura', 'attivo', 'impronta'],
                [[r['id'], r['email'], r['ruolo'], r['struttura_id'] or '-',
                  'si' if r['attivo'] else 'NO', r['impronta']] for r in righe]))
            return 0

        if args.azione == 'password':
            password = chiedi_password(args.email)
            try:
                mutenti.imposta_password(conn, args.email, password)
            except mutenti.UtenteInesistente as e:
                print(tui.riga_esito('errore', str(e)))
                return 1
            conn.commit()
            print(tui.riga_esito('ok', f'Password aggiornata per {args.email}. '
                                       f"L'account e' attivo."))
            return 0

        if args.azione == 'superadmin':
            esistente = conn.execute(
                "SELECT email FROM utenti WHERE ruolo = 'superadmin' "
                "AND eliminato_il IS NULL").fetchone()
            if esistente:
                print(f"Superadmin esistente: {esistente['email']}")
                if not conferma('Reimpostarne la password?'):
                    return 0
                password = chiedi_password(esistente['email'])
                mutenti.imposta_password(conn, esistente['email'], password)
                conn.commit()
                print(tui.riga_esito('ok', 'Password superadmin aggiornata.'))
                return 0
            email = input('Email superadmin [superadmin@medinventory.local]: ').strip() \
                or 'superadmin@medinventory.local'
            password = chiedi_password(email)
            try:
                mutenti.crea_accesso(conn, email, password, 'superadmin')
            except (mutenti.EmailGiaInUso, mutenti.PasswordDebole) as e:
                print(tui.riga_esito('errore', str(e)))
                return 1
            conn.commit()
            print(tui.riga_esito('ok', f'Superadmin creato: {email}'))
            return 0

        if args.azione == 'azzera':
            return _azzera(conn, args, percorso_db)
    finally:
        conn.close()
    return 2


def _azzera(conn, args, percorso_db):
    bersagli = mutenti.elenco(conn, args.struttura)
    vivi = [r for r in bersagli if r['eliminato_il'] is None]
    if not vivi:
        print(tui.riga_esito('ok', 'Nessun utente da azzerare.'))
        return 0

    from utente_service import conteggi_riferimenti
    print()
    print(tui.titolo('Utenti che verranno azzerati'))
    print(tui.tabella(
        ['email', 'ruolo', 'righe che lo citano'],
        [[r['email'], r['ruolo'],
          sum(conteggi_riferimenti(conn, r['id']).values())] for r in vivi]))
    semantica = 'DEFINITIVO (righe rimosse, tracciabilita\' persa)' \
        if args.definitivo else 'conservativo (righe storiche, tracciabilita\' intatta)'
    print(f'  Semantica: {semantica}')

    rimpiazzo = None
    if args.nuovo_admin:
        ruolo = 'admin' if args.struttura else 'superadmin'
        rimpiazzo = mutenti.Rimpiazzo(
            email=args.nuovo_admin, password=chiedi_password(args.nuovo_admin),
            ruolo=ruolo, struttura_id=args.struttura)

    if not args.yes:
        parola = 'AZZERA' if args.struttura is None else str(args.struttura)
        if not conferma_distruttiva(parola):
            print('Annullato.')
            return 0

    copia = operazioni.backup_di_sicurezza(percorso_db)
    print(tui.riga_esito('ok', f'Backup: {copia}'))

    try:
        esito = mutenti.azzera(conn, struttura_id=args.struttura,
                               definitivo=args.definitivo, rimpiazzo=rimpiazzo)
        conn.commit()
    except (mutenti.AccessoNonGarantito, mutenti.EmailGiaInUso,
            mutenti.PasswordDebole) as e:
        conn.rollback()
        print(tui.riga_esito('errore', str(e)))
        print(f'  Nulla e\' stato modificato. Backup conservato: {copia}')
        return 1
    except Exception as e:
        conn.rollback()
        print(tui.riga_esito('errore', f'Azzeramento fallito: {e}'))
        print(f'  Nulla e\' stato modificato. Backup conservato: {copia}')
        return 1

    print(tui.riga_esito('ok', f"{len(esito['coinvolti'])} utenti azzerati "
                               f"({esito['semantica']})."))
    if esito['rimpiazzo_id']:
        print(tui.riga_esito('ok', f'Nuovo accesso: {args.nuovo_admin}'))
    return 0


def comando_uploads(args):
    conn, config, _percorso = _contesto(args)
    if conn is None:
        return 1
    try:
        cartella = operazioni.percorso_uploads(config)
        trovati, byte_totali = operazioni.orfani(conn, cartella)
    except Exception as e:
        print(tui.riga_esito('errore', str(e)))
        return 1
    finally:
        conn.close()

    if not trovati:
        print(tui.riga_esito('ok', 'Nessun file orfano.'))
        return 0
    print(tui.riga_esito('avviso', f'{len(trovati)} file orfani, '
                                   f'{byte_totali / (1024*1024):.1f} MB'))
    for percorso in trovati[:20]:
        print(f'  {percorso}')
    if len(trovati) > 20:
        print(f'  (e altri {len(trovati) - 20})')
    if not args.elimina:
        return 0
    if not args.yes and not conferma(f'Eliminare {len(trovati)} file?'):
        return 0
    rimossi, falliti = operazioni.elimina_orfani(trovati)
    print(tui.riga_esito('ok', f'{rimossi} file rimossi.'))
    for percorso, errore in falliti:
        print(tui.riga_esito('errore', f'{percorso}: {errore}'))
    return 0 if not falliti else 1


def comando_modalita(args):
    config = operazioni.carica_config()
    attuale = operazioni.modalita_attuale(config)
    nome = 'single-struttura' if attuale else 'multi-struttura'
    if not args.single and not args.multi:
        print(tui.campo('Modalita', nome))
        return 0
    voluta = bool(args.single)
    if voluta == attuale:
        print(tui.riga_esito('ok', f'Gia\' in modalita\' {nome}.'))
        return 0
    operazioni.imposta_modalita(voluta)
    print(tui.riga_esito('ok', 'Modalita\' impostata a '
                               + ('single-struttura' if voluta else 'multi-struttura')))
    print('  Riavvia l\'applicazione perche\' abbia effetto.')
    return 0


def comando_backup(args):
    config = operazioni.carica_config()
    percorso_db = operazioni.percorso_database(args.db)
    cartella = operazioni.percorso_backup(config)

    if args.crea:
        esito = operazioni.crea_backup(percorso_db, cartella)
        print(tui.riga_esito('ok', f"Backup creato: {esito['filename']}"))
        return 0
    if args.ripristina:
        if not conferma(f'Sostituire il database con {args.ripristina}?'):
            return 0
        operazioni.ripristina_backup(
            os.path.join(cartella, args.ripristina), percorso_db)
        print(tui.riga_esito('ok', 'Database ripristinato.'))
        return 0
    elenco = operazioni.elenca_backup(cartella)
    if not elenco:
        print(tui.riga_esito('avviso', f'Nessun backup in {cartella}'))
        return 0
    print(tui.tabella(['file', 'dimensione', 'data'],
                      [[b['filename'], b.get('size', ''), b.get('created', '')]
                       for b in elenco]))
    return 0


def costruisci_parser():
    p = argparse.ArgumentParser(
        prog='manutenzione.py',
        description='Strumento unificato di manutenzione MedInventory.')
    p.add_argument('--db', metavar='PERCORSO',
                   help='database su cui operare (predefinito: quello di config)')
    sub = p.add_subparsers(dest='comando')

    ps = sub.add_parser('stato', help='fotografia dell\'installazione')
    ps.add_argument('--json', action='store_true', help='emette il dizionario grezzo')

    sub.add_parser('diagnosi', help='controlli; esce con 1 se ci sono errori')

    pm = sub.add_parser('migra', help='migrazioni dello schema')
    pm.add_argument('--check', action='store_true', help='solo analisi')
    pm.add_argument('-y', '--yes', action='store_true', help='senza conferma')

    pu = sub.add_parser('utenti', help='account e accessi')
    pu.add_argument('azione', choices=['elenca', 'azzera', 'password', 'superadmin'])
    pu.add_argument('email', nargs='?', help='per l\'azione password')
    pu.add_argument('--struttura', type=int, metavar='ID',
                    help='restringe a una struttura')
    pu.add_argument('--definitivo', action='store_true',
                    help='rimuove le righe invece di lasciarle come voci storiche')
    pu.add_argument('--nuovo-admin', metavar='EMAIL', dest='nuovo_admin',
                    help='accesso di rimpiazzo creato nella stessa transazione')
    pu.add_argument('-y', '--yes', action='store_true', help='senza conferma')

    pup = sub.add_parser('uploads', help='file orfani')
    pup.add_argument('--elimina', action='store_true')
    pup.add_argument('-y', '--yes', action='store_true')

    pmo = sub.add_parser('modalita', help='single o multi struttura')
    gruppo = pmo.add_mutually_exclusive_group()
    gruppo.add_argument('--single', action='store_true')
    gruppo.add_argument('--multi', action='store_true')

    pb = sub.add_parser('backup', help='backup del database')
    pb.add_argument('--crea', action='store_true')
    pb.add_argument('--elenca', action='store_true')
    pb.add_argument('--ripristina', metavar='FILE')

    return p


COMANDI = {
    'stato': comando_stato,
    'diagnosi': comando_diagnosi,
    'migra': comando_migra,
    'utenti': comando_utenti,
    'uploads': comando_uploads,
    'modalita': comando_modalita,
    'backup': comando_backup,
}


def main(argv=None):
    args = costruisci_parser().parse_args(argv)
    if args.comando is None:
        from manutenzione_lib import menu
        return menu.avvia(args)
    if args.comando == 'utenti' and args.azione == 'password' and not args.email:
        print(tui.riga_esito('errore', "L'azione password vuole un indirizzo."))
        return 2
    try:
        return COMANDI[args.comando](args)
    except KeyboardInterrupt:
        print('\nInterrotto.')
        return 1


if __name__ == '__main__':
    sys.exit(main())
```

Il Task 8 crea `manutenzione_lib/menu.py`; fino ad allora l'esecuzione senza argomenti fallisce con `ImportError`. E' voluto: i test di questo task passano tutti un subcomando.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: PASS, 42 test.

- [ ] **Step 5: Commit**

```bash
git add manutenzione.py manutenzione_lib tests/test_manutenzione.py
git commit -m "feat(manutenzione): i subcomandi, e un solo modo di sbagliarsi"
```

---

### Task 8: `menu.py` — la porta interattiva

**Files:**
- Create: `manutenzione_lib/menu.py`
- Test: `tests/test_manutenzione.py` (aggiunge)

**Interfaces:**
- Consumes: `manutenzione.COMANDI` e le funzioni di stampa dell'entry point.
- Produces: `avvia(args) -> int`, `VOCI: tuple[tuple[str, str, str]]` — `(tasto, etichetta, comando)`

- [ ] **Step 1: Write the failing test**

```python
def test_il_menu_mostra_stato_diagnosi_e_voci_ed_esce_con_q(conn, app, capsys,
                                                            tmp_path, monkeypatch):
    sid = _struttura(conn, 'Alfa')
    _utente(conn, 'admin@alfa.it', 'admin', sid)
    monkeypatch.setattr(cli.operazioni, 'carica_config',
                        lambda: {'uploads_path': str(tmp_path / 'uploads')})
    monkeypatch.setattr('builtins.input', lambda *_a: 'q')

    codice = cli.main(['--db', app.config['DATABASE_PATH']])

    uscita = capsys.readouterr().out
    assert codice == 0
    assert 'Stato installazione' in uscita
    assert 'Diagnosi' in uscita
    assert 'Utenti e accessi' in uscita


def test_il_menu_esce_pulito_su_interruzione(conn, app, capsys, tmp_path,
                                             monkeypatch):
    _struttura(conn, 'Alfa')
    conn.commit()
    monkeypatch.setattr(cli.operazioni, 'carica_config',
                        lambda: {'uploads_path': str(tmp_path / 'uploads')})

    def interrompi(*_a):
        raise KeyboardInterrupt

    monkeypatch.setattr('builtins.input', interrompi)
    assert cli.main(['--db', app.config['DATABASE_PATH']]) == 0
    assert 'Traceback' not in capsys.readouterr().out


def test_una_scelta_ignota_non_chiude_il_menu(conn, app, capsys, tmp_path,
                                              monkeypatch):
    _struttura(conn, 'Alfa')
    conn.commit()
    monkeypatch.setattr(cli.operazioni, 'carica_config',
                        lambda: {'uploads_path': str(tmp_path / 'uploads')})
    risposte = iter(['zzz', 'q'])
    monkeypatch.setattr('builtins.input', lambda *_a: next(risposte))

    assert cli.main(['--db', app.config['DATABASE_PATH']]) == 0
    assert 'Scelta non riconosciuta' in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: FAIL con `ImportError: cannot import name 'menu'`

- [ ] **Step 3: Write minimal implementation**

`manutenzione_lib/menu.py`:

```python
"""Il menu interattivo.

Unico posto del progetto che chiama input() per la manutenzione. Non
duplica nessuna logica: costruisce gli stessi oggetti args che argparse
darebbe e chiama gli stessi comandi, cosi' la porta interattiva e quella
scriptabile non possono divergere.
"""
import argparse

from manutenzione_lib import diagnosi, operazioni, stato, tui

# (tasto, etichetta, comando, argomenti aggiuntivi)
VOCI = (
    ('1', 'Migrazioni schema',    'migra',    {'check': False, 'yes': False}),
    ('2', 'Utenti e accessi',     'utenti',   {'azione': 'elenca', 'email': None,
                                               'struttura': None, 'definitivo': False,
                                               'nuovo_admin': None, 'yes': False}),
    ('3', 'Reimposta una password', 'utenti', {'azione': 'password', 'email': None,
                                               'struttura': None, 'definitivo': False,
                                               'nuovo_admin': None, 'yes': False}),
    ('4', 'Pulizia uploads',      'uploads',  {'elimina': False, 'yes': False}),
    ('5', "Modalita' single/multi", 'modalita', {'single': False, 'multi': False}),
    ('6', 'Backup',               'backup',   {'crea': False, 'elenca': True,
                                               'ripristina': None}),
)


def _args_per(comando, base, extra):
    valori = {'comando': comando, 'db': base.db, 'json': False}
    valori.update(extra)
    return argparse.Namespace(**valori)


def _mostra_intestazione(args):
    import manutenzione as cli
    conn, config, _percorso = cli._contesto(args)
    if conn is None:
        return False
    try:
        fotografia = stato.raccogli(conn, config, operazioni.radice())
        esiti = diagnosi.esegui(conn, config, fotografia)
    finally:
        conn.close()
    cli.stampa_stato(fotografia)
    cli.stampa_diagnosi(esiti)
    return True


def avvia(args):
    """Ciclo del menu. Torna 0 quando l'operatore esce.

    Lo stato viene ristampato dopo ogni operazione: e' cio' che rende il menu
    utile rispetto ai subcomandi - si vede subito l'effetto di quel che si e'
    appena fatto.
    """
    import manutenzione as cli

    while True:
        if not _mostra_intestazione(args):
            return 1
        print()
        print(tui.titolo('Operazioni'))
        for tasto, etichetta, _comando, _extra in VOCI:
            print(f'  [{tasto}] {etichetta}')
        print('  [q] Esci')
        try:
            scelta = input('\n  Scelta > ').strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            return 0

        if scelta == 'q':
            return 0
        if scelta == '':
            continue

        voce = next((v for v in VOCI if v[0] == scelta), None)
        if voce is None:
            print(tui.riga_esito('avviso', f'Scelta non riconosciuta: {scelta}'))
            continue

        _tasto, _etichetta, comando, extra = voce
        if comando == 'utenti' and extra.get('azione') == 'password':
            try:
                extra = dict(extra, email=input('  Indirizzo: ').strip())
            except (KeyboardInterrupt, EOFError):
                print()
                continue
        try:
            cli.COMANDI[comando](_args_per(comando, args, extra))
        except KeyboardInterrupt:
            print('\n  Interrotto.')
        except Exception as e:
            print(tui.riga_esito('errore', str(e)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: PASS, 45 test.

- [ ] **Step 5: Commit**

```bash
git add manutenzione_lib/menu.py tests/test_manutenzione.py
git commit -m "feat(manutenzione): il menu, che non duplica nessun comando"
```

---

### Task 9: `crea_superadmin.py` diventa un chiamante sottile

**Files:**
- Modify: `crea_superadmin.py` (sostituzione integrale)
- Test: `tests/test_manutenzione.py` (aggiunge)

**Interfaces:**
- Consumes: `manutenzione.main`.
- Produces: `crea_superadmin.main() -> int`, e `valida_password` resta esportata perche' altro codice o script dell'operatore potrebbe importarla.

- [ ] **Step 1: Write the failing test**

```python
def test_crea_superadmin_delega_allo_strumento_unificato(monkeypatch):
    """Lo script storico resta, ma smette di avere una logica sua.

    Due implementazioni della stessa cosa divergono: e' gia' successo con
    la validazione della password, che qui c'era e in admin.py no.
    """
    import crea_superadmin
    chiamate = []
    monkeypatch.setattr(crea_superadmin, '_esegui',
                        lambda argv: chiamate.append(argv) or 0)

    assert crea_superadmin.main() == 0
    assert chiamate == [['utenti', 'superadmin']]


def test_crea_superadmin_conserva_valida_password():
    import crea_superadmin
    assert crea_superadmin.valida_password('corta') != []
    assert crea_superadmin.valida_password('Password1') == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: FAIL con `AttributeError: module 'crea_superadmin' has no attribute '_esegui'`

- [ ] **Step 3: Write minimal implementation**

Sostituisci l'intero contenuto di `crea_superadmin.py`:

```python
"""
crea_superadmin.py - Crea o reimposta il superadmin di MedInventory.

Dalla 2.6.3 la logica vive in manutenzione_lib/utenti.py e questo script la
richiama: due implementazioni della stessa operazione divergono, e questa
era gia' l'unica a validare la password.

Uso:
    python crea_superadmin.py
    python manutenzione.py utenti superadmin    (equivalente)
"""
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from manutenzione_lib.utenti import valida_password  # noqa: F401  (riesportata)


def _esegui(argv):
    import manutenzione
    return manutenzione.main(argv)


def main():
    print("=" * 55)
    print("  MedInventory - Creazione superadmin")
    print("=" * 55)
    return _esegui(['utenti', 'superadmin'])


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_manutenzione.py -q`
Expected: PASS, 47 test.

Poi la suite intera, che deve restare verde: `python -m pytest tests/ -q`
Expected: PASS, 442 test (395 + 47).

- [ ] **Step 5: Commit**

```bash
git add crea_superadmin.py tests/test_manutenzione.py
git commit -m "refactor(superadmin): una sola implementazione, richiamata dallo script storico"
```

---

### Task 10: documentazione e rilascio 2.6.3

**Files:**
- Modify: `app.py:34`
- Modify: `config.json:2`
- Modify: `CHANGELOG.md`
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`
- Modify: `DOCUMENTAZIONE.md`
- Test: `tests/test_manutenzione.py` (aggiunge)

- [ ] **Step 1: Write the failing test**

```python
def test_la_versione_e_coerente_ovunque():
    """config.json e APP_VERSION devono dire la stessa cosa.

    Sono due file diversi letti da due percorsi diversi: quando divergono,
    l'interfaccia mostra una versione e il controllo aggiornamenti un'altra.
    """
    import json
    import app as modulo_app
    with open(os.path.join(RADICE, 'config.json'), encoding='utf-8') as f:
        config = json.load(f)
    assert config['version'] == modulo_app.APP_VERSION == '2.6.3'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_manutenzione.py::test_la_versione_e_coerente_ovunque -q`
Expected: FAIL, `assert '2.6.2' == '2.6.3'`

- [ ] **Step 3: Write minimal implementation**

1. `app.py:34` → `APP_VERSION = "2.6.3"`
2. `config.json` → `"version": "2.6.3"`
3. `CHANGELOG.md`, nuova voce in cima:

```markdown
## [2.6.3] - 2026-08-20

### Aggiunto
- `manutenzione.py`, strumento unificato a riga di comando. Senza argomenti
  fotografa l'installazione, ne diagnostica i problemi e apre un menu; con un
  subcomando lavora senza presidio. `--db PERCORSO` permette di ispezionare
  un'installazione diversa da quella corrente.
- Diagnosi degli accessi: distingue i casi che il login riassume tutti in
  "credenziali non valide" — indirizzo assente, utente disattivato, password
  diversa, blocco per tentativi ripetuti — e riconosce le impronte in formati
  che werkzeug 3 non sa piu' verificare, che fanno rispondere 500 invece di
  rifiutare.
- `manutenzione.py utenti azzera`: cancella gli utenti conservando apparecchi,
  manutenzioni, verifiche e documenti. Di predefinito lascia le righe come voci
  storiche e non tocca le colonne `*_by`; con `--definitivo` le rimuove.
  L'accesso di rimpiazzo nasce nella stessa transazione: l'operazione non puo'
  lasciare un'installazione senza nessuno che possa entrare.

### Modificato
- `crea_superadmin.py` non ha piu' una logica propria: richiama
  `manutenzione.py utenti superadmin`. Il comando resta invariato.
- `migrate.py`, `toggle_modalita.py` e `pulisci_uploads.py` sono invariati e
  continuano a funzionare come prima; lo strumento unificato li richiama.
```

4. `CLAUDE.md` e `AGENTS.md`, in **Running the Application**, dopo il blocco di `toggle_modalita.py`:

````markdown
```bash
# Unified maintenance tool: status report, diagnostics, and repairs
python manutenzione.py                  # status + diagnostics + interactive menu
python manutenzione.py diagnosi         # checks only; exit 1 on errors
python manutenzione.py utenti elenca    # users, with the state of each password hash
python manutenzione.py utenti azzera --nuovo-admin admin@example.it
python manutenzione.py --db OTHER/data/database.sqlite stato
```

`manutenzione.py` is the entry point; the logic lives in `manutenzione_lib/`.
It never imports Flask — it works on a raw `sqlite3.Connection`, which is what
lets `--db` point at a different installation. `migrate.py`,
`toggle_modalita.py` and `pulisci_uploads.py` are unchanged and still work on
their own; the tool calls into them.

Diagnosing a login nobody can pass: `check_password_hash` **raises** on a hash
whose method Werkzeug 3 dropped (the old `sha256$…`), and `auth.py:422` does not
catch it — so that installation answers 500, not "credenziali non valide".
`manutenzione.py diagnosi` separates that case from the three that really do
produce the rejection message.
````

E, nella tabella **Services** di entrambi, tre righe nuove:

| File | Responsibility |
|------|---------------|
| `manutenzione_lib/stato.py` | Installation snapshot: paths, schema version, counts. Never includes secrets |
| `manutenzione_lib/diagnosi.py` | Checks, each one a function returning an `Esito` with a remedy command |
| `manutenzione_lib/utenti.py` | Account operations outside Flask: hash inspection, password reset, wipe |

5. `DOCUMENTAZIONE.md`: sostituisci la ricetta sotto **Password dimenticata** (quella con la riga di Python e `sqlite3` scritta a mano) con:

````markdown
Se la password dell'unico amministratore e' persa:

```batch
cd C:\MedInventory
venv\Scripts\python manutenzione.py utenti password admin@medinventory.local
```

Lo strumento chiede la nuova password, la valida e riattiva l'account. Per
capire prima *perche'* l'accesso non funziona:

```batch
venv\Scripts\python manutenzione.py diagnosi
```

La diagnosi distingue i casi che la schermata di accesso riassume tutti in
"credenziali non valide": indirizzo inesistente, utente disattivato, password
diversa, blocco per tentativi ripetuti. Segnala anche le password salvate in un
formato che le versioni recenti non sanno piu' verificare — capita sulle
installazioni migrate da molto lontano, e in quel caso l'accesso risponde con
un errore del server invece che con il rifiuto.
````

E aggiungi, prima di **Il database e' corrotto**, una sezione nuova:

````markdown
### Manutenzione da riga di comando

```batch
cd C:\MedInventory
venv\Scripts\python manutenzione.py
```

Senza argomenti mostra lo stato dell'installazione, l'esito dei controlli e un
menu. Ogni voce del menu ha il subcomando corrispondente, utilizzabile senza
presidio: `stato`, `diagnosi`, `migra`, `utenti`, `uploads`, `modalita`,
`backup`. `--db PERCORSO` fa lavorare lo strumento su un'altra installazione.

**Azzerare gli utenti conservando tutto il resto:**

```batch
venv\Scripts\python manutenzione.py utenti azzera --nuovo-admin nuovo@struttura.it
```

Apparecchi, manutenzioni, verifiche e documenti restano. Gli account vengono
distrutti ma le righe sopravvivono come voci storiche, perche' le schede
continuino a dire chi ha inserito cosa; con `--definitivo` spariscono anche
quelle e i riferimenti si azzerano. Il nuovo accesso viene creato nella stessa
transazione dell'azzeramento: l'operazione non puo' chiudere fuori dalla porta.
Un backup del database viene fatto sempre, prima di scrivere.
````

Aggiorna infine il piede del file da `v1.1.6` a `v2.6.3`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ -q`
Expected: PASS, 443 test.

Poi la prova a mano, che nessun test sostituisce:

```bash
python manutenzione.py stato
python manutenzione.py diagnosi
python crea_superadmin.py    # e rispondi 'n'
```

- [ ] **Step 5: Commit**

```bash
git add app.py config.json CHANGELOG.md CLAUDE.md AGENTS.md DOCUMENTAZIONE.md tests/test_manutenzione.py
git commit -m "release: MedInventory 2.6.3"
```

---

## Self-Review

**Copertura della spec.** Ogni sezione ha un task: forma → 1-8; superficie CLI → 7; rapporto di stato → 2; diagnosi → 4 (con le impronte dal 3); azzeramento → 5; TUI → 1 e 8; errori → 2 (sezioni indipendenti), 7 (`_contesto`, `KeyboardInterrupt`), 8 (menu); test → distribuiti; documentazione e rilascio → 10. Gli adattatori (spec: "gli script esistenti restano") sono il Task 6, `crea_superadmin` il Task 9.

**Scostamenti consapevoli dalla spec.**
- La spec chiama il package `manutenzione/`; il piano lo chiama `manutenzione_lib/` fin dal Task 1. Un package e un modulo omonimi nella stessa radice non coesistono, e fra i due nomi cede quello che l'operatore non digita mai.
- La spec dice che `_supports_color` di `migrate.py` "si sposta" in `tui.py`. Non si sposta: `migrate.py` resta invariato per non toccare i suoi test, e `tui.py` ha la propria funzione. Tre righe duplicate, in cambio di zero rischio su uno script che applica migrazioni.
- La spec elenca la barra di avanzamento fra le primitive di `tui.py`. Nessuna operazione la usa (le uniche lunghe stampano riga per riga): YAGNI, non e' nel Task 1.

**Nomi.** `Esito` (diagnosi) e i dizionari di esito di `utenti.azzera` sono cose diverse con nomi simili: il primo e' la dataclass dei controlli, il secondo un `dict`. Restano distinti perche' vivono in moduli diversi e nessun task li scambia. `stato_impronta` torna sempre una delle tre stringhe `'ok' | 'metodo_sconosciuto' | 'malformata'`, usate identiche nei Task 3, 4, 5 e 7.
