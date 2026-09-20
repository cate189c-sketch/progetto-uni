"""
Mira assistita: gate sul puntamento, anticipo corto, correzione clampata.

Il contratto della consegna, in tre vincoli che il codice deve rendere
impossibili da violare:

  1. il bersaglio si sceglie vicino a DOVE PUNTA L'UTENTE, mai il "migliore"
     del frame -> gate in pixel attorno alla mira;
  2. la correzione e' limitata a pochi pixel -> clamp su max_correction_px;
  3. l'assist non muove il puntatore e non spara -> qui si restituisce solo un
     suggerimento, chi spara e' input_control su richiesta dell'utente.

Senza il gate al punto 1 il sistema diventa un aimbot: sceglierebbe un
bersaglio dall'altra parte del frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from config import AssistCfg
from intercept import lead_position, solve_intercept
from prediction import TrackSnapshot


@dataclass(slots=True, frozen=True)
class AssistHint:
    track_id: int | None = None
    suggested: tuple[float, float] | None = None
    dx: float = 0.0
    dy: float = 0.0
    mag: float = 0.0

    @property
    def active(self) -> bool:
        return self.mag > 0.0


EMPTY = AssistHint()


def compute_assist(
    aim: tuple[float, float],
    tracks: list[TrackSnapshot],
    cfg: AssistCfg,
    *,
    latency_s: float = 0.0,
    origin: tuple[float, float] | None = None,
) -> AssistHint:
    """
    `latency_s` e' l'eta' della misura: quando gira su due nodi, il frame su cui
    sono state calcolate le tracce e' partito una latenza fa. Sommarla
    all'anticipo e' la differenza tra correggere dove il bersaglio era e dove
    sara'. Resta comunque sotto il tetto `max_lead_ms`.

    `origin` e' la posizione dell'attuatore. Se c'e' e cfg.use_intercept e'
    attivo, il punto suggerito risolve l'intercetta invece di estrapolare a
    orizzonte fisso: sul poligono il tempo di volo del marcatore (~0.5 s) pesa
    dieci volte piu' dei 50 ms di lead, ed e' l'errore che domina il risultato.

    Nota sul contratto: il punto SUGGERITO non e' clampato - e' informazione
    mostrata all'utente ("guarda li'"), e un'informazione troncata sarebbe
    sbagliata. La CORREZIONE applicata al colpo resta clampata a
    max_correction_px. L'AI dice, l'utente decide.
    """
    if not cfg.enabled or not tracks:
        return EMPTY

    ax, ay = aim
    tetto = cfg.max_lead_ms / 1000.0
    lead = max(0.0, min(tetto, cfg.lead_ms / 1000.0 + max(0.0, latency_s)))

    def punto(t: TrackSnapshot) -> tuple[float, float]:
        if cfg.use_intercept and origin is not None:
            sol = solve_intercept(t.x, t.y, t.vx, t.vy, origin[0], origin[1], cfg.marker_speed)
            if sol is not None:
                # tau (volo) + lead (latenza della misura): due ritardi distinti
                # che si sommano.
                return lead_position(t.x, t.y, t.vx, t.vy, sol[2] + lead)
        return lead_position(t.x, t.y, t.vx, t.vy, lead)

    best: TrackSnapshot | None = None
    best_d = cfg.gate_px
    suggested: tuple[float, float] | None = None
    for t in tracks:
        if not t.confirmed or t.hits < cfg.min_hits or t.missed >= cfg.max_missed:
            continue
        # Il gate si misura sulla posizione ATTUALE del bersaglio, non sul punto
        # d'intercetta: decide se l'utente sta guardando quell'oggetto, e
        # l'utente guarda dov'e' adesso.
        d = math.hypot(t.x - ax, t.y - ay)
        if d < best_d:
            best_d, best = d, t
            suggested = punto(t)

    if best is None or suggested is None:
        return EMPTY

    ex, ey = suggested[0] - ax, suggested[1] - ay
    dist = math.hypot(ex, ey)
    if dist < 0.5:
        return AssistHint(track_id=best.id, suggested=suggested)

    # blend = quanta parte dell'errore si copre; max_correction_px = il tetto
    # assoluto. Il minimo dei due: su un bersaglio lontano vince il clamp, su
    # uno gia' quasi centrato vince il blend e la correzione svanisce.
    mag = min(dist * cfg.blend, cfg.max_correction_px)
    return AssistHint(
        track_id=best.id,
        suggested=suggested,
        dx=(ex / dist) * mag,
        dy=(ey / dist) * mag,
        mag=mag,
    )


def apply_assist(aim: tuple[float, float], hint: AssistHint) -> tuple[float, float]:
    """Unico punto in cui il delta viene sommato alla mira dell'utente."""
    return aim[0] + hint.dx, aim[1] + hint.dy
