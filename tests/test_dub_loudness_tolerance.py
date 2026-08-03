from __future__ import annotations

from pathlib import Path

import pytest

from tools.voxcpm2 import final_media_qa


ROOT = Path(__file__).resolve().parents[1]
MASTER = (
    ROOT
    / "tools"
    / "voxcpm2"
    / "examples"
    / "john_piper_z20py4yqhyq"
    / "master_constant_mix.py"
)


def _media() -> dict[str, object]:
    return {
        "audio_codec_name": "aac",
        "audio_sample_rate": 48_000,
        "audio_channels": 2,
        "audio_bit_rate": 320_000,
        "audio_duration": 59.0,
        "audio_start_time": 0.0,
        "video_codec_name": "h264",
        "video_start_time": 0.0,
        "av_start_delta_seconds": 0.0,
        "container_start_time": 0.0,
        "container_duration": 59.0,
    }


def _loudness(integrated: float) -> dict[str, float]:
    return {
        "integrated_lufs": integrated,
        "true_peak_dbtp": -1.2,
        "lra_lu": 4.0,
        "threshold_lufs": -27.0,
    }


def _verify(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, integrated: float):
    output = tmp_path / "result.mp4"
    output.write_bytes(b"not-empty")
    monkeypatch.setattr(final_media_qa, "probe_media", lambda _path: _media())
    monkeypatch.setattr(
        final_media_qa,
        "measure_loudness",
        lambda _path, **_kwargs: _loudness(integrated),
    )
    return final_media_qa.verify_final_file(
        output,
        source_duration=59.0,
        target_i=-16.0,
        target_lra=8.0,
        target_tp=-1.5,
    )


def test_fixed_original_mix_at_minus_17_01_lufs_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report = _verify(monkeypatch, tmp_path, integrated=-17.01)
    assert final_media_qa.LOUDNESS_TOLERANCE_LU == pytest.approx(1.25)
    assert report["passed"] is True
    assert report["limits"]["loudness_tolerance_lu"] == pytest.approx(1.25)


def test_mix_outside_shared_loudness_tolerance_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report = _verify(monkeypatch, tmp_path, integrated=-17.26)
    assert report["passed"] is False
    assert any("loudness=" in item for item in report["failures"])


def test_constant_mix_master_uses_shared_delivery_tolerance() -> None:
    source = MASTER.read_text(encoding="utf-8")
    assert "LOUDNESS_TOLERANCE_LU" in source
    assert "loudness_error > LOUDNESS_TOLERANCE_LU" in source
    assert "> 0.90" not in source
