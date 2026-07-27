"""Il report inviato via email deve essere lo stesso documento che si stampa a mano."""
import os


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


def test_il_report_dello_scheduler_regge_una_struttura_senza_scadenze(app, tmp_path):
    from models import execute
    from export_service import genera_report_scadenze_pdf

    with app.app_context():
        struttura = execute(
            "INSERT INTO strutture (nome,codice,attiva) VALUES ('Vuota','V',1)").lastrowid
        destinazione = str(tmp_path / 'vuoto.pdf')
        genera_report_scadenze_pdf(struttura_id=struttura, output_path=destinazione)

    assert os.path.exists(destinazione)
