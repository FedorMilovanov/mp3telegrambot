"""Deliver or roll back a cached LiveDub video and its companion set."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from services import livedub_delivery_coordinator as coordinator

logger = logging.getLogger(__name__)


async def deliver_cached_livedub(
    bot: Any,
    *,
    chat_id: Any,
    reply_to: Any,
    video_file_id: str,
    caption: str,
    publication_card: dict[str, Any],
    media_id: str,
    flush_source_audio: Callable[[str], Awaitable[Any]],
    discard_source_audio: Callable[[str], None],
) -> None:
    """Send a cached pair atomically from the user's perspective."""
    cached_video_message = None
    try:
        cached_video_message = await bot.send_video(
            chat_id=chat_id,
            video=video_file_id,
            caption=caption,
            parse_mode="HTML",
            reply_to_message_id=reply_to,
            supports_streaming=True,
        )
        companions_ok = await coordinator.deliver_cached_companions(
            bot,
            chat_id=chat_id,
            video_file_id=video_file_id,
            publication_card=publication_card,
            reply_to=reply_to,
        )
        if not companions_ok:
            raise RuntimeError("cached LiveDub companion set is missing")
    except Exception as exc:
        if cached_video_message is not None:
            await coordinator.delete_message_best_effort(bot, chat_id, cached_video_message)
        logger.warning("[LiveDub] cached pair rejected: %s", exc)
        try:
            from core.database import adb_set_livedub_file_id

            await adb_set_livedub_file_id(media_id, "")
        except Exception:
            logger.debug("Failed to invalidate rejected cached LiveDub file_id", exc_info=True)
        await flush_source_audio("cached LiveDub pair unavailable")
        with contextlib.suppress(Exception):
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "⚠️ Кэшированный перевод или его MP3-комплект устарел. "
                    "Отправьте ссылку ещё раз — полный комплект будет создан заново."
                ),
                reply_to_message_id=reply_to,
            )
        return

    discard_source_audio("cached LiveDub pair delivered")
