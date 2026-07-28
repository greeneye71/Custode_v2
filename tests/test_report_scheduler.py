"""Il report inviato via email deve essere lo stesso documento che si stampa a mano."""
import io
import os

from pypdf import PdfReader


def testo_di(percorso):
    """Estrae il testo di tutte le pagine di un PDF su disco."""
    with open(percorso, 'rb') as f:
        lettore = PdfReader(io.BytesIO(f.read()))
    return "\n".join(pagina.extract_text() for pagina in lettore.pages)


def test_il_report_dello_scheduler_usa_il_motore(app, tmp_path):
    from models import execute
    from export_service import genera_report_scadenze_pdf

    with app.app_context():
        struttura = execute(
            "INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
        divisione = execute(
            "INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Oculistica','OCU',?)",
            (struttura,)).lastrowid
        apparecchio = execute(
            "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato,ubicazione) "
            "VALUES (?,?,'R-1','REXXAM','OZY','funzionante','Sala 1')",
            (divisione, struttura)).lastrowid
        execute(
            "INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento,prossima_scadenza) "
            "VALUES (?,'preventiva',date('now','-1 year'),date('now','-10 days'))",
            (apparecchio,))

        destinazione = str(tmp_path / 'report.pdf')
        genera_report_scadenze_pdf(struttura_id=struttura, output_path=destinazione)

    assert os.path.exists(destinazione)
    with open(destinazione, 'rb') as f:
        assert f.read(4) == b'%PDF'


def test_il_report_dello_scheduler_distingue_manutenzioni_e_verifiche(app, tmp_path):
    """Il report email e' l'unico prospetto che mescola i due tipi: le stampe
    manuali sono separate proprio perche' vanno a destinatari diversi (la
    ditta di assistenza e lo studio di ingegneria). Senza una colonna che dica
    di cosa si tratta, due righe dello stesso apparecchio sono identiche in
    tutto tranne la data, e chi riceve l'email non puo' capire a chi girare
    cosa. Il generatore precedente il tipo lo stampava: perderlo e' una
    regressione."""
    from models import execute
    from export_service import genera_report_scadenze_pdf

    with app.app_context():
        struttura = execute(
            "INSERT INTO strutture (nome,codice,attiva) VALUES ('Mista','M',1)").lastrowid
        divisione = execute(
            "INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Oculistica','OCU',?)",
            (struttura,)).lastrowid
        apparecchio = execute(
            "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato,ubicazione) "
            "VALUES (?,?,'R-1','REXXAM','OZY','funzionante','Sala 1')",
            (divisione, struttura)).lastrowid
        # Stesso apparecchio, due scadenze di natura diversa: e' il caso in
        # cui le due righe sarebbero indistinguibili.
        execute(
            "INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento,prossima_scadenza) "
            "VALUES (?,'preventiva',date('now','-1 year'),date('now','+10 days'))",
            (apparecchio,))
        execute(
            "INSERT INTO verifiche (apparecchio_id,data_verifica,prossima_scadenza,esito) "
            "VALUES (?,date('now','-1 year'),date('now','+20 days'),'positivo')",
            (apparecchio,))

        destinazione = str(tmp_path / 'misto.pdf')
        genera_report_scadenze_pdf(struttura_id=struttura, output_path=destinazione)

    testo = testo_di(destinazione)
    assert 'Preventiva' in testo
    assert 'Verifica elettrica' in testo


def test_il_report_dello_scheduler_regge_una_struttura_senza_scadenze(app, tmp_path):
    from models import execute
    from export_service import genera_report_scadenze_pdf

    with app.app_context():
        struttura = execute(
            "INSERT INTO strutture (nome,codice,attiva) VALUES ('Vuota','V',1)").lastrowid
        destinazione = str(tmp_path / 'vuoto.pdf')
        genera_report_scadenze_pdf(struttura_id=struttura, output_path=destinazione)

    assert os.path.exists(destinazione)


def test_il_report_dello_scheduler_include_il_logo_presente_su_disco(app, tmp_path):
    """Ramo critico: genera_report_scadenze_pdf gira in un thread di sfondo,
    fuori da una request HTTP, quindi la composizione del percorso assoluto
    del logo con current_app.config['UPLOADS_PATH'] e' esattamente il codice
    che deve funzionare senza il contesto di request. Qui il file esiste
    davvero su disco, nel punto in cui lo mette models.upload_subdir().

    Non basta controllare che il PDF esca: un percorso composto male fa
    fallire silenziosamente os.path.exists() dentro ReportPDF.header() (che
    ignora un logo che non trova), quindi confrontiamo la dimensione del file
    con e senza logo sulla stessa struttura e con gli stessi dati, cosi' la
    sola variabile e' la presenza dell'immagine in testata."""
    from models import execute, upload_subdir
    from export_service import genera_report_scadenze_pdf
    from PIL import Image

    with app.app_context():
        struttura = execute(
            "INSERT INTO strutture (nome,codice,attiva) VALUES ('Con Logo','CL',1)").lastrowid
        divisione = execute(
            "INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Oculistica','OCU',?)",
            (struttura,)).lastrowid
        apparecchio = execute(
            "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato,ubicazione) "
            "VALUES (?,?,'R-1','REXXAM','OZY','funzionante','Sala 1')",
            (divisione, struttura)).lastrowid
        execute(
            "INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento,prossima_scadenza) "
            "VALUES (?,'preventiva',date('now','-1 year'),date('now','-10 days'))",
            (apparecchio,))

        # Stesso posto dove lo salva strutture_bp.carica_logo: se qui il
        # percorso non torna a coincidere, il test non prova nulla.
        cartella, prefisso = upload_subdir('loghi', struttura)
        Image.new('RGB', (60, 60), color=(30, 80, 200)).save(
            os.path.join(cartella, 'logo.png'))
        execute("UPDATE strutture SET logo_path = ? WHERE id = ?",
                (f"{prefisso}/logo.png", struttura))

        destinazione_con_logo = str(tmp_path / 'con_logo.pdf')
        genera_report_scadenze_pdf(struttura_id=struttura, output_path=destinazione_con_logo)

        # Stessa struttura, stessi dati, solo il logo tolto: isola la
        # variabile che vogliamo osservare.
        execute("UPDATE strutture SET logo_path = NULL WHERE id = ?", (struttura,))
        destinazione_senza_logo = str(tmp_path / 'senza_logo.pdf')
        genera_report_scadenze_pdf(struttura_id=struttura, output_path=destinazione_senza_logo)

    assert os.path.exists(destinazione_con_logo)
    with open(destinazione_con_logo, 'rb') as f:
        assert f.read(4) == b'%PDF'
    assert os.path.getsize(destinazione_con_logo) != os.path.getsize(destinazione_senza_logo)


def test_il_report_dello_scheduler_regge_un_logo_cancellato_dal_disco(app, tmp_path):
    """Caso simmetrico al precedente: logo_path e' valorizzato ma il file e'
    stato cancellato dal disco dopo il caricamento. Il report deve uscire lo
    stesso, senza eccezioni: ReportPDF.header() verifica gia' os.path.exists()
    da sola."""
    from models import execute, upload_subdir
    from export_service import genera_report_scadenze_pdf

    with app.app_context():
        struttura = execute(
            "INSERT INTO strutture (nome,codice,attiva) VALUES ('Logo Sparito','LS',1)").lastrowid
        # Percorso nella stessa forma prodotta da upload_subdir(), ma senza
        # che il file esista realmente.
        _, prefisso = upload_subdir('loghi', struttura)
        execute("UPDATE strutture SET logo_path = ? WHERE id = ?",
                (f"{prefisso}/sparito.png", struttura))

        destinazione = str(tmp_path / 'logo_sparito.pdf')
        genera_report_scadenze_pdf(struttura_id=struttura, output_path=destinazione)

    assert os.path.exists(destinazione)
    with open(destinazione, 'rb') as f:
        assert f.read(4) == b'%PDF'
