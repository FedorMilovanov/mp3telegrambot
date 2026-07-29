#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Continuous-first clean reference selection for direct VoxCPM2 cloning.

A single natural 5–10 second source span is preferred over a montage. The old
multi-window builder remains an explicit fallback when captions do not expose a
long enough continuous speech run.
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

from tools.voxcpm2 import generic_short_production as pipeline
from tools.voxcpm2 import professional_audio_v45

POLICY = "continuous-clean-reference-v1"
MIN_SECONDS = 5.0
MAX_SECONDS = 10.0
MERGE_GAP_SECONDS = 0.32


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
    if process.returncode != 0:
        raise RuntimeError("Не удалось декодировать source для voice reference.")
    samples, sample_rate = sf.read(output, dtype="float32")
    return np.asarray(samples, dtype=np.float32).reshape(-1), int(sample_rate)


def _merged_runs(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    runs: list[list[float]] = []
    for raw_start, raw_end in sorted(intervals):
        start = max(0.0, float(raw_start))
        end = max(start, float(raw_end))
        if end - start < 0.35:
            continue
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
    if float(activity["max_internal_gap"]) > 0.85:
        score += 140.0
    return score, {**pitch, **activity}


def _candidate_windows(
    audio: np.ndarray,
    sample_rate: int,
    intervals: list[tuple[float, float]],
    *,
    target_seconds: float,
) -> list[dict[str, Any]]:
    target = max(MIN_SECONDS, min(float(target_seconds), MAX_SECONDS))
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
            clip = np.asarray(
                audio[int(start * sample_rate) : int(end * sample_rate)],
                dtype=np.float32,
            )
            if len(clip) < int(MIN_SECONDS * sample_rate):
                continue
            score, stats = _window_score(clip, sample_rate)
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
        payload = json.loads(fallback_path.read_text(encoding="utf-8-sig"))
        payload.update(
            {
                "reference_policy": POLICY,
                "reference_mode": "multi-window-fallback",
                "profile_name": profile,
                "denoise": False,
                "spectral_filter": False,
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
    "MAX_SECONDS",
    "MERGE_GAP_SECONDS",
    "MIN_SECONDS",
    "POLICY",
    "build_calm_references",
    "build_reference",
]
