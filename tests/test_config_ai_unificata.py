"""M02 — una sola semantica per le chiavi AI (audit del 28/08/2026).

Il rilievo: la stessa impostazione aveva due nomi (`ai_provider` per la
struttura, `default_ai_provider` nella configurazione di sistema) e la
corrispondenza fra i due era ricopiata in cinque punti, in nessuno completa.
Il fallback globale di `get_struttura_config()` cercava il nome della chiave di
struttura, che globalmente non esiste, quindi non trovava mai niente; la cosa
non si vedeva solo perche' alla creazione di una struttura i default venivano
copiati dentro `strutture_config`. La copia rendeva ogni struttura un override
permanente: cambiare il default di sistema non raggiungeva piu' nessuno.

Ora la mappa sta solo in `ai_chiavi.py` e l'ordine di risoluzione e' unico:

    override della struttura -> `default_*` globale -> chiave legacy -> default

`ai_local_url_allowlist` resta fuori dalla mappa apposta: e' politica di
sistema (contenimento SSRF) e non deve poter essere allargata da un admin di
struttura.
"""
import pytest
from werkzeug.security import generate_password_hash

import ai_chiavi


# ---------------------------------------------------------------------------
# L'ordine di risoluzione, senza database
# ---------------------------------------------------------------------------

def test_l_override_della_struttura_vince():
    valore = ai_chiavi.risolvi('ai_provider', 'gemini',
                               {'default_ai_provider': 'openai'})
    assert valore == 'gemini'


def test_senza_override_si_usa_il_default_di_sistema():
    """E' il caso che prima non funzionava: il nome globale della chiave non e'
    quello di struttura, e chi cercava `ai_provider` nella config globale non
    trovava nulla."""
    assert ai_chiavi.risolvi('ai_provider', None,
                             {'default_ai_provider': 'openai'}) == 'openai'


def test_la_chiave_legacy_senza_prefisso_e_ancora_letta():
    """Le installazioni antecedenti alla 2.6 hanno ancora `anthropic_api_key`
    in config.local.json e il runtime la usava davvero: continuare a leggerla
    e' quello che evita di spegnere l'AI a chi aggiorna."""
    assert ai_chiavi.risolvi('anthropic_api_key', None,
                             {'anthropic_api_key': 'sk-vecchia'}) == 'sk-vecchia'


def test_il_default_con_prefisso_vince_sulla_chiave_legacy():
    config = {'default_anthropic_api_key': 'sk-nuova',
              'anthropic_api_key': 'sk-vecchia'}
    assert ai_chiavi.risolvi('anthropic_api_key', None, config) == 'sk-nuova'


def test_un_override_vuoto_vale_come_assente():
    """Svuotare un campo dall'interfaccia significa tornare al default, non
    spegnere l'AI della struttura."""
    assert ai_chiavi.risolvi('ai_provider', '',
                             {'default_ai_provider': 'openai'}) == 'openai'


def test_senza_niente_resta_il_default_di_programma():
    assert ai_chiavi.risolvi('ai_provider', None, {}) == 'anthropic'
    assert ai_chiavi.risolvi('ai_import_model', None, {}) == \
        ai_chiavi.DEFAULT_AI['ai_import_model']


def test_l_allowlist_degli_url_locali_non_e_una_chiave_di_struttura():
    """Politica di sistema: se entrasse nella mappa, un admin di struttura
    potrebbe scriversi in `strutture_config` l'allowlist che lo vincola."""
    assert not ai_chiavi.e_chiave_ai('ai_local_url_allowlist')
    assert 'ai_local_url_allowlist' not in ai_chiavi.CHIAVI_AI
    assert 'ai_local_url_allowlist' not in ai_chiavi.DEFAULT_AI


# ---------------------------------------------------------------------------
# La lettura per-struttura
# ---------------------------------------------------------------------------

@pytest.fixture
def dati(app):
    from models import execute
    with app.app_context():
        s = execute("INSERT INTO strutture (nome,codice,attiva) "
                    "VALUES ('Clinica A','A',1)").lastrowid
        hash_pw = generate_password_hash('Passw0rd!')
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,primo_accesso) "
                "VALUES ('super@x.it',?,'S','S','superadmin',0)", (hash_pw,))
    return {'s': s}


def entra(client, email):
    client.post('/login', data={'email': email, 'password': 'Passw0rd!'})


def test_una_struttura_senza_riga_segue_il_default_di_sistema(app, dati):
    from models import get_struttura_config
    with app.app_context():
        app.config['APP_CONFIG']['default_ai_provider'] = 'openai'
        assert get_struttura_config(dati['s'], 'ai_provider') == 'openai'


def test_una_riga_vuota_non_conta_come_override(app, dati):
    from models import execute, get_struttura_config
    with app.app_context():
        execute("INSERT INTO strutture_config (struttura_id, chiave, valore) "
                "VALUES (?, 'ai_provider', '')", (dati['s'],))
        app.config['APP_CONFIG']['default_ai_provider'] = 'openai'
        assert get_struttura_config(dati['s'], 'ai_provider') == 'openai'


def test_una_riga_valorizzata_resta_un_override(app, dati):
    from models import execute, get_struttura_config
    with app.app_context():
        execute("INSERT INTO strutture_config (struttura_id, chiave, valore) "
                "VALUES (?, 'ai_provider', 'gemini')", (dati['s'],))
        app.config['APP_CONFIG']['default_ai_provider'] = 'openai'
        assert get_struttura_config(dati['s'], 'ai_provider') == 'gemini'


def test_la_creazione_non_copia_i_default_dentro_strutture_config(client, app, dati):
    """La copia alla creazione era un override a tutti gli effetti: congelava
    la struttura sui valori del giorno in cui era stata creata."""
    from models import query_all, query_one
    entra(client, 'super@x.it')
    app.config['APP_CONFIG']['default_ai_provider'] = 'openai'
    risposta = client.post('/strutture/nuova', data={'nome': 'Clinica Nuova'},
                           follow_redirects=True)
    assert risposta.status_code == 200
    with app.app_context():
        nuova = query_one("SELECT id FROM strutture WHERE nome = 'Clinica Nuova'")
        assert nuova is not None
        righe = query_all("SELECT chiave FROM strutture_config WHERE struttura_id = ?",
                          (nuova['id'],))
        chiavi = {r['chiave'] for r in righe}
        assert chiavi & set(ai_chiavi.CHIAVI_AI) == set()


def test_il_default_cambiato_dopo_la_creazione_raggiunge_la_struttura(client, app, dati):
    """Il difetto che il rilievo descrive: con i default copiati alla
    creazione, cambiare la configurazione di sistema non arrivava a nessuna
    struttura gia' esistente."""
    from models import get_struttura_config, query_one
    entra(client, 'super@x.it')
    app.config['APP_CONFIG']['default_ai_provider'] = 'anthropic'
    client.post('/strutture/nuova', data={'nome': 'Clinica Nuova'},
                follow_redirects=True)
    app.config['APP_CONFIG']['default_ai_provider'] = 'gemini'
    with app.app_context():
        nuova = query_one("SELECT id FROM strutture WHERE nome = 'Clinica Nuova'")
        assert get_struttura_config(nuova['id'], 'ai_provider') == 'gemini'


# ---------------------------------------------------------------------------
# Il servizio AI legge la stessa cosa dell'interfaccia
# ---------------------------------------------------------------------------

def test_il_servizio_ai_risolve_i_default_di_sistema(app, dati):
    from ai_service import _get_ai_config
    with app.app_context():
        app.config['APP_CONFIG'].update({
            'default_ai_provider': 'openai',
            'default_openai_api_key': 'sk-globale',
            'default_ai_import_model': 'gpt-test',
        })
        cfg = _get_ai_config(struttura_id=dati['s'])
    assert cfg['provider'] == 'openai'
    assert cfg['openai_api_key'] == 'sk-globale'
    assert cfg['model_import'] == 'gpt-test'


def test_il_servizio_ai_legge_anche_la_chiave_legacy(app, dati):
    from ai_service import _get_ai_config
    with app.app_context():
        app.config['APP_CONFIG'].pop('default_anthropic_api_key', None)
        app.config['APP_CONFIG']['anthropic_api_key'] = 'sk-vecchia'
        cfg = _get_ai_config(struttura_id=dati['s'])
    assert cfg['api_key'] == 'sk-vecchia'


def test_l_override_della_struttura_arriva_al_servizio_ai(app, dati):
    from ai_service import _get_ai_config
    from models import set_struttura_config
    with app.app_context():
        app.config['APP_CONFIG']['default_ai_provider'] = 'openai'
        set_struttura_config(dati['s'], 'ai_provider', 'gemini')
        cfg = _get_ai_config(struttura_id=dati['s'])
    assert cfg['provider'] == 'gemini'


def test_l_allowlist_resta_di_sistema_anche_con_una_riga_di_struttura(app, dati):
    """Se l'allowlist si risolvesse come le altre chiavi AI, all'admin di una
    struttura basterebbe una riga in `strutture_config` per allargare il
    perimetro degli URL locali raggiungibili."""
    from ai_service import _get_ai_config
    from models import set_struttura_config
    with app.app_context():
        app.config['APP_CONFIG']['ai_local_url_allowlist'] = ['127.0.0.1:11434']
        set_struttura_config(dati['s'], 'ai_local_url_allowlist', 'evil.example.com')
        cfg = _get_ai_config(struttura_id=dati['s'])
    assert cfg['local_url_allowlist'] == ['127.0.0.1:11434']


# ---------------------------------------------------------------------------
# Quello che l'interfaccia dichiara
# ---------------------------------------------------------------------------

def test_la_configurazione_globale_dichiara_configurata_una_chiave_legacy(client, app, dati):
    """La pagina mostrava campi vuoti mentre il runtime usava davvero la
    chiave senza prefisso: l'operatore non aveva modo di sapere che l'AI era
    configurata."""
    entra(client, 'super@x.it')
    app.config['APP_CONFIG'].pop('default_anthropic_api_key', None)
    app.config['APP_CONFIG']['anthropic_api_key'] = 'sk-vecchia'
    app.config['APP_CONFIG']['ai_provider'] = 'gemini'
    risposta = client.get('/admin/configurazione')
    testo = risposta.get_data(as_text=True)
    assert 'Configurata' in testo
    assert 'sk-vecchia' not in testo


def test_la_config_di_struttura_dichiara_i_valori_ereditati(client, app, dati):
    """Senza la copia alla creazione i campi sarebbero vuoti: la pagina deve
    mostrare il valore di sistema che verrebbe usato davvero, dicendo che non
    e' della struttura."""
    entra(client, 'super@x.it')
    app.config['APP_CONFIG']['default_ai_local_model'] = 'llama-di-sistema'
    risposta = client.get(f"/strutture/{dati['s']}/config")
    testo = risposta.get_data(as_text=True)
    assert 'Dalla configurazione di sistema' in testo
    # Il valore ereditato compare come segnaposto, non come valore del campo:
    # salvare senza toccare nulla non deve trasformarlo in un override.
    assert 'placeholder="llama-di-sistema"' in testo
    assert 'value="llama-di-sistema"' not in testo
