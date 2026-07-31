"""La fusione di apparecchi duplicati.

candidati_duplicati e' una funzione pura su una lista di dizionari: si prova
con dieci righe in memoria invece che con un database popolato.
"""
import os
import sqlite3

import pytest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def riga(id, matricola, marca='REXXAM', modello='OZY', ubicazione='Sala 1'):
    return {'id': id, 'matricola': matricola, 'marca': marca,
            'modello': modello, 'ubicazione': ubicazione}


def test_matricola_equivalente_una_volta_normalizzata():
    from fusione_service import candidati_duplicati
    coppie = candidati_duplicati([riga(1, 'R-00015'), riga(2, 'r00015')])
    assert len(coppie) == 1
    assert {coppie[0].a['id'], coppie[0].b['id']} == {1, 2}
    assert coppie[0].criterio == 'matricola_equivalente'


def test_una_matricola_contenuta_nell_altra():
    from fusione_service import candidati_duplicati
    coppie = candidati_duplicati([riga(1, 'MON-1'), riga(2, 'MON-1/A')])
    assert len(coppie) == 1
    assert coppie[0].criterio == 'matricola_contenuta'


def test_matricole_a_distanza_uno_con_stesso_modello_e_ubicazione():
    from fusione_service import candidati_duplicati
    coppie = candidati_duplicati([riga(1, 'MON-1'), riga(2, 'MON-l')])
    assert len(coppie) == 1
    assert coppie[0].criterio == 'matricola_distanza_uno'


def test_distanza_uno_non_basta_se_il_modello_e_diverso():
    """Il criterio piu' debole dei tre e' anche quello che sbaglia piu'
    facilmente: 'MON-1' e 'MON-2' sono due monitor consecutivi, non un
    duplicato. Il modello e l'ubicazione uguali sono cio' che lo rende
    utilizzabile, e senza di essi non deve proporre nulla."""
    from fusione_service import candidati_duplicati
    assert candidati_duplicati([riga(1, 'MON-1', modello='OZY'),
                                riga(2, 'MON-2', modello='ALTRO')]) == []
    assert candidati_duplicati([riga(1, 'MON-1', ubicazione='Sala 1'),
                                riga(2, 'MON-2', ubicazione='Sala 2')]) == []


def test_due_apparecchi_consecutivi_nella_stessa_sala_sono_proposti_ma_e_il_caso_da_guardare():
    """Onesta' sul limite: stesso modello, stessa sala, matricole a distanza 1
    e' esattamente la forma di due macchine gemelle acquistate insieme
    ('MON-1' e 'MON-2' nella stessa sala). Il criterio le propone, ed e'
    voluto - non c'e' modo di distinguerle da un errore di battitura senza
    guardarle. La difesa non e' nel criterio ma nell'interfaccia: la coppia
    viene PROPOSTA, non fusa, e l'etichetta dice perche'."""
    from fusione_service import candidati_duplicati
    coppie = candidati_duplicati([riga(1, 'MON-1'), riga(2, 'MON-2')])
    assert len(coppie) == 1
    assert coppie[0].criterio == 'matricola_distanza_uno'


def test_matricole_corte_non_si_propongono_per_contenimento():
    """'1' e' contenuto in '12', '13', '104'... Su matricole corte il
    contenimento propone tutto con tutto e l'elenco diventa inutilizzabile,
    che e' il modo in cui questa funzione smette di essere usata."""
    from fusione_service import candidati_duplicati
    coppie = candidati_duplicati([riga(1, '1'), riga(2, '12'), riga(3, '13')])
    assert coppie == []


def test_una_coppia_non_viene_proposta_due_volte_ne_con_se_stessa():
    from fusione_service import candidati_duplicati
    coppie = candidati_duplicati([riga(1, 'R-00015'), riga(2, 'r00015'), riga(3, 'R 00015')])
    assert len(coppie) == 3          # 1-2, 1-3, 2-3: ogni coppia una volta sola
    viste = {frozenset((c.a['id'], c.b['id'])) for c in coppie}
    assert len(viste) == 3
    assert all(c.a['id'] != c.b['id'] for c in coppie)


def test_il_criterio_piu_forte_vince():
    """Due righe possono soddisfare piu' criteri: l'etichetta deve essere
    quella piu' forte, altrimenti l'elenco declassa una corrispondenza certa
    a somiglianza vaga e chi guarda si fida meno di quanto potrebbe."""
    from fusione_service import candidati_duplicati
    coppie = candidati_duplicati([riga(1, 'MON1'), riga(2, 'mon-1')])
    assert coppie[0].criterio == 'matricola_equivalente'


def test_matricola_vuota_non_propone_nulla():
    """Una matricola assente non e' una matricola uguale a un'altra assente."""
    from fusione_service import candidati_duplicati
    assert candidati_duplicati([riga(1, ''), riga(2, ''), riga(3, None)]) == []


def test_elenco_vuoto_o_singolo():
    from fusione_service import candidati_duplicati
    assert candidati_duplicati([]) == []
    assert candidati_duplicati([riga(1, 'R-1')]) == []


def test_matricole_cortissime_non_si_propongono_nemmeno_a_parita_di_modello():
    """Il vincolo di lunghezza governa anche il criterio piu' debole, non solo
    il contenimento: 'A-1' e 'A-2' nella stessa sala sono quasi sempre due
    macchine numerate di seguito, non la stessa registrata due volte. Il
    criterio che le distinguerebbe non esiste, e proporle tutte rende
    l'elenco inutilizzabile - che e' il modo in cui questa funzione smette
    di essere usata."""
    from fusione_service import candidati_duplicati
    assert candidati_duplicati([riga(1, 'A-1'), riga(2, 'A-2')]) == []


@pytest.fixture
def conn(tmp_path):
    """Due apparecchi duplicati della stessa struttura, ciascuno con la sua
    storia, piu' un terzo apparecchio che non deve essere toccato."""
    percorso = str(tmp_path / 'prova.db')
    con = sqlite3.connect(percorso)
    con.row_factory = sqlite3.Row
    with open(os.path.join(RADICE, 'schema.sql'), encoding='utf-8') as f:
        con.executescript(f.read())
    con.execute("PRAGMA foreign_keys = ON")

    s = con.execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica A','A',1)").lastrowid
    d = con.execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Oculistica','OCU',?)",
                    (s,)).lastrowid
    ids = {}
    for etichetta, matricola, anno, note in (
            ('principale', 'R-00015', None, None),
            ('scartato', 'R00015', 2019, 'Rev. 2024'),
            ('terzo', 'ALTRO-1', None, None)):
        ids[etichetta] = con.execute(
            "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,"
            "stato,ubicazione,anno_fabbricazione,note) "
            "VALUES (?,?,?,'REXXAM','OZY','funzionante','Sala 1',?,?)",
            (d, s, matricola, anno, note)).lastrowid

    for etichetta, quante in (('principale', 3), ('scartato', 1)):
        for n in range(quante):
            con.execute(
                "INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento,verbale_path) "
                "VALUES (?,'preventiva','2026-03-12',?)",
                (ids[etichetta], f'strutture/{s}/verbali/{etichetta}{n}.pdf'))
    for etichetta, quante in (('principale', 1), ('scartato', 2)):
        for n in range(quante):
            con.execute(
                "INSERT INTO verifiche (apparecchio_id,data_verifica,esito,documento_path) "
                "VALUES (?,'2025-11-08','positivo',?)",
                (ids[etichetta], f'strutture/{s}/verifiche/{etichetta}{n}.pdf'))
    con.execute("INSERT INTO documenti (apparecchio_id,tipo,filename,filepath) "
                "VALUES (?,'manuale','m.pdf','x/m.pdf')", (ids['scartato'],))
    con.execute("INSERT INTO accessori (apparecchio_id,descrizione) VALUES (?,'Sonda')",
                (ids['scartato'],))
    # Un apparecchio del terzo, per dimostrare che non viene toccato
    con.execute("INSERT INTO manutenzioni (apparecchio_id,tipo,data_intervento) "
                "VALUES (?,'correttiva','2026-01-01')", (ids['terzo'],))
    con.commit()
    return con, ids, s


def conta(con, tabella, apparecchio_id):
    return con.execute(
        f"SELECT COUNT(*) FROM {tabella} WHERE apparecchio_id = ?",
        (apparecchio_id,)).fetchone()[0]


def test_la_fusione_somma_gli_interventi_delle_due_schede(conn):
    """L'unica asserzione che distingue "ha fuso" da "ha fuso senza perdere
    niente". I figli hanno ON DELETE CASCADE: cancellare la scheda scartata
    prima di spostarli li porta via, e l'operazione riesce lo stesso."""
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    esito = fondi_apparecchi(con, ids['principale'], ids['scartato'])
    con.commit()

    assert conta(con, 'manutenzioni', ids['principale']) == 4   # 3 + 1
    assert conta(con, 'verifiche', ids['principale']) == 3      # 1 + 2
    assert conta(con, 'documenti', ids['principale']) == 1
    assert conta(con, 'accessori', ids['principale']) == 1
    assert esito['manutenzioni'] == 1    # quante ne ha SPOSTATE
    assert esito['verifiche'] == 2
    assert esito['documenti'] == 1
    assert esito['accessori'] == 1


def test_la_scheda_scartata_sparisce_e_la_principale_conserva_il_proprio_id(conn):
    """L'id della principale non cambia: i QR code stampati e attaccati
    sull'apparecchio restano validi. E' il motivo per cui la scelta di quale
    scheda sopravvive non e' indifferente."""
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    fondi_apparecchi(con, ids['principale'], ids['scartato'])
    con.commit()

    assert con.execute("SELECT COUNT(*) FROM apparecchi WHERE id=?",
                       (ids['scartato'],)).fetchone()[0] == 0
    riga = con.execute("SELECT id, matricola FROM apparecchi WHERE id=?",
                       (ids['principale'],)).fetchone()
    assert riga is not None and riga['matricola'] == 'R-00015'


def test_un_terzo_apparecchio_non_viene_toccato(conn):
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    fondi_apparecchi(con, ids['principale'], ids['scartato'])
    con.commit()
    assert conta(con, 'manutenzioni', ids['terzo']) == 1
    assert con.execute("SELECT COUNT(*) FROM apparecchi WHERE id=?",
                       (ids['terzo'],)).fetchone()[0] == 1


def test_nessun_allegato_resta_orfano(conn):
    """Nessun file si sposta: gli allegati stanno in
    uploads/strutture/<id>/<tipo>/, non in cartelle per apparecchio, quindi
    fondere due schede della stessa struttura cambia solo la riga che li
    referenzia. Il test lo inchioda: i percorsi devono essere ancora tutti
    li', invariati."""
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    prima = {r[0] for r in con.execute("SELECT verbale_path FROM manutenzioni")}
    prima |= {r[0] for r in con.execute("SELECT documento_path FROM verifiche")}

    fondi_apparecchi(con, ids['principale'], ids['scartato'])
    con.commit()

    dopo = {r[0] for r in con.execute("SELECT verbale_path FROM manutenzioni")}
    dopo |= {r[0] for r in con.execute("SELECT documento_path FROM verifiche")}
    assert dopo == prima


def test_i_riferimenti_di_import_preview_seguono_la_scheda_principale(conn):
    """import_preview.apparecchio_match_id non ha ON DELETE: se non lo si
    sposta, la cancellazione della scheda scartata fallisce con un errore di
    chiave esterna e l'intera fusione si annulla."""
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    imp = con.execute(
        "INSERT INTO import_history (struttura_id,tipo_import,filename,filepath) "
        "VALUES (?,'inventario','x.xlsx','x/x.xlsx')", (_s,)).lastrowid
    con.execute("INSERT INTO import_preview (import_id,riga_numero,dati_estratti,"
                "apparecchio_match_id) VALUES (?,1,'{}',?)", (imp, ids['scartato']))
    con.commit()

    esito = fondi_apparecchi(con, ids['principale'], ids['scartato'])
    con.commit()

    assert esito['preview'] == 1
    assert con.execute("SELECT apparecchio_match_id FROM import_preview").fetchone()[0] \
        == ids['principale']


def test_la_scheda_scartata_viene_restituita_per_intero(conn):
    """Il registro deve poter contenere la scheda cancellata campo per campo,
    cosi' ricostruirla a mano resta possibile: la fusione e' definitiva."""
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    esito = fondi_apparecchi(con, ids['principale'], ids['scartato'])
    con.commit()

    scartato = esito['scartato']
    assert scartato['matricola'] == 'R00015'
    assert scartato['anno_fabbricazione'] == 2019
    assert scartato['note'] == 'Rev. 2024'
    assert scartato['modello'] == 'OZY'


def test_fondere_una_scheda_con_se_stessa_e_rifiutato(conn):
    from fusione_service import fondi_apparecchi, FusioneRifiutataError
    con, ids, _s = conn
    with pytest.raises(FusioneRifiutataError):
        fondi_apparecchi(con, ids['principale'], ids['principale'])


def test_fondere_fra_strutture_diverse_e_rifiutato(conn):
    """La rotta controlla gia' l'accessibilita' di entrambe, ma la primitiva
    non deve dipendere dal fatto che il suo unico chiamante odierno lo faccia:
    e' l'ultima difesa dell'isolamento fra strutture."""
    from fusione_service import fondi_apparecchi, FusioneRifiutataError
    con, ids, _s = conn
    altra = con.execute("INSERT INTO strutture (nome,codice,attiva) VALUES ('Clinica B','B',1)").lastrowid
    div = con.execute("INSERT INTO divisioni (nome,codice,struttura_id) VALUES ('Cardio','CAR',?)",
                      (altra,)).lastrowid
    estraneo = con.execute(
        "INSERT INTO apparecchi (divisione_id,struttura_id,matricola,marca,modello,stato) "
        "VALUES (?,?,'B-1','SIEMENS','Y1','funzionante')", (div, altra)).lastrowid
    con.commit()

    with pytest.raises(FusioneRifiutataError):
        fondi_apparecchi(con, ids['principale'], estraneo)
    assert con.execute("SELECT COUNT(*) FROM apparecchi WHERE id=?",
                       (estraneo,)).fetchone()[0] == 1


def test_i_valori_scelti_finiscono_sulla_scheda_principale(conn):
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    esito = fondi_apparecchi(con, ids['principale'], ids['scartato'],
                             valori={'anno_fabbricazione': 2019, 'note': 'Rev. 2024'})
    con.commit()

    riga = con.execute("SELECT anno_fabbricazione, note, matricola FROM apparecchi WHERE id=?",
                       (ids['principale'],)).fetchone()
    assert riga['anno_fabbricazione'] == 2019
    assert riga['note'] == 'Rev. 2024'
    assert riga['matricola'] == 'R-00015'          # non scelta: resta la sua
    assert sorted(esito['valori_scelti']) == ['anno_fabbricazione', 'note']


def test_la_principale_puo_prendere_la_matricola_della_scartata(conn):
    """UNIQUE(struttura_id, modello, matricola) rifiuterebbe l'UPDATE finche'
    la scheda scartata esiste: i valori vanno applicati DOPO la cancellazione.
    E' il caso ordinario in cui si tiene la scheda con lo storico piu' lungo
    ma la matricola corretta e' quella dell'altra."""
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    fondi_apparecchi(con, ids['principale'], ids['scartato'],
                     valori={'matricola': 'R00015'})
    con.commit()
    assert con.execute("SELECT matricola FROM apparecchi WHERE id=?",
                       (ids['principale'],)).fetchone()[0] == 'R00015'


def test_un_campo_non_fondibile_viene_rifiutato(conn):
    """valori arriva da un form. I nomi di colonna finiscono in una f-string,
    quindi l'elenco dei campi ammessi e' una costante del modulo e non
    qualcosa che il chiamante puo' estendere."""
    from fusione_service import fondi_apparecchi, FusioneRifiutataError
    con, ids, _s = conn
    with pytest.raises(FusioneRifiutataError):
        fondi_apparecchi(con, ids['principale'], ids['scartato'],
                         valori={'struttura_id': 999})


def test_il_campo_id_non_e_fondibile_viene_rifiutato(conn):
    """Separato dal caso struttura_id: qui il valore e' quello dell'id del
    TERZO apparecchio (non quello, gia' proprio, della principale), cosi' che
    l'UPDATE, se eseguito, farebbe davvero qualcosa - collidere con una riga
    che esiste per davvero - invece di essere un'auto-assegnazione che non
    proverebbe nulla ne' con la guardia ne' senza."""
    from fusione_service import fondi_apparecchi, FusioneRifiutataError
    con, ids, _s = conn
    with pytest.raises(FusioneRifiutataError):
        fondi_apparecchi(con, ids['principale'], ids['scartato'],
                         valori={'id': ids['terzo']})
    assert con.execute("SELECT COUNT(*) FROM apparecchi WHERE id=?",
                       (ids['terzo'],)).fetchone()[0] == 1


def test_collisione_con_un_terzo_apparecchio_rifiuta_e_lo_nomina(conn):
    """Meglio un rifiuto che nomina il terzo apparecchio di un errore di
    database: chi legge deve capire che esiste gia' una scheda con quella
    matricola, e quale."""
    from fusione_service import fondi_apparecchi, FusioneCollisioneError
    con, ids, _s = conn
    con.execute("UPDATE apparecchi SET matricola='COLLIDE' WHERE id=?", (ids['terzo'],))
    con.commit()

    with pytest.raises(FusioneCollisioneError) as errore:
        fondi_apparecchi(con, ids['principale'], ids['scartato'],
                         valori={'matricola': 'COLLIDE'})
    assert errore.value.altro['id'] == ids['terzo']
    # E niente e' stato toccato: il rifiuto precede ogni scrittura.
    assert con.execute("SELECT COUNT(*) FROM apparecchi WHERE id=?",
                       (ids['scartato'],)).fetchone()[0] == 1
    assert conta(con, 'manutenzioni', ids['scartato']) == 1


def test_un_intervento_scartato_non_confluisce(conn):
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    da_scartare = con.execute(
        "SELECT id FROM verifiche WHERE apparecchio_id=? LIMIT 1",
        (ids['scartato'],)).fetchone()[0]

    esito = fondi_apparecchi(con, ids['principale'], ids['scartato'],
                             interventi_scartati=[('verifica', da_scartare)])
    con.commit()

    assert esito['interventi_scartati'] == 1
    assert conta(con, 'verifiche', ids['principale']) == 2      # 1 + 2 - 1
    assert con.execute("SELECT COUNT(*) FROM verifiche WHERE id=?",
                       (da_scartare,)).fetchone()[0] == 0


def test_si_puo_scartare_anche_un_intervento_della_scheda_principale(conn):
    """La coppia duplicata ha una copia su ciascuna scheda: chi conferma
    sceglie quale delle due buttare, e puo' benissimo essere quella della
    scheda che sopravvive."""
    from fusione_service import fondi_apparecchi
    con, ids, _s = conn
    da_scartare = con.execute(
        "SELECT id FROM manutenzioni WHERE apparecchio_id=? LIMIT 1",
        (ids['principale'],)).fetchone()[0]

    fondi_apparecchi(con, ids['principale'], ids['scartato'],
                     interventi_scartati=[('manutenzione', da_scartare)])
    con.commit()
    assert conta(con, 'manutenzioni', ids['principale']) == 3    # 3 + 1 - 1


def test_un_intervento_di_un_terzo_apparecchio_non_si_puo_scartare(conn):
    """interventi_scartati arriva da un form: un id qualunque non deve poter
    cancellare l'intervento di un apparecchio che non c'entra."""
    from fusione_service import fondi_apparecchi, FusioneRifiutataError
    con, ids, _s = conn
    estraneo = con.execute(
        "SELECT id FROM manutenzioni WHERE apparecchio_id=?",
        (ids['terzo'],)).fetchone()[0]

    with pytest.raises(FusioneRifiutataError):
        fondi_apparecchi(con, ids['principale'], ids['scartato'],
                         interventi_scartati=[('manutenzione', estraneo)])
    assert conta(con, 'manutenzioni', ids['terzo']) == 1


def test_valori_predefiniti_tiene_la_principale_tranne_dove_e_vuota(conn):
    """Nel caso comune basta confermare, e non si perde il dato che solo la
    scheda scartata aveva."""
    from fusione_service import valori_predefiniti
    con, ids, _s = conn
    p = dict(con.execute("SELECT * FROM apparecchi WHERE id=?", (ids['principale'],)).fetchone())
    s = dict(con.execute("SELECT * FROM apparecchi WHERE id=?", (ids['scartato'],)).fetchone())

    predefiniti = valori_predefiniti(p, s)
    assert predefiniti['matricola'] == 'R-00015'      # la principale ha un valore
    assert predefiniti['anno_fabbricazione'] == 2019  # la principale e' vuota
    assert predefiniti['note'] == 'Rev. 2024'         # idem
    assert 'marca' not in predefiniti                 # identici: non si sceglie


def test_valori_predefiniti_non_scambia_uno_zero_reale_per_vuoto(conn):
    """connesso_rete e' 0 o 1: uno 0 sulla principale e' un valore legittimo
    (l'apparecchio non e' in rete), non l'equivalente di campo assente. Un
    confronto ingenuo con `not a` lo confonderebbe con None o '' e la
    principale perderebbe uno zero corretto a favore dell'1 della scartata."""
    from fusione_service import valori_predefiniti
    con, ids, _s = conn
    con.execute("UPDATE apparecchi SET connesso_rete=0 WHERE id=?", (ids['principale'],))
    con.execute("UPDATE apparecchi SET connesso_rete=1 WHERE id=?", (ids['scartato'],))
    con.commit()
    p = dict(con.execute("SELECT * FROM apparecchi WHERE id=?", (ids['principale'],)).fetchone())
    s = dict(con.execute("SELECT * FROM apparecchi WHERE id=?", (ids['scartato'],)).fetchone())

    predefiniti = valori_predefiniti(p, s)
    assert predefiniti['connesso_rete'] == 0
