"""Il DDL degli impianti esiste in due posti: devono restare uguali.

`schema.sql` crea un database nuovo, `schema_impianti.DDL_IMPIANTI` aggiorna
uno esistente (da `models.apply_schema_updates()` e da `migrate.py`). Se le due
copie divergono, un'installazione nuova e una migrata si comportano in modo
diverso — con la vista `prossime_scadenze_impianti` la divergenza e' invisibile
finche' qualcuno non guarda lo scadenzario.
"""
import os
import re
import sqlite3
import sys

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)

from schema_impianti import DDL_IMPIANTI, TABELLE_IMPIANTI  # noqa: E402


def _normalizza(sql):
    """Il testo del DDL senza le differenze che SQLite ignora."""
    return re.sub(r'\s+', ' ', sql or '').strip()


def _oggetti_impianti(conn):
    """Nome -> DDL normalizzato, per tutto cio' che riguarda gli impianti."""
    righe = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
    ).fetchall()
    return {
        nome: _normalizza(sql) for nome, sql in righe
        if 'impianti' in nome or 'manutentori' in nome
    }


def _da_schema_sql():
    conn = sqlite3.connect(':memory:')
    with open(os.path.join(RADICE, 'schema.sql'), encoding='utf-8') as f:
        conn.executescript(f.read())
    return _oggetti_impianti(conn)


def _da_modulo():
    conn = sqlite3.connect(':memory:')
    for istruzione in DDL_IMPIANTI:
        try:
            conn.execute(istruzione)
        except sqlite3.OperationalError:
            # Gli ALTER TABLE ADD COLUMN di colonne gia' presenti: entrambi i
            # chiamanti reali li ignorano allo stesso modo.
            pass
    return _oggetti_impianti(conn)


def test_tutte_le_tabelle_dichiarate_vengono_create():
    creati = _da_modulo()
    for tabella in TABELLE_IMPIANTI:
        assert tabella in creati, f"DDL_IMPIANTI non crea {tabella}"


def test_schema_sql_e_modulo_creano_gli_stessi_oggetti():
    da_file = _da_schema_sql()
    da_modulo = _da_modulo()
    assert set(da_file) == set(da_modulo), (
        "Oggetti solo in schema.sql: %s | solo in schema_impianti.py: %s" % (
            sorted(set(da_file) - set(da_modulo)),
            sorted(set(da_modulo) - set(da_file)),
        )
    )


def test_il_ddl_dei_singoli_oggetti_coincide():
    da_file = _da_schema_sql()
    da_modulo = _da_modulo()
    for nome in sorted(set(da_file) & set(da_modulo)):
        assert da_file[nome] == da_modulo[nome], (
            f"{nome} differisce fra schema.sql e schema_impianti.py:\n"
            f"  schema.sql:         {da_file[nome]}\n"
            f"  schema_impianti.py: {da_modulo[nome]}"
        )
