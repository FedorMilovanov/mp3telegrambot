#!/usr/bin/env python3
"""Validated, self-healing entry point for MP3Bot. Run this file instead of main.py."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path


def _configure_stdio() -> None:
    """Make every early startup diagnostic safe on Windows legacy consoles."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError):
            pass


_configure_stdio()
_PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(_PROJECT_ROOT)
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

# Source provisioning belongs to the Python entrypoint, not only to the BAT
# wrapper. This keeps `Start Bot.bat` and direct `python bot_new.py` launches on
# one deterministic runtime path after `git pull`. A healthy runtime is reused;
# a missing or damaged one is rebuilt from the exact reviewed upstream commit.
from tools.ensure_bgutil_provider import ProvisionError, ensure_bgutil_provider

try:
    _youtube_provider_home = ensure_bgutil_provider()
except ProvisionError as exc:
    print(f"❌ YouTube maximum-quality runtime не удалось подготовить: {exc}")
    print("   Нужны Git, npm и Node.js >=22; quality fallback не используется.")
    sys.exit(2)

# YouTube changed GVS authorization in 2026-08: maximum-quality media URLs may
# require a video-bound Proof-of-Origin token. This is a production dependency,
# not an optional quality downgrade. Validate the automatic provider before the
# bot accepts work. Provisioning above repairs missing/partial local source; the
# checks below independently reject config/provider drift.
from services.youtube_po_token_runtime import (
    YouTubePoTokenRuntimeError,
    require_youtube_po_token_runtime,
)

try:
    _youtube_po_runtime = require_youtube_po_token_runtime()
except YouTubePoTokenRuntimeError as exc:
    print(f"❌ YouTube maximum-quality runtime не готов: {exc}")
    print("   Качество не понижено: format 18/360p fallback не используется.")
    sys.exit(2)
else:
    print(f"✅ YouTube PO Token: {_youtube_po_runtime.status_text()}")

from services import emit_service_bootstrap_diagnostics
from services.bot_lifecycle import run_bot_process
from services.database_migrations import apply_database_migrations
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
    apply_database_migrations(_main_module)
    bootstrap_post_main(_main_module)
    require_runtime_ready()
except (RuntimeBootstrapError, sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
    emit_service_bootstrap_diagnostics()
    print(f"❌ Обязательная runtime-композиция или миграция не готова: {exc}")
    sys.exit(3)

emit_service_bootstrap_diagnostics()

if __name__ == "__main__":
    raise SystemExit(run_bot_process(_main_module))
