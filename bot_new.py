#!/usr/bin/env python3
"""Validated, fail-closed entry point for MP3Bot. Run this file instead of main.py."""

from __future__ import annotations

import os
import sqlite3
import sys


def _configure_stdio() -> None:
    """Make every early startup diagnostic safe on Windows legacy consoles."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError):
            pass


_configure_stdio()
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

from services import emit_service_bootstrap_diagnostics
from services.bot_lifecycle import run_bot_process
from services.database_migrations import install_database_migrations
from services.runtime_manifest import (
    RuntimeBootstrapError,
    bootstrap_post_main,
    bootstrap_pre_main,
    require_runtime_ready,
)

try:
    bootstrap_pre_main()
except RuntimeBootstrapError as exc:
    emit_service_bootstrap_diagnostics()
    print(f"❌ Обязательная pre-main runtime-композиция не готова: {exc}")
    sys.exit(2)

# Deliberately use module ownership rather than "from main import main": the
# lifecycle service must execute run_bot_async exactly once in one event loop.
import main as _main_module

try:
    # services.__init__ applies the 3.7 environment policy before core.globals is
    # imported. This explicit idempotent install guarantees that legacy 3.6-only
    # Factory/editorial seams are upgraded before post-main Factory wiring even
    # if the compatibility import hook was not triggered by a particular import graph.
    from services.gemini_max_quality import install_max_quality_runtime

    install_max_quality_runtime()
    install_database_migrations(_main_module)
    bootstrap_post_main(_main_module)
    require_runtime_ready()
except (RuntimeBootstrapError, sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
    emit_service_bootstrap_diagnostics()
    print(f"❌ Обязательная runtime-композиция или миграция не готова: {exc}")
    sys.exit(3)

emit_service_bootstrap_diagnostics()

if __name__ == "__main__":
    raise SystemExit(run_bot_process(_main_module))
