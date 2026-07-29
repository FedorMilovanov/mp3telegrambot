#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Conservative timbre proxy for direct VoxCPM2 candidate ranking.

This is deliberately not a speaker-recognition model. It compares the long-term
log-frequency energy envelope of a candidate with its real voice reference. The
metric is used mainly as a soft tie-breaker; the hard floor is intentionally low
because phonetic content also changes the spectrum.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np


BAND_EDGES_HZ = np.geomspace(80.0, 7600.0, 19)
SOFT_SIMILARITY_TARGET = 0.82
HARD_SIMILARITY_FLOOR = 0.30
MAX_TIMBRE_PENALTY = SOFT_SIMILARITY_TARGET * 85.0


def _empty_profile() -> dict[str, Any]:
    return {
        "bands": [],
        "spectral_centroid_hz": 0.0,
        "frames": 0,
    }


def _mono(samples: np.ndarray) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.reshape(-1)


def spectral_envelope(samples: np.ndarray, sample_rate: int) -> dict[str, Any]:
    audio = _mono(samples)
    try:
        rate = int(sample_rate)
    except (TypeError, ValueError, OverflowError):
        return _empty_profile()
    if rate <= 0 or len(audio) == 0 or not np.isfinite(audio).all():
        return _empty_profile()
    if rate > 20_000:
        factor = max(1, int(round(rate / 16_000)))
        usable = len(audio) - len(audio) % factor
        if usable >= factor * 512:
            audio = (
                audio[:usable]
                .reshape(-1, factor)
                .mean(axis=1)
                .astype(np.float32)
            )
            rate = int(round(rate / factor))

    frame = max(512, int(rate * 0.032))
    hop = max(256, int(rate * 0.016))
    if len(audio) < frame:
        return _empty_profile()

    starts = list(range(0, len(audio) - frame + 1, hop))
    rms = np.asarray(
        [
            math.sqrt(
                float(
                    np.mean(
                        np.square(
                            audio[start : start + frame].astype(np.float64)
                        )
                    )
                )
                + 1e-12
            )
            for start in starts
        ],
        dtype=np.float64,
    )
    if not np.isfinite(rms).all():
        return _empty_profile()
    activity_floor = max(
        float(np.percentile(rms, 35)) * 0.60,
        10 ** (-45 / 20),
    )
    frequencies = np.fft.rfftfreq(frame, d=1.0 / rate)
    window = np.hanning(frame)
    accumulated = np.zeros(len(BAND_EDGES_HZ) - 1, dtype=np.float64)
    centroid_numerator = 0.0
    centroid_denominator = 0.0
    used = 0

    for index, start in enumerate(starts):
        if rms[index] < activity_floor:
            continue
        chunk = audio[start : start + frame].astype(np.float64)
        chunk -= float(np.mean(chunk))
        power = np.square(np.abs(np.fft.rfft(chunk * window)))
        if not np.isfinite(power).all():
            continue
        audible = (frequencies >= BAND_EDGES_HZ[0]) & (
            frequencies <= BAND_EDGES_HZ[-1]
        )
        audible_power = power[audible]
        audible_frequencies = frequencies[audible]
        total = float(np.sum(audible_power))
        if not math.isfinite(total) or total <= 1e-12:
            continue
        for band_index in range(len(accumulated)):
            mask = (frequencies >= BAND_EDGES_HZ[band_index]) & (
                frequencies < BAND_EDGES_HZ[band_index + 1]
            )
            band_power = float(np.sum(power[mask]))
            if not math.isfinite(band_power):
                continue
            accumulated[band_index] += math.log1p(max(0.0, band_power))
        centroid_numerator += float(
            np.sum(audible_frequencies * audible_power)
        )
        centroid_denominator += total
        used += 1

    accumulated_sum = float(np.sum(accumulated))
    if (
        used <= 0
        or not math.isfinite(accumulated_sum)
        or accumulated_sum <= 1e-12
        or not math.isfinite(centroid_numerator)
        or not math.isfinite(centroid_denominator)
        or centroid_denominator <= 1e-12
    ):
        return _empty_profile()

    distribution = accumulated / accumulated_sum
    centroid = centroid_numerator / centroid_denominator
    if not np.isfinite(distribution).all() or not math.isfinite(centroid):
        return _empty_profile()
    return {
        "bands": [round(float(value), 9) for value in distribution],
        "spectral_centroid_hz": float(centroid),
        "frames": int(used),
    }


def spectral_similarity(
    candidate_profile: dict[str, Any],
    reference_profile: dict[str, Any],
) -> float:
    try:
        candidate = np.asarray(
            candidate_profile.get("bands") or [],
            dtype=np.float64,
        )
        reference = np.asarray(
            reference_profile.get("bands") or [],
            dtype=np.float64,
        )
    except (TypeError, ValueError, OverflowError, AttributeError):
        return 0.0
    if (
        candidate.ndim != 1
        or reference.ndim != 1
        or len(candidate) == 0
        or len(candidate) != len(reference)
        or not np.isfinite(candidate).all()
        or not np.isfinite(reference).all()
    ):
        # Missing or corrupt evidence is not evidence of a perfect speaker match.
        return 0.0
    candidate = np.clip(candidate, 0.0, None)
    reference = np.clip(reference, 0.0, None)
    candidate_sum = float(np.sum(candidate))
    reference_sum = float(np.sum(reference))
    if (
        not math.isfinite(candidate_sum)
        or not math.isfinite(reference_sum)
        or candidate_sum <= 1e-12
        or reference_sum <= 1e-12
    ):
        return 0.0
    candidate /= candidate_sum
    reference /= reference_sum
    # Bhattacharyya coefficient is bounded 0..1 and is robust to small
    # phoneme-dependent shifts between neighbouring bands.
    similarity = float(np.sum(np.sqrt(candidate * reference)))
    return float(np.clip(similarity, 0.0, 1.0)) if math.isfinite(similarity) else 0.0


def timbre_penalty(similarity: float) -> float:
    try:
        value = float(similarity)
    except (TypeError, ValueError, OverflowError):
        return MAX_TIMBRE_PENALTY
    if not math.isfinite(value):
        return MAX_TIMBRE_PENALTY
    value = float(np.clip(value, 0.0, 1.0))
    if value >= SOFT_SIMILARITY_TARGET:
        return 0.0
    return (SOFT_SIMILARITY_TARGET - value) * 85.0


def timbre_hard_ok(similarity: float) -> bool:
    try:
        value = float(similarity)
    except (TypeError, ValueError, OverflowError):
        return False
    return bool(
        math.isfinite(value)
        and HARD_SIMILARITY_FLOOR <= value <= 1.0
    )


__all__ = [
    "BAND_EDGES_HZ",
    "HARD_SIMILARITY_FLOOR",
    "MAX_TIMBRE_PENALTY",
    "SOFT_SIMILARITY_TARGET",
    "spectral_envelope",
    "spectral_similarity",
    "timbre_hard_ok",
    "timbre_penalty",
]
