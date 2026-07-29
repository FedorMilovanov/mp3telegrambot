from __future__ import annotations

import json
from pathlib import Path

from tools.voxcpm2.direct_max_quality_io import POLICY
from tools.voxcpm2.generic_clean_audio_repair_runtime import _renderer_baseline_ready
from tools.voxcpm2.semantic_tts_guard import _retarget_checkpoints


def _checkpoint(
    root: Path,
    segment_id: int,
    *,
    policy: str = POLICY,
) -> Path:
    path = root / "checkpoints" / f"segment_{segment_id:02d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "signature": {
                    "policy": policy,
                    "model_config_sha256": "model-hash",
                    "reference_sha256": "reference-hash",
                    "base_seed": 100,
                },
                "report": {"id": segment_id},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    for directory in ("segments_clean", "segments_fitted"):
        target = root / directory / f"{segment_id:02d}_extended.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"wav")
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

    assert not failed.exists()
    assert not (tmp_path / "segments_clean" / "17_extended.wav").exists()
    assert not (tmp_path / "segments_fitted" / "17_extended.wav").exists()
    assert (tmp_path / "segments_clean" / "11_extended.wav").exists()
    assert (tmp_path / "segments_fitted" / "11_extended.wav").exists()


def test_selective_repair_accepts_only_complete_v3_baseline(tmp_path: Path) -> None:
    _checkpoint(tmp_path, 1)
    _checkpoint(tmp_path, 2)
    ready, detail = _renderer_baseline_ready(tmp_path, {1, 2})
    assert ready is True
    assert detail == POLICY


def test_selective_repair_rejects_old_timbreless_baseline(tmp_path: Path) -> None:
    _checkpoint(tmp_path, 1, policy="voxcpm2-direct-max-quality-v2")
    _checkpoint(tmp_path, 2)
    ready, detail = _renderer_baseline_ready(tmp_path, {1, 2})
    assert ready is False
    assert "устаревший renderer-policy" in detail
    assert POLICY in detail


def test_selective_repair_rejects_missing_checkpoint(tmp_path: Path) -> None:
    _checkpoint(tmp_path, 1)
    ready, detail = _renderer_baseline_ready(tmp_path, {1, 2})
    assert ready is False
    assert "нет checkpoint" in detail
