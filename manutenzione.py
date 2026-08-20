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


def conferma(domanda):
    try:
        risposta = input(f'{domanda} [s/N] ').strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return False
    return risposta in ('s', 'si', 'sì', 'y', 'yes')


def conferma_distruttiva(parola):
    print(f'  Per procedere digita esattamente: {parola}')
    try:
        return input('  > ').strip() == parola
    except (KeyboardInterrupt, EOFError):
        print()
        return False


def _contesto(args):
    """(conn, config, percorso_db), con conn None se il database non c'e'.

    Non solleva: l'assenza del database e' un esito da spiegare, non un
    traceback da mostrare all'operatore.
    """
    percorso_db = operazioni.percorso_database(args.db)
    config = operazioni.carica_config()
    try:
        conn = operazioni.apri(percorso_db)
    except FileNotFoundError:
        print(tui.riga_esito('errore', f'Database non trovato: {percorso_db}'))
        print('  Per una nuova installazione: python seed.py')
        return None, config, percorso_db
    return conn, config, percorso_db


# ---------------------------------------------------------------------------
# Presentazione
# ---------------------------------------------------------------------------

def stampa_stato(fotografia):
    print()
    print(tui.titolo('Stato installazione'))
    db = fotografia['database']
    if db.get('disponibile'):
        print(tui.campo('Database', f"{db['percorso']}  "
                                    f"{db['dimensione_byte'] / (1024 * 1024):.2f} MB  "
                                    f"{db['integrity_check']}"))
    schema = fotografia['schema']
    if schema.get('disponibile'):
        pendenti = (f"{len(schema['pendenti'])} pendenti" if schema['pendenti']
                    else 'aggiornato')
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
                                  + (f' ({ruoli})' if ruoli else '')
                                  + f", {ut['disattivati']} disattivati"
                                    f", {ut['cancellati']} cancellati"))
    dati = fotografia['dati']
    if dati.get('disponibile'):
        print(tui.campo('Dati', ', '.join(
            f'{n} {t}' for t, n in dati.items() if t != 'disponibile')))
    else:
        # Una sezione taciuta si legge come "zero", che e' falso. Su uno
        # schema vecchio il motivo e' proprio l'informazione che serve.
        print(tui.campo('Dati', f"non disponibile: {dati.get('motivo')}"))
    up = fotografia['uploads']
    if up.get('disponibile'):
        orfani = '' if up.get('orfani') is None else f", {up['orfani']} orfani"
        print(tui.campo('Uploads', f"{up['file']} file, "
                                   f"{up['byte'] / (1024 * 1024):.1f} MB{orfani}"))
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


# ---------------------------------------------------------------------------
# Comandi
# ---------------------------------------------------------------------------

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
        return 0 if operazioni.applica_migrazioni(
            conn, percorso_db, config, pendenti) else 1
    finally:
        conn.close()


def comando_utenti(args):
    conn, _config, percorso_db = _contesto(args)
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
            except (mutenti.UtenteInesistente, mutenti.PasswordDebole) as e:
                print(tui.riga_esito('errore', str(e)))
                return 1
            conn.commit()
            print(tui.riga_esito('ok', f'Password aggiornata per {args.email}. '
                                       f"L'account e' attivo."))
            return 0

        if args.azione == 'superadmin':
            return _superadmin(conn)

        if args.azione == 'azzera':
            return _azzera(conn, args, percorso_db)
    finally:
        conn.close()
    return 2


def _superadmin(conn):
    esistente = conn.execute(
        "SELECT email FROM utenti WHERE ruolo = 'superadmin' "
        "AND eliminato_il IS NULL").fetchone()
    if esistente:
        print(f"Superadmin esistente: {esistente['email']}")
        if not conferma('Reimpostarne la password?'):
            return 0
        mutenti.imposta_password(conn, esistente['email'],
                                 chiedi_password(esistente['email']))
        conn.commit()
        print(tui.riga_esito('ok', 'Password superadmin aggiornata.'))
        return 0

    try:
        email = input('Email superadmin [superadmin@medinventory.local]: ').strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return 1
    email = email or 'superadmin@medinventory.local'
    try:
        mutenti.crea_accesso(conn, email, chiedi_password(email), 'superadmin')
    except (mutenti.EmailGiaInUso, mutenti.PasswordDebole) as e:
        print(tui.riga_esito('errore', str(e)))
        return 1
    conn.commit()
    print(tui.riga_esito('ok', f'Superadmin creato: {email}'))
    return 0


def _azzera(conn, args, percorso_db):
    from utente_service import conteggi_riferimenti

    bersagli = mutenti.elenco(conn, args.struttura)
    vivi = [r for r in bersagli if r['eliminato_il'] is None]
    if not vivi:
        print(tui.riga_esito('ok', 'Nessun utente da azzerare.'))
        return 0

    print()
    print(tui.titolo('Utenti che verranno azzerati'))
    print(tui.tabella(
        ['email', 'ruolo', 'righe che lo citano'],
        [[r['email'], r['ruolo'],
          sum(conteggi_riferimenti(conn, r['id']).values())] for r in vivi]))
    semantica = ("DEFINITIVO (righe rimosse, tracciabilita' persa)"
                 if args.definitivo
                 else "conservativo (righe storiche, tracciabilita' intatta)")
    print(f'  Semantica: {semantica}')

    rimpiazzo = None
    if args.nuovo_admin:
        rimpiazzo = mutenti.Rimpiazzo(
            email=args.nuovo_admin, password=chiedi_password(args.nuovo_admin),
            ruolo='admin' if args.struttura else 'superadmin',
            struttura_id=args.struttura)

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
    except Exception as e:
        conn.rollback()
        print(tui.riga_esito('errore', str(e)))
        print(f"  Nulla e' stato modificato. Backup conservato: {copia}")
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
                                   f'{byte_totali / (1024 * 1024):.1f} MB'))
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
        print(tui.riga_esito('ok', f"Gia' in modalita' {nome}."))
        return 0
    operazioni.imposta_modalita(voluta)
    print(tui.riga_esito('ok', "Modalita' impostata a "
                         + ('single-struttura' if voluta else 'multi-struttura')))
    print("  Riavvia l'applicazione perche' abbia effetto.")
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


COMANDI = {
    'stato': comando_stato,
    'diagnosi': comando_diagnosi,
    'migra': comando_migra,
    'utenti': comando_utenti,
    'uploads': comando_uploads,
    'modalita': comando_modalita,
    'backup': comando_backup,
}


def costruisci_parser():
    p = argparse.ArgumentParser(
        prog='manutenzione.py',
        description='Strumento unificato di manutenzione MedInventory.')
    p.add_argument('--db', metavar='PERCORSO',
                   help='database su cui operare (predefinito: quello di config)')
    sub = p.add_subparsers(dest='comando')

    ps = sub.add_parser('stato', help="fotografia dell'installazione")
    ps.add_argument('--json', action='store_true', help='emette il dizionario grezzo')

    sub.add_parser('diagnosi', help='controlli; esce con 1 se ci sono errori')

    pm = sub.add_parser('migra', help='migrazioni dello schema')
    pm.add_argument('--check', action='store_true', help='solo analisi')
    pm.add_argument('-y', '--yes', action='store_true', help='senza conferma')

    pu = sub.add_parser('utenti', help='account e accessi')
    pu.add_argument('azione', choices=['elenca', 'azzera', 'password', 'superadmin'])
    pu.add_argument('email', nargs='?', help="per l'azione password")
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
