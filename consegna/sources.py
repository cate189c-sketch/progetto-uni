"""
Sorgenti video.

La consegna chiede "acquisizione ed elaborazione in tempo reale di video":
il nodo A deve leggere quello che succede SULLO SCHERMO mentre l'utente gioca.
Il poligono sintetico non soddisfa questo requisito - genera i bersagli invece
di osservarli - ma resta nel progetto per un motivo preciso: e' l'unico modo di
avere una verita' di riferimento (dove sta davvero ogni bersaglio, a che
velocita') e quindi di MISURARE l'errore dell'assist. Sorgente della consegna:
`screen`. Banco di prova: `synthetic`.

Tutte le sorgenti espongono la stessa interfaccia minima:

    read()  -> frame BGR alla risoluzione di lavoro, oppure None
    close()

e due conversioni che nella modalita' `screen` non sono un dettaglio:

    to_frame(sx, sy)   schermo  -> frame
    to_screen(fx, fy)  frame    -> schermo

Il frame viene ridimensionato a (width, height) prima di partire in rete - a
1920x1080 un JPEG pesa troppo per reggere 24 fps su UDP - quindi le coordinate
del frame NON sono pixel di schermo. Confondere i due sistemi significa
suggerire una correzione di 12 px al nodo A che sullo schermo ne vale 36: la
conversione sta qui, una volta sola, invece che sparsa nei chiamanti.

Nota sull'accesso allo schermo: si leggono i pixel del proprio desktop, gli
stessi che l'utente sta guardando. Non si legge la memoria di altri processi,
non si inietta nulla nel sistema operativo, non si muove il puntatore. Vedi
input_control.py.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

from config import CaptureCfg

log = logging.getLogger("kine.sources")


class FrameSource(Protocol):
    w: int
    h: int

    def read(self) -> np.ndarray | None: ...
    def close(self) -> None: ...
    def to_frame(self, sx: float, sy: float) -> tuple[float, float]: ...
    def to_screen(self, fx: float, fy: float) -> tuple[float, float]: ...


class _Base:
    """Ridimensionamento e conversione di coordinate, condivisi."""

    def __init__(self, w: int, h: int, origin: tuple[int, int] = (0, 0), native: tuple[int, int] | None = None):
        self.w, self.h = w, h
        self.origin = origin                      # angolo alto-sinistra sullo schermo
        self.native = native or (w, h)            # risoluzione prima del resize

    @property
    def scale(self) -> tuple[float, float]:
        return self.w / self.native[0], self.h / self.native[1]

    def to_frame(self, sx: float, sy: float) -> tuple[float, float]:
        kx, ky = self.scale
        return (sx - self.origin[0]) * kx, (sy - self.origin[1]) * ky

    def to_screen(self, fx: float, fy: float) -> tuple[float, float]:
        kx, ky = self.scale
        return fx / kx + self.origin[0], fy / ky + self.origin[1]

    def _fit(self, frame: np.ndarray) -> np.ndarray:
        if frame.shape[1] != self.w or frame.shape[0] != self.h:
            # INTER_AREA e' il filtro giusto per RIDURRE: media sull'area del
            # pixel di destinazione invece di campionare. Con INTER_LINEAR un
            # bersaglio di pochi pixel puo' sparire fra un frame e l'altro a
            # seconda di dove cade la griglia di campionamento, e il tracker
            # vede un lampeggio che non esiste.
            interp = cv2.INTER_AREA if frame.shape[1] > self.w else cv2.INTER_LINEAR
            frame = cv2.resize(frame, (self.w, self.h), interpolation=interp)
        return frame

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class ScreenSource(_Base):
    """
    Cattura dello schermo (o di una sua regione) via `mss`.

    E' la sorgente della consegna: il gioco gira sul nodo A, questa classe ne
    legge i frame e il nodo B li analizza dall'altra macchina.

    Perche' `mss` e non `cv2.VideoCapture`: OpenCV non sa catturare il desktop,
    e le alternative (ffmpeg con gdigrab/x11grab in sottoprocesso) aggiungono
    una dipendenza esterna e una pipe da gestire. mss e' ~100 KB, fa una sola
    cosa, e restituisce direttamente un buffer su cui numpy si affaccia senza
    copiare.
    """

    def __init__(self, cfg: CaptureCfg):
        try:
            import mss  # import locale: la dipendenza serve solo a questa sorgente
        except ImportError as e:  # pragma: no cover - dipende dall'ambiente
            raise RuntimeError(
                "capture.source='screen' richiede il pacchetto 'mss' "
                "(pip install mss). In alternativa usa source='video' con una "
                "clip registrata, oppure source='synthetic'."
            ) from e

        # mss solleva eccezioni proprie (XError su Linux senza display, errori
        # di piattaforma altrove) che il chiamante non conosce e non puo'
        # intercettare. Qui diventano un RuntimeError con dentro il rimedio:
        # succede su un server senza schermo, in SSH senza X forwarding, o in
        # un container - cioe' proprio dove si prova a far girare il nodo B.
        # mss 10 ha rinominato la factory in MSS e deprecato mss(); le versioni
        # precedenti hanno solo mss(). getattr copre entrambe senza pretendere
        # una versione minima che l'utente potrebbe non avere.
        apri = getattr(mss, "MSS", None) or mss.mss
        try:
            self._sct = apri()
            monitors = self._sct.monitors
        except Exception as e:
            raise RuntimeError(
                f"cattura dello schermo non disponibile ({e}). Serve una sessione "
                "grafica attiva: su Linux controlla $DISPLAY, in SSH usa -X. "
                "Per una prova senza schermo usa capture.source='video' con una "
                "clip registrata, oppure 'synthetic'."
            ) from e
        if cfg.region:
            left, top, rw, rh = cfg.region
            box = {"left": left, "top": top, "width": rw, "height": rh}
        else:
            if cfg.monitor >= len(monitors):
                raise RuntimeError(
                    f"capture.monitor={cfg.monitor} ma il sistema ne espone "
                    f"{len(monitors) - 1} (indice 0 = tutti insieme)."
                )
            m = monitors[cfg.monitor]
            box = {"left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]}

        self.box = box
        super().__init__(cfg.width, cfg.height, origin=(box["left"], box["top"]),
                         native=(box["width"], box["height"]))
        log.info(
            "schermo: regione %dx%d in (%d,%d) -> frame %dx%d (fattore %.2fx)",
            box["width"], box["height"], box["left"], box["top"], self.w, self.h, self.scale[0],
        )

    def read(self) -> np.ndarray | None:
        raw = self._sct.grab(self.box)
        # mss restituisce BGRA su un buffer RIUSATO a ogni grab: la fetta [:, :, :3]
        # e' una vista, non una copia. Se il resize non scatta (regione gia'
        # della misura giusta) consegnare la vista significa che il frame
        # cambia sotto i piedi del chiamante mentre lo sta codificando.
        frame = np.asarray(raw, dtype=np.uint8)[:, :, :3]
        fitted = self._fit(frame)
        return fitted if fitted is not frame else frame.copy()

    def close(self) -> None:
        self._sct.close()


class VideoFileSource(_Base):
    """
    Una clip registrata.

    Non e' un ripiego: e' la sorgente che rende la valutazione riproducibile.
    Lo schermo di chi corregge non contiene il gioco di chi ha scritto il
    codice, mentre lo stesso file .mp4 da' lo stesso risultato su qualunque
    macchina. La pipeline a valle (detection, tracking, assist, rete) e'
    identica a quella di `screen`: cambia solo da dove vengono i pixel.
    """

    def __init__(self, cfg: CaptureCfg):
        path = Path(cfg.video_path).expanduser()
        if not path.is_file():
            raise RuntimeError(f"capture.video_path: file non trovato ({path})")
        self.cap = cv2.VideoCapture(str(path))
        if not self.cap.isOpened():
            self.cap.release()
            raise RuntimeError(f"clip non apribile: {path} (codec non supportato?)")
        nw = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or cfg.width
        nh = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or cfg.height
        super().__init__(cfg.width, cfg.height, native=(nw, nh))
        self.loop = cfg.video_loop
        self.path = path

    def read(self) -> np.ndarray | None:
        ok, frame = self.cap.read()
        if not ok or frame is None:
            if not self.loop:
                return None
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self.cap.read()
            if not ok or frame is None:
                return None
            log.debug("clip riavvolta")
        return self._fit(frame)

    def close(self) -> None:
        self.cap.release()


class WebcamSource(_Base):
    """Telecamera. Apertura verificata e rilascio garantito."""

    def __init__(self, cfg: CaptureCfg):
        self.cap = cv2.VideoCapture(cfg.device)
        if not self.cap.isOpened():
            self.cap.release()
            raise RuntimeError(
                f"webcam {cfg.device} non disponibile. Usa capture.source='synthetic' "
                "oppure 'video'."
            )
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
        nw = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or cfg.width
        nh = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or cfg.height
        super().__init__(cfg.width, cfg.height, native=(nw, nh))

    def read(self) -> np.ndarray | None:
        ok, frame = self.cap.read()
        if not ok or frame is None:
            return None
        return self._fit(frame)

    def close(self) -> None:
        self.cap.release()


class SyntheticSource(_Base):
    """
    Il poligono, dietro la stessa interfaccia delle altre sorgenti.

    Cosi' il nodo A non sa da dove arrivano i pixel, e il percorso
    frame -> rete -> detection -> tracking -> assist e' lo stesso identico
    codice che gira con lo schermo: il banco di prova esercita la pipeline
    vera, non una sua imitazione.
    """

    def __init__(self, cfg: CaptureCfg, arena: Any):
        super().__init__(cfg.width, cfg.height)
        self.arena = arena

    def read(self) -> np.ndarray | None:
        return self.arena.render()

    def close(self) -> None:
        pass


def open_source(cfg: CaptureCfg, arena: Any = None) -> FrameSource:
    """Apre la sorgente indicata in configurazione."""
    if cfg.source == "screen":
        return ScreenSource(cfg)
    if cfg.source == "video":
        return VideoFileSource(cfg)
    if cfg.source == "webcam":
        return WebcamSource(cfg)
    if cfg.source == "synthetic":
        if arena is None:
            raise ValueError("source='synthetic' richiede l'arena")
        return SyntheticSource(cfg, arena)
    raise ValueError(f"sorgente sconosciuta: {cfg.source!r}")  # pragma: no cover - validata in config
