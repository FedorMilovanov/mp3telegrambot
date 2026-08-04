#!/usr/bin/env python3
"""Final no-compromise acceptance gate for Shorts Factory plans."""
from __future__ import annotations

import copy
import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MIN_SHORT_SCORE = 88.0
DEFAULT_MIN_LONG_SCORE = 85.0
_INSTALLED = False


def _score_threshold(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(value, 100.0))


def _score(item: dict[str, Any]) -> float:
    try:
        return float(item.get("quality_score", 0))
    except (TypeError, ValueError, OverflowError):
        return 0.0


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

    raw_shorts = result.get("shorts_candidates") or []
    raw_longs = result.get("long_candidates") or []
    accepted_shorts = [
        item
        for item in raw_shorts
        if isinstance(item, dict)
        and item.get("boundary_verified") is True
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
        and _score(item) >= long_threshold
        and str(item.get("title") or "").strip()
        and str(item.get("reason") or "").strip()
    ]

    accepted_shorts.sort(
        key=lambda item: (-_score(item), float(item.get("start_seconds", 0)))
    )
    accepted_longs.sort(
        key=lambda item: (-_score(item), float(item.get("start_seconds", 0)))
    )
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
    metadata = plan.get("metadata") if isinstance(plan, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    raw_language = str(metadata.get("language") or "").strip()

    from services.shorts_factory_execution_guard import (
        normalize_factory_language,
    )

    normalized = normalize_factory_language(raw_language)
    if not normalized:
        raise RuntimeError(
            "Gemini не доказала один доминирующий язык речи по аудио"
        )
    return normalized


def install_factory_plan_quality_gate() -> bool:
    """Install every post-media Factory/cut guard before lazy pipeline imports."""
    global _INSTALLED
    if _INSTALLED:
        return True

    from services.cut_mode_source_policy import install_cut_mode_source_policy
    from services.cut_replay_delivery_policy import (
        install_cut_replay_delivery_policy,
    )
    from services.shorts_factory_execution_guard import (
        install_shorts_factory_execution_guard,
    )
    from services.shorts_factory_no_downgrade import (
        install_factory_no_downgrade_policy,
    )
    from services.shorts_factory_source import (
        install_factory_source_quality_policy,
    )
    import services.shorts_factory_candidates as candidates_module

    if not install_cut_mode_source_policy():
        return False
    if not install_cut_replay_delivery_policy():
        return False
    if not install_factory_no_downgrade_policy():
        return False
    if not install_factory_source_quality_policy():
        return False

    original_boundary_prompt = candidates_module._boundary_prompt
    original_create_factory_plan = candidates_module.create_factory_plan

    def strict_boundary_prompt(judged_plan, duration):
        return original_boundary_prompt(judged_plan, duration) + (
            "\n\nОБЯЗАТЕЛЬНО: metadata.language должен содержать один "
            "доминирующий фактически услышанный язык речи как ISO 639-1 "
            "(например ru, en, de). Не определяй язык по заголовку. "
            "Если доминирующий язык доказать нельзя, верни mixed."
        )

    candidates_module._boundary_prompt = strict_boundary_prompt

    async def strict_create_factory_plan(*args, **kwargs):
        plan = await original_create_factory_plan(*args, **kwargs)
        gated = apply_factory_quality_gate(plan)
        normalized_language = validated_factory_plan_language(gated)
        metadata = gated.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["language"] = normalized_language
        if not gated.get("shorts_candidates") and not gated.get("long_candidates"):
            report = gated.get("quality_gate") or {}
            raise RuntimeError(
                "Gemini завершила три проверки, но ни один фрагмент не прошёл "
                "финальный MAX-порог качества: "
                f"Shorts>={report.get('min_short_score')}, "
                f"long>={report.get('min_long_score')}"
            )
        logger.info("Shorts Factory final quality gate: %s", gated["quality_gate"])
        return gated

    candidates_module.create_factory_plan = strict_create_factory_plan
    eager_factory = sys.modules.get("pipelines.shorts_factory")
    if eager_factory is not None:
        eager_factory.create_factory_plan = strict_create_factory_plan

    if not install_shorts_factory_execution_guard():
        return False

    _INSTALLED = True
    logger.info(
        "Shorts Factory post-media guards installed: validated no-downgrade "
        "configuration, maximum-quality native sources, exact audited "
        "boundaries, spoken-language execution, translated ENG source and "
        "truthful cached cut replay delivery"
    )
    return True


__all__ = [
    "DEFAULT_MIN_LONG_SCORE",
    "DEFAULT_MIN_SHORT_SCORE",
    "apply_factory_quality_gate",
    "install_factory_plan_quality_gate",
    "validated_factory_plan_language",
]
