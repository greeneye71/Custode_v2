# Reset della password dalla schermata di accesso — Implementation Plan

**Specifica:** `docs/superpowers/specs/2026-08-03-reset-password-design.md`
**Dipende da:** la miglioria 4 (posta), gia' consegnata — `scheduler._config_smtp` / `_invia`.

**Goal:** Chi dimentica la password se la reimposta da solo, ricevendo per email una temporanea che vale **accanto** a quella attuale e scade in 30 minuti.

**Architettura:** quattro strati. (1) `posta.py`, un solo posto da cui parte la posta, estratto dallo scheduler perche' ora serve anche fuori di li'. (2) Le due colonne su `utenti` piu' l'allargamento del CHECK di `login_attempts`. (3) `reset_password.py`, la logica di dominio senza Flask (come `utente_service.py`). (4) Le rotte e i template.

## Vincoli

- Italiano ovunque: interfaccia, commenti, messaggi, commit.
- **La password attuale non viene distrutta.** E' la scelta che distingue questa soluzione: sulla pagina di accesso chiunque puo' digitare l'indirizzo di un collega, e una temporanea che sostituisce la vecchia sarebbe un modo per buttarlo fuori dal proprio account.
- **La risposta e' sempre la stessa**, che l'indirizzo esista o no, sia attivo o no, sia cancellato o no.
- **Durata 30 minuti**, costante di modulo.
- Ogni test provato **sensibile**, eseguendo il **file intero**.
- Niente `CHANGELOG.md` e niente bump di versione: ultimo piano della release.

## Decisioni prese scrivendo il piano

**Il limite alle richieste non usa `esito = 'fallito'`.** La specifica dice di riusare `login_attempts` con le stesse soglie, e cosi' e'; ma scrivere `'fallito'` significherebbe che cinque richieste di reset bloccano il **login** da quell'IP per un quarto d'ora. Dietro un tunnel Cloudflare, dove i client possono presentarsi tutti con lo stesso `remote_addr`, sarebbe la serratura dell'intera azienda in mano a chiunque passi. Si aggiunge quindi il valore `'reset'` al CHECK della tabella: il limite del reset conta `fallito` + `reset`, quello del login continua a contare solo `fallito`.

**`posta.py` invece di un secondo invio.** Lo scheduler ha gia' la risoluzione dei parametri SMTP e l'invio; duplicarli in `auth.py` significherebbe due posti da correggere. Il modulo nuovo li ospita e lo scheduler ci delega.

**Il collegamento «Password dimenticata?»** compare solo se l'SMTP di sistema e' configurato, e la variabile arriva da `inject_globals()` in `app.py`: `login.html` viene reso da sei punti diversi di `auth.py`, e passarla a mano da ognuno e' il modo di dimenticarsene in uno.

---

## Task 1: `posta.py`, un solo posto da cui parte la posta

**File:** crea `posta.py`; modifica `scheduler.py` (`_config_smtp`, `_invia`); test in `tests/test_avvisi_scadenza.py` (che deve restare verde) e `tests/test_posta.py` (nuovo).

Funzioni:
- `smtp_configurato(cfg) -> bool` — host e utente presenti.
- `parametri(cfg) -> dict` con `host`, `porta`, `utente`, `password`, `mittente`, `usa_tls` (la logica che oggi sta in `scheduler._config_smtp`, `smtp_use_tls` booleano o stringa).
- `invia(cfg, destinatario, messaggio) -> bool` — valorizza `From`/`To`, apre la connessione, spedisce; `False` se il server non e' configurato o l'invio fallisce.

`scheduler._config_smtp` diventa `return posta.parametri(...)` e `_invia` delega a `posta.invia`, conservando le proprie righe di log (che nominano la struttura).

**Test nuovi:** `smtp_configurato` falso con host vuoto e con utente vuoto; `parametri` regge `smtp_use_tls` sia `True` sia `'0'`; `invia` non spedisce e torna `False` senza configurazione.

**Sensibilita':** togliere il controllo in `invia` -> cade il test dell'installazione senza posta, in entrambi i file.

---

## Task 2: le colonne e il CHECK

**File:** `schema.sql`, `models.apply_schema_updates()`; test in `tests/test_migrazioni.py`.

1. Su `utenti`: `reset_hash TEXT`, `reset_scadenza DATETIME` — in `schema.sql` per le installazioni nuove e nella lista `migrations` per quelle esistenti.
2. `login_attempts.esito` accetta anche `'reset'`. Il CHECK si cambia solo ricostruendo la tabella: blocco guardato fuori dalla lista `migrations`, sul modello di quello della v2.2 per `utenti`, che rinomina, ricrea, ricopia **nominando le colonne di destinazione**, ricrea gli indici e cancella la vecchia. Nessuna FK punta a questa tabella. Il blocco e' guardato da `"'reset'" not in sql_della_tabella`, quindi non fa nulla dal secondo avvio.

**Test:** le due colonne arrivano su un database esistente; un `INSERT` con `esito='reset'` riesce dopo la migrazione e le righe preesistenti sono ancora li'; la migrazione e' idempotente (due esecuzioni, nessuna tabella `_old` residua, `PRAGMA foreign_key_check` vuoto).

**Sensibilita':** togliere la voce delle colonne -> cade il primo test; togliere il blocco del CHECK -> cade il secondo.

---

## Task 3: `reset_password.py`, la logica senza Flask

**File:** crea `reset_password.py`; test in `tests/test_reset_password.py` (nuovo).

```
DURATA_MINUTI = 30
SOGLIA_IP = 5          # come il login: 5 in 15 minuti
FINESTRA_IP_MINUTI = 15
SOGLIA_EMAIL = 10      # come il login: 10 in 30 minuti
FINESTRA_EMAIL_MINUTI = 30

genera_temporanea() -> str                      # secrets.token_urlsafe(10)
troppe_richieste(conn, ip, email) -> bool       # conta 'fallito' + 'reset'
registra_richiesta(conn, ip, email)             # una riga esito='reset'
destinatario_valido(conn, email) -> row | None  # attivo=1 AND eliminato_il IS NULL
registra_reset(conn, utente_id, temporanea) -> scadenza (stringa)
consuma_temporanea(conn, utente_id, password) -> bool
azzera_reset(conn, utente_id)
messaggio_email(nome, temporanea, scadenza) -> (oggetto, corpo)
```

`consuma_temporanea` verifica impronta e scadenza; riuscendo azzera le due colonne, mette `primo_accesso = 1` e cancella le sessioni dell'utente. L'impronta si salva con `generate_password_hash`, come le password vere: una temporanea in chiaro nel database sarebbe una password in chiaro.

Il corpo dell'email contiene la temporanea, l'ora di scadenza e **la riga che dice che se non e' stato l'utente a chiedere il reset la sua password attuale funziona ancora e puo' ignorare il messaggio**.

**Test:** temporanea valida accettata una volta sola; scaduta rifiutata; utente disattivato e utente cancellato non sono destinatari validi; `azzera_reset` cancella entrambe le colonne; `troppe_richieste` conta anche i `'fallito'` del login; il corpo contiene temporanea, scadenza e la riga sull'ignorare.

---

## Task 4: le rotte e la schermata

**File:** `auth.py`, `templates/login.html`, `templates/password_dimenticata.html` (nuovo), `app.py` (`inject_globals`); test in `tests/test_reset_password_routes.py` (nuovo).

- `GET/POST /password-dimenticata`. Ordine obbligato nel POST: **prima** il limite, **poi** l'esistenza dell'utente — altrimenti il tempo di risposta distingue quello che il messaggio unico nasconde. Risposta sempre uguale. Se il destinatario e' valido: genera, registra, spedisce con `posta.invia`, `log_attivita('reset_password_richiesto', struttura_id=...)`. Se non lo e': nessuna email, nessuna voce in `log_attivita` (non c'e' un utente a cui legarla, e scriverci indirizzi forniti da chi passa vuol dire lasciare a un estraneo la penna sul registro).
- In `login()`: password giusta -> `azzera_reset`; password sbagliata -> `consuma_temporanea`, e se riesce si entra con l'obbligo di cambiarla (`primo_accesso` vale 1 anche se la riga letta prima diceva 0) e `log_attivita('reset_password_usato', struttura_id=...)`.
- `inject_globals()`: `smtp_configurato`. In `login.html`, il collegamento sotto il pulsante Accedi solo `{% if smtp_configurato %}`.

**Test:** giro completo (richiesta -> email -> accesso con la temporanea -> cambio password obbligato); **la password vecchia funziona ancora mentre un reset e' in sospeso**; temporanea scaduta e temporanea gia' usata non entrano; entrando con la password normale il reset sparisce; indirizzo inesistente / disattivato / cancellato danno lo stesso messaggio e non spediscono niente (tre test separati); il limite blocca e blocca prima di distinguere se l'utente esiste; senza SMTP il collegamento non compare; dopo l'uso della temporanea le altre sessioni sono chiuse.
