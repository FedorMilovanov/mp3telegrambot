#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compact repairable continuation gaps before assembled-timeline QA."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

from tools.voxcpm2.direct_russian_cadence import classify_cadence, prosody_contour

POLICY = "continuation-timeline-compaction-v1"
TARGET_GAP_SECONDS = 0.22
MAX_SHIFT_SECONDS = 2.40
_MIN_WINDOW_SECONDS = 0.12
_REPAIRABLE_CADENCES = {"continuation", "linked"}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _audio_evidence(path: Path) -> dict[str, Any]:
    import soundfile as sf

    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=False)
    return prosody_contour(samples, int(sample_rate))


def compact_timeline_segments(
    fitted_segments: list[tuple[dict[str, Any], Path]],
    *,
    evidence_reader: Callable[[Path], dict[str, Any]] | None = None,
) -> tuple[list[tuple[dict[str, Any], Path]], dict[str, Any]]:
    """Late-align a short continuation inside its own SRT window.

    Raw candidates remain untouched. When measured speech in a continuation ends
    too early, the padded fitted cue is moved later so its last voiced frame lands
    close to the following cue. All later cue starts remain unchanged; the shift is
    bounded, and final assembled QA still checks cadence, fit, tails and real gaps.
    """
    if not fitted_segments:
        return [], {"policy": POLICY, "segments": [], "shifted_segment_ids": []}

    read_evidence = evidence_reader or _audio_evidence
    rows: list[dict[str, Any]] = []
    for position, (raw_segment, path) in enumerate(fitted_segments, start=1):
        segment = dict(raw_segment)
        nominal_start = _finite(segment.get("start")) + _finite(
            segment.get("start_delay_ms")
        ) / 1000.0
        nominal_duration = max(
            _MIN_WINDOW_SECONDS,
            _finite(segment.get("end")) - _finite(segment.get("start")),
        )
        try:
            evidence = dict(read_evidence(Path(path)) or {})
        except Exception as exc:
            evidence = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
        active_start = max(0.0, _finite(evidence.get("active_start")))
        active_end = max(active_start, _finite(evidence.get("active_end")))
        rows.append(
            {
                "position": position,
                "segment": segment,
                "path": Path(path),
                "id": int(segment.get("id", position)),
                "cadence": classify_cadence(str(segment.get("text") or "")),
                "nominal_start": nominal_start,
                "planned_start": nominal_start,
                "nominal_duration": nominal_duration,
                "active_start": active_start,
                "active_end": active_end,
                "evidence_available": bool(evidence.get("available")),
                "evidence_error": str(evidence.get("error") or ""),
            }
        )

    for index in range(len(rows) - 1):
        current = rows[index]
        following = rows[index + 1]
        if (
            current["cadence"] not in _REPAIRABLE_CADENCES
            or not current["evidence_available"]
            or current["active_end"] <= current["active_start"]
        ):
            continue
        next_start = _finite(following["nominal_start"])
        desired_start = next_start - TARGET_GAP_SECONDS - _finite(current["active_end"])
        nominal_start = _finite(current["nominal_start"])
        latest_start = min(
            nominal_start + MAX_SHIFT_SECONDS,
            next_start - TARGET_GAP_SECONDS - _finite(current["active_end"]),
        )
        planned_start = max(nominal_start, min(desired_start, latest_start))
        if planned_start > nominal_start + 0.001:
            current["planned_start"] = planned_start

    adjusted: list[tuple[dict[str, Any], Path]] = []
    report_rows: list[dict[str, Any]] = []
    shifted_ids: list[int] = []
    for index, row in enumerate(rows):
        segment = dict(row["segment"])
        planned_start = _finite(row["planned_start"])
        planned_end = planned_start + _finite(row["nominal_duration"])
        if index + 1 < len(rows):
            next_start = _finite(rows[index + 1]["nominal_start"])
            minimum_end = (
                planned_start
                + max(_finite(segment.get("tail_guard")), 0.0)
                + _MIN_WINDOW_SECONDS
            )
            if minimum_end <= next_start < planned_end:
                planned_end = next_start

        shift = planned_start - _finite(row["nominal_start"])
        if shift > 0.001:
            shifted_ids.append(int(row["id"]))
        segment.update(
            start=planned_start,
            end=planned_end,
            start_delay_ms=0,
            timeline_original_start=_finite(row["nominal_start"]),
            timeline_shift_seconds=max(0.0, shift),
            timeline_compaction_policy=POLICY,
        )
        adjusted.append((segment, Path(row["path"])))
        report_rows.append(
            {
                "id": int(row["id"]),
                "cadence": str(row["cadence"]),
                "nominal_start": _finite(row["nominal_start"]),
                "planned_start": planned_start,
                "planned_end": planned_end,
                "shift_seconds": max(0.0, shift),
                "active_end_relative": _finite(row["active_end"]),
                "evidence_available": bool(row["evidence_available"]),
                "evidence_error": str(row["evidence_error"]),
            }
        )

    return adjusted, {
        "policy": POLICY,
        "target_gap_seconds": TARGET_GAP_SECONDS,
        "max_shift_seconds": MAX_SHIFT_SECONDS,
        "shifted_segment_ids": shifted_ids,
        "segments": report_rows,
    }


__all__ = [
    "MAX_SHIFT_SECONDS",
    "POLICY",
    "TARGET_GAP_SECONDS",
    "compact_timeline_segments",
]
