from __future__ import annotations

import ast
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


def test_master_verifies_encoded_mp4_not_only_pcm() -> None:
    source = MASTER.read_text(encoding="utf-8")
    ast.parse(source)
    assert "verify_final_outputs" in source
    assert "final_media_verification.json" in source
    assert '"apad=pad_dur=2"' in source
    assert '"-t"' in source
    assert '"-shortest"' not in source
    assert '"aac"' in source
    assert '"320k"' in source
    assert '"48000"' in source


def test_final_media_qa_accepts_only_delivery_contract(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "result.mp4"
    output.write_bytes(b"not-empty")
    monkeypatch.setattr(
        final_media_qa,
        "probe_media",
        lambda _path: {
            "codec_name": "aac",
            "sample_rate": 48_000,
            "channels": 2,
            "bit_rate": 320_000,
            "duration": 59.04,
        },
    )
    monkeypatch.setattr(
        final_media_qa,
        "measure_loudness",
        lambda _path, **_kwargs: {
            "integrated_lufs": -15.8,
            "true_peak_dbtp": -1.2,
            "lra_lu": 4.0,
            "threshold_lufs": -26.0,
        },
    )
    report = final_media_qa.verify_final_file(
        output,
        source_duration=59.0,
        target_i=-16.0,
        target_lra=8.0,
        target_tp=-1.5,
    )
    assert report["passed"] is True
    assert report["duration_delta_seconds"] == pytest.approx(0.04)


def test_final_media_qa_rejects_aac_true_peak_overshoot(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "result.mp4"
    output.write_bytes(b"not-empty")
    monkeypatch.setattr(
        final_media_qa,
        "probe_media",
        lambda _path: {
            "codec_name": "aac",
            "sample_rate": 48_000,
            "channels": 2,
            "bit_rate": 320_000,
            "duration": 59.0,
        },
    )
    monkeypatch.setattr(
        final_media_qa,
        "measure_loudness",
        lambda _path, **_kwargs: {
            "integrated_lufs": -16.0,
            "true_peak_dbtp": -0.7,
            "lra_lu": 4.0,
            "threshold_lufs": -26.0,
        },
    )
    with pytest.raises(RuntimeError, match="true peak"):
        final_media_qa.verify_final_file(
            output,
            source_duration=59.0,
            target_i=-16.0,
            target_lra=8.0,
            target_tp=-1.5,
        )


def test_final_media_qa_rejects_truncated_video(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "result.mp4"
    output.write_bytes(b"not-empty")
    monkeypatch.setattr(
        final_media_qa,
        "probe_media",
        lambda _path: {
            "codec_name": "aac",
            "sample_rate": 48_000,
            "channels": 2,
            "bit_rate": 320_000,
            "duration": 58.5,
        },
    )
    monkeypatch.setattr(
        final_media_qa,
        "measure_loudness",
        lambda _path, **_kwargs: {
            "integrated_lufs": -16.0,
            "true_peak_dbtp": -1.3,
            "lra_lu": 4.0,
            "threshold_lufs": -26.0,
        },
    )
    with pytest.raises(RuntimeError, match="duration delta"):
        final_media_qa.verify_final_file(
            output,
            source_duration=59.0,
            target_i=-16.0,
            target_lra=8.0,
            target_tp=-1.5,
        )
