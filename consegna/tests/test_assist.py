"""L'assist e' il punto in cui il progetto puo' sforare la consegna."""
import math

import pytest

from assist import compute_assist
from config import config_from_dict
from prediction import TrackSnapshot


def track(x, y, vx=0.0, vy=0.0, tid=1, hits=10, missed=0, confirmed=True):
    return TrackSnapshot(tid, "rosso", x, y, vx, vy, 44, 44, 1.0, hits, missed, confirmed)


def cfg(**kw):
    return config_from_dict({"assist": kw}).assist


def test_correzione_mai_oltre_il_tetto():
    """Invariante numero uno: qualunque bersaglio, qualunque distanza."""
    c = cfg(max_correction_px=8, gate_px=1000, blend=1.0)
    for dx in range(-400, 401, 7):
        for dy in (-200, 0, 200):
            h = compute_assist((0.0, 0.0), [track(float(dx), float(dy))], c)
            assert math.hypot(h.dx, h.dy) <= 8 + 1e-9


def test_fuori_dal_gate_nessuna_correzione():
    """Invariante numero due: non si mira a un bersaglio che l'utente non sta guardando."""
    c = cfg(gate_px=78)
    assert compute_assist((0.0, 0.0), [track(500.0, 500.0)], c).mag == 0.0
    assert compute_assist((0.0, 0.0), [track(500.0, 500.0)], c).track_id is None


def test_sceglie_il_piu_vicino_alla_mira_non_il_piu_vicino_all_attuatore():
    c = cfg(gate_px=120)
    vicino = track(30.0, 0.0, tid=7)
    lontano = track(110.0, 0.0, tid=9)
    assert compute_assist((20.0, 0.0), [lontano, vicino], c).track_id == 7


def test_disabilitato_non_corregge():
    assert compute_assist((0.0, 0.0), [track(10.0, 0.0)], cfg(enabled=False)).mag == 0.0


def test_track_non_confermate_ignorate():
    c = cfg(min_hits=3)
    assert compute_assist((0.0, 0.0), [track(10.0, 0.0, hits=1, confirmed=False)], c).mag == 0.0


def test_track_troppo_vecchie_ignorate():
    c = cfg(max_missed=4)
    assert compute_assist((0.0, 0.0), [track(10.0, 0.0, missed=9)], c).mag == 0.0


def test_lista_vuota_non_crasha():
    assert compute_assist((0.0, 0.0), [], cfg()).mag == 0.0


def test_anticipo_segue_la_velocita():
    c = cfg(lead_ms=100, max_lead_ms=120, gate_px=400)
    h = compute_assist((0.0, 0.0), [track(0.0, 0.0, vx=200.0)], c)
    assert h.suggested == pytest.approx((20.0, 0.0))     # 200 px/s * 0.1 s


def test_anticipo_clampato_dal_tetto():
    """Anche sommando la latenza di rete l'anticipo non supera max_lead_ms."""
    c = cfg(lead_ms=100, max_lead_ms=120, gate_px=4000)
    h = compute_assist((0.0, 0.0), [track(0.0, 0.0, vx=1000.0)], c, latency_s=5.0)
    assert h.suggested[0] == pytest.approx(120.0)        # 1000 px/s * 0.12 s


def test_gia_centrato_nessuna_correzione():
    h = compute_assist((100.0, 100.0), [track(100.0, 100.0)], cfg())
    assert h.mag == 0.0 and h.track_id == 1


def test_la_correzione_punta_verso_il_bersaglio():
    h = compute_assist((0.0, 0.0), [track(100.0, 0.0)], cfg(gate_px=200))
    assert h.dx > 0 and h.dy == pytest.approx(0.0)


def test_l_orizzonte_di_previsione_e_tagliato_anche_con_l_intercetta():
    """
    Il vincolo esplicito della consegna: "non un calcolo esagerato che indovina
    dove sara' il nemico tra 2 secondi".

    L'intercetta e' fisicamente corretta ma il suo tempo di volo puo' valere
    secondi: qui il bersaglio scappa quasi alla velocita' del proiettile,
    quindi tau esplode. Il punto suggerito non deve comunque mai trovarsi piu'
    in la' di max_lead_ms * velocita': il tetto vale per costruzione, non per
    buona volonta'.
    """
    cfg = config_from_dict(
        {"assist": {"use_intercept": True, "max_lead_ms": 120, "marker_speed": 380, "gate_px": 200}}
    ).assist
    t = track(320.0, 100.0, 360.0, 0.0)          # 360 px/s contro 380 del colpo
    hint = compute_assist((320.0, 100.0), [t], cfg, origin=(320.0, 344.0))

    assert hint.suggested is not None
    spostamento = math.hypot(hint.suggested[0] - t.x, hint.suggested[1] - t.y)
    massimo = (cfg.max_lead_ms / 1000.0) * math.hypot(t.vx, t.vy)
    assert spostamento <= massimo + 1e-9


def test_il_tetto_vale_anche_quando_la_latenza_di_rete_e_enorme():
    # Una rete che va male non e' un permesso per indovinare piu' lontano.
    cfg = config_from_dict({"assist": {"max_lead_ms": 120, "gate_px": 200}}).assist
    t = track(200.0, 200.0, 300.0, -150.0)
    hint = compute_assist((200.0, 200.0), [t], cfg, latency_s=5.0)

    assert hint.suggested is not None
    spostamento = math.hypot(hint.suggested[0] - t.x, hint.suggested[1] - t.y)
    assert spostamento <= (cfg.max_lead_ms / 1000.0) * math.hypot(t.vx, t.vy) + 1e-9
