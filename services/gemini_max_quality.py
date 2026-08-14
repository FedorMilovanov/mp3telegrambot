#!/usr/bin/env python3
"""Enforce the production maximum-quality Gemini and ASR policy.

Quality-sensitive semantic work uses Gemini 3.7 Flash with high thinking. Gemini
3.6 Flash/high is the only semantic fallback. The 3.5 family is reserved for
explicitly mechanical utility work and never enters Factory selection, semantic
LiveDub QA or full-sermon translation review. Whisper remains large-v3.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any, Callable

logger = logging.getLogger(__name__)
_INSTALLED = False
_HEAVY_MODEL = "gemini-3.7-flash"
_HEAVY_FALLBACK_MODEL = "gemini-3.6-flash"
_LIGHT_MODEL = "gemini-3.5-flash-lite"
_LIGHT_FALLBACK_MODEL = "gemini-3.5-flash"
_REQUIRED_WHISPER_MODEL = "large-v3"


def configure_max_quality_env() -> str:
    """Apply the quality-first policy before ``core.globals`` creates clients."""
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

    # Quality-sensitive fallbacks must preserve high-thinking semantics. 3.5/Lite
    # is intentionally absent from every semantic fallback list.
    os.environ["GEMINI_QUALITY_FALLBACK_MODELS"] = _HEAVY_FALLBACK_MODEL
    os.environ["LIVEDUB_INFO_FALLBACK_MODELS"] = _HEAVY_FALLBACK_MODEL
    os.environ["SHORTS_FACTORY_FALLBACK_MODELS"] = _HEAVY_FALLBACK_MODEL

    # Explicit utility route only. Callers that affect user-visible meaning must
    # select the heavy route rather than silently spending this quota.
    os.environ["GEMINI_LIGHT_MODEL"] = _LIGHT_MODEL
    os.environ["GEMINI_LIGHT_FALLBACK_MODELS"] = _LIGHT_FALLBACK_MODEL
    os.environ["GEMINI_LIGHT_ALLOW_MAIN_FALLBACK"] = "0"

    # Heavy Gemini reasoning. Schema output is not allowed to disable thinking.
    os.environ["GEMINI_FORCE_THINKING_LEVEL"] = "high"
    os.environ["GEMINI_SCHEMA_THINKING"] = "1"
    for name in (
        "LIVEDUB_QUICK_QA_THINKING",
        "LIVEDUB_LONG_QA_THINKING",
        "LIVEDUB_QA_VERIFY_THINKING",
        "LIVEDUB_INFO_THINKING",
    ):
        os.environ[name] = "high"

    # Maximum ASR accuracy in production.
    for name in (
        "WHISPER_MODEL",
        "WHISPER_ENG_SUBTITLES_MODEL",
        "SHORTS_FACTORY_WHISPER_MODEL",
    ):
        os.environ[name] = _REQUIRED_WHISPER_MODEL

    return (
        f"semantic={_HEAVY_MODEL}/high->{_HEAVY_FALLBACK_MODEL}/high; "
        f"utility={_LIGHT_MODEL}->{_LIGHT_FALLBACK_MODEL}; "
        "semantic_3.5_downgrade=disabled; "
        f"whisper={_REQUIRED_WHISPER_MODEL}"
    )


def _model_argument(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Resolve make_*_config's model_name argument without changing the call."""
    if len(args) > 2 and args[2]:
        return str(args[2]).strip()
    configured = kwargs.get("model_name")
    if configured:
        return str(configured).strip()
    return os.getenv("GEMINI_MODEL", _HEAVY_MODEL).strip() or _HEAVY_MODEL


def _apply_thinking_policy(
    function: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    """Force high on 3.7/3.6 semantic work and minimal only on explicit utility."""
    positional = list(args)
    options = dict(kwargs)
    model = _model_argument(args, kwargs)
    if model in {_HEAVY_MODEL, _HEAVY_FALLBACK_MODEL}:
        level = "high"
    elif model in {_LIGHT_MODEL, _LIGHT_FALLBACK_MODEL}:
        level = "minimal"
    else:
        return function(*args, **kwargs)

    if len(positional) > 3:
        positional[3] = level
        options.pop("thinking_level", None)
    else:
        options["thinking_level"] = level
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
    """Install model-aware thinking and legacy 3.7 compatibility seams."""
    global _INSTALLED
    if _INSTALLED:
        return

    import core.globals as globals_module

    original_text_smart = globals_module.make_text_config_smart
    original_audio = globals_module.make_audio_config
    original_text_legacy = globals_module.make_text_config

    def max_text_smart(*args, **kwargs):
        return _apply_thinking_policy(original_text_smart, args, kwargs)

    def max_audio(*args, **kwargs):
        return _apply_thinking_policy(original_audio, args, kwargs)

    def max_text_legacy(
        temperature: float = 0.2,
        max_output_tokens: int = 14000,
    ):
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

    # Older Factory/editorial modules encoded a 3.6-only allow-list. Patch those
    # seams after core globals exist; the installer is idempotent and is also
    # called explicitly by the validated entrypoint before post-main Factory setup.
    from services.gemini37_quality_routes import install_gemini37_quality_routes

    install_gemini37_quality_routes()

    _INSTALLED = True
    logger.info(
        "🧠 Gemini quality route: ✅ 3.7/high → 3.6/high; "
        "3.5/Lite semantic downgrade disabled; Whisper large-v3"
    )
