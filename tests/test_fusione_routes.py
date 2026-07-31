"""Le rotte della fusione: chi puo' arrivarci e cosa vede."""
import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def dati(app):
    """Una struttura con due schede duplicate e un utente per ogni ruolo che
    conta. La struttura B esiste per dimostrare l'isolamento."""
    from models import execute
    with app.app_context():
        a = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        b = execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica B','B',1)").lastrowid
        da = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Oculistica','OCU',?)",
                     (a,)).lastrowid
        db_ = execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Cardiologia','CAR',?)",
                      (b,)).lastrowid
        # Divisione senza apparecchi, nella stessa struttura A: l'utente
        # semplice ci sta dentro invece che in Oculistica. Se stesse in
        # Oculistica, dopo il redirect vedrebbe R00015 nel SUO elenco
        # normale - legittimamente, perche' e' un apparecchio della sua
        # divisione - e la presenza della matricola non proverebbe piu'
        # nulla sull'autorizzazione della pagina duplicati.
        d_vuota = execute(
            "INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Radiologia','RAD',?)",
            (a,)).lastrowid
        hash_pw = generate_password_hash('Passw0rd!')
        execute("INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
                "VALUES ('admin@a.it',?,'A','A','admin',?,0)", (hash_pw, a))
        semplice = execute(
            "INSERT INTO utenti (email,password_hash,nome,cognome,ruolo,struttura_id,primo_accesso) "
            "VALUES ('utente@a.it',?,'U','U','utente',?,0)", (hash_pw, a)).lastrowid
        execute("INSERT INTO utenti_divisioni (utente_id,divisione_id,ruolo_divisione) "
                "VALUES (?,?,'utente')", (semplice, d_vuota))

        uno = execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,"
                      "stato,ubicazione) VALUES (?,?,'R-00015','REXXAM','OZY','funzionante','Sala 1')",
                      (da, a)).lastrowid
        due = execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,"
                      "stato,ubicazione) VALUES (?,?,'R00015','REXXAM','OZY','funzionante','Sala 1')",
                      (da, a)).lastrowid
        estraneo = execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,"
                           "modello,stato) VALUES (?,?,'SEGRETO-B','SIEMENS','Y1','funzionante')",
                           (db_, b)).lastrowid
    return {'a': a, 'b': b, 'div_a': da, 'div_b': db_,
            'uno': uno, 'due': due, 'estraneo': estraneo}


def entra(client, email):
    client.post('/login', data={'email': email, 'password': 'Passw0rd!'})


def test_l_elenco_propone_la_coppia_duplicata(client, dati):
    entra(client, 'admin@a.it')
    risposta = client.get('/apparecchi/duplicati')
    assert risposta.status_code == 200
    testo = risposta.get_data(as_text=True)
    assert 'R-00015' in testo
    assert 'R00015' in testo


def test_l_elenco_non_mostra_apparecchi_di_altre_strutture(client, app, dati):
    """L'elenco confronta gli apparecchi fra loro: senza filtro di struttura
    proporrebbe coppie fra tenant diversi, e la pagina stessa mostrerebbe
    matricole altrui."""
    from models import execute
    with app.app_context():
        # Una matricola equivalente a 'SEGRETO-B' (stessa normalizzazione,
        # letterale diverso per non violare UNIQUE(struttura_id, modello,
        # matricola) contro l'apparecchio 'estraneo' della fixture): se il
        # filtro di struttura manca, la coppia compare di sicuro.
        execute("INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,"
                "stato,ubicazione) VALUES (?,?,'SEGRETO B','SIEMENS','Y1','funzionante','X')",
                (dati['div_b'], dati['b']))
    entra(client, 'admin@a.it')
    testo = client.get('/apparecchi/duplicati').get_data(as_text=True)
    assert 'SEGRETO-B' not in testo


def test_l_elenco_e_negato_a_un_utente_semplice(client, dati):
    """La fusione cancella una scheda, e un utente non puo' nemmeno
    dismetterne una."""
    entra(client, 'utente@a.it')
    risposta = client.get('/apparecchi/duplicati', follow_redirects=True)
    assert 'R00015' not in risposta.get_data(as_text=True)


def test_l_elenco_dice_perche_propone_la_coppia(client, dati):
    """Chi guarda deve sapere quale criterio l'ha proposta: e' cio' che
    distingue una corrispondenza certa da una somiglianza da verificare."""
    entra(client, 'admin@a.it')
    testo = client.get('/apparecchi/duplicati').get_data(as_text=True)
    assert 'matricola identica a meno di trattini' in testo
