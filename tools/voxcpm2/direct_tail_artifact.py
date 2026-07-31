#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic detection of detached broadband synthesis tails.

VoxCPM tails do not always contain a long silent valley. This detector accepts
two evidence paths: the historical quiet-valley rebound and an immediate
voice-to-broadband transition after the last sustained voiced region. Natural
final fricatives remain protected by duration, spectral-jump, location and
post-burst decay requirements.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

POLICY = "late-broadband-tail-v3"


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
    high_mask = frequencies >= min(3200.0, rate * 0.30)
    audible_mask = (frequencies >= 80.0) & (frequencies <= min(7600.0, rate * 0.48))
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
                float(np.exp(np.mean(np.log(audible))) / max(np.mean(audible), 1e-15))
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


def _last_sustained_voice(
    active: np.ndarray,
    zcr: np.ndarray,
    high_ratio: np.ndarray,
    flatness: np.ndarray,
) -> tuple[int, int] | None:
    voice_like = active & (
        (zcr <= 0.23)
        | (high_ratio <= 0.48)
        | (flatness <= 0.22)
    )
    sustained = [
        (left, right)
        for left, right in _runs(voice_like)
        if right - left >= 4
    ]
    return sustained[-1] if sustained else None


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
    last_voice = _last_sustained_voice(active, zcr, high_ratio, flatness)
    if last_voice is None:
        return {"policy": POLICY, "suspicious": False, "reason": "no_sustained_voice"}

    voice_left, voice_right = last_voice
    voice_end_time = min(duration, float(times[max(voice_left, voice_right - 1)] + 0.01))
    if voice_end_time < duration * 0.42:
        return {"policy": POLICY, "suspicious": False, "reason": "voice_ends_too_early"}

    # Search from the final voice region onward. The first frames may be a
    # natural final fricative, so broadband evidence must persist and differ
    # materially from the preceding voice posture.
    search_start = max(voice_left, voice_right - 3)
    broadband = (
        active
        & (zcr >= 0.17)
        & (high_ratio >= 0.34)
        & (flatness >= 0.16)
    )
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
        # Padding after the last spoken word can be long. Voice adjacency and
        # subsequent decay are the primary evidence; 60% is only a safety floor.
        if burst_time < max(duration * 0.60, voice_end_time - 0.06):
            continue
        burst_levels = levels[burst_start:burst_end]
        burst_zcr = zcr[burst_start:burst_end]
        burst_high = high_ratio[burst_start:burst_end]
        burst_flat = flatness[burst_start:burst_end]
        level = float(np.percentile(burst_levels, 80))
        median_zcr = float(np.median(burst_zcr))
        median_high = float(np.median(burst_high))
        median_flatness = float(np.median(burst_flat))
        spectral_jump = (
            max(0.0, median_zcr - voice_zcr) * 3.2
            + max(0.0, median_high - voice_high) * 2.5
            + max(0.0, median_flatness - voice_flatness) * 2.8
        )

        before = levels[max(0, burst_start - 12):burst_start]
        valley_rebound = 0.0
        low_pre_fraction = 0.0
        if len(before):
            pre_level = float(np.median(before))
            valley_rebound = level - pre_level
            low_pre_fraction = float(np.mean(before <= level - 8.0))

        after = levels[burst_end:]
        if len(after):
            quiet_fraction = float(np.mean(after <= quiet_threshold))
            active_after_seconds = float(np.sum(after > active_threshold)) * 0.010
        else:
            quiet_fraction = 1.0
            active_after_seconds = 0.0
        near_end = duration - burst_time <= 0.72
        followed_by_decay = bool(
            quiet_fraction >= 0.45
            and active_after_seconds <= 0.20
        )
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

        trim_time = max(0.0, min(burst_time - 0.020, voice_end_time + 0.035))
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
            "burst_end": min(duration, float(times[max(burst_start, burst_end - 1)] + 0.01)),
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
        "reason": "no_detached_late_broadband_tail",
        "last_sustained_voice_end": voice_end_time,
    }


__all__ = ["POLICY", "detect_late_broadband_tail"]
