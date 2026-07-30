#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic detection of late broadband synthesis tails.

The detector targets a narrow artifact seen in VoxCPM output: normal voiced
speech fades, then a short high-zero-crossing broadband island appears near the
end, followed by real silence. Natural fricatives and ordinary voiced fades are
kept because a candidate is rejected only when all temporal and spectral clues
agree.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

POLICY = "late-broadband-tail-v1"


def _mono(samples: Any) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.reshape(-1)


def detect_late_broadband_tail(samples: Any, sample_rate: int) -> dict[str, Any]:
    audio = _mono(samples)
    rate = max(1, int(sample_rate))
    duration = len(audio) / rate
    frame = max(64, int(rate * 0.020))
    hop = max(32, int(rate * 0.010))
    if len(audio) < frame * 8:
        return {"policy": POLICY, "suspicious": False, "reason": "too_short"}

    starts = np.arange(0, len(audio) - frame + 1, hop, dtype=np.int64)
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
    peak = float(np.percentile(levels, 95))
    quiet_threshold = max(-55.0, peak - 36.0)
    active_threshold = max(-48.0, peak - 28.0)
    search_start = int(np.searchsorted(times, duration * 0.68))

    for quiet_start in range(search_start, max(search_start, len(levels) - 8)):
        if levels[quiet_start] > quiet_threshold:
            continue
        quiet_end = quiet_start
        while quiet_end < len(levels) and levels[quiet_end] <= quiet_threshold:
            quiet_end += 1
        quiet_seconds = (quiet_end - quiet_start) * 0.01
        if quiet_seconds < 0.04 or quiet_end >= len(levels):
            continue

        burst_start = quiet_end
        while (
            burst_start < len(levels)
            and burst_start - quiet_end <= 4
            and levels[burst_start] < active_threshold
        ):
            burst_start += 1
        if burst_start >= len(levels) or burst_start - quiet_end > 4:
            continue
        burst_end = burst_start
        while burst_end < len(levels) and levels[burst_end] >= active_threshold:
            burst_end += 1
        burst_seconds = (burst_end - burst_start) * 0.01
        if not 0.06 <= burst_seconds <= 0.45:
            continue

        trailing_quiet = 0.0
        if burst_end < len(levels):
            trailing_quiet = float(
                np.sum(levels[burst_end:] <= quiet_threshold)
            ) * 0.01
        if trailing_quiet < 0.08:
            continue

        quiet_level = float(np.median(levels[quiet_start:quiet_end]))
        burst_level = float(np.percentile(levels[burst_start:burst_end], 80))
        rebound_db = burst_level - quiet_level
        burst_zcr = zcr[burst_start:burst_end]
        median_zcr = float(np.median(burst_zcr))
        high_zcr = float(np.percentile(burst_zcr, 90))
        if rebound_db < 10.0 or high_zcr < 0.25:
            continue
        if median_zcr > 0.16 and high_zcr < 0.40:
            continue

        trim_time = max(0.0, float(times[quiet_start]) - 0.015)
        return {
            "policy": POLICY,
            "suspicious": True,
            "repairable": True,
            "artifact_type": "late_broadband_burst",
            "trim_time": trim_time,
            "quiet_start": float(times[quiet_start]),
            "burst_start": float(times[burst_start]),
            "burst_end": min(
                duration,
                float(times[max(burst_start, burst_end - 1)] + 0.01),
            ),
            "burst_seconds": burst_seconds,
            "rebound_db": rebound_db,
            "burst_median_zcr": median_zcr,
            "burst_high_zcr": high_zcr,
            "trailing_quiet_seconds": trailing_quiet,
        }

    return {
        "policy": POLICY,
        "suspicious": False,
        "reason": "no_late_broadband_island",
    }


__all__ = ["POLICY", "detect_late_broadband_tail"]
