"""
Rilevamento per soglia cromatica (OpenCV).

Scelta rispetto a una rete (YOLO): e' deterministico, gira a 24 fps senza pesi
da scaricare, e rende visibile il nesso colore -> maschera -> blob -> centroide.
Su webcam con classi semantiche la rete resta l'estensione naturale.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from config import DetectionCfg


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


def _maschere_esclusive(frame_bgr: np.ndarray, cfg: DetectionCfg) -> list[np.ndarray]:
    """
    Una maschera per classe, con ogni pixel assegnato a UNA SOLA classe.

    La soglia per classe, presa indipendentemente, non e' una partizione: i box
    cromatici si intersecano e un pixel che cade nell'intersezione finisce in
    due maschere. Effetto osservato sul poligono appena si passa dal frame
    grezzo a quello compresso: il JPEG a qualita' 70 sposta di qualche unita' i
    pixel sui bordi dei dischi, uno stesso disco viene rilevato come "verde" E
    "azzurro", il tracker apre due track sullo stesso bersaglio e l'assist
    salta dall'una all'altra. Con il frame grezzo non succede, quindi il difetto
    era invisibile finche' la modalita' a due nodi non funzionava.

    Le soglie si calcolano con inRange (ottimizzato in C++); la distanza, che
    costa un ordine di grandezza in piu', si calcola SOLO sui pixel contesi da
    piu' classi. Sul poligono sono qualche decina su 230.000, e il costo della
    disambiguazione sparisce.
    """
    maschere: list[np.ndarray] = []
    for spec in cfg.colors:
        b, g, r = spec.bgr
        tol = spec.tolerance
        lower = np.array([max(0, b - tol), max(0, g - tol), max(0, r - tol)], dtype=np.uint8)
        upper = np.array([min(255, b + tol), min(255, g + tol), min(255, r + tol)], dtype=np.uint8)
        maschere.append(cv2.inRange(frame_bgr, lower, upper))

    if len(maschere) < 2:
        return maschere

    conteggio = np.zeros(maschere[0].shape, dtype=np.uint8)
    for m in maschere:
        conteggio += m // 255
    contesi = conteggio > 1
    if not contesi.any():
        return maschere

    # Solo i pixel ambigui: il vincitore e' la classe di colore piu' vicino.
    px = frame_bgr[contesi].astype(np.int32)
    distanze = np.stack(
        [((px - np.array(spec.bgr, dtype=np.int32)) ** 2).sum(axis=1) for spec in cfg.colors]
    )
    # Una classe che non aveva comunque superato la soglia non puo' vincere.
    ammesse = np.stack([m[contesi] > 0 for m in maschere])
    distanze[~ammesse] = np.iinfo(np.int32).max
    vincitore = np.argmin(distanze, axis=0)
    for i, m in enumerate(maschere):
        perdenti = np.zeros_like(conteggio, dtype=bool)
        perdenti[contesi] = vincitore != i
        m[perdenti] = 0
    return maschere


def detect_color_blobs(frame_bgr: np.ndarray, cfg: DetectionCfg) -> list[Detection]:
    if frame_bgr is None or frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
        raise ValueError("detect_color_blobs richiede un'immagine BGR HxWx3")

    kernel = (
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.open_kernel, cfg.open_kernel))
        if cfg.open_kernel >= 2
        else None
    )
    out: list[Detection] = []

    for spec, mask in zip(cfg.colors, _maschere_esclusive(frame_bgr, cfg)):
        if kernel is not None:
            # Apertura = erosione + dilatazione: elimina i pixel isolati della
            # webcam senza spostare il centroide dei blob veri.
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

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
                    name=spec.name,
                    x=float(x),
                    y=float(y),
                    w=float(w),
                    h=float(h),
                    cx=float(cx),
                    cy=float(cy),
                    area=area,
                    # Quanto il blob assomiglia a un bersaglio intero. Prima era
                    # area / (max_area * 0.25): con max_area=20000 un disco pieno
                    # dava 0.30 e sembrava una detection incerta. Ora il
                    # riferimento e' l'area nominale del bersaglio, quindi 1.0
                    # significa "blob della taglia giusta".
                    confidence=float(min(1.0, area / cfg.nominal_area)),
                )
            )
    return out
