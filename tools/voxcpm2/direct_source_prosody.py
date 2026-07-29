#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Soft source-prosody ranking for direct VoxCPM2 candidates.

Speaker identity and artifact checks remain hard gates elsewhere. This module
only breaks ties between already plausible candidates by preferring the one
whose pitch contour, voiced activity and rhetorical pauses are closer to the
matching source-language window. It never changes the supplied Russian text and
never relaxes the anti-shouting voice-reference limits.
"""
from __future__ import annotations

import math
from typing import Any

POLICY = "source-prosody-candidate-ranking-v1"
MAX_PENALTY = 95.0


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _positive(value: Any, *, low: float, high: float) -> float | None:
    result = _finite(value)
    if result is None or not low <= result <= high:
        return None
    return result


def _ratio(value: float, target: float) -> float:
    return value / max(target, 1e-9)


def _log_distance(ratio: float) -> float:
    return abs(math.log2(max(0.35, min(2.85, ratio))))


def _penalize_unavailable_candidate(
    result: dict[str, Any],
    reason: str,
) -> float:
    result.update(
        available=False,
        penalty=MAX_PENALTY,
        reason=reason,
    )
    return MAX_PENALTY


def source_prosody_penalty(
    candidate: dict[str, Any],
    segment: dict[str, Any],
) -> float:
    """Attach transparent source-prosody evidence and return a bounded penalty."""
    target = segment.get("source_prosody")
    pitch = candidate.get("pitch")
    activity = candidate.get("activity")
    tier = str(segment.get("expression_tier") or "unknown")
    result: dict[str, Any] = {
        "policy": POLICY,
        "available": False,
        "expression_tier": tier,
        "style_instruction": str(segment.get("style_instruction") or ""),
        "f0_median_ratio_to_source": None,
        "f0_p90_ratio_to_source": None,
        "voiced_ratio_delta_to_source": None,
        "active_ratio_delta_to_source": None,
        "internal_gap_delta_to_source": None,
        "penalty": 0.0,
        "reason": None,
    }
    candidate["source_prosody_match"] = result
    if not isinstance(target, dict):
        result["reason"] = "source_prosody отсутствует"
        return 0.0

    target_voiced = _positive(target.get("voiced_ratio"), low=0.12, high=1.0)
    target_median = _positive(target.get("f0_median"), low=55.0, high=350.0)
    target_p90 = _positive(target.get("f0_p90"), low=60.0, high=420.0)
    target_active = _positive(target.get("active_ratio"), low=0.05, high=1.0)
    target_gap = _positive(target.get("max_internal_gap"), low=0.0, high=6.0)
    if any(value is None for value in (target_voiced, target_median, target_p90)):
        result["reason"] = "невалидные source F0/voiced метрики"
        return 0.0

    if not isinstance(pitch, dict) or not isinstance(activity, dict):
        return _penalize_unavailable_candidate(
            result,
            "candidate pitch/activity отсутствуют",
        )

    candidate_voiced = _positive(pitch.get("voiced_ratio"), low=0.12, high=1.0)
    candidate_median = _positive(pitch.get("f0_median"), low=45.0, high=420.0)
    candidate_p90 = _positive(pitch.get("f0_p90"), low=50.0, high=500.0)
    candidate_active = _positive(activity.get("active_ratio"), low=0.0, high=1.0)
    candidate_gap = _positive(activity.get("max_internal_gap"), low=0.0, high=10.0)
    if any(
        value is None
        for value in (candidate_voiced, candidate_median, candidate_p90)
    ):
        return _penalize_unavailable_candidate(
            result,
            "невалидные candidate F0/voiced метрики",
        )

    median_ratio = _ratio(float(candidate_median), float(target_median))
    p90_ratio = _ratio(float(candidate_p90), float(target_p90))
    voiced_delta = abs(float(candidate_voiced) - float(target_voiced))
    active_delta = (
        abs(float(candidate_active) - float(target_active))
        if candidate_active is not None and target_active is not None
        else 0.0
    )
    gap_delta = (
        abs(float(candidate_gap) - float(target_gap))
        if candidate_gap is not None and target_gap is not None
        else 0.0
    )

    tier_weight = {
        "reflective": 0.82,
        "warm": 0.88,
        "earnest": 1.0,
        "emphatic": 1.12,
        "passionate": 1.18,
    }.get(tier, 0.95)
    penalty = tier_weight * (
        _log_distance(median_ratio) * 25.0
        + _log_distance(p90_ratio) * 15.0
        + voiced_delta * 22.0
        + active_delta * 12.0
        + min(gap_delta, 1.5) * 7.0
    )
    penalty = min(MAX_PENALTY, max(0.0, float(penalty)))
    result.update(
        available=True,
        f0_median_ratio_to_source=median_ratio,
        f0_p90_ratio_to_source=p90_ratio,
        voiced_ratio_delta_to_source=voiced_delta,
        active_ratio_delta_to_source=active_delta,
        internal_gap_delta_to_source=gap_delta,
        penalty=penalty,
        reason=None,
    )
    return penalty


__all__ = ["MAX_PENALTY", "POLICY", "source_prosody_penalty"]
