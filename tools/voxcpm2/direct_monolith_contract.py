#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-speaker continuity contract for independently synthesized Dub segments.

VoxCPM still renders bounded SRT windows, but every accepted candidate must stay
inside one stable synthetic identity trajectory.  The contract combines the
fixed calm reference, the previously accepted segment, duration fill, start
artifact evidence and explicit Russian stress evidence.  It never accepts a
best-of-bad candidate.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from tools.voxcpm2 import russian_pronunciation
from tools.voxcpm2.direct_timbre_analysis import spectral_envelope, spectral_similarity

POLICY = "single-speaker-monolithic-candidate-v1"
START_ARTIFACT_POLICY = "detached-reference-leak-start-v1"
NEIGHBOUR_POLICY = "adjacent-accepted-voice-continuity-v1"

ANCHOR_SOFT_SIMILARITY = 0.82
ANCHOR_HARD_SIMILARITY = 0.58
NEIGHBOUR_SOFT_SIMILARITY = 0.82
NEIGHBOUR_HARD_SIMILARITY = 0.66
MIN_LINKED_DURATION_RATIO = 0.62
MIN_TERMINAL_DURATION_RATIO = 0.48
MAX_MONOLITH_PENALTY = 240.0

_SEGMENTS: dict[int, dict[str, Any]] = {}
_CURRENT_SEGMENT_ID: int | None = None
_CURRENT_BEST_IDENTITY: dict[str, Any] | None = None
_PREVIOUS_IDENTITY: dict[str, Any] | None = None


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _mono(samples: Any) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.reshape(-1)


def register_segments(segments: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    global _SEGMENTS, _CURRENT_SEGMENT_ID, _CURRENT_BEST_IDENTITY, _PREVIOUS_IDENTITY
    result: list[dict[str, Any]] = []
    mapping: dict[int, dict[str, Any]] = {}
    for position, raw in enumerate(segments, start=1):
        item = dict(raw)
        segment_id = int(item.get("id") or position)
        item["id"] = segment_id
        item["reference_profile"] = "extended"
        item["identity_reference_profile"] = "extended"
        item["monolith_policy"] = POLICY
        item["pronunciation"] = russian_pronunciation.prepare_segment(item)
        mapping[segment_id] = item
        result.append(item)
    _SEGMENTS = mapping
    _CURRENT_SEGMENT_ID = None
    _CURRENT_BEST_IDENTITY = None
    _PREVIOUS_IDENTITY = None
    return result


def current_segment() -> dict[str, Any] | None:
    if _CURRENT_SEGMENT_ID is None:
        return None
    value = _SEGMENTS.get(int(_CURRENT_SEGMENT_ID))
    return dict(value) if isinstance(value, dict) else None


def set_current_segment_id(segment_id: Any) -> None:
    global _CURRENT_SEGMENT_ID, _CURRENT_BEST_IDENTITY, _PREVIOUS_IDENTITY
    value = int(segment_id)
    if _CURRENT_SEGMENT_ID == value:
        return
    if _CURRENT_BEST_IDENTITY is not None:
        _PREVIOUS_IDENTITY = dict(_CURRENT_BEST_IDENTITY)
    _CURRENT_BEST_IDENTITY = None
    _CURRENT_SEGMENT_ID = value


def _identity_from_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    pitch = candidate.get("pitch") or {}
    activity = candidate.get("activity") or {}
    timbre = candidate.get("timbre")
    if not isinstance(timbre, dict):
        timbre = spectral_envelope(
            candidate.get("samples"),
            int(candidate.get("sample_rate") or 1),
        )
        candidate["timbre"] = timbre
    voice = candidate.get("voice_match") or {}
    return {
        "f0_median": _finite(pitch.get("f0_median")),
        "f0_p90": _finite(pitch.get("f0_p90")),
        "voiced_ratio": _finite(pitch.get("voiced_ratio")),
        "active_ratio": _finite(activity.get("active_ratio")),
        "rms_dbfs": _finite(activity.get("rms_dbfs"), -120.0),
        "spectral_centroid_hz": _finite(timbre.get("spectral_centroid_hz")),
        "spectral_bands": [
            _finite(value)
            for value in (timbre.get("bands") or [])
        ],
        "anchor_spectral_similarity": _finite(voice.get("spectral_similarity")),
        "anchor_f0_median_ratio": _finite(voice.get("f0_median_ratio")),
        "anchor_f0_p90_ratio": _finite(voice.get("f0_p90_ratio")),
    }


def _identity_from_report(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    report = payload.get("report") if isinstance(payload.get("report"), dict) else payload
    selected_source = report.get("selected_source_prosody_match")
    if isinstance(selected_source, dict):
        monolith = selected_source.get("monolith_identity")
        if isinstance(monolith, dict):
            identity = monolith.get("identity")
            if isinstance(identity, dict) and identity.get("f0_median"):
                return dict(identity)
    selected_attempt = int(report.get("selected_attempt") or 0)
    attempts = report.get("attempts")
    if isinstance(attempts, list):
        for item in attempts:
            if not isinstance(item, dict) or int(item.get("attempt") or 0) != selected_attempt:
                continue
            f0 = _finite(item.get("f0_median"))
            if f0 <= 0.0:
                continue
            return {
                "f0_median": f0,
                "f0_p90": _finite(item.get("f0_p90")),
                "voiced_ratio": _finite(item.get("voiced_ratio")),
                "active_ratio": _finite(item.get("active_ratio")),
                "rms_dbfs": _finite(item.get("rms_dbfs"), -120.0),
                "spectral_centroid_hz": _finite(item.get("spectral_centroid_hz")),
                "spectral_bands": [],
                "anchor_spectral_similarity": _finite(item.get("spectral_similarity")),
                "anchor_f0_median_ratio": _finite(item.get("f0_median_ratio")),
                "anchor_f0_p90_ratio": _finite(item.get("f0_p90_ratio")),
            }
    return None


def _load_previous_checkpoint(work_dir: Path, segment_id: int) -> dict[str, Any] | None:
    checkpoint_dir = Path(work_dir) / "checkpoints"
    for previous_id in range(int(segment_id) - 1, 0, -1):
        path = checkpoint_dir / f"segment_{previous_id:02d}.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        identity = _identity_from_report(payload)
        if identity is not None:
            return identity
    return None


def _start_artifact(samples: Any, sample_rate: int) -> dict[str, Any]:
    audio = _mono(samples)
    rate = max(1, int(sample_rate))
    if len(audio) < int(rate * 0.18):
        return {"policy": START_ARTIFACT_POLICY, "suspicious": False, "reason": "too_short"}
    frame = max(64, int(rate * 0.010))
    hop = max(32, int(rate * 0.005))
    starts = np.arange(0, max(0, len(audio) - frame + 1), hop, dtype=np.int64)
    rms: list[float] = []
    zcr: list[float] = []
    step: list[float] = []
    for start in starts:
        chunk = audio[start : start + frame]
        rms.append(math.sqrt(float(np.mean(chunk.astype(np.float64) ** 2)) + 1e-12))
        zcr.append(
            float(np.mean(np.signbit(chunk[1:]) != np.signbit(chunk[:-1])))
            if len(chunk) > 1
            else 0.0
        )
        step.append(float(np.max(np.abs(np.diff(chunk)))) if len(chunk) > 1 else 0.0)
    levels = 20.0 * np.log10(np.maximum(np.asarray(rms), 1e-9))
    zcr_array = np.asarray(zcr, dtype=np.float64)
    step_array = np.asarray(step, dtype=np.float64)
    peak = float(np.percentile(levels, 95))
    active = levels >= max(-49.0, peak - 31.0)

    # Sustained speech requires at least 40 ms of activity.  A detached burst
    # before it is the reference-tail/chirp signature reported for one-shot clone.
    sustained_start: int | None = None
    for index in range(max(0, min(len(active), int(0.55 / 0.005)))):
        right = min(len(active), index + 8)
        if right - index >= 6 and float(np.mean(active[index:right])) >= 0.75:
            sustained_start = index
            break
    if sustained_start is None or sustained_start < 3:
        return {"policy": START_ARTIFACT_POLICY, "suspicious": False, "reason": "no_detached_prefix"}

    pre = active[:sustained_start]
    ids = np.flatnonzero(pre)
    if not len(ids):
        return {"policy": START_ARTIFACT_POLICY, "suspicious": False, "reason": "quiet_prefix"}
    left, right = int(ids[0]), int(ids[-1] + 1)
    duration = (right - left) * 0.005
    gap = max(0.0, (sustained_start - right) * 0.005)
    burst_level = float(np.percentile(levels[left:right], 80))
    speech_right = min(len(levels), sustained_start + 30)
    speech_level = float(np.median(levels[sustained_start:speech_right]))
    median_zcr = float(np.median(zcr_array[left:right]))
    max_step = float(np.max(step_array[left:right]))
    suspicious = bool(
        0.008 <= duration <= 0.120
        and 0.012 <= gap <= 0.220
        and burst_level >= speech_level - 18.0
        and (
            median_zcr >= 0.20
            or max_step >= 0.075
            or burst_level >= speech_level - 5.0
        )
    )
    return {
        "policy": START_ARTIFACT_POLICY,
        "suspicious": suspicious,
        "artifact_type": "detached_reference_leak" if suspicious else "",
        "burst_start_seconds": left * 0.005,
        "burst_duration_seconds": duration,
        "gap_to_speech_seconds": gap,
        "burst_level_db": burst_level,
        "speech_level_db": speech_level,
        "burst_median_zcr": median_zcr,
        "burst_max_step": max_step,
    }


def _log_distance(value: float) -> float:
    return abs(math.log2(max(0.25, min(4.0, value))))


def _ratio(value: float, reference: float) -> float:
    if value <= 0.0 or reference <= 0.0:
        return 0.0
    return value / reference


def evaluate_candidate(candidate: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    global _PREVIOUS_IDENTITY
    segment_id = int(segment.get("id") or 0)
    set_current_segment_id(segment_id)
    identity = _identity_from_candidate(candidate)
    path = Path(str(candidate.get("path") or "."))
    work_dir = path.parent.parent if path.parent.name == "attempts" else path.parent
    if _PREVIOUS_IDENTITY is None and segment_id > 1:
        _PREVIOUS_IDENTITY = _load_previous_checkpoint(work_dir, segment_id)

    failures: list[str] = []
    penalty = 0.0
    anchor_similarity = _finite(identity.get("anchor_spectral_similarity"))
    anchor_median = _finite(identity.get("anchor_f0_median_ratio"))
    anchor_p90 = _finite(identity.get("anchor_f0_p90_ratio"))
    if anchor_similarity < ANCHOR_HARD_SIMILARITY:
        failures.append("identity_anchor_timbre")
    if anchor_median and not 0.55 <= anchor_median <= 1.70:
        failures.append("identity_anchor_f0_median")
    if anchor_p90 and not 0.52 <= anchor_p90 <= 1.78:
        failures.append("identity_anchor_f0_p90")
    penalty += max(0.0, ANCHOR_SOFT_SIMILARITY - anchor_similarity) * 130.0
    if anchor_median:
        penalty += _log_distance(anchor_median) * 20.0
    if anchor_p90:
        penalty += _log_distance(anchor_p90) * 12.0

    previous = dict(_PREVIOUS_IDENTITY) if isinstance(_PREVIOUS_IDENTITY, dict) else None
    neighbour: dict[str, Any] = {
        "available": False,
        "spectral_similarity": None,
        "f0_median_ratio": None,
        "f0_p90_ratio": None,
        "spectral_centroid_ratio": None,
    }
    if previous is not None:
        median_ratio = _ratio(identity["f0_median"], _finite(previous.get("f0_median")))
        p90_ratio = _ratio(identity["f0_p90"], _finite(previous.get("f0_p90")))
        centroid_ratio = _ratio(
            identity["spectral_centroid_hz"],
            _finite(previous.get("spectral_centroid_hz")),
        )
        previous_bands = previous.get("spectral_bands") or []
        current_bands = identity.get("spectral_bands") or []
        neighbour_similarity = (
            spectral_similarity(
                {"bands": current_bands},
                {"bands": previous_bands},
            )
            if current_bands and previous_bands
            else anchor_similarity
        )
        neighbour.update(
            available=True,
            spectral_similarity=neighbour_similarity,
            f0_median_ratio=median_ratio,
            f0_p90_ratio=p90_ratio,
            spectral_centroid_ratio=centroid_ratio,
        )
        if median_ratio and not 0.66 <= median_ratio <= 1.52:
            failures.append("adjacent_f0_median_jump")
        if p90_ratio and not 0.62 <= p90_ratio <= 1.62:
            failures.append("adjacent_f0_p90_jump")
        if neighbour_similarity < NEIGHBOUR_HARD_SIMILARITY:
            failures.append("adjacent_timbre_jump")
        if (
            neighbour_similarity < 0.72
            and centroid_ratio
            and not 0.70 <= centroid_ratio <= 1.43
        ):
            failures.append("adjacent_spectral_posture_jump")
        penalty += max(0.0, NEIGHBOUR_SOFT_SIMILARITY - neighbour_similarity) * 165.0
        if median_ratio:
            penalty += _log_distance(median_ratio) * 42.0
        if p90_ratio:
            penalty += _log_distance(p90_ratio) * 24.0
        if centroid_ratio:
            penalty += _log_distance(centroid_ratio) * 18.0

    duration = _finite(candidate.get("duration"))
    slot = _finite(candidate.get("actual_speech_slot"))
    duration_ratio = duration / max(0.1, slot)
    cadence = str(segment.get("cadence_type") or "")
    minimum_duration = (
        MIN_LINKED_DURATION_RATIO
        if cadence in {"linked", "continuation"}
        else MIN_TERMINAL_DURATION_RATIO
    )
    if duration_ratio < minimum_duration:
        failures.append("monolithic_phrase_too_short")
        penalty += (minimum_duration - duration_ratio) * 220.0

    start_artifact = _start_artifact(
        candidate.get("samples"),
        int(candidate.get("sample_rate") or 1),
    )
    if start_artifact.get("suspicious"):
        failures.append("start_reference_leak")
        penalty += 170.0

    stress = russian_pronunciation.stress_evidence(
        candidate.get("samples"),
        int(candidate.get("sample_rate") or 1),
        segment,
    )
    if stress.get("required") and not stress.get("passed"):
        failures.append("pronunciation_stress_not_verified")
        penalty += 160.0

    result = {
        "policy": POLICY,
        "neighbour_policy": NEIGHBOUR_POLICY,
        "segment_id": segment_id,
        "hard_ok": not failures,
        "failures": failures,
        "penalty": min(MAX_MONOLITH_PENALTY, max(0.0, penalty)),
        "identity": identity,
        "previous_identity": previous,
        "neighbour": neighbour,
        "anchor_limits": {
            "soft_similarity": ANCHOR_SOFT_SIMILARITY,
            "hard_similarity": ANCHOR_HARD_SIMILARITY,
        },
        "neighbour_limits": {
            "soft_similarity": NEIGHBOUR_SOFT_SIMILARITY,
            "hard_similarity": NEIGHBOUR_HARD_SIMILARITY,
            "f0_median_ratio": [0.66, 1.52],
            "f0_p90_ratio": [0.62, 1.62],
        },
        "duration_ratio": duration_ratio,
        "minimum_duration_ratio": minimum_duration,
        "start_artifact": start_artifact,
        "stress_evidence": stress,
    }
    candidate["monolith_identity"] = result
    return result


def candidate_hard_ok(candidate: dict[str, Any]) -> bool:
    evidence = candidate.get("monolith_identity")
    return bool(isinstance(evidence, dict) and evidence.get("hard_ok") is True)


def candidate_penalty(candidate: dict[str, Any]) -> float:
    evidence = candidate.get("monolith_identity")
    return _finite((evidence or {}).get("penalty")) if isinstance(evidence, dict) else MAX_MONOLITH_PENALTY


def record_acceptable(candidates: Iterable[dict[str, Any]]) -> None:
    global _CURRENT_BEST_IDENTITY
    usable = [
        item
        for item in candidates
        if isinstance(item, dict) and candidate_hard_ok(item)
    ]
    if not usable:
        return
    best = min(usable, key=lambda item: _finite(item.get("score"), 1e9))
    evidence = best.get("monolith_identity") or {}
    identity = evidence.get("identity")
    if isinstance(identity, dict):
        _CURRENT_BEST_IDENTITY = dict(identity)


__all__ = [
    "ANCHOR_HARD_SIMILARITY",
    "ANCHOR_SOFT_SIMILARITY",
    "MAX_MONOLITH_PENALTY",
    "MIN_LINKED_DURATION_RATIO",
    "MIN_TERMINAL_DURATION_RATIO",
    "NEIGHBOUR_HARD_SIMILARITY",
    "NEIGHBOUR_POLICY",
    "NEIGHBOUR_SOFT_SIMILARITY",
    "POLICY",
    "START_ARTIFACT_POLICY",
    "candidate_hard_ok",
    "candidate_penalty",
    "current_segment",
    "evaluate_candidate",
    "record_acceptable",
    "register_segments",
    "set_current_segment_id",
]
