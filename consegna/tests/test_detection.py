import cv2
import numpy as np
import pytest

from capture import SyntheticArena
from conftest import CONSEGNA
from config import config_from_dict, load_config
from detection import CLASSE_MOTION, detect_color_blobs, make_detector


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


# --------------------------------------------------------------------------- #
# Modalita' HSV: la ragione per cui esiste
# --------------------------------------------------------------------------- #
def _scurisci(frame, fattore):
    """Stessa scena, meno luce: e' quello che fa un'ombra in un gioco."""
    return (frame.astype(np.float32) * fattore).astype(np.uint8)


def test_in_ombra_la_soglia_bgr_perde_i_bersagli_e_hsv_no():
    """
    Il test che giustifica detection.mode='hsv' nella configurazione della
    consegna. Non e' un gusto estetico: al 50% di luminosita' il cubo BGR non
    contiene piu' nessuno dei tre bersagli - la detection restituisce zero
    oggetti e il tracker non ha niente da seguire - mentre la soglia sulla
    tinta li trova tutti e tre. L'ombra abbassa V e lascia H dov'era.
    """
    cfg = load_config(CONSEGNA / "config.json")
    arena = SyntheticArena(cfg.capture, cfg.detection.colors)
    ombra = _scurisci(arena.render(), 0.5)

    cfg.detection.mode = "bgr"
    assert detect_color_blobs(ombra, cfg.detection) == []

    cfg.detection.mode = "hsv"
    trovati = {d.name for d in detect_color_blobs(ombra, cfg.detection)}
    assert trovati == {"rosso", "verde", "azzurro"}


def test_in_hsv_il_centroide_non_si_sposta_con_la_luce():
    # Se l'ombra spostasse il centroide, il Kalman leggerebbe una velocita'
    # che non esiste e l'assist anticiperebbe nella direzione sbagliata.
    cfg = load_config(CONSEGNA / "config.json")
    cfg.detection.mode = "hsv"
    arena = SyntheticArena(cfg.capture, cfg.detection.colors)
    pieno = arena.render()

    a = {d.name: d for d in detect_color_blobs(pieno, cfg.detection)}
    b = {d.name: d for d in detect_color_blobs(_scurisci(pieno, 0.6), cfg.detection)}
    assert set(a) == set(b)
    for nome in a:
        assert (a[nome].cx, a[nome].cy) == pytest.approx((b[nome].cx, b[nome].cy), abs=1.0)


def test_il_rosso_a_cavallo_dello_zero_viene_trovato_tutto():
    """
    La tinta e' un angolo: 179 e 0 sono adiacenti e il rosso sta proprio li'.
    Un intervallo [h0-tol, h0+tol] preso alla lettera taglierebbe a meta' il
    blob. Qui il bersaglio ha tinta ~0, quindi l'intervallo scavalca: l'area
    trovata deve essere quella del disco intero, non la meta'.
    """
    cfg = config_from_dict(
        {
            "detection": {
                "mode": "hsv",
                "colors": [{"name": "rosso", "bgr": [0, 0, 255], "hue_tol": 12, "sat_min": 80, "val_min": 60}],
                "min_area": 100,
                "nominal_area": 1500,
            }
        }
    )
    frame = np.full((240, 320, 3), 10, np.uint8)
    # Due rossi che cadono ai due lati dello zero: uno vira all'arancio, l'altro
    # al magenta. Con l'intervallo tagliato ne sopravviverebbe uno solo.
    cv2.circle(frame, (90, 120), 25, (0, 20, 255), -1)
    cv2.circle(frame, (220, 120), 25, (20, 0, 255), -1)
    dets = detect_color_blobs(frame, cfg.detection)
    assert len(dets) == 2
    for d in dets:
        assert d.area > 1500          # disco pieno: pi*25^2 ~ 1963


# --------------------------------------------------------------------------- #
# Modalita' motion
# --------------------------------------------------------------------------- #
def test_motion_trova_cio_che_si_muove_senza_nessuna_palette():
    cfg = config_from_dict({"detection": {"mode": "motion", "colors": [], "min_area": 150}})
    det = make_detector(cfg.detection)

    sfondo = np.full((240, 320, 3), 60, np.uint8)
    cv2.rectangle(sfondo, (0, 180), (320, 240), (90, 90, 90), -1)  # un fondo con struttura
    for _ in range(30):
        det(sfondo)                    # il modello di sfondo si impara qui

    mobile = sfondo.copy()
    cv2.circle(mobile, (160, 100), 24, (200, 200, 200), -1)
    dets = det(mobile)
    assert len(dets) == 1
    assert dets[0].name == CLASSE_MOTION
    assert (dets[0].cx, dets[0].cy) == pytest.approx((160, 100), abs=4)


def test_il_detector_motion_va_riusato_non_ricostruito():
    """
    MOG2 ha stato: il modello di sfondo si costruisce frame dopo frame, e sul
    primo frame in assoluto non ha niente con cui confrontare - restituisce
    una maschera vuota. Costruire un detector nuovo dentro il loop significa
    quindi essere PERMANENTEMENTE al primo frame: zero detection per sempre,
    con un log pulito e nessun errore. E' il motivo per cui make_detector
    viene chiamata una volta in NodeB.__init__ e non nel ciclo.
    """
    cfg = config_from_dict({"detection": {"mode": "motion", "colors": [], "min_area": 150}})

    def scena(i):
        f = np.full((240, 320, 3), 60, np.uint8)
        cv2.rectangle(f, (0, 180), (320, 240), (90, 90, 90), -1)
        cv2.circle(f, (40 + i * 8, 100), 24, (200, 200, 200), -1)
        return f

    riusato = make_detector(cfg.detection)
    trovate_riusando = sum(len(riusato(scena(i))) for i in range(30))
    trovate_ricostruendo = sum(len(make_detector(cfg.detection)(scena(i))) for i in range(30))

    assert trovate_riusando > 20
    assert trovate_ricostruendo == 0
