# MedInventory v2 — Documentazione REST API v1

## 1. Panoramica

| Attributo | Valore |
|---|---|
| Base URL | `/api/v1` |
| Autenticazione | Bearer token (header `Authorization`) |
| Formato risposta | JSON (`Content-Type: application/json`) |
| Encoding | UTF-8 |
| Rate limiting | Nessuno (distribuzione intranet LAN) |
| Scope disponibili | `read`, `read write` |

Tutti gli endpoint sono **scoped alla struttura** del token utilizzato: ogni richiesta restituisce solo i dati appartenenti alla struttura associata al token.

---

## 2. Autenticazione

### Ottenere un token

I token API si gestiscono dall'interfaccia web con accesso **superadmin**:

1. Accedere all'applicazione come superadmin.
2. Navigare in **Strutture** → selezionare la struttura → **Token API**.
3. Compilare il form: nome descrittivo, scope (`read` oppure `read write`), data di scadenza opzionale.
4. Premere **Crea token**.
5. Copiare il token mostrato nel messaggio flash: **viene visualizzato una sola volta**.

### Usare il token

Includere il token nell'header HTTP di ogni richiesta:

```
Authorization: Bearer <token>
```

### Errori di autenticazione e autorizzazione

| Codice | Causa |
|---|---|
| `401` | Header `Authorization` assente oppure token non valido / scaduto / struttura disattivata |
| `403` | Token valido ma scope insufficiente (operazione di scrittura con token `read`) |
| `404` | Risorsa non trovata nella struttura del token |

---

## 3. Paginazione

Tutti gli endpoint che restituiscono liste supportano la paginazione tramite i parametri query `page` e `per_page`.

### Parametri

| Parametro | Tipo | Default | Massimo | Descrizione |
|---|---|---|---|---|
| `page` | intero | `1` | — | Numero di pagina (base 1) |
| `per_page` | intero | `50` | `200` | Elementi per pagina |

### Formato risposta paginata

```json
{
  "dati": [ ... ],
  "paginazione": {
    "pagina": 1,
    "per_pagina": 50,
    "totale": 143
  }
}
```

> Nota: il numero totale di pagine si calcola come `ceil(totale / per_pagina)`.

---

## 4. Endpoint

### 4.1 `GET /api/v1/apparecchi`

Restituisce l'elenco degli apparecchi elettromedicali attivi della struttura (esclusi i dismessi).

**Scope richiesto:** `read`

**Parametri query**

| Parametro | Tipo | Default | Descrizione |
|---|---|---|---|
| `page` | intero | `1` | Pagina |
| `per_page` | intero | `50` | Elementi per pagina (max 200) |

**Risposta di successo — 200 OK**

```json
{
  "dati": [
    {
      "id": 42,
      "descrizione": "Elettrobisturi",
      "marca": "Erbe",
      "modello": "VIO 300 D",
      "matricola": "SN-20240101",
      "numero_inventario": "INV-0042",
      "stato": "funzionante",
      "ubicazione": "Sala Operatoria 1",
      "divisione": "Chirurgia"
    }
  ],
  "paginazione": {
    "pagina": 1,
    "per_pagina": 50,
    "totale": 87
  }
}
```

**Errori possibili**

| Codice | Descrizione |
|---|---|
| `401` | Token mancante o non valido |

---

### 4.2 `GET /api/v1/apparecchi/<id>`

Restituisce il dettaglio completo di un singolo apparecchio.

**Scope richiesto:** `read`

**Parametri path**

| Parametro | Tipo | Descrizione |
|---|---|---|
| `id` | intero | ID dell'apparecchio |

**Risposta di successo — 200 OK**

```json
{
  "id": 42,
  "descrizione": "Elettrobisturi",
  "marca": "Erbe",
  "modello": "VIO 300 D",
  "matricola": "SN-20240101",
  "numero_inventario": "INV-0042",
  "stato": "funzionante",
  "ubicazione": "Sala Operatoria 1",
  "divisione_id": 3,
  "divisione": "Chirurgia",
  "struttura_id": 1,
  "created_at": "2024-01-15 10:30:00"
}
```

**Errori possibili**

| Codice | Descrizione |
|---|---|
| `401` | Token mancante o non valido |
| `404` | Apparecchio non trovato nella struttura del token |

---

### 4.3 `GET /api/v1/scadenze`

Restituisce le prossime scadenze di manutenzione dalla vista `prossime_scadenze`, ordinate per data crescente. Include tutti gli apparecchi della struttura con scadenze pianificate.

**Scope richiesto:** `read`

**Parametri query**

| Parametro | Tipo | Default | Descrizione |
|---|---|---|---|
| `page` | intero | `1` | Pagina |
| `per_page` | intero | `50` | Elementi per pagina (max 200) |

**Campi `priorita` possibili:** `scaduto`, `urgente`, `attenzione`, `avviso`, `ok`

**Risposta di successo — 200 OK**

```json
{
  "dati": [
    {
      "apparecchio_id": 42,
      "descrizione": "Elettrobisturi",
      "marca": "Erbe",
      "modello": "VIO 300 D",
      "matricola": "SN-20240101",
      "tipo_manutenzione": "preventiva",
      "prossima_scadenza": "2026-04-15",
      "giorni_rimasti": 13,
      "priorita": "attenzione"
    }
  ],
  "paginazione": {
    "pagina": 1,
    "per_pagina": 50,
    "totale": 22
  }
}
```

**Errori possibili**

| Codice | Descrizione |
|---|---|
| `401` | Token mancante o non valido |

---

### 4.4 `GET /api/v1/manutenzioni`

Restituisce la lista degli interventi di manutenzione registrati per la struttura, ordinati per data decrescente.

**Scope richiesto:** `read`

**Parametri query**

| Parametro | Tipo | Default | Descrizione |
|---|---|---|---|
| `page` | intero | `1` | Pagina |
| `per_page` | intero | `50` | Elementi per pagina (max 200) |

**Risposta di successo — 200 OK**

```json
{
  "dati": [
    {
      "id": 101,
      "tipo": "preventiva",
      "data_intervento": "2026-03-10",
      "prossima_scadenza": "2027-03-10",
      "tecnico_ditta": "Tecnoservice Srl",
      "esito": "positivo",
      "costo": 250.00,
      "apparecchio": "Elettrobisturi",
      "matricola": "SN-20240101"
    }
  ],
  "paginazione": {
    "pagina": 1,
    "per_pagina": 50,
    "totale": 310
  }
}
```

**Errori possibili**

| Codice | Descrizione |
|---|---|
| `401` | Token mancante o non valido |

---

### 4.5 `POST /api/v1/manutenzioni`

Crea un nuovo record di manutenzione per un apparecchio della struttura.

**Scope richiesto:** `read write`

**Content-Type:** `application/json`

**Body JSON**

| Campo | Tipo | Obbligatorio | Valori ammessi / Note |
|---|---|---|---|
| `apparecchio_id` | intero | SI | ID dell'apparecchio (deve appartenere alla struttura del token) |
| `tipo` | stringa | SI | `preventiva`, `correttiva`, `verifica`, `calibrazione` |
| `data_intervento` | stringa | SI | Formato `YYYY-MM-DD` |
| `prossima_scadenza` | stringa | no | Formato `YYYY-MM-DD` |
| `periodicita_giorni` | intero | no | Periodicità in giorni per calcolo automatico scadenza |
| `tecnico_ditta` | stringa | no | Nome tecnico o ditta esecutrice |
| `descrizione` | stringa | no | Note sull'intervento |
| `esito` | stringa | no | Esito dell'intervento (testo libero) |
| `costo` | numero | no | Costo in euro |

**Esempio body**

```json
{
  "apparecchio_id": 42,
  "tipo": "preventiva",
  "data_intervento": "2026-04-02",
  "prossima_scadenza": "2027-04-02",
  "periodicita_giorni": 365,
  "tecnico_ditta": "Tecnoservice Srl",
  "descrizione": "Manutenzione programmata annuale",
  "esito": "positivo",
  "costo": 320.00
}
```

**Risposta di successo — 201 Created**

```json
{
  "id": 215,
  "messaggio": "Manutenzione creata"
}
```

**Errori possibili**

| Codice | Descrizione |
|---|---|
| `400` | Campi obbligatori mancanti, tipo non valido, o formato data errato |
| `401` | Token mancante o non valido |
| `403` | Token senza scope `write` |
| `404` | `apparecchio_id` non trovato nella struttura del token |

**Esempio risposta 400**

```json
{
  "errore": "Campi mancanti: tipo, data_intervento"
}
```

```json
{
  "errore": "tipo deve essere uno di: ('preventiva', 'correttiva', 'verifica', 'calibrazione')"
}
```

```json
{
  "errore": "data_intervento deve essere nel formato YYYY-MM-DD"
}
```

---

## 5. Codici di errore

| Codice HTTP | Significato |
|---|---|
| `200 OK` | Richiesta elaborata con successo |
| `201 Created` | Risorsa creata con successo (risposta a POST) |
| `400 Bad Request` | Dati della richiesta non validi (campi mancanti, formato errato, valore non ammesso) |
| `401 Unauthorized` | Token Bearer assente, non valido, scaduto, o struttura disattivata |
| `403 Forbidden` | Token valido ma scope insufficiente per l'operazione richiesta |
| `404 Not Found` | Risorsa non trovata o non appartenente alla struttura del token |

Il corpo delle risposte di errore segue sempre il formato:

```json
{
  "errore": "Descrizione del problema"
}
```

---

## 6. Esempi pratici

Sostituire `<TOKEN>` con il Bearer token reale e `<HOST>` con l'indirizzo del server (es. `http://192.168.1.10:5000`).

### Lista apparecchi (prima pagina)

```bash
curl -s \
  -H "Authorization: Bearer <TOKEN>" \
  "<HOST>/api/v1/apparecchi"
```

### Lista apparecchi (pagina 2, 20 elementi)

```bash
curl -s \
  -H "Authorization: Bearer <TOKEN>" \
  "<HOST>/api/v1/apparecchi?page=2&per_page=20"
```

### Dettaglio singolo apparecchio

```bash
curl -s \
  -H "Authorization: Bearer <TOKEN>" \
  "<HOST>/api/v1/apparecchi/42"
```

### Scadenze imminenti

```bash
curl -s \
  -H "Authorization: Bearer <TOKEN>" \
  "<HOST>/api/v1/scadenze"
```

### Lista manutenzioni

```bash
curl -s \
  -H "Authorization: Bearer <TOKEN>" \
  "<HOST>/api/v1/manutenzioni"
```

### Crea una nuova manutenzione

```bash
curl -s -X POST \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "apparecchio_id": 42,
    "tipo": "preventiva",
    "data_intervento": "2026-04-02",
    "prossima_scadenza": "2027-04-02",
    "periodicita_giorni": 365,
    "tecnico_ditta": "Tecnoservice Srl",
    "descrizione": "Manutenzione programmata annuale",
    "esito": "positivo",
    "costo": 320.00
  }' \
  "<HOST>/api/v1/manutenzioni"
```

### Crea manutenzione (campi minimi obbligatori)

```bash
curl -s -X POST \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "apparecchio_id": 42,
    "tipo": "correttiva",
    "data_intervento": "2026-04-02"
  }' \
  "<HOST>/api/v1/manutenzioni"
```

---

## 7. Gestione token

### Creare un token

1. Accedere come **superadmin**.
2. Navigare in **Strutture** → selezionare la struttura desiderata → scheda **Token API**.
3. Compilare il form:
   - **Nome**: etichetta descrittiva per identificare il token (es. `Integrazione GMAO`, `Dashboard BI`).
   - **Scope**: `read` (sola lettura) oppure `read write` (lettura e scrittura).
   - **Scadenza** *(opzionale)*: data di scadenza nel formato `YYYY-MM-DD`; lasciare vuoto per token senza scadenza.
4. Premere **Crea token**.

Il token in chiaro viene mostrato **una sola volta** nel messaggio di conferma. Copiarlo e conservarlo in un luogo sicuro (password manager o variabile d'ambiente del sistema client). L'applicazione memorizza solo l'hash SHA-256 del token e non e' in grado di mostrarlo nuovamente.

### Visualizzare i token attivi

La lista nella scheda **Token API** mostra per ogni token: nome, scope, data creazione, data scadenza, data ultimo utilizzo e stato (attivo / revocato). Il valore del token non e' mai visualizzato dopo la creazione.

### Revocare un token

Dalla lista dei token, premere **Revoca** accanto al token da disattivare. L'operazione e' immediata e irreversibile: il token non potra' piu' essere utilizzato per autenticarsi. Se necessario, creare un nuovo token in sostituzione.

### Note sulla sicurezza

- Trattare i token API alla stregua di password: non includerli in repository di codice sorgente, log applicativi o URL.
- Preferire token con scope `read` quando non e' necessaria la scrittura.
- Impostare una data di scadenza per token usati in contesti temporanei o da terze parti.
- In caso di compromissione sospetta, revocare immediatamente il token dalla UI.
