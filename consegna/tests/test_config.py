"""La config e' il punto in cui un errore di battitura deve fermarsi."""
import json

import pytest

from conftest import CONSEGNA
from config import ConfigError, config_from_dict, load_config


def test_default_completa():
    cfg = config_from_dict({})
    assert cfg.capture.width == 640
    assert len(cfg.detection.colors) == 3
    # MTU-safe di default: un chunk = un pacchetto IP
    assert cfg.network.chunk_bytes <= 1472


@pytest.mark.parametrize(
    "raw",
    [
        {"capture": {"fps": 0}},
        {"capture": {"source": "telepatia"}},
        {"capture": {"aim_mode": "boh"}},
        # aim_mode="center" e' il mirino fisso di uno sparatutto: sul poligono
        # non ha senso, li' il mirino e' il puntatore dell'utente.
        {"capture": {"source": "synthetic", "aim_mode": "center"}},
        {"capture": {"source": "video", "video_path": ""}},
        {"capture": {"region": [0, 0, 8, 720]}},
        {"capture": {"region": [0, 0, 640]}},
        {"detection": {"mode": "yolo"}},
        # La consegna chiede una previsione MODERATA: due secondi di orizzonte
        # non sono una previsione, sono un indovinello.
        {"assist": {"max_lead_ms": 2000}},
        {"network": {"chunk_bytes": 10}},
        {"network": {"port_frames": 5555, "port_cmds": 5555}},
        {"network": {"port_frames": 70000}},
        {"prediction": {"gate_px": -1}},
        {"assist": {"blend": 1.5}},
        {"detection": {"min_area": 500, "max_area": 100}},
    ],
)
def test_valori_fuori_range_respinti(raw):
    with pytest.raises(ConfigError):
        config_from_dict(raw)


def test_chiave_sbagliata_non_passa_in_silenzio():
    # `wdith` sarebbe stato ignorato, e il campo giusto restava al default:
    # un bug che si manifesta solo come "non cambia niente quando tocco la config".
    with pytest.raises(ConfigError, match="wdith"):
        config_from_dict({"capture": {"wdith": 320}})


def test_assist_non_puo_diventare_aimbot():
    with pytest.raises(ConfigError, match="max_correction_px"):
        config_from_dict({"assist": {"max_correction_px": 400}})


def test_colori_ambigui_respinti():
    with pytest.raises(ConfigError):
        config_from_dict(
            {"detection": {"colors": [
                {"name": "a", "bgr": [100, 100, 100], "tolerance": 60},
                {"name": "b", "bgr": [110, 110, 110], "tolerance": 60},
            ]}}
        )


def test_config_del_progetto_e_valida(tmp_path):
    from pathlib import Path
    cfg = load_config(Path(__file__).resolve().parents[1] / "config.json")
    assert cfg.assist.enabled


def test_commenti_nel_json_ammessi():
    # JSON non ha commenti: le chiavi "_..." sono la convenzione per spiegare
    # un file di configurazione dentro il file stesso.
    cfg = config_from_dict({"_commento": "banco di prova", "capture": {"fps": 30}})
    assert cfg.capture.fps == 30


def test_chiave_sbagliata_dentro_un_colore_non_passa():
    # `hue_toll` sarebbe finito nel nulla e la tolleranza sarebbe rimasta al
    # default: la detection "non cambia" e non si capisce perche'.
    with pytest.raises(ConfigError, match="hue_toll"):
        config_from_dict(
            {"detection": {"mode": "hsv", "colors": [{"name": "x", "bgr": [0, 0, 255], "hue_toll": 5}]}}
        )


def test_motion_non_pretende_una_palette():
    # In "motion" la classe e' una sola ed e' il movimento: chiedere dei colori
    # sarebbe un requisito inventato.
    cfg = config_from_dict({"detection": {"mode": "motion", "colors": []}})
    assert cfg.detection.mode == "motion"
    assert cfg.detection.colors == []


def test_config_della_consegna_valida():
    # config.gioco.json e' la configurazione che dimostra il requisito 1
    # (acquisizione video reale): deve restare valida insieme al codice.
    cfg = load_config(CONSEGNA / "config.gioco.json")
    assert cfg.capture.source == "screen"
    assert cfg.capture.aim_mode == "center"
    assert cfg.detection.mode == "hsv"
    # Previsione moderata, senza intercetta: le armi hitscan non hanno volo.
    assert cfg.assist.use_intercept is False
    assert cfg.assist.max_lead_ms <= 250
