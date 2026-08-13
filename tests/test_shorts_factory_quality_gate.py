from pathlib import Path

from services import shorts_factory_quality_gate as gate


def _candidate(title, score, *, hook="Сильный хук", reason="Самостоятельная мысль"):
    return {
        "title": title,
        "hook": hook,
        "reason": reason,
        "quality_score": score,
        "boundary_verified": True,
        "start_seconds": 10,
        "end_seconds": 100,
    }


def test_factory_quality_gate_prefers_quality_over_quantity(monkeypatch):
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
    assert [item["title"] for item in gated["long_candidates"]] == [
        "Сильный длинный"
    ]
    assert gated["quality_gate"] == {
        "policy": "shorts-factory-final-quality-v1",
        "min_short_score": 88.0,
        "min_long_score": 85.0,
        "shorts_before": 3,
        "shorts_after": 1,
        "longs_before": 2,
        "longs_after": 1,
    }


def test_factory_quality_gate_requires_verified_boundaries_and_reason():
    unverified = _candidate("Без проверки", 100)
    unverified["boundary_verified"] = False
    no_reason = _candidate("Без причины", 100, reason="")

    gated = gate.apply_factory_quality_gate(
        {
            "shorts_candidates": [unverified, no_reason],
            "long_candidates": [],
        }
    )

    assert gated["shorts_candidates"] == []


def test_factory_quality_gate_rejects_malformed_or_nonfinite_candidate_numbers():
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


def test_factory_quality_thresholds_have_explicit_override(monkeypatch):
    monkeypatch.setenv("SHORTS_FACTORY_MIN_SHORT_SCORE", "95")
    monkeypatch.setenv("SHORTS_FACTORY_MIN_LONG_SCORE", "93")

    gated = gate.apply_factory_quality_gate(
        {
            "shorts_candidates": [_candidate("94", 94), _candidate("96", 96)],
            "long_candidates": [_candidate("92", 92, hook=""), _candidate("94", 94, hook="")],
        }
    )

    assert [item["title"] for item in gated["shorts_candidates"]] == ["96"]
    assert [item["title"] for item in gated["long_candidates"]] == ["94"]


def test_factory_quality_thresholds_reject_nonfinite_environment_values(monkeypatch):
    monkeypatch.setenv("SHORTS_FACTORY_MIN_SHORT_SCORE", "nan")
    monkeypatch.setenv("SHORTS_FACTORY_MIN_LONG_SCORE", "inf")

    gated = gate.apply_factory_quality_gate(
        {
            "shorts_candidates": [_candidate("87", 87), _candidate("89", 89)],
            "long_candidates": [_candidate("84", 84, hook=""), _candidate("86", 86, hook="")],
        }
    )

    assert gated["quality_gate"]["min_short_score"] == gate.DEFAULT_MIN_SHORT_SCORE
    assert gated["quality_gate"]["min_long_score"] == gate.DEFAULT_MIN_LONG_SCORE
    assert [item["title"] for item in gated["shorts_candidates"]] == ["89"]
    assert [item["title"] for item in gated["long_candidates"]] == ["86"]


def test_factory_plan_language_is_normalized_and_nonsense_rejected():
    assert gate.validated_factory_plan_language(
        {"metadata": {"language": "English"}}
    ) == "en"

    for value in ("", "unknown", "mixed", "foo"):
        try:
            gate.validated_factory_plan_language(
                {"metadata": {"language": value}}
            )
        except RuntimeError as exc:
            assert "доминирующий язык речи" in str(exc)
        else:
            raise AssertionError(f"language {value!r} must fail closed")


def test_factory_quality_gate_is_explicitly_installed_by_required_runtime():
    gate_source = Path("services/shorts_factory_quality_gate.py").read_text(
        encoding="utf-8"
    )
    timing_source = Path("services/shorts_factory_timing.py").read_text(
        encoding="utf-8"
    )
    runtime_source = Path("services/shorts_factory_runtime.py").read_text(
        encoding="utf-8"
    )

    assert gate_source.count("\ninstall_factory_plan_quality_gate()\n") == 0
    assert timing_source.count("\ninstall_factory_plan_quality_gate()\n") == 0
    assert "install_factory_plan_quality_gate()" in runtime_source
