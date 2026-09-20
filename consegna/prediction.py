"""
Kalman 2D a velocita' costante + associazione dati.

Stato x = [px, py, vx, vy]^T. L'accelerazione (rimbalzi, cambi di direzione)
e' modellata come rumore di processo Q e non come stato: con poche misure
rumorose dal blob detector un modello CA diverge, un CV con Q generoso no.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from config import PredictionCfg
from detection import Detection

# Misura solo la posizione: H estrae px, py dallo stato.
_H = np.array([[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]])
_I4 = np.eye(4)


class KalmanCV:
    def __init__(self, px: float, py: float, measure_noise: float, process_noise: float):
        self.x = np.array([px, py, 0.0, 0.0], dtype=np.float64)
        self.P = np.eye(4) * 400.0
        self.R = np.eye(2) * (measure_noise ** 2)
        self.q_acc = float(process_noise)

    def predict(self, dt: float) -> None:
        F = _I4.copy()
        F[0, 2] = dt
        F[1, 3] = dt
        q = self.q_acc ** 2
        dt2 = dt * dt
        dt3 = dt2 * dt
        dt4 = dt2 * dt2
        Q = q * np.array(
            [
                [dt4 / 4, 0, dt3 / 2, 0],
                [0, dt4 / 4, 0, dt3 / 2],
                [dt3 / 2, 0, dt2, 0],
                [0, dt3 / 2, 0, dt2],
            ]
        )
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q

    def innovation(self, zx: float, zy: float) -> tuple[np.ndarray, np.ndarray]:
        y = np.array([zx, zy]) - _H @ self.x
        S = _H @ self.P @ _H.T + self.R
        return y, S

    def mahalanobis(self, zx: float, zy: float) -> float:
        """
        Distanza normalizzata sulla covarianza dell'innovazione.

        Un gate in pixel tratta allo stesso modo una traccia appena nata
        (P grande, posizione incerta) e una consolidata. Con la distanza di
        Mahalanobis il cancello si allarga da solo quando il filtro non sa
        dove si trova l'oggetto, e si stringe quando lo sa.
        """
        y, S = self.innovation(zx, zy)
        try:
            return float(y @ np.linalg.solve(S, y))
        except np.linalg.LinAlgError:
            return float("inf")

    def update(self, zx: float, zy: float) -> None:
        y, S = self.innovation(zx, zy)
        try:
            # K = P Hᵀ S⁻¹ risolto come sistema lineare: solve() e' piu' stabile
            # e piu' veloce di inv(), e solleva se S e' singolare invece di
            # restituire numeri enormi senza dirlo.
            K = np.linalg.solve(S, _H @ self.P).T
        except np.linalg.LinAlgError:
            return
        self.x = self.x + K @ y
        # Forma di Joseph: P = (I-KH) P (I-KH)ᵀ + K R Kᵀ.
        # La forma breve (I-KH)P e' algebricamente equivalente ma in virgola
        # mobile perde la simmetria di P e, dopo qualche migliaio di update,
        # puo' produrre una covarianza non definita positiva -> il filtro
        # smette di correggere. Joseph resta simmetrica per costruzione.
        A = _I4 - K @ _H
        self.P = A @ self.P @ A.T + K @ self.R @ K.T

    @property
    def pos(self) -> tuple[float, float]:
        return float(self.x[0]), float(self.x[1])

    @property
    def vel(self) -> tuple[float, float]:
        return float(self.x[2]), float(self.x[3])

    def coast(self, dt: float) -> tuple[float, float]:
        return float(self.x[0] + self.x[2] * dt), float(self.x[1] + self.x[3] * dt)


@dataclass(slots=True)
class Track:
    id: int
    name: str
    kf: KalmanCV
    w: float
    h: float
    confidence: float
    hits: int = 1
    missed: int = 0
    last_t: float = 0.0

    @property
    def confirmed(self) -> bool:
        return self._confirmed

    _confirmed: bool = field(default=False, repr=False)


@dataclass(slots=True, frozen=True)
class TrackSnapshot:
    id: int
    name: str
    x: float
    y: float
    vx: float
    vy: float
    w: float
    h: float
    confidence: float
    hits: int
    missed: int
    confirmed: bool

    @property
    def speed(self) -> float:
        return math.hypot(self.vx, self.vy)


class MultiTracker:
    """
    Associazione globale greedy per classe + Kalman.

    Gli id non dipendono dal timestamp (bug della prima versione: ogni frame
    produceva oggetti nuovi e la velocita' restava a zero). Vivono finche' la
    traccia riceve misure entro `max_missed` frame.

    L'associazione ordina *tutte* le coppie (traccia, detection) per distanza e
    le consuma in ordine, invece di far scegliere per prima la traccia che
    capita prima nella lista. Con due bersagli della stessa classe che si
    incrociano, il vecchio ordine per-traccia scambiava gli id; questo no.
    Il passo successivo sarebbe l'algoritmo ungherese (ottimo globale), qui
    non serve: con <= 8 tracce la differenza non si misura.
    """

    def __init__(self, cfg: PredictionCfg):
        self.cfg = cfg
        self.tracks: list[Track] = []
        self._next_id = 1

    def reset(self) -> None:
        self.tracks.clear()
        self._next_id = 1

    def update(self, detections: list[Detection], t: float) -> list[TrackSnapshot]:
        p = self.cfg
        for tr in self.tracks:
            dt = min(0.12, max(1 / 120, t - tr.last_t))
            tr.kf.predict(dt)
            tr.last_t = t

        coppie: list[tuple[float, int, int]] = []
        for ti, tr in enumerate(self.tracks):
            px, py = tr.kf.pos
            for di, d in enumerate(detections):
                if d.name != tr.name:
                    continue
                dist = math.hypot(d.cx - px, d.cy - py)
                if dist >= p.gate_px:
                    continue
                # Gate doppio: euclideo (interpretabile, sta in config) e
                # Mahalanobis a 3 sigma su 2 gradi di liberta' (chi2 ~ 11.8),
                # che scarta le associazioni incompatibili con l'incertezza.
                if tr.kf.mahalanobis(d.cx, d.cy) > 11.8:
                    continue
                coppie.append((dist, ti, di))
        coppie.sort()

        track_usate: set[int] = set()
        det_usate: set[int] = set()
        for dist, ti, di in coppie:
            if ti in track_usate or di in det_usate:
                continue
            track_usate.add(ti)
            det_usate.add(di)
            tr, d = self.tracks[ti], detections[di]
            tr.kf.update(d.cx, d.cy)
            self._clamp_velocity(tr)
            tr.w, tr.h, tr.confidence = d.w, d.h, d.confidence
            tr.hits += 1
            tr.missed = 0
            if tr.hits >= p.min_hits:
                tr._confirmed = True

        for ti, tr in enumerate(self.tracks):
            if ti not in track_usate:
                tr.missed += 1

        for di, d in enumerate(detections):
            if di in det_usate:
                continue
            self.tracks.append(
                Track(
                    id=self._next_id,
                    name=d.name,
                    kf=KalmanCV(d.cx, d.cy, p.measure_noise, p.process_noise),
                    w=d.w,
                    h=d.h,
                    confidence=d.confidence,
                    last_t=t,
                    _confirmed=p.min_hits <= 1,
                )
            )
            self._next_id += 1

        self.tracks = [tr for tr in self.tracks if tr.missed <= p.max_missed]
        return [self._snapshot(tr) for tr in self.tracks]

    def _clamp_velocity(self, tr: Track) -> None:
        vx, vy = tr.kf.vel
        sp = math.hypot(vx, vy)
        if sp > self.cfg.max_velocity:
            s = self.cfg.max_velocity / sp
            tr.kf.x[2] *= s
            tr.kf.x[3] *= s

    def _snapshot(self, tr: Track) -> TrackSnapshot:
        x, y = tr.kf.pos
        vx, vy = tr.kf.vel
        if not self.cfg.use_kalman:
            # use_kalman=False = "nessun anticipo": la velocita' stimata viene
            # azzerata in uscita cosi' l'assist mira alla posizione misurata.
            # E' l'ablazione da mettere nel report, non un bypass del filtro
            # (che continua a filtrare il rumore sulla posizione).
            vx = vy = 0.0
        return TrackSnapshot(
            id=tr.id,
            name=tr.name,
            x=x,
            y=y,
            vx=vx,
            vy=vy,
            w=tr.w,
            h=tr.h,
            confidence=tr.confidence,
            hits=tr.hits,
            missed=tr.missed,
            confirmed=tr.confirmed,
        )
