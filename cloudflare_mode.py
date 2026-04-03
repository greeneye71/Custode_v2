"""
cloudflare_mode.py — Attiva, disattiva e interroga la modalità Cloudflare Tunnel.

Uso:
    python cloudflare_mode.py             # mostra stato attuale e chiede conferma
    python cloudflare_mode.py --status    # mostra solo lo stato senza modificare
    python cloudflare_mode.py --on        # attiva la modalità Cloudflare
    python cloudflare_mode.py --off       # disattiva la modalità Cloudflare

In modalità Cloudflare attiva vengono impostati:
    - cloudflare_mode: true   → Waitress ascolta su 127.0.0.1 (default)
    - force_https: true       → cookie Secure, HSTS, redirect HTTP→HTTPS
    - host: "127.0.0.1"       → blocca connessioni dirette che bypassano il tunnel

In modalità Cloudflare disattiva vengono ripristinati:
    - cloudflare_mode: false
    - force_https: false
    - host: "0.0.0.0"         → accessibile su tutta la LAN
"""

import json
import os
import argparse

CONFIG_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.local.json')
CONFIG_EXAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.local.example.json')


def leggi_config():
    if os.path.exists(CONFIG_LOCAL):
        with open(CONFIG_LOCAL, 'r', encoding='utf-8') as f:
            return json.load(f)
    # Inizializza da esempio se il file locale non esiste ancora
    if os.path.exists(CONFIG_EXAMPLE):
        with open(CONFIG_EXAMPLE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def scrivi_config(config):
    with open(CONFIG_LOCAL, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def cloudflare_attivo(config):
    return bool(config.get('cloudflare_mode', False))


def descrivi_stato(attivo, config):
    host = config.get('host', '127.0.0.1' if attivo else '0.0.0.0')
    force_https = config.get('force_https', attivo)

    if attivo:
        return (
            "CLOUDFLARE TUNNEL ATTIVO\n"
            f"  - host            : {host}\n"
            f"  - force_https     : {force_https}\n"
            "  - Cookie Secure   : sì\n"
            "  - HSTS            : sì (12 mesi)\n"
            "  - Redirect HTTP→HTTPS: sì (via X-Forwarded-Proto)\n"
            "  - Accesso diretto : bloccato (solo tunnel)"
        )
    else:
        return (
            "CLOUDFLARE TUNNEL DISATTIVO  (modalità LAN locale)\n"
            f"  - host            : {host}\n"
            f"  - force_https     : {force_https}\n"
            "  - Cookie Secure   : no\n"
            "  - HSTS            : no\n"
            "  - Accesso diretto : sì (tutta la LAN)"
        )


def attiva(config):
    config['cloudflare_mode'] = True
    config['force_https'] = True
    # Imposta host a 127.0.0.1 solo se è ancora il default LAN (0.0.0.0)
    # o non è impostato. Non sovrascrive una scelta esplicita dell'utente.
    if config.get('host', '0.0.0.0') == '0.0.0.0':
        config['host'] = '127.0.0.1'


def disattiva(config):
    config['cloudflare_mode'] = False
    config['force_https'] = False
    # Ripristina l'ascolto su tutta la LAN solo se era su localhost
    if config.get('host', '127.0.0.1') == '127.0.0.1':
        config['host'] = '0.0.0.0'


def main():
    parser = argparse.ArgumentParser(
        description='Gestisce la modalità Cloudflare Tunnel di MedInventory'
    )
    gruppo = parser.add_mutually_exclusive_group()
    gruppo.add_argument('--status', action='store_true',
                        help='Mostra solo lo stato attuale senza modificare nulla')
    gruppo.add_argument('--on',  action='store_true', help='Attiva la modalità Cloudflare')
    gruppo.add_argument('--off', action='store_true', help='Disattiva la modalità Cloudflare')
    args = parser.parse_args()

    config = leggi_config()
    corrente = cloudflare_attivo(config)

    print("=" * 55)
    print("  MedInventory — Gestione modalità Cloudflare Tunnel")
    print("=" * 55)
    print(f"\nStato attuale:\n  {descrivi_stato(corrente, config)}\n")

    if args.status:
        return

    # Determina il nuovo stato
    if args.on:
        nuovo = True
    elif args.off:
        nuovo = False
    else:
        # Modalità interattiva: toggling
        nuovo = not corrente
        print(f"Nuovo stato:\n  {descrivi_stato(nuovo, config)}\n")
        risposta = input("Confermi il cambio? [s/N] ").strip().lower()
        if risposta not in ('s', 'si', 'sì', 'y', 'yes'):
            print("Operazione annullata.")
            return

    if nuovo == corrente:
        print("Nessuna modifica — la modalità è già quella richiesta.")
        return

    if not os.path.exists(CONFIG_LOCAL):
        print("AVVISO: config.local.json non trovato — verrà creato.")

    if nuovo:
        attiva(config)
    else:
        disattiva(config)

    scrivi_config(config)

    print(f"\n✓ Modalità impostata:\n  {descrivi_stato(nuovo, config)}")

    print("\nRiavvia l'applicazione per applicare le modifiche:")
    print("  python run_production.py")

    if nuovo:
        print("\nPassaggi successivi consigliati:")
        print("  1. Assicurati che cloudflared sia configurato e in esecuzione")
        print("  2. In Cloudflare Dashboard → SSL/TLS → imposta 'Full' o 'Full (strict)'")
        print("  3. In Cloudflare Dashboard → SSL/TLS → abilita 'Always Use HTTPS'")
        print("  4. Verifica la guida: CLOUDFLARE_TUNNEL.md")
    else:
        print("\nL'applicazione tornerà accessibile su tutta la LAN locale.")


if __name__ == '__main__':
    main()
