import type { KineConfig } from "./types";

export const DEFAULT_CONFIG: KineConfig = {
  capture: { width: 480, height: 270, fps: 24 },
  network: {
    latencyMs: 40,
    jitterMs: 12,
    packetLoss: 0.04,
    chunkCount: 8,
    timeoutMs: 180,
  },
  detection: {
    colors: [
      { name: "rosso", rgb: [196, 72, 58], tolerance: 72 },
      { name: "verde", rgb: [74, 148, 96], tolerance: 72 },
      { name: "azzurro", rgb: [70, 128, 176], tolerance: 72 },
    ],
    minArea: 180,
    maxArea: 18000,
  },
  prediction: {
    useKalman: true,
    maxVelocity: 520,
    processNoise: 28,
    measureNoise: 6,
    maxMissed: 18,
    gatePx: 72,
  },
  control: {
    markerSpeed: 380,
    assistEnabled: true,
    maxCorrectionPx: 8,
    assistBlend: 0.4,
    leadMs: 50,
    assistGatePx: 78,
    cooldownMs: 220,
    minHits: 3,
  },
  arena: {
    scenario: "rimbalzi",
    objectCount: 3,
  },
};

const STORAGE_KEY = "kine-config-v2";

export function loadConfig(): KineConfig {
  if (typeof window === "undefined") return structuredClone(DEFAULT_CONFIG);
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return structuredClone(DEFAULT_CONFIG);
    const parsed = JSON.parse(raw) as Partial<KineConfig>;
    return {
      ...structuredClone(DEFAULT_CONFIG),
      ...parsed,
      capture: { ...DEFAULT_CONFIG.capture, ...parsed.capture },
      network: { ...DEFAULT_CONFIG.network, ...parsed.network },
      detection: {
        ...DEFAULT_CONFIG.detection,
        ...parsed.detection,
        colors: parsed.detection?.colors ?? DEFAULT_CONFIG.detection.colors,
      },
      prediction: { ...DEFAULT_CONFIG.prediction, ...parsed.prediction },
      control: { ...DEFAULT_CONFIG.control, ...parsed.control },
      arena: { ...DEFAULT_CONFIG.arena, ...parsed.arena },
    };
  } catch {
    return structuredClone(DEFAULT_CONFIG);
  }
}

export function saveConfig(config: KineConfig) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
  } catch {
    /* ignore quota */
  }
}

export const SCENARIO_COPY: Record<KineConfig["arena"]["scenario"], string> = {
  lineare: "Velocità costante — l'anticipo di 50 ms è visibile ma piccolo.",
  rimbalzi: "Urti elastici: dopo il rimbalzo l'assist non deve strappare la mira.",
  accelerato: "Accelerazione casuale: il Kalman stima v, l'assist resta clampato.",
  occlusione: "Scomparse brevi: niente crash, nessuna correzione se il gate è vuoto.",
};
