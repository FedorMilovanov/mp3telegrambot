#!/usr/bin/env python3
"""Validated, fail-closed entry point for MP3Bot. Run this file instead of main.py."""

from __future__ import annotations

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

from services.runtime_manifest import (
    RuntimeBootstrapError,
    bootstrap_post_main,
    bootstrap_pre_main,
    require_runtime_ready,
)

try:
    bootstrap_pre_main()
except RuntimeBootstrapError as exc:
    print(f"❌ Обязательная pre-main runtime-композиция не готова: {exc}")
    sys.exit(2)

import main as _main_module
from main import main

try:
    bootstrap_post_main(_main_module)
    require_runtime_ready()
except RuntimeBootstrapError as exc:
    print(f"❌ Обязательная runtime-композиция не готова: {exc}")
    sys.exit(3)

if __name__ == "__main__":
    main()
