/**
 * Filtro di Kalman 2D a velocità costante.
 *
 * Stato x = [px, py, vx, vy]^T
 *
 * Perché questo modello: tra un frame e il successivo un oggetto nel piano
 * immagine si muove in modo approssimativamente lineare. L'accelerazione
 * (rimbalzi, cambi di direzione) è modellata come rumore di processo Q,
 * non come stato — più stabile con poche misure rumorose dal blob detector.
 *
 * La versione precedente del progetto usava F = I (nessuna integrazione
 * della velocità) e mescolava lo stato x con le covarianze: K e S non
 * erano definite. Qui P, Q, R, K seguono le equazioni classiche.
 */

export class KalmanCV {
  x = new Float64Array(4);
  P = new Float64Array(16);
  private qAcc = 20;
  private readonly r: number;

  constructor(px: number, py: number, measureNoise: number, processNoise: number) {
    this.x[0] = px;
    this.x[1] = py;
    this.r = measureNoise * measureNoise;
    const p0 = 400;
    this.P[0] = p0;
    this.P[5] = p0;
    this.P[10] = p0;
    this.P[15] = p0;
    this.setProcessNoise(processNoise);
  }

  setProcessNoise(qAcc: number) {
    this.qAcc = qAcc;
  }

  /** Predizione a dt secondi (modello CV: p += v dt). */
  predict(dt: number) {
    const { x, P } = this;
    x[0] += x[2] * dt;
    x[1] += x[3] * dt;

    // P = F P Fᵀ + Q  con F = [[1,0,dt,0],[0,1,0,dt],[0,0,1,0],[0,0,0,1]]
    const dt2 = dt * dt;
    const q = this.qAcc * this.qAcc;
    const q11 = q * dt2 * dt2 / 4;
    const q13 = q * dt2 * dt / 2;
    const q33 = q * dt2;

    const p00 = P[0], p01 = P[1], p02 = P[2], p03 = P[3];
    const p10 = P[4], p11 = P[5], p12 = P[6], p13 = P[7];
    const p20 = P[8], p21 = P[9], p22 = P[10], p23 = P[11];
    const p30 = P[12], p31 = P[13], p32 = P[14], p33 = P[15];

    const n00 = p00 + dt * (p20 + p02) + dt2 * p22;
    const n01 = p01 + dt * (p21 + p03) + dt2 * p23;
    const n02 = p02 + dt * p22;
    const n03 = p03 + dt * p23;
    const n10 = p10 + dt * (p30 + p12) + dt2 * p32;
    const n11 = p11 + dt * (p31 + p13) + dt2 * p33;
    const n12 = p12 + dt * p32;
    const n13 = p13 + dt * p33;
    const n20 = p20 + dt * p22;
    const n21 = p21 + dt * p23;
    const n22 = p22;
    const n23 = p23;
    const n30 = p30 + dt * p32;
    const n31 = p31 + dt * p33;
    const n32 = p32;
    const n33 = p33;

    P[0] = n00 + q11;
    P[1] = n01;
    P[2] = n02 + q13;
    P[3] = n03;
    P[4] = n10;
    P[5] = n11 + q11;
    P[6] = n12;
    P[7] = n13 + q13;
    P[8] = n20 + q13;
    P[9] = n21;
    P[10] = n22 + q33;
    P[11] = n23;
    P[12] = n30;
    P[13] = n31 + q13;
    P[14] = n32;
    P[15] = n33 + q33;
  }

  /** Aggiornamento con misura di posizione (centroid del blob). */
  update(zx: number, zy: number) {
    const { x, P, r } = this;
    // Innovazione y = z - H x, H prende solo px, py
    const y0 = zx - x[0];
    const y1 = zy - x[1];
    // S = H P Hᵀ + R  (2×2)
    const s00 = P[0] + r;
    const s01 = P[1];
    const s10 = P[4];
    const s11 = P[5] + r;
    const det = s00 * s11 - s01 * s10;
    if (Math.abs(det) < 1e-9) return;
    const inv00 = s11 / det;
    const inv01 = -s01 / det;
    const inv10 = -s10 / det;
    const inv11 = s00 / det;
    // K = P Hᵀ S⁻¹  (4×2)
    const k00 = P[0] * inv00 + P[1] * inv10;
    const k01 = P[0] * inv01 + P[1] * inv11;
    const k10 = P[4] * inv00 + P[5] * inv10;
    const k11 = P[4] * inv01 + P[5] * inv11;
    const k20 = P[8] * inv00 + P[9] * inv10;
    const k21 = P[8] * inv01 + P[9] * inv11;
    const k30 = P[12] * inv00 + P[13] * inv10;
    const k31 = P[12] * inv01 + P[13] * inv11;

    x[0] += k00 * y0 + k01 * y1;
    x[1] += k10 * y0 + k11 * y1;
    x[2] += k20 * y0 + k21 * y1;
    x[3] += k30 * y0 + k31 * y1;

    // P = (I - K H) P
    const p00 = P[0], p01 = P[1], p02 = P[2], p03 = P[3];
    const p10 = P[4], p11 = P[5], p12 = P[6], p13 = P[7];
    const p20 = P[8], p21 = P[9], p22 = P[10], p23 = P[11];
    const p30 = P[12], p31 = P[13], p32 = P[14], p33 = P[15];

    P[0] = (1 - k00) * p00 - k01 * p10;
    P[1] = (1 - k00) * p01 - k01 * p11;
    P[2] = (1 - k00) * p02 - k01 * p12;
    P[3] = (1 - k00) * p03 - k01 * p13;
    P[4] = -k10 * p00 + (1 - k11) * p10;
    P[5] = -k10 * p01 + (1 - k11) * p11;
    P[6] = -k10 * p02 + (1 - k11) * p12;
    P[7] = -k10 * p03 + (1 - k11) * p13;
    P[8] = -k20 * p00 - k21 * p10 + p20;
    P[9] = -k20 * p01 - k21 * p11 + p21;
    P[10] = -k20 * p02 - k21 * p12 + p22;
    P[11] = -k20 * p03 - k21 * p13 + p23;
    P[12] = -k30 * p00 - k31 * p10 + p30;
    P[13] = -k30 * p01 - k31 * p11 + p31;
    P[14] = -k30 * p02 - k31 * p12 + p32;
    P[15] = -k30 * p03 - k31 * p13 + p33;
  }

  get px() {
    return this.x[0];
  }
  get py() {
    return this.x[1];
  }
  get vx() {
    return this.x[2];
  }
  get vy() {
    return this.x[3];
  }

  coast(dt: number) {
    return { x: this.x[0] + this.x[2] * dt, y: this.x[1] + this.x[3] * dt };
  }
}
