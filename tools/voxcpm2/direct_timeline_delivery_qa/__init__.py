#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Assembled one-speaker continuity gate for the Russian timeline.

The sibling QA still verifies cadence, fit and late tails. This facade adds the
sequence-level contract: all accepted windows must form one synthetic speaker
trajectory, source-supported pitch motion is distinguished from identity drift,
linked gaps must sound connected, and boundary chirps or broadband islands
cannot survive assembly.
"""
from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from tools.voxcpm2 import direct_monolith_contract
from tools.voxcpm2 import direct_source_relative_continuity
from tools.voxcpm2.direct_max_quality_analysis import activity_stats, pitch_profile
from tools.voxcpm2.direct_retry_epoch import invalidate_segment_for_retry
from tools.voxcpm2.direct_russian_cadence import classify_cadence, prosody_contour
from tools.voxcpm2.direct_tail_artifact import detect_late_broadband_tail
from tools.voxcpm2.direct_timbre_analysis import spectral_envelope, spectral_similarity

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "direct_timeline_delivery_qa.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._direct_timeline_delivery_qa_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить assembled delivery QA: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

POLICY = "assembled-monolithic-voice-v1"
SOURCE_RELATIVE_POLICY = direct_source_relative_continuity.POLICY
PREFERRED_CONNECTED_GAP_SECONDS = 0.18
MAX_CONNECTED_GAP_SECONDS = 0.32
ANCHOR_SPECTRAL_FLOOR = 0.56
NEIGHBOUR_SPECTRAL_FLOOR = 0.62
GLOBAL_F0_BASE_LIMIT_ST = 8.4
GLOBAL_F0_SOURCE_HEADROOM_ST = 3.0
GLOBAL_F0_MAX_LIMIT_ST = 13.0

_legacy_verify_timeline_delivery = _legacy.verify_timeline_delivery


def _mono(samples: Any) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.reshape(-1)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _ratio(value: float, reference: float) -> float:
    if value <= 0.0 or reference <= 0.0:
        return 0.0
    return value / reference


def _semitones(value: float, reference: float) -> float | None:
    ratio = _ratio(value, reference)
    return 12.0 * math.log2(ratio) if ratio > 0.0 else None


def _source_pitch(segment: dict[str, Any], key: str) -> float:
    source = segment.get("source_prosody")
    return _finite(source.get(key)) if isinstance(source, dict) else 0.0


def _reference_profile(timeline: Path) -> dict[str, Any]:
    path = timeline.parent.parent / "references" / "extended_reference.wav"
    if not path.is_file():
        raise RuntimeError(f"Monolithic QA не нашёл identity reference: {path}")
    samples, rate = sf.read(str(path), dtype="float32", always_2d=False)
    audio = _mono(samples)
    return {
        "path": str(path),
        "pitch": pitch_profile(audio, int(rate)),
        "timbre": spectral_envelope(audio, int(rate)),
    }


def _window_rows(
    timeline: Path,
    fitted_segments: list[tuple[dict[str, Any], Path]],
) -> tuple[np.ndarray, int, list[dict[str, Any]], dict[str, Any]]:
    samples, sample_rate = sf.read(str(timeline), dtype="float32", always_2d=False)
    audio = _mono(samples)
    rate = max(1, int(sample_rate))
    anchor = _reference_profile(timeline)
    rows: list[dict[str, Any]] = []
    for position, (raw_segment, fitted_path) in enumerate(fitted_segments, start=1):
        segment = dict(raw_segment)
        segment_id = int(segment.get("id") or position)
        delay = max(0, int(segment.get("start_delay_ms", 0) or 0)) / 1000.0
        start = max(0.0, _finite(segment.get("start")) + delay)
        window = max(0.12, _finite(segment.get("end")) - _finite(segment.get("start")))
        left = max(0, int(round(start * rate)))
        right = min(len(audio), int(round((start + window) * rate)))
        clip = audio[left:right]
        contour = prosody_contour(clip, rate)
        active_left = max(0, int(round(_finite(contour.get("active_start")) * rate)))
        active_right = min(len(clip), int(round(_finite(contour.get("active_end")) * rate)))
        if active_right <= active_left:
            active_clip = clip
        else:
            margin = int(rate * 0.025)
            active_clip = clip[
                max(0, active_left - margin):min(len(clip), active_right + margin)
            ]
        pitch = pitch_profile(active_clip, rate)
        timbre = spectral_envelope(active_clip, rate)
        activity = activity_stats(active_clip, rate)
        anchor_similarity = spectral_similarity(timbre, anchor["timbre"])
        start_artifact = direct_monolith_contract._start_artifact(clip, rate)
        tail = detect_late_broadband_tail(clip, rate)
        rows.append(
            {
                "id": segment_id,
                "position": position,
                "segment": segment,
                "fitted_path": str(fitted_path),
                "start": start,
                "window_seconds": window,
                "active_start_time": start + _finite(contour.get("active_start")),
                "active_end_time": start + _finite(contour.get("active_end")),
                "cadence": classify_cadence(str(segment.get("text") or "")),
                "pitch": pitch,
                "timbre": timbre,
                "activity": activity,
                "anchor_spectral_similarity": anchor_similarity,
                "start_artifact": start_artifact,
                "late_tail": tail,
                "failures": [],
                "passed": True,
            }
        )
    return audio, rate, rows, anchor


def _append(row: dict[str, Any], reason: str) -> None:
    failures = row.setdefault("failures", [])
    if reason not in failures:
        failures.append(reason)
    row["passed"] = False


def _identity(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "f0_median": _finite(row["pitch"].get("f0_median")),
        "f0_p90": _finite(row["pitch"].get("f0_p90")),
        "spectral_centroid_hz": _finite(row["timbre"].get("spectral_centroid_hz")),
        "spectral_bands": [
            _finite(value) for value in (row["timbre"].get("bands") or [])
        ],
    }


def _sequence_checks(rows: list[dict[str, Any]]) -> dict[str, float]:
    valid_f0 = [
        _finite(row["pitch"].get("f0_median"))
        for row in rows
        if _finite(row["pitch"].get("voiced_ratio")) >= 0.12
        and _finite(row["pitch"].get("f0_median")) > 45.0
    ]
    valid_centroids = [
        _finite(row["timbre"].get("spectral_centroid_hz"))
        for row in rows
        if _finite(row["timbre"].get("spectral_centroid_hz")) > 80.0
    ]
    valid_source_f0 = [
        _source_pitch(row["segment"], "f0_median")
        for row in rows
        if _source_pitch(row["segment"], "f0_median") > 55.0
    ]
    baseline_f0 = float(np.median(np.asarray(valid_f0))) if valid_f0 else 0.0
    baseline_centroid = (
        float(np.median(np.asarray(valid_centroids))) if valid_centroids else 0.0
    )
    source_baseline_f0 = (
        float(np.median(np.asarray(valid_source_f0))) if valid_source_f0 else 0.0
    )

    for index, row in enumerate(rows):
        pitch = row["pitch"]
        timbre = row["timbre"]
        activity = row["activity"]
        f0_value = _finite(pitch.get("f0_median"))
        f0_ratio = _ratio(f0_value, baseline_f0)
        centroid_ratio = _ratio(
            _finite(timbre.get("spectral_centroid_hz")),
            baseline_centroid,
        )
        generated_global_st = _semitones(f0_value, baseline_f0)
        source_global_st = _semitones(
            _source_pitch(row["segment"], "f0_median"),
            source_baseline_f0,
        )
        global_limit_st = (
            min(
                GLOBAL_F0_MAX_LIMIT_ST,
                max(
                    GLOBAL_F0_BASE_LIMIT_ST,
                    abs(source_global_st) + GLOBAL_F0_SOURCE_HEADROOM_ST,
                ),
            )
            if source_global_st is not None
            else GLOBAL_F0_BASE_LIMIT_ST
        )
        row["baseline_f0_ratio"] = f0_ratio
        row["baseline_spectral_centroid_ratio"] = centroid_ratio
        row["generated_global_f0_jump_st"] = generated_global_st
        row["source_global_f0_jump_st"] = source_global_st
        row["allowed_global_f0_jump_st"] = global_limit_st
        if row["anchor_spectral_similarity"] < ANCHOR_SPECTRAL_FLOOR:
            _append(row, "identity_anchor_spectral_mismatch")
        if generated_global_st is not None and abs(generated_global_st) > global_limit_st:
            _append(row, "global_voice_f0_outlier")
        if centroid_ratio and (centroid_ratio < 0.54 or centroid_ratio > 1.82):
            _append(row, "global_spectral_posture_outlier")
        if row["start_artifact"].get("suspicious"):
            _append(row, "assembled_start_reference_leak")
        if row["late_tail"].get("suspicious"):
            _append(row, "assembled_late_broadband_tail")
        if _finite(activity.get("active_ratio")) < 0.16:
            _append(row, "assembled_insufficient_active_speech")

        if index == 0:
            continue
        previous = rows[index - 1]
        neighbour_similarity = spectral_similarity(timbre, previous["timbre"])
        neighbour_f0 = _ratio(
            _finite(pitch.get("f0_median")),
            _finite(previous["pitch"].get("f0_median")),
        )
        neighbour_p90 = _ratio(
            _finite(pitch.get("f0_p90")),
            _finite(previous["pitch"].get("f0_p90")),
        )
        rms_jump = abs(
            _finite(activity.get("rms_dbfs"), -120.0)
            - _finite(previous["activity"].get("rms_dbfs"), -120.0)
        )
        transition = direct_source_relative_continuity.evaluate_transition(
            current_identity=_identity(row),
            previous_identity=_identity(previous),
            current_segment=row["segment"],
            previous_segment=previous["segment"],
        )
        row["neighbour_spectral_similarity"] = neighbour_similarity
        row["neighbour_f0_median_ratio"] = neighbour_f0
        row["neighbour_f0_p90_ratio"] = neighbour_p90
        row["neighbour_rms_jump_db"] = rms_jump
        row["source_relative_transition"] = transition
        if neighbour_similarity < NEIGHBOUR_SPECTRAL_FLOOR:
            _append(row, "adjacent_voice_timbre_discontinuity")
        if transition.get("source_available"):
            for reason in transition.get("failures") or []:
                _append(row, str(reason))
        else:
            # Complete source evidence is unavailable: preserve conservative
            # absolute fallbacks instead of guessing that emotion caused the jump.
            if neighbour_f0 and (neighbour_f0 < 0.62 or neighbour_f0 > 1.62):
                _append(row, "adjacent_voice_pitch_discontinuity")
            if neighbour_p90 and (neighbour_p90 < 0.58 or neighbour_p90 > 1.72):
                _append(row, "adjacent_voice_range_discontinuity")
        if rms_jump > 10.0 and neighbour_similarity < 0.72:
            _append(row, "adjacent_voice_level_and_timbre_jump")
        if (
            f0_ratio
            and (f0_ratio < 0.70 or f0_ratio > 1.50)
            and neighbour_similarity < 0.74
        ):
            _append(row, "combined_global_and_adjacent_voice_shift")

    for index in range(len(rows) - 1):
        current = rows[index]
        following = rows[index + 1]
        gap = max(
            0.0,
            _finite(following.get("active_start_time"))
            - _finite(current.get("active_end_time")),
        )
        current["gap_to_next_seconds"] = gap
        if current["cadence"] not in {"linked", "continuation"}:
            continue
        current["preferred_connected_gap_seconds"] = PREFERRED_CONNECTED_GAP_SECONDS
        current["max_connected_gap_seconds"] = MAX_CONNECTED_GAP_SECONDS
        if gap > MAX_CONNECTED_GAP_SECONDS:
            _append(current, "connected_phrase_gap")
    return {
        "baseline_f0_median": baseline_f0,
        "source_baseline_f0_median": source_baseline_f0,
        "baseline_spectral_centroid_hz": baseline_centroid,
    }


def _invalidate_failures(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    invalidated: list[dict[str, Any]] = []
    for row in rows:
        if row.get("passed") is True:
            continue
        segment = dict(row["segment"])
        fitted = Path(row["fitted_path"])
        invalidated.append(
            invalidate_segment_for_retry(
                fitted.parent.parent,
                segment,
                reason="monolithic_delivery:" + ",".join(row.get("failures") or ["qa"]),
                fitted_path=fitted,
                evidence={
                    "policy": POLICY,
                    "source_relative_policy": SOURCE_RELATIVE_POLICY,
                    "anchor_spectral_similarity": row.get("anchor_spectral_similarity"),
                    "baseline_f0_ratio": row.get("baseline_f0_ratio"),
                    "generated_global_f0_jump_st": row.get("generated_global_f0_jump_st"),
                    "source_global_f0_jump_st": row.get("source_global_f0_jump_st"),
                    "neighbour_spectral_similarity": row.get("neighbour_spectral_similarity"),
                    "neighbour_f0_median_ratio": row.get("neighbour_f0_median_ratio"),
                    "source_relative_transition": row.get("source_relative_transition"),
                    "gap_to_next_seconds": row.get("gap_to_next_seconds"),
                    "start_artifact": row.get("start_artifact"),
                    "late_tail": row.get("late_tail"),
                },
            )
        )
    return invalidated


def verify_timeline_delivery(
    timeline: Path,
    fitted_segments: list[tuple[dict[str, Any], Path]],
) -> dict[str, Any]:
    base = _legacy_verify_timeline_delivery(timeline, fitted_segments)
    audio, rate, rows, anchor = _window_rows(Path(timeline), fitted_segments)
    baseline = _sequence_checks(rows)

    # Dedicated whole-file tail pass catches noise extending beyond the nominal
    # last cue analysis window after mixing/padding.
    if rows:
        tail_seconds = min(2.2, len(audio) / max(1, rate))
        tail_left = max(0, len(audio) - int(tail_seconds * rate))
        whole_tail = detect_late_broadband_tail(audio[tail_left:], rate)
        if whole_tail.get("suspicious"):
            whole_tail = dict(whole_tail)
            for key in ("trim_time", "last_sustained_voice_end", "burst_start", "burst_end"):
                if key in whole_tail:
                    whole_tail[key] = _finite(whole_tail[key]) + tail_left / rate
            rows[-1]["whole_timeline_tail"] = whole_tail
            _append(rows[-1], "whole_timeline_late_broadband_tail")

    failed = [int(row["id"]) for row in rows if row.get("passed") is not True]
    invalidated = _invalidate_failures(rows) if failed else []
    report = {
        "schema_version": 2,
        "policy": POLICY,
        "source_relative_policy": SOURCE_RELATIVE_POLICY,
        "timeline": str(timeline),
        "sample_rate": rate,
        "identity_reference": anchor,
        "baseline": baseline,
        "gap_policy": {
            "preferred_connected_seconds": PREFERRED_CONNECTED_GAP_SECONDS,
            "hard_connected_seconds": MAX_CONNECTED_GAP_SECONDS,
        },
        "segments": rows,
        "failed_segment_ids": failed,
        "invalidated_for_retry": invalidated,
        "passed": not failed,
        "base_delivery_qa": base,
    }
    report_path = Path(timeline).with_suffix(".monolith_qa.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    if failed:
        details = "; ".join(
            f"#{row['id']}: {','.join(row.get('failures') or ['qa'])}"
            for row in rows
            if row.get("passed") is not True
        )
        raise RuntimeError(
            "Собранная дорожка звучит не как один монолитный голос; "
            "проваленные сегменты переведены на новые seed epochs: " + details
        )
    return report


_legacy.verify_timeline_delivery = verify_timeline_delivery

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {
        "ANCHOR_SPECTRAL_FLOOR",
        "MAX_CONNECTED_GAP_SECONDS",
        "NEIGHBOUR_SPECTRAL_FLOOR",
        "POLICY",
        "PREFERRED_CONNECTED_GAP_SECONDS",
        "SOURCE_RELATIVE_POLICY",
        "verify_timeline_delivery",
    }
)
