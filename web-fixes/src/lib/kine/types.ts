export type Rgb = [number, number, number];

export type ColorSpec = {
  name: string;
  rgb: Rgb;
  tolerance: number;
};

export type Scenario = "lineare" | "rimbalzi" | "accelerato" | "occlusione";

export type KineConfig = {
  capture: { width: number; height: number; fps: number };
  network: {
    latencyMs: number;
    jitterMs: number;
    packetLoss: number;
    chunkCount: number;
    timeoutMs: number;
  };
  detection: {
    colors: ColorSpec[];
    minArea: number;
    maxArea: number;
  };
  prediction: {
    useKalman: boolean;
    maxVelocity: number;
    processNoise: number;
    measureNoise: number;
    maxMissed: number;
    gatePx: number;
  };
  control: {
    markerSpeed: number;
    assistEnabled: boolean;
    maxCorrectionPx: number;
    assistBlend: number;
    leadMs: number;
    assistGatePx: number;
    cooldownMs: number;
    minHits: number;
  };
  arena: {
    scenario: Scenario;
    objectCount: number;
  };
};

export type Detection = {
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
  cx: number;
  cy: number;
  confidence: number;
};

export type TrackSnapshot = {
  id: number;
  name: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  w: number;
  h: number;
  confidence: number;
  hits: number;
  missed: number;
  speed: number;
  predicted: { x: number; y: number } | null;
};

export type CommandMsg = {
  type: "fire";
  x: number;
  y: number;
  userX: number;
  userY: number;
  dx: number;
  dy: number;
  trackId: number | null;
  assist: boolean;
  sentAt: number;
};

export type Metrics = {
  fps: number;
  captureFps: number;
  oneWayMs: number;
  rttMs: number;
  packetLoss: number;
  frameLoss: number;
  framesOk: number;
  framesDrop: number;
  packetsSent: number;
  packetsLost: number;
  tracks: number;
  shots: number;
  hits: number;
  meanError: number;
  lastAssistPx: number;
  leadMs: number;
};

export type WorldObject = {
  id: number;
  name: string;
  rgb: Rgb;
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  hiddenUntil: number;
};

export type Marker = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  alive: boolean;
  targetName: string | null;
  /** Distanza minima dalla superficie del bersaglio durante il volo; 0 se a segno. */
  missPx: number;
};
