#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Voice/noise-separated facade with a narrow embedded-terminal artifact path.

The sibling module remains the general detached/immediate broadband detector.
This facade first prevents broadband noise from becoming the "last voice", then
checks the failure shape measured in the Piper render: a short quiet dip, a
35–240 ms high-frequency island, a short harmonic residue, and final decay.
Embedded islands are never auto-trimmed because valid speech follows them; the
whole candidate must be regenerated.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
from typing import Any

import numpy as np

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "direct_tail_artifact.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._direct_tail_artifact_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить late-tail detector: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _legacy
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

POLICY = "late-broadband-tail-v5"
VOICE_CLASSIFICATION_POLICY = "conjunctive-voiced-vs-broadband-tail-v2"
EMBEDDED_POLICY = "quiet-dip-broadband-island-voice-residue-v1"
BRACKETING_POLICY = "analysis-window-overlap-aware-voice-brackets-v1"
FRAME_OVERLAP_TOLERANCE = 2
_legacy_detect = _legacy.detect_late_broadband_tail


def _voice_runs(
    active: np.ndarray,
    zcr: np.ndarray,
    high_ratio: np.ndarray,
    flatness: np.ndarray,
) -> list[tuple[int, int]]:
    active = np.asarray(active, dtype=bool)
    zcr = np.asarray(zcr, dtype=np.float64)
    high_ratio = np.asarray(high_ratio, dtype=np.float64)
    flatness = np.asarray(flatness, dtype=np.float64)
    voice_like = active & (zcr <= 0.23) & (
        (high_ratio <= 0.48) | (flatness <= 0.22)
    )
    return [
        (left, right)
        for left, right in _legacy._runs(voice_like)
        if right - left >= 4
    ]


def _last_sustained_voice(
    active: np.ndarray,
    zcr: np.ndarray,
    high_ratio: np.ndarray,
    flatness: np.ndarray,
) -> tuple[int, int] | None:
    sustained = _voice_runs(active, zcr, high_ratio, flatness)
    return sustained[-1] if sustained else None


def _bracketing_voice_runs(
    voice_runs: list[tuple[int, int]],
    *,
    burst_start: int,
    burst_end: int,
) -> tuple[tuple[int, int], tuple[int, int], int, int] | None:
    """Return voice brackets while tolerating only STFT-window boundary overlap.

    Frame metrics use 20 ms windows with a 10 ms hop. A frame centred at the
    noise-to-voice boundary can therefore be both broadband and voice-like. The
    old strict ``following.start >= burst.end`` rule lost a real island whenever
    that single boundary frame joined the following harmonic run. We accept at
    most two overlapping analysis frames, require distinct runs on both sides,
    and expose the overlap in the report instead of silently widening a gap.
    """
    tolerance = int(FRAME_OVERLAP_TOLERANCE)
    previous_candidates = [
        item
        for item in voice_runs
        if item[0] < burst_start and item[1] <= burst_start + tolerance
    ]
    following_candidates = [
        item
        for item in voice_runs
        if item[1] > burst_end and item[0] >= burst_end - tolerance
    ]
    if not previous_candidates or not following_candidates:
        return None
    previous = previous_candidates[-1]
    following = following_candidates[0]
    if previous == following or previous[0] >= following[0]:
        return None
    overlap_before = max(0, int(previous[1]) - int(burst_start))
    overlap_after = max(0, int(burst_end) - int(following[0]))
    # The policy allows two boundary frames in total, not two on each side.
    # Otherwise a burst could be bracketed by four ambiguous frames while the
    # report still claims to be within the two-frame tolerance.
    if overlap_before + overlap_after > tolerance:
        return None
    return previous, following, overlap_before, overlap_after


def _embedded_terminal_island(samples: Any, sample_rate: int) -> dict[str, Any]:
    audio = _legacy._mono(samples)
    rate = max(1, int(sample_rate))
    duration = len(audio) / rate
    times, levels, zcr, high_ratio, flatness = _legacy._frame_metrics(audio, rate)
    if len(levels) < 16:
        return {
            "policy": POLICY,
            "embedded_policy": EMBEDDED_POLICY,
            "bracketing_policy": BRACKETING_POLICY,
            "suspicious": False,
            "reason": "too_short",
        }

    peak = float(np.percentile(levels, 95))
    active_threshold = max(-52.0, peak - 34.0)
    quiet_threshold = max(-61.0, peak - 47.0)
    active = levels >= active_threshold
    voice_runs = _voice_runs(active, zcr, high_ratio, flatness)
    if len(voice_runs) < 2:
        return {
            "policy": POLICY,
            "embedded_policy": EMBEDDED_POLICY,
            "bracketing_policy": BRACKETING_POLICY,
            "suspicious": False,
            "reason": "insufficient_voice_brackets",
        }

    broadband = active & (zcr >= 0.17) & (high_ratio >= 0.34) & (flatness >= 0.09)
    for burst_start, burst_end in _legacy._runs(broadband):
        burst_seconds = (burst_end - burst_start) * 0.010
        burst_time = float(times[burst_start])
        if not 0.035 <= burst_seconds <= 0.24:
            continue
        if burst_time < duration * 0.60 or duration - burst_time > 0.80:
            continue
        brackets = _bracketing_voice_runs(
            voice_runs,
            burst_start=burst_start,
            burst_end=burst_end,
        )
        if brackets is None:
            continue
        previous, following, overlap_before, overlap_after = brackets
        gap_before = max(0, burst_start - previous[1]) * 0.010
        gap_after = max(0, following[0] - burst_end) * 0.010
        if gap_before > 0.18 or gap_after > 0.18:
            continue

        voice_probe_left = max(previous[0], previous[1] - 14)
        voice_level = float(np.median(levels[voice_probe_left:previous[1]]))
        voice_zcr = float(np.median(zcr[voice_probe_left:previous[1]]))
        voice_high = float(np.median(high_ratio[voice_probe_left:previous[1]]))
        voice_flatness = float(np.median(flatness[voice_probe_left:previous[1]]))
        level = float(np.percentile(levels[burst_start:burst_end], 80))
        median_zcr = float(np.median(zcr[burst_start:burst_end]))
        median_high = float(np.median(high_ratio[burst_start:burst_end]))
        median_flatness = float(np.median(flatness[burst_start:burst_end]))
        spectral_jump = _legacy._spectral_jump(
            burst_zcr=median_zcr,
            burst_high=median_high,
            burst_flatness=median_flatness,
            voice_zcr=voice_zcr,
            voice_high=voice_high,
            voice_flatness=voice_flatness,
        )

        # Only the immediately preceding 60 ms describes the measured quiet dip.
        # A longer median is dominated by the previous word and hides the valley.
        before = levels[max(0, burst_start - 6):burst_start]
        if not len(before):
            continue
        pre_quiet_level = float(np.percentile(before, 25))
        valley_rebound = level - pre_quiet_level
        low_pre_fraction = float(np.mean(before <= level - 8.0))
        after = levels[following[1]:]
        if len(after):
            quiet_fraction = float(np.mean(after <= quiet_threshold))
            active_after_seconds = float(np.sum(after > active_threshold)) * 0.010
        else:
            quiet_fraction = 1.0
            active_after_seconds = 0.0
        followed_by_decay = bool(
            quiet_fraction >= 0.45 and active_after_seconds <= 0.20
        )
        residue_start = max(int(following[0]), int(burst_end))
        terminal_residue_seconds = max(0, following[1] - residue_start) * 0.010
        suspicious = bool(
            spectral_jump >= 0.70
            and median_zcr >= 0.22
            and median_high >= 0.50
            and valley_rebound >= 7.0
            and low_pre_fraction >= 0.20
            and 0.04 <= terminal_residue_seconds <= 0.34
            and followed_by_decay
        )
        if not suspicious:
            continue
        return {
            "policy": POLICY,
            "base_policy": _legacy.POLICY,
            "voice_classification_policy": VOICE_CLASSIFICATION_POLICY,
            "embedded_policy": EMBEDDED_POLICY,
            "bracketing_policy": BRACKETING_POLICY,
            "frame_overlap_tolerance": FRAME_OVERLAP_TOLERANCE,
            "suspicious": True,
            "repairable": False,
            "artifact_type": "embedded_terminal_broadband_island",
            "detection_path": "quiet_dip_broadband_island_then_voice_residue",
            "trim_time": None,
            "previous_voice_end": float(times[max(previous[0], previous[1] - 1)] + 0.01),
            "burst_start": burst_time,
            "burst_end": min(duration, float(times[max(burst_start, burst_end - 1)] + 0.01)),
            "following_voice_start": float(times[following[0]]),
            "following_voice_end": float(times[max(following[0], following[1] - 1)] + 0.01),
            "terminal_residue_seconds": terminal_residue_seconds,
            "burst_seconds": burst_seconds,
            "gap_before_seconds": gap_before,
            "gap_after_seconds": gap_after,
            "analysis_overlap_before_frames": overlap_before,
            "analysis_overlap_after_frames": overlap_after,
            "burst_level_db": level,
            "voice_level_db": voice_level,
            "pre_quiet_level_db": pre_quiet_level,
            "valley_rebound_db": valley_rebound,
            "low_pre_fraction": low_pre_fraction,
            "burst_median_zcr": median_zcr,
            "burst_high_zcr": median_zcr,
            "burst_high_frequency_ratio": median_high,
            "burst_spectral_flatness": median_flatness,
            "spectral_jump_score": spectral_jump,
            "quiet_fraction_after": quiet_fraction,
            "active_after_seconds": active_after_seconds,
        }

    return {
        "policy": POLICY,
        "base_policy": _legacy.POLICY,
        "voice_classification_policy": VOICE_CLASSIFICATION_POLICY,
        "embedded_policy": EMBEDDED_POLICY,
        "bracketing_policy": BRACKETING_POLICY,
        "frame_overlap_tolerance": FRAME_OVERLAP_TOLERANCE,
        "suspicious": False,
        "reason": "no_embedded_terminal_broadband_island",
    }


def detect_late_broadband_tail(samples: Any, sample_rate: int) -> dict[str, Any]:
    base = dict(_legacy_detect(samples, sample_rate))
    if base.get("suspicious"):
        base.setdefault("facade_policy", POLICY)
        base.setdefault("voice_classification_policy", VOICE_CLASSIFICATION_POLICY)
        base.setdefault("bracketing_policy", BRACKETING_POLICY)
        if "burst_high_zcr" not in base and "burst_median_zcr" in base:
            base["burst_high_zcr"] = base["burst_median_zcr"]
        if "trailing_quiet_seconds" not in base and base.get("burst_end") is not None:
            base["trailing_quiet_seconds"] = max(
                0.0,
                len(_legacy._mono(samples)) / max(1, int(sample_rate)) - float(base["burst_end"]),
            )
        return base
    embedded = _embedded_terminal_island(samples, sample_rate)
    if embedded.get("suspicious"):
        embedded["base_detector_result"] = base
        return embedded
    base["facade_policy"] = POLICY
    base["voice_classification_policy"] = VOICE_CLASSIFICATION_POLICY
    base["bracketing_policy"] = BRACKETING_POLICY
    base["embedded_detector_result"] = embedded
    return base


_legacy._last_sustained_voice = _last_sustained_voice
_legacy.detect_late_broadband_tail = detect_late_broadband_tail


class _WriteThroughModule(types.ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        types.ModuleType.__setattr__(self, name, value)
        if name in {"_legacy", "__class__"} or name.startswith("__"):
            return
        legacy = types.ModuleType.__getattribute__(self, "_legacy")
        if hasattr(legacy, name):
            setattr(legacy, name, value)

    def __getattr__(self, name: str):
        legacy = types.ModuleType.__getattribute__(self, "_legacy")
        return getattr(legacy, name)


_module = sys.modules[__name__]
_module.__class__ = _WriteThroughModule

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {
        "BRACKETING_POLICY",
        "EMBEDDED_POLICY",
        "FRAME_OVERLAP_TOLERANCE",
        "POLICY",
        "VOICE_CLASSIFICATION_POLICY",
        "_bracketing_voice_runs",
        "_embedded_terminal_island",
        "_last_sustained_voice",
        "detect_late_broadband_tail",
    }
)
