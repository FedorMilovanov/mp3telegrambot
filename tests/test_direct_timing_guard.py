from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.voxcpm2 import direct_timing_guard as guard


def segment(segment_id: int, text: str, start: float, end: float, tail: float = 0.18):
    return {
        "id": segment_id,
        "text": text,
        "start": start,
        "end": end,
        "tail_guard": tail,
    }


def candidate(attempt: int, seed: int, duration: float, slot: float):
    return {
        "attempt": attempt,
        "seed": seed,
        "duration": duration,
        "required_tempo": duration / slot,
        "score": 100.0,
        "cadence_evidence": {"failures": ["fit_tempo_exceeds_hard_limit"]},
        "tail_info": {"suspicious": False},
    }


def test_overloaded_text_fails_before_synthesis(tmp_path: Path) -> None:
    overloaded = segment(
        3,
        (
            "В десятой главе Послания к Римлянам сказано: Если устами "
            "твоими будешь исповедовать Иисуса Господом и сердцем веровать, "
            "что Бог воскресил Его из мёртвых, то спасёшься."
        ),
        15.28,
        22.20,
    )
    with pytest.raises(RuntimeError, match="физически перегружен"):
        guard.run_pre_model_guard(
            [overloaded],
            work_dir=tmp_path,
            max_tempo=1.36,
            signature_context={"backend": "voxcpm2"},
        )
    report = json.loads((tmp_path / guard.REPORT_NAME).read_text(encoding="utf-8"))
    assert report["critical_ids"] == [3]


def test_safe_unrelated_segments_pass_and_get_adaptive_plans(tmp_path: Path) -> None:
    segments = [
        segment(1, "Добрый вечер. Сегодня разберём важный вопрос.", 0.0, 5.5),
        segment(2, "Смысл текста сохраняется, а речь звучит естественно.", 5.8, 11.5),
    ]
    report = guard.run_pre_model_guard(
        segments,
        work_dir=tmp_path,
        max_tempo=1.36,
        signature_context={"backend": "voxcpm2", "model": "any"},
    )
    assert report["critical_ids"] == []
    assert all(row["candidate_plan"]["max_attempts"] <= 5 for row in report["segments"])


def test_dynamic_stop_requires_independent_candidates() -> None:
    item = segment(
        1,
        "В коротком окне находится очень длинная, плотная и заведомо "
        "непомещающаяся русская фраза с большим количеством слов.",
        0.0,
        4.0,
    )
    slot = 3.82
    duplicate_seed = [
        candidate(1, 700, 6.3, slot),
        candidate(2, 700, 6.5, slot),
    ]
    assert guard.evaluate_dynamic_timing_failure(
        duplicate_seed,
        segment=item,
        speech_slot=slot,
        retry_epoch=0,
        max_tempo=1.36,
    ) is None
    independent = [
        candidate(1, 700, 6.3, slot),
        candidate(2, 701, 6.5, slot),
    ]
    assert guard.evaluate_dynamic_timing_failure(
        independent,
        segment=item,
        speech_slot=slot,
        retry_epoch=0,
        max_tempo=1.36,
    ) is not None


def test_failure_scope_changes_with_text_model_and_reference() -> None:
    item = segment(1, "Исходный текст.", 0.0, 4.0)
    base = {
        "model_config_sha256": "a" * 64,
        "reference_sha256": "b" * 64,
    }
    first = guard.failure_scope_fingerprint(item, signature_context=base)
    second = guard.failure_scope_fingerprint(
        {**item, "text": "Исправленный текст."},
        signature_context=base,
    )
    third = guard.failure_scope_fingerprint(
        item,
        signature_context={**base, "reference_sha256": "c" * 64},
    )
    assert len({first, second, third}) == 3
