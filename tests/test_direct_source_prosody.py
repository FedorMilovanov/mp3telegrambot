from __future__ import annotations

import math

import pytest

from tools.voxcpm2 import direct_source_prosody as prosody
from tools.voxcpm2.direct_source_prosody import (
    MAX_PENALTY,
    POLICY,
    candidate_pitch_evidence_ok,
    source_prosody_penalty,
)


@pytest.fixture(autouse=True)
def _neutral_cadence_and_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep source-ranking tests independent from the separate cadence suite."""
    monkeypatch.setattr(
        prosody,
        "evaluate_candidate_cadence",
        lambda *_args, **_kwargs: {
            "hard_ok": True,
            "failures": [],
            "penalty": 0.0,
        },
    )
    monkeypatch.setattr(
        prosody,
        "detect_late_broadband_tail",
        lambda *_args, **_kwargs: {"suspicious": False},
    )


def _candidate(*, median: float, p90: float, voiced: float, active: float, gap: float):
    return {
        "pitch": {
            "f0_median": median,
            "f0_p90": p90,
            "voiced_ratio": voiced,
        },
        "activity": {
            "active_ratio": active,
            "max_internal_gap": gap,
        },
    }


def _segment(*, tier: str = "emphatic"):
    return {
        "expression_tier": tier,
        "style_instruction": "firm and emphatic, controlled intensity, never shout",
        "source_prosody": {
            "f0_median": 112.0,
            "f0_p90": 154.0,
            "voiced_ratio": 0.61,
            "active_ratio": 0.72,
            "max_internal_gap": 0.18,
        },
    }


def test_candidate_matching_source_prosody_gets_near_zero_penalty() -> None:
    candidate = _candidate(
        median=112.0,
        p90=154.0,
        voiced=0.61,
        active=0.72,
        gap=0.18,
    )
    penalty = source_prosody_penalty(candidate, _segment())
    report = candidate["source_prosody_match"]
    assert POLICY == "source-prosody-candidate-ranking-v2"
    assert candidate_pitch_evidence_ok(candidate) is True
    assert penalty == 0.0
    assert report["available"] is True
    assert report["f0_median_ratio_to_source"] == 1.0
    assert report["f0_p90_ratio_to_source"] == 1.0


def test_neutral_candidate_loses_to_matching_emphatic_candidate() -> None:
    matching = _candidate(
        median=112.0,
        p90=154.0,
        voiced=0.61,
        active=0.72,
        gap=0.18,
    )
    neutral = _candidate(
        median=88.0,
        p90=112.0,
        voiced=0.43,
        active=0.49,
        gap=0.52,
    )
    matching_penalty = source_prosody_penalty(matching, _segment())
    neutral_penalty = source_prosody_penalty(neutral, _segment())
    assert matching_penalty < neutral_penalty
    assert neutral_penalty > 15.0


def test_invalid_candidate_prosody_gets_maximum_soft_penalty() -> None:
    candidate = _candidate(
        median=420.0,
        p90=500.0,
        voiced=0.01,
        active=0.01,
        gap=9.0,
    )
    penalty = source_prosody_penalty(candidate, _segment(tier="passionate"))
    report = candidate["source_prosody_match"]
    assert candidate_pitch_evidence_ok(candidate) is False
    assert math.isfinite(penalty)
    assert penalty == MAX_PENALTY
    assert report["available"] is False
    assert report["penalty"] == MAX_PENALTY
    assert report["reason"] == "невалидные candidate F0/voiced/cadence метрики"


def test_contradictory_pitch_shape_fails_raw_hard_floor() -> None:
    candidate = _candidate(
        median=180.0,
        p90=90.0,
        voiced=0.60,
        active=0.70,
        gap=0.20,
    )
    assert candidate_pitch_evidence_ok(candidate) is False
    assert source_prosody_penalty(candidate, _segment()) == MAX_PENALTY


def test_missing_candidate_pitch_activity_gets_maximum_soft_penalty() -> None:
    candidate: dict = {}
    penalty = source_prosody_penalty(candidate, _segment())
    report = candidate["source_prosody_match"]
    assert candidate_pitch_evidence_ok(candidate) is False
    assert penalty == MAX_PENALTY
    assert report["available"] is False
    assert report["reason"] == "candidate pitch/activity отсутствуют"


def test_missing_source_prosody_is_neutral_not_fabricated() -> None:
    candidate = _candidate(
        median=112.0,
        p90=154.0,
        voiced=0.61,
        active=0.72,
        gap=0.18,
    )
    penalty = source_prosody_penalty(candidate, {"expression_tier": "earnest"})
    report = candidate["source_prosody_match"]
    assert penalty == 0.0
    assert report["available"] is False
    assert (
        report["reason"]
        == "source_prosody отсутствует; применён русский cadence gate"
    )


def test_nonfinite_source_metrics_do_not_poison_score() -> None:
    segment = _segment()
    segment["source_prosody"]["f0_median"] = float("nan")
    candidate = _candidate(
        median=112.0,
        p90=154.0,
        voiced=0.61,
        active=0.72,
        gap=0.18,
    )
    penalty = source_prosody_penalty(candidate, segment)
    assert penalty == 0.0
    report = candidate["source_prosody_match"]
    assert report["available"] is False
    assert (
        report["reason"]
        == "невалидные source F0/voiced метрики; применён русский cadence gate"
    )
