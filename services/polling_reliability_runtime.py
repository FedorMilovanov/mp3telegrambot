#!/usr/bin/env python3
"""Pending-update reliability policy used directly by the bot Application owner.

Recent Telegram commands survive process restarts, stale backlog is rejected before
normal handlers run, and polling transport failures are logged explicitly.  This
module does not replace PTB class methods.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, MutableMapping
from urllib.parse import urlsplit

from telegram.request import BaseRequest, HTTPXRequest, RequestData

from core import runtime_status

logger = logging.getLogger(__name__)


class PollingHeartbeatHTTPXRequest(HTTPXRequest):
    """HTTPX transport that witnesses successful empty and non-empty polls."""

    async def do_request(
        self,
        url: str,
        method: str,
        request_data: RequestData | None = None,
        read_timeout: Any = BaseRequest.DEFAULT_NONE,
        write_timeout: Any = BaseRequest.DEFAULT_NONE,
        connect_timeout: Any = BaseRequest.DEFAULT_NONE,
        pool_timeout: Any = BaseRequest.DEFAULT_NONE,
    ) -> tuple[int, bytes]:
        status, payload = await super().do_request(
            url,
            method,
            request_data,
            read_timeout,
            write_timeout,
            connect_timeout,
            pool_timeout,
        )
        is_get_updates = urlsplit(url).path.rstrip("/").endswith("/getUpdates")
        response_valid = False
        if status == 200 and is_get_updates:
            try:
                response = json.loads(payload)
            except ValueError:
                response = None
            result = response.get("result") if isinstance(response, dict) else None
            response_valid = (
                isinstance(response, dict)
                and response.get("ok") is True
                and isinstance(result, list)
                and all(isinstance(item, dict) for item in result)
            )
        if response_valid:
            runtime_status.record_polling_heartbeat()
        return status, payload


def _bounded_env_seconds(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None or not raw.strip() else int(raw.strip())
    except (TypeError, ValueError, OverflowError):
        value = default
    return max(minimum, min(value, maximum))


def _max_stale_noncommand_age() -> int:
    return _bounded_env_seconds("BOT_PENDING_NONCOMMAND_MAX_AGE_SEC", default=900, minimum=60, maximum=24 * 3600)


def _max_stale_command_age() -> int:
    return _bounded_env_seconds("BOT_PENDING_COMMAND_MAX_AGE_SEC", default=6 * 3600, minimum=5 * 60, maximum=24 * 3600)


def _message_age_seconds(update: Any) -> float | None:
    message = getattr(update, "effective_message", None)
    stamp = getattr(message, "date", None)
    if not isinstance(stamp, datetime):
        return None
    try:
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds())
    except Exception:
        return None


def _is_command(update: Any) -> bool:
    message = getattr(update, "effective_message", None)
    text = str(getattr(message, "text", "") or "").lstrip()
    return text.startswith("/")


def _stale_pending_reason(update: Any) -> tuple[str | None, float | None]:
    age = _message_age_seconds(update)
    if age is None:
        return None, None
    if _is_command(update):
        if age > _max_stale_command_age():
            return "stale-command", age
        return None, age
    if age > _max_stale_noncommand_age():
        return "stale-noncommand", age
    return None, age


def accept_pending_update(update: Any, bot_data: MutableMapping[str, Any]) -> bool:
    """Record a live update or reject stale backlog before normal handlers run."""
    stale_reason, age = _stale_pending_reason(update)
    command = _is_command(update)
    if stale_reason is not None:
        logger.warning(
            "🧹 Pending Telegram update dropped: type=%s update_id=%s age=%.0fs",
            stale_reason,
            getattr(update, "update_id", "?"),
            age or 0.0,
        )
        return False

    bot_data["telegram_last_update_monotonic"] = time.monotonic()
    bot_data["telegram_last_update_id"] = getattr(update, "update_id", None)
    if command:
        message = getattr(update, "effective_message", None)
        text = str(getattr(message, "text", "") or "").split(maxsplit=1)[0]
        user = getattr(update, "effective_user", None)
        logger.info(
            "📥 Telegram command received: %s user=%s update_id=%s age=%.1fs",
            text[:80],
            getattr(user, "id", "?"),
            getattr(update, "update_id", "?"),
            age or 0.0,
        )
    return True


def polling_error_callback(error: BaseException) -> None:
    """PTB ``start_polling`` error callback with full traceback evidence."""
    logger.error(
        "Telegram getUpdates error: %s: %s",
        type(error).__name__,
        error,
        exc_info=(type(error), error, error.__traceback__),
    )


__all__ = [
    "PollingHeartbeatHTTPXRequest",
    "accept_pending_update",
    "polling_error_callback",
]
