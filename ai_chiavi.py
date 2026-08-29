"""Mappa unica delle chiavi di configurazione AI.

Una struttura puo' avere le proprie impostazioni AI in `strutture_config`;
quando non le ha deve seguire i default di sistema, che nella configurazione
globale si chiamano `default_*`. Prima della 2.8.3 la corrispondenza fra i due
nomi era ricopiata in cinque punti diversi (ai_service, models, admin,
strutture_bp, manutenzione_lib.stato) e in nessuno di essi era completa: il
runtime di struttura leggeva solo `strutture_config` e il fallback globale di
`get_struttura_config()` cercava lo stesso nome della chiave di struttura, che
globalmente non esiste mai. Funzionava soltanto perche' i default venivano
copiati dentro `strutture_config` alla creazione della struttura, quindi una
modifica successiva al default non raggiungeva nessuno.

Qui la corrispondenza sta scritta una volta sola e la risoluzione ha un ordine
unico:

    override della struttura  ->  `default_*` globale  ->  chiave legacy  ->  default

La chiave legacy e' il nome senza prefisso (`ai_provider`, `anthropic_api_key`,
...), che le installazioni antecedenti alla 2.6 hanno ancora in
`config.local.json`: si continua a leggerla per non spegnere l'AI a chi
aggiorna, ma non la si scrive piu'.

`ai_local_url_allowlist` non compare in questa mappa apposta: e' politica di
sistema (contenimento SSRF, vedi `sicurezza_url.py`) e si legge sempre e solo
dalla configurazione globale, altrimenti l'admin di una struttura potrebbe
allargarsi il limite che lo vincola.

Modulo senza Flask: lo importa anche `manutenzione_lib/stato.py`, che deve
poter fotografare un'installazione diversa da quella in esecuzione.
"""

# chiave per-struttura -> chiave nella configurazione globale di sistema
CHIAVI_AI = {
    'ai_provider':       'default_ai_provider',
    'anthropic_api_key': 'default_anthropic_api_key',
    'gemini_api_key':    'default_gemini_api_key',
    'openai_api_key':    'default_openai_api_key',
    'ai_import_model':   'default_ai_import_model',
    'ai_email_model':    'default_ai_email_model',
    'ai_local_base_url': 'default_ai_local_base_url',
    'ai_local_model':    'default_ai_local_model',
}

# Valore usato quando ne' la struttura ne' la configurazione globale dicono
# nulla. Sono i default storici del programma, spostati qui dai punti che
# prima li ripetevano.
DEFAULT_AI = {
    'ai_provider':       'anthropic',
    'anthropic_api_key': '',
    'gemini_api_key':    '',
    'openai_api_key':    '',
    'ai_import_model':   'claude-sonnet-4-20250514',
    'ai_email_model':    'claude-haiku-4-5-20251001',
    'ai_local_base_url': 'http://localhost:11434',
    'ai_local_model':    '',
}

# chiave globale -> chiave per-struttura, per chi parte dal nome `default_*`
CHIAVI_GLOBALI = {globale: struttura for struttura, globale in CHIAVI_AI.items()}

_ASSENTE = object()


def e_chiave_ai(chiave):
    """Vero se la chiave e' una delle impostazioni AI per-struttura."""
    return chiave in CHIAVI_AI


def chiave_globale(chiave_struttura):
    """Nome `default_*` corrispondente, o None se non e' una chiave AI."""
    return CHIAVI_AI.get(chiave_struttura)


def _valorizzata(valore):
    """Una chiave svuotata vale come assente: chi cancella un campo
    dall'interfaccia vuole tornare al default, non spegnere l'AI."""
    return valore is not None and valore != ''


def valore_globale(config, chiave_struttura, default=_ASSENTE):
    """Risolve una chiave AI nella sola configurazione globale.

    Ordine: `default_<chiave>`, poi la chiave legacy senza prefisso, poi il
    default indicato dal chiamante, poi quello di `DEFAULT_AI`.
    """
    config = config or {}
    globale = CHIAVI_AI.get(chiave_struttura)
    if globale is not None:
        valore = config.get(globale)
        if _valorizzata(valore):
            return valore
    valore = config.get(chiave_struttura)
    if _valorizzata(valore):
        return valore
    if default is not _ASSENTE:
        return default
    return DEFAULT_AI.get(chiave_struttura)


def risolvi(chiave_struttura, valore_struttura, config, default=_ASSENTE):
    """Risoluzione completa: override di struttura, altrimenti il globale.

    `valore_struttura` e' il valore letto da `strutture_config` (None se la
    riga non c'e'). L'ordine e' quello documentato nel modulo.
    """
    if _valorizzata(valore_struttura):
        return valore_struttura
    return valore_globale(config, chiave_struttura, default)


def config_ai_globale(config):
    """Tutte le impostazioni AI risolte sulla sola configurazione globale."""
    return {chiave: valore_globale(config, chiave) for chiave in CHIAVI_AI}
