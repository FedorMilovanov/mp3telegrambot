from __future__ import annotations

import math

from tools.voxcpm2.direct_source_prosody import (
    MAX_PENALTY,
    POLICY,
    source_prosody_penalty,
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
    assert POLICY == "source-prosody-candidate-ranking-v1"
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


def test_source_prosody_ranking_is_soft_and_bounded() -> None:
    candidate = _candidate(
        median=420.0,
        p90=500.0,
        voiced=0.01,
        active=0.01,
        gap=9.0,
    )
    penalty = source_prosody_penalty(candidate, _segment(tier="passionate"))
    assert math.isfinite(penalty)
    assert 0.0 <= penalty <= MAX_PENALTY
    assert candidate["source_prosody_match"]["available"] is False


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
    assert report["reason"] == "source_prosody отсутствует"


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
    assert candidate["source_prosody_match"]["available"] is False
