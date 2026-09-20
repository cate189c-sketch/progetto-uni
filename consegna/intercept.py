"""
Intercetta cinematica 2D.

Dato un bersaglio in p con velocita' v e un attuatore in `origin` che emette un
marcatore a velocita' scalare s, il tempo di volo tau soddisfa

    |p + v*tau - origin| = s*tau

che elevata al quadrato e' un'equazione di secondo grado in tau:

    (|v|^2 - s^2) tau^2 + 2 (r . v) tau + |r|^2 = 0,   con r = p - origin

Si prende la radice positiva piu' piccola: e' il primo istante in cui il
marcatore puo' raggiungere il bersaglio.

Perche' non basta "mirare dove sta adesso": il marcatore impiega tau ad
arrivare e in quel tempo il bersaglio si e' spostato di v*tau. Sul poligono
640x360 con marcatori a 380 px/s, tau vale ~0.5 s e un disco a 100 px/s si
sposta di ~50 px: e' l'errore dominante, sei volte piu' grande dei 50 ms di
anticipo che compensano la latenza della misura.
"""

from __future__ import annotations

import math

# Coerente con prediction: oltre 4 s il modello a velocita' costante non vale piu'.
MAX_TAU = 4.0
MIN_TAU = 0.02


def solve_intercept(
    px: float,
    py: float,
    vx: float,
    vy: float,
    ox: float,
    oy: float,
    speed: float,
) -> tuple[float, float, float] | None:
    """(x, y, tau) del punto d'intercetta, oppure None se non e' raggiungibile."""
    rx, ry = px - ox, py - oy
    a = vx * vx + vy * vy - speed * speed
    b = 2.0 * (rx * vx + ry * vy)
    c = rx * rx + ry * ry

    if abs(a) < 1e-9:
        # Bersaglio alla stessa velocita' del marcatore: l'equazione degenera
        # in lineare.
        if abs(b) < 1e-9:
            return None
        tau = -c / b
    else:
        disc = b * b - 4.0 * a * c
        if disc < 0.0:
            return None          # troppo veloce per essere raggiunto
        radice = math.sqrt(disc)
        candidati = [t for t in ((-b - radice) / (2 * a), (-b + radice) / (2 * a)) if t > MIN_TAU]
        if not candidati:
            return None
        tau = min(candidati)

    if not (MIN_TAU < tau <= MAX_TAU):
        return None
    return px + vx * tau, py + vy * tau, tau


def lead_position(px: float, py: float, vx: float, vy: float, lead_s: float) -> tuple[float, float]:
    """Estrapolazione lineare a orizzonte fisso, quando il tempo di volo non conta."""
    return px + vx * lead_s, py + vy * lead_s
