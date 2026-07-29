from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.voxcpm2 import clean_runtime_contract
from tools.voxcpm2.direct_max_quality_io import POLICY
from tools.voxcpm2.generic_clean_audio_repair_runtime import (
    _next_seed,
    _renderer_baseline_ready,
)
from tools.voxcpm2.semantic_tts_guard import _retarget_checkpoints


def _checkpoint(
    root: Path,
    segment_id: int,
    *,
    policy: str = POLICY,
    profile: str = "extended",
    report_id: int | None = None,
    voice_match: dict | None = None,
    fitted_bytes: bytes = b"wav",
) -> Path:
    path = root / "checkpoints" / f"segment_{segment_id:02d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    selected_voice = voice_match or {
        "f0_median_ratio": 1.01,
        "f0_p90_ratio": 0.98,
        "spectral_similarity": 0.86,
    }
    path.write_text(
        json.dumps(
            {
                "signature": {
                    "policy": policy,
                    "model_config_sha256": "model-hash",
                    "reference_sha256": "reference-hash",
                    "reference_profile": profile,
                    "base_seed": 100,
                },
                "report": {
                    "id": segment_id if report_id is None else report_id,
                    "renderer_policy": policy,
                    "selected_voice_match": selected_voice,
                    "fit": {"fitted_duration": 3.25},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    clean = root / "segments_clean" / f"{segment_id:02d}_{profile}_clean.wav"
    fitted = root / "segments_fitted" / f"{segment_id:02d}_{profile}_fitted.wav"
    clean.parent.mkdir(parents=True, exist_ok=True)
    fitted.parent.mkdir(parents=True, exist_ok=True)
    clean.write_bytes(b"wav")
    fitted.write_bytes(fitted_bytes)
    return path


def test_targeted_retry_preserves_good_v3_fingerprints(tmp_path: Path) -> None:
    good = _checkpoint(tmp_path, 11)
    failed = _checkpoint(tmp_path, 17)

    _retarget_checkpoints(
        tmp_path,
        good_ids={11},
        failed_ids={17},
        new_base_seed=100_100,
    )

    payload = json.loads(good.read_text(encoding="utf-8"))
    signature = payload["signature"]
    assert signature["base_seed"] == 100_100
    assert signature["policy"] == "voxcpm2-direct-max-quality-v3"
    assert signature["model_config_sha256"] == "model-hash"
    assert signature["reference_sha256"] == "reference-hash"
    assert signature["reference_profile"] == "extended"

    assert not failed.exists()
    assert not (tmp_path / "segments_clean" / "17_extended_clean.wav").exists()
    assert not (tmp_path / "segments_fitted" / "17_extended_fitted.wav").exists()
    assert (tmp_path / "segments_clean" / "11_extended_clean.wav").exists()
    assert (tmp_path / "segments_fitted" / "11_extended_fitted.wav").exists()


def test_selective_repair_accepts_only_complete_v3_baseline(tmp_path: Path) -> None:
    _checkpoint(tmp_path, 1)
    _checkpoint(tmp_path, 2, profile="composite")
    ready, detail = _renderer_baseline_ready(tmp_path, {1, 2})
    assert ready is True
    assert detail == POLICY


def test_selective_repair_rejects_old_timbreless_baseline(tmp_path: Path) -> None:
    _checkpoint(tmp_path, 1, policy="voxcpm2-direct-max-quality-v2")
    _checkpoint(tmp_path, 2)
    ready, detail = _renderer_baseline_ready(tmp_path, {1, 2})
    assert ready is False
    assert "устаревший renderer-policy" in detail


def test_selective_repair_rejects_missing_checkpoint(tmp_path: Path) -> None:
    _checkpoint(tmp_path, 1)
    ready, detail = _renderer_baseline_ready(tmp_path, {1, 2})
    assert ready is False
    assert "#2: checkpoint JSON неполон" in detail


def test_selective_repair_rejects_wrong_report_id(tmp_path: Path) -> None:
    _checkpoint(tmp_path, 1, report_id=999)
    ready, detail = _renderer_baseline_ready(tmp_path, {1})
    assert ready is False
    assert "report id не совпадает" in detail


def test_selective_repair_rejects_nonfinite_voice_evidence(tmp_path: Path) -> None:
    _checkpoint(
        tmp_path,
        1,
        voice_match={
            "f0_median_ratio": float("nan"),
            "f0_p90_ratio": 1.0,
            "spectral_similarity": 0.9,
        },
    )
    ready, detail = _renderer_baseline_ready(tmp_path, {1})
    assert ready is False
    assert "selected voice evidence неполон" in detail


def test_selective_repair_rejects_empty_fitted_wav(tmp_path: Path) -> None:
    _checkpoint(tmp_path, 1, fitted_bytes=b"")
    ready, detail = _renderer_baseline_ready(tmp_path, {1})
    assert ready is False
    assert "fitted WAV отсутствует или пуст" in detail


def test_repair_seed_preserves_explicit_zero_before_offset() -> None:
    seed = _next_seed(
        {"base_seed": 0},
        {"base_seed": 0},
        {"audio_repairs": []},
    )
    assert seed == clean_runtime_contract.RETRY_SEED_OFFSET


@pytest.mark.parametrize("value", [-1, clean_runtime_contract.MAX_BASE_SEED + 1])
def test_repair_seed_rejects_out_of_range_marker(value: int) -> None:
    with pytest.raises(RuntimeError, match="Marker base_seed"):
        _next_seed(
            {"base_seed": 0},
            {"base_seed": value},
            {"audio_repairs": []},
        )
