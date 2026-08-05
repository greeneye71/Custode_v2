"""La struttura nella barra di navigazione.

Con una sola divisione accessibile il menu delle divisioni non e' una scelta:
e' un clic per scoprire che non fa niente, e mostra un nome che sembra quello
della struttura solo perche' la divisione predefinita nasce con lo stesso nome
(strutture_bp._crea_divisione_predefinita). Al suo posto si scrive il nome
della struttura, che per admin e utenti oggi non compare da nessuna parte.

Le strutture di questi test hanno la divisione con un nome DIVERSO da quello
della struttura: con i due nomi uguali — il caso reale — ogni asserzione
passerebbe qualunque cosa la barra mostri.
"""
import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def dati(app):
    from models import execute
    with app.app_context():
        h = generate_password_hash('Passw0rd!')

        # Struttura con UNA divisione attiva (nome diverso dalla struttura) e
        # una disattivata che contiene ancora apparecchi.
        una = execute("INSERT INTO strutture (nome,codice,attiva) "
                      "VALUES ('Casa di Cura Bianchi','CCB',1)").lastrowid
        sola = execute("INSERT INTO divisioni (nome,codice,struttura_id,attiva) "
                       "VALUES ('Reparto Unico','RU',?,1)", (una,)).lastrowid
        spenta = execute("INSERT INTO divisioni (nome,codice,struttura_id,attiva) "
                         "VALUES ('Vecchio Reparto','VR',?,0)", (una,)).lastrowid
        execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,"
                "modello,stato) VALUES (?,?,'ATTIVA-1','REXXAM','OZY','funzionante')",
                (sola, una))
        execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,"
                "modello,stato) VALUES (?,?,'SPENTA-1','TOPCON','CT80','funzionante')",
                (spenta, una))

        # Struttura con DUE divisioni attive.
        due = execute("INSERT INTO strutture (nome,codice,attiva) "
                      "VALUES ('Poliambulatorio Verdi','PV',1)").lastrowid
        prima = execute("INSERT INTO divisioni (nome,codice,struttura_id,attiva) "
                        "VALUES ('Oculistica','OCU',?,1)", (due,)).lastrowid
        seconda = execute("INSERT INTO divisioni (nome,codice,struttura_id,attiva) "
                          "VALUES ('Radiologia','RAD',?,1)", (due,)).lastrowid

        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,"
                "primo_accesso) VALUES ('admin1@x.it',?,'A','Uno','admin',?,0)", (h, una))
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,"
                "primo_accesso) VALUES ('admin2@x.it',?,'A','Due','admin',?,0)", (h, due))
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,"
                "primo_accesso) VALUES ('super@x.it',?,'S','S','superadmin',0)", (h,))
        semplice = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,"
            "primo_accesso) VALUES ('utente@x.it',?,'U','U','utente',?,0)",
            (h, due)).lastrowid
        execute("INSERT INTO utenti_divisioni (utente_id,divisione_id,ruolo_divisione) "
                "VALUES (?,?,'utente')", (semplice, prima))
        execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,"
                "modello,stato) VALUES (?,?,'ALTRUI-1','NIDEK','AR1','funzionante')",
                (seconda, due))
    return {'una': una, 'due': due, 'sola': sola, 'spenta': spenta}


def entra(client, email):
    client.post('/login', data={'email': email, 'password': 'Passw0rd!'})


def barra(client):
    return client.get('/apparecchi').get_data(as_text=True)


def test_con_una_sola_divisione_l_admin_vede_il_nome_della_struttura(client, dati):
    entra(client, 'admin1@x.it')
    pagina = barra(client)
    assert 'Casa di Cura Bianchi' in pagina


def test_con_una_sola_divisione_il_menu_divisioni_sparisce(client, dati):
    """Si cerca il comando che il menu offre, non il nome della divisione: il
    nome compare comunque nell'elenco degli apparecchi."""
    entra(client, 'admin1@x.it')
    assert 'Tutte le divisioni' not in barra(client)


def test_l_admin_con_una_sola_divisione_vede_le_divisioni_disattivate(client, dati):
    """L'asserzione che conta. Il menu che sparisce portava con se' «Tutte le
    divisioni», che per l'admin allargava l'ambito da divisione a struttura:
    senza compenso, gli apparecchi delle divisioni disattivate — che esistono e
    appartengono alla struttura — sparirebbero in silenzio, e non ci sarebbe
    piu' nessun comando per farli tornare."""
    entra(client, 'admin1@x.it')
    pagina = barra(client)
    assert 'ATTIVA-1' in pagina
    assert 'SPENTA-1' in pagina


def test_con_due_divisioni_il_menu_resta_com_era(client, dati):
    entra(client, 'admin2@x.it')
    pagina = barra(client)
    assert 'Tutte le divisioni' in pagina
    assert 'Radiologia' in pagina


def test_l_utente_semplice_vede_il_nome_della_struttura(client, dati):
    entra(client, 'utente@x.it')
    assert 'Poliambulatorio Verdi' in barra(client)


def test_l_ambito_dell_utente_semplice_non_si_allarga(client, dati):
    """«Tutte le divisioni» non ce l'ha mai avuto, e non deve guadagnarlo qui:
    resta chiuso nelle divisioni che gli sono assegnate."""
    entra(client, 'utente@x.it')
    pagina = barra(client)
    assert 'ALTRUI-1' not in pagina
    assert 'Tutte le divisioni' not in pagina


def test_il_superadmin_che_impersona_non_legge_il_nome_due_volte(client, dati):
    """Il selettore di struttura mostra gia' il nome: ripeterlo accanto
    sarebbe la stessa duplicazione che questa modifica toglie."""
    entra(client, 'super@x.it')
    client.get(f"/strutture/{dati['una']}/impersona", follow_redirects=True)
    pagina = barra(client)
    assert pagina.count('Casa di Cura Bianchi') == 1


def test_in_modalita_single_struttura_il_nome_compare(client, app, dati):
    """E' il nome del posto in cui si lavora, e su uno schermo condiviso in
    reparto dice a chi passa cosa sta guardando."""
    # Si MODIFICA il dizionario, non lo si sostituisce: inject_globals legge
    # single_struttura dalla chiusura di create_app, che e' lo stesso oggetto
    # di app.config['APP_CONFIG']. Rimpiazzandolo, la modalita' single non
    # arrivava affatto al template e il test passava per il motivo sbagliato.
    app.config['APP_CONFIG']['single_struttura'] = True
    entra(client, 'admin1@x.it')
    assert 'Casa di Cura Bianchi' in barra(client)
