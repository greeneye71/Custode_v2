"""
crea_superadmin.py - Crea o reimposta il superadmin di MedInventory.

Dalla 2.6.3 la logica vive in manutenzione_lib/utenti.py e questo script la
richiama: due implementazioni della stessa operazione divergono, e questa era
gia' l'unica delle due a validare la password.

Uso:
    python crea_superadmin.py
    python manutenzione.py utenti superadmin    (equivalente)
"""
import os
import sys

# Su Windows la console non è UTF-8: senza questo, stampare accenti o
# caratteri di riquadro fa fallire lo script con UnicodeEncodeError
# (succede appena l'output viene rediretto su file o log).
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from manutenzione_lib.utenti import valida_password  # noqa: F401,E402 (riesportata)


def _esegui(argv):
    import manutenzione
    return manutenzione.main(argv)


def main():
    print("=" * 55)
    print("  MedInventory — Creazione superadmin")
    print("=" * 55)
    return _esegui(['utenti', 'superadmin'])


if __name__ == '__main__':
    sys.exit(main())
