#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed cadence and late-tail QA for the assembled Russian timeline.

Candidate QA runs before timing filters. This module checks the actual timeline
written by FFmpeg, after atempo, fades, padding, delays and mixing, so a release
cannot pass merely because the raw candidate was clean. Failed segments advance
their durable seed epoch, so the next job does not regenerate identical audio.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from tools.voxcpm2.direct_retry_epoch import invalidate_segment_for_retry
from tools.voxcpm2.direct_russian_cadence import (
    evaluate_candidate_cadence,
    prosody_contour,
)
from tools.voxcpm2.direct_tail_artifact import detect_late_broadband_tail

POLICY = "assembled-russian-delivery-v3"
LINKED_PREFERRED_GAP_SECONDS = 0.32
LINKED_MAX_GAP_SECONDS = 0.55


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


def _append_cadence_failure(
    cadence: dict[str, Any],
    reason: str,
    *,
    penalty: float,
) -> None:
    failures = list(cadence.get("failures") or [])
    if reason not in failures:
        failures.append(reason)
    cadence["failures"] = failures
    cadence["hard_ok"] = False
    cadence["penalty"] = _finite(cadence.get("penalty")) + max(0.0, penalty)


def verify_timeline_delivery(
    timeline: Path,
    fitted_segments: list[tuple[dict[str, Any], Path]],
) -> dict[str, Any]:
    """Verify exact assembled segment windows and raise on audible delivery flaws."""
    if not timeline.is_file():
        raise RuntimeError(f"Не найден собранный русский timeline: {timeline}")
    samples, sample_rate = sf.read(str(timeline), dtype="float32")
    audio = _mono(samples)
    rate = max(1, int(sample_rate))
    checks: list[dict[str, Any]] = []

    for position, (raw_segment, _path) in enumerate(fitted_segments, start=1):
        segment = dict(raw_segment)
        segment_id = int(segment.get("id") or position)
        delay = max(0, int(segment.get("start_delay_ms", 0) or 0)) / 1000.0
        start = max(0.0, _finite(segment.get("start")) + delay)
        window = max(
            0.35,
            _finite(segment.get("end")) - _finite(segment.get("start")),
        )
        left = max(0, int(round(start * rate)))
        right = min(len(audio), int(round((start + window) * rate)))
        clip = audio[left:right]
        contour = prosody_contour(clip, rate)
        active_start = start + _finite(contour.get("active_start"))
        active_end = start + _finite(contour.get("active_end"))
        active_duration = max(0.0, active_end - active_start)
        cadence = evaluate_candidate_cadence(
            {
                "samples": clip,
                "sample_rate": rate,
                "duration": active_duration,
            },
            segment,
        )
        tail = detect_late_broadband_tail(clip, rate)
        passed = bool(cadence.get("hard_ok") and not tail.get("suspicious"))
        checks.append(
            {
                "id": segment_id,
                "start": start,
                "window_seconds": window,
                "active_start_time": active_start,
                "active_end_time": active_end,
                "active_speech_seconds": active_duration,
                "gap_to_next_seconds": None,
                "cadence": cadence,
                "late_tail": tail,
                "passed": passed,
            }
        )

    # A line without final punctuation is a syntactic continuation even when the
    # SRT stores the next words in another timing block. Measure the real audible
    # gap on the assembled timeline rather than trusting either block in isolation.
    for index in range(len(checks) - 1):
        current = checks[index]
        following = checks[index + 1]
        gap = max(
            0.0,
            _finite(following.get("active_start_time"))
            - _finite(current.get("active_end_time")),
        )
        current["gap_to_next_seconds"] = gap
        cadence = current["cadence"]
        if cadence.get("cadence") != "linked":
            continue
        cadence["linked_gap_seconds"] = gap
        cadence["linked_preferred_gap_seconds"] = LINKED_PREFERRED_GAP_SECONDS
        cadence["linked_max_gap_seconds"] = LINKED_MAX_GAP_SECONDS
        if gap > LINKED_PREFERRED_GAP_SECONDS:
            cadence["penalty"] = _finite(cadence.get("penalty")) + min(
                55.0,
                (gap - LINKED_PREFERRED_GAP_SECONDS) * 95.0,
            )
        if gap > LINKED_MAX_GAP_SECONDS:
            _append_cadence_failure(
                cadence,
                "linked_phrase_gap",
                penalty=90.0 + (gap - LINKED_MAX_GAP_SECONDS) * 80.0,
            )
            current["passed"] = False

    failed = [int(item["id"]) for item in checks if not bool(item.get("passed"))]
    invalidated: list[dict[str, Any]] = []
    if failed:
        failed_set = set(failed)
        checks_by_id = {int(item["id"]): item for item in checks}
        for position, (raw_segment, fitted_path) in enumerate(fitted_segments, start=1):
            segment_id = int(raw_segment.get("id") or position)
            if segment_id not in failed_set:
                continue
            fitted = Path(fitted_path)
            work_dir = fitted.parent.parent
            check = checks_by_id[segment_id]
            cadence = check.get("cadence") or {}
            tail = check.get("late_tail") or {}
            reasons = list(cadence.get("failures") or [])
            if tail.get("suspicious"):
                reasons.append(str(tail.get("artifact_type") or "late_tail"))
            invalidated.append(
                invalidate_segment_for_retry(
                    work_dir,
                    dict(raw_segment),
                    reason="assembled_delivery:" + ",".join(reasons or ["delivery_qa"]),
                    fitted_path=fitted,
                    evidence={
                        "policy": POLICY,
                        "ending_delta_semitones": cadence.get("ending_delta_semitones"),
                        "ending_energy_delta_db": cadence.get("ending_energy_delta_db"),
                        "gap_to_next_seconds": check.get("gap_to_next_seconds"),
                        "tail_artifact": tail.get("artifact_type"),
                    },
                )
            )

    report = {
        "schema_version": 3,
        "policy": POLICY,
        "timeline": str(timeline),
        "sample_rate": rate,
        "linked_gap_policy": {
            "preferred_max_seconds": LINKED_PREFERRED_GAP_SECONDS,
            "hard_max_seconds": LINKED_MAX_GAP_SECONDS,
        },
        "segments": checks,
        "failed_segment_ids": failed,
        "invalidated_for_retry": invalidated,
        "passed": not failed,
    }
    report_path = timeline.with_suffix(".delivery_qa.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    if failed:
        details: list[str] = []
        for item in checks:
            if item["passed"]:
                continue
            cadence = item["cadence"]
            tail = item["late_tail"]
            reasons = list(cadence.get("failures") or [])
            if tail.get("suspicious"):
                reasons.append(str(tail.get("artifact_type") or "late_tail"))
            gap = item.get("gap_to_next_seconds")
            gap_text = f"; next-gap={float(gap):.3f}s" if gap is not None else ""
            details.append(
                "#{id}: {reasons}; ending={ending:.2f}st; energy={energy:.2f}dB{gap}".format(
                    id=item["id"],
                    reasons=",".join(reasons or ["delivery_qa"]),
                    ending=_finite(cadence.get("ending_delta_semitones")),
                    energy=_finite(cadence.get("ending_energy_delta_db")),
                    gap=gap_text,
                )
            )
        raise RuntimeError(
            "Собранная русская дорожка не прошла cadence/tail QA; "
            "проваленные сегменты переведены на новые seed epochs: "
            + "; ".join(details)
        )
    return report


__all__ = [
    "LINKED_MAX_GAP_SECONDS",
    "LINKED_PREFERRED_GAP_SECONDS",
    "POLICY",
    "verify_timeline_delivery",
]
