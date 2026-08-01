#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Robust typical-voice facade for continuous reference selection.

The sibling implementation keeps decoding, continuous-first construction,
transactional reports and the validated fallback.  This facade retires the
absolute-F0 score that systematically preferred the lowest/bassiest clean window.
All valid continuous windows are collected first and ranked by quality plus
log-frequency distance from the speaker's robust median window.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import types
from typing import Any

import numpy as np

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "continuous_reference_policy.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._continuous_reference_policy_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить continuous reference policy: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _legacy
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_window_score = _legacy._window_score

POLICY = "continuous-clean-reference-v3"
SELECTION_POLICY = "robust-typical-f0-continuous-window-v1"
RETIRED_SELECTION_POLICY = "absolute-f0-low-bias-retired-v1"
MEDIAN_F0_WEIGHT = 55.0
P90_F0_WEIGHT = 30.0
VOICED_TARGET = 0.58
VOICED_WEIGHT = 28.0


def _positive(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result > 1.0 else None


def _log_distance(value: float | None, reference: float | None) -> float:
    if value is None or reference is None:
        return 4.0
    return abs(math.log2(value / reference))


def _quality_score(stats: dict[str, Any]) -> float:
    active = float(stats.get("active_ratio") or 0.0)
    gap = float(stats.get("max_internal_gap") or 0.0)
    voiced = float(stats.get("voiced_ratio") or 0.0)
    return (
        gap * 75.0
        + abs(active - 0.74) * 50.0
        + abs(voiced - VOICED_TARGET) * VOICED_WEIGHT
    )


def _candidate_windows(
    audio: np.ndarray,
    sample_rate: int,
    intervals: list[tuple[float, float]],
    *,
    target_seconds: float,
) -> list[dict[str, Any]]:
    target_value = float(target_seconds)
    if not math.isfinite(target_value):
        raise RuntimeError("Некорректная длительность voice reference.")
    target = max(_legacy.MIN_SECONDS, min(target_value, _legacy.MAX_SECONDS))
    candidates: list[dict[str, Any]] = []
    for run_start, run_end in _legacy._merged_runs(intervals):
        run_length = run_end - run_start
        if run_length < _legacy.MIN_SECONDS:
            continue
        window = min(target, run_length)
        travel = max(0.0, run_length - window)
        count = max(1, int(math.ceil(travel / 0.50)))
        starts = [run_start + travel * index / count for index in range(count + 1)]
        for start in starts:
            end = min(run_end, start + window)
            left = max(0, int(start * sample_rate))
            right = min(len(audio), int(end * sample_rate))
            clip = np.asarray(audio[left:right], dtype=np.float32)
            if len(clip) < int(_legacy.MIN_SECONDS * sample_rate):
                continue
            _retired_score, stats = _window_score(clip, sample_rate)
            if not _legacy._usable_stats(stats):
                continue
            candidates.append(
                {
                    "start": float(start),
                    "end": float(end),
                    "samples": clip,
                    "stats": stats,
                    "base_quality_score": _quality_score(stats),
                }
            )

    median_values = [
        value
        for value in (_positive(item["stats"].get("f0_median")) for item in candidates)
        if value is not None
    ]
    p90_values = [
        value
        for value in (_positive(item["stats"].get("f0_p90")) for item in candidates)
        if value is not None
    ]
    robust_median = float(np.median(median_values)) if median_values else None
    robust_p90 = float(np.median(p90_values)) if p90_values else None

    for item in candidates:
        stats = item["stats"]
        median_distance = _log_distance(_positive(stats.get("f0_median")), robust_median)
        p90_distance = _log_distance(_positive(stats.get("f0_p90")), robust_p90)
        item["score"] = float(
            item["base_quality_score"]
            + median_distance * MEDIAN_F0_WEIGHT
            + p90_distance * P90_F0_WEIGHT
        )
        item["selection_policy"] = SELECTION_POLICY
        item["robust_f0_median"] = robust_median
        item["robust_f0_p90"] = robust_p90
        item["f0_median_log_distance"] = median_distance
        item["f0_p90_log_distance"] = p90_distance
    return candidates


_legacy.POLICY = POLICY
_legacy._candidate_windows = _candidate_windows


class _WriteThroughModule(types.ModuleType):
    def __setattr__(self, name: str, value: Any) -> None:
        types.ModuleType.__setattr__(self, name, value)
        if name in {"_legacy", "__class__"} or name.startswith("__"):
            return
        legacy = types.ModuleType.__getattribute__(self, "_legacy")
        if hasattr(legacy, name):
            setattr(legacy, name, value)

    def __getattr__(self, name: str) -> Any:
        legacy = types.ModuleType.__getattribute__(self, "_legacy")
        return getattr(legacy, name)


_module = sys.modules[__name__]
_module.__class__ = _WriteThroughModule

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {
        "MEDIAN_F0_WEIGHT",
        "P90_F0_WEIGHT",
        "POLICY",
        "RETIRED_SELECTION_POLICY",
        "SELECTION_POLICY",
        "VOICED_TARGET",
        "VOICED_WEIGHT",
        "_candidate_windows",
        "_quality_score",
    }
)
