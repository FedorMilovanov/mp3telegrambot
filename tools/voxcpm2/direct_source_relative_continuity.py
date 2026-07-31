#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Source-relative continuity limits for independently rendered speech breaths.

A large adjacent F0 change is not automatically an identity failure: the source
speaker may genuinely raise or lower the voice at that discourse boundary. This
module compares the generated transition with the matching original-speaker
transition. The old absolute fallback may be replaced only when both median and
upper-range source pitch transitions are valid; partial evidence remains useful
for diagnostics and penalties but never weakens a fallback hard gate.
"""
from __future__ import annotations

import math
from typing import Any

POLICY = "source-relative-adjacent-voice-continuity-v1"

MEDIAN_BASE_LIMIT_ST = 5.0
MEDIAN_SOURCE_HEADROOM_ST = 2.5
MEDIAN_MAX_LIMIT_ST = 11.5
MEDIAN_UNGUIDED_LIMIT_ST = 6.5

P90_BASE_LIMIT_ST = 6.5
P90_SOURCE_HEADROOM_ST = 3.0
P90_MAX_LIMIT_ST = 12.5
P90_UNGUIDED_LIMIT_ST = 8.0

DIRECTION_MISMATCH_MIN_ST = 4.0
DIRECTION_MISMATCH_PENALTY = 28.0
MAX_PENALTY = 120.0


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _pitch(value: Any, *, low: float, high: float) -> float | None:
    result = _finite(value)
    if result is None or not low <= result <= high:
        return None
    return result


def _signed_semitones(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or current <= 0.0 or previous <= 0.0:
        return None
    return 12.0 * math.log2(current / previous)


def _source_pitch(segment: dict[str, Any] | None, key: str) -> float | None:
    if not isinstance(segment, dict):
        return None
    source = segment.get("source_prosody")
    if not isinstance(source, dict):
        return None
    if key == "f0_median":
        return _pitch(source.get(key), low=55.0, high=350.0)
    return _pitch(source.get(key), low=60.0, high=430.0)


def _identity_pitch(identity: dict[str, Any] | None, key: str) -> float | None:
    if not isinstance(identity, dict):
        return None
    if key == "f0_median":
        return _pitch(identity.get(key), low=45.0, high=430.0)
    return _pitch(identity.get(key), low=50.0, high=520.0)


def _allowed_jump(
    source_jump: float | None,
    *,
    base: float,
    headroom: float,
    maximum: float,
    unguided: float,
) -> float:
    if source_jump is None:
        return float(unguided)
    return min(float(maximum), max(float(base), abs(float(source_jump)) + float(headroom)))


def evaluate_transition(
    *,
    current_identity: dict[str, Any],
    previous_identity: dict[str, Any] | None,
    current_segment: dict[str, Any],
    previous_segment: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return fail-closed, source-supported adjacent pitch evidence."""
    generated_median = _signed_semitones(
        _identity_pitch(current_identity, "f0_median"),
        _identity_pitch(previous_identity, "f0_median"),
    )
    generated_p90 = _signed_semitones(
        _identity_pitch(current_identity, "f0_p90"),
        _identity_pitch(previous_identity, "f0_p90"),
    )
    source_median = _signed_semitones(
        _source_pitch(current_segment, "f0_median"),
        _source_pitch(previous_segment, "f0_median"),
    )
    source_p90 = _signed_semitones(
        _source_pitch(current_segment, "f0_p90"),
        _source_pitch(previous_segment, "f0_p90"),
    )

    median_limit = _allowed_jump(
        source_median,
        base=MEDIAN_BASE_LIMIT_ST,
        headroom=MEDIAN_SOURCE_HEADROOM_ST,
        maximum=MEDIAN_MAX_LIMIT_ST,
        unguided=MEDIAN_UNGUIDED_LIMIT_ST,
    )
    p90_limit = _allowed_jump(
        source_p90,
        base=P90_BASE_LIMIT_ST,
        headroom=P90_SOURCE_HEADROOM_ST,
        maximum=P90_MAX_LIMIT_ST,
        unguided=P90_UNGUIDED_LIMIT_ST,
    )

    failures: list[str] = []
    penalty = 0.0
    if generated_median is not None:
        excess = max(0.0, abs(generated_median) - median_limit)
        penalty += excess * 18.0
        if excess > 1e-9:
            failures.append("source_relative_f0_median_jump")
    if generated_p90 is not None:
        excess = max(0.0, abs(generated_p90) - p90_limit)
        penalty += excess * 10.0
        if excess > 1e-9:
            failures.append("source_relative_f0_p90_jump")

    direction_mismatch = bool(
        source_median is not None
        and generated_median is not None
        and abs(source_median) >= DIRECTION_MISMATCH_MIN_ST
        and abs(generated_median) >= DIRECTION_MISMATCH_MIN_ST
        and source_median * generated_median < 0.0
    )
    if direction_mismatch:
        # Opposite direction is suspicious, but cross-language phrasing may differ.
        # Keep it as ranking evidence; timbre/anchor and jump limits remain hard gates.
        penalty += DIRECTION_MISMATCH_PENALTY

    source_median_available = source_median is not None
    source_p90_available = source_p90 is not None
    # Replacing both blind fallback gates requires a complete two-metric source
    # transition. Partial evidence must never silently remove the other gate.
    source_available = bool(source_median_available and source_p90_available)
    return {
        "policy": POLICY,
        "available": previous_identity is not None,
        "source_available": source_available,
        "source_median_available": source_median_available,
        "source_p90_available": source_p90_available,
        "hard_ok": not failures,
        "failures": failures,
        "penalty": min(MAX_PENALTY, max(0.0, penalty)),
        "generated_f0_median_jump_st": generated_median,
        "generated_f0_p90_jump_st": generated_p90,
        "source_f0_median_jump_st": source_median,
        "source_f0_p90_jump_st": source_p90,
        "allowed_f0_median_jump_st": median_limit,
        "allowed_f0_p90_jump_st": p90_limit,
        "direction_mismatch": direction_mismatch,
        "limits": {
            "median_base_st": MEDIAN_BASE_LIMIT_ST,
            "median_source_headroom_st": MEDIAN_SOURCE_HEADROOM_ST,
            "median_max_st": MEDIAN_MAX_LIMIT_ST,
            "median_unguided_st": MEDIAN_UNGUIDED_LIMIT_ST,
            "p90_base_st": P90_BASE_LIMIT_ST,
            "p90_source_headroom_st": P90_SOURCE_HEADROOM_ST,
            "p90_max_st": P90_MAX_LIMIT_ST,
            "p90_unguided_st": P90_UNGUIDED_LIMIT_ST,
        },
    }


__all__ = [
    "DIRECTION_MISMATCH_MIN_ST",
    "MEDIAN_BASE_LIMIT_ST",
    "MEDIAN_MAX_LIMIT_ST",
    "MEDIAN_SOURCE_HEADROOM_ST",
    "MEDIAN_UNGUIDED_LIMIT_ST",
    "P90_BASE_LIMIT_ST",
    "P90_MAX_LIMIT_ST",
    "P90_SOURCE_HEADROOM_ST",
    "P90_UNGUIDED_LIMIT_ST",
    "POLICY",
    "evaluate_transition",
]
