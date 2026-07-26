#!/usr/bin/env python3
"""Repair empty or unsafe LiveDub publication error records at logging time.

Some Gemini/SDK exceptions stringify to an empty string. The publication pipeline
correctly continues through model/client fallbacks, but records such as
``model=... client=... failed:`` and ``title fallback:`` then lose the only useful
failure detail. Rewriting the generation pipeline only for diagnostics would add
risk, so this adapter installs a narrow filter on its logger:

* only known publication failure templates are touched;
* an empty final argument becomes an explicit, truthful placeholder;
* nonempty details pass through the project's credential masker;
* fallback behavior, exception handling and model selection remain unchanged.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

_LOCK = threading.Lock()
_INSTALLED = False
_EMPTY_DETAIL = "EmptyExceptionMessage: SDK exception contained no text"
_TARGET_TEMPLATES = (
    "[LiveDubPublication] model=%s client=%d failed: %s",
    "[LiveDubPublication] title fallback: %s",
)


def _safe_detail(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return _EMPTY_DETAIL
    try:
        from core.utils import mask_api_key

        text = mask_api_key(text)
    except Exception:
        # Keep the fallback deterministic and regex-free. The normal project
        # masker handles API-key formats and proxy credentials; this path still
        # removes every configured secret value when that import is unavailable.
        for name in (
            "BOT_TOKEN",
            "GEMINI_API_KEY",
            "GEMINI_API_KEY_2",
            "GEMINI_API_KEY_3",
            "GEMINI_API_KEY_4",
            "TELEGRAM_PROXY_URL",
            "HTTPS_PROXY",
            "HTTP_PROXY",
        ):
            secret = os.getenv(name, "").strip()
            if secret:
                text = text.replace(secret, f"***{name}***")
    return text[: max(48, int(limit))]


class _PublicationExceptionFilter(logging.Filter):
    """Normalize only the final detail argument of known publication failures."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "services.livedub_publication":
            return True
        template = str(record.msg or "")
        if template not in _TARGET_TEMPLATES:
            return True

        args = record.args
        if isinstance(args, tuple) and args:
            values = list(args)
            values[-1] = _safe_detail(values[-1])
            record.args = tuple(values)
        elif isinstance(args, dict):
            # The current templates are positional, but keep a safe fallback if
            # logging is refactored to mapping arguments later.
            values = dict(args)
            for key in ("error", "detail", "reason"):
                if key in values:
                    values[key] = _safe_detail(values[key])
                    break
            record.args = values
        else:
            record.msg = f"{template} {_EMPTY_DETAIL}"
            record.args = ()
        return True


def install_livedub_publication_error_diagnostics() -> None:
    """Install once before any user request can enter publication generation."""
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        logger = logging.getLogger("services.livedub_publication")
        if not any(isinstance(item, _PublicationExceptionFilter) for item in logger.filters):
            logger.addFilter(_PublicationExceptionFilter())
        _INSTALLED = True
        logging.getLogger(__name__).info(
            "🧯 LiveDub publication diagnostics: empty SDK errors and secrets guarded"
        )
