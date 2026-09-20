import numpy as np

from capture import SyntheticArena
from config import config_from_dict
from detection import detect_color_blobs


def test_trova_i_tre_dischi():
    cfg = config_from_dict({})
    a = SyntheticArena(cfg.capture, cfg.detection.colors)
    dets = detect_color_blobs(a.render(), cfg.detection)
    assert sorted(d.name for d in dets) == ["azzurro", "rosso", "verde"]


def test_centroide_vicino_al_centro_del_disco():
    cfg = config_from_dict({})
    a = SyntheticArena(cfg.capture, cfg.detection.colors)
    dets = {d.name: d for d in detect_color_blobs(a.render(), cfg.detection)}
    for o in a.objs:
        d = dets[o.name]
        assert abs(d.cx - o.x) < 1.5 and abs(d.cy - o.y) < 1.5


def test_confidenza_di_un_bersaglio_intero_e_circa_uno():
    """Prima un disco pieno dava 0.30 e sembrava una detection dubbia."""
    cfg = config_from_dict({})
    a = SyntheticArena(cfg.capture, cfg.detection.colors)
    for d in detect_color_blobs(a.render(), cfg.detection):
        assert 0.8 <= d.confidence <= 1.0


def test_frame_vuoto_nessuna_detection():
    cfg = config_from_dict({})
    vuoto = np.zeros((cfg.capture.height, cfg.capture.width, 3), dtype=np.uint8)
    assert detect_color_blobs(vuoto, cfg.detection) == []


def test_nessun_bersaglio_rilevato_due_volte_su_frame_compresso():
    """
    Regressione. Con le soglie per classe prese indipendentemente, il JPEG a
    qualita' 70 spingeva i pixel del bordo dei dischi nell'intersezione fra due
    box cromatici: uno stesso disco veniva rilevato come 'verde' E 'azzurro',
    il tracker apriva due track sullo stesso bersaglio e l'assist saltava
    dall'una all'altra. Sul frame grezzo non accade, quindi il difetto restava
    invisibile finche' la modalita' a due nodi non funzionava.
    """
    from capture import SyntheticArena, decode_jpeg, encode_jpeg

    cfg = config_from_dict({})
    a = SyntheticArena(cfg.capture, cfg.detection.colors)
    for _ in range(60):
        a.step(1 / 24)
        dets = detect_color_blobs(decode_jpeg(encode_jpeg(a.render(), 70)), cfg.detection)
        assert len(dets) == len(a.objs), [d.name for d in dets]
        centri = [(d.cx, d.cy) for d in dets]
        for i, (x1, y1) in enumerate(centri):
            for x2, y2 in centri[i + 1:]:
                assert (x1 - x2) ** 2 + (y1 - y2) ** 2 > 100, "due detection sullo stesso blob"


def test_detection_sta_nel_budget_di_un_frame():
    import time

    from capture import SyntheticArena

    cfg = config_from_dict({})
    a = SyntheticArena(cfg.capture, cfg.detection.colors)
    frames = []
    for _ in range(20):
        a.step(1 / 24)
        frames.append(a.render())
    t0 = time.perf_counter()
    for f in frames:
        detect_color_blobs(f, cfg.detection)
    ms = (time.perf_counter() - t0) / len(frames) * 1000
    assert ms < 1000 / cfg.capture.fps, f"{ms:.1f} ms per frame: non sta nei {1000 / cfg.capture.fps:.0f} ms"
