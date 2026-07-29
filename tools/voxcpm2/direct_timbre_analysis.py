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


def _mono(samples: np.ndarray) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.reshape(-1)


def spectral_envelope(samples: np.ndarray, sample_rate: int) -> dict[str, Any]:
    audio = _mono(samples)
    rate = max(1, int(sample_rate))
    if rate > 20_000:
        factor = max(1, int(round(rate / 16_000)))
        usable = len(audio) - len(audio) % factor
        if usable >= factor * 512:
            audio = audio[:usable].reshape(-1, factor).mean(axis=1).astype(np.float32)
            rate = int(round(rate / factor))

    frame = max(512, int(rate * 0.032))
    hop = max(256, int(rate * 0.016))
    if len(audio) < frame:
        return {
            "bands": [],
            "spectral_centroid_hz": 0.0,
            "frames": 0,
        }

    starts = list(range(0, len(audio) - frame + 1, hop))
    rms = np.asarray(
        [
            math.sqrt(
                float(
                    np.mean(
                        np.square(audio[start : start + frame].astype(np.float64))
                    )
                )
                + 1e-12
            )
            for start in starts
        ],
        dtype=np.float64,
    )
    activity_floor = max(float(np.percentile(rms, 35)) * 0.60, 10 ** (-45 / 20))
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
        audible = (frequencies >= BAND_EDGES_HZ[0]) & (
            frequencies <= BAND_EDGES_HZ[-1]
        )
        audible_power = power[audible]
        audible_frequencies = frequencies[audible]
        total = float(np.sum(audible_power))
        if total <= 1e-12:
            continue
        for band_index in range(len(accumulated)):
            mask = (frequencies >= BAND_EDGES_HZ[band_index]) & (
                frequencies < BAND_EDGES_HZ[band_index + 1]
            )
            accumulated[band_index] += math.log1p(float(np.sum(power[mask])))
        centroid_numerator += float(np.sum(audible_frequencies * audible_power))
        centroid_denominator += total
        used += 1

    if used <= 0 or float(np.sum(accumulated)) <= 1e-12:
        return {
            "bands": [],
            "spectral_centroid_hz": 0.0,
            "frames": 0,
        }

    distribution = accumulated / float(np.sum(accumulated))
    centroid = centroid_numerator / max(centroid_denominator, 1e-12)
    return {
        "bands": [round(float(value), 9) for value in distribution],
        "spectral_centroid_hz": float(centroid),
        "frames": int(used),
    }


def spectral_similarity(
    candidate_profile: dict[str, Any],
    reference_profile: dict[str, Any],
) -> float:
    candidate = np.asarray(candidate_profile.get("bands") or [], dtype=np.float64)
    reference = np.asarray(reference_profile.get("bands") or [], dtype=np.float64)
    if (
        candidate.ndim != 1
        or reference.ndim != 1
        or len(candidate) == 0
        or len(candidate) != len(reference)
    ):
        return 1.0
    candidate = np.clip(candidate, 0.0, None)
    reference = np.clip(reference, 0.0, None)
    candidate_sum = float(np.sum(candidate))
    reference_sum = float(np.sum(reference))
    if candidate_sum <= 1e-12 or reference_sum <= 1e-12:
        return 1.0
    candidate /= candidate_sum
    reference /= reference_sum
    # Bhattacharyya coefficient is bounded 0..1 and is robust to small
    # phoneme-dependent shifts between neighbouring bands.
    return float(np.clip(np.sum(np.sqrt(candidate * reference)), 0.0, 1.0))


def timbre_penalty(similarity: float) -> float:
    value = float(np.clip(similarity, 0.0, 1.0))
    if value >= SOFT_SIMILARITY_TARGET:
        return 0.0
    return (SOFT_SIMILARITY_TARGET - value) * 85.0


def timbre_hard_ok(similarity: float) -> bool:
    return float(similarity) >= HARD_SIMILARITY_FLOOR


__all__ = [
    "BAND_EDGES_HZ",
    "HARD_SIMILARITY_FLOOR",
    "SOFT_SIMILARITY_TARGET",
    "spectral_envelope",
    "spectral_similarity",
    "timbre_hard_ok",
    "timbre_penalty",
]
