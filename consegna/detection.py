"""
Rilevamento bersagli (OpenCV), in tre modalita'.

  bgr     soglia in un cubo attorno al colore di riferimento. Esatta su tinte
          piatte (il poligono), fragile su video vero: l'ombra abbassa tutti e
          tre i canali insieme e il bersaglio esce dal cubo.
  hsv     soglia stretta sulla TINTA piu' due soglie minime su saturazione e
          luminosita'. E' la modalita' per i frame che arrivano da uno schermo:
          un bersaglio in ombra mantiene la tinta e cambia solo V.
  motion  sottrazione dello sfondo (MOG2): nessuna palette, viene rilevato cio'
          che si muove rispetto al fondo appreso.

Scelta rispetto a una rete neurale (YOLO): deterministico, gira a 24 fps senza
pesi da scaricare, e rende visibile il nesso colore -> maschera -> blob ->
centroide. Con classi semantiche vere (un modello di nemico invece di un
colore) la rete resta l'estensione naturale, e si innesterebbe qui: basta che
produca oggetti `Detection`, il resto della pipeline non cambia.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from config import ColorCfg, DetectionCfg

CLASSE_MOTION = "mobile"


@dataclass(slots=True, frozen=True)
class Detection:
    name: str
    x: float
    y: float
    w: float
    h: float
    cx: float
    cy: float
    area: float
    confidence: float


# --------------------------------------------------------------------------- #
# Maschere
# --------------------------------------------------------------------------- #
def _hue_riferimento(bgr: tuple[int, int, int]) -> int:
    """Tinta del colore di riferimento, nella scala OpenCV 0..179."""
    px = np.array([[list(bgr)]], dtype=np.uint8)
    return int(cv2.cvtColor(px, cv2.COLOR_BGR2HSV)[0, 0, 0])


def _maschera_bgr(frame_bgr: np.ndarray, spec: ColorCfg) -> np.ndarray:
    b, g, r = spec.bgr
    tol = spec.tolerance
    lower = np.array([max(0, b - tol), max(0, g - tol), max(0, r - tol)], dtype=np.uint8)
    upper = np.array([min(255, b + tol), min(255, g + tol), min(255, r + tol)], dtype=np.uint8)
    return cv2.inRange(frame_bgr, lower, upper)


def _maschera_hsv(hsv: np.ndarray, spec: ColorCfg) -> np.ndarray:
    """
    La tinta e' un ANGOLO: 179 e 0 sono adiacenti. Il rosso sta proprio a
    cavallo dello zero, quindi un intervallo [h0-tol, h0+tol] preso alla
    lettera esclude meta' dei pixel rossi. Quando l'intervallo scavalca il
    bordo si usano due fette e si sommano.
    """
    h0 = _hue_riferimento(spec.bgr)
    lo, hi = h0 - spec.hue_tol, h0 + spec.hue_tol
    smin, vmin = spec.sat_min, spec.val_min
    if lo < 0 or hi > 179:
        bassa = cv2.inRange(hsv, np.array([0, smin, vmin], np.uint8),
                            np.array([hi % 180, 255, 255], np.uint8))
        alta = cv2.inRange(hsv, np.array([lo % 180, smin, vmin], np.uint8),
                           np.array([179, 255, 255], np.uint8))
        return cv2.bitwise_or(bassa, alta)
    return cv2.inRange(hsv, np.array([lo, smin, vmin], np.uint8),
                       np.array([hi, 255, 255], np.uint8))


def _rendi_esclusive(maschere: list[np.ndarray], distanze_contesi) -> list[np.ndarray]:
    """
    Assegna ogni pixel a UNA SOLA classe.

    La soglia per classe, presa indipendentemente, non e' una partizione: le
    regioni si intersecano e un pixel nell'intersezione finisce in due
    maschere. Effetto osservato appena si passa dal frame grezzo a quello
    compresso: il JPEG a qualita' 70 sposta di qualche unita' i pixel sui bordi,
    uno stesso bersaglio viene rilevato come due classi, il tracker apre due
    track sullo stesso oggetto e l'assist salta dall'una all'altra.

    Le soglie si calcolano con inRange (ottimizzato in C++); la distanza, che
    costa un ordine di grandezza in piu', si calcola SOLO sui pixel contesi.
    Sul poligono sono qualche decina su 230.000: il costo sparisce.

    `distanze_contesi(contesi)` e' iniettata perche' la metrica dipende dalla
    modalita': euclidea nel cubo BGR, circolare sulla tinta in HSV.
    """
    if len(maschere) < 2:
        return maschere

    conteggio = np.zeros(maschere[0].shape, dtype=np.uint8)
    for m in maschere:
        conteggio += m // 255
    contesi = conteggio > 1
    if not contesi.any():
        return maschere

    distanze = distanze_contesi(contesi)
    # Una classe che non aveva comunque superato la soglia non puo' vincere.
    ammesse = np.stack([m[contesi] > 0 for m in maschere])
    distanze = np.where(ammesse, distanze, np.inf)
    vincitore = np.argmin(distanze, axis=0)
    for i, m in enumerate(maschere):
        perdenti = np.zeros_like(conteggio, dtype=bool)
        perdenti[contesi] = vincitore != i
        m[perdenti] = 0
    return maschere


def _maschere_colore(frame_bgr: np.ndarray, cfg: DetectionCfg) -> list[np.ndarray]:
    if cfg.mode == "hsv":
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        maschere = [_maschera_hsv(hsv, spec) for spec in cfg.colors]
        hues = np.array([_hue_riferimento(spec.bgr) for spec in cfg.colors], dtype=np.float32)

        def distanze(contesi):
            h = hsv[contesi][:, 0].astype(np.float32)
            d = np.abs(h[None, :] - hues[:, None])
            return np.minimum(d, 180.0 - d)   # distanza sul cerchio delle tinte

    else:
        maschere = [_maschera_bgr(frame_bgr, spec) for spec in cfg.colors]
        riferimenti = np.array([spec.bgr for spec in cfg.colors], dtype=np.int32)

        def distanze(contesi):
            # int32 e non il dtype naturale: 3 * 255^2 = 195075 non ci sta in
            # int16 e il quadrato andrebbe in overflow silenzioso.
            px = frame_bgr[contesi].astype(np.int32)
            return np.stack([((px - rif) ** 2).sum(axis=1) for rif in riferimenti]).astype(np.float32)

    return _rendi_esclusive(maschere, distanze)


# --------------------------------------------------------------------------- #
# Blob
# --------------------------------------------------------------------------- #
def _blobs(mask: np.ndarray, name: str, cfg: DetectionCfg, kernel) -> list[Detection]:
    if kernel is not None:
        # Apertura = erosione + dilatazione: elimina i pixel isolati senza
        # spostare il centroide dei blob veri.
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    out: list[Detection] = []
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        if area < cfg.min_area or area > cfg.max_area:
            continue
        x, y, w, h = cv2.boundingRect(cnt)
        m = cv2.moments(cnt)
        if m["m00"] > 0:
            cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
        else:
            # Contorno degenere (linea sottile): il centroide dei momenti non
            # esiste, si ripiega sul centro del bounding box.
            cx, cy = x + w / 2, y + h / 2
        out.append(
            Detection(
                name=name,
                x=float(x), y=float(y), w=float(w), h=float(h),
                cx=float(cx), cy=float(cy),
                area=area,
                # Quanto il blob assomiglia a un bersaglio intero. Il
                # riferimento e' l'area nominale, quindi 1.0 = taglia giusta.
                confidence=float(min(1.0, area / cfg.nominal_area)),
            )
        )
    return out


def _kernel(cfg: DetectionCfg):
    return (
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.open_kernel, cfg.open_kernel))
        if cfg.open_kernel >= 2
        else None
    )


def _valida(frame_bgr: np.ndarray) -> None:
    if frame_bgr is None or frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
        raise ValueError("la detection richiede un'immagine BGR HxWx3")


def detect_color_blobs(frame_bgr: np.ndarray, cfg: DetectionCfg) -> list[Detection]:
    """Rilevamento cromatico senza stato (modalita' 'bgr' e 'hsv')."""
    _valida(frame_bgr)
    kernel = _kernel(cfg)
    out: list[Detection] = []
    for spec, mask in zip(cfg.colors, _maschere_colore(frame_bgr, cfg)):
        out.extend(_blobs(mask, spec.name, cfg, kernel))
    return out


class MotionDetector:
    """
    Sottrazione dello sfondo (MOG2): bersagli senza palette.

    LIMITE, da dire prima che lo dica il relatore: funziona a INQUADRATURA
    FERMA. In uno sparatutto in prima persona, appena l'utente ruota, l'intero
    fotogramma si muove rispetto allo sfondo appreso e la maschera si accende
    ovunque: le detection diventano centinaia e non significano niente. Vale
    per camera fissa, visuale dall'alto, torretta, telecamera di sorveglianza.

    Per questo non e' il default: e' l'alternativa da usare quando un colore
    distintivo non esiste E l'inquadratura non ruota. Il rimedio corretto per
    la camera in movimento sarebbe stimare l'omografia fra frame consecutivi e
    compensare il moto di fondo prima della sottrazione; e' fuori dallo scopo
    di questa consegna e va detto, non nascosto.
    """

    def __init__(self, cfg: DetectionCfg):
        self.cfg = cfg
        self._bg = cv2.createBackgroundSubtractorMOG2(
            history=cfg.motion_history,
            varThreshold=cfg.motion_threshold,
            detectShadows=False,   # le ombre non sono bersagli e costano tempo
        )

    def __call__(self, frame_bgr: np.ndarray) -> list[Detection]:
        _valida(frame_bgr)
        mask = self._bg.apply(frame_bgr)
        # MOG2 lascia sale-e-pepe: qui l'apertura serve sempre, anche quando
        # open_kernel e' 0 per le modalita' cromatiche.
        kernel = _kernel(self.cfg) or cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        return _blobs(mask, CLASSE_MOTION, self.cfg, kernel)


def make_detector(cfg: DetectionCfg):
    """
    Restituisce un chiamabile `frame -> list[Detection]` per la modalita' scelta.

    Le modalita' cromatiche sono senza stato, MOG2 no: il modello di sfondo si
    costruisce frame dopo frame. Chiamare `make_detector` una volta e riusare
    l'oggetto e' quindi obbligatorio, non un'ottimizzazione - un detector nuovo
    a ogni frame avrebbe sempre uno sfondo vuoto e rileverebbe tutto.
    """
    if cfg.mode == "motion":
        return MotionDetector(cfg)
    return lambda frame: detect_color_blobs(frame, cfg)
