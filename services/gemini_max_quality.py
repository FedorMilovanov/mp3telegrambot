#!/usr/bin/env python3
"""Enforce the production quality/cost split for Gemini and ASR.

Heavy semantic work stays on Gemini 3.6 Flash with high thinking. Small,
mechanical and high-volume text work uses the current Gemini 3.5 Flash-Lite
quota, with Gemini 3.5 Flash as its same-generation fallback. Production never
falls back to 3.1/2.x and never spends the 3.6 quota on explicitly light work.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any, Callable

logger = logging.getLogger(__name__)
_INSTALLED = False
_HEAVY_MODEL = "gemini-3.6-flash"
_LIGHT_MODEL = "gemini-3.5-flash-lite"
_LIGHT_FALLBACK_MODEL = "gemini-3.5-flash"
_REQUIRED_WHISPER_MODEL = "large-v3"


def configure_max_quality_env() -> str:
    """Apply the quality/cost policy before ``core.globals`` creates clients."""
    # Heavy user-facing semantic work: never downgrade the model family. Client
    # reliability comes from rotating GEMINI_API_KEY[_2.._4], not from 3.5/3.1.
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

    # Cheap/light work: titles, compact descriptions, small rewrites and other
    # mechanical formatting should consume the 3.5 quota, not the 3.6 quota.
    os.environ["GEMINI_LIGHT_MODEL"] = _LIGHT_MODEL
    os.environ["GEMINI_LIGHT_FALLBACK_MODELS"] = _LIGHT_FALLBACK_MODEL
    os.environ["GEMINI_LIGHT_ALLOW_MAIN_FALLBACK"] = "0"
    os.environ["LIVEDUB_PUBLICATION_FALLBACK_MODELS"] = _LIGHT_FALLBACK_MODEL
    os.environ["LIVEDUB_PUBLICATION_ALLOW_STRONG_FALLBACK"] = "1"

    # Heavy Gemini 3.x reasoning. Schema output must not disable thinking.
    os.environ["GEMINI_FORCE_THINKING_LEVEL"] = "high"
    os.environ["GEMINI_SCHEMA_THINKING"] = "1"
    for name in (
        "LIVEDUB_QUICK_QA_THINKING",
        "LIVEDUB_LONG_QA_THINKING",
        "LIVEDUB_QA_VERIFY_THINKING",
        "LIVEDUB_INFO_THINKING",
    ):
        os.environ[name] = "high"

    # ASR quality follows the user's maximum-quality requirement. Smaller
    # Whisper models remain an explicit non-production/experimental choice.
    for name in (
        "WHISPER_MODEL",
        "WHISPER_ENG_SUBTITLES_MODEL",
        "SHORTS_FACTORY_WHISPER_MODEL",
    ):
        os.environ[name] = _REQUIRED_WHISPER_MODEL

    return (
        f"heavy={_HEAVY_MODEL}/high; "
        f"light={_LIGHT_MODEL}->{_LIGHT_FALLBACK_MODEL}; "
        "heavy_model_fallbacks=none; "
        f"whisper={_REQUIRED_WHISPER_MODEL}"
    )


def _force_high_argument(
    function: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    """Replace the fourth argument (thinking_level) without duplicate kwargs."""
    positional = list(args)
    options = dict(kwargs)
    if len(positional) > 3:
        positional[3] = "high"
        options.pop("thinking_level", None)
    else:
        options["thinking_level"] = "high"
    return function(*positional, **options)


def _replace_loaded_references(old: Any, new: Any) -> None:
    """Update modules that imported a config helper before this runtime hook."""
    for module in tuple(sys.modules.values()):
        if module is None:
            continue
        try:
            namespace = vars(module)
        except Exception:
            continue
        for name, value in tuple(namespace.items()):
            if value is old:
                try:
                    namespace[name] = new
                except Exception:
                    pass


def install_max_quality_runtime() -> None:
    """Force high reasoning on shared helpers used by heavy semantic work."""
    global _INSTALLED
    if _INSTALLED:
        return

    import core.globals as globals_module

    original_text_smart = globals_module.make_text_config_smart
    original_audio = globals_module.make_audio_config
    original_text_legacy = globals_module.make_text_config

    def max_text_smart(*args, **kwargs):
        return _force_high_argument(original_text_smart, args, kwargs)

    def max_audio(*args, **kwargs):
        return _force_high_argument(original_audio, args, kwargs)

    def max_text_legacy(
        temperature: float = 0.2,
        max_output_tokens: int = 14000,
    ):
        # Legacy semantic helper is not a light-work selector; keep it on the
        # production heavy model and high reasoning.
        return max_text_smart(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            model_name=_HEAVY_MODEL,
            thinking_level="high",
        )

    max_text_smart._mp3bot_max_quality = True  # type: ignore[attr-defined]
    max_audio._mp3bot_max_quality = True  # type: ignore[attr-defined]
    max_text_legacy._mp3bot_max_quality = True  # type: ignore[attr-defined]

    globals_module.make_text_config_smart = max_text_smart
    globals_module.make_audio_config = max_audio
    globals_module.make_text_config = max_text_legacy

    _replace_loaded_references(original_text_smart, max_text_smart)
    _replace_loaded_references(original_audio, max_audio)
    _replace_loaded_references(original_text_legacy, max_text_legacy)

    _INSTALLED = True
    logger.info(
        "🧠 Gemini quality split: ✅ heavy=3.6/high; light=3.5-Lite→3.5; "
        "no 3.1/2.x; Whisper large-v3"
    )
