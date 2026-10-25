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


async def send_livedub_timeout_notice(
    *,
    bot: Any,
    chat_id: Any,
    reply_to_message_id: Any,
    flush_source_audio: Callable[[], Awaitable[Any]],
) -> None:
    """Flush deferred source audio and explain the historical 30-minute timeout."""
    await flush_source_audio()
    # wait_for отменяет задачу при таймауте — перевод уже не придёт.
    await bot.send_message(
        chat_id=chat_id,
        text=(
            "⏳ Перевод «Живые голоса» не успел за 30 минут и был отменён. Отправьте ссылку ещё раз — попробуем заново."
        ),
        reply_to_message_id=reply_to_message_id,
    )


async def notify_livedub_send_failure(
    *,
    bot: Any,
    chat_id: Any,
    reply_to_message_id: Any,
    error: Exception,
    max_file_size_mb: Callable[[], int],
    flush_source_audio: Callable[[], Awaitable[Any]],
    escape: Callable[[str], str],
) -> None:
    """Flush deferred audio and make a LiveDub send/generation error visible."""
    await flush_source_audio()
    logger.warning(f"[LiveDub] fail: {error}")
    # AUDIT R21: даже сгенерированный перевод нужно сопроводить внятной
    # ошибкой отправки, включая "Request Entity Too Large" из живого лога.
    try:
        error_text = str(error)
        size_related = "entity too large" in error_text.lower() or "too large" in error_text.lower()
        hint = (
            "\n💡 Подними локальный Bot API сервер (LOCAL_BOT_API_URL), чтобы отправлять до 2000 МБ."
            if size_related and max_file_size_mb() <= 50
            else ""
        )
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ <b>Не удалось отправить видео с переводом</b>\n"
                f"{escape(error_text[:300])}{hint}\n\n"
                "Перевод сгенерирован, но отправка не удалась."
            ),
            parse_mode="HTML",
            reply_to_message_id=reply_to_message_id,
        )
    except Exception as notify_error:
        logger.info(
            "[LiveDub] send-failure notice not sent: %s",
            str(notify_error)[:160],
        )
