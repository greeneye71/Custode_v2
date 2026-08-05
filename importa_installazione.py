"""
importa_installazione.py — Importa i dati di un'altra installazione MedInventory
in questa, come nuova struttura (o dentro una struttura esistente).

Pensato soprattutto per assorbire un'installazione monostruttura già in esercizio
dentro un deployment multi-struttura, ma regge anche sorgenti multi-struttura.

La sorgente non viene mai modificata: se ne legge una copia consistente.

Uso tipico:

    # 1. guarda cosa succederebbe, senza scrivere nulla
    python importa_installazione.py C:\\MedInventory_Ospedale --dry-run

    # 2. importa davvero
    python importa_installazione.py C:\\MedInventory_Ospedale \\
        --struttura-nome "Ospedale San Rocco" --con-config

Lo schema della sorgente può essere di una versione diversa: le colonne vengono
riconosciute per introspezione, con una mappa dei rinomini noti. Colonne assenti
prendono un default, colonne sconosciute vengono segnalate e ignorate.
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Forza UTF-8 su Windows (nomi di struttura e messaggi contengono accenti)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


# ===========================================================================
# Mappa dello schema
# ===========================================================================
#
# Per ogni colonna di destinazione: i nomi candidati nella sorgente, in ordine
# di preferenza. Il primo che esiste vince. Serve a coprire i rinomini storici
# (v1.2: apparecchi.codice_interno -> descrizione) senza scrivere un migratore
# dedicato per ogni versione.

RINOMINI = {
    'apparecchi': {
        'descrizione': ['descrizione', 'codice_interno'],
    },
}

# Valori ammessi dai vincoli CHECK del target. Un valore fuori lista non deve
# far fallire l'import: viene riportato al default e segnalato nel report.
VINCOLI = {
    'apparecchi': {
        'stato': ({'funzionante', 'in_manutenzione', 'dismesso', 'da_sostituire'}, 'funzionante'),
        'classificazione': ({'I', 'IIa', 'IIb', 'III'}, None),
    },
    'manutenzioni': {
        'tipo': ({'preventiva', 'correttiva', 'verifica', 'calibrazione'}, 'preventiva'),
    },
    'verifiche': {
        'esito': ({'positivo', 'negativo', 'con_riserva'}, 'positivo'),
    },
    'documenti': {
        'tipo': ({'manuale', 'certificato', 'foto', 'report'}, 'report'),
    },
    'utenti_divisioni': {
        'ruolo_divisione': ({'admin', 'utente'}, 'utente'),
    },
}

# Colonne che contengono un percorso di file da copiare, con la sottocartella
# di destinazione da usare quando il percorso sorgente non la esprime.
COLONNE_FILE = {
    'apparecchi': {'foto_path': 'foto'},
    'documenti': {'filepath': 'documenti'},
    'manutenzioni': {'verbale_path': 'verbali'},
    'verifiche': {'documento_path': 'verifiche'},
}

# Chiavi di configurazione copiate in strutture_config con --con-config.
# (chiave_target, [chiavi candidate nel config sorgente])
CONFIG_STRUTTURA = [
    ('ai_provider',       ['ai_provider', 'default_ai_provider']),
    ('anthropic_api_key', ['anthropic_api_key', 'default_anthropic_api_key']),
    ('gemini_api_key',    ['gemini_api_key', 'default_gemini_api_key']),
    ('openai_api_key',    ['openai_api_key', 'default_openai_api_key']),
    ('ai_import_model',   ['ai_import_model', 'default_ai_import_model']),
    ('ai_email_model',    ['ai_email_model', 'default_ai_email_model']),
    ('ai_local_base_url', ['ai_local_base_url', 'default_ai_local_base_url']),
    ('ai_local_model',    ['ai_local_model', 'default_ai_local_model']),
]
# Niente chiavi smtp_*: dalla 2.6.2 il server di posta e' unico e vive nella
# configurazione di sistema del target. Copiarci dentro quello della sorgente
# scriverebbe una credenziale che nessuno legge piu' e che la migrazione di
# models.apply_schema_updates() cancella al primo avvio.

# Entità di log_attivita i cui entita_id sono rimappabili sui nuovi id.
LOG_ENTITA_RIMAPPABILI = {
    'apparecchi': 'apparecchi',
    'manutenzioni': 'manutenzioni',
    'verifiche': 'verifiche',
    'divisioni': 'divisioni',
    'utenti': 'utenti',
}


# ===========================================================================
# Report
# ===========================================================================

class Report:
    """Accumula l'esito dell'import per la stampa finale e il file JSON."""

    def __init__(self):
        self.tabelle = {}          # nome -> {importati, saltati, errori}
        self.file_copiati = 0
        self.file_mancanti = []
        self.drift = []            # differenze di schema rilevate
        self.avvisi = []
        self.utenti_collisi = []
        self.normalizzazioni = []
        self.stati_sconosciuti = []   # apparecchi con stato non riconosciuto

    def conta(self, tabella, chiave, n=1):
        voce = self.tabelle.setdefault(
            tabella, {'importati': 0, 'saltati': 0, 'errori': 0})
        voce[chiave] += n

    def avviso(self, testo):
        if testo not in self.avvisi:
            self.avvisi.append(testo)

    def normalizzato(self, tabella, colonna, valore, nuovo):
        voce = f"{tabella}.{colonna}: '{valore}' non ammesso -> '{nuovo}'"
        if voce not in self.normalizzazioni:
            self.normalizzazioni.append(voce)

    def stato_sconosciuto(self, matricola, descrizione, originale, applicato):
        self.stati_sconosciuti.append({
            'matricola': matricola or '(senza matricola)',
            'descrizione': descrizione or '',
            'stato_originale': originale,
            'stato_applicato': applicato,
        })

    def as_dict(self):
        return {
            'tabelle': self.tabelle,
            'file_copiati': self.file_copiati,
            'file_mancanti': self.file_mancanti,
            'drift_schema': self.drift,
            'utenti_collisi': self.utenti_collisi,
            'normalizzazioni': self.normalizzazioni,
            'stati_sconosciuti': self.stati_sconosciuti,
            'avvisi': self.avvisi,
        }

    def stampa_avviso_stati(self):
        """Avviso in evidenza: apparecchi il cui stato non esiste nel target.

        Va detto forte perché la conversione è ottimistica: un apparecchio
        rottamato o fuori uso nella sorgente entra qui come 'funzionante' e
        tornerebbe a comparire fra quelli attivi, con le sue scadenze.
        """
        if not self.stati_sconosciuti:
            return
        n = len(self.stati_sconosciuti)
        print(f"\n  {'!' * 3} ATTENZIONE: {n} apparecchi con stato non riconosciuto {'!' * 3}")
        print("  Nella sorgente avevano uno stato che questa versione non prevede")
        print("  (es. 'rottamato'). Sono stati importati come 'funzionante', quindi")
        print("  risultano ATTIVI e generano scadenze di manutenzione e verifica.")
        print("  Controllali e, se sono fuori uso, dismettili dalla scheda apparecchio.")
        print()
        for v in self.stati_sconosciuti[:15]:
            etichetta = f"{v['matricola']}"
            if v['descrizione']:
                etichetta += f" — {v['descrizione']}"
            print(f"    - {etichetta}: '{v['stato_originale']}' -> '{v['stato_applicato']}'")
        if n > 15:
            print(f"    ... e altri {n - 15} (elenco completo con --report)")

    def stampa(self, titolo):
        print(f"\n{titolo}")
        print("=" * len(titolo))
        if self.tabelle:
            larghezza = max(len(t) for t in self.tabelle)
            print(f"\n  {'tabella'.ljust(larghezza)}  importati  saltati  errori")
            print(f"  {'-' * larghezza}  ---------  -------  ------")
            for tabella, v in self.tabelle.items():
                print(f"  {tabella.ljust(larghezza)}  "
                      f"{v['importati']:>9}  {v['saltati']:>7}  {v['errori']:>6}")
        print(f"\n  File allegati copiati: {self.file_copiati}")
        if self.file_mancanti:
            print(f"  File riferiti dal DB ma assenti su disco: {len(self.file_mancanti)}")
            for f in self.file_mancanti[:10]:
                print(f"    - {f}")
            if len(self.file_mancanti) > 10:
                print(f"    ... e altri {len(self.file_mancanti) - 10}")
        if self.utenti_collisi:
            print(f"\n  Utenti già presenti nel target (saltati, riferimenti "
                  f"rimappati sull'utente esistente): {len(self.utenti_collisi)}")
            for e in self.utenti_collisi:
                print(f"    - {e}")
        if self.drift:
            print("\n  Differenze di schema rispetto a questa versione:")
            for d in self.drift:
                print(f"    - {d}")
        if self.normalizzazioni:
            print("\n  Valori normalizzati per rispettare i vincoli del target:")
            for n in self.normalizzazioni:
                print(f"    - {n}")
        if self.avvisi:
            print("\n  Avvisi:")
            for a in self.avvisi:
                print(f"    - {a}")
        self.stampa_avviso_stati()   # per ultimo: è ciò che va guardato subito


# ===========================================================================
# Sorgente
# ===========================================================================

class Sorgente:
    """Installazione da cui leggere. Apre sempre e solo una copia del DB."""

    def __init__(self, percorso, db_override=None, uploads_override=None):
        self.percorso = os.path.abspath(percorso)
        if not os.path.isdir(self.percorso):
            raise SystemExit(f"Percorso sorgente inesistente: {self.percorso}")

        self.config = self._leggi_config()
        self.db_path = db_override or self._trova_db()
        self.uploads_path = uploads_override or self._trova_uploads()
        self._tmp = None
        self.conn = None

    def _leggi_config(self):
        config = {}
        for nome in ('config.json', 'config.local.json'):
            p = os.path.join(self.percorso, nome)
            if os.path.exists(p):
                try:
                    with open(p, 'r', encoding='utf-8') as f:
                        config.update(json.load(f))
                except Exception as e:
                    print(f"  ! {nome} illeggibile ({e}), ignorato")
        return config

    def _trova_db(self):
        rel = self.config.get('database_path', 'data/database.sqlite')
        candidati = [
            os.path.join(self.percorso, rel),
            os.path.join(self.percorso, 'data', 'database.sqlite'),
            self.percorso if self.percorso.endswith('.sqlite') else None,
        ]
        for c in candidati:
            if c and os.path.exists(c):
                return c
        raise SystemExit(
            f"Database non trovato in {self.percorso}. "
            f"Indicalo esplicitamente con --db.")

    def _trova_uploads(self):
        rel = self.config.get('uploads_path', 'uploads')
        p = os.path.join(self.percorso, rel)
        if os.path.exists(p):
            return p
        p = os.path.join(self.percorso, 'uploads')
        return p if os.path.exists(p) else None

    def apri(self):
        """Copia il DB in un file temporaneo e lo apre.

        Un backup() da una connessione read-only produce uno snapshot coerente
        anche se l'installazione sorgente è in esecuzione (WAL incluso), e
        garantisce che non la si tocchi in alcun modo.
        """
        fd, self._tmp = tempfile.mkstemp(suffix='.sqlite', prefix='medinv_src_')
        os.close(fd)
        src = sqlite3.connect(f'file:{self.db_path}?mode=ro', uri=True)
        try:
            dst = sqlite3.connect(self._tmp)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        self.conn = sqlite3.connect(self._tmp)
        self.conn.row_factory = sqlite3.Row
        return self.conn

    def chiudi(self):
        if self.conn:
            self.conn.close()
            self.conn = None
        if self._tmp and os.path.exists(self._tmp):
            try:
                os.remove(self._tmp)
            except OSError:
                pass

    # -- introspezione ----------------------------------------------------

    def tabelle(self):
        return {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    def colonne(self, tabella):
        if tabella not in self.tabelle():
            return set()
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({tabella})")}

    def versione(self):
        """Descrizione della versione sorgente, per il report."""
        user_version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        dichiarata = self.config.get('version', 'sconosciuta')
        tabs = self.tabelle()
        if 'strutture' in tabs:
            forma = 'multi-struttura (v2.x)'
        elif 'verifiche' in tabs:
            forma = 'monostruttura con verifiche (v1.4.x)'
        elif 'accessori' in tabs:
            forma = 'monostruttura (v1.2/v1.3)'
        else:
            forma = 'monostruttura senza verifiche (v1.0/v1.1)'
        return f"{forma} — config dichiara '{dichiarata}', user_version={user_version}"

    def nome_struttura_suggerito(self):
        for chiave in ('structure_name', 'app_name'):
            valore = (self.config.get(chiave) or '').strip()
            if valore and valore.lower() != 'medinventory':
                return valore
        return os.path.basename(self.percorso.rstrip(os.sep)) or 'Struttura importata'


# ===========================================================================
# Importatore
# ===========================================================================

class Importatore:

    def __init__(self, sorgente, target_db, target_uploads, opzioni, report):
        self.src = sorgente
        self.target_db = target_db
        self.target_uploads = target_uploads
        self.opz = opzioni
        self.rep = report
        self.conn = None
        self.struttura_id = None
        self.mappe = {}            # tabella -> {id_sorgente: id_target}
        self.file_scritti = []     # per il rollback
        self._residui = {}         # chiave naturale -> id già presenti nel target

    def _riusa_esistente(self, chiave, sql, params):
        """Id di una riga del target da riusare al posto di inserirne una nuova.

        Conta le righe equivalenti invece di limitarsi a chiedere se ne esiste
        una: la sorgente può contenere legittimamente più righe con la stessa
        chiave naturale — due verifiche dello stesso apparecchio nella stessa
        data, con verbali diversi, sono un caso reale. Con un semplice "esiste
        già?" la seconda verrebbe scartata e il suo allegato perso.

        Si riusa una riga esistente solo finché il target ne ha; le copie in
        più della sorgente vengono inserite. Così un secondo import non
        duplica nulla e un primo import non perde nulla.
        """
        if self.opz.se_esiste != 'salta':
            return None
        if chiave not in self._residui:
            self._residui[chiave] = [r[0] for r in self.conn.execute(sql, params)]
        coda = self._residui[chiave]
        return coda.pop(0) if coda else None

    # -- utilità ----------------------------------------------------------

    def _mappa(self, tabella):
        return self.mappe.setdefault(tabella, {})

    def _target_colonne(self, tabella):
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({tabella})")}

    def _righe(self, tabella, ordine='id'):
        if tabella not in self.src.tabelle():
            return []
        return self.src.conn.execute(f"SELECT * FROM {tabella} ORDER BY {ordine}").fetchall()

    def _valore(self, riga, tabella, colonna, default=None):
        """Legge una colonna dalla riga sorgente, seguendo i rinomini noti."""
        candidati = RINOMINI.get(tabella, {}).get(colonna, [colonna])
        chiavi = riga.keys()
        for nome in candidati:
            if nome in chiavi:
                valore = riga[nome]
                if valore is not None:
                    return valore
        return default

    def _normalizza(self, tabella, colonna, valore):
        vincolo = VINCOLI.get(tabella, {}).get(colonna)
        if not vincolo:
            return valore
        ammessi, default = vincolo
        if valore in ammessi:
            return valore
        if valore not in (None, ''):
            self.rep.normalizzato(tabella, colonna, valore, default)
        return default

    def _inserisci(self, tabella, dati):
        colonne = ', '.join(dati)
        segnaposto = ', '.join('?' * len(dati))
        cur = self.conn.execute(
            f"INSERT INTO {tabella} ({colonne}) VALUES ({segnaposto})",
            tuple(dati.values()))
        return cur.lastrowid

    def _confronta_schema(self):
        """Registra le differenze fra schema sorgente e schema target."""
        target_tabelle = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        src_tabelle = self.src.tabelle()

        for tabella in sorted(target_tabelle & src_tabelle):
            if tabella.startswith('sqlite_'):
                continue
            src_cols = self.src.colonne(tabella)
            tgt_cols = self._target_colonne(tabella)

            # Colonne già coperte: quelle che l'importatore valorizza da sé
            # (struttura_id) e quelle risolte da un rinomino noto.
            coperte = {'id', 'struttura_id'}
            rinominate = set()
            for destinazione, candidati in RINOMINI.get(tabella, {}).items():
                sorgenti = [c for c in candidati if c in src_cols]
                if sorgenti:
                    rinominate.update(sorgenti)
                    coperte.add(destinazione)

            mancanti = tgt_cols - src_cols - coperte
            extra = src_cols - tgt_cols - rinominate
            if mancanti:
                self.rep.drift.append(
                    f"{tabella}: assenti nella sorgente {sorted(mancanti)} (default applicati)")
            if extra:
                self.rep.drift.append(
                    f"{tabella}: presenti solo nella sorgente {sorted(extra)} (ignorate)")

        assenti = {'verifiche', 'accessori', 'strutture'} & (src_tabelle ^ target_tabelle)
        for tabella in sorted(assenti - src_tabelle):
            self.rep.drift.append(f"{tabella}: tabella assente nella sorgente")

    # -- file -------------------------------------------------------------

    def _dest_subdir(self, rel, default):
        """Ricava la sottocartella logica dal percorso relativo sorgente."""
        parti = rel.replace('\\', '/').strip('/').split('/')
        if len(parti) >= 3 and parti[0] == 'strutture':
            return parti[2], parti[-1]
        if len(parti) >= 2:
            return parti[0], parti[-1]
        return default, parti[-1]

    def _copia_file(self, rel_sorgente, default_subdir):
        """Copia un allegato nell'area della nuova struttura.

        Restituisce il nuovo percorso relativo, o None se il file non c'è.
        """
        if not rel_sorgente or self.opz.senza_file:
            return rel_sorgente if self.opz.senza_file else None
        if not self.src.uploads_path:
            self.rep.file_mancanti.append(f"{rel_sorgente} (cartella uploads sorgente non trovata)")
            return None

        src_abs = os.path.join(self.src.uploads_path,
                               rel_sorgente.replace('\\', '/').replace('/', os.sep))
        if not os.path.exists(src_abs):
            self.rep.file_mancanti.append(rel_sorgente)
            return None

        subdir, nome = self._dest_subdir(rel_sorgente, default_subdir)

        if self.opz.single_struttura_target:
            dest_dir = os.path.join(self.target_uploads, subdir)
            rel_prefix = subdir
        else:
            dest_dir = os.path.join(self.target_uploads, 'strutture',
                                    str(self.struttura_id), subdir)
            rel_prefix = f"strutture/{self.struttura_id}/{subdir}"
        os.makedirs(dest_dir, exist_ok=True)

        # Collisione di nome: solo possibile fondendo in una struttura esistente
        finale = nome
        contatore = 1
        while os.path.exists(os.path.join(dest_dir, finale)):
            radice, est = os.path.splitext(nome)
            finale = f"{radice}_{contatore}{est}"
            contatore += 1

        dest_abs = os.path.join(dest_dir, finale)
        shutil.copy2(src_abs, dest_abs)
        self.file_scritti.append(dest_abs)
        self.rep.file_copiati += 1
        return f"{rel_prefix}/{finale}"

    def _rimuovi_file_scritti(self):
        for p in self.file_scritti:
            try:
                os.remove(p)
            except OSError:
                pass
        self.file_scritti = []

    # -- fasi -------------------------------------------------------------

    def prepara(self):
        self.conn = sqlite3.connect(self.target_db)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        tabelle = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if 'strutture' not in tabelle:
            raise SystemExit(
                "Il database di destinazione non ha lo schema v2 (tabella 'strutture' assente).\n"
                "Avvia l'applicazione una volta (python app.py) per applicare lo schema, poi riprova.")
        self._confronta_schema()
        self._controlla_destinazione()

    def _nome_struttura(self):
        return self.opz.struttura_nome or self.src.nome_struttura_suggerito()

    def _controlla_destinazione(self):
        """Verifica la destinazione prima di toccare qualsiasi cosa."""
        if self.opz.in_struttura:
            riga = self.conn.execute(
                "SELECT id FROM strutture WHERE id = ?", (self.opz.in_struttura,)).fetchone()
            if not riga:
                raise SystemExit(
                    f"Struttura {self.opz.in_struttura} inesistente nel database di destinazione.")
            return
        nome = self._nome_struttura()
        # Una seconda esecuzione dello stesso comando creerebbe una struttura
        # gemella e duplicherebbe tutto: meglio fermarsi e far scegliere.
        gemella = self.conn.execute(
            "SELECT id FROM strutture WHERE nome = ?", (nome,)).fetchone()
        if gemella:
            raise SystemExit(
                f"Esiste già una struttura chiamata '{nome}' (id {gemella['id']}).\n"
                f"  - per aggiungere i dati a quella struttura: "
                f"--in-struttura {gemella['id']}\n"
                f"  - per crearne una distinta: --struttura-nome \"<altro nome>\"")

    def crea_struttura(self):
        if self.opz.in_struttura:
            riga = self.conn.execute(
                "SELECT id, nome FROM strutture WHERE id = ?",
                (self.opz.in_struttura,)).fetchone()
            if not riga:
                raise SystemExit(f"Struttura {self.opz.in_struttura} inesistente nel target.")
            self.struttura_id = riga['id']
            print(f"  Importo dentro la struttura esistente: {riga['nome']} (id {riga['id']})")
            return

        nome = self.opz.struttura_nome or self.src.nome_struttura_suggerito()

        # Una seconda esecuzione dello stesso comando creerebbe una struttura
        # gemella e duplicherebbe tutto: meglio fermarsi e far scegliere.
        gemella = self.conn.execute(
            "SELECT id FROM strutture WHERE nome = ?", (nome,)).fetchone()
        if gemella:
            raise SystemExit(
                f"Esiste già una struttura chiamata '{nome}' (id {gemella['id']}).\n"
                f"  - per aggiungere i dati a quella struttura: "
                f"--in-struttura {gemella['id']}\n"
                f"  - per crearne una distinta: --struttura-nome \"<altro nome>\"")

        codice = self._codice_univoco(nome)
        self.struttura_id = self._inserisci('strutture', {
            'nome': nome,
            'codice': codice,
            'descrizione': f"Importata da {self.src.percorso} il "
                           f"{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            'modalita': 'avanzata',
            'attiva': 1,
        })
        self.rep.conta('strutture', 'importati')
        print(f"  Creata struttura: {nome} (codice {codice}, id {self.struttura_id})")

    def _codice_univoco(self, nome):
        base = ''.join(c for c in nome.upper() if c.isalnum())[:8] or 'IMPORT'
        codice = base
        n = 1
        while self.conn.execute(
                "SELECT 1 FROM strutture WHERE codice = ?", (codice,)).fetchone():
            n += 1
            suffisso = str(n)
            codice = base[:8 - len(suffisso)] + suffisso
        return codice

    def importa_divisioni(self):
        mappa = self._mappa('divisioni')
        for riga in self._righe('divisioni'):
            nome = self._valore(riga, 'divisioni', 'nome', 'Divisione')
            codice = self._valore(riga, 'divisioni', 'codice') or nome[:8].upper()

            esistente = self.conn.execute(
                "SELECT id FROM divisioni WHERE struttura_id = ? AND (nome = ? OR codice = ?)",
                (self.struttura_id, nome, codice)).fetchone()
            if esistente:
                mappa[riga['id']] = esistente['id']
                self.rep.conta('divisioni', 'saltati')
                continue

            nuovo = self._inserisci('divisioni', {
                'nome': nome,
                'codice': codice,
                'colore': self._valore(riga, 'divisioni', 'colore', '#0ea5e9'),
                'descrizione': self._valore(riga, 'divisioni', 'descrizione'),
                'attiva': self._valore(riga, 'divisioni', 'attiva', 1),
                'created_at': self._valore(riga, 'divisioni', 'created_at'),
                'struttura_id': self.struttura_id,
            })
            mappa[riga['id']] = nuovo
            self.rep.conta('divisioni', 'importati')

    def importa_utenti(self):
        mappa = self._mappa('utenti')
        if self.opz.senza_utenti:
            self.rep.avviso("Utenti non importati (--senza-utenti): "
                            "created_by/updated_by saranno NULL.")
            return

        for riga in self._righe('utenti'):
            email = (self._valore(riga, 'utenti', 'email') or '').strip().lower()
            if not email:
                self.rep.conta('utenti', 'errori')
                continue

            esistente = self.conn.execute(
                "SELECT id FROM utenti WHERE email = ?", (email,)).fetchone()
            if esistente:
                # email è UNIQUE globale: l'utente esiste già in questo deployment.
                # I riferimenti puntano all'utente esistente, così la tracciabilità
                # non si perde e non nascono account duplicati.
                mappa[riga['id']] = esistente['id']
                self.rep.conta('utenti', 'saltati')
                self.rep.utenti_collisi.append(email)
                continue

            ruolo = self._valore(riga, 'utenti', 'ruolo', 'utente')
            if ruolo == 'superadmin':
                # Un superadmin della sorgente non deve ereditare poteri globali
                # su questo deployment: diventa admin della struttura importata.
                ruolo = 'admin'
                self.rep.normalizzato('utenti', 'ruolo', 'superadmin',
                                      'admin (nessun potere globale sul target)')
            elif ruolo not in ('admin', 'utente', 'tecnico'):
                self.rep.normalizzato('utenti', 'ruolo', ruolo, 'utente')
                ruolo = 'utente'

            nuovo = self._inserisci('utenti', {
                'email': email,
                'password_hash': self._valore(riga, 'utenti', 'password_hash', '!'),
                'nome': self._valore(riga, 'utenti', 'nome', ''),
                'cognome': self._valore(riga, 'utenti', 'cognome', ''),
                'ruolo': ruolo,
                'attivo': self._valore(riga, 'utenti', 'attivo', 1),
                'primo_accesso': 1 if self.opz.reset_password
                                 else self._valore(riga, 'utenti', 'primo_accesso', 0),
                'ultimo_accesso': self._valore(riga, 'utenti', 'ultimo_accesso'),
                'created_at': self._valore(riga, 'utenti', 'created_at'),
                'struttura_id': self.struttura_id,
            })
            mappa[riga['id']] = nuovo
            self.rep.conta('utenti', 'importati')

        # divisione_default_id si può rimappare solo dopo aver creato le divisioni
        div_map = self._mappa('divisioni')
        for riga in self._righe('utenti'):
            nuovo_id = mappa.get(riga['id'])
            vecchia_div = self._valore(riga, 'utenti', 'divisione_default_id')
            if nuovo_id and vecchia_div and vecchia_div in div_map:
                self.conn.execute(
                    "UPDATE utenti SET divisione_default_id = ? WHERE id = ?",
                    (div_map[vecchia_div], nuovo_id))

    def importa_utenti_divisioni(self):
        if self.opz.senza_utenti:
            return
        u_map, d_map = self._mappa('utenti'), self._mappa('divisioni')
        for riga in self._righe('utenti_divisioni'):
            u = u_map.get(self._valore(riga, 'utenti_divisioni', 'utente_id'))
            d = d_map.get(self._valore(riga, 'utenti_divisioni', 'divisione_id'))
            if not (u and d):
                self.rep.conta('utenti_divisioni', 'saltati')
                continue
            if self.conn.execute(
                    "SELECT 1 FROM utenti_divisioni WHERE utente_id = ? AND divisione_id = ?",
                    (u, d)).fetchone():
                self.rep.conta('utenti_divisioni', 'saltati')
                continue
            self._inserisci('utenti_divisioni', {
                'utente_id': u,
                'divisione_id': d,
                'ruolo_divisione': self._normalizza(
                    'utenti_divisioni', 'ruolo_divisione',
                    self._valore(riga, 'utenti_divisioni', 'ruolo_divisione', 'utente')),
            })
            self.rep.conta('utenti_divisioni', 'importati')

    def importa_apparecchi(self):
        mappa = self._mappa('apparecchi')
        d_map, u_map = self._mappa('divisioni'), self._mappa('utenti')
        semplici = [
            'matricola', 'numero_inventario', 'marca', 'modello',
            'anno_fabbricazione', 'ubicazione', 'connesso_rete', 'ip_address',
            'mac_address', 'hostname', 'porta', 'protocollo', 'url_interfaccia',
            'fornitore', 'codice_fornitore', 'garanzia_scadenza',
            'contratto_manutenzione', 'note', 'created_at', 'updated_at',
        ]
        for riga in self._righe('apparecchi'):
            divisione = d_map.get(self._valore(riga, 'apparecchi', 'divisione_id'))
            if divisione is None:
                # Apparecchio orfano nella sorgente: lo agganciamo alla prima
                # divisione importata, altrimenti andrebbe perso.
                divisione = next(iter(d_map.values()), None)
                if divisione is None:
                    self.rep.conta('apparecchi', 'errori')
                    continue
                self.rep.avviso("Apparecchi senza divisione valida agganciati "
                                "alla prima divisione importata.")

            matricola = self._valore(riga, 'apparecchi', 'matricola', '')
            modello = self._valore(riga, 'apparecchi', 'modello', '')
            esistente = self._riusa_esistente(
                ('apparecchi', self.struttura_id, matricola, modello),
                """SELECT id FROM apparecchi
                   WHERE struttura_id = ? AND matricola = ? AND modello = ? ORDER BY id""",
                (self.struttura_id, matricola, modello))
            if esistente:
                mappa[riga['id']] = esistente
                self.rep.conta('apparecchi', 'saltati')
                continue

            dati = {c: self._valore(riga, 'apparecchi', c) for c in semplici}
            dati['descrizione'] = self._valore(riga, 'apparecchi', 'descrizione')

            stato_originale = self._valore(riga, 'apparecchi', 'stato', 'funzionante')
            dati['stato'] = self._normalizza('apparecchi', 'stato', stato_originale)
            if dati['stato'] != stato_originale and stato_originale not in (None, ''):
                # Va tracciato per nome: chi importa deve poter andare a
                # ricontrollare questi apparecchi uno per uno.
                self.rep.stato_sconosciuto(
                    matricola, dati['descrizione'], stato_originale, dati['stato'])
            dati['classificazione'] = self._normalizza(
                'apparecchi', 'classificazione', self._valore(riga, 'apparecchi', 'classificazione'))
            dati['soggetto_verifica'] = self._valore(riga, 'apparecchi', 'soggetto_verifica', 1)
            dati['divisione_id'] = divisione
            dati['struttura_id'] = self.struttura_id
            dati['created_by'] = u_map.get(self._valore(riga, 'apparecchi', 'created_by'))
            dati['updated_by'] = u_map.get(self._valore(riga, 'apparecchi', 'updated_by'))
            dati['foto_path'] = self._copia_file(
                self._valore(riga, 'apparecchi', 'foto_path'), 'foto')

            mappa[riga['id']] = self._inserisci('apparecchi', dati)
            self.rep.conta('apparecchi', 'importati')

    def importa_accessori(self):
        a_map, u_map = self._mappa('apparecchi'), self._mappa('utenti')
        for riga in self._righe('accessori'):
            app_id = a_map.get(self._valore(riga, 'accessori', 'apparecchio_id'))
            if not app_id:
                self.rep.conta('accessori', 'saltati')
                continue

            descrizione = self._valore(riga, 'accessori', 'descrizione', '')
            matricola = self._valore(riga, 'accessori', 'matricola')
            if self._riusa_esistente(
                    ('accessori', app_id, descrizione, matricola),
                    """SELECT id FROM accessori
                       WHERE apparecchio_id = ? AND descrizione = ? AND matricola IS ? ORDER BY id""",
                    (app_id, descrizione, matricola)):
                self.rep.conta('accessori', 'saltati')
                continue

            self._inserisci('accessori', {
                'apparecchio_id': app_id,
                'descrizione': descrizione,
                'produttore': self._valore(riga, 'accessori', 'produttore'),
                'modello': self._valore(riga, 'accessori', 'modello'),
                'matricola': matricola,
                'created_by': u_map.get(self._valore(riga, 'accessori', 'created_by')),
                'created_at': self._valore(riga, 'accessori', 'created_at'),
            })
            self.rep.conta('accessori', 'importati')

    def importa_manutenzioni(self):
        mappa = self._mappa('manutenzioni')
        a_map, u_map = self._mappa('apparecchi'), self._mappa('utenti')
        for riga in self._righe('manutenzioni'):
            app_id = a_map.get(self._valore(riga, 'manutenzioni', 'apparecchio_id'))
            if not app_id:
                self.rep.conta('manutenzioni', 'saltati')
                continue
            tipo = self._normalizza('manutenzioni', 'tipo',
                                    self._valore(riga, 'manutenzioni', 'tipo', 'preventiva'))
            data = self._valore(riga, 'manutenzioni', 'data_intervento')

            esistente = self._riusa_esistente(
                ('manutenzioni', app_id, tipo, data),
                """SELECT id FROM manutenzioni
                   WHERE apparecchio_id = ? AND tipo = ? AND data_intervento IS ? ORDER BY id""",
                (app_id, tipo, data))
            if esistente:
                mappa[riga['id']] = esistente
                self.rep.conta('manutenzioni', 'saltati')
                continue

            mappa[riga['id']] = self._inserisci('manutenzioni', {
                'apparecchio_id': app_id,
                'tipo': tipo,
                'data_intervento': data,
                'prossima_scadenza': self._valore(riga, 'manutenzioni', 'prossima_scadenza'),
                'periodicita_giorni': self._valore(riga, 'manutenzioni', 'periodicita_giorni'),
                'tecnico_ditta': self._valore(riga, 'manutenzioni', 'tecnico_ditta'),
                'descrizione': self._valore(riga, 'manutenzioni', 'descrizione'),
                'esito': self._valore(riga, 'manutenzioni', 'esito'),
                'costo': self._valore(riga, 'manutenzioni', 'costo'),
                'verbale_path': self._copia_file(
                    self._valore(riga, 'manutenzioni', 'verbale_path'), 'verbali'),
                'created_by': u_map.get(self._valore(riga, 'manutenzioni', 'created_by')),
                'created_at': self._valore(riga, 'manutenzioni', 'created_at'),
            })
            self.rep.conta('manutenzioni', 'importati')

    def importa_verifiche(self):
        mappa = self._mappa('verifiche')
        a_map, u_map = self._mappa('apparecchi'), self._mappa('utenti')
        for riga in self._righe('verifiche'):
            app_id = a_map.get(self._valore(riga, 'verifiche', 'apparecchio_id'))
            if not app_id:
                self.rep.conta('verifiche', 'saltati')
                continue
            data = self._valore(riga, 'verifiche', 'data_verifica')

            esistente = self._riusa_esistente(
                ('verifiche', app_id, data),
                "SELECT id FROM verifiche WHERE apparecchio_id = ? AND data_verifica IS ? ORDER BY id",
                (app_id, data))
            if esistente:
                mappa[riga['id']] = esistente
                self.rep.conta('verifiche', 'saltati')
                continue

            mappa[riga['id']] = self._inserisci('verifiche', {
                'apparecchio_id': app_id,
                'data_verifica': data,
                'prossima_scadenza': self._valore(riga, 'verifiche', 'prossima_scadenza'),
                'periodicita_giorni': self._valore(riga, 'verifiche', 'periodicita_giorni', 730),
                'esito': self._normalizza('verifiche', 'esito',
                                          self._valore(riga, 'verifiche', 'esito', 'positivo')),
                'tecnico_ditta': self._valore(riga, 'verifiche', 'tecnico_ditta'),
                'note': self._valore(riga, 'verifiche', 'note'),
                'documento_path': self._copia_file(
                    self._valore(riga, 'verifiche', 'documento_path'), 'verifiche'),
                'created_by': u_map.get(self._valore(riga, 'verifiche', 'created_by')),
                'created_at': self._valore(riga, 'verifiche', 'created_at'),
            })
            self.rep.conta('verifiche', 'importati')

    def importa_documenti(self):
        a_map, u_map = self._mappa('apparecchi'), self._mappa('utenti')
        for riga in self._righe('documenti'):
            app_id = a_map.get(self._valore(riga, 'documenti', 'apparecchio_id'))
            if not app_id:
                self.rep.conta('documenti', 'saltati')
                continue

            nome_file = self._valore(riga, 'documenti', 'filename', '')
            if self._riusa_esistente(
                    ('documenti', app_id, nome_file),
                    "SELECT id FROM documenti WHERE apparecchio_id = ? AND filename = ? ORDER BY id",
                    (app_id, nome_file)):
                self.rep.conta('documenti', 'saltati')
                continue

            nuovo_path = self._copia_file(
                self._valore(riga, 'documenti', 'filepath'), 'documenti')
            if nuovo_path is None and not self.opz.senza_file:
                # Il file non c'è più: il record da solo non serve a nulla.
                self.rep.conta('documenti', 'saltati')
                continue
            self._inserisci('documenti', {
                'apparecchio_id': app_id,
                'tipo': self._normalizza('documenti', 'tipo',
                                         self._valore(riga, 'documenti', 'tipo', 'report')),
                'filename': nome_file,
                'filepath': nuovo_path,
                'filesize': self._valore(riga, 'documenti', 'filesize'),
                'uploaded_by': u_map.get(self._valore(riga, 'documenti', 'uploaded_by')),
                'uploaded_at': self._valore(riga, 'documenti', 'uploaded_at'),
            })
            self.rep.conta('documenti', 'importati')

    def importa_log(self):
        if not self.opz.con_log:
            return
        u_map = self._mappa('utenti')
        # Il registro non ha una chiave naturale e può contenere molte righe:
        # il controllo anti-duplicato ha senso solo quando si fonde in una
        # struttura che potrebbe già averne (in una struttura appena creata no).
        controlla_duplicati = bool(self.opz.in_struttura)
        for riga in self._righe('log_attivita'):
            entita = self._valore(riga, 'log_attivita', 'entita', '')
            entita_id = self._valore(riga, 'log_attivita', 'entita_id')
            tabella = LOG_ENTITA_RIMAPPABILI.get(entita)
            if tabella and entita_id is not None:
                # Se non rimappabile azzeriamo l'id: puntare a una riga altrui
                # sarebbe peggio che non puntare a nulla. Il testo resta.
                entita_id = self._mappa(tabella).get(entita_id)
            elif entita_id is not None:
                entita_id = None

            utente = u_map.get(self._valore(riga, 'log_attivita', 'utente_id'))
            azione = self._valore(riga, 'log_attivita', 'azione', 'sconosciuta')
            creato = self._valore(riga, 'log_attivita', 'created_at')
            if controlla_duplicati and self._riusa_esistente(
                    ('log_attivita', self.struttura_id, utente, azione,
                     entita or 'sconosciuta', creato),
                    """SELECT id FROM log_attivita
                       WHERE struttura_id = ? AND utente_id IS ? AND azione = ?
                         AND entita = ? AND created_at IS ? ORDER BY id""",
                    (self.struttura_id, utente, azione, entita or 'sconosciuta',
                     creato)):
                self.rep.conta('log_attivita', 'saltati')
                continue

            self._inserisci('log_attivita', {
                'utente_id': utente,
                'azione': azione,
                'entita': entita or 'sconosciuta',
                'entita_id': entita_id,
                'dettagli': self._valore(riga, 'log_attivita', 'dettagli'),
                'ip_address': self._valore(riga, 'log_attivita', 'ip_address'),
                'struttura_id': self.struttura_id,
                'created_at': creato,
            })
            self.rep.conta('log_attivita', 'importati')

    def importa_import_history(self):
        if not self.opz.con_import_history:
            return
        mappa = self._mappa('import_history')
        d_map, u_map = self._mappa('divisioni'), self._mappa('utenti')
        for riga in self._righe('import_history'):
            mappa[riga['id']] = self._inserisci('import_history', {
                'tipo_import': self._valore(riga, 'import_history', 'tipo_import', 'inventario'),
                'filename': self._valore(riga, 'import_history', 'filename', ''),
                'filepath': self._valore(riga, 'import_history', 'filepath', ''),
                'tipo_documento': self._valore(riga, 'import_history', 'tipo_documento'),
                'divisione_id': d_map.get(self._valore(riga, 'import_history', 'divisione_id')),
                'struttura_id': self.struttura_id,
                'email_from': self._valore(riga, 'import_history', 'email_from'),
                'email_subject': self._valore(riga, 'import_history', 'email_subject'),
                'totale_righe': self._valore(riga, 'import_history', 'totale_righe'),
                'righe_importate': self._valore(riga, 'import_history', 'righe_importate'),
                'righe_errori': self._valore(riga, 'import_history', 'righe_errori'),
                'stato': self._valore(riga, 'import_history', 'stato', 'completed'),
                'ai_response': self._valore(riga, 'import_history', 'ai_response'),
                'errori_dettaglio': self._valore(riga, 'import_history', 'errori_dettaglio'),
                'imported_by': u_map.get(self._valore(riga, 'import_history', 'imported_by')),
                'created_at': self._valore(riga, 'import_history', 'created_at'),
                'completed_at': self._valore(riga, 'import_history', 'completed_at'),
            })
            self.rep.conta('import_history', 'importati')

    def importa_config(self):
        """Copia le impostazioni AI della sorgente in strutture_config."""
        if not self.opz.con_config:
            return
        config = self.src.config
        for chiave, candidati in CONFIG_STRUTTURA:
            valore = None
            for c in candidati:
                v = config.get(c)
                if v not in (None, ''):
                    valore = v
                    break
            if valore is None:
                continue
            if isinstance(valore, bool):
                valore = '1' if valore else ''
            self.conn.execute(
                """INSERT INTO strutture_config (struttura_id, chiave, valore)
                   VALUES (?, ?, ?)
                   ON CONFLICT(struttura_id, chiave) DO UPDATE SET valore = excluded.valore""",
                (self.struttura_id, chiave, str(valore)))
            self.rep.conta('strutture_config', 'importati')

        # Il server di posta della sorgente NON si importa. Prima finiva qui
        # dentro, password compresa, ricifrata con la encryption_key del
        # target; dalla 2.6.2 quelle chiavi non le legge piu' nessuno e la
        # migrazione le cancella al primo avvio. L'avviso serve perche' il
        # silenzio farebbe credere che gli avvisi di scadenza partiranno da
        # soli: vanno configurati una volta nel sistema, non per struttura.
        if any(config.get(c) for c in ('smtp_host', 'smtp_user', 'smtp_password')):
            self.rep.avviso("Il server di posta della sorgente non viene importato: "
                            "dalla 2.6.2 e' unico e si configura nella configurazione "
                            "di sistema. Controlla che sia impostato, altrimenti gli "
                            "avvisi di scadenza non partiranno.")

    # -- orchestrazione ---------------------------------------------------

    def esegui(self):
        self.conn.execute("BEGIN")
        try:
            self.crea_struttura()
            self.importa_divisioni()
            self.importa_utenti()
            self.importa_utenti_divisioni()
            self.importa_apparecchi()
            self.importa_accessori()
            self.importa_manutenzioni()
            self.importa_verifiche()
            self.importa_documenti()
            self.importa_import_history()
            self.importa_log()
            self.importa_config()
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            self._rimuovi_file_scritti()
            raise

    def chiudi(self):
        if self.conn:
            self.conn.close()
            self.conn = None


# ===========================================================================
# Analisi (dry-run)
# ===========================================================================

def analizza(sorgente, report):
    """Conta cosa c'è nella sorgente, senza scrivere nulla."""
    interesse = ['divisioni', 'utenti', 'utenti_divisioni', 'apparecchi',
                 'accessori', 'manutenzioni', 'verifiche', 'documenti',
                 'import_history', 'log_attivita']
    tabelle = sorgente.tabelle()
    conteggi = {}
    for t in interesse:
        if t in tabelle:
            conteggi[t] = sorgente.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        else:
            conteggi[t] = None
    return conteggi


def stati_non_riconosciuti(sorgente):
    """Stati di apparecchi presenti nella sorgente ma non ammessi qui.

    Serve a mostrare l'avviso già in fase di analisi (quindi anche con
    --dry-run), prima che qualcuno lanci l'import vero.
    """
    if 'apparecchi' not in sorgente.tabelle():
        return []
    if 'stato' not in sorgente.colonne('apparecchi'):
        return []
    ammessi = VINCOLI['apparecchi']['stato'][0]
    righe = sorgente.conn.execute(
        "SELECT stato, COUNT(*) FROM apparecchi WHERE stato IS NOT NULL GROUP BY stato")
    return [(stato, n) for stato, n in righe if stato not in ammessi]


def conta_file(sorgente):
    """Quanti allegati referenziati esistono davvero su disco."""
    presenti, mancanti = 0, 0
    if not sorgente.uploads_path:
        return 0, 0
    for tabella, colonne in COLONNE_FILE.items():
        if tabella not in sorgente.tabelle():
            continue
        disponibili = sorgente.colonne(tabella)
        for colonna in colonne:
            if colonna not in disponibili:
                continue
            for (rel,) in sorgente.conn.execute(
                    f"SELECT {colonna} FROM {tabella} WHERE {colonna} IS NOT NULL AND {colonna} != ''"):
                p = os.path.join(sorgente.uploads_path,
                                 rel.replace('\\', '/').replace('/', os.sep))
                if os.path.exists(p):
                    presenti += 1
                else:
                    mancanti += 1
    return presenti, mancanti


# ===========================================================================
# CLI
# ===========================================================================

def costruisci_parser():
    p = argparse.ArgumentParser(
        prog='importa_installazione.py',
        description="Importa i dati di un'altra installazione MedInventory in questa, "
                    "come nuova struttura. La sorgente non viene modificata.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Esempi:
  # Analisi preliminare (nessuna scrittura)
  python importa_installazione.py C:\\MedInventory_Ospedale --dry-run

  # Import completo con configurazione AI e log attività
  python importa_installazione.py C:\\MedInventory_Ospedale \\
      --struttura-nome "Ospedale San Rocco" --con-config --con-log

  # Fusione dentro una struttura già presente
  python importa_installazione.py C:\\vecchia_installazione --in-struttura 3
""")
    p.add_argument('sorgente',
                   help="Cartella dell'installazione da importare "
                        "(quella che contiene config.json, data/, uploads/)")

    g = p.add_argument_group('destinazione')
    g.add_argument('--struttura-nome', metavar='NOME',
                   help="Nome della struttura da creare (default: dedotto dal config sorgente)")
    g.add_argument('--in-struttura', type=int, metavar='ID',
                   help="Importa dentro una struttura già esistente invece di crearne una")
    g.add_argument('--target', metavar='DIR', default=BASE_DIR,
                   help="Installazione di destinazione (default: questa)")

    g = p.add_argument_group('cosa importare')
    g.add_argument('--con-log', action='store_true',
                   help="Importa anche il registro attività")
    g.add_argument('--con-import-history', action='store_true',
                   help="Importa anche lo storico degli import AI")
    g.add_argument('--con-config', action='store_true',
                   help="Copia provider/chiavi AI e impostazioni SMTP nella nuova struttura")
    g.add_argument('--senza-file', action='store_true',
                   help="Non copiare gli allegati (solo dati)")
    g.add_argument('--senza-utenti', action='store_true',
                   help="Non importare gli utenti (created_by/updated_by restano vuoti)")

    g = p.add_argument_group('comportamento')
    g.add_argument('--dry-run', action='store_true',
                   help="Analizza e mostra il piano, senza scrivere nulla")
    g.add_argument('--se-esiste', choices=('salta', 'duplica'), default='salta',
                   help="Record già presenti nel target (default: salta)")
    g.add_argument('--reset-password', action='store_true',
                   help="Forza il cambio password al primo accesso per gli utenti importati")
    g.add_argument('--no-backup', action='store_true',
                   help="Non fare il backup del database di destinazione (sconsigliato)")
    g.add_argument('-y', '--yes', action='store_true',
                   help="Non chiedere conferma")

    g = p.add_argument_group('percorsi espliciti')
    g.add_argument('--db', metavar='FILE', help="Database sorgente, se non autorilevabile")
    g.add_argument('--uploads', metavar='DIR', help="Cartella uploads sorgente")
    g.add_argument('--report', metavar='FILE', help="Scrive il report dettagliato in JSON")

    return p


def carica_config_target(target_dir):
    config = {}
    for nome in ('config.json', 'config.local.json'):
        p = os.path.join(target_dir, nome)
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    config.update(json.load(f))
            except Exception:
                pass
    return config


def main(argv=None):
    args = costruisci_parser().parse_args(argv)

    target_dir = os.path.abspath(args.target)
    target_config = carica_config_target(target_dir)
    target_db = os.path.join(target_dir, target_config.get('database_path', 'data/database.sqlite'))
    target_uploads = os.path.join(target_dir, target_config.get('uploads_path', 'uploads'))
    target_backups = os.path.join(target_dir, target_config.get('backups_path', 'backups'))

    if not os.path.exists(target_db):
        raise SystemExit(
            f"Database di destinazione non trovato: {target_db}\n"
            f"Avvia l'applicazione una volta per crearlo, poi riprova.")

    args.target_config = target_config
    args.single_struttura_target = bool(target_config.get('single_struttura', False))

    report = Report()
    sorgente = Sorgente(args.sorgente, args.db, args.uploads)

    print("\nMedInventory — importazione da un'altra installazione")
    print("=" * 53)
    print(f"  Sorgente : {sorgente.percorso}")
    print(f"     DB    : {sorgente.db_path}")
    print(f"     upload: {sorgente.uploads_path or '(non trovata)'}")
    print(f"  Target   : {target_dir}")

    sorgente.apri()
    try:
        print(f"  Versione sorgente rilevata: {sorgente.versione()}")

        conteggi = analizza(sorgente, report)
        presenti, mancanti = conta_file(sorgente)

        print("\nContenuto della sorgente:")
        for tabella, n in conteggi.items():
            if n is None:
                print(f"  {tabella.ljust(18)} —  (tabella assente in questa versione)")
            else:
                print(f"  {tabella.ljust(18)} {n}")
        print(f"\n  Allegati referenziati: {presenti} presenti, {mancanti} mancanti su disco")

        stati_estranei = stati_non_riconosciuti(sorgente)
        if stati_estranei:
            totale = sum(n for _, n in stati_estranei)
            default_stato = VINCOLI['apparecchi']['stato'][1]
            print(f"\n  !!! ATTENZIONE: {totale} apparecchi hanno uno stato che questa "
                  f"versione non prevede:")
            for stato, n in stati_estranei:
                print(f"        '{stato}': {n} apparecchi")
            print(f"  Verranno importati come '{default_stato}', quindi risulteranno ATTIVI")
            print("  e genereranno scadenze di manutenzione e verifica. Se sono fuori uso,")
            print("  vanno dismessi a mano dopo l'import: l'elenco per matricola è nel")
            print("  riepilogo finale e nel file --report.")

        if not args.con_log and conteggi.get('log_attivita'):
            print("\n  Nota: il registro attività non verrà importato (usa --con-log).")
        if not args.con_import_history and conteggi.get('import_history'):
            print("  Nota: lo storico import AI non verrà importato (usa --con-import-history).")
        print("  Nota: sessioni, tentativi di login, token API e configurazioni email "
              "della sorgente\n        non vengono importati: sono legati "
              "all'installazione d'origine.")

        if args.dry_run:
            destinazione = (f"struttura esistente id {args.in_struttura}" if args.in_struttura
                            else f"nuova struttura '{args.struttura_nome or sorgente.nome_struttura_suggerito()}'")
            print(f"\n  I dati finirebbero in: {destinazione}")
            print("\n  --dry-run: nessuna modifica effettuata.")
            return 0

        # Valida schema e destinazione prima di chiedere conferma e di fare il
        # backup: se qualcosa non torna, si esce senza aver toccato nulla.
        importatore = Importatore(sorgente, target_db, target_uploads, args, report)
        importatore.prepara()

        if not args.yes:
            destinazione = (f"la struttura esistente id {args.in_struttura}" if args.in_struttura
                            else f"una nuova struttura '{args.struttura_nome or sorgente.nome_struttura_suggerito()}'")
            print(f"\n  I dati verranno importati in {destinazione}.")
            risposta = input("  Procedere? [s/N] ").strip().lower()
            if risposta not in ('s', 'si', 'sì', 'y', 'yes'):
                print("  Annullato.")
                importatore.chiudi()
                return 1

        if not args.no_backup:
            try:
                from backup_service import create_backup
                esito = create_backup(target_db, target_backups,
                                      target_config.get('backup_retention', 4))
                print(f"\n  Backup del database di destinazione: {esito['filename']}")
            except Exception as e:
                raise SystemExit(
                    f"Backup del target fallito: {e}\n"
                    f"Import interrotto. Usa --no-backup solo se hai già una copia sicura.")

        print("\n  Importazione in corso...")
        try:
            importatore.esegui()
        finally:
            importatore.chiudi()

        report.stampa("Importazione completata")
        print(f"\n  Struttura di destinazione: id {importatore.struttura_id}")
        if report.utenti_collisi and not args.senza_utenti:
            print("  Gli utenti già esistenti mantengono la password del target, non quella "
                  "della sorgente.")
        print()

    finally:
        sorgente.chiudi()

    if args.report:
        dati = report.as_dict()
        dati['sorgente'] = sorgente.percorso
        dati['target'] = target_dir
        dati['eseguito_il'] = datetime.now().isoformat(timespec='seconds')
        with open(args.report, 'w', encoding='utf-8') as f:
            json.dump(dati, f, indent=2, ensure_ascii=False)
        print(f"  Report scritto in {args.report}\n")

    return 0


if __name__ == '__main__':
    sys.exit(main())
