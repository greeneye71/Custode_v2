# manutenzione.py — strumento unificato di manutenzione a riga di comando

**Versione di rilascio:** 2.6.3
**Data:** 2026-08-20

## Il problema

Le operazioni di manutenzione di un'installazione MedInventory sono sparse su
quattro script che non si parlano: `migrate.py` (schema), `toggle_modalita.py`
(single/multi), `pulisci_uploads.py` (file orfani), `crea_superadmin.py`
(accesso globale). Ognuno chiede a modo suo dov'e' il database, stampa a modo
suo, e nessuno risponde alla domanda che si fa per prima davanti a
un'installazione altrui o migrata: **com'e' messa questa installazione, e cosa
non va?**

Manca inoltre un'operazione: azzerare gli utenti conservando tutto il resto —
apparecchi, manutenzioni, verifiche, documenti. Serve quando un'installazione
cambia mani, o quando l'elenco utenti arrivato da una migrazione e' inservibile.

Il caso concreto che ha originato il lavoro: un'installazione migrata da una
versione vecchia rifiuta il login con "credenziali non valide", e non c'e' modo
di sapere perche' senza aprire il database a mano.

## Cosa costruiamo

Un entry point `manutenzione.py` con due porte:

- **senza argomenti** — rapporto di stato, diagnosi dei problemi, poi un menu
  testuale per agire;
- **con subcomandi** — non interattivo, scriptabile, adatto ai `.bat` di
  installazione e ai test.

Le due porte chiamano le stesse funzioni. Nessuna dipendenza nuova: la resa
grafica e' ANSI di libreria standard, che degrada a testo puro quando l'output
non e' un terminale.

### Non fa parte di questo lavoro

- Interfaccia web equivalente: l'applicazione ha gia' *Amministrazione*.
- Riscrittura delle migrazioni: `migrate.py` resta l'autorita' sullo schema.
- Cancellazione di strutture o di singoli apparecchi: esiste in
  `struttura_service.py` e nell'interfaccia.
- Modifica del flusso di login: la diagnosi lo osserva, non lo cambia.

## Forma

```
manutenzione.py            entry point: argparse + menu
manutenzione/
    __init__.py
    tui.py                 colori, tabelle, prompt, conferme distruttive
    stato.py               raccolta parametri, nessun giudizio
    diagnosi.py            controlli, nessuna stampa
    utenti.py              elenco, azzeramento, superadmin, reset password
    operazioni.py          adattatori verso migrate/uploads/modalita/backup
    menu.py                menu interattivo
```

Regola di separazione, verificabile leggendo gli import:

- `tui.py` non importa nulla del dominio;
- `stato.py` e `diagnosi.py` non stampano;
- `utenti.py` riceve una `sqlite3.Connection` e non apre transazioni proprie,
  come `utente_service.py` e `struttura_service.py`;
- `menu.py` e' l'unico che chiama `input()`.

### Gli script esistenti restano

`migrate.py`, `toggle_modalita.py` e `pulisci_uploads.py` hanno gia' la logica
in funzioni importabili. **Non vengono toccati**: `manutenzione/operazioni.py`
li importa. Questo tiene verdi `test_migrazioni.py`, `test_toggle_modalita.py`
e `test_pulisci_uploads.py` senza riscritture, e lascia funzionanti i comandi
gia' documentati.

Riuso puntuale:

| Da dove | Cosa |
|---|---|
| `migrate.load_db_path`, `load_config` | risoluzione del percorso database |
| `migrate.analyze`, `apply_all`, `MIGRATIONS` | stato e applicazione migrazioni |
| `pulisci_uploads.percorsi_referenziati`, `trova_orfani`, `elimina_file` | file orfani |
| `toggle_modalita.stato_attuale`, `scrivi_config` | modalita' single/multi |
| `backup_service.create_backup`, `list_backups`, `restore_backup` | backup |
| `utente_service.cancella_utente`, `conteggi_riferimenti` | azzeramento conservativo |
| `struttura_service._rimuovi_utenti`, `RIFERIMENTI_UTENTE` | azzeramento definitivo |

`crea_superadmin.py` e' l'unica eccezione: la sua logica sta dentro `main()` ed
e' legata a `create_app()`, che non si puo' usare per operare su un database
arbitrario indicato con `--db`. La logica si sposta in
`manutenzione/utenti.py` come funzioni su una connessione; `crea_superadmin.py`
diventa un chiamante sottile che conserva la sua interfaccia attuale.

## Superficie a riga di comando

```
python manutenzione.py                        stato + diagnosi + menu
python manutenzione.py stato [--json]         solo rapporto
python manutenzione.py diagnosi               solo controlli; exit 1 se problemi
python manutenzione.py migra [--check] [-y]
python manutenzione.py utenti elenca
python manutenzione.py utenti azzera [--struttura ID] [--definitivo] [-y]
                                     [--nuovo-admin EMAIL]
python manutenzione.py utenti password EMAIL
python manutenzione.py utenti superadmin
python manutenzione.py uploads [--elimina] [-y]
python manutenzione.py modalita [--single|--multi]
python manutenzione.py backup [--crea|--elenca|--ripristina FILE]
```

`--db PERCORSO` e' globale e vale per ogni subcomando e per il menu: e' cio'
che permette di ispezionare un'installazione diversa da quella corrente.

Codici di uscita: `0` tutto bene, `1` problemi rilevati o operazione fallita,
`2` errore d'uso (lo fornisce argparse). `diagnosi` distingue: `0` nessun
problema, `1` almeno un problema di gravita' `errore`. Gli avvisi non alterano
il codice di uscita.

## Rapporto di stato

`stato.raccogli(conn, config, radice)` restituisce un dizionario, che
`--json` stampa cosi' com'e' e la TUI formatta. Contenuto:

- **database**: percorso, dimensione, `user_version`, esito `integrity_check`,
  modalita' journal;
- **schema**: versione rilevata (`migrate.describe_version`), migrazioni
  pendenti;
- **modalita'**: `single_struttura`, numero di strutture, elenco con id e nome;
- **utenti**: totale attivi per ruolo, cancellati, disattivati, per struttura;
- **dati**: conteggi di apparecchi, manutenzioni, verifiche, documenti;
- **uploads**: cartella, numero file, byte, orfani;
- **AI**: provider predefinito, quali chiavi sono presenti (mai il valore);
- **posta**: host e porta SMTP e IMAP, se configurati;
- **backup**: quanti, il piu' recente.

Nessun segreto viene stampato: le chiavi e le password compaiono come
`presente` / `assente`. Vale anche per `--json`, che finisce nei log.

## Diagnosi

Ogni controllo e' una funzione `(conn, config, stato) -> Esito | None`, dove
`Esito` porta `gravita` (`errore` | `avviso`), `titolo`, `dettaglio` e
`rimedio` (il comando da eseguire). `diagnosi.esegui()` le chiama tutte e
restituisce la lista; nessuna stampa, nessun `sys.exit`.

| Controllo | Gravita' | Perche' |
|---|---|---|
| `integrity_check` fallito | errore | database corrotto |
| `foreign_key_check` non vuoto | errore | FK pendenti da migrazione interrotta |
| migrazioni pendenti | errore | l'applicazione non parte o si comporta male |
| nessun utente attivo | errore | installazione inaccessibile |
| struttura senza admin attivo | errore | nessuno puo' amministrarla |
| hash password non riconosciuto | errore | il login solleva un'eccezione, risposta 500 |
| email bloccata in `login_attempts` | avviso | 30 minuti letti come credenziali sbagliate |
| utenti con `attivo = 0` | avviso | ricevono "credenziali non valide" |
| email con suffisso `#eliminato-` | avviso | account distrutti che qualcuno cerca ancora |
| `single_struttura` incoerente col numero di strutture | avviso | |
| cartella uploads assente | errore | |
| righe che puntano a file mancanti | avviso | |
| file orfani | avviso | |
| provider AI senza chiave | avviso | |
| SMTP non configurato ma avvisi attivi | avviso | le scadenze non partono |
| sessioni scadute accumulate | avviso | |

### Perche' proprio questi, per il login

`auth.py:411` cerca `SELECT * FROM utenti WHERE email = ? AND attivo = 1`, e
`auth.py:422-431` confronta con `check_password_hash`. Il messaggio
"Credenziali non valide" copre quindi tre situazioni indistinguibili
dall'esterno: nessuna riga con quell'indirizzo, la riga c'e' ma ha
`attivo = 0`, oppure l'impronta e' verificabile e la password non corrisponde.
Un quarto caso a monte, il blocco per tentativi ripetuti, ha un messaggio
proprio che pero' chi legge i log confonde con gli altri.

Un quinto caso non produce affatto quel messaggio, ed e' il piu' insidioso su
un'installazione migrata da lontano. `check_password_hash` **solleva**
`ValueError: Invalid hash method` quando l'impronta usa un metodo che werkzeug
3 non conosce piu' — i vecchi `sha256$sale$impronta` prodotti da werkzeug 2.
`auth.py:422` non la cattura, quindi il login risponde 500 invece di rifiutare.
Verificato:

```
'sha256$abc$def'    -> ValueError: Invalid hash method 'sha256'.
'!utente-eliminato' -> False   (split fallisce, nessuna eccezione)
```

La diagnosi separa i cinque casi. Il controllo sull'hash accetta solo i due
metodi che `werkzeug.security._hash_internal` implementa oggi (`pbkdf2:` e
`scrypt`) e segnala tutto il resto, distinguendo le impronte che sollevano da
quelle che tornano semplicemente `False` (il sentinella `!utente-eliminato` di
`utente_service`, che non ha la forma `metodo$sale$impronta`).

## Azzeramento degli utenti

Due semantiche.

**Conservativa (predefinita)** — chiama `utente_service.cancella_utente()` su
ogni utente in ambito. La riga resta come voce storica: email spostata a
`#eliminato-N`, `password_hash` inutilizzabile, `attivo = 0`, `eliminato_il`
valorizzato. Le otto colonne `*_by` di `RIFERIMENTI_UTENTE` **non si toccano**:
su un registro di apparecchi elettromedicali "chi ha inserito questo
apparecchio" e' tracciabilita', ed e' la scelta gia' presa nella 2.6.2.

**Definitiva (`--definitivo`)** — chiama `struttura_service._rimuovi_utenti()`:
le righe spariscono, i riferimenti `*_by` vengono azzerati, l'identita' si
slega dal registro attivita'. Si perde la tracciabilita', quindi sta dietro un
flag esplicito e una conferma in piu'.

### Vincoli, in ordine di applicazione

1. **Un accesso deve sopravvivere.** L'operazione si rifiuta di lasciare il
   database senza nessun utente in grado di entrare. Chi azzera indica
   l'accesso di rimpiazzo (`--nuovo-admin EMAIL`, password chiesta con
   `getpass`, oppure i dati raccolti dal menu). Azzeramento e creazione
   avvengono **nella stessa transazione**: o si esce con un accesso valido, o
   non e' successo niente. Con `--struttura ID` il vincolo si restringe: deve
   restare un amministratore per quella struttura, e se esistono superadmin
   globali quelli bastano.
2. **Backup prima di scrivere**, con lo stesso schema di `migrate.py`
   (`<db>.bak_manutenzione_<timestamp>`), sempre, anche con `-y`.
3. **Ambito.** Senza `--struttura` l'operazione tocca tutti gli utenti,
   superadmin compresi. Con `--struttura ID` tocca solo chi ha quel
   `struttura_id`; i superadmin hanno `struttura_id NULL` e restano fuori per
   costruzione, e i tecnici assegnati perdono la riga in `tecnici_strutture`
   per quella struttura ma sopravvivono se servono altrove.
4. **Conferma doppia** in modalita' interattiva: prima il riepilogo con i
   conteggi di `conteggi_riferimenti()` per ogni utente, poi la digitazione del
   nome della struttura, o della parola `AZZERA` in ambito globale. `-y` salta
   le domande, mai il backup ne' il vincolo 1.
5. **Registrazione.** `log_attivita()` di `models.py` vuole il contesto Flask,
   che qui non c'e'. L'operazione scrive direttamente in `log_attivita` una
   riga con `utente_id` NULL, azione `azzeramento_utenti` e dettaglio che
   riporta ambito, semantica e numero di utenti coinvolti.

## Interfaccia testuale

`tui.py` fornisce: rilevamento del supporto colore (gia' presente in
`migrate.py` come `_supports_color`, che si sposta qui), intestazioni,
tabelle a colonne allineate calcolate sui dati, righe di esito
(`ok` / `avviso` / `errore`), barra di avanzamento per le operazioni lunghe,
prompt di conferma e prompt di conferma distruttiva con parola da digitare.

Vincoli di resa: nessun carattere fuori da CP1252 quando `sys.stdout.encoding`
non e' UTF-8 (le console Windows vecchie), riconfigurazione dello stream come
gia' fanno gli script attuali, e degradazione a testo puro quando l'output non
e' un terminale — cosi' `manutenzione.py stato > rapporto.txt` produce un file
leggibile.

Il menu e' a un livello di profondita': voci numerate, `q` per uscire, invio
vuoto ricarica lo stato. Dopo ogni operazione lo stato viene ricalcolato e
ristampato, cosi' si vede l'effetto.

## Errori

- Database assente: messaggio e uscita `1`, con l'indicazione di `seed.py`.
- Database non leggibile o non SQLite: messaggio e uscita `1`.
- Schema che manca di tabelle attese: la raccolta dello stato prosegue e
  marca la sezione come non disponibile; la diagnosi lo segnala come errore
  con rimedio `python manutenzione.py migra`. Non e' un caso di rifiuto: e'
  esattamente l'installazione vecchia che lo strumento deve saper ispezionare.
- Operazione distruttiva interrotta: transazione annullata, backup conservato,
  percorso del backup stampato.
- `Ctrl-C` nel menu: uscita pulita senza traceback.

## Test

Nuovo `tests/test_manutenzione.py`, sopra le fixture di `conftest.py`:

- raccolta dello stato su un database di prova, e su uno a cui mancano tabelle;
- ogni controllo di diagnosi, con e senza il problema presente;
- i cinque casi di accesso rifiutato distinti correttamente, compreso l'hash
  con metodo abbandonato che fa sollevare `check_password_hash`;
- azzeramento conservativo: `*_by` intatti, righe storiche presenti, dati non
  utente invariati;
- azzeramento definitivo: righe assenti, `*_by` azzerati;
- rifiuto senza accesso di rimpiazzo, e nessuna scrittura dopo il rifiuto;
- atomicita': un fallimento a meta' non lascia scritture;
- ambito `--struttura`: gli utenti delle altre strutture non si toccano;
- subcomandi via `main(argv)` con codici di uscita attesi;
- `stato --json` non contiene nessun segreto;
- `tui` degrada senza colore quando l'output non e' un terminale.

I 395 test esistenti devono restare verdi.

## Documentazione e rilascio

- `CHANGELOG.md`: voce 2.6.3.
- `app.py:34` e `config.json`: `2.6.3`.
- `CLAUDE.md` e `AGENTS.md`: `manutenzione.py` nella sezione dei comandi, con
  una riga sul rapporto di stato e una sull'azzeramento utenti.
- `DOCUMENTAZIONE.md`: sezione nuova sullo strumento, e aggiornamento della
  ricetta "Password dimenticata", che oggi consiglia una riga di Python con
  `sqlite3` scritta a mano.
