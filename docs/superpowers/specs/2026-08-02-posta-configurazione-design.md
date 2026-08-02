# MedInventory 2.6.2 — La posta: un server solo, preferenze per struttura

**Versione target:** 2.6.2
**Data:** 2026-08-02
**Stato:** progettato e approvato.

Quarta miglioria della 2.6.2. Tocca `strutture_bp.py`, `templates/strutture/config.html`,
`scheduler.py` e `models.apply_schema_updates()`. Indipendente dalle altre tre.

---

## Il problema

La configurazione di posta di una struttura mescola due cose che non stanno insieme:

- **il server** — `smtp_host`, `smtp_port`, `smtp_user`, `smtp_from`, TLS e la password
  cifrata, salvati in `strutture_config` (`strutture_bp.py:728-754`) e letti da
  `scheduler.py` con ripiego sul globale;
- **le preferenze** — `strutture.email_notifiche` (a chi scrivere), `report_frequenza`,
  e un interruttore.

Un server di posta e' infrastruttura del deployment, non un dato della clinica. Averlo per
struttura significa moltiplicare le credenziali da tenere aggiornate, e — come si e' visto
esportando una struttura nella 2.6.0 — portarsele dietro in un archivio consegnabile.

**Il server diventa solo di sistema. Alla struttura restano le preferenze.**

---

## Due difetti trovati verificando

### Il report PDF non si puo' accendere

`scheduler.py:204` invia il report PDF alle strutture con `report_pdf_attivo = 1`. Quella
chiave **non viene scritta da nessuna parte**: ne' dal modulo di configurazione, ne' da un
template, da niente — verificato per grep su tutto il progetto. Resta a zero per sempre.

Il report esiste ed e' completo: genera il documento col motore di stampa e il logo della
struttura, lavoro della 2.5. Non e' mai stato raggiungibile.

### L'etichetta dice una cosa e ne fa un'altra

La casella che l'operatore vede si chiama «Report schedulato attivo»
(`config.html:227`) e scrive `report_schedulato_attivo`, che controlla il **digest di
testo** (`scheduler.py:173`). Chi la spunta aspettandosi il PDF riceve un elenco testuale.

---

## Cosa resta alla struttura

Una domanda sola, con la sua risposta: **gli avvisi di scadenza li vuoi, e in che forma?**

| Impostazione | Valori |
|---|---|
| Destinatario | `strutture.email_notifiche`, come oggi |
| Avvisi di scadenza | attivi / non attivi |
| Formato | testo nel corpo, oppure PDF allegato |
| Frequenza | giornaliera, settimanale, mensile |

Le due chiavi `report_schedulato_attivo` e `report_pdf_attivo` si unificano in una coppia:
un interruttore e un formato. Il percorso del PDF diventa raggiungibile per la prima
volta, invece di essere ritirato: e' codice che funziona.

**Chi oggi riceve il digest deve continuare a riceverlo.** La migrazione porta
`report_schedulato_attivo = 1` nel nuovo interruttore con formato «testo». Un
aggiornamento che spegne in silenzio gli avvisi di scadenza di un parco di elettromedicali
sarebbe il peggior modo di consegnare questa modifica.

### Il monitoraggio della casella

Nella pagina compare anche l'opzione **«monitora una casella di posta per i verbali di
manutenzione»**, **disabilitata in permanenza**, con scritto accanto che arrivera' in una
versione successiva. Oggi l'IMAP e' solo globale: non esiste una casella per struttura, e
costruirla e' lavoro a se'.

**Non si aggiunge una chiave di configurazione per essa.** Un valore che niente puo'
scrivere e' esattamente il difetto appena trovato con `report_pdf_attivo`: sembra
configurazione viva e non lo e'. Il controllo e' disabilitato nel modulo e basta.

---

## Il mittente e' unico

Tolto `smtp_from` per struttura, tutte le email partono dall'indirizzo di sistema. Il
destinatario capisce di quale struttura si tratta **dal messaggio**, non dal mittente.

Verificato cosa c'e' gia':

- **Digest di testo** — a posto. Oggetto: `Scadenzario <nome struttura> — <data>`. Corpo:
  intestazione con la struttura, e ogni riga porta la divisione:
  `Ecotomografo (mat. R-00015) — Oculistica — scade: 12/03/2026 (18 gg)`.
- **Email col PDF** — da correggere. Oggetto: `Report scadenze <nome struttura> — <data>`,
  a posto. Corpo: una riga sola, «In allegato il report periodico delle scadenze», senza
  struttura ne' altro. Va scritto di quale struttura si tratta.

La divisione compare per riga e non nell'oggetto, ed e' corretto: un avviso di scadenza
copre l'intera struttura e attraversa piu' divisioni. Un oggetto che ne nominasse una sola
sarebbe falso.

---

## La migrazione

In `models.apply_schema_updates()`, idempotente come le altre:

1. **Cancellare** da `strutture_config` le chiavi del server: `smtp_host`, `smtp_port`,
   `smtp_user`, `smtp_from`, `smtp_use_tls`, `smtp_password_encrypted`. Restano lette da
   nessuno; lasciarle significa tenere configurazione morta che sembra viva, con dentro
   una credenziale cifrata.
2. **Convertire** `report_schedulato_attivo = 1` nel nuovo interruttore, formato «testo».
3. **Cancellare** `report_pdf_attivo`, che nessuno ha mai potuto scrivere.

Non serve un passo corrispondente in `migrate.py`: quelli portano avanti lo **schema** da
una versione all'altra, mentre questa e' pulizia di **dati** che deve avvenire una volta
sola su qualunque installazione — ed `apply_schema_updates()` gira a ogni avvio, che e'
esattamente la garanzia che serve.

### Effetti collaterali graditi

`struttura_service._azzera_config_sensibile` — aggiunta nella 2.6.0 perche' gli archivi
esportati contenevano le credenziali SMTP e le chiavi AI in chiaro — dopo questa modifica
non trovera' piu' nulla di SMTP da azzerare. **Non va tolta**: continua a servire per le
chiavi AI, che restano per struttura, e per qualunque chiave sensibile futura. Il criterio
per schema del nome resta quello giusto.

---

## Test

- **Chi riceveva il digest continua a riceverlo dopo la migrazione**: e' l'asserzione che
  conta piu' di tutte, perche' il difetto peggiore di questa modifica sarebbe spegnere
  avvisi senza che nessuno se ne accorga.
- **Il report PDF si puo' accendere e arriva** — cosa che oggi non e' mai stata possibile.
- **Le chiavi del server spariscono da `strutture_config`**, password cifrata compresa, e
  la migrazione e' idempotente: eseguirla due volte non cambia il risultato.
- **Una struttura che aveva un server proprio continua a ricevere le email**, ora tramite
  quello di sistema: il ripiego esisteva gia', ma va provato che l'invio non si interrompa.
- **Il corpo dell'email col PDF nomina la struttura.**
- **Il digest continua a nominare struttura e divisione** — un test che lo inchioda ora
  che la posta viene riordinata.
- **Il controllo del monitoraggio casella e' presente e non attivabile**, e non esiste una
  chiave in `strutture_config` che lo rappresenti.
- **Nel modulo di configurazione non compaiono piu' i campi del server.**

Ogni test va provato **sensibile**: reintrodurre il difetto e verificare che cada. In
questo progetto sette test si sono rivelati ciechi.

---

## Fuori ambito

- **Il monitoraggio della casella per struttura.** E' la «migliore implementazione» che
  questa modifica rimanda: richiede credenziali IMAP per struttura, una casella per
  ciascuna, e la separazione dei messaggi in arrivo.
- **`email_monitor._find_apparecchio`**, che se non trova nulla dentro la struttura
  prosegue la ricerca senza filtro e aggancia il verbale a un apparecchio di un'altra —
  dichiarato fra i limiti noti della 2.6.0. Va corretto quando si riprende in mano l'IMAP.
- **La tabella `email_config`**, legacy e non interrogata da nessuno.
