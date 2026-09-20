import type { KineConfig, Marker, Rgb, WorldObject } from "./types";

const PALETTE: { name: string; rgb: Rgb }[] = [
  { name: "rosso", rgb: [196, 72, 58] },
  { name: "verde", rgb: [74, 148, 96] },
  { name: "azzurro", rgb: [70, 128, 176] },
];

export class Arena {
  objects: WorldObject[] = [];
  markers: Marker[] = [];
  w = 480;
  h = 270;
  private t = 0;
  private nextOcclude = 2.4;
  shots = 0;
  hits = 0;
  errors: number[] = [];

  reset(cfg: KineConfig) {
    this.w = cfg.capture.width;
    this.h = cfg.capture.height;
    this.objects = [];
    this.markers = [];
    this.t = 0;
    this.shots = 0;
    this.hits = 0;
    this.errors = [];
    this.nextOcclude = 1.8;
    const n = Math.max(1, Math.min(5, cfg.arena.objectCount));
    for (let i = 0; i < n; i++) {
      const pal = PALETTE[i % PALETTE.length];
      const speed = 70 + i * 28;
      const ang = 0.4 + i * 0.9;
      this.objects.push({
        id: i + 1,
        name: pal.name,
        rgb: pal.rgb,
        x: this.w * (0.2 + 0.2 * i),
        y: this.h * (0.25 + 0.15 * (i % 3)),
        vx: Math.cos(ang) * speed,
        vy: Math.sin(ang) * speed,
        r: 22 + (i % 2) * 5,
        hiddenUntil: 0,
      });
    }
    if (cfg.arena.scenario === "lineare") {
      for (const o of this.objects) {
        o.vy = 0;
        o.vx = 90 + o.id * 25;
        o.y = this.h * (0.22 + o.id * 0.18);
      }
    }
  }

  step(dt: number, cfg: KineConfig) {
    this.t += dt;
    const scenario = cfg.arena.scenario;
    const margin = 18;

    for (const o of this.objects) {
      if (scenario === "accelerato") {
        o.vx += (Math.random() - 0.5) * 180 * dt;
        o.vy += (Math.random() - 0.5) * 180 * dt;
      }

      o.x += o.vx * dt;
      o.y += o.vy * dt;

      if (scenario === "lineare") {
        if (o.x > this.w - margin) o.x = margin;
        if (o.x < margin) o.x = this.w - margin;
      } else {
        if (o.x < margin) {
          o.x = margin;
          o.vx = Math.abs(o.vx);
        }
        if (o.x > this.w - margin) {
          o.x = this.w - margin;
          o.vx = -Math.abs(o.vx);
        }
        if (o.y < margin) {
          o.y = margin;
          o.vy = Math.abs(o.vy);
        }
        if (o.y > this.h - 36) {
          o.y = this.h - 36;
          o.vy = -Math.abs(o.vy);
        }
      }

      const sp = Math.hypot(o.vx, o.vy);
      const cap = cfg.prediction.maxVelocity;
      if (sp > cap) {
        o.vx *= cap / sp;
        o.vy *= cap / sp;
      }
    }

    if (scenario === "occlusione" && this.t > this.nextOcclude && this.objects.length) {
      const o = this.objects[Math.floor(Math.random() * this.objects.length)];
      o.hiddenUntil = this.t + 0.55;
      this.nextOcclude = this.t + 1.6 + Math.random();
    }

    for (const m of this.markers) {
      if (!m.alive) continue;
      m.x += m.vx * dt;
      m.y += m.vy * dt;
      if (m.x < -10 || m.y < -10 || m.x > this.w + 10 || m.y > this.h + 10) {
        m.alive = false;
        this.pushError(m.missPx);
        continue;
      }
      for (const o of this.objects) {
        if (this.t < o.hiddenUntil) continue;
        const d = Math.hypot(m.x - o.x, m.y - o.y) - o.r;
        // Distanza di mancato dalla SUPERFICIE, aggiornata a ogni passo e
        // registrata a fine volo. `errors.push(d)` al momento dell'impatto
        // registrava la distanza alla quale SCATTA la collisione, cioe' ~ il
        // raggio: il cruscotto mostrava ~23 px con qualunque mira, compresa
        // quella perfetta. Era un numero costante spacciato per una metrica.
        if (d < m.missPx) m.missPx = Math.max(0, d);
        if (d <= 0) {
          m.alive = false;
          m.missPx = 0;
          this.hits++;
          break;
        }
      }
    }
    for (const m of this.markers) {
      if (!m.alive && Number.isFinite(m.missPx)) this.pushError(m.missPx);
    }
    this.markers = this.markers.filter((m) => m.alive);
  }

  private pushError(d: number) {
    if (!Number.isFinite(d)) return;
    this.errors.push(d);
    if (this.errors.length > 40) this.errors.shift();
  }

  emit(x: number, y: number, speed: number, originX: number, originY: number) {
    const dx = x - originX;
    const dy = y - originY;
    const d = Math.hypot(dx, dy) || 1;
    this.markers.push({
      x: originX,
      y: originY,
      vx: (dx / d) * speed,
      vy: (dy / d) * speed,
      alive: true,
      targetName: null,
      missPx: Infinity,
    });
    this.shots++;
  }

  meanError() {
    if (!this.errors.length) return 0;
    return this.errors.reduce((a, b) => a + b, 0) / this.errors.length;
  }
}
