from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tools.voxcpm2 import final_media_qa


def _branches(*, seconds: int = 16, sample_rate: int = 8_000):
    rng = np.random.default_rng(2026072902)
    length = seconds * sample_rate
    source = rng.normal(0.0, 0.11, length).astype(np.float64)
    russian = rng.normal(0.0, 0.09, length).astype(np.float64)
    return source, russian


def test_original_bed_alignment_recovers_seven_millisecond_delay() -> None:
    sample_rate = 8_000
    source, russian = _branches(sample_rate=sample_rate)
    lag = int(round(sample_rate * 0.007))
    delayed_russian = np.concatenate([np.zeros(lag), russian[:-lag]])
    mixed = np.concatenate(
        [
            np.zeros(lag),
            0.18 * source[:-lag] + 0.73 * russian[:-lag],
        ]
    )

    report = final_media_qa.estimate_original_bed(
        source,
        mixed,
        delayed_russian,
        expected_level=0.18,
        sample_rate=sample_rate,
    )

    assert report["passed"] is True
    assert report["policy"] == "post-aac-original-bed-regression-v2"
    assert report["alignment_lag_samples"] == lag
    assert report["alignment_lag_ms"] == pytest.approx(7.0, abs=0.2)
    assert report["estimated_original_level"] == pytest.approx(0.18, abs=1e-6)
    assert report["estimated_russian_gain"] == pytest.approx(0.73, abs=1e-6)


def test_explicit_zero_original_bed_is_measurable_and_passes() -> None:
    source, russian = _branches(seconds=8)
    mixed = 0.73 * russian

    report = final_media_qa.estimate_original_bed(
        source,
        mixed,
        russian,
        expected_level=0.0,
    )

    assert report["passed"] is True
    assert report["absolute_level_mode"] is True
    assert report["estimated_original_level"] == pytest.approx(0.0, abs=1e-10)
    assert report["local_median_level"] == pytest.approx(0.0, abs=1e-10)
    assert report["local_spread_db"] is None
    assert report["local_spread_absolute"] == pytest.approx(0.0, abs=1e-10)


def test_zero_original_bed_still_rejects_real_source_leakage() -> None:
    source, russian = _branches(seconds=8)
    mixed = 0.03 * source + 0.73 * russian

    report = final_media_qa.estimate_original_bed(
        source,
        mixed,
        russian,
        expected_level=0.0,
    )

    assert report["passed"] is False
    assert report["estimated_original_level"] == pytest.approx(0.03, abs=1e-6)
    assert any("original level=" in item for item in report["failures"])


def test_short_four_second_clip_requires_only_available_windows() -> None:
    source, russian = _branches(seconds=4)
    mixed = 0.18 * source + 0.73 * russian

    report = final_media_qa.estimate_original_bed(
        source,
        mixed,
        russian,
        expected_level=0.18,
    )

    assert report["passed"] is True
    assert report["local_available_full_windows"] == 2
    assert report["local_required_windows"] == 2
    assert report["local_window_count"] == 2


def test_missing_project_inputs_stay_json_serializable(tmp_path: Path) -> None:
    root = tmp_path / "project"
    output = root / "output"
    output.mkdir(parents=True)
    mixed = output / "final_upload.mp4"
    mixed.write_bytes(b"mixed")

    report = final_media_qa.verify_original_bed(
        source_duration=59.0,
        mixed_video=mixed,
        russian_only_video=output / "russian_only.mp4",
    )

    assert report["applicable"] is True
    assert report["passed"] is False
    assert report["policy"] == "post-aac-original-bed-regression-v2"
    assert "source" not in report
    raw = json.dumps(report, ensure_ascii=False, allow_nan=False)
    assert "request.json" in raw
    assert "source/source.mp4" in raw


def test_zero_safe_package_keeps_legacy_alignment_bounded() -> None:
    assert Path(final_media_qa.__file__).name == "final_media_qa.py"
    legacy = Path(final_media_qa.__file__).resolve().parents[1] / "final_media_qa.py"
    source = legacy.read_text(encoding="utf-8")
    assert "ORIGINAL_ALIGNMENT_MAX_SECONDS = 0.15" in source
    assert "ORIGINAL_ALIGNMENT_PROBE_SECONDS = 180.0" in source
    assert "_estimate_alignment_lag" in source
    assert "_align_three" in source
