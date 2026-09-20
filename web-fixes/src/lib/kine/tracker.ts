import { KalmanCV } from "./kalman";
import type { Detection, KineConfig, TrackSnapshot } from "./types";

type Track = {
  id: number;
  name: string;
  kf: KalmanCV;
  w: number;
  h: number;
  confidence: number;
  hits: number;
  missed: number;
  lastT: number;
};

/**
 * Associazione greedy stessa-classe + Kalman.
 *
 * Gli id NON possono dipendere dal timestamp (bug della versione iniziale:
 * ogni detection diventava un oggetto nuovo, la velocità restava zero).
 * Qui l'id vive finché il track riceve misure entro `maxMissed` frame.
 *
 * Gating euclideo sul predìtto, non sulla misura grezza: se il filtro
 * coast-a durante un frame perso, il gate resta centrato dove l'oggetto
 * dovrebbe essere.
 */
export class MultiTracker {
  private tracks: Track[] = [];
  // Di istanza, non di modulo: con `let nextId` a livello di file due engine
  // vivi sullo stesso tab (StrictMode monta due volte in sviluppo, o due
  // pannelli affiancati) condividono il contatore, e il reset dell'uno
  // riassegna gli id dell'altro.
  private nextId = 1;

  reset() {
    this.tracks = [];
    this.nextId = 1;
  }

  update(detections: Detection[], t: number, cfg: KineConfig): TrackSnapshot[] {
    const { prediction: p } = cfg;

    for (const tr of this.tracks) {
      const dt = Math.max(1 / 60, Math.min(0.12, t - tr.lastT));
      tr.kf.predict(dt);
      tr.lastT = t;
    }

    // Associazione globale: si ordinano TUTTE le coppie (traccia, detection)
    // per distanza e si consumano in ordine. Facendo scegliere per prima la
    // traccia che capita prima nella lista, una traccia lontana ma dentro il
    // gate poteva prendersi la detection di un'altra che le stava a 1 px:
    // la prima saltava sul bersaglio sbagliato, la seconda andava in missed.
    const coppie: { dist: number; ti: number; di: number }[] = [];
    for (let ti = 0; ti < this.tracks.length; ti++) {
      const tr = this.tracks[ti];
      for (let di = 0; di < detections.length; di++) {
        const d = detections[di];
        if (d.name !== tr.name) continue;
        const dist = Math.hypot(d.cx - tr.kf.px, d.cy - tr.kf.py);
        if (dist < p.gatePx) coppie.push({ dist, ti, di });
      }
    }
    coppie.sort((a, b) => a.dist - b.dist);

    const claimed = new Set<number>();
    const assegnate = new Set<number>();
    for (const { ti, di } of coppie) {
      if (assegnate.has(ti) || claimed.has(di)) continue;
      assegnate.add(ti);
      claimed.add(di);
      const tr = this.tracks[ti];
      const d = detections[di];
      tr.kf.update(d.cx, d.cy);
      const sp = Math.hypot(tr.kf.vx, tr.kf.vy);
      if (sp > p.maxVelocity) {
        const s = p.maxVelocity / sp;
        tr.kf.x[2] *= s;
        tr.kf.x[3] *= s;
      }
      tr.w = d.w;
      tr.h = d.h;
      tr.confidence = d.confidence;
      tr.hits++;
      tr.missed = 0;
    }
    for (let ti = 0; ti < this.tracks.length; ti++) {
      if (!assegnate.has(ti)) this.tracks[ti].missed++;
    }

    for (let i = 0; i < detections.length; i++) {
      if (claimed.has(i)) continue;
      const d = detections[i];
      const kf = new KalmanCV(d.cx, d.cy, p.measureNoise, p.processNoise);
      this.tracks.push({
        id: this.nextId++,
        name: d.name,
        kf,
        w: d.w,
        h: d.h,
        confidence: d.confidence,
        hits: 1,
        missed: 0,
        lastT: t,
      });
    }

    this.tracks = this.tracks.filter((tr) => tr.missed <= p.maxMissed);

    for (const tr of this.tracks) {
      tr.kf.setProcessNoise(p.processNoise);
    }

    return this.tracks.map((tr) => snapshot(tr));
  }
}

function snapshot(tr: Track): TrackSnapshot {
  return {
    id: tr.id,
    name: tr.name,
    x: tr.kf.px,
    y: tr.kf.py,
    vx: tr.kf.vx,
    vy: tr.kf.vy,
    w: tr.w,
    h: tr.h,
    confidence: tr.confidence,
    hits: tr.hits,
    missed: tr.missed,
    speed: Math.hypot(tr.kf.vx, tr.kf.vy),
    predicted: null,
  };
}
