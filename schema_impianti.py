"""
MedInventory - DDL delle tabelle impianti (2.7.x).

Modulo condiviso da due percorsi di migrazione che non possono importarsi a
vicenda:

- ``models.apply_schema_updates()``, che gira a ogni avvio dell'applicazione;
- ``migrate.py``, strumento standalone che non importa Flask - e' cio' che gli
  permette di puntare con ``--db`` a un'altra installazione.

Le istruzioni sono idempotenti (``IF NOT EXISTS``, oppure ``ALTER TABLE ADD
COLUMN`` che fallisce se la colonna c'e' gia'): entrambi i chiamanti le
eseguono una per una ignorando gli errori dei passi gia' applicati.
"""

# Versione schema (PRAGMA user_version) introdotta dalle tabelle impianti.
# Convenzione: major*100 + minor*10 + patch  (v2.7.1 -> 271)
SCHEMA_VERSION_IMPIANTI = 271

# Tabelle create qui sotto: servono a capire se la migrazione e' gia' passata
# senza doverne interpretare il DDL.
TABELLE_IMPIANTI = (
    'manutentori',
    'impianti',
    'impianti_componenti',
    'impianti_documenti',
    'impianti_scadenze',
    'impianti_interventi',
    'impianti_avvisi_inviati',
)


def tabelle_impianti_presenti(conn):
    """True se tutte le tabelle impianti esistono gia' nel database."""
    presenti = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    return all(t in presenti for t in TABELLE_IMPIANTI)


DDL_IMPIANTI = [
        # Dati opzionali della divisione: servono come destinatari degli
        # avvisi di scadenza degli impianti.
        "ALTER TABLE divisioni ADD COLUMN indirizzo TEXT",
        "ALTER TABLE divisioni ADD COLUMN email TEXT",
        "ALTER TABLE divisioni ADD COLUMN telefono TEXT",
        "ALTER TABLE divisioni ADD COLUMN responsabile TEXT",
        """CREATE TABLE IF NOT EXISTS manutentori (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            struttura_id INTEGER NOT NULL,
            ragione_sociale TEXT NOT NULL,
            indirizzo TEXT,
            telefono TEXT,
            email TEXT,
            partita_iva TEXT,
            note TEXT,
            attivo INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE,
            UNIQUE (struttura_id, ragione_sociale)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_manutentori_struttura ON manutentori(struttura_id)",
        "CREATE INDEX IF NOT EXISTS idx_manutentori_attivo ON manutentori(attivo)",
        """CREATE TABLE IF NOT EXISTS impianti (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            struttura_id INTEGER NOT NULL,
            divisione_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'altro' CHECK(tipo IN (
                'elettrico', 'idraulico', 'riscaldamento', 'climatizzazione',
                'antincendio', 'gas_medicali', 'ascensori', 'rete_dati', 'altro')),
            tipo_custom TEXT,
            descrizione TEXT,
            ubicazione TEXT,
            anno_installazione INTEGER,
            identificativo TEXT,
            stato TEXT NOT NULL DEFAULT 'attivo' CHECK(stato IN (
                'attivo', 'in_manutenzione', 'fuori_servizio', 'dismesso')),
            manutentore_id INTEGER,
            note TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER,
            updated_by INTEGER,
            FOREIGN KEY (struttura_id) REFERENCES strutture(id) ON DELETE CASCADE,
            FOREIGN KEY (divisione_id) REFERENCES divisioni(id) ON DELETE RESTRICT,
            FOREIGN KEY (manutentore_id) REFERENCES manutentori(id) ON DELETE SET NULL,
            FOREIGN KEY (created_by) REFERENCES utenti(id),
            FOREIGN KEY (updated_by) REFERENCES utenti(id),
            UNIQUE (struttura_id, nome)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_impianti_struttura ON impianti(struttura_id)",
        "CREATE INDEX IF NOT EXISTS idx_impianti_divisione ON impianti(divisione_id)",
        "CREATE INDEX IF NOT EXISTS idx_impianti_tipo ON impianti(tipo)",
        "CREATE INDEX IF NOT EXISTS idx_impianti_stato ON impianti(stato)",
        """CREATE TABLE IF NOT EXISTS impianti_componenti (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            impianto_id INTEGER NOT NULL,
            descrizione TEXT NOT NULL,
            marca TEXT,
            modello TEXT,
            matricola TEXT,
            ubicazione TEXT,
            note TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (impianto_id) REFERENCES impianti(id) ON DELETE CASCADE
        )""",
        "CREATE INDEX IF NOT EXISTS idx_impianti_componenti_impianto"
        " ON impianti_componenti(impianto_id)",
        """CREATE TABLE IF NOT EXISTS impianti_documenti (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            impianto_id INTEGER NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'altro' CHECK(tipo IN (
                'progetto', 'dichiarazione_conformita', 'collaudo', 'certificato',
                'libretto', 'planimetria', 'verbale', 'altro')),
            descrizione TEXT,
            data_documento DATE,
            emittente_ragione_sociale TEXT,
            emittente_indirizzo TEXT,
            emittente_telefono TEXT,
            emittente_email TEXT,
            filename TEXT NOT NULL,
            filepath TEXT NOT NULL,
            filesize INTEGER,
            uploaded_by INTEGER,
            uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (impianto_id) REFERENCES impianti(id) ON DELETE CASCADE,
            FOREIGN KEY (uploaded_by) REFERENCES utenti(id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_impianti_documenti_impianto"
        " ON impianti_documenti(impianto_id)",
        "CREATE INDEX IF NOT EXISTS idx_impianti_documenti_tipo"
        " ON impianti_documenti(tipo)",
        """CREATE TABLE IF NOT EXISTS impianti_scadenze (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            impianto_id INTEGER NOT NULL,
            componente_id INTEGER,
            nome TEXT NOT NULL,
            riferimento_normativo TEXT,
            periodicita_mesi INTEGER,
            prossima_scadenza DATE NOT NULL,
            giorni_anticipo INTEGER NOT NULL DEFAULT 30,
            email_extra TEXT,
            avvisa_manutentore INTEGER NOT NULL DEFAULT 1,
            attiva INTEGER NOT NULL DEFAULT 1,
            note TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (impianto_id) REFERENCES impianti(id) ON DELETE CASCADE,
            FOREIGN KEY (componente_id) REFERENCES impianti_componenti(id)
                ON DELETE SET NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_impianti_scadenze_impianto"
        " ON impianti_scadenze(impianto_id)",
        "CREATE INDEX IF NOT EXISTS idx_impianti_scadenze_prossima"
        " ON impianti_scadenze(prossima_scadenza)",
        "CREATE INDEX IF NOT EXISTS idx_impianti_scadenze_attiva"
        " ON impianti_scadenze(attiva)",
        """CREATE TABLE IF NOT EXISTS impianti_interventi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            impianto_id INTEGER NOT NULL,
            scadenza_id INTEGER,
            componente_id INTEGER,
            tipo TEXT NOT NULL DEFAULT 'ordinaria' CHECK(tipo IN (
                'verifica', 'ordinaria', 'straordinaria', 'riparazione')),
            data_intervento DATE NOT NULL,
            esito TEXT CHECK(esito IN ('positivo', 'negativo', 'con_riserva')),
            manutentore_id INTEGER,
            tecnico_ditta TEXT,
            descrizione TEXT,
            costo REAL,
            verbale_path TEXT,
            note TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER,
            FOREIGN KEY (impianto_id) REFERENCES impianti(id) ON DELETE CASCADE,
            FOREIGN KEY (scadenza_id) REFERENCES impianti_scadenze(id)
                ON DELETE SET NULL,
            FOREIGN KEY (componente_id) REFERENCES impianti_componenti(id)
                ON DELETE SET NULL,
            FOREIGN KEY (manutentore_id) REFERENCES manutentori(id)
                ON DELETE SET NULL,
            FOREIGN KEY (created_by) REFERENCES utenti(id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_impianti_interventi_impianto"
        " ON impianti_interventi(impianto_id)",
        "CREATE INDEX IF NOT EXISTS idx_impianti_interventi_scadenza"
        " ON impianti_interventi(scadenza_id)",
        "CREATE INDEX IF NOT EXISTS idx_impianti_interventi_data"
        " ON impianti_interventi(data_intervento)",
        """CREATE TABLE IF NOT EXISTS impianti_avvisi_inviati (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scadenza_id INTEGER NOT NULL,
            soglia TEXT NOT NULL,
            scadenza_target DATE NOT NULL,
            destinatari TEXT,
            inviato_il DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (scadenza_id) REFERENCES impianti_scadenze(id)
                ON DELETE CASCADE,
            UNIQUE (scadenza_id, soglia, scadenza_target)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_impianti_avvisi_scadenza"
        " ON impianti_avvisi_inviati(scadenza_id)",
        # La vista si ricrea a ogni avvio: cambiarne il corpo in un rilascio
        # successivo non richiede altro che modificare queste righe.
        "DROP VIEW IF EXISTS prossime_scadenze_impianti",
        """CREATE VIEW prossime_scadenze_impianti AS
        SELECT
          i.id AS impianto_id, i.struttura_id, i.divisione_id,
          i.nome AS impianto_nome, i.tipo, i.tipo_custom, i.ubicazione,
          s.id AS scadenza_id, s.nome AS scadenza_nome, s.riferimento_normativo,
          s.periodicita_mesi, s.giorni_anticipo,
          c.descrizione AS componente_descrizione,
          s.prossima_scadenza,
          CAST((julianday(s.prossima_scadenza) - julianday('now')) AS INTEGER)
              AS giorni_rimasti,
          CASE
            WHEN julianday(s.prossima_scadenza) - julianday('now') < 0  THEN 'scaduto'
            WHEN julianday(s.prossima_scadenza) - julianday('now') <= 7  THEN 'urgente'
            WHEN julianday(s.prossima_scadenza) - julianday('now') <= 15 THEN 'attenzione'
            WHEN julianday(s.prossima_scadenza) - julianday('now') <= 30 THEN 'avviso'
            ELSE 'ok'
          END AS priorita
        FROM impianti i
        INNER JOIN impianti_scadenze s ON i.id = s.impianto_id
        LEFT JOIN impianti_componenti c ON c.id = s.componente_id
        WHERE i.stato != 'dismesso'
          AND s.attiva = 1
        ORDER BY s.prossima_scadenza ASC""",
]
