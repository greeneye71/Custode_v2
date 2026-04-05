# UX/UI Improvements — MedInventory v1.4.3

Analisi prodotta il 2026-04-05 tramite revisione Opus dei template HTML e blueprint Python.
Prioritizzata per rapporto impatto/sforzo.

---

## Priorità ALTA

### 1. Tom Select sui `<select>` apparecchio
**File:** `manutenzioni/form.html`, `verifiche/form.html`, `import/email_dettaglio.html`, `import/preview.html`
**Problema:** `<select>` nativo senza ricerca. Con centinaia di apparecchi è impraticabile per il personale.
**Soluzione:** Integrare Tom Select (no jQuery):
```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/tom-select@2/dist/css/tom-select.bootstrap5.min.css">
<script src="https://cdn.jsdelivr.net/npm/tom-select@2/dist/js/tom-select.complete.min.js"></script>
<script>new TomSelect('#apparecchio_id', {create: false});</script>
```
**Sforzo:** Basso

---

### 2. Alert scadenze urgenti in dashboard
**File:** `templates/index.html`
**Problema:** Il badge numerico nella sidebar non è abbastanza visibile; l'utente potrebbe non aprire mai lo scadenzario.
**Soluzione:** Aggiungere un alert dismissabile nella dashboard:
```jinja
{% if scadenze_critiche > 0 %}
<div class="alert alert-danger alert-dismissible fade show" role="alert">
  <i class="bi bi-exclamation-triangle-fill me-2"></i>
  Ci sono <strong>{{ scadenze_critiche }}</strong> scadenze urgenti o scadute.
  <a href="{{ url_for('manutenzioni.scadenzario') }}" class="alert-link ms-2">Vai allo scadenzario →</a>
  <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
</div>
{% endif %}
```
Passare `scadenze_critiche` dal backend (count di `prossime_scadenze` dove `priorita IN ('scaduto','urgente')`).
**Sforzo:** Basso

---

### 3. Admin accessibile da mobile (offcanvas)
**File:** `templates/base.html`
**Problema:** La sidebar è `d-none d-lg-block`. Su tablet/mobile tutta la sezione Admin (Utenti, Divisioni, Configurazione, Backup, Log, Import, Coda Email) è inaccessibile.
**Soluzione:** Trasformare la sidebar in un offcanvas Bootstrap attivabile dall'hamburger già presente nella navbar:
```html
<!-- Cambiare il sidebar div da class="sidebar d-none d-lg-block" a: -->
<div class="offcanvas offcanvas-start d-lg-none" id="sidebarOffcanvas" ...>
  <!-- stessa struttura della sidebar esistente -->
</div>
<!-- La sidebar desktop rimane invariata -->
<div class="sidebar d-none d-lg-flex flex-column" ...>
```
L'hamburger `data-bs-toggle="offcanvas" data-bs-target="#sidebarOffcanvas"` è già in navbar (basta collegarlo).
**Sforzo:** Medio

---

## Priorità MEDIA

### 4. Filtro stato apparecchi ambiguo
**File:** `templates/apparecchi/lista.html` (riga ~53)
**Problema:** Prima opzione del select ha `value=""` con label "Funzionanti", ma subito sotto c'è anche `value="funzionante"`. La differenza è invisibile all'utente.
**Soluzione:** Rinominare l'opzione default in "Tutti (attivi)" o "Tutti gli stati attivi" e rimuovere/chiarire la duplicazione.
**Sforzo:** Basso

---

### 5. Export PDF mancante per manutenzioni
**File:** `templates/manutenzioni/lista.html`
**Problema:** Apparecchi, Verifiche e Scadenzario hanno dropdown Excel+PDF; Manutenzioni ha solo un bottone Excel.
**Soluzione:** Aggiungere l'endpoint export PDF in `export_bp.py` per manutenzioni e uniformare il template al pattern dropdown delle altre sezioni.
**Sforzo:** Medio

---

### 6. Elimina manutenzione dalla lista
**File:** `templates/partials/manutenzioni_table.html`
**Problema:** Il bottone "Elimina" è presente solo nel dettaglio apparecchio, non nella lista manutenzioni. L'utente deve fare un passo in più.
**Soluzione:** Aggiungere bottone elimina con modal di conferma nella tabella, come già fatto per apparecchi.
**Sforzo:** Basso

---

### 7. Indicatore di caricamento HTMX
**File:** `templates/apparecchi/lista.html`, `manutenzioni/lista.html`, `verifiche/lista.html`
**Problema:** Le tabelle si aggiornano silenziosamente con HTMX; nessun feedback visivo durante la richiesta.
**Soluzione:**
```html
<!-- Aggiungere accanto al conteggio risultati: -->
<span class="htmx-indicator spinner-border spinner-border-sm ms-2" role="status"></span>
<!-- Aggiungere agli input filtro: -->
hx-indicator=".htmx-indicator"
```
**Sforzo:** Basso

---

### 8. Breadcrumb di navigazione
**File:** Tutti i form e le pagine di dettaglio
**Problema:** Nessuna sezione ha breadcrumb. In "Modifica Apparecchio" o "Revisione Verbale Email" non è chiaro il percorso.
**Soluzione:** Aggiungere sotto `.page-header` in ogni pagina:
```html
<nav aria-label="breadcrumb" class="mb-3">
  <ol class="breadcrumb small">
    <li class="breadcrumb-item"><a href="{{ url_for('apparecchi.lista') }}">Apparecchi</a></li>
    <li class="breadcrumb-item active">Modifica</li>
  </ol>
</nav>
```
**Sforzo:** Basso

---

### 9. Cambio password perde la navigazione
**File:** `templates/auth/cambio_password.html`
**Problema:** La pagina ha layout standalone completo (non estende `base.html`); l'utente perde tutta la navigazione quando accede volontariamente al cambio password.
**Soluzione:** Usare `extends "base.html"` nel caso `primo_accesso=False`, riservando il layout standalone al flusso obbligatorio del primo accesso.
**Sforzo:** Basso

---

### 10. Ordinamento colonne nelle tabelle
**File:** `templates/partials/*_table.html`
**Problema:** Nessuna tabella indica su quale colonna è ordinata né permette di cambiarla.
**Soluzione:** Aggiungere header cliccabili con `hx-get` e parametri `sort=colonna&dir=asc|desc`, con icona freccia sull'header attivo. Pattern HTMX standard senza JS aggiuntivo.
**Sforzo:** Medio

---

## Priorità BASSA

### 11. Layout pagine admin non uniforme
**File:** `templates/admin/backup.html`, `log_attivita.html`, `sicurezza.html`, `import/email_queue.html`
**Problema:** Queste pagine usano markup diretto invece del pattern `.page-header` usato nel resto dell'app.
**Soluzione:** Uniformare all'header standard con classe `.page-header` e sottotitolo.
**Sforzo:** Basso

---

### 12. Ricerca globale dalla navbar
**File:** `templates/base.html`, nuovo endpoint in `api_bp.py`
**Problema:** Nessuna ricerca cross-sezione. Un operatore deve navigare alla lista e usare i filtri.
**Soluzione:** Input nella navbar con `hx-get="/api/search?q=..."` che restituisce un dropdown di risultati misti (apparecchi + manutenzioni recenti). Bootstrap dropdown già disponibile.
**Sforzo:** Medio

---

### 13. Scheda stampabile per ispezioni ASL
**File:** `templates/apparecchi/dettaglio.html` + nuovo template `scheda_stampa.html`
**Problema:** Il dettaglio ha CSS `@media print` di base ma non una vista compatta con anagrafica + ultima manutenzione + stato verifica.
**Soluzione:** Aggiungere endpoint `/apparecchi/<id>/scheda` con template ottimizzato per stampa. Opzionale: selezione batch dalla lista per stampa multipla.
**Sforzo:** Medio

---

### 14. Upload multiplo documenti
**File:** `templates/apparecchi/dettaglio.html` (sezione documenti), `apparecchi.py` route `upload_documento`
**Problema:** L'input accetta un file alla volta. Per un tecnico con più certificati è laborioso.
**Soluzione:** Aggiungere `multiple` all'input file e adattare il backend con un loop su `request.files.getlist('documento')`.
**Sforzo:** Basso

---

### 15. Drag-and-drop per upload foto
**File:** `templates/apparecchi/dettaglio.html` (sezione foto)
**Problema:** Upload foto tramite `<input type="file">` semplice.
**Soluzione:** Listener `dragover`/`drop` CSS + JS vanilla (~20 righe) per creare una drop zone visiva. Nessuna libreria esterna necessaria.
**Sforzo:** Basso

---

### 16. Scorciatoie da tastiera
**File:** `static/app.js`
**Problema:** Nessuna scorciatoia. Per uso intensivo su desktop LAN sarebbero molto produttive.
**Soluzione:** `document.addEventListener('keydown', ...)` con `Ctrl+N` (nuovo apparecchio), `Ctrl+K` (apri ricerca globale), `Esc` (chiudi modal).
**Sforzo:** Basso

---

## Riepilogo

| # | Intervento | Priorità | Sforzo |
|---|-----------|---------|--------|
| 1 | Tom Select sui select apparecchio | Alta | Basso |
| 2 | Alert scadenze urgenti in dashboard | Alta | Basso |
| 3 | Admin accessibile da mobile (offcanvas) | Alta | Medio |
| 4 | Filtro stato ambiguo | Media | Basso |
| 5 | Export PDF manutenzioni | Media | Medio |
| 6 | Elimina manutenzione dalla lista | Media | Basso |
| 7 | Indicatore caricamento HTMX | Media | Basso |
| 8 | Breadcrumb navigazione | Media | Basso |
| 9 | Cambio password perde navigazione | Media | Basso |
| 10 | Ordinamento colonne tabelle | Media | Medio |
| 11 | Layout admin non uniforme | Bassa | Basso |
| 12 | Ricerca globale navbar | Bassa | Medio |
| 13 | Scheda stampabile ASL | Bassa | Medio |
| 14 | Upload multiplo documenti | Bassa | Basso |
| 15 | Drag-and-drop foto | Bassa | Basso |
| 16 | Scorciatoie da tastiera | Bassa | Basso |
