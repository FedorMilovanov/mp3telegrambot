#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fit-aware facade for direct VoxCPM2 candidate analysis.

The established acoustic and identity diagnostics remain in the sibling
``direct_max_quality_analysis.py`` module. This package adds one missing hard
contract: a candidate is not acceptable when fitting it into its exact SRT
speech slot would require tempo above the declared natural-speech limit.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from typing import Any

from tools.voxcpm2.direct_max_quality_io import MAX_TEMPO

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "direct_max_quality_analysis.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._direct_max_quality_analysis_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить direct candidate analysis: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

FIT_TEMPO_POLICY = "candidate-fit-tempo-hard-gate-v2"
_legacy_candidate_hard_ok = _legacy.candidate_hard_ok


def required_tempo(candidate: dict[str, Any], speech_slot: float) -> float:
    """Return the exact atempo factor the fitter would need for this candidate.

    Cadence analysis knows the original segment and records ``actual_speech_slot``.
    Prefer that value over a legacy caller's provisional slot so short SRT cues
    can never pass against a fabricated one-second window.
    """
    try:
        duration = float(candidate.get("duration"))
        slot_value = candidate.get("actual_speech_slot", speech_slot)
        slot = float(slot_value)
    except (TypeError, ValueError, OverflowError):
        return math.inf
    if not math.isfinite(duration) or not math.isfinite(slot) or duration <= 0.0 or slot <= 0.0:
        return math.inf
    return max(1.0, duration / slot)


def candidate_hard_ok(candidate: dict[str, Any], speech_slot: float) -> bool:
    """Reject overlong speech before selection, not after expensive fitting."""
    if not _legacy_candidate_hard_ok(candidate, speech_slot):
        return False
    tempo = required_tempo(candidate, speech_slot)
    candidate["required_tempo"] = tempo
    candidate["fit_tempo_policy"] = FIT_TEMPO_POLICY
    return bool(math.isfinite(tempo) and tempo <= float(MAX_TEMPO) + 1e-9)


_legacy.candidate_hard_ok = candidate_hard_ok

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {
        "FIT_TEMPO_POLICY",
        "candidate_hard_ok",
        "required_tempo",
    }
)
