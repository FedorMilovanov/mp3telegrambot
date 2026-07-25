from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SEGMENTED_PATH = ROOT / "tools" / "voxcpm2" / "segmented_cpu_dub.py"
VALIDATOR_PATH = ROOT / "tools" / "voxcpm2" / "validate_run.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


segmented = _load_module("voxcpm2_segmented_cpu_dub", SEGMENTED_PATH)
validator = _load_module("voxcpm2_validate_run", VALIDATOR_PATH)


def test_atempo_chain_handles_extreme_slowdown() -> None:
    chain = segmented.atempo_chain(0.394299)
    assert chain[0] == "atempo=0.5"
    factors = [float(item.split("=", 1)[1]) for item in chain]
    product = 1.0
    for factor in factors:
        assert 0.5 <= factor <= 2.0
        product *= factor
    assert product == pytest.approx(0.394299, rel=1e-6)


def test_atempo_chain_handles_speedup() -> None:
    chain = segmented.atempo_chain(4.5)
    factors = [float(item.split("=", 1)[1]) for item in chain]
    product = 1.0
    for factor in factors:
        assert 0.5 <= factor <= 2.0
        product *= factor
    assert product == pytest.approx(4.5, rel=1e-6)


def test_read_segments_accepts_gaps_and_rejects_overlap(tmp_path: Path) -> None:
    valid = tmp_path / "valid.json"
    valid.write_text(
        json.dumps(
            [
                {"id": 1, "start": 0.0, "end": 2.0, "text": "Один."},
                {"id": 2, "start": 2.5, "end": 4.0, "text": "Два."},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    segments = segmented.read_segments(valid)
    assert len(segments) == 2
    assert segments[1]["start"] == 2.5

    invalid = tmp_path / "invalid.json"
    invalid.write_text(
        json.dumps(
            [
                {"id": 1, "start": 0.0, "end": 2.0, "text": "Один."},
                {"id": 2, "start": 1.9, "end": 4.0, "text": "Два."},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="overlap"):
        segmented.read_segments(invalid)


def _valid_report(tmp_path: Path) -> dict:
    raw = tmp_path / "01_raw.wav"
    fitted = tmp_path / "01_fitted.wav"
    output = tmp_path / "timeline.wav"
    for path in (raw, fitted, output):
        path.write_bytes(b"placeholder")

    return {
        "video_duration": 5.12,
        "final_audio_duration": 5.12,
        "cuda_available": False,
        "output": str(output),
        "segments": [
            {
                "id": 1,
                "start": 0.0,
                "end": 5.12,
                "text": "Проверочная реплика.",
                "target_duration": 5.12,
                "raw_duration": 5.376,
                "fitted_duration": 5.12,
                "tempo": 1.05,
                "raw_path": str(raw),
                "fitted_path": str(fitted),
            }
        ],
    }


def test_validator_accepts_complete_cpu_report(tmp_path: Path) -> None:
    warnings = validator.validate_report(_valid_report(tmp_path))
    assert warnings == []


def test_validator_rejects_cuda_report(tmp_path: Path) -> None:
    report = _valid_report(tmp_path)
    report["cuda_available"] = True
    with pytest.raises(validator.ValidationError, match="cuda_available"):
        validator.validate_report(report)


def test_validator_rejects_extreme_tempo(tmp_path: Path) -> None:
    report = _valid_report(tmp_path)
    report["segments"][0]["raw_duration"] = 2.0
    report["segments"][0]["tempo"] = 2.0 / 5.12
    with pytest.raises(validator.ValidationError, match="tempo"):
        validator.validate_report(report)


def test_validator_warns_for_review_range(tmp_path: Path) -> None:
    report = _valid_report(tmp_path)
    report["segments"][0]["raw_duration"] = 6.656
    report["segments"][0]["tempo"] = 1.30
    warnings = validator.validate_report(report)
    assert warnings and "listening review" in warnings[0]
