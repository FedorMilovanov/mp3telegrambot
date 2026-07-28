#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Source-guided expressive continuity for clean Dub Studio production.

The renderer still synthesizes short, stable phrases.  This module analyses the
corresponding source-speech windows, builds a smoothed emotional/prosodic arc,
and writes short VoxCPM2 style-control instructions into the segment metadata.
It never changes the Russian text and never wraps or patches the renderer.
"""
from __future__ import annotations

import json
import math
import re
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from tools.voxcpm2 import professional_audio_v45 as audio_policy

POLICY = "source-guided-expression-v1"


def _words(value: str) -> int:
    return len(re.findall(r"\w+", str(value or ""), flags=re.UNICODE))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _window(item: dict[str, Any], duration: float) -> tuple[float, float]:
    start = max(
        0.0,
        _number(item.get("original_srt_start", item.get("start", 0.0))),
    )
    end = _number(item.get("source_end", item.get("end", start)))
    if end <= start:
        end = _number(item.get("end", start + 0.35)) + max(
            0,
            int(item.get("start_delay_ms", 0) or 0),
        ) / 1000.0
    return min(float(duration), start), min(float(duration), max(start + 0.35, end))


def _robust_z(values: list[float], valid: list[bool]) -> list[float]:
    usable = np.asarray(
        [value for value, keep in zip(values, valid, strict=True) if keep and math.isfinite(value)],
        dtype=np.float64,
    )
    if len(usable) < 2:
        return [0.0 for _ in values]
    median = float(np.median(usable))
    mad = float(np.median(np.abs(usable - median))) * 1.4826
    scale = mad if mad >= 1e-4 else float(np.std(usable))
    scale = max(scale, 1e-4)
    return [
        max(-2.5, min(2.5, (float(value) - median) / scale)) if keep else 0.0
        for value, keep in zip(values, valid, strict=True)
    ]


def _smooth(values: list[float]) -> list[float]:
    if len(values) <= 1:
        return list(values)
    first: list[float] = []
    for index, value in enumerate(values):
        previous = values[index - 1] if index else value
        following = values[index + 1] if index + 1 < len(values) else value
        first.append(previous * 0.22 + value * 0.56 + following * 0.22)

    # Preserve a real emotional build, but prevent adjacent segments from jumping
    # from reflective to passionate merely because of one noisy local estimate.
    limited = [first[0]]
    for value in first[1:]:
        limited.append(max(limited[-1] - 0.72, min(limited[-1] + 0.72, value)))
    for index in range(len(limited) - 2, -1, -1):
        limited[index] = max(
            limited[index + 1] - 0.72,
            min(limited[index + 1] + 0.72, limited[index]),
        )
    return [max(-1.65, min(1.65, value)) for value in limited]


def _style(score: float, rate_z: float, text: str) -> tuple[str, str]:
    punctuation = str(text or "")
    if score <= -0.72:
        tier = "reflective"
        instruction = "calm and reflective, sincere, slightly slower, natural connected phrasing"
    elif score <= -0.12:
        tier = "warm"
        instruction = "warm and sincere, gently expressive, natural pace and pauses"
    elif score <= 0.48:
        tier = "earnest"
        instruction = "earnest and conversational, clearly shaped emphasis, natural rhythm"
    elif score <= 1.08:
        tier = "emphatic"
        instruction = "firm and emphatic, controlled intensity, clear stress, do not shout"
    else:
        tier = "passionate"
        instruction = "passionate and urgent but controlled, strong emphasis, never shout"

    if rate_z <= -0.85 and "slightly slower" not in instruction:
        instruction += ", slightly slower"
    elif rate_z >= 1.05 and tier not in {"reflective", "warm"}:
        instruction += ", slightly quicker"
    if "?" in punctuation and tier in {"warm", "earnest"}:
        instruction += ", genuine questioning intonation"
    return tier, instruction


def plan_segments(
    *,
    source: Path,
    segments: list[dict[str, Any]],
    duration: float,
    report_path: Path,
) -> list[dict[str, Any]]:
    """Attach a smooth source-derived performance plan without changing words."""
    if not source.is_file():
        raise RuntimeError(f"Не найден source для анализа эмоциональности: {source}")
    if not segments:
        raise RuntimeError("Нельзя построить эмоциональную дугу для пустых реплик.")

    original_text = [str(item.get("text") or "") for item in segments]
    with tempfile.TemporaryDirectory(prefix="dub-expression-") as temp_raw:
        decoded = Path(temp_raw) / "source.wav"
        samples, sample_rate = audio_policy._decode(source, decoded)
        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        sample_rate = int(sample_rate)

        metrics: list[dict[str, Any]] = []
        rms_values: list[float] = []
        pitch_values: list[float] = []
        rate_values: list[float] = []
        pitch_valid: list[bool] = []

        for item in segments:
            start, end = _window(item, duration)
            clip = audio[int(start * sample_rate) : int(end * sample_rate)]
            if len(clip) < max(1, int(sample_rate * 0.25)):
                clip = np.zeros(max(1, int(sample_rate * 0.35)), dtype=np.float32)
            pitch = audio_policy.pitch_profile(clip, sample_rate)
            activity = audio_policy.activity_stats(clip, sample_rate)
            source_text = str(item.get("source") or item.get("text") or "")
            speech_rate = _words(source_text) / max(0.35, end - start)
            valid_pitch = bool(
                _number(pitch.get("voiced_ratio")) >= 0.12
                and _number(pitch.get("f0_median")) >= 55.0
            )
            metrics.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "speech_rate": round(speech_rate, 4),
                    **{key: round(_number(value), 5) for key, value in pitch.items()},
                    **{key: round(_number(value), 5) for key, value in activity.items()},
                }
            )
            rms_values.append(_number(activity.get("rms_dbfs"), -60.0))
            pitch_values.append(_number(pitch.get("f0_p90") or pitch.get("f0_median"), 0.0))
            rate_values.append(speech_rate)
            pitch_valid.append(valid_pitch)

    rms_z = _robust_z(rms_values, [True] * len(rms_values))
    pitch_z = _robust_z(pitch_values, pitch_valid)
    rate_z = _robust_z(rate_values, [True] * len(rate_values))

    raw_scores: list[float] = []
    for index, item in enumerate(segments):
        text = str(item.get("text") or "")
        punctuation_boost = 0.0
        if "!" in text:
            punctuation_boost += 0.24
        if "?" in text:
            punctuation_boost += 0.10
        if re.search(r"(?:^|\s)[—–-](?:\s|$)|:", text):
            punctuation_boost += 0.05
        raw_scores.append(
            rms_z[index] * 0.52
            + pitch_z[index] * 0.30
            + rate_z[index] * 0.18
            + punctuation_boost
        )

    scores = _smooth(raw_scores)
    result: list[dict[str, Any]] = []
    report_segments: list[dict[str, Any]] = []
    for index, (item, score) in enumerate(zip(segments, scores, strict=True)):
        updated = dict(item)
        tier, instruction = _style(score, rate_z[index], str(item.get("text") or ""))
        updated.update(
            {
                "style_instruction": instruction,
                "expression_tier": tier,
                "expression_score": round(float(score), 5),
                "expression_policy": POLICY,
                "source_prosody": metrics[index],
            }
        )
        result.append(updated)
        report_segments.append(
            {
                "id": int(updated.get("id") or index + 1),
                "tier": tier,
                "score": round(float(score), 5),
                "raw_score": round(float(raw_scores[index]), 5),
                "style_instruction": instruction,
                "source_prosody": metrics[index],
            }
        )

    if [str(item.get("text") or "") for item in result] != original_text:
        raise RuntimeError("План эмоциональности изменил русский текст; операция остановлена.")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "policy": POLICY,
                "source": str(source),
                "segments": report_segments,
                "notes": [
                    "Short synthesis windows are retained for stability.",
                    "Expression is derived from source F0, energy and speaking rate.",
                    "Adjacent scores are smoothed and limited to prevent robotic jumps.",
                    "Russian text is not changed.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return result


__all__ = ["POLICY", "plan_segments"]
