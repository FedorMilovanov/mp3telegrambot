#!/usr/bin/env python3
"""Keep recent Telegram commands across restarts and expose polling failures.

The legacy startup passed ``drop_pending_updates=True`` to PTB. A command sent
while the Windows bot was restarting was therefore deleted just before polling
started, which looked exactly like a healthy bot ignoring ``/mode``. This
runtime keeps recent pending updates, drops stale non-command backlog quickly,
and also prevents very old commands from executing unexpectedly after a long
downtime.
"""
from __future__ import annotations

import functools
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_INSTALLED = False


def _bounded_env_seconds(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None or not raw.strip() else int(raw.strip())
    except (TypeError, ValueError, OverflowError):
        value = default
    return max(minimum, min(value, maximum))


def _max_stale_noncommand_age() -> int:
    return _bounded_env_seconds(
        "BOT_PENDING_NONCOMMAND_MAX_AGE_SEC",
        default=900,
        minimum=60,
        maximum=24 * 3600,
    )


def _max_stale_command_age() -> int:
    return _bounded_env_seconds(
        "BOT_PENDING_COMMAND_MAX_AGE_SEC",
        default=6 * 3600,
        minimum=5 * 60,
        maximum=24 * 3600,
    )


def _message_age_seconds(update: Any) -> float | None:
    message = getattr(update, "effective_message", None)
    stamp = getattr(message, "date", None)
    if stamp is None:
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


def install_polling_reliability_runtime() -> None:
    """Patch PTB once before ``main.run_bot_async`` builds the Application."""
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return

        from telegram.ext import Application, Updater

        original_start_polling = Updater.start_polling
        original_process_update = Application.process_update

        if not getattr(original_start_polling, "_mp3bot_reliable_polling", False):

            @functools.wraps(original_start_polling)
            async def reliable_start_polling(self: Any, *args: Any, **kwargs: Any):
                requested_drop = kwargs.get("drop_pending_updates")
                kwargs["drop_pending_updates"] = False
                previous_error_callback = kwargs.get("error_callback")

                def polling_error_callback(error: BaseException) -> None:
                    logger.error(
                        "Telegram getUpdates error: %s: %s",
                        type(error).__name__,
                        error,
                        exc_info=(type(error), error, error.__traceback__),
                    )
                    if previous_error_callback is not None:
                        try:
                            previous_error_callback(error)
                        except Exception:
                            logger.exception("User polling error_callback failed")

                kwargs["error_callback"] = polling_error_callback
                logger.info(
                    "📡 Polling reliability: recent pending commands preserved "
                    "(drop_pending_updates %r → False; command max age=%ss)",
                    requested_drop,
                    _max_stale_command_age(),
                )
                return await original_start_polling(self, *args, **kwargs)

            reliable_start_polling._mp3bot_reliable_polling = True  # type: ignore[attr-defined]
            Updater.start_polling = reliable_start_polling

        if not getattr(original_process_update, "_mp3bot_update_probe", False):

            @functools.wraps(original_process_update)
            async def process_update_with_probe(self: Any, update: Any) -> None:
                stale_reason, age = _stale_pending_reason(update)
                command = _is_command(update)
                if stale_reason is not None:
                    logger.warning(
                        "🧹 Pending Telegram update dropped: type=%s update_id=%s age=%.0fs",
                        stale_reason,
                        getattr(update, "update_id", "?"),
                        age or 0.0,
                    )
                    return

                self.bot_data["telegram_last_update_monotonic"] = time.monotonic()
                self.bot_data["telegram_last_update_id"] = getattr(update, "update_id", None)
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
                return await original_process_update(self, update)

            process_update_with_probe._mp3bot_update_probe = True  # type: ignore[attr-defined]
            Application.process_update = process_update_with_probe

        _INSTALLED = True
        logger.info(
            "📡 Polling reliability runtime: recent commands kept; stale backlog guarded"
        )


__all__ = ["install_polling_reliability_runtime"]
