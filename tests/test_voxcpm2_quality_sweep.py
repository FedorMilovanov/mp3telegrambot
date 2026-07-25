from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "voxcpm2" / "quality_sweep.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("voxcpm2_quality_sweep", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


quality = _load_module()


def test_parse_csv_values() -> None:
    assert quality.parse_csv_floats("1.55, 1.75,1.95") == [1.55, 1.75, 1.95]
    assert quality.parse_csv_ints("10,16") == [10, 16]


def test_parse_csv_ints_rejects_zero() -> None:
    with pytest.raises(Exception):
        quality.parse_csv_ints("10,0")


def _tone(sample_rate: int, seconds: float, amplitude: float = 0.2) -> np.ndarray:
    time = np.arange(int(sample_rate * seconds), dtype=np.float32) / sample_rate
    return amplitude * np.sin(2.0 * np.pi * 180.0 * time)


def test_analyze_wave_detects_pause_restart() -> None:
    sample_rate = 16000
    samples = np.concatenate(
        [
            _tone(sample_rate, 2.8),
            np.zeros(int(sample_rate * 0.35), dtype=np.float32),
            _tone(sample_rate, 0.45, amplitude=0.08),
            np.zeros(int(sample_rate * 0.10), dtype=np.float32),
        ]
    )
    metrics = quality.analyze_wave(samples, sample_rate)
    assert metrics["pause_restart"]["suspicious"] is True
    assert metrics["artifact_score"] >= 20.0


def test_analyze_wave_accepts_clean_ending() -> None:
    sample_rate = 16000
    samples = np.concatenate(
        [
            _tone(sample_rate, 3.2),
            np.zeros(int(sample_rate * 0.25), dtype=np.float32),
        ]
    )
    metrics = quality.analyze_wave(samples, sample_rate)
    assert metrics["pause_restart"]["suspicious"] is False
    assert metrics["clipping_ratio"] == 0.0


def test_edge_silence_is_measured() -> None:
    sample_rate = 16000
    samples = np.concatenate(
        [
            np.zeros(int(sample_rate * 0.40), dtype=np.float32),
            _tone(sample_rate, 1.0),
            np.zeros(int(sample_rate * 0.30), dtype=np.float32),
        ]
    )
    metrics = quality.analyze_wave(samples, sample_rate)
    assert metrics["leading_silence"] == pytest.approx(0.39, abs=0.03)
    assert metrics["trailing_silence"] == pytest.approx(0.29, abs=0.03)
