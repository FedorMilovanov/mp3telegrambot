#!/usr/bin/env python3
"""Enforce one maximum-quality Gemini/ASR policy for production.

User-facing AI work must not silently downgrade to an older/lighter model. The
project therefore uses Gemini 3.6 Flash with high thinking everywhere, rotates
API keys rather than model families, and keeps faster-whisper large-v3 for ASR.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any, Callable

logger = logging.getLogger(__name__)
_INSTALLED = False
_PRIMARY_MODEL = "gemini-3.6-flash"
_REQUIRED_WHISPER_MODEL = "large-v3"


def configure_max_quality_env() -> str:
    """Apply fail-closed quality knobs before ``core.globals`` creates clients."""
    # One model family everywhere: availability comes from key rotation, not
    # from silently dropping user-visible work onto an older/Lite model.
    for name in (
        "GEMINI_MODEL",
        "GEMINI_LIGHT_MODEL",
        "GEMINI_MAX_MODEL",
        "LIVEDUB_INFO_MODEL",
        "LIVEDUB_QUICK_QA_MODEL",
        "LIVEDUB_LONG_QA_MODEL",
        "LIVEDUB_QA_VERIFY_MODEL",
        "SHORTS_FACTORY_MODEL",
    ):
        os.environ[name] = _PRIMARY_MODEL

    # Empty fallback lists are intentional. Existing selectors then rotate the
    # configured GEMINI_CLIENTS but have no weaker model to fall through to.
    for name in (
        "GEMINI_LIGHT_FALLBACK_MODELS",
        "LIVEDUB_INFO_FALLBACK_MODELS",
        "LIVEDUB_PUBLICATION_FALLBACK_MODELS",
    ):
        os.environ[name] = ""
    os.environ["GEMINI_LIGHT_ALLOW_MAIN_FALLBACK"] = "1"
    os.environ["LIVEDUB_PUBLICATION_ALLOW_STRONG_FALLBACK"] = "1"

    # Gemini 3.x native reasoning contract. No legacy thinking-budget route and
    # no schema-specific opt-out are allowed in the production quality profile.
    os.environ["GEMINI_FORCE_THINKING_LEVEL"] = "high"
    os.environ["GEMINI_SCHEMA_THINKING"] = "1"
    for name in (
        "LIVEDUB_QUICK_QA_THINKING",
        "LIVEDUB_LONG_QA_THINKING",
        "LIVEDUB_QA_VERIFY_THINKING",
        "LIVEDUB_INFO_THINKING",
    ):
        os.environ[name] = "high"

    # ASR quality follows the same rule. Smaller Whisper models remain possible
    # only in non-production experiments that bypass bot_new.py intentionally.
    for name in (
        "WHISPER_MODEL",
        "WHISPER_ENG_SUBTITLES_MODEL",
        "SHORTS_FACTORY_WHISPER_MODEL",
    ):
        os.environ[name] = _REQUIRED_WHISPER_MODEL

    return (
        f"model={_PRIMARY_MODEL}; thinking=high; fallbacks=none; "
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


def _install_publication_quality(globals_module) -> None:
    """Remove the historical Lite/minimal exception from publication cards."""
    try:
        import services.livedub_publication_core as publication
    except Exception as exc:
        logger.warning("Publication max-quality hook unavailable: %s", exc)
        return

    def publication_models() -> list[str]:
        return [_PRIMARY_MODEL]

    def publication_config(_model_name: str):
        types = globals_module.types
        if types is None:
            return None
        thinking = globals_module._build_thinking_config("high")
        kwargs: dict[str, Any] = {
            "max_output_tokens": 1400,
            "response_mime_type": "application/json",
            "response_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "author": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["title", "author", "description"],
            },
        }
        if thinking is not None:
            kwargs["thinking_config"] = thinking
        return types.GenerateContentConfig(**kwargs)

    publication.publication_models = publication_models
    publication._economy_config = publication_config


def install_max_quality_runtime() -> None:
    """Force high reasoning and remove the last user-facing Lite exception."""
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
        # Route the legacy helper through the model-aware 3.x path. For 3.6 the
        # legacy temperature argument is intentionally ignored.
        return max_text_smart(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            model_name=_PRIMARY_MODEL,
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
    _install_publication_quality(globals_module)

    _INSTALLED = True
    logger.info(
        "🧠 Gemini maximum quality: ✅ 3.6 Flash + high thinking everywhere; "
        "no weaker-model fallback; Whisper large-v3"
    )
