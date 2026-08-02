#!/usr/bin/env python3
"""Structured Telegraph edit transport with explicit retry classification.

The legacy boolean edit helper hides the reason for failure.  Publication code
then cannot distinguish a transient network error from deterministic
``CONTENT_TOO_BIG`` and wastes 3/6/12-second retries.  This transport preserves
the exact API error and classifies whether another identical request can help.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urlparse

import requests

from core.globals import TELEGRAPH_TOKEN


_FLOOD_WAIT_RE = re.compile(r"^FLOOD_WAIT_(\d+)$")
_NON_RETRYABLE_ERRORS = {
    "CONTENT_TOO_BIG",
    "PAGE_NOT_FOUND",
    "ACCESS_TOKEN_INVALID",
    "ACCESS_TOKEN_REQUIRED",
    "TITLE_REQUIRED",
    "CONTENT_REQUIRED",
}


@dataclass(frozen=True)
class TelegraphEditResult:
    ok: bool
    error: str = ""
    retryable: bool = False
    retry_after_seconds: int = 0
    status_code: int | None = None


def telegraph_page_path(page_url: str) -> str:
    """Extract a safe Telegraph page path from an absolute URL or raw path."""
    raw = str(page_url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    path = parsed.path if parsed.scheme or parsed.netloc else raw
    return path.strip().lstrip("/")


def classify_telegraph_edit_error(
    error: object,
    *,
    status_code: int | None = None,
) -> TelegraphEditResult:
    text = str(error or "unknown error").strip() or "unknown error"
    flood = _FLOOD_WAIT_RE.match(text)
    if flood:
        wait = max(1, min(int(flood.group(1)), 30))
        return TelegraphEditResult(
            ok=False,
            error=text,
            retryable=True,
            retry_after_seconds=wait,
            status_code=status_code,
        )
    if text in _NON_RETRYABLE_ERRORS or text.startswith("page_audit_strict"):
        return TelegraphEditResult(
            ok=False,
            error=text,
            retryable=False,
            status_code=status_code,
        )
    retryable = status_code in {408, 425, 429} or bool(status_code and status_code >= 500)
    return TelegraphEditResult(
        ok=False,
        error=text,
        retryable=retryable,
        status_code=status_code,
    )


async def edit_telegraph_page_once(
    page_url: str,
    title: str,
    author: str,
    nodes: list,
    loop,
    *,
    author_url: str = "",
    token: str | None = None,
    post: Callable[..., Any] = requests.post,
) -> TelegraphEditResult:
    """Perform one editPage request and preserve its exact outcome.

    Node cleaning/auditing remains the caller's responsibility.  The function
    is intentionally one-shot: retry policy belongs to the publication layer,
    which knows whether it can rebuild a smaller payload.
    """
    access_token = str(token if token is not None else TELEGRAPH_TOKEN or "").strip()
    if not access_token:
        return TelegraphEditResult(ok=False, error="no_token", retryable=False)
    path = telegraph_page_path(page_url)
    if not path:
        return TelegraphEditResult(ok=False, error="invalid_page_path", retryable=False)

    try:
        response = await loop.run_in_executor(
            None,
            lambda: post(
                f"https://api.telegra.ph/editPage/{path}",
                json={
                    "access_token": access_token,
                    "title": str(title or "")[:256],
                    "author_name": str(author or "")[:128],
                    "author_url": str(author_url or "")[:512],
                    "content": nodes,
                    "return_content": False,
                },
                timeout=30,
            ),
        )
        status_code = getattr(response, "status_code", None)
        data = response.json()
        if data.get("ok"):
            return TelegraphEditResult(ok=True, status_code=status_code)
        return classify_telegraph_edit_error(
            data.get("error", "unknown error"),
            status_code=status_code,
        )
    except (requests.Timeout, requests.ConnectionError, OSError) as exc:
        return TelegraphEditResult(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            retryable=True,
        )
    except Exception as exc:
        return TelegraphEditResult(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            retryable=False,
        )


def telegraph_retry_delay(result: TelegraphEditResult, attempt_index: int) -> int:
    """Return the exact wait for one transient retry, or zero if none is valid."""
    if not result.retryable:
        return 0
    return result.retry_after_seconds or min(3 * (2 ** max(0, attempt_index)), 12)


async def run_telegraph_edit_with_retry(
    operation: Callable[[], Awaitable[TelegraphEditResult]],
    *,
    max_attempts: int = 3,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> TelegraphEditResult:
    """Retry only transient outcomes; deterministic failures return immediately."""
    attempts = max(1, int(max_attempts))
    result = TelegraphEditResult(ok=False, error="not_attempted", retryable=False)
    for attempt_index in range(attempts):
        result = await operation()
        if result.ok or not result.retryable or attempt_index >= attempts - 1:
            return result
        await sleep(telegraph_retry_delay(result, attempt_index))
    return result


async def wait_before_telegraph_retry(
    result: TelegraphEditResult,
    attempt_index: int,
) -> None:
    """Backward-compatible one-step wait for callers that own their loop."""
    delay = telegraph_retry_delay(result, attempt_index)
    if delay:
        await asyncio.sleep(delay)
