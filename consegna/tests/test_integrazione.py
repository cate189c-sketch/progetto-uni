"""
Pipeline completa: poligono -> JPEG -> UDP a chunk -> detection -> Kalman ->
assist -> comando di sparo -> marcatore.

Serve perche' i moduli presi uno a uno possono essere tutti corretti e il
sistema restare inerte: nella versione precedente il nodo AI riceveva i frame,
tracciava, e non rimandava mai nulla. Nessun test di unita' lo avrebbe visto.
"""
import threading
import time

import pytest

from assist import compute_assist
from capture import Shot, SyntheticArena, decode_jpeg, encode_jpeg
from config import config_from_dict
from detection import detect_color_blobs
from prediction import MultiTracker


def cfg_locale():
    return config_from_dict({"network": {"bind_ip": "127.0.0.1", "peer_ip": "127.0.0.1",
                                         "port_frames": 0, "port_cmds": 0}})


def test_catena_completa_in_locale():
    """Frame reale, codificato, spezzato, ricostruito e analizzato."""
    from network import FrameSocket

    cfg = cfg_locale()
    arena = SyntheticArena(cfg.capture, cfg.detection.colors)
    tracker = MultiTracker(cfg.prediction)
    a = FrameSocket("127.0.0.1", 0)
    b = FrameSocket("127.0.0.1", 0)
    try:
        tracks = []
        for k in range(30):
            arena.step(1 / 24)
            jpeg = encode_jpeg(arena.render(), cfg.network.jpeg_quality)
            a.send_jpeg(jpeg, ("127.0.0.1", b.port), k + 1, cfg.network.chunk_bytes, capture_ts=k / 24)
            ricevuto = b.recv_frame(0.5)
            assert ricevuto is not None, f"frame {k} mai ricostruito"
            blob, ts = ricevuto
            frame = decode_jpeg(blob)
            assert frame is not None
            tracks = tracker.update(detect_color_blobs(frame, cfg.detection), ts)
    finally:
        a.close()
        b.close()

    assert len(tracks) == 3
    assert all(t.confirmed for t in tracks)
    # Le velocita' stimate non sono nulle: il Kalman integra davvero.
    assert any(abs(t.vx) > 20 for t in tracks)

    # E l'assist produce un suggerimento su un bersaglio vicino alla mira.
    bersaglio = tracks[0]
    hint = compute_assist((bersaglio.x + 10, bersaglio.y), tracks, cfg.assist, origin=arena.origin)
    assert hint.track_id == bersaglio.id
    assert 0 < hint.mag <= cfg.assist.max_correction_px


def test_il_nodo_ai_risponde_alla_richiesta_di_sparo():
    """
    Regressione del difetto piu' grave: `run_ai` riceveva i frame, aggiornava il
    tracker e non inviava MAI un comando. Il protocollo descritto nel README
    (B -> A con il fire) non esisteva, e la modalita' a due nodi era inerte.
    """
    from main import NodeB

    cfg = cfg_locale()
    cfg.network.port_frames = 0
    stop = threading.Event()
    b = NodeB(cfg, "127.0.0.1", stop)
    try:
        arena = SyntheticArena(cfg.capture, cfg.detection.colors)
        for k in range(20):
            arena.step(1 / 24)
            b.tracks = b.tracker.update(
                detect_color_blobs(arena.render(), cfg.detection), k / 24
            )
        b._last_frame_local = time.monotonic()
        bersaglio = b.tracks[0]

        risposta = b._resolve_fire({
            "type": "fire_request",
            "x": bersaglio.x + 12, "y": bersaglio.y,
            "useAssist": True, "seq": 3, "sentAt": 111.0, "rttMs": 40.0,
        })
    finally:
        stop.set()
        b.frames.close()
        b.cmds.close()

    assert risposta["type"] == "fire"
    assert risposta["seq"] == 3
    assert risposta["echo"] == 111.0          # RTT calcolabile sull'orologio di A
    assert risposta["assist"] is True
    assert risposta["trackId"] == bersaglio.id
    assert (risposta["dx"] ** 2 + risposta["dy"] ** 2) ** 0.5 <= cfg.assist.max_correction_px + 1e-9
    assert risposta["suggested"] is not None


def test_sparo_puro_non_riceve_correzione():
    from main import NodeB

    cfg = cfg_locale()
    stop = threading.Event()
    b = NodeB(cfg, "127.0.0.1", stop)
    try:
        arena = SyntheticArena(cfg.capture, cfg.detection.colors)
        for k in range(20):
            arena.step(1 / 24)
            b.tracks = b.tracker.update(detect_color_blobs(arena.render(), cfg.detection), k / 24)
        t = b.tracks[0]
        r = b._resolve_fire({"x": t.x + 5, "y": t.y, "useAssist": False, "seq": 1, "sentAt": 0.0})
    finally:
        stop.set()
        b.frames.close()
        b.cmds.close()
    assert r["dx"] == 0.0 and r["dy"] == 0.0 and r["assist"] is False
    assert r["x"] == pytest.approx(t.x + 5)


def test_l_assist_riduce_la_distanza_di_mancato():
    """
    La dimostrazione che il progetto deve fare: con la stessa mano tremante,
    l'assist avvicina i colpi al bersaglio.
    """
    import math
    import random

    def campagna(assist_on: bool) -> float:
        cfg = config_from_dict({"assist": {"enabled": assist_on, "cooldown_ms": 0}})
        arena = SyntheticArena(cfg.capture, cfg.detection.colors, seed=3)
        tracker = MultiTracker(cfg.prediction)
        rng = random.Random(3)
        t = 0.0
        sparati = 0
        while sparati < 60:
            arena.step(1 / 24)
            t += 1 / 24
            tracks = tracker.update(detect_color_blobs(arena.render(), cfg.detection), t)
            if tracks and sparati * 5 < int(t * 24):
                o = arena.objs[sparati % len(arena.objs)]
                ang, raggio = rng.uniform(0, 6.28), abs(rng.gauss(0, 16))
                mira = (o.x + math.cos(ang) * raggio, o.y + math.sin(ang) * raggio)
                h = compute_assist(mira, tracks, cfg.assist, origin=arena.origin)
                arena.emit(mira[0] + h.dx, mira[1] + h.dy, cfg.assist.marker_speed,
                           Shot(assisted=h.active, aim_error_px=raggio, target_id=o.id))
                sparati += 1
        while arena.markers:
            arena.step(1 / 24)
        return arena.log.summary()["totale"]["miss_px"]

    senza, con = campagna(False), campagna(True)
    assert con < senza, f"assist inefficace: {con:.2f} px contro {senza:.2f} px"
