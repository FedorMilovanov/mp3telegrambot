#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stricter Russian sentence-ending and emphasis contract.

The sibling module performs deterministic F0 and energy analysis. This facade
keeps those measurements and rejects the audible defects found in the real Dub
Studio sample: unfinished declaratives, rising exclamations, and strong emotion
that bursts before a source-guided thought reaches its actual emphasis region.
"""
from __future__ import annotations

import importlib.util
import math
import re
from pathlib import Path
from typing import Any

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "direct_russian_cadence.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._direct_russian_cadence_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить Russian cadence analysis: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

POLICY = "russian-cadence-contour-v3"
DELIVERY_POLICY = "russian-ending-and-source-emphasis-hard-gate-v2"
_STRONG_TIERS = {"emphatic", "passionate"}
_legacy_evaluate_candidate_cadence = _legacy.evaluate_candidate_cadence


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _source_peak_bin(segment: dict[str, Any]) -> int | None:
    source = segment.get("source_prosody")
    if not isinstance(source, dict):
        return None
    contour = source.get("contour")
    if not isinstance(contour, dict):
        return None
    value = contour.get("peak_energy_bin")
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if 0 <= result <= 4 else None


def evaluate_candidate_cadence(
    candidate: dict[str, Any],
    segment: dict[str, Any],
) -> dict[str, Any]:
    """Apply Russian-syntax and source-guided emphasis hard gates."""
    result = dict(_legacy_evaluate_candidate_cadence(candidate, segment))
    cadence = str(result.get("cadence") or _legacy.classify_cadence(str(segment.get("text") or "")))
    delta = _finite(result.get("ending_delta_semitones"))
    ending_energy = _finite(result.get("ending_energy_delta_db"))
    peak_bin = result.get("peak_energy_bin")
    failures = list(result.get("failures") or [])
    text = str(segment.get("text") or "")
    word_count = len(re.findall(r"\w+", text, flags=re.UNICODE))
    tier = str(segment.get("expression_tier") or "")
    source_peak = _source_peak_bin(segment)
    source_late_peak_expected = bool(
        tier in _STRONG_TIERS
        and word_count >= 3
        and source_peak is not None
        and source_peak >= 2
    )

    if cadence == "terminal":
        # A period may be nearly level only when energy clearly releases. A
        # shallow pitch ending with no release is the exact "будет продолжение"
        # defect heard in the supplied sample.
        if delta > -0.55 and ending_energy > -1.0:
            if "terminal_not_resolved" not in failures:
                failures.append("terminal_not_resolved")
    elif cadence == "firm_terminal":
        if delta > 0.35 and "terminal_rises" not in failures:
            failures.append("terminal_rises")
        if delta > -0.75 and ending_energy > -0.50:
            if "firm_terminal_not_resolved" not in failures:
                failures.append("firm_terminal_not_resolved")
        if word_count >= 3 and isinstance(peak_bin, int) and peak_bin <= 1:
            if "emphasis_too_early" not in failures:
                failures.append("emphasis_too_early")

    # Do not copy English word timing blindly: Russian word order can differ.
    # The hard rule is deliberately one-way and narrow. When a genuinely strong
    # source delivery builds toward the middle/end, a Russian candidate whose
    # maximum energy already occurs in the first 40% is audibly misplaced.
    if source_late_peak_expected and isinstance(peak_bin, int) and peak_bin <= 1:
        if "source_emphasis_misplaced_early" not in failures:
            failures.append("source_emphasis_misplaced_early")

    expected_bins: list[int] | None = None
    if cadence == "firm_terminal" and word_count >= 3:
        expected_bins = [2, 3, 4]
    elif source_late_peak_expected:
        expected_bins = [2, 3, 4]

    result.update(
        policy=POLICY,
        delivery_policy=DELIVERY_POLICY,
        failures=failures,
        hard_ok=not failures,
        russian_word_count=word_count,
        expression_tier=tier,
        source_peak_energy_bin=source_peak,
        source_late_peak_expected=source_late_peak_expected,
        expected_emphasis_bins=expected_bins,
    )
    return result


_legacy.POLICY = POLICY
_legacy.evaluate_candidate_cadence = evaluate_candidate_cadence

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {
        "DELIVERY_POLICY",
        "POLICY",
        "evaluate_candidate_cadence",
    }
)
