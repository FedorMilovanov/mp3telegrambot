#!/usr/bin/env python3
"""Pre-main maximum-quality Gemini and ASR policy.

Quality is enforced before ``core.globals`` creates clients/config helpers.  This
module never scans ``sys.modules`` or rewrites references after import.
"""
from __future__ import annotations

import os

_HEAVY_MODEL = "gemini-3.6-flash"
_LIGHT_MODEL = "gemini-3.5-flash-lite"
_LIGHT_FALLBACK_MODEL = "gemini-3.5-flash"
_REQUIRED_WHISPER_MODEL = "large-v3"


def configure_max_quality_env() -> str:
    """Apply the production semantic/utility/ASR quality contract."""
    for name in (
        "GEMINI_MODEL",
        "GEMINI_MAX_MODEL",
        "LIVEDUB_INFO_MODEL",
        "LIVEDUB_QUICK_QA_MODEL",
        "LIVEDUB_LONG_QA_MODEL",
        "LIVEDUB_QA_VERIFY_MODEL",
        "SHORTS_FACTORY_MODEL",
    ):
        os.environ[name] = _HEAVY_MODEL
    os.environ["LIVEDUB_INFO_FALLBACK_MODELS"] = ""

    os.environ["GEMINI_LIGHT_MODEL"] = _LIGHT_MODEL
    os.environ["GEMINI_LIGHT_FALLBACK_MODELS"] = _LIGHT_FALLBACK_MODEL
    os.environ["GEMINI_LIGHT_ALLOW_MAIN_FALLBACK"] = "0"

    os.environ["LIVEDUB_PUBLICATION_FALLBACK_MODELS"] = ""
    os.environ["LIVEDUB_PUBLICATION_ALLOW_STRONG_FALLBACK"] = "0"

    os.environ["GEMINI_FORCE_THINKING_LEVEL"] = "high"
    os.environ["GEMINI_SCHEMA_THINKING"] = "1"
    for name in (
        "LIVEDUB_QUICK_QA_THINKING",
        "LIVEDUB_LONG_QA_THINKING",
        "LIVEDUB_QA_VERIFY_THINKING",
        "LIVEDUB_INFO_THINKING",
    ):
        os.environ[name] = "high"

    for name in (
        "WHISPER_MODEL",
        "WHISPER_ENG_SUBTITLES_MODEL",
        "SHORTS_FACTORY_WHISPER_MODEL",
    ):
        os.environ[name] = _REQUIRED_WHISPER_MODEL

    return (
        f"semantic={_HEAVY_MODEL}/high; "
        f"utility={_LIGHT_MODEL}->{_LIGHT_FALLBACK_MODEL}/minimal; "
        "semantic_model_fallbacks=none; publication=3.6/high; "
        f"whisper={_REQUIRED_WHISPER_MODEL}"
    )


__all__ = ["configure_max_quality_env"]
