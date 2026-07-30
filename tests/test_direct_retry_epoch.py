from __future__ import annotations

import json
from pathlib import Path

from tools.voxcpm2.direct_retry_epoch import (
    POLICY,
    SEED_EPOCH_STRIDE,
    advance_retry_epoch,
    invalidate_segment_for_retry,
    load_retry_epoch,
    retry_epoch_path,
    seed_for_attempt,
)


def _segment(segment_id: int, profile: str = "extended") -> dict[str, object]:
    return {
        "id": segment_id,
        "reference_profile": profile,
        "text": f"Сегмент {segment_id}.",
    }


def test_retry_epoch_defaults_to_zero_and_changes_seed_by_fixed_stride(tmp_path: Path) -> None:
    assert POLICY == "failed-segment-seed-epoch-v1"
    assert SEED_EPOCH_STRIDE == 100_000
    assert load_retry_epoch(tmp_path, 19) == 0

    seed0 = seed_for_attempt(2026072900, 19, 2, 0)
    seed1 = seed_for_attempt(2026072900, 19, 2, 1)
    seed2 = seed_for_attempt(2026072900, 19, 2, 2)

    assert seed1 - seed0 == SEED_EPOCH_STRIDE
    assert seed2 - seed1 == SEED_EPOCH_STRIDE


def test_advance_retry_epoch_is_durable_atomic_and_keeps_bounded_history(tmp_path: Path) -> None:
    first = advance_retry_epoch(
        tmp_path,
        7,
        reason="raw_candidate_hard_failure",
        evidence={"attempts": 5},
    )
    second = advance_retry_epoch(
        tmp_path,
        7,
        reason="assembled_delivery:terminal_not_resolved",
        evidence={"ending": 0.4},
    )

    path = retry_epoch_path(tmp_path, 7)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert first["epoch"] == 1
    assert second["epoch"] == 2
    assert load_retry_epoch(tmp_path, 7) == 2
    assert payload["epoch"] == 2
    assert payload["history"][-2]["reason"] == "raw_candidate_hard_failure"
    assert payload["history"][-1]["reason"].startswith("assembled_delivery")
    assert list(path.parent.glob("*.tmp")) == []


def test_invalidate_removes_only_failed_segment_and_preserves_neighbors(tmp_path: Path) -> None:
    work = tmp_path / "segment_work"
    fitted_dir = work / "segments_fitted"
    clean_dir = work / "segments_clean"
    checkpoint_dir = work / "checkpoints"
    for directory in (fitted_dir, clean_dir, checkpoint_dir):
        directory.mkdir(parents=True, exist_ok=True)

    failed_fitted = fitted_dir / "19_extended_fitted.wav"
    failed_clean = clean_dir / "19_extended_clean.wav"
    failed_checkpoint = checkpoint_dir / "segment_19.json"
    neighbor_fitted = fitted_dir / "18_extended_fitted.wav"
    neighbor_clean = clean_dir / "18_extended_clean.wav"
    neighbor_checkpoint = checkpoint_dir / "segment_18.json"
    for path in (
        failed_fitted,
        failed_clean,
        failed_checkpoint,
        neighbor_fitted,
        neighbor_clean,
        neighbor_checkpoint,
    ):
        path.write_bytes(b"checkpoint")

    report = invalidate_segment_for_retry(
        work,
        _segment(19),
        reason="post_aac_delivery:late_broadband_burst",
        fitted_path=failed_fitted,
        evidence={"tail_artifact": "late_broadband_burst"},
    )

    assert report["retry_epoch"] == 1
    assert not failed_fitted.exists()
    assert not failed_clean.exists()
    assert not failed_checkpoint.exists()
    assert neighbor_fitted.exists()
    assert neighbor_clean.exists()
    assert neighbor_checkpoint.exists()
    assert load_retry_epoch(work, 19) == 1
    assert load_retry_epoch(work, 18) == 0


def test_repeated_failure_advances_only_target_segment_epoch(tmp_path: Path) -> None:
    work = tmp_path / "segment_work"
    invalidate_segment_for_retry(work, _segment(3), reason="first")
    invalidate_segment_for_retry(work, _segment(3), reason="second")
    invalidate_segment_for_retry(work, _segment(4), reason="neighbor")

    assert load_retry_epoch(work, 3) == 2
    assert load_retry_epoch(work, 4) == 1
