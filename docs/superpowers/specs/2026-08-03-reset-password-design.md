# MedInventory 2.6.2 — Reset della password dalla schermata di accesso

**Versione target:** 2.6.2
**Data:** 2026-08-03
**Stato:** progettato e approvato.

Quinta miglioria della 2.6.2. **Dipende dalla quarta**
(`2026-08-02-posta-configurazione-design.md`): senza un server SMTP di sistema questa
funzione non ha come consegnare nulla. Va implementata dopo.

---

## Il problema

Chi dimentica la password oggi deve chiedere a un amministratore, che gliela reimposta e
gli comunica a voce o per messaggio una password temporanea. Fuori orario, o quando
l'amministratore e' l'unico che non risponde, si resta chiusi fuori.

Il meccanismo per farlo da soli esiste gia' quasi tutto: `admin.utente_reset_password`
(`admin.py:302`) genera una temporanea con `secrets.token_urlsafe(10)`, forza
`primo_accesso = 1` e chiude le sessioni. Manca il modo di chiederlo dalla schermata di
accesso e di riceverla per email.

---

## La scelta che conta: la password attuale non viene distrutta

Nella versione piu' semplice — quella che il reset dell'amministratore fa oggi — la
temporanea **sostituisce** la vecchia. Sulla pagina di accesso, dove chiunque puo'
digitare un indirizzo, questo significa che **chiunque conosca l'email di un collega puo'
buttarlo fuori dal proprio account**: basta chiedere il reset. Il collega arriva la
mattina, la sua password non funziona piu', e deve mettersi a cercare un'email. In un
reparto, prima di un turno, non e' un fastidio teorico.

**Quindi la temporanea vale accanto alla password attuale, e scade.**

Due colonne nuove su `utenti`:

```sql
ALTER TABLE utenti ADD COLUMN reset_hash TEXT
ALTER TABLE utenti ADD COLUMN reset_scadenza DATETIME
```

Chi non ha chiesto niente continua a entrare come sempre. Chi ha chiesto il reset puo'
usare la temporanea finche' non scade. Nessuno puo' chiudere fuori nessuno.

**Durata: 30 minuti**, una costante del modulo. E' il tempo di leggere un'email, non di
lasciare una credenziale valida in una casella.

---

## Il giro completo

1. **Sulla schermata di accesso** compare «Password dimenticata?». Il collegamento
   **appare solo se il server SMTP di sistema e' configurato**: un pulsante che accetta la
   richiesta e non manda niente e' peggio che non averlo, perche' l'utente aspetta invano.
   Quando manca, nel log resta scritto perche'.

2. **Si chiede l'indirizzo.** La risposta e' **sempre la stessa**, che l'indirizzo esista o
   no: «Se l'indirizzo e' registrato, riceverai a breve un'email con una password
   temporanea», piu' cosa fare se non arriva nulla — controllare l'indirizzo, rivolgersi
   all'amministratore.

   Non si dice se l'account esiste. Il progetto ha `cloudflare_mode.py` e l'opzione
   `force_https`: e' previsto che l'applicazione stia dietro un tunnel e sia raggiungibile
   da fuori. Su una LAN chiusa la differenza sarebbe teorica; esposta su Internet e'
   l'elenco degli indirizzi validi su cui poi provare le password.

3. **Se l'indirizzo corrisponde a un utente attivo e non cancellato**, si genera la
   temporanea con lo stesso `secrets.token_urlsafe(10)` gia' in uso, se ne salva
   l'impronta in `reset_hash` con la scadenza, e si spedisce.

   Un utente **disattivato** o **cancellato** (vedi
   `2026-08-02-cancellazione-utenti-design.md`) non riceve nulla — non potrebbe comunque
   entrare — e chi ha chiesto vede lo stesso messaggio di tutti.

4. **L'email** parte dal mittente di sistema e contiene: la password temporanea, entro
   quando va usata, e — riga che non va omessa — **che se non e' stato l'utente a
   chiederlo la sua password attuale funziona ancora e puo' ignorare il messaggio**. E'
   cio' che rende comprensibile la scelta di non distruggere la vecchia.

5. **All'accesso**, se la password inserita non corrisponde a quella dell'utente, si prova
   la temporanea, se c'e' e non e' scaduta. Riuscendo: si azzerano `reset_hash` e
   `reset_scadenza`, si mette `primo_accesso = 1` — quindi l'utente e' costretto a
   sceglierne una nuova, con le regole gia' in vigore — e si chiudono le altre sessioni.

6. **Entrando con la password normale**, un reset in sospeso viene azzerato: se l'utente
   se l'e' ricordata, la temporanea non ha piu' motivo di restare valida.

---

## Il limite alle richieste

Senza limite, chiunque puo' far arrivare a un collega cinquecento email, o consumare la
quota del server di posta.

Si riusa **`login_attempts`**, che gia' registra per indirizzo e per IP e blocca per
trenta minuti: stessa tabella, stesse soglie, nessuna macchina nuova da mantenere. Una
richiesta di reset conta come un tentativo; superata la soglia si risponde con il solito
messaggio senza spedire nulla.

Il conteggio va fatto **prima** di guardare se l'utente esiste, altrimenti il tempo di
risposta diverso fra indirizzo noto e ignoto rivela quello che il messaggio unico
nasconde.

---

## Il registro

`log_attivita` per la temporanea **spedita** e per la temporanea **usata**, con
`struttura_id` valorizzato — la lezione della 2.6.1, dove la voce nasceva con
`struttura_id` nullo ed era invisibile in `/admin/log-attivita` proprio a chi doveva
leggerla.

Le richieste per indirizzi che non esistono **non** finiscono in `log_attivita`: non c'e'
un utente a cui legarle, e scriverci dentro indirizzi arbitrari forniti da chi passa
significa lasciare a un estraneo la penna sul registro di sistema. Restano in
`login_attempts`, che e' il posto fatto per quello.

---

## Test

- **Il giro completo**: si chiede, arriva l'email, si entra con la temporanea, viene
  chiesto di cambiarla.
- **La password vecchia funziona ancora mentre un reset e' in sospeso.** E' l'asserzione
  che distingue questa soluzione da quella semplice, ed e' il motivo per cui e' stata
  scelta.
- **Una temporanea scaduta non entra**; una **gia' usata** non entra una seconda volta.
- **Entrando con la password normale** il reset in sospeso sparisce.
- **Indirizzo inesistente, utente disattivato, utente cancellato**: stesso identico
  messaggio, e **nessuna email spedita**. I tre casi separati, perche' e' il punto in cui
  una svista rivela l'esistenza di un account.
- **Il limite alle richieste blocca**, e blocca **prima** di distinguere se l'utente
  esiste.
- **Senza SMTP configurato il collegamento non compare** nella schermata di accesso.
- **L'email contiene la temporanea, la scadenza, e la riga che dice di ignorare il
  messaggio se non e' stato richiesto.**
- **Dopo l'uso della temporanea le altre sessioni sono chiuse.**

Ogni test va provato **sensibile**: reintrodurre il difetto e verificare che cada. In
questo progetto sette test si sono rivelati ciechi, e l'ultimo cadeva per il motivo
sbagliato.

---

## Fuori ambito

- **Il collegamento usa-e-getta** al posto della password nell'email. Piu' solido, perche'
  nella casella non resta nulla di riutilizzabile; ma e' piu' lavoro e cambia
  l'esperienza. Su una LAN o dietro un tunnel la differenza pratica e' piccola.
- **Le regole della password scelta**, che restano quelle di `auth.cambio_password`.
- **Il reset dell'amministratore** (`admin.utente_reset_password`), che continua a
  mostrare la temporanea a schermo: e' un canale diverso, per il caso in cui l'utente non
  abbia accesso alla propria email.
