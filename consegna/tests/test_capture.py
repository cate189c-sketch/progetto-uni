"""Il poligono: rimbalzi che non incastrano e metriche che misurano qualcosa."""
import math

from capture import Shot, SyntheticArena
from config import config_from_dict


def arena():
    cfg = config_from_dict({})
    return SyntheticArena(cfg.capture, cfg.detection.colors)


def test_oggetti_restano_nell_arena():
    a = arena()
    for _ in range(2000):
        a.step(1 / 24)
    for o in a.objs:
        assert 0 <= o.x <= a.w and 0 <= o.y <= a.h


def test_disco_lento_non_resta_incastrato_nel_muro():
    """
    Regressione. Con `if fuori: vx *= -1` un disco lento invertiva il segno a
    ogni frame e vibrava contro il bordo per sempre, senza mai rientrare.
    """
    a = arena()
    a.objs = a.objs[:1]
    a.objs[0].x, a.objs[0].vx, a.objs[0].vy = 20.0, -5.0, 0.0
    for _ in range(40):
        a.step(1 / 24)
    assert a.objs[0].vx > 0
    assert a.objs[0].x > 24.0


def test_dt_grande_non_teletrasporta_fuori():
    a = arena()
    a.objs = a.objs[:1]
    a.objs[0].x, a.objs[0].vx = 610.0, 300.0
    for _ in range(10):
        a.step(0.5)  # finestra trascinata / processo in swap
        assert 0 <= a.objs[0].x <= a.w


def _spara(errore_px: float):
    a = arena()
    a.objs = a.objs[:1]
    o = a.objs[0]
    o.x, o.y, o.vx, o.vy = 320.0, 120.0, 0.0, 0.0
    a.emit(320.0 + errore_px, 120.0, 380.0,
           Shot(assisted=False, aim_error_px=errore_px, target_id=o.id))
    for _ in range(400):
        a.step(1 / 60)
        if not a.markers:
            break
    return a.log.shots[0]


def test_miss_px_e_monotona_con_l_errore_di_mira():
    """
    La vecchia metrica registrava la distanza alla quale SCATTA la collisione,
    cioe' circa il raggio: restava ~23 px con qualunque mira, e non misurava
    niente. Questa e' la distanza di mancato dalla superficie del bersaglio:
    0 se il colpo va a segno, e cresce con l'errore.
    """
    valori = [_spara(e).miss_px for e in (0.0, 30.0, 60.0, 120.0)]
    assert valori == sorted(valori), valori
    assert valori[0] == 0.0
    assert valori[-1] > 60.0


def test_colpo_centrato_e_un_hit_con_mancato_zero():
    s = _spara(0.0)
    assert s.hit and s.miss_px == 0.0


def test_colpo_largo_e_un_miss():
    s = _spara(120.0)
    assert not s.hit and s.miss_px > 0.0


def test_render_non_disegna_il_mirino_nel_frame_di_rete():
    """Il nodo B non deve rilevare come bersaglio un overlay del nodo A."""
    a = arena()
    pulito = a.render(aim=(100, 100))
    con_hud = a.render(aim=(100, 100), hud=True)
    assert not (pulito == con_hud).all()
