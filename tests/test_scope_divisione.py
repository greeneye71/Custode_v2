"""A01 e M11: lo scope di divisione sulle scritture, e le rotte che cambiano
stato senza essere POST.

A01 — fino alla 2.8.0 diversi punti convalidavano la *struttura* e si
fermavano li'. Un ruolo 'utente', assegnato a un solo reparto, poteva aprire
ed eseguire l'import di un reparto non suo e depositarci schede nuove. Il
controllo mancante ha ora un nome, models.divisione_accessibile(), gemello di
apparecchio_accessibile().

M11 — logout, cambio divisione, impersonazione e scelta della struttura del
tecnico erano GET: un <img src> su una pagina qualunque bastava a sloggare
chi la apriva o a spostargli l'ambito di lavoro sotto i piedi. Ora sono POST
con token CSRF, e un GET deve rispondere 405.
"""
import pytest
from werkzeug.security import generate_password_hash


def entra(client, email):
    client.post('/login', data={'email': email, 'password': 'Passw0rd!'})


@pytest.fixture
def due_divisioni(app):
    """Una struttura, due reparti, un 'utente' assegnato solo al primo.
    E' la configurazione minima in cui il controllo sulla sola struttura
    passa e quello sulla divisione no."""
    from models import execute
    with app.app_context():
        s = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica S','SS',1)").lastrowid
        altra = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica T','TT',1)").lastrowid
        mia = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Mia','MIA',?)", (s,)).lastrowid
        altrui = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Altrui','ALT',?)", (s,)).lastrowid
        estranea = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Estranea','EST',?)", (altra,)).lastrowid
        pw = generate_password_hash('Passw0rd!')
        admin = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('admin@s.it',?,'A','S','admin',?,0)", (pw, s)).lastrowid
        utente = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('utente@s.it',?,'U','S','utente',?,0)", (pw, s)).lastrowid
        execute("INSERT INTO utenti_divisioni (utente_id,divisione_id,ruolo_divisione) VALUES (?,?,'utente')",
                (utente, mia))
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
                "VALUES ('super@s.it',?,'S','S','superadmin',NULL,0)", (pw,))
    return {'s': s, 'altra': altra, 'mia': mia, 'altrui': altrui,
            'estranea': estranea, 'admin': admin, 'utente': utente}


def _import_di(app, dati, divisione_id, tipo='inventario', stato='completed'):
    """Una riga di import_history attribuita al reparto indicato."""
    from models import execute
    with app.app_context():
        return execute(
            "INSERT INTO import_history (tipo_import,filename,filepath,divisione_id,struttura_id,"
            "stato,totale_righe,righe_importate,righe_errori,imported_by) "
            "VALUES (?,'x.xlsx','uploads/x.xlsx',?,?,?,1,0,0,?)",
            (tipo, divisione_id, dati['s'], stato, dati['admin'])
        ).lastrowid


# ---------------------------------------------------------------------------
# A01 — models.divisione_accessibile()
# ---------------------------------------------------------------------------

def _scope(app, ruolo, struttura_id, divisioni_ids):
    """Ricostruisce il contesto che _load_user_from_session lascia in g.

    divisione_accessibile() legge g.user, g.struttura_id e g.divisioni: sono
    i tre valori che decidono, e testarla direttamente li rende espliciti."""
    from flask import g
    ctx = app.test_request_context()
    ctx.push()
    g.user = {'id': 1, 'ruolo': ruolo}
    g.struttura_id = struttura_id
    g.divisioni = [{'id': i} for i in divisioni_ids]
    return ctx


def test_utente_non_raggiunge_una_divisione_non_assegnata(app, due_divisioni):
    """Stessa struttura, reparto diverso: e' il caso che prima passava."""
    import models
    ctx = _scope(app, 'utente', due_divisioni['s'], [due_divisioni['mia']])
    try:
        assert models.divisione_accessibile(due_divisioni['mia'])
        assert models.divisione_accessibile(due_divisioni['altrui']) is None
    finally:
        ctx.pop()


def test_admin_raggiunge_ogni_divisione_della_sua_struttura_e_nessun_altra(app, due_divisioni):
    """L'admin non e' vincolato ai reparti assegnati, ma la struttura resta
    il confine."""
    import models
    ctx = _scope(app, 'admin', due_divisioni['s'], [])
    try:
        assert models.divisione_accessibile(due_divisioni['altrui'])
        assert models.divisione_accessibile(due_divisioni['estranea']) is None
    finally:
        ctx.pop()


def test_superadmin_senza_impersonazione_raggiunge_tutto(app, due_divisioni):
    """Vista globale: e' l'unico ruolo per cui l'assenza di struttura attiva
    non significa 'nessun dato'."""
    import models
    ctx = _scope(app, 'superadmin', None, [])
    try:
        assert models.divisione_accessibile(due_divisioni['estranea'])
    finally:
        ctx.pop()


def test_senza_struttura_attiva_nessun_altro_ruolo_passa(app, due_divisioni):
    """Admin orfano della sua struttura: niente scope, quindi niente
    divisioni — non tutte."""
    import models
    ctx = _scope(app, 'admin', None, [])
    try:
        assert models.divisione_accessibile(due_divisioni['mia']) is None
    finally:
        ctx.pop()


def test_divisione_inesistente_non_passa(app, due_divisioni):
    import models
    ctx = _scope(app, 'admin', due_divisioni['s'], [])
    try:
        assert models.divisione_accessibile(999999) is None
        assert models.divisione_accessibile(None) is None
    finally:
        ctx.pop()


# ---------------------------------------------------------------------------
# A01 — get_import_in_scope() e _scope_import()
# ---------------------------------------------------------------------------

def test_utente_non_apre_la_preview_di_un_import_di_un_altro_reparto(client, app, due_divisioni):
    """La struttura coincide: prima bastava, e la preview si apriva."""
    id_import = _import_di(app, due_divisioni, due_divisioni['altrui'])
    entra(client, 'utente@s.it')
    risposta = client.get(f'/import/{id_import}/preview', follow_redirects=True)
    assert 'Import non trovato' in risposta.data.decode('utf-8', errors='replace')


def test_utente_puo_aprire_la_preview_del_proprio_reparto(client, app, due_divisioni):
    """Controprova: il caso sano non deve essere stato chiuso insieme."""
    id_import = _import_di(app, due_divisioni, due_divisioni['mia'])
    entra(client, 'utente@s.it')
    risposta = client.get(f'/import/{id_import}/preview', follow_redirects=True)
    assert 'Import non trovato' not in risposta.data.decode('utf-8', errors='replace')


def test_utente_non_esegue_un_import_di_un_altro_reparto(client, app, due_divisioni):
    """La rotta che scrive davvero, non solo quella che mostra."""
    id_import = _import_di(app, due_divisioni, due_divisioni['altrui'], stato='pending')
    entra(client, 'utente@s.it')
    risposta = client.post(f'/import/{id_import}/esegui', data={}, follow_redirects=True)
    assert 'Import non trovato' in risposta.data.decode('utf-8', errors='replace')


def test_lo_storico_non_elenca_gli_import_degli_altri_reparti(client, app, due_divisioni):
    """L'elenco ha lo stesso perimetro del dettaglio: mostrare una riga che
    non si puo' aprire e' comunque una fuga di informazione."""
    _import_di(app, due_divisioni, due_divisioni['altrui'])
    entra(client, 'utente@s.it')
    pagina = client.get('/import/storico').data.decode('utf-8', errors='replace')
    assert 'Altrui' not in pagina


def test_utente_non_vede_gli_import_dalla_posta(client, app, due_divisioni):
    """Gli import email hanno divisione_id NULL: non appartengono ad alcun
    reparto, quindi li gestisce solo chi vede l'intera struttura."""
    id_import = _import_di(app, due_divisioni, None, tipo='verbale_email', stato='pending')
    entra(client, 'utente@s.it')
    risposta = client.get(f'/import/email/{id_import}', follow_redirects=True)
    assert 'Record non trovato' in risposta.data.decode('utf-8', errors='replace')


def test_admin_vede_gli_import_dalla_posta(client, app, due_divisioni):
    """Controprova sul ruolo a cui la coda email e' destinata."""
    id_import = _import_di(app, due_divisioni, None, tipo='verbale_email', stato='pending')
    entra(client, 'admin@s.it')
    risposta = client.get(f'/import/email/{id_import}', follow_redirects=True)
    assert 'Record non trovato' not in risposta.data.decode('utf-8', errors='replace')


# ---------------------------------------------------------------------------
# M11 — le rotte che cambiano stato non rispondono piu' in GET
# ---------------------------------------------------------------------------

ROTTE_DI_STATO = [
    '/logout',
    '/divisione/tutte',
    '/impersona/1',
    '/esci-impersonazione',
    '/tecnico/struttura/1',
]


@pytest.mark.parametrize('rotta', ROTTE_DI_STATO)
def test_le_rotte_di_stato_rifiutano_il_get(client, app, due_divisioni, rotta):
    """405, non 302: un GET non deve poter cambiare la sessione di chi lo
    subisce, e il codice dice perche' — Method Not Allowed, non 'non
    autorizzato'."""
    entra(client, 'admin@s.it')
    assert client.get(rotta).status_code == 405


def test_il_logout_in_post_funziona_ancora(client, app, due_divisioni):
    entra(client, 'admin@s.it')
    assert client.get('/apparecchi').status_code == 200
    client.post('/logout')
    # Senza sessione la rotta protetta rimanda al login.
    assert client.get('/apparecchi').status_code == 302


def test_il_cambio_divisione_in_post_funziona_ancora(client, app, due_divisioni):
    entra(client, 'utente@s.it')
    client.post(f"/divisione/{due_divisioni['mia']}")
    with client.session_transaction() as sessione:
        assert sessione['divisione_attiva_id'] == due_divisioni['mia']
