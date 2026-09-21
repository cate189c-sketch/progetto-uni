"""
Le sorgenti video: il requisito 1 della consegna.

Quello che conta qui non e' che una classe "funzioni", ma che la conversione
fra coordinate di schermo e coordinate di frame sia esatta: il nodo B ragiona
su un frame ridimensionato, e una correzione di 8 px su un frame 640x360 che
viene da uno schermo 1280x720 vale 16 px di schermo. Sbagliare il fattore
significa suggerire sistematicamente la meta' di quello che serve.
"""
import numpy as np
import pytest

import cv2
from config import CaptureCfg
from sources import ScreenSource, VideoFileSource, _Base, open_source


class _ArenaFinta:
    def __init__(self, w, h):
        self.frame = np.zeros((h, w, 3), np.uint8)

    def render(self):
        return self.frame


def test_conversione_schermo_frame_tiene_conto_di_scala_e_origine():
    # Regione 1280x720 che parte da (100, 50), ridotta a 640x360: fattore 0.5.
    s = _Base(640, 360, origin=(100, 50), native=(1280, 720))
    assert s.to_frame(100, 50) == (0.0, 0.0)          # l'angolo della regione
    assert s.to_frame(1380, 770) == (640.0, 360.0)    # l'angolo opposto
    assert s.to_frame(740, 410) == (320.0, 180.0)     # il centro


def test_le_due_conversioni_sono_una_l_inversa_dell_altra():
    s = _Base(640, 360, origin=(100, 50), native=(1920, 1080))
    for punto in ((0.0, 0.0), (317.5, 92.25), (640.0, 360.0)):
        rientro = s.to_frame(*s.to_screen(*punto))
        assert rientro == pytest.approx(punto, abs=1e-9)


def test_una_correzione_in_pixel_di_frame_vale_di_piu_sullo_schermo():
    # E' l'errore che la conversione esiste per evitare: 8 px suggeriti sul
    # frame ridotto sono 16 px da percorrere davvero sullo schermo.
    s = _Base(640, 360, native=(1280, 720))
    x0, _ = s.to_screen(320, 180)
    x1, _ = s.to_screen(328, 180)
    assert x1 - x0 == pytest.approx(16.0)


def test_il_ridimensionamento_porta_il_frame_alla_misura_di_lavoro(tmp_path):
    clip = tmp_path / "prova.avi"
    scrittore = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"MJPG"), 10, (320, 240))
    if not scrittore.isOpened():  # pragma: no cover - dipende dai codec installati
        pytest.skip("nessun codec MJPG disponibile")
    for i in range(6):
        f = np.full((240, 320, 3), 20, np.uint8)
        cv2.circle(f, (40 + i * 40, 120), 20, (40, 40, 220), -1)
        scrittore.write(f)
    scrittore.release()

    cfg = CaptureCfg(width=640, height=360, source="video", video_path=str(clip), video_loop=False)
    with VideoFileSource(cfg) as src:
        frame = src.read()
        assert frame is not None
        assert frame.shape == (360, 640, 3)
        assert src.native == (320, 240)


def test_la_clip_si_riavvolge_solo_se_glielo_chiedi(tmp_path):
    clip = tmp_path / "corta.avi"
    scrittore = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 64))
    if not scrittore.isOpened():  # pragma: no cover
        pytest.skip("nessun codec MJPG disponibile")
    for _ in range(3):
        scrittore.write(np.zeros((64, 64, 3), np.uint8))
    scrittore.release()

    cfg = CaptureCfg(width=64, height=64, source="video", video_path=str(clip), video_loop=False)
    with VideoFileSource(cfg) as src:
        letti = 0
        while src.read() is not None and letti < 20:
            letti += 1
        assert letti == 3                      # finita, e si ferma

    cfg.video_loop = True
    with VideoFileSource(cfg) as src:
        # Con il loop attivo il quarto read riparte dall'inizio invece di
        # restituire None: serve per fare demo lunghe con una clip corta.
        assert all(src.read() is not None for _ in range(8))


def test_clip_inesistente_fallisce_subito_e_dice_quale():
    cfg = CaptureCfg(source="video", video_path="/non/esiste/mai.mp4")
    with pytest.raises(RuntimeError, match="non trovato"):
        VideoFileSource(cfg)


def test_il_poligono_passa_dalla_stessa_interfaccia():
    # Il banco di prova deve esercitare il codice vero, non una sua imitazione:
    # se la sorgente sintetica avesse un'interfaccia sua, il percorso testato
    # non sarebbe quello che gira con lo schermo.
    cfg = CaptureCfg(width=64, height=48, source="synthetic")
    src = open_source(cfg, _ArenaFinta(64, 48))
    assert src.read().shape == (48, 64, 3)
    assert src.to_frame(10, 10) == (10.0, 10.0)   # nessun ridimensionamento
    src.close()


def test_sorgente_sintetica_senza_arena_e_un_errore_di_programmazione():
    with pytest.raises(ValueError, match="arena"):
        open_source(CaptureCfg(source="synthetic"), None)


def test_lo_schermo_indisponibile_diventa_un_errore_chiaro():
    """
    Il contratto: qualunque cosa vada storta nell'aprire lo schermo - pacchetto
    `mss` assente, nessuna sessione grafica, $DISPLAY non impostato - esce come
    RuntimeError con dentro il rimedio, e non come un'eccezione interna di mss
    che il chiamante non conosce.

    Non e' un dettaglio estetico: `main()` intercetta RuntimeError e termina
    con un messaggio, mentre una XError risalirebbe come traceback. Ed e' lo
    scenario piu' probabile di tutti - il nodo B lanciato su una macchina senza
    schermo, o in SSH senza X forwarding.
    """
    try:
        sorgente = ScreenSource(CaptureCfg(source="screen"))
    except RuntimeError as e:
        # Il messaggio deve dire cosa fare, non solo cosa e' andato storto.
        assert "video" in str(e) or "mss" in str(e)
    else:  # pragma: no cover - solo su una macchina con schermo
        sorgente.close()
