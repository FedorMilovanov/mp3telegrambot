#!/usr/bin/env python3
"""Enforce the owner's maximum-quality Gemini policy across the whole bot.

The project intentionally prefers answer quality over latency and token economy.
Every Gemini 3.x request therefore receives ``thinking_level=high``, including
Quick QA, LiveDub publication cards, title translation, and old helper calls
that still use ``make_text_config``.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any, Callable

logger = logging.getLogger(__name__)
_INSTALLED = False


def configure_max_quality_env() -> str:
    """Set quality knobs before ``core.globals`` creates Gemini clients."""
    os.environ["GEMINI_FORCE_THINKING_LEVEL"] = "high"
    os.environ["LIVEDUB_QUICK_QA_THINKING"] = "high"
    os.environ["LIVEDUB_LONG_QA_THINKING"] = "high"
    os.environ["LIVEDUB_INFO_THINKING"] = "high"
    return "thinking=high for all Gemini calls"


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
    """Force high reasoning even when an older call site asks for minimal/low."""
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
        # Legacy helper had no thinking_config at all. Route it through the
        # model-aware Gemini 3.x helper with maximum reasoning.
        return max_text_smart(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            model_name=os.getenv("GEMINI_MODEL", "gemini-3.6-flash"),
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
    logger.info("🧠 Gemini maximum quality: ✅ thinking_level=high for every active call")
