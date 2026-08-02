# MedInventory 2.6.2 — La struttura nella barra di navigazione

**Versione target:** 2.6.2
**Data:** 2026-08-02
**Stato:** progettato e approvato.

Terza miglioria della 2.6.2, indipendente dalle altre due (`cancellazione-utenti`,
`fusione-punti-ingresso`): tocca `templates/base.html` e `auth.py`.

---

## Il problema

In alto a destra, su una struttura con una sola divisione, compaiono due nomi uguali.
Non e' un caso: alla creazione di una struttura la divisione predefinita viene creata
**con lo stesso nome della struttura** (`strutture_bp._crea_divisione_predefinita`).

E un menu a tendina che offre una sola voce non e' una scelta: e' un elemento che costa
un clic per scoprire che non fa niente.

### Cosa si vede davvero oggi

Verificando chi mostra cosa, il quadro e' diverso da come appare:

| Ruolo | Nome della struttura | Menu divisioni |
|---|---|---|
| superadmin (multi) | si', nel selettore di struttura | si' |
| tecnico su piu' strutture (multi) | si', nel suo selettore | si' |
| tecnico su una sola struttura | **no** — il selettore compare solo con `strutture_list|length > 1` | si' |
| admin | **mai** | si' |
| utente | **mai** | si' |
| chiunque, modalita' single-struttura | **mai** — il selettore e' dentro `{% if not single_struttura %}` | si' |

Quindi per un admin quello che si vede **non e'** «struttura e divisione»: e' **solo la
divisione**, che sembra la struttura perche' porta lo stesso nome. Se una struttura avesse
una divisione sola chiamata diversamente — «Casa di Cura Bianchi» con dentro «Reparto
Unico» — l'admin leggerebbe «Reparto Unico», e in che struttura sta lavorando non sarebbe
scritto da nessuna parte.

---

## La regola

**Quando l'utente ha una sola divisione accessibile, il menu delle divisioni sparisce e al
suo posto compare il nome della struttura, come testo semplice.**

La condizione e' «una sola divisione **accessibile all'utente**», non «una sola divisione
nella struttura»: un utente semplice assegnato a una divisione dentro una struttura da
dieci ha la stessa non-scelta.

- **Una sola divisione accessibile** → niente menu; nome della struttura come testo.
- **Chi ha gia' un selettore di struttura** (superadmin, tecnico su piu' strutture) → il
  nome e' gia' li', non si ripete: si toglie soltanto il menu divisioni.
- **Piu' divisioni accessibili** → tutto come oggi.
- **Modalita' single-struttura** → il nome si mostra lo stesso. E' il nome del posto in
  cui si lavora, e su uno schermo condiviso in reparto dice a chi passa cosa sta
  guardando.

Il nome viene da `g_struttura`, gia' disponibile nei template (`app.py:302`) e popolato
anche per admin e utenti, non solo per il superadmin che impersona.

---

## La trappola: le divisioni disattivate

Questa e' la parte che va fatta bene, o la modifica fa sparire dati dalla vista.

Per `admin` e `tecnico` il menu non offre solo le divisioni: offre anche **«Tutte le
divisioni»**, che non e' la stessa cosa. In `models.filtro_divisione()`:

- con una divisione selezionata il filtro e' `divisione_id = ?`;
- con «tutte» diventa `struttura_id = ?`.

I due insiemi **non coincidono** quando esistono divisioni **disattivate** che contengono
ancora apparecchi: `g.divisioni` elenca le sole divisioni attive (`auth.py`, `WHERE
attiva = 1`), ma gli apparecchi delle divisioni disattivate esistono e appartengono alla
struttura.

Una struttura con una divisione attiva e due disattivate piene di macchine ha oggi un
admin che vede una sola voce nel menu ma puo' scegliere «Tutte» e vedere tutto. Togliendo
il menu senza altro, quell'admin resta bloccato sulla vista ristretta **senza piu' alcun
comando per allargarla**, e gli apparecchi delle divisioni disattivate spariscono in
silenzio.

**Quindi:** quando `admin` o `tecnico` hanno una sola divisione accessibile, l'ambito
diventa quello di **struttura** — cioe' esattamente quello che «Tutte le divisioni»
darebbe. Si toglie il comando e si tiene il comportamento piu' ampio, non il piu' stretto.

**Per l'utente semplice non cambia nulla:** il suo ambito resta l'insieme delle divisioni
che gli sono assegnate. Non ha «Tutte le divisioni» nel menu e non deve guadagnarlo qui.

E' la stessa radice del difetto corretto nella 2.6.1, dove l'elenco dei duplicati era
cieco fra reparti perche' un admin che non aveva ancora scelto riceveva una divisione
specifica invece dell'intera struttura.

---

## Test

- **Un admin con una sola divisione** non vede il menu, vede il nome della struttura, e
  **vede gli apparecchi di una divisione disattivata** della sua struttura. Quest'ultima
  e' l'asserzione che conta: senza, la modifica passerebbe restringendo la vista.
- **Un admin con due divisioni** vede il menu come oggi, «Tutte le divisioni» compresa.
- **Un utente semplice con una sola divisione** non vede il menu, vede il nome della
  struttura, e **continua a non vedere** gli apparecchi delle altre divisioni: il suo
  ambito non si allarga.
- **Un superadmin che impersona** non vede il nome due volte.
- **In modalita' single-struttura** il nome compare.
- Il nome mostrato e' quello della **struttura**, non quello della divisione: va provato
  con una struttura la cui unica divisione ha un nome diverso, altrimenti i due valori
  coincidono e il test passa qualunque cosa mostri. E' esattamente il modo in cui, in
  questo progetto, sette test si sono rivelati ciechi.

Ogni test va provato **sensibile**: reintrodurre il difetto e verificare che cada.

---

## Fuori ambito

- **Il nome della divisione predefinita uguale a quello della struttura.** E' la causa
  della duplicazione visiva, ma cambiarlo tocca la creazione delle strutture e i dati
  esistenti. Qui si corregge cosa si mostra, non come si chiamano le divisioni.
- **Il selettore di struttura per il tecnico assegnato a una sola struttura**, che oggi
  non compare. Con questa modifica quel tecnico vede comunque il nome, per la via nuova.
