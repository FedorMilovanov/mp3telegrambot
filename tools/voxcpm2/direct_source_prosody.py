#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Source-prosody and Russian-cadence ranking for direct VoxCPM2 candidates.

Speaker identity and artifact checks remain hard gates elsewhere. This module
compares each plausible candidate with the matching source-language window and
also checks the Russian syntactic ending actually produced. It never changes
the supplied Russian text and never relaxes the anti-shouting voice limits.
"""
from __future__ import annotations

import math
from typing import Any

from tools.voxcpm2.direct_russian_cadence import evaluate_candidate_cadence
from tools.voxcpm2.direct_tail_artifact import detect_late_broadband_tail

POLICY = "source-prosody-candidate-ranking-v2"
MAX_PENALTY = 180.0


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


def candidate_pitch_evidence_ok(candidate: dict[str, Any]) -> bool:
    """Reject contradictory pitch, cadence or artifact evidence."""
    if candidate.get("cadence_hard_ok") is False:
        return False
    pitch = candidate.get("pitch")
    if not isinstance(pitch, dict):
        return False
    voiced = _positive(pitch.get("voiced_ratio"), low=0.12, high=1.0)
    median = _positive(pitch.get("f0_median"), low=45.0, high=420.0)
    p90 = _positive(pitch.get("f0_p90"), low=50.0, high=500.0)
    return bool(
        voiced is not None
        and median is not None
        and p90 is not None
        and float(p90) >= float(median) * 0.75
    )


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
    """Attach source, contour, cadence and late-tail evidence."""
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
        "acoustic_penalty": 0.0,
        "cadence_penalty": 0.0,
        "penalty": 0.0,
        "reason": None,
    }
    candidate["source_prosody_match"] = result

    cadence = evaluate_candidate_cadence(candidate, segment)
    tail_artifact = detect_late_broadband_tail(
        candidate.get("samples"),
        int(candidate.get("sample_rate") or 1),
    )
    if tail_artifact.get("suspicious"):
        cadence = dict(cadence)
        failures = list(cadence.get("failures") or [])
        if "late_broadband_tail" not in failures:
            failures.append("late_broadband_tail")
        cadence.update(
            hard_ok=False,
            failures=failures,
            penalty=float(cadence.get("penalty") or 0.0) + 145.0,
            tail_artifact=tail_artifact,
        )
    else:
        cadence = {**cadence, "tail_artifact": tail_artifact}
    result["cadence"] = cadence
    result["cadence_penalty"] = float(cadence.get("penalty") or 0.0)
    candidate["cadence_hard_ok"] = bool(cadence.get("hard_ok"))
    candidate["cadence_evidence"] = cadence

    if not isinstance(target, dict):
        total = min(MAX_PENALTY, float(cadence.get("penalty") or 0.0))
        result.update(
            available=False,
            penalty=total,
            reason="source_prosody отсутствует; применён русский cadence gate",
        )
        return total

    target_voiced = _positive(target.get("voiced_ratio"), low=0.12, high=1.0)
    target_median = _positive(target.get("f0_median"), low=55.0, high=350.0)
    target_p90 = _positive(target.get("f0_p90"), low=60.0, high=420.0)
    target_active = _positive(target.get("active_ratio"), low=0.05, high=1.0)
    target_gap = _positive(target.get("max_internal_gap"), low=0.0, high=6.0)
    if any(value is None for value in (target_voiced, target_median, target_p90)):
        total = min(MAX_PENALTY, float(cadence.get("penalty") or 0.0))
        result.update(
            available=False,
            penalty=total,
            reason="невалидные source F0/voiced метрики; применён русский cadence gate",
        )
        return total

    if not isinstance(pitch, dict) or not isinstance(activity, dict):
        acoustic = _penalize_unavailable_candidate(
            result,
            "candidate pitch/activity отсутствуют",
        )
        total = min(MAX_PENALTY, acoustic + float(cadence.get("penalty") or 0.0))
        result["penalty"] = total
        return total
    if not candidate_pitch_evidence_ok(candidate):
        acoustic = _penalize_unavailable_candidate(
            result,
            "невалидные candidate F0/voiced/cadence метрики",
        )
        total = min(MAX_PENALTY, acoustic + float(cadence.get("penalty") or 0.0))
        result["penalty"] = total
        return total

    candidate_voiced = _positive(pitch.get("voiced_ratio"), low=0.12, high=1.0)
    candidate_median = _positive(pitch.get("f0_median"), low=45.0, high=420.0)
    candidate_p90 = _positive(pitch.get("f0_p90"), low=50.0, high=500.0)
    candidate_active = _positive(activity.get("active_ratio"), low=0.0, high=1.0)
    candidate_gap = _positive(activity.get("max_internal_gap"), low=0.0, high=10.0)
    if any(
        value is None
        for value in (candidate_voiced, candidate_median, candidate_p90)
    ):
        acoustic = _penalize_unavailable_candidate(
            result,
            "невалидные candidate F0/voiced метрики",
        )
        total = min(MAX_PENALTY, acoustic + float(cadence.get("penalty") or 0.0))
        result["penalty"] = total
        return total

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
    acoustic_penalty = tier_weight * (
        _log_distance(median_ratio) * 25.0
        + _log_distance(p90_ratio) * 15.0
        + voiced_delta * 22.0
        + active_delta * 12.0
        + min(gap_delta, 1.5) * 7.0
    )
    cadence_penalty = float(cadence.get("penalty") or 0.0)
    total_penalty = min(
        MAX_PENALTY,
        max(0.0, float(acoustic_penalty) + cadence_penalty),
    )
    result.update(
        available=True,
        f0_median_ratio_to_source=median_ratio,
        f0_p90_ratio_to_source=p90_ratio,
        voiced_ratio_delta_to_source=voiced_delta,
        active_ratio_delta_to_source=active_delta,
        internal_gap_delta_to_source=gap_delta,
        acoustic_penalty=acoustic_penalty,
        cadence_penalty=cadence_penalty,
        penalty=total_penalty,
        reason=None,
    )
    return total_penalty


__all__ = [
    "MAX_PENALTY",
    "POLICY",
    "candidate_pitch_evidence_ok",
    "source_prosody_penalty",
]

_BASE_ALL = tuple(globals().get('__all__', ()))

from pathlib import Path

import types


from tools.voxcpm2 import source_prosody_policy

CANDIDATE_CONTINUATION_POLICY = "defer-short-continuation-to-timeline-v1"

_legacy_evaluate_candidate_cadence = evaluate_candidate_cadence

_legacy_source_prosody_penalty = source_prosody_penalty

def _defer_short_continuation(result: dict[str, Any]) -> dict[str, Any]:
    revised = dict(result)
    cadence = str(revised.get("cadence") or "")
    failures = list(revised.get("failures") or [])
    if cadence not in {"continuation", "linked"}:
        return revised
    if "continuation_too_short" not in failures:
        return revised

    remaining = [item for item in failures if item != "continuation_too_short"]
    revised.update(
        failures=remaining,
        hard_ok=not remaining,
        timeline_compaction_required=True,
        candidate_continuation_policy=CANDIDATE_CONTINUATION_POLICY,
        deferred_candidate_failure="continuation_too_short",
    )
    return revised

def evaluate_candidate_cadence(
    candidate: dict[str, Any],
    segment: dict[str, Any],
) -> dict[str, Any]:
    return _defer_short_continuation(
        dict(_legacy_evaluate_candidate_cadence(candidate, segment))
    )

def source_prosody_penalty(
    candidate: dict[str, Any],
    segment: dict[str, Any],
) -> float:
    """Populate diagnostics but return zero for cross-language ranking."""
    diagnostic = float(_legacy_source_prosody_penalty(candidate, segment))
    match = candidate.get("source_prosody_match")
    if not isinstance(match, dict):
        match = {}
        candidate["source_prosody_match"] = match
    match["source_prosody_policy"] = source_prosody_policy.POLICY
    match["diagnostic_penalty"] = diagnostic
    diagnostic_only = source_prosody_policy.is_diagnostic_only(segment)
    match["source_prosody_ranking_enabled"] = not diagnostic_only
    return 0.0 if diagnostic_only else diagnostic

evaluate_candidate_cadence = evaluate_candidate_cadence

source_prosody_penalty = source_prosody_penalty

candidate_pitch_evidence_ok = candidate_pitch_evidence_ok

__all__ = sorted(
    set(_BASE_ALL)
    | {
        "CANDIDATE_CONTINUATION_POLICY",
        "_defer_short_continuation",
        "candidate_pitch_evidence_ok",
        "evaluate_candidate_cadence",
        "source_prosody_penalty",
    }
)
