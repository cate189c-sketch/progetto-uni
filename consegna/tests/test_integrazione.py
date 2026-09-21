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
        # Una traccia VIVA: la lista conserva anche quelle in via di
        # estinzione (missed alto), che l'assist scarta per regolamento.
        vive = [t for t in b.tracks if t.confirmed and t.missed == 0]
        assert vive, "nessuna traccia viva al momento della mira"
        bersaglio = vive[0]

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


def test_il_nodo_b_suggerisce_gia_mentre_l_utente_mira():
    """
    Il requisito 4 della consegna nella sua versione aggiornata: l'AI
    interviene AL MOMENTO DELLA MIRA, non solo allo sparo. Qui gira tutto sulla
    rete vera - frame in UDP a chunk, comandi in UDP - e si verifica che a un
    messaggio "aim" il nodo B risponda con un "hint".

    Il punto non e' il protocollo in se': e' che il suggerimento esista PRIMA
    del colpo. Un assist che parla solo quando il grilletto e' gia' premuto
    non e' un assistente che dice "guarda li'", e' una correzione subita.
    """
    from main import NodeB
    from network import CommandSocket, FrameSocket

    cfg = cfg_locale()
    stop = threading.Event()
    b = NodeB(cfg, "127.0.0.1", stop)
    threading.Thread(target=b.run, name="B-test", daemon=True).start()

    arena = SyntheticArena(cfg.capture, cfg.detection.colors)
    mittente = FrameSocket("127.0.0.1", 0)
    utente = CommandSocket("127.0.0.1", 0)
    try:
        # Il nodo B deve prima vedere abbastanza frame da confermare le tracce.
        for k in range(40):
            arena.step(1 / 24)
            mittente.send_jpeg(
                encode_jpeg(arena.render(), cfg.network.jpeg_quality),
                ("127.0.0.1", b.frames.port), k + 1, cfg.network.chunk_bytes,
                capture_ts=time.monotonic(),
            )
            time.sleep(0.004)

        scadenza = time.monotonic() + 2.0
        while not b.tracks and time.monotonic() < scadenza:
            time.sleep(0.02)
        assert b.tracks, "il nodo B non ha tracciato niente"

        # Una traccia VIVA: la lista conserva anche quelle in via di
        # estinzione (missed alto), che l'assist scarta per regolamento.
        vive = [t for t in b.tracks if t.confirmed and t.missed == 0]
        assert vive, "nessuna traccia viva al momento della mira"
        bersaglio = vive[0]
        # L'utente sta MIRANDO vicino al bersaglio: non ha ancora sparato.
        utente.send_json(
            {"type": "aim", "x": bersaglio.x + 12, "y": bersaglio.y, "sentAt": 777.0, "rttMs": 0.0},
            ("127.0.0.1", b.cmds.port),
        )
        risposta = None
        scadenza = time.monotonic() + 2.0
        while risposta is None and time.monotonic() < scadenza:
            ricevuto = utente.recv_json()
            if ricevuto is not None:
                risposta = ricevuto[0]
    finally:
        stop.set()
        mittente.close()
        utente.close()

    assert risposta is not None, "nessun suggerimento arrivato mentre l'utente mirava"
    assert risposta["type"] == "hint"
    assert risposta["suggested"] is not None
    assert risposta["trackId"] == bersaglio.id
    # L'echo e' l'orologio di CHI CHIEDE: l'RTT si misura senza sincronizzare
    # gli orologi delle due macchine.
    assert risposta["echo"] == 777.0
    # E resta un suggerimento: la correzione che porta con se' e' clampata come
    # quella dello sparo, ma qui non viene applicata a niente.
    assert (risposta["dx"] ** 2 + risposta["dy"] ** 2) ** 0.5 <= cfg.assist.max_correction_px + 1e-9


def test_pipeline_su_video_vero_stima_le_velocita_giuste(tmp_path):
    """
    Requisito 1 + 2 della consegna, verificati insieme su VIDEO e non sul
    poligono: i frame arrivano da un file, passano dalla compressione JPEG
    della rete, e il Kalman deve ricostruire velocita' che corrispondono al
    moto reale.

    La clip e' generata qui con velocita' note - 4 px/frame e 3 px/frame a
    24 fps, cioe' 96 e -72 px/s - quindi esiste una verita' di riferimento
    anche senza poligono, ed e' l'unico modo di dire se il filtro stima o
    inventa.
    """
    import cv2
    import numpy as np

    from detection import make_detector
    from sources import VideoFileSource

    clip = tmp_path / "gameplay.avi"
    scrittore = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"MJPG"), 24, (640, 360))
    if not scrittore.isOpened():  # pragma: no cover - dipende dai codec installati
        pytest.skip("nessun codec MJPG disponibile")
    for i in range(100):
        f = np.full((360, 640, 3), 18, np.uint8)
        cv2.circle(f, (60 + i * 4, 120), 26, (40, 40, 220), -1)      # +96 px/s
        cv2.circle(f, (500 - i * 3, 240), 22, (40, 40, 215), -1)     # -72 px/s
        scrittore.write(f)
    scrittore.release()

    cfg = config_from_dict(
        {
            "capture": {"source": "video", "video_path": str(clip), "video_loop": False},
            "detection": {
                "mode": "hsv",
                "colors": [{"name": "nemico", "bgr": [40, 40, 220], "hue_tol": 10,
                            "sat_min": 110, "val_min": 70}],
                "min_area": 150,
                "nominal_area": 2000,
                "open_kernel": 3,
            },
        }
    )
    detect = make_detector(cfg.detection)
    tracker = MultiTracker(cfg.prediction)

    n = 0
    tracks = []
    with VideoFileSource(cfg.capture) as src:
        while (frame := src.read()) is not None and n < 100:
            # Passaggio dalla codifica di rete: il nodo B non vede mai il frame
            # originale, vede quello ricostruito da JPEG.
            frame = decode_jpeg(encode_jpeg(frame, cfg.network.jpeg_quality))
            tracks = tracker.update(detect(frame), n / 24)
            n += 1

    assert n == 100, "la clip non e' stata letta per intero"
    assert len(tracks) == 2
    assert all(t.confirmed and t.missed == 0 for t in tracks)

    velocita = sorted(t.vx for t in tracks)
    assert velocita[0] == pytest.approx(-72.0, abs=8.0)
    assert velocita[1] == pytest.approx(+96.0, abs=8.0)
    # Moto orizzontale: la componente verticale stimata deve restare vicina a
    # zero, altrimenti l'assist anticiperebbe verso l'alto o il basso.
    assert all(abs(t.vy) < 12.0 for t in tracks)


def test_i_due_nodi_montati_come_in_main_si_parlano_davvero(tmp_path):
    """
    Il test che mancava, e che l'assenza di ha lasciato passare un difetto
    grave: gli altri test di integrazione mandano i comandi direttamente a
    `b.cmds.port`, cioe' alla porta che il nodo B ha ottenuto di fatto. Cosi'
    non verificano mai la cosa che conta fra due macchine - che il nodo A
    sappia DOVE trovare il nodo B partendo dalla sola configurazione.

    Il difetto: il nodo B legava la porta comandi in modo effimero, mentre il
    nodo A indirizzava `network.port_cmds`. Su due PC i comandi finivano nel
    vuoto; in locale tornavano al nodo A stesso. Nessun errore, nessun log: il
    canale semplicemente non esisteva, e la demo sembrava funzionare perche' in
    modalita' headless nessuno premeva il grilletto.

    Qui i due nodi si costruiscono come li costruisce `main()`, e si verifica
    che il suggerimento arrivi davvero dall'altra parte.
    """
    import cv2
    import numpy as np

    from main import NodeA, NodeB

    clip = tmp_path / "due_nodi.avi"
    scrittore = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"MJPG"), 24, (640, 360))
    if not scrittore.isOpened():  # pragma: no cover - dipende dai codec installati
        pytest.skip("nessun codec MJPG disponibile")
    for i in range(96):
        f = np.full((360, 640, 3), 18, np.uint8)
        cv2.circle(f, (60 + i * 4, 180), 26, (40, 40, 220), -1)
        scrittore.write(f)
    scrittore.release()

    cfg = config_from_dict(
        {
            "capture": {"source": "video", "video_path": str(clip), "aim_mode": "mouse", "fps": 30},
            # Porte fisse e non effimere: e' esattamente il punto del test.
            # I due nodi devono accordarsi attraverso la configurazione.
            "network": {"bind_ip": "127.0.0.1", "peer_ip": "127.0.0.1",
                        "port_frames": 5655, "port_cmds": 5665},
            "detection": {
                "mode": "hsv",
                "colors": [{"name": "nemico", "bgr": [40, 40, 220], "hue_tol": 10,
                            "sat_min": 110, "val_min": 70}],
                "min_area": 150,
                "nominal_area": 2000,
                "open_kernel": 3,
            },
        }
    )

    stop = threading.Event()
    b = NodeB(cfg, "127.0.0.1", stop)
    threading.Thread(target=b.run, name="B-due-nodi", daemon=True).start()
    time.sleep(0.2)
    a = NodeA(cfg, "127.0.0.1", stop, headless=True)
    threading.Thread(target=a.run, name="A-due-nodi", daemon=True).start()
    try:
        scadenza = time.monotonic() + 10.0
        while a.suggested is None and time.monotonic() < scadenza:
            vive = [t for t in b.tracks if t.confirmed and t.missed == 0]
            if vive:
                a.aim[0], a.aim[1] = vive[0].x + 15, vive[0].y + 4
            time.sleep(0.03)
        suggerito, rtt, dx, dy, n = a.suggested, a.rtt_ms, a.hint_dx, a.hint_dy, a.n_tracks
    finally:
        stop.set()
        time.sleep(0.3)

    assert suggerito is not None, "nessun suggerimento e' mai tornato dal nodo B"
    assert n >= 1, "il nodo B non ha tracciato niente dai frame ricevuti"
    # RTT misurato su loopback: piccolo ma non nullo, e soprattutto CALCOLATO
    # (resta 0 se il campo `echo` non torna indietro).
    assert 0.0 < rtt < 1000.0
    # La correzione che il suggerimento porta con se' resta clampata.
    assert (dx ** 2 + dy ** 2) ** 0.5 <= cfg.assist.max_correction_px + 1e-9
