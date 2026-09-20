"""
Configurazione tipizzata e validata.

Perché non un dict nudo: `cfg["prediction"]["gate_px"]` esplode con KeyError
al primo frame se manca una chiave, e il traceback arriva dentro il loop a
24 fps, lontano dal punto in cui il JSON è stato scritto. Qui il JSON viene
validato una volta sola all'avvio: chiave mancante -> default documentato,
valore fuori range -> errore esplicito con il nome del campo.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

log = logging.getLogger("kine.config")


class ConfigError(ValueError):
    """Config non valida. Il messaggio nomina sempre il campo colpevole."""


def _check(cond: bool, campo: str, atteso: str, valore: Any) -> None:
    if not cond:
        raise ConfigError(f"{campo}: atteso {atteso}, trovato {valore!r}")


@dataclass(slots=True)
class CaptureCfg:
    width: int = 640
    height: int = 360
    fps: int = 24
    source: str = "synthetic"  # "synthetic" | "webcam"

    def validate(self) -> None:
        _check(64 <= self.width <= 1920, "capture.width", "64..1920", self.width)
        _check(64 <= self.height <= 1080, "capture.height", "64..1080", self.height)
        _check(1 <= self.fps <= 120, "capture.fps", "1..120", self.fps)
        _check(
            self.source in ("synthetic", "webcam"),
            "capture.source",
            "'synthetic' o 'webcam'",
            self.source,
        )


@dataclass(slots=True)
class NetworkCfg:
    bind_ip: str = "0.0.0.0"
    peer_ip: str = "127.0.0.1"
    port_frames: int = 5555
    port_cmds: int = 5565
    # 1400 e non 12000: sotto l'MTU Ethernet (1500) il datagramma viaggia in un
    # solo pacchetto IP. A 12000 byte l'IP frammenta in ~9 pezzi e basta perderne
    # uno perche' il kernel scarti l'intero datagramma -> la perdita effettiva
    # per chunk diventa ~9x quella di rete.
    chunk_bytes: int = 1400
    timeout_ms: int = 180
    max_pending_frames: int = 8
    jpeg_quality: int = 70

    def validate(self) -> None:
        _check(512 <= self.chunk_bytes <= 60000, "network.chunk_bytes", "512..60000", self.chunk_bytes)
        # 0 = porta effimera scelta dal sistema: e' un valore valido, lo usano
        # il nodo A per i frame in uscita e i test.
        _check(0 <= self.port_frames <= 65535, "network.port_frames", "0..65535", self.port_frames)
        _check(0 <= self.port_cmds <= 65535, "network.port_cmds", "0..65535", self.port_cmds)
        _check(
            self.port_frames != self.port_cmds or self.port_frames == 0,
            "network.port_cmds",
            "porta diversa da port_frames",
            self.port_cmds,
        )
        _check(10 <= self.timeout_ms <= 5000, "network.timeout_ms", "10..5000", self.timeout_ms)
        _check(1 <= self.max_pending_frames <= 64, "network.max_pending_frames", "1..64", self.max_pending_frames)
        _check(1 <= self.jpeg_quality <= 100, "network.jpeg_quality", "1..100", self.jpeg_quality)


@dataclass(slots=True)
class ColorCfg:
    name: str
    bgr: tuple[int, int, int]
    tolerance: int = 72

    def validate(self) -> None:
        _check(bool(self.name), "detection.colors[].name", "stringa non vuota", self.name)
        _check(len(self.bgr) == 3, f"detection.colors[{self.name}].bgr", "3 canali", self.bgr)
        for canale, v in zip("bgr", self.bgr):
            _check(0 <= v <= 255, f"detection.colors[{self.name}].bgr.{canale}", "0..255", v)
        _check(1 <= self.tolerance <= 128, f"detection.colors[{self.name}].tolerance", "1..128", self.tolerance)


@dataclass(slots=True)
class DetectionCfg:
    colors: list[ColorCfg] = field(default_factory=list)
    min_area: int = 180
    max_area: int = 20000
    # Apertura morfologica: toglie il sale-e-pepe della webcam prima di cercare
    # i contorni. 0 = disattivata (sul poligono sintetico non serve).
    open_kernel: int = 0
    # Area del bersaglio "pieno" usata per normalizzare la confidenza.
    # Prima era max_area * 0.25, cioe' un numero senza significato fisico.
    nominal_area: int = 1500

    def validate(self) -> None:
        _check(bool(self.colors), "detection.colors", "almeno un colore", self.colors)
        _check(self.min_area >= 1, "detection.min_area", ">= 1", self.min_area)
        _check(
            self.max_area > self.min_area,
            "detection.max_area",
            "> min_area",
            self.max_area,
        )
        _check(0 <= self.open_kernel <= 15, "detection.open_kernel", "0..15", self.open_kernel)
        _check(self.nominal_area >= 1, "detection.nominal_area", ">= 1", self.nominal_area)
        nomi = [c.name for c in self.colors]
        _check(
            len(set(nomi)) == len(nomi),
            "detection.colors[].name",
            "nomi distinti (il tracker associa per classe)",
            nomi,
        )
        for c in self.colors:
            c.validate()
        self._check_overlap()

    def _check_overlap(self) -> None:
        """
        Due classi con box cromatici che si intersecano possono produrre due
        detection per lo stesso blob, e il tracker le assegna a due track
        diverse: id che sfarfallano e assist che salta da un bersaglio
        all'altro.

        Errore solo nel caso indifendibile (il centro di una classe cade dentro
        il box di un'altra: i pixel *pieni* del bersaglio sono ambigui).
        Avviso quando si toccano solo i bordi: sul poligono sintetico i colori
        sono piatti e non succede mai, su webcam succede spesso.
        """
        for i, a in enumerate(self.colors):
            for b in self.colors[i + 1 :]:
                if all(abs(ca - cb) <= b.tolerance for ca, cb in zip(a.bgr, b.bgr)) or all(
                    abs(ca - cb) <= a.tolerance for ca, cb in zip(a.bgr, b.bgr)
                ):
                    raise ConfigError(
                        f"detection.colors: il colore di '{a.name}' cade dentro il box di "
                        f"'{b.name}' (o viceversa): ogni pixel pieno verrebbe rilevato due "
                        f"volte. Abbassa tolerance o separa i colori."
                    )
                somma = a.tolerance + b.tolerance
                if all(abs(ca - cb) <= somma for ca, cb in zip(a.bgr, b.bgr)):
                    log.warning(
                        "detection.colors: i box di '%s' e '%s' si intersecano ai bordi; "
                        "su sorgente webcam i pixel di transizione possono essere rilevati "
                        "come entrambe le classi.",
                        a.name,
                        b.name,
                    )


@dataclass(slots=True)
class PredictionCfg:
    use_kalman: bool = True
    process_noise: float = 28.0
    measure_noise: float = 6.0
    max_missed: int = 18
    gate_px: float = 72.0
    max_velocity: float = 520.0
    min_hits: int = 3

    def validate(self) -> None:
        _check(self.process_noise > 0, "prediction.process_noise", "> 0", self.process_noise)
        _check(self.measure_noise > 0, "prediction.measure_noise", "> 0", self.measure_noise)
        _check(0 <= self.max_missed <= 300, "prediction.max_missed", "0..300", self.max_missed)
        _check(self.gate_px > 0, "prediction.gate_px", "> 0", self.gate_px)
        _check(self.max_velocity > 0, "prediction.max_velocity", "> 0", self.max_velocity)
        _check(self.min_hits >= 1, "prediction.min_hits", ">= 1", self.min_hits)


@dataclass(slots=True)
class AssistCfg:
    enabled: bool = True
    max_correction_px: float = 8.0
    blend: float = 0.4
    lead_ms: float = 50.0
    max_lead_ms: float = 120.0
    gate_px: float = 78.0
    min_hits: int = 3
    max_missed: int = 4
    marker_speed: float = 380.0
    cooldown_ms: float = 220.0
    # True: il punto suggerito e' la soluzione dell'intercetta (tiene conto del
    # tempo di volo del marcatore). False: semplice estrapolazione a lead_ms.
    use_intercept: bool = True

    def validate(self) -> None:
        _check(self.max_correction_px >= 0, "assist.max_correction_px", ">= 0", self.max_correction_px)
        _check(0.0 <= self.blend <= 1.0, "assist.blend", "0.0..1.0", self.blend)
        _check(self.lead_ms >= 0, "assist.lead_ms", ">= 0", self.lead_ms)
        _check(self.max_lead_ms > 0, "assist.max_lead_ms", "> 0", self.max_lead_ms)
        _check(self.gate_px >= 0, "assist.gate_px", ">= 0", self.gate_px)
        _check(self.min_hits >= 1, "assist.min_hits", ">= 1", self.min_hits)
        _check(self.max_missed >= 0, "assist.max_missed", ">= 0", self.max_missed)
        _check(self.marker_speed > 0, "assist.marker_speed", "> 0", self.marker_speed)
        _check(self.cooldown_ms >= 0, "assist.cooldown_ms", ">= 0", self.cooldown_ms)
        # Limite etico, non estetico: la consegna dice "assistita", non "automatica".
        # 8 px su 640 sono ~1.3% della larghezza: sposta il colpo, non la mira.
        _check(
            self.max_correction_px <= 32,
            "assist.max_correction_px",
            "<= 32 (oltre non e' piu' assistenza)",
            self.max_correction_px,
        )


@dataclass(slots=True)
class Config:
    capture: CaptureCfg = field(default_factory=CaptureCfg)
    network: NetworkCfg = field(default_factory=NetworkCfg)
    detection: DetectionCfg = field(default_factory=DetectionCfg)
    prediction: PredictionCfg = field(default_factory=PredictionCfg)
    assist: AssistCfg = field(default_factory=AssistCfg)

    def validate(self) -> Config:
        for f in fields(self):
            getattr(self, f.name).validate()
        return self


def _build(cls: type, raw: Any, path: str):
    """Costruisce una dataclass da un dict, ignorando chiavi sconosciute."""
    if raw is None:
        return cls()
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: atteso un oggetto JSON, trovato {type(raw).__name__}")
    noti = {f.name for f in fields(cls)}
    ignote = set(raw) - noti
    if ignote:
        raise ConfigError(f"{path}: chiavi sconosciute {sorted(ignote)} (attese: {sorted(noti)})")
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in raw:
            continue
        kwargs[f.name] = raw[f.name]
    return cls(**kwargs)


def load_config(path: str | Path) -> Config:
    """Legge il JSON, applica i default, valida. Solleva ConfigError se non torna."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ConfigError(f"config non trovata: {p}") from e
    except json.JSONDecodeError as e:
        raise ConfigError(f"config non e' JSON valido ({p}): {e}") from e
    return config_from_dict(raw)


def config_from_dict(raw: Any) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError(f"config: atteso un oggetto JSON, trovato {type(raw).__name__}")
    ignote = set(raw) - {f.name for f in fields(Config)}
    if ignote:
        raise ConfigError(f"config: sezioni sconosciute {sorted(ignote)}")

    colori_raw = (raw.get("detection") or {}).get("colors")
    detection_raw = dict(raw.get("detection") or {})
    detection_raw.pop("colors", None)
    detection = _build(DetectionCfg, detection_raw, "detection")
    if colori_raw is None:
        detection.colors = list(DEFAULT_COLORS)
    else:
        if not isinstance(colori_raw, list):
            raise ConfigError("detection.colors: atteso un array")
        detection.colors = [
            ColorCfg(
                name=str(c.get("name", f"colore{i}")),
                bgr=tuple(int(v) for v in c["bgr"]),  # type: ignore[arg-type]
                tolerance=int(c.get("tolerance", 72)),
            )
            for i, c in enumerate(colori_raw)
        ]

    cfg = Config(
        capture=_build(CaptureCfg, raw.get("capture"), "capture"),
        network=_build(NetworkCfg, raw.get("network"), "network"),
        detection=detection,
        prediction=_build(PredictionCfg, raw.get("prediction"), "prediction"),
        assist=_build(AssistCfg, raw.get("assist"), "assist"),
    )
    return cfg.validate()


DEFAULT_COLORS: tuple[ColorCfg, ...] = (
    ColorCfg("rosso", (58, 72, 196), 72),
    ColorCfg("verde", (96, 148, 74), 72),
    ColorCfg("azzurro", (176, 128, 70), 72),
)
