import type { TrackSnapshot } from "./types";

export type AssistHint = {
  trackId: number | null;
  suggested: { x: number; y: number } | null;
  dx: number;
  dy: number;
  mag: number;
};

const EMPTY: AssistHint = {
  trackId: null,
  suggested: null,
  dx: 0,
  dy: 0,
  mag: 0,
};

/**
 * Mira assistita, non controllo.
 *
 * L'utente sceglie dove puntare. Qui si prende il bersaglio più vicino al
 * puntamento (entro un gate), si anticipa di pochi millisecondi, e si
 * restituisce una correzione clampata. Senza gate l'AI "ruberebbe" la mira
 * verso un oggetto dall'altra parte del frame: viola la consegna.
 */
export function computeAssist(
  aim: { x: number; y: number },
  tracks: TrackSnapshot[],
  opts: {
    enabled: boolean;
    maxCorrectionPx: number;
    blend: number;
    leadMs: number;
    gatePx: number;
    minHits: number;
  },
): AssistHint {
  if (!opts.enabled) return EMPTY;

  const ready = tracks.filter((t) => t.hits >= opts.minHits && t.missed < 4);
  if (!ready.length) return EMPTY;

  const lead = Math.max(0, Math.min(0.12, opts.leadMs / 1000));
  let best: TrackSnapshot | null = null;
  let bestD = opts.gatePx;

  for (const t of ready) {
    const sx = t.x + t.vx * lead;
    const sy = t.y + t.vy * lead;
    const d = Math.hypot(sx - aim.x, sy - aim.y);
    if (d < bestD) {
      bestD = d;
      best = t;
    }
  }
  if (!best) return EMPTY;

  const suggested = {
    x: best.x + best.vx * lead,
    y: best.y + best.vy * lead,
  };
  const ex = suggested.x - aim.x;
  const ey = suggested.y - aim.y;
  const dist = Math.hypot(ex, ey);
  if (dist < 0.5) {
    return { trackId: best.id, suggested, dx: 0, dy: 0, mag: 0 };
  }

  const want = dist * Math.max(0, Math.min(1, opts.blend));
  const mag = Math.min(want, Math.max(0, opts.maxCorrectionPx));
  const dx = (ex / dist) * mag;
  const dy = (ey / dist) * mag;
  return { trackId: best.id, suggested, dx, dy, mag };
}

export function applyAssist(
  aim: { x: number; y: number },
  hint: AssistHint,
): { x: number; y: number } {
  return { x: aim.x + hint.dx, y: aim.y + hint.dy };
}
