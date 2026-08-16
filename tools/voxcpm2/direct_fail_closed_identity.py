#!/usr/bin/env python3
"""Pure fail-closed speaker identity policy for assembled Russian audio."""
from __future__ import annotations

import math
from typing import Any

FAIL_CLOSED_IDENTITY_POLICY = "cross-language-prosody-cannot-override-identity-v1"

ABSOLUTE_GLOBAL_F0_LIMIT_ST = 8.4

ABSOLUTE_ADJACENT_F0_RATIO = (0.62, 1.62)

ABSOLUTE_ADJACENT_P90_RATIO = (0.58, 1.72)

def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return result if math.isfinite(result) else float(default)

def _ratio(value: float, reference: float) -> float:
    return value / reference if value > 0.0 and reference > 0.0 else 0.0

def _semitones(value: float, reference: float) -> float | None:
    ratio = _ratio(value, reference)
    return 12.0 * math.log2(ratio) if ratio > 0.0 else None

def _append_failure(row: dict[str, Any], reason: str) -> None:
    failures = row.setdefault("failures", [])
    if reason not in failures:
        failures.append(reason)
    row["passed"] = False

def enforce_fail_closed_identity(
    rows: list[dict[str, Any]],
    *,
    baseline_f0: float,
) -> list[dict[str, Any]]:
    """Reapply speaker-identity pitch limits without cross-language exceptions.

    Source prosody remains attached as advisory evidence, but English timing or
    lexical stress can never widen the Russian hard limits.
    """
    baseline = _finite(baseline_f0)
    for index, row in enumerate(rows):
        row["fail_closed_identity_policy"] = FAIL_CLOSED_IDENTITY_POLICY
        transition = row.get("source_relative_transition")
        if isinstance(transition, dict):
            transition["absolute_gate_override_allowed"] = False
            transition["role"] = "ranking_and_diagnostics_only"

        pitch = row.get("pitch") if isinstance(row.get("pitch"), dict) else {}
        global_jump = _semitones(_finite(pitch.get("f0_median")), baseline)
        row["fail_closed_global_f0_jump_st"] = global_jump
        row["fail_closed_global_f0_limit_st"] = ABSOLUTE_GLOBAL_F0_LIMIT_ST
        if global_jump is not None and abs(global_jump) > ABSOLUTE_GLOBAL_F0_LIMIT_ST:
            _append_failure(row, "global_voice_f0_outlier_fail_closed")

        if index == 0:
            continue
        previous_pitch = (
            rows[index - 1].get("pitch")
            if isinstance(rows[index - 1].get("pitch"), dict)
            else {}
        )
        median_ratio = _ratio(
            _finite(pitch.get("f0_median")),
            _finite(previous_pitch.get("f0_median")),
        )
        p90_ratio = _ratio(
            _finite(pitch.get("f0_p90")),
            _finite(previous_pitch.get("f0_p90")),
        )
        row["fail_closed_neighbour_f0_median_ratio"] = median_ratio
        row["fail_closed_neighbour_f0_p90_ratio"] = p90_ratio
        if median_ratio and not (
            ABSOLUTE_ADJACENT_F0_RATIO[0]
            <= median_ratio
            <= ABSOLUTE_ADJACENT_F0_RATIO[1]
        ):
            _append_failure(row, "adjacent_voice_pitch_discontinuity_fail_closed")
        if p90_ratio and not (
            ABSOLUTE_ADJACENT_P90_RATIO[0]
            <= p90_ratio
            <= ABSOLUTE_ADJACENT_P90_RATIO[1]
        ):
            _append_failure(row, "adjacent_voice_range_discontinuity_fail_closed")
    return rows

__all__ = [
    "ABSOLUTE_ADJACENT_F0_RATIO",
    "ABSOLUTE_ADJACENT_P90_RATIO",
    "ABSOLUTE_GLOBAL_F0_LIMIT_ST",
    "FAIL_CLOSED_IDENTITY_POLICY",
    "enforce_fail_closed_identity",
]
