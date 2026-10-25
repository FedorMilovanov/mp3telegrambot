"""Telegram LiveDub video upload with the historical cover retry policy."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


async def send_livedub_video_with_cover(
    *,
    bot: Any,
    chat_id: Any,
    cover: Path | None,
    send_kwargs: dict[str, Any],
) -> Any:
    """Send upload action and video, retrying without cover only when safe."""
    try:
        await bot.send_chat_action(chat_id=chat_id, action="upload_video")
    except Exception:
        pass
    try:
        if cover is not None:
            return await bot.send_video(cover=cover, **send_kwargs)
        return await bot.send_video(**send_kwargs)
    except Exception as cover_error:
        if cover is None:
            raise
        # AUDIT R21: removing cover cannot fix an oversized payload. Retry
        # only for errors that can plausibly come from an older Bot API server.
        error_text = str(cover_error).lower()
        if "entity too large" in error_text or "too large" in error_text:
            raise
        logger.info(f"[LiveDub] cover не принят ({str(cover_error)[:100]}) — отправка без cover")
        return await bot.send_video(**send_kwargs)
