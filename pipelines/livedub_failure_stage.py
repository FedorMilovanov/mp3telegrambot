"""ENG Full user notification when the promised LiveDub result is unavailable."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


async def send_full_livedub_failure_notice(
    *,
    bot: Any,
    chat_id: Any,
    reply_to_message_id: Any,
    reason: str,
    flush_source_audio: Callable[[], Awaitable[Any]],
    escape: Callable[[str], str],
) -> bool:
    """Flush deferred source audio, then send the historical failure notice."""
    await flush_source_audio()
    reason = str(reason or "").strip()
    if not reason:
        reason = "Яндекс/VOT не отдал живой дубляж для этого ролика."
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ <b>Живой перевод Яндекса не получился</b>\n"
                f"{escape(reason)}\n\n"
                "Анализ/конспект я всё равно отправил, но видео с дубляжом "
                "и проверка точности перевода не запускались — проверять нечего."
            ),
            parse_mode="HTML",
            reply_to_message_id=reply_to_message_id,
        )
        return True
    except Exception as exc:
        logger.info("[LiveDub] failure notice not sent: %s", str(exc)[:160])
        return False
