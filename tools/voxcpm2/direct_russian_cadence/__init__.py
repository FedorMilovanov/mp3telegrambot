#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stricter Russian sentence-ending and emphasis contract.

The sibling module performs deterministic F0 and energy analysis.  This facade
keeps those measurements and tightens only cases that were audibly wrong in the
real Dub Studio sample: declaratives that sound unfinished, exclamations that
rise at the end, and multiword emotional peaks that occur before the thought has
been delivered.
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

POLICY = "russian-cadence-contour-v2"
DELIVERY_POLICY = "russian-ending-and-emphasis-hard-gate-v1"
_legacy_evaluate_candidate_cadence = _legacy.evaluate_candidate_cadence


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def evaluate_candidate_cadence(
    candidate: dict[str, Any],
    segment: dict[str, Any],
) -> dict[str, Any]:
    """Apply Russian-syntax hard gates to the measured acoustic contour."""
    result = dict(_legacy_evaluate_candidate_cadence(candidate, segment))
    cadence = str(result.get("cadence") or _legacy.classify_cadence(str(segment.get("text") or "")))
    delta = _finite(result.get("ending_delta_semitones"))
    ending_energy = _finite(result.get("ending_energy_delta_db"))
    peak_bin = result.get("peak_energy_bin")
    failures = list(result.get("failures") or [])
    text = str(segment.get("text") or "")
    word_count = len(re.findall(r"\w+", text, flags=re.UNICODE))

    if cadence == "terminal":
        # A period may be nearly level only when energy clearly releases.  A
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

    result.update(
        policy=POLICY,
        delivery_policy=DELIVERY_POLICY,
        failures=failures,
        hard_ok=not failures,
        russian_word_count=word_count,
        expected_emphasis_bins=(
            [2, 3, 4] if cadence == "firm_terminal" and word_count >= 3 else None
        ),
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
