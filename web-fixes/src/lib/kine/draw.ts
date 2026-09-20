import type { AssistHint } from "./assist";
import type { Detection, Marker, TrackSnapshot, WorldObject } from "./types";

const INK = "#0c0d0e";
const PAPER = "#efece6";
const STEEL = "#b7c0cc";
const MUTED = "#8c8e93";
const LINE = "rgba(239,236,230,0.10)";

export function drawWorld(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  objects: WorldObject[],
  markers: Marker[],
  now: number,
  aim: { x: number; y: number } | null,
  origin: { x: number; y: number },
) {
  ctx.fillStyle = INK;
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = LINE;
  ctx.lineWidth = 1;
  const step = 24;
  ctx.beginPath();
  for (let x = 0; x <= w; x += step) {
    ctx.moveTo(x + 0.5, 0);
    ctx.lineTo(x + 0.5, h);
  }
  for (let y = 0; y <= h; y += step) {
    ctx.moveTo(0, y + 0.5);
    ctx.lineTo(w, y + 0.5);
  }
  ctx.stroke();

  ctx.fillStyle = "rgba(239,236,230,0.04)";
  ctx.fillRect(0, h - 28, w, 28);

  drawActuator(ctx, origin.x, origin.y, aim);

  for (const m of markers) {
    ctx.fillStyle = PAPER;
    ctx.beginPath();
    ctx.arc(m.x, m.y, 3.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "rgba(239,236,230,0.35)";
    ctx.beginPath();
    ctx.moveTo(m.x - m.vx * 0.06, m.y - m.vy * 0.06);
    ctx.lineTo(m.x, m.y);
    ctx.stroke();
  }

  for (const o of objects) {
    if (now < o.hiddenUntil) continue;
    ctx.beginPath();
    ctx.fillStyle = `rgb(${o.rgb[0]},${o.rgb[1]},${o.rgb[2]})`;
    ctx.arc(o.x, o.y, o.r, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = PAPER;
    ctx.lineWidth = 1.25;
    ctx.stroke();
  }
}

function drawActuator(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  aim: { x: number; y: number } | null,
) {
  ctx.save();
  ctx.translate(x, y);
  let ang = -Math.PI / 2;
  if (aim) ang = Math.atan2(aim.y - y, aim.x - x);
  ctx.rotate(ang);
  ctx.fillStyle = STEEL;
  ctx.beginPath();
  ctx.moveTo(16, 0);
  ctx.lineTo(-7, 7);
  ctx.lineTo(-7, -7);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = PAPER;
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(0, 0, 5, 0, Math.PI * 2);
  ctx.fillStyle = INK;
  ctx.fill();
  ctx.strokeStyle = STEEL;
  ctx.stroke();
  ctx.restore();
}

export function drawHud(
  ctx: CanvasRenderingContext2D,
  args: {
    detections: Detection[];
    tracks: TrackSnapshot[];
    aim: { x: number; y: number };
    hint: AssistHint;
    flash: boolean;
  },
) {
  const { detections, tracks, aim, hint, flash } = args;

  for (const d of detections) {
    ctx.strokeStyle = "rgba(143,191,181,0.85)";
    ctx.lineWidth = 1;
    ctx.strokeRect(d.x + 0.5, d.y + 0.5, d.w, d.h);
  }

  for (const tr of tracks) {
    ctx.strokeStyle = "rgba(239,236,230,0.55)";
    ctx.lineWidth = 1;
    ctx.strokeRect(tr.x - tr.w / 2, tr.y - tr.h / 2, tr.w, tr.h);
    ctx.fillStyle = PAPER;
    ctx.font = "500 10px 'IBM Plex Mono', ui-monospace, monospace";
    ctx.fillText(`#${tr.id} ${tr.name}`, tr.x - tr.w / 2, Math.max(10, tr.y - tr.h / 2 - 4));
    ctx.strokeStyle = "rgba(183,192,204,0.9)";
    ctx.beginPath();
    ctx.moveTo(tr.x, tr.y);
    ctx.lineTo(tr.x + tr.vx * 0.12, tr.y + tr.vy * 0.12);
    ctx.stroke();
    if (tr.predicted) {
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = "rgba(212,184,150,0.75)";
      ctx.beginPath();
      ctx.arc(tr.predicted.x, tr.predicted.y, 5, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  if (hint.suggested) {
    ctx.setLineDash([2, 3]);
    ctx.strokeStyle = "rgba(183,192,204,0.7)";
    ctx.beginPath();
    ctx.moveTo(aim.x, aim.y);
    ctx.lineTo(hint.suggested.x, hint.suggested.y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.strokeStyle = "rgba(183,192,204,0.95)";
    ctx.beginPath();
    ctx.arc(hint.suggested.x, hint.suggested.y, 6, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = STEEL;
    ctx.font = "500 9px 'IBM Plex Sans', sans-serif";
    ctx.fillText("guarda lì", hint.suggested.x + 8, hint.suggested.y - 6);
  }

  if (flash && hint.mag > 0.2) {
    ctx.strokeStyle = PAPER;
    ctx.beginPath();
    ctx.moveTo(aim.x, aim.y);
    ctx.lineTo(aim.x + hint.dx, aim.y + hint.dy);
    ctx.stroke();
  }

  drawCrosshair(ctx, aim.x, aim.y);
}

function drawCrosshair(ctx: CanvasRenderingContext2D, x: number, y: number) {
  ctx.strokeStyle = PAPER;
  ctx.lineWidth = 1.25;
  ctx.beginPath();
  ctx.arc(x, y, 9, 0, Math.PI * 2);
  ctx.moveTo(x - 14, y);
  ctx.lineTo(x - 4, y);
  ctx.moveTo(x + 4, y);
  ctx.lineTo(x + 14, y);
  ctx.moveTo(x, y - 14);
  ctx.lineTo(x, y - 4);
  ctx.moveTo(x, y + 4);
  ctx.lineTo(x, y + 14);
  ctx.stroke();
}

// Canvas di appoggio riusato: `document.createElement("canvas")` dentro il
// loop creava 20-60 elementi al secondo, tutti da raccogliere.
let pipScratch: HTMLCanvasElement | null = null;

export function drawPip(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  image: ImageData | null,
  dropped: boolean,
) {
  ctx.fillStyle = INK;
  ctx.fillRect(0, 0, w, h);
  if (image) {
    if (!pipScratch) pipScratch = document.createElement("canvas");
    if (pipScratch.width !== image.width || pipScratch.height !== image.height) {
      pipScratch.width = image.width;
      pipScratch.height = image.height;
    }
    pipScratch.getContext("2d")!.putImageData(image, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(pipScratch, 0, 0, w, h);
  }
  ctx.fillStyle = "rgba(12,13,14,0.45)";
  ctx.fillRect(0, 0, w, 22);
  ctx.fillStyle = dropped ? MUTED : PAPER;
  ctx.font = "500 10px 'IBM Plex Mono', ui-monospace, monospace";
  ctx.fillText(!image || dropped ? "NODO B · IN ATTESA" : "NODO B · FRAME RICOSTRUITO", 8, 14);
}
