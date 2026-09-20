"""
Punto di ingresso. Un PC (due thread) oppure due PC in LAN.

  --role both     nodo A + nodo B in locale su loopback (default)
  --role capture  nodo A: poligono, finestra, mira e grilletto dell'utente
  --role ai       nodo B: detection, tracking, calcolo dell'assist

I ruoli sono gli stessi in entrambi i casi: `both` non e' una scorciatoia che
salta la rete, avvia il nodo B in un thread e lo fa parlare su 127.0.0.1 con lo
stesso protocollo usato fra due macchine. Cosi' la modalita' distribuita viene
esercitata a ogni esecuzione della demo, invece di essere codice mai percorso.

Protocollo
----------
  A -> B  frame   header !IHHd (frame_id, indice, totale, capture_ts) + JPEG
  A -> B  comando {"type":"aim", "x","y","sentAt"}
  A -> B  comando {"type":"fire_request", "x","y","useAssist","seq","sentAt","rttMs"}
  B -> A  comando {"type":"fire", "x","y","userX","userY","dx","dy",
                   "trackId","assist","seq","sentAt","echo"}
  entrambi        {"type":"ack", "echo": <sentAt del mittente>}

`echo` riporta sempre l'istante letto sull'orologio di CHI HA FATTO LA
RICHIESTA, cosi' l'RTT si calcola con due letture dello stesso clock: fra due
macchine gli orologi non sono sincronizzati e una differenza incrociata non
vorrebbe dire niente.
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
from pathlib import Path

import cv2

from assist import AssistHint, compute_assist
from capture import Shot, SyntheticArena, Webcam, decode_jpeg, encode_jpeg
from config import Config, ConfigError, load_config
from detection import detect_color_blobs
from input_control import FireCommand, VirtualTrigger
from network import CommandSocket, FrameSocket, command_loop
from prediction import MultiTracker, TrackSnapshot

log = logging.getLogger("kine")
WIN = "KINE - mira assistita"


# --------------------------------------------------------------------------- #
# Nodo B: occhi e cervello. Non spara, non decide quando sparare.
# --------------------------------------------------------------------------- #
class NodeB:
    def __init__(self, cfg: Config, peer_ip: str, stop: threading.Event):
        self.cfg = cfg
        self.stop = stop
        self.frames = FrameSocket(cfg.network.bind_ip, cfg.network.port_frames, cfg.network.max_pending_frames)
        self.cmds = CommandSocket(cfg.network.bind_ip, 0)
        self.peer_cmds = (peer_ip, cfg.network.port_cmds)
        self.tracker = MultiTracker(cfg.prediction)
        self.aim = (cfg.capture.width / 2, cfg.capture.height / 2)
        self.tracks: list[TrackSnapshot] = []
        self._last_frame_local = 0.0
        self._lock = threading.Lock()

    def run(self) -> None:
        threading.Thread(target=self._command_thread, name="B-cmds", daemon=True).start()
        log.info("[B] in ascolto frame su :%d, comandi da :%d", self.frames.port, self.cmds.port)
        try:
            while not self.stop.is_set():
                ricevuto = self.frames.recv_frame(self.cfg.network.timeout_ms / 1000)
                if ricevuto is None:
                    continue
                blob, capture_ts = ricevuto
                frame = decode_jpeg(blob)
                if frame is None:
                    log.debug("[B] JPEG corrotto, frame scartato")
                    continue
                dets = detect_color_blobs(frame, self.cfg.detection)
                snaps = self.tracker.update(dets, capture_ts)
                with self._lock:
                    self.tracks = snaps
                    self._last_frame_local = time.monotonic()
        finally:
            self.frames.close()
            self.cmds.close()

    def _command_thread(self) -> None:
        command_loop(self.cmds, self._on_cmd, self.stop, auto_ack=False)

    def _on_cmd(self, msg: dict, addr: tuple[str, int]) -> None:
        tipo = msg.get("type")
        if tipo == "aim":
            with self._lock:
                self.aim = (float(msg["x"]), float(msg["y"]))
        elif tipo == "fire_request":
            self.cmds.send_json(self._resolve_fire(msg), addr)

    def _resolve_fire(self, msg: dict) -> dict:
        user = (float(msg["x"]), float(msg["y"]))
        use_assist = bool(msg.get("useAssist", True))
        with self._lock:
            tracks = list(self.tracks)
            eta_frame = max(0.0, time.monotonic() - self._last_frame_local) if self._last_frame_local else 0.0
        # Il frame su cui sono calcolate le tracce e' vecchio di `eta_frame`, e
        # la risposta impieghera' ancora mezzo RTT per tornare al nodo A: la
        # somma e' l'anticipo che serve solo per compensare la rete, prima di
        # quello "estetico" di lead_ms.
        one_way = max(0.0, float(msg.get("rttMs", 0.0)) / 2000.0)
        hint = (
            compute_assist(user, tracks, self.cfg.assist, latency_s=eta_frame + one_way)
            if use_assist
            else AssistHint()
        )
        x, y = user[0] + hint.dx, user[1] + hint.dy
        return {
            "type": "fire",
            "userX": user[0],
            "userY": user[1],
            "x": x,
            "y": y,
            "dx": hint.dx,
            "dy": hint.dy,
            "trackId": hint.track_id if hint.active else None,
            "assist": hint.active,
            "seq": int(msg.get("seq", 0)),
            "sentAt": time.monotonic(),
            "echo": float(msg.get("sentAt", 0.0)),
            "suggested": list(hint.suggested) if hint.suggested else None,
        }


# --------------------------------------------------------------------------- #
# Nodo A: poligono, finestra, mira e grilletto dell'utente.
# --------------------------------------------------------------------------- #
class NodeA:
    def __init__(self, cfg: Config, peer_ip: str, stop: threading.Event, headless: bool = False):
        self.cfg = cfg
        self.stop = stop
        self.headless = headless
        self.arena = SyntheticArena(cfg.capture, cfg.detection.colors)
        self.frames = FrameSocket(cfg.network.bind_ip, 0, cfg.network.max_pending_frames)
        self.cmds = CommandSocket(cfg.network.bind_ip, cfg.network.port_cmds)
        self.peer_frames = (peer_ip, cfg.network.port_frames)
        self.peer_cmds: tuple[str, int] | None = None
        self.trigger = VirtualTrigger(cfg.assist.cooldown_ms)
        self.aim = [cfg.capture.width / 2, cfg.capture.height / 2]
        self.webcam: Webcam | None = None
        self.rtt_ms = 0.0
        self.suggested: tuple[float, float] | None = None
        self.last_assist_px = 0.0
        self._pending: dict[int, tuple[bool, float, int]] = {}  # seq -> (use_assist, errore, bersaglio)
        self._frame_id = 0
        self._last_aim_sent = 0.0

    # -- rete -------------------------------------------------------------- #
    def _command_thread(self) -> None:
        command_loop(self.cmds, self._on_cmd, self.stop, auto_ack=False)

    def _on_cmd(self, msg: dict, addr: tuple[str, int]) -> None:
        if msg.get("type") != "fire":
            return
        self.peer_cmds = addr
        echo = float(msg.get("echo", 0.0))
        if echo:
            self.rtt_ms = (time.monotonic() - echo) * 1000.0
        cmd = FireCommand.from_json(msg)
        sugg = msg.get("suggested")
        self.suggested = (float(sugg[0]), float(sugg[1])) if isinstance(sugg, list) and len(sugg) == 2 else None
        self.last_assist_px = (cmd.dx ** 2 + cmd.dy ** 2) ** 0.5
        _use_assist, aim_err, target_id = self._pending.pop(cmd.seq, (cmd.assist, 0.0, 1))
        self.arena.emit(
            cmd.x,
            cmd.y,
            self.cfg.assist.marker_speed,
            Shot(assisted=cmd.assist, aim_error_px=aim_err, target_id=target_id, track_id=cmd.track_id),
        )

    def _send_aim(self, now: float) -> None:
        if now - self._last_aim_sent < 1 / 30:
            return
        self._last_aim_sent = now
        self.cmds.send_json(
            {"type": "aim", "x": self.aim[0], "y": self.aim[1], "sentAt": now},
            (self.peer_frames[0], self.cfg.network.port_cmds) if self.peer_cmds is None else self.peer_cmds,
        )

    def fire(self, use_assist: bool) -> None:
        hint = AssistHint()  # il suggerimento lo calcola il nodo B, qui si chiede solo
        cmd = self.trigger.on_fire((self.aim[0], self.aim[1]), hint, use_assist=use_assist)
        if cmd is None:
            return  # cooldown
        err, bersaglio = self.arena.nearest_object(self.aim[0], self.aim[1])
        self._pending[cmd.seq] = (use_assist, err, bersaglio.id if bersaglio else 1)
        if len(self._pending) > 64:
            self._pending.clear()  # risposte mai arrivate: e' UDP
        self.cmds.send_json(
            {
                "type": "fire_request",
                "x": self.aim[0],
                "y": self.aim[1],
                "useAssist": use_assist,
                "seq": cmd.seq,
                "sentAt": time.monotonic(),
                "rttMs": self.rtt_ms,
            },
            (self.peer_frames[0], self.cfg.network.port_cmds) if self.peer_cmds is None else self.peer_cmds,
        )

    # -- loop --------------------------------------------------------------- #
    def run(self) -> None:
        threading.Thread(target=self._command_thread, name="A-cmds", daemon=True).start()
        if self.cfg.capture.source == "webcam":
            self.webcam = Webcam(0, self.cfg.capture.width, self.cfg.capture.height)
        if not self.headless:
            cv2.namedWindow(WIN)
            cv2.setMouseCallback(WIN, self._on_mouse)
            print("Click = sparo assistito | Shift+click = sparo puro | R = azzera metriche | Esc = esci")

        periodo = 1.0 / self.cfg.capture.fps
        last = time.monotonic()
        prossimo = last
        try:
            while not self.stop.is_set():
                now = time.monotonic()
                self.arena.step(now - last)
                last = now

                frame = self.webcam.read() if self.webcam else self.arena.render()
                if frame is None:
                    continue
                # Il frame che va in rete e' pulito: mirino e overlay si
                # disegnano solo sulla copia a schermo, altrimenti il nodo B
                # rileverebbe come bersagli i disegni del nodo A.
                self._send_frame(frame, now)
                self._send_aim(now)

                if not self.headless and not self._draw(frame):
                    break

                prossimo += periodo
                attesa = prossimo - time.monotonic()
                if attesa < -periodo:
                    prossimo = time.monotonic()  # siamo in ritardo: si riparte da adesso
                elif attesa > 0:
                    time.sleep(attesa)
        finally:
            self.close()

    def _send_frame(self, frame, now: float) -> None:
        self._frame_id += 1
        jpeg = encode_jpeg(frame, self.cfg.network.jpeg_quality)
        self.frames.send_jpeg(
            jpeg, self.peer_frames, self._frame_id, self.cfg.network.chunk_bytes, capture_ts=now
        )

    def _draw(self, frame) -> bool:
        vis = frame.copy()
        ax, ay = int(self.aim[0]), int(self.aim[1])
        cv2.drawMarker(vis, (ax, ay), (236, 236, 239), cv2.MARKER_CROSS, 16, 1)
        if self.suggested:
            sx, sy = int(self.suggested[0]), int(self.suggested[1])
            cv2.circle(vis, (sx, sy), 6, (204, 192, 183), 1)
            cv2.line(vis, (ax, ay), (sx, sy), (204, 192, 183), 1)
            cv2.putText(vis, "guarda li'", (sx + 8, sy - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (204, 192, 183), 1)
        s = self.arena.log.summary()
        righe = [
            f"RTT {self.rtt_ms:5.1f} ms   assist {self.last_assist_px:4.1f} px   frame persi {self.frames.frame_loss * 100:4.1f}%",
            f"assistiti  n={s['assistiti']['n']:3d}  a segno {s['assistiti']['hit_rate'] * 100:5.1f}%  mancato {s['assistiti']['miss_px']:5.1f} px",
            f"puri       n={s['puri']['n']:3d}  a segno {s['puri']['hit_rate'] * 100:5.1f}%  mancato {s['puri']['miss_px']:5.1f} px",
        ]
        for i, riga in enumerate(righe):
            cv2.putText(vis, riga, (8, 16 + i * 14), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (236, 236, 239), 1)
        cv2.imshow(WIN, vis)
        tasto = cv2.waitKey(1) & 0xFF
        if tasto == 27:
            return False
        if tasto in (ord("r"), ord("R")):
            self.arena.log.reset()
        return True

    def _on_mouse(self, event, x, y, flags, _param) -> None:
        self.aim[0], self.aim[1] = float(x), float(y)
        if event == cv2.EVENT_LBUTTONDOWN:
            self.fire(use_assist=not bool(flags & cv2.EVENT_FLAG_SHIFTKEY))

    def close(self) -> None:
        if self.webcam:
            self.webcam.close()
        self.frames.close()
        self.cmds.close()
        if not self.headless:
            cv2.destroyAllWindows()
        s = self.arena.log.summary()
        log.info(
            "riepilogo | assistiti n=%d a_segno=%.0f%% mancato=%.1f px | puri n=%d a_segno=%.0f%% mancato=%.1f px",
            s["assistiti"]["n"],
            s["assistiti"]["hit_rate"] * 100,
            s["assistiti"]["miss_px"],
            s["puri"]["n"],
            s["puri"]["hit_rate"] * 100,
            s["puri"]["miss_px"],
        )


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="KINE - mira assistita (progetto didattico)")
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.json")))
    ap.add_argument("--role", choices=("both", "capture", "ai"), default="both")
    ap.add_argument("--peer", default=None, help="IP dell'altro nodo")
    ap.add_argument("--bind", default=None, help="IP locale su cui mettersi in ascolto")
    ap.add_argument("--headless", action="store_true", help="senza finestra (test, macchine senza display)")
    ap.add_argument("--seconds", type=float, default=0.0, help="termina dopo N secondi (0 = mai)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)-12s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        log.error("configurazione non valida: %s", e)
        return 2

    if args.bind:
        cfg.network.bind_ip = args.bind
    peer = args.peer or cfg.network.peer_ip
    if args.role == "both":
        peer = "127.0.0.1"
        cfg.network.bind_ip = "127.0.0.1"

    stop = threading.Event()

    def _spegni(*_a):
        log.info("interruzione richiesta, chiusura in corso")
        stop.set()

    signal.signal(signal.SIGINT, _spegni)
    if args.seconds > 0:
        threading.Timer(args.seconds, stop.set).start()

    try:
        if args.role == "ai":
            NodeB(cfg, peer, stop).run()
            return 0
        b_thread = None
        if args.role == "both":
            nodo_b = NodeB(cfg, peer, stop)
            b_thread = threading.Thread(target=nodo_b.run, name="nodo-B", daemon=True)
            b_thread.start()
            time.sleep(0.1)  # lascia legare le socket prima del primo frame
        NodeA(cfg, peer, stop, headless=args.headless).run()
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
