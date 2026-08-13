#!/usr/bin/env python3
"""Optional delivery of the full Yandex LiveDub sermon from Shorts Factory MAX."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


async def factory_full_video_requested(update: Any) -> bool:
    """Read the current user's persistent full-video switch."""
    user = getattr(update, "effective_user", None)
    user_id = int(getattr(user, "id", 0) or 0)
    if not user_id:
        return False

    from handlers.mode_command import get_shorts_factory_full_video

    return await get_shorts_factory_full_video(user_id)


async def send_factory_full_translation_if_enabled(
    update: Any,
    video_path: Path,
    *,
    title: str,
    duration: int,
    translation_required: bool,
    silent_errors: bool = False,
) -> bool:
    """Send the already prepared full LiveDub file once; never retranslate it."""
    if not translation_required or not await factory_full_video_requested(update):
        return False

    message = getattr(update, "effective_message", None) or getattr(update, "message", None)
    path = Path(video_path)
    if message is None or not path.is_file() or path.stat().st_size <= 0:
        logger.warning("Factory full-video delivery skipped: missing message or file %s", path)
        return False

    clean_title = " ".join(str(title or "Полная проповедь").split())[:700]
    caption = f"🎬 Полная проповедь — русский видео-перевод\n{clean_title}"
    try:
        with path.open("rb") as handle:
            await message.reply_video(
                video=handle,
                filename=path.name,
                caption=caption,
                duration=max(1, int(duration)),
                supports_streaming=True,
                write_timeout=1800,
                read_timeout=1800,
                connect_timeout=120,
                pool_timeout=120,
            )
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Factory full translated sermon delivery failed: %s", exc)
        if not silent_errors:
            try:
                await message.reply_text(
                    "⚠️ Нарезки готовы, но полный видео-перевод не отправился. "
                    f"Причина: {str(exc)[:300]}"
                )
            except Exception:
                pass
        return False


__all__ = [
    "factory_full_video_requested",
    "send_factory_full_translation_if_enabled",
]
