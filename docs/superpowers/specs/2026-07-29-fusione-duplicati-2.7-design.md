# MedInventory 2.7 — Fusione di apparecchi duplicati

**Versione target:** 2.7.0
**Data:** 2026-07-29
**Stato:** progettato e approvato. Da implementare **insieme** allo spostamento di
apparecchi fra strutture, che va ancora progettato: le due funzioni condividono la
primitiva che sposta un apparecchio con tutti i suoi figli, e scritte insieme la
scrivono una volta sola.

---

## Il problema

Lo stesso apparecchio fisico finisce registrato due volte, con piccole differenze o
errori nella matricola: `R-00015` e `R00015`, `MON-1` e `MON-l`. Succede con gli import
da documenti diversi e con l'inserimento manuale in reparti diversi.

Quando accade, **entrambe le schede hanno gia' una storia**: manutenzioni, verifiche,
documenti, allegati. Cancellarne una perde dei dati; tenerle entrambe spezza lo storico
dell'apparecchio in due e falsa lo scadenzario, perche' la vista `prossime_scadenze`
tiene l'ultimo record per (apparecchio, tipo) e ne vede due di apparecchi.

Serve fonderle preservando tutto.

---

## Come ci si arriva

Due vie, perche' nessun criterio automatico trova tutto e nessuna ricerca manuale trova
quello che non sai di avere.

**Elenco dei candidati** — `/apparecchi/duplicati`: confronta gli apparecchi della
struttura e propone le coppie sospette, con i conteggi di manutenzioni e verifiche di
ciascuna.

```
Possibili duplicati — Clinica Sant'Anna            12 coppie

  REXXAM OZY    R-00015     Sala visite 1   3 manut. 1 verif.
  REXXAM OZY    R00015      Sala visite 1   1 manut. 2 verif.
  → matricola quasi identica, stesso modello        [Confronta]
```

**Fusione manuale** — dalla scheda di un apparecchio, "fondi con...", cercando l'altro.

---

## Architettura

Modulo `fusione_service.py`, stesso principio di `report_service.py` e
`struttura_service.py`: niente Flask, niente `g`.

```python
candidati_duplicati(righe) -> list[Coppia]
fondi_apparecchi(conn, id_principale, id_scartato, valori, interventi_scartati) -> dict
```

`candidati_duplicati` e' una **funzione pura** su una lista di dizionari: nessuna query,
quindi si prova con dieci righe in memoria invece che con un database popolato. I criteri
girano in Python e non in SQL perche' SQLite non ha una distanza fra stringhe, e su
qualche migliaio di apparecchi il confronto in memoria costa nulla.

Tre criteri, ognuno con la propria etichetta nel risultato — chi guarda l'elenco deve
sapere *perche'* due schede gli vengono proposte:

| Criterio | Esempio |
|---|---|
| Matricola uguale una volta normalizzata (maiuscole, via i non alfanumerici) | `R-00015` ≡ `r00015` |
| Una matricola contenuta nell'altra | `MON-1` ⊂ `MON-1/A` |
| Stesso modello e stessa ubicazione, matricole a distanza 1 | `MON-1` / `MON-l` |

---

## Il risultato della fusione

### Scelta campo per campo

Si sceglie prima quale scheda sopravvive, poi per ogni campo diverso quale valore tenere.

```
Scheda che sopravvive:  (o) R-00015   ( ) R00015

Campi diversi:
  Matricola     (o) R-00015          ( ) R00015
  Marca         (o) REXXAM           ( ) Rexxam
  Anno          ( ) --vuoto--        (o) 2019      <- preselezionato
  Ubicazione    (o) Sala visite 1    ( ) Sala 1
  Note          (o) --vuoto--        ( ) Rev. 2024
```

Preselezionato il valore della scheda principale, **tranne dove e' vuoto e l'altra ha un
valore**: li' vince l'altra. Nel caso comune basta confermare, e non si perde il dato che
solo la scheda scartata aveva.

### La scheda principale conserva il proprio id

QR code stampati e attaccati sull'apparecchio, link salvati e riferimenti esterni restano
validi. E' il motivo per cui la scelta di quale sopravvive non e' indifferente e va fatta
consapevolmente, non dedotta dalla data di creazione.

### Cosa si sposta

`manutenzioni`, `verifiche`, `documenti`, `accessori`, e i riferimenti
`import_preview.apparecchio_match_id`.

**Nessun file si sposta.** Gli allegati stanno in `uploads/strutture/<id>/<tipo>/`, non in
cartelle per apparecchio (`models.upload_subdir()`): fondendo due schede della stessa
struttura cambia solo la riga che referenzia il file. L'unica eccezione e' `foto_path`,
che e' un campo della scheda e segue la scelta campo per campo.

### Interventi che sembrano lo stesso

Confluiscono tutti. La pagina di conferma elenca le coppie con stessa data e stesso tipo
— tipiche di un documento importato due volte — con una casella per scartarne una.
Predefinito: si tiene tutto.

```
Interventi che sembrano duplicati:

  [ ] Manutenzione preventiva  12/03/2026
      A: verbale_2026_03.pdf     B: verbale_2026_03.pdf

  [ ] Verifica elettrica       08/11/2025
      A: (nessun file)           B: verifica_nov.pdf
      -> attenzione: solo B ha il verbale allegato
```

Preservare e' il comportamento automatico, scartare e' una scelta esplicita. E chi decide
vede se erano davvero lo stesso intervento: due interventi nello stesso giorno sullo
stesso apparecchio possono essere legittimamente due.

Quando una sola delle due copie ha il verbale allegato, la riga lo dice: scartare quella
sbagliata butterebbe via l'unico documento.

---

## Vincoli e rifiuti

- Entrambe le schede devono passare `apparecchio_accessibile()` per l'utente: niente
  fusioni fra strutture diverse, ne' fra divisioni non sue.
- Se i valori scelti facessero collidere la scheda risultante con un **terzo** apparecchio
  (`UNIQUE(struttura_id, modello, matricola)`), l'operazione viene rifiutata con un
  messaggio che nomina il terzo, invece di fallire con un errore di database.
- Permessi: `admin`, `tecnico`, `superadmin`. Non `utente`: la fusione cancella una
  scheda, e un utente non puo' nemmeno dismetterne una.
- Tutto in una transazione sola.

---

## Tracciabilita'

La fusione e' **definitiva**. Nel registro attivita' finisce la scheda scartata per
intero, campo per campo, cosi' ricostruirla a mano resta possibile:

```
29/07/2026  admin  fusione  apparecchi  id=142
Fusi "REXXAM OZY R00015" (id 187) in "REXXAM OZY R-00015" (id 142).
Scheda scartata: matricola=R00015 marca=Rexxam modello=OZY anno=2019
  ubicazione="Sala 1" note="Rev. 2024" stato=funzionante
Spostati: 1 manutenzione, 2 verifiche, 1 documento
Scartati: 1 intervento duplicato (verifica 08/11/2025)
Valori scelti dalla scartata: anno, note
```

Nessun annullamento dall'interfaccia: richiederebbe di conservare lo stato precedente e
di decidere cosa fare se nel frattempo la scheda risultante e' stata modificata o ha
ricevuto nuovi interventi. La pagina di conferma mostra tutto prima di procedere.

---

## Test

Quello che conta: dopo la fusione il numero di manutenzioni, verifiche, documenti e
accessori sulla scheda risultante e' la somma dei due, meno gli scartati esplicitamente.
E' l'unica asserzione che distingue "ha fuso" da "ha fuso senza perdere niente".

Poi:

- nessun `verbale_path` o `documento_path` punta piu' a un file inesistente;
- un terzo apparecchio della stessa struttura non e' toccato;
- rifiuto fra strutture diverse e fra divisioni non assegnate;
- rifiuto su collisione con un terzo apparecchio, con il messaggio che lo nomina;
- `candidati_duplicati` **non** propone due apparecchi legittimamente distinti con
  matricole simili (il falso positivo e' il modo in cui questa funzione fa danni: fa
  fondere due macchine diverse);
- il registro contiene tutti i campi della scheda scartata.

---

## Da progettare prima di implementare

**Spostamento di apparecchi fra strutture.** Condivide con la fusione la primitiva che
sposta un apparecchio con tutti i suoi figli, ma ha i suoi casi limite: il vincolo
`UNIQUE(struttura_id, modello, matricola)` sulla struttura di destinazione, la mappatura
delle divisioni (quella di partenza non esiste nella destinazione), e — a differenza
della fusione — **i file si spostano davvero**, perche' cambia il prefisso
`uploads/strutture/<id>/`.

Va brainstormato a parte e aggiunto a questa spec prima di scrivere il piano della 2.7.
