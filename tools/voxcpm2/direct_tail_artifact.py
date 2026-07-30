#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic detection of late broadband synthesis tails.

The detector targets a narrow artifact seen in VoxCPM output: normal voiced
speech decays, then a short high-zero-crossing broadband island rebounds near
the end and is followed by real silence. Natural fricatives and ordinary voiced
fades are kept because every temporal and spectral clue must agree.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

POLICY = "late-broadband-tail-v2"


def _mono(samples: Any) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.reshape(-1)


def _frame_metrics(
    audio: np.ndarray,
    rate: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    frame = max(64, int(rate * 0.020))
    hop = max(32, int(rate * 0.010))
    starts = np.arange(0, max(0, len(audio) - frame + 1), hop, dtype=np.int64)
    if not len(starts):
        empty = np.asarray([], dtype=np.float64)
        return empty, empty, empty
    rms = np.asarray(
        [
            math.sqrt(
                float(np.mean(audio[pos : pos + frame].astype(np.float64) ** 2))
                + 1e-12
            )
            for pos in starts
        ],
        dtype=np.float64,
    )
    levels = 20.0 * np.log10(np.maximum(rms, 1e-9))
    zcr = np.asarray(
        [
            float(
                np.mean(
                    np.signbit(audio[pos : pos + frame - 1])
                    != np.signbit(audio[pos + 1 : pos + frame])
                )
            )
            for pos in starts
        ],
        dtype=np.float64,
    )
    times = (starts + frame / 2) / rate
    return times, levels, zcr


def detect_late_broadband_tail(samples: Any, sample_rate: int) -> dict[str, Any]:
    audio = _mono(samples)
    rate = max(1, int(sample_rate))
    duration = len(audio) / rate
    times, levels, zcr = _frame_metrics(audio, rate)
    if len(levels) < 16:
        return {"policy": POLICY, "suspicious": False, "reason": "too_short"}

    peak = float(np.percentile(levels, 95))
    quiet_threshold = max(-58.0, peak - 44.0)
    search_start = int(np.searchsorted(times, duration * 0.68))
    broadband = (zcr >= 0.18) & (levels >= peak - 18.0)

    index = search_start
    while index < len(levels):
        if not broadband[index]:
            index += 1
            continue
        burst_start = index
        while index < len(levels) and broadband[index]:
            index += 1
        burst_end = index
        burst_seconds = (burst_end - burst_start) * 0.01
        if not 0.05 <= burst_seconds <= 0.32:
            continue
        burst_time = float(times[burst_start])
        if burst_time < duration * 0.76:
            continue

        pre_start = max(search_start, burst_start - 10)
        pre = levels[pre_start:burst_start]
        if len(pre) < 4:
            continue
        burst_levels = levels[burst_start:burst_end]
        burst_zcr = zcr[burst_start:burst_end]
        pre_level = float(np.median(pre))
        burst_level = float(np.percentile(burst_levels, 80))
        rebound_db = burst_level - pre_level
        median_zcr = float(np.median(burst_zcr))
        high_zcr = float(np.percentile(burst_zcr, 90))
        low_pre_fraction = float(np.mean(pre <= burst_level - 10.0))
        if rebound_db < 10.0 or low_pre_fraction < 0.40:
            continue
        if median_zcr < 0.18 or high_zcr < 0.25:
            continue

        after = levels[burst_end:]
        quiet_ids = np.flatnonzero(after <= quiet_threshold)
        if not len(quiet_ids):
            continue
        first_quiet = int(quiet_ids[0])
        if first_quiet * 0.01 > 0.30:
            continue
        trailing_quiet = float(
            np.sum(after[first_quiet:] <= quiet_threshold)
        ) * 0.01
        if trailing_quiet < 0.08:
            continue

        trim_anchor = max(0, pre_start + int(np.argmin(pre)))
        trim_time = max(0.0, float(times[trim_anchor]) - 0.015)
        return {
            "policy": POLICY,
            "suspicious": True,
            "repairable": True,
            "artifact_type": "late_broadband_burst",
            "trim_time": trim_time,
            "valley_start": float(times[pre_start]),
            "burst_start": burst_time,
            "burst_end": min(
                duration,
                float(times[max(burst_start, burst_end - 1)] + 0.01),
            ),
            "burst_seconds": burst_seconds,
            "rebound_db": rebound_db,
            "low_pre_fraction": low_pre_fraction,
            "burst_median_zcr": median_zcr,
            "burst_high_zcr": high_zcr,
            "first_quiet_after_seconds": first_quiet * 0.01,
            "trailing_quiet_seconds": trailing_quiet,
        }

    return {
        "policy": POLICY,
        "suspicious": False,
        "reason": "no_late_broadband_island",
    }


__all__ = ["POLICY", "detect_late_broadband_tail"]
