#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Continuous-first clean reference selection for direct VoxCPM2 cloning.

A single natural 5–10 second source span is preferred over a montage. The old
multi-window builder remains an explicit fallback when captions do not expose a
long enough continuous speech run or every continuous candidate fails the hard
speech-quality floor. Continuous windows use the stricter editorial floor. A
fallback montage is judged by the actual assembled WAV against the renderer's
pre-model release floor, not rejected merely because every individual source
window misses the stricter continuous-window preference.
"""
from __future__ import annotations

import json
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from tools.voxcpm2 import direct_max_quality_analysis as release_analysis
from tools.voxcpm2 import generic_short_production as pipeline
from tools.voxcpm2 import professional_audio_v45

POLICY = "continuous-clean-reference-v2"
FALLBACK_VALIDATION_POLICY = "assembled-reference-release-floor-v1"
MIN_SECONDS = 5.0
MAX_SECONDS = 10.0
MERGE_GAP_SECONDS = 0.32

# Strict preference floor for one uninterrupted natural source window.
MIN_VOICED_RATIO = 0.16
MIN_ACTIVE_RATIO = 0.25
MAX_INTERNAL_GAP = 0.85


def _decode_source(source: Path, output: Path) -> tuple[np.ndarray, int]:
    command = [
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
        "16000",
        "-c:a",
        "pcm_f32le",
        str(output),
    ]
    process = subprocess.run(command, check=False)
    if process.returncode != 0 or not output.is_file():
        raise RuntimeError("Не удалось декодировать source для voice reference.")
    samples, sample_rate = sf.read(output, dtype="float32")
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    if sample_rate <= 0 or not len(audio) or not np.isfinite(audio).all():
        raise RuntimeError("Декодированный source для voice reference повреждён.")
    return audio, int(sample_rate)


def _merged_runs(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    normalized: list[tuple[float, float]] = []
    for raw_start, raw_end in intervals:
        try:
            start = float(raw_start)
            end = float(raw_end)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(start) or not math.isfinite(end):
            continue
        start = max(0.0, start)
        end = max(start, end)
        if end - start >= 0.35:
            normalized.append((start, end))

    runs: list[list[float]] = []
    for start, end in sorted(normalized):
        if runs and start - runs[-1][1] <= MERGE_GAP_SECONDS:
            runs[-1][1] = max(runs[-1][1], end)
        else:
            runs.append([start, end])
    return [(start, end) for start, end in runs]


def _window_score(samples: np.ndarray, sample_rate: int) -> tuple[float, dict[str, float]]:
    pitch = professional_audio_v45.pitch_profile(samples, sample_rate)
    activity = professional_audio_v45.activity_stats(samples, sample_rate)
    score = (
        float(pitch["f0_median"]) * 0.45
        + float(pitch["f0_p90"]) * 0.18
        + float(activity["max_internal_gap"]) * 75.0
        + abs(float(activity["active_ratio"]) - 0.74) * 50.0
    )
    if float(pitch["voiced_ratio"]) < 0.18:
        score += 160.0
    if float(activity["active_ratio"]) < 0.32:
        score += 120.0
    if float(activity["max_internal_gap"]) > MAX_INTERNAL_GAP:
        score += 140.0
    return score, {**pitch, **activity}


def _float_metric(stats: dict[str, Any], key: str, default: float) -> float:
    """Read a numeric metric without treating the valid value 0.0 as missing."""
    raw = stats.get(key, default)
    if raw is None:
        raw = default
    return float(raw)


def _finite_stats(stats: dict[str, Any]) -> tuple[float, float, float, float, float] | None:
    try:
        values = (
            _float_metric(stats, "voiced_ratio", 0.0),
            _float_metric(stats, "active_ratio", 0.0),
            _float_metric(stats, "max_internal_gap", 99.0),
            _float_metric(stats, "f0_median", 0.0),
            _float_metric(stats, "f0_p90", 0.0),
        )
    except (TypeError, ValueError, OverflowError):
        return None
    return values if all(math.isfinite(value) for value in values) else None


def _usable_stats(stats: dict[str, Any]) -> bool:
    values = _finite_stats(stats)
    if values is None:
        return False
    voiced, active, gap, f0_median, f0_p90 = values
    return bool(
        voiced >= MIN_VOICED_RATIO
        and active >= MIN_ACTIVE_RATIO
        and gap <= MAX_INTERNAL_GAP
        and f0_median > 1.0
        and f0_p90 > 1.0
    )


def _report_has_selection(payload: Any) -> bool:
    return bool(
        isinstance(payload, dict)
        and isinstance(payload.get("selected"), list)
        and payload.get("selected")
    )


def _report_has_usable_selection(payload: Any) -> bool:
    if not _report_has_selection(payload):
        return False
    return any(
        _usable_stats(item)
        for item in payload["selected"]
        if isinstance(item, dict)
    )


def _assembled_reference_stats(path: Path) -> dict[str, float]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("Fallback voice reference WAV не создан.")
    samples, sample_rate = sf.read(path, dtype="float32")
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.reshape(-1)
    if sample_rate <= 0 or not len(audio) or not np.isfinite(audio).all():
        raise RuntimeError("Fallback voice reference WAV повреждён.")
    pitch = release_analysis.pitch_profile(audio, int(sample_rate))
    activity = release_analysis.activity_stats(audio, int(sample_rate))
    return {
        "sample_rate": float(sample_rate),
        "duration_seconds": float(len(audio) / sample_rate),
        "peak": float(np.max(np.abs(audio))),
        "clipping_ratio": float(release_analysis.clipping_ratio(audio)),
        **{key: float(value) for key, value in pitch.items()},
        **{key: float(value) for key, value in activity.items()},
    }


def _assembled_release_failures(stats: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    values = _finite_stats(stats)
    if values is None:
        return ["невалидные pitch/activity метрики"]
    voiced, active, gap, f0_median, f0_p90 = values
    try:
        peak = _float_metric(stats, "peak", 0.0)
        clipping = _float_metric(stats, "clipping_ratio", 1.0)
        duration = _float_metric(stats, "duration_seconds", 0.0)
    except (TypeError, ValueError, OverflowError):
        return ["невалидные level/duration метрики"]
    if duration < 2.0:
        failures.append(f"duration={duration:.3f}s < 2.0s")
    if peak < release_analysis.MIN_REFERENCE_PEAK:
        failures.append(
            f"peak={peak:.6f} < {release_analysis.MIN_REFERENCE_PEAK:.3f}"
        )
    if clipping > release_analysis.MAX_REFERENCE_CLIPPING_RATIO:
        failures.append(
            "clipping_ratio="
            f"{clipping:.6f} > {release_analysis.MAX_REFERENCE_CLIPPING_RATIO:.3f}"
        )
    if voiced < release_analysis.MIN_REFERENCE_VOICED_RATIO:
        failures.append(
            "voiced_ratio="
            f"{voiced:.3f} < {release_analysis.MIN_REFERENCE_VOICED_RATIO:.2f}"
        )
    if active < release_analysis.MIN_REFERENCE_ACTIVE_RATIO:
        failures.append(
            "active_ratio="
            f"{active:.3f} < {release_analysis.MIN_REFERENCE_ACTIVE_RATIO:.2f}"
        )
    if gap > release_analysis.MAX_REFERENCE_INTERNAL_GAP:
        failures.append(
            "max_internal_gap="
            f"{gap:.3f} > {release_analysis.MAX_REFERENCE_INTERNAL_GAP:.2f}"
        )
    if f0_median <= 1.0 or f0_p90 <= 1.0:
        failures.append(
            f"pitch evidence missing (f0={f0_median:.2f}/{f0_p90:.2f})"
        )
    return failures


def _rounded_stats(stats: dict[str, Any]) -> dict[str, float]:
    return {
        key: round(float(value), 6)
        for key, value in stats.items()
        if isinstance(value, (int, float)) and math.isfinite(float(value))
    }


def _candidate_windows(
    audio: np.ndarray,
    sample_rate: int,
    intervals: list[tuple[float, float]],
    *,
    target_seconds: float,
) -> list[dict[str, Any]]:
    target_value = float(target_seconds)
    if not math.isfinite(target_value):
        raise RuntimeError("Некорректная длительность voice reference.")
    target = max(MIN_SECONDS, min(target_value, MAX_SECONDS))
    candidates: list[dict[str, Any]] = []
    for run_start, run_end in _merged_runs(intervals):
        run_length = run_end - run_start
        if run_length < MIN_SECONDS:
            continue
        window = min(target, run_length)
        travel = max(0.0, run_length - window)
        count = max(1, int(math.ceil(travel / 0.50)))
        starts = [
            run_start + (travel * index / count)
            for index in range(count + 1)
        ]
        for start in starts:
            end = min(run_end, start + window)
            left = max(0, int(start * sample_rate))
            right = min(len(audio), int(end * sample_rate))
            clip = np.asarray(audio[left:right], dtype=np.float32)
            if len(clip) < int(MIN_SECONDS * sample_rate):
                continue
            score, stats = _window_score(clip, sample_rate)
            if not _usable_stats(stats):
                continue
            candidates.append(
                {
                    "score": float(score),
                    "start": float(start),
                    "end": float(end),
                    "samples": clip,
                    "stats": stats,
                }
            )
    return candidates


def _gain_only_level(audio: np.ndarray) -> tuple[np.ndarray, float]:
    samples = np.asarray(audio, dtype=np.float32).copy()
    rms = math.sqrt(float(np.mean(np.square(samples.astype(np.float64)))) + 1e-12)
    peak = float(np.max(np.abs(samples))) + 1e-12
    gain = min(
        10 ** (-24 / 20) / max(rms, 1e-9),
        10 ** (-3 / 20) / peak,
        10 ** (5 / 20),
    )
    samples = np.clip(samples * gain, -0.999, 0.999).astype(np.float32)
    return samples, float(gain)


def build_reference(
    source: Path,
    intervals: list[tuple[float, float]],
    output: Path,
    *,
    target_seconds: float,
    profile: str,
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dub-continuous-ref-") as raw:
        audio, sample_rate = _decode_source(source, Path(raw) / "source.wav")
        candidates = _candidate_windows(
            audio,
            sample_rate,
            intervals,
            target_seconds=target_seconds,
        )

    if not candidates:
        professional_audio_v45.build_reference_v45(
            source,
            intervals,
            output,
            target_seconds=target_seconds,
        )
        fallback_path = output.with_suffix(".selection.json")
        if not fallback_path.is_file():
            raise RuntimeError("Fallback voice reference не создал selection report.")
        try:
            payload = json.loads(fallback_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Fallback voice reference создал повреждённый selection report.") from exc
        if not _report_has_selection(payload):
            output.unlink(missing_ok=True)
            fallback_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Fallback voice reference {profile} не содержит выбранных окон."
            )

        assembled_stats = _assembled_reference_stats(output)
        failures = _assembled_release_failures(assembled_stats)
        if failures:
            output.unlink(missing_ok=True)
            fallback_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Fallback voice reference {profile} не прошёл release floor: "
                + "; ".join(failures)
            )

        payload.update(
            {
                "reference_policy": POLICY,
                "reference_mode": "multi-window-fallback",
                "fallback_reason": "no_continuous_candidate_passed_strict_floor",
                "profile_name": profile,
                "denoise": False,
                "spectral_filter": False,
                "strict_window_floor_passed": _report_has_usable_selection(payload),
                "hard_floor": {
                    "min_voiced_ratio": MIN_VOICED_RATIO,
                    "min_active_ratio": MIN_ACTIVE_RATIO,
                    "max_internal_gap": MAX_INTERNAL_GAP,
                },
                "fallback_validation": {
                    "policy": FALLBACK_VALIDATION_POLICY,
                    "assembled_reference_passed": True,
                    "limits": {
                        "min_peak": release_analysis.MIN_REFERENCE_PEAK,
                        "max_clipping_ratio": release_analysis.MAX_REFERENCE_CLIPPING_RATIO,
                        "min_voiced_ratio": release_analysis.MIN_REFERENCE_VOICED_RATIO,
                        "min_active_ratio": release_analysis.MIN_REFERENCE_ACTIVE_RATIO,
                        "max_internal_gap": release_analysis.MAX_REFERENCE_INTERNAL_GAP,
                    },
                    "assembled_reference": _rounded_stats(assembled_stats),
                },
            }
        )
        fallback_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return payload

    selected = min(candidates, key=lambda item: float(item["score"]))
    samples, gain = _gain_only_level(selected["samples"])
    fade = min(int(sample_rate * 0.025), len(samples) // 8)
    if fade > 1:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        samples[:fade] *= ramp
        samples[-fade:] *= ramp[::-1]
    sf.write(output, samples, sample_rate, subtype="PCM_24")

    report = {
        "reference_policy": POLICY,
        "reference_mode": "single-continuous-window",
        "profile_name": profile,
        "denoise": False,
        "spectral_filter": False,
        "gain_only_leveling": True,
        "gain_linear": round(gain, 8),
        "duration_seconds": round(len(samples) / sample_rate, 6),
        "hard_floor": {
            "min_voiced_ratio": MIN_VOICED_RATIO,
            "min_active_ratio": MIN_ACTIVE_RATIO,
            "max_internal_gap": MAX_INTERNAL_GAP,
        },
        "selected": [
            {
                "start": round(float(selected["start"]), 3),
                "end": round(float(selected["end"]), 3),
                "score": round(float(selected["score"]), 4),
                **{
                    key: round(float(value), 6)
                    for key, value in selected["stats"].items()
                },
            }
        ],
    }
    output.with_suffix(".selection.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def build_calm_references(
    *,
    source: Path,
    cues: list[pipeline.Cue],
    duration: float,
    reference_dir: Path,
) -> tuple[Path, Path]:
    reference_dir.mkdir(parents=True, exist_ok=True)
    extended = reference_dir / "extended_reference.wav"
    composite = reference_dir / "composite_reference.wav"
    extended_intervals, composite_intervals = pipeline.reference_intervals(cues, duration)
    build_reference(
        source,
        extended_intervals,
        extended,
        target_seconds=9.0,
        profile="extended",
    )
    build_reference(
        source,
        composite_intervals,
        composite,
        target_seconds=8.0,
        profile="composite_calm",
    )
    return extended, composite


__all__ = [
    "FALLBACK_VALIDATION_POLICY",
    "MAX_INTERNAL_GAP",
    "MAX_SECONDS",
    "MERGE_GAP_SECONDS",
    "MIN_ACTIVE_RATIO",
    "MIN_SECONDS",
    "MIN_VOICED_RATIO",
    "POLICY",
    "build_calm_references",
    "build_reference",
]
