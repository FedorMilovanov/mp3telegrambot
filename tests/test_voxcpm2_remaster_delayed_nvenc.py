from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "voxcpm2" / "remaster_delayed_nvenc.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "voxcpm2_remaster_delayed_nvenc",
        MODULE_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


remaster = _load_module()


def _segments():
    return [
        {"id": 1, "start": 0.0, "end": 10.88},
        {"id": 2, "start": 10.88, "end": 24.16},
        {"id": 3, "start": 24.72, "end": 32.6},
        {"id": 4, "start": 33.2, "end": 48.694},
    ]


def test_parse_delays_accepts_four_block_profile() -> None:
    assert remaster.parse_delays("220,160,100,40", 4) == [
        220,
        160,
        100,
        40,
    ]


def test_parse_delays_rejects_wrong_count() -> None:
    with pytest.raises(RuntimeError, match="expected 4 delays"):
        remaster.parse_delays("220,160", 4)


def test_build_delay_filter_uses_absolute_timeline_positions() -> None:
    filter_text = remaster.build_delay_filter(
        _segments(),
        [220, 160, 100, 40],
        48.694,
    )

    assert "adelay=220:all=1" in filter_text
    assert "adelay=11040:all=1" in filter_text
    assert "adelay=24820:all=1" in filter_text
    assert "adelay=33240:all=1" in filter_text
    assert "atrim=duration=48.694000" in filter_text


def test_parse_delays_rejects_excessive_shift() -> None:
    with pytest.raises(RuntimeError, match="0..1500"):
        remaster.parse_delays("220,160,100,1600", 4)
