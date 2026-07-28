#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final release gate for Professional Audio v4.5."""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from tools.voxcpm2 import professional_audio_v45 as policy
from tools.voxcpm2 import semantic_tts_guard_v4


def _reference_profiles(timeline: Path) -> dict[str, dict[str, float]]:
    root = timeline.parent.parent
    result: dict[str, dict[str, float]] = {}
    for name in ("extended", "composite"):
        path = root / "references" / f"{name}_reference.wav"
        if not path.is_file():
            continue
        samples, sample_rate = sf.read(path, dtype="float32")
        if np.asarray(samples).ndim > 1:
            samples = np.asarray(samples, dtype=np.float32).mean(axis=1)
        result[name] = policy.pitch_profile(np.asarray(samples), int(sample_rate))
    return result


def verify_timeline_v45(
    timeline: Path,
    segments: list[dict[str, Any]],
    report_path: Path,
) -> tuple[list[int], dict[str, Any]]:
    failed, report = policy._ORIGINAL_VERIFY(timeline, segments, report_path)
    failed_ids = {int(value) for value in failed}
    checks = {
        int(item.get("id")): item
        for item in report.get("segments", [])
        if isinstance(item, dict) and str(item.get("id", "")).isdigit()
    }
    references = _reference_profiles(timeline)

    with tempfile.TemporaryDirectory(prefix="dub-professional-qa-v45-") as temp_raw:
        temp = Path(temp_raw)
        for item in segments:
            segment_id = int(item["id"])
            delay = max(0, int(item.get("start_delay_ms", 0))) / 1000.0
            start = float(item["start"]) + delay
            duration = max(0.35, float(item["end"]) - float(item["start"]))
            clip = temp / f"segment_{segment_id:03d}.wav"
            semantic_tts_guard_v4.legacy._extract_clip(timeline, clip, start, duration)
            samples, sample_rate = semantic_tts_guard_v4.legacy._read_pcm_mono(clip)
            audio = np.asarray(samples, dtype=np.float32)

            activity = policy.activity_stats(audio, int(sample_rate))
            punctuation = bool(re.search(r"[.!?…;:]", str(item.get("text") or "")))
            max_gap = 0.78 if punctuation else 0.58
            continuity_passed = bool(
                activity["max_internal_gap"] <= max_gap
                and activity["active_ratio"] >= 0.20
            )

            profile_name = str(item.get("reference_profile") or "extended")
            reference = references.get(profile_name) or references.get("extended") or {}
            pitch = policy.pitch_profile(audio, int(sample_rate))
            reference_median = float(reference.get("f0_median") or 0.0)
            reference_p90 = float(reference.get("f0_p90") or 0.0)
            median_ratio = (
                float(pitch["f0_median"]) / reference_median
                if reference_median > 1.0 and pitch["f0_median"] > 0.0
                else 1.0
            )
            p90_ratio = (
                float(pitch["f0_p90"]) / reference_p90
                if reference_p90 > 1.0 and pitch["f0_p90"] > 0.0
                else 1.0
            )
            voice_passed = bool(
                pitch["voiced_ratio"] >= 0.12
                and median_ratio <= 1.25
                and p90_ratio <= 1.35
            )

            check = checks.setdefault(segment_id, {"id": segment_id, "passed": True})
            check["continuity_v45"] = {
                **activity,
                "max_allowed_internal_gap": max_gap,
                "passed": continuity_passed,
            }
            check["voice_match_v45"] = {
                **pitch,
                "reference_profile": profile_name,
                "reference_f0_median": reference_median,
                "reference_f0_p90": reference_p90,
                "f0_median_ratio": round(median_ratio, 6),
                "f0_p90_ratio": round(p90_ratio, 6),
                "max_median_ratio": 1.25,
                "max_p90_ratio": 1.35,
                "passed": voice_passed,
            }
            check["passed"] = bool(
                check.get("passed") and continuity_passed and voice_passed
            )
            if not check["passed"]:
                failed_ids.add(segment_id)

    result = sorted(failed_ids)
    report["professional_audio_policy"] = policy.POLICY
    report["passed"] = not result
    report["failed_segment_ids"] = result
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result, report


def install() -> None:
    semantic_tts_guard_v4.verify_timeline_v4 = verify_timeline_v45
