from typing import Any

import pytest
from pathlib import Path

from services import shorts_factory_quality_gate as gate


def _candidate(
    title: str, score: int | float, *, hook: str = "Сильный хук", reason: str = "Самостоятельная мысль"
) -> dict[str, Any]:
    return {
        "title": title,
        "hook": hook,
        "reason": reason,
        "quality_score": score,
        "boundary_verified": True,
        "start_seconds": 10,
        "end_seconds": 100,
    }


def test_factory_quality_gate_prefers_quality_over_quantity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHORTS_FACTORY_MIN_SHORT_SCORE", raising=False)
    monkeypatch.delenv("SHORTS_FACTORY_MIN_LONG_SCORE", raising=False)
    plan = {
        "shorts_candidates": [
            _candidate("Сильный", 96),
            _candidate("Посредственный", 72),
            _candidate("Без хука", 99, hook=""),
        ],
        "long_candidates": [
            _candidate("Сильный длинный", 92, hook=""),
            _candidate("Слабый длинный", 70, hook=""),
        ],
    }
    gated = gate.apply_factory_quality_gate(plan)
    assert [item["title"] for item in gated["shorts_candidates"]] == ["Сильный"]
    assert [item["title"] for item in gated["long_candidates"]] == ["Сильный длинный"]
    assert gated["quality_gate"] == {
        "policy": "shorts-factory-final-quality-v1",
        "min_short_score": 88.0,
        "min_long_score": 85.0,
        "shorts_before": 3,
        "shorts_after": 1,
        "longs_before": 2,
        "longs_after": 1,
    }


def test_factory_quality_gate_requires_verified_boundaries_and_reason() -> None:
    unverified = _candidate("Без проверки", 100)
    unverified["boundary_verified"] = False
    no_reason = _candidate("Без причины", 100, reason="")
    gated = gate.apply_factory_quality_gate({"shorts_candidates": [unverified, no_reason], "long_candidates": []})
    assert gated["shorts_candidates"] == []


def test_factory_quality_gate_rejects_malformed_or_nonfinite_candidate_numbers() -> None:
    bad_start = _candidate("Плохой start", 99)
    bad_start["start_seconds"] = "not-a-number"
    infinite_end = _candidate("Бесконечный end", 99)
    infinite_end["end_seconds"] = float("inf")
    reversed_range = _candidate("Обратный диапазон", 99)
    reversed_range["start_seconds"] = 100
    reversed_range["end_seconds"] = 10
    infinite_score = _candidate("Бесконечный score", float("inf"))
    valid = _candidate("Валидный", 96)
    gated = gate.apply_factory_quality_gate(
        {
            "shorts_candidates": [
                bad_start,
                infinite_end,
                reversed_range,
                infinite_score,
                valid,
            ],
            "long_candidates": [bad_start, infinite_end, infinite_score, valid],
        }
    )
    assert [item["title"] for item in gated["shorts_candidates"]] == ["Валидный"]
    assert [item["title"] for item in gated["long_candidates"]] == ["Валидный"]


def test_factory_quality_gate_rejects_cut_inside_measured_russian_final_word() -> None:
    clipped = _candidate("Обрезанный Джон", 99)
    clipped["start_seconds"] = 0.0
    clipped["end_seconds"] = 51.852
    clipped["ru_master_word_intervals"] = [{"start": 51.76, "end": 51.94, "word": "Джон"}]

    too_tight = _candidate("Нет места для согласной", 99)
    too_tight["end_seconds"] = 52.0
    too_tight["ru_master_word_intervals"] = [{"start": 51.76, "end": 51.94, "word": "Джон"}]

    complete = _candidate("Завершено", 99)
    complete["end_seconds"] = 52.3
    complete["ru_master_word_intervals"] = [{"start": 51.76, "end": 51.94, "word": "Джон"}]

    malformed = _candidate("Непроверяемые таймкоды", 99)
    malformed["ru_master_word_intervals"] = [{"start": 51.7, "end": "nan"}]

    gated = gate.apply_factory_quality_gate(
        {"shorts_candidates": [clipped, too_tight, complete, malformed], "long_candidates": []}
    )
    assert [item["title"] for item in gated["shorts_candidates"]] == ["Завершено"]


def test_factory_quality_gate_does_not_claim_listening_approval_without_word_evidence() -> None:
    # Legacy candidates without authoritative RU-master word times keep their
    # existing selection policy; passing this gate is not listening QA.
    gated = gate.apply_factory_quality_gate(
        {"shorts_candidates": [_candidate("Без пословных данных", 99)], "long_candidates": []}
    )
    assert [item["title"] for item in gated["shorts_candidates"]] == ["Без пословных данных"]


def test_factory_quality_thresholds_have_explicit_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHORTS_FACTORY_MIN_SHORT_SCORE", "95")
    monkeypatch.setenv("SHORTS_FACTORY_MIN_LONG_SCORE", "93")
    gated = gate.apply_factory_quality_gate(
        {
            "shorts_candidates": [_candidate("94", 94), _candidate("96", 96)],
            "long_candidates": [
                _candidate("92", 92, hook=""),
                _candidate("94", 94, hook=""),
            ],
        }
    )
    assert [item["title"] for item in gated["shorts_candidates"]] == ["96"]
    assert [item["title"] for item in gated["long_candidates"]] == ["94"]


def test_factory_quality_thresholds_reject_nonfinite_environment_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHORTS_FACTORY_MIN_SHORT_SCORE", "nan")
    monkeypatch.setenv("SHORTS_FACTORY_MIN_LONG_SCORE", "inf")
    gated = gate.apply_factory_quality_gate(
        {
            "shorts_candidates": [_candidate("87", 87), _candidate("89", 89)],
            "long_candidates": [
                _candidate("84", 84, hook=""),
                _candidate("86", 86, hook=""),
            ],
        }
    )
    assert gated["quality_gate"]["min_short_score"] == gate.DEFAULT_MIN_SHORT_SCORE
    assert gated["quality_gate"]["min_long_score"] == gate.DEFAULT_MIN_LONG_SCORE
    assert [item["title"] for item in gated["shorts_candidates"]] == ["89"]
    assert [item["title"] for item in gated["long_candidates"]] == ["86"]


def test_factory_plan_language_is_normalized_and_nonsense_rejected() -> None:
    assert gate.validated_factory_plan_language({"metadata": {"language": "English"}}) == "en"
    for value in ("", "unknown", "mixed", "foo"):
        with __import__("pytest").raises(RuntimeError, match="доминирующий язык речи"):
            gate.validated_factory_plan_language({"metadata": {"language": value}})


def test_factory_quality_gate_is_a_pure_policy_not_an_installer() -> None:
    source = Path("services/shorts_factory_quality_gate.py").read_text(encoding="utf-8")
    assert "install_factory_plan_quality_gate" not in source
    assert "install_shorts_factory_execution_guard" not in source
    assert "install_factory_disk_guard" not in source
    assert "setattr(" not in source
