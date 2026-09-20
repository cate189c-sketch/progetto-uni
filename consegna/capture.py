"""
Sorgente frame: poligono sintetico (dischi colorati) o webcam.
Non cattura lo schermo ne' altri processi: solo un canvas generato qui o la
telecamera che l'utente accende esplicitamente.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from config import CaptureCfg, ColorCfg

log = logging.getLogger("kine.capture")

BG = (14, 13, 12)
INK = (236, 236, 239)


@dataclass(slots=True)
class Shot:
    """
    Un colpo, dall'emissione all'esito. E' l'unita' di misura del progetto.

    `miss_px` e' la distanza di mancato: 0 se il colpo va a segno, altrimenti
    di quanto il marcatore ha sfiorato la SUPERFICIE del bersaglio che l'utente
    stava prendendo di mira.

    Due trappole evitate, entrambe presenti nella versione precedente:
      - non e' la distanza alla quale scatta la collisione (quella e' ~ il
        raggio, sempre, con qualunque mira: non misura niente);
      - e' riferita al bersaglio INTESO, non al piu' vicino di passaggio, e al
        centro del bersaglio e non alla linea di tiro: con un tiro anticipato
        la linea di tiro punta dove il bersaglio SARA', quindi la distanza
        dalla linea peggiora proprio quando il colpo e' piu' corretto.
    """

    assisted: bool
    aim_error_px: float          # |mira utente - centro bersaglio| al momento dello sparo
    target_id: int               # quale bersaglio l'utente stava prendendo di mira
    miss_px: float = float("inf")
    hit: bool = False
    track_id: int | None = None


class ShotLog:
    """
    Registro dei colpi.

    Serve a rispondere all'unica domanda che il progetto deve dimostrare:
    l'assist migliora la precisione, e di quanto? Senza questo il cruscotto
    mostra numeri che non misurano niente.
    """

    def __init__(self, max_len: int = 500) -> None:
        self.shots: list[Shot] = []
        self.max_len = max_len

    def add(self, shot: Shot) -> None:
        self.shots.append(shot)
        if len(self.shots) > self.max_len:
            del self.shots[: len(self.shots) - self.max_len]

    def summary(self) -> dict[str, Any]:
        def stats(gruppo: list[Shot]) -> dict[str, Any]:
            if not gruppo:
                return {"n": 0, "hit_rate": 0.0, "miss_px": 0.0, "aim_error_px": 0.0}
            chiusi = [s for s in gruppo if math.isfinite(s.miss_px)]
            return {
                "n": len(gruppo),
                "hit_rate": sum(s.hit for s in gruppo) / len(gruppo),
                "miss_px": sum(s.miss_px for s in chiusi) / len(chiusi) if chiusi else 0.0,
                "aim_error_px": sum(s.aim_error_px for s in gruppo) / len(gruppo),
            }

        return {
            "totale": stats(self.shots),
            "assistiti": stats([s for s in self.shots if s.assisted]),
            "puri": stats([s for s in self.shots if not s.assisted]),
        }

    def reset(self) -> None:
        self.shots.clear()


@dataclass(slots=True)
class _Obj:
    id: int
    name: str
    bgr: tuple[int, int, int]
    x: float
    y: float
    vx: float
    vy: float
    r: float


@dataclass(slots=True)
class _Marker:
    x: float
    y: float
    vx: float
    vy: float
    shot: Shot
    alive: bool = True


class SyntheticArena:
    """
    Poligono: dischi colorati che rimbalzano, piu' i marcatori sparati
    dall'attuatore in basso al centro.
    """

    def __init__(self, cfg: CaptureCfg, colors: tuple[ColorCfg, ...] | list[ColorCfg], seed: int | None = 7):
        self.w = cfg.width
        self.h = cfg.height
        # Seed fisso: due run con la stessa configurazione devono dare gli stessi
        # numeri, altrimenti il confronto "con assist / senza assist" nel report
        # misura il rumore invece dell'effetto.
        self.rng = random.Random(seed)
        self.margin = 24.0
        self.floor = 36.0
        self.objs: list[_Obj] = []
        self.markers: list[_Marker] = []
        self.log = ShotLog()
        self._colors = list(colors)
        self.reset()

    @property
    def origin(self) -> tuple[float, float]:
        return self.w / 2, self.h - 16

    def reset(self) -> None:
        self.objs = []
        self.markers = []
        self.log.reset()
        for i, spec in enumerate(self._colors):
            self.objs.append(
                _Obj(
                    id=i + 1,
                    name=spec.name,
                    bgr=tuple(int(v) for v in spec.bgr),  # type: ignore[arg-type]
                    x=self.w * (0.25 + 0.2 * i),
                    y=self.h * (0.3 + 0.12 * i),
                    vx=80 + i * 20,
                    vy=40 - i * 15,
                    r=22,
                )
            )

    def step(self, dt: float) -> None:
        # dt clampato: se la finestra viene trascinata o il processo va in
        # swap, un dt di mezzo secondo teletrasporta i dischi oltre il muro.
        dt = max(0.0, min(0.05, dt))
        self._step_objects(dt)
        self._step_markers(dt)

    def _step_objects(self, dt: float) -> None:
        lo_x, hi_x = self.margin, self.w - self.margin
        lo_y, hi_y = self.margin, self.h - self.floor
        for o in self.objs:
            o.x += o.vx * dt
            o.y += o.vy * dt
            # Rimbalzo: si riporta la posizione DENTRO il bordo e si impone il
            # segno della velocita'. Il vecchio `if fuori: vx *= -1` invertiva
            # il segno a ogni frame in cui l'oggetto era ancora oltre il bordo:
            # un disco lento restava incastrato nel muro a vibrare per sempre.
            if o.x < lo_x:
                o.x = lo_x
                o.vx = abs(o.vx)
            elif o.x > hi_x:
                o.x = hi_x
                o.vx = -abs(o.vx)
            if o.y < lo_y:
                o.y = lo_y
                o.vy = abs(o.vy)
            elif o.y > hi_y:
                o.y = hi_y
                o.vy = -abs(o.vy)

    def _step_markers(self, dt: float) -> None:
        vivi: list[_Marker] = []
        for m in self.markers:
            px, py = m.x, m.y
            m.x += m.vx * dt
            m.y += m.vy * dt
            for o in self.objs:
                if o.id != m.shot.target_id:
                    continue
                # Distanza dal SEGMENTO percorso in questo passo: a 380 px/s un
                # frame copre ~6 px, e campionare le sole posizioni discrete
                # salterebbe il punto di massimo avvicinamento.
                d = _dist_punto_segmento(o.x, o.y, px, py, m.x, m.y) - o.r
                if d < m.shot.miss_px:
                    m.shot.miss_px = max(0.0, d)
                if d <= 0.0:
                    m.shot.hit = True
                    m.shot.miss_px = 0.0
                    m.alive = False
                break
            if not m.alive:
                self.log.add(m.shot)
                continue
            if not (-10 < m.x < self.w + 10 and -10 < m.y < self.h + 10):
                self.log.add(m.shot)  # uscito dal campo: miss, con la sua miss_px
                continue
            vivi.append(m)
        self.markers = vivi

    def emit(self, tx: float, ty: float, speed: float, shot: Shot) -> None:
        ox, oy = self.origin
        d = math.hypot(tx - ox, ty - oy) or 1.0
        self.markers.append(
            _Marker(x=ox, y=oy, vx=(tx - ox) / d * speed, vy=(ty - oy) / d * speed, shot=shot)
        )

    def nearest_object(self, x: float, y: float) -> tuple[float, _Obj | None]:
        """Distanza e bersaglio piu' vicini al punto: serve a sapere a CHI mirava l'utente."""
        best, best_d = None, float("inf")
        for o in self.objs:
            d = math.hypot(o.x - x, o.y - y)
            if d < best_d:
                best_d, best = d, o
        return best_d, best

    def render(self, aim: tuple[float, float] | None = None, hud: bool = False) -> np.ndarray:
        """
        hud=False produce il frame *pulito* che va in rete: mirino e overlay non
        devono finire nel flusso, altrimenti il nodo B rileva blob disegnati da
        se' stesso. Il mirino si disegna solo sulla copia mostrata a schermo.
        """
        img = np.empty((self.h, self.w, 3), dtype=np.uint8)
        img[:] = BG
        for m in self.markers:
            cv2.circle(img, (int(m.x), int(m.y)), 3, INK, -1)
        for o in self.objs:
            cv2.circle(img, (int(o.x), int(o.y)), int(o.r), o.bgr, -1)
        if hud and aim:
            cv2.drawMarker(img, (int(aim[0]), int(aim[1])), INK, cv2.MARKER_CROSS, 16, 1)
        return img


class Webcam:
    """Wrapper sulla VideoCapture con apertura verificata e rilascio garantito."""

    def __init__(self, index: int, w: int, h: int):
        self.cap = cv2.VideoCapture(index)
        if not self.cap.isOpened():
            self.cap.release()
            raise RuntimeError(
                f"webcam {index} non disponibile. Usa capture.source='synthetic' nella config."
            )
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        self.w, self.h = w, h

    def read(self) -> np.ndarray | None:
        ok, frame = self.cap.read()
        if not ok or frame is None:
            return None
        if frame.shape[1] != self.w or frame.shape[0] != self.h:
            frame = cv2.resize(frame, (self.w, self.h))
        return frame

    def close(self) -> None:
        self.cap.release()

    def __enter__(self) -> Webcam:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _dist_punto_segmento(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Distanza fra il punto P e il segmento AB."""
    abx, aby = bx - ax, by - ay
    den = abx * abx + aby * aby
    if den == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / den))
    return math.hypot(px - (ax + t * abx), py - (ay + t * aby))


def encode_jpeg(frame_bgr: np.ndarray, quality: int = 70) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError("codifica JPEG fallita")
    return buf.tobytes()


def decode_jpeg(blob: bytes) -> np.ndarray | None:
    """None su payload corrotto: un frame rotto non deve fermare la pipeline."""
    if not blob:
        return None
    arr = np.frombuffer(blob, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)
