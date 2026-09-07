#!/usr/bin/env python3
"""Pure Factory Gemini capacity/retry policy.

No installer, ContextVar or runtime rebinding lives here. Request-local progress
is passed explicitly by the Factory owner.
"""
from __future__ import annotations

import asyncio
import math
import os
from typing import Any, Awaitable

FACTORY_HTTP_TIMEOUT_MS = 900_000


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, min(value, maximum))


def retry_cache_ttl_seconds() -> float:
    return _env_float("SHORTS_FACTORY_RETRY_CACHE_HOURS", 6.0, 1.0, 24.0) * 3600.0


def heartbeat_seconds() -> float:
    return _env_float("SHORTS_FACTORY_PROGRESS_HEARTBEAT_SEC", 45.0, 15.0, 120.0)


async def safe_status(status_msg: Any, text: str) -> None:
    if status_msg is None:
        return
    try:
        await status_msg.edit_text(str(text)[:4000])
    except Exception:
        pass


async def await_with_heartbeat(
    awaitable: Awaitable[Any],
    *,
    label: str,
    status_msg: Any = None,
    heartbeat: float | None = None,
) -> Any:
    task = asyncio.ensure_future(awaitable)
    loop = asyncio.get_running_loop()
    started = loop.time()
    interval = heartbeat or heartbeat_seconds()
    while True:
        done, _pending = await asyncio.wait({task}, timeout=interval)
        if task in done:
            return await task
        elapsed = int(loop.time() - started)
        await safe_status(
            status_msg,
            f"{label}\n⏱ Работа продолжается: {elapsed // 60} мин {elapsed % 60:02d} сек",
        )


def _factory_api_keys() -> list[str]:
    from core import globals as core_globals

    keys = [
        str(getattr(core_globals, "GEMINI_API_KEY", "") or "").strip(),
        str(getattr(core_globals, "GEMINI_API_KEY_2", "") or "").strip(),
        str(getattr(core_globals, "GEMINI_API_KEY_3", "") or "").strip(),
        str(getattr(core_globals, "GEMINI_API_KEY_4", "") or "").strip(),
    ]
    return list(dict.fromkeys(key for key in keys if key))


def factory_gemini_clients() -> list[Any]:
    """Create Factory clients with a 900s request timeout and SDK retries off."""
    from core import globals as core_globals

    if (
        not core_globals.HAS_GEMINI
        or core_globals.genai is None
        or core_globals.types is None
    ):
        return []
    options = core_globals.types.HttpOptions(
        timeout=FACTORY_HTTP_TIMEOUT_MS,
        retry_options=core_globals.types.HttpRetryOptions(attempts=1),
    )
    return [
        core_globals.genai.Client(api_key=key, http_options=options)
        for key in _factory_api_keys()
    ]


def _exception_status_code(exc: BaseException) -> int | None:
    for name in ("code", "status_code", "status"):
        try:
            value = int(getattr(exc, name, None))
        except (TypeError, ValueError):
            continue
        if 100 <= value <= 599:
            return value
    text = str(exc or "").casefold()
    for code in (429, 500, 502, 503, 504):
        if str(code) in text:
            return code
    return None


def factory_quota_error(exc: BaseException) -> bool:
    """Return True only for quota/client-domain exhaustion, not backend 503."""
    status_code = _exception_status_code(exc)
    if status_code == 429:
        return True
    if status_code is not None:
        return False
    text = str(exc or "").casefold().replace("_", " ")
    return any(
        marker in text
        for marker in (
            "resource exhausted",
            "quota exceeded",
            "quota exhausted",
            "rate limit exceeded",
        )
    )


def factory_retryable_service_error(exc: BaseException) -> bool:
    if _exception_status_code(exc) in {429, 500, 502, 503, 504}:
        return True
    text = str(exc or "").casefold()
    return any(
        marker in text
        for marker in (
            "unavailable",
            "high demand",
            "resource exhausted",
            "temporarily unavailable",
        )
    )


def factory_overload_error(exc: BaseException) -> bool:
    text = str(exc or "").casefold()
    return _exception_status_code(exc) == 503 or "high demand" in text


__all__ = [
    "FACTORY_HTTP_TIMEOUT_MS",
    "await_with_heartbeat",
    "factory_gemini_clients",
    "factory_overload_error",
    "factory_quota_error",
    "factory_retryable_service_error",
    "heartbeat_seconds",
    "retry_cache_ttl_seconds",
    "safe_status",
]
