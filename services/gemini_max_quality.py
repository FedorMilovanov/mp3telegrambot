#!/usr/bin/env python3
"""Pre-main maximum-quality Gemini and ASR policy.

Quality is enforced before ``core.globals`` creates clients/config helpers.  This
module never scans ``sys.modules`` or rewrites references after import.
"""

from __future__ import annotations

from core.model_catalog import (
    MODEL_PRIMARY as _HEAVY_MODEL,
    MODEL_UTILITY as _LIGHT_MODEL,
)
from services.env_policy import force, force_all, override_summary

_REQUIRED_WHISPER_MODEL = "large-v3"


def configure_max_quality_env() -> str:
    """Apply the production semantic/utility/ASR quality contract."""
    owner = "services.gemini_max_quality"
    force_all(
        (
            "GEMINI_MODEL",
            "LIVEDUB_INFO_MODEL",
            "LIVEDUB_QUICK_QA_MODEL",
            "LIVEDUB_LONG_QA_MODEL",
            "LIVEDUB_QA_VERIFY_MODEL",
            "SHORTS_FACTORY_MODEL",
        ),
        _HEAVY_MODEL,
        owner=owner,
    )
    force("LIVEDUB_INFO_FALLBACK_MODELS", "", owner=owner)

    force("LIVEDUB_PUBLICATION_FALLBACK_MODELS", "", owner=owner)

    force("GEMINI_SCHEMA_THINKING", "1", owner=owner)
    force_all(
        (
            "LIVEDUB_QUICK_QA_THINKING",
            "LIVEDUB_LONG_QA_THINKING",
            "LIVEDUB_QA_VERIFY_THINKING",
        ),
        "high",
        owner=owner,
    )

    force_all(
        ("WHISPER_MODEL", "SHORTS_FACTORY_WHISPER_MODEL"),
        _REQUIRED_WHISPER_MODEL,
        owner=owner,
    )

    diagnostic = (
        f"semantic={_HEAVY_MODEL}/high; "
        f"utility={_LIGHT_MODEL}/minimal/no-fallback; "
        "semantic_model_fallbacks=none; publication=3.8/high; "
        f"whisper={_REQUIRED_WHISPER_MODEL}"
    )
    overridden = override_summary()
    return f"{diagnostic}; overridden_env={overridden}" if overridden else diagnostic


__all__ = ["configure_max_quality_env"]
