#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed Russian pronunciation overrides for direct speech synthesis.

The display/ASR text is never changed. A bounded set of synthesis strings may
contain conservative syllable hints and the official VoxCPM ``(control)text``
prefix. Candidate attempts alternate those forms, while known final-word stress
is accepted only after independent acoustic evidence.
"""
from __future__ import annotations

import math
import re
from typing import Any

import numpy as np

POLICY = "russian-pronunciation-overrides-v1"
VARIANT_POLICY = "bounded-pronunciation-candidate-variants-v1"
CONTROL_POLICY = "stable-monolithic-control-instruction-v1"
STRESS_EVIDENCE_POLICY = "final-stressed-syllable-energy-duration-v1"

# Manual Russian stress syntax is not a documented VoxCPM2 contract. Keep the
# table deliberately small and test both the canonical spelling and one bounded
# syllable hint instead of assuming that a hyphen is universally beneficial.
_RULES: tuple[dict[str, Any], ...] = (
    {
        "name": "gryadyot-final-stress",
        "pattern": re.compile(r"\bгряд[её]т\b", re.IGNORECASE),
        "spoken_form": "грядёт",
        "replacement": "гря-дёт",
        "variants": ("грядёт", "гря-дёт"),
        "stress": "final",
    },
)


def _clean_control(value: str) -> str:
    return re.sub(r"[()（）]+", " ", str(value or "")).strip()


def _tier_control(tier: str) -> str:
    normalized = str(tier or "").casefold()
    if normalized == "emphatic":
        return "slightly firmer emphasis, still restrained and conversational"
    if normalized in {"reflective", "warm"}:
        return "calm, warm and conversational"
    return "natural conversational emphasis"


def _is_final_lexical_match(text: str, end: int) -> bool:
    return not any(char.isalpha() or char.isdigit() for char in str(text)[int(end):])


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = re.sub(r"\s+", " ", str(value or "")).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def prepare_segment(segment: dict[str, Any]) -> dict[str, Any]:
    """Return transparent display, bounded synthesis variants and control metadata."""
    display = re.sub(r"\s+", " ", str(segment.get("text") or "")).strip()
    variants = [display]
    overrides: list[dict[str, Any]] = []
    for rule in _RULES:
        display_matches = list(rule["pattern"].finditer(display))
        if not display_matches:
            continue
        final_word = _is_final_lexical_match(display, display_matches[-1].end())
        forms = [str(value) for value in rule.get("variants") or () if str(value)]
        if not forms:
            forms = [str(rule["spoken_form"]), str(rule["replacement"])]
        variants = _dedupe(
            [
                rule["pattern"].sub(form, existing)
                for existing in variants
                for form in forms
            ]
        )
        overrides.append(
            {
                "name": str(rule["name"]),
                "spoken_form": str(rule["spoken_form"]),
                "synthesis_form": str(rule["replacement"]),
                "synthesis_forms": forms,
                "stress": str(rule["stress"]),
                "final_word": final_word,
                "acoustic_evidence_required": final_word,
            }
        )

    controls = [
        "same adult male speaker and timbre as the reference",
        "one connected sermon performance",
        _tier_control(str(segment.get("expression_tier") or "")),
        "no character voice, no sudden bass shift, no shouting",
    ]
    lowered = display.casefold().replace("ё", "е")
    if "смеет" in lowered or "смею" in lowered:
        controls.append("say the words normally; do not laugh, chuckle or imitate laughter")
    if overrides:
        controls.append("Russian pronunciation: stress the final syllable in gryadyot")
    control = ", ".join(_clean_control(item) for item in controls if _clean_control(item))
    controlled = [f"({control}){value}" if control else value for value in variants]
    evidence_required = any(
        bool(item.get("acoustic_evidence_required")) for item in overrides
    )
    return {
        "policy": POLICY,
        "variant_policy": VARIANT_POLICY,
        "control_policy": CONTROL_POLICY,
        "display_text": display,
        "synthesis_text": controlled[0] if controlled else display,
        "synthesis_text_without_control": variants[0] if variants else display,
        "synthesis_variants": controlled or [display],
        "synthesis_variants_without_control": variants or [display],
        "control_instruction": control,
        "overrides": overrides,
        "stress_evidence_required": evidence_required,
    }


def variant_for_attempt(segment: dict[str, Any], attempt: int = 1) -> dict[str, Any]:
    prepared = segment.get("pronunciation")
    if not isinstance(prepared, dict) or prepared.get("policy") != POLICY:
        prepared = prepare_segment(segment)
    controlled = [str(value) for value in prepared.get("synthesis_variants") or []]
    plain = [
        str(value)
        for value in prepared.get("synthesis_variants_without_control") or []
    ]
    if not controlled:
        controlled = [str(prepared.get("synthesis_text") or segment.get("text") or "")]
    if not plain:
        plain = [
            str(
                prepared.get("synthesis_text_without_control")
                or segment.get("text")
                or ""
            )
        ]
    index = (max(1, int(attempt)) - 1) % len(controlled)
    return {
        "policy": VARIANT_POLICY,
        "attempt": max(1, int(attempt)),
        "variant_index": index,
        "variant_count": len(controlled),
        "synthesis_text": controlled[index],
        "synthesis_text_without_control": plain[min(index, len(plain) - 1)],
    }


def synthesis_text(segment: dict[str, Any], attempt: int = 1) -> str:
    return str(variant_for_attempt(segment, attempt).get("synthesis_text") or "").strip()


def _mono(samples: Any) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.reshape(-1)


def _frame_features(audio: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    frame = max(160, int(rate * 0.020))
    hop = max(80, int(rate * 0.010))
    starts = np.arange(0, max(0, len(audio) - frame + 1), hop, dtype=np.int64)
    if not len(starts):
        empty = np.asarray([], dtype=np.float64)
        return empty, empty, empty
    levels: list[float] = []
    zcr: list[float] = []
    times: list[float] = []
    for start in starts:
        chunk = audio[start : start + frame].astype(np.float64)
        rms = math.sqrt(float(np.mean(chunk**2)) + 1e-12)
        levels.append(20.0 * math.log10(max(rms, 1e-9)))
        zcr.append(
            float(np.mean(np.signbit(chunk[1:]) != np.signbit(chunk[:-1])))
            if len(chunk) > 1
            else 0.0
        )
        times.append((start + frame / 2) / rate)
    return (
        np.asarray(times, dtype=np.float64),
        np.asarray(levels, dtype=np.float64),
        np.asarray(zcr, dtype=np.float64),
    )


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(mask.tolist()):
        if value and start is None:
            start = index
        if start is not None and (not value or index == len(mask) - 1):
            end = index if not value else index + 1
            if end > start:
                result.append((start, end))
            start = None
    return result


def stress_evidence(samples: Any, sample_rate: int, segment: dict[str, Any]) -> dict[str, Any]:
    """Verify a known final stress through the last two vowel-like nuclei.

    This is intentionally narrow. It does not claim to be a general Russian
    stress recognizer and runs only when the overridden word is the final lexical
    word in the segment.
    """
    prepared = segment.get("pronunciation")
    if not isinstance(prepared, dict):
        prepared = prepare_segment(segment)
    overrides = prepared.get("overrides")
    required = bool(
        isinstance(overrides, list)
        and any(
            isinstance(item, dict) and item.get("acoustic_evidence_required") is True
            for item in overrides
        )
    )
    if not required:
        return {
            "policy": STRESS_EVIDENCE_POLICY,
            "required": False,
            "passed": True,
            "reason": "no_final_known_override",
        }

    audio = _mono(samples)
    rate = max(1, int(sample_rate))
    if not len(audio) or not np.isfinite(audio).all():
        return {
            "policy": STRESS_EVIDENCE_POLICY,
            "required": True,
            "passed": False,
            "reason": "invalid_audio",
        }
    times, levels, zcr = _frame_features(audio, rate)
    if len(levels) < 12:
        return {
            "policy": STRESS_EVIDENCE_POLICY,
            "required": True,
            "passed": False,
            "reason": "too_short",
        }

    peak = float(np.percentile(levels, 95))
    active = levels >= max(-48.0, peak - 30.0)
    active_ids = np.flatnonzero(active)
    if not len(active_ids):
        return {
            "policy": STRESS_EVIDENCE_POLICY,
            "required": True,
            "passed": False,
            "reason": "no_active_speech",
        }
    active_end = int(active_ids[-1])
    search_start = max(int(active_ids[0]), active_end - 115)
    vowel_like = active & (zcr <= 0.18) & (levels >= max(-43.0, peak - 23.0))
    nuclei = [
        (left, right)
        for left, right in _runs(vowel_like[search_start:active_end + 1])
        if right - left >= 2
    ]
    nuclei = [(left + search_start, right + search_start) for left, right in nuclei]
    merged: list[tuple[int, int]] = []
    for left, right in nuclei:
        if merged and left - merged[-1][1] <= 2:
            merged[-1] = (merged[-1][0], right)
        else:
            merged.append((left, right))
    if len(merged) < 2:
        return {
            "policy": STRESS_EVIDENCE_POLICY,
            "required": True,
            "passed": False,
            "reason": "two_syllable_nuclei_not_resolved",
            "nuclei": len(merged),
        }

    previous = merged[-2]
    final = merged[-1]
    previous_duration = (previous[1] - previous[0]) * 0.010
    final_duration = (final[1] - final[0]) * 0.010
    previous_level = float(np.median(levels[previous[0]:previous[1]]))
    final_level = float(np.median(levels[final[0]:final[1]]))
    duration_ratio = final_duration / max(0.01, previous_duration)
    level_delta = final_level - previous_level
    final_near_end = bool(final[1] >= active_end - 7)
    passed = bool(
        final_near_end
        and duration_ratio >= 0.78
        and level_delta >= -2.2
        and (duration_ratio >= 1.02 or level_delta >= -0.8)
    )
    return {
        "policy": STRESS_EVIDENCE_POLICY,
        "required": True,
        "passed": passed,
        "reason": "" if passed else "final_stressed_nucleus_not_supported",
        "previous_start": float(times[previous[0]]),
        "previous_end": float(times[min(len(times) - 1, previous[1] - 1)]),
        "final_start": float(times[final[0]]),
        "final_end": float(times[min(len(times) - 1, final[1] - 1)]),
        "previous_duration": previous_duration,
        "final_duration": final_duration,
        "duration_ratio": duration_ratio,
        "previous_level_db": previous_level,
        "final_level_db": final_level,
        "level_delta_db": level_delta,
        "final_near_active_end": final_near_end,
    }


__all__ = [
    "CONTROL_POLICY",
    "POLICY",
    "STRESS_EVIDENCE_POLICY",
    "VARIANT_POLICY",
    "prepare_segment",
    "stress_evidence",
    "synthesis_text",
    "variant_for_attempt",
]
