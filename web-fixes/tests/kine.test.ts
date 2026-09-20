/**
 * Test dei moduli corretti. Node >= 22:
 *   node --experimental-strip-types --test web-fixes/tests/kine.test.ts
 */
import assert from "node:assert/strict";
import test from "node:test";

import { KalmanCV } from "../src/lib/kine/kalman.ts";
import { MultiTracker } from "../src/lib/kine/tracker.ts";
import { computeAssist } from "../src/lib/kine/assist.ts";
import { solveIntercept } from "../src/lib/kine/intercept.ts";
import { DEFAULT_CONFIG } from "../src/lib/kine/config.ts";
import type { Detection, TrackSnapshot } from "../src/lib/kine/types.ts";

const det = (name: string, cx: number, cy: number): Detection => ({
  name, x: cx - 22, y: cy - 22, w: 44, h: 44, cx, cy, confidence: 1,
});

test("il Kalman stima la velocita' di un moto uniforme", () => {
  const tr = new MultiTracker();
  let snaps: TrackSnapshot[] = [];
  for (let k = 0; k < 50; k++) {
    const t = k / 24;
    snaps = tr.update([det("rosso", 100 + 200 * t, 150)], t, DEFAULT_CONFIG);
  }
  assert.ok(Math.abs(snaps[0].vx - 200) < 10, `vx = ${snaps[0].vx}`);
});

test("la covarianza resta simmetrica dopo molti update", () => {
  const kf = new KalmanCV(100, 100, 6, 28);
  for (let k = 0; k < 5000; k++) {
    kf.predict(1 / 24);
    kf.update(100 + k, 100);
  }
  for (let i = 0; i < 4; i++) {
    for (let j = 0; j < 4; j++) {
      const a = kf.P[i * 4 + j];
      const b = kf.P[j * 4 + i];
      assert.ok(Math.abs(a - b) < 1e-6 * Math.max(1, Math.abs(a)), `P[${i}][${j}] asimmetrica`);
    }
  }
});

test("gli id restano unici quando un altro engine fa reset", () => {
  // `let nextId = 1` a livello di MODULO e' condiviso da tutte le istanze, e
  // reset() lo riporta a 1 per tutti. Due engine vivi sullo stesso tab -
  // StrictMode che monta due volte in sviluppo, o due pannelli affiancati -
  // bastano a far riassegnare a uno gli id gia' in uso dall'altro: due track
  // con lo stesso id nello stesso tracker, chiavi React duplicate e un
  // hint.trackId che non individua piu' un bersaglio solo.
  const cfg = structuredClone(DEFAULT_CONFIG);
  const a = new MultiTracker();
  const b = new MultiTracker();
  a.update([det("rosso", 100, 100)], 0, cfg);
  b.update([det("rosso", 300, 100)], 0, cfg);
  a.reset();
  b.update([det("rosso", 300, 100), det("verde", 100, 100)], 1 / 24, cfg);
  const snaps = b.update(
    [det("rosso", 300, 100), det("verde", 100, 100), det("azzurro", 60, 220)],
    2 / 24,
    cfg,
  );
  const ids = snaps.map((s) => s.id);
  assert.equal(new Set(ids).size, ids.length, `id duplicati nello stesso tracker: ${ids}`);
});

test("una traccia non ruba la detection di un'altra", () => {
  const cfg = structuredClone(DEFAULT_CONFIG);
  cfg.prediction.gatePx = 72;
  const tr = new MultiTracker();
  for (let k = 0; k < 6; k++) {
    tr.update([det("rosso", 0, 150), det("rosso", 50, 150)], k / 24, cfg);
  }
  const snaps = tr.update([det("rosso", 49, 150)], 6 / 24, cfg);
  const vicina = snaps.find((s) => Math.abs(s.x - 50) < 10)!;
  const lontana = snaps.find((s) => Math.abs(s.x - 0) < 10)!;
  assert.equal(vicina.missed, 0, "la detection a 1 px doveva andare alla traccia vicina");
  assert.equal(lontana.missed, 1);
});

test("la correzione non supera mai il tetto", () => {
  const track = (x: number, y: number): TrackSnapshot => ({
    id: 1, name: "rosso", x, y, vx: 0, vy: 0, w: 44, h: 44,
    confidence: 1, hits: 10, missed: 0, speed: 0, predicted: null,
  });
  const opts = { enabled: true, maxCorrectionPx: 8, blend: 1, leadMs: 0, gatePx: 1000, minHits: 3 };
  for (let dx = -400; dx <= 400; dx += 7) {
    const h = computeAssist({ x: 0, y: 0 }, [track(dx, 0)], opts);
    assert.ok(Math.hypot(h.dx, h.dy) <= 8 + 1e-9);
  }
});

test("fuori dal gate nessuna correzione", () => {
  const track: TrackSnapshot = {
    id: 1, name: "rosso", x: 500, y: 500, vx: 0, vy: 0, w: 44, h: 44,
    confidence: 1, hits: 10, missed: 0, speed: 0, predicted: null,
  };
  const h = computeAssist({ x: 0, y: 0 }, [track],
    { enabled: true, maxCorrectionPx: 8, blend: 0.4, leadMs: 50, gatePx: 78, minHits: 3 });
  assert.equal(h.mag, 0);
  assert.equal(h.trackId, null);
});

test("solveIntercept risolve un caso verificabile a mano", () => {
  // bersaglio fermo a 100 px sopra l'attuatore, marcatore a 200 px/s -> tau = 0.5 s
  const s = solveIntercept(0, 0, 0, 0, 0, 100, 200)!;
  assert.ok(s !== null);
  assert.ok(Math.abs(s.tau - 0.5) < 1e-6, `tau = ${s.tau}`);
  // bersaglio piu' veloce del marcatore e in fuga: irraggiungibile
  assert.equal(solveIntercept(0, 0, 500, 0, 0, 100, 200), null);
});
