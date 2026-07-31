#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monolithic-expression facade for source-guided Dub Studio delivery.

The sibling implementation still measures the source prosody.  This facade
changes the unsafe performance decision: every segment uses one calm identity
reference, emotional scores move slowly, isolated strong bursts are suppressed,
and style metadata explicitly requests one connected sermon performance.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from tools.voxcpm2 import russian_pronunciation

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "expressive_continuity.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._expressive_continuity_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить source-guided expression: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

POLICY = "source-guided-monolithic-expression-v3"
REFERENCE_POLICY = "single-calm-identity-reference-v1"
ARC_POLICY = "bounded-neighbour-supported-emotion-v1"
MAX_ADJACENT_SCORE_STEP = 0.26
MIN_STRONG_NEIGHBOUR_SCORE = 0.20


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return result if result == result and abs(result) != float("inf") else float(default)


def _monolithic_scores(values: list[float]) -> list[float]:
    if not values:
        return []
    # Two low-pass passes keep real builds but remove one-cue emotional spikes.
    current = [max(-0.72, min(0.72, _number(value))) for value in values]
    for _ in range(2):
        smoothed: list[float] = []
        for index, value in enumerate(current):
            left = current[index - 1] if index else value
            right = current[index + 1] if index + 1 < len(current) else value
            smoothed.append(left * 0.24 + value * 0.52 + right * 0.24)
        limited = [smoothed[0]]
        for value in smoothed[1:]:
            limited.append(
                max(
                    limited[-1] - MAX_ADJACENT_SCORE_STEP,
                    min(limited[-1] + MAX_ADJACENT_SCORE_STEP, value),
                )
            )
        for index in range(len(limited) - 2, -1, -1):
            limited[index] = max(
                limited[index + 1] - MAX_ADJACENT_SCORE_STEP,
                min(limited[index + 1] + MAX_ADJACENT_SCORE_STEP, limited[index]),
            )
        current = [max(-0.65, min(0.68, value)) for value in limited]
    return current


def _tier(scores: list[float], index: int) -> str:
    score = scores[index]
    if score <= -0.38:
        return "reflective"
    if score <= -0.04:
        return "warm"
    if score <= 0.38:
        return "earnest"
    neighbours = []
    if index:
        neighbours.append(scores[index - 1])
    if index + 1 < len(scores):
        neighbours.append(scores[index + 1])
    return (
        "emphatic"
        if neighbours and max(neighbours) >= MIN_STRONG_NEIGHBOUR_SCORE
        else "earnest"
    )


def _style(tier: str, cadence: str) -> str:
    base = {
        "reflective": "calm reflective delivery, natural connected phrasing",
        "warm": "warm sincere delivery, natural connected phrasing",
        "earnest": "earnest conversational delivery, restrained natural emphasis",
        "emphatic": "slightly firmer emphasis, controlled and conversational, never theatrical",
    }.get(tier, "natural connected conversational delivery")
    if cadence in {"linked", "continuation"}:
        return base + ", carry the thought smoothly into the next phrase"
    if cadence == "question":
        return base + ", genuine but restrained questioning cadence"
    if cadence in {"terminal", "firm_terminal"}:
        return base + ", finish naturally without a sudden emotional burst"
    return base


def plan_segments(
    *,
    source: Path,
    segments: list[dict[str, Any]],
    duration: float,
    report_path: Path,
) -> list[dict[str, Any]]:
    original_text = [str(item.get("text") or "") for item in segments]
    measured = _legacy.plan_segments(
        source=source,
        segments=segments,
        duration=duration,
        report_path=report_path,
    )
    scores = _monolithic_scores(
        [_number(item.get("expression_score")) for item in measured]
    )
    result: list[dict[str, Any]] = []
    report_segments: list[dict[str, Any]] = []
    for index, item in enumerate(measured):
        updated = dict(item)
        tier = _tier(scores, index)
        cadence = str(updated.get("cadence_type") or _legacy.classify_cadence(original_text[index]))
        updated.update(
            expression_policy=POLICY,
            expression_arc_policy=ARC_POLICY,
            expression_score=round(scores[index], 6),
            expression_tier=tier,
            reference_profile="extended",
            identity_reference_profile="extended",
            reference_policy=REFERENCE_POLICY,
            style_instruction=_style(tier, cadence),
            cadence_type=cadence,
        )
        updated["pronunciation"] = russian_pronunciation.prepare_segment(updated)
        result.append(updated)
        report_segments.append(
            {
                "id": int(updated.get("id") or index + 1),
                "tier": tier,
                "score": round(scores[index], 6),
                "raw_score": round(_number(item.get("expression_score")), 6),
                "reference_profile": "extended",
                "identity_reference_profile": "extended",
                "cadence_type": cadence,
                "style_instruction": updated["style_instruction"],
                "pronunciation": updated["pronunciation"],
                "source_prosody": updated.get("source_prosody") or {},
            }
        )

    if [str(item.get("text") or "") for item in result] != original_text:
        raise RuntimeError("Monolithic expression plan изменил русский текст.")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "policy": POLICY,
                "reference_policy": REFERENCE_POLICY,
                "arc_policy": ARC_POLICY,
                "source": str(source),
                "segments": report_segments,
                "notes": [
                    "Every segment uses the same extended identity reference.",
                    "Adjacent expression scores are low-pass filtered and step-limited.",
                    "An isolated strong cue is downgraded unless a neighbour supports the build.",
                    "Passionate/character-acting tiers are disabled for short-form dubbing.",
                    "Display and ASR text remain unchanged; synthesis text is explicit metadata.",
                ],
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    return result


def plan_json(
    *,
    source: Path,
    segments_path: Path,
    duration: float,
    report_path: Path,
) -> list[dict[str, Any]]:
    payload = json.loads(segments_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("segments_ru_final.json пуст или повреждён.")
    if any(not isinstance(item, dict) for item in payload):
        raise RuntimeError("segments_ru_final.json содержит повреждённые записи.")
    planned = plan_segments(
        source=source,
        segments=[dict(item) for item in payload],
        duration=duration,
        report_path=report_path,
    )
    temporary = segments_path.with_suffix(segments_path.suffix + ".monolith.tmp")
    temporary.write_text(
        json.dumps(planned, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(segments_path)
    return planned


def build_controlled_expressive_reference(
    *,
    source: Path,
    segments: list[dict[str, Any]],
    output: Path,
    target_seconds: float = 7.0,
) -> bool:
    """Keep the calm composite; expression may not replace speaker identity."""
    return False


_legacy.POLICY = POLICY
_legacy.plan_segments = plan_segments
_legacy.plan_json = plan_json
_legacy.build_controlled_expressive_reference = build_controlled_expressive_reference

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {
        "ARC_POLICY",
        "MAX_ADJACENT_SCORE_STEP",
        "MIN_STRONG_NEIGHBOUR_SCORE",
        "POLICY",
        "REFERENCE_POLICY",
        "build_controlled_expressive_reference",
        "plan_json",
        "plan_segments",
    }
)
