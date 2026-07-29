from __future__ import annotations

import ast
import json
from pathlib import Path

import numpy as np
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


def _synthetic_branches(*, seconds: int = 12, sample_rate: int = 8_000):
    rng = np.random.default_rng(20260729)
    length = seconds * sample_rate
    source = rng.normal(0.0, 0.11, length)
    russian = rng.normal(0.0, 0.09, length)
    return source.astype(np.float64), russian.astype(np.float64)


def test_master_verifies_encoded_mp4_not_only_pcm() -> None:
    source = MASTER.read_text(encoding="utf-8")
    qa = Path(final_media_qa.__file__).read_text(encoding="utf-8")
    ast.parse(source)
    ast.parse(qa)
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
    assert 'post-aac-original-bed-regression-v1' in qa
    assert 'estimate_original_bed' in qa
    assert 'original_bed' in qa


def test_original_bed_regression_accepts_fixed_eighteen_percent() -> None:
    source, russian = _synthetic_branches()
    mixed = 0.18 * source + 0.73 * russian
    report = final_media_qa.estimate_original_bed(
        source,
        mixed,
        russian,
        expected_level=0.18,
        sample_rate=8_000,
    )
    assert report["passed"] is True
    assert report["estimated_original_level"] == pytest.approx(0.18, abs=1e-6)
    assert report["estimated_russian_gain"] == pytest.approx(0.73, abs=1e-6)
    assert report["local_window_count"] >= 3
    assert report["local_spread_db"] == pytest.approx(0.0, abs=1e-6)


def test_original_bed_regression_rejects_loud_english_branch() -> None:
    source, russian = _synthetic_branches()
    mixed = 0.48 * source + 0.73 * russian
    report = final_media_qa.estimate_original_bed(
        source,
        mixed,
        russian,
        expected_level=0.18,
        sample_rate=8_000,
    )
    assert report["passed"] is False
    assert report["estimated_original_level"] == pytest.approx(0.48, abs=1e-6)
    assert any("original level" in item for item in report["failures"])


def test_original_bed_regression_rejects_local_gain_pumping() -> None:
    sample_rate = 8_000
    source, russian = _synthetic_branches(seconds=20, sample_rate=sample_rate)
    coefficient = np.empty_like(source)
    window = sample_rate * 2
    for index, start in enumerate(range(0, len(source), window)):
        coefficient[start : start + window] = 0.16 if index % 2 == 0 else 0.20
    mixed = coefficient * source + 0.73 * russian
    report = final_media_qa.estimate_original_bed(
        source,
        mixed,
        russian,
        expected_level=0.18,
        sample_rate=sample_rate,
    )
    assert report["estimated_original_level"] == pytest.approx(0.18, abs=0.002)
    assert report["passed"] is False
    assert report["local_spread_db"] > final_media_qa.ORIGINAL_LOCAL_SPREAD_DB
    assert any("локальный разброс" in item for item in report["failures"])


def test_project_original_contract_reads_expected_level(tmp_path: Path) -> None:
    root = tmp_path / "dub-project"
    output = root / "output"
    source_dir = root / "source"
    output.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    mixed = output / "final_upload.mp4"
    mixed.write_bytes(b"mixed")
    (source_dir / "source.mp4").write_bytes(b"source")
    (root / "request.json").write_text(
        json.dumps({"schema_version": 1, "original_level": 0.18}),
        encoding="utf-8",
    )
    contract = final_media_qa._project_original_contract(mixed)
    assert contract["applicable"] is True
    assert contract["passed"] is True
    assert contract["expected_original_level"] == 0.18


def test_final_media_qa_accepts_only_delivery_contract(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "result.mp4"
    output.write_bytes(b"not-empty")
    monkeypatch.setattr(final_media_qa, "probe_media", lambda _path: _media())
    monkeypatch.setattr(final_media_qa, "measure_loudness", lambda _path, **_kwargs: _loudness())
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_duration", float("nan")),
        ("source_duration", float("inf")),
        ("target_i", float("nan")),
        ("target_lra", float("inf")),
        ("target_tp", float("-inf")),
    ],
)
def test_nonfinite_release_contract_fails_before_media_probe(
    monkeypatch,
    tmp_path: Path,
    field: str,
    value: float,
) -> None:
    output = tmp_path / "result.mp4"
    output.write_bytes(b"not-empty")
    called = False

    def probe(_path: Path):
        nonlocal called
        called = True
        return _media()

    monkeypatch.setattr(final_media_qa, "probe_media", probe)
    kwargs = {
        "source_duration": 59.0,
        "target_i": -16.0,
        "target_lra": 8.0,
        "target_tp": -1.5,
    }
    kwargs[field] = value
    report = final_media_qa.verify_final_file(output, **kwargs)
    assert report["passed"] is False
    assert called is False
    assert any("contract:" in item for item in report["failures"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_duration", 0.0),
        ("target_i", -71.0),
        ("target_i", -4.0),
        ("target_lra", 0.0),
        ("target_lra", 51.0),
        ("target_tp", -10.0),
        ("target_tp", 0.1),
    ],
)
def test_out_of_range_release_contract_is_rejected(
    tmp_path: Path,
    field: str,
    value: float,
) -> None:
    output = tmp_path / "result.mp4"
    output.write_bytes(b"not-empty")
    kwargs = {
        "source_duration": 59.0,
        "target_i": -16.0,
        "target_lra": 8.0,
        "target_tp": -1.5,
    }
    kwargs[field] = value
    report = final_media_qa.verify_final_file(output, **kwargs)
    assert report["passed"] is False
    assert any("contract:" in item for item in report["failures"])


def test_invalid_contract_report_is_strict_json(monkeypatch, tmp_path: Path) -> None:
    mixed = tmp_path / "mixed.mp4"
    russian = tmp_path / "russian.mp4"
    mixed.write_bytes(b"not-empty")
    russian.write_bytes(b"not-empty")
    report_path = tmp_path / "final_media_verification.json"
    with pytest.raises(RuntimeError, match="Отчёт сохранён"):
        final_media_qa.verify_final_outputs(
            source_duration=float("nan"),
            mixed_video=mixed,
            russian_only_video=russian,
            target_i=-16.0,
            target_lra=8.0,
            target_tp=-1.5,
            report_path=report_path,
        )
    raw = report_path.read_text(encoding="utf-8")
    assert "NaN" not in raw
    payload = json.loads(raw)
    assert payload["passed"] is False
    assert payload["mixed"]["source_duration"] == "nan"


def test_loudnorm_parser_ignores_noise_and_uses_last_object() -> None:
    text = (
        "prefix {not json}\n"
        '{"input_i":"-20.0","input_tp":"-2.0","input_lra":"3.0","input_thresh":"-30.0"}\n'
        "diagnostic\n"
        '{"input_i":"-16.1","input_tp":"-1.2","input_lra":"4.0","input_thresh":"-26.0"}\n'
    )
    payload = final_media_qa._last_json_object(text)
    assert payload["input_i"] == "-16.1"


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


def test_failed_original_bed_writes_report_before_raising(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "project"
    output = root / "output"
    output.mkdir(parents=True)
    mixed = output / "final_upload.mp4"
    russian = output / "russian_only.mp4"
    mixed.write_bytes(b"mixed")
    russian.write_bytes(b"russian")
    report_path = root / "master_work" / "final_media_verification.json"

    monkeypatch.setattr(
        final_media_qa,
        "verify_final_file",
        lambda path, **_kwargs: {
            "path": str(path),
            "passed": True,
            "failures": [],
        },
    )
    monkeypatch.setattr(
        final_media_qa,
        "verify_original_bed",
        lambda **_kwargs: {
            "policy": final_media_qa.ORIGINAL_BED_POLICY,
            "applicable": True,
            "passed": False,
            "failures": ["original level=0.48; нужен 0.18"],
        },
    )
    with pytest.raises(RuntimeError, match="original-bed"):
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
    assert payload["original_bed"]["passed"] is False
