#!/usr/bin/env python3
"""Keep Telegram commands across restarts and expose polling failures clearly.

The legacy startup passed ``drop_pending_updates=True`` to PTB.  A command sent
while the Windows bot was restarting was therefore deleted just before polling
started, which looked exactly like a healthy bot ignoring ``/mode``.  This
runtime keeps pending updates, drops only genuinely stale non-command messages,
and logs command receipt plus getUpdates transport errors.
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


def _max_stale_noncommand_age() -> int:
    try:
        value = int(os.getenv("BOT_PENDING_NONCOMMAND_MAX_AGE_SEC", "900").strip() or "900")
    except ValueError:
        value = 900
    return max(60, min(value, 24 * 3600))


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
                    "📡 Polling reliability: pending commands preserved "
                    "(drop_pending_updates %r → False)",
                    requested_drop,
                )
                return await original_start_polling(self, *args, **kwargs)

            reliable_start_polling._mp3bot_reliable_polling = True  # type: ignore[attr-defined]
            Updater.start_polling = reliable_start_polling

        if not getattr(original_process_update, "_mp3bot_update_probe", False):

            @functools.wraps(original_process_update)
            async def process_update_with_probe(self: Any, update: Any) -> None:
                age = _message_age_seconds(update)
                command = _is_command(update)
                if age is not None and age > _max_stale_noncommand_age() and not command:
                    logger.warning(
                        "🧹 Stale pending non-command update dropped: update_id=%s age=%.0fs",
                        getattr(update, "update_id", "?"),
                        age,
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
            "📡 Polling reliability runtime: pending commands kept; stale non-command backlog guarded"
        )


__all__ = ["install_polling_reliability_runtime"]
