import type { ColorSpec, Detection } from "./types";

type Blob = {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  sx: number;
  sy: number;
  n: number;
};

/**
 * Rilevamento per soglia cromatica in RGB.
 *
 * Scelta deliberata rispetto a YOLO: è deterministico, gira a 24 fps sul
 * thread principale senza pesi da scaricare, e rende visibile il nesso
 * "colore → maschera → blob → centroid". YOLO resterebbe l'estensione
 * naturale su un flusso webcam con classi semantiche.
 *
 * La maschera usa distanza per-canale (box in RGB), non HSV, perché i
 * bersagli sintetici hanno colori piatti: meno conversioni, stessa robustezza
 * con tolleranza ~70. minArea scarta il rumore da un pixel isolato.
 */
export function detectColorBlobs(
  image: ImageData,
  colors: ColorSpec[],
  minArea: number,
  maxArea: number,
): Detection[] {
  const { width: w, height: h, data } = image;
  const seen = new Uint8Array(w * h);
  const out: Detection[] = [];

  for (const spec of colors) {
    seen.fill(0);
    const [tr, tg, tb] = spec.rgb;
    const tol = spec.tolerance;

    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = y * w + x;
        if (seen[i]) continue;
        const o = i * 4;
        const r = data[o];
        const g = data[o + 1];
        const b = data[o + 2];
        if (
          Math.abs(r - tr) > tol ||
          Math.abs(g - tg) > tol ||
          Math.abs(b - tb) > tol
        ) {
          continue;
        }

        const blob = flood(data, seen, w, h, x, y, tr, tg, tb, tol);
        if (blob.n < minArea || blob.n > maxArea) continue;

        const bw = blob.maxX - blob.minX + 1;
        const bh = blob.maxY - blob.minY + 1;
        out.push({
          name: spec.name,
          x: blob.minX,
          y: blob.minY,
          w: bw,
          h: bh,
          cx: blob.sx / blob.n,
          cy: blob.sy / blob.n,
          confidence: Math.min(1, blob.n / Math.max(maxArea * 0.25, 1)),
        });
      }
    }
  }
  return out;
}

function flood(
  data: Uint8ClampedArray,
  seen: Uint8Array,
  w: number,
  h: number,
  sx: number,
  sy: number,
  tr: number,
  tg: number,
  tb: number,
  tol: number,
): Blob {
  const stack = [sy * w + sx];
  seen[sy * w + sx] = 1;
  const blob: Blob = {
    minX: sx,
    minY: sy,
    maxX: sx,
    maxY: sy,
    sx: 0,
    sy: 0,
    n: 0,
  };

  while (stack.length) {
    const idx = stack.pop()!;
    const x = idx % w;
    const y = (idx / w) | 0;
    const o = idx * 4;
    if (
      Math.abs(data[o] - tr) > tol ||
      Math.abs(data[o + 1] - tg) > tol ||
      Math.abs(data[o + 2] - tb) > tol
    ) {
      continue;
    }
    blob.n++;
    blob.sx += x;
    blob.sy += y;
    if (x < blob.minX) blob.minX = x;
    if (y < blob.minY) blob.minY = y;
    if (x > blob.maxX) blob.maxX = x;
    if (y > blob.maxY) blob.maxY = y;

    if (x > 0 && !seen[idx - 1]) {
      seen[idx - 1] = 1;
      stack.push(idx - 1);
    }
    if (x + 1 < w && !seen[idx + 1]) {
      seen[idx + 1] = 1;
      stack.push(idx + 1);
    }
    if (y > 0 && !seen[idx - w]) {
      seen[idx - w] = 1;
      stack.push(idx - w);
    }
    if (y + 1 < h && !seen[idx + w]) {
      seen[idx + w] = 1;
      stack.push(idx + w);
    }
  }
  return blob;
}
