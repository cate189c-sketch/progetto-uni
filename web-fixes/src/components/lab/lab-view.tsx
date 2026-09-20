import { useEffect, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { Activity, Aperture, Camera, Pause, Play, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DEFAULT_CONFIG, loadConfig, saveConfig, SCENARIO_COPY } from "@/lib/kine/config";
import { KineEngine } from "@/lib/kine/engine";
import type { KineConfig, Metrics, Scenario, TrackSnapshot } from "@/lib/kine/types";
import { cn } from "@/lib/utils";

export function LabView() {
  const worldRef = useRef<HTMLCanvasElement>(null);
  const pipRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<KineEngine | null>(null);
  const [cfg, setCfg] = useState<KineConfig>(DEFAULT_CONFIG);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [tracks, setTracks] = useState<TrackSnapshot[]>([]);
  const [assistPx, setAssistPx] = useState(0);
  const [running, setRunning] = useState(true);
  const [source, setSource] = useState<"synthetic" | "webcam">("synthetic");
  const [camError, setCamError] = useState<string | null>(null);
  const [tab, setTab] = useState<"scene" | "tracks" | "config">("scene");

  useEffect(() => {
    const loaded = loadConfig();
    setCfg(loaded);
    const world = worldRef.current;
    const pip = pipRef.current;
    if (!world || !pip) return;
    const engine = new KineEngine(loaded);
    engine.attach({ world, pip });
    engine.onUi = () => {
      setMetrics({ ...engine.metrics });
      setTracks([...engine.tracks]);
      setAssistPx(engine.hint.mag);
    };
    engine.start();
    engineRef.current = engine;
    return () => engine.stop();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== "Space" || e.repeat) return;
      // Senza questo controllo `preventDefault()` intercettava la barra
      // spaziatrice su tutta la pagina: non si poteva scrivere uno spazio in
      // un campo di testo, e premendola su un pulsante a fuoco si sparava e
      // si attivava il pulsante insieme.
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT", "BUTTON", "A"].includes(target.tagName))
      ) {
        return;
      }
      e.preventDefault();
      engineRef.current?.fireUser(!e.shiftKey);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function patch(next: KineConfig) {
    setCfg(next);
    saveConfig(next);
    engineRef.current?.setConfig(next);
  }

  function resetAll() {
    const fresh = structuredClone(DEFAULT_CONFIG);
    patch(fresh);
    engineRef.current?.arena.reset(fresh);
    engineRef.current?.tracker.reset();
  }

  async function toggleCam() {
    const engine = engineRef.current;
    if (!engine) return;
    setCamError(null);
    if (source === "webcam") {
      engine.stopWebcam();
      setSource("synthetic");
      return;
    }
    try {
      await engine.startWebcam();
      setSource("webcam");
    } catch {
      setCamError("Webcam non disponibile. Resta sul poligono sintetico.");
    }
  }

  function pointer(e: React.PointerEvent<HTMLCanvasElement>) {
    const canvas = worldRef.current;
    const engine = engineRef.current;
    if (!canvas || !engine) return;
    engine.setAimFromPointer(e.clientX, e.clientY, canvas);
  }

  const hitRate = metrics && metrics.shots ? metrics.hits / metrics.shots : 0;

  return (
    <div className="min-h-dvh bg-bg text-fg">
      <header className="border-b border-line">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between gap-4 px-4 py-4 sm:px-6">
          <div className="min-w-0">
            <p className="font-mono text-[11px] tracking-[0.18em] text-muted uppercase">
              Mira assistita · computer vision
            </p>
            <h1 className="font-display text-3xl leading-none tracking-tight text-fg sm:text-4xl">
              KINE
            </h1>
          </div>
          <nav className="flex items-center gap-1">
            <span className="hidden rounded-md border border-line px-3 py-2 font-mono text-[11px] text-muted sm:inline">
              Poligono
            </span>
            <Link
              to="/codice"
              className="inline-flex h-11 items-center rounded-md px-3 text-sm text-muted transition-colors hover:text-fg"
            >
              Codice
            </Link>
            <Link
              to="/report"
              className="inline-flex h-11 items-center rounded-md px-3 text-sm text-muted transition-colors hover:text-fg"
            >
              Report
            </Link>
          </nav>
        </div>
      </header>

      <div className="mx-auto max-w-[1400px] px-4 py-5 sm:px-6">
        <p className="max-w-2xl text-sm leading-relaxed text-muted">
          Tu miri, tu spari. L'AI non muove il puntatore: allo sparo
          suggerisce &ldquo;guarda lì&rdquo; e, se il bersaglio è nel gate,
          spinge il colpo di pochi pixel. Shift+click = colpo puro, senza assist.
        </p>

        <Pipeline />

        <dl className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
          <Stat label="RTT" value={fmtMs(metrics?.rttMs)} />
          <Stat label="One-way" value={fmtMs(metrics?.oneWayMs)} />
          <Stat label="Anticipo" value={fmtMs(metrics?.leadMs)} />
          <Stat label="FPS" value={metrics ? metrics.fps.toFixed(0) : "—"} />
          <Stat label="Perdita pkt" value={fmtPct(metrics?.packetLoss)} />
          <Stat label="Assist" value={`${assistPx.toFixed(1)} px`} />
          <Stat label="Tracce" value={metrics ? String(metrics.tracks) : "—"} />
          <Stat
            label="Colpi"
            value={metrics ? `${metrics.hits}/${metrics.shots}` : "—"}
            hint={metrics && metrics.shots ? `${Math.round(hitRate * 100)}%` : undefined}
          />
        </dl>

        <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(280px,0.8fr)]">
          <section className="rounded-xl border border-line bg-surface p-3">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-mono text-[11px] tracking-[0.14em] text-muted uppercase">
                Nodo A · tu punti
              </h2>
              <div className="flex flex-wrap gap-1">
                <Button
                  size="sm"
                  variant={running ? "outline" : "default"}
                  onClick={() => {
                    const e = engineRef.current;
                    if (!e) return;
                    if (running) {
                      e.pause();
                      setRunning(false);
                    } else {
                      if (!e.running) e.start();
                      else e.resume();
                      setRunning(true);
                    }
                  }}
                >
                  {running ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
                  {running ? "Pausa" : "Avvia"}
                </Button>
                <Button size="sm" variant="outline" onClick={resetAll}>
                  <RotateCcw className="size-3.5" />
                  Reset
                </Button>
                <Button size="sm" variant={source === "webcam" ? "default" : "outline"} onClick={toggleCam}>
                  <Camera className="size-3.5" />
                  Webcam
                </Button>
              </div>
            </div>
            {camError ? <p className="mb-2 text-xs text-danger">{camError}</p> : null}
            <div className="overflow-hidden rounded-md bg-ink ring-1 ring-line">
              <canvas
                ref={worldRef}
                className="block h-auto w-full touch-none"
                style={{ aspectRatio: "16 / 9" }}
                aria-label="Poligono: muovi il puntatore e clicca per sparare"
                onPointerMove={pointer}
                onPointerDown={(e) => {
                  pointer(e);
                  (e.currentTarget as HTMLCanvasElement).setPointerCapture(e.pointerId);
                  engineRef.current?.fireUser(!e.shiftKey);
                }}
              />
            </div>
            <p className="mt-2 text-xs text-muted">
              Mouse o dito sul canvas. Click / Spazio = sparo assistito. Shift = senza AI.
            </p>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <div className="overflow-hidden rounded-md bg-ink ring-1 ring-line">
                <canvas
                  ref={pipRef}
                  className="block h-auto w-full"
                  style={{ aspectRatio: "16 / 9" }}
                  aria-label="Frame ricostruito dal nodo B"
                />
              </div>
              <Legend hitRate={hitRate} meanError={metrics?.meanError ?? 0} lastAssist={metrics?.lastAssistPx ?? 0} />
            </div>
            <ScenarioBar
              value={cfg.arena.scenario}
              onChange={(scenario) => patch({ ...cfg, arena: { ...cfg.arena, scenario } })}
            />
            <p className="mt-2 text-xs leading-relaxed text-muted">{SCENARIO_COPY[cfg.arena.scenario]}</p>
          </section>

          <aside className="flex min-w-0 flex-col gap-3">
            <div className="flex rounded-lg bg-surface p-1 lg:hidden">
              {(["scene", "tracks", "config"] as const).map((id) => (
                <button
                  key={id}
                  className={cn(
                    "h-11 flex-1 rounded-md text-xs font-medium",
                    tab === id ? "bg-surface-2 text-fg" : "text-muted",
                  )}
                  onClick={() => setTab(id)}
                >
                  {id === "scene" ? "Scena" : id === "tracks" ? "Tracce" : "Config"}
                </button>
              ))}
            </div>
            <div className={cn(tab === "tracks" ? "block" : "hidden lg:block")}>
              <TrackList tracks={tracks} />
            </div>
            <div className={cn(tab === "config" ? "block" : "hidden lg:block")}>
              <ConfigPanel cfg={cfg} onChange={patch} />
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}

function Pipeline() {
  const nodes = ["Cattura", "UDP", "Blob", "Kalman", "Suggerisci", "Tu spari"];
  return (
    <ol className="mt-6 flex flex-wrap items-center gap-x-2 gap-y-2 font-mono text-[11px] tracking-wide text-muted uppercase">
      {nodes.map((n, i) => (
        <li key={n} className="flex items-center gap-2">
          <span className="rounded-md border border-line bg-surface px-2 py-1 text-fg">{n}</span>
          {i < nodes.length - 1 ? <span aria-hidden="true">→</span> : null}
        </li>
      ))}
    </ol>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2.5">
      <dt className="font-mono text-[10px] tracking-[0.12em] text-muted uppercase">{label}</dt>
      <dd className="mt-1 font-mono text-sm tabular-nums text-fg">
        {value}
        {hint ? <span className="ml-1 text-muted">{hint}</span> : null}
      </dd>
    </div>
  );
}

function Legend({
  hitRate,
  meanError,
  lastAssist,
}: {
  hitRate: number;
  meanError: number;
  lastAssist: number;
}) {
  return (
    <div className="flex flex-col justify-between rounded-md border border-line bg-surface-2 p-3">
      <p className="font-mono text-[10px] tracking-[0.12em] text-muted uppercase">Legenda</p>
      <ul className="mt-2 space-y-1.5 text-xs text-muted">
        <li className="flex items-center gap-2">
          <span className="size-2 rounded-full bg-fg" />
          Tua mira (crosshair)
        </li>
        <li className="flex items-center gap-2">
          <span className="size-2 rounded-full bg-steel" />
          Suggerimento &ldquo;guarda lì&rdquo;
        </li>
        <li className="flex items-center gap-2">
          <span className="size-2 rounded-full bg-detect" />
          Box detection
        </li>
        <li className="flex items-center gap-2">
          <span className="size-2 rounded-full bg-predict" />
          Anticipo breve
        </li>
      </ul>
      <p className="mt-3 font-mono text-[11px] tabular-nums text-fg">
        ultimo assist {lastAssist.toFixed(1)} px · a segno {(hitRate * 100).toFixed(0)}% ·
        mancato {meanError.toFixed(1)} px
      </p>
    </div>
  );
}

function ScenarioBar({ value, onChange }: { value: Scenario; onChange: (s: Scenario) => void }) {
  const items: Scenario[] = ["lineare", "rimbalzi", "accelerato", "occlusione"];
  return (
    <div className="mt-3 grid grid-cols-2 gap-1 sm:grid-cols-4">
      {items.map((s) => (
        <button
          key={s}
          onClick={() => onChange(s)}
          className={cn(
            "h-11 rounded-md border text-xs capitalize",
            value === s
              ? "border-steel bg-steel text-steel-fg"
              : "border-line bg-transparent text-muted hover:text-fg",
          )}
        >
          {s}
        </button>
      ))}
    </div>
  );
}

function TrackList({ tracks }: { tracks: TrackSnapshot[] }) {
  return (
    <section className="rounded-xl border border-line bg-surface p-3">
      <h2 className="flex items-center gap-2 font-mono text-[11px] tracking-[0.14em] text-muted uppercase">
        <Activity className="size-3.5" />
        Nodo B · tracce
      </h2>
      {tracks.length === 0 ? (
        <p className="mt-3 text-sm text-muted">Nessuna traccia. I blob compariranno al primo frame integro.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {tracks.map((t) => (
            <li key={t.id} className="rounded-md bg-surface-2 px-3 py-2">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm text-fg">
                  #{t.id} {t.name}
                </span>
                <span className="font-mono text-[11px] tabular-nums text-muted">
                  {t.speed.toFixed(0)} px/s
                </span>
              </div>
              <p className="mt-1 font-mono text-[11px] tabular-nums text-muted">
                ({t.x.toFixed(0)}, {t.y.toFixed(0)}) · conf {(t.confidence * 100).toFixed(0)}% · miss {t.missed}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ConfigPanel({ cfg, onChange }: { cfg: KineConfig; onChange: (c: KineConfig) => void }) {
  return (
    <section className="rounded-xl border border-line bg-surface p-3">
      <h2 className="flex items-center gap-2 font-mono text-[11px] tracking-[0.14em] text-muted uppercase">
        <Aperture className="size-3.5" />
        Configurazione
      </h2>
      <div className="mt-3 space-y-4">
        <Toggle
          label="Assist allo sparo"
          on={cfg.control.assistEnabled}
          onChange={(assistEnabled) => onChange({ ...cfg, control: { ...cfg.control, assistEnabled } })}
        />
        <Toggle
          label="Kalman"
          on={cfg.prediction.useKalman}
          onChange={(useKalman) => onChange({ ...cfg, prediction: { ...cfg.prediction, useKalman } })}
        />
        <Slider
          label="Correzione max"
          min={0}
          max={20}
          value={cfg.control.maxCorrectionPx}
          suffix=" px"
          onChange={(maxCorrectionPx) => onChange({ ...cfg, control: { ...cfg.control, maxCorrectionPx } })}
        />
        <Slider
          label="Anticipo (ms)"
          min={0}
          max={120}
          value={cfg.control.leadMs}
          onChange={(leadMs) => onChange({ ...cfg, control: { ...cfg.control, leadMs } })}
        />
        <Slider
          label="Gate bersaglio"
          min={30}
          max={140}
          value={cfg.control.assistGatePx}
          suffix=" px"
          onChange={(assistGatePx) => onChange({ ...cfg, control: { ...cfg.control, assistGatePx } })}
        />
        <Slider
          label="Latenza (ms)"
          min={0}
          max={180}
          value={cfg.network.latencyMs}
          onChange={(latencyMs) => onChange({ ...cfg, network: { ...cfg.network, latencyMs } })}
        />
        <Slider
          label="Perdita pacchetti"
          min={0}
          max={25}
          value={Math.round(cfg.network.packetLoss * 100)}
          suffix="%"
          onChange={(v) => onChange({ ...cfg, network: { ...cfg.network, packetLoss: v / 100 } })}
        />
        <Slider
          label="Tolleranza colore"
          min={30}
          max={120}
          value={cfg.detection.colors[0]?.tolerance ?? 72}
          onChange={(tolerance) =>
            onChange({
              ...cfg,
              detection: {
                ...cfg.detection,
                colors: cfg.detection.colors.map((c) => ({ ...c, tolerance })),
              },
            })
          }
        />
        <Slider
          label="Oggetti"
          min={1}
          max={5}
          value={cfg.arena.objectCount}
          onChange={(objectCount) => onChange({ ...cfg, arena: { ...cfg.arena, objectCount } })}
        />
      </div>
    </section>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  suffix,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  suffix?: string;
  onChange: (v: number) => void;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 flex items-center justify-between text-xs text-muted">
        {label}
        <span className="font-mono tabular-nums text-fg">
          {value}
          {suffix ?? ""}
        </span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-11 w-full"
      />
    </label>
  );
}

function Toggle({ label, on, onChange }: { label: string; on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!on)}
      className="flex h-11 w-full items-center justify-between rounded-md px-1 text-sm"
    >
      <span className="text-fg">{label}</span>
      <span
        role="switch"
        aria-checked={on}
        className={cn("relative h-6 w-10 rounded-full", on ? "bg-steel" : "bg-line")}
      >
        <span
          className={cn(
            "absolute top-0.5 size-5 rounded-full bg-bg transition-transform duration-150",
            on ? "translate-x-4" : "translate-x-0.5",
          )}
        />
      </span>
    </button>
  );
}

function fmtMs(v?: number) {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v.toFixed(0)} ms`;
}
function fmtPct(v?: number) {
  if (v == null) return "—";
  return `${(v * 100).toFixed(0)}%`;
}
