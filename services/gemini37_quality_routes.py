#!/usr/bin/env python3
"""Install Gemini 3.7 quality-first routes without semantic downgrades.

The project keeps Gemini 3.7 Flash/high as the primary quality route and permits
Gemini 3.6 Flash/high only as a quality-preserving capacity fallback. 3.5/Lite
never enters Factory candidate selection or full-sermon translation review.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

PRIMARY_MODEL = "gemini-3.7-flash"
FALLBACK_MODEL = "gemini-3.6-flash"
_ALLOWED_QUALITY_MODELS = {PRIMARY_MODEL, FALLBACK_MODEL}
_INSTALLED = False
_EDITORIAL_LOCK = asyncio.Lock()


def quality_model_chain() -> tuple[str, ...]:
    """Return the fail-closed semantic model chain configured for production."""
    primary = os.getenv("GEMINI_MODEL", PRIMARY_MODEL).strip() or PRIMARY_MODEL
    if primary not in _ALLOWED_QUALITY_MODELS:
        raise RuntimeError(
            "Quality-sensitive Gemini work requires gemini-3.7-flash or "
            f"gemini-3.6-flash; GEMINI_MODEL={primary!r} is not allowed"
        )
    raw_fallbacks = os.getenv("GEMINI_QUALITY_FALLBACK_MODELS", FALLBACK_MODEL)
    models = [primary]
    for value in str(raw_fallbacks or "").split(","):
        model = value.strip()
        if not model or model in models:
            continue
        if model not in _ALLOWED_QUALITY_MODELS:
            raise RuntimeError(
                "Semantic fallback may only use gemini-3.7-flash/high or "
                f"gemini-3.6-flash/high; got {model!r}"
            )
        models.append(model)
    return tuple(models)


def _install_factory_model_policy() -> None:
    import services.shorts_factory_candidates as candidates

    def require_factory_model(model: str, source: str) -> str:
        value = str(model or "").strip()
        if value not in _ALLOWED_QUALITY_MODELS:
            raise RuntimeError(
                "SHORTS FACTORY MAX requires Gemini 3.7 Flash/high with "
                "Gemini 3.6 Flash/high as the only semantic fallback; "
                f"{source}={value!r} is not allowed"
            )
        return value

    def factory_model() -> str:
        explicit = os.getenv("SHORTS_FACTORY_MODEL", "").strip()
        return require_factory_model(
            explicit or PRIMARY_MODEL,
            "SHORTS_FACTORY_MODEL" if explicit else "default",
        )

    candidates.DEFAULT_SHORTS_FACTORY_MODEL = PRIMARY_MODEL
    candidates._require_factory_model = require_factory_model
    candidates.shorts_factory_model = factory_model


def _install_editorial_model_policy() -> None:
    import services.translation_editorial_factory as editorial

    if getattr(editorial.generate_gemini_editorial_review, "_mp3bot_gemini37_route", False):
        editorial.FACTORY_EDITORIAL_GEMINI_MODEL = PRIMARY_MODEL
        return

    original = editorial.generate_gemini_editorial_review

    async def quality_first_editorial(pack_path):
        # The legacy implementation stores the selected model in one module-level
        # constant. Serialize this optional review seam so concurrent jobs cannot
        # observe each other's primary/fallback model while retaining the original
        # validated review schema and quota bounds.
        async with _EDITORIAL_LOCK:
            last_model = PRIMARY_MODEL
            try:
                for model in quality_model_chain():
                    last_model = model
                    editorial.FACTORY_EDITORIAL_GEMINI_MODEL = model
                    result = await original(pack_path)
                    if result is not None:
                        if isinstance(result, dict):
                            result.setdefault("quality_route", {})
                            result["quality_route"].update(
                                primary_model=PRIMARY_MODEL,
                                effective_model=model,
                                thinking_level="high",
                                semantic_downgrade=False,
                            )
                        return result
                    if model != quality_model_chain()[-1]:
                        logger.warning(
                            "Factory full-sermon editorial review soft-failed on %s; "
                            "retrying on quality fallback %s/high",
                            model,
                            FALLBACK_MODEL,
                        )
                return None
            finally:
                editorial.FACTORY_EDITORIAL_GEMINI_MODEL = PRIMARY_MODEL
                if last_model != PRIMARY_MODEL:
                    logger.info(
                        "Factory editorial quality route returned to primary %s",
                        PRIMARY_MODEL,
                    )

    quality_first_editorial._mp3bot_gemini37_route = True  # type: ignore[attr-defined]
    editorial.FACTORY_EDITORIAL_GEMINI_MODEL = PRIMARY_MODEL
    editorial.generate_gemini_editorial_review = quality_first_editorial


def install_gemini37_quality_routes() -> str:
    """Patch legacy 3.6-only seams after core globals exist, before Factory runs."""
    global _INSTALLED
    if _INSTALLED:
        return "Gemini 3.7/high primary; 3.6/high semantic fallback"

    _install_factory_model_policy()
    _install_editorial_model_policy()
    _INSTALLED = True
    logger.info(
        "🧠 Gemini semantic route: 3.7/high primary → 3.6/high fallback; "
        "no 3.5/Lite semantic downgrade"
    )
    return "Gemini 3.7/high primary; 3.6/high semantic fallback"


__all__ = [
    "FALLBACK_MODEL",
    "PRIMARY_MODEL",
    "install_gemini37_quality_routes",
    "quality_model_chain",
]
