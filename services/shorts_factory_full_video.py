#!/usr/bin/env python3
"""Optional delivery of the full Yandex LiveDub sermon from Shorts Factory MAX."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from services.shorts_factory_delivery_ledger import (
    FactoryDeliveryJournal,
    FactoryDeliveryReconciliationRequired,
    factory_delivery_media_sha256,
)

logger = logging.getLogger(__name__)


async def factory_full_video_requested(update: Any) -> bool:
    """Read the current user's persistent full-video switch."""
    user = getattr(update, "effective_user", None)
    user_id = int(getattr(user, "id", 0) or 0)
    if not user_id:
        return False

    from core.user_modes import get_shorts_factory_full_video

    return await get_shorts_factory_full_video(user_id)


def _file_has_content(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


async def send_factory_full_translation_if_enabled(
    update: Any,
    video_path: Path,
    *,
    title: str,
    # Длительность приходит из ffprobe (float); внутри приводится к int.
    duration: int | float,
    translation_required: bool,
    silent_errors: bool = False,
    delivery_journal: FactoryDeliveryJournal | None = None,
) -> bool:
    """Send the already prepared full LiveDub file once; never retranslate it."""
    if not translation_required or not await factory_full_video_requested(update):
        return False

    message = getattr(update, "effective_message", None) or getattr(update, "message", None)
    path = Path(video_path)
    if message is None or not await asyncio.to_thread(_file_has_content, path):
        logger.warning("Factory full-video delivery skipped: missing message or file %s", path)
        return False

    clean_title = " ".join(str(title or "Полная проповедь").split())[:700]
    caption = f"🎬 Полная проповедь — русский видео-перевод\n{clean_title}"
    try:

        async def _send_full() -> Any:
            with await asyncio.to_thread(path.open, "rb") as handle:
                return await message.reply_video(
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

        if delivery_journal is not None:
            media_digest = await factory_delivery_media_sha256(path)
            await delivery_journal.send_once(
                "full:translated",
                semantic={
                    "kind": "full-translation",
                    "title": clean_title,
                    "duration": round(float(duration), 3),
                    "media_sha256": media_digest,
                },
                sender=_send_full,
            )
        else:
            await _send_full()
        return True
    except (asyncio.CancelledError, FactoryDeliveryReconciliationRequired):
        raise
    except Exception as exc:
        logger.exception("Factory full translated sermon delivery failed: %s", exc)
        if not silent_errors:
            with contextlib.suppress(Exception):
                await message.reply_text(
                    f"⚠️ Нарезки готовы, но полный видео-перевод не отправился. Причина: {str(exc)[:300]}"
                )
        return False


__all__ = [
    "factory_full_video_requested",
    "send_factory_full_translation_if_enabled",
]
