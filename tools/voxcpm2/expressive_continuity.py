#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Source-guided expressive continuity for clean Dub Studio production.

Short synthesis windows remain for timing stability.  This module analyses the
matching source-speech windows, builds a smoothed emotional/prosodic arc, assigns
either a calm or controlled-expressive real voice reference, and writes a fully
transparent report.  It never changes the Russian text and never wraps VoxCPM.
"""
from __future__ import annotations

import json
import math
import re
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from tools.voxcpm2 import professional_audio_v45 as audio_policy
from tools.voxcpm2.direct_russian_cadence import (
    classify_cadence,
    prosody_contour,
)

POLICY = "source-guided-expression-v2"


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
        [
            value
            for value, keep in zip(values, valid, strict=True)
            if keep and math.isfinite(value)
        ],
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
    # from reflective to passionate because of one noisy local estimate.
    limited = [first[0]]
    for value in first[1:]:
        limited.append(max(limited[-1] - 0.72, min(limited[-1] + 0.72, value)))
    for index in range(len(limited) - 2, -1, -1):
        limited[index] = max(
            limited[index + 1] - 0.72,
            min(limited[index + 1] + 0.72, limited[index]),
        )
    return [max(-1.65, min(1.65, value)) for value in limited]


def _source_style(
    score: float,
    rate_z: float,
    text: str,
) -> tuple[str, str]:
    punctuation = str(text or "")
    cadence = classify_cadence(punctuation)
    if score <= -0.72:
        tier = "reflective"
        instruction = (
            "calm and reflective, sincere, slightly slower, natural connected phrasing"
        )
    elif score <= -0.12:
        tier = "warm"
        instruction = "warm and sincere, gently expressive, natural pace and pauses"
    elif score <= 0.48:
        tier = "earnest"
        instruction = (
            "earnest and conversational, clearly shaped emphasis, natural rhythm"
        )
    elif score <= 1.08:
        tier = "emphatic"
        instruction = (
            "firm and emphatic, controlled intensity, clear stress, do not shout"
        )
    else:
        tier = "passionate"
        instruction = (
            "passionate and urgent but controlled, strong emphasis, never shout"
        )

    if rate_z <= -0.85 and "slightly slower" not in instruction:
        instruction += ", slightly slower"
    elif rate_z >= 1.05 and tier not in {"reflective", "warm"}:
        instruction += ", slightly quicker"

    if cadence == "question":
        instruction += ", genuine questioning cadence"
    elif cadence in {"terminal", "firm_terminal"}:
        instruction += ", clearly finish the sentence with a natural falling cadence"
    elif cadence in {"continuation", "linked"}:
        instruction += ", keep the phrase connected and open toward the next line"
    elif cadence == "suspense":
        instruction += ", hold restrained suspense without a full stop"
    return tier, instruction


def _reference_profile(tier: str) -> str:
    """Use real expressive source delivery only for the stronger arc sections."""
    return "composite" if tier in {"emphatic", "passionate"} else "extended"


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
            contour = prosody_contour(clip, sample_rate)
            metrics.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "speech_rate": round(speech_rate, 4),
                    "contour": contour,
                    **{
                        key: round(_number(value), 5)
                        for key, value in pitch.items()
                    },
                    **{
                        key: round(_number(value), 5)
                        for key, value in activity.items()
                    },
                }
            )
            rms_values.append(_number(activity.get("rms_dbfs"), -60.0))
            pitch_values.append(
                _number(pitch.get("f0_p90") or pitch.get("f0_median"), 0.0)
            )
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
        text = str(item.get("text") or "")
        tier, instruction = _source_style(
            score,
            rate_z[index],
            text,
        )
        profile = _reference_profile(tier)
        updated.update(
            {
                "reference_profile": profile,
                "style_instruction": instruction,
                "expression_tier": tier,
                "expression_score": round(float(score), 5),
                "expression_policy": POLICY,
                "cadence_type": classify_cadence(text),
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
                "reference_profile": profile,
                "cadence_type": updated["cadence_type"],
                "style_instruction": instruction,
                "source_prosody": metrics[index],
            }
        )

    if [str(item.get("text") or "") for item in result] != original_text:
        raise RuntimeError(
            "План эмоциональности изменил русский текст; операция остановлена."
        )

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
                    "Five-bin source pitch/energy contours guide within-phrase emphasis.",
                    "Russian punctuation defines terminal, question and continuation cadence.",
                    "Adjacent scores are smoothed and limited to prevent robotic jumps.",
                    "Strong arc sections use a controlled expressive real voice reference.",
                    "Russian text is not changed.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return result


def plan_json(
    *,
    source: Path,
    segments_path: Path,
    duration: float,
    report_path: Path,
) -> list[dict[str, Any]]:
    """Plan expression and atomically replace the segment JSON metadata."""
    payload = json.loads(segments_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("segments_ru_final.json пуст или повреждён.")
    segments = [dict(item) for item in payload if isinstance(item, dict)]
    if len(segments) != len(payload):
        raise RuntimeError("segments_ru_final.json содержит повреждённые записи.")
    planned = plan_segments(
        source=source,
        segments=segments,
        duration=duration,
        report_path=report_path,
    )
    temporary = segments_path.with_suffix(segments_path.suffix + ".expression.tmp")
    temporary.write_text(
        json.dumps(planned, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(segments_path)
    return planned


def _trim_active(clip: np.ndarray, sample_rate: int) -> np.ndarray:
    """Trim only outer silence, never internal rhetorical pauses."""
    audio = np.asarray(clip, dtype=np.float32).reshape(-1)
    frame = max(160, int(sample_rate * 0.020))
    hop = max(80, int(sample_rate * 0.010))
    if len(audio) < frame:
        return audio
    starts = list(range(0, len(audio) - frame + 1, hop))
    levels = np.asarray(
        [math.sqrt(float(np.mean(audio[start : start + frame] ** 2)) + 1e-12) for start in starts],
        dtype=np.float64,
    )
    threshold = max(10 ** (-43.0 / 20.0), float(np.percentile(levels, 35)) * 1.45)
    active = np.where(levels >= threshold)[0]
    if not len(active):
        return audio
    left = max(0, starts[int(active[0])] - int(sample_rate * 0.045))
    right = min(
        len(audio),
        starts[int(active[-1])] + frame + int(sample_rate * 0.070),
    )
    return audio[left:right]


def _expressive_candidates(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    voiced = [
        _number((item.get("source_prosody") or {}).get("f0_median"))
        for item in segments
        if _number((item.get("source_prosody") or {}).get("voiced_ratio")) >= 0.12
        and _number((item.get("source_prosody") or {}).get("f0_median")) >= 55.0
    ]
    p90_values = [
        _number((item.get("source_prosody") or {}).get("f0_p90"))
        for item in segments
        if _number((item.get("source_prosody") or {}).get("voiced_ratio")) >= 0.12
        and _number((item.get("source_prosody") or {}).get("f0_p90")) >= 60.0
    ]
    rms_values = [
        _number((item.get("source_prosody") or {}).get("rms_dbfs"), -60.0)
        for item in segments
    ]
    median_f0 = float(np.median(voiced)) if voiced else 120.0
    median_p90 = float(np.median(p90_values)) if p90_values else median_f0 * 1.30
    median_rms = float(np.median(rms_values)) if rms_values else -24.0

    result: list[dict[str, Any]] = []
    for item in segments:
        metrics = item.get("source_prosody") or {}
        start = _number(metrics.get("start"), _number(item.get("start")))
        end = _number(metrics.get("end"), _number(item.get("source_end", item.get("end"))))
        expression = _number(item.get("expression_score"))
        f0 = _number(metrics.get("f0_median"))
        p90 = _number(metrics.get("f0_p90"))
        voiced_ratio = _number(metrics.get("voiced_ratio"))
        active_ratio = _number(metrics.get("active_ratio"))
        internal_gap = _number(metrics.get("max_internal_gap"), 99.0)
        rms = _number(metrics.get("rms_dbfs"), -60.0)
        span = end - start
        safe = bool(
            0.20 <= expression <= 1.35
            and span >= 1.15
            and voiced_ratio >= 0.16
            and active_ratio >= 0.30
            and internal_gap <= 0.75
            and 55.0 <= f0 <= median_f0 * 1.30
            and 60.0 <= p90 <= median_p90 * 1.40
            and rms <= median_rms + 8.0
        )
        if not safe:
            continue
        selection_score = (
            abs(expression - 0.72) * 42.0
            + max(0.0, f0 / max(1.0, median_f0) - 1.12) * 20.0
            + internal_gap * 12.0
            + abs(active_ratio - 0.72) * 15.0
        )
        result.append(
            {
                "id": int(item.get("id") or 0),
                "start": start,
                "end": end,
                "expression_score": expression,
                "selection_score": selection_score,
                "metrics": dict(metrics),
            }
        )
    return sorted(result, key=lambda item: (item["selection_score"], item["start"]))


def build_controlled_expressive_reference(
    *,
    source: Path,
    segments: list[dict[str, Any]],
    output: Path,
    target_seconds: float = 7.0,
) -> bool:
    """Overwrite composite with engaged source delivery while rejecting shouting."""
    candidates = _expressive_candidates(segments)
    if not candidates:
        return False

    selected: list[dict[str, Any]] = []
    accumulated = 0.0
    for candidate in candidates:
        if any(
            min(candidate["end"], existing["end"])
            - max(candidate["start"], existing["start"])
            > 0.25
            for existing in selected
        ):
            continue
        selected.append(candidate)
        accumulated += min(3.4, candidate["end"] - candidate["start"])
        if accumulated >= max(4.8, float(target_seconds)):
            break
    if accumulated < 3.2:
        return False

    with tempfile.TemporaryDirectory(prefix="dub-expressive-ref-") as temp_raw:
        decoded = Path(temp_raw) / "source.wav"
        samples, sample_rate = audio_policy._decode(source, decoded)
        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        sample_rate = int(sample_rate)
        parts: list[np.ndarray] = []
        actual_selected: list[dict[str, Any]] = []
        total = 0.0
        for candidate in selected:
            if total >= float(target_seconds):
                break
            start = max(0.0, float(candidate["start"]))
            end = min(float(candidate["end"]), start + 3.4)
            clip = audio[int(start * sample_rate) : int(end * sample_rate)]
            clip = _trim_active(clip, sample_rate)
            remaining = int(max(0.0, float(target_seconds) - total) * sample_rate)
            if remaining <= 0:
                break
            clip = clip[:remaining]
            if len(clip) < int(sample_rate * 0.85):
                continue
            parts.append(clip)
            clip_duration = len(clip) / sample_rate
            total += clip_duration
            actual_selected.append(
                {
                    "id": candidate["id"],
                    "start": round(start, 3),
                    "end": round(start + clip_duration, 3),
                    "expression_score": round(candidate["expression_score"], 5),
                    "selection_score": round(candidate["selection_score"], 5),
                    **{
                        key: round(_number(value), 5)
                        for key, value in candidate["metrics"].items()
                        if isinstance(value, (int, float))
                    },
                }
            )

        if not parts or total < 3.2:
            return False
        combined = audio_policy.dub_quality_v4._crossfade(parts, sample_rate)
        combined = combined[: int(float(target_seconds) * sample_rate)]
        rms = math.sqrt(float(np.mean(combined**2)) + 1e-12)
        peak = float(np.max(np.abs(combined))) + 1e-12
        gain = min(
            10 ** (-24.0 / 20.0) / max(rms, 1e-9),
            10 ** (-3.0 / 20.0) / peak,
            10 ** (5.0 / 20.0),
        )
        combined = np.clip(combined * gain, -0.999, 0.999).astype(np.float32)
        fade = min(int(sample_rate * 0.025), len(combined) // 8)
        if fade > 1:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            combined[:fade] *= ramp
            combined[-fade:] *= ramp[::-1]

        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output, combined, sample_rate, subtype="PCM_24")

    output.with_suffix(".selection.json").write_text(
        json.dumps(
            {
                "policy": POLICY,
                "profile": "controlled_expressive",
                "purpose": "real source prosody for emphatic arc sections; shouting rejected",
                "selected": actual_selected,
                "duration_seconds": round(len(combined) / sample_rate, 4),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return True


__all__ = [
    "POLICY",
    "build_controlled_expressive_reference",
    "plan_json",
    "plan_segments",
]

_BASE_ALL = tuple(globals().get('__all__', ()))

import json

import math

from pathlib import Path

from typing import Any

from tools.voxcpm2 import russian_pronunciation

POLICY = "source-guided-monolithic-expression-v3"

REFERENCE_POLICY = "single-calm-identity-reference-v1"

ARC_POLICY = "bounded-neighbour-supported-emotion-v1"

MAX_ADJACENT_SCORE_STEP = 0.26

MIN_STRONG_NEIGHBOUR_SCORE = 0.20

_legacy_plan_segments = plan_segments

def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return result if math.isfinite(result) else float(default)

def _monolithic_scores(values: list[float]) -> list[float]:
    if not values:
        return []
    current = [max(-0.72, min(0.72, _number(value))) for value in values]
    for _ in range(2):
        smoothed: list[float] = []
        for index, value in enumerate(current):
            left = current[index - 1] if index else value
            right = current[index + 1] if index + 1 < len(current) else value
            smoothed.append(left * 0.24 + value * 0.52 + right * 0.24)
        limited = [smoothed[0]]
        for value in smoothed[1:]:
            limited.append(
                max(
                    limited[-1] - MAX_ADJACENT_SCORE_STEP,
                    min(limited[-1] + MAX_ADJACENT_SCORE_STEP, value),
                )
            )
        for index in range(len(limited) - 2, -1, -1):
            limited[index] = max(
                limited[index + 1] - MAX_ADJACENT_SCORE_STEP,
                min(limited[index + 1] + MAX_ADJACENT_SCORE_STEP, limited[index]),
            )
        current = [max(-0.65, min(0.68, value)) for value in limited]
    return current

def _tier(scores: list[float], index: int) -> str:
    score = scores[index]
    if score <= -0.38:
        return "reflective"
    if score <= -0.04:
        return "warm"
    if score <= 0.38:
        return "earnest"
    neighbours: list[float] = []
    if index:
        neighbours.append(scores[index - 1])
    if index + 1 < len(scores):
        neighbours.append(scores[index + 1])
    return (
        "emphatic"
        if neighbours and max(neighbours) >= MIN_STRONG_NEIGHBOUR_SCORE
        else "earnest"
    )

def _style(tier: str, cadence: str) -> str:
    base = {
        "reflective": "calm reflective delivery, natural connected phrasing",
        "warm": "warm sincere delivery, natural connected phrasing",
        "earnest": "earnest conversational delivery, restrained natural emphasis",
        "emphatic": "slightly firmer emphasis, controlled and conversational, never theatrical",
    }.get(tier, "natural connected conversational delivery")
    if cadence in {"linked", "continuation"}:
        return base + ", carry the thought smoothly into the next phrase"
    if cadence == "question":
        return base + ", genuine but restrained questioning cadence"
    if cadence in {"terminal", "firm_terminal"}:
        return base + ", finish naturally without a sudden emotional burst"
    return base

def plan_segments(
    *,
    source: Path,
    segments: list[dict[str, Any]],
    duration: float,
    report_path: Path,
) -> list[dict[str, Any]]:
    original_text = [str(item.get("text") or "") for item in segments]
    measured = _legacy_plan_segments(
        source=source,
        segments=segments,
        duration=duration,
        report_path=report_path,
    )
    if len(measured) != len(original_text):
        raise RuntimeError("Source expression analysis изменил число сегментов.")
    scores = _monolithic_scores(
        [_number(item.get("expression_score")) for item in measured]
    )
    result: list[dict[str, Any]] = []
    report_segments: list[dict[str, Any]] = []
    for index, item in enumerate(measured):
        updated = dict(item)
        tier = _tier(scores, index)
        cadence = str(
            updated.get("cadence_type")
            or classify_cadence(original_text[index])
        )
        updated.update(
            expression_policy=POLICY,
            expression_arc_policy=ARC_POLICY,
            expression_score=round(scores[index], 6),
            expression_tier=tier,
            reference_profile="extended",
            identity_reference_profile="extended",
            reference_policy=REFERENCE_POLICY,
            style_instruction=_style(tier, cadence),
            cadence_type=cadence,
        )
        updated["pronunciation"] = russian_pronunciation.prepare_segment(updated)
        result.append(updated)
        report_segments.append(
            {
                "id": int(updated.get("id") or index + 1),
                "tier": tier,
                "score": round(scores[index], 6),
                "raw_score": round(_number(item.get("expression_score")), 6),
                "reference_profile": "extended",
                "identity_reference_profile": "extended",
                "cadence_type": cadence,
                "style_instruction": updated["style_instruction"],
                "pronunciation": updated["pronunciation"],
                "source_prosody": updated.get("source_prosody") or {},
            }
        )

    if [str(item.get("text") or "") for item in result] != original_text:
        raise RuntimeError("Monolithic expression plan изменил русский текст.")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "policy": POLICY,
                "reference_policy": REFERENCE_POLICY,
                "arc_policy": ARC_POLICY,
                "source": str(source),
                "segments": report_segments,
                "notes": [
                    "Every segment uses the same extended identity reference.",
                    "Adjacent expression scores are low-pass filtered and step-limited.",
                    "An isolated strong cue is downgraded unless a neighbour supports the build.",
                    "Passionate/character-acting tiers are disabled for short-form dubbing.",
                    "Display and ASR text remain unchanged; synthesis text is explicit metadata.",
                ],
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    return result

def plan_json(
    *,
    source: Path,
    segments_path: Path,
    duration: float,
    report_path: Path,
) -> list[dict[str, Any]]:
    payload = json.loads(segments_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("segments_ru_final.json пуст или повреждён.")
    if any(not isinstance(item, dict) for item in payload):
        raise RuntimeError("segments_ru_final.json содержит повреждённые записи.")
    planned = plan_segments(
        source=source,
        segments=[dict(item) for item in payload],
        duration=duration,
        report_path=report_path,
    )
    temporary = segments_path.with_suffix(segments_path.suffix + ".monolith.tmp")
    temporary.write_text(
        json.dumps(planned, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(segments_path)
    return planned

def build_controlled_expressive_reference(
    *,
    source: Path,
    segments: list[dict[str, Any]],
    output: Path,
    target_seconds: float = 7.0,
) -> bool:
    """Keep the calm composite; expression may not replace speaker identity."""
    return False

POLICY = POLICY

plan_segments = plan_segments

plan_json = plan_json

build_controlled_expressive_reference = build_controlled_expressive_reference

__all__ = sorted(
    set(_BASE_ALL)
    | {
        "ARC_POLICY",
        "MAX_ADJACENT_SCORE_STEP",
        "MIN_STRONG_NEIGHBOUR_SCORE",
        "POLICY",
        "REFERENCE_POLICY",
        "_legacy_plan_segments",
        "build_controlled_expressive_reference",
        "plan_json",
        "plan_segments",
    }
)
