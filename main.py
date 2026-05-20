#!/usr/bin/env python3
"""
main.py — run_bot_async, run_bot, main().

⚠️  Не запускать напрямую — используй bot_new.py.
    bot_new.py выполняет load_dotenv() и валидацию BOT_TOKEN до импорта
    тяжёлых зависимостей. Прямой запуск main.py это шаг пропускает.
"""
from core.globals import (
    BOT_TOKEN, flask_app, DB_PATH,
    GEMINI_CLIENTS, DAILY_LIMIT, COOLDOWN_SECONDS,
)
from core.database import (
    db_init, asettings_get,
    GEMINI_MODEL, WHITELIST_IDS, ADMIN_IDS,
)
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters,
)
from services.shorts_video import (
    HAS_FASTER_WHISPER,
    get_subtitles_mode_settings,
    _get_whisper_model,
)
from handlers.commands import start, help_command, handle_message, reset_cache_command, pdf_command, stop_command
from handlers.callbacks import handle_callback, settings_command

import asyncio
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

async def run_bot_async():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не найден!")
        return

    # AUDIT C5: чистим словарь per-video locks. После краша run_bot_async и
    # создания нового event loop старые Lock'и привязаны к мёртвому loop,
    # что в Python 3.10+ даёт RuntimeError при первом параллельном запросе.
    from core.globals import _video_processing_locks, _video_locks_mutex
    with _video_locks_mutex:
        _stale = len(_video_processing_locks)
        _video_processing_locks.clear()
    if _stale:
        logger.info(f"🧹 Очищено per-video locks от предыдущего запуска: {_stale}")

    logger.info("🚀 Бот запускается...")
    logger.info(f"🧠 AI ({GEMINI_MODEL}): {'✅' if GEMINI_CLIENTS else '❌'} (ключей: {len(GEMINI_CLIENTS)})")

    # AUDIT L6: обновлённые списки моделей по официальной странице
    # https://ai.google.dev/gemini-api/docs/deprecations (на 2026-05-20)
    _KNOWN_LIVE_MODELS = {
        # Gemini 3
        "gemini-3-flash-preview",
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-lite-preview",
        # Gemini 2.5 (stable до 16 окт 2026, 2.5-flash/pro до 17 июня 2026)
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
        "gemini-2.5-pro-preview",
    }
    _DEPRECATED_MODELS = {
        "gemini-2.0-flash",
        "gemini-2.0-flash-001",
        "gemini-2.0-flash-lite",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
        "gemini-1.5-flash-001",
        "gemini-1.0-pro",
        "gemini-pro",
        "gemini-pro-vision",
    }
    if GEMINI_MODEL in _DEPRECATED_MODELS:
        logger.warning(
            "⚠️  GEMINI_MODEL='%s' — устарела и скоро будет отключена. "
            "Рекомендуется GEMINI_MODEL='gemini-2.5-flash' (стабильная) или "
            "'gemini-3-flash-preview' (новая, free tier).",
            GEMINI_MODEL,
        )
    elif GEMINI_MODEL == "gemini-2.5-pro":
        logger.warning(
            "⚠️  GEMINI_MODEL='gemini-2.5-pro' — с 1 апреля 2026 Pro-модели "
            "требуют платного биллинга (free tier больше не работает). "
            "Если ключи free tier — все запросы получат 429/quota error, "
            "и бот будет выдавать только базовую информацию без анализа. "
            "Рекомендуется GEMINI_MODEL='gemini-2.5-flash' в .env"
        )
    elif GEMINI_MODEL not in _KNOWN_LIVE_MODELS:
        logger.warning(
            "⚠️  GEMINI_MODEL='%s' — модель не входит в список проверенных живых моделей. "
            "Если бот падает при первом запросе — проверьте правильность имени модели.",
            GEMINI_MODEL,
        )
    logger.info(f"🛡  Whitelist: {len(WHITELIST_IDS)} VIP-пользователей")
    logger.info(f"📵 Лимит: {DAILY_LIMIT} видео/день | {COOLDOWN_SECONDS}с между запросами")
    _subtitles_enabled = await asettings_get("shorts_subtitles")
    if _subtitles_enabled and not HAS_FASTER_WHISPER:
        logger.warning("⚠️  Субтитры включены, но faster-whisper НЕ установлен! Установите: pip install faster-whisper")
    elif HAS_FASTER_WHISPER:
        _sub_cfg = get_subtitles_mode_settings()
        if _subtitles_enabled:
            _sub_mode = (
                f"модель={_sub_cfg['model_name']} | "
                f"karaoke={'вкл' if _sub_cfg['karaoke'] else 'выкл'} | "
                f"режим={'лёгкий' if _sub_cfg['light'] else 'полный'}"
            )
            # AUDIT: проверяем что есть качественный шрифт для субтитров
            try:
                from services.shorts_video import _pick_subtitle_font
                _font = _pick_subtitle_font()
                if _font in ("Arial", "Arial Bold"):
                    logger.warning(
                        "⚠️  Шрифт субтитров: %s (fallback). "
                        "Для красивых субтитров установите Montserrat ExtraBold: "
                        "https://fonts.google.com/specimen/Montserrat → Download family → "
                        "распакуйте Montserrat-ExtraBold.ttf в C:/Windows/Fonts/", _font
                    )
                else:
                    logger.info(f"🅰️  Шрифт субтитров: {_font}")
            except Exception as _fe:
                logger.debug(f"font check: {_fe}")
            logger.info(f"💬 Субтитры Shorts: ✅ включены ({_sub_mode})")
            # Preload запустим в фоне после старта бота — не блокируем polling
            async def _preload_whisper_bg():
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, lambda: _get_whisper_model(_sub_cfg["model_name"]))
                    logger.info(f"🎤 Whisper preloaded: {_sub_cfg['model_name']}")
                except Exception as _e:
                    logger.warning(f"⚠️  Whisper preload не удался: {_e}")
            asyncio.create_task(_preload_whisper_bg())
        else:
            logger.info("💬 Субтитры Shorts: ⬜ выключены")
    else:
        logger.info("💬 Субтитры Shorts: ❌ (faster-whisper не установлен)")

    from telegram.request import HTTPXRequest

    # Кастомный request с увеличенными таймаутами — решает проблему
    # обрывов соединения в httpx 0.28+ (агрессивное закрытие idle-соединений)
    t_request = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=60.0,
        write_timeout=60.0,
        connect_timeout=60.0,
        pool_timeout=60.0,
    )

    app = Application.builder().token(BOT_TOKEN).request(t_request).build()
    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("help",       help_command))
    app.add_handler(CommandHandler("resetcache", reset_cache_command))
    app.add_handler(CommandHandler("settings",   settings_command))
    app.add_handler(CommandHandler("stop",       stop_command))
    app.add_handler(CommandHandler("pdf",        pdf_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    logger.info("✅ Бот запущен!")

    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        # Регистрируем команды — появится кнопка «Menu» слева от поля ввода
        from telegram import BotCommand, BotCommandScopeDefault, BotCommandScopeChat
        default_commands = [
            BotCommand("start", "▶️ Главная"),
            BotCommand("help",  "ℹ️ Справка"),
        ]
        await app.bot.set_my_commands(default_commands, scope=BotCommandScopeDefault())

        # VIP/Admin видят расширенное меню с /resetcache и /settings
        for admin_id in ADMIN_IDS:
            try:
                vip_commands = default_commands + [
                    BotCommand("resetcache", "🗑 Сбросить кэш видео"),
                    BotCommand("settings",   "⚙️ Настройки бота"),
                    BotCommand("stop",       "🛑 Остановить бота"),
                    BotCommand("pdf",        "📄 PDF из кэша"),
                ]
                await app.bot.set_my_commands(
                    vip_commands, scope=BotCommandScopeChat(chat_id=admin_id)
                )
            except Exception:
                pass

        # AUDIT M9: фоновая периодическая чистка временных файлов и БД
        from core.utils import cleanup_nosub_files
        from core.database import db_cleanup_old_records
        from core.globals import mark_bot_alive

        async def _periodic_maintenance():
            loop = asyncio.get_running_loop()
            while True:
                mark_bot_alive()
                try:
                    await loop.run_in_executor(None, cleanup_nosub_files)
                    await loop.run_in_executor(None, db_cleanup_old_records)
                except Exception as _e:
                    logger.warning(f"periodic maintenance: {_e}")
                await asyncio.sleep(3600)

        asyncio.create_task(_periodic_maintenance())

        while True:
            mark_bot_alive()
            await asyncio.sleep(60)


def run_bot():
    restart_delay = 5  # секунд между перезапусками
    while True:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_bot_async())
        except Exception as e:
            # AUDIT L7: убран print() — logger.error и так пишет ошибку,
            # а print не маскирует токены через _TokenMaskFilter.
            logger.error(f"run_bot завершился с ошибкой: {e}", exc_info=True)
        finally:
            try:
                loop.close()
            except Exception:
                pass
        logger.warning(f"🔄 Бот перезапускается через {restart_delay} сек...")
        time.sleep(restart_delay)


def main():
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    port = int(os.environ.get("PORT", 10000))
    # Используем production-grade WSGI-сервер вместо Flask dev-сервера.
    # waitress: многопоточный, не выдаёт "WARNING: Do not use dev server in production".
    try:
        from waitress import serve
        logger.info(f"🌐 HTTP health-check: waitress на порту {port}")
        serve(flask_app, host="0.0.0.0", port=port, threads=4)
    except ImportError:
        # Fallback на Flask dev-сервер если waitress не установлен.
        # Добавьте waitress в requirements.txt: waitress>=3.0.0,<4.0.0
        logger.warning(
            "⚠️  waitress не установлен — используется Flask dev-сервер. "
            "Установите: pip install waitress"
        )
        flask_app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
