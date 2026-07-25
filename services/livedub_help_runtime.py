#!/usr/bin/env python3
"""Keep /help aligned with the actual LiveDub delivery contract."""
from __future__ import annotations

import logging
import threading
from types import ModuleType

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_INSTALLED = False


async def help_command(update, context) -> None:
    from core.database import WHITELIST_IDS
    from core.globals import DAILY_LIMIT

    user_id = update.effective_user.id
    limit_line = (
        "👑 VIP — без ограничений"
        if user_id in WHITELIST_IDS
        else f"📵 {DAILY_LIMIT} видео/день • 1 запрос/мин"
    )
    audio_set = "видео с переводом + чистый русский MP3 + финальный объединённый MP3"
    text = (
        "ℹ️ <b>Помощь</b>\n\n"
        "Отправьте ссылку на видео или плейлист.\n\n"
        "🇷🇺 <b>Русский режим</b>\n"
        "MP3, тема, таймкоды, конспект и дополнительные материалы.\n\n"
        "🇬🇧 <b>ENG Full</b>\n"
        f"Полный анализ + {audio_set} + смысловая проверка перевода.\n\n"
        "⚡ <b>ENG Quick</b>\n"
        f"{audio_set}. Без конспекта и смысловой QA.\n\n"
        "⚡🔍 <b>ENG Quick QA</b>\n"
        f"{audio_set} + лёгкая проверка коротких роликов.\n\n"
        f"🔒 Ваши лимиты: {limit_line}\n\n"
        "/start — приветствие\n"
        "/help — эта справка\n"
        "/mode — выбор режима\n"
        "/archive — последние публикации\n"
        "/search &lt;текст&gt; — поиск по архиву\n"
        "/segments — список сегментов\n"
        "/cut — вырезать сегмент\n\n"
        "🔑 Для стабильных живых голосов требуется VOT_API_TOKEN "
        "или YANDEX_OAUTH_TOKEN в .env."
    )
    await update.message.reply_text(text, parse_mode="HTML")


def install_livedub_help_runtime(main_module: ModuleType) -> None:
    """Replace both imported bindings before Telegram handlers are constructed."""
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        import handlers.commands as commands

        commands.help_command = help_command
        main_module.help_command = help_command
        _INSTALLED = True
        logger.info("ℹ️ LiveDub help runtime: dual-MP3 mode descriptions enabled")
