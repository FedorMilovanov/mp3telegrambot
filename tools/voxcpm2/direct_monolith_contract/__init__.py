#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resume-safe facade for the monolithic candidate identity contract.

The sibling module keeps the core acoustic implementation. This facade resolves
the immediate previous identity from durable checkpoints, detects a detached
start chirp as a short active island before sustained voice, and replaces a
blind adjacent-pitch limit with source-relative transition evidence.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import types
from typing import Any

import numpy as np

from tools.voxcpm2 import direct_source_relative_continuity

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "direct_monolith_contract.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._direct_monolith_contract_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить monolithic candidate contract: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

RESUME_POLICY = "nearest-accepted-checkpoint-identity-v1"
# Public compatibility name consumed by the established health contract.
START_VOICE_POLICY = "first-sustained-voice-after-detached-burst-v2"
START_VOICE_IMPLEMENTATION_POLICY = "short-island-before-sustained-voice-v3"
SOURCE_RELATIVE_POLICY = direct_source_relative_continuity.POLICY
_legacy_evaluate_candidate = _legacy.evaluate_candidate


def _mono(samples: Any) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.reshape(-1)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    left: int | None = None
    for index, value in enumerate(mask.tolist()):
        if value and left is None:
            left = index
        if left is not None and (not value or index == len(mask) - 1):
            right = index if not value else index + 1
            if right > left:
                result.append((left, right))
            left = None
    return result


def _start_artifact(samples: Any, sample_rate: int) -> dict[str, Any]:
    """Reject a short noisy island detached from the first sustained voice run."""
    audio = _mono(samples)
    rate = max(1, int(sample_rate))
    if len(audio) < int(rate * 0.18):
        return {
            "policy": _legacy.START_ARTIFACT_POLICY,
            "voice_policy": START_VOICE_POLICY,
            "implementation_policy": START_VOICE_IMPLEMENTATION_POLICY,
            "suspicious": False,
            "reason": "too_short",
        }

    frame = max(64, int(rate * 0.010))
    hop = max(32, int(rate * 0.005))
    starts = np.arange(0, max(0, len(audio) - frame + 1), hop, dtype=np.int64)
    if not len(starts):
        return {
            "policy": _legacy.START_ARTIFACT_POLICY,
            "voice_policy": START_VOICE_POLICY,
            "implementation_policy": START_VOICE_IMPLEMENTATION_POLICY,
            "suspicious": False,
            "reason": "no_frames",
        }

    rms: list[float] = []
    zcr: list[float] = []
    step: list[float] = []
    periodicity: list[float] = []
    window = np.hanning(frame)
    lag_lo = max(2, int(rate / 340))
    lag_hi = min(frame - 3, int(rate / 65))
    for start in starts:
        chunk = audio[start:start + frame].astype(np.float64)
        rms.append(math.sqrt(float(np.mean(chunk**2)) + 1e-12))
        zcr.append(
            float(np.mean(np.signbit(chunk[1:]) != np.signbit(chunk[:-1])))
            if len(chunk) > 1
            else 0.0
        )
        step.append(float(np.max(np.abs(np.diff(chunk)))) if len(chunk) > 1 else 0.0)
        work = (chunk - float(np.mean(chunk))) * window
        correlation = np.correlate(work, work, "full")[frame - 1:]
        if correlation[0] <= 1e-10 or lag_hi <= lag_lo:
            periodicity.append(0.0)
        else:
            search = correlation[lag_lo:lag_hi + 1]
            periodicity.append(float(np.max(search) / correlation[0]))

    levels = 20.0 * np.log10(np.maximum(np.asarray(rms), 1e-9))
    zcr_array = np.asarray(zcr, dtype=np.float64)
    step_array = np.asarray(step, dtype=np.float64)
    periodicity_array = np.asarray(periodicity, dtype=np.float64)
    peak = float(np.percentile(levels, 95))
    active = levels >= max(-49.0, peak - 31.0)
    active_runs = _runs(active)

    sustained: tuple[int, int] | None = None
    search_frame_limit = int(0.80 / 0.005)
    for left, right in active_runs:
        if left >= search_frame_limit:
            break
        duration = (right - left) * 0.005
        if duration < 0.070:
            continue
        run_zcr = float(np.median(zcr_array[left:right]))
        run_periodicity = float(np.median(periodicity_array[left:right]))
        if run_zcr <= 0.20 or run_periodicity >= 0.16:
            sustained = (left, right)
            break
    if sustained is None:
        return {
            "policy": _legacy.START_ARTIFACT_POLICY,
            "voice_policy": START_VOICE_POLICY,
            "implementation_policy": START_VOICE_IMPLEMENTATION_POLICY,
            "suspicious": False,
            "reason": "no_sustained_voice_run",
        }

    voice_left, voice_right = sustained
    candidates = [
        (left, right)
        for left, right in active_runs
        if right <= voice_left and right > left
    ]
    if not candidates:
        return {
            "policy": _legacy.START_ARTIFACT_POLICY,
            "voice_policy": START_VOICE_POLICY,
            "implementation_policy": START_VOICE_IMPLEMENTATION_POLICY,
            "suspicious": False,
            "reason": "no_detached_active_island",
        }

    speech_level = float(np.median(levels[voice_left:min(len(levels), voice_left + 30)]))
    evidence: list[dict[str, Any]] = []
    for left, right in candidates:
        duration = (right - left) * 0.005
        gap = max(0.0, (voice_left - right) * 0.005)
        burst_level = float(np.percentile(levels[left:right], 80))
        median_zcr = float(np.median(zcr_array[left:right]))
        median_periodicity = float(np.median(periodicity_array[left:right]))
        max_step = float(np.max(step_array[left:right]))
        suspicious = bool(
            0.008 <= duration <= 0.130
            and 0.010 <= gap <= 0.240
            and burst_level >= speech_level - 20.0
            and (
                median_zcr >= 0.20
                or median_periodicity < 0.14
                or max_step >= 0.075
            )
        )
        item = {
            "burst_start_seconds": left * 0.005,
            "burst_duration_seconds": duration,
            "gap_to_speech_seconds": gap,
            "burst_level_db": burst_level,
            "speech_level_db": speech_level,
            "burst_median_zcr": median_zcr,
            "burst_median_periodicity": median_periodicity,
            "burst_max_step": max_step,
            "suspicious": suspicious,
        }
        evidence.append(item)
        if suspicious:
            return {
                "policy": _legacy.START_ARTIFACT_POLICY,
                "voice_policy": START_VOICE_POLICY,
                "implementation_policy": START_VOICE_IMPLEMENTATION_POLICY,
                "suspicious": True,
                "artifact_type": "detached_reference_leak",
                "sustained_voice_start_seconds": voice_left * 0.005,
                "sustained_voice_duration_seconds": (voice_right - voice_left) * 0.005,
                "candidate_islands": evidence,
                **item,
            }

    return {
        "policy": _legacy.START_ARTIFACT_POLICY,
        "voice_policy": START_VOICE_POLICY,
        "implementation_policy": START_VOICE_IMPLEMENTATION_POLICY,
        "suspicious": False,
        "reason": "prefix_islands_not_artifacts",
        "sustained_voice_start_seconds": voice_left * 0.005,
        "candidate_islands": evidence,
    }


def _work_dir(candidate: dict[str, Any]) -> Path:
    path = Path(str(candidate.get("path") or "."))
    return path.parent.parent if path.parent.name == "attempts" else path.parent


def _previous_segment(segment_id: int) -> dict[str, Any] | None:
    for previous_id in range(int(segment_id) - 1, 0, -1):
        value = _legacy._SEGMENTS.get(previous_id)
        if isinstance(value, dict):
            return dict(value)
    return None


def _apply_source_relative_transition(
    result: dict[str, Any],
    candidate: dict[str, Any],
    segment: dict[str, Any],
) -> dict[str, Any]:
    previous_identity = result.get("previous_identity")
    identity = result.get("identity")
    evidence = direct_source_relative_continuity.evaluate_transition(
        current_identity=identity if isinstance(identity, dict) else {},
        previous_identity=(
            previous_identity if isinstance(previous_identity, dict) else None
        ),
        current_segment=segment,
        previous_segment=_previous_segment(int(segment.get("id") or 0)),
    )

    failures = [str(value) for value in result.get("failures") or []]
    # The core absolute gate is only an emergency fallback. When valid source
    # windows explicitly support the generated movement, source-relative limits
    # replace those two blind adjacent-F0 failures; timbre and anchor gates stay.
    if evidence.get("source_available") and evidence.get("hard_ok") is True:
        failures = [
            value
            for value in failures
            if value not in {"adjacent_f0_median_jump", "adjacent_f0_p90_jump"}
        ]
    for failure in evidence.get("failures") or []:
        value = str(failure)
        if value not in failures:
            failures.append(value)

    result["failures"] = failures
    result["hard_ok"] = not failures
    result["source_relative_transition"] = evidence
    result["source_relative_policy"] = SOURCE_RELATIVE_POLICY
    result["penalty"] = min(
        float(_legacy.MAX_MONOLITH_PENALTY),
        max(0.0, float(result.get("penalty") or 0.0) + float(evidence.get("penalty") or 0.0)),
    )
    candidate["monolith_identity"] = result
    return result


def evaluate_candidate(candidate: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    segment_id = int(segment.get("id") or 0)
    _legacy.set_current_segment_id(segment_id)
    _legacy._PREVIOUS_IDENTITY = (
        _legacy._load_previous_checkpoint(_work_dir(candidate), segment_id)
        if segment_id > 1
        else None
    )
    result = dict(_legacy_evaluate_candidate(candidate, segment))
    result = _apply_source_relative_transition(result, candidate, segment)
    result["resume_policy"] = RESUME_POLICY
    result["start_voice_policy"] = START_VOICE_POLICY
    result["start_voice_implementation_policy"] = START_VOICE_IMPLEMENTATION_POLICY
    return result


_legacy._start_artifact = _start_artifact
_legacy.evaluate_candidate = evaluate_candidate


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
        "RESUME_POLICY",
        "SOURCE_RELATIVE_POLICY",
        "START_VOICE_IMPLEMENTATION_POLICY",
        "START_VOICE_POLICY",
        "_apply_source_relative_transition",
        "_start_artifact",
        "evaluate_candidate",
    }
)
