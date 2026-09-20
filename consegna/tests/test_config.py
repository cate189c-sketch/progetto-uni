"""La config e' il punto in cui un errore di battitura deve fermarsi."""
import json

import pytest

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
        {"capture": {"source": "screen"}},
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
