from __future__ import annotations

from tools.voxcpm2 import direct_source_relative_continuity
from tools.voxcpm2 import direct_fail_closed_identity as monolithic_runtime_install


def _identity(f0: float, p90: float) -> dict[str, float]:
    return {"f0_median": f0, "f0_p90": p90}


def _segment(f0: float, p90: float) -> dict[str, object]:
    return {"source_prosody": {"f0_median": f0, "f0_p90": p90}}


def test_cross_language_source_is_diagnostic_and_never_changes_ranking() -> None:
    evidence = direct_source_relative_continuity.evaluate_transition(
        current_identity=_identity(90.0, 125.0),
        previous_identity=_identity(170.0, 230.0),
        current_segment=_segment(161.0, 221.0),
        previous_segment=_segment(160.0, 220.0),
    )

    assert evidence["policy"] == "cross-language-source-prosody-diagnostic-v3"
    assert evidence["advisory_source_available"] is True
    assert evidence["source_available"] is False
    assert evidence["absolute_gate_override_allowed"] is False
    assert evidence["ranking_penalty_enabled"] is False
    assert evidence["failures"] == []
    assert evidence["warnings"]
    assert evidence["raw_diagnostic_score"] > 0.0
    assert evidence["penalty"] == 0.0


def test_fail_closed_timeline_rejects_large_jump_even_with_source_support() -> None:
    rows = [
        {
            "pitch": _identity(170.0, 230.0),
            "failures": [],
            "passed": True,
            "source_relative_transition": {
                "advisory_source_available": True,
                "absolute_gate_override_allowed": True,
            },
        },
        {
            "pitch": _identity(90.0, 125.0),
            "failures": [],
            "passed": True,
            "source_relative_transition": {
                "advisory_source_available": True,
                "absolute_gate_override_allowed": True,
            },
        },
    ]

    monolithic_runtime_install.enforce_fail_closed_identity(
        rows,
        baseline_f0=170.0,
    )

    assert rows[1]["passed"] is False
    assert "global_voice_f0_outlier_fail_closed" in rows[1]["failures"]
    assert "adjacent_voice_pitch_discontinuity_fail_closed" in rows[1]["failures"]
    assert "adjacent_voice_range_discontinuity_fail_closed" in rows[1]["failures"]
    assert rows[1]["source_relative_transition"]["absolute_gate_override_allowed"] is False
    assert rows[1]["source_relative_transition"]["role"] == "ranking_and_diagnostics_only"


def test_fail_closed_timeline_keeps_normal_connected_variation() -> None:
    rows = [
        {"pitch": _identity(170.0, 230.0), "failures": [], "passed": True},
        {"pitch": _identity(187.0, 250.0), "failures": [], "passed": True},
    ]

    monolithic_runtime_install.enforce_fail_closed_identity(
        rows,
        baseline_f0=178.0,
    )

    assert rows[0]["passed"] is True
    assert rows[1]["passed"] is True
    assert rows[1]["failures"] == []
