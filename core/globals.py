#!/usr/bin/env python3
"""
Globals — импорты и глобальные переменные.
Telegram Bot: Media Audio Converter + AI Analysis
"""

import uuid
import os
import re
import html as html_mod
import sqlite3
import shutil
import urllib.parse
import hashlib
import sys
import subprocess
import asyncio
import logging
import json
import requests
import time
import threading
from io import BytesIO
from datetime import date
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# Явно указываем кэш HuggingFace — модели Whisper ищутся здесь
os.environ.setdefault("HF_HOME", str(Path.home() / ".cache" / "huggingface"))

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
)
import yt_dlp

# === Flask-сервер (обязателен для Render.com!) ===
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bot is running!"

@flask_app.route("/health")
def health():
    return "OK", 200

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

try:
    from google import genai
    from google.genai import types
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

# ─── Настройки ───────────────────────────────────────────────
BOT_TOKEN      = os.getenv("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
DOWNLOAD_DIR   = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)
THUMBS_DIR     = DOWNLOAD_DIR / "thumbs"
THUMBS_DIR.mkdir(exist_ok=True)
DB_PATH        = Path("bot_cache.db")

# ─── Лимиты и throttle ────────────────────────────────────────
DAILY_LIMIT      = 2   # Макс. видео в день для обычных пользователей
COOLDOWN_SECONDS = 60  # Минимум секунд между запросами

# FIXED #36: throttle для обхода THUMBS_DIR — не чаще раза в час
_THUMBS_LAST_CLEANUP: float = 0.0
_THUMBS_CLEANUP_INTERVAL: float = 3600.0  # секунд

# ─── Инициализация Gemini ─────────────────────────────────────
# FIX #1: все переменные определяются ДО их использования
GEMINI_API_KEY_2 = os.getenv("GEMINI_API_KEY_2", "").strip()
GEMINI_API_KEY_3 = os.getenv("GEMINI_API_KEY_3", "").strip()
GEMINI_API_KEY_4 = os.getenv("GEMINI_API_KEY_4", "").strip()

# FIXED #32: читаем один раз при старте
TELEGRAPH_TOKEN = os.getenv("TELEGRAPH_TOKEN", "").strip()

# Прокси для Gemini — httpx читает из os.environ напрямую.
_proxy_url = (
    os.environ.get("HTTPS_PROXY") or
    os.environ.get("https_proxy") or
    os.environ.get("HTTP_PROXY") or
    os.environ.get("http_proxy")
)
if _proxy_url:
    os.environ["HTTPS_PROXY"] = _proxy_url
    os.environ["https_proxy"] = _proxy_url
    os.environ["HTTP_PROXY"]  = _proxy_url
    os.environ["http_proxy"]  = _proxy_url

# FIX #1: инициализируем None ДО создания клиентов
gemini_client   = None
gemini_client_2 = None
gemini_client_3 = None
gemini_client_4 = None

# HttpOptions: явный таймаут 900 000 мс = 15 минут
_gemini_http_options = None
if HAS_GEMINI:
    try:
        _gemini_http_options = types.HttpOptions(timeout=900_000)
    except Exception as _e:
        pass

def _make_gemini_client(api_key: str):
    if _gemini_http_options is not None:
        try:
            return genai.Client(api_key=api_key, http_options=_gemini_http_options)
        except TypeError:
            pass
    return genai.Client(api_key=api_key)

if HAS_GEMINI and GEMINI_API_KEY:
    gemini_client = _make_gemini_client(GEMINI_API_KEY)
if HAS_GEMINI and GEMINI_API_KEY_2:
    gemini_client_2 = _make_gemini_client(GEMINI_API_KEY_2)
if HAS_GEMINI and GEMINI_API_KEY_3:
    gemini_client_3 = _make_gemini_client(GEMINI_API_KEY_3)
if HAS_GEMINI and GEMINI_API_KEY_4:
    gemini_client_4 = _make_gemini_client(GEMINI_API_KEY_4)

# Список клиентов по порядку — используется для fallback
GEMINI_CLIENTS = [c for c in [gemini_client, gemini_client_2, gemini_client_3, gemini_client_4] if c]

# FIX #2: убран вызов db_cleanup_old_records() — функция из database.py,
# которая здесь не импортирована (импортировать нельзя — циклическая зависимость).
# db_cleanup_old_records() вызывается в конце database.py после определения констант.

# ─── Per-video lock ───────────────────────────────────────────
_video_processing_locks = {}  # dict[str, asyncio.Lock]
_video_locks_mutex = threading.Lock()

def _get_video_lock(video_id: str) -> asyncio.Lock:
    """Возвращает (или создаёт) asyncio.Lock для данного video_id."""
    with _video_locks_mutex:
        if video_id not in _video_processing_locks:
            _video_processing_locks[video_id] = asyncio.Lock()
        return _video_processing_locks[video_id]


def is_quota_error(e) -> bool:
    s = str(e).lower()
    return "quota" in s or "429" in s or "resource_exhausted" in s

def is_overload_error(e) -> bool:
    s = str(e).lower()
    name = type(e).__name__.lower()
    return (
        "503" in s or bool(re.search(r'\b500\b', s)) or "unavailable" in s or "overloaded" in s
        or "high demand" in s or "internal server error" in s
        or "remoteprotocolerror" in name or "disconnect" in s
        or "server disconnected" in s or "without sending a response" in s
    )


async def gemini_generate(client_list, fn):
    """Пробует каждый клиент по порядку, переключается при квоте. При 503 — retry с паузой."""
    last_err = None
    for client in client_list:
        for attempt in range(3):
            try:
                return await fn(client)
            except Exception as e:
                if is_quota_error(e):
                    logger.warning("Gemini квота, пробую следующий ключ...")
                    last_err = e
                    break
                elif is_overload_error(e):
                    wait = 10 * (attempt + 1)
                    logger.warning(f"Gemini перегружен (500/503), жду {wait}с... (попытка {attempt+1}/3)")
                    await asyncio.sleep(wait)
                    last_err = e
                    continue
                else:
                    raise
    raise last_err or RuntimeError("Все Gemini-клиенты недоступны или список пуст")


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


class _TokenMaskFilter(logging.Filter):
    """Маскирует все секретные токены/ключи во всех лог-сообщениях."""

    def __init__(self):
        super().__init__()
        self._secrets: list[str] | None = None

    def _get_secrets(self) -> list[str]:
        if self._secrets is None:
            candidates = [
                os.getenv("BOT_TOKEN",       "").strip(),
                os.getenv("GEMINI_API_KEY",  "").strip(),
                os.getenv("GEMINI_API_KEY_2","").strip(),
                os.getenv("GEMINI_API_KEY_3","").strip(),
                os.getenv("GEMINI_API_KEY_4","").strip(),
                os.getenv("TELEGRAPH_TOKEN", "").strip(),
                os.getenv("VK_API_TOKEN",    "").strip(),
            ]
            self._secrets = [s for s in candidates if len(s) >= 8]
        return self._secrets

    def _mask(self, text: str) -> str:
        for secret in self._get_secrets():
            if secret in text:
                text = text.replace(secret, "***")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.msg and isinstance(record.msg, str):
                record.msg = self._mask(record.msg)
            if record.args:
                formatted = record.getMessage()
                masked = self._mask(formatted)
                if masked != formatted:
                    record.msg  = masked
                    record.args = ()
        except Exception:
            pass
        return True


_token_filter = _TokenMaskFilter()
logging.getLogger().addFilter(_token_filter)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("google_genai.models").setLevel(logging.WARNING)
logging.getLogger("telegram.vendor.ptb_urllib3").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Application").setLevel(logging.WARNING)
