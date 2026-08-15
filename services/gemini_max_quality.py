#!/usr/bin/env python3
"""Enforce the production quality/cost split for Gemini and ASR.

User-visible and semantic work stays on Gemini 3.6 Flash with high thinking.
Only explicitly mechanical/high-volume utility work may use Gemini 3.5
Flash-Lite, with Gemini 3.5 Flash as its same-generation utility fallback.
Production never falls back to 3.1/2.x for semantic output.
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
    """Apply the quality policy before ``core.globals`` creates clients."""
    # User-facing semantic work: never downgrade the model family.
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

    # Utility lane only: deterministic extraction/routing/formatting that does
    # not become user-visible semantic copy may spend the separate 3.5 quota.
    os.environ["GEMINI_LIGHT_MODEL"] = _LIGHT_MODEL
    os.environ["GEMINI_LIGHT_FALLBACK_MODELS"] = _LIGHT_FALLBACK_MODEL
    os.environ["GEMINI_LIGHT_ALLOW_MAIN_FALLBACK"] = "0"

    # Publication title/author/description is user-visible semantic output and
    # must not silently enter the 3.5 utility lane.
    os.environ["LIVEDUB_PUBLICATION_FALLBACK_MODELS"] = ""
    os.environ["LIVEDUB_PUBLICATION_ALLOW_STRONG_FALLBACK"] = "0"

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

    # ASR quality follows the maximum-quality requirement. Smaller Whisper
    # models remain an explicit non-production/experimental choice.
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
    """Use high on semantic 3.6 and minimal on explicitly utility 3.5 work."""
    positional = list(args)
    options = dict(kwargs)
    model = _model_argument(args, kwargs)
    if model == _HEAVY_MODEL:
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


def install_max_quality_runtime() -> str:
    """Compatibility validator; quality is enforced by config owners/pre-main env."""
    import core.globals as globals_module

    if not callable(globals_module.make_text_config_smart):
        raise RuntimeError("Gemini smart config owner is unavailable")
    return "source-owned Gemini thinking policy; no post-import reference replacement"
