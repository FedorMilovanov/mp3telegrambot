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


def test_factory_quality_gate_is_installed_before_timing_runtime():
    from services import shorts_factory_timing

    assert gate._INSTALLED is True
    assert callable(shorts_factory_timing.align_factory_livedub_candidates)
