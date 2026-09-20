"""Kalman e associazione dati."""
import math

import numpy as np
import pytest

from config import config_from_dict
from detection import Detection
from prediction import KalmanCV, MultiTracker


def det(name, cx, cy, w=44.0, h=44.0):
    return Detection(name, cx - w / 2, cy - h / 2, w, h, cx, cy, 1500.0, 1.0)


def test_stima_la_velocita_di_un_moto_uniforme():
    """
    La prima versione teneva F = I: lo stato non integrava mai la velocita',
    che restava a zero, e l'anticipo non anticipava niente.
    """
    cfg = config_from_dict({})
    tr = MultiTracker(cfg.prediction)
    for k in range(50):
        t = k / 24
        snaps = tr.update([det("rosso", 100 + 200 * t, 150)], t)
    assert snaps[0].vx == pytest.approx(200, abs=8)
    assert snaps[0].vy == pytest.approx(0, abs=8)


def test_id_stabile_su_tutta_la_traiettoria():
    cfg = config_from_dict({})
    tr = MultiTracker(cfg.prediction)
    ids = set()
    for k in range(60):
        t = k / 24
        ids.update(s.id for s in tr.update([det("rosso", 100 + 150 * t, 150)], t))
    assert ids == {1}


def test_covarianza_resta_simmetrica_e_definita_positiva():
    """
    Forma di Joseph. Con `P = (I-KH)P` gli errori di arrotondamento rompono la
    simmetria e dopo molti update P puo' diventare non definita positiva: il
    filtro smette di correggere senza dare errori.
    """
    kf = KalmanCV(100.0, 100.0, 6.0, 28.0)
    for k in range(5000):
        kf.predict(1 / 24)
        kf.update(100.0 + k, 100.0)
    assert np.allclose(kf.P, kf.P.T, atol=1e-9)
    assert np.all(np.linalg.eigvalsh(kf.P) > 0)


def test_traccia_sopravvive_a_occlusioni_brevi():
    cfg = config_from_dict({})
    tr = MultiTracker(cfg.prediction)
    for k in range(20):
        tr.update([det("rosso", 100 + 200 * (k / 24), 150)], k / 24)
    for k in range(20, 30):          # 10 frame senza misure
        snaps = tr.update([], k / 24)
    assert len(snaps) == 1 and snaps[0].id == 1
    assert snaps[0].missed == 10


def test_traccia_scade_dopo_max_missed():
    cfg = config_from_dict({"prediction": {"max_missed": 5}})
    tr = MultiTracker(cfg.prediction)
    tr.update([det("rosso", 100, 150)], 0.0)
    for k in range(1, 12):
        snaps = tr.update([], k / 24)
    assert snaps == []


def test_una_traccia_non_ruba_la_detection_di_un_altra():
    """
    Regressione dell'associazione greedy per-traccia.

    Due bersagli della stessa classe (con objectCount > 3 la palette si ripete),
    id 1 a x=0 e id 2 a x=50. Ne resta visibile uno solo, a x=49: appartiene
    ovviamente a id 2, che dista 1 px. Ma il vecchio ciclo faceva scegliere per
    prima la traccia che capitava prima nella lista, e id 1 se la prendeva
    perche' 49 px rientravano comunque nel gate: id 1 saltava a x~13 e id 2
    andava in missed. Ordinando TUTTE le coppie per distanza il conflitto si
    risolve a favore della piu' vicina.
    """
    cfg = config_from_dict({"prediction": {"gate_px": 72, "min_hits": 1}})
    tr = MultiTracker(cfg.prediction)
    for k in range(6):
        tr.update([det("rosso", 0.0, 150.0), det("rosso", 50.0, 150.0)], k / 24)
    snaps = {s.id: s for s in tr.update([det("rosso", 49.0, 150.0)], 6 / 24)}

    assert snaps[2].missed == 0, "la detection doveva andare alla traccia a 1 px"
    assert snaps[1].missed == 1
    assert snaps[1].x == pytest.approx(0.0, abs=2.0), "la traccia 1 non deve saltare"


def test_incrocio_di_due_bersagli_stessa_classe_mantiene_gli_id():
    cfg = config_from_dict({"prediction": {"gate_px": 120}})
    tr = MultiTracker(cfg.prediction)
    identita = []
    for k in range(120):
        t = k / 60
        a_x = 100 + 200 * t          # va verso destra
        b_x = 400 - 200 * t          # va verso sinistra, si incrociano a meta'
        snaps = tr.update([det("rosso", a_x, 150), det("rosso", b_x, 150)], t)
        if t > 1.2:                  # dopo l'incrocio
            identita.append(min(snaps, key=lambda s: abs(s.x - a_x)).id)
    assert len(set(identita)) == 1, f"id scambiato durante l'incrocio: {set(identita)}"


def test_velocita_clampata():
    cfg = config_from_dict({"prediction": {"max_velocity": 100, "gate_px": 400}})
    tr = MultiTracker(cfg.prediction)
    for k in range(30):
        snaps = tr.update([det("rosso", 100 + 900 * (k / 24), 150)], k / 24)
    assert math.hypot(snaps[0].vx, snaps[0].vy) <= 100 + 1e-6


def test_use_kalman_false_azzera_l_anticipo():
    cfg = config_from_dict({"prediction": {"use_kalman": False}})
    tr = MultiTracker(cfg.prediction)
    for k in range(30):
        snaps = tr.update([det("rosso", 100 + 200 * (k / 24), 150)], k / 24)
    assert snaps[0].vx == 0.0 and snaps[0].vy == 0.0


def test_track_non_confermata_finche_non_raccoglie_min_hits():
    cfg = config_from_dict({"prediction": {"min_hits": 3}})
    tr = MultiTracker(cfg.prediction)
    assert not tr.update([det("rosso", 100, 150)], 0.0)[0].confirmed
    assert not tr.update([det("rosso", 102, 150)], 1 / 24)[0].confirmed
    assert tr.update([det("rosso", 104, 150)], 2 / 24)[0].confirmed
