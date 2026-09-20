"""
Esperimento riproducibile: l'assist migliora la precisione, e di quanto?

Gira la pipeline completa (poligono -> render -> detection -> Kalman -> assist
-> colpo) a passo fisso e senza finestra, con un "utente" simulato che mira con
un errore gaussiano noto. Niente socket: il tempo e' simulato, quindi due
esecuzioni con lo stesso seed danno gli stessi numeri e il confronto misura
l'effetto invece del rumore. Il percorso di rete e' coperto dai test di
integrazione.

    python3 bench.py --shots 200 --aim-error 18

Le tre righe che stampa sono quelle da mettere nella relazione.
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import statistics
from dataclasses import dataclass

from assist import compute_assist
from capture import Shot, SyntheticArena
from config import Config, load_config
from detection import detect_color_blobs
from prediction import MultiTracker
from pathlib import Path


@dataclass
class Risultato:
    etichetta: str
    colpi: int
    hit_rate: float
    dmin_media: float
    dmin_mediana: float
    correzione_media: float


def run(cfg: Config, *, assist_on: bool, segue_suggerimento: bool, shots: int,
        aim_error: float, seed: int) -> Risultato:
    cfg.assist.enabled = assist_on
    cfg.assist.cooldown_ms = 0.0
    rng = random.Random(seed)
    arena = SyntheticArena(cfg.capture, cfg.detection.colors, seed=seed)
    tracker = MultiTracker(cfg.prediction)

    dt = 1.0 / cfg.capture.fps
    t = 0.0
    sparati = 0
    correzioni: list[float] = []

    while sparati < shots:
        arena.step(dt)
        t += dt
        tracks = tracker.update(detect_color_blobs(arena.render(), cfg.detection), t)

        # L'utente prende di mira un disco e sbaglia di aim_error px in una
        # direzione casuale: e' la "mano tremante" che l'assist deve correggere.
        if tracks and sparati * 6 < int(t * cfg.capture.fps):
            bersaglio = arena.objs[sparati % len(arena.objs)]
            ang = rng.uniform(0, 2 * math.pi)
            raggio = abs(rng.gauss(0, aim_error))
            mira = (bersaglio.x + math.cos(ang) * raggio, bersaglio.y + math.sin(ang) * raggio)
            hint = compute_assist(mira, tracks, cfg.assist, origin=arena.origin)
            correzioni.append(hint.mag)
            errore_reale, _ = arena.nearest_object(*mira)
            # "segue_suggerimento": l'utente guarda dove l'AI indica e ri-punta
            # li'. E' il modo previsto di usare il sistema; la sola correzione
            # automatica di 8 px e' volutamente troppo piccola per sostituirlo.
            if segue_suggerimento and hint.suggested is not None:
                bersaglio_finale = hint.suggested
            else:
                bersaglio_finale = (mira[0] + hint.dx, mira[1] + hint.dy)
            arena.emit(
                bersaglio_finale[0],
                bersaglio_finale[1],
                cfg.assist.marker_speed,
                Shot(assisted=hint.active, aim_error_px=errore_reale,
                     target_id=bersaglio.id, track_id=hint.track_id),
            )
            sparati += 1

    while arena.markers:            # lascia atterrare i colpi in volo
        arena.step(dt)

    dmin = [s.miss_px for s in arena.log.shots if math.isfinite(s.miss_px)]
    tot = arena.log.summary()["totale"]
    return Risultato(
        etichetta="assist ON " if assist_on else "assist OFF",
        colpi=tot["n"],
        hit_rate=tot["hit_rate"],
        dmin_media=statistics.mean(dmin) if dmin else float("nan"),
        dmin_mediana=statistics.median(dmin) if dmin else float("nan"),
        correzione_media=statistics.mean(correzioni) if correzioni else 0.0,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.json")))
    ap.add_argument("--shots", type=int, default=200)
    ap.add_argument("--aim-error", type=float, default=18.0, help="dev. standard dell'errore di mira, px")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    logging.basicConfig(level=logging.ERROR)

    print(f"{args.shots} colpi per condizione | errore di mira sigma = {args.aim_error} px | seed {args.seed}\n")
    intestazione = ("condizione", "colpi", "a segno", "mancato medio", "mediana", "corr. media")
    print(f"{intestazione[0]:<26} {intestazione[1]:>6} {intestazione[2]:>9} "
          f"{intestazione[3]:>14} {intestazione[4]:>10} {intestazione[5]:>12}")
    print("-" * 84)
    condizioni = [
        ("assist OFF", False, False),
        ("assist ON (solo nudge)", True, False),
        ("assist ON + segui il sugg.", True, True),
    ]
    risultati = {}
    for etichetta, assist_on, segue in condizioni:
        r = run(load_config(args.config), assist_on=assist_on, segue_suggerimento=segue,
                shots=args.shots, aim_error=args.aim_error, seed=args.seed)
        risultati[etichetta] = r
        print(f"{etichetta:<26} {r.colpi:>6} {r.hit_rate * 100:>8.1f}% "
              f"{r.dmin_media:>13.2f}p {r.dmin_mediana:>9.2f}p {r.correzione_media:>11.2f}p")

    off = risultati["assist OFF"]
    nudge = risultati["assist ON (solo nudge)"]
    segui = risultati["assist ON + segui il sugg."]
    print("-" * 84)
    print(f"La sola correzione automatica ({nudge.correzione_media:.1f} px in media, tetto "
          f"{load_config(args.config).assist.max_correction_px:.0f} px) riduce l'errore di "
          f"{off.dmin_media - nudge.dmin_media:+.2f} px: e' poco, ed e' voluto.")
    print(f"Seguire il punto suggerito porta i colpi a segno da {off.hit_rate * 100:.1f}% a "
          f"{segui.hit_rate * 100:.1f}% e l'errore da {off.dmin_media:.2f} a "
          f"{segui.dmin_media:.2f} px: il valore del sistema sta nell'informazione,")
    print("non nella correzione. L'AI dice dove guardare, il giocatore decide e spara.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
