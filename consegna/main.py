"""
Punto di ingresso. Due PC in LAN, oppure un PC con due thread per la prova.

  --role capture  nodo A: il gioco, la cattura video, il mirino e il grilletto
  --role ai       nodo B: detection, tracking, calcolo dell'assist
  --role both     A + B in locale su 127.0.0.1 (default)

I ruoli sono gli stessi nei due casi: `both` non e' una scorciatoia che salta
la rete, avvia il nodo B in un thread e lo fa parlare in UDP su loopback con lo
stesso protocollo usato fra due macchine. Cosi' la modalita' distribuita viene
esercitata a ogni esecuzione, invece di essere codice mai percorso.

Divisione del lavoro (requisito 5 della consegna)
-------------------------------------------------
  nodo A  il PC su cui si gioca: cattura lo schermo, tiene il mirino, riceve i
          suggerimenti e li mostra. Non analizza niente.
  nodo B  il PC che guarda: decodifica i frame, rileva, traccia, stima le
          velocita' e risponde. Non vede il mouse e non spara.

La separazione non e' decorativa: il nodo B fa il lavoro pesante (detection +
Kalman) e lo fa su un'altra macchina, quindi il frame rate del gioco sul nodo A
non paga il costo della visione.

Protocollo
----------
  A -> B  frame   header !IHHd (frame_id, indice, totale, capture_ts) + JPEG
  A -> B  {"type":"aim", "x","y","sentAt"}
  B -> A  {"type":"hint", "suggested","dx","dy","trackId","echo","sentAt"}
  A -> B  {"type":"fire_request", "x","y","useAssist","seq","sentAt","rttMs"}
  B -> A  {"type":"fire", "x","y","userX","userY","dx","dy","trackId",
           "assist","seq","sentAt","echo","suggested"}

`hint` e' l'assistenza AL MOMENTO DELLA MIRA: mentre l'utente punta, il nodo B
dice dove sta il bersaglio piu' probabile. Non tocca niente, si disegna e
basta. `fire` e' l'unico messaggio che porta una correzione applicata, e nasce
solo da un grilletto premuto dall'utente.

`echo` riporta sempre l'istante letto sull'orologio di CHI HA FATTO LA
RICHIESTA, cosi' l'RTT e' la differenza di due letture dello stesso clock: fra
due macchine gli orologi non sono sincronizzati e una differenza incrociata non
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
from capture import Shot, SyntheticArena, decode_jpeg, encode_jpeg
from config import Config, ConfigError, load_config
from detection import make_detector
from input_control import FireCommand, VirtualTrigger
from network import CommandSocket, FrameSocket, command_loop
from prediction import MultiTracker, TrackSnapshot
from sources import open_source

log = logging.getLogger("kine")
WIN = "KINE - mira assistita (nodo A)"
INK = (236, 236, 239)
GUIDA = (204, 192, 183)


# --------------------------------------------------------------------------- #
# Nodo B: occhi e cervello. Non spara, non decide quando sparare.
# --------------------------------------------------------------------------- #
class NodeB:
    def __init__(self, cfg: Config, peer_ip: str, stop: threading.Event):
        self.cfg = cfg
        self.stop = stop
        self.frames = FrameSocket(cfg.network.bind_ip, cfg.network.port_frames, cfg.network.max_pending_frames)
        # Il nodo B e' il SERVIZIO: lega la porta comandi nota, quella che il
        # nodo A trova in configurazione. Con una porta effimera qui, A non
        # avrebbe modo di indirizzarlo - manderebbe i comandi all'unico
        # indirizzo che conosce, cioe' `port_cmds`, dove non c'e' nessuno.
        self.cmds = CommandSocket(cfg.network.bind_ip, cfg.network.port_cmds)
        self.peer_cmds = (peer_ip, cfg.network.port_cmds)
        self.tracker = MultiTracker(cfg.prediction)
        # Il detector si costruisce UNA VOLTA: in modalita' "motion" porta con
        # se' il modello di sfondo, e ricrearlo a ogni frame vorrebbe dire
        # ripartire sempre da uno sfondo vuoto.
        self.detect = make_detector(cfg.detection)
        self.aim = (cfg.capture.width / 2, cfg.capture.height / 2)
        self.tracks: list[TrackSnapshot] = []
        self._last_frame_local = 0.0
        self._lock = threading.Lock()

    def run(self) -> None:
        threading.Thread(target=self._command_thread, name="B-cmds", daemon=True).start()
        log.info(
            "[B] in ascolto frame su :%d, comandi da :%d | detection '%s'",
            self.frames.port, self.cmds.port, self.cfg.detection.mode,
        )
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
                snaps = self.tracker.update(self.detect(frame), capture_ts)
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
            self.cmds.send_json(self._hint(msg), addr)
        elif tipo == "fire_request":
            self.cmds.send_json(self._resolve_fire(msg), addr)

    # -- assistenza al momento della mira ---------------------------------- #
    def _hint(self, msg: dict) -> dict:
        """
        Risposta al puntamento: "guarda li'".

        E' informazione pura - nessuna correzione viene applicata da questo
        messaggio - ma porta comunque dx/dy, cioe' quanto SAREBBE la
        correzione se in questo istante l'utente premesse. Mostrarla prima
        dello sparo e' il punto della consegna: l'utente sa cosa sta per fare
        l'assist, invece di subirlo.
        """
        hint, _ = self._assist_per(msg, use_assist=True)
        return {
            "type": "hint",
            "suggested": list(hint.suggested) if hint.suggested else None,
            "dx": hint.dx,
            "dy": hint.dy,
            "trackId": hint.track_id if hint.active else None,
            "nTracks": len(self.tracks),
            "sentAt": time.monotonic(),
            "echo": float(msg.get("sentAt", 0.0)),
        }

    def _assist_per(self, msg: dict, *, use_assist: bool) -> tuple[AssistHint, float]:
        user = (float(msg["x"]), float(msg["y"]))
        with self._lock:
            tracks = list(self.tracks)
            eta_frame = max(0.0, time.monotonic() - self._last_frame_local) if self._last_frame_local else 0.0
        # Il frame su cui sono calcolate le tracce e' vecchio di `eta_frame`, e
        # la risposta impieghera' ancora mezzo RTT per tornare al nodo A: la
        # somma e' l'anticipo che serve solo a compensare la rete, prima di
        # quello "estetico" di lead_ms.
        one_way = max(0.0, float(msg.get("rttMs", 0.0)) / 2000.0)
        if not use_assist:
            return AssistHint(), eta_frame
        origin = (self.cfg.capture.width / 2, self.cfg.capture.height - 16)
        return (
            compute_assist(
                user, tracks, self.cfg.assist,
                latency_s=eta_frame + one_way,
                origin=origin if self.cfg.assist.use_intercept else None,
            ),
            eta_frame,
        )

    def _resolve_fire(self, msg: dict) -> dict:
        use_assist = bool(msg.get("useAssist", True))
        hint, _ = self._assist_per(msg, use_assist=use_assist)
        user = (float(msg["x"]), float(msg["y"]))
        return {
            "type": "fire",
            "userX": user[0],
            "userY": user[1],
            "x": user[0] + hint.dx,
            "y": user[1] + hint.dy,
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
# Nodo A: il gioco, la cattura, il mirino e il grilletto.
# --------------------------------------------------------------------------- #
class NodeA:
    def __init__(self, cfg: Config, peer_ip: str, stop: threading.Event, headless: bool = False):
        self.cfg = cfg
        self.stop = stop
        self.headless = headless
        # L'arena esiste solo come banco di prova. Con una sorgente vera non
        # c'e' verita' di riferimento: niente colpi simulati, niente hit rate.
        self.arena = (
            SyntheticArena(cfg.capture, cfg.detection.colors)
            if cfg.capture.source == "synthetic"
            else None
        )
        self.source = open_source(cfg.capture, self.arena)
        self.frames = FrameSocket(cfg.network.bind_ip, 0, cfg.network.max_pending_frames)
        # Il nodo A e' il CLIENTE: porta effimera in uscita. Il nodo B risponde
        # all'indirizzo del mittente, quindi non deve conoscerla in anticipo -
        # ed e' anche cio' che permette di far girare due nodi A sulla stessa
        # macchina senza collisioni di porta.
        self.cmds = CommandSocket(cfg.network.bind_ip, 0)
        self.peer_frames = (peer_ip, cfg.network.port_frames)
        self.peer_cmds: tuple[str, int] | None = None
        self.trigger = VirtualTrigger(cfg.assist.cooldown_ms)
        self.aim = [cfg.capture.width / 2, cfg.capture.height / 2]
        self.rtt_ms = 0.0
        self.suggested: tuple[float, float] | None = None
        self.hint_dx = 0.0
        self.hint_dy = 0.0
        self.n_tracks = 0
        self.last_assist_px = 0.0
        self._pending: dict[int, tuple[bool, float, int]] = {}  # seq -> (assist, errore, bersaglio)
        self._frame_id = 0
        self._last_aim_sent = 0.0

    @property
    def mirino_fisso(self) -> bool:
        """Sparatutto in prima persona: il mirino sta al centro e non si muove."""
        return self.cfg.capture.aim_mode == "center"

    # -- rete -------------------------------------------------------------- #
    def _dest(self) -> tuple[str, int]:
        return self.peer_cmds or (self.peer_frames[0], self.cfg.network.port_cmds)

    def _command_thread(self) -> None:
        command_loop(self.cmds, self._on_cmd, self.stop, auto_ack=False)

    def _on_cmd(self, msg: dict, addr: tuple[str, int]) -> None:
        tipo = msg.get("type")
        if tipo not in ("hint", "fire"):
            return
        self.peer_cmds = addr
        echo = float(msg.get("echo", 0.0))
        if echo:
            # RTT misurato su OGNI risposta, non solo sugli spari: al primo
            # colpo la latenza e' gia' nota invece di valere zero.
            self.rtt_ms = (time.monotonic() - echo) * 1000.0
        sugg = msg.get("suggested")
        self.suggested = (float(sugg[0]), float(sugg[1])) if isinstance(sugg, list) and len(sugg) == 2 else None

        if tipo == "hint":
            self.hint_dx = float(msg.get("dx", 0.0))
            self.hint_dy = float(msg.get("dy", 0.0))
            self.n_tracks = int(msg.get("nTracks", 0))
            return

        cmd = FireCommand.from_json(msg)
        self.last_assist_px = (cmd.dx ** 2 + cmd.dy ** 2) ** 0.5
        _assist, aim_err, target_id = self._pending.pop(cmd.seq, (cmd.assist, 0.0, 1))
        if self.arena is not None:
            self.arena.emit(
                cmd.x, cmd.y, self.cfg.assist.marker_speed,
                Shot(assisted=cmd.assist, aim_error_px=aim_err, target_id=target_id, track_id=cmd.track_id),
            )
        else:
            log.info(
                "colpo #%d: mira (%.0f,%.0f) correzione (%+.1f,%+.1f) px su traccia %s",
                cmd.seq, cmd.user_x, cmd.user_y, cmd.dx, cmd.dy, cmd.track_id,
            )

    def _send_aim(self, now: float) -> None:
        if now - self._last_aim_sent < 1 / 30:
            return
        self._last_aim_sent = now
        self.cmds.send_json(
            {"type": "aim", "x": self.aim[0], "y": self.aim[1], "sentAt": now, "rttMs": self.rtt_ms},
            self._dest(),
        )

    def fire(self, use_assist: bool) -> None:
        # Il suggerimento lo calcola il nodo B: qui si chiede soltanto. Il
        # grilletto resta di chi lo preme.
        cmd = self.trigger.on_fire((self.aim[0], self.aim[1]), AssistHint(), use_assist=use_assist)
        if cmd is None:
            return  # cooldown
        if self.arena is not None:
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
            self._dest(),
        )

    # -- loop --------------------------------------------------------------- #
    def run(self) -> None:
        threading.Thread(target=self._command_thread, name="A-cmds", daemon=True).start()
        if self.mirino_fisso:
            self.aim = [self.cfg.capture.width / 2, self.cfg.capture.height / 2]
        if not self.headless:
            cv2.namedWindow(WIN)
            if not self.mirino_fisso:
                cv2.setMouseCallback(WIN, self._on_mouse)
            print(self._istruzioni())

        periodo = 1.0 / self.cfg.capture.fps
        last = time.monotonic()
        prossimo = last
        try:
            while not self.stop.is_set():
                now = time.monotonic()
                if self.arena is not None:
                    self.arena.step(now - last)
                last = now

                frame = self.source.read()
                if frame is None:
                    if self.cfg.capture.source == "video":
                        log.info("clip finita")
                        break
                    continue
                # Il frame che va in rete e' PULITO: mirino e overlay si
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

    def _istruzioni(self) -> str:
        if self.mirino_fisso:
            return (
                "Mirino fisso al centro (sparatutto in prima persona).\n"
                "  Spazio = colpo assistito   P = colpo puro\n"
                "  R = azzera metriche        Esc = esci\n"
                "L'AI non muove il puntatore: la freccia dice di quanto ruotare."
            )
        return (
            "Mirino = puntatore sulla finestra.\n"
            "  Click = colpo assistito    Shift+click = colpo puro\n"
            "  Spazio = colpo assistito   P = colpo puro\n"
            "  R = azzera metriche        Esc = esci"
        )

    def _send_frame(self, frame, now: float) -> None:
        self._frame_id += 1
        jpeg = encode_jpeg(frame, self.cfg.network.jpeg_quality)
        self.frames.send_jpeg(
            jpeg, self.peer_frames, self._frame_id, self.cfg.network.chunk_bytes, capture_ts=now
        )

    def _draw(self, frame) -> bool:
        vis = frame.copy()
        ax, ay = int(self.aim[0]), int(self.aim[1])
        cv2.drawMarker(vis, (ax, ay), INK, cv2.MARKER_CROSS, 16, 1)

        if self.suggested:
            sx, sy = int(self.suggested[0]), int(self.suggested[1])
            cv2.circle(vis, (sx, sy), 6, GUIDA, 1)
            cv2.arrowedLine(vis, (ax, ay), (sx, sy), GUIDA, 1, tipLength=0.2)
            dist = ((sx - ax) ** 2 + (sy - ay) ** 2) ** 0.5
            cv2.putText(vis, f"guarda li'  {dist:.0f} px", (sx + 8, sy - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, GUIDA, 1)

        for i, riga in enumerate(self._hud()):
            cv2.putText(vis, riga, (8, 16 + i * 14), cv2.FONT_HERSHEY_SIMPLEX, 0.38, INK, 1)

        cv2.imshow(WIN, vis)
        tasto = cv2.waitKey(1) & 0xFF
        if tasto == 27:
            return False
        if tasto in (ord("r"), ord("R")) and self.arena is not None:
            self.arena.log.reset()
        # Grilletto da tastiera: indispensabile con il mirino fisso, dove non
        # c'e' un puntatore da cliccare. waitKey non espone i modificatori,
        # quindi il colpo puro ha un tasto suo invece di Shift+Spazio.
        if tasto == 32:
            self.fire(use_assist=True)
        elif tasto in (ord("p"), ord("P")):
            self.fire(use_assist=False)
        return True

    def _hud(self) -> list[str]:
        stato = (
            f"RTT {self.rtt_ms:5.1f} ms   tracce {self.n_tracks}   "
            f"frame persi {self.frames.frame_loss * 100:4.1f}%   "
            f"sorgente {self.cfg.capture.source}/{self.cfg.detection.mode}"
        )
        correzione = (
            f"correzione pronta ({self.hint_dx:+.1f}, {self.hint_dy:+.1f}) px "
            f"= {(self.hint_dx ** 2 + self.hint_dy ** 2) ** 0.5:.1f} / "
            f"{self.cfg.assist.max_correction_px:.0f} max"
        )
        if self.arena is None:
            return [stato, correzione]
        s = self.arena.log.summary()
        return [
            stato,
            correzione,
            f"assistiti  n={s['assistiti']['n']:3d}  a segno {s['assistiti']['hit_rate'] * 100:5.1f}%"
            f"  mancato {s['assistiti']['miss_px']:5.1f} px",
            f"puri       n={s['puri']['n']:3d}  a segno {s['puri']['hit_rate'] * 100:5.1f}%"
            f"  mancato {s['puri']['miss_px']:5.1f} px",
        ]

    def _on_mouse(self, event, x, y, flags, _param) -> None:
        self.aim[0], self.aim[1] = float(x), float(y)
        if event == cv2.EVENT_LBUTTONDOWN:
            self.fire(use_assist=not bool(flags & cv2.EVENT_FLAG_SHIFTKEY))

    def close(self) -> None:
        self.source.close()
        self.frames.close()
        self.cmds.close()
        if not self.headless:
            cv2.destroyAllWindows()
        if self.arena is None:
            return
        s = self.arena.log.summary()
        log.info(
            "riepilogo | assistiti n=%d a_segno=%.0f%% mancato=%.1f px | puri n=%d a_segno=%.0f%% mancato=%.1f px",
            s["assistiti"]["n"], s["assistiti"]["hit_rate"] * 100, s["assistiti"]["miss_px"],
            s["puri"]["n"], s["puri"]["hit_rate"] * 100, s["puri"]["miss_px"],
        )


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="KINE - mira assistita (progetto didattico)")
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.json")))
    ap.add_argument("--role", choices=("both", "capture", "ai"), default="both")
    ap.add_argument("--peer", default=None, help="IP dell'altro nodo (l'altro PC della LAN)")
    ap.add_argument("--bind", default=None, help="IP locale su cui mettersi in ascolto")
    ap.add_argument("--source", choices=("screen", "video", "webcam", "synthetic"), default=None)
    ap.add_argument("--video", default=None, help="clip da usare con --source video")
    ap.add_argument("--aim", choices=("center", "mouse"), default=None, help="dove sta il mirino")
    ap.add_argument("--detect", choices=("bgr", "hsv", "motion"), default=None)
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
        # Gli override da riga di comando passano dalla stessa validazione del
        # file: --source screen con una regione assurda deve fallire adesso,
        # non al primo frame.
        if args.source:
            cfg.capture.source = args.source
        if args.video:
            cfg.capture.video_path = args.video
        if args.aim:
            cfg.capture.aim_mode = args.aim
        if args.detect:
            cfg.detection.mode = args.detect
        if any((args.source, args.video, args.aim, args.detect)):
            cfg.validate()   # solo se qualcosa e' cambiato: rivalidare a vuoto
                             # ripeterebbe gli avvisi gia' stampati da load_config
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
        if args.role == "both":
            nodo_b = NodeB(cfg, peer, stop)
            threading.Thread(target=nodo_b.run, name="nodo-B", daemon=True).start()
            time.sleep(0.1)  # lascia legare le socket prima del primo frame
        NodeA(cfg, peer, stop, headless=args.headless).run()
    except RuntimeError as e:
        log.error("%s", e)
        return 3
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
