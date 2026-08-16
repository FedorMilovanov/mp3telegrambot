#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic detection of detached or embedded broadband synthesis tails.

VoxCPM artifacts do not always occur strictly after the last voice frame. Some
outputs contain a quiet dip, a short broadband island, and then a brief harmonic
residue. This detector therefore supports three evidence paths: quiet-valley
rebound after speech, immediate voice-to-broadband transition, and an embedded
terminal broadband island bracketed by short voice runs. Embedded islands are
fail-closed and never auto-trimmed because valid speech may follow them.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

POLICY = "late-broadband-tail-v4"


def _mono(samples: Any) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.reshape(-1)


def _frame_metrics(
    audio: np.ndarray,
    rate: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    frame = max(128, int(rate * 0.020))
    hop = max(64, int(rate * 0.010))
    starts = np.arange(0, max(0, len(audio) - frame + 1), hop, dtype=np.int64)
    if not len(starts):
        empty = np.asarray([], dtype=np.float64)
        return empty, empty, empty, empty, empty
    window = np.hanning(frame)
    frequencies = np.fft.rfftfreq(frame, d=1.0 / rate)
    audible_mask = (frequencies >= 80.0) & (
        frequencies <= min(7600.0, rate * 0.48)
    )
    # The numerator must be a subset of the audible denominator. The previous
    # mask included ultrasonic bins at 48 kHz and could produce ratios > 1.0.
    high_mask = audible_mask & (frequencies >= min(3200.0, rate * 0.30))
    levels: list[float] = []
    zcr: list[float] = []
    high_ratio: list[float] = []
    flatness: list[float] = []
    for pos in starts:
        chunk = audio[pos:pos + frame].astype(np.float64)
        rms = math.sqrt(float(np.mean(chunk**2)) + 1e-12)
        levels.append(20.0 * math.log10(max(rms, 1e-9)))
        zcr.append(
            float(np.mean(np.signbit(chunk[1:]) != np.signbit(chunk[:-1])))
            if len(chunk) > 1
            else 0.0
        )
        work = chunk - float(np.mean(chunk))
        power = np.square(np.abs(np.fft.rfft(work * window))) + 1e-15
        audible = power[audible_mask]
        total = float(np.sum(audible)) if len(audible) else 0.0
        high = float(np.sum(power[high_mask])) if np.any(high_mask) else 0.0
        high_ratio.append(high / max(total, 1e-15))
        if len(audible):
            flatness.append(
                float(
                    np.exp(np.mean(np.log(audible)))
                    / max(np.mean(audible), 1e-15)
                )
            )
        else:
            flatness.append(0.0)
    times = (starts + frame / 2) / rate
    return (
        times,
        np.asarray(levels, dtype=np.float64),
        np.asarray(zcr, dtype=np.float64),
        np.asarray(high_ratio, dtype=np.float64),
        np.asarray(flatness, dtype=np.float64),
    )


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(mask.tolist()):
        if value and start is None:
            start = index
        if start is not None and (not value or index == len(mask) - 1):
            end = index if not value else index + 1
            if end > start:
                result.append((start, end))
            start = None
    return result


def _sustained_voice_runs(
    active: np.ndarray,
    zcr: np.ndarray,
    high_ratio: np.ndarray,
    flatness: np.ndarray,
) -> list[tuple[int, int]]:
    # A frame is voice-like only when its crossing rate is speech-compatible and
    # at least one spectral measure is harmonic. The former OR-chain allowed a
    # broadband tail to become the "last voice" merely because one metric was low.
    voice_like = active & (zcr <= 0.23) & (
        (high_ratio <= 0.48) | (flatness <= 0.22)
    )
    return [
        (left, right)
        for left, right in _runs(voice_like)
        if right - left >= 4
    ]


def _last_sustained_voice(
    active: np.ndarray,
    zcr: np.ndarray,
    high_ratio: np.ndarray,
    flatness: np.ndarray,
) -> tuple[int, int] | None:
    sustained = _sustained_voice_runs(active, zcr, high_ratio, flatness)
    return sustained[-1] if sustained else None


def _spectral_jump(
    *,
    burst_zcr: float,
    burst_high: float,
    burst_flatness: float,
    voice_zcr: float,
    voice_high: float,
    voice_flatness: float,
) -> float:
    return (
        max(0.0, burst_zcr - voice_zcr) * 3.2
        + max(0.0, burst_high - voice_high) * 2.5
        + max(0.0, burst_flatness - voice_flatness) * 2.8
    )


def _after_decay(
    levels: np.ndarray,
    *,
    start: int,
    quiet_threshold: float,
    active_threshold: float,
) -> tuple[float, float, bool]:
    after = levels[int(start):]
    if len(after):
        quiet_fraction = float(np.mean(after <= quiet_threshold))
        active_after_seconds = float(np.sum(after > active_threshold)) * 0.010
    else:
        quiet_fraction = 1.0
        active_after_seconds = 0.0
    followed_by_decay = bool(
        quiet_fraction >= 0.45 and active_after_seconds <= 0.20
    )
    return quiet_fraction, active_after_seconds, followed_by_decay


def detect_late_broadband_tail(samples: Any, sample_rate: int) -> dict[str, Any]:
    audio = _mono(samples)
    rate = max(1, int(sample_rate))
    duration = len(audio) / rate
    times, levels, zcr, high_ratio, flatness = _frame_metrics(audio, rate)
    if len(levels) < 16:
        return {"policy": POLICY, "suspicious": False, "reason": "too_short"}

    peak = float(np.percentile(levels, 95))
    active_threshold = max(-52.0, peak - 34.0)
    quiet_threshold = max(-61.0, peak - 47.0)
    active = levels >= active_threshold
    voice_runs = _sustained_voice_runs(active, zcr, high_ratio, flatness)
    if not voice_runs:
        return {"policy": POLICY, "suspicious": False, "reason": "no_sustained_voice"}

    broadband = active & (zcr >= 0.17) & (high_ratio >= 0.34) & (flatness >= 0.09)

    # First catch an island embedded in the terminal decay. It may be followed by
    # a short harmonic residue, so selecting only the final voice run would hide it.
    for burst_start, burst_end in _runs(broadband):
        burst_seconds = (burst_end - burst_start) * 0.010
        burst_time = float(times[burst_start])
        if not 0.035 <= burst_seconds <= 0.24:
            continue
        if burst_time < duration * 0.60 or duration - burst_time > 0.80:
            continue
        previous_runs = [item for item in voice_runs if item[1] <= burst_start]
        following_runs = [item for item in voice_runs if item[0] >= burst_end]
        if not previous_runs or not following_runs:
            continue
        previous = previous_runs[-1]
        following = following_runs[0]
        gap_before = (burst_start - previous[1]) * 0.010
        gap_after = (following[0] - burst_end) * 0.010
        if gap_before > 0.18 or gap_after > 0.18:
            continue

        voice_probe_left = max(previous[0], previous[1] - 14)
        voice_level = float(np.median(levels[voice_probe_left:previous[1]]))
        voice_zcr = float(np.median(zcr[voice_probe_left:previous[1]]))
        voice_high = float(np.median(high_ratio[voice_probe_left:previous[1]]))
        voice_flatness = float(np.median(flatness[voice_probe_left:previous[1]]))
        burst_levels = levels[burst_start:burst_end]
        level = float(np.percentile(burst_levels, 80))
        median_zcr = float(np.median(zcr[burst_start:burst_end]))
        median_high = float(np.median(high_ratio[burst_start:burst_end]))
        median_flatness = float(np.median(flatness[burst_start:burst_end]))
        spectral_jump = _spectral_jump(
            burst_zcr=median_zcr,
            burst_high=median_high,
            burst_flatness=median_flatness,
            voice_zcr=voice_zcr,
            voice_high=voice_high,
            voice_flatness=voice_flatness,
        )
        before = levels[max(0, burst_start - 12):burst_start]
        valley_rebound = 0.0
        low_pre_fraction = 0.0
        if len(before):
            pre_level = float(np.median(before))
            valley_rebound = level - pre_level
            low_pre_fraction = float(np.mean(before <= level - 8.0))
        quiet_fraction, active_after_seconds, followed_by_decay = _after_decay(
            levels,
            start=following[1],
            quiet_threshold=quiet_threshold,
            active_threshold=active_threshold,
        )
        terminal_residue_seconds = (following[1] - following[0]) * 0.010
        suspicious = bool(
            spectral_jump >= 0.70
            and median_zcr >= 0.22
            and median_high >= 0.50
            and valley_rebound >= 7.0
            and low_pre_fraction >= 0.20
            and terminal_residue_seconds <= 0.34
            and followed_by_decay
        )
        if suspicious:
            return {
                "policy": POLICY,
                "suspicious": True,
                "repairable": False,
                "artifact_type": "embedded_terminal_broadband_island",
                "detection_path": "quiet_dip_broadband_island_then_voice_residue",
                "trim_time": None,
                "previous_voice_end": float(times[max(previous[0], previous[1] - 1)] + 0.01),
                "burst_start": burst_time,
                "burst_end": min(
                    duration,
                    float(times[max(burst_start, burst_end - 1)] + 0.01),
                ),
                "following_voice_start": float(times[following[0]]),
                "following_voice_end": float(times[max(following[0], following[1] - 1)] + 0.01),
                "terminal_residue_seconds": terminal_residue_seconds,
                "burst_seconds": burst_seconds,
                "burst_level_db": level,
                "voice_level_db": voice_level,
                "valley_rebound_db": valley_rebound,
                "low_pre_fraction": low_pre_fraction,
                "burst_median_zcr": median_zcr,
                "burst_high_frequency_ratio": median_high,
                "burst_spectral_flatness": median_flatness,
                "spectral_jump_score": spectral_jump,
                "quiet_fraction_after": quiet_fraction,
                "active_after_seconds": active_after_seconds,
            }

    last_voice = voice_runs[-1]
    voice_left, voice_right = last_voice
    voice_end_time = min(
        duration,
        float(times[max(voice_left, voice_right - 1)] + 0.01),
    )
    if voice_end_time < duration * 0.42:
        return {"policy": POLICY, "suspicious": False, "reason": "voice_ends_too_early"}

    search_start = max(voice_left, voice_right - 3)
    voice_probe_left = max(0, voice_right - 14)
    voice_level = float(np.median(levels[voice_probe_left:voice_right]))
    voice_zcr = float(np.median(zcr[voice_probe_left:voice_right]))
    voice_high = float(np.median(high_ratio[voice_probe_left:voice_right]))
    voice_flatness = float(np.median(flatness[voice_probe_left:voice_right]))

    for burst_start, burst_end in _runs(broadband[search_start:]):
        burst_start += search_start
        burst_end += search_start
        burst_seconds = (burst_end - burst_start) * 0.010
        if not 0.035 <= burst_seconds <= 0.38:
            continue
        burst_time = float(times[burst_start])
        if burst_time < max(duration * 0.60, voice_end_time - 0.06):
            continue
        burst_levels = levels[burst_start:burst_end]
        level = float(np.percentile(burst_levels, 80))
        median_zcr = float(np.median(zcr[burst_start:burst_end]))
        median_high = float(np.median(high_ratio[burst_start:burst_end]))
        median_flatness = float(np.median(flatness[burst_start:burst_end]))
        spectral_jump = _spectral_jump(
            burst_zcr=median_zcr,
            burst_high=median_high,
            burst_flatness=median_flatness,
            voice_zcr=voice_zcr,
            voice_high=voice_high,
            voice_flatness=voice_flatness,
        )

        before = levels[max(0, burst_start - 12):burst_start]
        valley_rebound = 0.0
        low_pre_fraction = 0.0
        if len(before):
            pre_level = float(np.median(before))
            valley_rebound = level - pre_level
            low_pre_fraction = float(np.mean(before <= level - 8.0))

        quiet_fraction, active_after_seconds, followed_by_decay = _after_decay(
            levels,
            start=burst_end,
            quiet_threshold=quiet_threshold,
            active_threshold=active_threshold,
        )
        near_end = duration - burst_time <= 0.72
        detached = bool(
            (valley_rebound >= 8.0 and low_pre_fraction >= 0.30)
            or (
                spectral_jump >= 0.36
                and level <= voice_level + 7.0
                and burst_time >= voice_end_time - 0.04
            )
        )
        if not (near_end and followed_by_decay and detached):
            continue

        trim_time = max(
            0.0,
            min(burst_time - 0.020, voice_end_time + 0.035),
        )
        return {
            "policy": POLICY,
            "suspicious": True,
            "repairable": True,
            "artifact_type": "late_broadband_burst",
            "detection_path": (
                "quiet_valley_rebound"
                if valley_rebound >= 8.0 and low_pre_fraction >= 0.30
                else "immediate_voice_to_broadband_transition"
            ),
            "trim_time": trim_time,
            "last_sustained_voice_end": voice_end_time,
            "burst_start": burst_time,
            "burst_end": min(
                duration,
                float(times[max(burst_start, burst_end - 1)] + 0.01),
            ),
            "burst_seconds": burst_seconds,
            "burst_level_db": level,
            "voice_level_db": voice_level,
            "valley_rebound_db": valley_rebound,
            "low_pre_fraction": low_pre_fraction,
            "burst_median_zcr": median_zcr,
            "burst_high_frequency_ratio": median_high,
            "burst_spectral_flatness": median_flatness,
            "spectral_jump_score": spectral_jump,
            "quiet_fraction_after": quiet_fraction,
            "active_after_seconds": active_after_seconds,
        }

    return {
        "policy": POLICY,
        "suspicious": False,
        "reason": "no_detached_or_embedded_late_broadband_tail",
        "last_sustained_voice_end": voice_end_time,
    }


__all__ = ["POLICY", "detect_late_broadband_tail"]

_BASE_ALL = tuple(globals().get('__all__', ()))

from pathlib import Path

import types



POLICY = "late-broadband-tail-v5"

VOICE_CLASSIFICATION_POLICY = "conjunctive-voiced-vs-broadband-tail-v2"

EMBEDDED_POLICY = "quiet-dip-broadband-island-voice-residue-v1"

BRACKETING_POLICY = "analysis-window-overlap-aware-voice-brackets-v1"

FRAME_OVERLAP_TOLERANCE = 2

_legacy_detect = detect_late_broadband_tail

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
        for left, right in _runs(voice_like)
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
    audio = _mono(samples)
    rate = max(1, int(sample_rate))
    duration = len(audio) / rate
    times, levels, zcr, high_ratio, flatness = _frame_metrics(audio, rate)
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
    for burst_start, burst_end in _runs(broadband):
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
        spectral_jump = _spectral_jump(
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
            "base_policy": POLICY,
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
        "base_policy": POLICY,
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
                len(_mono(samples)) / max(1, int(sample_rate)) - float(base["burst_end"]),
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

_last_sustained_voice = _last_sustained_voice

detect_late_broadband_tail = detect_late_broadband_tail

__all__ = sorted(
    set(name for name in _BASE_ALL if not name.startswith("__"))
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
