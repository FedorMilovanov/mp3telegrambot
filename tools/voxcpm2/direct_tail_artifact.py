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
