"""Adegua config.json e config.local.json alla disposizione del container.

Nel container lo stato vive sotto MEDINVENTORY_RADICE (/stato) con una
disposizione fissa: data/database.sqlite, uploads, backups. Una configurazione
portata da un'altra installazione può nominare percorsi che lì non esistono:
riscriverli farebbe partire l'app su un database vuoto, quindi si rifiuta.

Solo libreria standard, niente Flask: gira prima che l'app sia configurata.

    python3 /app/deploy/podman/config_container.py adegua [--radice DIR] [--prova]
"""
import argparse
import copy
import json
import os
import re
import shutil
import sys

# Lo stesso nome di percorsi.VARIABILE (un test verifica che coincidano).
VARIABILE = 'MEDINVENTORY_RADICE'

# Vivono in config.json; config.local.json, se le contiene, vince (load_config).
PERCORSI_ATTESI = {
    'database_path': 'data/database.sqlite',
    'uploads_path': 'uploads',
    'backups_path': 'backups',
}

# Vivono in config.local.json (LOCAL_CONFIG_KEYS in app.py).
VALORI_FORZATI = {
    'host': '127.0.0.1',
    'port': 5000,
    'cloudflare_mode': True,
    'force_https': True,
}

FILE_BASE = 'config.json'
FILE_LOCALE = 'config.local.json'

_UNITA_WINDOWS = re.compile(r'^[A-Za-z]:')
_ASSENTE = object()


class ConfigNonPortabile(ValueError):
    """Un percorso della configurazione non può valere dentro il container."""


def _percorso_canonico(chiave, valore, origine):
    """Restituisce il percorso atteso, o solleva se `valore` non gli equivale."""
    atteso = PERCORSI_ATTESI[chiave]
    dove = f'{origine}: {chiave} = {valore!r}'
    if not isinstance(valore, str) or not valore.strip():
        raise ConfigNonPortabile(f'{dove}: serve un percorso relativo, {atteso!r}')
    grezzo = valore.strip()
    # Su Linux os.path.isabs non riconosce C:\... né \\server\...: controllo esplicito.
    if _UNITA_WINDOWS.match(grezzo) or grezzo.startswith('\\'):
        raise ConfigNonPortabile(
            f'{dove}: percorso assoluto di Windows. Nel container deve essere '
            f'{atteso!r}: spostare lì i dati prima di importare')
    if grezzo.startswith('/'):
        raise ConfigNonPortabile(
            f'{dove}: percorso assoluto. Nel container deve essere {atteso!r}: '
            f'spostare lì i dati prima di importare')
    parti = [p for p in grezzo.replace('\\', '/').split('/') if p not in ('', '.')]
    if '..' in parti:
        raise ConfigNonPortabile(
            f'{dove}: contiene "..", esce dalla radice dello stato. '
            f'Nel container deve essere {atteso!r}')
    if '/'.join(parti) != atteso:
        raise ConfigNonPortabile(
            f'{dove}: fuori dalla disposizione del container, che prevede '
            f'{atteso!r}. Spostare lì i dati prima di importare')
    return atteso


def _imposta(dati, chiave, valore, origine, modifiche):
    attuale = dati.get(chiave, _ASSENTE)
    if attuale is not _ASSENTE and type(attuale) is type(valore) and attuale == valore:
        return
    dati[chiave] = valore
    if attuale is _ASSENTE:
        modifiche.append(f'{origine}: {chiave} mancante, impostato a {valore!r}')
    else:
        modifiche.append(f'{origine}: {chiave} {attuale!r} -> {valore!r}')


def adegua(config, locale):
    """Adegua le due configurazioni al container.

    Restituisce (config, locale, modifiche): copie adeguate e l'elenco leggibile
    di ciò che è cambiato. Gli argomenti non vengono modificati. Solleva
    ConfigNonPortabile se un percorso non è quello della disposizione attesa.
    """
    nuovo = copy.deepcopy(config)
    nuovo_locale = copy.deepcopy(locale)
    modifiche = []

    for chiave, atteso in PERCORSI_ATTESI.items():
        if chiave in nuovo_locale:
            canonico = _percorso_canonico(chiave, nuovo_locale[chiave], FILE_LOCALE)
            _imposta(nuovo_locale, chiave, canonico, FILE_LOCALE, modifiche)
        if chiave in nuovo:
            canonico = _percorso_canonico(chiave, nuovo[chiave], FILE_BASE)
            _imposta(nuovo, chiave, canonico, FILE_BASE, modifiche)
        else:
            _imposta(nuovo, chiave, atteso, FILE_BASE, modifiche)

    for chiave, valore in VALORI_FORZATI.items():
        _imposta(nuovo_locale, chiave, valore, FILE_LOCALE, modifiche)

    return nuovo, nuovo_locale, modifiche


def _leggi(percorso):
    if not os.path.isfile(percorso):
        raise ValueError(
            f'{percorso}: file non trovato. Niente da adeguare: i due file '
            f'{FILE_BASE} e {FILE_LOCALE} devono esistere entrambi')
    with open(percorso, 'r', encoding='utf-8') as f:
        dati = json.load(f)
    if not isinstance(dati, dict):
        raise ValueError(f'{percorso}: il contenuto non è un oggetto JSON')
    return dati


def _prepara(percorso, dati):
    """Scrive accanto all'originale un file provvisorio con gli stessi permessi."""
    provvisorio = percorso + '.tmp'
    with open(provvisorio, 'w', encoding='utf-8') as f:
        json.dump(dati, f, indent=2, ensure_ascii=False)
    shutil.copymode(percorso, provvisorio)
    return provvisorio


def comando_adegua(radice, prova):
    percorso_base = os.path.join(radice, FILE_BASE)
    percorso_locale = os.path.join(radice, FILE_LOCALE)
    config = _leggi(percorso_base)
    locale = _leggi(percorso_locale)

    # Solleva qui, prima di qualunque scrittura.
    nuovo, nuovo_locale, modifiche = adegua(config, locale)

    if not modifiche:
        print('Configurazione adatta al container: nessuna modifica.')
        return 0
    for riga in modifiche:
        print(riga)
    if prova:
        print('Prova: nessun file scritto.')
        return 0

    da_scrivere = []
    if nuovo != config:
        da_scrivere.append((percorso_base, nuovo))
    if nuovo_locale != locale:
        da_scrivere.append((percorso_locale, nuovo_locale))
    # Prima tutti i provvisori, poi le sostituzioni: un errore di scrittura
    # lascia intatti entrambi gli originali.
    provvisori = []
    try:
        for percorso, dati in da_scrivere:
            provvisori.append((_prepara(percorso, dati), percorso))
        for provvisorio, percorso in provvisori:
            os.replace(provvisorio, percorso)
    finally:
        for provvisorio, _ in provvisori:
            if os.path.exists(provvisorio):
                os.remove(provvisorio)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Adegua la configurazione di Custode alla disposizione del container.')
    comandi = parser.add_subparsers(dest='comando', required=True)
    p = comandi.add_parser('adegua', help='controlla e riscrive config.json e config.local.json')
    p.add_argument('--radice', metavar='DIR',
                   help=f'cartella dei due file (predefinita: {VARIABILE})')
    p.add_argument('--prova', action='store_true', help='mostra le modifiche senza scrivere')
    args = parser.parse_args(argv)

    radice = args.radice or os.environ.get(VARIABILE)
    try:
        if not radice:
            raise ValueError(f'radice non indicata: usare --radice o impostare {VARIABILE}')
        return comando_adegua(radice, args.prova)
    except (OSError, ValueError) as errore:
        # ConfigNonPortabile e gli errori di JSON sono ValueError.
        print(f'config_container: {errore}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
