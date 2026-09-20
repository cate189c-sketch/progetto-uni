/**
 * Intercetta cinematica 2D.
 *
 * Dato un oggetto in p con velocità v e un attuatore in origin che emette
 * un marcatore a velocità scalare s, il tempo τ soddisfa
 *   |p + v τ − origin| = s τ
 * che è un'equazione quadratica. Si prende il minimo τ > 0.
 *
 * Perché non "mirare dove sta ora": il marcatore impiega τ a arrivare, e
 * a quel punto l'oggetto si è spostato di vτ. La latenza di rete si somma
 * a τ perché la misura è già vecchia quando il comando torna al nodo A.
 */

export function solveIntercept(
  px: number,
  py: number,
  vx: number,
  vy: number,
  ox: number,
  oy: number,
  speed: number,
): { x: number; y: number; tau: number } | null {
  const rx = px - ox;
  const ry = py - oy;
  const a = vx * vx + vy * vy - speed * speed;
  const b = 2 * (rx * vx + ry * vy);
  const c = rx * rx + ry * ry;

  let tau = -1;
  if (Math.abs(a) < 1e-6) {
    if (Math.abs(b) < 1e-6) return null;
    tau = -c / b;
  } else {
    const disc = b * b - 4 * a * c;
    if (disc < 0) return null;
    const sqrt = Math.sqrt(disc);
    const t1 = (-b - sqrt) / (2 * a);
    const t2 = (-b + sqrt) / (2 * a);
    const cand = [t1, t2].filter((t) => t > 0.02);
    if (!cand.length) return null;
    tau = Math.min(...cand);
  }
  if (tau <= 0 || tau > 4) return null;
  return { x: px + vx * tau, y: py + vy * tau, tau };
}

export function leadPosition(
  px: number,
  py: number,
  vx: number,
  vy: number,
  leadSec: number,
) {
  return { x: px + vx * leadSec, y: py + vy * leadSec };
}
