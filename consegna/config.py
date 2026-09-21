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


SORGENTI = ("screen", "video", "webcam", "synthetic")
MIRE = ("center", "mouse")


@dataclass(slots=True)
class CaptureCfg:
    """
    Da dove arrivano i frame e dove sta il mirino.

    `source`:
      screen     lo schermo (o una sua regione): il gioco gira qui -> e' la
                 modalita' della consegna
      video      una clip registrata: stessa pipeline, risultato riproducibile
                 senza avere il gioco installato (serve per la valutazione)
      webcam     telecamera
      synthetic  poligono generato qui: banco di prova deterministico per i
                 test e per le misure del report, non il sistema

    `aim_mode` e' la scelta progettuale che cambia tutto in uno sparatutto:
      center  il mirino sta FERMO al centro dello schermo ed e' il mondo a
              ruotare sotto. E' il caso reale: non serve leggere il puntatore
              del sistema, non serve agganciarsi al processo del gioco. L'AI
              dice "il bersaglio e' 12 px alla tua destra" e l'utente ruota.
      mouse   il puntatore sopra la finestra di anteprima. Giochi a camera
              fissa, top-down, e il poligono.
    """

    width: int = 640
    height: int = 360
    fps: int = 24
    source: str = "synthetic"
    aim_mode: str = "mouse"
    # Regione di schermo [left, top, width, height]. Vuota = monitor intero.
    region: list[int] = field(default_factory=list)
    monitor: int = 1
    video_path: str = ""
    video_loop: bool = True
    device: int = 0

    def validate(self) -> None:
        _check(64 <= self.width <= 1920, "capture.width", "64..1920", self.width)
        _check(64 <= self.height <= 1080, "capture.height", "64..1080", self.height)
        _check(1 <= self.fps <= 120, "capture.fps", "1..120", self.fps)
        _check(self.source in SORGENTI, "capture.source", f"uno di {list(SORGENTI)}", self.source)
        _check(self.aim_mode in MIRE, "capture.aim_mode", f"uno di {list(MIRE)}", self.aim_mode)
        _check(self.monitor >= 0, "capture.monitor", ">= 0", self.monitor)
        _check(self.device >= 0, "capture.device", ">= 0", self.device)
        if self.region:
            _check(len(self.region) == 4, "capture.region", "[left, top, width, height]", self.region)
            _check(
                all(isinstance(v, int) for v in self.region),
                "capture.region",
                "quattro interi",
                self.region,
            )
            _check(self.region[2] >= 16, "capture.region[2]", ">= 16 (larghezza)", self.region[2])
            _check(self.region[3] >= 16, "capture.region[3]", ">= 16 (altezza)", self.region[3])
        if self.source == "video":
            _check(bool(self.video_path), "capture.video_path", "percorso non vuoto", self.video_path)
        # Il poligono si disegna su un canvas: non ha una regione di schermo ne'
        # un mirino fisso al centro (li' il mirino e' il mouse dell'utente).
        if self.source == "synthetic":
            _check(
                self.aim_mode == "mouse",
                "capture.aim_mode",
                "'mouse' con source='synthetic' (il poligono non ha un mirino fisso)",
                self.aim_mode,
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
    """
    Un bersaglio cromatico, descritto una volta sola in BGR e interpretato
    secondo `detection.mode`.

    In BGR il riferimento e' un cubo attorno al colore: va bene su un canvas a
    tinte piatte, non su video vero. Una maglia rossa in ombra ha gli stessi
    RAPPORTI fra i canali ma valori molto piu' bassi, quindi esce dal cubo e il
    bersaglio sparisce appena entra in una zona buia.

    In HSV i tre assi si separano e ognuno prende la tolleranza che merita:
    stretta sulla TINTA (`hue_tol`, il colore vero e proprio), larghissima su
    saturazione e luminosita' (`sat_min`, `val_min` sono soglie, non finestre).
    E' la stessa idea di prima, ma misurata lungo gli assi giusti: l'ombra
    abbassa V e lascia H dov'era.
    """

    name: str
    bgr: tuple[int, int, int]
    tolerance: int = 72          # modalita' "bgr": semilato del cubo
    hue_tol: int = 10            # modalita' "hsv": +/- gradi di tinta (scala OpenCV 0..179)
    sat_min: int = 70            # sotto = grigio: la tinta non e' piu' affidabile
    val_min: int = 50            # sotto = nero: idem

    def validate(self) -> None:
        _check(bool(self.name), "detection.colors[].name", "stringa non vuota", self.name)
        _check(len(self.bgr) == 3, f"detection.colors[{self.name}].bgr", "3 canali", self.bgr)
        for canale, v in zip("bgr", self.bgr):
            _check(0 <= v <= 255, f"detection.colors[{self.name}].bgr.{canale}", "0..255", v)
        _check(1 <= self.tolerance <= 128, f"detection.colors[{self.name}].tolerance", "1..128", self.tolerance)
        _check(1 <= self.hue_tol <= 90, f"detection.colors[{self.name}].hue_tol", "1..90", self.hue_tol)
        _check(0 <= self.sat_min <= 255, f"detection.colors[{self.name}].sat_min", "0..255", self.sat_min)
        _check(0 <= self.val_min <= 255, f"detection.colors[{self.name}].val_min", "0..255", self.val_min)


MODI_DETECTION = ("bgr", "hsv", "motion")


@dataclass(slots=True)
class DetectionCfg:
    # "bgr"    soglia nel cubo BGR: esatta su colori piatti, fragile su video
    # "hsv"    soglia su tinta + saturazione/luminosita' minime: e' il default
    #          quando i frame arrivano da uno schermo o da una clip
    # "motion" sottrazione dello sfondo: nessuna palette, trova cio' che si
    #          muove. Vale solo a inquadratura ferma (vedi detection.py).
    mode: str = "bgr"
    colors: list[ColorCfg] = field(default_factory=list)
    min_area: int = 180
    max_area: int = 20000
    # Parametri della modalita' "motion" (MOG2).
    motion_history: int = 240
    motion_threshold: float = 28.0
    # Apertura morfologica: toglie il sale-e-pepe della webcam prima di cercare
    # i contorni. 0 = disattivata (sul poligono sintetico non serve).
    open_kernel: int = 0
    # Area del bersaglio "pieno" usata per normalizzare la confidenza.
    # Prima era max_area * 0.25, cioe' un numero senza significato fisico.
    nominal_area: int = 1500

    def validate(self) -> None:
        _check(self.mode in MODI_DETECTION, "detection.mode", f"uno di {list(MODI_DETECTION)}", self.mode)
        # In "motion" non esiste una palette: la classe e' una sola ed e' il
        # movimento. Pretendere dei colori sarebbe un requisito inventato.
        if self.mode != "motion":
            _check(bool(self.colors), "detection.colors", "almeno un colore", self.colors)
        _check(self.motion_history >= 1, "detection.motion_history", ">= 1", self.motion_history)
        _check(self.motion_threshold > 0, "detection.motion_threshold", "> 0", self.motion_threshold)
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
        if self.mode == "bgr":
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
    # True: il punto suggerito risolve l'intercetta, cioe' tiene conto del tempo
    # di volo del proiettile. Serve solo quando il colpo VIAGGIA (il poligono,
    # le armi balistiche). Con un'arma hitscan - la maggioranza degli
    # sparatutto - il colpo arriva nel frame stesso e non c'e' niente da
    # anticipare: default False.
    #
    # Anche quando e' attivo l'orizzonte totale resta tagliato a max_lead_ms:
    # il tempo di volo e' una grandezza fisica, ma la consegna chiede una
    # previsione moderata e un tetto esplicito e' l'unico modo di garantirla.
    use_intercept: bool = False

    def validate(self) -> None:
        _check(self.max_correction_px >= 0, "assist.max_correction_px", ">= 0", self.max_correction_px)
        _check(0.0 <= self.blend <= 1.0, "assist.blend", "0.0..1.0", self.blend)
        _check(self.lead_ms >= 0, "assist.lead_ms", ">= 0", self.lead_ms)
        _check(self.max_lead_ms > 0, "assist.max_lead_ms", "> 0", self.max_lead_ms)
        # Tetto all'orizzonte di previsione. La consegna: "non un calcolo
        # esagerato che indovina dove sara' il nemico tra 2 secondi". Oltre un
        # secondo il modello a velocita' costante non descrive piu' niente -
        # e' un'estrapolazione, non una previsione - quindi e' un errore.
        _check(
            self.max_lead_ms <= 1000,
            "assist.max_lead_ms",
            "<= 1000 (oltre non e' piu' una previsione moderata)",
            self.max_lead_ms,
        )
        if self.max_lead_ms > 250:
            log.warning(
                "assist.max_lead_ms = %.0f ms: sopra i ~250 ms l'anticipo si vede a occhio "
                "e il colpo parte dove il bersaglio non e' ancora. Giustificalo nel report "
                "(p.es. proiettile lento) o scendi.",
                self.max_lead_ms,
            )
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
    # JSON non ha commenti. Le chiavi che iniziano con "_" sono trattate come
    # tali e ignorate: senza questa convenzione l'unico modo di spiegare un
    # parametro nel file sarebbe non spiegarlo.
    raw = {k: v for k, v in raw.items() if not k.startswith("_")}
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
        noti_colore = {f.name for f in fields(ColorCfg)}
        for i, c in enumerate(colori_raw):
            if not isinstance(c, dict):
                raise ConfigError(f"detection.colors[{i}]: atteso un oggetto JSON")
            ignote_c = set(c) - noti_colore
            if ignote_c:
                raise ConfigError(
                    f"detection.colors[{i}]: chiavi sconosciute {sorted(ignote_c)} "
                    f"(attese: {sorted(noti_colore)})"
                )
        detection.colors = [
            ColorCfg(
                name=str(c.get("name", f"colore{i}")),
                bgr=tuple(int(v) for v in c["bgr"]),  # type: ignore[arg-type]
                tolerance=int(c.get("tolerance", 72)),
                hue_tol=int(c.get("hue_tol", 10)),
                sat_min=int(c.get("sat_min", 70)),
                val_min=int(c.get("val_min", 50)),
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
