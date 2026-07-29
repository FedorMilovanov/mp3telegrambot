#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final release gate for clean expressive direct production.

The semantic/timing gate remains independent from the renderer. Voice-register
limits are profile- and source-prosody-aware. A failed auto-language Whisper
reading receives one forced-Russian diagnostic pass, but confidently foreign
audio can never be rescued by that pass.
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

from tools.voxcpm2 import professional_audio_v45 as policy
from tools.voxcpm2 import semantic_tts_guard_v4
from tools.voxcpm2.direct_max_quality_analysis import (
    activity_stats,
    pitch_profile,
)


POLICY = "clean-expression-aware-qa-v3"
VOICE_EVIDENCE_POLICY = "fail-closed-reference-f0-v1"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if math.isfinite(result) else float(default)


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
        result[name] = pitch_profile(
            np.asarray(samples, dtype=np.float32),
            int(sample_rate),
        )
    return result


def _voice_limits(
    item: dict[str, Any],
    *,
    profile_name: str,
    reference_median: float,
    reference_p90: float,
) -> dict[str, float]:
    """Return strict but expression-aware ratio limits."""
    tier = str(item.get("expression_tier") or "")
    score = _number(item.get("expression_score"), 0.0)
    expressive = (
        profile_name == "composite"
        or tier in {"emphatic", "passionate"}
    )

    max_median = 1.48 if expressive else 1.35
    max_p90 = 1.58 if expressive else 1.45
    min_median = 0.55 if expressive else 0.62
    min_p90 = 0.50 if expressive else 0.56

    source = item.get("source_prosody") or {}
    source_median = _number(source.get("f0_median"), 0.0)
    source_p90 = _number(source.get("f0_p90"), 0.0)
    if reference_median > 1.0 and source_median > 1.0:
        source_ratio = source_median / reference_median
        max_median = max(
            max_median,
            min(1.58, source_ratio * 1.16 + 0.04),
        )
    if reference_p90 > 1.0 and source_p90 > 1.0:
        source_ratio = source_p90 / reference_p90
        max_p90 = max(
            max_p90,
            min(1.68, source_ratio * 1.16 + 0.05),
        )

    if score > 0.0:
        max_median = min(
            1.58,
            max_median + min(0.06, score * 0.03),
        )
        max_p90 = min(
            1.68,
            max_p90 + min(0.07, score * 0.035),
        )

    return {
        "min_median_ratio": round(min_median, 6),
        "max_median_ratio": round(max_median, 6),
        "min_p90_ratio": round(min_p90, 6),
        "max_p90_ratio": round(max_p90, 6),
    }


def _forced_russian_fallback(
    clip: Path,
    target: str,
    auto_semantic: dict[str, Any],
) -> dict[str, Any]:
    """Retry ASR in Russian, without overriding confident foreign detection."""
    auto = dict(auto_semantic)
    heard, language, probability = semantic_tts_guard_v4.legacy._transcribe(
        clip,
        language="ru",
    )
    forced = semantic_tts_guard_v4.legacy.compare_spoken_text(
        target,
        heard,
        language,
        probability,
    )
    confident_foreign = bool(auto.get("foreign_language"))
    rescued = bool(forced.get("passed") and not confident_foreign)
    selected = forced if rescued else auto
    merged = dict(selected)
    merged.update(
        {
            "passed": rescued,
            "auto_language": auto,
            "forced_russian": forced,
            "forced_russian_rescued": rescued,
            "confident_foreign_block": confident_foreign,
        }
    )
    return merged


def _base_semantic_acoustic_pass(check: dict[str, Any]) -> bool:
    acoustic = check.get("acoustic") or {}
    timing = check.get("timing") or {}
    return bool(
        acoustic.get("passed")
        and (not timing or timing.get("passed"))
    )


def _pitch_valid(profile: dict[str, Any] | None) -> bool:
    if not isinstance(profile, dict):
        return False
    return bool(
        _number(profile.get("voiced_ratio")) >= 0.12
        and _number(profile.get("f0_median")) > 1.0
        and _number(profile.get("f0_p90")) > 1.0
    )


def _voice_evaluation(
    item: dict[str, Any],
    *,
    profile_name: str,
    reference: dict[str, Any] | None,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate voice register and fail closed when evidence is unavailable."""
    reference_available = isinstance(reference, dict)
    reference_pitch_valid = _pitch_valid(reference)
    candidate_pitch_valid = _pitch_valid(candidate)

    reference_median = _number(
        (reference or {}).get("f0_median"),
        0.0,
    )
    reference_p90 = _number(
        (reference or {}).get("f0_p90"),
        0.0,
    )
    candidate_median = _number(candidate.get("f0_median"), 0.0)
    candidate_p90 = _number(candidate.get("f0_p90"), 0.0)

    if reference_pitch_valid and candidate_pitch_valid:
        median_ratio = candidate_median / reference_median
        p90_ratio = candidate_p90 / reference_p90
    else:
        median_ratio = 0.0
        p90_ratio = 0.0

    limits = _voice_limits(
        item,
        profile_name=profile_name,
        reference_median=reference_median,
        reference_p90=reference_p90,
    )
    passed = bool(
        reference_available
        and reference_pitch_valid
        and candidate_pitch_valid
        and limits["min_median_ratio"]
        <= median_ratio
        <= limits["max_median_ratio"]
        and limits["min_p90_ratio"]
        <= p90_ratio
        <= limits["max_p90_ratio"]
    )

    reason = ""
    if not reference_available:
        reason = "missing_reference_profile"
    elif not reference_pitch_valid:
        reason = "invalid_reference_pitch"
    elif not candidate_pitch_valid:
        reason = "invalid_candidate_pitch"
    elif not passed:
        reason = "pitch_ratio_out_of_range"

    return {
        **candidate,
        "policy": POLICY,
        "voice_evidence_policy": VOICE_EVIDENCE_POLICY,
        "reference_profile": profile_name,
        "expression_tier": str(item.get("expression_tier") or ""),
        "expression_score": _number(item.get("expression_score"), 0.0),
        "reference_available": reference_available,
        "reference_pitch_valid": reference_pitch_valid,
        "candidate_pitch_valid": candidate_pitch_valid,
        "reference_f0_median": reference_median,
        "reference_f0_p90": reference_p90,
        "f0_median_ratio": round(median_ratio, 6),
        "f0_p90_ratio": round(p90_ratio, 6),
        **limits,
        "failure_reason": reason,
        "passed": passed,
    }


def verify_timeline_v45(
    timeline: Path,
    segments: list[dict[str, Any]],
    report_path: Path,
) -> tuple[list[int], dict[str, Any]]:
    _failed, report = policy._ORIGINAL_VERIFY(
        timeline,
        segments,
        report_path,
    )
    checks = {
        int(item.get("id")): item
        for item in report.get("segments", [])
        if isinstance(item, dict)
        and str(item.get("id", "")).isdigit()
    }
    references = _reference_profiles(timeline)

    with tempfile.TemporaryDirectory(
        prefix="dub-expression-aware-qa-"
    ) as temp_raw:
        temp = Path(temp_raw)
        for item in segments:
            segment_id = int(item["id"])
            delay = max(
                0,
                int(item.get("start_delay_ms", 0)),
            ) / 1000.0
            start = float(item["start"]) + delay
            duration = max(
                0.35,
                float(item["end"]) - float(item["start"]),
            )
            clip = temp / f"segment_{segment_id:03d}.wav"
            semantic_tts_guard_v4.legacy._extract_clip(
                timeline,
                clip,
                start,
                duration,
            )
            samples, sample_rate = (
                semantic_tts_guard_v4.legacy._read_pcm_mono(clip)
            )
            audio = np.asarray(samples, dtype=np.float32)

            check = checks.setdefault(
                segment_id,
                {"id": segment_id, "passed": True},
            )
            semantic = check.get("semantic")
            if isinstance(semantic, dict) and not semantic.get("passed"):
                semantic = _forced_russian_fallback(
                    clip,
                    str(item.get("text") or ""),
                    semantic,
                )
                check["semantic"] = semantic
                check["passed"] = bool(
                    _base_semantic_acoustic_pass(check)
                    and semantic.get("passed")
                )

            activity = activity_stats(audio, int(sample_rate))
            punctuation = bool(
                re.search(r"[.!?…;:]", str(item.get("text") or ""))
            )
            max_gap = 0.78 if punctuation else 0.58
            continuity_passed = bool(
                activity["max_internal_gap"] <= max_gap
                and activity["active_ratio"] >= 0.20
            )

            profile_name = str(
                item.get("reference_profile") or "extended"
            )
            candidate_pitch = pitch_profile(audio, int(sample_rate))
            voice = _voice_evaluation(
                item,
                profile_name=profile_name,
                reference=references.get(profile_name),
                candidate=candidate_pitch,
            )

            check["continuity_v45"] = {
                **activity,
                "max_allowed_internal_gap": max_gap,
                "passed": continuity_passed,
            }
            check["voice_match_v45"] = voice
            check["passed"] = bool(
                check.get("passed")
                and continuity_passed
                and voice.get("passed")
            )

    result = sorted(
        int(segment_id)
        for segment_id, check in checks.items()
        if not bool(check.get("passed"))
    )
    report["professional_audio_policy"] = POLICY
    report["semantic_asr_policy"] = (
        "auto-language + conservative forced-Russian fallback"
    )
    report["voice_evidence_policy"] = VOICE_EVIDENCE_POLICY
    report["passed"] = not result
    report["failed_segment_ids"] = result
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result, report


def install() -> None:
    semantic_tts_guard_v4.verify_timeline_v4 = verify_timeline_v45
