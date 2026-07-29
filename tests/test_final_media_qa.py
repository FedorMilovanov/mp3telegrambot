from __future__ import annotations

import ast
import json
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


def _media(
    *,
    audio_duration: float = 59.04,
    container_duration: float = 59.04,
    av_start_delta: float = 0.0,
):
    return {
        "audio_codec_name": "aac",
        "audio_sample_rate": 48_000,
        "audio_channels": 2,
        "audio_bit_rate": 320_000,
        "audio_duration": audio_duration,
        "audio_start_time": av_start_delta,
        "video_codec_name": "h264",
        "video_start_time": 0.0,
        "av_start_delta_seconds": abs(av_start_delta),
        "container_start_time": 0.0,
        "container_duration": container_duration,
    }


def _loudness(*, true_peak: float = -1.2, integrated: float = -15.8):
    return {
        "integrated_lufs": integrated,
        "true_peak_dbtp": true_peak,
        "lra_lu": 4.0,
        "threshold_lufs": -26.0,
    }


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
    assert "10.0 ** (float(target_tp) / 20.0)" in source
    assert "alimiter=limit={limiter_linear:.8f}:level=false:latency=true" in source
    assert "alimiter=limit=0.985:level=false:latency=true" in source
    assert '"limiter_auto_level": False' in source
    assert '"limiter_latency_compensated": True' in source


def test_final_media_qa_accepts_only_delivery_contract(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "result.mp4"
    output.write_bytes(b"not-empty")
    monkeypatch.setattr(final_media_qa, "probe_media", lambda _path: _media())
    monkeypatch.setattr(
        final_media_qa,
        "measure_loudness",
        lambda _path, **_kwargs: _loudness(),
    )
    report = final_media_qa.verify_final_file(
        output,
        source_duration=59.0,
        target_i=-16.0,
        target_lra=8.0,
        target_tp=-1.5,
    )
    assert report["passed"] is True
    assert report["audio_duration_delta_seconds"] == pytest.approx(0.04)
    assert report["container_duration_delta_seconds"] == pytest.approx(0.04)
    assert report["av_start_delta_seconds"] == 0.0


def test_final_media_qa_reports_aac_true_peak_overshoot(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "result.mp4"
    output.write_bytes(b"not-empty")
    monkeypatch.setattr(final_media_qa, "probe_media", lambda _path: _media())
    monkeypatch.setattr(
        final_media_qa,
        "measure_loudness",
        lambda _path, **_kwargs: _loudness(true_peak=-0.7, integrated=-16.0),
    )
    report = final_media_qa.verify_final_file(
        output,
        source_duration=59.0,
        target_i=-16.0,
        target_lra=8.0,
        target_tp=-1.5,
    )
    assert report["passed"] is False
    assert any("true peak" in item for item in report["failures"])


def test_final_media_qa_checks_audio_and_container_duration(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "result.mp4"
    output.write_bytes(b"not-empty")
    monkeypatch.setattr(
        final_media_qa,
        "probe_media",
        lambda _path: _media(audio_duration=58.5, container_duration=58.7),
    )
    monkeypatch.setattr(
        final_media_qa,
        "measure_loudness",
        lambda _path, **_kwargs: _loudness(integrated=-16.0),
    )
    report = final_media_qa.verify_final_file(
        output,
        source_duration=59.0,
        target_i=-16.0,
        target_lra=8.0,
        target_tp=-1.5,
    )
    assert report["passed"] is False
    assert any("audio duration delta" in item for item in report["failures"])
    assert any("container duration delta" in item for item in report["failures"])


def test_final_media_qa_rejects_audio_video_start_desync(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "result.mp4"
    output.write_bytes(b"not-empty")
    monkeypatch.setattr(
        final_media_qa,
        "probe_media",
        lambda _path: _media(av_start_delta=0.081),
    )
    monkeypatch.setattr(
        final_media_qa,
        "measure_loudness",
        lambda _path, **_kwargs: _loudness(integrated=-16.0),
    )
    report = final_media_qa.verify_final_file(
        output,
        source_duration=59.0,
        target_i=-16.0,
        target_lra=8.0,
        target_tp=-1.5,
    )
    assert report["passed"] is False
    assert report["av_start_delta_seconds"] == pytest.approx(0.081)
    assert any("A/V start delta" in item for item in report["failures"])


def test_failed_final_outputs_write_report_before_raising(monkeypatch, tmp_path: Path) -> None:
    mixed = tmp_path / "mixed.mp4"
    russian = tmp_path / "russian.mp4"
    mixed.write_bytes(b"not-empty")
    russian.write_bytes(b"not-empty")
    report_path = tmp_path / "final_media_verification.json"

    monkeypatch.setattr(final_media_qa, "probe_media", lambda _path: _media())

    def measured(path: Path, **_kwargs):
        return _loudness(
            true_peak=-0.6 if Path(path).name == "mixed.mp4" else -1.3,
            integrated=-16.0,
        )

    monkeypatch.setattr(final_media_qa, "measure_loudness", measured)
    with pytest.raises(RuntimeError, match="Отчёт сохранён"):
        final_media_qa.verify_final_outputs(
            source_duration=59.0,
            mixed_video=mixed,
            russian_only_video=russian,
            target_i=-16.0,
            target_lra=8.0,
            target_tp=-1.5,
            report_path=report_path,
        )

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert payload["mixed"]["passed"] is False
    assert payload["russian_only"]["passed"] is True
    assert any("true peak" in item for item in payload["mixed"]["failures"])
