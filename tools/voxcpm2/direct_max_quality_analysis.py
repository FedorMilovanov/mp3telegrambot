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
from tools.voxcpm2.direct_timbre_analysis import (
    BAND_EDGES_HZ,
    spectral_envelope,
    spectral_similarity,
    timbre_hard_ok,
    timbre_penalty,
)

MIN_REFERENCE_VOICED_RATIO = 0.12
MIN_REFERENCE_ACTIVE_RATIO = 0.20
MAX_REFERENCE_INTERNAL_GAP = 1.20
MAX_REFERENCE_CLIPPING_RATIO = 0.005
MIN_REFERENCE_PEAK = 0.001


def _mono(samples: np.ndarray) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.reshape(-1)


def frame_levels(
    samples: np.ndarray,
    sample_rate: int,
    *,
    frame_ms: float = 20.0,
    hop_ms: float = 10.0,
) -> tuple[np.ndarray, np.ndarray]:
    audio = _mono(samples)
    frame = max(1, int(sample_rate * frame_ms / 1000.0))
    hop = max(1, int(sample_rate * hop_ms / 1000.0))
    levels: list[float] = []
    centers: list[float] = []
    for start in range(0, max(1, len(audio) - frame + 1), hop):
        chunk = audio[start : start + frame]
        if len(chunk) < frame:
            break
        rms = float(
            np.sqrt(np.mean(np.square(chunk.astype(np.float64))) + 1e-12)
        )
        levels.append(20.0 * math.log10(max(rms, 1e-9)))
        centers.append((start + frame / 2) / sample_rate)
    return (
        np.asarray(levels, dtype=np.float64),
        np.asarray(centers, dtype=np.float64),
    )


def edge_silence(
    samples: np.ndarray,
    sample_rate: int,
    *,
    threshold_db: float = -52.0,
) -> tuple[float, float]:
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
        return {
            "active_ratio": 0.0,
            "max_internal_gap": 99.0,
            "rms_dbfs": -120.0,
            "peak_dbfs": -120.0,
        }
    peak_level = float(np.percentile(levels, 95))
    threshold = max(-48.0, peak_level - 28.0)
    active = levels >= threshold
    ids = np.flatnonzero(active)
    max_gap = 0.0
    if len(ids) > 1:
        run = 0
        for value in active[ids[0] : ids[-1] + 1]:
            if value:
                max_gap = max(max_gap, run * 0.01)
                run = 0
            else:
                run += 1
    rms = math.sqrt(
        float(np.mean(np.square(audio.astype(np.float64)))) + 1e-12
    )
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    return {
        "active_ratio": float(np.mean(active)),
        "max_internal_gap": float(max_gap),
        "rms_dbfs": 20.0 * math.log10(max(rms, 1e-9)),
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-9)),
    }


def _pitch_analysis_audio(
    samples: np.ndarray,
    sample_rate: int,
) -> tuple[np.ndarray, int]:
    """Return a cheap diagnostic copy without touching the native 48 kHz WAV."""
    audio = _mono(samples)
    rate = max(1, int(sample_rate))
    if rate > 20_000:
        factor = max(1, int(round(rate / EXPECTED_ENCODE_SR)))
        usable = len(audio) - (len(audio) % factor)
        if factor > 1 and usable >= factor * 320:
            audio = (
                audio[:usable]
                .reshape(-1, factor)
                .mean(axis=1)
                .astype(np.float32)
            )
            rate = int(round(rate / factor))
    return audio, rate


def pitch_profile(samples: np.ndarray, sample_rate: int) -> dict[str, float]:
    audio, sample_rate = _pitch_analysis_audio(samples, sample_rate)
    frame = max(320, int(sample_rate * 0.04))
    hop = max(160, int(sample_rate * 0.02))
    if len(audio) < frame:
        return {"voiced_ratio": 0.0, "f0_median": 0.0, "f0_p90": 0.0}
    starts = list(range(0, len(audio) - frame + 1, hop))
    rms = np.asarray(
        [
            np.sqrt(
                np.mean(
                    np.square(
                        audio[start : start + frame].astype(np.float64)
                    )
                )
                + 1e-12
            )
            for start in starts
        ],
        dtype=np.float64,
    )
    threshold = max(
        float(np.percentile(rms, 35)) * 0.50,
        10 ** (-45 / 20),
    )
    lag_lo = max(2, int(sample_rate / 300))
    lag_hi = min(frame - 3, int(sample_rate / 65))
    values: list[float] = []
    for index, start in enumerate(starts):
        if rms[index] < threshold:
            continue
        chunk = audio[start : start + frame].astype(np.float64)
        chunk -= chunk.mean()
        chunk *= np.hanning(frame)
        autocorrelation = np.correlate(chunk, chunk, "full")[frame - 1 :]
        if autocorrelation[0] <= 1e-9:
            continue
        lag = lag_lo + int(
            np.argmax(autorrelation[lag_lo : lag_hi + 1])
        )
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


def detect_tail_restart(
    samples: np.ndarray,
    sample_rate: int,
) -> dict[str, Any]:
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
                    if (
                        resume_start > duration * 0.62
                        and resumed_duration <= 1.60
                    ):
                        return {
                            "suspicious": True,
                            "silence_start": max(
                                0.0,
                                float(centers[run_start] - 0.01),
                            ),
                            "resume_start": max(0.0, resume_start),
                            "resume_end": min(duration, resume_end),
                            "resumed_duration": resumed_duration,
                        }
            run_start = None
    return {"suspicious": False}


def clean_tail_restart(
    samples: np.ndarray,
    sample_rate: int,
    info: dict[str, Any],
) -> tuple[np.ndarray, bool, float | None]:
    if not info.get("suspicious"):
        return _mono(samples), False, None
    trim_time = float(info["silence_start"]) + 0.03
    trim_sample = min(len(samples), max(1, int(trim_time * sample_rate)))
    cleaned = _mono(samples)[:trim_sample].copy()
    fade = min(len(cleaned), max(1, int(0.018 * sample_rate)))
    if fade > 1:
        cleaned[-fade:] *= np.linspace(
            1.0,
            0.0,
            fade,
            dtype=np.float32,
        )
    return cleaned, True, trim_time


def _trim_reference_edges(
    samples: np.ndarray,
    sample_rate: int,
) -> np.ndarray:
    audio = _mono(samples)
    levels, _ = frame_levels(audio, sample_rate)
    if not len(levels):
        return audio
    peak = float(np.percentile(levels, 95))
    threshold = max(-52.0, peak - 38.0)
    active = np.flatnonzero(levels >= threshold)
    if not len(active):
        return audio
    start = max(
        0,
        int((active[0] * 0.01 - 0.05) * sample_rate),
    )
    end = min(
        len(audio),
        int((active[-1] * 0.01 + 0.08) * sample_rate),
    )
    return audio[start:end].copy()


def _read_reference_transport(
    source: Path,
    converted: Path,
    sf_module: Any,
) -> tuple[np.ndarray, int, str]:
    """Use prepared mono-16k WAV directly; otherwise only resample/downmix."""
    try:
        info = sf_module.info(str(source))
        native = (
            int(info.samplerate) == EXPECTED_ENCODE_SR
            and int(info.channels) == 1
        )
    except Exception:
        native = False
    if native:
        samples, sample_rate = sf_module.read(
            str(source),
            dtype="float32",
        )
        return np.asarray(samples, dtype=np.float32), int(sample_rate), "native-mono-16k"

    run_checked(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(EXPECTED_ENCODE_SR),
            "-c:a",
            "pcm_s24le",
            str(converted),
        ]
    )
    samples, sample_rate = sf_module.read(
        str(converted),
        dtype="float32",
    )
    converted.unlink(missing_ok=True)
    return np.asarray(samples, dtype=np.float32), int(sample_rate), "resample-mono-only"


def _reference_quality(
    audio: np.ndarray,
    sample_rate: int,
    source: Path,
) -> dict[str, Any]:
    if not np.isfinite(audio).all():
        raise RuntimeError(f"Voice reference содержит NaN/Inf: {source}")
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    clip = clipping_ratio(audio) if len(audio) else 1.0
    pitch = pitch_profile(audio, sample_rate)
    activity = activity_stats(audio, sample_rate)
    spectrum = spectral_envelope(audio, sample_rate)
    failures: list[str] = []
    if peak < MIN_REFERENCE_PEAK:
        failures.append(f"peak={peak:.6f} < {MIN_REFERENCE_PEAK:.3f}")
    if clip > MAX_REFERENCE_CLIPPING_RATIO:
        failures.append(
            f"clipping={clip:.6f} > {MAX_REFERENCE_CLIPPING_RATIO:.3f}"
        )
    if float(pitch["voiced_ratio"]) < MIN_REFERENCE_VOICED_RATIO:
        failures.append(
            f"voiced_ratio={pitch['voiced_ratio']:.3f} < {MIN_REFERENCE_VOICED_RATIO:.2f}"
        )
    if float(activity["active_ratio"]) < MIN_REFERENCE_ACTIVE_RATIO:
        failures.append(
            f"active_ratio={activity['active_ratio']:.3f} < {MIN_REFERENCE_ACTIVE_RATIO:.2f}"
        )
    if float(activity["max_internal_gap"]) > MAX_REFERENCE_INTERNAL_GAP:
        failures.append(
            f"max_internal_gap={activity['max_internal_gap']:.3f} > {MAX_REFERENCE_INTERNAL_GAP:.2f}"
        )
    if (
        int(spectrum.get("frames") or 0) <= 0
        or len(spectrum.get("bands") or []) != len(BAND_EDGES_HZ) - 1
    ):
        failures.append("нет валидного spectral envelope")
    if failures:
        raise RuntimeError(
            f"Voice reference не прошёл pre-model hard-floor: {source}: "
            + "; ".join(failures)
        )
    return {
        "reference_quality_policy": "pre-model-reference-hard-floor-v1",
        "peak": peak,
        "clipping_ratio": clip,
        "limits": {
            "min_peak": MIN_REFERENCE_PEAK,
            "max_clipping_ratio": MAX_REFERENCE_CLIPPING_RATIO,
            "min_voiced_ratio": MIN_REFERENCE_VOICED_RATIO,
            "min_active_ratio": MIN_REFERENCE_ACTIVE_RATIO,
            "max_internal_gap": MAX_REFERENCE_INTERNAL_GAP,
            "spectral_bands": len(BAND_EDGES_HZ) - 1,
        },
        "spectral_envelope": spectrum,
        **pitch,
        **activity,
    }


def prepare_reference(
    source: Path,
    output: Path,
    sf_module: Any,
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    converted = output.with_suffix(".decoded.wav")
    samples, sample_rate, transport = _read_reference_transport(
        source,
        converted,
        sf_module,
    )
    audio = _trim_reference_edges(samples, sample_rate)
    duration = len(audio) / max(1, sample_rate)
    if duration < 5.0:
        raise RuntimeError(
            f"Voice reference короче 5 секунд после очистки: {source}"
        )
    if duration > 30.0:
        raise RuntimeError(
            f"Voice reference длиннее 30 секунд после очистки: {source}"
        )
    quality = _reference_quality(audio, sample_rate, source)
    fade = min(int(sample_rate * 0.025), len(audio) // 8)
    if fade > 1:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        audio[:fade] *= ramp
        audio[-fade:] *= ramp[::-1]
    if REFERENCE_TAIL_SILENCE > 0.0:
        tail = np.zeros(
            int(sample_rate * REFERENCE_TAIL_SILENCE),
            dtype=np.float32,
        )
        audio = np.concatenate([audio, tail])
    sf_module.write(
        str(output),
        audio,
        sample_rate,
        subtype="PCM_24",
    )
    return {
        "path": str(output),
        "sha256": sha256_file(output),
        "sample_rate": sample_rate,
        "duration": len(audio) / sample_rate,
        "transport": transport,
        "spectral_filter": False,
        "denoise": False,
        **quality,
    }


def _ratio(
    value: float,
    reference: float,
    default: float = 1.0,
) -> float:
    if value <= 0 or reference <= 0:
        return default
    return value / reference


def candidate_score(
    candidate: dict[str, Any],
    speech_slot: float,
    reference_voice: dict[str, Any],
) -> float:
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
        score += (
            float(activity["max_internal_gap"]) - 0.68
        ) * 100.0
    if float(pitch["voiced_ratio"]) < 0.18:
        score += (
            120.0
            + (0.18 - float(pitch["voiced_ratio"])) * 500.0
        )

    median_ratio = _ratio(
        float(pitch["f0_median"]),
        float(reference_voice.get("f0_median") or 0.0),
    )
    p90_ratio = _ratio(
        float(pitch["f0_p90"]),
        float(reference_voice.get("f0_p90") or 0.0),
    )
    candidate_timbre = spectral_envelope(
        candidate["samples"],
        int(candidate["sample_rate"]),
    )
    similarity = spectral_similarity(
        candidate_timbre,
        reference_voice.get("spectral_envelope") or {},
    )
    candidate["timbre"] = candidate_timbre
    candidate["voice_match"] = {
        "f0_median_ratio": median_ratio,
        "f0_p90_ratio": p90_ratio,
        "voiced_ratio": float(pitch["voiced_ratio"]),
        "spectral_similarity": similarity,
    }

    score += abs(
        math.log2(max(0.20, min(5.0, median_ratio)))
    ) * 28.0
    score += abs(
        math.log2(max(0.20, min(5.0, p90_ratio)))
    ) * 16.0
    if median_ratio < 0.68 or median_ratio > 1.38:
        score += 85.0
    if p90_ratio < 0.62 or p90_ratio > 1.45:
        score += 65.0
    score += timbre_penalty(similarity)
    score += abs(min(duration, speech_slot) - speech_slot) * 0.30
    return score


def _finite_voice_metric(voice: dict[str, Any], key: str) -> float | None:
    if key not in voice:
        return None
    try:
        value = float(voice[key])
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def candidate_hard_ok(
    candidate: dict[str, Any],
    speech_slot: float,
) -> bool:
    try:
        duration = float(candidate["duration"])
        slot = float(speech_slot)
        clipping = float(candidate["clipping_ratio"])
        active = float(candidate["activity"]["active_ratio"])
        voiced = float(candidate["pitch"]["voiced_ratio"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    if not all(math.isfinite(value) for value in (duration, slot, clipping, active, voiced)):
        return False
    if slot <= 0.0:
        return False

    voice = candidate.get("voice_match")
    if not isinstance(voice, dict):
        return False
    median_ratio = _finite_voice_metric(voice, "f0_median_ratio")
    p90_ratio = _finite_voice_metric(voice, "f0_p90_ratio")
    similarity = _finite_voice_metric(voice, "spectral_similarity")
    if median_ratio is None or p90_ratio is None or similarity is None:
        return False

    tail = candidate.get("tail_info")
    if not isinstance(tail, dict):
        return False
    duration_ratio = duration / max(0.1, slot)
    return bool(
        not tail.get("suspicious")
        and 0.42 <= duration_ratio <= 1.55
        and clipping <= 0.0015
        and active >= 0.16
        and voiced >= 0.12
        and 0.55 <= median_ratio <= 1.65
        and 0.50 <= p90_ratio <= 1.75
        and timbre_hard_ok(similarity)
    )
