#!/usr/bin/env python3
"""Replace stale model warnings in ``main.py`` with runtime-policy diagnostics.

The validated entry point configures Gemini 3.6 before importing ``main``.  The
legacy lifecycle file still owns an older hard-coded allow-list and can therefore
warn that the *correct* production model is unknown while recommending retired
models.  This adapter leaves lifecycle code untouched, filters only those exact
legacy records and logs one diagnostic derived from the policy actually installed
by :mod:`services.livedub_quality_runtime`.
"""
from __future__ import annotations

import functools
import logging
import threading
from types import ModuleType
from typing import Any

from services.livedub_quality_runtime import (
    _LIGHT_MODEL,
    _PRIMARY_MODEL,
    _RETIRED_MODELS,
    _STRONG_FALLBACK_MODEL,
)

_LOCK = threading.Lock()
_INSTALLED = False


class _LegacyGeminiModelFilter(logging.Filter):
    """Suppress only the obsolete model-policy records emitted by ``main``."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "main":
            return True
        template = str(record.msg or "")
        if "GEMINI_MODEL" not in template:
            return True
        stale_markers = (
            "не входит в список проверенных живых моделей",
            "устарела и скоро будет отключена",
            "с 1 апреля 2026 Pro-модели",
        )
        return not any(marker in template for marker in stale_markers)


def model_diagnostic(model: str) -> tuple[int, str]:
    """Return logging level and a truthful message for the effective main model."""
    effective = str(model or "").strip()
    if effective == _PRIMARY_MODEL:
        return (
            logging.INFO,
            f"🧠 Gemini startup policy: ✅ main={effective}; "
            f"fallback={_STRONG_FALLBACK_MODEL}; light={_LIGHT_MODEL}",
        )
    if effective in _RETIRED_MODELS:
        return (
            logging.ERROR,
            f"🧠 Gemini startup policy: ❌ main={effective} retired by project policy; "
            f"use {_PRIMARY_MODEL}",
        )
    if effective == _LIGHT_MODEL:
        return (
            logging.WARNING,
            f"🧠 Gemini startup policy: ⚠️ main={effective} is reserved for mechanical "
            f"tasks; quality analysis should use {_PRIMARY_MODEL}",
        )
    if effective == _STRONG_FALLBACK_MODEL:
        return (
            logging.WARNING,
            f"🧠 Gemini startup policy: ⚠️ main={effective} is the strong fallback; "
            f"the production primary is {_PRIMARY_MODEL}",
        )
    return (
        logging.WARNING,
        f"🧠 Gemini startup policy: ⚠️ main={effective or '<empty>'} is a custom or "
        f"unverified override; project primary={_PRIMARY_MODEL}",
    )


def _log_effective_model(logger: logging.Logger) -> None:
    try:
        from core.database import GEMINI_MODEL

        level, message = model_diagnostic(GEMINI_MODEL)
    except Exception as exc:
        level = logging.WARNING
        message = f"🧠 Gemini startup policy diagnostic unavailable: {exc}"
    logger.log(level, message)


def install_gemini_startup_diagnostics(main_module: ModuleType) -> None:
    """Install once before ``main.main()`` constructs and starts the application."""
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return

        logger = getattr(main_module, "logger", logging.getLogger("main"))
        if not any(isinstance(item, _LegacyGeminiModelFilter) for item in logger.filters):
            logger.addFilter(_LegacyGeminiModelFilter())

        current = getattr(main_module, "run_bot_async")
        if not getattr(current, "_mp3bot_gemini_startup_diagnostics", False):

            @functools.wraps(current)
            async def diagnosed(*args: Any, **kwargs: Any):
                _log_effective_model(logger)
                return await current(*args, **kwargs)

            diagnosed._mp3bot_gemini_startup_diagnostics = True  # type: ignore[attr-defined]
            main_module.run_bot_async = diagnosed

        _INSTALLED = True
        logger.info("🧠 Gemini startup diagnostics: runtime policy is the source of truth")
