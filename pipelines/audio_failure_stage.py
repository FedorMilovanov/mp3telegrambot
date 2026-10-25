"""User-facing early return for audio that remains above Telegram's limit."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pipelines.audio_stage import AudioTooLarge

logger = logging.getLogger(__name__)


async def fail_audio_too_large(
    error: AudioTooLarge,
    update: Any,
    send_livedub: Callable[[], Awaitable[bool]],
    media_id: str,
    cleanup: Callable[[str], None],
    *,
    cache_hit: bool,
) -> bool:
    """Report the size, preserve a promised LiveDub, clean up, and fail.

    Fresh and cache-hit paths historically used different size messages; that
    distinction remains explicit in ``cache_hit``.  Reply and cleanup failures
    propagate as before, while a LiveDub delivery error is best-effort only.
    """
    message = (
        f"⚠️ Файл слишком большой ({error.size_mb:.1f} МБ) "
        "даже после сжатия."
        if cache_hit
        else str(error)
    )
    await update.message.reply_text(message)

    # LiveDub has an independent size check: an oversized MP3 must not discard
    # an already generated translation.  Its own failure must not replace the
    # audio early-return result.
    try:
        await send_livedub()
    except Exception as send_error:
        logger.warning("[LiveDub] send on early return failed: %s", send_error)

    cleanup(media_id)
    return False
