"""Il menu interattivo.

Unico posto della manutenzione che chiama input() per scegliere. Non duplica
nessuna logica: costruisce gli stessi oggetti args che argparse produrrebbe e
chiama gli stessi comandi, cosi' la porta interattiva e quella scriptabile
non possono divergere.
"""
import argparse

from manutenzione_lib import diagnosi, operazioni, stato, tui

# (tasto, etichetta, comando, argomenti del comando)
VOCI = (
    ('1', 'Migrazioni schema', 'migra',
     {'check': False, 'yes': False}),
    ('2', 'Utenti e accessi', 'utenti',
     {'azione': 'elenca', 'email': None, 'struttura': None,
      'definitivo': False, 'nuovo_admin': None, 'yes': False}),
    ('3', 'Reimposta una password', 'utenti',
     {'azione': 'password', 'email': None, 'struttura': None,
      'definitivo': False, 'nuovo_admin': None, 'yes': False}),
    ('4', 'Pulizia uploads', 'uploads',
     {'elimina': False, 'yes': False}),
    ('5', "Modalita' single/multi", 'modalita',
     {'single': False, 'multi': False}),
    ('6', 'Backup', 'backup',
     {'crea': False, 'elenca': True, 'ripristina': None}),
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
