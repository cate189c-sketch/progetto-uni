import { Arena } from "./arena";
import { applyAssist, computeAssist, type AssistHint } from "./assist";
import { detectColorBlobs } from "./detect";
import { leadPosition } from "./intercept";
import { SimulatedUdp } from "./network";
import { MultiTracker } from "./tracker";
import { drawHud, drawPip, drawWorld } from "./draw";
import type { CommandMsg, Detection, KineConfig, Metrics, TrackSnapshot } from "./types";

export type EngineSink = {
  world: HTMLCanvasElement;
  pip: HTMLCanvasElement;
};

export class KineEngine {
  readonly arena = new Arena();
  readonly net = new SimulatedUdp();
  readonly tracker = new MultiTracker();
  cfg: KineConfig;
  running = false;
  paused = false;

  tracks: TrackSnapshot[] = [];
  detections: Detection[] = [];
  metrics: Metrics;
  aim = { x: 240, y: 135 };
  hint: AssistHint = {
    trackId: null,
    suggested: null,
    dx: 0,
    dy: 0,
    mag: 0,
  };
  lastAssistPx = 0;
  lastDrop = false;
  flashUntil = 0;

  private worldCtx: CanvasRenderingContext2D | null = null;
  private pipCtx: CanvasRenderingContext2D | null = null;
  private offscreen: HTMLCanvasElement | null = null;
  private offCtx: CanvasRenderingContext2D | null = null;
  private raf = 0;
  private lastTs = 0;
  private acc = 0;
  private frames = 0;
  private fpsT = 0;
  private fps = 0;
  private lastFire = 0;
  private lastFrame: ImageData | null = null;
  private video: HTMLVideoElement | null = null;
  source: "synthetic" | "webcam" = "synthetic";
  onUi: (() => void) | null = null;

  constructor(cfg: KineConfig) {
    this.cfg = cfg;
    this.metrics = emptyMetrics();
    this.aim = { x: cfg.capture.width / 2, y: cfg.capture.height / 2 };
    this.net.onFrame = (img, sentAt, recvAt) => this.onRemoteFrame(img, sentAt, recvAt);
    this.net.onCommand = (json) => this.onRemoteCommand(json);
    this.net.onAck = (rtt) => {
      this.metrics.rttMs = rtt;
    };
  }

  attach(sink: EngineSink) {
    this.worldCtx = sink.world.getContext("2d");
    this.pipCtx = sink.pip.getContext("2d");
    this.resize(sink.world, sink.pip);
  }

  resize(world: HTMLCanvasElement, pip: HTMLCanvasElement) {
    const { width, height } = this.cfg.capture;
    world.width = width;
    world.height = height;
    pip.width = width;
    pip.height = height;
    if (!this.offscreen) this.offscreen = document.createElement("canvas");
    this.offscreen.width = width;
    this.offscreen.height = height;
    this.offCtx = this.offscreen.getContext("2d", { willReadFrequently: true });
  }

  setAimFromPointer(clientX: number, clientY: number, canvas: HTMLCanvasElement) {
    const r = canvas.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;
    this.aim = {
      x: ((clientX - r.left) / r.width) * this.cfg.capture.width,
      y: ((clientY - r.top) / r.height) * this.cfg.capture.height,
    };
  }

  setConfig(cfg: KineConfig) {
    const scenarioChanged =
      cfg.arena.scenario !== this.cfg.arena.scenario ||
      cfg.arena.objectCount !== this.cfg.arena.objectCount ||
      cfg.capture.width !== this.cfg.capture.width ||
      cfg.capture.height !== this.cfg.capture.height;
    this.cfg = cfg;
    this.net.configure(cfg.network);
    if (scenarioChanged) {
      this.arena.reset(cfg);
      this.tracker.reset();
    }
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.paused = false;
    this.arena.reset(this.cfg);
    this.tracker.reset();
    this.net.configure(this.cfg.network);
    this.lastTs = performance.now();
    this.fpsT = this.lastTs;
    const loop = (ts: number) => {
      if (!this.running) return;
      this.raf = requestAnimationFrame(loop);
      if (this.paused) {
        this.lastTs = ts;
        return;
      }
      this.tick(ts);
    };
    this.raf = requestAnimationFrame(loop);
  }

  pause() {
    this.paused = true;
  }

  resume() {
    this.paused = false;
    this.lastTs = performance.now();
  }

  stop() {
    this.running = false;
    this.paused = false;
    cancelAnimationFrame(this.raf);
    this.net.dispose();
    this.stopWebcam();
  }

  async startWebcam() {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("getUserMedia non disponibile (serve HTTPS o localhost)");
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { width: 640, height: 360, facingMode: "user" },
      audio: false,
    });
    const video = document.createElement("video");
    video.srcObject = stream;
    video.muted = true;
    video.playsInline = true;
    try {
      await video.play();
    } catch (e) {
      // Se play() fallisce lo stream resta aperto e la spia della webcam
      // accesa, senza che niente lo usi.
      for (const t of stream.getTracks()) t.stop();
      throw e;
    }
    this.video = video;
    this.source = "webcam";
    this.tracker.reset();
  }

  stopWebcam() {
    const v = this.video;
    if (v?.srcObject) {
      for (const t of (v.srcObject as MediaStream).getTracks()) t.stop();
    }
    this.video = null;
    this.source = "synthetic";
  }

  /** Sparo dell'utente. Shift / assist=false = colpo puro, senza correzione. */
  fireUser(useAssist = true) {
    const now = performance.now();
    if (now - this.lastFire < this.cfg.control.cooldownMs) return;
    this.lastFire = now;

    const hint = useAssist
      ? computeAssist(this.aim, this.tracks, {
          enabled: this.cfg.control.assistEnabled,
          maxCorrectionPx: this.cfg.control.maxCorrectionPx,
          blend: this.cfg.control.assistBlend,
          leadMs: this.cfg.control.leadMs,
          gatePx: this.cfg.control.assistGatePx,
          minHits: this.cfg.control.minHits,
        })
      : {
          trackId: null,
          suggested: null,
          dx: 0,
          dy: 0,
          mag: 0,
        };

    const aimed = applyAssist(this.aim, hint);
    this.lastAssistPx = hint.mag;
    this.flashUntil = now + 180;
    this.hint = hint;

    const cmd: CommandMsg = {
      type: "fire",
      x: aimed.x,
      y: aimed.y,
      userX: this.aim.x,
      userY: this.aim.y,
      dx: hint.dx,
      dy: hint.dy,
      trackId: hint.trackId,
      assist: useAssist && this.cfg.control.assistEnabled,
      sentAt: now,
    };
    this.net.sendCommand(JSON.stringify(cmd));
  }

  private tick(ts: number) {
    const dt = Math.min(0.05, (ts - this.lastTs) / 1000);
    this.lastTs = ts;
    this.frames++;
    if (ts - this.fpsT > 500) {
      this.fps = (this.frames * 1000) / (ts - this.fpsT);
      this.frames = 0;
      this.fpsT = ts;
      this.pushMetrics();
      this.onUi?.();
    }

    if (this.source === "synthetic") {
      this.arena.step(dt, this.cfg);
    }

    this.hint = computeAssist(this.aim, this.tracks, {
      enabled: this.cfg.control.assistEnabled,
      maxCorrectionPx: this.cfg.control.maxCorrectionPx,
      blend: this.cfg.control.assistBlend,
      leadMs: this.cfg.control.leadMs,
      gatePx: this.cfg.control.assistGatePx,
      minHits: this.cfg.control.minHits,
    });

    this.renderWorld(ts / 1000);
    this.acc += dt;
    const period = 1 / Math.max(8, this.cfg.capture.fps);
    if (this.acc >= period) {
      // `-= period`, non `= 0`: azzerando si butta via il resto e la cattura
      // si aggancia al ritmo del requestAnimationFrame. Con rAF a 60 Hz e fps
      // configurati a 24, l'accumulatore superava la soglia ogni 3 frame ->
      // 20 fps reali contro i 24 dichiarati dalla metrica captureFps.
      this.acc -= period;
      if (this.acc > period) this.acc = 0; // rientro da una pausa lunga
      this.captureAndSend();
    }

    if (this.worldCtx) {
      drawHud(this.worldCtx, {
        detections: this.detections,
        tracks: this.tracks,
        aim: this.aim,
        hint: this.hint,
        flash: ts < this.flashUntil,
      });
    }
    if (this.pipCtx) {
      drawPip(
        this.pipCtx,
        this.cfg.capture.width,
        this.cfg.capture.height,
        this.lastFrame,
        this.lastDrop,
      );
    }
  }

  private origin() {
    return { x: this.cfg.capture.width / 2, y: this.cfg.capture.height - 16 };
  }

  private renderWorld(now: number) {
    const ctx = this.worldCtx;
    if (!ctx) return;
    const { width, height } = this.cfg.capture;
    if (this.source === "webcam" && this.video) {
      ctx.fillStyle = "#0c0d0e";
      ctx.fillRect(0, 0, width, height);
      ctx.drawImage(this.video, 0, 0, width, height);
    } else {
      drawWorld(
        ctx,
        width,
        height,
        this.arena.objects,
        this.arena.markers,
        now,
        this.aim,
        this.origin(),
      );
    }
  }

  private captureAndSend() {
    const off = this.offCtx;
    const world = this.worldCtx;
    if (!off || !world || !this.offscreen) return;
    off.drawImage(world.canvas, 0, 0);
    const img = off.getImageData(0, 0, this.cfg.capture.width, this.cfg.capture.height);
    this.net.sendFrame(img, this.cfg.network.chunkCount);
  }

  private onRemoteFrame(img: ImageData, sentAt: number, recvAt: number) {
    this.lastFrame = img;
    this.lastDrop = false;
    this.metrics.oneWayMs = recvAt - sentAt;
    const dets = detectColorBlobs(
      img,
      this.cfg.detection.colors,
      this.cfg.detection.minArea,
      this.cfg.detection.maxArea,
    );
    this.detections = dets;
    // Il tracker viene datato con l'istante di CATTURA, non di arrivo: usando
    // recvAt il jitter di rete (+/-12 ms su frame da 42 ms, ~30%) entrava nel
    // dt del Kalman e ne usciva come rumore sulla velocita' stimata, cioe'
    // proprio nella grandezza su cui si basa l'anticipo.
    const snaps = this.tracker.update(dets, sentAt / 1000, this.cfg);
    const lead = Math.max(0, Math.min(0.12, this.cfg.control.leadMs / 1000));
    for (const tr of snaps) {
      if (!this.cfg.prediction.useKalman) {
        tr.vx = 0;
        tr.vy = 0;
      }
      tr.predicted = leadPosition(tr.x, tr.y, tr.vx, tr.vy, lead);
    }
    this.tracks = snaps;
  }

  private onRemoteCommand(json: string) {
    try {
      const cmd = JSON.parse(json) as CommandMsg;
      if (cmd.type === "fire" && this.source === "synthetic") {
        const o = this.origin();
        this.arena.emit(cmd.x, cmd.y, this.cfg.control.markerSpeed, o.x, o.y);
      }
    } catch {
      /* comandi malformati: il sistema non deve crashare */
    }
  }

  private pushMetrics() {
    this.metrics = {
      fps: this.fps,
      captureFps: this.cfg.capture.fps,
      oneWayMs: this.net.lastOneWay,
      rttMs: this.net.lastRtt,
      packetLoss: this.net.packetLossRate,
      frameLoss: this.net.frameLossRate,
      framesOk: this.net.framesOk,
      framesDrop: this.net.framesDrop,
      packetsSent: this.net.packetsSent,
      packetsLost: this.net.packetsLost,
      tracks: this.tracks.length,
      shots: this.arena.shots,
      hits: this.arena.hits,
      meanError: this.arena.meanError(),
      lastAssistPx: this.lastAssistPx,
      leadMs: this.cfg.control.leadMs,
    };
  }
}

function emptyMetrics(): Metrics {
  return {
    fps: 0,
    captureFps: 0,
    oneWayMs: 0,
    rttMs: 0,
    packetLoss: 0,
    frameLoss: 0,
    framesOk: 0,
    framesDrop: 0,
    packetsSent: 0,
    packetsLost: 0,
    tracks: 0,
    shots: 0,
    hits: 0,
    meanError: 0,
    lastAssistPx: 0,
    leadMs: 0,
  };
}
