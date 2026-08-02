# MedInventory 2.6.2 — Cancellazione degli utenti

**Versione target:** 2.6.2
**Data:** 2026-08-02
**Stato:** progettato e approvato.

La 2.6.2 conterra' anche altre migliorie, ancora da progettare. Questa specifica
copre la sola cancellazione degli utenti e si puo' implementare da sola.

---

## Il problema

Oggi un utente si puo' solo disattivare. L'elenco della gestione utenti accumula chi
non lavora piu' nella struttura, e non c'e' modo di ripulirlo. Serve poter cancellare.

Cancellare un utente pero' non e' come cancellare una riga qualunque: `utenti.id` e'
referenziato da otto colonne sparse su sette tabelle — chi ha inserito un apparecchio,
chi ha registrato una manutenzione, chi ha caricato un documento. Su un registro di
apparecchi elettromedicali quella e' tracciabilita', non metadato accessorio.

---

## La scelta di fondo

**Chi cancella vuole togliere l'accesso, non riscrivere la storia.** L'utente sparisce
dall'elenco e non entra piu'; ma su un apparecchio inserito tre anni fa continua a
leggersi chi l'ha inserito.

Non c'e' una componente di privacy: nessuno sta chiedendo che i propri dati personali
spariscano dal sistema. Se un domani quella richiesta arrivasse, questa soluzione **non**
la soddisferebbe, e servirebbe una cancellazione diversa — vale la pena saperlo adesso.

### Perche' la riga resta

`created_by` e le altre sette colonne sono chiavi esterne verso `utenti(id)`, senza
`ON DELETE`. Con le chiavi esterne attive — e in questo progetto lo sono — non esiste
uno stato in cui la riga e' cancellata e il riferimento continua a puntarle: o il
riferimento e' valido, o va azzerato. Azzerarlo significa perdere l'autore ovunque, che
e' precisamente cio' che non si vuole.

Restano due modi per tenere il nome:

1. **La riga sopravvive come voce storica**, con l'account distrutto.
2. **Cancellare la riga davvero**, togliendo il vincolo di chiave esterna dalle otto
   colonne e conservando l'identita' in una tabella a parte, ricomposta con una UNION.

Il risultato che l'operatore vede e' identico. Il costo no: la seconda e' una migrazione
su sette tabelle, e in questo progetto le migrazioni hanno gia' fatto danni veri — la
v2.2 svuotava `utenti`, e su un'installazione anteriore alla v1.2 l'avvio muore in
`init_db()` prima ancora che le migrazioni partano. Non si spende quel rischio per un
guadagno che nessuno vede.

**Si adotta la prima.**

### Perche' non e' la disattivazione con un altro nome

Un utente disattivato si riattiva con un clic, conserva la password e tiene occupato il
proprio indirizzo email. Un utente cancellato non torna, non ha piu' credenziali, e il
suo indirizzo e' di nuovo libero: se la persona rientra fra due anni le si crea un
account nuovo con la stessa email.

---

## Meccanica della cancellazione

Una colonna nuova su `utenti`:

```sql
ALTER TABLE utenti ADD COLUMN eliminato_il DATETIME
```

`NULL` significa utente normale. Valorizzata significa cancellato.

Cancellare, in **una sola transazione**:

| Campo | Cosa succede | Perche' |
|---|---|---|
| `password_hash` | sostituito con un valore che nessuna password puo' soddisfare | la colonna e' `NOT NULL`; non basta svuotarla |
| `email` | diventa `<email originale>#eliminato-<id>`: `mario@x.it` → `mario@x.it#eliminato-42` | la colonna e' `UNIQUE`; l'indirizzo deve tornare disponibile per un account nuovo. L'`id` nel suffisso non e' decorativo: se Mario viene ricreato e ricancellato, il secondo account ha un id diverso e non collide col primo. La forma resta leggibile a chi guarda il database |
| `attivo` | `0` | l'accesso passa gia' da `attivo = 1` (`auth.py:380`) |
| `eliminato_il` | adesso | e' cio' che lo toglie dagli elenchi |
| `sessioni` | cancellate | l'utente esce **subito**, non al prossimo accesso |
| `utenti_divisioni` | cancellate | senza account non significano piu' nulla |
| `tecnici_strutture` | cancellate | idem |

**Restano intatti** `nome`, `cognome`, `ruolo`, `struttura_id`: sono cio' che fa
funzionare «inserito da Mario Rossi» sulle schede e dicono a quale struttura
apparteneva. Nessuna delle otto colonne `*_by` viene toccata — e' il punto della scelta.

L'operazione **non e' reversibile** dall'interfaccia: non esiste un pulsante che
ripristini un utente cancellato. Chi sbaglia ricrea l'account e riassegna le divisioni.

### Dove vive

In `struttura_service.py` esiste gia' `_rimuovi_utenti(conn, ids_utenti, annota_email)`,
che cancella fisicamente gli utenti di una struttura in cancellazione. **Non e' la stessa
operazione** e non va riusata: quella azzera gli otto riferimenti, questa li conserva.

La nuova primitiva sta accanto, nello stesso modulo o in uno suo, con le stesse regole:
niente Flask, riceve una `sqlite3.Connection`, il chiamante apre e chiude la transazione.
La differenza fra le due va scritta nel docstring di entrambe, perche' il prossimo che
legge non ne usi una per l'altra.

---

## Chi puo' cancellare, e cosa si rifiuta

### Autorizzazione

`admin.py:_check_utente_scope()` fa gia' quel che serve e non va toccata: il superadmin
puo' sempre; un admin non puo' agire su un superadmin ne' su utenti di altre strutture.

Un admin non puo' cancellare un **tecnico**: i tecnici hanno `struttura_id` nullo e non
risultano «suoi». E' il principio fissato nella 2.6.0 — un tecnico e' un account
condiviso fra strutture, non proprieta' di una. Solo il superadmin lo cancella.

### I rifiuti

Ogni rifiuto deve dire **come rimediare**, non solo che e' vietato.

- **Se stessi.** Vale gia' per la disattivazione.
- **L'ultimo admin di una struttura.** Senza, quella struttura resta senza nessuno che
  possa amministrarla: aggiungere utenti, gestire divisioni, configurare l'AI. Si contano
  **tutti gli admin esistenti** della struttura, non solo quelli attivi: e' il freno che
  non obbliga l'operatore a ragionare sullo stato di attivazione mentre sta cancellando.
  Messaggio: nomina prima un altro amministratore per questa struttura.
- **L'ultimo superadmin.** Stessa logica un piano sopra, e piu' grave: senza superadmin
  nessuno puo' piu' creare strutture, fare backup, o riparare una struttura rimasta senza
  admin. Non c'e' nessuno sopra che possa rimediare — si resta chiusi fuori dal proprio
  deployment.

Un utente gia' cancellato non e' modificabile, non e' reimpostabile nella password e non
e' ri-cancellabile: le rotte che agiscono su un utente per id devono rifiutare chi ha
`eliminato_il` valorizzato.

---

## L'interfaccia

### L'elenco

Nella gestione utenti il pulsante di **disattivazione sparisce** e al suo posto c'e'
**Elimina**. Gli utenti cancellati non compaiono.

### La casella «attivo» nel modulo di modifica

Oggi `attivo` si cambia **soltanto** da quel pulsante: il modulo di modifica non lo tocca.
E il sistema disattiva utenti da solo — `models.py:624`, all'avvio, mette `attivo = 0`
agli utenti rimasti senza struttura quando ci sono due o piu' strutture attive, ed e' una
protezione della 2.6.0. Il log dice all'operatore «Riassegnalo a una struttura per
riabilitarlo».

Togliendo il pulsante senza altro, quella frase diventa un'istruzione impossibile e
l'utente resta disattivato per sempre. Quindi `attivo` diventa una **casella di spunta nel
modulo di modifica**, accanto al campo struttura che e' la causa del problema.

### La pagina di conferma

Pagina dedicata, come per la cancellazione di una struttura, ma **senza dover riscrivere
nulla**. La conferma e' doppia nel senso che conta: due atti deliberati su due schermate
diverse, non due clic vicini.

Poiche' non si digita, l'unica difesa contro «ho cliccato la riga sbagliata» e' che la
pagina dica con chiarezza di chi si tratta. **Nome, cognome, email, ruolo e struttura
vanno in testa e in evidenza**, non in fondo fra i dettagli: sono cio' che sostituisce la
digitazione.

Poi, prima del pulsante:

- che l'operazione **non e' reversibile**, a differenza della disattivazione di prima;
- **quanti** apparecchi, interventi e documenti portano il suo nome, e che quel nome
  **resta** su quelle schede;
- che l'indirizzo email **torna libero** per un account nuovo.

I conteggi si ottengono dalle otto colonne di `RIFERIMENTI_UTENTE`, che esiste gia' in
`struttura_service.py`.

---

## Il registro

`log_attivita` con l'azione di eliminazione, e nei dettagli: **l'email originale** (dopo
la cancellazione nel database c'e' solo la forma spostata), nome, cognome, ruolo, la
struttura, e i conteggi di cio' che porta il suo nome.

La voce va scritta **dentro la transazione, prima del commit**, e con `struttura_id`
valorizzato. Sono le due lezioni della 2.6.1: li' la registrazione stava fuori dal `try`,
e un guasto al suo interno lasciava l'operazione avvenuta senza alcuna traccia; e nasceva
con `struttura_id` nullo, quindi era invisibile in `/admin/log-attivita` proprio a chi
aveva eseguito l'operazione.

---

## Gli elenchi da cui un utente cancellato deve sparire

E' il punto in cui questa modifica puo' sbagliare in silenzio: se un elenco se ne
dimentica, un utente cancellato ricompare selezionabile. L'elenco e' esatto, ricavato da
`grep "FROM utenti"`:

**Da filtrare con `eliminato_il IS NULL`:**

- `admin.py:96` e `admin.py:106` — la gestione utenti, due rami (superadmin e admin)
- `admin.py:1218` — l'elenco dei tecnici
- `strutture_bp.py:389` e `strutture_bp.py:392` — utenti e tecnici nella scheda della
  struttura
- `struttura_service.py:302` — `contenuto_struttura`, il conteggio utenti mostrato prima
  di cancellare una struttura

**Gia' a posto, da non toccare** (e da verificare con un test, non per deduzione):

- `auth.py:380` — l'accesso filtra `attivo = 1`, e l'email e' comunque spostata
- `admin.py:1111` e `admin.py:1119` — filtrano gia' `attivo = 1`
- `admin.py:166`, `250`, `1252`, `1331` — i controlli di unicita' dell'email: dopo la
  cancellazione l'indirizzo originale e' libero, ed e' il comportamento voluto
- `models.py:579` — la disattivazione degli orfani all'avvio filtra `attivo = 1`

**Deliberatamente non filtrato:** `struttura_service.py:193` e `:231`, dentro
`rimuovi_strutture`. Cancellando una struttura, le righe storiche dei suoi utenti
cancellati devono sparire con lei.

---

## Un difetto esistente che questa modifica chiude

`admin.py:1369-1400`, `tecnico_elimina`, cancella gia' fisicamente un utente — ed **e'
rotta**. Azzera dieci colonne, fra cui `manutenzioni.updated_by` e `verifiche.updated_by`,
che **non esistono**: solo `apparecchi` ha `updated_by`. Verificato eseguendo la rotta
vera:

```
POST /admin/tecnici/<id>/elimina  ->  STATUS: 500
tecnico ancora presente: True
```

E' il quarto elenco divergente delle stesse colonne nel progetto. La rotta va portata
sulla primitiva nuova: elimina il 500, e fa sparire la copia.

Nota di merito: cancellare un tecnico oggi azzererebbe comunque l'autore ovunque, mentre
con la primitiva nuova il nome resta — un miglioramento, non solo una riparazione.

---

## Test

Quello che conta piu' di tutto: **dopo la cancellazione, chi ha inserito un apparecchio
si legge ancora**. E' l'unica asserzione che distingue questa soluzione dalla
cancellazione fisica, ed e' la ragione per cui e' stata scelta.

Poi:

- l'indirizzo email e' di nuovo utilizzabile per un account nuovo;
- l'utente cancellato non entra piu', e le sue sessioni aperte sono chiuse subito;
- non compare in **nessuno** degli elenchi sopra — un test per ciascuno, perche' e' il
  punto in cui si sbaglia in silenzio;
- i tre rifiuti, ciascuno con la propria misura sul database: se stessi, l'ultimo admin
  di una struttura, l'ultimo superadmin;
- un admin non cancella utenti di altre strutture, ne' superadmin, ne' tecnici;
- un utente gia' cancellato non e' modificabile ne' ri-cancellabile;
- `attivo` si cambia dal modulo di modifica, e un utente disattivato all'avvio dal
  controllo sugli orfani torna riattivabile;
- la voce di registro contiene l'email originale ed e' visibile all'admin della struttura
  in `/admin/log-attivita`;
- `POST /admin/tecnici/<id>/elimina` non restituisce piu' 500 e il tecnico sparisce.

Ogni test va provato **sensibile**: reintrodurre il difetto e verificare che cada. In
questo progetto sette test si sono rivelati ciechi, e l'ultimo cadeva per il motivo
sbagliato.

---

## Fuori ambito

- **Cancellazione con rimozione dei dati personali.** Non serve oggi. Servirebbe la
  seconda strada, o l'azzeramento degli autori.
- **Ripristino di un utente cancellato.** Chi sbaglia ricrea l'account.
- **Le altre chiamate a `log_attivita` in `apparecchi.py` che omettono `struttura_id`**
  (righe 563, 751, 777). Stesso difetto della 2.6.1, preesistente, fuori da questa
  modifica.
