"""toggle_modalita.py - il default di 'single_struttura' quando la chiave
manca deve concordare con il resto del progetto (models.py, app.py), non
dichiarare 'single' mentre l'applicazione si comporta gia' da multi."""
import toggle_modalita


def test_stato_attuale_di_default_e_multi_come_il_resto_del_progetto():
    """Minore della revisione finale: qui il default era True ('single'),
    mentre app.py/models.py usano False quando la chiave manca. Proprio
    nell'area del Critico 2 (installazione promossa): un disallineamento fa
    si' che lo strumento dichiari 'single' su un'installazione che il resto
    del codice tratta gia' da multi."""
    assert toggle_modalita.stato_attuale({}) is False


def test_stato_attuale_rispetta_il_valore_esplicito():
    assert toggle_modalita.stato_attuale({'single_struttura': True}) is True
    assert toggle_modalita.stato_attuale({'single_struttura': False}) is False
