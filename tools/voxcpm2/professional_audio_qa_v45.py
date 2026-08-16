#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independent semantic, timing, continuity and voice-evidence release gate."""
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
from tools.voxcpm2 import russian_spoken_numbers
from tools.voxcpm2 import semantic_tts_guard_v4
from tools.voxcpm2.direct_max_quality_analysis import activity_stats, pitch_profile

POLICY = "clean-expression-aware-qa-v3"
VOICE_EVIDENCE_POLICY = "fail-closed-reference-f0-v1"
SEMANTIC_RESCUE_POLICY = "forced-russian-script-gate-v2"
NUMERIC_SEMANTIC_POLICY = "wetext-aligned-exact-numeric-anchors-v2"
_RUSSIAN_FAMILY = {"ru", "uk", "be"}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _reference_profiles(timeline: Path) -> dict[str, dict[str, float]]:
    root = timeline.parent.parent
    profiles: dict[str, dict[str, float]] = {}
    for name in ("extended", "composite"):
        path = root / "references" / f"{name}_reference.wav"
        if not path.is_file():
            continue
        samples, rate = sf.read(path, dtype="float32")
        audio = np.asarray(samples, dtype=np.float32)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        profiles[name] = pitch_profile(audio, int(rate))
    return profiles


def _voice_limits(
    item: dict[str, Any],
    *,
    profile_name: str,
    reference_median: float,
    reference_p90: float,
) -> dict[str, float]:
    tier = str(item.get("expression_tier") or "")
    score = _number(item.get("expression_score"), 0.0)
    expressive = profile_name == "composite" or tier in {"emphatic", "passionate"}
    max_median, max_p90 = ((1.48, 1.58) if expressive else (1.35, 1.45))
    min_median, min_p90 = ((0.55, 0.50) if expressive else (0.62, 0.56))
    source = item.get("source_prosody") or {}
    if str(item.get("source_prosody_role") or "") == "diagnostic-only-no-cross-language-ranking-v1":
        # Source-language contour is advisory only and cannot widen identity gates.
        source = {}
    source_median = _number(source.get("f0_median"))
    source_p90 = _number(source.get("f0_p90"))
    if reference_median > 1.0 and source_median > 1.0:
        max_median = max(max_median, min(1.58, source_median / reference_median * 1.16 + 0.04))
    if reference_p90 > 1.0 and source_p90 > 1.0:
        max_p90 = max(max_p90, min(1.68, source_p90 / reference_p90 * 1.16 + 0.05))
    if score > 0.0:
        max_median = min(1.58, max_median + min(0.06, score * 0.03))
        max_p90 = min(1.68, max_p90 + min(0.07, score * 0.035))
    return {
        "min_median_ratio": round(min_median, 6),
        "max_median_ratio": round(max_median, 6),
        "min_p90_ratio": round(min_p90, 6),
        "max_p90_ratio": round(max_p90, 6),
    }


def _script_evidence(value: str) -> dict[str, Any]:
    letters = [char for char in str(value or "") if char.isalpha()]
    cyrillic = [char for char in letters if "\u0400" <= char <= "\u052f"]
    latin = [char for char in letters if "a" <= char.casefold() <= "z"]
    total = len(letters)
    return {
        "letters": total,
        "cyrillic_letters": len(cyrillic),
        "latin_letters": len(latin),
        "other_script_letters": max(0, total - len(cyrillic) - len(latin)),
        "cyrillic_ratio": round(len(cyrillic) / max(1, total), 6),
        "latin_ratio_unicode": round(len(latin) / max(1, total), 6),
    }


def _forced_russian_eligibility(
    semantic: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    language = str(semantic.get("language") or "").casefold()
    probability = _number(semantic.get("language_probability"))
    script = _script_evidence(str(semantic.get("heard") or ""))
    ratio = float(script["cyrillic_ratio"])
    if bool(semantic.get("foreign_language")):
        return False, "confident_foreign_language", script
    if int(script["letters"]) > 0 and ratio < 0.55:
        return False, "foreign_script", script
    if (
        int(script["letters"]) == 0
        and language
        and language not in _RUSSIAN_FAMILY
        and probability >= 0.35
    ):
        return False, "empty_auto_foreign_language", script
    if language and language not in _RUSSIAN_FAMILY and probability >= 0.55 and ratio < 0.80:
        return False, "probable_foreign_language", script
    return True, "", script


def _numeric_anchor_evidence(
    groups: list[list[str]],
    heard: str,
) -> tuple[bool, list[dict[str, Any]]]:
    normalized_heard = semantic_tts_guard_v4.legacy.normalize_asr_text(heard)
    padded = f" {normalized_heard} "
    details: list[dict[str, Any]] = []
    for group in groups:
        alternatives = [semantic_tts_guard_v4.legacy.normalize_asr_text(value) for value in group]
        alternatives = [value for value in alternatives if value]
        matched = next((value for value in alternatives if f" {value} " in padded), "")
        details.append({"alternatives": alternatives, "matched": matched, "passed": bool(matched)})
    return bool(groups and all(item["passed"] for item in details)), details


def _numeric_semantic_target(
    target: str,
    auto_semantic: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    original = str(target or "")
    spoken = russian_spoken_numbers.normalize_numeric_text(original)
    groups = russian_spoken_numbers.numeric_anchor_groups(original)
    if spoken == original and not groups:
        return original, dict(auto_semantic)
    compared = semantic_tts_guard_v4.legacy.compare_spoken_text(
        spoken,
        str(auto_semantic.get("heard") or ""),
        str(auto_semantic.get("language") or ""),
        _number(auto_semantic.get("language_probability")),
    )
    anchors_passed, evidence = _numeric_anchor_evidence(groups, str(auto_semantic.get("heard") or ""))
    passed = bool(compared.get("passed") and anchors_passed)
    return spoken, {
        **compared,
        "passed": passed,
        "numeric_semantic_policy": NUMERIC_SEMANTIC_POLICY,
        "numeric_source_policy": russian_spoken_numbers.POLICY,
        "numeric_target_original": original,
        "numeric_target_spoken": spoken,
        "numeric_anchor_groups": groups,
        "numeric_anchor_evidence": evidence,
        "numeric_anchors_passed": anchors_passed,
        "numeric_normalization_rescued": passed,
        "original_target_semantic": dict(auto_semantic),
    }


def _forced_russian_fallback(
    clip: Path,
    target: str,
    auto_semantic: dict[str, Any],
    *,
    numeric_anchor_groups: list[list[str]] | None = None,
) -> dict[str, Any]:
    auto = dict(auto_semantic)
    eligible, block_reason, script = _forced_russian_eligibility(auto)
    heard, language, probability = semantic_tts_guard_v4.legacy._transcribe(clip, language="ru")
    forced = semantic_tts_guard_v4.legacy.compare_spoken_text(target, heard, language, probability)
    groups = list(numeric_anchor_groups or [])
    if groups:
        anchors_passed, evidence = _numeric_anchor_evidence(groups, heard)
        forced.update(
            numeric_anchor_groups=groups,
            numeric_anchor_evidence=evidence,
            numeric_anchors_passed=anchors_passed,
            passed=bool(forced.get("passed") and anchors_passed),
        )
    rescued = bool(forced.get("passed") and eligible)
    selected = forced if rescued else auto
    return {
        **selected,
        "passed": rescued,
        "semantic_rescue_policy": SEMANTIC_RESCUE_POLICY,
        "auto_language": auto,
        "auto_script_evidence": script,
        "forced_russian": forced,
        "forced_russian_target": target,
        "forced_russian_eligible": eligible,
        "forced_russian_rescued": rescued,
        "forced_russian_block_reason": block_reason,
        "confident_foreign_block": bool(auto.get("foreign_language")),
    }


def _base_semantic_acoustic_pass(check: dict[str, Any]) -> bool:
    acoustic = check.get("acoustic") or {}
    timing = check.get("timing") or {}
    return bool(acoustic.get("passed") and (not timing or timing.get("passed")))


def _pitch_valid(profile: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(profile, dict)
        and _number(profile.get("voiced_ratio")) >= 0.12
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
    reference_available = isinstance(reference, dict)
    reference_pitch_valid = _pitch_valid(reference)
    candidate_pitch_valid = _pitch_valid(candidate)
    reference_median = _number((reference or {}).get("f0_median"))
    reference_p90 = _number((reference or {}).get("f0_p90"))
    candidate_median = _number(candidate.get("f0_median"))
    candidate_p90 = _number(candidate.get("f0_p90"))
    if reference_pitch_valid and candidate_pitch_valid:
        median_ratio = candidate_median / reference_median
        p90_ratio = candidate_p90 / reference_p90
    else:
        median_ratio = p90_ratio = 0.0
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
        and limits["min_median_ratio"] <= median_ratio <= limits["max_median_ratio"]
        and limits["min_p90_ratio"] <= p90_ratio <= limits["max_p90_ratio"]
    )
    if not reference_available:
        reason = "missing_reference_profile"
    elif not reference_pitch_valid:
        reason = "invalid_reference_pitch"
    elif not candidate_pitch_valid:
        reason = "invalid_candidate_pitch"
    elif not passed:
        reason = "pitch_ratio_out_of_range"
    else:
        reason = ""
    return {
        **candidate,
        "policy": POLICY,
        "voice_evidence_policy": VOICE_EVIDENCE_POLICY,
        "reference_profile": profile_name,
        "expression_tier": str(item.get("expression_tier") or ""),
        "expression_score": _number(item.get("expression_score")),
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
    _failed, report = policy._ORIGINAL_VERIFY(timeline, segments, report_path)
    checks = {
        int(item.get("id")): item
        for item in report.get("segments", [])
        if isinstance(item, dict) and str(item.get("id", "")).isdigit()
    }
    references = _reference_profiles(timeline)
    with tempfile.TemporaryDirectory(prefix="dub-expression-aware-qa-") as raw:
        temp = Path(raw)
        for item in segments:
            segment_id = int(item["id"])
            delay = max(0, int(item.get("start_delay_ms", 0))) / 1000.0
            start = float(item["start"]) + delay
            duration = max(0.35, float(item["end"]) - float(item["start"]))
            clip = temp / f"segment_{segment_id:03d}.wav"
            semantic_tts_guard_v4.legacy._extract_clip(timeline, clip, start, duration)
            samples, sample_rate = semantic_tts_guard_v4.legacy._read_pcm_mono(clip)
            audio = np.asarray(samples, dtype=np.float32)
            check = checks.setdefault(segment_id, {"id": segment_id, "passed": True})
            semantic = check.get("semantic")
            if isinstance(semantic, dict) and not semantic.get("passed"):
                spoken, numeric = _numeric_semantic_target(str(item.get("text") or ""), semantic)
                semantic = numeric if numeric.get("passed") else _forced_russian_fallback(
                    clip,
                    spoken,
                    numeric,
                    numeric_anchor_groups=numeric.get("numeric_anchor_groups"),
                )
                check["semantic"] = semantic
                check["passed"] = bool(_base_semantic_acoustic_pass(check) and semantic.get("passed"))

            activity = activity_stats(audio, int(sample_rate))
            punctuation = bool(re.search(r"[.!?…;:]", str(item.get("text") or "")))
            max_gap = 0.78 if punctuation else 0.58
            continuity_passed = bool(
                activity["max_internal_gap"] <= max_gap and activity["active_ratio"] >= 0.20
            )
            profile_name = str(item.get("reference_profile") or "extended")
            voice = _voice_evaluation(
                item,
                profile_name=profile_name,
                reference=references.get(profile_name),
                candidate=pitch_profile(audio, int(sample_rate)),
            )
            check["continuity_v45"] = {
                **activity,
                "max_allowed_internal_gap": max_gap,
                "passed": continuity_passed,
            }
            check["voice_match_v45"] = voice
            check["passed"] = bool(check.get("passed") and continuity_passed and voice.get("passed"))

    result = sorted(
        int(segment_id) for segment_id, check in checks.items() if not bool(check.get("passed"))
    )
    report.update(
        professional_audio_policy=POLICY,
        semantic_asr_policy=(
            "original + value-preserving numeric target with exact anchors + "
            "forced-Russian retry with foreign-script gate"
        ),
        numeric_semantic_policy=NUMERIC_SEMANTIC_POLICY,
        semantic_rescue_policy=SEMANTIC_RESCUE_POLICY,
        voice_evidence_policy=VOICE_EVIDENCE_POLICY,
        passed=not result,
        failed_segment_ids=result,
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return result, report

_BASE_ALL = tuple(globals().get('__all__', ()))

import json

from pathlib import Path

import types

from typing import Any

import numpy as np

import soundfile as sf

from tools.voxcpm2 import timeline_onset_repair

TIMING_REPAIR_POLICY = "cheap-timeline-onset-before-resynthesis-v1"

TIMING_RECHECK_POLICY = "repaired-windows-only-no-repeat-asr-v1"

_legacy_verify_timeline_v45 = verify_timeline_v45

def _pre_repair_report_path(report_path: Path) -> Path:
    return report_path.with_name(report_path.stem + ".pre_onset_repair.json")

def _segment_map(segments: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {
        int(item["id"]): item
        for item in segments
        if isinstance(item, dict) and str(item.get("id") or "").isdigit()
    }

def _check_map(report: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(item["id"]): item
        for item in report.get("segments", [])
        if isinstance(item, dict) and str(item.get("id") or "").isdigit()
    }

def _all_non_timing_checks_pass(check: dict[str, Any]) -> bool:
    for name in ("semantic", "acoustic", "continuity_v45", "voice_match_v45"):
        component = check.get(name)
        if isinstance(component, dict) and component.get("passed") is False:
            return False
    return True

def _remeasure_repaired_timing(
    timeline: Path,
    segments: list[dict[str, Any]],
    report: dict[str, Any],
    repaired_ids: list[int],
) -> tuple[list[int], dict[str, Any]]:
    samples, sample_rate = sf.read(str(timeline), dtype="float32", always_2d=False)
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.reshape(-1)
    rate = max(1, int(sample_rate))
    segment_by_id = _segment_map(segments)
    check_by_id = _check_map(report)

    for segment_id in repaired_ids:
        segment = segment_by_id.get(segment_id)
        check = check_by_id.get(segment_id)
        if not isinstance(segment, dict) or not isinstance(check, dict):
            continue
        previous = check.get("timing") if isinstance(check.get("timing"), dict) else {}
        delay = max(0, int(segment.get("start_delay_ms", 0) or 0)) / 1000.0
        start = max(0.0, float(segment.get("start", 0.0)) + delay)
        duration = max(
            0.35,
            float(segment.get("end", 0.0)) - float(segment.get("start", 0.0)),
        )
        left = max(0, int(round(start * rate)))
        right = min(len(audio), int(round((start + duration) * rate)))
        clip = audio[left:right]
        timing = semantic_tts_guard_v4.measure_timing_quality(
            clip,
            rate,
            max_onset_ms=int(previous.get("max_onset_ms", 220) or 220),
            min_trailing_ms=int(previous.get("min_trailing_ms", 45) or 45),
        )
        timing["recheck_policy"] = TIMING_RECHECK_POLICY
        timing["previous_onset_ms"] = previous.get("onset_ms")
        timing["previous_trailing_ms"] = previous.get("trailing_ms")
        check["timing"] = timing
        check["passed"] = bool(
            timing.get("passed") and _all_non_timing_checks_pass(check)
        )

    failed = sorted(
        int(segment_id)
        for segment_id, check in check_by_id.items()
        if check.get("passed") is not True
    )
    report["failed_segment_ids"] = failed
    report["passed"] = not failed
    return failed, report

def verify_timeline_v45(
    timeline: Path,
    segments: list[dict[str, Any]],
    report_path: Path,
) -> tuple[list[int], dict[str, Any]]:
    """Run independent QA, repair timing-only onsets, then remeasure those IDs."""
    failed, report = _legacy_verify_timeline_v45(timeline, segments, report_path)
    if not failed:
        report["timing_repair_policy"] = TIMING_REPAIR_POLICY
        report["timing_recheck_policy"] = TIMING_RECHECK_POLICY
        report["timing_repair_attempted"] = False
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        return failed, report

    repairable = timeline_onset_repair.repairable_segment_ids(report)
    if not repairable:
        report["timing_repair_policy"] = TIMING_REPAIR_POLICY
        report["timing_recheck_policy"] = TIMING_RECHECK_POLICY
        report["timing_repair_attempted"] = False
        report["timing_repairable_segment_ids"] = []
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        return failed, report

    pre_repair_path = _pre_repair_report_path(report_path)
    pre_repair_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    repair = timeline_onset_repair.repair_timeline_onsets(
        timeline,
        segments,
        report,
        report_path=report_path.with_name(report_path.stem + ".onset_repair.json"),
    )
    repaired_ids = [int(value) for value in repair.get("repaired_segment_ids", [])]
    if not repaired_ids:
        report["timing_repair_policy"] = TIMING_REPAIR_POLICY
        report["timing_recheck_policy"] = TIMING_RECHECK_POLICY
        report["timing_repair_attempted"] = True
        report["timing_repair"] = repair
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        return failed, report

    repaired_failed, repaired_report = _remeasure_repaired_timing(
        timeline,
        segments,
        report,
        repaired_ids,
    )
    repaired_report.update(
        timing_repair_policy=TIMING_REPAIR_POLICY,
        timing_recheck_policy=TIMING_RECHECK_POLICY,
        timing_repair_attempted=True,
        timing_repair=repair,
        pre_timing_repair_report=str(pre_repair_path),
        timing_repair_resolved_segment_ids=sorted(set(failed) - set(repaired_failed)),
        timing_repair_remaining_failed_segment_ids=repaired_failed,
        repeated_semantic_asr=False,
        repeated_voice_analysis=False,
    )
    report_path.write_text(
        json.dumps(repaired_report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return repaired_failed, repaired_report

verify_timeline_v45 = verify_timeline_v45

__all__ = sorted(
    set(_BASE_ALL)
    | {
        "TIMING_RECHECK_POLICY",
        "TIMING_REPAIR_POLICY",
        "_remeasure_repaired_timing",
        "verify_timeline_v45",
    }
)
