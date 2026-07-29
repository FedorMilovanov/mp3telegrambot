from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from tools.voxcpm2 import continuous_reference_policy


ROOT = Path(__file__).resolve().parents[1]
ROUTES = (
    "generic_clean_gemini_runtime.py",
    "generic_clean_direct_runtime.py",
    "generic_clean_custom_runtime.py",
    "generic_clean_audio_repair_runtime.py",
)


def test_continuous_window_is_preferred_over_montage(monkeypatch, tmp_path: Path) -> None:
    sample_rate = 16_000
    seconds = 10.0
    timeline = np.arange(int(sample_rate * seconds), dtype=np.float32) / sample_rate
    audio = (0.14 * np.sin(2.0 * np.pi * 108.0 * timeline)).astype(np.float32)

    monkeypatch.setattr(
        continuous_reference_policy,
        "_decode_source",
        lambda _source, _output: (audio, sample_rate),
    )
    output = tmp_path / "extended_reference.wav"
    report = continuous_reference_policy.build_reference(
        tmp_path / "source.mp4",
        [(0.0, 10.0)],
        output,
        target_seconds=9.0,
        profile="extended",
    )

    info = sf.info(str(output))
    assert report["reference_policy"] == continuous_reference_policy.POLICY
    assert report["reference_mode"] == "single-continuous-window"
    assert 8.95 <= info.duration <= 9.05
    assert len(report["selected"]) == 1
    saved = json.loads(output.with_suffix(".selection.json").read_text(encoding="utf-8"))
    assert saved["reference_mode"] == "single-continuous-window"
    assert saved["denoise"] is False
    assert saved["spectral_filter"] is False


def test_short_runs_keep_explicit_multi_window_fallback(monkeypatch, tmp_path: Path) -> None:
    sample_rate = 16_000
    audio = np.zeros(sample_rate * 8, dtype=np.float32)
    monkeypatch.setattr(
        continuous_reference_policy,
        "_decode_source",
        lambda _source, _output: (audio, sample_rate),
    )

    def fallback(_source, _intervals, output, *, target_seconds):
        sf.write(output, np.zeros(sample_rate * 6, dtype=np.float32), sample_rate)
        output.with_suffix(".selection.json").write_text(
            json.dumps({"selected": [{"start": 0.0, "end": 3.0}, {"start": 4.0, "end": 7.0}]}),
            encoding="utf-8",
        )

    monkeypatch.setattr(
        continuous_reference_policy.professional_audio_v45,
        "build_reference_v45",
        fallback,
    )
    output = tmp_path / "composite_reference.wav"
    report = continuous_reference_policy.build_reference(
        tmp_path / "source.mp4",
        [(0.0, 2.0), (3.0, 5.0)],
        output,
        target_seconds=8.0,
        profile="composite_calm",
    )
    assert report["reference_mode"] == "multi-window-fallback"
    assert report["reference_policy"] == continuous_reference_policy.POLICY


def test_all_production_routes_use_continuous_first_builder() -> None:
    for name in ROUTES:
        source = (ROOT / "tools" / "voxcpm2" / name).read_text(encoding="utf-8")
        assert "continuous_reference_policy.build_calm_references" in source
        assert "clean.build_calm_references(" not in source
