#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed cadence and late-tail QA for the assembled Russian timeline.

Candidate QA runs before timing filters.  This module checks the actual timeline
written by FFmpeg, after atempo, fades, padding, delays and mixing, so a release
cannot pass merely because the raw candidate was clean.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from tools.voxcpm2.direct_russian_cadence import (
    evaluate_candidate_cadence,
    prosody_contour,
)
from tools.voxcpm2.direct_tail_artifact import detect_late_broadband_tail

POLICY = "assembled-russian-delivery-v1"


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
    failed: list[int] = []

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
        active_duration = max(
            0.0,
            _finite(contour.get("active_end")) - _finite(contour.get("active_start")),
        )
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
        if not passed:
            failed.append(segment_id)
        checks.append(
            {
                "id": segment_id,
                "start": start,
                "window_seconds": window,
                "active_speech_seconds": active_duration,
                "cadence": cadence,
                "late_tail": tail,
                "passed": passed,
            }
        )

    invalidated: list[dict[str, str]] = []
    if failed:
        failed_set = set(failed)
        for position, (raw_segment, fitted_path) in enumerate(fitted_segments, start=1):
            segment_id = int(raw_segment.get("id") or position)
            if segment_id not in failed_set:
                continue
            fitted = Path(fitted_path)
            checkpoint = (
                fitted.parent.parent
                / "checkpoints"
                / f"segment_{segment_id:02d}.json"
            )
            fitted.unlink(missing_ok=True)
            checkpoint.unlink(missing_ok=True)
            invalidated.append(
                {
                    "id": str(segment_id),
                    "fitted": str(fitted),
                    "checkpoint": str(checkpoint),
                }
            )

    report = {
        "schema_version": 1,
        "policy": POLICY,
        "timeline": str(timeline),
        "sample_rate": rate,
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
            details.append(
                "#{id}: {reasons}; ending={ending:.2f}st; energy={energy:.2f}dB".format(
                    id=item["id"],
                    reasons=",".join(reasons or ["delivery_qa"]),
                    ending=_finite(cadence.get("ending_delta_semitones")),
                    energy=_finite(cadence.get("ending_energy_delta_db")),
                )
            )
        raise RuntimeError(
            "Собранная русская дорожка не прошла cadence/tail QA: "
            + "; ".join(details)
        )
    return report


__all__ = ["POLICY", "verify_timeline_delivery"]
