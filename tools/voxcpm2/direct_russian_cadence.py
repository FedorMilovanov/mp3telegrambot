#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Russian phrase-cadence and within-phrase contour diagnostics.

The renderer cannot ask VoxCPM2 for an abstract style label reliably.  Instead,
this module measures what the candidate actually produced: whether the ending
falls or rises, where the energy peak lands, how much empty tail remains and
whether a continuation was rendered like a full stop.  The functions are
deterministic, model-agnostic and never alter text or audio.
"""
from __future__ import annotations

import math
import re
from typing import Any

import numpy as np

POLICY = "russian-cadence-contour-v1"
_CLOSERS_RE = re.compile(r'[\s"\'»”)\]}]+$')


def classify_cadence(text: str) -> str:
    """Classify the syntactic job of the final punctuation."""
    value = _CLOSERS_RE.sub("", str(text or "").strip())
    if not value:
        return "linked"
    if value.endswith(("?!", "!?")):
        return "question"
    if value.endswith("?"):
        return "question"
    if value.endswith("!"):
        return "firm_terminal"
    if value.endswith(("...", "…")):
        return "suspense"
    if value.endswith((",", ";", ":")):
        return "continuation"
    if value.endswith("."):
        return "terminal"
    return "linked"


def _mono(samples: Any) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.reshape(-1)


def _frames(audio: np.ndarray, frame: int, hop: int) -> tuple[list[int], list[np.ndarray]]:
    if len(audio) < frame:
        return [], []
    starts = list(range(0, len(audio) - frame + 1, hop))
    return starts, [audio[start : start + frame] for start in starts]


def _pitch_frames(
    audio: np.ndarray,
    sample_rate: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    frame = max(320, int(sample_rate * 0.040))
    hop = max(160, int(sample_rate * 0.020))
    starts, chunks = _frames(audio, frame, hop)
    if not chunks:
        return (
            np.asarray([], dtype=np.float64),
            np.asarray([], dtype=np.float64),
            np.asarray([], dtype=bool),
        )
    rms = np.asarray(
        [math.sqrt(float(np.mean(chunk.astype(np.float64) ** 2)) + 1e-12) for chunk in chunks],
        dtype=np.float64,
    )
    threshold = max(float(np.percentile(rms, 30)) * 0.72, 10 ** (-46.0 / 20.0))
    lag_lo = max(2, int(sample_rate / 320))
    lag_hi = min(frame - 3, int(sample_rate / 60))
    times: list[float] = []
    values: list[float] = []
    voiced: list[bool] = []
    window = np.hanning(frame)
    for start, chunk, level in zip(starts, chunks, rms, strict=True):
        times.append((start + frame / 2) / sample_rate)
        if level < threshold:
            values.append(0.0)
            voiced.append(False)
            continue
        work = chunk.astype(np.float64)
        work -= work.mean()
        work *= window
        autocorrelation = np.correlate(work, work, "full")[frame - 1 :]
        if autocorrelation[0] <= 1e-10:
            values.append(0.0)
            voiced.append(False)
            continue
        search = autocorrelation[lag_lo : lag_hi + 1]
        lag = lag_lo + int(np.argmax(search))
        periodicity = float(autorrelation_value) if False else float(autocorrelation[lag] / autocorrelation[0])
        f0 = sample_rate / lag
        valid = bool(periodicity >= 0.28 and 45.0 <= f0 <= 500.0)
        values.append(float(f0) if valid else 0.0)
        voiced.append(valid)
    times_array = np.asarray(times, dtype=np.float64)
    values_array = np.asarray(values, dtype=np.float64)
    voiced_array = np.asarray(voiced, dtype=bool)
    values_array = _stabilize_pitch_track(values_array, voiced_array)
    return times_array, values_array, voiced_array


def _stabilize_pitch_track(
    values: np.ndarray,
    voiced: np.ndarray,
) -> np.ndarray:
    """Remove isolated octave errors without flattening real intonation.

    Autocorrelation can briefly report the second harmonic as F0, especially on
    fricative-heavy Russian endings.  We only halve/double a frame when it is
    more than roughly three quarters of an octave away from the recent voiced
    median and an octave-equivalent candidate restores continuity.
    """
    corrected = np.asarray(values, dtype=np.float64).copy()
    history: list[float] = []
    unvoiced_run = 0
    for index, raw in enumerate(corrected):
        if not bool(voiced[index]) or not math.isfinite(float(raw)) or raw <= 0.0:
            unvoiced_run += 1
            if unvoiced_run >= 6:
                history.clear()
            continue
        unvoiced_run = 0
        value = float(raw)
        if history:
            anchor = float(np.median(np.asarray(history[-7:], dtype=np.float64)))
            raw_distance = abs(math.log2(value / max(anchor, 1e-9)))
            if raw_distance >= 0.72:
                candidates = [
                    candidate
                    for candidate in (value * 0.5, value, value * 2.0)
                    if 45.0 <= candidate <= 500.0
                ]
                best = min(
                    candidates,
                    key=lambda candidate: abs(
                        math.log2(candidate / max(anchor, 1e-9))
                    ),
                )
                best_distance = abs(math.log2(best / max(anchor, 1e-9)))
                if best_distance + 0.20 < raw_distance:
                    value = float(best)
        corrected[index] = value
        history.append(value)
    return corrected


def _level_frames(
    audio: np.ndarray,
    sample_rate: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    frame = max(1, int(sample_rate * 0.020))
    hop = max(1, int(sample_rate * 0.010))
    starts, chunks = _frames(audio, frame, hop)
    if not chunks:
        return (
            np.asarray([], dtype=np.float64),
            np.asarray([], dtype=np.float64),
            np.asarray([], dtype=bool),
        )
    rms = np.asarray(
        [math.sqrt(float(np.mean(chunk.astype(np.float64) ** 2)) + 1e-12) for chunk in chunks],
        dtype=np.float64,
    )
    db = 20.0 * np.log10(np.maximum(rms, 1e-9))
    peak = float(np.percentile(db, 95))
    threshold = max(-50.0, peak - 30.0)
    active = db >= threshold
    times = np.asarray([(start + frame / 2) / sample_rate for start in starts], dtype=np.float64)
    return times, db, active


def _median_positive(values: np.ndarray) -> float:
    usable = values[np.isfinite(values) & (values > 0.0)]
    return float(np.median(usable)) if len(usable) else 0.0


def _semitones(late: float, early: float) -> float:
    if late <= 0.0 or early <= 0.0:
        return 0.0
    return 12.0 * math.log2(late / early)


def prosody_contour(samples: Any, sample_rate: int, *, bins: int = 5) -> dict[str, Any]:
    """Measure cadence and a coarse normalized contour over active speech."""
    audio = _mono(samples)
    rate = max(1, int(sample_rate))
    duration = len(audio) / rate
    level_times, levels_db, active = _level_frames(audio, rate)
    pitch_times, pitch_hz, voiced = _pitch_frames(audio, rate)
    if not len(level_times) or not np.any(active):
        return {
            "policy": POLICY,
            "available": False,
            "duration": duration,
            "active_start": 0.0,
            "active_end": 0.0,
            "trailing_silence": duration,
            "ending_delta_semitones": 0.0,
            "ending_energy_delta_db": 0.0,
            "energy_contour": [],
            "pitch_contour": [],
            "peak_energy_bin": None,
            "voiced_ending": False,
        }

    active_ids = np.flatnonzero(active)
    active_start = max(0.0, float(level_times[active_ids[0]] - 0.01))
    active_end = min(duration, float(level_times[active_ids[-1]] + 0.01))
    trailing = max(0.0, duration - active_end)
    edges = np.linspace(active_start, max(active_start + 1e-6, active_end), max(2, bins + 1))
    energy_values: list[float] = []
    pitch_values: list[float] = []
    voiced_ratios: list[float] = []
    for left, right in zip(edges[:-1], edges[1:], strict=True):
        level_mask = (level_times >= left) & (level_times < right)
        energy_values.append(float(np.median(levels_db[level_mask])) if np.any(level_mask) else -120.0)
        pitch_mask = (pitch_times >= left) & (pitch_times < right)
        selected = pitch_hz[pitch_mask & voiced] if np.any(pitch_mask) else np.asarray([])
        pitch_values.append(_median_positive(selected))
        voiced_ratios.append(float(np.mean(voiced[pitch_mask])) if np.any(pitch_mask) else 0.0)

    usable_energy = np.asarray(energy_values, dtype=np.float64)
    energy_floor = float(np.min(usable_energy))
    energy_norm = usable_energy - energy_floor
    energy_scale = max(1.0, float(np.max(energy_norm)))
    energy_contour = (energy_norm / energy_scale).tolist()

    positive_pitch = np.asarray([value for value in pitch_values if value > 0.0], dtype=np.float64)
    pitch_reference = float(np.median(positive_pitch)) if len(positive_pitch) else 0.0
    pitch_contour = [
        12.0 * math.log2(value / pitch_reference) if value > 0.0 and pitch_reference > 0.0 else 0.0
        for value in pitch_values
    ]

    ending_left = max(active_start, active_end - 0.68)
    ending_mid = max(ending_left + 0.08, active_end - 0.26)
    early_mask = (pitch_times >= ending_left) & (pitch_times < ending_mid) & voiced
    late_mask = (pitch_times >= ending_mid) & (pitch_times <= active_end + 0.03) & voiced
    early_f0 = _median_positive(pitch_hz[early_mask])
    late_f0 = _median_positive(pitch_hz[late_mask])
    ending_delta = _semitones(late_f0, early_f0)

    early_level_mask = (level_times >= ending_left) & (level_times < ending_mid)
    late_level_mask = (level_times >= ending_mid) & (level_times <= active_end + 0.03)
    early_db = float(np.median(levels_db[early_level_mask])) if np.any(early_level_mask) else -120.0
    late_db = float(np.median(levels_db[late_level_mask])) if np.any(late_level_mask) else -120.0

    return {
        "policy": POLICY,
        "available": True,
        "duration": duration,
        "active_start": active_start,
        "active_end": active_end,
        "trailing_silence": trailing,
        "ending_delta_semitones": ending_delta,
        "ending_energy_delta_db": late_db - early_db,
        "ending_early_f0": early_f0,
        "ending_late_f0": late_f0,
        "energy_contour": [float(value) for value in energy_contour],
        "pitch_contour": [float(value) for value in pitch_contour],
        "voiced_ratio_contour": [float(value) for value in voiced_ratios],
        "peak_energy_bin": int(np.argmax(usable_energy)),
        "voiced_ending": bool(early_f0 > 0.0 and late_f0 > 0.0),
    }


def _shape_distance(left: Any, right: Any) -> float | None:
    try:
        a = np.asarray(left, dtype=np.float64)
        b = np.asarray(right, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if a.ndim != 1 or b.ndim != 1 or len(a) < 3 or len(a) != len(b):
        return None
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        return None
    a = a - float(np.mean(a))
    b = b - float(np.mean(b))
    scale_a = max(0.25, float(np.std(a)))
    scale_b = max(0.25, float(np.std(b)))
    return float(np.mean(np.abs(a / scale_a - b / scale_b)))


def evaluate_candidate_cadence(
    candidate: dict[str, Any],
    segment: dict[str, Any],
) -> dict[str, Any]:
    """Return transparent penalty and fail-closed cadence evidence."""
    text = str(segment.get("text") or "")
    cadence = classify_cadence(text)
    contour = prosody_contour(candidate.get("samples"), int(candidate.get("sample_rate") or 1))
    delta = float(contour.get("ending_delta_semitones") or 0.0)
    ending_energy = float(contour.get("ending_energy_delta_db") or 0.0)
    trailing = float(contour.get("trailing_silence") or 0.0)
    target_duration = max(
        0.1,
        float(segment.get("end") or 0.0)
        - float(segment.get("start") or 0.0)
        - float(segment.get("tail_guard") or 0.0),
    )
    duration_ratio = float(candidate.get("duration") or 0.0) / target_duration
    penalty = 0.0
    failures: list[str] = []

    if not contour.get("available"):
        penalty += 80.0
        failures.append("cadence_unavailable")
    elif cadence in {"terminal", "firm_terminal"}:
        if cadence == "firm_terminal":
            if delta > 0.0:
                penalty += 78.0 + delta * 22.0
            elif delta > -1.0:
                penalty += 34.0
            if delta > 1.0:
                failures.append("terminal_rises")
            peak_bin = contour.get("peak_energy_bin")
            if isinstance(peak_bin, int) and peak_bin <= 1:
                penalty += 42.0
        else:
            if delta > 0.30:
                penalty += 68.0 + (delta - 0.30) * 24.0
            elif delta > -0.80:
                penalty += 28.0
            if delta > 1.40:
                failures.append("terminal_rises")
            elif delta > 0.45 and ending_energy > -1.0:
                failures.append("terminal_not_resolved")
    elif cadence == "question":
        if delta < -1.5:
            penalty += min(55.0, (-1.5 - delta) * 9.0)
        elif delta < 0.35:
            penalty += 10.0
    elif cadence in {"continuation", "linked"}:
        if delta < -1.7:
            penalty += 30.0 + min(65.0, (-1.7 - delta) * 15.0)
        if delta < -4.2:
            failures.append("continuation_closes")
        if duration_ratio < 0.72:
            penalty += 32.0 + (0.72 - duration_ratio) * 120.0
        if duration_ratio < 0.50:
            failures.append("continuation_too_short")
        if trailing > 0.42:
            penalty += min(55.0, (trailing - 0.42) * 65.0)
        if trailing > 0.92:
            failures.append("continuation_dead_tail")
    else:  # suspense
        if abs(delta) > 3.2:
            penalty += (abs(delta) - 3.2) * 10.0
        if trailing > 0.70:
            penalty += (trailing - 0.70) * 25.0

    source = segment.get("source_prosody")
    contour_distance = None
    if isinstance(source, dict):
        source_contour = source.get("contour")
        if isinstance(source_contour, dict):
            energy_distance = _shape_distance(
                contour.get("energy_contour"),
                source_contour.get("energy_contour"),
            )
            pitch_distance = _shape_distance(
                contour.get("pitch_contour"),
                source_contour.get("pitch_contour"),
            )
            distances = [value for value in (energy_distance, pitch_distance) if value is not None]
            if distances:
                contour_distance = float(np.mean(distances))
                penalty += min(48.0, contour_distance * 18.0)

    hard_ok = not failures
    return {
        "policy": POLICY,
        "cadence": cadence,
        "hard_ok": hard_ok,
        "failures": failures,
        "penalty": float(max(0.0, penalty)),
        "duration_ratio": duration_ratio,
        "contour_distance_to_source": contour_distance,
        **contour,
    }


__all__ = [
    "POLICY",
    "classify_cadence",
    "evaluate_candidate_cadence",
    "prosody_contour",
]
