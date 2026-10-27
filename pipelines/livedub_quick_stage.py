"""Final delivery, user notification, and cleanup for ENG Quick modes.

The actual LiveDub delivery remains owned by ``process_single_video`` because it
shares substantial state with the full pipeline.  This module owns only the
small post-delivery contract: status-message lifecycle, honest failure hints,
and returning the real delivery status to playlist accounting.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


async def finish_livedub_quick(
    *,
    update: Any,
    status_msg: Any,
    title: str,
    send_result: Callable[[], Awaitable[bool]],
    silent_errors: bool,
    workdir: Path | None,
    media_id: str,
    cleanup: Callable[[str], None],
) -> bool:
    """Finish ENG Quick without turning a failed delivery into success.

    ``send_result`` and ``cleanup`` are supplied by the caller so existing
    pipeline monkeypatch seams and the nested LiveDub delivery helper keep
    their historical behavior.  ``workdir=None`` represents the cache-hit path,
    where no LiveDub working directory was created.
    """
    if status_msg:
        with contextlib.suppress(Exception):
            await status_msg.edit_text(f"⚡ Режим ENG Quick: жду перевод «Живые голоса»...\n📝 {title}")

    delivered = await send_result()

    if status_msg:
        with contextlib.suppress(Exception):
            await status_msg.delete()

    if not delivered:
        # A missing OAuth token gets an actionable hint instead of the generic
        # "translation unavailable" message.  Marker inspection remains
        # best-effort, exactly as it was in the nested pipeline block.
        auth_required = False
        with contextlib.suppress(OSError):
            auth_required = workdir is not None and (workdir / ".auth_required").exists()

        # Playlists emit one aggregate failure summary instead of one message
        # per failed entry (silent_errors=True).
        try:
            if silent_errors:
                pass
            elif auth_required:
                await update.message.reply_text(
                    "❌ Яндекс теперь требует авторизацию для «Живых голосов».\n"
                    "🔑 Добавь VOT_API_TOKEN в .env (инструкция в .env.example):\n"
                    "1) открой https://rust-server-531j.onrender.com/v1/auth/handle\n"
                    "2) войди в аккаунт Яндекса\n"
                    "3) скопируй access_token из адресной строки после редиректа\n"
                    "4) VOT_API_TOKEN=<токен> (или YANDEX_OAUTH_TOKEN=<токен>) в .env и перезапусти бота."
                )
            else:
                await update.message.reply_text(
                    "❌ Перевод «Живые голоса» не получился для этого видео.\n"
                    "Возможные причины: перевод недоступен у Яндекса, vot-cli-live "
                    "не установлен, или видео слишком длинное.\n"
                    "Попробуйте режим 🇬🇧 ENG Full (/mode) — там будет хотя бы анализ."
                )
        except Exception:
            logger.debug("Failed to send LiveDub failure notification", exc_info=True)

    cleanup(media_id)
    logger.info("ENG Quick done: %s delivered=%s", media_id, delivered)
    # Playlist accounting must receive False when delivery failed; otherwise a
    # consumed rate-limit slot is incorrectly reported as a successful entry.
    return delivered
