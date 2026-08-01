#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Russian sentence-ending and source-diagnostic cadence contract.

The sibling module performs deterministic F0 and energy analysis. This facade
keeps those measurements and rejects unfinished Russian endings, rising
exclamations and speech that cannot fit its real cue. Source-language contours
remain visible in reports but never rank or hard-gate a Russian candidate.
"""
from __future__ import annotations

import importlib.util
import math
import re
import sys
from pathlib import Path
from typing import Any

from tools.voxcpm2 import source_prosody_policy
from tools.voxcpm2.direct_max_quality_io import (
    MAX_TEMPO,
    SPEECH_SLOT_POLICY,
    speech_slot_seconds,
)

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "direct_russian_cadence.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._direct_russian_cadence_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить Russian cadence analysis: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _legacy
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

POLICY = "russian-cadence-contour-v4"
DELIVERY_POLICY = "russian-ending-source-diagnostic-v3"
EMPHASIS_EVIDENCE_POLICY = "russian-only-emphasis-advisory-v2"
_STRONG_TIERS = {"emphatic", "passionate"}
_SOURCE_PEAK_MIN_DOMINANCE = 0.18
_legacy_evaluate_candidate_cadence = _legacy.evaluate_candidate_cadence


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _source_peak_evidence(segment: dict[str, Any]) -> tuple[int | None, float]:
    """Return validated source contour evidence for diagnostics only."""
    source = segment.get("source_prosody")
    if not isinstance(source, dict):
        return None, 0.0
    contour = source.get("contour")
    if not isinstance(contour, dict):
        return None, 0.0
    value = contour.get("peak_energy_bin")
    if isinstance(value, bool):
        return None, 0.0
    try:
        peak = int(value)
    except (TypeError, ValueError, OverflowError):
        return None, 0.0
    energy = contour.get("energy_contour")
    if not 0 <= peak <= 4 or not isinstance(energy, list) or len(energy) != 5:
        return None, 0.0
    values = [_finite(item, -1.0) for item in energy]
    if any(item < 0.0 for item in values):
        return None, 0.0
    ordered = sorted(values, reverse=True)
    dominance = max(0.0, ordered[0] - ordered[1])
    if abs(values[peak] - ordered[0]) > 1e-6:
        return None, 0.0
    return peak, dominance


def _apply_emphasis_policy(
    *,
    cadence: str,
    word_count: int,
    peak_bin: Any,
) -> tuple[bool, list[str]]:
    """Report an early Russian exclamation peak without cross-language gating."""
    early_emphasis_observed = bool(
        cadence == "firm_terminal"
        and word_count >= 3
        and isinstance(peak_bin, int)
        and peak_bin <= 1
    )
    return (
        early_emphasis_observed,
        ["emphasis_too_early"] if early_emphasis_observed else [],
    )


def evaluate_candidate_cadence(
    candidate: dict[str, Any],
    segment: dict[str, Any],
) -> dict[str, Any]:
    """Apply Russian syntax and exact-fit gates; retain source diagnostics."""
    target_duration = _finite(segment.get("end")) - _finite(segment.get("start"))
    tail_guard = _finite(segment.get("tail_guard"))
    actual_speech_slot = speech_slot_seconds(target_duration, tail_guard)
    candidate["actual_speech_slot"] = actual_speech_slot
    candidate["speech_slot_policy"] = SPEECH_SLOT_POLICY

    ranking_segment = source_prosody_policy.ranking_view(segment)
    result = dict(_legacy_evaluate_candidate_cadence(candidate, ranking_segment))
    cadence = str(
        result.get("cadence")
        or _legacy.classify_cadence(str(segment.get("text") or ""))
    )
    delta = _finite(result.get("ending_delta_semitones"))
    ending_energy = _finite(result.get("ending_energy_delta_db"))
    peak_bin = result.get("peak_energy_bin")
    failures = list(result.get("failures") or [])
    text = str(segment.get("text") or "")
    word_count = len(re.findall(r"\w+", text, flags=re.UNICODE))
    tier = str(segment.get("expression_tier") or "").casefold()

    source_peak, source_peak_dominance = _source_peak_evidence(segment)
    source_late_peak_diagnostic = bool(
        tier in _STRONG_TIERS
        and word_count >= 4
        and source_peak is not None
        and source_peak >= 3
        and source_peak_dominance >= _SOURCE_PEAK_MIN_DOMINANCE
    )

    candidate_duration = _finite(candidate.get("duration"))
    required_fit_tempo = (
        max(1.0, candidate_duration / actual_speech_slot)
        if candidate_duration > 0.0
        else math.inf
    )
    if (
        required_fit_tempo > MAX_TEMPO + 1e-9
        and "fit_tempo_exceeds_hard_limit" not in failures
    ):
        failures.append("fit_tempo_exceeds_hard_limit")

    if cadence == "terminal":
        if (
            delta > -0.55
            and ending_energy > -1.0
            and "terminal_not_resolved" not in failures
        ):
            failures.append("terminal_not_resolved")
    elif cadence == "firm_terminal":
        if delta > 0.35 and "terminal_rises" not in failures:
            failures.append("terminal_rises")
        if (
            delta > -0.75
            and ending_energy > -0.50
            and "firm_terminal_not_resolved" not in failures
        ):
            failures.append("firm_terminal_not_resolved")

    early_emphasis_observed, emphasis_advisories = _apply_emphasis_policy(
        cadence=cadence,
        word_count=word_count,
        peak_bin=peak_bin,
    )

    preferred_bins: list[int] | None = None
    if cadence == "firm_terminal" and word_count >= 3:
        preferred_bins = [2, 3, 4]

    result.update(
        policy=POLICY,
        delivery_policy=DELIVERY_POLICY,
        emphasis_evidence_policy=EMPHASIS_EVIDENCE_POLICY,
        failures=failures,
        hard_ok=not failures,
        russian_word_count=word_count,
        expression_tier=tier,
        source_prosody_role=source_prosody_policy.DIAGNOSTIC_ONLY_ROLE,
        source_peak_energy_bin=source_peak,
        source_peak_dominance=source_peak_dominance,
        source_peak_min_dominance=_SOURCE_PEAK_MIN_DOMINANCE,
        source_late_peak_diagnostic=source_late_peak_diagnostic,
        source_late_peak_expected=False,
        early_emphasis_observed=early_emphasis_observed,
        emphasis_hard_gate_evidence=False,
        emphasis_advisories=emphasis_advisories,
        emphasis_peak_distance=(
            abs(source_peak - peak_bin)
            if isinstance(source_peak, int) and isinstance(peak_bin, int)
            else None
        ),
        preferred_emphasis_bins=preferred_bins,
        expected_emphasis_bins=None,
        actual_speech_slot=actual_speech_slot,
        speech_slot_policy=SPEECH_SLOT_POLICY,
        required_fit_tempo=required_fit_tempo,
        max_fit_tempo=MAX_TEMPO,
    )
    return result


_legacy.POLICY = POLICY
_legacy.evaluate_candidate_cadence = evaluate_candidate_cadence

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {
        "DELIVERY_POLICY",
        "EMPHASIS_EVIDENCE_POLICY",
        "POLICY",
        "_apply_emphasis_policy",
        "evaluate_candidate_cadence",
    }
)
