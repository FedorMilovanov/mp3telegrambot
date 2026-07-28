from __future__ import annotations

import json
from pathlib import Path

from tools.voxcpm2 import semantic_tts_guard_v4
from tools.voxcpm2 import semantic_tts_guard_v46 as focused


def _checkpoint(work_dir: Path, segment_id: int, base_seed: int = 123) -> None:
    path = work_dir / "checkpoints" / f"segment_{segment_id:02d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"signature": {"base_seed": base_seed}}),
        encoding="utf-8",
    )


def test_recovers_partial_marker_from_failed_full_qa(tmp_path: Path) -> None:
    work_dir = tmp_path / "segment_work"
    for segment_id in range(1, 19):
        if segment_id not in {13, 18}:
            _checkpoint(work_dir, segment_id, base_seed=456)

    report = {
        "guard_version": semantic_tts_guard_v4._GUARD_VERSION,
        "passed": False,
        "failed_segment_ids": [13, 18],
        "segments": [],
    }
    report_path = tmp_path / "audio" / "russian.semantic_qa.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps(report), encoding="utf-8")

    recovered = focused._recover_partial_marker(
        work_dir,
        all_ids=set(range(1, 19)),
        selected_ids={13, 18},
    )
    assert recovered
    marker = json.loads(
        (work_dir / "semantic_guard.marker.json").read_text(encoding="utf-8")
    )
    assert marker["guard_version"] == semantic_tts_guard_v4._GUARD_VERSION
    assert marker["state"] == "partial_recovered"
    assert marker["failed_segment_ids"] == [13, 18]
    assert marker["base_seed"] == 456


def test_partial_recovery_rejects_unrelated_selection(tmp_path: Path) -> None:
    work_dir = tmp_path / "segment_work"
    for segment_id in {1, 2, 4}:
        _checkpoint(work_dir, segment_id)
    report_path = tmp_path / "audio" / "russian.semantic_qa.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps({"failed_segment_ids": [3], "segments": []}),
        encoding="utf-8",
    )
    assert not focused._recover_partial_marker(
        work_dir,
        all_ids={1, 2, 3, 4},
        selected_ids={2},
    )


def test_failure_summary_names_each_quality_gate() -> None:
    report = {
        "failed_segment_ids": [13, 18],
        "segments": [
            {
                "id": 13,
                "passed": False,
                "semantic": {
                    "passed": False,
                    "token_recall": 0.52,
                    "sequence_similarity": 0.49,
                    "heard": "неполная фраза",
                },
                "continuity_v45": {
                    "passed": False,
                    "max_internal_gap": 1.14,
                    "active_ratio": 0.42,
                },
            },
            {
                "id": 18,
                "passed": False,
                "timing": {
                    "passed": False,
                    "onset_ms": 40.0,
                    "trailing_ms": 12.0,
                    "isolated_start_artifact": False,
                },
                "voice_match_v45": {
                    "passed": False,
                    "f0_median_ratio": 1.41,
                    "f0_p90_ratio": 1.48,
                    "voiced_ratio": 0.61,
                },
            },
        ],
    }
    summary = focused._failure_summary(report)
    assert "#13" in summary
    assert "распознавание" in summary
    assert "пауза" in summary
    assert "#18" in summary
    assert "стык" in summary
    assert "голос" in summary


def test_all_v45_entrypoints_install_focused_guard() -> None:
    for path in (
        Path("tools/voxcpm2/generic_audio_repair_runtime_v45.py"),
        Path("tools/voxcpm2/generic_gemini_runtime_v45.py"),
        Path("tools/voxcpm2/generic_direct_checked_runtime_v45.py"),
    ):
        source = path.read_text(encoding="utf-8")
        assert "semantic_tts_guard_v46.install()" in source
