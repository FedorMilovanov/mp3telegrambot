#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cheap timeline repair for valid speech placed too late inside an SRT window.

Independent QA measures the final assembled WAV. A model may produce a clean,
semantically correct phrase with excess leading silence. Regenerating that phrase
for hours is unnecessary: when every non-timing check passes and the ending has
safe room, this module shifts the existing PCM earlier inside the same immutable
SRT window and leaves synthesis checkpoints untouched.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

POLICY = "timeline-onset-cheap-repair-v1"
TARGET_ONSET_MS = 120.0
MAX_SHIFT_MS = 4_000.0


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _component_passed(check: dict[str, Any], name: str) -> bool:
    component = check.get(name)
    return not isinstance(component, dict) or component.get("passed") is not False


def repairable_timing_failure(check: dict[str, Any]) -> bool:
    """Return True only for a late-onset-only failure with intact speech evidence."""
    timing = check.get("timing")
    if not isinstance(timing, dict) or timing.get("passed") is not False:
        return False
    onset_ms = _number(timing.get("onset_ms"), -1.0)
    max_onset_ms = _number(timing.get("max_onset_ms"), 220.0)
    trailing_ms = _number(timing.get("trailing_ms"), -1.0)
    min_trailing_ms = _number(timing.get("min_trailing_ms"), 45.0)
    if (
        onset_ms <= max_onset_ms
        or onset_ms > MAX_SHIFT_MS + TARGET_ONSET_MS
        or trailing_ms < min_trailing_ms
        or timing.get("isolated_start_artifact") is True
    ):
        return False
    return all(
        _component_passed(check, name)
        for name in (
            "semantic",
            "acoustic",
            "continuity_v45",
            "voice_match_v45",
        )
    )


def repairable_segment_ids(report: dict[str, Any]) -> list[int]:
    result: list[int] = []
    for item in report.get("segments", []):
        if not isinstance(item, dict) or not repairable_timing_failure(item):
            continue
        try:
            segment_id = int(item.get("id"))
        except (TypeError, ValueError, OverflowError):
            continue
        if segment_id > 0:
            result.append(segment_id)
    return sorted(set(result))


def _segment_map(segments: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in segments:
        if not isinstance(item, dict):
            continue
        try:
            segment_id = int(item.get("id"))
        except (TypeError, ValueError, OverflowError):
            continue
        if segment_id > 0:
            result[segment_id] = item
    return result


def _check_map(report: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in report.get("segments", []):
        if not isinstance(item, dict):
            continue
        try:
            segment_id = int(item.get("id"))
        except (TypeError, ValueError, OverflowError):
            continue
        if segment_id > 0:
            result[segment_id] = item
    return result


def _shift_window_earlier(window: np.ndarray, shift_samples: int) -> np.ndarray:
    if shift_samples <= 0 or shift_samples >= len(window):
        return np.asarray(window).copy()
    repaired = np.zeros_like(window)
    repaired[: len(window) - shift_samples] = window[shift_samples:]
    return repaired


def repair_timeline_onsets(
    timeline: Path,
    segments: list[dict[str, Any]],
    report: dict[str, Any],
    *,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """Shift only proven late-onset speech earlier inside its existing window."""
    timeline = Path(timeline)
    if not timeline.is_file():
        raise RuntimeError(f"Не найден timeline для onset repair: {timeline}")

    requested = repairable_segment_ids(report)
    segment_by_id = _segment_map(segments)
    check_by_id = _check_map(report)
    info = sf.info(str(timeline))
    samples, sample_rate = sf.read(str(timeline), dtype="float32", always_2d=False)
    audio = np.asarray(samples, dtype=np.float32)
    rate = max(1, int(sample_rate))
    repairs: list[dict[str, Any]] = []

    for segment_id in requested:
        segment = segment_by_id.get(segment_id)
        check = check_by_id.get(segment_id)
        if not isinstance(segment, dict) or not isinstance(check, dict):
            continue
        timing = check.get("timing") or {}
        onset_ms = _number(timing.get("onset_ms"), 0.0)
        target_ms = min(
            TARGET_ONSET_MS,
            max(40.0, _number(timing.get("max_onset_ms"), 220.0) * 0.60),
        )
        shift_ms = onset_ms - target_ms
        if not 0.0 < shift_ms <= MAX_SHIFT_MS:
            continue

        delay = max(0.0, _number(segment.get("start_delay_ms"))) / 1000.0
        start = max(0.0, _number(segment.get("start")) + delay)
        duration = max(
            0.35,
            _number(segment.get("end")) - _number(segment.get("start")),
        )
        left = max(0, int(round(start * rate)))
        right = min(len(audio), int(round((start + duration) * rate)))
        if right - left < max(2, int(rate * 0.20)):
            continue
        shift_samples = int(round(shift_ms * rate / 1000.0))
        if shift_samples <= 0 or shift_samples >= right - left:
            continue

        audio[left:right] = _shift_window_earlier(audio[left:right], shift_samples)
        repairs.append(
            {
                "id": segment_id,
                "window_start": start,
                "window_seconds": duration,
                "original_onset_ms": onset_ms,
                "target_onset_ms": target_ms,
                "shift_ms": shift_samples * 1000.0 / rate,
                "original_trailing_ms": _number(timing.get("trailing_ms")),
                "checkpoint_preserved": True,
            }
        )

    if repairs:
        temporary = timeline.with_name(
            timeline.stem + ".onset-repair.tmp" + timeline.suffix
        )
        sf.write(
            str(temporary),
            audio,
            rate,
            format=info.format,
            subtype=info.subtype,
        )
        os.replace(temporary, timeline)

    payload = {
        "schema_version": 1,
        "policy": POLICY,
        "timeline": str(timeline),
        "target_onset_ms": TARGET_ONSET_MS,
        "requested_segment_ids": requested,
        "repaired_segment_ids": [int(item["id"]) for item in repairs],
        "repairs": repairs,
        "synthesis_invoked": False,
        "checkpoints_preserved": True,
        "changed": bool(repairs),
    }
    destination = report_path or timeline.with_suffix(".onset_repair.json")
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return payload


__all__ = [
    "MAX_SHIFT_MS",
    "POLICY",
    "TARGET_ONSET_MS",
    "repair_timeline_onsets",
    "repairable_segment_ids",
    "repairable_timing_failure",
]
