#!/usr/bin/env python3
"""Pure Factory plan acceptance policy."""
from __future__ import annotations

import copy
import math
import os
from typing import Any

DEFAULT_MIN_SHORT_SCORE = 88.0
DEFAULT_MIN_LONG_SCORE = 85.0


def _score_threshold(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(value):
        return default
    return max(0.0, min(value, 100.0))


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _score(item: dict[str, Any]) -> float:
    value = _finite_number(item.get("quality_score"))
    return value if value is not None else -math.inf


def _valid_interval(item: dict[str, Any]) -> bool:
    start = _finite_number(item.get("start_seconds"))
    end = _finite_number(item.get("end_seconds"))
    return bool(start is not None and end is not None and end > start >= 0.0)


def _start(item: dict[str, Any]) -> float:
    value = _finite_number(item.get("start_seconds"))
    return value if value is not None else math.inf


def _candidate_items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def apply_factory_quality_gate(plan: dict[str, Any]) -> dict[str, Any]:
    """Keep only editorially strong, complete and boundary-verified candidates."""
    result = copy.deepcopy(plan if isinstance(plan, dict) else {})
    short_threshold = _score_threshold(
        "SHORTS_FACTORY_MIN_SHORT_SCORE",
        DEFAULT_MIN_SHORT_SCORE,
    )
    long_threshold = _score_threshold(
        "SHORTS_FACTORY_MIN_LONG_SCORE",
        DEFAULT_MIN_LONG_SCORE,
    )

    raw_shorts = _candidate_items(result.get("shorts_candidates"))
    raw_longs = _candidate_items(result.get("long_candidates"))
    accepted_shorts = [
        item
        for item in raw_shorts
        if isinstance(item, dict)
        and item.get("boundary_verified") is True
        and _valid_interval(item)
        and _score(item) >= short_threshold
        and str(item.get("title") or "").strip()
        and str(item.get("hook") or "").strip()
        and str(item.get("reason") or "").strip()
    ]
    accepted_longs = [
        item
        for item in raw_longs
        if isinstance(item, dict)
        and item.get("boundary_verified") is True
        and _valid_interval(item)
        and _score(item) >= long_threshold
        and str(item.get("title") or "").strip()
        and str(item.get("reason") or "").strip()
    ]

    accepted_shorts.sort(key=lambda item: (-_score(item), _start(item)))
    accepted_longs.sort(key=lambda item: (-_score(item), _start(item)))
    result["shorts_candidates"] = accepted_shorts[:5]
    result["long_candidates"] = accepted_longs[:3]
    result["quality_gate"] = {
        "policy": "shorts-factory-final-quality-v1",
        "min_short_score": short_threshold,
        "min_long_score": long_threshold,
        "shorts_before": len(raw_shorts),
        "shorts_after": len(result["shorts_candidates"]),
        "longs_before": len(raw_longs),
        "longs_after": len(result["long_candidates"]),
    }
    return result


def validated_factory_plan_language(plan: dict[str, Any]) -> str:
    """Normalize the audio-proven language and fail closed on mixed/unknown input."""
    metadata = plan.get("metadata") if isinstance(plan, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    raw_language = str(metadata.get("language") or "").strip()

    from services.shorts_factory_execution_guard import normalize_factory_language

    normalized = normalize_factory_language(raw_language)
    if not normalized:
        raise RuntimeError(
            "Gemini не доказала один доминирующий язык речи по аудио"
        )
    return normalized


__all__ = [
    "DEFAULT_MIN_LONG_SCORE",
    "DEFAULT_MIN_SHORT_SCORE",
    "apply_factory_quality_gate",
    "validated_factory_plan_language",
]
