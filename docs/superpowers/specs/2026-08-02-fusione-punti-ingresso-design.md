# MedInventory 2.6.2 — I due punti d'ingresso della fusione

**Versione target:** 2.6.2
**Data:** 2026-08-02
**Stato:** progettato e approvato.

Seconda miglioria della 2.6.2, indipendente dalla cancellazione degli utenti
(`2026-08-02-cancellazione-utenti-design.md`): file diversi, si possono implementare in
qualunque ordine.

---

## Il problema

La specifica della fusione prevedeva **due** vie per arrivarci, con una motivazione
esplicita: «nessun criterio automatico trova tutto e nessuna ricerca manuale trova quello
che non sai di avere».

1. L'**elenco dei candidati**, che propone le coppie sospette. Costruito.
2. La **fusione manuale dalla scheda di un apparecchio** — «fondi con…», cercando
   l'altro. **Mai implementata.**

Oggi si puo' fondere solo cio' che l'algoritmo propone. Due schede che sono la stessa
macchina ma con matricole troppo diverse perche' un criterio le accosti — `R-00015` e
`INV/2019/887` — non sono fondibili dall'interfaccia.

E il pulsante che apre l'elenco dei candidati sta nel posto sbagliato:
`templates/apparecchi/dettaglio.html:44`, cioe' dentro la scheda di un **singolo**
apparecchio, mentre la pagina che apre riguarda **tutti** gli apparecchi della struttura.
E' un errore del piano della 2.6.1, che diceva di metterlo «accanto agli altri pulsanti di
azione» della scheda senza accorgersi dell'incoerenza.

---

## Le due correzioni

### Il pulsante dell'elenco va nell'elenco

«Possibili duplicati» si sposta da `dettaglio.html` a
`templates/apparecchi/lista.html`, accanto a «Nuovo apparecchio» (`lista.html:24`): un
comando che riguarda tutto il parco sta dove il parco si guarda.

Va mostrato **solo** ad `admin`, `superadmin` e `tecnico`, gli stessi ruoli a cui la
rotta e' riservata. Oggi in `dettaglio.html` la condizione di ruolo c'e'; spostandolo non
va persa, altrimenti un utente semplice vede un pulsante che gli risponde con un rifiuto.

### La fusione manuale dalla scheda

Nella scheda dell'apparecchio compare **«Fondi con…»**, che porta a una pagina di ricerca:
si cerca l'altra scheda, si sceglie, e si arriva alla stessa pagina di confronto che
l'elenco dei candidati usa gia'. Da li' in poi non cambia nulla — stessa conferma, stessa
esecuzione, stessi rifiuti.

**Nuova rotta:** `GET /apparecchi/<int:id>/fondi`, senza il secondo id. Convive con
`/apparecchi/<int:id>/fondi/<int:altro_id>`, che resta la pagina di confronto.

**Cosa si cerca.** Gli stessi campi dell'elenco apparecchi (`apparecchi.lista`):
matricola, marca, modello, descrizione, ubicazione, fornitore. Riusare quei campi
significa che chi ha imparato a cercare in un posto sa cercare anche qui.

**Cosa si trova.** Solo apparecchi della **stessa struttura**, con lo stesso ambito della
pagina dei candidati. L'apparecchio di partenza e' escluso da se' stesso.

**Senza una ricerca digitata non si elenca niente**: un invito a cercare, non l'intero
parco. Su una struttura con migliaia di apparecchi un elenco completo non aiuta a trovare
il duplicato e costa una pagina lenta.

**Autorizzazione:** `admin`, `superadmin`, `tecnico`. Entrambe le schede devono passare
`models.apparecchio_accessibile()`, come gia' fa la pagina di confronto — e' l'unico modo
accettabile di raggiungere un apparecchio per id in questo progetto.

---

## Gli apparecchi dismessi

**La ricerca manuale li trova, marcati chiaramente come dismessi. L'elenco automatico dei
candidati continua a escluderli.**

I due comportamenti divergono per una ragione precisa. Un elenco di proposte generato da
un algoritmo deve stare stretto: proporre macchine ritirate lo riempirebbe di rumore, e
un elenco rumoroso e' un elenco che nessuno apre. Una ricerca digitata a mano e' l'opposto
— serve ai casi che l'algoritmo non trova, e chi la usa sa gia' cosa sta cercando.

C'e' un caso concreto che senza questo non si ripara: **qualcuno si accorge del doppione e,
invece di fondere, dismette una delle due schede.** Resta una macchina sola con lo storico
spezzato in due, meta' del quale su una scheda dismessa. Escludendo i dismessi dalla
ricerca, l'unica via sarebbe riattivare la scheda, fondere, e sperare che nel frattempo
nessuno l'abbia guardata.

Questo chiude anche un rilievo minore lasciato aperto dalla revisione finale della 2.6.1:
fondere con una scheda dismessa **e' gia' possibile** oggi, scrivendo l'URL a mano. Delle
due l'una — o lo si vieta in tutte le rotte, o lo si rende una scelta consapevole e
visibile. Si sceglie la seconda: e' la piu' utile, e non lascia una scorciatoia che
funziona senza che l'interfaccia la dichiari.

Nella pagina di ricerca un apparecchio dismesso porta un contrassegno visibile accanto
alla matricola. Nella pagina di confronto, dove i due apparecchi sono gia' scelti, lo
stato compare fra i campi confrontati come qualunque altro.

---

## Test

- **Il pulsante «Possibili duplicati» non e' piu' nella scheda** ed e' nell'elenco; non
  compare a un utente semplice.
- **La ricerca trova un duplicato che l'algoritmo non propone**: due schede con matricole
  cosi' diverse che nessuno dei tre criteri le accosta — e' l'intero motivo per cui questa
  via esiste, e un test che usasse una coppia proponibile non proverebbe nulla.
- La ricerca **non trova apparecchi di altre strutture**, nemmeno cercandone la matricola
  esatta.
- La ricerca **non restituisce l'apparecchio di partenza**.
- **Senza testo cercato non elenca niente.**
- **Trova un apparecchio dismesso e lo segnala come tale**; l'elenco automatico dei
  candidati continua a non proporlo — le due asserzioni insieme, perche' e' la divergenza
  voluta ed e' quella che qualcuno "uniformera'" per sbaglio.
- La ricerca e' **negata a un utente semplice**, guardando lo status della rotta e non il
  contenuto della pagina di atterraggio.
- Dalla ricerca si arriva alla pagina di confronto e la fusione si conclude: il giro
  completo, perche' due meta' che funzionano da sole non fanno una funzione che funziona.

Ogni test va provato **sensibile**: reintrodurre il difetto e verificare che cada. In
questo progetto sette test si sono rivelati ciechi, e l'ultimo cadeva per il motivo
sbagliato.

---

## Fuori ambito

- **I criteri dell'elenco automatico** non si toccano. La ricerca manuale esiste proprio
  perche' nessun criterio li trova tutti.
- **Il costo quadratico dell'elenco**, gia' dichiarato fra i limiti noti della 2.6.1.
