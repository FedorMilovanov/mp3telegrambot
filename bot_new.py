#!/usr/bin/env python3
"""
bot_new.py — единственная правильная точка входа для MP3Bot.

Запуск:
    python bot_new.py       # Linux / macOS
    py -3.13 bot_new.py     # Windows

Зачем этот файл существует отдельно от main.py?
    main.py содержит run_bot_async() и main(), но не делает ранней валидации
    окружения — тяжёлые импорты произойдут раньше, чем бот проверит наличие
    BOT_TOKEN. bot_new.py выполняет load_dotenv() и проверку обязательных
    переменных ДО любых импортов, что даёт понятную ошибку вместо трейсбека.

Архитектура модулей (пакетная структура):
    core/               — фундамент
      globals.py        — глобальные переменные, Flask, Gemini-клиенты
      core_utils.py     — чистые утилиты без зависимостей (разрыв циклов)
      database.py       — SQLite CRUD, кэш, настройки
      text_utils.py     — очистка текста, нормализация
      url_utils.py      — YouTube URL helpers
      utils.py          — вспомогательные функции (rate limit, файлы, thumbnail)
      prompts.py        — все промпты для Gemini
      json_parser.py    — парсинг JSON от Gemini
      progress.py       — прогресс-бар

    converters/         — конвертеры контента
      md_telegraph.py   — Markdown → Telegraph nodes (бывш. markdown.py)
      caption.py        — build_caption для Telegram

    services/           — внешние сервисы
      telegraph.py      — Telegraph publisher
      telegraph_pages.py— Study/Reflection/Analytics/Terms/Questions страницы
      search.py         — RuTube/VK поиск
      gemini_analyze.py — gemini_analyze_audio
      ffmpeg.py         — FFmpeg / yt-dlp
      shorts_video.py   — рендер субтитров и видео
      shorts_candidates.py — поиск кандидатов Shorts/Clips
      render_clips_montage.py — рендер клипов и монтажа
      pdf_generator.py  — генерация PDF

    handlers/           — Telegram хэндлеры
      commands.py       — /start, /help, /settings, /pdf, /resetcache, /stop
      callbacks.py      — handle_callback (кнопки)

    pipelines/          — бизнес-логика обработки
      main_pipeline.py  — process_single_video
      shorts.py         — process_and_send_shorts
      clips.py          — process_and_send_clips
      montage.py        — process_and_send_montage
      playlist.py       — handle_playlist

    main.py             — run_bot_async, main()
    bot_new.py          — точка входа (этот файл)
"""

import os
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"  # ДО импорта faster-whisper/huggingface


# ── 1. Проверка версии Python ────────────────────────────────────────────────
import sys

if sys.version_info < (3, 11):
    print("❌ Требуется Python 3.11+")
    print(f"   Текущая версия: {sys.version}")
    sys.exit(1)

# ── 2. Ранняя загрузка .env — до импорта тяжёлых модулей ────────────────────
from dotenv import load_dotenv
load_dotenv()

# ── 3. Ранняя валидация обязательных переменных окружения ─────────────────────

_bot_token = os.getenv("BOT_TOKEN", "").strip()
_gemini_key = os.getenv("GEMINI_API_KEY", "").strip()

if not _bot_token:
    print("❌ КРИТИЧЕСКАЯ ОШИБКА: BOT_TOKEN не задан в .env!")
    print("   Создай файл .env и добавь: BOT_TOKEN=твой_токен")
    sys.exit(1)

if not _gemini_key:
    print("⚠️ GEMINI_API_KEY не задан — AI-функции будут недоступны")

# ── 4. Умный pre-flight локального Bot API ──────────────────────────────────
# Выполняется ДО импорта core.globals/main, поэтому при неудаче может выбрать
# облачный API только для текущего процесса, не меняя .env. Быстрый сетевой probe
# теперь только диагностический: он не имеет права заранее объявить local API
# невозможным без реального запуска telegram-bot-api.exe и проверки /getMe.
try:
    from services.local_botapi_bootstrap import prepare_local_bot_api
    prepare_local_bot_api()
except Exception as _local_bootstrap_error:
    # Ошибка диагноста не должна ронять весь бот: основной main.py сохранит свой
    # штатный local/cloud fallback.
    print(f"⚠️ Smart Local Bot API pre-flight пропущен: {_local_bootstrap_error}")

# ── 5. Импорт приложения ────────────────────────────────────────────────────
from main import main

# ── 6. Резерв для облачного лимита ──────────────────────────────────────────
# После импорта main уже загружены pipeline-модули. Устанавливаем узкий адаптер:
# если фактический Bot работает через api.telegram.org и готовый media-файл больше
# облачного лимита, он один раз автоматически пересжимается до безопасных ~48 МБ.
# Local Bot API при этом не трогаем и не ухудшаем качество.
try:
    from services.cloud_media_fallback import install_cloud_media_fallback
    install_cloud_media_fallback()
except Exception as _cloud_media_fallback_error:
    print(f"⚠️ Cloud media fallback не установлен: {_cloud_media_fallback_error}")

if __name__ == "__main__":
    main()
