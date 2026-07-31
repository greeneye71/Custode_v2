"""
toggle_modalita.py — Cambia la modalità di MedInventory tra single-struttura e multi-struttura.

Uso:
    python toggle_modalita.py            # mostra stato attuale e chiede conferma
    python toggle_modalita.py --status   # mostra solo lo stato senza modificare
    python toggle_modalita.py --single   # forza modalità single-struttura
    python toggle_modalita.py --multi    # forza modalità multi-struttura
"""

import json
import os
import sys
import argparse

# Su Windows la console non è UTF-8: senza questo, stampare accenti o
# caratteri di riquadro fa fallire lo script con UnicodeEncodeError
# (succede appena l'output viene rediretto su file o log).
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

CONFIG_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.local.json')
CONFIG_EXAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.local.example.json')


def leggi_config():
    if not os.path.exists(CONFIG_LOCAL):
        return {}
    with open(CONFIG_LOCAL, 'r', encoding='utf-8') as f:
        return json.load(f)


def scrivi_config(config):
    with open(CONFIG_LOCAL, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def stato_attuale(config):
    # Il resto del progetto (models.py, app.py) usa False come default per
    # 'single_struttura' quando la chiave manca: qui era True, quindi con la
    # chiave assente lo strumento dichiarava "single" mentre l'applicazione
    # si comportava gia' da multi. Proprio l'area del Critico 2: un
    # disallineamento del genere confonde l'operatore su quale schema di
    # allegati la sua installazione stia davvero usando.
    return config.get('single_struttura', False)


def descrivi_stato(is_single):
    if is_single:
        return (
            "SINGLE-STRUTTURA  (modalità v1.x compatibile)\n"
            "  - Menu 'Strutture' nascosto\n"
            "  - Nessun superadmin necessario\n"
            "  - Modalità avanzata forzata"
        )
    else:
        return (
            "MULTI-STRUTTURA  (modalità v2)\n"
            "  - Menu 'Strutture' visibile al superadmin\n"
            "  - Superadmin può gestire più strutture\n"
            "  - Ogni struttura ha config indipendente"
        )


def main():
    parser = argparse.ArgumentParser(
        description='Cambia modalità MedInventory: single-struttura ↔ multi-struttura'
    )
    gruppo = parser.add_mutually_exclusive_group()
    gruppo.add_argument('--status', action='store_true', help='Mostra solo lo stato attuale')
    gruppo.add_argument('--single', action='store_true', help='Forza modalità single-struttura')
    gruppo.add_argument('--multi', action='store_true', help='Forza modalità multi-struttura')
    args = parser.parse_args()

    config = leggi_config()

    if not os.path.exists(CONFIG_LOCAL):
        print("AVVISO: config.local.json non trovato — verrà creato.")
        # Inizializza da esempio se disponibile
        if os.path.exists(CONFIG_EXAMPLE):
            with open(CONFIG_EXAMPLE, 'r', encoding='utf-8') as f:
                config = json.load(f)
        else:
            config = {}

    corrente = stato_attuale(config)

    print("=" * 55)
    print("  MedInventory — Gestione modalità operativa")
    print("=" * 55)
    print(f"\nStato attuale:  {descrivi_stato(corrente)}\n")

    # Solo status
    if args.status:
        return

    # Determina il nuovo stato
    if args.single:
        nuovo = True
    elif args.multi:
        nuovo = False
    else:
        # Modalità interattiva: toggling
        nuovo = not corrente
        print(f"Nuovo stato:    {descrivi_stato(nuovo)}\n")
        risposta = input("Confermi il cambio? [s/N] ").strip().lower()
        if risposta not in ('s', 'si', 'sì', 'y', 'yes'):
            print("Operazione annullata.")
            return

    if nuovo == corrente:
        print(f"Nessuna modifica — la modalità è già quella richiesta.")
        return

    config['single_struttura'] = nuovo
    scrivi_config(config)

    print(f"\n✓ Modalità impostata: {descrivi_stato(nuovo)}")
    print("\nRiavvia l'applicazione per applicare le modifiche:")
    print("  python app.py           (sviluppo)")
    print("  python run_production.py  (produzione)")

    if not nuovo:
        # Passaggio a multi-struttura: avvisa se manca il superadmin
        _avvisa_superadmin()


def _avvisa_superadmin():
    """Verifica se esiste un superadmin nel database e avvisa se mancante."""
    try:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from app import create_app
        from models import query_one

        app = create_app()
        with app.app_context():
            sa = query_one("SELECT id, email FROM utenti WHERE ruolo = 'superadmin' AND attivo = 1")
            if sa:
                print(f"\nSuperadmin trovato: {sa['email']}")
            else:
                print("\nATTENZIONE: Nessun superadmin trovato nel database.")
                print("Per crearne uno esegui:")
                print("  python crea_superadmin.py")
    except Exception:
        # Non critico: se l'import fallisce non blocchiamo il toggle
        pass


if __name__ == '__main__':
    main()
