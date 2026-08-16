from __future__ import annotations

from pathlib import Path

import pytest

from tools.voxcpm2 import direct_surgical_polish_v2
from tools.voxcpm2 import direct_timing_guard as guard




def seg(text="Текст.", **extra):
    return {
        "id": 1,
        "text": text,
        "start": 0.0,
        "end": 4.0,
        "tail_guard": 0.18,
        **extra,
    }


def test_structure_validation_runs_before_base_guard(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="speech_slot"):
        guard.run_pre_model_guard(
            [seg(speech_slot=99.0)],
            work_dir=tmp_path,
            max_tempo=1.36,
            signature_context={},
        )


def test_exact_epoch_cap_is_independent_of_history(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="исчерпаны"):
        guard.enforce_retry_epoch_budget(
            work_dir=tmp_path,
            segment=seg(),
            retry_epoch=3,
            signature_context={},
        )


def test_marker_repeat_is_strict_and_does_not_mutate_input(tmp_path: Path) -> None:
    item = seg("Плотный текст.")
    evidence = {
        "kind": "measured",
        "max_tempo": 1.36,
        "attempts": [
            {"attempt": 1, "seed": 11, "duration": 6.0, "required_tempo": 1.57},
            {"attempt": 2, "seed": 12, "duration": 6.2, "required_tempo": 1.62},
        ],
    }
    marker = guard.persist_timing_block(
        tmp_path,
        segment=item,
        signature_context={"model": "a"},
        retry_epoch=1,
        evidence=evidence,
    )
    assert marker["schema_version"] == 3
    assert marker["policy"] == direct_surgical_polish_v2.MARKER_POLICY
    assert guard.load_matching_timing_block(
        tmp_path,
        segment=item,
        signature_context={"model": "a"},
    ) is not None
    assert guard.load_matching_timing_block(
        tmp_path,
        segment={**item, "text": "Новый текст."},
        signature_context={"model": "a"},
    ) is None
    assert list((tmp_path / "timing_blocks").glob("*.stale-input-changed-*"))
