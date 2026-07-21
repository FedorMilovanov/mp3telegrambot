#!/usr/bin/env python3
"""Validated entry point for MP3Bot. Run this file instead of main.py."""

import os
import sys

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

if sys.version_info < (3, 11):
    print("❌ Требуется Python 3.11+")
    print(f"   Текущая версия: {sys.version}")
    sys.exit(1)

from dotenv import load_dotenv

load_dotenv()

_bot_token = os.getenv("BOT_TOKEN", "").strip()
_gemini_key = os.getenv("GEMINI_API_KEY", "").strip()

if not _bot_token:
    print("❌ КРИТИЧЕСКАЯ ОШИБКА: BOT_TOKEN не задан в .env!")
    print("   Создай файл .env и добавь: BOT_TOKEN=твой_токен")
    sys.exit(1)

if not _gemini_key:
    print("⚠️ GEMINI_API_KEY не задан — AI-функции будут недоступны")

# Reserve one process before Local API recovery and heavy imports. The legacy
# main.py guard is rebound to the same absolute path after import.
try:
    from services.project_runtime_hardening import acquire_early_singleton

    if not acquire_early_singleton():
        print("❌ Бот уже запущен в другом процессе. Новый экземпляр завершён.")
        sys.exit(2)
except Exception as _singleton_error:
    print(f"⚠️ Ранний singleton guard недоступен: {_singleton_error}")

try:
    from services.local_botapi_runtime import prepare_local_bot_api

    prepare_local_bot_api()
except Exception as _local_bootstrap_error:
    print(f"⚠️ Smart Local Bot API pre-flight пропущен: {_local_bootstrap_error}")

import main as _main_module

main = _main_module.main

try:
    from services.cloud_media_fallback import install_cloud_media_fallback

    install_cloud_media_fallback()
except Exception as _cloud_media_fallback_error:
    print(f"⚠️ Cloud media fallback не установлен: {_cloud_media_fallback_error}")

try:
    from services.project_runtime_hardening import install_project_runtime_hardening

    install_project_runtime_hardening(_main_module)
except Exception as _runtime_hardening_error:
    print(f"⚠️ Project runtime hardening не установлен: {_runtime_hardening_error}")

if __name__ == "__main__":
    main()
