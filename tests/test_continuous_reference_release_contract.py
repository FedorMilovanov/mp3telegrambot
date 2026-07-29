from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tools.voxcpm2 import continuous_reference_policy as policy


ROOT = Path(__file__).resolve().parents[1]


def _stats(
    *,
    voiced: float = 0.30,
    active: float = 0.70,
    gap: float = 0.10,
) -> dict[str, float]:
    return {
        "voiced_ratio": voiced,
        "f0_median": 120.0,
        "f0_p90": 160.0,
        "active_ratio": active,
        "max_internal_gap": gap,
        "rms_dbfs": -24.0,
        "peak_dbfs": -5.0,
    }


def test_all_clean_entrypoints_use_continuous_reference_policy() -> None:
    names = (
        "generic_clean_gemini_runtime.py",
        "generic_clean_direct_runtime.py",
        "generic_clean_custom_runtime.py",
        "generic_clean_audio_repair_runtime.py",
    )
    for name in names:
        source = (ROOT / "tools" / "voxcpm2" / name).read_text(encoding="utf-8")
        assert "from tools.voxcpm2 import continuous_reference_policy" in source
        assert "continuous_reference_policy.build_calm_references(" in source


def test_unusable_continuous_window_is_not_ranked(monkeypatch) -> None:
    monkeypatch.setattr(
        policy,
        "_window_score",
        lambda _clip, _rate: (1.0, _stats(voiced=0.01)),
    )
    audio = np.zeros(10 * 16_000, dtype=np.float32)
    result = policy._candidate_windows(
        audio,
        16_000,
        [(0.0, 10.0)],
        target_seconds=8.0,
    )
    assert result == []


def test_usable_continuous_window_remains_candidate(monkeypatch) -> None:
    monkeypatch.setattr(
        policy,
        "_window_score",
        lambda _clip, _rate: (1.0, _stats()),
    )
    audio = np.zeros(10 * 16_000, dtype=np.float32)
    result = policy._candidate_windows(
        audio,
        16_000,
        [(0.0, 10.0)],
        target_seconds=8.0,
    )
    assert result
    assert all(policy._usable_stats(item["stats"]) for item in result)


def test_bad_multiwindow_fallback_is_deleted_and_rejected(
    monkeypatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "reference.wav"
    monkeypatch.setattr(
        policy,
        "_decode_source",
        lambda _source, _output: (np.zeros(16_000, dtype=np.float32), 16_000),
    )
    monkeypatch.setattr(policy, "_candidate_windows", lambda *args, **kwargs: [])

    def bad_fallback(_source, _intervals, destination, *, target_seconds):
        Path(destination).write_bytes(b"bad-reference")
        Path(destination).with_suffix(".selection.json").write_text(
            json.dumps({"selected": [{**_stats(voiced=0.01)}]}),
            encoding="utf-8",
        )

    monkeypatch.setattr(
        policy.professional_audio_v45,
        "build_reference_v45",
        bad_fallback,
    )
    with pytest.raises(RuntimeError, match="hard-quality floor"):
        policy.build_reference(
            tmp_path / "source.mp4",
            [(0.0, 4.0)],
            output,
            target_seconds=8.0,
            profile="extended",
        )
    assert not output.exists()
    assert not output.with_suffix(".selection.json").exists()


def test_good_multiwindow_fallback_keeps_explicit_mode(
    monkeypatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "reference.wav"
    monkeypatch.setattr(
        policy,
        "_decode_source",
        lambda _source, _output: (np.zeros(16_000, dtype=np.float32), 16_000),
    )
    monkeypatch.setattr(policy, "_candidate_windows", lambda *args, **kwargs: [])

    def good_fallback(_source, _intervals, destination, *, target_seconds):
        Path(destination).write_bytes(b"good-reference")
        Path(destination).with_suffix(".selection.json").write_text(
            json.dumps({"selected": [{**_stats()}]}),
            encoding="utf-8",
        )

    monkeypatch.setattr(
        policy.professional_audio_v45,
        "build_reference_v45",
        good_fallback,
    )
    report = policy.build_reference(
        tmp_path / "source.mp4",
        [(0.0, 4.0)],
        output,
        target_seconds=8.0,
        profile="extended",
    )
    assert report["reference_mode"] == "multi-window-fallback"
    assert report["reference_policy"] == policy.POLICY
    assert output.exists()
