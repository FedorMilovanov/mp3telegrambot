#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quality v4 entrypoint around the proven reference-only NoChew renderer.

It deliberately does not patch VoxCPM.generate.  The underlying renderer keeps
its original min_len=2, retry_badcase=False and requested CFG.  This adapter only
scores unstable starts and normalizes candidate edge silence before timeline fit.
"""
from __future__ import annotations

import math
import os
import runpy
import sys
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

_QUALITY_VERSION = "voxcpm2-quality-v4"


def log(message: str) -> None:
    print(f"[VOXCPM2-QUALITY-V4] {message}", flush=True)


def _frame_rms(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray, int]:
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    frame = max(64, int(sample_rate * 0.020))
    hop = max(32, int(sample_rate * 0.010))
    starts = np.arange(0, max(1, len(audio) - frame + 1), hop, dtype=np.int64)
    levels = np.asarray(
        [math.sqrt(float(np.mean(audio[start : start + frame] ** 2)) + 1e-12) for start in starts],
        dtype=np.float64,
    )
    return levels, starts, frame


def _sustained_activity(active: np.ndarray, *, from_start: bool) -> int | None:
    if not len(active):
        return None
    indices = range(len(active)) if from_start else range(len(active) - 1, -1, -1)
    for index in indices:
        left = max(0, index - 2)
        right = min(len(active), index + 3)
        # Require a sustained local run, so an isolated chirp is not mistaken
        # for the beginning of the requested sentence.
        if active[index] and int(np.count_nonzero(active[left:right])) >= 3:
            return int(index)
    return None


def trim_candidate_edges(
    samples: np.ndarray,
    sample_rate: int,
    *,
    pre_roll: float = 0.065,
    post_roll: float = 0.140,
) -> tuple[np.ndarray, dict[str, float]]:
    """Remove variable model silence/chirps while preserving a fixed natural edge."""
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    if len(audio) < int(sample_rate * 0.25):
        return audio, {"trimmed_leading": 0.0, "trimmed_trailing": 0.0}

    levels, starts, frame = _frame_rms(audio, sample_rate)
    peak_db = 20.0 * math.log10(float(np.max(levels)) + 1e-12)
    threshold_db = max(-49.0, peak_db - 33.0)
    level_db = 20.0 * np.log10(levels + 1e-12)
    active = level_db >= threshold_db
    first_index = _sustained_activity(active, from_start=True)
    last_index = _sustained_activity(active, from_start=False)
    if first_index is None or last_index is None or last_index < first_index:
        return audio, {"trimmed_leading": 0.0, "trimmed_trailing": 0.0}

    speech_start = int(starts[first_index])
    speech_end = int(starts[last_index]) + frame
    cut_start = max(0, speech_start - int(sample_rate * pre_roll))
    cut_end = min(len(audio), speech_end + int(sample_rate * post_roll))
    if cut_end - cut_start < int(sample_rate * 0.22):
        return audio, {"trimmed_leading": 0.0, "trimmed_trailing": 0.0}

    result = audio[cut_start:cut_end].copy()
    fade = min(int(sample_rate * 0.008), len(result) // 8)
    if fade > 1:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        result[:fade] *= ramp
        result[-fade:] *= ramp[::-1]
    return result, {
        "trimmed_leading": cut_start / sample_rate,
        "trimmed_trailing": (len(audio) - cut_end) / sample_rate,
        "speech_preroll": pre_roll,
        "speech_postroll": post_roll,
    }


def _initial_artifact_score(candidate: dict[str, Any]) -> float:
    samples = np.asarray(candidate.get("samples"), dtype=np.float32).reshape(-1)
    sample_rate = int(candidate.get("sample_rate") or 0)
    if sample_rate <= 0 or len(samples) < int(sample_rate * 0.12):
        return 0.0
    first = samples[: int(sample_rate * 0.07)]
    later = samples[int(sample_rate * 0.09) : int(sample_rate * 0.34)]
    first_peak = float(np.max(np.abs(first)))
    later_rms = math.sqrt(float(np.mean(later**2)) + 1e-12) if len(later) else 0.0
    max_step = float(np.max(np.abs(np.diff(first)))) if len(first) > 1 else 0.0
    score = 0.0
    # Penalize a short high-energy click/chirp followed by near-silence.
    if first_peak > 0.22 and later_rms < first_peak * 0.11:
        score += 45.0 + min(40.0, first_peak * 35.0)
    if max_step > 0.34:
        score += (max_step - 0.34) * 80.0
    return score


def main() -> None:
    original = Path(os.environ.get("VOXCPM_ORIGINAL_RENDERER", "")).expanduser().resolve()
    if not original.is_file():
        raise RuntimeError(f"Исходный NoChew renderer не найден: {original}")

    namespace = runpy.run_path(str(original), run_name="voxcpm2_quality_v4_base")
    original_score = namespace["candidate_score"]
    original_fit = namespace["fit_without_slowdown"]

    def quality_score(candidate: dict[str, Any], speech_slot: float) -> float:
        score = float(original_score(candidate, speech_slot))
        leading = float(candidate.get("leading_silence") or 0.0)
        if leading > 0.14:
            score += 24.0 + (leading - 0.14) * 95.0
        score += _initial_artifact_score(candidate)
        return score

    def quality_fit(
        clean_path: Path,
        fitted_path: Path,
        target_duration: float,
        tail_guard: float,
    ) -> dict[str, Any]:
        samples, sample_rate = sf.read(clean_path, dtype="float32")
        if np.asarray(samples).ndim > 1:
            samples = np.asarray(samples, dtype=np.float32).mean(axis=1)
        trimmed, trim_report = trim_candidate_edges(np.asarray(samples), int(sample_rate))
        sf.write(clean_path, trimmed, int(sample_rate), subtype="PCM_24")
        report = dict(original_fit(clean_path, fitted_path, target_duration, tail_guard))
        report.update(trim_report)
        report["quality_version"] = _QUALITY_VERSION
        return report

    namespace["candidate_score"] = quality_score
    namespace["fit_without_slowdown"] = quality_fit
    log("reference-only NoChew; requested CFG preserved; retry_badcase remains disabled")
    namespace["main"]()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        import traceback

        print(f"ОШИБКА QUALITY V4: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
