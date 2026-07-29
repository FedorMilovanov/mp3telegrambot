#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audio diagnostics and candidate scoring for direct VoxCPM2 production."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from tools.voxcpm2.direct_max_quality_io import (
    EXPECTED_ENCODE_SR,
    REFERENCE_TAIL_SILENCE,
    run_checked,
    sha256_file,
)


def _mono(samples: np.ndarray) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.reshape(-1)


def frame_levels(samples: np.ndarray, sample_rate: int, *, frame_ms: float = 20.0, hop_ms: float = 10.0) -> tuple[np.ndarray, np.ndarray]:
    audio = _mono(samples)
    frame = max(1, int(sample_rate * frame_ms / 1000.0))
    hop = max(1, int(sample_rate * hop_ms / 1000.0))
    levels: list[float] = []
    centers: list[float] = []
    for start in range(0, max(1, len(audio) - frame + 1), hop):
        chunk = audio[start:start + frame]
        if len(chunk) < frame:
            break
        rms = float(np.sqrt(np.mean(np.square(chunk.astype(np.float64))) + 1e-12))
        levels.append(20.0 * math.log10(max(rms, 1e-9)))
        centers.append((start + frame / 2) / sample_rate)
    return np.asarray(levels, dtype=np.float64), np.asarray(centers, dtype=np.float64)


def edge_silence(samples: np.ndarray, sample_rate: int, *, threshold_db: float = -52.0) -> tuple[float, float]:
    levels, _ = frame_levels(samples, sample_rate)
    if not len(levels):
        return 0.0, 0.0
    leading = 0
    for value in levels:
        if value < threshold_db:
            leading += 1
        else:
            break
    trailing = 0
    for value in levels[::-1]:
        if value < threshold_db:
            trailing += 1
        else:
            break
    return leading * 0.01, trailing * 0.01


def activity_stats(samples: np.ndarray, sample_rate: int) -> dict[str, float]:
    audio = _mono(samples)
    levels, _ = frame_levels(audio, sample_rate)
    if not len(levels):
        return {"active_ratio": 0.0, "max_internal_gap": 99.0, "rms_dbfs": -120.0, "peak_dbfs": -120.0}
    peak_level = float(np.percentile(levels, 95))
    threshold = max(-48.0, peak_level - 28.0)
    active = levels >= threshold
    ids = np.flatnonzero(active)
    max_gap = 0.0
    if len(ids) > 1:
        run = 0
        for value in active[ids[0]:ids[-1] + 1]:
            if value:
                max_gap = max(max_gap, run * 0.01)
                run = 0
            else:
                run += 1
    rms = math.sqrt(float(np.mean(np.square(audio.astype(np.float64)))) + 1e-12)
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    return {
        "active_ratio": float(np.mean(active)),
        "max_internal_gap": float(max_gap),
        "rms_dbfs": 20.0 * math.log10(max(rms, 1e-9)),
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-9)),
    }


def pitch_profile(samples: np.ndarray, sample_rate: int) -> dict[str, float]:
    audio = _mono(samples)
    frame = max(320, int(sample_rate * 0.04))
    hop = max(160, int(sample_rate * 0.02))
    if len(audio) < frame:
        return {"voiced_ratio": 0.0, "f0_median": 0.0, "f0_p90": 0.0}
    starts = list(range(0, len(audio) - frame + 1, hop))
    rms = np.asarray([
        np.sqrt(np.mean(np.square(audio[start:start + frame].astype(np.float64))) + 1e-12)
        for start in starts
    ], dtype=np.float64)
    threshold = max(float(np.percentile(rms, 35)) * 1.7, 10 ** (-40 / 20))
    lag_lo = max(2, int(sample_rate / 300))
    lag_hi = min(frame - 3, int(sample_rate / 65))
    values: list[float] = []
    for index, start in enumerate(starts):
        if rms[index] < threshold:
            continue
        chunk = audio[start:start + frame].astype(np.float64)
        chunk -= chunk.mean()
        chunk *= np.hanning(frame)
        autocorrelation = np.correlate(chunk, chunk, "full")[frame - 1:]
        if autocorrelation[0] <= 1e-9:
            continue
        lag = lag_lo + int(np.argmax(autocorrelation[lag_lo:lag_hi + 1]))
        if autocorrelation[lag] / autocorrelation[0] >= 0.30:
            values.append(sample_rate / lag)
    if not values:
        return {"voiced_ratio": 0.0, "f0_median": 0.0, "f0_p90": 0.0}
    array = np.asarray(values, dtype=np.float64)
    return {
        "voiced_ratio": len(values) / max(1, len(starts)),
        "f0_median": float(np.median(array)),
        "f0_p90": float(np.percentile(array, 90)),
    }


def clipping_ratio(samples: np.ndarray) -> float:
    return float(np.mean(np.abs(_mono(samples)) >= 0.995))


def detect_tail_restart(samples: np.ndarray, sample_rate: int) -> dict[str, Any]:
    levels, centers = frame_levels(samples, sample_rate)
    duration = len(_mono(samples)) / sample_rate
    if len(levels) < 20:
        return {"suspicious": False}
    peak = float(np.percentile(levels, 95))
    active_threshold = max(-48.0, peak - 28.0)
    silence_threshold = min(-46.0, peak - 36.0)
    active = levels > active_threshold
    silent = levels < silence_threshold
    run_start: int | None = None
    search_start = int(len(levels) * 0.55)
    for index in range(search_start, len(levels)):
        if silent[index] and run_start is None:
            run_start = index
        elif not silent[index] and run_start is not None:
            if index - run_start >= 24:
                resumed = np.flatnonzero(active[index:])
                if len(resumed):
                    start_index = index + int(resumed[0])
                    later = np.flatnonzero(active[start_index:])
                    end_index = start_index + int(later[-1])
                    resume_start = float(centers[start_index] - 0.01)
                    resume_end = float(centers[end_index] + 0.01)
                    resumed_duration = resume_end - resume_start
                    if resume_start > duration * 0.62 and resumed_duration <= 1.60:
                        return {
                            "suspicious": True,
                            "silence_start": max(0.0, float(centers[run_start] - 0.01)),
                            "resume_start": max(0.0, resume_start),
                            "resume_end": min(duration, resume_end),
                            "resumed_duration": resumed_duration,
                        }
            run_start = None
    return {"suspicious": False}


def clean_tail_restart(samples: np.ndarray, sample_rate: int, info: dict[str, Any]) -> tuple[np.ndarray, bool, float | None]:
    if not info.get("suspicious"):
        return _mono(samples), False, None
    trim_time = float(info["silence_start"]) + 0.03
    trim_sample = min(len(samples), max(1, int(trim_time * sample_rate)))
    cleaned = _mono(samples)[:trim_sample].copy()
    fade = min(len(cleaned), max(1, int(0.018 * sample_rate)))
    if fade > 1:
        cleaned[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return cleaned, True, trim_time


def _trim_reference_edges(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    audio = _mono(samples)
    levels, _ = frame_levels(audio, sample_rate)
    if not len(levels):
        return audio
    peak = float(np.percentile(levels, 95))
    threshold = max(-52.0, peak - 38.0)
    active = np.flatnonzero(levels >= threshold)
    if not len(active):
        return audio
    start = max(0, int((active[0] * 0.01 - 0.05) * sample_rate))
    end = min(len(audio), int((active[-1] * 0.01 + 0.08) * sample_rate))
    return audio[start:end].copy()


def prepare_reference(source: Path, output: Path, sf_module: Any) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    converted = output.with_suffix(".decoded.wav")
    run_checked([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-vn", "-ac", "1", "-ar", str(EXPECTED_ENCODE_SR),
        "-af", "highpass=f=45,lowpass=f=7600", "-c:a", "pcm_s24le", str(converted),
    ])
    samples, sample_rate = sf_module.read(str(converted), dtype="float32")
    sample_rate = int(sample_rate)
    audio = _trim_reference_edges(samples, sample_rate)
    if len(audio) < sample_rate * 4:
        raise RuntimeError(f"Voice reference слишком короткий после очистки: {source}")
    fade = min(int(sample_rate * 0.025), len(audio) // 8)
    if fade > 1:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        audio[:fade] *= ramp
        audio[-fade:] *= ramp[::-1]
    tail = np.zeros(int(sample_rate * REFERENCE_TAIL_SILENCE), dtype=np.float32)
    audio = np.concatenate([audio, tail])
    sf_module.write(str(output), audio, sample_rate, subtype="PCM_24")
    converted.unlink(missing_ok=True)
    return {
        "path": str(output),
        "sha256": sha256_file(output),
        "sample_rate": sample_rate,
        "duration": len(audio) / sample_rate,
        **pitch_profile(audio, sample_rate),
        **activity_stats(audio, sample_rate),
    }


def _ratio(value: float, reference: float, default: float = 1.0) -> float:
    if value <= 0 or reference <= 0:
        return default
    return value / reference


def candidate_score(candidate: dict[str, Any], speech_slot: float, reference_voice: dict[str, Any]) -> float:
    duration = float(candidate["duration"])
    ratio = duration / max(0.1, speech_slot)
    score = 0.0
    if candidate["tail_info"].get("suspicious"):
        score += 130.0
    if ratio < 0.55:
        score += 90.0 + (0.55 - ratio) * 180.0
    elif ratio > 1.45:
        score += 55.0 + (ratio - 1.45) * 80.0
    score += float(candidate["clipping_ratio"]) * 8000.0
    leading = float(candidate["leading_silence"])
    trailing = float(candidate["trailing_silence"])
    if leading > 0.35:
        score += (leading - 0.35) * 35.0
    if trailing > 0.75:
        score += (trailing - 0.75) * 10.0
    activity = candidate["activity"]
    pitch = candidate["pitch"]
    if float(activity["active_ratio"]) < 0.22:
        score += (0.22 - float(activity["active_ratio"])) * 360.0
    if float(activity["max_internal_gap"]) > 0.68:
        score += (float(activity["max_internal_gap"]) - 0.68) * 100.0
    if float(pitch["voiced_ratio"]) < 0.18:
        score += 120.0 + (0.18 - float(pitch["voiced_ratio"])) * 500.0
    median_ratio = _ratio(float(pitch["f0_median"]), float(reference_voice.get("f0_median") or 0.0))
    p90_ratio = _ratio(float(pitch["f0_p90"]), float(reference_voice.get("f0_p90") or 0.0))
    candidate["voice_match"] = {
        "f0_median_ratio": median_ratio,
        "f0_p90_ratio": p90_ratio,
        "voiced_ratio": float(pitch["voiced_ratio"]),
    }
    score += abs(math.log2(max(0.20, min(5.0, median_ratio)))) * 28.0
    score += abs(math.log2(max(0.20, min(5.0, p90_ratio)))) * 16.0
    if median_ratio < 0.68 or median_ratio > 1.38:
        score += 85.0
    if p90_ratio < 0.62 or p90_ratio > 1.45:
        score += 65.0
    score += abs(min(duration, speech_slot) - speech_slot) * 0.30
    return score


def candidate_hard_ok(candidate: dict[str, Any], speech_slot: float) -> bool:
    duration_ratio = float(candidate["duration"]) / max(0.1, speech_slot)
    voice = candidate.get("voice_match") or {}
    return bool(
        not candidate["tail_info"].get("suspicious")
        and 0.42 <= duration_ratio <= 1.55
        and float(candidate["clipping_ratio"]) <= 0.0015
        and float(candidate["activity"]["active_ratio"]) >= 0.16
        and float(candidate["pitch"]["voiced_ratio"]) >= 0.12
        and 0.55 <= float(voice.get("f0_median_ratio", 1.0)) <= 1.65
        and 0.50 <= float(voice.get("f0_p90_ratio", 1.0)) <= 1.75
    )
