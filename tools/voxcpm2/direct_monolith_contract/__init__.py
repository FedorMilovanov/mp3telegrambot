#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resume-safe facade for the monolithic candidate identity contract.

The sibling module keeps the core acoustic implementation. This facade resolves
the immediate previous identity from durable checkpoints and detects a detached
start chirp against the first sustained voice-like region rather than treating
the chirp's own energy as speech.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import types
from typing import Any

import numpy as np

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
START_VOICE_POLICY = "first-sustained-voice-after-detached-burst-v2"
_legacy_evaluate_candidate = _legacy.evaluate_candidate


def _mono(samples: Any) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.reshape(-1)


def _start_artifact(samples: Any, sample_rate: int) -> dict[str, Any]:
    """Find detached activity before the first sustained voice-like nucleus."""
    audio = _mono(samples)
    rate = max(1, int(sample_rate))
    if len(audio) < int(rate * 0.18):
        return {
            "policy": _legacy.START_ARTIFACT_POLICY,
            "voice_policy": START_VOICE_POLICY,
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
            "suspicious": False,
            "reason": "no_frames",
        }
    rms: list[float] = []
    zcr: list[float] = []
    step: list[float] = []
    periodicity: list[float] = []
    lag_lo = max(2, int(rate / 320))
    lag_hi = min(frame - 3, int(rate / 65))
    window = np.hanning(frame)
    for start in starts:
        chunk = audio[start : start + frame].astype(np.float64)
        rms.append(math.sqrt(float(np.mean(chunk**2)) + 1e-12))
        zcr.append(
            float(np.mean(np.signbit(chunk[1:]) != np.signbit(chunk[:-1])))
            if len(chunk) > 1
            else 0.0
        )
        step.append(float(np.max(np.abs(np.diff(chunk)))) if len(chunk) > 1 else 0.0)
        work = chunk - float(np.mean(chunk))
        work *= window
        correlation = np.correlate(work, work, "full")[frame - 1:]
        if correlation[0] <= 1e-10 or lag_hi <= lag_lo:
            periodicity.append(0.0)
        else:
            search = correlation[lag_lo:lag_hi + 1]
            lag = lag_lo + int(np.argmax(search))
            periodicity.append(float(correlation[lag] / correlation[0]))

    levels = 20.0 * np.log10(np.maximum(np.asarray(rms), 1e-9))
    zcr_array = np.asarray(zcr, dtype=np.float64)
    step_array = np.asarray(step, dtype=np.float64)
    periodicity_array = np.asarray(periodicity, dtype=np.float64)
    peak = float(np.percentile(levels, 95))
    active = levels >= max(-49.0, peak - 31.0)
    # A 10 ms frame can contain less than two periods of a low male voice, so
    # low ZCR is primary evidence; autocorrelation is only an extra path.
    voice_like = active & (
        (zcr_array <= 0.16)
        | (periodicity_array >= 0.18)
    )

    sustained_start: int | None = None
    search_limit = max(0, min(len(active), int(0.75 / 0.005)))
    for index in range(search_limit):
        right = min(len(active), index + 10)
        if right - index < 7:
            continue
        if (
            float(np.mean(active[index:right])) >= 0.70
            and float(np.mean(voice_like[index:right])) >= 0.50
        ):
            sustained_start = index
            break
    if sustained_start is None or sustained_start < 3:
        return {
            "policy": _legacy.START_ARTIFACT_POLICY,
            "voice_policy": START_VOICE_POLICY,
            "suspicious": False,
            "reason": "no_detached_prefix",
        }

    pre = active[:sustained_start]
    ids = np.flatnonzero(pre)
    if not len(ids):
        return {
            "policy": _legacy.START_ARTIFACT_POLICY,
            "voice_policy": START_VOICE_POLICY,
            "suspicious": False,
            "reason": "quiet_prefix",
        }
    left, right = int(ids[0]), int(ids[-1] + 1)
    duration = (right - left) * 0.005
    gap = max(0.0, (sustained_start - right) * 0.005)
    burst_level = float(np.percentile(levels[left:right], 80))
    speech_right = min(len(levels), sustained_start + 30)
    speech_level = float(np.median(levels[sustained_start:speech_right]))
    median_zcr = float(np.median(zcr_array[left:right]))
    median_periodicity = float(np.median(periodicity_array[left:right]))
    max_step = float(np.max(step_array[left:right]))
    suspicious = bool(
        0.008 <= duration <= 0.130
        and 0.010 <= gap <= 0.240
        and burst_level >= speech_level - 20.0
        and (
            median_zcr >= 0.20
            or median_periodicity < 0.16
            or max_step >= 0.075
            or burst_level >= speech_level - 4.0
        )
    )
    return {
        "policy": _legacy.START_ARTIFACT_POLICY,
        "voice_policy": START_VOICE_POLICY,
        "suspicious": suspicious,
        "artifact_type": "detached_reference_leak" if suspicious else "",
        "burst_start_seconds": left * 0.005,
        "burst_duration_seconds": duration,
        "gap_to_speech_seconds": gap,
        "burst_level_db": burst_level,
        "speech_level_db": speech_level,
        "burst_median_zcr": median_zcr,
        "burst_median_periodicity": median_periodicity,
        "burst_max_step": max_step,
        "sustained_voice_start_seconds": sustained_start * 0.005,
    }


def _work_dir(candidate: dict[str, Any]) -> Path:
    path = Path(str(candidate.get("path") or "."))
    return path.parent.parent if path.parent.name == "attempts" else path.parent


def evaluate_candidate(candidate: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    segment_id = int(segment.get("id") or 0)
    _legacy.set_current_segment_id(segment_id)
    _legacy._PREVIOUS_IDENTITY = (
        _legacy._load_previous_checkpoint(_work_dir(candidate), segment_id)
        if segment_id > 1
        else None
    )
    result = dict(_legacy_evaluate_candidate(candidate, segment))
    result["resume_policy"] = RESUME_POLICY
    result["start_voice_policy"] = START_VOICE_POLICY
    return result


# The sibling evaluate function resolves this name in its own module globals.
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
    | {"RESUME_POLICY", "START_VOICE_POLICY", "_start_artifact", "evaluate_candidate"}
)
