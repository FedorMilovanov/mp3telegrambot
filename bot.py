#!/usr/bin/env python3
"""
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
from dotenv import load_dotenv
from pathlib import Path
load_dotenv()
# Явно указываем кэш HuggingFace — модели Whisper ищутся здесь
os.environ.setdefault("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
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

# ─── Per-video lock — защита от параллельной обработки одного видео ───────────
# Два одновременных запроса к одному video_id от разных пользователей
# приведут к коллизии temp-файлов и двойному скачиванию.
# Второй запрос ждёт завершения первого, после чего берёт результат из кэша.
_video_processing_locks: dict[str, asyncio.Lock] = {}
_video_locks_mutex = threading.Lock()

def _get_video_lock(video_id: str) -> asyncio.Lock:
    """Возвращает (или создаёт) asyncio.Lock для данного video_id."""
    with _video_locks_mutex:
        if video_id not in _video_processing_locks:
            _video_processing_locks[video_id] = asyncio.Lock()
        return _video_processing_locks[video_id]

# ─── SQLite кэш ───────────────────────────────────────────────
DB_PATH = Path("bot_cache.db")

def db_init():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS video_cache (
                video_id        TEXT PRIMARY KEY,
                url             TEXT DEFAULT '',
                quotes          TEXT DEFAULT '[]',
                questions       TEXT DEFAULT '[]',
                share_text      TEXT DEFAULT '',
                quotes_tg_url   TEXT DEFAULT '',
                questions_tg_url TEXT DEFAULT '',
                ai_data         TEXT DEFAULT '',
                telegraph_url   TEXT DEFAULT '',
                created_at      INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rate_limit (
                user_id      INTEGER PRIMARY KEY,
                last_request REAL    DEFAULT 0,
                daily_count  INTEGER DEFAULT 0,
                daily_date   TEXT    DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS short_trims (
                short_id      TEXT PRIMARY KEY,
                video_path    TEXT NOT NULL,
                start_seconds INTEGER NOT NULL,
                end_seconds   INTEGER NOT NULL,
                visual_mode   TEXT DEFAULT 'full_frame_vertical',
                yt_url        TEXT DEFAULT '',
                vk_url        TEXT DEFAULT '',
                rutube_url    TEXT DEFAULT '',
                performer     TEXT DEFAULT '',
                real_author   TEXT DEFAULT '',
                real_event    TEXT DEFAULT '',
                format_name   TEXT DEFAULT '',
                candidate_json TEXT DEFAULT '{}',
                created_at    INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        # Миграция video_cache: добавляем колонки если их нет (для старых баз данных)
        migrated = []
        for col, default in [
            ("quotes",           "'[]'"),
            ("questions",        "'[]'"),
            ("share_text",       "''"),
            ("quotes_tg_url",    "''"),
            ("questions_tg_url", "''"),
            ("ai_data",          "''"),
            ("telegraph_url",    "''"),
            ("analytics_json",   "''"),
            # Умный кэш — версионирование
            ("cache_version",    "''"),
            ("prompt_version",   "''"),
            ("model_name",       "''"),
            ("updated_at",       "0"),
            # Глоссарий терминов
            ("glossary_tg_url",  "''"),
            # Блок «Слова»
            ("terms_tg_url",     "''"),
            # Альтернативные платформы (кэшируем чтобы не искать повторно)
            ("rutube_url",           "''"),
            ("vk_url",               "''"),
            # Новые article-like страницы
            ("study_tg_url",         "''"),
            ("reflection_tg_url",    "''"),
        ]:
            try:
                col_type = "INTEGER DEFAULT 0" if col == "updated_at" else f"TEXT DEFAULT {default}"
                conn.execute(f"ALTER TABLE video_cache ADD COLUMN {col} {col_type}")
                migrated.append(col)
            except sqlite3.OperationalError:
                pass  # Колонка уже существует
        # Миграция short_trims: добавляем колонку для файла без субтитров
        try:
            conn.execute("ALTER TABLE short_trims ADD COLUMN video_path_nosub TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # Колонка уже есть
        conn.commit()
        if migrated:
            pass

def db_cleanup_old_records():
    """Удаляет записи кэша старше CACHE_TTL_DAYS. Вызывать после определения констант."""
    try:
        cutoff = int(time.time()) - CACHE_TTL_DAYS * 86400
        with sqlite3.connect(DB_PATH) as conn:
            deleted = conn.execute(
                "DELETE FROM video_cache WHERE updated_at > 0 AND updated_at < ?", (cutoff,)
            ).rowcount
            conn.commit()
        pass
    except Exception as _ce:
        pass

def db_save(video_id: str, url: str, questions: list,
            quotes_tg_url: str = "", questions_tg_url: str = "",
            ai_data: dict = None, telegraph_url: str = "",
            cache_version: str = "", prompt_version: str = "", model_name: str = "",
            terms_tg_url: str = "",
            rutube_url: str = "", vk_url: str = "",
            study_tg_url: str = "", reflection_tg_url: str = ""):
    _cache_version  = cache_version  or CACHE_VERSION
    _prompt_version = prompt_version or get_prompt_fingerprint()
    _model_name     = model_name     or GEMINI_MODEL
    _updated_at     = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO video_cache
                (video_id, url, questions, quotes_tg_url, questions_tg_url,
                 ai_data, telegraph_url,
                 cache_version, prompt_version, model_name, updated_at, terms_tg_url,
                 rutube_url, vk_url, study_tg_url, reflection_tg_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                url              = excluded.url,
                questions        = excluded.questions,
                quotes_tg_url    = excluded.quotes_tg_url,
                questions_tg_url = excluded.questions_tg_url,
                ai_data          = excluded.ai_data,
                telegraph_url    = excluded.telegraph_url,
                cache_version    = excluded.cache_version,
                prompt_version   = excluded.prompt_version,
                model_name       = excluded.model_name,
                updated_at       = excluded.updated_at,
                terms_tg_url     = excluded.terms_tg_url,
                rutube_url       = excluded.rutube_url,
                vk_url           = excluded.vk_url,
                study_tg_url     = excluded.study_tg_url,
                reflection_tg_url = excluded.reflection_tg_url
        """, (video_id, url,
              json.dumps(questions, ensure_ascii=False),
              quotes_tg_url, questions_tg_url,
              json.dumps(ai_data, ensure_ascii=False) if ai_data else "",
              telegraph_url or "",
              _cache_version, _prompt_version, _model_name, _updated_at,
              terms_tg_url or "",
              rutube_url or "", vk_url or "",
              study_tg_url or "", reflection_tg_url or ""))
        conn.commit()

def db_get(video_id: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT url, questions, quotes_tg_url, questions_tg_url, ai_data, telegraph_url, "
            "cache_version, prompt_version, model_name, updated_at, terms_tg_url, "
            "rutube_url, vk_url, study_tg_url, reflection_tg_url "
            "FROM video_cache WHERE video_id = ?", (video_id,)
        ).fetchone()
    if not row:
        return None
    ai_data = None
    if row[4]:
        try:
            ai_data = json.loads(row[4])
        except Exception:
            pass
    questions = []
    try:
        questions = json.loads(row[1]) if row[1] else []
    except Exception:
        pass
    return {
        "url":               row[0],
        "questions":         questions,
        "quotes_tg_url":     row[2],
        "questions_tg_url":  row[3],
        "ai_data":           ai_data,
        "telegraph_url":     row[5],
        "cache_version":     row[6]  or "",
        "prompt_version":    row[7]  or "",
        "model_name":        row[8]  or "",
        "updated_at":        row[9]  or 0,
        "terms_tg_url":      row[10] or "",
        "rutube_url":        row[11] or "",
        "vk_url":            row[12] or "",
        "study_tg_url":      row[13] or "",
        "reflection_tg_url": row[14] or "",
    }

# ─── Async-обёртки для DB (не блокируют event loop) ──────────

async def adb_get(video_id: str) -> dict | None:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, db_get, video_id)

async def adb_save(video_id: str, url: str, questions: list, **kwargs) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: db_save(video_id, url, questions, **kwargs))

async def asettings_get(key: str) -> bool:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, settings_get, key)

async def asettings_set(key: str, value: bool) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, settings_set, key, value)

db_init()

# ─── Short trim helpers ───────────────────────────────────────

def short_trim_save(short_id: str, video_path: str, start_seconds: int, end_seconds: int,
                    visual_mode: str = "full_frame_vertical", yt_url: str = "",
                    vk_url: str = "", rutube_url: str = "", performer: str = "",
                    real_author: str = "", real_event: str = "", format_name: str = "",
                    candidate_json: str = "{}", video_path_nosub: str = "") -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO short_trims
                (short_id, video_path, start_seconds, end_seconds, visual_mode,
                 yt_url, vk_url, rutube_url, performer, real_author, real_event,
                 format_name, candidate_json, video_path_nosub)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (short_id, video_path, start_seconds, end_seconds, visual_mode,
              yt_url, vk_url, rutube_url, performer, real_author, real_event,
              format_name, candidate_json, video_path_nosub))
        conn.commit()

def short_trim_get(short_id: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT video_path, start_seconds, end_seconds, visual_mode, yt_url, vk_url, "
            "rutube_url, performer, real_author, real_event, format_name, candidate_json, "
            "video_path_nosub "
            "FROM short_trims WHERE short_id = ?", (short_id,)
        ).fetchone()
    if not row:
        return None
    return {
        "video_path": row[0], "start_seconds": row[1], "end_seconds": row[2],
        "visual_mode": row[3], "yt_url": row[4], "vk_url": row[5],
        "rutube_url": row[6], "performer": row[7], "real_author": row[8],
        "real_event": row[9], "format_name": row[10],
        "candidate_json": row[11] or "{}",
        "video_path_nosub": row[12] if len(row) > 12 else "",
    }



def is_cache_valid(cached: dict | None) -> tuple[bool, str]:
    """Проверяет актуальность записи кэша. Возвращает (True, 'ok') или (False, причина)."""
    if not cached:
        return False, "no_record"
    if not cached.get("ai_data"):
        return False, "no_ai_data"
    # TTL
    updated_at = cached.get("updated_at") or 0
    if not updated_at:
        return False, "no_updated_at (legacy record)"
    age_days = (time.time() - updated_at) / 86400
    if age_days > CACHE_TTL_DAYS:
        return False, f"ttl_expired ({age_days:.1f}d > {CACHE_TTL_DAYS}d)"
    # Версия кэша
    if cached.get("cache_version") != CACHE_VERSION:
        return False, f"cache_version_mismatch"
    # Версия промпта
    fp = get_prompt_fingerprint()
    if cached.get("prompt_version") != fp:
        return False, "prompt_version_mismatch"
    # Модель
    if cached.get("model_name") != GEMINI_MODEL:
        return False, "model_mismatch"
    return True, "ok"


# ─── Настройки бота (персистентные) ─────────────────────────

# Дефолтные значения всех настроек
SETTINGS_DEFAULTS: dict[str, bool] = {
    "synopsis":               True,   # Конспект (Telegraph)
    "analytics":              True,   # Аналитика
    "questions":              True,   # Вопросы для размышления
    "terms":                  True,   # Слова (понятия, Писание, переводы, лексика)
    "shorts":                 False,  # Shorts-кандидаты (вырезка фрагментов)
    "shorts_audio_normalize": True,   # Нормализация громкости для shorts
    "shorts_snapshot":        True,   # Snapshot (plain) для каждого short
    "shorts_subtitles":       False,  # Субтитры (burn-in) для Shorts
    "shorts_subtitles_karaoke": True,  # Karaoke word-level подсветка (только если subtitles вкл.)
    "shorts_subtitles_light": False,   # Лёгкий режим: medium модель вместо large-v3
    "shorts_montage":         False,  # Тематическая склейка из разных моментов
    "shorts_highlights":      False,  # Рекламный highlights reel
    "shorts_title_poster":    True,   # Стильный постер с заголовком для Shorts
    "clips":                  False,  # Длинные clips (5–15 мин) из Q&A / лекций
    "clips_snapshot":         True,   # Poster/snapshot для каждого clip
    # ── Новая продуктовая модель: две сильных article-like страницы ──────────
    "study_analysis":         True,   # «Разбор материала» (аналитика+термины+богословие)
    "reflection_application": True,   # «Размышление и применение» (пасторский guide)
    "caption_full_text":      True,   # Отправлять полный текст отдельным сообщением
    "generate_pdf":           False,  # Генерировать PDF (выключено по умолчанию)
}

SETTINGS_LABELS: dict[str, str] = {
    "synopsis":               "📋 Конспект",
    "analytics":              "📊 Аналитика",
    "questions":              "🗣 Вопросы",
    "terms":                  "🔤 Термины",
    "shorts":                 "✂️ Shorts",
    "shorts_audio_normalize": "🔊 Normalize",
    "shorts_snapshot":        "📸 Snapshot",
    "shorts_subtitles":       "💬 Субтитры",
    "shorts_subtitles_karaoke": "🎤 Karaoke",
    "shorts_subtitles_light": "⚡ Light model",
    "shorts_title_poster":    "🖼 Poster",
    "shorts_montage":         "🎬 Montage",
    "shorts_highlights":      "🌟 Highlights",
    "clips":                  "🎬 Clips",
    "clips_snapshot":         "📸 Poster",
    "study_analysis":         "📖 Разбор материала",
    "reflection_application": "🙏 Размышление и применение",
    "caption_full_text":      "📋 Полный текст отдельно",
    "generate_pdf":           "📄 Генерировать PDF",
}

# ─── Скорость Shorts (не bool, отдельная настройка) ──────────
SHORTS_SPEED_STEPS: list[str] = ["1.0", "1.1", "1.3", "1.5"]
SHORTS_SPEED_DEFAULT: str     = "1.0"

def shorts_speed_get() -> str:
    """Читает текущую скорость Shorts из БД."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT value FROM bot_settings WHERE key = 'shorts_speed'"
            ).fetchone()
        if row and row[0] in SHORTS_SPEED_STEPS:
            return row[0]
    except Exception:
        pass
    return SHORTS_SPEED_DEFAULT

def shorts_speed_set(value: str) -> None:
    """Сохраняет скорость Shorts в БД."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES ('shorts_speed', ?)",
            (value,)
        )
        conn.commit()

def shorts_speed_cycle() -> str:
    """Циклически переключает скорость и возвращает новое значение."""
    current = shorts_speed_get()
    try:
        idx = SHORTS_SPEED_STEPS.index(current)
    except ValueError:
        idx = 0
    next_val = SHORTS_SPEED_STEPS[(idx + 1) % len(SHORTS_SPEED_STEPS)]
    shorts_speed_set(next_val)
    return next_val


def settings_get(key: str) -> bool:
    """Читает настройку из БД. Fallback на дефолт если не задана."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)).fetchone()
        if row:
            return row[0].lower() in ("1", "true", "yes")
    except Exception:
        pass
    return SETTINGS_DEFAULTS.get(key, True)


def settings_set(key: str, value: bool) -> None:
    """Сохраняет настройку в БД."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            (key, "1" if value else "0")
        )
        conn.commit()


def settings_get_all() -> dict[str, bool]:
    """Возвращает все настройки."""
    return {k: settings_get(k) for k in SETTINGS_DEFAULTS}


async def asettings_get_all() -> dict[str, bool]:
    """Async-обёртка: возвращает все настройки за один вызов executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, settings_get_all)


# ─── Маппинг каналов YouTube → RuTube / VK ───────────────────
# YouTube-канал "Fedor Milovanov" → RuTube "Господь Бог - Сила Моя" (ID 1876662)
#                                 → VK "† Господь Бог - Сила Моя! †" (vk_owner_id — заполнить!)
# Чтобы узнать vk_owner_id: открыть в браузере:
# https://api.vk.com/method/groups.getById?group_id=the_lord_god_is_my_strength&v=5.199
# и вставить число из поля "id" (со знаком минус: -XXXXXXX)
CHANNEL_MAP: dict[str, dict] = {
    "fedor milovanov": {
        "rutube_channel_id": "1876662",
        "vk_owner_id": "-60805374",
        "vk_domain": "the_lord_god_is_my_strength",
    },
}

def load_channel_mappings():
    raw = os.getenv("CHANNEL_MAPPINGS", "{}")
    try:
        for key, val in json.loads(raw).items():
            CHANNEL_MAP[key.lower()] = val
    except Exception:
        pass

load_channel_mappings()

def get_channel_mapping(channel_name: str) -> dict | None:
    if not channel_name:
        return None
    key = channel_name.lower().strip()
    if key in CHANNEL_MAP:
        return CHANNEL_MAP[key]
    for map_key, mapping in CHANNEL_MAP.items():
        if map_key in key or key in map_key:
            return mapping
    return None
MAX_FILE_SIZE_MB  = 50
MAX_PLAYLIST_SIZE = 50
# Модели для быстрого переключения:
# "gemini-3-flash-preview"        — новая, сильная (текущая)
# "gemini-2.5-flash"              — стабильная, проверенная
# "gemini-3.1-flash-lite-preview" — лёгкая, скудная (не рекомендуется)
GEMINI_MODEL  = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")

# ─── Умный кэш — версионирование ─────────────────────────────
CACHE_VERSION         = os.getenv("CACHE_VERSION",         "2026-03-22-v5")
CACHE_TTL_DAYS        = int(os.getenv("CACHE_TTL_DAYS",    "45"))
PROMPT_SCHEMA_VERSION = os.getenv("PROMPT_SCHEMA_VERSION", "synopsis-v4")


def get_prompt_fingerprint() -> str:
    """SHA-256 от сочетания версии промпта и модели → первые 16 hex-символов."""
    raw = f"{PROMPT_SCHEMA_VERSION}|{GEMINI_MODEL}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

# ─── Защита от спама ─────────────────────────────────────────
# WHITELIST — ваши люди, без ограничений.
# Узнать свой Telegram ID: написать @userinfobot
# Пример: WHITELIST_IDS = {123456789, 987654321}
_wl_raw = os.getenv("WHITELIST_IDS", "")
WHITELIST_IDS: set[int] = {int(x) for x in _wl_raw.split(",") if x.strip().lstrip("-").isdigit()}

# ADMIN_IDS — могут использовать /resetcache. По умолчанию совпадает с WHITELIST.
# Можно задать отдельно через ADMIN_IDS=123456789 в .env
_admin_raw = os.getenv("ADMIN_IDS", _wl_raw)
ADMIN_IDS: set[int] = {int(x) for x in _admin_raw.split(",") if x.strip().lstrip("-").isdigit()}

DAILY_LIMIT      = 2   # Макс. видео в день для обычных пользователей
COOLDOWN_SECONDS = 60  # Минимум секунд между запросами

# rate limit теперь хранится в SQLite (таблица rate_limit) — не сбрасывается при перезапуске

# ─── Инициализация Gemini ─────────────────────────────────────
GEMINI_API_KEY_2 = os.getenv("GEMINI_API_KEY_2", "").strip()
GEMINI_API_KEY_3 = os.getenv("GEMINI_API_KEY_3", "").strip()
GEMINI_API_KEY_4 = os.getenv("GEMINI_API_KEY_4", "").strip()

# Прокси для Gemini — httpx читает из os.environ напрямую.
# Прописываем явно до создания клиентов, чтобы гарантировать применение.
# Берём из .env если задан, иначе используем системный прокси Windows.
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

gemini_client = None
gemini_client_2 = None
gemini_client_3 = None
gemini_client_4 = None

# HttpOptions: явный таймаут 300 000 мс = 5 минут.
# Решает WriteTimeout при загрузке ~30 MB через Hiddify.
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

# Автоочистка старых записей кэша — вызываем после определения CACHE_TTL_DAYS
db_cleanup_old_records()


def is_quota_error(e) -> bool:
    s = str(e).lower()
    return "quota" in s or "429" in s or "resource_exhausted" in s

def is_overload_error(e) -> bool:
    s = str(e).lower()
    name = type(e).__name__.lower()
    return (
        "503" in s or "500" in s or "unavailable" in s or "overloaded" in s
        or "high demand" in s or "internal" in s
        or "remoteprotocolerror" in name or "disconnect" in s
        or "server disconnected" in s or "without sending a response" in s
    )


async def gemini_generate(client_list, fn):
    """Пробует каждый клиент по порядку, переключается при квоте. При 503 — retry с паузой."""
    last_err = None
    for client in client_list:
        for attempt in range(3):  # до 3 попыток при 503/overload
            try:
                return await fn(client)
            except Exception as e:
                if is_quota_error(e):
                    # Квота исчерпана на этом ключе — сразу переходим к следующему
                    logger.warning("Gemini квота, пробую следующий ключ...")
                    last_err = e
                    break  # выходим из inner loop → следующий client
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
        self._secrets: list[str] | None = None  # кэш — заполняется один раз при первом вызове

    def _get_secrets(self) -> list[str]:
        """Собирает секреты из env один раз и кэширует — они не меняются в runtime."""
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
            # Маскируем msg до форматирования
            if record.msg and isinstance(record.msg, str):
                record.msg = self._mask(record.msg)
            # Маскируем args (они вставляются в msg через % форматирование)
            if record.args:
                formatted = record.getMessage()
                masked = self._mask(formatted)
                if masked != formatted:
                    record.msg  = masked
                    record.args = ()
        except Exception:
            pass
        return True


# Вешаем фильтр на root-логгер — покрывает все дочерние (telegram, httpx, httpcore и т.д.)
_token_filter = _TokenMaskFilter()
logging.getLogger().addFilter(_token_filter)

# Приглушаем шумные библиотечные логгеры (они пишут URL с токеном на уровне DEBUG/INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("google_genai.models").setLevel(logging.WARNING)
logging.getLogger("telegram.vendor.ptb_urllib3").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Application").setLevel(logging.WARNING)

# ─── Regex для ссылок ─────────────────────────────────────────
VIDEO_REGEX = re.compile(
    r"(?:https?://)?"
    r"(?:www\.)?"
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/live/)"
    r"[\w\-]{11}"
)

PLAYLIST_REGEX = re.compile(
    r"(?:https?://)?"
    r"(?:www\.)?"
    r"youtube\.com/(?:playlist\?list=|watch\?.*list=)"
    r"[\w\-]+"
)

TITLE_SEPARATORS = [" — ", " - ", " | ", " // ", ": ", " – "]


# ─── Базовые настройки yt-dlp ─────────────────────────────────
PYTHON_EXEC = sys.executable
COOKIES_FILE = Path("cookies.txt")

def _build_ytdlp_base_args() -> list:
    args = [PYTHON_EXEC, "-m", "yt_dlp", "--sleep-interval", "2", "--quiet"]
    if COOKIES_FILE.exists():
        args += ["--cookies", str(COOKIES_FILE)]
    elif shutil.which("firefox"):
        args += ["--cookies-from-browser", "firefox"]
    else:
        print("⚠️ Нет cookies — YouTube может блокировать запросы")
    # JS runtime для решения YouTube n challenge
    js_runtimes = []
    if shutil.which("node"):
        js_runtimes.append("node")
    if shutil.which("deno"):
        js_runtimes.append("deno")
    if js_runtimes:
        args += ["--js-runtimes", ",".join(js_runtimes)]
        args += ["--remote-components", "ejs:github"]
    else:
        print("⚠️ Node.js/Deno не найдены — js-runtimes отключён")
    return args

YTDLP_BASE_ARGS = _build_ytdlp_base_args()



# ─── Вспомогательные функции ─────────────────────────────────

def is_media_url(text: str) -> bool:
    return bool(VIDEO_REGEX.search(text) or PLAYLIST_REGEX.search(text))


def is_playlist_url(text: str) -> bool:
    return bool(PLAYLIST_REGEX.search(text))


def extract_media_url(text: str) -> str | None:
    url_match = re.search(r"(https?://[^\s]+)", text)
    if url_match:
        url = url_match.group(0)
        if "youtube.com" in url or "youtu.be" in url:
            return url
    match = PLAYLIST_REGEX.search(text) or VIDEO_REGEX.search(text)
    return match.group(0) if match else None


def parse_title(full_title: str, channel_name: str = "") -> tuple[str, str]:
    full_title = full_title.strip()
    for sep in TITLE_SEPARATORS:
        if sep in full_title:
            parts = full_title.split(sep, 1)
            left  = parts[0].strip()
            right = parts[1].strip()
            if not left or not right:
                continue
            channel_lower = channel_name.lower().strip()
            if channel_lower and channel_lower in left.lower():
                return left, right
            elif channel_lower and channel_lower in right.lower():
                return right, left
            left_words  = len(left.split())
            right_words = len(right.split())
            if left_words <= 4 and right_words > left_words:
                return left, right
            if right_words <= 4 and left_words > right_words:
                return right, left
            return left, right
    if channel_name:
        return channel_name, full_title
    return "", full_title


def _crop_black_bars_image(img):
    # Обрезает черные полосы (letterbox/pillarbox) с изображения
    THRESHOLD = 18
    MIN_STRIP = 4
    try:
        import numpy as np
        arr = np.array(img.convert("L"))
        h, w = arr.shape
        top = 0
        while top < h and arr[top, :].mean() < THRESHOLD:
            top += 1
        bottom = h - 1
        while bottom > top and arr[bottom, :].mean() < THRESHOLD:
            bottom -= 1
        bottom += 1
        left = 0
        while left < w and arr[:, left].mean() < THRESHOLD:
            left += 1
        right = w - 1
        while right > left and arr[:, right].mean() < THRESHOLD:
            right -= 1
        right += 1
        crop_top    = top    if top    >= MIN_STRIP else 0
        crop_bottom = bottom if h - bottom >= MIN_STRIP else h
        crop_left   = left   if left   >= MIN_STRIP else 0
        crop_right  = right  if w - right >= MIN_STRIP else w
        if (crop_top, crop_left, crop_bottom, crop_right) != (0, 0, h, w):
            img = img.crop((crop_left, crop_top, crop_right, crop_bottom))
    except Exception:
        pass
    return img


def prepare_thumbnail(thumb_path: Path) -> BytesIO | None:
    try:
        if HAS_PILLOW:
            with Image.open(thumb_path) as img:
                img = img.convert("RGB")
                # Убираем черные полосы перед кропом в квадрат
                img = _crop_black_bars_image(img)
                width, height = img.size
                min_side = min(width, height)
                left   = (width  - min_side) // 2
                top    = (height - min_side) // 2
                right  = left + min_side
                bottom = top  + min_side
                img = img.crop((left, top, right, bottom))
                img = img.resize((480, 480), Image.LANCZOS)
                buffer = BytesIO()
                img.save(buffer, format="JPEG", quality=85)
                buffer.seek(0)
                buffer.name = "thumb.jpg"
                return buffer
        else:
            # Без Pillow: читаем файл, но проверяем что это действительно JPEG
            # (по магическим байтам FF D8 FF)
            raw = thumb_path.read_bytes()
            if raw[:2] == b"\xff\xd8":
                buffer = BytesIO(raw)
                buffer.name = "thumb.jpg"
                return buffer
            # Не JPEG — Telegram может не принять, пропускаем thumbnail
            logger.warning(f"prepare_thumbnail: {thumb_path.name} не является JPEG (нет Pillow для конвертации)")
            return None
    except Exception as e:
        logger.warning(f"Не удалось подготовить обложку: {e}")
        return None


def cleanup_files(media_id: str, keep_mp3: bool = True) -> None:
    for f in DOWNLOAD_DIR.glob(f"{media_id}*"):
        if not f.is_file():
            continue
        # Сохраняем mp3 и обложку для повторного использования
        if keep_mp3 and (f.suffix == ".mp3" or "_thumb" in f.name):
            continue
        f.unlink(missing_ok=True)
    # Удаляем устаревшие обложки из THUMBS_DIR (старше CACHE_TTL_DAYS)
    try:
        _thumbs_cutoff = time.time() - CACHE_TTL_DAYS * 86400
        for _tf in THUMBS_DIR.iterdir():
            if _tf.is_file() and _tf.stat().st_mtime < _thumbs_cutoff:
                _tf.unlink(missing_ok=True)
    except Exception:
        pass


def cleanup_nosub_files(max_age_hours: int = 24) -> int:
    """Удаляет nosub-файлы старше max_age_hours часов."""
    cutoff = time.time() - max_age_hours * 3600
    deleted = 0
    try:
        for f in DOWNLOAD_DIR.glob("*_nosub.mp4"):
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
                deleted += 1
        if deleted:
            logger.info(f"Автоочистка: удалено {deleted} nosub-файлов старше {max_age_hours}ч")
    except Exception as e:
        logger.warning(f"cleanup_nosub_files error: {e}")
    return deleted

# Автоочистка nosub-файлов — вызываем после определения функции
cleanup_nosub_files()


def format_timestamp(seconds: float) -> str:
    seconds = max(0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ─── Защита от спама ─────────────────────────────────────────

def check_rate_limit(user_id: int) -> tuple[bool, str]:
    """Проверяет лимиты. Возвращает (разрешено, причина). Данные хранятся в SQLite.

    Намеренно использует обычное чтение (без EXCLUSIVE): check и update разделены
    по дизайну — update вызывается только после успешной обработки, поэтому
    объединить их в одну транзакцию невозможно без изменения архитектуры.
    Гонка при конкурентных запросах от одного пользователя маловероятна
    на практике (Telegram не шлёт параллельные updates от одного user_id).
    Атомарность записи гарантируется в update_rate_limit через единый SQL UPSERT.
    """
    if user_id in WHITELIST_IDS:
        return True, ""

    now   = time.time()
    today = str(date.today())

    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT last_request, daily_count, daily_date FROM rate_limit WHERE user_id = ?",
                (user_id,)
            ).fetchone()
    except Exception:
        return True, ""  # при ошибке БД не блокируем пользователя

    last_request = row[0] if row else 0.0
    daily_count  = row[1] if row else 0
    daily_date   = row[2] if row else ""

    # Антиспам: cooldown между запросами
    elapsed = now - last_request
    if elapsed < COOLDOWN_SECONDS:
        wait = int(COOLDOWN_SECONDS - elapsed)
        return False, (
            f"⏳ Подождите ещё {wait} сек.\n"
            f"Ограничение: 1 запрос в минуту."
        )

    # Дневной лимит
    if daily_date != today:
        daily_count = 0
    if daily_count >= DAILY_LIMIT:
        return False, (
            f"📵 Дневной лимит исчерпан ({DAILY_LIMIT} видео/день).\n"
            f"Приходите завтра! 🙏"
        )

    return True, ""


def update_rate_limit(user_id: int) -> None:
    """Обновляет счётчики после запроса в SQLite.

    Атомарность обеспечивается единым SQL UPSERT: инкремент счётчика
    вычисляется прямо в SQL без отдельного SELECT + Python-логики.
    Это устраняет race condition между SELECT и INSERT из предыдущей версии
    (там BEGIN EXCLUSIVE + отдельный SELECT + INSERT были тремя шагами).
    SQLite сам сериализует одиночные write-запросы — BEGIN EXCLUSIVE не нужен.
    """
    if user_id in WHITELIST_IDS:
        return
    today = str(date.today())
    now   = time.time()
    try:
        with sqlite3.connect(DB_PATH, timeout=5) as conn:
            conn.execute(
                """
                INSERT INTO rate_limit (user_id, last_request, daily_count, daily_date)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    last_request = excluded.last_request,
                    daily_count  = CASE
                                       WHEN daily_date = excluded.daily_date
                                       THEN daily_count + 1
                                       ELSE 1
                                   END,
                    daily_date   = excluded.daily_date
                """,
                (user_id, now, today)
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"update_rate_limit DB error: {e}")


# ─── Gemini AI анализ ─────────────────────────────────────────

async def gemini_analyze_audio(mp3_path, title, performer, duration, status_msg, prefix=""):
    if not GEMINI_CLIENTS:
        return None, None, None
    try:
        file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
        await set_progress(status_msg, 4, {"info": "🧠 Загружаю аудио для анализа..."})
        loop = asyncio.get_running_loop()
        audio_bytes = mp3_path.read_bytes() if file_size_mb <= 20 else None

        async def upload_to_client(client):
            """Загружает файл через конкретный ключ и возвращает audio_part."""
            if file_size_mb > 20:
                uf = await client.aio.files.upload(
                    file=mp3_path,
                    config=types.UploadFileConfig(mime_type="audio/mpeg", display_name=f"{performer} - {title}")
                )
                while uf.state == "PROCESSING":
                    await set_progress(status_msg, 4, {"info": "🧠 Gemini обрабатывает аудио... ⏳"})
                    await asyncio.sleep(3)
                    uf = await client.aio.files.get(name=uf.name)
                if uf.state == "FAILED":
                    raise Exception("File processing failed")
                return uf, client
            else:
                return types.Part.from_bytes(data=audio_bytes, mime_type="audio/mpeg"), client

        await set_progress(status_msg, 4, {"info": "🧠 AI анализирует материал..."})
        duration_str = format_timestamp(duration)

        prompt = (
            "Ты — ассистент для анализа проповедей и лекций.\n"
            "\n"
            f"YouTube-название: {title}\n"
            f"YouTube-канал: {performer if performer else 'не указан'}\n"
            f"Длительность: {duration_str}\n"
            "\n"
            "ПРАВИЛО НЕОПРЕДЕЛЁННОСТИ: если не уверен — возвращай пустую строку \"\" или []. Не выдумывай.\n"
            "ПРАВИЛО ФАКТИЧНОСТИ: только то, что реально звучит в аудио. Не дополнять, не домысливать.\n"
            "ПРАВИЛО ЧИСТОТЫ: одна строка без переносов. Никогда не включай: 'Cocoon AI Summary', 'AI Summary', 'Auto-generated', 'Статья представляет'.\n"
            "ЗАПРЕТ НА МЕТА-МАРКЕРЫ: НИКОГДА не вставляй в любое поле ответа фразы 'Cocoon AI Summary', 'Cocoon AI', 'AI Summary', 'Content Summary', 'Auto-generated summary', 'статья содержит', 'статья представляет', 'статья обсуждает', 'данный материал представляет', 'данное видео'. Никаких мета-подписей, атрибуций или маркеров источника.\n"
            "\n"
            "ЗАДАЧА 0 — АВТОР И НАЗВАНИЕ:\n"
            "- real_author: имя реального говорящего. Скобки с именем → автор: (Джон МакАртур) → 'Джон МакАртур'. Если не уверен — \"\".\n"
            "- real_title: только тема проповеди. Убрать номер серии, имя автора в скобках, бренд серии.\n"
            "- real_event: название серии/конференции если явно присутствует, иначе \"\".\n"
            "Пример: 'Свет Евангелия | 2 | Слава в Глиняных Горшках (МакАртур)'\n"
            "→ real_event='Свет Евангелия', real_title='Слава в Глиняных Горшках', real_author='Джон МакАртур'\n"
            "\n"
            "ЗАДАЧА 1 — ФОРМАТ МАТЕРИАЛА:\n"
            "Определи формат. Поле: format. Одно из значений:\n"
            "- \"sermon\" — последовательная проповедь одного говорящего\n"
            "- \"lecture\" — семинар, лекция, обучение\n"
            "- \"qa\" — сессия вопросов и ответов (слышна явная структура смены вопросов)\n"
            "- \"interview\" — интервью\n"
            "- \"discussion\" — панель или разговор нескольких участников\n"
            "- \"other\" — формат неясен\n"
            "\"qa\" — только если реально слышна структура смены вопросов. Не угадывать.\n"
            "\n"
            "ЗАДАЧА 2 — ОПИСАНИЕ (main_topic):\n"
            "2–3 предложения, суммарно не более 300 символов. Живой язык, русский.\n"
            "Первое — главный вопрос или провокация материала. Второе — что автор открывает или утверждает.\n"
            "main_topic — это анонс для читателя: о чём этот материал. НЕ аналитика, НЕ оценка — просто ёмкая суть.\n"
            "analysis_summary позже углубится в технику и структуру — main_topic этого не делает.\n"
            "СТИЛЬ: если тема позволяет — добавь лёгкую интригу, оставь вопрос открытым, заинтересуй читателя. Без фанатизма, аккуратно.\n"
            "ФОРМАТИРОВАНИЕ: выдели **жирным** 2–4 ключевых слова или коротких фразы (самую суть). Используй **слово** прямо в тексте.\n"
            "ЭМОДЗИ: органично вставляй 2–4 тематических эмодзи прямо в текст по смыслу. Ставь эмодзи ПЕРЕД знаком препинания, не после: '...перед Богом 🕊️?' а не '...перед Богом? 🕊️'\n"
            "\n"
            "ЗАДАЧА 3 — ТАЙМКОДЫ:\n"
            "Сначала определи формат материала через поле format: sermon / lecture / qa / interview / discussion / other.\n"
            "\n"
            "Дальше правила таймкодов зависят от format.\n"
            "\n"
            "Если format = sermon:\n"
            "- 8–14 таймкодов\n"
            "- только крупные смысловые блоки\n"
            "- не дробить слишком мелко\n"
            "- ОБЯЗАТЕЛЬНО: покрывай всё видео до конца; если между последним таймкодом и концом больше 5 минут — значит финал пропущен, добавь его\n"
            "- равномерно распределяй таймкоды по всей длительности материала\n"
            "\n"
            "Если format = lecture:\n"
            "- 8–14 таймкодов\n"
            "- выделять основные тезисы, переходы темы и ключевые этапы объяснения\n"
            "- ОБЯЗАТЕЛЬНО: покрывай всё видео до конца; если между последним таймкодом и концом больше 5 минут — значит финал пропущен, добавь его\n"
            "- равномерно распределяй таймкоды по всей длительности материала\n"
            "\n"
            "Если format = qa:\n"
            "- 10–18 таймкодов\n"
            "- КАЖДЫЙ таймкод = один конкретный вопрос\n"
            "- каждый таймкод должен отмечать начало нового вопроса\n"
            "- заголовок ВСЕГДА в вопросительной форме\n"
            "- если один ответ длинный, но вопрос тот же, не дробить без необходимости\n"
            "- ЗАПРЕЩЕНО: 'Продолжение ответа', 'Дополнение к теме', 'второй вопрос'\n"
            "- хорошо: 'Нужно ли верить в доктрины благодати для спасения?'\n"
            "- хорошо: 'Как отличить ложных учителей?'\n"
            "- хорошо: 'Какова роль женщин в церкви?'\n"
            "- ОБЯЗАТЕЛЬНО: покрывай вопросы до конца материала; если между последним таймкодом и концом больше 8 минут и разговор продолжается — значит поздние вопросы пропущены, добавь их\n"
            "\n"
            "Если format = interview:\n"
            "- 8–14 таймкодов\n"
            "- выделяй смену темы, новые сюжетные узлы и важные вопросы собеседника\n"
            "\n"
            "Если format = discussion:\n"
            "- 8–14 таймкодов\n"
            "- выделяй смену предмета обсуждения и новые смысловые узлы разговора\n"
            "\n"
            "Если format = other:\n"
            "- 6–10 таймкодов\n"
            "- используй обычную смысловую разбивку\n"
            "\n"
            "Общие правила для всех форматов:\n"
            "- первый таймкод всегда 0:00\n"
            f"- КРИТИЧЕСКИ ВАЖНО: длительность материала = {duration_str}. "
            f"Все таймкоды ОБЯЗАНЫ быть строго в пределах 0:00 — {duration_str}.\n"
            f"- КРИТИЧЕСКИ ВАЖНО: последний таймкод должен быть не раньше чем за 5 минут до конца ({duration_str}). Не обрывай на середине материала.\n"
            f"- КРИТИЧЕСКИ ВАЖНО: таймкоды должны равномерно покрывать ВСЁ видео от начала до конца, а не только первые 10–15 минут.\n"
            f"- формат времени: {'M:SS' if duration < 3600 else 'H:MM:SS'} "
            f"({'видео короче часа — НЕТ часового компонента, пиши 42:12, а НЕ 1:42:12' if duration < 3600 else 'видео длиннее часа — формат H:MM:SS'})\n"
            f"- ЗАПРЕЩЕНО писать таймкоды вроде 1:42:12 для видео длительностью {duration_str} — это выходит за пределы видео\n"
            "- строго по возрастанию\n"
            "- описание должно быть коротким и естественным\n"
            "- не делать таймкоды слишком частыми без реальной смены темы\n"
            "- ЗАПРЕЩЕНО: равномерные интервалы по 5/10/15 минут — это механическая нарезка, не смысловая\n"
            "- таймкоды ставятся ТОЛЬКО на смену темы/вопроса/аргумента\n"
            "- разница между соседними таймкодами может быть 2 минуты или 25 минут — зависит от содержания, НЕ от часов\n"
            "- если не уверен, лучше меньше, но точнее\n"
            "\n"
            "ЗАДАЧА 4 — ХЭШТЕГИ:\n"
            "4-7 хэштегов на русском, СлитноСЗаглавной (CamelCase), БЕЗ символа #, массив строк.\n"
            "Пример: [\"ИстиннаяВера\", \"Покаяние\", \"ДжонМакАртур\"]\n"
            "\n"
            "ЗАДАЧА 5 — АНАЛИТИКА (три плоских поля):\n"
            "\n"
            "КРИТИЧЕСКИ ВАЖНО:\n"
            "- main_topic = краткий смысловой центр материала.\n"
            "- analysis_summary = аналитический разбор, НЕ пересказ темы.\n"
            "- argument_arc = карта движения мысли, НЕ summary.\n"
            "- key_categories = ключевые опоры материала, НЕ словарь терминов.\n"
            "Эти поля ОБЯЗАНЫ отличаться друг от друга по функции и содержанию.\n"
            "\n"
            "СТИЛЬ:\n"
            "Пиши живо, лично и плотно.\n"
            "Не академически и не энциклопедически.\n"
            "Не используй канцелярские формулы: 'автор рассматривает', 'данный материал посвящён', 'в проповеди говорится о'.\n"
            "Предпочитай наблюдение, диагноз, напряжение, контраст, духовную остроту.\n"
            "Если известно имя автора (real_author) — используй его естественно и уместно.\n"
            "Если имя автора неизвестно — пиши без имени, но всё равно живо.\n"
            "\n"
            "--- analysis_summary ---\n"
            "\n"
            "analysis_summary — это НЕ краткий пересказ темы и НЕ повтор main_topic.\n"
            "Это аналитический разбор: как автор действует, где главный нерв, какой духовный конфликт ставится и почему выступление работает.\n"
            "\n"
            "Если format = sermon или lecture:\n"
            "4–6 плотных предложений. Покажи:\n"
            "1. В чём главный нерв проповеди.\n"
            "2. Какой духовный конфликт автор ставит перед слушателем.\n"
            "3. Каким тоном и методом воздействует: обличение, контраст, пастырская тревога, давление на совесть, разрушение ложных опор.\n"
            "4. В чём сила именно этого выступления и почему оно работает.\n"
            "\n"
            "Если format = qa:\n"
            "4–6 плотных предложений. Покажи:\n"
            "1. Какие темы и тревоги стоят за вопросами.\n"
            "2. Как автор обычно отвечает: доктринально, пастырски, обличительно, корректируя ложные предпосылки.\n"
            "3. Какой общий духовный нерв объединяет разные ответы.\n"
            "4. Что эта Q&A вскрывает о мышлении и состоянии аудитории.\n"
            "5. В чём сила именно этой сессии.\n"
            "Не пересказывай каждый вопрос по отдельности. Показывай, как множество вопросов складываются в одну духовную картину.\n"
            "\n"
            "Если format = interview или discussion:\n"
            "4–6 предложений. Покажи:\n"
            "1. О чём на самом деле разговор, помимо внешней темы.\n"
            "2. Где собеседники совпадают, а где появляется напряжение.\n"
            "3. Какой интеллектуальный или духовный нерв проходит через беседу.\n"
            "4. За счёт чего разговор удерживает внимание и к чему ведёт.\n"
            "\n"
            "Если format = other:\n"
            "3–5 плотных предложений. Покажи главный нерв, форму воздействия и смысловой центр.\n"
            "\n"
            "--- argument_arc ---\n"
            "\n"
            "argument_arc — это НЕ пересказ материала по порядку.\n"
            "Это карта движения мысли: от какого напряжения начинается материал, через какие ступени проходит, где переломный момент и к чему всё ведёт.\n"
            "\n"
            "Если format = sermon или lecture:\n"
            "4–6 предложений. Покажи:\n"
            "1. С какого напряжения, вопроса или проблемы начинается проповедь.\n"
            "2. Через какие ступени развивается аргумент.\n"
            "3. Где происходит переломный момент.\n"
            "4. К какому выводу, обличению или призыву всё приходит.\n"
            "5. Почему такая структура воздействует на слушателя.\n"
            "Не своди к 'сначала..., затем..., потом...'. Нужна архитектура, а не хронология.\n"
            "Последнее предложение — итог, вызов или разрешение напряжения.\n"
            "\n"
            "Если format = qa:\n"
            "4–6 предложений. Это НЕ линейная структура проповеди, а карта движения по вопросам. Покажи:\n"
            "1. С каких вопросов начинается сессия и какой тон они задают.\n"
            "2. Какие вопросы оказываются центральными, а какие — производными.\n"
            "3. Как ответы группируются вокруг общих тем.\n"
            "4. Где происходит углубление: от частного вопроса к более глубокой проблеме сердца, веры, покаяния.\n"
            "5. К какому общему выводу или предупреждению сводится вся сессия.\n"
            "Не перечисляй вопросы подряд. Показывай внутреннюю логику движения между ними.\n"
            "\n"
            "Если format = interview или discussion:\n"
            "4–6 предложений. Покажи:\n"
            "1. Как разговор входит в тему.\n"
            "2. Какие смысловые узлы поднимаются.\n"
            "3. Где появляется напряжение, уточнение или смена рамки.\n"
            "4. Как беседа движется к общему выводу или остаётся открытой.\n"
            "5. За счёт чего разговор держит смысловую динамику.\n"
            "\n"
            "Если format = other:\n"
            "3–5 предложений. Покажи как материал движется от начала к финалу и что является его поворотной точкой.\n"
            "\n"
            "--- key_categories ---\n"
            "\n"
            "key_categories — это НЕ просто список терминов.\n"
            "Это список ключевых опор материала: понятий, контрастов, образов, болевых точек, формул автора, практических осей.\n"
            "Формат: массив строк \"Категория — краткое плотное объяснение\"\n"
            "6–10 категорий. Каждое объяснение — 1 короткое плотное предложение.\n"
            "Не словарное определение, а объяснение того, как эта категория работает в материале.\n"
            "Не дублируй блок terms_data.\n"
            "\n"
            "Если format = sermon или lecture:\n"
            "Включай: ключевые понятия, контрасты, образы и метафоры, болевые точки, формулы автора, практические оси.\n"
            "Примеры: 'Ложная уверенность — уверенность без нового сердца', 'Иисусленд — развлекательная подмена церкви'\n"
            "\n"
            "Если format = qa:\n"
            "Включай: повторяющиеся темы вопросов, скрытые духовные проблемы, типичные контрасты в ответах, центральные болевые точки аудитории.\n"
            "Примеры: 'Ложная уверенность — скрытый фон многих вопросов', 'Вопрос как симптом — аудитория обнаруживает духовную путаницу'\n"
            "\n"
            "Если format = interview или discussion:\n"
            "Включай: смысловые узлы беседы, линии согласия и напряжения, ключевые противопоставления, образы и формулы.\n"
            "\n"
            "Если format = other:\n"
            "Выбирай самые сильные смысловые опоры. Избегай слишком общих и пустых категорий.\n"
            "\n"
            "ЗАПРЕЩЕНО:\n"
            "- пустые энциклопедические категории без связи с материалом;\n"
            "- повторять terms_data;\n"
            "- делать категории слишком общими и бессодержательными.\n"
            "\n"
            "ЗАДАЧА 6 — ВОПРОСЫ:\n"
            "10–12 вопросов, массив строк. Пометить каждый:\n"
            "🟢 — понять и применить (ясные, конкретные, связаны с практикой)\n"
            "🔵 — углубиться (вскрывают предпосылки, создают интеллектуальное напряжение)\n"
            "Примерно 6–7 зелёных, 4–5 синих. Только открытые вопросы. Разные аспекты.\n"
            "Пример: [\"🟢 Как этот аргумент меняет твоё понимание молитвы?\", \"🔵 Какую предпосылку проповедник считает очевидной?\"]\n"
            "Если нет уверенности в содержании — вернуть [].\n"
            "\n"
            "ЗАДАЧА 7 — БЛОК «ТЕРМИНЫ»:\n"
            "Сформируй блок terms_data.\n"
            "Задача блока — помочь думающему читателю понять ключевые понятия, важные тексты Писания,\n"
            "аккуратные переводческие наблюдения и осторожные словарные пояснения.\n"
            "Это не академическая статья и не псевдоучёный словарь. Это ясный, живой вспомогательный разбор.\n"
            "\n"
            "Блок общий для всех форматов: sermon, lecture, qa, interview, discussion, other.\n"
            "\n"
            "Если format = sermon или lecture:\n"
            "- можно давать более насыщенную структуру понятий, текстов и наблюдений\n"
            "\n"
            "Если format = qa, interview или discussion:\n"
            "- включай только то, что реально помогает понять ход разговора и ответы\n"
            "- не раздувай блок искусственно\n"
            "- лучше мало, но точно и полезно\n"
            "\n"
            "ПРАВИЛО ФАКТИЧНОСТИ:\n"
            "- только то, что реально звучит в материале\n"
            "- не выдумывай греческие / еврейские слова\n"
            "- не изображай точные цитаты из BDAG, HALOT и т.п.\n"
            "- 'по BDAG: ...' — допустимо для коротких осторожных глоссов\n"
            "- если раздел не нужен — вернуть []\n"
            "- лучше меньше, но каждый элемент полезен\n"
            "\n"
            "ПРАВИЛО АККУРАТНОСТИ ПРИ ПЕРЕВОДАХ:\n"
            "- переводами занимались серьёзные учёные. Задача не унижать переводы, а помочь заметить оттенки.\n"
            "- не писать 'только оригинал открывает смысл', 'переводы здесь слабы', 'греческий намного глубже'\n"
            "- нейтральное, фактичное наблюдение: что именно различается и какой оттенок это создаёт\n"
            "- NA28 — это греческий текст Нового Завета, не перевод. Обозначать явно.\n"
            "\n"
            "terms_data состоит из четырёх массивов строк:\n"
            "\n"
            f"ВАЖНО ДЛЯ ТАЙМКОДОВ В TERMS_DATA: длительность материала = {duration_str}. "
            f"Формат: {'M:SS (без часового компонента — пиши 42:12, а НЕ 1:42:12)' if duration < 3600 else 'H:MM:SS'}. "
            f"Все таймкоды строго в пределах 0:00 — {duration_str}.\n"
            "\n"
            "1. concepts — ключевые понятия\n"
            "Каждая строка: \"Понятие || краткое ясное объяснение || почему важно именно в этом материале || таймкоды\"\n"
            "ВАЖНО: concepts НЕ должны повторять key_categories.\n"
            "key_categories — общие идеи и тезисы проповеди.\n"
            "concepts — конкретные термины и определения, которые автор ОБЪЯСНЯЕТ, ПЕРЕОПРЕДЕЛЯЕТ или КРИТИКУЕТ в материале.\n"
            "Объяснение: 1 предложение, до 100 символов. Как именно автор раскрывает этот термин — не словарное определение.\n"
            "Почему важно: 1 предложение. Роль понятия в логике ЭТОГО материала, не в общем.\n"
            "Таймкоды: ТОЛЬКО 1–3 ключевых момента. НЕ все упоминания — только где тема начинается и где ключевой аргумент.\n"
            "Для sermon/lecture: 5–10 строк. Для qa/interview/discussion: 3–6 строк.\n"
            "Пример: \"Предопределение || Божий замысел о спасении по Его воле, не по человеческому решению || Панелисты отличают активный замысел Бога от пассивного предвидения || 18:18, 20:00\"\n"
            "\n"
            "2. scripture — важные места Писания\n"
            "Каждая строка: \"Ссылка || как ИМЕННО проповедник использует этот текст в аргументе || ключевая фраза если важна || таймкоды\"\n"
            "Показывай НЕ общеизвестное значение стиха, а КАК ИМЕННО проповедник его применяет:\n"
            "в каком аргументе, против какого заблуждения, с каким выводом.\n"
            "Таймкоды: максимум 2–3 момента.\n"
            "Для sermon/lecture: 3–8 строк. Для qa/interview/discussion: 1–5 строк.\n"
            "Пример: \"Иоанна 10:27–28 || Главный текст для понятия знания как личного отношения, а не интеллектуального факта || Мои овцы слышат голос Мой, и Я знаю их || 18:38, 19:01\"\n"
            "\n"
            "3. translations — точечные переводческие наблюдения\n"
            "Только если различие реально помогает заметить важный оттенок.\n"
            "НЕ сравнивать ради сравнения. НЕ занимать место наблюдениями, которые ничего не открывают.\n"
            "Переводы: Синодальный, Кассиана, НРП, Кулакова (русские); ESV, LSB, NASB95, KJV (английские).\n"
            "НУМЕРАЦИЯ ПСАЛМОВ: основная нумерация — русская (по Септуагинте/LXX, как в Синодальном переводе). "
            "Русский Псалом = английский Psalm + 1 (для Пс. 9–147). Например: английский Psalm 103 = русский Псалом 102. "
            "Если нужно показать английский вариант или сравнить переводы — пиши оба: Псалом 102 (англ. Psalm 103).\n"
            "ВАЖНО: если приводятся английские варианты перевода — обязательно рядом указывай русский эквивалент в скобках.\n"
            "Пример: ESV: lawlessness (беззаконие), LSB: lawlessness (беззаконие)\n"
            "Формат каждой строки:\n"
            "\"Ссылка || фокус-слово или выражение || Перевод1: вариант; Перевод2: вариант || Перевод3: вариант (рус. эквивалент); Перевод4: вариант (рус. эквивалент) || нейтральное наблюдение об оттенке || таймкоды\"\n"
            "Наблюдение должно быть спокойным и фактичным. Не ставить один перевод выше другого без явного основания.\n"
            "Обычно 0–3 строк.\n"
            "Пример: \"Притч 5:1 || внимай || Синодальный: внимай; Кассиана: внемли || ESV: be attentive (внимать); LSB: give attention (уделить внимание) || Английские варианты чуть заметнее подчёркивают намеренное, направленное внимание || 6:27\"\n"
            "\n"
            "4. lexicon_notes — осторожные словарные пояснения\n"
            "Всегда привязывай словарную заметку к конкретному месту Писания и к русскому слову или фразе, которую обсуждает автор.\n"
            "Не возвращай оригинальное слово само по себе без контекста.\n"
            "ВАЖНО: если приводится оригинальное греческое или еврейское слово — рядом обязательно давай краткий русский перевод или смысл в скобках.\n"
            "Пример: ἀνομία (беззаконие), חָכְמָה (мудрость)\n"
            "\n"
            "Каждая строка формата:\n"
            "\"Ссылка на текст || русское ключевое слово/фраза || оригинальное слово || источник оригинала || словарное пояснение || почему это важно здесь || таймкоды || как автор употребляет это слово в материале\"\n"
            "\n"
            "8-е поле «как автор употребляет это слово в материале» — ОБЯЗАТЕЛЬНОЕ:\n"
            "Короткая фраза (1–2 предложения): как именно автор использует это слово или понятие,\n"
            "на каком языке он его произносит (на языке оригинала, в переводе, своими словами),\n"
            "и к какой мысли или аргументу оно привязано в его речи.\n"
            "Если автор явно не называет это слово, но рассуждает о понятии, стоящем за ним —\n"
            "укажи это: «автор не называет слово, но разбирает понятие...».\n"
            "\n"
            "Для Нового Завета пример:\n"
            "\"Галатам 6:14 || хвалиться || καυχάομαι || NA28 (греческий текст Нового Завета) || по BDAG: хвалиться, славиться, ставить предметом похвалы || это помогает точнее увидеть, что речь идёт не просто о словах, а о том, чем человек сознательно дорожит и что считает своей славой || 17:55 || Автор цитирует стих по-русски («да не буду хвалиться»), затем объясняет, что настоящая слава христианина — только крест, противопоставляя это самопиару и религиозному достижению\"\n"
            "\n"
            "Для Ветхого Завета пример:\n"
            "\"Притчи 1:7 || мудрость || חָכְמָה || Еврейский текст Ветхого Завета || по HALOT/BDB: мудрость, искусность, разумное умение действовать || это помогает увидеть, что речь идёт не только о знании, но и о практической способности жить правильно || 12:40, 18:10 || Автор произносит еврейское слово вслух и объясняет, что «мудрость» здесь — не IQ, а умение жить: навык правильного действия в реальных ситуациях\"\n"
            "\n"
            "Обычно 0–2 строк.\n"
            "Строгие ограничения:\n"
            "- только строки, никаких вложенных объектов\n"
            "- не дублировать одно и то же в разных разделах\n"
            "- если раздел пустой — вернуть []\n"
            "- не перегружать: качество важнее количества\n"
            "\n"
            "Минимум терминов для sermon/lecture длиннее 30 минут:\n"
            "- concepts: минимум 5\n"
            "- scripture: минимум 4\n"
            "- translations: минимум 1 (если есть разбор переводов)\n"
            "- lexicon_notes: минимум 1 (если есть разбор оригинальных слов)\n"
            "\n"
            "ЧАСТЬ 9 — ГЕРМЕНЕВТИЧЕСКИЙ МЕТОД (hermeneutic_method):\n"
            "Определи метод проповедника. Одно значение из списка:\n"
            "- \"expository\"            — экспозиция текст-за-текстом, структура от отрывка\n"
            "- \"topical\"               — тематическая, тексты подбираются к теме\n"
            "- \"narrative\"             — нарративная экспозиция, история как форма\n"
            "- \"typological\"           — типологическое чтение (тени → исполнение в Христе)\n"
            "- \"redemptive_historical\" — искупительно-историческая линия через Библию\n"
            "- \"catechetical\"          — по вопросам катехизиса или символа веры\n"
            "- \"apologetic\"            — защита веры против возражений\n"
            "- \"evangelistic\"          — прямой призыв к обращению\n"
            "- \"practical\"             — практический, минимум формальной экзегезы\n"
            "- \"mixed\"                 — комбинация подходов\n"
            "\n"
            "ВАЖНО: поле влияет на конспект:\n"
            "- expository → допустимо 2-3 scripture-block на раздел\n"
            "- topical/practical → 0-1 scripture-block на раздел\n"
            "\n"
            "ЧАСТЬ 8 — ПОДСКАЗКИ ДЛЯ ТРАНСКРИПЦИИ (whisper_hints):\n"
            "Составь список из 30-80 слов и коротких фраз которые реально звучат "
            "в этом материале и которые автоматическая транскрипция может исказить.\n"
            "\n"
            "ОБЯЗАТЕЛЬНО включай:\n"
            "1. Имена богословов и проповедников — ТОЧНОЕ написание:\n"
            "   Реформаторы: МакАртур, Сперджен, Кальвин, Лютер, Цвингли, Беза, Нокс\n"
            "   Пуритане: Оуэн, Эдвардс, Уотсон, Баньян, Чарнок, Мантон, Гудвин,\n"
            "   Берроуз, Сиббс, Перкинс, Бакстер, Брукс, Гурналл, Мэтью Генри\n"
            "   Баптисты: Кич, Гилл, Фуллер, Кэри, Бойс, Дэгг, Пинк,\n"
            "   Пайпер, Бокам, Девер, Уайт, Аскол, Мбеве, Платт, Бегг, Мастерс\n"
            "   Пресвитериане: Ходж, Варфилд, Беркхоф, Бавинк, Кайпер, Спрол,\n"
            "   Фрейм, Летам, Фергюсон, Дункан, ДеЯнг, Хортон, Бики, Пойтресс, Ортланд\n"
            "   Современные: Карсон, Келлер, Вошер, Чандлер, Ллойд-Джонс, Шрайнер,\n"
            "   Клауни, Голдсуорти, Грейданус, Абнер Чау\n"
            "   Историки: Маллер, Трумэн, Пеликан, МакГрат\n"
            "   Древние: Августин, Афанасий, Ириней, Златоуст, Ансельм,\n"
            "   Турретин, а Бракел, Витсиус, Эймс\n"
            "   Русские: Прокопенко, Коломийцев, Павлов, Проханов, Пашков,\n"
            "   Воронин, Фетлер, Винс\n"
            "   Библейские имена: Малахия, Навуходоносор, Неемия, Ездра, "
            "Мелхиседек, Варавва, Никодим\n"
            "2. Архаичные и церковнославянские слова:\n"
            "   глас (не глаз), закланный (не заклонный), заклан (не заклон), "
            "грядущий, паче, инаго, сие, днесь, благоволение\n"
            "3. Богословские термины которые транскрипция может исказить:\n"
            "   сокрушение, благодать, оправдание, освящение, нечестие, благочестие, "
            "лжеучитель, лицемерие, богохульство, идолопоклонство, предопределение\n"
            "4. Слова где звонкие/глухие на конце путаются:\n"
            "   глас не глаз, плод не плот, Бог не бок, кровь не крофь, суд не сут\n"
            "5. Английские слова если автор их произносит:\n"
            "   lawlessness, iniquity, grace, sin, repentance, ESV, NASB, KJV, LSB\n"
            "6. Греческие/еврейские слова если звучат:\n"
            "   anomia, hamartia, chesed, shalom, метаноя, агапе\n"
            "7. Короткие фразы-паттерны (5-10 штук): глас Божий, закланный Агнец, суд Божий\n"
            "\n"
            "ВАЖНО: включай ТОЛЬКО слова которые РЕАЛЬНО звучат в этом материале.\n"
            "Не выдумывай слова которых нет в проповеди.\n"
            "\n"
            "ФОРМАТ ОТВЕТА — АБСОЛЮТНЫЕ ПРАВИЛА:\n"
            "1. Только чистый JSON — никаких ```json, комментариев, текста до/после.\n"
            "2. Только эти поля: real_author, real_title, real_event, format, main_topic, timestamps, hashtags, analysis_summary, argument_arc, key_categories, questions, terms_data, whisper_hints, hermeneutic_method.\n"
            "3. terms_data — объект с четырьмя ключами, каждый — массив строк.\n"
            "4. whisper_hints — плоский массив строк.\n"
            "5. Никаких лишних полей вне этого списка.\n"
            "{\n"
            '  "real_author": "Джон МакАртур",\n'
            '  "real_title": "Признаки истинного спасения",\n'
            '  "format": "sermon",\n'
            '  "real_event": "Shepherds Conference",\n'
            '  "hermeneutic_method": "expository",\n'
            '  "main_topic": "МакАртур задаёт вопрос: а вдруг ты не спасён? Он разбирает признаки ложной веры.",\n'
            '  "timestamps": [{"time": "0:00", "topic": "Вступление и главный вопрос"}],\n'
            '  "hashtags": ["ИстиннаяВера", "Покаяние"],\n'
            '  "analysis_summary": "МакАртур бьёт в главный нерв: миллионы уверены в спасении, которого у них нет. Он не просто объясняет доктрину — он ставит слушателя перед диагнозом. Конфликт проповеди: исповедание без возрождения против подлинной веры, меняющей жизнь. Тон — хирургический: ни паники, ни мягкости, только последовательное разрушение ложных опор. Сила проповеди в том, что богословская точность соединяется с пастырской срочностью — слушатель не может остаться нейтральным.",\n'
            '  "argument_arc": "Проповедь начинается с провокации: а вдруг твоя уверенность в спасении — самообман? МакАртур разрушает стандартные критерии — молитву, хождение в церковь, эмоциональный опыт — и показывает их недостаточность. Переломный момент: переход от внешней религиозности к вопросу о новом сердце. Аргумент движется от диагностики ложной веры к библейским признакам подлинного обращения. Финал не оставляет выхода в нейтральность: либо проверь себя, либо признай.",\n'
            '  "key_categories": ["Ложная уверенность — исповедание без возрождения, самый опасный вид самообмана", "Диагностика спасения — проверка не по молитве в прошлом, а по плодам жизни сейчас", "Хирургическая проповедь — стиль без мягкости и без паники, только точный духовный диагноз"],\n'
            '  "questions": ["🟢 Как аргумент МакАртура меняет твоё понимание покаяния?"],\n'
            '  "terms_data": {\n'
            '    "concepts": ["Ложная вера || Внешнее исповедание без внутреннего преображения || Контекст: отправная точка диагностики || 0:00, 4:30"],\n'
            '    "scripture": ["Матфея 7:21-23 || Главный текст диагностики — кто войдёт в Царство || Ключевая фраза: «Не всякий говорящий Мне...» || 3:15"],\n'
            '    "translations": ["Матфея 7:23 || беззаконие / lawlessness || Синодальный: беззаконие || ESV: lawlessness; LSB: lawlessness || Формулировки близки, но ESV и LSB делают чуть заметнее принципиальный характер нарушения || 5:00"],\n'
            '    "lexicon_notes": ["Матфея 7:23 || беззаконие || ἀνομία || NA28 (греческий текст Нового Завета) || по BDAG: принципиальное отвержение закона, а не просто ошибки || помогает точнее увидеть, что МакАртур описывает не случайные грехи, а принципиальное нежелание подчиняться Богу || 5:10"]\n'
            '  },\n'
            '  "whisper_hints": ["глас", "глас Божий", "закланный", "закланный Агнец",'
            ' "МакАртур", "Сперджен", "anomia", "lawlessness",'
            ' "сокрушение", "Малахия", "суд Божий", "нечестие", "благодать"]\n'
            "}"
        )

        # Для каждого клиента загружаем файл отдельно (файлы не переносятся между ключами)
        last_err = None
        response = None
        used_client = None
        used_audio_part = None
        success = False
        for client in GEMINI_CLIENTS:
            if success:
                break
            for attempt in range(3):
                try:
                    audio_part, used_client = await upload_to_client(client)
                    used_audio_part = audio_part
                    # Retry при ReadTimeout — Hiddify может разорвать idle-соединение
                    # пока Gemini обрабатывает длинное аудио
                    for _gen_attempt in range(3):
                        try:
                            response = await asyncio.wait_for(
                                client.aio.models.generate_content(
                                    model=GEMINI_MODEL,
                                    contents=[audio_part, prompt],
                                    config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=65536),
                                ),
                                timeout=960.0,
                            )
                            break  # успех
                        except (asyncio.TimeoutError, Exception) as _gen_err:
                            _is_read_timeout = "ReadTimeout" in type(_gen_err).__name__ or isinstance(_gen_err, asyncio.TimeoutError)
                            if _is_read_timeout and _gen_attempt < 2:
                                _wait = 30 * (_gen_attempt + 1)
                                logger.warning(f"Gemini ReadTimeout, повтор {_gen_attempt + 1}/3 через {_wait}с...")
                                await asyncio.sleep(_wait)
                                continue
                            raise
                    success = True
                    break
                except Exception as e:
                    if is_quota_error(e) or is_overload_error(e):
                        logger.warning(f"Gemini {'квота' if is_quota_error(e) else '503/disconnect'}, пробую следующий ключ...")
                        if file_size_mb > 20:
                            try:
                                if audio_part is not None:
                                    asyncio.ensure_future(client.aio.files.delete(name=audio_part.name))
                            except Exception:
                                pass
                        last_err = e
                        break  # следующий клиент
                    raise  # неизвестная ошибка — пробрасываем
        if response is None:
            raise last_err or RuntimeError("Все Gemini-клиенты недоступны")

        # Bug #2: response.text can be None (thinking model) or raise ValueError (safety filter)
        raw_text = None
        try:
            raw_text = response.text
        except ValueError:
            pass
        if not raw_text:
            # Collect text from parts manually, skipping thought parts
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, "thought") and part.thought:
                        continue
                    if hasattr(part, "text") and part.text:
                        raw_text = part.text
                        break
        if not raw_text:
            logger.warning("Gemini вернул пустой ответ (thinking-only или safety filter)")
            return None, used_client, used_audio_part
        answer = raw_text.strip()
        logger.info(f"Gemini ответ (первые 2000 символов): {answer[:2000]}")

        # Файл НЕ удаляем здесь — он нужен для create_telegraph_synopsis
        return _parse_gemini_response(answer, duration), used_client, used_audio_part
    except Exception as e:
        # Намеренно не логируем str(e) целиком — Gemini SDK может включать API-ключ в PermissionDenied
        err_type = type(e).__name__
        err_msg  = str(e)
        # Фильтр маскирует секреты, но на всякий случай урезаем до 200 символов
        logger.error(f"Ошибка Gemini AI: {err_type}: {err_msg[:200]}")
        if used_audio_part and hasattr(used_audio_part, 'name') and used_client:
            try:
                asyncio.ensure_future(used_client.aio.files.delete(name=used_audio_part.name))
            except Exception:
                pass
        return None, None, None


BAD_META_PATTERNS = [
    # Cocoon / AI / meta-boilerplate — только явный мусор
    r"cocoon\s*ai\s*summary",
    r"\bcocoon\b",
    r"content\s*summary",
    r"ai\s+summary",
    r"summary\s+by",
    r"auto.?generated\s+summary",
    r"auto.?generated",
    r"^generated\s+by",
    r"^powered\s+by",
    r"^content\s+by",
    r"\bbyline\b",
    # Дата-подписи вида "March 15 at 18:55"
    r"[\s\u2022\-]*\w+\s+\d{1,2}\s+at\s+\d{1,2}:\d{2}",
    r"march\s+\d{1,2}\s+at\s+\d{1,2}:\d{2}",
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},?\s*\d{4}",
    # Русские мета-фразы — только строгие якоря
    r"^статья\s+(содержит|описывает|рассматривает|анализирует|представляет)",
    r"^статья\s+представляет\s+собой",
    r"^this\s+(video|article|sermon|lecture)\s+",
    r"^в\s+этом\s+(видео|ролике)\s+",
    r"текст\s+подготовлен\s+с\s+помощью",
    r"подготовлен[оа]?\s+с\s+помощью\s+(gemini|ai|ии)",
]

# Паттерны для инлайн-замены (встречаются внутри строки, не только в начале)
_INLINE_SCRUB_PATTERNS = [
    # Только строки-маркеры которые встречаются внутри текста как вставки
    r"cocoon\s*ai\s*summary",
    r"\bcocoon\b",
    r"content\s*summary",
    r"ai\s+summary",
    r"summary by[^.]*",
    r"auto.?generated[^.]*",
    r"generated by[^.]*",
    r"powered by[^.]*",
    r"\bbyline\b",
    r"\w+\s+\d{1,2}\s+at\s+\d{1,2}:\d{2}",    # "March 7 at 5:11"
    r"текст\s+подготовлен\s+с\s+помощью[^.]*",
    r"подготовлен[оа]?\s+с\s+помощью\s+gemini\s*ai[^.]*",
]


def _scrub_inline(text: str) -> str:
    """Удаляет мусорные AI/meta-фразы встречающиеся внутри строки.
    НЕ трогает пунктуацию, края строки и смысловые пробелы —
    чтобы не ломать богословский текст, scripture, переводческие блоки."""
    if not text:
        return text
    for pat in _INLINE_SCRUB_PATTERNS:
        text = re.sub(pat, "", text, flags=re.IGNORECASE)
    # Схлопываем только множественные пробелы/табы внутри строки
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text

def _clean_meta_line(s: str) -> str:
    """Берёт первую осмысленную строку, отфильтровывая мусорные метки платформ."""
    s = (s or "").strip()
    if not s:
        return ""
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    for ln in lines:
        low = ln.lower()
        if any(re.search(p, low) for p in BAD_META_PATTERNS):
            continue
        return ln
    return ""

def _clean_field(s: str) -> str:
    """Очищает произвольное текстовое поле — возвращает пустую строку если мусор."""
    s = (s or "").strip()
    if not s:
        return ""
    if any(re.search(p, s.lower(), re.IGNORECASE) for p in BAD_META_PATTERNS):
        return ""
    if s[-1] not in '.!?…':
        s = s + '.'
    return s

def _clean_list(lst: list) -> list:
    """Фильтрует список строк, убирая мусорные элементы."""
    return [_clean_field(str(item)) for item in (lst or []) if _clean_field(str(item))]


def _strip_meta_lines(text: str) -> str:
    """Удаляет строки, соответствующие BAD_META_PATTERNS, возвращает чистый текст."""
    if not text:
        return ""
    clean_lines = []
    for ln in text.splitlines():
        stripped = ln.strip()
        if not stripped:
            clean_lines.append(ln)
            continue
        if any(re.search(p, stripped, re.IGNORECASE) for p in BAD_META_PATTERNS):
            continue
        clean_lines.append(ln)
    return "\n".join(clean_lines).strip()


def _has_dirty_meta(text: str) -> bool:
    """True если в тексте есть хотя бы одна мусорная строка."""
    for ln in (text or "").splitlines():
        if ln.strip() and any(re.search(p, ln.strip(), re.IGNORECASE) for p in BAD_META_PATTERNS):
            return True
    return False


def is_meta_garbage(s: str) -> bool:
    """True если строка целиком является мусором (summary/byline/cocoon и т.п.)."""
    s = (s or "").strip()
    if not s:
        return True
    return any(re.search(p, s, re.IGNORECASE) for p in BAD_META_PATTERNS)


def normalize_author_name(s: str) -> str:
    """Нормализует имя автора: убирает мусор, длинные тире → дефис, схлопывает пробелы."""
    s = _strip_meta_lines((s or "").strip())
    if not s or is_meta_garbage(s):
        return ""
    s = re.sub(r"[—–]", "-", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    s = s.strip("-").strip()
    return s


def normalize_title_text(s: str) -> str:
    """Нормализует заголовок: убирает ведущий номер серии, имя автора в скобках, лишние разделители."""
    s = _strip_meta_lines((s or "").strip())
    if not s or is_meta_garbage(s):
        return ""

    s = re.sub(r"^\d+\s*[|:]\s*", "", s)
    s = re.sub(r"^№\d+\s*[|:\-]?\s*", "", s)
    s = re.sub(r"^[Ss]\d+[Ee]\d+\s*[|:\-]?\s*", "", s)
    s = re.sub(r"^[Ee]pisode\s+\d+\s*[|:\-]?\s*", "", s, flags=re.IGNORECASE)

    m = re.search(r"\(([^)]{2,50})\)\s*$", s)
    if m:
        candidate = m.group(1).strip()
        words = candidate.split()
        meaningful_words = [w for w in words if w]
        word_count = len(meaningful_words)
        if word_count in (2, 3, 4) and meaningful_words:
            _NOT_NAME_WORDS = {
                "часть", "серия", "том", "выпуск", "новый", "старый",
                "ветхий", "завет", "глава", "книга", "версия",
                "old", "new", "part", "vol", "series", "chapter",
                "version", "official", "audio", "video", "live",
                "full", "hd", "original", "remix", "edit",
            }
            looks_like_name = all(
                w[0].isupper()
                and not any(c.isdigit() for c in w)
                and w.lower() not in _NOT_NAME_WORDS
                for w in meaningful_words
            )
            if looks_like_name:
                s = re.sub(r"\s*\([^)]{2,50}\)\s*$", "", s)

    s = re.sub(r"[\s|]+$", "", s).strip()
    s = re.sub(r"\s{2,}", " ", s).strip()
    s = re.sub(r"(?<!\.)\.(?!\.)$", "", s).strip()

    return s



# Слова/акронимы которые нельзя трогать при title case
_PRESERVE_CASE: frozenset = frozenset({
    # Версии Библии и богословские аббревиатуры
    "ESV", "KJV", "NASB", "NIV", "LSB", "NLT", "CSB", "NKJV", "RSV", "NET",
    "NRSV", "LEB", "ASV", "LBCF", "LBCF1689", "WCF", "TULIP",
    # Форматы
    "Q&A", "QA",
    # Технические/бренды
    "YouTube", "RuTube", "VK", "iPhone", "iPad",
    # Языки оригинала
    "NA28", "BHS", "LXX",
})


def title_case_fragment(s: str) -> str:
    """
    Title Case для названий фрагментов (Shorts / Clips).
    Каждое слово с заглавной буквы, кроме коротких предлогов/союзов
    в середине фразы. Акронимы и слова из _PRESERVE_CASE не трогаются.
    """
    if not s:
        return s

    _LOWER_MID = {
        "в", "на", "за", "из", "по", "к", "с", "о", "у", "до", "об", "от",
        "под", "над", "при", "про", "без", "для", "через", "между",
        "и", "а", "но", "или", "да", "не", "ни", "же", "ли", "бы",
        "a", "an", "the", "of", "in", "on", "at", "to", "for", "and",
        "or", "but", "nor", "as", "by", "up",
    }

    def _capitalize_first_letter(word: str) -> str:
        """Капитализирует первую БУКВУ в слове, пропуская ведущую пунктуацию."""
        for i, ch in enumerate(word):
            if ch.isalpha():
                return word[:i] + ch.upper() + word[i + 1:]
        return word

    words = s.split()
    result = []
    for i, word in enumerate(words):
        if word in _PRESERVE_CASE or word.upper() in _PRESERVE_CASE:
            canonical = next((w for w in _PRESERVE_CASE if w.upper() == word.upper()), word)
            result.append(canonical)

        elif len(word) >= 2 and word.isupper() and word.isalpha():
            result.append(word)

        elif i == 0 or i == len(words) - 1:
            result.append(_capitalize_first_letter(word))

        elif word.lower() in _LOWER_MID:
            result.append(word.lower())

        else:
            result.append(_capitalize_first_letter(word))

    return " ".join(result)

def _filter_times_str(times_str: str, duration: int) -> str:
    """
    Фильтрует строку с таймкодами: оставляет только те, что не превышают duration.
    Автокоррекция: H:MM:SS → MM:SS для видео короче часа (типичная ошибка Gemini).
    """
    if not times_str or not duration:
        return times_str or ""
    times_str = re.sub(r"[•·]", ", ", times_str)
    tokens = [t.strip() for t in re.split(r"[,\s]+", times_str) if t.strip()]
    good: list[str] = []
    for token in tokens:
        secs = time_to_seconds(token)
        if secs is None:
            continue
        if duration < 3600 and secs > duration:
            parts = token.split(":")
            if len(parts) == 3:
                corrected = f"{parts[1]}:{parts[2]}"
                corrected_secs = time_to_seconds(corrected)
                if corrected_secs is not None and corrected_secs <= duration:
                    good.append(corrected)
                    continue
            continue
        if secs <= duration:
            good.append(token)
    return ", ".join(good)


def _parse_gemini_response(text: str, duration: int = 0) -> dict | None:
    """v3.2 — парсит плоский JSON от Gemini + terms_data. При ошибке возвращает None."""
    def _valid_time(t: str) -> bool:
        if not duration or not t:
            return True
        secs = time_to_seconds(t)
        return secs is not None and secs <= duration + 30

    def _clean_times(times_raw) -> list:
        out = []
        if not isinstance(times_raw, list):
            return out
        for t in times_raw:
            t = str(t or "").strip()
            if t and _valid_time(t):
                out.append(t)
        out = list(dict.fromkeys(out))
        out.sort(key=lambda x: (time_to_seconds(x) or 0))
        return out

    try:
        clean = text.strip()
        clean = re.sub(r"^```[a-z]*\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean).strip()
        start = clean.find("{")
        end   = clean.rfind("}")
        if start == -1 or end <= start:
            logger.warning("_parse_gemini_response: JSON не найден в ответе")
            return None
        clean = clean[start:end + 1]
        data  = json.loads(clean)
    except json.JSONDecodeError as e:
        logger.warning(f"_parse_gemini_response JSONDecodeError: {e} | текст: {text[:300]}")
        return None

    result: dict = {
        "real_author":      "",
        "real_title":       "",
        "real_event":       "",
        "format":           "other",
        "main_topic":       "",
        "timestamps":       "",
        "hashtags":         "",
        "analysis_summary": "",
        "argument_arc":     "",
        "key_categories":   [],
        "questions":        [],
        "terms_data": {
            "concepts":      [],
            "scripture":     [],
            "translations":  [],
            "lexicon_notes": [],
        },
        "whisper_hints": [],
    }

    result["real_author"] = normalize_author_name(_clean_meta_line(data.get("real_author", "")))
    result["real_title"]  = normalize_title_text(_clean_meta_line(data.get("real_title", "")))
    _fmt = str(data.get("format", "") or "").strip().lower()
    result["format"] = _fmt if _fmt in ("sermon", "lecture", "qa", "interview", "discussion") else "other"
    result["real_event"]  = _clean_meta_line(data.get("real_event", ""))
    result["main_topic"]  = _scrub_inline(_strip_meta_lines(_clean_field(data.get("main_topic", ""))))

    # timestamps → строка "MM:SS описание\n..."
    ts_list = data.get("timestamps", [])
    if isinstance(ts_list, list):
        lines = []
        dropped = []
        for ts in ts_list[:35]:
            if not isinstance(ts, dict):
                continue
            t_str = (ts.get("time") or "").strip()
            topic = _scrub_inline(_strip_meta_lines((ts.get("topic", "") or "").strip()))
            if not t_str or not topic:
                continue
            if not _valid_time(t_str):
                dropped.append(t_str)
                continue
            parts_t = t_str.split(":")
            try:
                if len(parts_t) == 2:
                    t_str = f"{int(parts_t[0]):02d}:{parts_t[1].zfill(2)}"
                elif len(parts_t) == 3:
                    t_str = f"{int(parts_t[0])}:{int(parts_t[1]):02d}:{parts_t[2].zfill(2)}"
            except ValueError:
                pass
            lines.append(f"{t_str} {topic}")
        if dropped:
            logger.warning(f"Отброшены таймкоды > длительности ({duration}s): {dropped}")
        result["timestamps"] = "\n".join(lines)

    # hashtags → строка "#Тег #Тег ..."
    ht_list = data.get("hashtags", [])
    if isinstance(ht_list, list):
        tags = []
        for tag in ht_list[:8]:
            tag = str(tag).strip().lstrip("#")
            if tag:
                tags.append(f"#{tag[0].upper()}{tag[1:]}")
        result["hashtags"] = " ".join(tags)

    result["analysis_summary"] = _scrub_inline(_strip_meta_lines(
        _clean_field(data.get("analysis_summary", ""))
    ))
    result["argument_arc"] = _scrub_inline(_strip_meta_lines(
        _clean_field(data.get("argument_arc", ""))
    ))

    # key_categories — массив строк "Понятие — объяснение"
    kc_raw = data.get("key_categories", [])
    if isinstance(kc_raw, list):
        result["key_categories"] = [
            _scrub_inline(_strip_meta_lines(_clean_field(str(item))))
            for item in kc_raw
            if _clean_field(str(item))
        ][:10]

    # questions — плоский массив строк (могут начинаться с 🟢/🔵)
    q_raw = data.get("questions", [])
    if isinstance(q_raw, list):
        result["questions"] = [
            _scrub_inline(_strip_meta_lines(_clean_field(str(q))))
            for q in q_raw
            if _clean_field(str(q))
        ][:18]

    # terms_data — плоские массивы строк с || разделителем
    try:
        td = data.get("terms_data", {})
        if isinstance(td, dict):
            # limits по разделам
            section_limits = {
                "concepts": 10,
                "scripture": 10,
                "translations": 6,
                "lexicon_notes": 6,
            }

            for key, limit in section_limits.items():
                raw_list = td.get(key, [])
                if not isinstance(raw_list, list):
                    continue

                cleaned_items = []

                for item in raw_list:
                    s = _scrub_inline(_strip_meta_lines(_clean_field(str(item))))
                    if not s:
                        continue
                    if is_meta_garbage(s):
                        continue

                    # Для lexicon_notes ожидаем 8 полей:
                    # ref || ru_key || original_word || source_label || note || why || times || context_mention
                    if key == "lexicon_notes":
                        parts = [p.strip() for p in s.split("||")]
                        while len(parts) < 8:
                            parts.append("")
                        parts = parts[:8]

                        ref, ru_key, original_word, source_label, note, why, times_str, context_mention = parts

                        ref = _scrub_inline(_clean_field(ref))
                        ru_key = _scrub_inline(_clean_field(ru_key))
                        original_word = _scrub_inline(_clean_field(original_word))
                        source_label = _scrub_inline(_clean_field(source_label))
                        note = _scrub_inline(_clean_field(note))
                        why = _scrub_inline(_clean_field(why))
                        times_str = _scrub_inline(times_str.strip())
                        times_str = _filter_times_str(times_str, duration)
                        context_mention = _scrub_inline(_clean_field(context_mention))

                        # Минимальная валидность: должно быть либо место Писания, либо оригинальное слово,
                        # и обязательно словарное пояснение
                        if not note:
                            continue
                        if not ref and not original_word:
                            continue

                        # Чистим поля и собираем обратно в каноническом виде (8 полей)
                        normalized_parts = [
                            ref[:120],
                            ru_key[:120],
                            original_word[:120],
                            source_label[:160],
                            note[:260],
                            why[:220],
                            times_str[:120],
                            context_mention[:300],
                        ]
                        s = " || ".join(normalized_parts)

                    # Для translations ожидаем 6 полей:
                    # ref || focus || ru_raw || en_raw || observation || times
                    elif key == "translations":
                        parts = [p.strip() for p in s.split("||")]
                        while len(parts) < 6:
                            parts.append("")
                        parts = parts[:6]

                        ref, focus, ru_raw, en_raw, observation, times_str = parts
                        ref = _scrub_inline(_clean_field(ref))
                        focus = _scrub_inline(_clean_field(focus))
                        ru_raw = _scrub_inline(_clean_field(ru_raw))
                        en_raw = _scrub_inline(_clean_field(en_raw))
                        observation = _scrub_inline(_clean_field(observation))
                        times_str = _scrub_inline(times_str.strip())
                        times_str = _filter_times_str(times_str, duration)

                        if not ref:
                            continue
                        if not observation and not (ru_raw or en_raw):
                            continue

                        normalized_parts = [
                            ref[:120],
                            focus[:120],
                            ru_raw[:260],
                            en_raw[:260],
                            observation[:320],
                            times_str[:120],
                        ]
                        s = " || ".join(normalized_parts)

                    # Для scripture ожидаем 4 поля:
                    # ref || use || phrase || times
                    elif key == "scripture":
                        parts = [p.strip() for p in s.split("||")]
                        while len(parts) < 4:
                            parts.append("")
                        parts = parts[:4]

                        ref, use, phrase, times_str = parts
                        ref = _scrub_inline(_clean_field(ref))
                        use = _scrub_inline(_clean_field(use))
                        phrase = _scrub_inline(_clean_field(phrase))
                        times_str = _scrub_inline(times_str.strip())
                        times_str = _filter_times_str(times_str, duration)

                        if not ref:
                            continue

                        normalized_parts = [
                            ref[:120],
                            use[:260],
                            phrase[:220],
                            times_str[:120],
                        ]
                        s = " || ".join(normalized_parts)

                    # Для concepts ожидаем 4 поля:
                    # term || explanation || why || times
                    elif key == "concepts":
                        parts = [p.strip() for p in s.split("||")]
                        while len(parts) < 4:
                            parts.append("")
                        parts = parts[:4]

                        term, explanation, why, times_str = parts
                        term = _scrub_inline(_clean_field(term))
                        explanation = _scrub_inline(_clean_field(explanation))
                        why = _scrub_inline(_clean_field(why))
                        times_str = _scrub_inline(times_str.strip())
                        times_str = _filter_times_str(times_str, duration)

                        if not term:
                            continue

                        normalized_parts = [
                            term[:120],
                            explanation[:260],
                            why[:220],
                            times_str[:120],
                        ]
                        s = " || ".join(normalized_parts)

                    cleaned_items.append(s[:700])

                result["terms_data"][key] = cleaned_items[:limit]

    except Exception as e:
        logger.warning(f"_parse_gemini_response: terms_data parsing error ({e}), skipping")
        result["terms_data"] = {
            "concepts": [],
            "scripture": [],
            "translations": [],
            "lexicon_notes": [],
        }

    # whisper_hints — плоский массив строк для initial_prompt Whisper
    wh_raw = data.get("whisper_hints", [])
    if isinstance(wh_raw, list):
        result["whisper_hints"] = [
            str(w).strip() for w in wh_raw
            if isinstance(w, str) and str(w).strip()
        ][:80]
    else:
        result["whisper_hints"] = []

    # hermeneutic_method — тип проповеди (expository/topical/narrative/...)
    _valid_methods = {
        "expository", "topical", "narrative", "typological",
        "redemptive_historical", "catechetical", "apologetic",
        "evangelistic", "practical", "mixed"
    }
    _hm_raw = data.get("hermeneutic_method", "")
    result["hermeneutic_method"] = _hm_raw if _hm_raw in _valid_methods else "mixed"

    has_content = any([
        result["main_topic"], result["timestamps"],
        result["real_author"], result["real_title"],
        result["questions"], result["analysis_summary"],
        any([
            result["terms_data"]["concepts"],
            result["terms_data"]["scripture"],
            result["terms_data"]["translations"],
            result["terms_data"]["lexicon_notes"],
        ]),
    ])
    return result if has_content else None


def time_to_seconds(t: str) -> int | None:
    """Конвертирует M:SS или H:MM:SS в секунды. Возвращает None для невалидного времени, 0 для 0:00."""
    if not t or not t.strip():
        return None
    parts = t.strip().split(":")
    try:
        if len(parts) == 2:
            m, s = int(parts[0]), int(parts[1])
            if m < 0 or s < 0 or s >= 60:
                return None
            return m * 60 + s
        elif len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
            if h < 0 or m < 0 or s < 0 or m >= 60 or s >= 60:
                return None
            return h * 3600 + m * 60 + s
    except (ValueError, TypeError):
        pass
    return None


def get_youtube_video_url(url: str) -> str:
    """Извлекает чистый URL с video ID."""
    parsed = urllib.parse.urlparse(url)
    if "youtu.be" in parsed.netloc:
        video_id = parsed.path.strip("/").split("/")[0]
        return f"https://youtu.be/{video_id}"
    # /live/VIDEO_ID и /shorts/VIDEO_ID
    for prefix in ("/live/", "/shorts/"):
        if parsed.path.startswith(prefix):
            video_id = parsed.path[len(prefix):].split("/")[0].split("?")[0]
            if video_id:
                return f"https://www.youtube.com/watch?v={video_id}"
    params = urllib.parse.parse_qs(parsed.query)
    video_id = params.get("v", [None])[0]
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return url


def get_youtube_timestamp_url(url: str, seconds: int, offset: int = 0) -> str:
    """Ссылка на YouTube с таймкодом. offset — сдвиг назад в секундах (для глоссария и т.п.)."""
    seconds = max(0, seconds - offset)
    parsed = urllib.parse.urlparse(url)
    if "youtu.be" in parsed.netloc:
        video_id = parsed.path.strip("/").split("/")[0]
        return f"https://youtu.be/{video_id}?t={seconds}"
    params = urllib.parse.parse_qs(parsed.query)
    video_id = params.get("v", [None])[0]
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}&t={seconds}"
    return url


# ─── Synopsis v2.x: промпты (format-aware) ──────────────────

# Стандартный конспект для sermon / lecture / interview / discussion / other
SYNOPSIS_PROMPT_V2 = """\
Ты создаёшь конспект проповеди, лекции или свидетельства по аудиоматериалу.

Материал: {title}
Длительность: {duration}
Тип проповеди: {hermeneutic_method}

ГЛАВНЫЙ ПРИНЦИП:
Этот конспект должен быть максимально близким к реальной речи автора.

Это НЕ:
- не аналитическая статья,
- не summary,
- не moral takeaway,
- не редакторская интерпретация,
- не объяснение читателю "что это значит",
- не улучшенная редактором версия речи.

Это:
- сжатая, очищенная от мусора, но максимально близкая к автору речь,
- структурированная по реальным смысловым узлам материала,
- с сохранением порядка, тона и лексики автора.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
КАК ПИСАТЬ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Пиши так, как будто ты не объясняешь автора, а сжато проводишь читателя по самой речи автора.

Сохраняй:
- порядок мысли автора,
- характерные авторские формулировки,
- его тон,
- его переходы,
- его сильные фразы.

Можно убирать:
- словесный мусор,
- оговорки,
- случайные повторы подряд,
- технические паузы,
- разговорные пустоты без смысловой нагрузки.

Нельзя:
- добавлять выводы от себя,
- подводить мораль,
- писать "главная мысль",
- писать "это показывает",
- писать "важно понять",
- писать "таким образом",
- писать "из этого следует",
- писать "почему это важно",
- превращать section в мини-эссе,
- улучшать речь автора до редакторской статьи.

Если автор не делает вывод — не делай его за него.
Если автор просто рассказывает — просто передавай рассказ.
Если автор обличает — передавай обличение.
Если автор свидетельствует — сохраняй свидетельство.
Если автор объясняет текст — передавай ход объяснения, а не свой комментарий к нему.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ФОРМАТ ОТВЕТА
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Верни только чистый JSON, без ```json и без пояснений:

{{
  "outline": [
    {{"title": "Название раздела", "time": "0:00"}},
    {{"title": "Название раздела", "time": "8:43"}}
  ],
  "sections": [
    {{
      "title": "Название раздела",
      "time": "0:00",
      "content": "Markdown..."
    }}
  ]
}}

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
КОЛИЧЕСТВО И ЛОГИКА SECTIONS
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Количество sections должно определяться реальными смысловыми переходами автора.

Не делай sections по круглому числу.
Не делай sections по искусственной схеме.
Не объединяй в один section разные этапы речи, если автор реально переходит к новому узлу.
Не дроби section без необходимости.

Если автор:
- рассказывает длинную историю с несколькими этапами — sections идут по этапам истории;
- объясняет текст — sections идут по этапам объяснения;
- переходит к новому вопросу — это новый section;
- меняет тон или предмет разговора — это может быть новый section.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
НАЗВАНИЯ SECTIONS
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Название section должно быть нейтральным навигационным заголовком, а не выводом.

Хороший title:
- называет сцену,
- называет вопрос,
- называет этап истории,
- называет смысловой узел речи.

Плохой title:
- делает вывод за автора,
- интерпретирует фрагмент,
- звучит как редакторская статья,
- превращает section в moral lesson.

ХОРОШО:
- Смерть отца и начало внутреннего крушения
- Жизнь без ограничений и ложь как образ жизни
- Разговор в общежитии
- Библиотека и осознание краткости жизни
- Первые шаги после обращения
- Молитвенная борьба в шкафу

ПЛОХО:
- Крушение ложной идентичности
- Главный нерв пробуждения
- Урок благодати для падшего человека
- От иллюзии безопасности к ужасу истины

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
КАК ДОЛЖЕН ВЫГЛЯДЕТЬ CONTENT
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

content — это не тезисы преподавателя и не выводы редактора.
content — это сжатая авторская речь, очищенная от мусора.

Пиши обычными абзацами.
Не делай искусственных выводов.
Не вставляй moral summary в конце section.
Не добавляй application, если его нет в этом месте речи.

Если автор рассказывает историю:
- передавай историю как историю,
- сохраняй её ход,
- не вытаскивай отдельную мораль от себя.

Если автор объясняет текст Писания:
- передавай, как он объясняет,
- не превращай section в богословский комментарий от себя.

Если автор говорит особенно сильно и ясно:
- можно сохранить короткую фразу почти дословно.

ГОЛОС КОНСПЕКТА — ПЕРВОЕ ЛИЦО ИЛИ БЕЗЛИЧНОЕ:
Нарративное свидетельство (автор рассказывает о себе) → пиши ОТ ПЕРВОГО ЛИЦА:
  ❌ "Пол вспоминает, что в 17 лет он потерял отца."
  ✅ "В 17 лет отец умер прямо на моих глазах — от сердечного приступа."
Тезисная проповедь (автор излагает доктрину) → пиши безлично:
  ❌ "Вошер объясняет, что сдерживающая благодать ограничивает грех."
  ✅ "Сдерживающая благодать ограничивает проявление греха через внешние факторы."

Запрещено: "он сказал", "автор считает", "проповедник объясняет", "спикер отмечает" и любые конструкции от третьего лица.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
БЛИЗОСТЬ К АВТОРУ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

ЛЕКСИКА:
Сохраняй слова автора. Если он говорит "Бог вас любит не потому
что вы хорошие, а потому что Он добрый" — пиши ИМЕННО ЭТО.
Не переформулируй в "безусловность Божьей любви определяется
не заслугами человека, а природой Бога".

МЕТАФОРЫ:
Если автор использует метафору — сохрани её.
"Жизнь как трава" — не заменяй на "кратковременность бытия".

ТОН:
Если автор говорит резко и обличительно — конспект резкий.
Если мягко и пасторски — конспект мягкий.
Не сглаживай. Не усиливай. Передавай.

ЛОГИКА:
Сохраняй порядок аргументов автора.
Не перестраивай "логичнее". Его порядок — это его проповедь.

ГДЕ ДОПУСТИМО ОТОЙТИ:
- Сократить длинноты (повторы, отступления, технические паузы)
- Убрать неинформативные вводные ("ну вот", "как бы сказать")
- Уплотнить формулировку, сохраняя смысл и лексику
- Объединить 2 предложения в одно если они говорят одно и то же

ГДЕ ЗАПРЕЩЕНО ОТХОДИТЬ:
- Менять лексику автора на "более аналитическую"
- Перестраивать порядок аргументов
- Добавлять мысли, которых автор не высказывал
- Делать оценочные заключения от себя
- Менять тон (смягчать обличение, сушить пасторское)

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ПИСАНИЕ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Если автор реально читает или цитирует текст Писания вслух, оформи через blockquote:

> **«Текст Писания»** *(Книга X:XX)*

После цитаты — обычный текст, близкий к речи автора.
Не превращай текст Писания в длинный академический комментарий от себя.

Inline (мимоходное упоминание):
...здесь звучит: **«книги раскрыты были»** *(Откр. 20:12)* — и автор...

Цитата Писания и тезис автора ОБЯЗАНЫ выглядеть по-разному:
- «Ёлочки» = Писание: **«Текст»** *(Кн. Х:Х)*
- Без кавычек = мысль автора: **Тезис.**

BLOCKQUOTE содержит ТОЛЬКО текст цитаты. После blockquote — объяснение отдельным абзацем.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ФОРМАТИРОВАНИЕ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Используй Markdown, но очень умеренно.

**жирный** — для трёх случаев:
  - короткой сильной фразы автора (прямая цитата или чёткий тезис),
  - короткого подзаголовка или краткой формулы,
  - поворотной мысли или ключевого момента — фраза, после которой всё меняется.

  ЛИМИТ: не больше 1–2 жирных элемента на section. Лучше меньше, чем больше.

  ЧТО выделять — зависит от типа материала:

  Нарратив / свидетельство (narrative, evangelistic):
  ✅ Поворот истории: **Я не был жертвой — я был преступником.**
  ✅ Цитата-удар: **«Ты ничтожество, пока не отдашь жизнь Христу»**
  ✅ Ключевое осознание: **Моя жизнь была сплошной фабрикацией.**

  Экспозиционная / тематическая проповедь (expository, topical):
  ✅ Главный тезис раздела: **Оправдание — это не процесс, а Божий приговор.**
  ✅ Ключевой контраст: **Не заслуга — а дар.**
  ✅ Заголовок аргумента: **Первая причина: страх не от Бога.**

  Лекция (lecture):
  ✅ Определение термина: **Sanctification — это не событие, а процесс.**
  ✅ Центральный вывод: **Без регенерации воля остаётся в рабстве.**

  ЧТО НЕ выделять (для всех типов):
  ❌ Нарративный контекст и фоновые детали
  ❌ Переходы между мыслями
  ❌ Длинные предложения (больше 10–12 слов)
  ❌ Всё подряд — если жирного много, он перестаёт работать

*курсив* — ТОЛЬКО для:
  - ссылок на Писание: *(Пс. 102:15)*
  - иностранных/оригинальных слов: *anomia*
  БОЛЬШЕ НИЧЕГО. Курсив запрещён для нарративного текста и целых абзацев.

Blockquote — только для реально звучащего текста Писания.

ЖИРНЫЙ ПОДЗАГОЛОВОК — на отдельной строке, пустая строка после:
**Тщеславие как двигатель жизни.**

Желание быть супергероем привело к одержимости...

Между абзацами — двойной перенос (\\n\\n).
Не используй ## или ### внутри content.
Квадратные скобки [ и ] — нигде, никогда.

ТИРЕ: слово — слово (с пробелами).
Ссылки: *(Рим. 8:28)* — курсив, скобки, точка после книги.

ПРАВИЛО INLINE-ТАЙМКОДОВ:
Таймкод ВСЕГДА стоит ВНУТРИ предложения — ПЕРЕД точкой, не после.

Формат: ⏱ **M:SS** или 📌 **M:SS**

✅ Когда отношения только начали налаживаться ⏱ **17:30**.
❌ Когда отношения налаживаться. ⏱ **17:30** ← точка до таймкода

Таймкод всегда ВНЕ любого форматирования (*курсив*, **жирный**).
Между маркером и числом — пробел: ⏱ **7:30**, не ⏱**7:30**.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
СТРОГИЕ ЗАПРЕТЫ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Не писать:
- "главная мысль"
- "важно понять"
- "это показывает"
- "таким образом"
- "в итоге"
- "из этого следует"
- "почему это важно"
- "автор подчеркивает"
- "проповедник говорит о том, что"
- "здесь видно"
- "этот раздел показывает"
- "ключевой тезис состоит в том"
- "следует отметить"

Не подводить выводы от себя.
Не добавлять мораль.
Не делать application, если его нет в речи автора.
Не писать как редактор статьи.
Не использовать конструкции от третьего лица: "он сказал", "автор считает", "спикер отмечает".

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ФИНАЛЬНАЯ ПРОВЕРКА
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Перед тем как вернуть JSON, проверь:

- Это действительно звучит как сжатая речь автора, а не как summary?
- Нет ли здесь выводов, которых автор сам не делал?
- Нет ли морали, добавленной от себя?
- Не стал ли title section редакторским выводом?
- Сохранился ли порядок речи автора?
- Не превратился ли section в study note или reflection?

Верни только JSON.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ОГРАНИЧЕНИЯ ПО ДЛИНЕ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Для этого материала ({duration}):
- sections: {syn_sections}
- каждый section: {syn_section_len} символов
- суммарно: от {syn_total} символов

Но главное — не длина, а точность.
Лучше плотнее и ближе к реальной речи автора, чем красивее и более "редакторски".
"""

# Q&A конспект — sections строятся по уже найденным вопросам из таймкодов
SYNOPSIS_PROMPT_QA = """\
Ты создаёшь конспект сессии вопросов и ответов по аудиоматериалу.

Материал: {title}
Длительность: {duration}

Уже известные вопросы (таймкод → формулировка):
{timestamps_block}

ГЛАВНЫЙ ПРИНЦИП:
Этот конспект должен быть максимально близким к реальным ответам автора.

Это НЕ:
- не аналитическая статья,
- не moral summary,
- не список выводов,
- не application guide,
- не reflection page,
- не редакторские комментарии по мотивам ответов.

Это:
- сжатая, очищенная от мусора, но максимально близкая к речи автора карта ответов,
- идущая строго по порядку вопросов,
- без добавленных выводов и моралей.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ЧТО ДЕЛАТЬ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Для каждого вопроса из списка выше создай section.

Каждый section должен:
- сохранять формулировку вопроса максимально близко к тому, как он звучит;
- передавать реальный ход ответа;
- сохранять тон автора;
- не подменять ответ редакторским summary;
- не добавлять применение от себя;
- не добавлять "главный вывод" от себя.

Можно убирать:
- словесный мусор,
- разговорные повторы без смысловой нагрузки,
- пустые заходы,
- оговорки.

Нельзя:
- улучшать ответ редакторски,
- делать moral takeaway,
- делать theological commentary от себя,
- делать "вопрос для личного размышления" внутри synopsis,
- добавлять application,
- писать "суть ответа в том, что",
- писать "важно понять",
- писать "это показывает",
- писать "таким образом",
- писать "в итоге".

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
СТРУКТУРА
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Верни только чистый JSON, без ```json и без пояснений:

{{
  "outline": [
    {{"title": "Краткий вопрос", "time": "0:00"}},
    ...
  ],
  "sections": [
    {{
      "title": "Краткий вопрос",
      "time": "0:00",
      "content": "Markdown..."
    }},
    ...
  ]
}}

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ПРАВИЛА ПО SECTION TITLE
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

title каждого section = краткая формулировка вопроса.

Не превращай title в вывод.
Не делай title "умнее", чем вопрос.
Не редактируй вопрос в редакторский тезис.

ХОРОШО:
- Как отличить ложных учителей?
- Что значит родиться свыше?
- Почему человек может обманывать сам себя?
- Что делать, когда не чувствуешь Божьего присутствия?

ПЛОХО:
- Диагноз ложного христианства
- Главный нерв самообмана
- Проблема мёртвой религии
- Тайна подлинного обращения

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
КАК ДОЛЖЕН ВЫГЛЯДЕТЬ CONTENT
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

content должен передавать сжатый реальный ответ автора.

Не делай внутри section:
- вывод section,
- moral lesson,
- личное применение,
- редакторскую оценку ответа,
- комментарий "что это значит".

Если автор:
- отвечает прямо — передай прямоту;
- объясняет долго — сожми, но сохрани порядок;
- обличает — сохрани обличение;
- различает два понятия — передай это различие без добавленной аналитики;
- цитирует Писание — можно красиво оформить цитату.

Если автор не завершает ответ явным выводом, не добавляй вывод сам.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ПИСАНИЕ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Если автор реально читает или цитирует текст Писания вслух, можно оформить через blockquote.

Формат:
> **«Текст Писания»** *(Книга X:XX)*

После цитаты — обычный текст, близкий к речи автора.
Не превращай section в богословскую заметку от себя.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ФОРМАТИРОВАНИЕ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Используй Markdown умеренно.

Разрешено:
- **жирный** — только для короткой сильной фразы автора или краткой формулы;
- *курсив* — для ссылок на Писание или очень редких тонких случаев;
- blockquote — для реально звучащего текста Писания.

Запрещено:
- жирнить длинные предложения;
- жирнить итог section;
- делать content похожим на study note;
- делать внутри section application blocks;
- делать внутри section "вопрос для личного размышления".

Таймкоды:
- section.time должен совпадать с данным из списка;
- внутри content можно указывать важные моменты только если это реально помогает;
- не перегружай content таймкодами.

Формат таймкода: ⏱ **M:SS** — всегда внутри предложения перед точкой, не после.
Пробел между маркером и числом обязателен: ⏱ **7:30**, не ⏱**7:30**.

Между абзацами — двойной перенос (\\n\\n).
Не используй ## или ### внутри content.
Квадратные скобки [ и ] — нигде, никогда.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
СТРОГИЕ ЗАПРЕТЫ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Не писать:
- "главный вывод"
- "суть ответа"
- "это показывает"
- "важно понять"
- "таким образом"
- "в итоге"
- "из этого следует"
- "автор подчеркивает"
- "спикер говорит о том, что"
- "здесь видно"
- "в этом разделе"

Не добавлять:
- личное применение,
- диагностические вопросы,
- молитвенные направления,
- moral takeaway,
- reflection content.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ВАЖНО
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

outline содержит только реальные разделы-ответы.
Поле time для каждого section должно совпадать с вопросом из списка.
Первый section: time = "0:00" если первый вопрос начинается с 0:00.
Последний section = последний вопрос из списка. Не обрывай Q&A раньше.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ОГРАНИЧЕНИЯ ПО ДЛИНЕ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

- Каждый section должен быть плотным, но без редакторских выводов.
- Суммарный объём должен быть достаточным для содержательного чтения.
- Лучше короче, но ближе к реальному ответу, чем красивее и более "редакторски".

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ФИНАЛЬНАЯ ПРОВЕРКА
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Перед тем как вернуть JSON, проверь:

- Это действительно похоже на сжатый реальный ответ автора?
- Нет ли здесь выводов и моралей от тебя?
- Не стал ли title редакторским тезисом?
- Не превратился ли ответ в analysis или reflection?
- Сохранился ли порядок вопросов и ответов?

Верни только JSON.
"""



# ─── Synopsis v2.0: вспомогательные функции ──────────────────

def _fix_rtl_in_text(text: str) -> str:
    """Оборачивает RTL-слова (иврит, арабский) в RLI/PDI изоляторы,
    чтобы браузер не переключал направление всего абзаца.
    Пробелы вокруг изоляторов не добавляются — они уже есть в тексте."""
    RLI = '\u2067'
    PDI = '\u2069'
    return re.sub(
        r'([\u0590-\u05FF\u0600-\u06FF][^\s]*(?:\s+[\u0590-\u05FF\u0600-\u06FF][^\s]*)*)',
        lambda m: RLI + m.group(0) + PDI,
        text,
    )


def _clamp_content_timestamps(content: str, duration: int) -> str:
    """Удаляет или корректирует таймкоды в тексте, превышающие duration видео.
    Работает только с маркированными таймкодами (⏱, 🔗, 📌, ⏳, 📍)."""
    if not duration or not content:
        return content

    def _replace(m: re.Match) -> str:
        marker = m.group(1) or ''
        bold_open = m.group(2) or ''
        time_str = m.group(3)
        bold_close = m.group(4) or ''
        secs = time_to_seconds(time_str)
        if secs is None:
            return m.group(0)
        if secs > duration + 30:
            return ''
        # Для коротких видео (<1ч): если таймкод > duration, попробуем поправить
        if duration < 3600 and secs > duration:
            # Пробуем интерпретировать MM:SS как SS (редкий случай)
            corrected_secs = secs % 60
            if corrected_secs > 60:
                return ''
        return m.group(0)

    pattern = re.compile(
        r'([⏱🔗📌⏳📍]\s*)'
        r'(\*\*)?'
        r'(\d{1,2}:\d{2}(?::\d{2})?)'
        r'(\*\*)?'
    )
    return pattern.sub(_replace, content)


def _fix_orphaned_bold_markers(content: str) -> str:
    """Чинит непарные ** и *** маркеры, которые рендерятся как сырой текст."""
    if '**' not in content:
        return content

    lines = content.split('\n')
    result: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append(line)
            continue

        test = stripped
        triple_count = test.count('***')
        test_no_triple = test.replace('***', '')
        double_count = test_no_triple.count('**')

        if triple_count % 2 == 1:
            if stripped.startswith('***') and triple_count == 1:
                result.append(line.rstrip() + '***')
                continue
            elif stripped.endswith('***') and triple_count == 1:
                result.append(re.sub(r'\*{3}\s*$', '', line).rstrip())
                continue

        if double_count % 2 == 1:
            if stripped.startswith('**') and not stripped.startswith('***'):
                result.append(line.rstrip() + '**')
                continue
            elif stripped.endswith('**') and not stripped.endswith('***'):
                result.append(re.sub(r'\*{2}\s*$', '', line).rstrip())
                continue
            else:
                idx = line.rfind('**')
                if idx >= 0:
                    result.append(line[:idx] + line[idx + 2:])
                    continue

        result.append(line)

    # Зачистка одиночных непарных * (остатки незакрытого курсива)
    final = []
    for line in result:
        stripped = line.strip()
        if not stripped:
            final.append(line)
            continue
        test = stripped.replace('***', '').replace('**', '')
        single_count = test.count('*')
        if single_count % 2 == 1:
            chars = list(line)
            for idx_c in range(len(chars) - 1, -1, -1):
                if chars[idx_c] == '*':
                    before = chars[idx_c - 1] if idx_c > 0 else ''
                    after = chars[idx_c + 1] if idx_c < len(chars) - 1 else ''
                    if before != '*' and after != '*':
                        chars.pop(idx_c)
                        break
            line_fixed = ''.join(chars)
            line_fixed = re.sub(r'\s+([.,;:!?])', r'\1', line_fixed)
            line_fixed = re.sub(r'  +', ' ', line_fixed)
            final.append(line_fixed)
        else:
            final.append(line)
    result = final

    return '\n'.join(result)


def _ensure_trailing_period(text: str) -> str:
    """Если последний непустой абзац content не заканчивается на .!?»"…
    — ставим точку.
    Если строка заканчивается на inline-таймкод — точка ставится ПОСЛЕ него.
    Строки-лейблы (**...:), строки на ':', заголовки, hr — пропускаем."""
    if not text.strip():
        return text
    _TS_INLINE = re.compile(
        r'(\s*[⏱📌]?\s*\*{0,2}\d{1,2}:\d{2}(?::\d{2})?\*{0,2})\s*$'
    )
    lines = text.rstrip().splitlines()
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].rstrip()
        if not s:
            continue
        # Пропускаем заголовки, blockquote, hr, dividers
        if re.match(r'^(#{1,4} |>|---|___|\*\*\*\s*$)', s):
            continue
        # Пропускаем лейблы и строки-заголовки с ':' в конце (с возможной пунктуацией/** после)
        if re.match(r'^\*\*.*:\*?\*?\s*[.!]?\s*$', s) or re.search(r':[*\s.!]*$', s):
            continue
        # Уже есть завершающий знак в конце — всё хорошо
        if re.search(r'[.!?»"\u2026]\s*$', s):
            break
        # Строка кончается inline-таймкодом — точка ПОСЛЕ таймкода
        m = _TS_INLINE.search(s)
        if m:
            lines[i] = s + '.'
            break
        # Ставим точку: перед ** если незакрытый (нечётное число), после если закрытый
        if s.endswith('**'):
            tmp = s.replace('***', '\x00\x00\x00')
            count = tmp.count('**')
            # Проверяем есть ли точка прямо перед закрывающим ** или *** (с возможным пробелом)
            # Пример: "**Текст.**" или "***Текст.***" — точка уже есть, не дублируем
            _before_stars = re.sub(r'\*{2,3}\s*$', '', s.rstrip())
            if re.search(r'[.!?»"\u2026]\s*$', _before_stars):
                break  # точка уже есть перед ** — ничего не добавляем
            if count % 2 == 1:
                lines[i] = s[:-2].rstrip() + '.**'
            else:
                lines[i] = s + '.'
        else:
            lines[i] = s + '.'
        break
    return '\n'.join(lines)


def _ensure_all_paragraphs_period(text: str) -> str:
    """Применяет _ensure_trailing_period к каждому визуальному абзацу.
    Telegraph превращает каждую строку (даже через одинарный \n) в отдельный <p>,
    поэтому обрабатываем каждую строку отдельно, а не только последнюю в блоке."""
    # Разбиваем сохраняя разделители — \n\n+ остаются между частями
    _paras = re.split(r'(\n{2,})', text)
    parts = []
    for p in _paras:
        if not p.strip():
            # Это разделитель (\n\n+) или пустота — не трогаем
            parts.append(p)
            continue
        # Внутри абзаца строки через одинарный \n — каждая идёт в свой <p> в Telegraph
        sub_lines = p.split('\n')
        parts.append('\n'.join(
            _ensure_trailing_period(line) if line.strip() else line
            for line in sub_lines
        ))
    result = ''.join(parts)
    # Убираем ровно две точки подряд (не трогаем многоточие ...)
    result = re.sub(r'(?<!\.)\.\.(?!\.)', '.', result)
    return result


def _linkify_inline_timestamps(nodes: list, yt_url: str, duration: int = 0) -> list:
    """Делает кликабельными inline-таймкоды вида **22:15** внутри content.
    Поддерживает:
    1) таймкод внутри italic-ноды;
    2) text + следующая bold-нода с таймкодом;
    3) stray-мусор вокруг таймкодов;
    4) fallback: plain bold timestamp без маркера (только в обычных абзацах).
    """
    if not yt_url:
        return nodes

    TS_RE = re.compile(r'^\d{1,2}:\d{2}(?::\d{2})?$')
    TS_MARKERS = ('⏱', '🔗', '📌', '⏳', '📍', '\u00a0⏱', '\u00a0🔗')
    STRAY_RE = re.compile(r'^\s*(?:\\?\*+|_\.?|\*{1,2}\.?)\s*$')

    def _make_ts_link(marker: str, bold_text: str) -> dict | None:
        secs = time_to_seconds(bold_text)
        if secs is None:
            return None
        if duration and secs > duration + 30:
            return None
        link = get_youtube_timestamp_url(yt_url, secs)
        return {
            "tag": "a",
            "attrs": {"href": link},
            "children": [marker + "\u00a0" + bold_text] if marker else [bold_text],
        }

    def _is_plain_bold_timestamp(node) -> str | None:
        if not isinstance(node, dict):
            return None
        if node.get("tag") != "b":
            return None
        children = node.get("children", [])
        if len(children) != 1 or not isinstance(children[0], str):
            return None
        txt = children[0].strip()
        return txt if TS_RE.match(txt) else None

    def _process_children(children: list, allow_plain_bold_fallback: bool) -> list:
        new_children = []
        i = 0

        while i < len(children):
            child = children[i]

            # ── Случай 1: italic-нода, внутри которой может быть таймкод ──────────
            if isinstance(child, dict) and child.get("tag") == "i":
                ic = list(child.get("children", []))
                rebuilt_ic: list = []
                extracted_links: list = []
                j = 0

                while j < len(ic):
                    ic_child = ic[j]
                    if (
                        isinstance(ic_child, str)
                        and any(m in ic_child for m in TS_MARKERS)
                        and j + 1 < len(ic)
                    ):
                        ic_nxt = ic[j + 1]
                        if isinstance(ic_nxt, dict) and ic_nxt.get("tag") in ("b", "i"):
                            bc = ic_nxt.get("children", [])
                            bold_text = bc[0].strip() if bc and isinstance(bc[0], str) else ""
                            if TS_RE.match(bold_text):
                                marker = next((_m for _m in TS_MARKERS if _m in ic_child), "")
                                lnk = _make_ts_link(marker, bold_text)
                                if lnk is not None:
                                    _marker_pos = ic_child.rfind(marker) if marker else -1
                                    text_b4 = ic_child[:_marker_pos].rstrip() if _marker_pos >= 0 else ic_child
                                    # Текст ПОСЛЕ маркера внутри той же ic_child-строки (Bug #3)
                                    text_after_marker = (
                                        ic_child[_marker_pos + len(marker):].strip()
                                        if (marker and _marker_pos >= 0) else ""
                                    )
                                    if text_b4:
                                        rebuilt_ic.append(text_b4)
                                    extracted_links.append(lnk)
                                    if text_after_marker:
                                        sep = "" if text_after_marker[0] in '.!?,;:)»"' else " "
                                        extracted_links.append(sep + text_after_marker)
                                    j += 2
                                    continue
                    rebuilt_ic.append(ic_child)
                    j += 1

                if extracted_links:
                    while rebuilt_ic and isinstance(rebuilt_ic[-1], str) and STRAY_RE.match(rebuilt_ic[-1]):
                        rebuilt_ic.pop()
                    if rebuilt_ic and isinstance(rebuilt_ic[-1], str):
                        rebuilt_ic[-1] = re.sub(r'[\*_]+\s*$', '', rebuilt_ic[-1])
                        if not rebuilt_ic[-1].strip():
                            rebuilt_ic.pop()
                    if rebuilt_ic:
                        new_children.append({**child, "children": rebuilt_ic})
                    if new_children and not (
                        isinstance(new_children[-1], str) and new_children[-1].endswith(" ")
                    ):
                        new_children.append(" ")
                    new_children.extend(extracted_links)
                else:
                    new_children.append(child)

                i += 1
                continue

            # ── Случай 2: text-нода с маркером + следующая bold-нода с таймкодом ──
            if (
                isinstance(child, str)
                and any(m in child for m in TS_MARKERS)
            ):
                # ── Случай 2а: маркер + таймкод в одной строке (незакрытый ** или без **)
                # Например: "текст ⏱ **11:45" или "текст ⏱ 11:45"
                _inline_ts_re = re.compile(r'(⏱|🔗)\s*\*{0,2}(\d{1,2}:\d{2}(?::\d{2})?)\*{0,2}')
                _m = _inline_ts_re.search(child)
                if _m:
                    marker = _m.group(1)
                    bold_text = _m.group(2)
                    lnk = _make_ts_link(marker, bold_text)
                    if lnk is not None:
                        text_before = child[:_m.start()].rstrip()
                        text_after = child[_m.end():].lstrip("* ")
                        if text_before:
                            new_children.append(text_before + " ")
                        elif new_children and isinstance(new_children[-1], dict):
                            new_children.append(" ")
                        new_children.append(lnk)
                        if text_after:
                            # Пробел перед text_after только если он не начинается с пунктуации
                            if text_after[0] in '.!?,;:)»"\u00bb':
                                new_children.append(text_after)
                            else:
                                new_children.append(" " + text_after)
                        else:
                            # таймкод в конце — добавляем точку если её нет
                            prev_text = text_before.rstrip()
                            if prev_text and prev_text[-1] not in '.!?':
                                new_children.append(".")
                        i += 1
                        continue

                # ── Случай 2б: маркер в этой строке + следующая bold/italic-нода с таймкодом
                if i + 1 < len(children):
                    nxt = children[i + 1]
                    if isinstance(nxt, dict) and nxt.get("tag") in ("b", "i"):
                        bc = nxt.get("children", [])
                        bold_text = bc[0].strip() if bc and isinstance(bc[0], str) else ""
                        if TS_RE.match(bold_text):
                            marker = next((_m for _m in TS_MARKERS if _m in child), "")
                            lnk = _make_ts_link(marker, bold_text)
                            if lnk is not None:
                                _mp = child.rfind(marker) if marker else -1
                                text_before = child[:_mp].rstrip() if _mp >= 0 else child
                                # Текст в child ПОСЛЕ маркера (до конца строки) — Новый баг А
                                text_after_marker_2b = (
                                    child[_mp + len(marker):].strip()
                                    if (marker and _mp >= 0) else ""
                                )
                                if text_before:
                                    new_children.append(text_before + " ")
                                elif new_children and isinstance(new_children[-1], dict):
                                    new_children.append(" ")
                                new_children.append(lnk)
                                if text_after_marker_2b:
                                    sep2b = "" if text_after_marker_2b[0] in '.!?,;:)»"' else " "
                                    new_children.append(sep2b + text_after_marker_2b)
                                i += 2
                                if (
                                    i < len(children)
                                    and isinstance(children[i], str)
                                    and STRAY_RE.match(children[i])
                                ):
                                    i += 1
                                continue

            # ── Случай 3: fallback — plain bold timestamp без маркера ─────────────
            if allow_plain_bold_fallback:
                plain_ts = _is_plain_bold_timestamp(child)
                if plain_ts:
                    lnk = _make_ts_link("", plain_ts)
                    if lnk is not None:
                        new_children.append(lnk)
                        i += 1
                        continue

            # ── Случай 4: stray-нода (* / \* / _. / **.) — выбрасываем ───────────
            if isinstance(child, str) and STRAY_RE.match(child):
                stripped = child.strip()
                if stripped in ('*', r'\*', '_.', '**.', '**', '_'):
                    i += 1
                    continue

            new_children.append(child)
            i += 1

        return new_children

    result = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("tag") not in ("p", "blockquote"):
            result.append(node)
            continue

        children = list(node.get("children", []))
        allow_plain_bold_fallback = node.get("tag") == "p"
        new_children = _process_children(children, allow_plain_bold_fallback=allow_plain_bold_fallback)
        result.append({**node, "children": new_children})

    return result


def _section_to_nodes_v2(
    section: dict,
    yt_url: str = "",
    rutube_url: str = "",
    vk_url: str = "",
    page_title: str = "",
    duration: int = 0,
) -> list:
    """
    Конвертирует один section в Telegraph nodes.

    Стабилизированная версия:
    - меньше агрессивных regex-склеек;
    - лучше сохраняет numbered/bullet структуры;
    - убирает stray '*' возле таймкодов;
    - не тянет жирный через длинные куски;
    - не рендерит лишние divider lines.
    """
    nodes = []

    title = (section.get("title") or "").strip()
    time_str = (section.get("time") or "").strip()

    # Если Gemini вернул диапазон "0:03:50•0:12:40" или "0:03:50-0:12:40" — берём только начало
    if time_str:
        time_str = re.split(r"[•\-–—]", time_str)[0].strip()

    # Для аналитических страниц без yt_url "0:00" бессмысленно
    if time_str in ("0:00", "00:00", "0:00:00", "00:00:00") and not yt_url:
        time_str = ""

    if time_str and duration:
        secs = time_to_seconds(time_str)
        if secs is not None and secs > duration + 30:
            time_str = ""

    # Убираем тех. пометки частей
    title = re.sub(r'\s*\(ч\.\s*\d+(?:/\d+)?\)\s*', '', title).strip()
    title = re.sub(r'\s*\(часть\s*\d+(?:/\d+)?\)\s*', '', title, flags=re.IGNORECASE).strip()

    content = (section.get("content") or "").strip()
    content = _strip_meta_lines(content)

    # ------------------------------------------------------------------
    # 1. БАЗОВАЯ НОРМАЛИЗАЦИЯ ТЕКСТА
    # ------------------------------------------------------------------

    # Убираем явные markdown-артефакты вокруг скобок/заголовков
    content = re.sub(r'\*\*\*\[([^\]]+)\]\*\*\*', r'***\1***', content)
    content = re.sub(r'\*\*\[([^\]]+)\]\*\*', r'**\1**', content)
    content = re.sub(r'\[\*\*([^\]]+)\*\*\]', r'**\1**', content)
    content = re.sub(r'\*\[([^\]]+)\]\*', r'*\1*', content)

    # Слипание с жирным/цитатой
    content = re.sub(r'([а-яА-ЯёЁa-zA-Z])\*\*«', r'\1 **«', content)
    content = re.sub(r'»\*\*([а-яА-ЯёЁa-zA-Z])', r'»** \1', content)



    # Пустые / двойные blockquote markers
    content = re.sub(r'^>\s*>\s*', '> ', content, flags=re.MULTILINE)
    content = re.sub(r'^>\s*$', '', content, flags=re.MULTILINE)

    # Слишком много звёздочек
    content = re.sub(r'\*{4,}', '**', content)

    # Висячий bullet-маркер "*" -> bullet (включая "* **Bold**")
    content = re.sub(r'(?m)^\*\s+(\*\*|[^\*\n])', r'• \1', content)

    # Нормализуем ссылки на Писание в скобках -> курсив
    # поддерживает: (Рим. 8:28), (Рим. 8:28, 29), (Рим. 8:28; 1 Кор. 15:14), (1 Кор. 15:1–4)
    _scripture_book_re = (
        r'(?:Быт|Исх|Лев|Чис|Втор|Нав|Суд|Руфь|1\s*Цар|2\s*Цар|3\s*Цар|4\s*Цар|'
        r'1\s*Пар|2\s*Пар|Ездр|Неем|Есф|Иов|Пс|Притч|Еккл|Песн|Ис|Иер|Плач|'
        r'Иез|Дан|Ос|Иоил|Ам|Авд|Ион|Мих|Наум|Авв|Соф|Агг|Зах|Мал|'
        r'Мф|Мк|Лк|Ин|Деян|Рим|1\s*Кор|2\s*Кор|Гал|Еф|Фил|Кол|'
        r'1\s*Фес|2\s*Фес|1\s*Тим|2\s*Тим|Тит|Флм|Евр|Иак|'
        r'1\s*Пет|2\s*Пет|1\s*Ин|2\s*Ин|3\s*Ин|Иуд|Откр)'
    )
    _scripture_ref_re = re.compile(
        rf'(?<!\*)\('
        rf'(?:'
        rf'{_scripture_book_re}\.?\s*\d+:\d+(?:[-–]\d+)?'
        rf'(?:\s*,\s*\d+(?:[-–]\d+)?)?'
        rf'(?:\s*;\s*{_scripture_book_re}\.?\s*\d+:\d+(?:[-–]\d+)?(?:\s*,\s*\d+(?:[-–]\d+)?)?)*'
        rf')'
        rf'\)(?!\*)'
    )
    content = _scripture_ref_re.sub(lambda m: f'*{m.group(0)}*', content)

    # ------------------------------------------------------------------
    # 2. ЧИСТКА НЕЗАКРЫТЫХ BOLD И ТАЙМКОДНЫХ АРТЕФАКТОВ
    # ------------------------------------------------------------------

    # **Заголовок: без закрывающего ** в конце строки → закрываем
    # Например: "• **2 Коринфянам 5:17" или "**В материале:"
    content = re.sub(r'(?m)^(\s*[•\-]?\s*)\*\*([^*\n]+?)\s*$', r'\1**\2**', content)

    # **Метка:\nТекст... - разбиваем на bold-заголовок + обычный параграф
    # Например: "**В материале:\nВошер..." -> "**В материале:**\nВошер..."
    content = re.sub(r'\*\*([^*\n]{1,60}:)\n', r'**\1**\n', content)
    content = re.sub(r'(?m)^\s*\*\*(В материале:?)\s*$', r'**\1**', content)

    # Gemini оборачивает текст до таймкода в bold: **текст...icon** 16:15
    # Убираем лишний bold, оставляем только таймкод
    content = re.sub(r'\*\*(.{10,}?)(\u23F1|\U0001F550|\U0001F517)\*\*(\s*)(\d{1,2}:\d{2}(?::\d{2})?)', r'\1\2 **\4**', content)
    # Незакрытый ** внутри строки перед иконкой таймкода: **текст ⏱ 31:30 -> текст ⏱ **31:30**
    content = re.sub(r'\*\*([^*\n]{5,}?)(\s*)(\u23F1|\U0001F550|\U0001F517)(\s*)(\d{1,2}:\d{2}(?::\d{2})?)\s*$', r'\1\2\3 **\5**', content, flags=re.MULTILINE)

    content = re.sub(r'(⏱|🔗)?\s*\*\*(\d{1,2}:\d{2}(?::\d{2})?)\*\*', r'\1 **\2**', content)
    content = re.sub(r'(⏱|🔗)?\s*\*\*(\d{1,2}:\d{2}(?::\d{2})?)(?!\*)(?=\s*$)', r'\1 **\2**', content, flags=re.MULTILINE)
    content = re.sub(r'(⏱|🔗)?\s*\*\*(\d{1,2}:\d{2}(?::\d{2})?)(?!\*)(?!:\d)', r'\1 **\2**', content)
    content = re.sub(r'\*\*(⏱|🔗)', r'** \1', content)

    # **Текст ⏱** 26:00 → Текст ⏱ 26:00  (AI оборачивает весь блок в bold с иконкой внутри)
    content = re.sub(r'\*\*(.+?)\s*⏱\*\*\s*(\d{1,2}:\d{2}(?::\d{2})?)(\s*)', r'\1 ⏱ \2\3', content)
    # Остаток **. после таймкода (только если пробел перед **, иначе это закрывающий bold)
    content = re.sub(r'(\d{1,2}:\d{2}(?::\d{2})?)\s+\*\*\s*\.?\s*$', r'\1', content, flags=re.MULTILINE)
    # Абзац из одиночного знака пунктуации (AI-артефакт вида "\n\n.\n\n")
    content = re.sub(r'\n{2,}[.,:;]\n{2,}', '\n\n', content)
    content = re.sub(r'(⏱|🔗)?\s*_\s*(\d{1,2}:\d{2}(?::\d{2})?)', r'\1 **\2**', content)

    content = re.sub(r'\s*\\\*\s*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'(?<!\*)(\d{1,2}:\d{2}(?::\d{2})?)\s*\*+\s*$', r'\1', content, flags=re.MULTILINE)
    content = re.sub(r'(\d{1,2}:\d{2}(?::\d{2})?)\s+\*(?!\*)', r'\1 ', content)
    content = re.sub(r'(?<!\*)(\d{1,2}:\d{2}(?::\d{2})?)\s*\*{2}\.?\s*$', r'\1', content, flags=re.MULTILINE)
    content = re.sub(r'(\d{1,2}:\d{2}(?::\d{2})?)\s*_\.?', r'\1', content)
    content = re.sub(r'(?<!\*)\*(?!\*)(\s*\d{1,2}:\d{2}(?::\d{2})?)', r'\1', content)

    content = re.sub(r'([\wа-яА-ЯёЁ])([⏱🔗])', r'\1 \2', content)
    content = re.sub(r'  +(\d{1,2}:\d{2}(?::\d{2})?)', r' \1', content)

    # ------------------------------------------------------------------
    # 3. ЧИСТКА СКЛЕЕК И ПРОБЕЛОВ
    # ------------------------------------------------------------------

    # Пробел после точки/восклиц./вопроса/двоеточия, если дальше буква
    content = re.sub(r'([.!?…:])([А-ЯЁA-Z])', r'\1 \2', content)
    content = re.sub(r'([.!?…:])(\*\*)', r'\1 \2', content)

    # Пробел вокруг тире
    content = re.sub(r'([\wа-яА-ЯёЁ\*\)])\s*—\s*([\wа-яА-ЯёЁ\*\(])', r'\1 — \2', content)
    content = re.sub(r'([\wа-яА-ЯёЁ\*\)])\s*–\s*([\wа-яА-ЯёЁ\*\(])', r'\1 – \2', content)

    # "слово(**" / ")(буква"
    content = re.sub(r'([а-яА-ЯёЁa-zA-Z\*])\(', r'\1 (', content)
    content = re.sub(r'\)([а-яА-ЯёЁa-zA-Z])', r') \1', content)

    # Стык кириллицы/латиницы
    content = re.sub(r'([а-яА-ЯёЁ])([a-zA-Z])', r'\1 \2', content)
    content = re.sub(r'([a-zA-Z])([а-яА-ЯёЁ])', r'\1 \2', content)

    # Двойные пробелы
    content = re.sub(r'[ \t]{2,}', ' ', content)

    # ------------------------------------------------------------------
    # 4. СТАБИЛИЗАЦИЯ NUMBERED / BULLET / MICRO-BLOCK СТРУКТУРЫ
    # ------------------------------------------------------------------

    lines = content.split("\n")
    rebuilt_lines = []
    j = 0

    while j < len(lines):
        raw = lines[j]
        s = raw.strip()

        # пропускаем мусорные divider-повторы — hr потом поставит markdown-конвертер
        if re.match(r'^[-=_*═]{3,}$', s):
            # не копим по два divider подряд
            if rebuilt_lines and re.match(r'^\s*[-=_*═]{3,}\s*$', rebuilt_lines[-1].strip()):
                j += 1
                continue
            rebuilt_lines.append('---')
            j += 1
            continue

        # heading markdown внутри content -> bold line
        if s.startswith("#### "):
            rebuilt_lines.append(f"**{s[5:].strip()}**")
            j += 1
            continue
        if s.startswith("### "):
            rebuilt_lines.append(f"**{s[4:].strip()}**")
            j += 1
            continue
        if s.startswith("## "):
            rebuilt_lines.append(f"**{s[3:].strip()}**")
            j += 1
            continue
        if s.startswith("# "):
            rebuilt_lines.append(f"**{s[2:].strip()}**")
            j += 1
            continue

        # Если строка оканчивается на ":" и следующая — bullet/numbered/bold-заголовок,
        # вставляем paragraph boundary. Обычное продолжение текста — не трогаем.
        if s.endswith(":") and j + 1 < len(lines):
            nxt = lines[j + 1].strip()
            if nxt and re.match(r'^([•\-]|\d+\.|\*\*[^*])', nxt):
                rebuilt_lines.append(s)
                rebuilt_lines.append("")
                j += 1
                continue

        # number-only line: "1." -> подтягиваем следующую строку
        if re.match(r'^\d+\.$', s) and j + 1 < len(lines):
            nxt = lines[j + 1].strip()
            if nxt:
                rebuilt_lines.append(f"{s} {nxt}")
                j += 2
                continue

        # "-Текст" -> "- Текст"
        if re.match(r'^-[^\s]', s):
            s = re.sub(r'^-([^\s])', r'- \1', s)

        rebuilt_lines.append(s if s else "")
        j += 1

    content = "\n".join(rebuilt_lines)

    # ------------------------------------------------------------------
    # 5. АККУРАТНАЯ РАБОТА С ЖИРНЫМ
    # ------------------------------------------------------------------

    # Плохо: "**Тезис.Длинное тело без пробела..."
    content = re.sub(
        r'(\*\*[^*\n]{3,120}[.!?»"]\*\*)([А-ЯЁA-Z])',
        r'\1\n\n\2',
        content
    )

    # numbered title + bold title должны оставаться на одной строке
    content = re.sub(
        r'(?m)^(\d+\.)\s*\n+\s*(\*\*[^*\n]+\*\*)',
        r'\1 \2',
        content
    )

    # Слишком длинный bold-абзац -> если он реально длинный, не держим всё жирным
    def _shrink_overbold_line(m):
        inner = m.group(1).strip()
        # Сохраняем таймкод до его удаления (Bug #4)
        _ts_save = re.search(r'(([⏱🔗]\s*)?\*{0,2}\d{1,2}:\d{2}(?::\d{2})?\*{0,2})\s*$', inner)
        ts_suffix = ""
        if _ts_save:
            _raw = _ts_save.group(1).strip()
            _tm = re.search(r'(\d{1,2}:\d{2}(?::\d{2})?)', _raw)
            _mk = re.search(r'([⏱🔗])', _raw)
            if _tm:
                ts_suffix = ((_mk.group(1) + " ") if _mk else "") + f"**{_tm.group(1)}**"
        inner = re.sub(r'\s*[⏱🔗]?\s*\**\s*\d{1,2}:\d{2}(?::\d{2})?\s*\**\s*$', '', inner).strip()
        inner = re.sub(r'\s+\d{1,2}:\d{2}(?::\d{2})?\s*$', '', inner).strip()
        _suffix = (" " + ts_suffix) if ts_suffix else ""
        if len(inner) > 120:
            split_m = re.search(r'(?<=[.!?])\s+(?=[А-ЯЁA-Z])', inner)
            if split_m:
                head = inner[:split_m.start()].strip()
                tail = inner[split_m.end():].strip()
                return f"**{head}**\n\n{tail}{_suffix}"
        return f"**{inner}**{_suffix}"

    content = re.sub(
        r'(?m)^\*\*([^\n*]{120,})\*\*$',
        _shrink_overbold_line,
        content
    )

    # Ложный курсив-блок (Gemini обернул весь абзац в *...*) — убираем обёртку,
    # оставляем внутренний **bold** нетронутым.
    def _strip_italic_wrap(m):
        return m.group(1)

    # Строки 80+ символов
    content = re.sub(
        r'(?m)^\*([^\n*]{80,})\*\s*$',
        _strip_italic_wrap,
        content
    )
    # Строки 20–79 символов (короткие описательные абзацы)
    content = re.sub(
        r'(?m)^\*([А-ЯЁA-Zа-яёa-z«][^\n]{20,}[^\n*])\*$',
        _strip_italic_wrap,
        content
    )

    content = _fix_orphaned_bold_markers(content)
    if duration:
        content = _clamp_content_timestamps(content, duration)

    # ------------------------------------------------------------------
    # 6. ФИНАЛЬНАЯ НОРМАЛИЗАЦИЯ АБЗАЦЕВ
    # ------------------------------------------------------------------

    # Не даём схлопнуться служебным заголовкам аудиторий/разделов с первым подпунктом
    content = re.sub(
        r'(?m)^(?!\d+\.)\s*([^\n:]{5,120}(?<!\d):)\s+([•\-]|\d+\.|\*\*)',
        r'\1\n\n\2',
        content
    )

    # Нумерованные prayer blocks: если "1. Текст" и потом сразу тело в той же строке
    # допускаем, это лучше чем разваленный номер на отдельной строке
    content = re.sub(r'\n{3,}', '\n\n', content).strip()
    content = _ensure_all_paragraphs_period(content)
    # Убираем 2+ точек в конце строки (AI поставил точку + наш код добавил ещё одну).
    # (?=\s*$) гарантирует что трогаем только конец строки, не .. внутри текста.
    content = re.sub(r'\.{2,}(?=\s*$)', '.', content, flags=re.MULTILINE)
    content = re.sub(r'\?\.\s*\.', '?.', content)

    # ------------------------------------------------------------------
    # 7. H3 + TIMESTAMP LINKS В ЗАГОЛОВКЕ SECTION
    # ------------------------------------------------------------------

    _title_is_page_title = (
        page_title and title and
        title.strip().lower() == page_title.strip().lower()
    )

    if title and not _title_is_page_title:
        nodes.append({"tag": "h3", "children": [title]})

        if time_str:
            secs = time_to_seconds(time_str) if time_str else None
            ts_parts = []

            if yt_url and secs is not None:
                yt_link = get_youtube_timestamp_url(yt_url, secs)
                ts_parts.append("⏱\u00a0")
                ts_parts.append({
                    "tag": "a",
                    "attrs": {"href": yt_link},
                    "children": [f"YouTube {time_str}"]
                })
            elif yt_url:
                ts_parts.append(f"⏱\u00a0YouTube {time_str}")

            if rutube_url and secs is not None:
                rt_base = rutube_url.rstrip("/")
                rt_link = rt_base + (("&" if "?" in rt_base else "?") + f"t={secs}")
                if ts_parts:
                    ts_parts.append(" · ")
                ts_parts.append("⏱\u00a0")
                ts_parts.append({
                    "tag": "a",
                    "attrs": {"href": rt_link},
                    "children": [f"RuTube {time_str}"]
                })

            if vk_url and secs is not None:
                vk_link = vk_url + (("&" if "?" in vk_url else "?") + f"t={secs}s")
                if ts_parts:
                    ts_parts.append(" · ")
                ts_parts.append("⏱\u00a0")
                ts_parts.append({
                    "tag": "a",
                    "attrs": {"href": vk_link},
                    "children": [f"VK {time_str}"]
                })

            if ts_parts:
                nodes.append({"tag": "p", "children": [
                    {"tag": "i", "children": ts_parts}
                ]})
            elif time_str:
                nodes.append({"tag": "p", "children": [
                    {"tag": "i", "children": [f"⏱\u00a0{time_str}"]}
                ]})

    # ------------------------------------------------------------------
    # 8. CONTENT -> TELEGRAPH NODES
    # ------------------------------------------------------------------

    if content:
        content_nodes = _md_to_telegraph_nodes(content)
        if yt_url:
            content_nodes = _linkify_inline_timestamps(content_nodes, yt_url, duration=duration)
        nodes.extend(content_nodes)

    return nodes


def _estimate_nodes_v2(sections: list, yt_url: str = "",
                       rutube_url: str = "", vk_url: str = "",
                       duration: int = 0) -> int:
    """Оценивает кол-во Telegraph nodes для списка разделов."""
    return sum(
        len(_section_to_nodes_v2(s, yt_url=yt_url,
                                  rutube_url=rutube_url, vk_url=vk_url,
                                  duration=duration))
        for s in sections
    )


def _split_sections_smart(sections: list, node_limit: int = 180, yt_url: str = "") -> list:
    """
    Делит sections на части для Telegraph по границам разделов.
    Никогда не разрывает section посередине.
    Если последняя часть < 25% лимита — объединяет с предыдущей (если итог ≤ лимита).
    Возвращает список групп (list of list of section).
    """
    if not sections:
        return [[]]

    parts: list[list] = []
    current: list     = []
    current_count: int = 0

    for sec in sections:
        sec_size = len(_section_to_nodes_v2(sec, yt_url))
        if current and current_count + sec_size > node_limit:
            parts.append(current)
            current = [sec]
            current_count = sec_size
        else:
            current.append(sec)
            current_count += sec_size

    if current:
        parts.append(current)

    # Последняя часть < 25% лимита — объединяем с предыдущей только если влезает
    if len(parts) >= 2:
        last_size = _estimate_nodes_v2(parts[-1], yt_url)
        prev_size = _estimate_nodes_v2(parts[-2], yt_url)
        if last_size < node_limit * 0.25 and prev_size + last_size <= node_limit:
            parts = parts[:-2] + [parts[-2] + parts[-1]]

    return parts or [[]]


def _split_sections_recursive(sections: list, node_limit: int, yt_url: str = "") -> list:
    """
    Рекурсивно делит sections пока каждая часть не влезает в node_limit.
    Единственный способ нарушить — section из одного элемента > node_limit
    (тогда публикуем как есть, Telegraph примет или вернёт ошибку).
    """
    if not sections:
        return [[]]

    total_nodes = _estimate_nodes_v2(sections, yt_url)
    if total_nodes <= node_limit:
        return [sections]

    # Нельзя делить одиночный section
    if len(sections) == 1:
        return [sections]

    mid = max(1, len(sections) // 2)
    left  = sections[:mid]
    right = sections[mid:]

    left_result  = _split_sections_recursive(left, node_limit, yt_url)
    right_result = _split_sections_recursive(right, node_limit, yt_url)
    return left_result + right_result


def _build_toc_nodes_v2(outline: list, yt_url: str = "", parts: list = None, duration: int = 0) -> list:
    """Строит оглавление «Структура материала» с кликабельными таймкодами."""
    nodes = []
    nodes.append({"tag": "p", "children": [
        {"tag": "b", "children": [" Структура материала"]}
    ]})

    for i, item in enumerate(outline, 1):
        title = (item.get("title") or "").strip()
        time_str = (item.get("time") or "").strip()

        if time_str in ("0:00", "00:00", "0:00:00", "00:00:00") and not yt_url:
            time_str = ""

        if time_str and duration:
            secs = time_to_seconds(time_str)
            if secs is not None and secs > duration + 30:
                time_str = ""

        title = re.sub(r'\s*\(ч\.\s*\d+(?:/\d+)?\)\s*', '', title).strip()
        title = re.sub(r'\s*\(часть\s*\d+(?:/\d+)?\)\s*', '', title, flags=re.IGNORECASE).strip()

        children = [{"tag": "b", "children": [f"{i}.\u00a0"]}]
        children.append("\u00a0" + title)

        if time_str and yt_url:
            secs = time_to_seconds(time_str)
            if secs is not None:
                yt_link = get_youtube_timestamp_url(yt_url, secs)
                children.append(" — ⏱\u00a0")
                children.append({
                    "tag": "a",
                    "attrs": {"href": yt_link},
                    "children": [time_str],
                })
            else:
                children.append(f" — ⏱\u00a0{time_str}")
        elif time_str:
            children.append(f" — ⏱\u00a0{time_str}")

        nodes.append({"tag": "p", "children": children})

    nodes.append({"tag": "hr"})
    return nodes


def _build_nav_nodes_v2(part_idx: int, total_parts: int, parts_urls: list) -> list:
    """
    Строит навигационный блок между частями в формате [N/total].
    Пример: ⬅ Назад: [1/3]     ➡ Дальше: [3/3]
    """
    if total_parts <= 1:
        return []

    nodes = [{"tag": "hr"}]
    nav_children = []

    if part_idx > 0:
        prev_url = parts_urls[part_idx - 1] if part_idx - 1 < len(parts_urls) else None
        if prev_url:
            nav_children.append({
                "tag": "a",
                "attrs": {"href": prev_url},
                "children": [f"⬅ Назад: [{part_idx}/{total_parts}]"],
            })

    if part_idx < total_parts - 1:
        next_url = parts_urls[part_idx + 1] if part_idx + 1 < len(parts_urls) else None
        if next_url:
            if nav_children:
                nav_children.append("     ")
            nav_children.append({
                "tag": "a",
                "attrs": {"href": next_url},
                "children": [f"➡ Дальше: [{part_idx + 2}/{total_parts}]"],
            })

    if nav_children:
        nodes.append({"tag": "p", "children": nav_children})

    return nodes


async def _create_telegraph_page_single(title: str, author: str,
                                         nodes: list, loop) -> tuple:
    """Публикует одну Telegraph-страницу. Возвращает (url, error).
    НЕ усекает контент — CONTENT_TOO_BIG возвращается как ошибка
    для обработки выше по стеку через дробление sections.
    """
    token = os.getenv("TELEGRAPH_TOKEN", "").strip()
    if not token:
        return None, "no_token"
    nodes = _clean_telegraph_nodes(nodes)
    try:
        resp = await loop.run_in_executor(None, lambda: requests.post(
            "https://api.telegra.ph/createPage",
            json={
                "access_token": token,
                "title": title,
                "author_name": "",
                "content": nodes,
                "return_content": False,
            },
            timeout=15,
        ))
        data = resp.json()
        if data.get("ok"):
            return data["result"]["url"], ""
        return None, data.get("error", "unknown")
    except Exception as e:
        logger.warning(f"Telegraph createPage: {e}")
        return None, str(e)


async def _edit_telegraph_page(page_url: str, title: str, author: str,
                                nodes: list, loop) -> bool:
    """Обновляет существующую Telegraph-страницу навигационными ссылками."""
    token = os.getenv("TELEGRAPH_TOKEN", "").strip()
    if not token:
        return False
    nodes = _clean_telegraph_nodes(nodes)
    try:
        path = page_url.replace("https://telegra.ph/", "")
        resp = await loop.run_in_executor(None, lambda: requests.post(
            f"https://api.telegra.ph/editPage/{path}",
            json={
                "access_token": token,
                "title": title,
                "author_name": "",
                "content": nodes,
                "return_content": False,
            },
            timeout=15,
        ))
        data = resp.json()
        return bool(data.get("ok"))
    except Exception as e:
        logger.warning(f"Telegraph editPage: {e}")
        return False


# ─── Synopsis v2.0: helper для Gemini ────────────────────────

def _extract_partial_sections(text: str) -> list:
    """
    Пытается вытащить секции из обрезанного/сломанного JSON конспекта.
    Ищет все законченные объекты {"title":..., "time":..., "content":...}
    и возвращает их как список. Если ничего не найдено — возвращает [].
    """
    sections = []
    # Ищем все блоки с title+content через regex — даже в неполном JSON
    pattern = re.compile(
        r'\{[^{}]*"title"\s*:\s*"([^"]*)"[^{}]*"time"\s*:\s*"([^"]*)"[^{}]*"content"\s*:\s*"((?:[^"\\]|\\.)*)"',
        re.DOTALL,
    )
    for m in pattern.finditer(text):
        title_val   = m.group(1).strip()
        time_val    = m.group(2).strip() or "0:00"
        content_val = m.group(3).replace("\\n", "\n").replace('\\"', '"').strip()
        if title_val and content_val:
            sections.append({"title": title_val, "time": time_val, "content": content_val})
    return sections


async def _synopsis_with_client(client, upload_fn, prompt, loop):
    """Helper: загружает файл через клиент и вызывает generate_content.
    Если файл был загружен через Files API (file_size_mb > 20), удаляет его после ответа.
    """
    audio_part = await upload_fn(client)
    _uploaded = getattr(audio_part, "name", None) is not None
    try:
        return await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=[audio_part, prompt],
            config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=32000),
        )
    finally:
        if _uploaded:
            try:
                await client.aio.files.delete(name=audio_part.name)
            except Exception:
                pass


# ─── create_telegraph_synopsis v2.0 ──────────────────────────

async def create_telegraph_synopsis(mp3_path, title, performer, duration, url="",
                                     existing_audio_part=None, existing_client=None,
                                     ai_data=None, rutube_url="", vk_url=""):
    """
    v2.1 — Format-aware конспект в Telegraph.

    1. Gemini возвращает JSON {outline, sections}.
    2. Для format=qa использует SYNOPSIS_PROMPT_QA с timestamps как каркасом.
    3. Для остальных форматов — обычный SYNOPSIS_PROMPT_V2.
    4. Деление на части — только по границам разделов.
    5. Навигация добавляется через editPage после публикации всех частей.
    """
    try:
        if not GEMINI_CLIENTS:
            logger.warning("Synopsis: нет GEMINI_CLIENTS — пропускаем")
            return None, None

        try:
            _duration = float(duration)
        except (ValueError, TypeError):
            _duration = 0

        duration_str = format_timestamp(_duration)
        _fmt         = (ai_data or {}).get("format", "other") or "other"
        _timestamps  = (ai_data or {}).get("timestamps", "") or ""
        _real_author = normalize_author_name((ai_data or {}).get("real_author") or performer)
        author       = _real_author or normalize_author_name(performer) or "Проповедь"
        _real_title  = normalize_title_text((ai_data or {}).get("real_title") or "") or ""
        prompt_title = _real_title or normalize_title_text(title) or title
        prompt_title = title_case_fragment(prompt_title)
        tg_title     = prompt_title
        if author and author != "Проповедь":
            tg_title = f"{tg_title} — {author}"

        is_qa = (_fmt == "qa")
        _ts_lines = [l.strip() for l in _timestamps.splitlines() if l.strip()]

        if is_qa and _ts_lines:
            timestamps_block = "\n".join(f"- {l}" for l in _ts_lines)
            _qa_prompt_template = SYNOPSIS_PROMPT_QA
            logger.info("Synopsis QA: единый промпт для всех вызовов")
            prompt = _qa_prompt_template.format(
                title=prompt_title,
                duration=duration_str,
                timestamps_block=timestamps_block,
            )
            logger.info(
                f"Synopsis: mode=QA format={_fmt} questions={len(_ts_lines)} "
                f"duration={int(_duration)//60}min"
            )
        else:
            _hm = (ai_data or {}).get("hermeneutic_method") or "mixed"

            if _duration < 1200:
                _syn_sections    = "3-5"
                _syn_section_len = "200-700"
                _syn_total       = "1200"
            elif _duration < 3000:
                _syn_sections    = "4-8"
                _syn_section_len = "350-1100"
                _syn_total       = "2500"
            elif _duration < 5400:
                _syn_sections    = "6-12"
                _syn_section_len = "400-1200"
                _syn_total       = "4000"
            else:
                _syn_sections    = "8-16"
                _syn_section_len = "400-1300"
                _syn_total       = "5500"
            prompt = SYNOPSIS_PROMPT_V2.format(
                title=prompt_title,
                duration=duration_str,
                hermeneutic_method=_hm,
                syn_sections=_syn_sections,
                syn_section_len=_syn_section_len,
                syn_total=_syn_total,
            )
            logger.info(
                f"Synopsis: mode=normal format={_fmt} hermeneutic={_hm} duration={int(_duration)//60}min"
            )
        loop   = asyncio.get_running_loop()
        file_size_mb = mp3_path.stat().st_size / (1024 * 1024)

        def _extract_response_text(resp) -> str:
            if resp is None:
                return ""
            try:
                return (resp.text or "").strip()
            except Exception:
                chunks = []
                candidates = getattr(resp, "candidates", None) or []
                if candidates:
                    first = candidates[0]
                    content = getattr(first, "content", None)
                    parts = getattr(content, "parts", None) or []
                    for part in parts:
                        if not getattr(part, "thought", False):
                            text_part = getattr(part, "text", None)
                            if text_part:
                                chunks.append(text_part)
                return "".join(chunks).strip()

        # ── Запрос к Gemini ───────────────────────────────────
        response = None
        await asyncio.sleep(5)  # Пауза после анализа аудио — снижаем риск квоты

        if existing_audio_part is not None and existing_client is not None:
            try:
                response = await existing_client.aio.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=[existing_audio_part, prompt],
                    config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=32000),
                )
            except Exception as e:
                logger.warning(f"Synopsis v2 existing_audio_part failed: {e}, re-uploading...")
                response = None

        if response is None:
            async def _upload(client):
                if file_size_mb > 20:
                    uf = await client.aio.files.upload(
                        file=mp3_path,
                        config=types.UploadFileConfig(
                            mime_type="audio/mpeg",
                            display_name=f"{performer} - {title}",
                        ),
                    )
                    while uf.state == "PROCESSING":
                        await asyncio.sleep(3)
                        uf = await client.aio.files.get(name=uf.name)
                    return uf
                else:
                    audio_bytes = mp3_path.read_bytes()
                    return types.Part.from_bytes(data=audio_bytes, mime_type="audio/mpeg")

            async def _call_synopsis(client):
                return await _synopsis_with_client(client, _upload, prompt, loop)

            response = await gemini_generate(GEMINI_CLIENTS, _call_synopsis)

        # ── Извлекаем текст ответа ────────────────────────────
        synopsis_text = _extract_response_text(response)
        if not synopsis_text:
            return None, None

        # ── Парсим JSON (с retry при сломанном JSON) ─────────

        def _try_parse_synopsis_json(text: str) -> tuple[list, list] | None:
            """Возвращает (outline, sections) или None если не распарсилось."""
            try:
                clean = text.strip()
                clean = re.sub(r"^```[a-z]*\s*", "", clean)
                clean = re.sub(r"\s*```$",       "", clean).strip()
                s = clean.find("{")
                e = clean.rfind("}")
                if s == -1 or e <= s:
                    return None
                chunk = clean[s:e + 1]
                # Защита: Gemini иногда вставляет реальные переносы строк
                # внутри JSON-строк — это невалидный JSON. Экранируем их.
                def _fix_json_newlines(s: str) -> str:
                    result, in_str, i = [], False, 0
                    while i < len(s):
                        c = s[i]
                        if c == "\\" and i + 1 < len(s):
                            result.append(c + s[i + 1]); i += 2; continue
                        if c == '"': in_str = not in_str
                        if in_str and c == "\n": result.append("\\n"); i += 1; continue
                        if in_str and c == "\r": result.append("\\r"); i += 1; continue
                        result.append(c); i += 1
                    return "".join(result)
                try:
                    data = json.loads(chunk)
                except Exception:
                    data = json.loads(_fix_json_newlines(chunk))
                return data.get("outline", []) or [], data.get("sections", []) or []
            except Exception:
                return None

        def _looks_like_json(text: str) -> bool:
            t = text.strip()
            return t.startswith("{") or '"sections"' in t or '"outline"' in t

        sections: list = []
        outline:  list = []

        parsed = _try_parse_synopsis_json(synopsis_text)
        if parsed is not None:
            outline, sections = parsed
            logger.info("Synopsis: JSON распарсен успешно")
        elif _looks_like_json(synopsis_text):
            # Сломанный JSON → один retry с пониженной температурой
            logger.warning("Synopsis: сломанный JSON от Gemini — выполняю retry")
            await asyncio.sleep(10)  # Пауза перед retry
            retry_response = None
            try:
                retry_prompt = (
                    "Твой предыдущий ответ содержал сломанный JSON. "
                    "Повтори запрос строго: только валидный JSON {outline, sections}, "
                    "без ```json, без текста до/после.\n\n" + prompt
                )
                if existing_audio_part is not None and existing_client is not None:
                    try:
                        retry_response = await existing_client.aio.models.generate_content(
                            model=GEMINI_MODEL,
                            contents=[existing_audio_part, retry_prompt],
                            config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=32000),
                        )
                    except Exception as re_e:
                        logger.warning(f"Synopsis retry existing_audio_part failed: {re_e}")
                        retry_response = None
                # Определяем _upload_retry здесь — вне условия — чтобы она была
                # доступна и в _call_synopsis_simple (3-й retry), даже если
                # 2-й retry прошёл через ветку existing_audio_part и этот блок
                # не выполнялся. Иначе возможен NameError.
                async def _upload_retry(client):
                    if file_size_mb > 20:
                        uf = await client.aio.files.upload(
                            file=mp3_path,
                            config=types.UploadFileConfig(
                                mime_type="audio/mpeg",
                                display_name=f"{performer} - {title}",
                            ),
                        )
                        while uf.state == "PROCESSING":
                            await asyncio.sleep(3)
                            uf = await client.aio.files.get(name=uf.name)
                        return uf
                    else:
                        return types.Part.from_bytes(data=mp3_path.read_bytes(), mime_type="audio/mpeg")
                if retry_response is None:
                    async def _call_synopsis_retry(client):
                        return await _synopsis_with_client(client, _upload_retry, retry_prompt, loop)
                    retry_response = await gemini_generate(GEMINI_CLIENTS, _call_synopsis_retry)
                retry_text = _extract_response_text(retry_response)
                parsed_retry = _try_parse_synopsis_json(retry_text)
                if parsed_retry is not None:
                    outline, sections = parsed_retry
                    logger.info("Synopsis: retry успешен — JSON распарсен")
                else:
                    logger.warning("Synopsis: retry тоже вернул сломанный JSON — пробую упрощённый промпт")
                    # ── 3-й retry: упрощённый промпт (6 секций, меньше текста) ──
                    simple_prompt = (
                        f"Создай краткий конспект материала «{title}» ({duration_str}).\n"
                        "Только валидный JSON без ```json и без текста вне JSON.\n"
                        "Ровно 6 разделов, каждый не более 150 слов.\n\n"
                        '{{\n'
                        '  "outline": [{{"title": "...", "time": "0:00"}}, ...],\n'
                        '  "sections": [{{"title": "...", "time": "0:00", "content": "..."}}, ...]\n'
                        '}}'
                    )
                    simple_response = None
                    try:
                        if existing_audio_part is not None and existing_client is not None:
                            try:
                                simple_response = await existing_client.aio.models.generate_content(
                                    model=GEMINI_MODEL,
                                    contents=[existing_audio_part, simple_prompt],
                                    config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=32000),
                                )
                            except Exception:
                                simple_response = None
                        if simple_response is None:
                            async def _call_synopsis_simple(client):
                                return await _synopsis_with_client(client, _upload_retry, simple_prompt, loop)
                            simple_response = await gemini_generate(GEMINI_CLIENTS, _call_synopsis_simple)
                        simple_text = _extract_response_text(simple_response)
                        parsed_simple = _try_parse_synopsis_json(simple_text)
                        if parsed_simple is not None:
                            outline, sections = parsed_simple
                            logger.info("Synopsis: упрощённый retry успешен")
                        else:
                            # Все три попытки провалились — Markdown-fallback из лучшего текста
                            logger.warning("Synopsis: все 3 попытки дали сломанный JSON — Markdown fallback")
                            best_text = retry_text if len(retry_text) > len(synopsis_text) else synopsis_text
                            # Пробуем вытащить хоть что-то из частично сгенерированного JSON
                            _partial = _extract_partial_sections(best_text)
                            if _partial:
                                outline, sections = [], _partial
                                logger.info(f"Synopsis: восстановлено {len(sections)} секций из неполного JSON")
                            else:
                                content = _md_to_telegraph_nodes(best_text)
                                return await _telegraph_post(tg_title, author, content, loop), None
                    except Exception as simple_err:
                        logger.warning(f"Synopsis: упрощённый retry exception: {simple_err} — Markdown fallback")
                        content = _md_to_telegraph_nodes(retry_text or synopsis_text)
                        return await _telegraph_post(tg_title, author, content, loop), None
            except Exception as retry_err:
                logger.warning(f"Synopsis: retry exception: {retry_err} — abort")
                return None, None
        else:
            # Не JSON вообще → Markdown fallback (как было)
            logger.warning("Synopsis: не-JSON от Gemini — Markdown fallback")
            content = _md_to_telegraph_nodes(synopsis_text)
            return await _telegraph_post(tg_title, author, content, loop), None

        # ── Валидация sections ────────────────────────────────
        if not isinstance(sections, list):
            logger.warning("Synopsis v2: sections не является списком — abort")
            return None, None
        if not isinstance(outline, list):
            outline = []

        sections = [s for s in sections if isinstance(s, dict)]
        if not sections:
            logger.warning("Synopsis v2: sections пуст после валидации — abort")
            return None, None

        total_before_filter = len(sections)
        sections = [s for s in sections if (s.get("content") or "").strip()]
        removed_empty = total_before_filter - len(sections)
        if removed_empty > 0:
            logger.warning(
                f"Synopsis v2: удалено пустых sections={removed_empty} "
                f"из {total_before_filter}"
            )

        if not sections:
            logger.warning("Synopsis v2: все sections пусты после фильтрации content — abort")
            return None, None

        # ── Защита от рассинхрона outline / sections ─────────
        # Для QA: промпт инструктирует модель НЕ включать 4 секции применения
        # (малая группа, семья, самоиспытание, молитва) в outline — у них time="".
        # Дополняем outline этими секциями явно, чтобы они попали в оглавление
        # без таймкода и без ложного rebuild-предупреждения.
        outline_rebuilt = False
        # Всегда строим финальный outline из sections — sections является единственным
        # источником правды. outline от Gemini используем только для извлечения таймкодов
        # (Gemini иногда пропускает последний раздел или путает порядок).
        if outline and len(outline) == len(sections):
            # Синхронизируем: берём title/time из sections, но если outline дал таймкод
            # для этой позиции — доверяем ему (он точнее при QA-формате)
            merged = []
            for sec, oi in zip(sections, outline):
                merged.append({
                    "title": sec.get("title", ""),
                    "time":  (oi.get("time") or sec.get("time") or ""),
                })
            outline = merged
        else:
            # Длины не совпадают — полностью перестраиваем из sections
            outline = [
                {"title": sec.get("title", ""), "time": sec.get("time", "")}
                for sec in sections
            ]
            outline_rebuilt = True

        # ── Диагностика ───────────────────────────────────────
        try:
            total_nodes_estimate = _estimate_nodes_v2(
                sections, url, rutube_url=rutube_url, vk_url=vk_url
            )
        except Exception:
            total_nodes_estimate = -1

        mins = int(_duration) // 60
        _mode_label = f"QA({len(_ts_lines)}q)" if is_qa else f"normal({_fmt})"
        logger.info(
            f"Synopsis v2: mode={_mode_label} sections={len(sections)} outline={len(outline)} "
            f"outline_rebuilt={outline_rebuilt} nodes_estimate={total_nodes_estimate} "
            f"duration={mins}min"
        )

        if is_qa:
            question_like_titles = sum(
                1 for s in sections
                if ((s.get("title") or "").strip().endswith("?"))
            )
            logger.info(
                f"Synopsis QA: question_like_titles={question_like_titles}/{len(sections)}"
            )
            if len(sections) >= 3 and question_like_titles < max(1, len(sections) // 2):
                logger.warning(
                    "Synopsis QA: значительная часть заголовков не выглядит вопросительной — "
                    "проверь качество Q&A-структуры"
                )

            titles = [(s.get("title") or "").strip() for s in sections]
            non_empty_titles = [t for t in titles if t]
            dup_count = len(non_empty_titles) - len(set(non_empty_titles))
            if dup_count > 0:
                logger.warning(
                    f"Synopsis QA: обнаружены повторяющиеся заголовки={dup_count} — "
                    "это может быть как реальное совпадение вопросов, так и признак деградации структуры"
                )

        if is_qa and _ts_lines and len(sections) < len(_ts_lines):
            logger.warning(
                f"Synopsis QA: sections={len(sections)} < questions={len(_ts_lines)} "
                f"— часть вопросов может быть пропущена в конспекте"
            )

        if _duration > 1800 and len(sections) < 4:
            logger.warning(
                f"Synopsis v2: мало sections ({len(sections)}) для {mins}мин "
                f"— возможно неполный конспект"
            )

        if _duration > 600 and total_nodes_estimate != -1 and total_nodes_estimate < 20:
            logger.warning(
                f"Synopsis v2: мало nodes ({total_nodes_estimate}) для {mins}мин "
                f"— конспект может быть слишком коротким"
            )

        # ── Рекурсивная публикация по sections ───────────────
        # Стратегия: publish-first → при CONTENT_TOO_BIG делить sections пополам.
        # Деление только по границам sections. Никакой обрезки нод.
        # Если любая часть не публикуется — весь synopsis считается неуспешным.

        published_parts: list[tuple[list, str]] = []  # (part_secs, url)

        async def _publish_recursive(secs: list, depth: int = 0) -> bool:
            """Публикует список sections рекурсивно.
            Возвращает True если ВСЕ части успешно опубликованы.
            При любом fail — возвращает False (без частичного результата).
            """
            if not secs:
                return True

            sec_offset = sum(len(p[0]) for p in published_parts)
            sec_range  = f"sections[{sec_offset}..{sec_offset + len(secs) - 1}]"
            part_nodes = []
            for sec_idx, sec in enumerate(secs):
                if sec_idx > 0:
                    part_nodes.append({"tag": "hr"})
                part_nodes.extend(_section_to_nodes_v2(sec, yt_url=url,
                                                        rutube_url=rutube_url, vk_url=vk_url,
                                                        duration=duration))

            logger.info(
                f"Synopsis v2 depth={depth}: {sec_range} "
                f"({len(secs)} secs, {len(part_nodes)} nodes) — публикую..."
            )
            tmp_title = f"{tg_title} [draft]"
            page_url, err = await _create_telegraph_page_single(tmp_title, author, part_nodes, loop)

            if page_url:
                logger.info(f"Synopsis v2 depth={depth}: {sec_range} → OK {page_url}")
                published_parts.append((secs, page_url))
                return True

            if err != "CONTENT_TOO_BIG":
                logger.warning(f"Synopsis v2 depth={depth}: {sec_range} ошибка '{err}' — fail")
                return False

            # CONTENT_TOO_BIG — делим пополам по sections
            if len(secs) == 1:
                logger.warning(
                    f"Synopsis v2 depth={depth}: {sec_range} один section "
                    f"({len(part_nodes)} nodes) не влезает в Telegraph — fail"
                )
                return False

            mid = max(1, len(secs) // 2)
            logger.info(
                f"Synopsis v2 depth={depth}: {sec_range} CONTENT_TOO_BIG "
                f"({len(part_nodes)} nodes) → делю [{mid}]+[{len(secs)-mid}]"
            )
            ok_left = await _publish_recursive(secs[:mid], depth + 1)
            if not ok_left:
                return False
            return await _publish_recursive(secs[mid:], depth + 1)

        success = await _publish_recursive(sections)

        if not success:
            logger.warning("Synopsis v2: публикация не удалась — возвращаю None")
            # Удалять уже созданные страницы не можем (нет API delete),
            # но не возвращаем ссылку — бот не покажет Конспект в caption
            return None, None

        total      = len(published_parts)
        parts      = [p[0]   for p in published_parts]
        parts_urls = [p[1]   for p in published_parts]

        logger.info(f"Synopsis v2: опубликовано {total} частей")

        # ── Переименовываем и добавляем TOC + навигацию ──────
        for i, (part_secs, page_url) in enumerate(published_parts):
            part_num   = i + 1
            part_title = tg_title if total == 1 else f"{tg_title} [{part_num}/{total}]"

            nodes_edit: list = []
            if i == 0:
                nodes_edit.extend(_build_toc_nodes_v2(outline, yt_url=url, parts=parts))
            for sec_idx, sec in enumerate(part_secs):
                if sec_idx > 0:
                    nodes_edit.append({"tag": "hr"})
                nodes_edit.extend(_section_to_nodes_v2(sec, yt_url=url,
                                                        rutube_url=rutube_url, vk_url=vk_url,
                                                        page_title=part_title, duration=duration))
            if total > 1:
                nodes_edit.extend(_build_nav_nodes_v2(i, total, parts_urls))

            ok = False
            for retry_attempt in range(3):
                ok = await _edit_telegraph_page(page_url, part_title, author, nodes_edit, loop)
                if ok:
                    break
                logger.warning(
                    f"Synopsis v2: editPage часть {part_num}/{total} "
                    f"попытка {retry_attempt + 1}/3 failed для {page_url}"
                )
                await asyncio.sleep(3)

            if ok:
                logger.info(f"Synopsis v2: editPage часть {part_num}/{total} → {page_url}")
            else:
                logger.warning(
                    f"Synopsis v2: editPage часть {part_num}/{total} окончательно failed для {page_url}"
                )

        return parts_urls[0], outline

    except Exception as e:
        logger.warning(f"Synopsis v2 error: {e}")
    return None, None


# ─── Видимая длина HTML-caption и умная обрезка ──────────────

def visible_length(text: str) -> int:
    """
    Считает длину текста так, как его видит Telegram после парсинга HTML:
      • все теги (<b>, <a href="...">, </a>, <tg-emoji ...>, </tg-emoji> и т.д.) не считаются
      • &amp; &lt; &gt; &quot; &#32; и т.п. считаются как 1 символ каждый
      • <tg-emoji>...</tg-emoji> — считается вложенный текст (обычно 1 emoji)
      • <a href="...">текст</a> — считается только «текст»
    Не использует внешних библиотек. Работает через re.
    """
    # Убираем все теги целиком, оставляя только их текстовое содержимое
    # Порядок важен: сначала убираем самозакрывающиеся / пустые теги без текста внутри
    s = text
    # Убираем открывающие и закрывающие теги (содержимое между тегами остаётся)
    s = re.sub(r'<[^>]+>', '', s)
    # Декодируем HTML-сущности вручную (каждая → 1 символ)
    s = re.sub(r'&[a-zA-Z]+;', 'x', s)      # &amp; &lt; &gt; &quot; &nbsp; и т.п.
    s = re.sub(r'&#\d+;', 'x', s)            # &#32; &#8203; и т.п.
    s = re.sub(r'&#x[0-9a-fA-F]+;', 'x', s) # &#x200B; и т.п.
    # Одиночный & (не являющийся частью сущности) — 1 символ, не трогаем
    # Telegram считает символы в UTF-16 code units (суррогатные пары = 2 единицы)
    # Эмодзи вне BMP (U+1F000+) занимают 2 единицы → корректируем
    extra = sum(1 for ch in s if ord(ch) > 0xFFFF)
    return len(s) + extra


def safe_trim_caption(caption: str, limit: int = 1024) -> str:
    """
    Обрезает caption по видимой длине, не ломая HTML.
    Используется ТОЛЬКО как последний резерв — вызывать после всех попыток
    пересобрать caption через _build().
    Гарантирует: visible_length(result) <= limit и все HTML-теги закрыты.
    """
    if visible_length(caption) <= limit:
        return caption

    # Разбиваем на строки и добавляем по одной, пока влезает
    lines = caption.split("\n")
    result_lines = []
    current_len = 0
    for line in lines:
        line_vlen = visible_length(line)
        # +1 за \n (кроме первой строки)
        sep = 1 if result_lines else 0
        if current_len + sep + line_vlen > limit:
            # Если это первая строка и она уже длиннее лимита —
            # обрезаем посимвольно (без лома тегов внутри неё)
            if not result_lines:
                buf = ""
                vis = 0
                i = 0
                while i < len(line):
                    if line[i] == '<':
                        end = line.find('>', i)
                        if end == -1:
                            break
                        tag_chunk = line[i:end + 1]
                        buf += tag_chunk
                        i = end + 1
                    elif line[i] == '&':
                        end = line.find(';', i)
                        if end == -1 or end - i > 10:
                            if vis < limit:
                                buf += line[i]
                                vis += 1
                            i += 1
                        else:
                            entity = line[i:end + 1]
                            if vis < limit:
                                buf += entity
                                vis += 1
                            i = end + 1
                    else:
                        if vis < limit:
                            buf += line[i]
                            vis += 1
                        i += 1
                result_lines.append(buf)
                current_len = vis
            # Строка не влезает (первая уже добавлена с обрезкой, остальные пропускаем)
            break
        else:
            result_lines.append(line)
            current_len += sep + line_vlen

    result = "\n".join(result_lines)

    # Закрываем незакрытые теги (простые парные теги Telegram HTML)
    _PAIRED = ["b", "i", "u", "s", "a", "tg-emoji", "code", "pre", "spoiler"]
    open_tags = []
    for m in re.finditer(r'<(/?)(\w[\w-]*)(?:\s[^>]*)?>',  result):
        closing, tag = m.group(1), m.group(2).lower()
        if tag not in _PAIRED:
            continue
        if closing:
            # Ищем тег в стеке (не только вершину) — на случай неправильной вложенности
            if tag in open_tags:
                # Убираем все теги от вершины до найденного включительно
                while open_tags and open_tags[-1] != tag:
                    open_tags.pop()
                if open_tags:
                    open_tags.pop()
        else:
            open_tags.append(tag)
    for tag in reversed(open_tags):
        result += f"</{tag}>"

    return result


def _trim_timestamps(timestamps_str: str, max_lines: int) -> str:
    """
    Умная обрезка таймкодов — сохраняет покрытие всего материала.
    Всегда сохраняет первый (0:00) и последний таймкод,
    остальные слоты распределяет равномерно по всей длительности.
    Результат покрывает материал от начала до конца, а не только первую половину.
    """
    lines = [l for l in timestamps_str.split("\n") if l.strip()]
    if len(lines) <= max_lines:
        return timestamps_str
    if max_lines <= 0:
        return ""
    if max_lines == 1:
        return lines[0]
    if max_lines == 2:
        return "\n".join([lines[0], lines[-1]])

    # Всегда первый и последний
    result = [lines[0]]
    middle_lines = lines[1:-1]
    remaining_slots = max_lines - 2

    if remaining_slots >= len(middle_lines):
        result.extend(middle_lines)
    else:
        # Равномерная выборка из середины
        step = len(middle_lines) / remaining_slots
        for i in range(remaining_slots):
            idx = int(i * step)
            result.append(middle_lines[idx])

    result.append(lines[-1])
    return "\n".join(result)


def get_caption_timestamp_limit(format_name: str) -> int:
    """Возвращает максимальное число строк таймкодов в caption по format.
    Поскольку полное описание отправляется отдельным сообщением (4096 символов),
    лимиты увеличены: qa — все вопросы, sermon/lecture — умное покрытие."""
    return {
        "sermon":     30,   # умная выборка равномерно по всей проповеди
        "lecture":    30,   # то же для лекций и семинаров
        "qa":         999,  # все вопросы без обрезки
        "interview":  25,
        "discussion": 25,
        "other":      20,
    }.get(format_name or "other", 20)


def build_caption(performer, title, duration, file_size_mb, ai_data=None, bitrate="128", url="", telegraph_url="", rutube_url="", vk_url="", quotes_tg_url="", questions_tg_url="", terms_tg_url="", study_tg_url="", reflection_tg_url="", full_mode=False):
    parts = []

    # Шапка — используем данные от Gemini если есть, иначе из yt-dlp
    real_author = normalize_author_name((ai_data or {}).get("real_author", "") or performer)
    real_title  = title_case_fragment(normalize_title_text((ai_data or {}).get("real_title", "") or title) or title)
    real_event  = _scrub_inline((ai_data or {}).get("real_event", ""))

    if real_event:
        parts.append(f'<b><tg-emoji emoji-id="5283043763898328413">📜</tg-emoji> {html_mod.escape(real_event)}</b>')
    safe_title = html_mod.escape(real_title)
    title_emoji = '📖 ' if real_event else '<tg-emoji emoji-id="5283043763898328413">📜</tg-emoji> '
    parts.append(f"<b>{title_emoji}{safe_title}</b>")
    if real_author:
        parts.append(f"<b>👤 {html_mod.escape(real_author)}</b>")

    # Краткое описание (main_topic) — между шапкой и таймкодами
    main_topic = _scrub_inline((ai_data or {}).get("main_topic", ""))
    if main_topic:
        def _topic_to_html(text: str) -> str:
            """Конвертирует **жирный** из Gemini в <b>жирный</b> для Telegram HTML."""
            _count = text.count('**')
            if _count % 2 == 1:
                idx = text.rfind('**')
                text = text[:idx] + text[idx + 2:]
            parts_t = []
            last = 0
            for m in re.finditer(r'\*\*(.+?)\*\*', text):
                if m.start() > last:
                    parts_t.append(html_mod.escape(text[last:m.start()]))
                parts_t.append(f"<b>{html_mod.escape(m.group(1))}</b>")
                last = m.end()
            if last < len(text):
                parts_t.append(html_mod.escape(text[last:]))
            return "".join(parts_t)

        if full_mode:
            # В полном режиме — весь main_topic без обрезки, без курсива
            parts.append("")
            parts.append(_topic_to_html(main_topic.strip()))
        else:
            # Обрезаем до трёх предложений, без курсива
            sentences = re.split(r'(?<=[.!?])\s+', main_topic.strip())
            short_topic = " ".join(sentences[:3])
            parts.append("")
            parts.append(_topic_to_html(short_topic))

    # Таймкоды
    if ai_data and ai_data.get("timestamps"):
        parts.append("")
        parts.append("<b>⏱ Таймкоды:</b>")
        timestamp_lines = []
        for line in ai_data["timestamps"].split("\n"):
            line = line.strip()
            if not line:
                continue
            # Зачищаем мусор ДО html.escape
            line = _scrub_inline(line)
            if not line or any(re.search(p, line, re.IGNORECASE) for p in BAD_META_PATTERNS):
                continue
            # Снимаем markdown-форматирование из топика (** * _) — Gemini иногда присылает жирный/курсив
            line = re.sub(r'\*{1,3}([^*\n]+)\*{1,3}', r'\1', line)
            line = re.sub(r'_([^_\n]+)_', r'\1', line)
            _p = line.split(' ', 1)
            time_str = _p[0]
            topic_str = _p[1] if len(_p) == 2 else ""
            # Вставляем zero-width space после ":" в топике чтобы Telegram не делал ссылку
            topic_escaped = re.sub(r':(\d)', r':​\1', html_mod.escape(topic_str)) if topic_str else ""
            # Делаем таймкод кликабельной ссылкой на YouTube если передан url
            if url and topic_escaped:
                secs = time_to_seconds(time_str)
                if secs is not None:
                    yt_link = get_youtube_timestamp_url(url, secs)
                    escaped = f'<a href="{yt_link}">{html_mod.escape(time_str)}</a> {topic_escaped}'
                else:
                    escaped = html_mod.escape(time_str) + (' ' + topic_escaped if topic_escaped else '')
            else:
                escaped = html_mod.escape(time_str) + (' ' + topic_escaped if topic_escaped else '')
            timestamp_lines.append(escaped)
        parts.append("\n".join(timestamp_lines))

    # Платформы + Telegraph ссылки — никогда не убираются
    platform_links = build_platform_links(url, rutube_url, vk_url)
    tg_links       = build_telegraph_links(telegraph_url, quotes_tg_url, questions_tg_url, terms_tg_url, study_tg_url=study_tg_url, reflection_tg_url=reflection_tg_url)
    if tg_links:
        parts.append("")
        parts.append("<b>📂 Читать Подробный Разбор:</b>")
        parts.append(tg_links)
    if platform_links:
        parts.append("")
        parts.append("<b>Смотреть Видео:</b>")
        parts.append(platform_links)

    # Хэштеги — убираются первыми при обрезке
    if ai_data and ai_data.get("hashtags"):
        parts.append("")
        parts.append(html_mod.escape(ai_data["hashtags"]))

    result = "\n".join(parts)
    # Финальная чистка — только строки-мусор, без редактирования пунктуации
    result = _strip_meta_lines(result)
    return result


# ─── Markdown → Telegraph nodes ─────────────────────────────

def _fix_inline_spacing(nodes: list) -> list:
    """
    Исправляет слипание жирного/курсивного текста с окружающим.
    Если перед dict-нодой (bold/italic) стоит строка без пробела на конце,
    или после dict-ноды стоит строка без пробела в начале — вставляет пробел.
    """
    if len(nodes) < 2:
        return nodes
    result = []
    for i, node in enumerate(nodes):
        if isinstance(node, dict) and node.get("tag") in ("b", "i"):
            # Проверяем предыдущий элемент — нужен ли пробел перед bold/italic
            if result and isinstance(result[-1], str):
                prev = result[-1]
                if prev and not prev.endswith((" ", "\n", "(", "«", "—", "–", "-", "/")):
                    result[-1] = prev + " "
            result.append(node)
            # Проверяем следующий элемент — нужен ли пробел после bold/italic
            if i + 1 < len(nodes) and isinstance(nodes[i + 1], str):
                next_text = nodes[i + 1]
                if next_text and not next_text.startswith((" ", "\n", ".", ",", ";", ":", "!", "?", ")", "»", "—", "–")):
                    nodes[i + 1] = " " + next_text
        else:
            result.append(node)
    return result


def _fix_unclosed_bold(text: str) -> str:
    """Страховка перед парсером: исправляет незакрытые ** в тексте.
    - Если ** висит в конце строки без пары — удаляем его
    - Если ** открыт посередине строки без закрытия — закрываем в конце строки
    - Корректный текст не трогаем
    """
    lines = text.split('\n')
    result = []
    for line in lines:
        # Считаем **, не трогая ***
        tmp = line.replace('***', '\x00\x00\x00')
        count = tmp.count('**')
        if count % 2 == 0:
            result.append(line)
            continue
        stripped = line.rstrip()
        if stripped.endswith('**'):
            # Висящий ** в конце — убираем
            result.append(stripped[:-2].rstrip())
        else:
            # Открытый ** посередине — закрываем
            result.append(line + '**')
    return '\n'.join(result)


def _md_parse_inline(text: str) -> list:
    """**bold**, *italic*, ***bold+italic***, _italic_ → Telegraph nodes.
    Поддерживает вложенный курсив внутри жирного: **текст *курсив* текст**
    """
    text = _fix_unclosed_bold(text)
    nodes = []
    # Bold/italic не должны захватывать двойной перенос строки (граница абзаца).
    pattern = re.compile(r'(\*\*\*((?:(?!\n\n).)+?)\*\*\*|\*\*((?:(?!\n\n).)+?)\*\*|\*((?:(?!\n\n).)+?)\*|_((?:(?!\n\n).)+?)_)', re.DOTALL)
    last = 0
    for m in pattern.finditer(text):
        if m.start() > last:
            nodes.append(text[last:m.start()])
        def _make_b_node(children):
            return {"tag": "b", "children": children}

        if m.group(2):
            nodes.append(_make_b_node([{"tag": "i", "children": [m.group(2)]}]))
        elif m.group(3):
            # Жирный блок — рекурсивно парсим содержимое на вложенный курсив
            inner = m.group(3)
            inner_nodes = _md_parse_inline_inner(inner)
            nodes.append(_make_b_node(inner_nodes))
        else:
            nodes.append({"tag": "i", "children": [m.group(4) or m.group(5)]})
        last = m.end()
    if last < len(text):
        nodes.append(text[last:])
    # Fallback: убираем сырые ** которые не распознал парсер
    nodes = [n.replace('**', '') if isinstance(n, str) else n for n in nodes]
    return _fix_inline_spacing(nodes) if nodes else [text]


def _md_parse_inline_inner(text: str) -> list:
    """Парсит только *курсив* и _курсив_ внутри уже жирного блока."""
    if re.search(r'[\u0590-\u05FF\u0600-\u06FF]', text):
        text = _fix_rtl_in_text(text)
    nodes = []
    pattern = re.compile(r'(\*(.+?)\*|_(.+?)_)', re.DOTALL)
    last = 0
    for m in pattern.finditer(text):
        if m.start() > last:
            nodes.append(text[last:m.start()])
        nodes.append({"tag": "i", "children": [m.group(2) or m.group(3)]})
        last = m.end()
    if last < len(text):
        nodes.append(text[last:])
    return nodes if nodes else [text]

def _split_into_sentences(text: str) -> list[str]:
    """Разбивает текст на предложения по концу предложения (.!?).
    Не разрывает сокращения типа 'т.е.', 'т.д.', инициалы, числа."""
    # Разбиваем по границе предложения: символ конца + пробел + заглавная/цифра/«
    parts = re.split(r'(?<=[.!?])\s+(?=[А-ЯЁA-Z«""\d\-—])', text)
    return [p.strip() for p in parts if p.strip()]


def _md_to_telegraph_nodes(md: str) -> list:
    """Полный Markdown → Telegraph nodes."""
    nodes = []
    prev_was_empty = False
    for line in md.split("\n"):
        s = line.strip()
        if not s:
            prev_was_empty = True
            continue
        # Пропускаем строки-мусор (Cocoon AI Summary и т.п.)
        if any(re.search(p, s, re.IGNORECASE) for p in BAD_META_PATTERNS):
            continue
        # Инлайн-очистка мусора внутри строки
        s = _scrub_inline(s)
        if not s:
            continue
        # Пропускаем строки из одних знаков препинания/разделителей (. .. ─── и т.п.)
        # Gemini иногда генерирует одиночную точку как визуальный разделитель между блоками
        if re.fullmatch(r'[.\-–—─·•*_~\s]+', s):
            prev_was_empty = True
            continue
        # Добавляем визуальный отступ только после заголовков — между обычными <p>
        # Telegraph сам даёт отступ, лишний <br> даёт двойной пробел
        last_tag = nodes[-1].get("tag") if nodes else ""
        if prev_was_empty and nodes and last_tag in ("h3", "h4") and not s.startswith(("#", "---", "***", "___")):
            nodes.append({"tag": "p", "children": [{"tag": "br"}]})
        prev_was_empty = False
        if s.startswith("#### "):
            nodes.append({"tag": "h4", "children": [s[5:]]})
        elif s.startswith("### "):
            nodes.append({"tag": "h4", "children": [s[4:]]})
        elif s.startswith("## "):
            nodes.append({"tag": "h3", "children": [s[3:]]})
        elif s.startswith("# "):
            nodes.append({"tag": "h3", "children": [s[2:]]})
        elif s in ("---", "***", "___"):
            nodes.append({"tag": "hr"})
        elif s.startswith("> "):
            _bq_text = s[2:]
            if re.search(r'[\u0590-\u05FF\u0600-\u06FF]', _bq_text):
                _bq_text = _fix_rtl_in_text(_bq_text)
            nodes.append({"tag": "blockquote", "children": _md_parse_inline(_bq_text)})
        elif re.match(r'^\d+\.\s', s):
            num_m = re.match(r'^(\d+)\.\s+(.*)', s)
            if num_m:
                _num, _rest = num_m.group(1), num_m.group(2)
                _rest_stripped = _rest.lstrip()
                # Если _rest — только жирный заголовок (**Title.**) без текста после —
                # вливаем номер прямо внутрь <b>, чтобы получился один тег: <b>1. Title.</b>
                # Иначе два соседних <b> рендерятся в Telegraph непоследовательно.
                _only_bold = re.match(r'^\*\*([^*]+)\*\*\.?$', _rest_stripped)
                if _only_bold:
                    _title = _rest_stripped[2:].rstrip('*').rstrip('.')
                    nodes.append({"tag": "p", "children": [
                        {"tag": "b", "children": [f"{_num}. {_title}."]}
                    ]})
                elif _rest_stripped.startswith("**"):
                    # Заголовок + текст: сливаем номер в первый <b>
                    _inline = _md_parse_inline(_rest_stripped)
                    if _inline and isinstance(_inline[0], dict) and _inline[0].get("tag") == "b":
                        _inline[0]["children"] = [f"{_num}. "] + _inline[0]["children"]
                        nodes.append({"tag": "p", "children": _inline})
                    else:
                        nodes.append({"tag": "p", "children":
                            [{"tag": "b", "children": [f"{_num}.\u00a0"]}] + _inline})
                else:
                    if re.search(r'[\u0590-\u05FF\u0600-\u06FF]', _rest):
                        _rest = _fix_rtl_in_text(_rest)
                    nodes.append({"tag": "p", "children":
                        [{"tag": "b", "children": [f"{_num}.\u00a0"]}] + _md_parse_inline(_rest)
                    })
            else:
                nodes.append({"tag": "p", "children": _md_parse_inline(re.sub(r'^\d+\.\s', '', s))})
        elif s.startswith(("- ", "• ", "· ")) or (
            s.startswith("* ") and not s.endswith("*/") and
            not (re.search(r'(?<!\*)\*\s*$', s) and not re.search(r'\*\*\s*$', s))
        ):  # "* " как список; исключаем CSS-комментарии и курсив-обёртки "* весь текст *"
            _li_text = s[2:]
            if re.search(r'[\u0590-\u05FF\u0600-\u06FF]', _li_text):
                _li_text = _fix_rtl_in_text(_li_text)
            nodes.append({"tag": "p", "children": ["• "] + _md_parse_inline(_li_text)})
        else:
            # Обычный абзац — сохраняем как единый <p>, не дробим на предложения.
            # dir="ltr" для строк содержащих RTL-символы (иврит, арабский) —
            # иначе браузер переключает выравнивание всего абзаца вправо.
            _has_rtl = bool(re.search(r'[\u0590-\u05FF\u0600-\u06FF]', s))
            _s_for_parse = _fix_rtl_in_text(s) if _has_rtl else s
            _p_node = {"tag": "p", "children": _md_parse_inline(_s_for_parse)}
            if _has_rtl:
                _p_node["attrs"] = {"dir": "ltr"}
            nodes.append(_p_node)
    return nodes or [{"tag": "p", "children": [md]}]


def _clean_text_block(text: str) -> str:
    """Безопасная очистка текстового блока.
    Удаляет блок целиком ТОЛЬКО если он является 100% мусором (Cocoon AI и т.д.).
    НЕ редактирует нормальный текст, НЕ удаляет внутренние пробелы и переносы строк."""
    if not text:
        return ""
    if any(re.search(p, text, re.IGNORECASE) for p in BAD_META_PATTERNS):
        return ""
    return text


def _clean_node_texts(node):
    """Рекурсивно убирает None и пустые children из Telegraph nodes.
    НЕ редактирует текстовое содержимое — чтобы не ломать форматирование
    богословских статей, scripture, lexicon notes и narrative content."""
    if not isinstance(node, dict):
        return node
    children = node.get("children")
    if not isinstance(children, list):
        return node
    new_children = []
    for child in children:
        if isinstance(child, str):
            # Оставляем строку как есть — не прогоняем через _clean_text_block
            if child is not None:
                new_children.append(child)
        elif isinstance(child, dict):
            cleaned_child = _clean_node_texts(child)
            if cleaned_child:
                new_children.append(cleaned_child)
        elif child is not None:
            new_children.append(child)
    if not new_children:
        return None
    node["children"] = new_children
    return node


def _clean_telegraph_nodes(nodes: list) -> list:
    """Централизованная очистка Telegraph nodes от мусора. Применять перед любой публикацией."""
    cleaned = []
    for n in nodes:
        cn = _clean_node_texts(n)
        if not cn:
            continue
        # Выбрасываем node если после чистки он целиком мусор
        if isinstance(cn, dict) and cn.get("tag") in ("p", "h3", "h4", "blockquote"):
            children = cn.get("children", [])
            if all(isinstance(c, str) for c in children):
                if all(is_meta_garbage(c.strip()) for c in children if isinstance(c, str)):
                    continue
        cleaned.append(cn)
    # Убираем ведущие мусорные параграфы — они попадают в preview/snippet
    # Пустой text (is_meta_garbage возвращает True на "") не удаляем —
    # пустые параграфы служат визуальными разделителями между секциями.
    while cleaned and isinstance(cleaned[0], dict):
        if cleaned[0].get("tag") == "p":
            children = cleaned[0].get("children", [])
            text = " ".join(c for c in children if isinstance(c, str)).strip()
            if is_meta_garbage(text):
                cleaned.pop(0)
                continue
            # Убираем строки с датами типа "March 15 at 18:55"
            if re.search(r'\w+\s+\d{1,2}\s+at\s+\d{1,2}:\d{2}', text, re.IGNORECASE):
                cleaned.pop(0)
                continue
        break
    return cleaned


async def _telegraph_post(title: str, author: str, nodes: list, loop) -> str | None:
    """Публикует страницу в Telegraph, возвращает URL или None.
    При CONTENT_TOO_BIG автоматически разбивает на части и создаёт оглавление."""

    nodes = _clean_telegraph_nodes(nodes)

    # Ядерная чистка: убираем Cocoon AI и мусор из всех нод и title
    _dirty_phrases = [
        "Cocoon AI Summary", "Cocoon AI", "cocoon ai summary", "cocoon ai",
        "AI Summary", "Content Summary", "Auto-generated summary", "Auto-Generated Summary",
    ]
    for _phrase in _dirty_phrases:
        title = title.replace(_phrase, "").strip()
    try:

        _raw = json.dumps(nodes, ensure_ascii=False)
        for _phrase in _dirty_phrases:
            _raw = _raw.replace(_phrase, "")
        nodes = json.loads(_raw)
        # Убираем пустые ноды после чистки
        nodes = [n for n in nodes if n and (
            not isinstance(n, dict) or n.get("children", [True])
        )]
    except Exception:
        pass

    token = os.getenv("TELEGRAPH_TOKEN", "").strip()
    if not token:
        logger.warning("TELEGRAPH_TOKEN не задан")
        return None

    async def _post_once(t, ns):
        logger.info(f"Telegraph: публикую '{t}' ({len(ns)} блоков)")
        try:
            resp = await loop.run_in_executor(None, lambda: requests.post(
                "https://api.telegra.ph/createPage",
                json={"access_token": token, "title": t, "author_name": "",
                      "content": ns, "return_content": False},
                timeout=15,
            ))
            data = resp.json()
            if data.get("ok"):
                return data["result"]["url"], None
            return None, data.get("error", "")
        except Exception as e:
            logger.warning(f"Telegraph ошибка: {e}")
            return None, str(e)

    url, err = await _post_once(title, nodes)
    if url:
        return url

    # Если ошибка не CONTENT_TOO_BIG — выходим
    if err != "CONTENT_TOO_BIG":
        logger.warning(f"Telegraph API: {err}")
        return None

    # CONTENT_TOO_BIG — разбиваем пополам и публикуем по частям
    logger.info(f"Telegraph: CONTENT_TOO_BIG для '{title}', разбиваю на части...")
    chunk_size = max(1, len(nodes) // 2)
    chunks = [nodes[i:i+chunk_size] for i in range(0, len(nodes), chunk_size)]
    parts_urls = []
    for idx, chunk in enumerate(chunks):
        part_title = f"{title} ({idx+1}/{len(chunks)})"
        part_url, part_err = await _post_once(part_title, chunk)
        if part_url:
            parts_urls.append((idx + 1, part_url))
        elif part_err == "CONTENT_TOO_BIG":
            # Ещё раз делим пополам
            sub_size = max(1, len(chunk) // 2)
            sub_chunks = [chunk[i:i+sub_size] for i in range(0, len(chunk), sub_size)]
            for sidx, sub in enumerate(sub_chunks):
                sub_title = f"{title} ({idx+1}.{sidx+1})"
                sub_url, _ = await _post_once(sub_title, sub)
                if sub_url:
                    parts_urls.append((f"{idx+1}.{sidx+1}", sub_url))

    if not parts_urls:
        return None
    if len(parts_urls) == 1:
        return parts_urls[0][1]

    # Оглавление
    toc_nodes = [{"tag": "hr"}]
    for num, part_url in parts_urls:
        toc_nodes.append({"tag": "p", "children": [
            {"tag": "a", "attrs": {"href": part_url}, "children": [f"Часть {num}"]}
        ]})
    toc_url, _ = await _post_once(f"{title} — Содержание", toc_nodes)
    return toc_url


async def create_telegraph_analytics(ai_data: dict, title: str, author: str,
                                      yt_url: str = "") -> str | None:
    """v3.0 — Публикует аналитику (analysis_summary + argument_arc + key_categories) в Telegraph."""
    author = _clean_meta_line(author or "") or "Автор не указан"
    title  = _clean_meta_line(title  or "") or "Без названия"

    analysis_summary = _scrub_inline(_strip_meta_lines((ai_data or {}).get("analysis_summary", "")))
    argument_arc     = _scrub_inline(_strip_meta_lines((ai_data or {}).get("argument_arc", "")))
    key_categories   = (ai_data or {}).get("key_categories", []) or []

    if not analysis_summary and not argument_arc and not key_categories:
        return None

    loop  = asyncio.get_running_loop()
    nodes = []

    if analysis_summary and not is_meta_garbage(analysis_summary):
        nodes.append({"tag": "h3", "children": ["🧠 Аналитика"]})
        nodes.append({"tag": "p", "children": [analysis_summary]})

    if argument_arc:
        if nodes:
            nodes.append({"tag": "hr"})
        nodes.append({"tag": "h3", "children": ["📈 Ход аргументации"]})
        nodes.append({"tag": "p", "children": [{"tag": "i", "children": [argument_arc]}]})

    if key_categories:
        if nodes:
            nodes.append({"tag": "hr"})
        nodes.append({"tag": "h3", "children": ["🗂 Ключевые понятия"]})
        for item in key_categories:
            item = _scrub_inline(_strip_meta_lines(_clean_field(str(item))))
            if not item:
                continue
            # Разбиваем "Понятие — объяснение" на bold + plain
            if " — " in item:
                term, _, expl = item.partition(" — ")
                nodes.append({"tag": "p", "children": [
                    {"tag": "b", "children": [term.strip()]},
                    f"  —  {expl.strip()}",
                ]})
            else:
                nodes.append({"tag": "p", "children": [item]})

    return await _telegraph_post(f"Аналитика: {title}", author, nodes, loop)


async def create_telegraph_questions(questions: list, title: str, author: str) -> str | None:
    """Публикует вопросы для обсуждения в Telegraph.
    Двухфазная публикация по подобию Конспекта:
    1) createPage с body-only
    2) editPage с финальным оформлением
    """
    author = _clean_meta_line(author) or "Автор не указан"
    title  = _clean_meta_line(title)  or "Без названия"

    if not isinstance(questions, list):
        return None

    green = [q for q in questions if str(q).startswith("🟢")]
    blue  = [q for q in questions if str(q).startswith("🔵")]
    other = [q for q in questions if not str(q).startswith(("🟢", "🔵"))]
    green = green + other

    if not green and not blue:
        return None

    loop = asyncio.get_running_loop()

    # ── Фаза 1: body-only для createPage ─────────────────────────
    body_nodes: list = []

    if green:
        for i, q in enumerate(green, 1):
            raw = re.sub(r"^🟢\s*", "", str(q)).strip()
            for chunk in raw.splitlines():
                text = _scrub_inline(_strip_meta_lines(_clean_field(chunk.strip())))
                if not text or is_meta_garbage(text):
                    continue
                body_nodes.append({"tag": "p", "children": [f"{i}. {text}"]})

    if blue:
        if body_nodes:
            body_nodes.append({"tag": "hr"})
        for i, q in enumerate(blue, 1):
            raw = re.sub(r"^🔵\s*", "", str(q)).strip()
            for chunk in raw.splitlines():
                text = _scrub_inline(_strip_meta_lines(_clean_field(chunk.strip())))
                if not text or is_meta_garbage(text):
                    continue
                body_nodes.append({"tag": "p", "children": [f"{i}. {text}"]})

    if not body_nodes:
        return None

    page_url, err = await _create_telegraph_page_single(
        f"{title} [draft]", author, body_nodes, loop
    )
    if not page_url:
        logger.warning(f"Questions draft createPage failed: {err}")
        return None

    # ── Фаза 2: финальное оформление через editPage ───────────────
    final_nodes: list = []

    if green:
        final_nodes.append({"tag": "h3", "children": ["Вопросы для размышления"]})
        for i, q in enumerate(green, 1):
            raw = re.sub(r"^🟢\s*", "", str(q)).strip()
            for chunk in raw.splitlines():
                text = _scrub_inline(_strip_meta_lines(_clean_field(chunk.strip())))
                if not text or is_meta_garbage(text):
                    continue
                final_nodes.append({"tag": "p", "children": [f"{i}. {text}"]})

    if blue:
        if final_nodes:
            final_nodes.append({"tag": "hr"})
        final_nodes.append({"tag": "h3", "children": ["Углубиться"]})
        for i, q in enumerate(blue, 1):
            raw = re.sub(r"^🔵\s*", "", str(q)).strip()
            for chunk in raw.splitlines():
                text = _scrub_inline(_strip_meta_lines(_clean_field(chunk.strip())))
                if not text or is_meta_garbage(text):
                    continue
                final_nodes.append({"tag": "p", "children": [f"{i}. {text}"]})

    ok = await _edit_telegraph_page(
        page_url=page_url,
        title=f"Вопросы: {title}",
        author=author,
        nodes=final_nodes,
        loop=loop,
    )
    if not ok:
        logger.warning(f"Questions editPage failed for {page_url}")

    return page_url


async def create_telegraph_terms(terms_data: dict, title: str, author: str, yt_url: str = "") -> str | None:
    """Публикует блок «Термины» в Telegraph.
    terms_data — плоские массивы строк с разделителем ||.
    """
    author = _clean_meta_line(author or "") or "Автор не указан"
    title = _clean_meta_line(title or "") or "Без названия"

    td = terms_data or {}
    concepts = td.get("concepts", []) or []
    scripture = td.get("scripture", []) or []
    translations = td.get("translations", []) or []
    lexicon_notes = td.get("lexicon_notes", []) or []

    if not any([concepts, scripture, translations, lexicon_notes]):
        return None

    loop = asyncio.get_running_loop()
    nodes: list = []

    def _ts_nodes(times_str: str) -> list:
        if not times_str:
            return []
        parts = [t.strip() for t in re.split(r"[,\s•]+", times_str) if t.strip()]
        if not parts:
            return []
        # Лимит: максимум 3 таймкода на запись (начало темы + ключевой момент)
        # Валидация: пропускаем обрезанные/невалидные ("5", "41" без ":")
        valid_parts = []
        for t in parts:
            if ":" not in t:
                continue  # обрезанный или невалидный таймкод
            if time_to_seconds(t) is None:
                continue
            valid_parts.append(t)
            if len(valid_parts) >= 3:
                break
        if not valid_parts:
            return []
        children = []
        for i, ts in enumerate(valid_parts):
            secs = time_to_seconds(ts)
            if yt_url and secs is not None:
                href = get_youtube_timestamp_url(yt_url, secs)
                children.append({"tag": "a", "attrs": {"href": href}, "children": [ts]})
            else:
                children.append(ts)
            if i < len(valid_parts) - 1:
                children.append(", ")
        return children

    def _parse_line(line: str, n_fields: int) -> list[str]:
        parts = [p.strip() for p in str(line).split("||")]
        while len(parts) < n_fields:
            parts.append("")
        return parts[:n_fields]

    def _clean_text(s: str) -> str:
        s = _scrub_inline(_strip_meta_lines(_clean_field(s)))
        s = re.sub(r"[•·]", ", ", s)
        s = re.sub(r"\s*,\s*", ", ", s)
        s = s.strip()
        if re.search(r'[\u0590-\u05FF\u0600-\u06FF]', s):
            s = _fix_rtl_in_text(s)
        return s

    first_section = True

    def _section_header(label: str):
        nonlocal first_section
        if not first_section:
            nodes.append({"tag": "hr"})
        first_section = False
        nodes.append({"tag": "h3", "children": [label]})

    # ── 🧩 Понятия ─────────────────────────────────────────────
    # Формат:
    # Термин || Объяснение || Почему важно: ... || Таймкоды: 5:09, 11:40
    if concepts:
        _section_header("🧩 Понятия")
        for raw in concepts:
            term, explanation, why, times_str = _parse_line(raw, 4)
            term = _clean_text(term)
            explanation = _clean_text(explanation)
            why = _clean_text(why)
            times_str = _clean_text(times_str)

            if not term:
                continue

            nodes.append({"tag": "p", "children": [{"tag": "b", "children": [term]}]})

            # explanation и why — отдельными параграфами для читаемости
            if explanation:
                nodes.append({"tag": "p", "children": [explanation]})
            if why:
                why_clean = re.sub(r"^Почему важно:\s*", "", why, flags=re.IGNORECASE).strip()
                if why_clean:
                    nodes.append({"tag": "p", "children": [{"tag": "i", "children": [why_clean]}]})

            ts = _ts_nodes(times_str)
            if ts:
                nodes.append({"tag": "p", "children": ts})

    # ── 📖 Писание ─────────────────────────────────────────────
    # Формат:
    # Ссылка || Как используется || Ключевая фраза: ... || Таймкоды: ...
    if scripture:
        _section_header("📖 Писание")
        for raw in scripture:
            ref, use, phrase, times_str = _parse_line(raw, 4)
            ref = _clean_text(ref)
            use = _clean_text(use)
            phrase = _clean_text(phrase)
            times_str = _clean_text(times_str)

            if not ref:
                continue

            nodes.append({"tag": "p", "children": [{"tag": "b", "children": [ref]}]})

            if use:
                nodes.append({"tag": "p", "children": [use]})

            if phrase:
                phrase = re.sub(r"^Ключевая фраза:\s*", "", phrase, flags=re.IGNORECASE).strip()
                if phrase:
                    if not phrase.startswith("«"):
                        phrase = f"«{phrase}»"
                    nodes.append({"tag": "p", "children": [{"tag": "i", "children": [phrase]}]})

            ts = _ts_nodes(times_str)
            if ts:
                nodes.append({"tag": "p", "children": ts})

    # ── 🌍 Переводы ────────────────────────────────────────────
    # Формат:
    # Ссылка || ключевое слово/фраза || Русские переводы || Английские переводы || Наблюдение || Таймкоды
    if translations:
        _section_header("🌍 Переводы")
        for raw in translations:
            ref, focus, ru_raw, en_raw, observation, times_str = _parse_line(raw, 6)
            ref = _clean_text(ref)
            focus = _clean_text(focus)
            ru_raw = _clean_text(ru_raw)
            en_raw = _clean_text(en_raw)
            observation = _clean_text(observation)
            times_str = _clean_text(times_str)

            if not ref:
                continue

            header = ref
            if focus:
                header += f" — «{focus}»"
            nodes.append({"tag": "p", "children": [{"tag": "b", "children": [header]}]})

            if ru_raw:
                ru_variants = [v.strip() for v in ru_raw.split(";") if v.strip()]
                for variant in ru_variants:
                    nodes.append({"tag": "p", "children": [variant]})

            if en_raw:
                en_variants = [v.strip() for v in en_raw.split(";") if v.strip()]
                for variant in en_variants:
                    nodes.append({"tag": "p", "children": [{"tag": "i", "children": [variant]}]})

            if observation:
                observation = re.sub(r"^Наблюдение:\s*", "", observation, flags=re.IGNORECASE).strip()
                if observation:
                    nodes.append({"tag": "p", "children": [observation]})

            ts = _ts_nodes(times_str)
            if ts:
                nodes.append({"tag": "p", "children": ts})

    # ── 🔤 Словарные заметки ───────────────────────────────────
    # Новый формат:
    # Ссылка на текст || русское ключевое слово/фраза || оригинальное слово || источник оригинала
    # || словарное пояснение || почему это важно здесь || таймкоды
    if lexicon_notes:
        _section_header("🔤 Словарные заметки")
        for raw in lexicon_notes:
            ref, ru_key, original_word, source_label, note, why, times_str, context_mention = _parse_line(raw, 8)
            ref = _clean_text(ref)
            ru_key = _clean_text(ru_key)
            original_word = _clean_text(original_word)
            source_label = _clean_text(source_label)
            note = _clean_text(note)
            why = _clean_text(why)
            times_str = _clean_text(times_str)
            context_mention = _clean_text(context_mention)

            if not ref and not original_word:
                continue

            # Заголовок
            header_parts = []
            if ref:
                header_parts.append(ref)
            if ru_key:
                header_parts.append(f"«{ru_key}»")
            header = " — ".join(header_parts) if header_parts else ""

            if header:
                nodes.append({"tag": "p", "children": [{"tag": "b", "children": [header]}]})

            # Оригинал и источник
            # dir="ltr" — принудительное выравнивание по левому краю для строк с ивритом/греческим,
            # чтобы браузер не переключался в RTL-режим из-за наличия RTL-символов
            if original_word:
                if source_label:
                    nodes.append({
                        "tag": "p",
                        "attrs": {"dir": "ltr"},
                        "children": [
                            {"tag": "b", "children": [original_word]},
                            f" — {source_label}",
                        ],
                    })
                else:
                    nodes.append({
                        "tag": "p",
                        "attrs": {"dir": "ltr"},
                        "children": [{"tag": "b", "children": [original_word]}],
                    })

            # Словарное пояснение
            if note:
                nodes.append({"tag": "p", "children": [note]})

            # Почему важно
            if why:
                why = re.sub(r"^Почему важно:\s*", "", why, flags=re.IGNORECASE).strip()
                if why:
                    nodes.append({"tag": "p", "children": [{"tag": "i", "children": [why]}]})

            # Как автор употребляет слово в материале
            if context_mention:
                nodes.append({
                    "tag": "p",
                    "children": [
                        {"tag": "b", "children": ["В материале: "]},
                        context_mention,
                    ],
                })

            # Таймкоды
            ts = _ts_nodes(times_str)
            if ts:
                nodes.append({"tag": "p", "children": ts})

    if not nodes:
        return None

    return await _telegraph_post(f"Термины: {title}", author, nodes, loop)


# ════════════════════════════════════════════════════════════════════════════
# ARTICLE-LIKE СТРАНИЦЫ: Разбор материала + Размышление и применение
# ════════════════════════════════════════════════════════════════════════════

# ─── Helper: единый Gemini text-only запрос (без аудио) ──────────────────

async def _gemini_text_request(prompt: str, temperature: float = 0.4,
                                max_tokens: int = 8000) -> str | None:
    """Текстовый запрос к Gemini (без аудио). Возвращает text или None.
    Автоматически повторяет при 500 INTERNAL (до 3 попыток с паузой)."""
    if not GEMINI_CLIENTS:
        return None

    def _is_internal_error(e: Exception) -> bool:
        s = str(e)
        return "500" in s or "INTERNAL" in s.upper()

    async def _fn(client):
        resp = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )
        return resp.text or ""

    # Retry при 500 INTERNAL — Gemini иногда выдаёт их случайно
    for attempt in range(3):
        try:
            return await gemini_generate(GEMINI_CLIENTS, _fn)
        except Exception as e:
            if _is_internal_error(e) and attempt < 2:
                wait = 15 * (attempt + 1)
                logger.warning(
                    "_gemini_text_request: 500 INTERNAL (попытка %d/3), жду %dс...",
                    attempt + 1, wait,
                )
                await asyncio.sleep(wait)
                continue
            logger.warning("_gemini_text_request error: %s", e)
            return None
    return None


def _parse_expanded_json(text: str) -> tuple[list, list] | None:
    """Парсит JSON {outline, sections} из ответа Gemini. Возвращает (outline, sections) или None.
    Поддерживает восстановление обрезанного JSON (max_tokens hit).
    """
    text = text.strip()
    # Убираем Unicode bidi-изоляторы которые Gemini иногда вставляет вокруг иврита/арабского
    text = re.sub(r'[\u2066-\u2069\u202a-\u202e\u200e\u200f]', '', text)
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())

    # Попытка 1: чистый JSON
    try:
        data = json.loads(text)
        return data.get("outline", []), data.get("sections", [])
    except json.JSONDecodeError:
        pass

    # Попытка 2: найти первый { ... }
    s = text.find("{")
    e = text.rfind("}")
    if s != -1 and e > s:
        try:
            data = json.loads(text[s:e+1])
            return data.get("outline", []), data.get("sections", [])
        except json.JSONDecodeError:
            pass

    # Попытка 3: Gemini обрезал JSON на полуслове (max_tokens) —
    # восстанавливаем sections до последнего полного объекта.
    # Ищем последнюю закрытую секцию по паттерну }"  (конец content поля)
    if s != -1:
        chunk = text[s:]
        # Находим все позиции где заканчивается полный секционный объект
        # Признак: "content": "..." } — последняя } внутри sections-массива
        last_complete = -1
        depth = 0
        in_str = False
        escape = False
        for idx, ch in enumerate(chunk):
            if escape:
                escape = False
                continue
            if ch == "\\" and in_str:
                escape = True
                continue
            if ch == '"' and not escape:
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 1:  # закрыли один section (внутри sections-массива)
                    last_complete = idx
        if last_complete > 0:
            # Обрезаем до последнего полного section и закрываем массив + объект
            fixed = chunk[:last_complete+1] + "\n  ]\n}"
            try:
                data = json.loads(fixed)
                sections = data.get("sections", [])
                if sections:
                    outline = data.get("outline", [
                        {"title": s.get("title", ""), "time": s.get("time") or ""}
                        for s in sections
                    ])
                    logger.info(
                        f"_parse_expanded_json: восстановлен обрезанный JSON, "
                        f"sections={len(sections)}"
                    )
                    return outline, sections
            except json.JSONDecodeError:
                pass

    return None


async def _publish_expanded_page(
    sections: list,
    outline: list,
    page_title: str,
    tg_title: str,
    author: str,
    yt_url: str = "",
    include_toc: bool = True,
    rutube_url: str = "",
    vk_url: str = "",
    duration: int = 0,
) -> str | None:
    """Публикует article-like страницу по модели Конспекта (createPage + editPage).
    Использует рекурсивное дробление при CONTENT_TOO_BIG — аналогично Synopsis."""
    loop = asyncio.get_running_loop()

    published_parts: list[tuple[list, str]] = []

    async def _publish_recursive(secs: list, depth: int = 0) -> bool:
        if not secs:
            return True

        body_nodes: list = []
        for sec_idx, sec in enumerate(secs):
            if sec_idx > 0:
                body_nodes.append({"tag": "hr"})
            body_nodes.extend(_section_to_nodes_v2(sec, yt_url=yt_url,
                                                    rutube_url=rutube_url, vk_url=vk_url,
                                                    duration=duration))

        sec_offset = sum(len(p[0]) for p in published_parts)
        sec_range  = f"sections[{sec_offset}..{sec_offset + len(secs) - 1}]"
        logger.info(
            "Expanded publish depth=%d: %s (%d secs, %d nodes) — публикую...",
            depth, sec_range, len(secs), len(body_nodes),
        )

        draft_title = f"{tg_title} [draft]" if depth == 0 and not published_parts else tg_title
        page_url, err = await _create_telegraph_page_single(draft_title, author, body_nodes, loop)

        if page_url:
            logger.info("Expanded publish depth=%d: %s → OK %s", depth, sec_range, page_url)
            published_parts.append((secs, page_url))
            return True

        if err != "CONTENT_TOO_BIG":
            logger.warning("Expanded publish depth=%d: %s ошибка '%s' — fail", depth, sec_range, err)
            return False

        if len(secs) == 1:
            logger.warning(
                "Expanded publish depth=%d: %s один section (%d nodes) не влезает — fail",
                depth, sec_range, len(body_nodes),
            )
            return False

        mid = max(1, len(secs) // 2)
        logger.info(
            "Expanded publish depth=%d: %s CONTENT_TOO_BIG (%d nodes) → делю [%d]+[%d]",
            depth, sec_range, len(body_nodes), mid, len(secs) - mid,
        )
        if not await _publish_recursive(secs[:mid], depth + 1):
            return False
        return await _publish_recursive(secs[mid:], depth + 1)

    success = await _publish_recursive(sections)
    if not success or not published_parts:
        return None

    parts      = [p[0] for p in published_parts]
    parts_urls = [p[1] for p in published_parts]
    total      = len(parts)

    for i, (page_url, part_secs) in enumerate(zip(parts_urls, parts)):
        part_num   = i + 1
        part_title = f"{page_title} (часть {part_num}/{total})" if total > 1 else page_title

        final_nodes: list = []
        if include_toc and i == 0:
            final_nodes.extend(_build_toc_nodes_v2(outline, yt_url=yt_url, parts=parts, duration=duration))

        for sec_idx, sec in enumerate(part_secs):
            if sec_idx > 0:
                final_nodes.append({"tag": "hr"})
            final_nodes.extend(_section_to_nodes_v2(sec, yt_url=yt_url,
                                                     rutube_url=rutube_url, vk_url=vk_url,
                                                     page_title=part_title, duration=duration))

        if total > 1:
            final_nodes.extend(_build_nav_nodes_v2(i, total, parts_urls))

        ok = False
        for retry_attempt in range(3):
            ok = await _edit_telegraph_page(page_url, part_title, author, final_nodes, loop)
            if ok:
                break
            logger.warning(
                "Expanded publish: editPage часть %d/%d попытка %d/3 failed для %s",
                part_num, total, retry_attempt + 1, page_url,
            )
            await asyncio.sleep(3)

        if ok:
            logger.info("Expanded publish: editPage часть %d/%d -> %s", part_num, total, page_url)
        else:
            logger.warning(
                "Expanded publish: editPage часть %d/%d окончательно failed для %s",
                part_num, total, page_url,
            )

    return parts_urls[0]


async def _run_expanded_pipeline(
    label: str,
    prompt: str,
    page_prefix: str,
    tg_title: str,
    author: str,
    yt_url: str = "",
    include_toc: bool = True,
    fallback_fn=None,
    max_tokens: int = 8000,
    rutube_url: str = "",
    vk_url: str = "",
    duration: int = 0,
) -> str | None:
    """Универсальный runner для article-like pages: Gemini -> парсинг -> публикация -> fallback."""

    try:
        logger.info("%s: запрос к Gemini", label)
        raw = await _gemini_text_request(prompt, temperature=0.4, max_tokens=max_tokens)
        if not raw:
            logger.warning("%s: Gemini вернул пустой ответ -- fallback", label)
            return await fallback_fn() if fallback_fn else None

        parsed = _parse_expanded_json(raw)

        if parsed is None:
            logger.warning("%s: сломанный JSON -- retry. Начало ответа: %.500s", label, raw)
            await asyncio.sleep(5)
            retry_prompt = (
                "Твой предыдущий ответ содержал сломанный JSON. "
                "Повтори строго: только валидный JSON {outline, sections}, "
                "без ```json, без текста до/после.\n\n" + prompt
            )
            raw2 = await _gemini_text_request(retry_prompt, temperature=0.1, max_tokens=max_tokens)
            if raw2:
                parsed = _parse_expanded_json(raw2)
            if parsed is None:
                logger.warning("%s: retry тоже дал сломанный JSON -- fallback", label)
                return await fallback_fn() if fallback_fn else None
            logger.info("%s: retry успешен", label)

        outline, sections = parsed

        if not isinstance(sections, list):
            logger.warning("%s: sections не список -- fallback", label)
            return await fallback_fn() if fallback_fn else None

        sections = [s for s in sections if isinstance(s, dict) and (s.get("title") or s.get("content"))]
        if not sections:
            logger.warning("%s: sections пуст после валидации -- fallback", label)
            return await fallback_fn() if fallback_fn else None

        if (
            isinstance(outline, list)
            and outline
            and len(outline) == len(sections)
            and all(isinstance(oi, dict) for oi in outline)
        ):
            outline = [
                {"title": s.get("title", ""), "time": (oi.get("time") or s.get("time") or "")}
                for s, oi in zip(sections, outline)
            ]
        else:
            outline = [
                {"title": s.get("title", ""), "time": s.get("time") or ""}
                for s in sections
            ]

        try:
            est = _estimate_nodes_v2(sections, yt_url,
                                     rutube_url=rutube_url, vk_url=vk_url,
                                     duration=duration)
        except Exception:
            est = -1

        logger.info(
            "%s: sections=%d outline=%d nodes_estimate=%d",
            label, len(sections), len(outline), est,
        )

        if " — " in tg_title:
            _sep = ": "
        elif ":" in tg_title:
            _sep = " — "
        else:
            _sep = ": "

        url = await _publish_expanded_page(
            sections=sections,
            outline=outline,
            page_title=f"{page_prefix}{_sep}{tg_title}",
            tg_title=f"{page_prefix}{_sep}{tg_title}",
            author=author,
            yt_url=yt_url,
            include_toc=include_toc,
            rutube_url=rutube_url,
            vk_url=vk_url,
            duration=duration,
        )

        if url:
            return url

        logger.warning("%s: publish failed -- fallback", label)
        return await fallback_fn() if fallback_fn else None

    except Exception as e:
        logger.exception("%s: unexpected error -- fallback: %s", label, e)
        return await fallback_fn() if fallback_fn else None




# ════════════════════════════════════════════════════════════════════════════
# ПРОМПТ: РАЗБОР МАТЕРИАЛА
# ════════════════════════════════════════════════════════════════════════════

STUDY_ANALYSIS_PROMPT = """\
========================================================
КОНФЕССИОНАЛЬНАЯ РАМКА КАНАЛА
========================================================

Канал создаётся с позиции реформированного баптиста.
Это НЕ значит игнорировать другие взгляды — значит знать, где ты стоишь.

ТРИ УРОВНЯ ДОКТРИНАЛЬНОЙ ЯСНОСТИ:

── АБСОЛЮТНЫЕ ОСНОВАНИЯ (здесь нет «позиций», есть истина) ────
• Богодухновенность и непогрешимость Писания (inerrancy, 2 Тим 3:16)
  Основание: Chicago Statement on Biblical Inerrancy (1978), 19 статей
• Троица: три Лица, одна сущность — Никея (325), Константинополь (381)
• Полная Божественность и полная человечность Христа — Халкидон (451)
• Спасение только по благодати, только через веру, только во Христа
  (Sola Gratia, Sola Fide, Solus Christus; 1689 Лондонское исповедание (LBCF) гл. 11)
• Исключительность Христа как единственного пути (Ин 14:6; Деян 4:12)
• Телесное воскресение Христа (1 Кор 15:14: «тщетна вера ваша»)
• Реальный, личный, грядущий суд (Евр 9:27; Откр 20:11-15)

── ПОЗИЦИЯ КАНАЛА (говорим с убеждением, уважаем братьев) ─────
• Крещение верующих по исповеданию веры (1689 Лондонское исповедание (LBCF) гл. 29)
  Не отрицаем спасение пресвитерианских братьев — разногласие по практике
• Кальвинизм: реформатское понимание благодати (5 точек)
  Включая определённое искупление с всеобщим внешним призывом
  (позиция Эндрю Фуллера: Бог искупил избранных, но зовёт всех искренно)

  СПЕКТР ПОЗИЦИЙ ПО БЛАГОДАТИ — от близкого к кальвинизму до далёкого:
  [Ближе] Умеренный арминианин / 4-пунктный кальвинист
    → разногласие по 1-2 пунктам, здесь: отмечаем, не акцентируем
  [Середина] Классическое арминианство (Ремонстранты 1610)
    → осуждено Дортским синодом; братья-арминиане не еретики, но система — заблуждение
  [Дальше] Арминианство с открытым теизмом → вопрос о природе Бога
  [Граница] Пелагианство / полупелагианство → называем ересью

  ГИПЕРКАЛЬВИНИЗМ — тоже заблуждение:
  Отказ от искреннего всеобщего призыва противоречит Писанию.
  Позиция Фуллера: определённое искупление + искренний зов ко всем.
• Цессационизм: знамения апостолов завершены со смертью апостолов
  (1689 Лондонское исповедание (LBCF) гл. 1 пар. 1; Евр 1:1-2; Еф 2:20)
• Конгрегационализм с множественностью пресвитеров-пасторов (1 Тим 5:17)
• Претриб премилленаризм: Христос заберёт Церковь до 7 лет Скорби,
  вернётся с ней после, буквальное Тысячелетнее Царство (Откр 20:1-6)

── ВНУТРИБРАТСКИЕ НЮАНСЫ (имеем мнение, не разрываем общение) ──
• Детали эсхатологической хронологии
• Вопросы крещения при согласии по его значению
• Детали пресвитерианских моделей управления
• Некоторые вопросы богослужебной практики

ПРИМЕНЕНИЕ В АНАЛИЗЕ:
• Абсолютные основания — говори ясно, без «с одной стороны».
  Отступление от них — ересь, а не позиция.
• Позиция канала — с убеждением, но честно обозначая, что есть братья
  с другим взглядом.
• Нюансы — показывай богатство традиции без навязывания.

========================================================
Ты — ассистент для создания **исследовательского богословско-экзегетического досье** к аудиоматериалу.

Материал: {title}
Автор: {author}
Длительность: {duration}

Уже известные данные о материале:
- format: {format_name}
- hermeneutic_method: {hermeneutic_method}
- main_topic: {main_topic}
- analysis_summary: {analysis_summary}
- argument_arc: {argument_arc}
- key_categories: {key_categories}
- timestamps: {timestamps}
- terms_data concepts: {concepts}
- terms_data scripture: {scripture}
- terms_data translations: {translations}
- terms_data lexicon_notes: {lexicon_notes}

{synopsis_context}

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ГЛАВНАЯ ЗАДАЧА
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Создай **справочно-аналитическую study page**.

Это НЕ:
- не пересказ проповеди,
- не второй Конспект,
- не обзорная статья,
- не essay "о теме вообще".

Это ДОЛЖНО БЫТЬ:
- **редакторски отобранное исследовательское досье**,
- набор лучших инструментов для глубокого изучения материала,
- краткие, плотные, интеллектуально полезные блоки,
- которые помогают читателю понять:
  - что здесь doctrinally важно,
  - какие тексты и слова несут вес,
  - какие традиции и документы помогают читать тему,
  - какие ложные чтения надо отсечь,
  - где проходят настоящие богословские линии напряжения.

Ты должен действовать как богословский редактор, исследователь, аннотатор — а не как пересказчик.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ГЛАВНЫЙ ПРИНЦИП ОТБОРА
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Включай только то, что:
- реально добавляет понимание,
- не дублирует Конспект,
- даёт новую исследовательскую ценность,
- помогает читателю увидеть то, что он бы сам не заметил.

Не включай блок, если он:
- просто увеличивает объём,
- даёт общие слова,
- не несёт конкретной пользы,
- повторяет уже очевидное из Конспекта.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ПРОДУКТОВАЯ ЦЕЛЬ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Эта страница отвечает не на вопрос "О чём была проповедь?" (это делает Конспект),
а на вопрос: "Какие богословские, экзегетические, языковые, конфессиональные
и исторические инструменты помогут глубже понять эту проповедь?"

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ОБЯЗАТЕЛЬНЫЕ ТРЕБОВАНИЯ К СТИЛЮ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

- Пиши по-русски.
- Пиши плотно и предметно.
- Не пиши длинных портянок.
- Не используй общие формулы:
  - "автор подчеркивает"
  - "в более широкой традиции"
  - "современные богословы отмечают"
  - "это важный момент"
  - "следует заметить"
  - "можно сказать"
  - "материал касается"
- Не делай section в виде essay.
- Каждый section должен быть **модульным и инструментальным**.
- Каждый абзац должен давать конкретную исследовательскую ценность.
- Если нет конкретики — лучше опусти этот элемент.
- Если внутри section несколько смысловых узлов идут подряд — НЕ сливай их в поток.
  Каждый узел оформляй как отдельный микро-блок с жирным началом и воздухом после него.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ОБЩАЯ АРХИТЕКТУРА СТРАНИЦЫ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Сделай 5–7 sections. Не все разделы обязательны всегда.
Выбирай только релевантные. Обычно используй 5–6.

====================================================================
SECTION TYPE 1 — КЛЮЧЕВЫЕ ПОНЯТИЯ И DOCTRINAL STAKES
====================================================================

Это один из самых важных разделов.

ЗАДАЧА: показать не просто "термины", а:
- какие понятия несут вес,
- и что doctrinally стоит на кону.

5–10 карточек. Каждая карточка — отдельный микро-блок, НЕ сливать в поток.

Формат каждой карточки:
- **Термин / понятие** — краткое определение.
Почему это важно именно в этой проповеди.
Что doctrinally рушится, если это понять неправильно.
Если уместно — таймкод.

Пример:
- **Оправдание** — Божий судебный акт, в котором грешник объявляется праведным исключительно на основании вменённой праведности Христа.
В этом материале на кону стоит различие между оправданием как внешним Божьим приговором и внутренним нравственным изменением.
Если это смешать, неизбежно размоется Евангелие и человек начнёт искать основание мира с Богом в себе, а не во Христе.

Между каждыми двумя карточками — двойной перенос строки (\n\n). Без слипания.
Не включай общеизвестные слова без исследовательской нагрузки.

====================================================================
SECTION TYPE 2 — КЛЮЧЕВЫЕ ТЕКСТЫ И ЭКЗЕГЕТИЧЕСКИЕ УЗЛЫ
====================================================================

ЗАДАЧА: выделить 3–6 библейских текстов, которые реально несут вес в этом материале,
и показать: как они работают в аргументе, что важно в тексте, где проходит экзегетический нерв.

Каждый текст — отдельный микро-блок. НЕ сливать в один сплошной абзац.

Формат каждого микро-блока:
- Ссылка на текст.
Как этот текст используется в аргументе.
Какой экзегетический узел здесь важен.
Что часто понимают поверхностно или ошибочно.
Если уместно — связь с другими текстами.

ОБЯЗАТЕЛЬНО: ссылка на текст — жирным (это заголовок блока): **Матфея 7:21**, **Галатам 6:14**.
Цитаты из Писания в кавычках внутри текста — курсивом: *«Я никогда не знал вас»*, *«трава засыхает»*.

Пример:
- **Матфея 7:21–23**
Текст используется не просто как предупреждение лицемерам, а как разоблачение религиозной активности без подчинения Христу.
Экзегетически важен контраст между внешним *«Господи, Господи»* и внутренним отсутствием заветного знания со стороны Христа.
Ошибка поверхностного чтения — воспринимать текст как предупреждение только откровенно лжерелигиозным людям.

Между каждыми двумя текстами — двойной перенос строки (\n\n).
Не просто перечисляй стихи. Показывай, где текст держит аргумент.

ПРАВИЛО ПАРАЛЛЕЛЬНЫХ ТЕКСТОВ (scripture chains):
После основного текста, добавляй параллельные ссылки где они реально помогают
увидеть связь. Максимум 3 параллели. В скобках — ключевая фраза 4-7 слов из стиха.
Только тексты реально связанные по теме и аргументу. Не притягивать искусственно.

Формат цепочки: → **Ссылка1** (*«цитата»*); **Ссылка2** (*«цитата»*); **Ссылка3** (*«цитата»*)

ВАЖНО: ссылка на книгу/главу/стих — жирным (это метка/заголовок).
Цитата из Писания в кавычках — курсивом: *«текст цитаты»*

Пример:
- **Иоанна 10:27-28**
Основа уверенности в спасении. Экзегетически важно — Христос говорит об овцах:
*«они слышат голос Мой»* (состояние, а не однократное действие).
→ **Рим 8:30** (*«кого предопределил, тех и призвал»*);
  **1 Ин 3:14** (*«знаем, что мы перешли»*);
  **Фил 1:6** (*«начавший доброе дело»*)

====================================================================
SECTION TYPE 3 — ЯЗЫКИ ОРИГИНАЛА И ЛЕКСИКО-СЕМАНТИЧЕСКИЕ УЗЛЫ
====================================================================

Используй только если реально добавляет понимание.

ЗАДАЧА: дать не подстрочник, а короткие **качественные словарные заметки уровня BDAG / HALOT**.

Для каждого слова ответь на 5 вопросов:
1. Какие основные значения у слова есть?
2. Какое значение реально активировано здесь?
3. Какой расхожий или ложный перевод («gloss») надо отсечь?
4. Почему это важно для темы проповеди?
5. Как именно автор использует это слово или понятие в материале?

Каждое слово — отдельный микро-блок. НЕ сливать несколько терминов в один поток.

Формат каждого микро-блока — как в академическом словаре:
**оригинальное слово** (*транслитерация*, яз.) — семантический диапазон.

Источники для Нового Завета: BDAG (золотой стандарт), Thayer, TDNT (Kittel), Louw-Nida.
Источники для Ветхого Завета: HALOT (золотой стандарт), BDB, TWOT.
Контекстуальный словарь: NIDNTT (Colin Brown) — богословие слов в контексте.

Какое значение активировано здесь.
Какой слишком узкий или ложный gloss надо отсечь — вместо слова «gloss» пиши по-русски: «расхожий перевод», «поверхностное прочтение», «привычное значение» и т.п.
Почему это важно для темы этого материала.

ВАЖНО: Пиши для образованного русскоязычного читателя без семинарского образования.
Академические термины (gloss, semantic range, hapax legomenon и подобные) заменяй русскими эквивалентами или поясняй в скобках при первом появлении.
Пример правильно: «привычный перевод "беззаконие" здесь недостаточен» — а не «ложный gloss здесь неуместен».

ОБЯЗАТЕЛЬНО в конце каждого блока — строка «**В материале:**»:
Как именно это слово или понятие звучит в проповеди: произносит ли проповедник его вслух
(на языке оригинала или в переводе), к какому аргументу или моменту привязано.
Если проповедник не называет слово, но разворачивает стоящее за ним понятие — напиши об этом.
Используй имя проповедника, а не безличное «автор». Пиши живо — не пересказ в третьем лице.
Эта строка обязательна — без неё читатель не понимает зачем слово здесь.

Если слово разбирается подробно в TDNT/NIDNTT — можно сослаться:
"по TDNT (Kittel): ..." — но только если ты УВЕРЕН в содержании.
Не выдумывай том и страницу. Осторожная ссылка лучше ложной точности.

ВАЖНО: формат каждого блока СТРОГО такой — всё в ОДНОМ абзаце без переносов строки:
**оригинальное_слово** (*транслитерация*, яз.) — словарь: значение.

Транслитерация — курсивом в скобках СРАЗУ после жирного слова на ТОЙ ЖЕ строке.
НЕ использовать тире "-" как маркер перед словом. Слово само является заголовком блока.
НЕ ставь оригинальное слово (иврит, арабский) на отдельную строку без контекста.
Весь блок «слово + транслитерация + словарь + пояснение» — ОДИН абзац.

Пример:
**ἀνομία** (*anomia*, греч.) — BDAG: 1) беззаконие как нарушение закона; 2) состояние бунта против Божьего порядка.
В контексте Мф 7:23 активировано не просто «отдельный проступок», а глубокая идея жизни вне Божьего правления.
Привычный перевод «беззаконие = список неправильных поступков» здесь недостаточен — речь о принципиальной автономии от Бога.
**В материале:** МакАртур цитирует «делающие беззаконие» и разворачивает именно это — не грехи по списку, а принципиальное нежелание подчиниться Богу. Именно это разоблачает религиозную активность без настоящего обращения.

**אֱנוֹשׁ** (*ʾĕnôš*, евр.) — HALOT: человек в его хрупкости, смертности и слабости.
В Псалме 102:15 активировано значение радикальной немощи перед лицом вечности.
Расхожее прочтение — «человек как венец творения» — здесь не работает: контекст псалма о тлении, а не о достоинстве.
**В материале:** Вошер не произносит еврейское слово — но вся история о трусе и лжеце про это: человек в тотальной несостоятельности перед Богом, которую не прикрыть ни успехом, ни репутацией.

Между каждыми двумя словами — двойной перенос строки (\n\n).

ПРАВИЛА:
- Не больше 3–8 слов
- Только действительно важные слова
- Давай семантический диапазон, контекстуальный выбор и исследовательскую ценность
- Если языковой блок слабый и нерелевантный — лучше опусти его

====================================================================
SECTION TYPE 4 — ПЕРЕВОДЧЕСКИЕ РАЗВИЛКИ И ВАРИАНТЫ ЧТЕНИЯ
====================================================================

Включай только если переводческие различия реально влияют на смысл или акцент.

ЗАДАЧА: показать 2–5 мест, где переводы расходятся так, что это меняет восприятие текста.

ПЕРЕВОДЫ ДЛЯ СРАВНЕНИЯ — используй оба ряда как равноценный материал:

Английские: LSB, NASB / NASB95, ESV, KJV
Русские: Синодальный, Кассиан, НРП, Кулаков

НУМЕРАЦИЯ ПСАЛМОВ: основная нумерация — русская (по Септуагинте/LXX, как в Синодальном переводе). Русский Псалом = английский Psalm + 1 (для Пс. 9–147). Например: английский Psalm 103 = русский Псалом 102. Если нужно показать английский вариант или сравнить переводы — пиши оба: Псалом 102 (англ. Psalm 103).

Русские переводы — не фон, а полноценный материал для анализа.
Каждый несёт свой характер:
- Синодальный — традиционный церковный звук, иногда архаичный;
- Кассиан — академическая ясность, близость к греческому тексту;
- НРП — современная динамичная передача, сглаживает остроту;
- Кулаков — часто интересен по точности и стилю.

ПРАВИЛО ПРИМЕНЕНИЯ:
Не перечисляй все переводы механически для каждого слова.
Сравнение появляется только там, где различие реально помогает понять текст —
когда один перевод вскрывает оттенок, который другой скрывает или сглаживает.

Каждая заметка — отдельный микро-блок. НЕ сливать несколько развилок в один абзац.

Формат каждого микро-блока:
- **Ключевая фраза / слово.**
Как переводят наиболее показательные версии (не обязательно все).
Какой оттенок уходит или добавляется.
Почему это важно именно в контексте этой проповеди.

Пример:
- **«Книги раскрыты были» (Откр. 20:12).**
LSB: «books were opened» — подчёркивает судебный акт.
Синодальный: «книги раскрыты» — точная передача.
НРП: «раскрылись книги» — пассивная конструкция стирает акцент Судьи как активного деятеля.
Именно этот агентивный оттенок важен в контексте проповеди.

Между каждыми двумя заметками — двойной перенос строки (\n\n).
Не включай этот section, если различия поверхностны и ничего не меняют.

====================================================================
SECTION TYPE 5 — ИСТОРИКО-БОГОСЛОВСКИЕ ЛИНИИ ЧТЕНИЯ
====================================================================

Это НЕ общий исторический обзор. Это НЕ "вот отцы, вот реформаторы, вот современные".

Это section про: **какие линии чтения этой темы существуют в истории церкви**.
Организуй материал не по хронологии, а по смысловым линиям.

ОСОБОЕ ПРАВИЛО ДЛЯ АРМИНИАНСТВА И КАЛЬВИНИЗМА:

Если материал затрагивает сотериологию — избрание, возрождение,
предопределение, стойкость святых, свободу воли:

1. ПОКАЖИ СПЕКТР, не одну точку:
   - Реформатская позиция (Дорт, 1689 Лондонское исповедание (LBCF), Кальвин, Оуэн, Эдвардс)
   - Умеренно-кальвинистская / 4-пунктная (Амираут, часть баптистов)
   - Классически арминианская (Арминий, Уэсли, Ремонстранты)
   - Пелагианская / полупелагианская (граница, за которой — ересь)

2. БОГОЦЕНТРИЗМ — нерв позиции канала:
   Ключевой вопрос: «кто действует первым в спасении — Бог или человек?»
   Каноны Дорта 3/4: полная испорченность и необходимость возрождающей
   благодати — фундамент, не деноминационный тег.

3. КОНКРЕТНЫЕ ИСТОЧНИКИ:
   - Каноны Дорта, Гл. 1-4; Лютер «О рабстве воли» (1525);
     Эдвардс «Freedom of the Will» (1754); Оуэн «Death of Death»;
     Фуллер «Gospel Worthy of All Acceptation» (1785)

4. ТОН: братьев-арминиан не называем еретиками; систему называем
   серьёзным заблуждением; богословская ясность ради того, чтобы
   вся слава спасения оставалась за Богом.

========================================================
ТРИ УРОВНЯ КОНКРЕТНОСТИ ИСТОЧНИКОВ
========================================================

УРОВЕНЬ 1 — точные формулировки из конфессиональных документов (используй уверенно):

- 1689 Лондонское баптистское исповедание
  Ключевые главы: Гл.1 (Писание), Гл.3 (предопределение), Гл.6 (грех), Гл.8 (Христос),
  Гл.10 (призвание), Гл.11 (оправдание), Гл.13 (освящение), Гл.14 (вера),
  Гл.15 (покаяние), Гл.17 (стойкость), Гл.32 (последний суд)
- Вестминстерское исповедание (1646)
- Краткий Вестминстерский катехизис — ключевые В.1, В.14, В.19, В.33, В.36, В.38
- Гейдельбергский катехизис — В.1, В.2, В.5, В.21, В.52, В.60, В.86, В.115
- Каноны Дортского синода — Гл.1 (избрание), Гл.2 (искупление), Гл.3/4 (благодать), Гл.5 (стойкость)
- Бельгийское исповедание — Ст.14–15 (грех), Ст.22–23 (оправдание), Ст.37 (суд)
- Второе Гельветическое исповедание
- Апостольский, Никейский, Афанасьевский символы веры
- Халкидонское определение (451)
- Каноны Оранжского собора (529)
- Chicago Statement on Biblical Inerrancy (1978) — 19 статей
- Chicago Statement on Hermeneutics (1982)
- Cambridge Declaration (1996)
- Nashville Statement (2017)
- Danvers Statement (1987)
- Statement on Social Justice and the Gospel (2018)
- Together for the Gospel doctrinal articles (2006)
- Lausanne Covenant (1974)
- Baptist Faith & Message (2000)
- Савойская декларация (1658) — конгрегационалистская адаптация Вестминстерского
- Филадельфийское исповедание (1742) — американская баптистская адаптация 1689
- New Hampshire Confession (1833) — влиятельное баптистское исповедание
- Abstract of Principles (1858) — основополагающий документ SBTS
- Orthodox Catechism (Hercules Collins, 1680) — баптистская адаптация Гейдельбергского
- Keach's Catechism (1693) — баптистский катехизис Бенджамина Кича
- Тридентский собор — сессия 6 (об оправдании), как антитеза реформатской позиции
- Joint Declaration on Justification (1999) — как компромисс, отвергнутый реформатами

Формат Уровня 1:
**Название документа**, глава/вопрос/статья N: «точная или близкая к точной формулировка».
Затем 1–2 предложения — как это связано с темой проповеди.

========================================================
УРОВЕНЬ 2 — АВТОРИЗОВАННЫЙ СПИСОК АВТОРОВ И ИСТОЧНИКОВ
========================================================

Ты можешь ссылаться ТОЛЬКО на авторов из этого списка.
Если автора нет в списке — НЕ упоминай его.
Это не ограничение знаний, а фильтр качества:
каждый автор здесь проверен и уместен.

────────────────────────────────────────────────
ПРАВИЛО ЦИТИРОВАНИЯ — ТРИ УРОВНЯ УВЕРЕННОСТИ:

Уровень A — ТОЧНАЯ ЦИТАТА:
  Используй ТОЛЬКО для конфессиональных документов (1689 Лондонское исповедание (LBCF), Вестминстерское и т.п.)
  и Писания. Их формулировки фиксированы и проверяемы.
  Формат: «точный текст в кавычках» — Документ, глава N.
  ЗАПРЕЩЕНО давать «точные цитаты» из книг авторов.

Уровень B — ИЗВЕСТНАЯ ПОЗИЦИЯ:
  Для авторов и их работ. Ты описываешь СУТЬ позиции, не цитируешь.
  Формат: "В работе X автор Y утверждает, что..."
  Формат: "Y в «X» развивает мысль о том, что..."
  ЗАПРЕЩЕНО: «Y пишет: "точная цитата"» — если ты не уверен на 100%.
  Лучше честное «Y утверждает...» чем фальшивая «точная цитата».

Уровень C — ТРАДИЦИЯ:
  Fallback если не можешь дать конкретную работу.
  Формат: "В пуританской традиции принято..."
  Не начинай с этого уровня — всегда пробуй A → B → C.

ЖЕЛЕЗНОЕ ПРАВИЛО:
  НИКОГДА не выдумывай цитат. НИКОГДА не приписывай автору
  формулировку, в которой ты не уверен. Не делай «выжимку» из
  нескольких мыслей автора и не подавай её как его слова.
  Одна честная ссылка лучше десяти приблизительных.
  Если не уверен — используй Уровень C или пропусти.
────────────────────────────────────────────────

1. ОСНОВОПОЛАГАЮЩИЕ ФИГУРЫ:

Августин Гиппонский (Augustine) — Confessions; City of God; On the Spirit and the Letter;
  Anti-Pelagian Writings. Особенно ценен: первородный грех, избрание, благодать.
  ВАЖНО: историческое свидетельство, НЕ авторитет наравне с Писанием.
  Церковь исповедовала благодать задолго до Реформации.
Мартин Лютер (Martin Luther) — О рабстве воли; О свободе христианина; 95 тезисов
Жан Кальвин (John Calvin) — Наставление в христианской вере (Institutes, 4 книги);
  Commentaries (все книги НЗ + большинство ВЗ — экзегетический стандарт)
Теодор Беза (Theodore Beza) — преемник Кальвина, систематизация предопределения
Ульрих Цвингли (Ulrich Zwingli) — реформатор Цюриха
Джон Нокс (John Knox) — реформатор Шотландии

2. ДРЕВНИЕ АВТОРЫ (историческое свидетельство):

Афанасий Великий (Athanasius) — On the Incarnation (божественность Христа vs арианство)
Ириней Лионский (Irenaeus) — Against Heresies (ранняя апологетика, рекапитуляция)
Иоанн Златоуст (Chrysostom) — Гомилии (применительная экспозиция, пастырский тон)
Ансельм Кентерберийский (Anselm) — Cur Deus Homo (заместительная жертва)

ПРАВИЛО ПО ОТЦАМ: использовать для аргумента преемственности —
  ключевые доктрины НЕ изобретение Реформации.

3. ПУРИТАНЕ И ПОСТ-РЕФОРМАЦИОННЫЕ БОГОСЛОВЫ:

Уильям Перкинс (William Perkins) — A Golden Chain (порядок спасения)
Джон Оуэн (John Owen) — Mortification of Sin; Glory of Christ; The Death of Death
  in the Death of Christ (definite atonement — классика)
Томас Уотсон (Thomas Watson) — Doctrine of Repentance; Body of Divinity; All Things for Good
Джон Баньян (John Bunyan) — Pilgrim's Progress; Grace Abounding (опыт обращения)
Стивен Чарнок (Stephen Charnock) — The Existence and Attributes of God (атрибуты Бога)
Томас Мантон (Thomas Manton) — комментарий на послание Иакова, Иуды
Томас Гудвин (Thomas Goodwin) — Heart of Christ; Christ Set Forth
Джеремия Берроуз (Jeremiah Burroughs) — The Rare Jewel of Christian Contentment
Ричард Сиббс (Richard Sibbes) — The Bruised Reed (пастырское утешение)
Джонатан Эдвардс (Jonathan Edwards) — Religious Affections; Sinners in the Hands
  of an Angry God; Freedom of the Will
Мэтью Генри (Matthew Henry) — Commentary (полный, дочитан до практики)
Ричард Бакстер (Richard Baxter) — The Reformed Pastor (с оговорками по сотериологии)
Томас Брукс (Thomas Brooks) — Precious Remedies Against Satan's Devices
Уильям Гурналл (William Gurnall) — The Christian in Complete Armour
Уильям Эймс (William Ames) — The Marrow of Theology (пуританская систематика)

4. РЕФОРМИРОВАННЫЕ БАПТИСТЫ (PARTICULAR / REFORMED BAPTISTS):

Исторические:
Бенджамин Кич (Benjamin Keach) — Tropologia; Baptist Catechism (Keach's Catechism, 1693)
Геркулес Коллинз (Hercules Collins) — An Orthodox Catechism (1680)
Джон Гилл (John Gill) — Exposition of the OT and NT (очень подробный баптист-кальвинист);
  Body of Divinity; The Cause of God and Truth
Эндрю Фуллер (Andrew Fuller) — The Gospel Worthy of All Acceptation
  (definite atonement + duty faith: Христос умер за избранных,
  но призыв к вере обращён ко всем без исключения)
Чарльз Сперджен (Charles Haddon Spurgeon) — Treasury of David (Псалмы);
  Morning/Evening; Lectures to My Students; тысячи проповедей
Уильям Кэри (William Carey) — отец современной миссии
Джеймс Петтигрю Бойс (James P. Boyce) — Abstract of Systematic Theology (основатель SBTS)
Джон Л. Дэгг (John L. Dagg) — Manual of Theology (первая систематика Southern Baptist)

Современные (20–21 вв.):
Артур Пинк (A.W. Pink) — The Sovereignty of God; The Attributes of God
Джон Пайпер (John Piper) — Desiring God; Let the Nations Be Glad
Водди Бокам (Voddie Baucham) — Family Driven Faith; Fault Lines
Марк Девер (Mark Dever) — Nine Marks of a Healthy Church; The Gospel and Personal Evangelism
Джеймс Уайт (James White) — The Potter's Freedom; Scripture Alone; апологетика
Томас Аскол (Thomas Ascol) — Founders Ministries, возрождение кальвинизма у баптистов
Конрад Мбеве (Conrad Mbewe) — пастор из Замбии, «Сперджен Африки»
Дэвид Платт (David Platt) — Radical
Алистер Бегг (Alistair Begg) — экспозиторская проповедь, Truth for Life
Питер Мастерс (Peter Masters) — Metropolitan Tabernacle (после Сперджена)
Том Неттлс (Tom Nettles) — By His Grace and for His Glory (история кальвинизма у баптистов)

5. ПРЕСВИТЕРИАНСКИЕ И КОНТИНЕНТАЛЬНЫЕ РЕФОРМИРОВАННЫЕ:

Франциск Турретин (Francis Turretin) — Institutes of Elenctic Theology
  (вершина реформатской схоластики, полемика с Римом и арминианством)
Вильгельмус а Бракел (Wilhelmus à Brakel) — The Christian's Reasonable Service (4 тома)
Чарльз Ходж (Charles Hodge) — Systematic Theology (3 тома, Принстонская школа)
Б.Б. Варфилд (B.B. Warfield) — Inspiration and Authority of the Bible;
  The Person and Work of Christ
Луис Беркхоф (Louis Berkhof) — Systematic Theology (учебник систематики)
Герман Бавинк (Herman Bavinck) — Reformed Dogmatics (4 тома)
Авраам Кайпер (Abraham Kuyper) — Lectures on Calvinism (сферы суверенитета)
Герхардус Вос (Geerhardus Vos) — Biblical Theology (основоположник
  реформатского библейского богословия, redemptive-historical метод)
Герман Витсиус (Herman Witsius) — The Economy of the Covenants
Р.Ч. Спрол (R.C. Sproul) — The Holiness of God; Faith Alone; Chosen by God;
  Are We Together?; Ligonier Ministries
Джон Фрейм (John Frame) — Systematic Theology (трёхперспективный подход)
Роберт Летам (Robert Letham) — The Holy Trinity; The Westminster Assembly
Синклер Фергюсон (Sinclair Ferguson) — The Whole Christ; Devoted to God
Лигон Дункан (Ligon Duncan) — Together for the Gospel
Кевин ДеЯнг (Kevin DeYoung) — Taking God at His Word; The Hole in Our Holiness
Майкл Хортон (Michael Horton) — The Christian Faith; Christless Christianity
Джоэл Бики (Joel Beeke) — Reformed Experiential Preaching; A Puritan Theology
Верн Пойтресс (Vern Poythress) — Symphonic Theology; Interpreting Eden
Гэвин Ортланд (Gavin Ortlund) — Finding the Right Hills to Die On

6. ДРУГИЕ ВЛИЯТЕЛЬНЫЕ ЕВАНГЕЛЬСКИЕ КАЛЬВИНИСТЫ:

Абнер Чау (Abner Chou) — The Hermeneutics of the Biblical Writers (2018).
  Президент The Master's University. Школа МакАртура.
  Специализация: герменевтика ВЗ-авторов, Израиль/Церковь,
  прогрессивное исполнение пророчеств. ПРИОРИТЕТНЫЙ источник
  по вопросам герменевтики и буквального чтения пророчеств.

Джон МакАртур (John MacArthur) — The Gospel According to Jesus;
  Strange Fire (цессационизм); MacArthur Study Bible
Д.А. Карсон (D.A. Carson) — Exegetical Fallacies; The Gagging of God;
  New Testament Commentary on John
Тим Келлер (Tim Keller) — The Reason for God; Preaching; Center Church
Пол Вошер (Paul Washer) — The Gospel's Power and Message;
  проповеди о ложной уверенности (Shocking Youth Message)
Мэтт Чандлер (Matt Chandler) — The Explicit Gospel
Мартин Ллойд-Джонс (Martyn Lloyd-Jones) — Studies in the Sermon on the Mount;
  Romans commentary series; Preaching and Preachers
Эдмунд Клауни (Edmund Clowney) — Preaching Christ in All of Scripture
Грэм Голдсуорти (Graeme Goldsworthy) — Gospel and Kingdom; According to Plan
Сидней Грейданус (Sidney Greidanus) — Preaching Christ from the Old Testament
Том Шрайнер (Tom Schreiner) — New Testament Theology; Romans commentary
Роберт Реймонд (Robert Reymond) — A New Systematic Theology
А.Х. Стронг (A.H. Strong) — Systematic Theology (баптистская классика)

7. ИСТОРИЧЕСКОЕ БОГОСЛОВИЕ:

Ричард Маллер (Richard Muller) — Post-Reformation Reformed Dogmatics (4 тома)
Карл Трумэн (Carl Trueman) — The Creedal Imperative; The Rise and Triumph of the Modern Self
Ярослав Пеликан (Jaroslav Pelikan) — The Christian Tradition (5 томов)
Алистер МакГрат (Alister McGrath) — Iustitia Dei (история доктрины оправдания)

8. РУССКОЕ ЕВАНГЕЛЬСКОЕ НАСЛЕДИЕ:

Современные: Алексей Прокопенко, Алексей Коломийцев (проповеди, пастырское служение)
Исторические: Василий Павлов, Иван Проханов, Василий Пашков,
  Никита Воронин, Уильям Фетлер, Георгий Винс

Правила по русским авторам:
- Используй если материал обрабатывается на русском языке
  и автор проповеди ссылается на русских братьев
- Для истории евангельского движения в России
- Не выдумывай цитат — у большинства нет широко известных опубликованных работ

9. ЛЕКСИКОНЫ И СЛОВАРИ:

BDAG — A Greek-English Lexicon of the NT (Bauer-Danker, 3rd ed., золотой стандарт НЗ)
HALOT — Hebrew and Aramaic Lexicon of the OT (золотой стандарт ВЗ)
BDB — Brown-Driver-Briggs Hebrew and English Lexicon
TDNT (Kittel) — Theological Dictionary of the NT (10 томов, богословские статьи)
NIDNTT (Colin Brown) — New International Dictionary of NT Theology (4 тома)
TWOT — Theological Wordbook of the OT
Louw-Nida — Greek-English Lexicon Based on Semantic Domains
Thayer's Greek-English Lexicon of the NT
Strong's Concordance — справочник номеров оригинальных слов

ПРАВИЛА ПО ЛЕКСИКОНАМ:
- BDAG/HALOT — для точного семантического диапазона
- TDNT (Kittel) — для богословского развития слова
  (осторожно: устаревшая этимологическая школа James Barr)
- TWOT — для ВЗ богословских оттенков
- Louw-Nida — для семантических полей и контекстных глоссов
- NIDNTT — для богословия слова в контексте (более современный чем TDNT)
- НЕ ВЫДУМЫВАТЬ тома и страницы. «По TDNT: ...» — допустимо.
  «TDNT, том 3, с. 456» — ЗАПРЕЩЕНО (если ты не уверен на 100%).
- НЕ изображать точные глоссы. «По BDAG: примерно...» — допустимо.
  «BDAG даёт дословно: ...» — только если ты АБСОЛЮТНО уверен.

10. КОММЕНТАРИИ И ЭКСПОЗИЦИЯ:

Matthew Henry's Commentary (полный — дочитан до практики)
John Calvin's Commentaries (все книги НЗ + большинство ВЗ)
John Gill's Exposition (ВЗ + НЗ — баптист-кальвинист, подробный)
William Hendriksen (New Testament Commentary series — реформатский)
Robert Haldane (Exposition of Romans — баптист, доктрины благодати)
Matthew Poole's Commentary (пуританский, сжатый)

11. ЭСХАТОЛОГИЯ И ИЗРАИЛЬ/ЦЕРКОВЬ:

John Walvoord (The Rapture Question — претрибуляционная позиция)
J. Dwight Pentecost (Things to Come — классика диспенсационального премилленаризма)
Charles Ryrie (Dispensationalism — систематизация диспенсационализма)
Wayne Grudem (Systematic Theology, эсхатология — премилленаризм)
Anthony Hoekema (The Bible and the Future — амилленаризм, для сравнения)
Sam Storms (Kingdom Come — прогрессивный амилленаризм, для сравнения)

12. ГЕРМЕНЕВТИКА И ВОПРОС ИЗРАИЛЬ/ЦЕРКОВЬ:

Абнер Чау (Abner Chou) — The Hermeneutics of the Biblical Writers (2018);
  президент The Master's University (семинария МакАртура).
  ПРИОРИТЕТНЫЙ ИСТОЧНИК по герменевтике и Израиль/Церковь.
  Позиция: авторы НЗ читали ВЗ буквально и пророчески —
  пророчества об Израиле исполняются буквально, а не переносятся на Церковь.
  Прогрессивный диспенсационализм с акцентом на авторский замысел ВЗ-авторов.
  Связь с МакАртуром: одна богословская школа, схожие герменевтические принципы.

John MacArthur — Israel and the Church (ключевые проповеди и статьи);
  Strange Fire (цессационизм); MacArthur Study Bible.
  По вопросу Израиль/Церковь — строгий диспенсационалист:
  обетования Израилю не переходят к Церкви, будут исполнены буквально.

Walter Kaiser (Recovering the Unity of the Bible; Toward an Old Testament Theology)
  — прогрессивное открытие герменевтики ВЗ авторов.

ПРАВИЛА ДЛЯ БЛОКА ИЗРАИЛЬ/ЦЕРКОВЬ:
- Если тема затрагивает — показывай: диспенсационализм (Чау, МакАртур) vs
  теология завета (Беркхоф, Хокема).
- ПОЗИЦИЯ КАНАЛА: обетования Израилю исполнятся буквально. Церковь ≠ Израиль.
- Не называй диспенсационализм «одной из позиций» там, где текст ясен.
  Называй замену Израиля Церковью «теологией замещения» (supersessionism) —
  позицией, которую позиция канала не разделяет.

НИКОГО ВНЕ ЭТОГО СПИСКА. Если автор не здесь — не упоминай.

УРОВЕНЬ 3 — общие акценты традиции.
Используй ТОЛЬКО как fallback если не можешь дать Уровень 1 или 2.
Формулировки: 'В пуританской традиции...', 'Реформатская экзегетика обычно...'
НИКОГДА не начинай с Уровня 3 — всегда пробуй сначала Уровень 1, потом 2.

Тематические ориентиры источников:
- Тема суда → 1689 гл.32, Бельгийское ст.37, Афанасьевский, Вестминстерское гл.33
- Тема оправдания → 1689 гл.11, Каноны Дорта гл.1, Тридентский (как антитеза), Спрол Faith Alone
- Тема Писания → 1689 гл.1, Чикагское заявление 1978, Warfield, Packer
- Тема святости → Гейдельбергский В.1, 1689 гл.13, Краткий кат. В.35, Спрол Holiness of God
- Тема покаяния → 1689 гл.15, Гейдельбергский В.88–90, Уотсон, Эдвардс Religious Affections
- Тема избрания → Каноны Дорта гл.1, 1689 гл.3, Спрол Chosen by God, Мюррей
- Тема Христа → 1689 гл.8, Халкидон, Мюррей Redemption Accomplished
- Тема ложной уверенности → Эдвардс Religious Affections, Уотсон, Оуэн Mortification
- Тема экуменизма/католицизма → ECT, Тридентский, Кембриджская декларация, Спрол Are We Together

Формат: 4–7 абзацев. Каждый — одна ТЕМА, внутри которой переплетаются разные эпохи и авторы.
Каждый тематический абзац — отдельный микро-блок с тире "-" как маркером (без эмодзи перед пунктом).

Формат каждого тематического блока:
- **Название темы / смысловой линии.**
Как эта линия раскрывается в документах и авторах.
Минимум 3 конкретные ссылки (Уровень 1 или 2) внутри каждого блока.

Пример:
- **Доктрина последнего суда в конфессиональных документах**
1689 Лондонское исповедание, гл.32 утверждает... Бельгийское исповедание, ст.37...

- **Пуританская линия пробуждающей проповеди**
Эдвардс в «Sinners in the Hands of an Angry God» строит...

Между тематическими блоками — двойной перенос строки (\n\n). Не сливать в один поток.
Жирным — название темы или источника.

====================================================================
SECTION TYPE 6 — ЗАБЛУЖДЕНИЯ И ОТВЕТ ОРТОДОКСИИ ⚖️
====================================================================

КОГДА ПОЯВЛЯЕТСЯ ЭТОТ РАЗДЕЛ:
Только если материал САМИ затрагивает конкретное заблуждение или ересь.
Если материал не касается спорных доктрин — раздел НЕ включать.
Не делать его обязательным. Не притягивать искусственно.

НАЗЫВАЕМ ВЕЩИ СВОИМИ ИМЕНАМИ:
Если это ересь — называем ересью. Если еретик — называем еретиком.
Не ищем ложной нейтральности там, где Писание и церковь говорят ясно.
Тон — аналитический, как историк богословия и защитник истины.
Без злости. Без личных выпадов. Обличение — ради святости Бога и истины.

РАЗЛИЧИЕ — что ересь, а что братское разногласие:

▸ ЕРЕСЬ (отступление от христианства — обличаем прямо):
  - Отрицание Троицы: арианство («Сын — творение»), модализм,
    Свидетели Иеговы, мормонизм
  - Отрицание полной Божественности Христа
  - Пелагианство и полупелагианство: спасение делами / совместными усилиями
  - Евангелие процветания (prosperity gospel) — «другое евангелие» (Гал 1:8-9)
  - Либерализм: отрицание авторитета и историчности Писания
  - Универсализм: все спасутся
  - Открытый теизм: Бог не знает будущего
  - Новоапостольская реформация (НАР): «новые апостолы», доминионизм

▸ БРАТСКИЕ РАЗНОГЛАСИЯ (обозначаем позицию, не разрываем общение):
  - Арминианство (РАЗНОГЛАСИЕ, не ересь — не всех арминиан называть еретиками)
  - Крещение: баптисты vs пресвитериане
  - Эсхатология: амилл, постмилл, премилл
  - Дары Духа: цессационизм vs континуационизм

ФОРМАТ КАЖДОЙ КАРТОЧКИ:

⚠️ **Название ереси/заблуждения**
Что это такое. Краткая история: когда и где возникло, кто продвигал.
Почему это НЕ просто «другая позиция» — что doctrinally разрушается.
Связь с другими заблуждениями если есть (ереси часто ходят парами).
Какие плоды приносит в жизни церкви и отдельного человека.

✅ **Ответ ортодоксальной церкви.**
Какие Соборы или Синоды осудили это заблуждение и когда.
Какие исповедания дают ответ (конкретно: документ, глава/статья).
Кто из верных служителей противостоял — и чем именно ответил.
Ключевые аргументы из Писания которые опровергают это учение.

СТИЛЬ:
- Аналитический и исторически точный
- Как Павел: «противостал ему, потому что он был неправ» (Гал 2:11)
- Без личной обиды, но с полной ясностью
- Показывать: вот что говорит ересь, вот почему это неправда,
  вот как церковь это уже разобрала

КОЛИЧЕСТВО КАРТОЧЕК: 1-3, только те что реально всплывают в материале.

====================================================================
SECTION TYPE 7 — КАРТА ИСТОЧНИКОВ ДЛЯ ДАЛЬНЕЙШЕГО ИЗУЧЕНИЯ
====================================================================

Не всегда обязательный, но очень полезный section.
Используй если тема богато раскрывается в традиции.

3–6 лучших источников: документ / книга / комментарий. Каждый — отдельный микро-блок.

Формат каждой карточки:
- **Русский автор, «Русское название» (English Author, *Original Title*)**
1–2 предложения: зачем именно это читать по этой теме. Что конкретно здесь найдёт читатель.

ВАЖНО — ФОРМАТ НАЗВАНИЙ:
1. Всегда сначала русское название, потом оригинальное в скобках курсивом — ВСЁ внутри одного **...**. Точку в конце НЕ ставить.
2. ПРИОРИТЕТ — УСТОЯВШИЙСЯ ПЕРЕВОД: прежде чем писать название, проверь по своим знаниям —
   издавалась ли эта книга на русском языке (Россия, СНГ, эмигрантские издательства)?
   Если да — используй именно то название, под которым она известна русскоязычным читателям.
   НЕ переводи самостоятельно если знаешь что перевод существует — самодельный перевод создаёт ложное название.
   Примеры ошибок которых нельзя допускать:
   «Сила и весть Евангелия» вместо «Сила и смысл Евангелия» (Paul Washer)
   «Путь паломника» вместо «Путешествие пилигрима» (John Bunyan)
3. Если ты НЕ уверен что книга издавалась по-русски — переведи название сам максимально точно.
   Лучше честный перевод, чем придуманное «официальное» название которого не существует.
4. Никогда не оставляй название только на английском.
5. Правильные русские имена авторов: R.C. Sproul = Р.Ч. Спрол (Роберт Чарльз), Paul Washer = Пол Вошер, John Owen = Джон Оуэн, Jonathan Edwards = Джонатан Эдвардс, Thomas Watson = Томас Уотсон, John Calvin = Жан Кальвин, John Murray = Джон Мюррей, John Bunyan = Джон Баньян, Charles Spurgeon = Чарльз Сперджен, D.A. Carson = Д.А. Карсон, Tim Keller = Тим Келлер, Wayne Grudem = Уэйн Грудем, J.I. Packer = Дж.И. Пакер.

Пример:
- **1689 Лондонское баптистское исповедание, главы 11, 13, 15**
Краткая и очень точная конфессиональная рамка по оправданию, освящению и покаянию.

- **Джон Мюррей, «Искупление совершённое и применённое» (John Murray, *Redemption Accomplished and Applied*)**
Особенно полезен для различения совершённого Христом искупления и его применения к верующему.

Между карточками — двойной перенос строки (\n\n). Не делай сухую стену имён.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ТИПОГРАФИЯ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

**жирный** — обязательно для:
- ссылок на Писание внутри текста абзаца: **2 Кор 5:17**, **Иез 36:26**, **Мф 7:21–23**
- ключевых богословских терминов при первом упоминании: **оправдание**, **возрождение**, **заветная верность**
- центральных цитат из текста Писания в кавычках: **«Я никогда не знал вас»**, **«новое творение»**
- названий доктрин и богословских позиций: **Каноны Дорта**, **1689 Лондонское исповедание (LBCF)**, **sola fide**
- имён богословов при первом упоминании в блоке: **Джон Оуэн**, **Джонатан Эдвардс**
- английских богословских терминов с русским пояснением (см. ниже)

*курсив* — для:
- оригинальных слов и транслитераций: *kainos*, *hesed*, *anomia*
- названий книг на латинице: *Religious Affections*, *Institutes*
- тонких смысловых оттенков и акцентов

***жирный курсив*** — очень редко, только для центральной богословской формулировки всего блока.

ПРАВИЛО АНГЛИЙСКИХ ТЕРМИНОВ:
Любой английский богословский термин — СНАЧАЛА русское название жирным, потом английский оригинал ТОЖЕ жирным с заглавной буквы в скобках.
Формат: **«Русское название»** (**English Term**) — краткое объяснение сути.

Русский перевод стоит ПЕРВЫМ и жирным В КАВЫЧКАХ — особенно если это буквальная калька, которая звучит непривычно по-русски («Лёгкое верие», «Спасение Господством»).
Английский оригинал — жирным в скобках, с заглавной буквы каждого слова (Title Case), для справки.
Правило применяется при ПЕРВОМ упоминании. При повторном — просто **«Русское название»**.

Примеры:
- **«Спасение Господством»** (**Lordship Salvation**) — принять Христа Спасителем невозможно без принятия Его Господом над жизнью
- **«Лёгкое верие»** (**Easy Believism**) — позиция, что для спасения достаточно интеллектуального согласия без покаяния и подчинения
- **«1689 Лондонское исповедание»** (**LBCF**) — главный вероисповедный документ реформатских баптистов
- **«Только верой»** (**Sola Fide**) — один из принципов Реформации: спасение по вере, без заслуг
- **«Только благодатью»** (**Sola Gratia**) — спасение исключительно по Божьей незаслуженной милости
- **Монергизм** (**Monergism**, от греч. «одно действие») — спасение как исключительно Божий акт, без человеческого соработничества
- **Синергизм** (**Synergism**, от греч. «совместное действие») — позиция, что в спасении участвуют и Бог, и свободная воля человека

Лимит: не больше 3–4 жирных на абзац. Не жирить всё подряд — только то что реально помогает глазу найти ключевое.

Blockquote (`> текст`) — для особенно сильной цитаты или сжатой формулировки. 0–1 на section.

ПРАВИЛО INLINE-ТАЙМКОДОВ:
Когда в content нужно сослаться на конкретный момент видео — пиши строго так:
⏱ **M:SS** или 📌 **M:SS**
Примеры: ⏱ **22:15**, 📌 **40:30**, ⏱ **1:05:42**
Никаких скобок, никакого текста рядом с числами внутри **, только время.
Система автоматически превратит это в кликабельную ссылку.

КРИТИЧЕСКИ ВАЖНО — ПОЗИЦИЯ ТАЙМКОДА В ПРЕДЛОЖЕНИИ:
Таймкод ВСЕГДА стоит ВНУТРИ предложения — ПЕРЕД точкой, не после. Точка ставится ПОСЛЕ таймкода.
НИКОГДА не начинай предложение или абзац с таймкода.
НИКОГДА не ставь таймкод в начале строки с тире после него.

❌ Плохо: ⏱ **17:30** — Когда отношения только начали налаживаться.
❌ Плохо: Когда отношения только начали налаживаться. ⏱ **17:30** ← точка до таймкода — ЗАПРЕЩЕНО.
✅ Хорошо: Когда отношения только начали налаживаться ⏱ **17:30**.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
СТИЛЬ ОФОРМЛЕНИЯ — ЗАГОЛОВКИ И ПУНКТЫ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

ЗАГОЛОВКИ РАЗДЕЛОВ (поле "title"):
В конце каждого заголовка ставь ОДНО образное тематическое эмодзи, подходящее по смыслу.
Примеры: «Ключевые понятия и Doctrinal Stakes 🔑», «Экзегетические узлы 📖»,
«Языки оригинала и лексика 📜», «Историко-богословские линии 🏛️»,
«Ложные чтения и спорные трактовки ⚖️», «Карта источников 📚».
Запрещено: эмодзи В НАЧАЛЕ заголовка. Только в конце.

ПУНКТЫ ВНУТРИ БОЛЬШИНСТВА РАЗДЕЛОВ:
Используй тире с жирным началом — без эмодзи-маркеров перед каждым пунктом:
- **Термин / понятие / текст / источник** — Аналитическое раскрытие.

КРИТИЧЕСКИ ВАЖНО — МАРКЕРЫ ПУНКТОВ:
Эмодзи из заголовка раздела (🏛️, 🔑, 📖 и т.д.) НЕ повторять перед каждым пунктом.
Эмодзи — только в заголовке section (поле "title"). Внутри content — только тире "-" как маркер.
Неправильно: 🏛️ **Пуританская линия...** (эмодзи перед пунктом)
Правильно:   - **Пуританская линия...** (тире перед пунктом)

ПРАВИЛО МИКРО-БЛОКОВ — НЕТ СПЛОШНЫМ ПОТОКАМ:
Если внутри section серия однотипных элементов (тексты, термины, исторические линии) —
каждый элемент = отдельный микро-блок с тире и жирным началом + двойной перенос строки.

Пример для серии библейских текстов:
- **Откровение 20:11**
Текст используется как... Экзегетически важно...

- **Исаия 6:1–5**
Параллель со святостью Судьи... Что поверхностное чтение упускает...

- **Матфея 7:21–23**
Разоблачение религиозной активности без...

Пример для исторических линий:
- **Пуританская линия пробуждающей проповеди**
...

- **Доктрина святости Бога в реформатском богословии**
...

ДЛЯ TYPE 6 (Заблуждения и ответ ортодоксии) — строго пары с визуальным разделением.
Каждая пара ⚠️ + ✅ — ОТДЕЛЬНЫЙ микро-блок. Между парами — двойной перенос строки (\\n\\n).

⚠️ **Название заблуждения.**
Описание: в чём суть ошибки, почему она опасна.

✅ **Ответ ортодоксальной церкви.**
Правильная позиция, документы и авторы.

КРИТИЧЕСКИ ВАЖНО: НЕ разрывай **bold** через перенос строки. Закрывай ** на ОДНОЙ строке.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ФОРМАТИРОВАНИЕ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

- content — только Markdown
- **Жирный текст** — для терминов, документов, названий работ
- Ссылки на книги/главы Библии — обычным текстом, НЕ жирным: Гал 6:14, Рим 8:28, Мф 7:21
- Цитаты из Библии в кавычках — курсивом: *«трава засыхает»*, *«Я никогда не знал вас»*
- *Курсив* — для оригинальных слов, транслитераций, названий книг на латинице
- Между карточками и микро-блоками — двойной перенос строки (\\n\\n)
- Не используй ## или ### внутри content
- Не создавай длинных essay-абзацев
- Каждый абзац — максимум 3–5 предложений
- Квадратные скобки [ и ] — нигде, никогда. Правильно: **Термин:** без скобок.
- Не оборачивай длинные абзацы (>60 символов) целиком в **жирный** — только короткие термины/метки жирным

КРИТИЧЕСКИ ВАЖНО — ПРОБЕЛЫ:
- После **жирного** перед тире ВСЕГДА пробел: **Термин** — описание (не **Термин**—)
- Между ** и ( ВСЕГДА пробел: **слово** (*транслитерация*, греч.)
- Между названиями переводов ВСЕГДА запятая: ESV, NASB, KJV (не ESVNASBKJV)
- Транслитерации ВСЕГДА курсивом: *anomia*, *ephygen*

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ОГРАНИЧЕНИЯ ПО ДЛИНЕ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

- 5–7 sections
- каждый section: 5–12 карточек / абзацев
- суммарный объём: примерно 3500–7000 символов
- не меньше 3000 символов
- не раздувай ради объёма

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ФОРМАТ ОТВЕТА
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Верни только чистый JSON, без ```json и без пояснений:

{{
  "outline": [
    {{"title": "Ключевые понятия и doctrinal stakes", "time": ""}},
    {{"title": "Ключевые тексты и экзегетические узлы", "time": ""}},
    {{"title": "Историко-богословские линии чтения", "time": ""}}
  ],
  "sections": [
    {{
      "title": "Ключевые понятия и doctrinal stakes",
      "time": "",
      "content": "**Оправдание** — ...\\n\\n**Покаяние** — ..."
    }}
  ]
}}

ПРАВИЛА JSON:
- time всегда пустая строка ""
- outline и sections должны совпадать
- не включай нерелевантные sections
- не включай пустые sections
- минимум 5 sections, максимум 7

Сделай именно такое исследовательское досье.
"""



# ════════════════════════════════════════════════════════════════════════════
# ПРОМПТ: РАЗМЫШЛЕНИЕ И ПРИМЕНЕНИЕ
# ════════════════════════════════════════════════════════════════════════════

REFLECTION_APPLICATION_PROMPT = """\
Ты пишешь как внимательный пастор-редактор.
Не как аналитик. Не как AI-эссеист. Не как составитель методичек.

ПРАВИЛО ПОЛНОТЫ БЛОКОВ:
Каждый активный раздел должен быть содержательно ПОЛНЫМ.
Независимо от того, какие другие страницы генерируются.

НЕ ДОПУСКАЕТСЯ:
- Раздел из 2-3 общих предложений
- «Также важно молиться» без конкретики
- Повтор очевидного из конспекта
- Формальные вопросы ради количества
- Абстрактные призывы без связи с конкретным материалом

МИНИМАЛЬНЫЙ ОБЪЁМ:
- TYPE 1 (Главный нерв): минимум 3 плотных абзаца
- TYPE 3 (Диагностика сердца): минимум 4 конкретных вопроса с раскрытием
- TYPE 7 (Молитвенный отклик): минимум 4 направления, привязанных к теме
- Суммарный объём: не менее 3500 символов

КАЖДЫЙ ВОПРОС И КАЖДОЕ НАПРАВЛЕНИЕ должны быть:
- Конкретными для ЭТОГО материала
- Невозможными применить к любой другой проповеди без изменений
- Вскрывающими реальные точки сопротивления или роста

Твоя задача — создать живую Telegraph-статью «Размышление и применение»,
которую захочется читать медленно, возвращаться к ней и применять.

Материал: {title}
Автор: {author}
Длительность: {duration}

Данные о материале:
- hermeneutic_method: {hermeneutic_method}
- main_topic: {main_topic}
- analysis_summary: {analysis_summary}
- argument_arc: {argument_arc}
- key_categories: {key_categories}

Вопросы для размышления (используй как сырьё, не копируй):
{questions_block}

Таймкоды ключевых моментов:
{timestamps_block}

{synopsis_context}

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ШАГ 0 — ОПРЕДЕЛИ ДУХ МАТЕРИАЛА ПРЕЖДЕ ЧЕМ ПИСАТЬ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Каждый материал требует своего тона. Ошибиться в тоне — значит написать мимо.

Подсказка: hermeneutic_method={hermeneutic_method} указывает стиль проповедника.
Используй это для калибровки тона: expository → следуй тексту, evangelistic → акцент на призыве,
narrative → живые образы, practical → конкретные шаги, topical → тематические связи.
key_categories выше показывают ключевые болевые точки и контрасты именно этого материала —
используй их как опоры при выборе разделов.

▸ Обличающая / покаянная проповедь
  Будь прямым. Вскрывай самообман и идолов сердца без смягчений.
  Задача: чтобы слово ударило, а не погладило.

▸ Проповедь о благодати, утешении, надежде, верности Бога
  Будь пастырем, который перевязывает раны, а не требует.
  Не притягивай покаяние туда, где текст говорит о радости и любви Бога.
  Задача: чтобы читатель ощутил, что его держат.

▸ Богословская / экзегетическая / историческая лекция
  Фокусируйся на смене угла зрения, богословском восторге, последствиях для мышления.
  Вопросы — про то, как видишь Бога и Писание, а не только про поведение.
  Задача: чтобы человек увидел что-то иначе, чем раньше.

▸ Евангелизационная / миссиологическая проповедь
  Применение через призму отношений с неверующими и личного свидетельства.
  Задача: конкретный разговор или шаг веры — не «думайте об этом», а «сделайте это».

▸ Практическая / этическая (семья, деньги, труд, служение)
  Конкретика без общих слов. Практика в деталях.
  Задача: реальный инструмент на эту неделю.

Материал может сочетать несколько жанров — адаптируй тон гибко внутри статьи.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ЧТО ЭТО И ЧЕМ НЕ ЯВЛЯЕТСЯ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Это — живое пасторское сопровождение после проповеди.
Инструмент, чтобы слово дошло до сердца, а не осталось в голове.

Это НЕ:
— второй конспект или аналитический разбор,
— список вопросов без живого контекста,
— essay «о теме вообще»,
— AI-дайджест с нейтральными округлыми формулировками.

Каждый раздел несёт конкретный пасторский импульс,
привязанный именно к этому материалу, а не к «теме вообще».

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ТИПОГРАФИЯ — ВНУТРЕННЯЯ МУЗЫКА ТЕКСТА
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Правильно дозированные акценты делают текст живым. Перегруз убивает.

**жирный** — обязательно для:
- центрального доктринального тезиса абзаца или поворотной мысли
- английских богословских терминов с двойным пояснением (см. ниже)

*курсив* — для цитат из Писания в кавычках: *«Я никогда не знал вас»*, *«трава засыхает»*
- ссылки на Писание (Мф 7:21, Рим 8:16) — обычным текстом, не жирным и не курсивом
***жирный курсив*** — очень редко, только для центрального нерва всего раздела.

ПРАВИЛО АНГЛИЙСКИХ ТЕРМИНОВ:
Формат: **«Русское название»** (**English Term**) — краткое объяснение сути.
Русский перевод стоит ПЕРВЫМ и жирным В КАВЫЧКАХ — особенно если это буквальная калька, звучащая непривычно по-русски.
Английский оригинал — жирным в скобках, Title Case (каждое слово с заглавной).
Применяется при ПЕРВОМ упоминании. При повторном — просто **«Русское название»**.
- **«Спасение Господством»** (**Lordship Salvation**) — принять Христа Спасителем невозможно без принятия Его Господом
- **«Лёгкое верие»** (**Easy Believism**) — спасение как интеллектуальное согласие без покаяния и подчинения
- **«1689 Лондонское исповедание»** (**LBCF**) — главный документ реформатских баптистов
- **«Только верой»** (**Sola Fide**) — спасение по вере, без заслуг

Лимит на абзац:
- максимум 2–3 жирных акцента,
- 0–1 курсивный,
- ***жирный курсив*** — не более 1 раза на весь раздел, только в самом сильном месте.

Примеры правильного использования:
— Мы живём так, будто покаяние можно отложить *на потом*.
— Самое страшное здесь — **не отсутствие Бога, а Его присутствие как Судьи**.
— ***У Белого Престола больше не будет переговоров — только истина.***

ПРАВИЛО INLINE-ТАЙМКОДОВ:
Когда в content нужно сослаться на конкретный момент видео — пиши строго так:
⏱ **M:SS** или 📌 **M:SS**
Примеры: ⏱ **22:15**, 📌 **40:30**, ⏱ **1:05:42**
Никаких скобок, никакого текста рядом с числами внутри **, только время.
Система автоматически превратит это в кликабельную ссылку.

КРИТИЧЕСКИ ВАЖНО — ПОЗИЦИЯ ТАЙМКОДА В ПРЕДЛОЖЕНИИ:
Таймкод ВСЕГДА стоит ВНУТРИ предложения — ПЕРЕД точкой, не после. Точка ставится ПОСЛЕ таймкода.
НИКОГДА не начинай предложение или абзац с таймкода.
НИКОГДА не ставь таймкод в начале строки с тире после него.

❌ Плохо: ⏱ **17:30** — Когда отношения только начали налаживаться.
❌ Плохо: Когда отношения только начали налаживаться. ⏱ **17:30** ← точка до таймкода — ЗАПРЕЩЕНО.
✅ Хорошо: Когда отношения только начали налаживаться ⏱ **17:30**.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
СТИЛЬ ОФОРМЛЕНИЯ — ЗАГОЛОВКИ, ПУНКТЫ, НУМЕРАЦИЯ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

ЗАГОЛОВКИ РАЗДЕЛОВ (поле "title"):
В конце каждого заголовка ставь ОДНО образное тематическое эмодзи — живое, подходящее по смыслу.
Это не функциональный маркер, а образный акцент. Выбирай творчески.
Примеры: «Зеркало для труса и лжеца 🪞», «Религия как способ спрятаться 🫣»,
«Молитва у подножия Истины 🙏», «Диагностика мёртвого сердца 🩻».
Запрещено: ставить эмодзи В НАЧАЛЕ заголовка. Только в конце.

ПУНКТЫ ВНУТРИ БОЛЬШИНСТВА РАЗДЕЛОВ:
Используй формат тире с жирным названием:
- **Название пункта.** Раскрытие — конкретное, живое, по существу.

Запрещено: ставить функциональные эмодзи (🔍, 🙏, 🏠, 👥 и т.д.) перед каждым пунктом.
Они делают текст пёстрым и шаблонным.

АНТИШАБЛОННОЕ ПРАВИЛО — СОДЕРЖАТЕЛЬНЫЕ ПОДПУНКТЫ:
Название каждого пункта должно раскрывать конкретную тему, а не служебную категорию.
Плохо: «Тема молитвы», «Для семейного разговора», «Практический шаг».
Хорошо: «Исповедание тайного самооправдания», «Жажда личного познания Бога».

ДЛЯ TYPE 6 (Предупреждения и контрасты) — строго пары с визуальным разделением.
Каждая пара ❌ + ✅ — ОТДЕЛЬНЫЙ микро-блок. Между парами — двойной перенос строки (\\n\\n).

❌ **Название ложной опоры или подмены.** Описание ошибки изнутри.

✅ **Библейская реальность.** Что верно по этой теме.

КРИТИЧЕСКИ ВАЖНО: НЕ разрывай **bold** через перенос строки. Закрывай ** на ОДНОЙ строке.

ДЛЯ TYPE 7 (Молитвенный отклик) — строго нумерованный список:
1. **Название направления молитвы.** О чём конкретно молиться, исходя из материала.
2. **Следующее направление.** Конкретно, привязанно к этой проповеди.
(и так далее, 4–6 пунктов)

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
АРХИТЕКТУРА — ВЫБЕРИ 5–7 РАЗДЕЛОВ ИЗ МЕНЮ НИЖЕ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

TYPE 1 и TYPE 7 — обязательные. Остальные — по смыслу материала.
Не включай раздел, если он мог бы подойти к любой другой проповеди без изменений.

====================================================================
TYPE 1 — ГЛАВНЫЙ НЕРВ (обязательный)
====================================================================

Первый раздел — сразу по существу. Не вводный обзор, не пересказ.

Ответь: где именно эта проповедь бьёт в жизнь обычного человека?
Что она требует прямо сейчас? Где болит сильнее всего?

Формат: 3–4 абзаца. Первый захватывает читателя.
Самую главную мысль раздела выдели ***жирным курсивом***.
Чередуй плотные абзацы с короткими ударными выводами.

====================================================================
TYPE 2 — СМЕНА ПАРАДИГМЫ
====================================================================

Как этот материал меняет представление о Боге, о себе, о мире или Писании.

Используй если проповедь несёт реальный богословский сдвиг —
разрушение стереотипа, новый угол зрения, неожиданное открытие.

Формат каждого пункта:
- **Конкретное ложное представление, которое разрушает эта проповедь.**
Что открывается вместо него — и что это меняет в жизни.

3–5 пунктов. Только реальные сдвиги, не общие духовные истины.

====================================================================
TYPE 3 — ДИАГНОСТИКА СЕРДЦА
====================================================================

4–6 глубоких вопросов для личной работы с совестью.

Каждый вопрос — отдельный абзац с раскрытием.
Используй вопросы из списка выше как сырьё — переработай, не копируй.

Обязательно включи хотя бы один вопрос:
— о сопротивлении (где я не хочу принимать сказанное),
— о переносе на других (где я применяю это к другим, а не к себе),
— о самообмане (где я оправдываю себя, не замечая этого).

ЕСЛИ МАТЕРИАЛ ЗАТРАГИВАЕТ БЛАГОДАТЬ, ИЗБРАНИЕ, СУВЕРЕНИТЕТ БОГА:
Включи минимум один вопрос на уровне:
«Что моё представление о спасении говорит о том, как я вижу Бога?»
«Где я инстинктивно приписываю себе роль в том, что целиком — Его дело?»
«Как понимание избрания меняет мою молитву? Мою миссию?»
НЕ делай длинную лекцию о 5 точках — один острый вопрос, затрагивающий сердце.

Формат каждого пункта:
- **Конкретный диагностический вопрос или утверждение.**
Раскрытие: почему это важно, что вскрывает, куда ведёт.

====================================================================
TYPE 4 — ПРАКТИКА
====================================================================

Конкретные шаги в повседневной жизни, привычках или служении.
Используй только если действие реально вытекает из материала.

Не «старайся быть лучше» — а: что конкретно, когда, как, с кем.

Формат каждого пункта:
- **Название конкретного действия.**
Что именно. Когда. Как. С кем. Детали, а не общие слова.

2–4 шага. Лучше меньше, но реальных.

====================================================================
TYPE 5 — ОТНОШЕНИЯ
====================================================================

Применение в браке, семье, дружбе, церковной общине или благовестии.

3–5 направлений — мягче по тону, но не банальные.
Можно разделить: для супруга / для детей / для малой группы / для неверующего друга.

Для семейного блока:
- **Содержательная тема разговора.** Короткое раскрытие.

Для группового блока — обязательно 1–2 вопроса на напряжение:
- **Где этот принцип вступает в конфликт с тем, как мы живём.**
- **Что из сказанного хочется опровергнуть — и почему стоит остановиться.**

====================================================================
TYPE 6 — ПРЕДУПРЕЖДЕНИЯ И КОНТРАСТЫ
====================================================================

Используй если материал обнажает ложные пути, духовные подмены,
религиозные суррогаты (самоправедность, формализм, страх без любви и т.д.).

Формат каждого пункта — содержательный подзаголовок для ❌:
❌ **Конкретная ложная опора или духовная подмена.**
Описание: как она выглядит изнутри, почему кажется нормальной.

✅ **Библейская реальность.**
Что говорит этот материал вместо неё — конкретно и без общих слов.

3–5 контрастов. Только те, что прямо вытекают из проповеди.

====================================================================
TYPE 7 — МОЛИТВЕННЫЙ ОТКЛИК (обязательный)
====================================================================

4–6 конкретных направлений молитвы после этого материала.

Это НЕ текст молитвы. Это НЕ «молитесь о ближних вообще».
Каждое направление привязано к конкретному содержанию этой проповеди.

Тон соответствует жанру:
— обличающая проповедь → молитва честного покаяния и просьба об изменении,
— проповедь о благодати → молитва благодарности, восхищения, доверия.

Формат — строго нумерованный список (не тире, не эмодзи-маркеры):
1. **Конкретное направление молитвы.**
Раскрытие: почему важно молиться именно об этом после этого материала.
2. **Следующее направление.** И так далее.

====================================================================
TYPE 8 — ЕВАНГЕЛЬСКИЙ ПРИЗЫВ И ПРОВЕРКА ВЕРЫ (условный)
====================================================================

Используй ТОЛЬКО если материал содержит хотя бы одно из:
- явное евангельское послание или призыв к обращению,
- обличение ложной веры, самоуверенности или самоправедности,
- призыв к покаянию, самоиспытанию или проверке состояния перед Богом,
- предупреждение о суде, вечности или цене нерешительности.

Если материал чисто образовательный / исторический / экзегетический
без евангельского нерва — ПРОПУСТИ этот раздел целиком.
Лучше не включить, чем включить формально.

Тон: прямой, но не жестокий. Пастырский, но не мягкотелый.
Как врач, который говорит правду — не чтобы напугать, а чтобы спасти.

Формат: 3 блока, каждый — отдельная аудитория.

─────────────────────────────────────────────────────────

**Для того, кто ещё не верит или честно ищет:**

- **Конкретный вопрос или вызов из этого материала.**
Что именно этот материал говорит человеку, который ещё вне Христа?
Не общие слова «Бог любит тебя» — а конкретное зерно из ЭТОЙ проповеди.

- **Что мешает сделать шаг — и что помогает.**
Какой конкретный страх, предубеждение или ложное представление
мог бы удерживать? И что из сказанного способно это преодолеть?

- **Евангелие в одном предложении — как оно звучит из этого материала.**
Не формула. Не богословский тезис. А живое слово Евангелия,
которое вытекает из этой конкретной проповеди.

─────────────────────────────────────────────────────────

**Для того, кто внутри церкви, но, возможно, не возрождён:**

Это самая опасная категория — человек, который ПРИВЫК считать себя
верующим, регулярно ходит в церковь, знает «правильные ответы»,
но никогда не был рождён свыше.

- **Признаки ложной уверенности, которые обнажает этот материал.**
Что может ВЫГЛЯДЕТЬ как вера, но не является ею?
  · Внешняя религиозность без внутреннего преображения
  · «Я всегда верил» без реального сокрушения и обращения
  · Эмоциональный опыт без плодов послушания
  · Знание доктрин без любви к Богу
  · Исповедание ртом без подчинения жизни

- **Одно главное предупреждение из этого материала.**
Самая трезвящая мысль — для того, кто никогда не проверял свою веру.
Матфея 7:21-23 — «Не всякий, говорящий Мне: Господи! Господи!»

─────────────────────────────────────────────────────────

**Для проверяющего свою веру — самоиспытание (2 Кор 13:5):**

«Испытывайте самих себя, в вере ли вы; самих себя исследывайте.»

- **Признаки подлинного обращения, которые звучат в этом материале.**
Что обнаруживает в себе человек, рождённый свыше?
  · борьба с грехом (не безгрешие, а БОРЬБА — Рим 7:15-25)
  · сокрушение о падениях (не равнодушие — Пс 50)
  · любовь к истине (не безразличие к доктрине — 1 Ин 2:21)
  · жажда Бога и Его слова (не привычка — Пс 41:2-3)
  · рост в послушании (не совершенство, а направление — Фил 3:12-14)
  · любовь к братьям (1 Ин 3:14)

- **Конкретный диагностический вопрос из этого материала.**
Один вопрос, который ставит перед зеркалом — не «веришь ли ты в Бога?»,
а более глубокий, вытекающий из сути ЭТОЙ проповеди.

Каждый подпункт — отдельный микро-блок с жирным началом
и двойным переносом строки.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
РИТМ И ВОЗДУХ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Текст должен дышать. Правила:

— Двойной перенос строки (\\n\\n) между каждым смысловым блоком.
— Большие плотные абзацы (3–5 предложений) чередуются с короткими ударными выводами.
— Короткое предложение после большого абзаца — это точка. Пауза. Удар.
— Не запрещай себе объёмные абзацы — если мысль требует раскрытия, раскрывай.
  Но после него дай читателю вздохнуть.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
СТРОГИЕ ЗАПРЕТЫ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

— Квадратные скобки [ и ] — нигде, никогда. Правильно: **Текст:** без скобок.
— «автор подчёркивает», «проповедник говорит о том, что»
— «данный раздел», «этот блок», «в этой секции»
— «следует отметить», «можно сказать», «таким образом», «необходимо подчеркнуть»
— «материал касается», «важно заметить», «стоит отметить»
— «AI Summary», «Cocoon AI», «Автор говорит»
— Любые мета-подписи, атрибуции, маркеры источника
— ## или ### внутри content (подзаголовки только через **жирный**)
— Эмодзи в заголовках разделов (title) — только в подпунктах внутри content
— Оборачивать длинные абзацы (>60 символов) целиком в **жирный** — только короткие заголовки/метки жирным, длинный текст пишется обычным шрифтом

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ФОРМАТ ОТВЕТА
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Верни только чистый JSON, без ```json и без пояснений:

{{
  "outline": [
    {{"title": "Заголовок раздела", "time": ""}},
    ...
  ],
  "sections": [
    {{
      "title": "Заголовок раздела",
      "time": "",
      "content": "Markdown..."
    }},
    ...
  ]
}}

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ФИНАЛЬНАЯ ПРОВЕРКА ПЕРЕД ОТПРАВКОЙ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Перед тем как вернуть JSON, проверь каждый раздел:

✓ Он уникален для этого материала — не подходит к любой другой проповеди?
✓ Ни один служебный ярлык не повторяется подряд (не «Тема молитвы:» × 5)?
✓ Каждый подпункт называет конкретную тему, а не категорию?
✓ В тексте есть живая типографика — жирный, курсив — без перегруза?
✓ Тон соответствует жанру материала — не навязано покаяние там, где текст о радости?
✓ Текст дышит — есть короткие ударные фразы после плотных абзацев?
✓ Нет квадратных скобок ни в одном месте?

Требования к объёму:
- 5–7 sections, все с time = "".
- outline совпадает с sections (только title + time, без content).
- Каждый section.content: минимум 3 абзаца через \\n\\n.
- Вокруг **жирного**, *курсива*, ***жирного курсива*** — пробелы.
- НЕ повторяй одну мысль разными словами.
- Суммарный объём: не менее 3500 символов, желательно 4000–6000.
- Не раздувай ради объёма — каждый абзац несёт реальную пасторскую нагрузку.
"""


# ════════════════════════════════════════════════════════════════════════════
# ФУНКЦИЯ: create_telegraph_study_analysis
# ════════════════════════════════════════════════════════════════════════════

async def create_telegraph_study_analysis(
    ai_data: dict | None,
    title: str,
    author: str,
    yt_url: str = "",
    compact_fn=None,
    rutube_url: str = "",
    vk_url: str = "",
    synopsis_outline: list | None = None,
    duration: float = 0,
) -> str | None:
    """
    «Разбор материала» -- большая article-like страница.
    Объединяет и усиливает: аналитику, термины, Писание, богословскую глубину.
    compact_fn -- callable() без аргументов, fallback (напр. create_telegraph_analytics).
    """
    if not GEMINI_CLIENTS:
        logger.info("StudyAnalysis: нет GEMINI_CLIENTS -- fallback")
        return await compact_fn() if compact_fn else None

    _ai = ai_data or {}
    author_clean = _clean_meta_line(author or "") or "Автор не указан"
    title_clean  = _clean_meta_line(title  or "") or "Без названия"

    real_author  = normalize_author_name(_ai.get("real_author") or author) or author_clean
    prompt_title = normalize_title_text(_ai.get("real_title") or "") or title_clean
    prompt_title = title_case_fragment(prompt_title)

    _fmt = _ai.get("format", "other") or "other"

    _duration_raw = _ai.get("duration") or 0
    try:
        _duration = float(_duration_raw)
    except (ValueError, TypeError):
        _duration = 0

    _key_cats    = _ai.get("key_categories", []) or []
    key_cats_str = "\n".join(f"- {c}" for c in _key_cats[:12]) if _key_cats else "не указаны"

    _td           = _ai.get("terms_data") or {}
    concepts      = _td.get("concepts", [])      or []
    scripture     = _td.get("scripture", [])      or []
    translations  = _td.get("translations", [])   or []
    lexicon_notes = _td.get("lexicon_notes", [])  or []

    def _fmt_block(items, max_n=12):
        if not items:
            return "не указаны"
        return "\n".join(f"- {str(x).split('||')[0].strip()}" for x in items[:max_n])

    def _fmt_scripture(items, max_n=12):
        if not items:
            return "не указаны"
        parts = []
        for x in items[:max_n]:
            fields = str(x).split("||")
            ref = fields[0].strip() if fields else ""
            use = fields[1].strip() if len(fields) > 1 else ""
            parts.append(f"- {ref}: {use}" if use else f"- {ref}")
        return "\n".join(parts)

    _hm_study = _ai.get("hermeneutic_method") or "mixed"

    if synopsis_outline:
        _outline_lines = "\n".join(
            f"  {i+1}. {s.get('title', '')} [{s.get('time', '')}]"
            for i, s in enumerate(synopsis_outline)
        )
        _synopsis_context = (
            f"Конспект уже создан со следующими разделами:\n{_outline_lines}\n\n"
            f"НЕ ПОВТОРЯЙ содержание этих разделов. Твоя задача — дать то, "
            f"чего в конспекте нет: исследовательскую глубину, богословский анализ, "
            f"разбор терминов и Писания."
        )
    else:
        _synopsis_context = ""

    prompt = STUDY_ANALYSIS_PROMPT.format(
        title=prompt_title,
        author=real_author,
        duration=format_timestamp(_duration) if _duration else "не указана",
        format_name=_fmt,
        hermeneutic_method=_hm_study,
        main_topic=(_ai.get("main_topic") or "").strip()[:800]        or "не указана",
        analysis_summary=(_ai.get("analysis_summary") or "").strip()[:1000] or "не указано",
        argument_arc=(_ai.get("argument_arc") or "").strip()[:800]    or "не указан",
        key_categories=key_cats_str,
        timestamps=(_ai.get("timestamps") or "").strip()[:800]        or "не указаны",
        concepts=_fmt_block(concepts, 12),
        scripture=_fmt_scripture(scripture, 12),
        translations=_fmt_block(translations, 8),
        lexicon_notes=_fmt_block(lexicon_notes, 8),
        synopsis_context=_synopsis_context,
    )

    tg_title = prompt_title
    if real_author and real_author != "Автор не указан":
        tg_title = f"{tg_title} — {real_author}"

    return await _run_expanded_pipeline(
        label="StudyAnalysis",
        prompt=prompt,
        page_prefix="Разбор материала",
        tg_title=tg_title,
        author=author_clean,
        yt_url=yt_url,
        include_toc=True,
        fallback_fn=compact_fn,
        max_tokens=16000,
        rutube_url=rutube_url,
        vk_url=vk_url,
        duration=int(duration) if duration else 0,
    )


# ════════════════════════════════════════════════════════════════════════════
# ФУНКЦИЯ: create_telegraph_reflection_application
# ════════════════════════════════════════════════════════════════════════════

async def create_telegraph_reflection_application(
    questions: list,
    title: str,
    author: str,
    ai_data: dict | None = None,
    duration: int = 0,
    yt_url: str = "",
    compact_fn=None,
    rutube_url: str = "",
    vk_url: str = "",
    synopsis_outline: list | None = None,
) -> str | None:
    """
    «Размышление и применение» -- большая пасторская article-like страница.
    Объединяет и усиливает: вопросы, применение, самоиспытание, молитву.
    compact_fn -- callable() без аргументов, fallback (напр. create_telegraph_questions).
    """
    if not GEMINI_CLIENTS:
        logger.info("ReflectionApplication: нет GEMINI_CLIENTS -- fallback")
        return await compact_fn() if compact_fn else None

    _ai          = ai_data or {}
    author_clean = _clean_meta_line(author or "") or "Автор не указан"
    title_clean  = _clean_meta_line(title  or "") or "Без названия"
    real_author  = normalize_author_name(_ai.get("real_author") or author) or author_clean
    prompt_title = normalize_title_text(_ai.get("real_title") or "") or title_clean
    prompt_title = title_case_fragment(prompt_title)  # единый стиль капитализации

    _ts       = (_ai.get("timestamps") or "").strip()
    _ts_block = "\n".join(
        f"- {l.strip()}" for l in _ts.splitlines() if l.strip()
    ) or "не указаны"
    duration_str = format_timestamp(duration) if duration else "не указана"

    if isinstance(questions, list) and questions:
        green  = [q for q in questions if str(q).startswith("\U0001f7e2")]
        blue   = [q for q in questions if str(q).startswith("\U0001f535")]
        other  = [q for q in questions if not str(q).startswith(("\U0001f7e2", "\U0001f535"))]
        merged = (green + other + blue)[:15]
    else:
        merged = []

    if not merged:
        logger.info("ReflectionApplication: нет вопросов -- fallback")
        return await compact_fn() if compact_fn else None

    def _strip_marker(q: str) -> str:
        return re.sub(r"^[\U0001f7e2\U0001f535]\s*", "", str(q)).strip()

    questions_block = "\n".join(f"- {_strip_marker(q)}" for q in merged)

    _key_cats_list = (_ai.get("key_categories") or [])
    _key_cats_str  = "\n".join(f"- {c}" for c in _key_cats_list[:12]) if _key_cats_list else "не указаны"
    _hm            = (_ai.get("hermeneutic_method") or "mixed") or "mixed"

    # Формируем контекст конспекта для Reflection — чтобы не повторять его содержание
    if synopsis_outline:
        _outline_lines = "\n".join(
            f"  {i+1}. {s.get('title', '')} [{s.get('time', '')}]"
            for i, s in enumerate(synopsis_outline)
        )
        _synopsis_context = (
            f"Конспект уже создан со следующими разделами:\n{_outline_lines}\n\n"
            f"НЕ ПОВТОРЯЙ содержание этих разделов. Твоя задача — дать то, "
            f"чего в конспекте нет: пасторское применение, самоиспытание, "
            f"молитвенный отклик и личные вопросы к читателю."
        )
    else:
        _synopsis_context = ""

    prompt = REFLECTION_APPLICATION_PROMPT.format(
        title=prompt_title,
        author=real_author,
        duration=duration_str,
        hermeneutic_method=_hm,
        main_topic=(_ai.get("main_topic") or "").strip()[:800]              or "не указана",
        analysis_summary=(_ai.get("analysis_summary") or "").strip()[:1000] or "не указана",
        argument_arc=(_ai.get("argument_arc") or "").strip()[:800]          or "не указан",
        key_categories=_key_cats_str,
        questions_block=questions_block,
        timestamps_block=_ts_block,
        synopsis_context=_synopsis_context,
    )

    tg_title = (
        f"{prompt_title} — {real_author}"
        if real_author and real_author != "Автор не указан"
        else prompt_title
    )

    return await _run_expanded_pipeline(
        label="ReflectionApplication",
        prompt=prompt,
        page_prefix="Размышление и применение",
        tg_title=tg_title,
        author=author_clean,
        yt_url=yt_url,
        include_toc=True,
        fallback_fn=compact_fn,
        max_tokens=14000,
        rutube_url=rutube_url,
        vk_url=vk_url,
        duration=int(duration) if duration else 0,
    )


def _normalize(s: str) -> str:
    """Нормализует строку для сравнения: lower, ё→е, убрать пунктуацию/скобки/|."""
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[|()\[\]{}<>\"'«».,!?;:\-–—]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _normalize_duration(d) -> int:
    """Нормализует duration — может быть int, float, str ('3542' или '00:59:02')."""
    if isinstance(d, (int, float)):
        return int(d)
    if isinstance(d, str):
        d = d.strip()
        if not d:
            return 0
        if ":" in d:
            parts = d.split(":")
            try:
                if len(parts) == 3:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                elif len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
            except ValueError:
                return 0
        try:
            return int(float(d))
        except ValueError:
            return 0
    return 0

# Стоп-слова для религиозного контента — встречаются в каждом втором видео
# и не несут идентификационной нагрузки при поиске по RuTube/VK
_SEARCH_STOPWORDS: frozenset = frozenset({
    # Русские
    "вера", "благодать", "покаяние", "суд", "истина", "бог", "господь", "христос",
    "иисус", "библия", "писание", "церковь", "молитва", "грех", "спасение",
    "евангелие", "проповедь", "слово", "дух", "святой", "жизнь", "любовь",
    "свидетельство", "служение", "братья", "сестры", "боже", "святого",
    # Английские
    "faith", "grace", "truth", "god", "lord", "christ", "jesus", "bible",
    "prayer", "sin", "salvation", "gospel", "church", "sermon", "spirit",
    "holy", "love", "life", "word",
})

# Порог досрочного выхода: score >= этого значения считается надёжным совпадением
_EARLY_EXIT_SCORE  = 0.80
# Сколько лучших кандидатов выводить в лог (остальные молча отбрасываются)
_LOG_TOP_N         = 5

def _best_match(results: list, title: str, duration: int, platform: str,
                early_exit: bool = True, log: bool = True) -> tuple[str | None, float]:
    norm_title  = _normalize(title)
    title_words = set(norm_title.split())
    # Убираем стоп-слова из scoring: они встречаются в каждом религиозном видео
    # и дают ложные совпадения по частым словам
    meaningful_words = title_words - _SEARCH_STOPWORDS if len(title_words) > 2 else title_words
    rare_words  = {w for w in meaningful_words if len(w) > 5}
    best_score, best_url = 0.0, None
    threshold = 0.15 if len(title_words) <= 3 else 0.18
    candidates: list[tuple[float, str, str]] = []   # (score, url, log_line)

    for item in results:
        item_title    = item.get("title", "") or item.get("name", "")
        item_duration = _normalize_duration(item.get("duration", 0))
        item_url      = f"https://rutube.ru/video/{item.get('id','')}/" if platform == "rutube" else (item.get("url") or item.get("player", ""))
        if duration > 300 and 0 < item_duration < 60:
            continue
        norm_item  = _normalize(item_title)
        item_words = set(norm_item.split())
        item_meaningful = item_words - _SEARCH_STOPWORDS if len(item_words) > 2 else item_words
        # Двустороннее сравнение по значимым словам (без стоп-слов)
        intersection = meaningful_words & item_meaningful
        if not meaningful_words or not item_meaningful:
            word_score = 0.0
        else:
            word_score = min(len(intersection) / min(len(meaningful_words), len(item_meaningful)), 1.0)
        # Штраф за большой избыток слов у кандидата (много лишнего в названии)
        excess_ratio = len(item_meaningful) / max(len(meaningful_words), 1)
        if excess_ratio > 3.0:
            word_score *= 0.7  # кандидат в 3+ раза длиннее запроса — скорее всего не то
        rare_bonus = 0.1 * len(rare_words & item_meaningful) / max(len(rare_words), 1) if rare_words else 0
        dur_diff   = abs(item_duration - duration) if duration and item_duration else 999
        dur_score  = (1.0 if dur_diff < 30 else 0.7 if dur_diff < 120
                      else 0.4 if dur_diff < 300 else 0.15 if dur_diff < 600 else 0.0)
        score      = word_score * 0.60 + dur_score * 0.30 + rare_bonus * 0.10

        # Минимальный порог по словам: запрет возвращать результат только
        # потому что совпало имя автора, а название проповеди — нет.
        # Для коротких запросов (≤3 слова) порог ниже — там всё важно.
        min_word_score = 0.20 if len(meaningful_words) <= 3 else 0.30
        if word_score < min_word_score:
            score = 0.0  # обнуляем — даже хорошая длительность не спасёт

        if score > threshold:
            log_line = (
                f"  [{platform}] '{item_title[:55]}' dur={item_duration}s "
                f"words={len(intersection)}/{len(title_words)}∩{len(item_words)} "
                f"word={word_score:.2f} dur={dur_score:.1f} rare={rare_bonus:.2f} → {score:.2f}"
            )
            candidates.append((score, item_url, log_line))

        if score > best_score and score > threshold:
            best_score, best_url = score, item_url

        # Досрочный выход при очень хорошем совпадении
        if early_exit and best_score >= _EARLY_EXIT_SCORE:
            break

    # Логируем только топ-N кандидатов, отсортированных по убыванию score
    if log:
        top = sorted(candidates, key=lambda x: x[0], reverse=True)[:_LOG_TOP_N]
        for _, _, log_line in top:
            logger.info(log_line)
        if len(candidates) > _LOG_TOP_N:
            logger.info(f"  [{platform}] ... и ещё {len(candidates) - _LOG_TOP_N} кандидатов с score > threshold (не показаны)")

    return best_url, best_score

def _best_match_confident(results: list, title: str, duration: int) -> bool:
    """Возвращает True если в results уже есть кандидат с score >= _EARLY_EXIT_SCORE.
    Используется для страничного раннего выхода в search_rutube."""
    norm_title  = _normalize(title)
    title_words = set(norm_title.split())
    meaningful_words = title_words - _SEARCH_STOPWORDS if len(title_words) > 2 else title_words
    rare_words  = {w for w in meaningful_words if len(w) > 5}
    threshold   = _EARLY_EXIT_SCORE
    for item in results:
        item_title    = item.get("title", "") or item.get("name", "")
        item_duration = _normalize_duration(item.get("duration", 0))
        if duration > 300 and 0 < item_duration < 60:
            continue
        norm_item  = _normalize(item_title)
        item_words = set(norm_item.split())
        item_meaningful = item_words - _SEARCH_STOPWORDS if len(item_words) > 2 else item_words
        intersection = meaningful_words & item_meaningful
        if not meaningful_words or not item_meaningful:
            continue
        word_score = min(len(intersection) / min(len(meaningful_words), len(item_meaningful)), 1.0)
        # Синхронизировано с _best_match: минимальный порог по словам
        # без него score от duration может дать ложное "уверенное" совпадение
        min_word_score = 0.20 if len(meaningful_words) <= 3 else 0.30
        if word_score < min_word_score:
            continue
        rare_bonus = 0.1 * len(rare_words & item_words) / max(len(rare_words), 1) if rare_words else 0
        dur_diff   = abs(item_duration - duration) if duration and item_duration else 999
        dur_score  = (1.0 if dur_diff < 30 else 0.7 if dur_diff < 120
                      else 0.4 if dur_diff < 300 else 0.15 if dur_diff < 600 else 0.0)
        score = word_score * 0.60 + dur_score * 0.30 + rare_bonus * 0.10
        if score >= threshold:
            return True
    return False


def _clean_search_title(title: str) -> str:
    """Убираем номер серии, имя автора в скобках — для поиска на RuTube/VK."""
    t = re.sub(r"^\d+\s*[|:]\s*", "", title)   # '7 | Название' → 'Название'
    t = re.sub(r"\s*\([^)]+\)\s*$", "", t)       # 'Название (Автор)' → 'Название'
    return t.strip()

def _extract_search_keywords(title: str) -> str:
    """Извлекает ключевые слова для поиска на RuTube — убирает ссылки на Писание и лишние дефисы."""
    t = _clean_search_title(title)
    # Убираем ссылки на Писание: "Даниила 4:4-37", "Матфея 11:28-30"
    t = re.sub(r'\b\w+\s+\d+[:\-]\d+(?:[:\-]\d+)?\b', '', t)
    # Убираем одиночные разделители-дефисы
    t = re.sub(r'\s+-\s+', ' ', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t or _clean_search_title(title)  # fallback если всё вырезало

def _build_search_title(ai_data: dict | None, fallback_title: str) -> str:
    """Строит оптимальный поисковый заголовок для RuTube/VK.
    Приоритет: real_event + real_title + real_author → full_title из yt-dlp.
    Не использует короткий real_title в одиночку если он < 20 символов.
    """
    if not ai_data:
        return _clean_search_title(fallback_title)

    real_event  = (ai_data.get("real_event")  or "").strip()
    real_title  = normalize_title_text(ai_data.get("real_title")  or "")
    real_author = normalize_author_name(ai_data.get("real_author") or "")

    parts = []
    if real_event:
        parts.append(real_event)
    if real_title:
        parts.append(real_title)
    if real_author:
        parts.append(real_author)

    combined = " - ".join(parts) if parts else ""

    # Если combined получился осмысленным и длиннее короткого real_title — используем его
    if combined and len(combined) >= 10:
        return combined

    # Fallback: исходный full_title из yt-dlp (наиболее точный)
    return _clean_search_title(fallback_title)


async def search_rutube(title: str, channel_name: str = "", duration: int = 0,
                        fallback_title: str = "") -> str | None:
    loop = asyncio.get_running_loop()
    mapping = get_channel_mapping(channel_name)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Bot/1.0)"}
    search_title = _extract_search_keywords(title)

    try:
        if not (mapping and mapping.get("rutube_channel_id")):
            logger.info(f"RuTube: для канала '{channel_name}' нет rutube_channel_id")
            return None

        cid = mapping["rutube_channel_id"]
        logger.info(
            f"RuTube: поиск ТОЛЬКО по листингу канала cid={cid}, "
            f"q='{search_title}' (original: '{title}')"
        )

        all_results: list[dict] = []
        max_pages = 100  # 100 стр × 20 = 2000 видео — покрывает большие каналы
        per_page  = 20

        for page in range(1, max_pages + 1):
            try:
                resp = await loop.run_in_executor(
                    None,
                    lambda p=page: requests.get(
                        f"https://rutube.ru/api/video/person/{cid}/?page={p}&per_page={per_page}",
                        timeout=10,
                        headers=headers,
                    ),
                )

                if resp.status_code != 200:
                    logger.info(f"RuTube листинг: status={resp.status_code} на стр.{page}")
                    break

                page_results = resp.json().get("results", []) or []
                if not page_results:
                    logger.info(f"RuTube листинг: пусто на стр.{page}")
                    break

                all_results.extend(page_results)

                # Страничный ранний выход: после каждой страницы проверяем,
                # не нашлось ли уже уверенное совпадение — если да, не грузим дальше
                if page >= 2 and _best_match_confident(all_results, search_title, duration):
                    logger.info(f"RuTube: досрочный выход на стр.{page} ({len(all_results)} видео) — найдено уверенное совпадение")
                    break

                if len(page_results) < per_page:
                    break

            except Exception as e:
                logger.warning(f"RuTube листинг стр.{page} ошибка: {e}")
                break

        if not all_results:
            logger.info("RuTube: листинг канала пуст, видео не найдено")
            return None
        logger.info(f"RuTube: листинг загружен — {len(all_results)} видео за {min(page, max_pages)} стр.")

        # Ищем лучшее совпадение в листинге по всем вариантам заголовка
        listing_result = None
        listing_score  = 0.0
        for i, _t in enumerate([search_title, title] + ([fallback_title] if fallback_title and fallback_title != title else [])):
            _r, _sc = _best_match(all_results, _t, duration, "rutube", log=(i == 0))
            if _r and _sc > listing_score:
                listing_score  = _sc
                listing_result = _r
            if listing_score >= _EARLY_EXIT_SCORE:
                break

        # Уверенное совпадение из листинга — сразу возвращаем
        if listing_result and listing_score >= _EARLY_EXIT_SCORE:
            logger.info(f"RuTube best_match (листинг, уверенный score={listing_score:.2f}): {listing_result}")
            return listing_result

        # Слабое совпадение — не возвращаем мусор
        _MIN_RETURN_SCORE = 0.50
        if listing_result and listing_score >= _MIN_RETURN_SCORE:
            logger.info(f"RuTube: возвращаем листинговый (score={listing_score:.2f}): {listing_result}")
            return listing_result

        if listing_result:
            logger.info(f"RuTube: score={listing_score:.2f} ниже порога {_MIN_RETURN_SCORE} — видео не найдено (лучший кандидат не подходит)")
        else:
            logger.info("RuTube: видео не найдено в листинге")
        return None

    except Exception as e:
        logger.warning(f"RuTube поиск: {e}")
        return None

def _build_vk_search_title(
    full_title: str,
    real_title: str = "",
    real_event: str = "",
    real_author: str = "",
) -> str:
    """Строит короткий поисковый запрос для VK video.search (3–8 слов).
    VK плохо ищет по длинным перегруженным заголовкам.
    """
    def _cl(s):
        s = (s or "").strip()
        s = re.sub(r"\s+", " ", s)
        return s.strip(" -–—|:,.;")

    def _dedup(text):
        seen, words = set(), []
        for w in text.split():
            if w.lower() not in seen:
                seen.add(w.lower())
                words.append(w)
        return " ".join(words)

    def _shorten_authors(a):
        a = _cl(a)
        if not a:
            return ""
        parts = [p.strip() for p in re.split(r",|/|;|and|и", a, flags=re.IGNORECASE) if p.strip()]
        return " ".join(parts[:2])

    def _is_qa(t):
        t = t.lower()
        return any(m in t for m in ["вопросы и ответы", "q&a", "qa", "questions and answers",
                                     "вопрос-ответ", "panel", "панель", "дискуссия"])

    ft  = _cl(re.sub(r"\[[^\]]+\]|\([^\)]*\)$", "", full_title))
    rt  = _cl(real_title)
    rev = _cl(real_event)
    ra  = _shorten_authors(real_author)

    qa = _is_qa(ft) or _is_qa(rt) or _is_qa(rev)

    parts = []
    if qa:
        if rev and len(rev.split()) <= 4:
            parts.append(rev)
        if rt:
            parts.append(rt)
        elif "вопросы и ответы" in ft.lower():
            parts.append("Вопросы и Ответы")
        if ra:
            parts.append(ra)
    else:
        if rev:
            parts.append(rev)
        if rt:
            parts.append(rt)
        if ra:
            parts.append(ra)

    candidate = _dedup(_cl(" - ".join(p for p in parts if p)))
    if len(candidate.split()) < 3:
        candidate = _dedup(ft)

    words = candidate.split()
    if len(words) > 8:
        candidate = " ".join(words[:8])

    return _cl(candidate) or _cl(ft)


async def search_vk_video(title: str, channel_name: str = "", duration: int = 0,
                          ai_data: dict | None = None) -> str | None:
    loop      = asyncio.get_running_loop()
    mapping   = get_channel_mapping(channel_name)
    vk_token  = os.getenv("VK_API_TOKEN", "").strip()
    headers   = {"User-Agent": "Mozilla/5.0 (compatible; Bot/1.0)"}
    search_title = _build_vk_search_title(
        full_title=title,
        real_title=(ai_data or {}).get("real_title", ""),
        real_event=(ai_data or {}).get("real_event", ""),
        real_author=(ai_data or {}).get("real_author", ""),
    )
    logger.info(f"VK поиск: q='{search_title}', token present={bool(vk_token)}")
    try:
        params: dict = {"q": search_title, "count": 10, "v": "5.199"}
        if mapping and mapping.get("vk_owner_id"):
            params["owner_id"] = mapping["vk_owner_id"]
        elif mapping and mapping.get("vk_domain"):
            try:
                r = await loop.run_in_executor(None, lambda: requests.get(
                    "https://api.vk.com/method/groups.getById",
                    params={"group_id": mapping["vk_domain"], "v": "5.199"},
                    timeout=8, headers=headers))
                gdata = r.json().get("response", [{}])
                if gdata:
                    gid = gdata[0].get("id")
                    if gid:
                        params["owner_id"] = f"-{gid}"
            except Exception:
                pass
        if vk_token:
            params["access_token"] = vk_token
        resp = await loop.run_in_executor(None, lambda: requests.get(
            "https://api.vk.com/method/video.search", params=params, timeout=10, headers=headers))
        logger.info(f"VK ответ: status={resp.status_code}")
        if resp.status_code == 200:
            rjson = resp.json()
            # Логируем ошибку VK API если есть
            if "error" in rjson:
                logger.warning(f"VK API error: {rjson['error'].get('error_msg', rjson['error'])}")
                return None
            items = rjson.get("response", {}).get("items", [])
            logger.info(f"VK items: {len(items)}")
            for item in items:
                owner_id = item.get("owner_id", "")
                vid_id   = item.get("id", "")
                canonical = f"https://vk.com/video{owner_id}_{vid_id}"
                if mapping and mapping.get("vk_domain"):
                    item["url"] = f"https://vkvideo.ru/@{mapping['vk_domain']}?z=video{owner_id}_{vid_id}"
                else:
                    item["url"] = canonical
                # Всегда держим fallback на каноничный vk.com
                item["_canonical"] = canonical
            result, _sc = _best_match(items, search_title, duration, "vk")
            logger.info(f"VK best_match: {result}")
            return result
    except Exception as e:
        logger.warning(f"VK Video поиск: {type(e).__name__}")
    return None

async def find_alternative_links(title: str, channel_name: str, duration: int,
                               ai_data: dict | None = None,
                               fallback_title: str = "") -> dict:
    # Ищем только если канал известен — есть в CHANNEL_MAP
    if not get_channel_mapping(channel_name):
        logger.info(f"Канал '{channel_name}' не в CHANNEL_MAP — поиск RuTube/VK пропущен")
        return {"rutube": None, "vk": None}
    logger.info(f"Ищем альт-ссылки: title='{title}', channel='{channel_name}', duration={duration}")
    rutube_url, vk_url = await asyncio.gather(
        asyncio.create_task(search_rutube(title, channel_name, duration, fallback_title=fallback_title)),
        asyncio.create_task(search_vk_video(title, channel_name, duration, ai_data=ai_data)),
        return_exceptions=True,
    )
    if isinstance(rutube_url, Exception): logger.warning(f"RuTube: {rutube_url}"); rutube_url = None
    if isinstance(vk_url,    Exception): logger.warning(f"VK: {vk_url}");          vk_url    = None
    logger.info(f"Альт-ссылки: rutube={rutube_url}, vk={vk_url}")
    return {"rutube": rutube_url, "vk": vk_url}


# ─── Share-текст и клавиатура ─────────────────────────────────


def build_telegraph_links(
    telegraph_url="", quotes_tg_url="", questions_tg_url="",
    terms_tg_url="", study_tg_url="", reflection_tg_url="",
) -> str:
    items = []
    if telegraph_url:
        items.append(f'├ 📝 <a href="{telegraph_url}">Читать конспект</a>')
    if study_tg_url:
        items.append(f'├ 📖 <a href="{study_tg_url}">Разбор материала</a>')
    elif quotes_tg_url:
        # Legacy: показываем Аналитику только если нет новой страницы Разбора
        items.append(f'├ 🧠 <a href="{quotes_tg_url}">Аналитика</a>')
    if reflection_tg_url:
        items.append(f'├ 🙏 <a href="{reflection_tg_url}">Размышление и применение</a>')
    elif questions_tg_url:
        # Legacy: показываем Вопросы только если нет новой страницы Размышления
        items.append(f'├ ❓ <a href="{questions_tg_url}">Вопросы</a>')
    if terms_tg_url:
        items.append(f'├ 📚 <a href="{terms_tg_url}">Термины</a>')
    if not items:
        return ""
    items[-1] = items[-1].replace("├", "└", 1)
    return "\n".join(items)


def build_platform_links(url: str = "", rutube_url: str = "", vk_url: str = "") -> str:
    # Премиум emoji-id для справки:
    # YouTube:  5463206079913533096  (оставлен как tg-emoji)
    # RuTube:   5321388265549373570  (заменён на обычный 🎬)
    # VK:       5278229754099540071  (заменён на обычный 🎦)
    parts = []
    if url:
        yt = get_youtube_video_url(url)
        parts.append(f'<tg-emoji emoji-id="5463206079913533096">🎥</tg-emoji> <a href="{yt}">YouTube</a>')
    if rutube_url:
        parts.append(f'<tg-emoji emoji-id="5321388265549373570">🎬</tg-emoji> <a href="{rutube_url}">RuTube</a>')
    if vk_url:
        parts.append(f'<tg-emoji emoji-id="5278229754099540071">🎦</tg-emoji> <a href="{vk_url}">VK</a>')
    if not parts:
        return ""
    return "\n".join(parts)



# ─── Группировка настроек для /settings ─────────────────────
_SETTINGS_GROUPS: list[tuple[str, list[str | tuple[str, str]]]] = [
    # ── Конспект и разборы ──────────────────────────────────────
    ("📋 Конспект", ["synopsis", "caption_full_text", "generate_pdf"]),
    ("📖 Разборы",  ["study_analysis", "reflection_application"]),
    ("📊 Компактные", ["analytics", "questions", "terms"]),
    # ── Шортс ───────────────────────────────────────────────────
    ("✂️ Шортс",   ["shorts", "shorts_audio_normalize", "shorts_subtitles",
                     "shorts_subtitles_karaoke", "shorts_subtitles_light",
                     "shorts_title_poster", "shorts_snapshot",
                     "shorts_montage", "shorts_highlights",
                     "__speed__"]),
    # ── Клипы ───────────────────────────────────────────────────
    ("🎬 Клипы",    ["clips", "clips_snapshot"]),
]

def _build_settings_keyboard(settings: dict[str, bool] | None = None) -> InlineKeyboardMarkup:
    """Строит клавиатуру настроек: по 2 кнопки в ряд для коротких, 1 для длинных.

    Принимает словарь настроек ``settings`` (уже загруженный через settings_get_all).
    Если не передан — делает одно синхронное чтение всех настроек за раз.
    Вызывайте из async-кода через: settings_get_all() → run_in_executor → передать сюда,
    чтобы не блокировать event loop.
    """
    if settings is None:
        settings = settings_get_all()
    buttons: list[list[InlineKeyboardButton]] = []
    speed   = shorts_speed_get()

    # Кнопки длиннее этого порога (символов в label) — на всю строку
    WIDE_THRESHOLD = 18

    for group_title, keys in _SETTINGS_GROUPS:
        # Разделитель-заголовок (не-кликабельный)
        buttons.append([InlineKeyboardButton(f"─── {group_title} ───", callback_data="noop")])

        # Собираем кнопки группы, потом укладываем по 2 в ряд где возможно
        group_btns: list[InlineKeyboardButton] = []
        for key in keys:
            if key == "__speed__":
                # Скорость — сначала сбрасываем буфер, потом на отдельную строку
                if group_btns:
                    # укладываем накопленные кнопки
                    _flush_buttons(buttons, group_btns, WIDE_THRESHOLD)
                    group_btns = []
                buttons.append([InlineKeyboardButton(
                    f"⚡ Скорость: {speed}x",
                    callback_data="setting_speed"
                )])
            else:
                state = settings.get(key, SETTINGS_DEFAULTS.get(key, True))
                icon  = "✅" if state else "☑️"
                label = SETTINGS_LABELS.get(key, key)
                group_btns.append(InlineKeyboardButton(
                    f"{icon} {label}",
                    callback_data=f"setting:{key}"
                ))

        if group_btns:
            _flush_buttons(buttons, group_btns, WIDE_THRESHOLD)

    return InlineKeyboardMarkup(buttons)


def _flush_buttons(
    rows: list,
    btns: list,
    wide_threshold: int,
) -> None:
    """Укладывает список кнопок в rows: короткие по 2 в ряд, длинные по одной."""
    i = 0
    while i < len(btns):
        label = btns[i].text  # текст уже с иконкой
        # Убираем иконку состояния (✅/☑️) для замера длины — ☑️ = 2 кодпойнта
        # поэтому используем lstrip вместо slicing
        pure = label.lstrip("✅☑️️ ").strip()
        if len(pure) > wide_threshold:
            # Длинная — на всю строку
            rows.append([btns[i]])
            i += 1
        else:
            # Короткая — пробуем взять следующую в пару
            if i + 1 < len(btns):
                next_label = btns[i + 1].text
                next_pure  = next_label[2:] if len(next_label) > 2 else next_label
                if len(next_pure) <= wide_threshold:
                    rows.append([btns[i], btns[i + 1]])
                    i += 2
                    continue
            rows.append([btns[i]])
            i += 1


async def settings_command(update, context):
    """Показывает панель настроек с переключаемыми галочками."""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Настройки доступны только администраторам.")
        return
    all_settings = await asettings_get_all()
    await update.message.reply_text(
        "⚙️ <b>Настройки</b>\n\n"
        "Нажмите на пункт — он сразу переключится.\n"
        "<i>Shorts и Clips влияют только на видеофрагменты.</i>",
        parse_mode="HTML",
        reply_markup=_build_settings_keyboard(all_settings)
    )


async def handle_callback(update, context) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    # ── Разделитель (нажатие игнорируется) ──────────────────────
    if data == "noop":
        return

    # ── Скорость Shorts (цикличное переключение) ─────────────
    if data == "setting_speed":
        user_id = update.effective_user.id
        if ADMIN_IDS and user_id not in ADMIN_IDS:
            await query.answer("⛔ Нет доступа.", show_alert=True)
            return
        new_speed = shorts_speed_cycle()
        await query.answer(f"⚡ Скорость Shorts: {new_speed}x")
        try:
            all_settings = await asettings_get_all()
            await query.edit_message_reply_markup(reply_markup=_build_settings_keyboard(all_settings))
        except Exception:
            pass
        return

    # ── Настройки (toggle bool) ───────────────────────────────
    if data.startswith("setting:"):
        user_id = update.effective_user.id
        if ADMIN_IDS and user_id not in ADMIN_IDS:
            await query.answer("⛔ Нет доступа.", show_alert=True)
            return
        key = data.split(":", 1)[1]
        if key in SETTINGS_DEFAULTS:
            new_val = not await asettings_get(key)
            await asettings_set(key, new_val)
            state_text = "включено ✅" if new_val else "выключено ☑️"
            label = SETTINGS_LABELS.get(key, key)
            await query.answer(f"{label}: {state_text}")
            try:
                all_settings = await asettings_get_all()
                await query.edit_message_reply_markup(reply_markup=_build_settings_keyboard(all_settings))
            except Exception:
                pass
        return

    # ── Short trim (подрезка начала/конца) ──────────────────────
    if data.startswith("strim:"):
        parts = data.split(":", 2)
        if len(parts) != 3:
            await query.answer("Неверный формат.", show_alert=True)
            return
        _, mode, short_id = parts
        rec = short_trim_get(short_id)
        if not rec:
            await query.answer("Данные Short не найдены — возможно видео устарело.", show_alert=True)
            return
        video_path = Path(rec["video_path"])
        if not video_path.exists():
            await query.answer("Исходное видео не найдено на сервере.", show_alert=True)
            return

        start_s = rec["start_seconds"]
        end_s   = rec["end_seconds"]

        if mode == "nosub":
            # Отправить версию без субтитров
            nosub_path_str = rec.get("video_path_nosub", "")
            if not nosub_path_str:
                await query.answer("🚫 Версия без субтитров недоступна для этого клипа.", show_alert=True)
                return
            nosub_path = Path(nosub_path_str)
            if not nosub_path.exists():
                await query.answer("⏳ Файл без субтитров истёк — пришлите видео заново.", show_alert=True)
                return

            await query.answer("🚫 Отправляю без субтитров...")

            try:
                candidate = {}
                try:
                    candidate = json.loads(rec.get("candidate_json", "{}"))
                except Exception:
                    pass

                caption = build_short_caption(
                    candidate=candidate,
                    performer=rec.get("performer", ""),
                    real_author=rec.get("real_author", ""),
                    real_event=rec.get("real_event", ""),
                    format_name=rec.get("format_name", ""),
                    yt_url=rec.get("yt_url", ""),
                    vk_url=rec.get("vk_url", ""),
                    rutube_url=rec.get("rutube_url", ""),
                )
                if len(caption) > 1024:
                    caption = caption[:1021] + "..."

                with open(nosub_path, "rb") as vf:
                    await query.message.reply_video(
                        video=vf,
                        caption=f"🚫 Без субтитров\n\n{caption}",
                        duration=end_s - start_s,
                        width=720,
                        height=1280,
                        parse_mode="HTML",
                        write_timeout=120,
                        read_timeout=120,
                        connect_timeout=30,
                    )
            except Exception as nosub_err:
                logger.warning(f"strim:nosub error: {nosub_err}")
                await query.message.reply_text(f"❌ Ошибка: {str(nosub_err)[:150]}")
            return

        if mode == "s10":
            start_s = max(0, start_s - 10)
            label = "Начало расширено -10 сек"
        elif mode == "e10":
            end_s = end_s + 10
            label = "Конец расширен +10 сек"
        elif mode == "e20":
            end_s = end_s + 20
            label = "Конец расширен +20 сек"
        else:
            await query.answer("Неизвестный режим.", show_alert=True)
            return

        if start_s >= end_s:
            await query.answer("⚠️ Начало не может быть позже конца.", show_alert=True)
            return

        await query.answer(f"✂️ {label}, перерезаю...")

        candidate = {}
        try:
            candidate = json.loads(rec["candidate_json"])
        except Exception:
            pass
        candidate["start_seconds"] = start_s
        candidate["end_seconds"]   = end_s
        candidate["duration_seconds"] = end_s - start_s

        try:
            out_path = DOWNLOAD_DIR / f"{uuid.uuid4().hex[:16]}_trim.mp4"
            ok = await render_short_clip(
                source_video_path=video_path,
                output_path=out_path,
                start_seconds=start_s,
                end_seconds=end_s,
                visual_mode=rec.get("visual_mode", "full_frame_vertical"),
            )
            if not ok or not out_path.exists():
                await query.message.reply_text("❌ Не удалось перерезать Short.")
                return

            # Сохраняем новый short для возможных повторных trim
            new_short_id = uuid.uuid4().hex[:16]
            short_trim_save(
                short_id=new_short_id,
                video_path=str(out_path),
                start_seconds=start_s,
                end_seconds=end_s,
                visual_mode=rec.get("visual_mode", "full_frame_vertical"),
                yt_url=rec.get("yt_url", ""),
                vk_url=rec.get("vk_url", ""),
                rutube_url=rec.get("rutube_url", ""),
                performer=rec.get("performer", ""),
                real_author=rec.get("real_author", ""),
                real_event=rec.get("real_event", ""),
                format_name=rec.get("format_name", ""),
                candidate_json=json.dumps(candidate, ensure_ascii=False),
                video_path_nosub=rec.get("video_path_nosub", ""),  # сохраняем путь до субтитров
            )
            # Кнопки ретрима + 🚫Sub если есть версия без субтитров
            _new_nosub_path = rec.get("video_path_nosub", "")
            _new_nosub_buttons = []
            if _new_nosub_path and Path(_new_nosub_path).exists():
                _new_nosub_buttons = [InlineKeyboardButton("🚫Sub", callback_data=f"strim:nosub:{new_short_id}")]
            new_trim_keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("⏪ Начало -10", callback_data=f"strim:s10:{new_short_id}"),
                InlineKeyboardButton("⏭ Конец +10",  callback_data=f"strim:e10:{new_short_id}"),
                InlineKeyboardButton("⏭⏭ Конец +20", callback_data=f"strim:e20:{new_short_id}"),
                *_new_nosub_buttons,
            ]])

            caption = build_short_caption(
                candidate=candidate,
                performer=rec.get("performer", ""),
                real_author=rec.get("real_author", ""),
                real_event=rec.get("real_event", ""),
                format_name=rec.get("format_name", ""),
                yt_url=rec.get("yt_url", ""),
                vk_url=rec.get("vk_url", ""),
                rutube_url=rec.get("rutube_url", ""),
            )
            if len(caption) > 1024:
                caption = caption[:1021] + "..."

            with open(out_path, "rb") as vf:
                await query.message.reply_video(
                    video=vf,
                    caption=f"✂️ {label}\n\n{caption}",
                    duration=end_s - start_s,
                    width=720,
                    height=1280,
                    parse_mode="HTML",
                    reply_markup=new_trim_keyboard,
                    write_timeout=120,
                    read_timeout=120,
                    connect_timeout=30,
                )
        except Exception as trim_err:
            logger.warning(f"strim callback error: {trim_err}")
            await query.message.reply_text(f"❌ Ошибка при перерезке: {str(trim_err)[:150]}")
        return

    # ── Сброс всего кэша ─────────────────────────────────────
    if data == "resetcache:all":
        user_id = update.effective_user.id
        if ADMIN_IDS and user_id not in ADMIN_IDS:
            await query.answer("⛔ Нет доступа.", show_alert=True)
            return
        loop = asyncio.get_running_loop()
        def _delete_all_cache():
            with sqlite3.connect(DB_PATH) as conn:
                r = conn.execute("DELETE FROM video_cache").rowcount
                conn.commit()
                return r
        rows = await loop.run_in_executor(None, _delete_all_cache)
        await query.edit_message_text(f"🗑 Весь кэш удалён: {rows} записей.")
        return


    if ":" not in data:
        await query.answer("Неизвестное действие.", show_alert=True)
        return
    action, video_id = data.split(":", 1)
    # Нет зарегистрированных обработчиков для этого action.
    # Сообщаем пользователю вместо молчаливого завершения.
    logger.warning(f"handle_callback: необработанный action={action!r} video_id={video_id!r}")
    await query.answer("⚠️ Действие устарело или не поддерживается.", show_alert=True)


# ─── Shorts MVP ───────────────────────────────────────────────

SHORTS_PROMPT = """\
Ты — ассистент для поиска лучших фрагментов для Shorts из длинного христианского видео.

Материал: {title}
Длительность: {duration}

Уже известные данные о материале:
- format: {format_name}
- real_author: {real_author}
- real_event: {real_event}
- analysis_summary: {analysis_summary}
- argument_arc: {argument_arc}
- key_categories: {key_categories}
- timestamps:
{timestamps}
{questions_block}
ЗАДАЧА:
Найди 3–5 лучших кандидатов для Shorts.

Это должны быть не случайные нарезки, а короткие, ёмкие, автономные фрагменты, которые:
- понятны без большого пропущенного контекста,
- содержат сильную мысль, ответ, предупреждение, определение или ясное объяснение,
- подходят для отдельного просмотра как самостоятельный ролик,
- не начинаются с пустого захода,
- не заканчиваются на оборванной мысли,
- не повторяют одно и то же.

ГЛАВНЫЙ ПРИНЦИП:
Выбирай не "красивую длину", а естественно завершённый фрагмент.
Если мысль заканчивается на 1:43, не сокращай её до 1:30.
Если ей нужно 2:28, не обрезай её ради короткого стандарта.

ПРАВИЛА ПО ФОРМАТАМ:

Если format = qa:
- ищи shorts по отдельным вопросам и сильным ответам
- каждый short ОБЯЗАТЕЛЬНО начинается с момента озвучивания вопроса или максимально близко к нему
- каждый short ОБЯЗАТЕЛЬНО заканчивается ПОСЛЕ завершения ответа на этот вопрос
- никогда не начинай short с середины ответа и не обрывай до его завершения
- особенно хороши вопросы, которые понятны и важны сами по себе

Если format = sermon или lecture:
- ищи сильные тезисы
- яркие предупреждения
- практические применения
- точные определения
- афористичные места
- короткие автономные объяснения

Если format = interview или discussion:
- ищи короткие самостоятельные смысловые фрагменты
- сильные ответы
- ясные противопоставления
- фрагменты, понятные без длинного контекста

ОГРАНИЧЕНИЯ ПО ДЛИНЕ:
- минимум: 35 секунд
- желательно: 45–150 секунд
- максимум: 180 секунд
- фрагменты длиной 2:00–3:00 — это нормально, если они цельные и автономные
- не сокращай хороший фрагмент только потому, что он длиннее 90 секунд

КРИТИЧЕСКОЕ ПРАВИЛО:
НЕ выбирай длину по круглой цифре.
Не надо специально делать 60, 90, 120 или 180 секунд.
Длина должна определяться содержанием и естественным завершением мысли.
Хороший short может быть 52 сек, 1:18, 1:47, 2:26 или 2:54.

КРИТИЧЕСКИЕ ПРАВИЛА ДЛЯ НАЧАЛА И КОНЦА:
- Каждый short должен быть смыслово завершённым.
- Не обрывай фрагмент на середине предложения, примера, аргумента или вывода.
- Конец short должен приходиться на естественное завершение мысли.
- Если для завершения смысла нужно добавить ещё 2–7 секунд, лучше добавить их.
- Начало short тоже должно быть достаточно близким к сути: не брать долгий пустой заход.
- Избегай фрагментов, которые понятны только после большого пропущенного контекста.
- Каждый кандидат должен быть автономным и законченным как мини-фрагмент.

СТРОГО:
- не выдумывать start/end
- не указывать фрагмент, если не уверен
- не брать скучные переходы, служебные реплики и пустые вступления
- не делать overlap кандидатов более чем на 10 секунд без очень веской причины
- каждый кандидат должен быть реально отдельным кандидатом, а не вариацией того же самого
{speed_limits}
Используй kind только из этого списка:
- qa_answer
- key_thesis
- warning
- application
- definition
- contrast
- exhortation
- quote

Для каждого кандидата верни:
- start — время начала
- end — время конца
- title — короткий заголовок
- hook — короткая цепляющая первая строка для short
- reason — почему этот фрагмент хорош для short
- kind — тип фрагмента
- hashtags — 2–4 релевантных коротких хэштега без символа #

Требования к title:
- короткий
- ясный
- читаемый
- не техничный
- если это qa, title может быть самим вопросом

Требования к hook:
- короткий
- цепляющий
- человеческий
- без пошлого кликбейта

Требования к reason:
- коротко объясняет, почему этот фрагмент хорош как Shorts

Требования к hashtags:
- только 2–4
- на русском языке
- CamelCase
- без символа #

ФОРМАТ ОТВЕТА:
Только чистый JSON, без ```json и без пояснений.

{{
  "shorts_candidates": [
    {{
      "start": "12:40",
      "end": "14:28",
      "title": "Как отличить ложных учителей?",
      "hook": "Где проходит опасная граница между ошибкой и лжеучением?",
      "reason": "Цельный и автономный сильный ответ на важный вопрос.",
      "kind": "qa_answer",
      "hashtags": ["Лжеучителя", "БиблейскаяИстина", "ДоктриныБлагодати"]
    }}
  ]
}}
"""

EXTRAS_PROMPT = """\
Ты — ассистент для создания двух типов дополнительных коротких роликов из христианского видео.

Материал: {title}
Длительность: {duration}
Формат: {format_name}
Автор: {real_author}

Уже известные данные:
- analysis_summary: {analysis_summary}
- argument_arc: {argument_arc}
- timestamps: {timestamps}
- key_categories: {key_categories}

ЗАДАЧА 1 — MONTAGE
Найди 1–3 кандидата для тематической склейки из разных частей видео.

Правила:
- 3–5 фрагментов
- каждый фрагмент: 7–30 секунд
- суммарно: 45–100 секунд
- фрагменты из разных частей материала
- минимум 90 секунд между соседними фрагментами
- фрагменты должны раскрывать ОДНУ тему
- не выбирать по красивым круглым цифрам, а по естественной завершённости фразы

ЗАДАЧА 2 — HIGHLIGHTS
Найди один лучший рекламный ролик из самых сильных моментов.

Правила:
- 4–7 фрагментов
- каждый фрагмент: 4–18 секунд
- суммарно: 50–100 секунд
- фрагменты должны быть яркими, цепляющими и автономными
- не брать организационные вставки
- не выбирать длину по круглым числам

ФОРМАТ ОТВЕТА:
Только чистый JSON, без ```json и без комментариев.

{{
  "montage_candidates": [
    {{
      "theme": "Общая тема",
      "title": "Короткий заголовок",
      "fragments": [
        {{"start": "5:30", "end": "5:48", "summary": "Что в этом фрагменте"}},
        {{"start": "23:15", "end": "23:35", "summary": "Что в этом фрагменте"}},
        {{"start": "41:02", "end": "41:20", "summary": "Что в этом фрагменте"}}
      ],
      "hashtags": ["Тег1", "Тег2", "Тег3"]
    }}
  ],
  "highlights": {{
    "title": "Заголовок highlights",
    "fragments": [
      {{"start": "3:22", "end": "3:31", "hook": "Яркая фраза"}},
      {{"start": "15:48", "end": "15:59", "hook": "Яркая фраза"}}
    ],
    "hashtags": ["Тег1", "Тег2", "Тег3"]
  }}
}}
"""

SHORTS_MIN_SEC = 35
SHORTS_MAX_SEC = 180
SHORTS_IDEAL_MAX = 120

# Скорость воспроизведения shorts (без изменения тона голоса).
# Допустимые значения: 1.0, 1.1, 1.3, 1.5
# 1.0 = без ускорения (по умолчанию)
# ВНИМАНИЕ: скорость Shorts управляется через /settings в боте и хранится в БД.
# Реальное значение читается через shorts_speed_get() — НЕ через env var SHORTS_SPEED.
# Переменная SHORTS_SPEED из env НЕ используется в runtime.


async def create_shorts_candidates(
    mp3_path: Path,
    ai_data: dict,
    title: str,
    performer: str,
    duration: int,
    status_msg=None,
    prefix: str = "",
    existing_audio_part=None,
    existing_client=None,
    speed: float = 1.0,
) -> list[dict]:
    """
    Ищет 3–5 кандидатов для Shorts через Gemini.
    Возвращает список словарей:
    [{"start","end","title","reason","kind","start_seconds","end_seconds","duration_seconds"}]
    speed — текущее ускорение; максимальная длина исходного клипа масштабируется.
    """
    if not GEMINI_CLIENTS or not HAS_GEMINI:
        return []

    try:
        format_name      = (ai_data or {}).get("format", "other") or "other"
        real_author      = (ai_data or {}).get("real_author", "") or performer or ""
        real_event       = (ai_data or {}).get("real_event", "") or ""
        analysis_summary = (ai_data or {}).get("analysis_summary", "") or ""
        argument_arc     = (ai_data or {}).get("argument_arc", "") or ""
        key_categories   = "; ".join((ai_data or {}).get("key_categories", []) or [])
        timestamps       = (ai_data or {}).get("timestamps", "") or ""
        questions        = "\n".join((ai_data or {}).get("questions", []) or [])

        # При ускорении максимальная длина исходника масштабируется:
        # итоговый шортс = исходник / speed → можно брать исходник * speed
        # Цель: итоговый шортс до 3 мин (180s) при любой скорости
        _use_speed   = abs(speed - 1.0) > 0.01
        _shorts_max  = round(180 * speed)   # исходник → итоговые 180s
        _ideal_max   = round(120 * speed)   # желательный максимум
        _shorts_min  = 35                   # минимум не масштабируем

        prompt = SHORTS_PROMPT.format(
            title=title,
            duration=format_timestamp(duration),
            format_name=format_name,
            real_author=real_author,
            real_event=real_event,
            analysis_summary=analysis_summary[:800],
            argument_arc=argument_arc[:600],
            key_categories=key_categories,
            timestamps=timestamps[:1000],
            questions_block=f"- questions:\n{questions}\n" if questions.strip() else "",
            speed_limits=(
                f"\n\nУСКОРЕНИЕ: видео будет ускорено в {speed}x раз. "
                f"Выбирай фрагменты до {_shorts_max} секунд — после ускорения это станет {round(_shorts_max/speed)}s. "
                f"Желательный диапазон исходника: 35–{_ideal_max} секунд."
            ) if _use_speed else "",
        )

        if status_msg:
            try:
                await status_msg.edit_text(f"{prefix}✂️ Ищу лучшие фрагменты для Shorts...")
            except Exception:
                pass

        loop = asyncio.get_running_loop()
        file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
        response = None

        if existing_audio_part is not None and existing_client is not None:
            try:
                response = await existing_client.aio.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=[existing_audio_part, prompt],
                    config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=16000),
                )
            except Exception as e:
                logger.warning(f"Shorts candidates existing_audio_part failed: {e}")
                response = None

        if response is None:
            audio_bytes = mp3_path.read_bytes() if file_size_mb <= 20 else None

            async def _upload(client):
                if file_size_mb > 20:
                    uf = await client.aio.files.upload(
                        file=mp3_path,
                        config=types.UploadFileConfig(
                            mime_type="audio/mpeg",
                            display_name=f"{performer} - {title}",
                        ),
                    )
                    while uf.state == "PROCESSING":
                        await asyncio.sleep(3)
                        uf = await client.aio.files.get(name=uf.name)
                    return uf
                return types.Part.from_bytes(data=audio_bytes, mime_type="audio/mpeg")

            async def _shorts_with_client(client):
                audio_part = await _upload(client)
                _uploaded = getattr(audio_part, "name", None) is not None
                try:
                    return await client.aio.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=[audio_part, prompt],
                        config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=16000),
                    )
                finally:
                    if _uploaded:
                        try:
                            await client.aio.files.delete(name=audio_part.name)
                        except Exception:
                            pass

            async def _call_shorts(client):
                return await _shorts_with_client(client)

            response = await gemini_generate(GEMINI_CLIENTS, _call_shorts)

        raw_text = ""
        try:
            raw_text = response.text or ""
        except Exception:
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if not getattr(part, "thought", False):
                        raw_text += part.text or ""

        if not raw_text.strip():
            logger.warning("Shorts candidates: Gemini вернул пустой ответ")
            return []

        clean = raw_text.strip()
        clean = re.sub(r"^```[a-z]*\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean).strip()
        start = clean.find("{")
        end   = clean.rfind("}")
        if start == -1 or end <= start:
            logger.warning(f"Shorts candidates: JSON не найден | текст: {raw_text[:500]}")
            return []

        try:
            data = json.loads(clean[start:end + 1])
        except json.JSONDecodeError as e:
            logger.warning(f"Shorts candidates JSONDecodeError: {e} | текст: {raw_text[:500]}")
            # Пробуем починить обрезанный JSON — добавляем закрывающие скобки
            _fixed = clean[start:end + 1].rstrip()
            # Найти последний валидный элемент (до последней полной фигурной скобки)
            _last_brace = _fixed.rfind("},")
            if _last_brace == -1:
                _last_brace = _fixed.rfind("}")
            if _last_brace > 0:
                # Обрезаем до последнего полного объекта и закрываем массив и объект
                _fixed = _fixed[:_last_brace + 1] + "\n  ]\n}"
                try:
                    data = json.loads(_fixed)
                    logger.info("Shorts candidates: JSON восстановлен (обрезанный ответ)")
                except json.JSONDecodeError:
                    return []
            else:
                return []

        candidates_raw = data.get("shorts_candidates", [])
        if not isinstance(candidates_raw, list):
            return []

        out: list[dict] = []
        seen_ranges: set[tuple[int, int]] = set()

        for item in candidates_raw[:7]:
            if not isinstance(item, dict):
                continue
            start_t    = str(item.get("start", "")).strip()
            end_t      = str(item.get("end",   "")).strip()
            clip_title = _scrub_inline(_strip_meta_lines(_clean_field(item.get("title",  ""))))
            hook       = _scrub_inline(_strip_meta_lines(_clean_field(item.get("hook",   ""))))
            reason     = _scrub_inline(_strip_meta_lines(_clean_field(item.get("reason", ""))))
            kind       = str(item.get("kind", "")).strip()
            # hashtags: список строк без #
            raw_tags   = item.get("hashtags", [])
            if isinstance(raw_tags, list):
                hashtags = [
                    str(t).strip().lstrip("#").strip()
                    for t in raw_tags
                    if str(t).strip()
                ][:4]
            else:
                hashtags = []

            if not start_t or not end_t or not clip_title:
                continue

            start_s = time_to_seconds(start_t)
            end_s   = time_to_seconds(end_t)
            if start_s is None or end_s is None:
                continue
            if start_s < 0 or end_s <= start_s:
                continue
            if end_s > duration + 5:
                continue

            clip_len = end_s - start_s
            if clip_len < _shorts_min or clip_len > _shorts_max:
                continue

            key = (start_s, end_s)
            if key in seen_ranges:
                continue
            seen_ranges.add(key)

            out.append({
                "start":            start_t,
                "end":              end_t,
                "title":            clip_title[:120],
                "hook":             hook[:120],
                "reason":           reason[:220],
                "kind":             kind[:40],
                "hashtags":         hashtags,
                "start_seconds":    start_s,
                "end_seconds":      end_s,
                "duration_seconds": clip_len,
            })

        logger.info(f"Shorts candidates: найдено {len(out)} из {len(candidates_raw)} предложенных")
        return out[:5]

    except Exception as e:
        logger.warning(f"create_shorts_candidates error: {type(e).__name__}: {e}")
        return []


def _pick_video_file(media_id: str) -> Path | None:
    """Возвращает первый найденный видеофайл с предпочтительным расширением."""
    preferred_exts = [".mp4", ".mkv", ".webm", ".mov"]
    files = [p for p in DOWNLOAD_DIR.glob(f"{media_id}_video.*") if p.is_file()]
    for ext in preferred_exts:
        for p in files:
            if p.suffix.lower() == ext:
                return p
    return None


async def download_video_for_shorts(url: str, media_id: str) -> Path | None:
    """
    Скачивает видео в mp4 для вырезки Shorts.
    Использует bestvideo[height<=720]+bestaudio — оптимально для Shorts.
    Возвращает Path к mp4 или None при ошибке.
    """
    existing = _pick_video_file(media_id)
    if existing:
        return existing
    try:
        cmd = YTDLP_BASE_ARGS + [
            "--format", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]/best",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--output", str(DOWNLOAD_DIR / f"{media_id}_video.%(ext)s"),
            url,
        ]
        loop = asyncio.get_running_loop()
        proc = await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=900),
        )
        if proc.returncode != 0:
            logger.warning(f"Shorts video download failed: {proc.stderr[-300:]}")
            return None
        return _pick_video_file(media_id)
    except Exception as e:
        logger.warning(f"Shorts download_video error: {e}")
        return None


def get_shorts_visual_mode(format_name: str, visual_mode: str = "auto") -> str:
    """
    Возвращает итоговый visual_mode для рендера short.

    Режимы:
    - crop_zoom          — medium crop: берём центральные ~56% ширины (crop по 9:16),
                           масштабируем до 720×1280. Спикер заметно ближе, жесты и
                           плечи остаются в кадре. Для sermon, lecture.
    - full_frame_vertical — исходный 16:9 кадр целиком на вертикальном 9:16 холсте.
                           Фон — размытая копия того же кадра (blur background).
                           Для Q&A, панелей, интервью — никого не теряем.
    - auto               — выбирает режим по format_name.
    """
    if visual_mode != "auto":
        return visual_mode
    if format_name in ("sermon", "lecture"):
        return "crop_zoom"
    # qa, discussion, interview, other — безопасный full frame
    return "full_frame_vertical"


# ─── GPU/CPU encoder detection ───────────────────────────────

_VIDEO_ENCODER: str | None = None  # кэш результата проверки

def _get_video_encoder() -> tuple[str, list[str], list[str]]:
    """
    Возвращает (encoder, quality_args, preset_args) для ffmpeg.

    Пробует h264_nvenc (NVIDIA GPU) — если доступен, использует его.
    Fallback: libx264 (CPU).

    nvenc параметры:
    - preset p4   — баланс скорость/качество (p1=быстро, p7=медленно)
    - -cq 23      — constant quality (аналог -crf для nvenc)
    - -rc vbr     — variable bitrate mode (нужен для -cq)

    libx264 параметры:
    - preset veryfast
    - -crf 23
    """
    global _VIDEO_ENCODER
    if _VIDEO_ENCODER is not None:
        if _VIDEO_ENCODER == "h264_nvenc":
            return "h264_nvenc", ["-rc", "vbr", "-cq", "23"], ["-preset", "p4"]
        return "libx264", ["-crf", "23"], ["-preset", "veryfast"]

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        try:
            result = subprocess.run(
                [ffmpeg, "-f", "lavfi", "-i", "nullsrc=s=720x1280:d=0.1",
                 "-c:v", "h264_nvenc", "-f", "null", "-"],
                capture_output=True, timeout=10,
            )
            if result.returncode == 0:
                _VIDEO_ENCODER = "h264_nvenc"
                logger.info("Video encoder: h264_nvenc (NVIDIA GPU) ✅")
                return "h264_nvenc", ["-rc", "vbr", "-cq", "23"], ["-preset", "p4"]
        except Exception:
            pass

    _VIDEO_ENCODER = "libx264"
    logger.info("Video encoder: libx264 (CPU fallback)")
    return "libx264", ["-crf", "23"], ["-preset", "veryfast"]


async def _find_silence_end(
    video_path: Path,
    target_end: float,
    search_window: float = 5.0,
    noise_db: float = -30.0,
    min_duration: float = 0.3,
) -> float:
    """
    Ищет ближайшую паузу к target_end в окне [target_end-2s .. target_end+3s].
    Возвращает скорректированное время конца или исходный target_end если пауз нет.

    Параметры:
      noise_db      — порог тишины в дБ (-30 достаточно для пауз между фразами)
      min_duration  — минимальная длительность паузы (0.3s = 300ms)
      search_window — ширина окна поиска (5s → ищем в ±2.5s от target_end)
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not video_path.exists():
        return target_end

    scan_start = max(0.0, target_end - search_window * 0.4)  # смещение: -2s от target
    scan_duration = search_window

    cmd = [
        ffmpeg,
        "-ss", f"{scan_start:.3f}",
        "-i", str(video_path),
        "-t", f"{scan_duration:.3f}",
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null", "-",
    ]
    try:
        proc = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=30),
        )
        silences = []
        for line in proc.stderr.splitlines():
            if "silence_end:" in line:
                m = re.search(r"silence_end:\s*([\d.]+)", line)
                if m:
                    # время относительно scan_start → абсолютное время в видео
                    abs_time = scan_start + float(m.group(1))
                    silences.append(abs_time)
        if not silences:
            return target_end
        # Берём паузу ближайшую к исходному target_end
        best = min(silences, key=lambda t: abs(t - target_end))
        return best
    except Exception as e:
        logger.warning(f"_find_silence_end error: {e}")
        return target_end


async def _detect_black_bars(video_path: Path, sample_start: float = 0.0) -> str:
    """
    Запускает ffmpeg cropdetect на трёх точках фрагмента (начало, середина, +10с).
    Возвращает строку 'crop=W:H:X:Y' если найдены полосы, иначе ''.
    limit=32 — ловит серые/тёмные полосы (не только чисто чёрные).
    """
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg or not video_path.exists():
            return ""
        loop = asyncio.get_running_loop()

        # Три точки: начало фрагмента + 2с, середина, + 10с — берём наиболее стабильный результат
        sample_points = [
            sample_start + 2.0,
            sample_start + 10.0,
            sample_start + 20.0,
        ]

        crop_votes: dict[str, int] = {}
        for sp in sample_points:
            cmd = [
                ffmpeg,
                "-ss", str(sp),
                "-i", str(video_path),
                "-t", "8",
                "-vf", "cropdetect=limit=32:round=2:reset=0",
                "-an", "-f", "null", "-",
            ]
            proc = await loop.run_in_executor(
                None,
                lambda c=cmd: subprocess.run(c, capture_output=True, text=True, timeout=30),
            )
            output = proc.stderr or ""
            crop_str = ""
            for line in output.splitlines():
                m = re.search(r"crop=(\d+:\d+:\d+:\d+)", line)
                if m:
                    crop_str = m.group(1)
            if crop_str:
                crop_votes[crop_str] = crop_votes.get(crop_str, 0) + 1

        if not crop_votes:
            return ""

        # Берём самый частый результат
        crop_str = max(crop_votes, key=lambda k: crop_votes[k])

        # Проверяем что реально что-то срезается (порог 4px)
        probe_cmd = [ffmpeg, "-i", str(video_path), "-f", "null", "-"]
        probe = await loop.run_in_executor(
            None,
            lambda: subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10),
        )
        dim_m = re.search(r"(\d{3,4})x(\d{3,4})", probe.stderr or "")
        if dim_m:
            orig_w, orig_h = int(dim_m.group(1)), int(dim_m.group(2))
            parts = crop_str.split(":")
            crop_w, crop_h = int(parts[0]), int(parts[1])
            if crop_w >= orig_w - 4 and crop_h >= orig_h - 4:
                return ""
            logger.info(
                f"Black bars detected: {orig_w}x{orig_h} → crop={crop_str} "
                f"(срезано: {orig_w - crop_w}px по X, {orig_h - crop_h}px по Y)"
                f" | votes={crop_votes}"
            )
        return f"crop={crop_str}"
    except Exception as e:
        logger.warning(f"_detect_black_bars error: {type(e).__name__}: {e}")
        return ""


async def render_short_clip(
    source_video_path: Path,
    output_path: Path,
    start_seconds: int,
    end_seconds: int,
    *,
    visual_mode: str = "full_frame_vertical",
) -> bool:
    """
    Вырезает short-клип из исходного видео через ffmpeg.
    Итоговый формат: 9:16 (720×1280).

    visual_mode:
    - crop_zoom           — medium crop: центральные ~56% ширины исходника под 9:16.
                            Спикер заметно ближе, голова/плечи/жесты в кадре.
                            Для sermon, lecture.
    - full_frame_vertical — исходный кадр целиком, фон — блюр той же картинки.
                            Для Q&A, панелей, интервью — никого не теряем.

    Возвращает True при успехе, False при ошибке.
    """
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("render_short_clip: ffmpeg не найден")
            return False

        if not source_video_path.exists():
            logger.warning(f"render_short_clip: исходный файл не найден: {source_video_path}")
            return False

        if end_seconds <= start_seconds:
            logger.warning(f"render_short_clip: невалидный диапазон {start_seconds}..{end_seconds}")
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Корректируем точку конца до ближайшей паузы (±2–3 сек от Gemini-оценки)
        adjusted_end = await _find_silence_end(source_video_path, float(end_seconds))
        if abs(adjusted_end - end_seconds) > 0.1:
            logger.info(f"Short end adjusted: {end_seconds}s → {adjusted_end:.1f}s (silence snap)")
            end_seconds = int(round(adjusted_end))
        clip_duration = end_seconds - start_seconds
        if clip_duration <= 0:
            logger.warning("render_short_clip: clip_duration ≤ 0 после коррекции паузы")
            return False

        # Детектируем чёрные полосы (letterbox/pillarbox) и срезаем их
        black_bars = await _detect_black_bars(source_video_path, float(start_seconds))

        _use_filter_complex = False   # True для blur-overlay графа с лейблами
        if visual_mode == "crop_zoom":
            # Medium crop для одного спикера (sermon, lecture):
            # берём центральные ~56% ширины (соотношение 9:16) и масштабируем до 720×1280.
            bc = f"{black_bars}," if black_bars else ""
            vf = (
                f"{bc}crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
                "scale=720:1280"
            )
            mode_label = "crop_zoom(medium)"
        else:
            if black_bars:
                vf = (
                    f"[0:v]{black_bars}[clean];"
                    "[clean]split=2[bg][fg];"
                    "[bg]scale=720:1280,gblur=sigma=20[blurred];"
                    "[fg]scale=720:-2[small];"
                    "[blurred][small]overlay=(W-w)/2:(H-h)/2[out]"
                )
                _use_filter_complex = True
            else:
                vf = (
                    "[0:v]split=2[bg][fg];"
                    "[bg]scale=720:1280,gblur=sigma=20[blurred];"
                    "[fg]scale=720:-2[small];"
                    "[blurred][small]overlay=(W-w)/2:(H-h)/2[out]"
                )
                _use_filter_complex = True
            mode_label = "full_frame_blur"
        _enc, _quality, _preset = _get_video_encoder()
        _hwaccel = []  # hwaccel cuda убран: CPU-фильтры несовместимы с CUDA decode
        _vf_args = (
            ["-filter_complex", vf, "-map", "[out]", "-map", "0:a?"]
            if _use_filter_complex else
            ["-vf", vf]
        )
        cmd = [
            ffmpeg,
            *_hwaccel,
            "-ss", str(start_seconds),
            "-i", str(source_video_path),
            "-t", str(clip_duration),
            *_vf_args,
            "-c:v", _enc,
            *_preset,
            *_quality,
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-y",
            str(output_path),
        ]

        proc = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=600),
        )

        if proc.returncode != 0:
            stderr = (proc.stderr or "")[-1000:]
            logger.warning(f"render_short_clip ffmpeg error ({mode_label}): {stderr}")
            return False

        if not output_path.exists() or output_path.stat().st_size == 0:
            logger.warning("render_short_clip: выходной файл не создан или пуст")
            return False

        # Меньше 10KB для видео — явно битый файл (ffmpeg завершился без ошибки, но ничего не записал)
        if output_path.stat().st_size < 10_240:
            stderr = (proc.stderr or "")[-1000:]
            logger.warning(
                f"render_short_clip: файл подозрительно мал ({output_path.stat().st_size} байт). "
                f"ffmpeg stderr: {stderr}"
            )
            output_path.unlink(missing_ok=True)
            return False

        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info(
            f"Short rendered [{mode_label}] 9:16: {output_path.name} "
            f"({start_seconds}s..{end_seconds}s, {clip_duration}s, {size_mb:.1f}MB)"
        )
        return True

    except subprocess.TimeoutExpired:
        logger.warning("render_short_clip: ffmpeg timeout")
        return False
    except Exception as e:
        logger.warning(f"render_short_clip error: {type(e).__name__}: {e}")
        return False


async def postprocess_short(
    input_path: Path,
    output_path: Path,
    *,
    normalize_audio: bool = True,
    speed: float = 1.0,
) -> bool:
    """
    Постобработка short-клипа: нормализация громкости и/или ускорение.
    Применяется только к shorts, MP3-пайплайн не затрагивается.

    - normalize_audio: loudnorm через ffmpeg (EBU R128, target -16 LUFS).
    - speed: ускорение без изменения тона (atempo + setpts).
      Допустимые значения: 1.0, 1.1, 1.3, 1.5.
      При 1.0 этот фильтр не применяется.

    Возвращает True при успехе, False при ошибке (input_path при этом остаётся нетронутым).
    """
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("postprocess_short: ffmpeg не найден")
            return False

        if not input_path.exists():
            logger.warning(f"postprocess_short: входной файл не найден: {input_path}")
            return False

        # Нормализован ли speed
        speed = float(speed)
        use_speed = abs(speed - 1.0) > 0.01

        # Строим audio и video filter
        # ── audio ──────────────────────────────────────────────
        audio_filters = []
        if normalize_audio:
            # loudnorm: target integrated loudness -16 LUFS, true peak -1.5 dBTP
            audio_filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        if use_speed:
            # atempo поддерживает диапазон 0.5–2.0
            audio_filters.append(f"atempo={speed}")

        # ── video ──────────────────────────────────────────────
        video_filters = []
        if use_speed:
            # setpts уменьшает PTS пропорционально скорости
            video_filters.append(f"setpts={1.0/speed}*PTS")

        vf_arg = ",".join(video_filters) if video_filters else None
        af_arg = ",".join(audio_filters) if audio_filters else None

        if not vf_arg and not af_arg:
            # Нечего делать — просто скопируем поток

            shutil.copy2(input_path, output_path)
            return True

        _enc, _quality, _preset = _get_video_encoder()
        _hwaccel = []  # hwaccel cuda убран: CPU-фильтры несовместимы с CUDA decode
        cmd = [ffmpeg, *_hwaccel, "-i", str(input_path)]
        if vf_arg:
            cmd += ["-vf", vf_arg]
        if af_arg:
            cmd += ["-af", af_arg]
        cmd += [
            "-c:v", _enc,
            *_preset,
            *_quality,
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-y",
            str(output_path),
        ]

        proc = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=600),
        )
        if proc.returncode != 0:
            logger.warning(f"postprocess_short ffmpeg error: {(proc.stderr or '')[-800:]}")
            return False
        if not output_path.exists() or output_path.stat().st_size == 0:
            logger.warning("postprocess_short: выходной файл не создан или пуст")
            return False

        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info(
            f"postprocess_short: normalize={normalize_audio} speed={speed} → "
            f"{output_path.name} ({size_mb:.1f}MB)"
        )
        return True

    except subprocess.TimeoutExpired:
        logger.warning("postprocess_short: ffmpeg timeout")
        return False
    except Exception as e:
        logger.warning(f"postprocess_short error: {type(e).__name__}: {e}")
        return False


# ── Субтитры для Shorts ───────────────────────────────────────

try:
    from faster_whisper import WhisperModel
    HAS_FASTER_WHISPER = True
except ImportError:
    HAS_FASTER_WHISPER = False


def _wrap_subtitle_text(text: str, max_chars: int = 38) -> str:
    """Разбивает текст субтитра на 1–2 строки, не разрывая слова."""
    words = text.split()
    if not words:
        return text
    lines: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        add_len = len(word) + (1 if current else 0)
        if current_len + add_len <= max_chars:
            current.append(word)
            current_len += add_len
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
            current_len = len(word)
    if current:
        lines.append(" ".join(current))
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
    return "\n".join(lines)


def _seconds_to_ass_time(seconds: float) -> str:
    """Секунды → ASS-время H:MM:SS.cc"""
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    cs = int((seconds - int(seconds)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _pick_subtitle_font() -> str:
    """Выбирает лучший доступный шрифт для субтитров (кириллица)."""

    candidates = [
        # Inter — современный, чистый
        ("Inter SemiBold", [
            "/usr/share/fonts/truetype/inter/Inter-SemiBold.ttf",
            "/usr/local/share/fonts/Inter-SemiBold.ttf",
            "C:/Windows/Fonts/Inter-SemiBold.ttf",
        ]),
        # Noto Sans — отличная поддержка кириллицы
        ("Noto Sans", [
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/noto/NotoSans-Bold.ttf",
            "C:/Windows/Fonts/NotoSans-Bold.ttf",
        ]),
        # Montserrat — стильный, читаемый
        ("Montserrat SemiBold", [
            "/usr/share/fonts/truetype/montserrat/Montserrat-SemiBold.ttf",
            "C:/Windows/Fonts/Montserrat-SemiBold.ttf",
        ]),
        # Roboto — широко доступен
        ("Roboto", [
            "/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf",
            "C:/Windows/Fonts/Roboto-Bold.ttf",
        ]),
        # Arial — универсальный fallback
        ("Arial", [
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ]),
    ]
    for font_name, paths in candidates:
        for p in paths:
            if os.path.exists(p):
                return font_name
    return "Arial"  # системный fallback


def _normalize_word_timings(words: list[dict]) -> list[dict]:
    """
    Нормализует тайминги слов от Whisper:
    - минимальная длительность слова 80ms
    - убирает overlaps между соседними словами
    - сглаживает слишком короткие интервалы
    """
    if not words:
        return words
    result = []
    MIN_DUR = 0.08  # 80ms минимум на слово
    for i, w in enumerate(words):
        start = float(w.get("start", 0))
        end   = float(w.get("end", start + MIN_DUR))
        # Минимальная длительность
        if end - start < MIN_DUR:
            end = start + MIN_DUR
        # Убираем overlap с предыдущим словом
        if result and start < result[-1]["end"]:
            start = result[-1]["end"]
            if end <= start:
                end = start + MIN_DUR
        result.append({**w, "start": start, "end": end})
    return result


def _merge_hyphenated_particles(words: list[dict]) -> list[dict]:
    """
    Склеивает русские частицы через дефис обратно в одно слово.

    Whisper разбивает «что-то» на два токена: «что» + «-то».
    В субтитрах это выглядит как «что -то» с лишним пробелом.

    Частицы: -то, -либо, -нибудь, -ка, -таки, -де, -с, -ж, -же
    Также: кое- (префикс)
    """
    if len(words) < 2:
        return words

    PARTICLES = {"-то", "-либо", "-нибудь", "-ка", "-таки", "-де", "-с", "-ж", "-же"}

    result = []
    i = 0
    while i < len(words):
        w = words[i]
        word_text = w.get("word", "").strip()

        # Текущее слово — частица с дефисом → склеиваем с предыдущим
        if word_text.lower() in PARTICLES and result:
            prev = result[-1]
            merged_word = prev.get("word", "").strip() + word_text
            result[-1] = {
                **prev,
                "word": merged_word,
                "end": w.get("end", prev.get("end", 0)),
            }
        # Текущее слово заканчивается дефисом (кое-) → склеиваем со следующим
        elif word_text.endswith("-") and i + 1 < len(words):
            next_w = words[i + 1]
            merged_word = word_text + next_w.get("word", "").strip()
            result.append({
                **w,
                "word": merged_word,
                "end": next_w.get("end", w.get("end", 0)),
            })
            i += 2
            continue
        else:
            result.append({**w})

        i += 1

    return result


def _chunk_words_smart(words: list[dict], max_chars: int = 36, max_pause: float = 0.4) -> list[list[dict]]:
    """
    Умная группировка слов в subtitle chunks.

    Логика:
    - режет по паузам между словами (> max_pause секунд)
    - режет по пунктуации (., !, ?, ,)
    - соблюдает лимит символов
    - старается не оставлять короткие служебные слова в одиночестве
    - максимум 2 строки на chunk
    """
    if not words:
        return []

    # Служебные слова — не разрывать после них
    PREPOSITIONS = {"в", "на", "за", "из", "по", "к", "с", "о", "у", "до", "об",
                    "от", "под", "над", "при", "про", "без", "для", "и", "а", "но",
                    "или", "не", "ни", "же", "что", "как", "это", "то"}

    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_len = 0

    for i, w in enumerate(words):
        word_text = w.get("word", "").strip()
        word_len  = len(word_text) + (1 if current else 0)

        # Определяем паузу после предыдущего слова
        pause = 0.0
        if current and i > 0:
            pause = w["start"] - words[i - 1]["end"]

        # Пунктуация в конце предыдущего слова
        prev_text = current[-1].get("word", "").strip() if current else ""
        ends_sentence = prev_text.endswith((".", "!", "?"))
        ends_clause   = prev_text.endswith(",")

        # Решаем — резать или нет
        should_cut = False
        if current:
            if current_len + word_len > max_chars:
                should_cut = True
            elif pause > max_pause:
                should_cut = True
            elif ends_sentence:
                should_cut = True
            elif ends_clause and current_len > max_chars * 0.5:
                should_cut = True

        # Не режем если предыдущее слово — предлог/союз
        if should_cut and prev_text.lower() in PREPOSITIONS:
            should_cut = False

        if should_cut and current:
            chunks.append(current)
            current = []
            current_len = 0

        current.append(w)
        current_len += word_len

    if current:
        chunks.append(current)

    return chunks


def _wrap_chunk_to_lines(words: list[dict], max_chars: int = 36) -> str:
    """
    Разбивает chunk слов на 1-2 строки, балансируя длину.
    Возвращает текст с \\N между строками если нужно 2 строки.
    """
    text = " ".join(w.get("word", "").strip() for w in words)
    if len(text) <= max_chars:
        return text

    # Пытаемся найти красивую точку разрыва ближе к середине
    words_list = text.split()
    mid = len(text) // 2
    best_pos = -1
    best_dist = len(text)

    pos = 0
    for i, word in enumerate(words_list[:-1]):
        pos += len(word) + 1
        dist = abs(pos - mid)
        if dist < best_dist:
            best_dist = dist
            best_pos = i + 1

    if best_pos > 0:
        line1 = " ".join(words_list[:best_pos])
        line2 = " ".join(words_list[best_pos:])
        return f"{line1}\\N{line2}"

    return text


def _fill_timing_gaps(words: list[dict], max_gap: float = 0.8) -> list[dict]:
    """
    Заполняет дыры в тайминге слов.

    Когда Whisper пропускает английское слово (language="ru"), в тайминге
    образуется дыра — караоке молчит и потом резко прыгает.
    Решение: если между словами дыра > max_gap секунд,
    растягиваем end предыдущего слова до start следующего.
    """
    if len(words) < 2:
        return words
    result = []
    for i, w in enumerate(words):
        if i == 0:
            result.append({**w})
            continue
        gap = w["start"] - result[-1]["end"]
        if gap > max_gap:
            result[-1] = {**result[-1], "end": w["start"]}
        result.append({**w})
    return result


def _generate_ass_from_segments(segments: list[dict], karaoke: bool = True) -> str:
    """
    Генерирует .ass-файл из сегментов (faster-whisper).

    karaoke=True:
    - word-level подсветка: активное слово тёплый жёлтый, остальные белые полупрозрачные
    - умная группировка по паузам и пунктуации
    - нормализация тайминга слов

    karaoke=False:
    - plain subtitles с умным chunking
    - балансировка строк

    Стиль: современный clean Shorts-style, хороший шрифт для кириллицы.
    """
    # Цвета: тёплый жёлтый для активного слова, белый для остальных
    COLOUR_ACTIVE   = "&H0000D7FF"   # тёплый жёлтый (BGR: FF D7 00)
    COLOUR_INACTIVE = "&H00FFFFFF"   # белый

    font_name = _pick_subtitle_font()

    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 720\nPlayResY: 1280\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        # Bold=1, Outline=2.8, Shadow=1.2, MarginV=130 (выше нижнего края)
        f"Style: Default,{font_name},54,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "1,0,0,0,100,100,0.5,0,1,2.8,1.2,2,40,40,130,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    # ── Plain subtitle режим (karaoke=False) ─────────────────────
    if not karaoke:
        # Собираем все слова из сегментов
        all_words: list[dict] = []
        for seg in segments:
            words_raw = seg.get("words") or []
            if words_raw:
                for w in words_raw:
                    word = str(w.get("word", "")).strip()
                    if word:
                        all_words.append({
                            "word":  word,
                            "start": float(w.get("start", seg.get("start", 0))),
                            "end":   float(w.get("end",   seg.get("end",   0))),
                        })
            else:
                # Fallback по сегментам без word timestamps
                text = seg.get("text", "").strip()
                if not text:
                    continue
                seg_start = float(seg.get("start", 0))
                seg_end   = float(seg.get("end", seg_start + 1))
                words_split = text.split()
                dur_per = (seg_end - seg_start) / max(len(words_split), 1)
                for i, word in enumerate(words_split):
                    all_words.append({
                        "word":  word,
                        "start": seg_start + i * dur_per,
                        "end":   seg_start + (i + 1) * dur_per,
                    })

        if not all_words:
            return header

        # Нормализуем и заполняем дыры
        all_words = _normalize_word_timings(all_words)
        all_words = _fill_timing_gaps(all_words)
        all_words = _merge_hyphenated_particles(all_words)

        chunks = _chunk_words_smart(all_words, max_chars=36)
        ass_lines = [header]
        for chunk in chunks:
            if not chunk:
                continue
            t_s  = _seconds_to_ass_time(chunk[0]["start"])
            t_e  = _seconds_to_ass_time(chunk[-1]["end"])
            text = _wrap_chunk_to_lines(chunk, max_chars=36)
            text = text.replace("{", "\\{").replace("}", "\\}")
            ass_lines.append(f"Dialogue: 0,{t_s},{t_e},Default,,0,0,0,,{text}")
        return "\n".join(ass_lines)

    # ── Karaoke режим (karaoke=True) ─────────────────────────────
    all_words = []
    for seg in segments:
        words_raw = seg.get("words") or []
        if words_raw:
            for w in words_raw:
                word = str(w.get("word", "")).strip()
                if word:
                    all_words.append({
                        "word":  word,
                        "start": float(w.get("start", seg.get("start", 0))),
                        "end":   float(w.get("end",   seg.get("end",   0))),
                    })
        else:
            seg_start = float(seg.get("start", 0))
            seg_end   = float(seg.get("end",   seg_start + 1))
            words_split = str(seg.get("text", "")).strip().split()
            if not words_split:
                continue
            dur_per = (seg_end - seg_start) / len(words_split)
            for i, word in enumerate(words_split):
                all_words.append({
                    "word":  word,
                    "start": seg_start + i * dur_per,
                    "end":   seg_start + (i + 1) * dur_per,
                })

    if not all_words:
        return header

    # Нормализуем тайминги и заполняем дыры от пропущенных слов
    all_words = _normalize_word_timings(all_words)
    all_words = _fill_timing_gaps(all_words)
    all_words = _merge_hyphenated_particles(all_words)

    # Умная группировка по паузам и смыслу
    chunks = _chunk_words_smart(all_words, max_chars=36, max_pause=0.35)

    ass_lines = [header]
    for chunk in chunks:
        if not chunk:
            continue
        chunk_start = chunk[0]["start"]
        chunk_end   = chunk[-1]["end"]
        if chunk_end <= chunk_start:
            chunk_end = chunk_start + 0.5

        t_s = _seconds_to_ass_time(chunk_start)
        t_e = _seconds_to_ass_time(chunk_end)

        # Строим karaoke-текст: все слова белые, активное — жёлтое через \k
        text_parts: list[str] = []
        for w in chunk:
            word_dur_cs = max(4, int(round((w["end"] - w["start"]) * 100)))
            word_text   = w["word"].replace("{", "\\{").replace("}", "\\}")
            # \k подсвечивает слово на word_dur_cs сотых секунды
            # Сначала неактивный цвет, потом \k переключает на активный
            text_parts.append(
                f"{{\\c{COLOUR_INACTIVE}}}{{\\k{word_dur_cs}}}{{\\c{COLOUR_ACTIVE}}}{word_text}"
            )

        dialogue_text = " ".join(text_parts)
        ass_lines.append(
            f"Dialogue: 0,{t_s},{t_e},Default,,0,0,0,,{dialogue_text}"
        )

    return "\n".join(ass_lines)


# ─── Whisper singleton ────────────────────────────────────────
# Модель создаётся один раз при первом вызове и переиспользуется.
# Lock защищает от гонки при параллельном preload и первом запросе субтитров.
_whisper_model = None
_whisper_model_name: str | None = None
_whisper_lock = threading.Lock()


def get_subtitles_mode_settings() -> dict:
    """Возвращает настройки subtitle pipeline на основе /settings.

    heavy default (karaoke=True, light=False):
      model=large-v3, word_timestamps=True, karaoke=True

    light mode (light=True):
      model=medium, word_timestamps только если karaoke=True
    """
    karaoke = bool(settings_get("shorts_subtitles_karaoke"))
    light   = bool(settings_get("shorts_subtitles_light"))
    model_name = "medium" if light else os.getenv("WHISPER_MODEL", "large-v3")
    return {
        "model_name":      model_name,
        "karaoke":         karaoke,
        "word_timestamps": karaoke,   # word-level нужен только для karaoke
        "light":           light,
    }


def _get_whisper_model(model_size: str | None = None):
    """Возвращает singleton WhisperModel, создаёт при первом вызове или при смене модели."""
    global _whisper_model, _whisper_model_name
    if model_size is None:
        model_size = os.getenv("WHISPER_MODEL", "large-v3")
    with _whisper_lock:
        if _whisper_model is None or _whisper_model_name != model_size:
            from faster_whisper import WhisperModel as _WM
            _whisper_device = os.getenv("WHISPER_DEVICE", "cuda").strip().lower()
            _whisper_compute = "float16" if _whisper_device == "cuda" else "int8"
            _whisper_model = _WM(model_size, device=_whisper_device, compute_type=_whisper_compute)
            _whisper_model_name = model_size
            logger.info(f"Whisper model loaded: {model_size}")
        return _whisper_model


def preload_whisper_if_needed():
    """Устаревшая функция — не вызывается. Используйте _preload_whisper_bg внутри run_bot_async.
    Оставлена для обратной совместимости на случай внешних вызовов.
    """
    import warnings
    warnings.warn(
        "preload_whisper_if_needed() устарела и не используется ботом. "
        "Whisper загружается через _preload_whisper_bg в run_bot_async().",
        DeprecationWarning, stacklevel=2
    )
    if not HAS_FASTER_WHISPER:
        return
    try:
        sub_cfg = get_subtitles_mode_settings()
        _get_whisper_model(sub_cfg["model_name"])
        logger.info(f"Whisper preloaded on startup: {sub_cfg['model_name']}")
    except Exception as e:
        logger.warning(f"Whisper preload failed: {e}")


async def transcribe_short_clip(video_path: Path, ai_data: dict = None) -> list[dict]:
    """
    Транскрибирует аудио клипа через faster-whisper.
    Возвращает сегменты [{"start", "end", "text"}] или [] если нет модели/ошибка.
    Время в сегментах — относительно начала видео (0 = старт клипа).
    """
    # ── Backend check ──────────────────────────────────────────
    if not HAS_FASTER_WHISPER:
        logger.warning(
            "Subtitles: faster-whisper не установлен — субтитры недоступны. "
            "Установите: pip install faster-whisper"
        )
        return []

    # ── File check ─────────────────────────────────────────────
    if not video_path.exists():
        logger.warning(f"Subtitles: файл для транскрипции не найден: {video_path}")
        return []
    video_size_mb = video_path.stat().st_size / (1024 * 1024)
    logger.info(
        f"Subtitles: транскрибирую {video_path.name} "
        f"({video_size_mb:.1f} MB) через faster-whisper"
    )

    wav_path: Path | None = None
    try:
        import tempfile
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("Subtitles: ffmpeg не найден — невозможно извлечь аудио")
            return []

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)

        # Извлекаем аудио в WAV 16kHz mono
        cmd = [
            ffmpeg, "-i", str(video_path),
            "-ar", "16000", "-ac", "1", "-f", "wav", "-y", str(wav_path),
        ]
        loop = asyncio.get_running_loop()
        proc = await loop.run_in_executor(
            None, lambda: subprocess.run(cmd, capture_output=True, timeout=120)
        )

        if proc.returncode != 0:
            logger.warning(
                f"Subtitles: ffmpeg извлечение аудио упало (code={proc.returncode}): "
                f"{(proc.stderr or b'')[-300:]}"
            )
            wav_path.unlink(missing_ok=True)
            return []

        # Проверяем что WAV не пустой
        wav_size = wav_path.stat().st_size if wav_path.exists() else 0
        if wav_size < 1024:
            logger.warning(
                f"Subtitles: WAV файл слишком мал или пустой ({wav_size} байт) — "
                "возможно видео без аудиодорожки"
            )
            wav_path.unlink(missing_ok=True)
            return []

        logger.info(f"Subtitles: WAV извлечён ({wav_size / 1024:.0f} KB), запускаю Whisper...")

        subtitle_cfg = get_subtitles_mode_settings()
        model_size   = subtitle_cfg["model_name"]
        word_ts      = subtitle_cfg["word_timestamps"]
        logger.info(
            f"Subtitles mode: model={model_size} "
            f"karaoke={subtitle_cfg['karaoke']} "
            f"word_ts={word_ts} "
            f"light={subtitle_cfg['light']}"
        )
        _wav_path_for_thread = wav_path  # явная копия для closure

        # Строим initial_prompt из Gemini-подсказок для точной транскрипции
        _whisper_initial_prompt = "Проповедь на русском языке."
        if ai_data:
            hints = (ai_data.get("whisper_hints") or [])
            _extra_names = []
            _real_author = (ai_data.get("real_author") or "").strip()
            if _real_author:
                _extra_names.append(_real_author)
            _real_event = (ai_data.get("real_event") or "").strip()
            if _real_event:
                _extra_names.append(_real_event)
            all_hints = _extra_names + hints
            if all_hints:
                hints_str = ", ".join(all_hints[:70])
                _whisper_initial_prompt = (
                    f"Проповедь на русском языке. "
                    f"Ключевые слова и имена: {hints_str}."
                )
                logger.info(
                    f"Subtitles: initial_prompt построен, "
                    f"{len(all_hints)} подсказок: {hints_str[:120]}..."
                )
        _initial_prompt_for_thread = _whisper_initial_prompt

        def _run_whisper():
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.synchronize()
            except Exception:
                pass
            model = _get_whisper_model(model_size)
            segs, info = model.transcribe(
                str(_wav_path_for_thread),
                language="ru",
                initial_prompt=_initial_prompt_for_thread,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                word_timestamps=word_ts,
            )
            # segs — генератор, материализуем его здесь же, в том же потоке
            result = [
                {
                    "start": s.start,
                    "end":   s.end,
                    "text":  s.text.strip(),
                    "words": [
                        {"word": w.word, "start": w.start, "end": w.end}
                        for w in (s.words or [])
                    ],
                }
                for s in segs
            ]
            return result, getattr(info, "duration", 0), getattr(info, "language", "?"), getattr(info, "language_probability", 1.0)

        segments, audio_duration, detected_lang, lang_prob = await loop.run_in_executor(None, _run_whisper)
        wav_path.unlink(missing_ok=True)
        wav_path = None

        # Заполняем дыры в тайминге — убирает скачки на местах пропущенных слов
        for seg in segments:
            if seg.get("words"):
                seg["words"] = _fill_timing_gaps(seg["words"])
                seg["words"] = _merge_hyphenated_particles(seg["words"])

        logger.info(
            f"Subtitles: Whisper detected lang={detected_lang} "
            f"confidence={lang_prob:.2f}, model={model_size}"
        )

        # Если уверенность низкая — скорее всего синхронный перевод или смешанный язык
        # Субтитры в таком случае будут мусором
        if lang_prob < 0.4:
            logger.info(
                f"Subtitles: низкая уверенность в языке ({lang_prob:.2f}) — "
                "вероятно синхронный перевод, субтитры пропущены"
            )
            return []

        non_empty = [s for s in segments if s.get("text")]
        logger.info(
            f"Subtitles: Whisper вернул {len(segments)} сегментов "
            f"({len(non_empty)} непустых) из {video_path.name} "
            f"(audio_duration={audio_duration:.1f}s, model={model_size})"
        )
        if not non_empty:
            logger.info(
                "Subtitles: все сегменты пустые — возможно тишина, музыка или "
                "неподходящий язык (выставлен 'ru')"
            )
        return non_empty

    except Exception as e:
        logger.warning(f"Subtitles transcribe error ({type(e).__name__}): {e}")
        if wav_path is not None:
            try:
                wav_path.unlink(missing_ok=True)
            except Exception:
                pass
        return []


async def burn_subtitles_into_short(
    input_path: Path,
    output_path: Path,
    segments: list[dict],
) -> bool:
    """
    Вшивает субтитры в клип через ffmpeg ASS burn-in.
    Стиль: белый текст, полупрозрачная тёмная подложка, нижняя safe-zone.
    При ошибке исходный файл остаётся нетронутым.
    """
    try:
        import tempfile
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg or not input_path.exists() or not segments:
            return False
        subtitle_cfg = get_subtitles_mode_settings()
        ass_content  = _generate_ass_from_segments(segments, karaoke=subtitle_cfg["karaoke"])
        with tempfile.NamedTemporaryFile(
            suffix=".ass", mode="w", encoding="utf-8", delete=False
        ) as tmp:
            tmp.write(ass_content)
            ass_path = Path(tmp.name)
        ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
        _enc, _quality, _preset = _get_video_encoder()
        _hwaccel = []  # hwaccel cuda убран: CPU-фильтры несовместимы с CUDA decode
        cmd = [
            ffmpeg, *_hwaccel, "-i", str(input_path),
            "-vf", f"subtitles='{ass_escaped}'",
            "-c:v", _enc, *_preset, *_quality,
            "-c:a", "copy", "-movflags", "+faststart", "-y", str(output_path),
        ]
        loop = asyncio.get_running_loop()
        proc = await loop.run_in_executor(
            None, lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        )
        ass_path.unlink(missing_ok=True)
        if proc.returncode != 0:
            logger.warning(f"burn_subtitles ffmpeg error: {(proc.stderr or '')[-500:]}")
            return False
        if not output_path.exists() or output_path.stat().st_size == 0:
            return False
        logger.info(
            f"Subtitles burned: {output_path.name} "
            f"({output_path.stat().st_size / 1024 / 1024:.1f}MB)"
        )
        return True
    except Exception as e:
        logger.warning(f"burn_subtitles_into_short error: {type(e).__name__}: {e}")
        return False


# ── Постер с заголовком для Shorts ───────────────────────────

def _wrap_poster_title(title: str, max_chars: int = 22) -> list[str]:
    """Разбивает заголовок постера на строки (макс. 3), не разрывая слова."""
    words = title.split()
    lines: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        add_len = len(word) + (1 if current else 0)
        if current_len + add_len <= max_chars:
            current.append(word)
            current_len += add_len
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
            current_len = len(word)
    if current:
        lines.append(" ".join(current))
    if len(lines) > 3:
        lines = lines[:2] + [" ".join(lines[2:])]
    return lines[:3]


async def create_short_title_poster(
    video_path: Path,
    poster_path: Path,
    title: str,
    clip_duration_seconds: float,
) -> bool:
    """
    Создаёт стильный постер для short (PIL):
    - кадр из видео на 25% длины клипа
    - мягкое градиентное затемнение нижней части
    - заголовок крупным белым шрифтом с тёмной тенью

    Требует Pillow (HAS_PILLOW). При любой ошибке возвращает False.
    """
    if not HAS_PILLOW:
        return False
    try:
        import tempfile
        from PIL import Image, ImageDraw, ImageFont

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg or not video_path.exists():
            return False

        seek_time = max(1.0, clip_duration_seconds * 0.25)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            frame_path = Path(tmp.name)

        cmd = [
            ffmpeg, "-ss", str(seek_time), "-i", str(video_path),
            "-vframes", "1", "-q:v", "2", "-y", str(frame_path),
        ]
        loop = asyncio.get_running_loop()
        proc = await loop.run_in_executor(
            None, lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        )
        if proc.returncode != 0 or not frame_path.exists() or frame_path.stat().st_size == 0:
            frame_path.unlink(missing_ok=True)
            return False

        def _draw_poster() -> bool:
            try:
                with Image.open(frame_path) as base:
                    img = base.convert("RGBA")
                W, H = img.size

                # Градиентное затемнение нижних 55% — мягкий, не агрессивный
                overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
                draw_ov = ImageDraw.Draw(overlay)
                grad_top = int(H * 0.38)
                steps = 50
                for step in range(steps):
                    alpha = int((step / steps) * 175)  # макс. ~69% — не давит на картинку
                    y0 = grad_top + int((H - grad_top) * step / steps)
                    y1 = grad_top + int((H - grad_top) * (step + 1) / steps)
                    draw_ov.rectangle([(0, y0), (W, y1)], fill=(0, 0, 0, alpha))
                img = Image.alpha_composite(img, overlay)
                draw = ImageDraw.Draw(img)

                # Шрифт: приоритет Noto Sans / Inter / Montserrat (SemiBold/Bold),
                # чистые sans-serif, хорошо работают с русским.
                # Fallback: DejaVu → Liberation → FreeSans → default.
                font_size = max(56, W // 13)  # ~56px при 720px, чуть крупнее прежнего
                font = None
                for fp in [
                    # Noto Sans — лучший выбор для русского
                    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
                    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
                    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
                    # Inter (если установлен)
                    "/usr/share/fonts/truetype/inter/Inter-SemiBold.ttf",
                    "/usr/local/share/fonts/Inter-SemiBold.ttf",
                    # Montserrat (если установлен)
                    "/usr/share/fonts/truetype/montserrat/Montserrat-SemiBold.ttf",
                    # Liberation Sans — хороший fallback, похож на Arial
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
                    # DejaVu — широко доступен
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    # FreeSans
                    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
                ]:
                    if Path(fp).exists():
                        try:
                            font = ImageFont.truetype(fp, font_size)
                            break
                        except Exception:
                            continue
                if font is None:
                    font = ImageFont.load_default()

                lines = _wrap_poster_title(title)
                line_h = int(font_size * 1.30)   # чуть больший межстрочный интервал
                block_h = len(lines) * line_h
                # Safe-zone: 13% от нижнего края (≈167px при H=1280) — под интерфейс Shorts
                safe_margin = int(H * 0.13)
                text_top = H - safe_margin - block_h

                # Тень: многослойная мягкая — 3 слоя с нарастающей прозрачностью
                # Это даёт ощущение «глубины» без грубой жёсткой обводки
                shadow_layers = [
                    (3, 3, (0, 0, 0, 100)),   # дальний слой — очень мягкий
                    (2, 2, (0, 0, 0, 160)),   # средний
                    (1, 1, (0, 0, 0, 200)),   # близкий
                ]

                for li, line in enumerate(lines):
                    y = text_top + li * line_h
                    bbox = draw.textbbox((0, 0), line, font=font)
                    x = (W - (bbox[2] - bbox[0])) // 2

                    # Рисуем тень (все слои)
                    for sx, sy, sc in shadow_layers:
                        draw.text((x + sx, y + sy), line, font=font, fill=sc)

                    # Тонкая тёмная обводка (1px вокруг — читаемость на светлом фоне)
                    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 180))

                    # Основной белый текст
                    draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))

                img.convert("RGB").save(str(poster_path), "JPEG", quality=88)
                return True
            except Exception as e:
                logger.warning(f"_draw_poster error: {e}")
                return False

        result = await loop.run_in_executor(None, _draw_poster)
        frame_path.unlink(missing_ok=True)

        if result and poster_path.exists() and poster_path.stat().st_size > 0:
            logger.info(f"Title poster: {poster_path.name}")
            return True
        return False

    except Exception as e:
        logger.warning(f"create_short_title_poster error: {type(e).__name__}: {e}")
        return False


async def create_short_snapshot(
    video_path: Path,
    snapshot_path: Path,
    clip_duration_seconds: float,
) -> bool:
    """
    Извлекает один кадр из short-клипа в виде JPEG-постера.
    Кадр берётся на 30% длины клипа — минует черноту в начале, но ещё до середины.

    Возвращает True при успехе, False при ошибке.
    """
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return False
        if not video_path.exists():
            return False

        seek_time = max(1.0, clip_duration_seconds * 0.30)
        cmd = [
            ffmpeg,
            "-ss", str(seek_time),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",       # JPEG quality 2 = высокое качество
            "-y",
            str(snapshot_path),
        ]
        proc = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=60),
        )
        if proc.returncode != 0 or not snapshot_path.exists() or snapshot_path.stat().st_size == 0:
            logger.warning(f"create_short_snapshot: не удалось извлечь кадр из {video_path.name}")
            return False

        logger.info(f"Snapshot: {snapshot_path.name} (t={seek_time:.1f}s)")
        return True

    except Exception as e:
        logger.warning(f"create_short_snapshot error: {type(e).__name__}: {e}")
        return False


def _build_links_block(yt_url: str = "", rutube_url: str = "", vk_url: str = "") -> str:
    """Строит блок ссылок с эмодзи для caption. Каждая ссылка на своей строке."""
    lines = []
    if yt_url:
        _yt = get_youtube_video_url(yt_url)
        lines.append(f'<tg-emoji emoji-id="5463206079913533096">🎥</tg-emoji> <a href="{_yt}">YouTube</a>')
    if rutube_url:
        lines.append(f'<tg-emoji emoji-id="5321388265549373570">🎬</tg-emoji> <a href="{rutube_url}">RuTube</a>')
    if vk_url:
        lines.append(f'<tg-emoji emoji-id="5278229754099540071">🎦</tg-emoji> <a href="{vk_url}">VK</a>')
    if not lines:
        return ""
    return "<b>Полное видео:</b>\n" + "\n".join(lines)


def build_short_caption(
    candidate: dict,
    performer: str,
    real_author: str,
    real_event: str,
    format_name: str,
    yt_url: str = "",
    vk_url: str = "",
    rutube_url: str = "",
) -> str:
    """
    Строит подпись для YouTube Shorts.
    Компактный стиль: заголовок - Автор, ссылки с эмодзи, хэштеги.
    """
    kind         = (candidate.get("kind") or "").strip()
    hook         = (candidate.get("hook") or candidate.get("title") or "").strip()
    tags         = candidate.get("hashtags") or []
    author_label = real_author or performer or ""

    hook_tc = title_case_fragment(hook) if hook else ""
    if kind == "quote":
        hook_tc = f"«{hook_tc}»" if hook_tc and not hook_tc.startswith("«") else hook_tc

    if hook_tc and author_label:
        # Если заголовок уже заканчивается на вопросительный/восклицательный знак — разделитель не нужен
        if hook_tc[-1] in ("?", "!"):
            first_line = f"{hook_tc} {author_label}"
        else:
            first_line = f"{hook_tc} — {author_label}"
    else:
        first_line = hook_tc or author_label

    links_block = _build_links_block(yt_url, rutube_url, vk_url)
    tags_line   = " ".join(f"#{t}" for t in tags[:4]) if tags else ""

    parts = [p for p in [first_line, links_block, tags_line] if p]
    return "\n\n".join(parts)



# ─── Clips MVP (длинные фрагменты 5–15 мин) ──────────────────

CLIPS_PROMPT = """\
Ты — ассистент для поиска лучших длинных фрагментов из Q&A-видео.

Материал: {title}
Длительность: {duration}

Уже известные данные о материале:
- format: {format_name}
- real_author: {real_author}
- real_event: {real_event}
- analysis_summary: {analysis_summary}
- argument_arc: {argument_arc}
- key_categories: {key_categories}
- timestamps:
{timestamps}
- questions:
{questions}

ЗАДАЧА:
Найди 1–3 лучших длинных фрагмента (clip_candidates) для отдельной публикации.

Это не Shorts.
Это цельные, содержательные, самостоятельные отрывки длиной 5–15 минут,
которые хорошо работают как отдельные вырезки из длинного Q&A.

Главный принцип:
длина клипа должна определяться завершённостью ответа, а не красивой круглой цифрой.
Не выбирай 10:00 только потому, что это удобно.
Если ответ естественно заканчивается на 6:37, 8:54, 11:42 или 13:18 — оставь именно так.

Фрагмент должен:
- быть посвящён одному важному вопросу или одному крупному смысловому узлу
- включать полноценный ответ
- быть понятным и полезным как самостоятельное видео
- не начинаться слишком рано
- не заканчиваться на оборванной мысли
- не пересекаться сильно с другими clip-кандидатами
- не быть случайной нарезкой

ОГРАНИЧЕНИЯ ПО ДЛИНЕ:
- минимум: 5 минут
- желательно: 6–12 минут
- максимум: 15 минут

ПРИНЦИП ПЛОТНОСТИ:
- если полноценный ответ можно уместить в 6–10 минут, не растягивай его до 13–15
- не захватывай слишком ранний заход, если вопрос уже ясно сформулирован позже
- не оставляй длинный хвост после фактического завершения ответа
- не округляй длину до 10:00 или 12:00 без причины
- лучше естественный конец, чем красивая цифра

КРИТЕРИИ ХОРОШЕГО CLIP:
- один важный вопрос
- один цельный ответ
- понятный вход
- ясное завершение
- достаточная автономность
- можно смотреть отдельно от полного видео

СТРОГО:
- не выдумывать start/end
- не давать фрагменты, где нет завершённой мысли
- не обрывать ответ посередине
- не брать пустые вступления, технические объявления и затянутые переходы
- не делать кандидатов, которые сильно дублируют друг друга

Используй kind только из этого списка:
- qa_full_answer
- doctrinal_explanation
- practical_discussion
- controversial_topic
- pastoral_answer

Для каждого кандидата верни:
- start — время начала
- end — время конца
- title — короткий заголовок
- reason — почему именно этот фрагмент стоит выделить отдельно
- kind — тип фрагмента
- hashtags — 2–4 коротких релевантных хэштега без символа #

Требования к title:
- короткий
- ясный
- отражает суть вопроса
- хорошо подходит как название отдельного фрагмента

Требования к hashtags:
- только 2–4
- на русском языке
- CamelCase
- без символа #

ФОРМАТ ОТВЕТА:
Только чистый JSON, без ```json и без пояснений.

{{
  "clip_candidates": [
    {{
      "start": "31:30",
      "end": "40:06",
      "title": "Что такое лжеучителя и как их отличить?",
      "reason": "Цельный и сильный разбор одного важного вопроса.",
      "kind": "qa_full_answer",
      "hashtags": ["Лжеучителя", "БиблейскаяИстина", "ДоктриныБлагодати"]
    }}
  ]
}}
"""

CLIPS_SERMON_PROMPT = """\
Ты — ассистент для поиска лучших фрагментов из проповеди или лекции.

Материал: {title}
Автор: {real_author}
Длительность: {duration}

Данные о материале:
- format: {format_name}
- main_topic: {analysis_summary}
- argument_arc: {argument_arc}
- key_categories: {key_categories}
- timestamps:
{timestamps}

ЗАДАЧА:
Найди 1–3 лучших фрагмента (clip_candidates) для отдельной публикации.

Это НЕ Shorts.
Это цельные самостоятельные отрывки длиной 4–12 минут,
каждый из которых работает как отдельный мини-выпуск.

Главный принцип:
выбирай длину по завершённости фрагмента, а не по круглой цифре.
Не делай 10:00 или 12:00 только потому, что это удобно.
Хороший клип может длиться 5:42, 7:58, 9:36, 11:14.

ТИПЫ ФРАГМЕНТОВ:
- sermon_highlight    — кульминационный момент, центральный нерв проповеди
- conviction_appeal   — прямое обличение или призыв
- illustration_story  — живая история или иллюстрация с завершённым смыслом
- doctrinal_moment    — сжатое, плотное богословское объяснение
- application_block   — конкретный блок применения

КРИТЕРИИ ХОРОШЕГО ФРАГМЕНТА:
- законченная мысль с понятным входом и ясным завершением
- можно смотреть отдельно от полной проповеди
- несёт самостоятельный смысловой нерв
- не обрывается на середине аргумента
- не начинается посередине незавершённой мысли

ОГРАНИЧЕНИЯ ПО ДЛИНЕ:
- минимум: 4 минуты
- желательно: 5–10 минут
- максимум: 12 минут

ПРИНЦИП ОТБОРА:
Ищи места где проповедник:
- говорит особенно прямо и лично
- рассказывает историю с сильной моральной/духовной нагрузкой
- даёт доктринальный удар
- делает кульминационный призыв к действию или покаянию

Не бери:
- вступление
- технические объявления
- вялые переходы
- фрагменты, длина которых выбрана по круглой цифре, а не по реальному завершению мысли

Для каждого кандидата верни:
- start — время начала
- end   — время конца
- title — короткий заголовок
- reason — почему именно этот фрагмент стоит выделить
- kind — тип из списка выше
- hashtags — 2–4 коротких хэштега без символа #, CamelCase, на русском

ФОРМАТ ОТВЕТА:
Только чистый JSON, без ```json и без пояснений.

{{
  "clip_candidates": [
    {{
      "start": "12:30",
      "end": "21:45",
      "title": "Когда религия становится способом спрятаться от Бога",
      "reason": "Кульминационный момент — прямое обличение самообмана религиозного человека.",
      "kind": "conviction_appeal",
      "hashtags": ["СамообманСердца", "РелигияБезБога", "ПолВошер"]
    }}
  ]
}}
"""

CLIPS_MIN_SEC = 5 * 60    # 5 минут
CLIPS_MAX_SEC = 15 * 60   # 15 минут
CLIPS_IDEAL_MAX_SEC = 12 * 60  # желательный максимум


async def create_clips_candidates(
    mp3_path: Path,
    ai_data: dict,
    title: str,
    performer: str,
    duration: int,
    existing_audio_part=None,
    existing_client=None,
) -> list[dict]:
    """
    Ищет 1–3 длинных clip-кандидата через Gemini.
    Возвращает список словарей:
    [{"start","end","title","reason","kind","hashtags",
      "start_seconds","end_seconds","duration_seconds"}]

    Использует уже загруженный audio_part если доступен (без повторной загрузки).
    Работает независимо от Shorts. Не затрагивает MP3-пайплайн.
    """
    if not GEMINI_CLIENTS or not HAS_GEMINI:
        return []

    try:
        format_name      = (ai_data or {}).get("format", "other") or "other"
        real_author      = (ai_data or {}).get("real_author", "") or performer or ""
        real_event       = (ai_data or {}).get("real_event", "") or ""
        analysis_summary = (ai_data or {}).get("analysis_summary", "") or ""
        argument_arc     = (ai_data or {}).get("argument_arc", "") or ""
        key_categories   = "; ".join((ai_data or {}).get("key_categories", []) or [])
        timestamps       = (ai_data or {}).get("timestamps", "") or ""
        questions        = "\n".join((ai_data or {}).get("questions", []) or [])

        _qa_formats = ("qa", "interview", "discussion")
        if format_name in _qa_formats:
            # Q&A / интервью / дискуссия — промпт заточен под вопрос/ответ
            prompt = CLIPS_PROMPT.format(
                title=title,
                duration=format_timestamp(duration),
                format_name=format_name,
                real_author=real_author,
                real_event=real_event,
                analysis_summary=analysis_summary[:900],
                argument_arc=argument_arc[:700],
                key_categories=key_categories,
                timestamps=timestamps[:1500],
                questions=questions[:1000] if questions.strip() else "(нет данных)",
            )
            logger.info(f"Clips: используем QA-промпт (format={format_name})")
        else:
            # Проповедь / лекция / другое — промпт заточен под смысловые нервы
            prompt = CLIPS_SERMON_PROMPT.format(
                title=title,
                duration=format_timestamp(duration),
                format_name=format_name,
                real_author=real_author,
                real_event=real_event,
                analysis_summary=analysis_summary[:900],
                argument_arc=argument_arc[:700],
                key_categories=key_categories,
                timestamps=timestamps[:1500],
            )
            logger.info(f"Clips: используем sermon-промпт (format={format_name})")

        file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
        response = None

        # Используем уже загруженный audio_part (переиспользование из основного пайплайна)
        if existing_audio_part is not None and existing_client is not None:
            try:
                response = await existing_client.aio.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=[existing_audio_part, prompt],
                    config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=12000),
                )
            except Exception as e:
                logger.warning(f"Clips candidates existing_audio_part failed: {e}")
                response = None

        if response is None:
            audio_bytes = mp3_path.read_bytes() if file_size_mb <= 20 else None

            async def _upload(client):
                if file_size_mb > 20:
                    uf = await client.aio.files.upload(
                        file=mp3_path,
                        config=types.UploadFileConfig(
                            mime_type="audio/mpeg",
                            display_name=f"{performer} - {title}",
                        ),
                    )
                    while uf.state == "PROCESSING":
                        await asyncio.sleep(3)
                        uf = await client.aio.files.get(name=uf.name)
                    return uf
                return types.Part.from_bytes(data=audio_bytes, mime_type="audio/mpeg")

            async def _clips_with_client(client):
                audio_part = await _upload(client)
                _uploaded = getattr(audio_part, "name", None) is not None
                try:
                    return await client.aio.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=[audio_part, prompt],
                        config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=12000),
                    )
                finally:
                    if _uploaded:
                        try:
                            await client.aio.files.delete(name=audio_part.name)
                        except Exception:
                            pass

            async def _call_clips(client):
                return await _clips_with_client(client)

            response = await gemini_generate(GEMINI_CLIENTS, _call_clips)

        raw_text = ""
        try:
            raw_text = response.text or ""
        except Exception:
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if not getattr(part, "thought", False):
                        raw_text += part.text or ""

        if not raw_text.strip():
            logger.warning("Clips candidates: Gemini вернул пустой ответ")
            return []

        clean = raw_text.strip()
        clean = re.sub(r"^```[a-z]*\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean).strip()
        start = clean.find("{")
        end   = clean.rfind("}")
        if start == -1 or end <= start:
            logger.warning(f"Clips candidates: JSON не найден | текст: {raw_text[:500]}")
            return []

        try:
            data = json.loads(clean[start:end + 1])
        except json.JSONDecodeError as e:
            logger.warning(f"Clips candidates JSONDecodeError: {e} | текст: {raw_text[:500]}")
            return []

        candidates_raw = data.get("clip_candidates", [])
        if not isinstance(candidates_raw, list):
            return []

        out: list[dict] = []
        seen_ranges: set[tuple[int, int]] = set()

        for item in candidates_raw[:5]:
            if not isinstance(item, dict):
                continue
            start_t    = str(item.get("start", "")).strip()
            end_t      = str(item.get("end",   "")).strip()
            clip_title = _scrub_inline(_strip_meta_lines(_clean_field(item.get("title",  ""))))
            reason     = _scrub_inline(_strip_meta_lines(_clean_field(item.get("reason", ""))))
            kind       = str(item.get("kind", "")).strip()
            raw_tags   = item.get("hashtags", [])
            if isinstance(raw_tags, list):
                hashtags = [
                    str(t).strip().lstrip("#").strip()
                    for t in raw_tags if str(t).strip()
                ][:4]
            else:
                hashtags = []

            if not start_t or not end_t or not clip_title:
                continue

            start_s = time_to_seconds(start_t)
            end_s   = time_to_seconds(end_t)
            if start_s is None or end_s is None:
                continue
            if start_s < 0 or end_s <= start_s:
                continue
            if end_s > duration + 10:
                end_s = duration
            if end_s <= start_s:
                continue

            clip_len = end_s - start_s

            # Жёсткие границы длины
            if clip_len < CLIPS_MIN_SEC:
                logger.info(
                    f"Clips: пропускаю '{clip_title}' — слишком короткий "
                    f"({clip_len:.0f}s < {CLIPS_MIN_SEC}s)"
                )
                continue
            if clip_len > CLIPS_MAX_SEC:
                logger.info(
                    f"Clips: пропускаю '{clip_title}' — слишком длинный "
                    f"({clip_len:.0f}s > {CLIPS_MAX_SEC}s)"
                )
                continue

            # Мягкая эвристика против раздутых клипов:
            # если clip превышает желательный максимум (12 мин), логируем предупреждение.
            # Сам clip не отбрасываем — модель могла дать обоснованный длинный фрагмент,
            # но в логах будет видно для мониторинга качества.
            if clip_len > CLIPS_IDEAL_MAX_SEC:
                overshoot_sec = clip_len - CLIPS_IDEAL_MAX_SEC
                logger.info(
                    f"Clips: '{clip_title}' превышает желательный максимум "
                    f"на {overshoot_sec:.0f}s ({clip_len:.0f}s > {CLIPS_IDEAL_MAX_SEC}s) — "
                    f"принят, но проверь плотность"
                )

            # Детекция пересечений: пропускаем кандидатов с >50% overlap с уже принятыми.
            # Exact-match (start_s, end_s) тоже блокируется через seen_ranges.
            overlaps_existing = False
            for (ex_start, ex_end) in seen_ranges:
                overlap_start = max(start_s, ex_start)
                overlap_end   = min(end_s, ex_end)
                overlap_len   = max(0, overlap_end - overlap_start)
                if overlap_len > 0:
                    overlap_ratio = overlap_len / min(clip_len, ex_end - ex_start)
                    if overlap_ratio > 0.5:
                        logger.info(
                            f"Clips: пропускаю '{clip_title}' — "
                            f"{overlap_ratio:.0%} пересечения с уже принятым клипом"
                        )
                        overlaps_existing = True
                        break
            if overlaps_existing:
                continue

            seen_ranges.add((start_s, end_s))
            out.append({
                "start":            start_t,
                "end":              end_t,
                "title":            clip_title[:140],
                "reason":           reason[:280],
                "kind":             kind[:40],
                "hashtags":         hashtags,
                "start_seconds":    start_s,
                "end_seconds":      end_s,
                "duration_seconds": clip_len,
            })

        _durations = [f"{c['duration_seconds']:.0f}s" for c in out]
        logger.info(
            f"Clips candidates: принято {len(out)} из {len(candidates_raw)} предложенных "
            f"(длины: {_durations})"
        )
        return out[:3]

    except Exception as e:
        logger.warning(f"create_clips_candidates error: {type(e).__name__}: {e}")
        return []


async def render_clip(
    source_video_path: Path,
    output_path: Path,
    start_seconds: int,
    end_seconds: int,
) -> bool:
    """
    Вырезает длинный clip из исходного видео через ffmpeg.
    Сохраняет оригинальное соотношение сторон (16:9 или как есть).
    Без вертикальной трансформации — clips не для Shorts.
    Возвращает True при успехе.
    """
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("render_clip: ffmpeg не найден")
            return False
        if not source_video_path.exists():
            logger.warning(f"render_clip: исходный файл не найден: {source_video_path}")
            return False
        if end_seconds <= start_seconds:
            logger.warning(f"render_clip: невалидный диапазон {start_seconds}..{end_seconds}")
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Корректируем точку конца до ближайшей паузы
        adjusted_end = await _find_silence_end(source_video_path, float(end_seconds), search_window=6.0)
        if abs(adjusted_end - end_seconds) > 0.1:
            logger.info(f"Clip end adjusted: {end_seconds}s → {adjusted_end:.1f}s (silence snap)")
            end_seconds = int(round(adjusted_end))
        clip_duration = end_seconds - start_seconds
        if clip_duration <= 0:
            logger.warning("render_clip: clip_duration ≤ 0 после коррекции паузы")
            return False

        _enc, _quality, _preset = _get_video_encoder()
        # Clips: чуть лучше качество чем у shorts — cq/crf 22 вместо 23
        _quality_clip = ["-rc", "vbr", "-cq", "22"] if _enc == "h264_nvenc" else ["-crf", "22"]
        _hwaccel = []  # hwaccel cuda убран: CPU-фильтры несовместимы с CUDA decode
        cmd = [
            ffmpeg,
            *_hwaccel,
            "-ss", str(start_seconds),
            "-i", str(source_video_path),
            "-t", str(clip_duration),
            "-c:v", _enc,
            *_preset,
            *_quality_clip,
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-y",
            str(output_path),
        ]

        proc = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=900),
        )
        if proc.returncode != 0:
            stderr_tail = (proc.stderr or '')[-800:]
            # ffmpeg на Windows иногда завершается с кодом != 0 после "received signal 2"
            # (SIGINT от родительского процесса), но файл при этом уже записан корректно.
            # Если файл существует и не пуст — считаем успехом.
            file_ok = output_path.exists() and output_path.stat().st_size > 0
            if file_ok and "received signal 2" in stderr_tail:
                logger.info(
                    f"render_clip: ffmpeg вышел по signal 2, но файл создан — "
                    f"считаем успехом (rc={proc.returncode})"
                )
            else:
                logger.warning(f"render_clip ffmpeg error: {stderr_tail}")
                return False
        if not output_path.exists() or output_path.stat().st_size == 0:
            logger.warning("render_clip: выходной файл не создан или пуст")
            return False

        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info(
            f"Clip rendered: {output_path.name} "
            f"({start_seconds}s..{end_seconds}s, {clip_duration}s, {size_mb:.1f}MB)"
        )
        return True

    except subprocess.TimeoutExpired:
        logger.warning("render_clip: ffmpeg timeout")
        return False
    except Exception as e:
        logger.warning(f"render_clip error: {type(e).__name__}: {e}")
        return False


async def create_clip_snapshot(
    video_path: Path,
    snapshot_path: Path,
    clip_duration_seconds: float,
) -> bool:
    """
    Извлекает кадр-постер из clip (20% от длины — раньше чем у Shorts,
    т.к. clips начинаются с содержательного момента).
    Возвращает True при успехе.
    """
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg or not video_path.exists():
            return False
        seek_time = max(2.0, clip_duration_seconds * 0.20)
        cmd = [
            ffmpeg,
            "-ss", str(seek_time),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            "-y",
            str(snapshot_path),
        ]
        proc = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=60),
        )
        if proc.returncode != 0 or not snapshot_path.exists() or snapshot_path.stat().st_size == 0:
            logger.warning(f"create_clip_snapshot: не удалось извлечь кадр из {video_path.name}")
            return False
        logger.info(f"Clip snapshot: {snapshot_path.name} (t={seek_time:.1f}s)")
        return True
    except Exception as e:
        logger.warning(f"create_clip_snapshot error: {type(e).__name__}: {e}")
        return False


def build_clip_caption(
    candidate: dict,
    performer: str,
    real_author: str,
    real_event: str,
    format_name: str,
    yt_url: str = "",
    vk_url: str = "",
    rutube_url: str = "",
) -> str:
    """
    Строит подпись для длинного clip-фрагмента.
    Компактный стиль: заголовок - Автор ✂️ [старт — конец], ссылки с эмодзи, хэштеги.
    """
    title        = (candidate.get("title") or "").strip()
    tags         = candidate.get("hashtags") or []
    start_s      = candidate.get("start_seconds", 0)
    end_s        = candidate.get("end_seconds", 0)
    author_label = real_author or performer or ""

    def _fmt(secs: int) -> str:
        m, s = divmod(int(secs), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    kind = (candidate.get("kind") or "").strip()
    _KIND_EMOJI = {
        "sermon_highlight":   "🔥",
        "conviction_appeal":  "⚡",
        "illustration_story": "📖",
        "doctrinal_moment":   "💡",
        "application_block":  "👣",
        # QA-типы оставляем ✂️
    }
    clip_emoji = _KIND_EMOJI.get(kind, "✂️")
    range_label = f"{clip_emoji} [{_fmt(start_s)} — {_fmt(end_s)}]"

    title_tc = title_case_fragment(title) if title else ""
    if title_tc and author_label:
        if title_tc[-1] in ("?", "!"):
            first_line = f"{title_tc} {author_label} {range_label}"
        else:
            first_line = f"{title_tc} — {author_label} {range_label}"
    elif title_tc:
        first_line = f"{title_tc} {range_label}"
    else:
        first_line = range_label

    links_block = _build_links_block(yt_url, rutube_url, vk_url)
    tags_line   = " ".join(f"#{t}" for t in tags[:4]) if tags else ""

    parts = [p for p in [first_line, links_block, tags_line] if p]
    return "\n\n".join(parts)


async def process_and_send_clips(
    url: str,
    media_id: str,
    mp3_path: Path,
    title: str,
    performer: str,
    duration: int,
    ai_data: dict,
    update,
    existing_audio_part=None,
    existing_client=None,
    rutube_url: str = "",
    vk_url: str = "",
) -> None:
    """
    Полный clips-пайплайн:
      1. Найти clip-кандидатов (Gemini)
      2. Скачать видео (переиспользуем download_video_for_shorts)
      3. Вырезать clip (render_clip) — оригинальное соотношение сторон
      4. [опц] Snapshot-постер (create_clip_snapshot)
      5. Отправить в Telegram как video
      6. Логировать результат

    Ошибки не пробрасываются — clips не должны валить основной результат.
    MP3-пайплайн и Shorts-пайплайн не затронуты.
    """
    video_path = None
    clip_paths: list[Path] = []
    snap_paths: list[Path] = []
    try:
        # ── Шаг 1: найти кандидатов ──────────────────────────
        candidates = await create_clips_candidates(
            mp3_path=mp3_path,
            ai_data=ai_data,
            title=title,
            performer=performer,
            duration=duration,
            existing_audio_part=existing_audio_part,
            existing_client=existing_client,
        )
        if not candidates:
            logger.info("Clips: кандидаты не найдены")
            return

        logger.info(f"Clips: найдено {len(candidates)} кандидатов, скачиваю видео...")

        # ── Шаг 2: скачать видео (тот же хелпер что у Shorts) ──
        video_path = await download_video_for_shorts(url, media_id)
        if not video_path:
            logger.warning("Clips: не удалось скачать видео")
            await update.message.reply_text("🎬 Не удалось скачать видео для Clips.")
            return

        format_name = (ai_data or {}).get("format", "other") or "other"
        real_author = (ai_data or {}).get("real_author", "") or performer or ""
        real_event  = (ai_data or {}).get("real_event", "") or ""
        do_snapshot = await asettings_get("clips_snapshot")

        logger.info(
            f"Clips: format={format_name} snapshot={do_snapshot} "
            f"кандидатов={len(candidates)}"
        )

        total = len(candidates)
        sent  = 0

        for i, c in enumerate(candidates, 1):
            clip_path = DOWNLOAD_DIR / f"{media_id}_clip_{i}.mp4"
            snap_path = DOWNLOAD_DIR / f"{media_id}_clip_{i}_snap.jpg"
            clip_paths.append(clip_path)
            snap_paths.append(snap_path)

            logger.info(
                f"Clips: рендер {i}/{total} "
                f"({c['start']}–{c['end']}) '{c['title']}'"
            )

            # ── Шаг 3: вырезать ──────────────────────────────
            ok = await render_clip(
                video_path, clip_path,
                c["start_seconds"], c["end_seconds"],
            )
            if not ok:
                logger.warning(
                    f"Clips: не удалось вырезать {i}/{total} ({c['start']}–{c['end']})"
                )
                continue

            # Проверка размера: Telegram лимит 2GB, но для video-сообщений ~50MB удобнее
            clip_size_mb = clip_path.stat().st_size / (1024 * 1024)
            if clip_size_mb > MAX_FILE_SIZE_MB:
                logger.warning(
                    f"Clips {i}/{total}: файл слишком большой ({clip_size_mb:.0f}MB), пропускаем"
                )
                continue

            # ── Шаг 4: snapshot (опционально) ────────────────
            thumb_buf = None
            if do_snapshot:
                try:
                    snap_ok = await create_clip_snapshot(
                        clip_path, snap_path, c["duration_seconds"]
                    )
                    if snap_ok and snap_path.exists():
                        thumb_buf = BytesIO(snap_path.read_bytes())
                        thumb_buf.name = snap_path.name
                except Exception as snap_err:
                    logger.warning(f"Clips {i}/{total}: snapshot error: {snap_err}")

            # ── Шаг 5: caption ────────────────────────────────
            caption = build_clip_caption(
                candidate=c,
                performer=performer,
                real_author=real_author,
                real_event=real_event,
                format_name=format_name,
                yt_url=url,
                vk_url=vk_url,
                rutube_url=rutube_url,
            )
            if len(caption) > 1024:
                caption = caption[:1021] + "..."

            # ── Шаг 6: отправить ─────────────────────────────
            try:
                logger.info(
                    f"Clips: отправляю {i}/{total} "
                    f"({clip_size_mb:.1f}MB, {c['duration_seconds']:.0f}s)"
                )
                with open(clip_path, "rb") as vf:
                    # Получаем оригинальные размеры через ffprobe если возможно,
                    # иначе не передаём — Telegram сам определит
                    await update.message.reply_video(
                        video=vf,
                        caption=caption,
                        duration=int(c["duration_seconds"]),
                        thumbnail=thumb_buf,
                        supports_streaming=True,
                        parse_mode="HTML",
                        write_timeout=300,   # длинные clips = больше времени на отправку
                        read_timeout=300,
                        connect_timeout=30,
                    )
                sent += 1
                logger.info(
                    f"Clips: отправлен {i}/{total} "
                    f"({c['start']}–{c['end']}) '{c['title']}'"
                )
            except Exception as send_err:
                logger.warning(f"Clips: ошибка отправки {i}/{total}: {send_err}")
            finally:
                if thumb_buf:
                    try:
                        thumb_buf.close()
                    except Exception:
                        pass

        # ── Шаг 6: итог ──────────────────────────────────────
        if sent == 0:
            logger.warning("Clips: ни один clip не был отправлен")
        else:
            logger.info(f"Clips: итого отправлено {sent}/{total}")

    except Exception as e:
        logger.warning(f"Clips process_and_send error: {e}")
        try:
            await update.message.reply_text(
                f"🎬 Ошибка при подготовке Clips: {str(e)[:150]}"
            )
        except Exception:
            pass
    finally:
        for p in clip_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        for p in snap_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        if video_path:
            try:
                video_path.unlink(missing_ok=True)
            except Exception:
                pass


async def process_and_send_shorts(
    url: str,
    media_id: str,
    mp3_path: Path,
    title: str,
    performer: str,
    duration: int,
    ai_data: dict,
    update,
    existing_audio_part=None,
    existing_client=None,
    rutube_url: str = "",
    vk_url: str = "",
) -> None:
    """
    Полный shorts-пайплайн v2:
      1. Найти кандидатов (Gemini)
      2. Скачать видео
      3. Вырезать short (render_short_clip)
      4. Постобработка: normalize + speed (postprocess_short)
      5. [опц] Субтитры: transcribe → burn_subtitles_into_short
      6. [опц] Постер с заголовком: create_short_title_poster
      7. [опц] Snapshot: create_short_snapshot (fallback thumbnail)
      8. Отправить в Telegram
      9. Логировать результат

    Ошибки в шагах 5–7 не прерывают пайплайн.
    MP3-пайплайн не затронут.
    """
    video_path = None
    short_paths: list[Path] = []
    poster_paths: list[Path] = []
    # Читаем speed один раз в начале — используется и для кандидатов и для рендера
    speed = float(shorts_speed_get())
    # Читаем заранее — используется в finally (await там нельзя).
    # Если исключение случится до строки внутри try где это читалось — UnboundLocalError.
    _keep_for_montage = False  # будет перезаписан внутри try после загрузки видео
    try:
        # ── Шаг 1: найти кандидатов ──────────────────────────
        candidates = await create_shorts_candidates(
            mp3_path=mp3_path,
            ai_data=ai_data,
            title=title,
            performer=performer,
            duration=duration,
            existing_audio_part=existing_audio_part,
            existing_client=existing_client,
            speed=speed,
        )
        if not candidates:
            logger.info("Shorts: кандидаты не найдены")
            return

        logger.info(f"Shorts: найдено {len(candidates)} кандидатов, скачиваю видео...")

        # ── Шаг 2: скачать видео ─────────────────────────────
        video_path = await download_video_for_shorts(url, media_id)
        if not video_path:
            logger.warning("Shorts: не удалось скачать видео")
            await update.message.reply_text("✂️ Не удалось скачать видео для Shorts.")
            return

        # ── Настройки ─────────────────────────────────────────
        format_name      = (ai_data or {}).get("format", "other") or "other"
        real_author      = (ai_data or {}).get("real_author", "") or performer or ""
        real_event       = (ai_data or {}).get("real_event", "") or ""

        visual_mode      = get_shorts_visual_mode(format_name)
        do_normalize     = await asettings_get("shorts_audio_normalize")
        do_snapshot      = await asettings_get("shorts_snapshot")
        do_subtitles     = await asettings_get("shorts_subtitles")
        do_title_poster  = await asettings_get("shorts_title_poster")
        # speed читается в начале функции — не повторяем здесь
        # Читаем заранее — используется в finally (await там нельзя)
        _keep_for_montage = (
            await asettings_get("shorts_montage") or await asettings_get("shorts_highlights")
        )

        logger.info(
            f"Shorts: format={format_name} visual={visual_mode} "
            f"normalize={do_normalize} speed={speed} snapshot={do_snapshot} "
            f"subtitles={do_subtitles} title_poster={do_title_poster}"
        )

        # Для multi-speaker форматов субтитры работают, но могут ошибаться
        if do_subtitles and format_name in ("qa", "discussion", "interview"):
            logger.info("Shorts subtitles: формат multi-speaker — субтитры могут ошибаться")

        total = len(candidates)
        sent  = 0

        for i, c in enumerate(candidates, 1):
            raw_path          = DOWNLOAD_DIR / f"{media_id}_short_{i}_raw.mp4"
            post_path         = DOWNLOAD_DIR / f"{media_id}_short_{i}_post.mp4"
            sub_path          = DOWNLOAD_DIR / f"{media_id}_short_{i}_sub.mp4"
            snapshot_path     = DOWNLOAD_DIR / f"{media_id}_short_{i}_snap.jpg"
            title_poster_path = DOWNLOAD_DIR / f"{media_id}_short_{i}_poster.jpg"

            short_paths  += [raw_path, post_path, sub_path]
            poster_paths += [snapshot_path, title_poster_path]

            logger.info(
                f"Shorts: рендер {i}/{total} "
                f"({c['start']}–{c['end']}) '{c['title']}' [{visual_mode}]"
            )

            # ── Шаг 3: вырезать ──────────────────────────────
            # Если будет ускорение, расширяем окно конца клипа чтобы
            # после speed пользователь слышал завершённую мысль.
            # Пример: speed=1.1, end=808s → реально берём до 808+5=813s,
            # после ускорения это ужмётся обратно до ~807s.
            speed_extra = 0
            if abs(speed - 1.0) > 0.01:
                speed_extra = int((c["end_seconds"] - c["start_seconds"]) * (speed - 1.0)) + 2
            render_end = c["end_seconds"] + speed_extra

            ok = await render_short_clip(
                video_path, raw_path,
                c["start_seconds"], render_end,
                visual_mode=visual_mode,
            )
            if not ok:
                logger.warning(
                    f"Shorts: не удалось вырезать {i}/{total} ({c['start']}–{c['end']})"
                )
                continue

            # ── Шаг 4: постобработка ─────────────────────────
            need_post = do_normalize or (abs(speed - 1.0) > 0.01)
            current_path = raw_path
            if need_post:
                post_ok = await postprocess_short(
                    raw_path, post_path,
                    normalize_audio=do_normalize,
                    speed=speed,
                )
                if post_ok:
                    current_path = post_path
                    # Логируем итоговую длительность после speed/normalize
                    _orig_dur = c["duration_seconds"]
                    _final_dur = round(_orig_dur / speed) if abs(speed - 1.0) > 0.01 else _orig_dur
                    logger.info(
                        f"Shorts {i}/{total}: postprocess OK — "
                        f"исходно {_orig_dur}s, после speed={speed} → ~{_final_dur}s"
                    )
                else:
                    logger.warning(
                        f"Shorts: постобработка {i}/{total} не удалась, использую raw"
                    )

            # ── Шаг 5: субтитры (опционально) ────────────────
            subtitles_applied = False
            nosub_path = None  # Путь к файлу ДО субтитров
            # Сохраняем копию до субтитров (для кнопки 🚫Sub)
            if do_subtitles and HAS_FASTER_WHISPER:
                nosub_save_path = DOWNLOAD_DIR / f"{media_id}_short_{i}_nosub.mp4"
                try:
                    shutil.copy2(current_path, nosub_save_path)
                    nosub_path = nosub_save_path
                except Exception as _cp_err:
                    logger.warning(f"Shorts {i}/{total}: не удалось сохранить nosub копию: {_cp_err}")
                    nosub_path = None
            if do_subtitles:
                if not HAS_FASTER_WHISPER:
                    logger.warning(
                        "Subtitles: faster-whisper не установлен. "
                        "Установите: pip install faster-whisper"
                    )
                else:
                    try:
                        logger.info(
                            f"Shorts {i}/{total}: subtitle pipeline start, "
                            f"файл={current_path.name} "
                            f"exists={current_path.exists()} "
                            f"backend=faster-whisper({HAS_FASTER_WHISPER})"
                        )
                        segments = await transcribe_short_clip(current_path, ai_data=ai_data)
                        if segments:
                            logger.info(
                                f"Shorts {i}/{total}: транскрипция OK "
                                f"({len(segments)} сегм.), запускаю burn-in..."
                            )
                            sub_ok = await burn_subtitles_into_short(
                                current_path, sub_path, segments
                            )
                            if sub_ok:
                                current_path = sub_path
                                subtitles_applied = True
                                logger.info(
                                    f"Shorts {i}/{total}: субтитры вшиты → {sub_path.name}"
                                )
                            else:
                                logger.warning(
                                    f"Shorts {i}/{total}: burn-in не удался, "
                                    "продолжаем без субтитров"
                                )
                        else:
                            logger.warning(
                                f"Shorts {i}/{total}: транскрипция вернула пустой список — "
                                "субтитры пропущены (см. логи выше)"
                            )
                    except Exception as sub_err:
                        logger.warning(
                            f"Shorts {i}/{total}: subtitle pipeline exception "
                            f"({type(sub_err).__name__}): {sub_err}"
                        )

            # ── Шаг 6: постер с заголовком (опционально) ─────
            thumb_buf = None
            if do_title_poster:
                try:
                    poster_ok = await create_short_title_poster(
                        current_path, title_poster_path,
                        c["title"], c["duration_seconds"],
                    )
                    if poster_ok and title_poster_path.exists():
                        thumb_buf = BytesIO(title_poster_path.read_bytes())
                        thumb_buf.name = title_poster_path.name
                        logger.info(f"Shorts {i}/{total}: title poster создан")
                except Exception as poster_err:
                    logger.warning(f"Shorts {i}/{total}: title poster error: {poster_err}")

            # ── Шаг 7: snapshot (fallback, если постер не создан) ─
            if thumb_buf is None and do_snapshot:
                try:
                    snap_ok = await create_short_snapshot(
                        current_path, snapshot_path, c["duration_seconds"]
                    )
                    if snap_ok and snapshot_path.exists():
                        thumb_buf = BytesIO(snapshot_path.read_bytes())
                        thumb_buf.name = snapshot_path.name
                except Exception as snap_err:
                    logger.warning(f"Shorts {i}/{total}: snapshot error: {snap_err}")

            # ── Шаг 8: caption ───────────────────────────────
            caption = build_short_caption(
                candidate=c,
                performer=performer,
                real_author=real_author,
                real_event=real_event,
                format_name=format_name,
                yt_url=url,
                vk_url=vk_url,
                rutube_url=rutube_url,
            )
            if len(caption) > 1024:
                caption = caption[:1021] + "..."

            # ── Шаг 9: отправить ─────────────────────────────
            try:
                logger.info(f"Shorts: отправляю {i}/{total}")
                # Сохраняем данные для trim-кнопок
                short_id = uuid.uuid4().hex[:16]
                short_trim_save(
                    short_id=short_id,
                    video_path=str(current_path),
                    start_seconds=c.get("start_seconds", 0),
                    end_seconds=c.get("end_seconds", 0),
                    visual_mode=visual_mode,
                    yt_url=url,
                    vk_url=vk_url,
                    rutube_url=rutube_url,
                    performer=performer,
                    real_author=real_author,
                    real_event=real_event,
                    format_name=format_name,
                    candidate_json=json.dumps(c, ensure_ascii=False),
                    video_path_nosub=str(nosub_path) if nosub_path else "",
                )
                _nosub_buttons = []
                if subtitles_applied:
                    _nosub_buttons = [InlineKeyboardButton("🚫Sub", callback_data=f"strim:nosub:{short_id}")]
                trim_keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏪ Начало -10", callback_data=f"strim:s10:{short_id}"),
                    InlineKeyboardButton("⏭ Конец +10",  callback_data=f"strim:e10:{short_id}"),
                    InlineKeyboardButton("⏭⏭ Конец +20", callback_data=f"strim:e20:{short_id}"),
                    *_nosub_buttons,
                ]])
                with open(current_path, "rb") as vf:
                    await update.message.reply_video(
                        video=vf,
                        caption=caption,
                        duration=int(c["duration_seconds"]),
                        width=720,
                        height=1280,
                        thumbnail=thumb_buf,
                        parse_mode="HTML",
                        reply_markup=trim_keyboard,
                        write_timeout=120,
                        read_timeout=120,
                        connect_timeout=30,
                    )
                sent += 1
                logger.info(
                    f"Shorts: отправлен {i}/{total} ({c['start']}–{c['end']}) '{c['title']}'"
                )
                # Сохраняем копию short в папку downloads с читаемым именем
                try:
                    safe_title = re.sub(r'[\\/:*?"<>|]', '_', c.get("title", "") or "").strip() or f"short_{i}"
                    safe_title = safe_title[:60]
                    copy_name = f"{media_id}_{i:02d}_{safe_title}.mp4"
                    copy_dest = DOWNLOAD_DIR / copy_name
                    shutil.copy2(str(current_path), str(copy_dest))
                    logger.info(f"Shorts: сохранена копия → {copy_dest}")
                except Exception as copy_err:
                    logger.warning(f"Shorts: не удалось сохранить копию {i}: {copy_err}")
                # Уведомляем если субтитры были включены но не применились
                if do_subtitles and not subtitles_applied and HAS_FASTER_WHISPER:
                    try:
                        await update.message.reply_text(
                            "⚠️ Субтитры для этого Short не удалось создать "
                            "(тихая речь, смешанный язык или ошибка транскрипции)."
                        )
                    except Exception:
                        pass
            except Exception as send_err:
                logger.warning(f"Shorts: ошибка отправки {i}/{total}: {send_err}")
            finally:
                if thumb_buf:
                    try:
                        thumb_buf.close()
                    except Exception:
                        pass

        # ── Шаг 9: итог ──────────────────────────────────────
        if sent == 0:
            logger.warning("Shorts: ни один short не был отправлен")
        else:
            logger.info(f"Shorts: итого отправлено {sent}/{total}")

    except Exception as e:
        logger.warning(f"Shorts process_and_send error: {e}")
        try:
            await update.message.reply_text(
                f"✂️ Ошибка при подготовке Shorts: {str(e)[:150]}"
            )
        except Exception:
            pass
    finally:
        for p in short_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        for p in poster_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        if video_path:
            try:
                if not _keep_for_montage:
                    video_path.unlink(missing_ok=True)
            except Exception:
                pass


# ─── Montage Short & Highlights Reel ─────────────────────────


async def render_montage_short(
    source_video_path: Path,
    output_path: Path,
    fragments: list[dict],
    *,
    visual_mode: str = "full_frame_vertical",
) -> bool:
    """Склеивает несколько фрагментов в один Short 9:16 через ffmpeg concat."""
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg or not source_video_path.exists() or not fragments:
            return False
        output_path.parent.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()

        if visual_mode == "crop_zoom":
            vf = "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=720:1280"
            _use_fc = False
        else:
            vf = (
                "[0:v]split=2[bg][fg];"
                "[bg]scale=720:1280,gblur=sigma=20[blurred];"
                "[fg]scale=720:-2[small];"
                "[blurred][small]overlay=(W-w)/2:(H-h)/2[out]"
            )
            _use_fc = True

        temp_parts: list[Path] = []
        _enc, _quality, _preset = _get_video_encoder()
        _hwaccel = []  # hwaccel cuda убран: CPU-фильтры несовместимы с CUDA decode
        for i, frag in enumerate(fragments):
            part_path = output_path.parent / f"{output_path.stem}_part{i}.mp4"
            temp_parts.append(part_path)
            start_s = frag["start_seconds"]
            end_s   = frag["end_seconds"]
            dur     = end_s - start_s
            if dur <= 0:
                continue
            _vf_args_m = (
                ["-filter_complex", vf, "-map", "[out]", "-map", "0:a?"]
                if _use_fc else ["-vf", vf]
            )
            cmd = [
                ffmpeg, *_hwaccel,
                "-ss", str(start_s), "-i", str(source_video_path),
                "-t", str(dur), *_vf_args_m,
                "-c:v", _enc, *_preset, *_quality,
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                "-y", str(part_path),
            ]
            proc = await loop.run_in_executor(
                None, lambda c=cmd: subprocess.run(c, capture_output=True, text=True, timeout=120)
            )
            if proc.returncode != 0 or not part_path.exists():
                logger.warning(f"Montage: фрагмент {i} не отрендерен")
                for p in temp_parts: p.unlink(missing_ok=True)
                return False

        existing_parts = [p for p in temp_parts if p.exists() and p.stat().st_size > 0]
        if len(existing_parts) < 2:
            for p in temp_parts: p.unlink(missing_ok=True)
            return False

        concat_list_path = output_path.parent / f"{output_path.stem}_concat.txt"
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for part_path in existing_parts:
                f.write(f"file '{part_path.resolve()}'\n")

        concat_cmd = [
            ffmpeg, *_hwaccel, "-f", "concat", "-safe", "0", "-i", str(concat_list_path),
            "-c:v", _enc, *_preset, *_quality,
            "-c:a", "aac", "-b:a", "128k",
            "-vsync", "vfr",    # убирает drift между фрагментами с разным GOP
            "-af", "aresample=async=1",  # синхронизирует аудио после склейки
            "-movflags", "+faststart",
            "-y", str(output_path),
        ]
        proc = await loop.run_in_executor(
            None, lambda: subprocess.run(concat_cmd, capture_output=True, text=True, timeout=300)
        )
        for p in temp_parts: p.unlink(missing_ok=True)
        concat_list_path.unlink(missing_ok=True)

        if proc.returncode != 0 or not output_path.exists():
            logger.warning(f"Montage: concat failed: {(proc.stderr or '')[-300:]}")
            return False

        total_dur = sum(f["end_seconds"] - f["start_seconds"] for f in fragments)
        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info(f"Montage rendered: {output_path.name} ({len(existing_parts)} фрагм., {total_dur}s, {size_mb:.1f}MB)")
        return True
    except subprocess.TimeoutExpired:
        logger.warning("render_montage_short: ffmpeg timeout")
        return False
    except Exception as e:
        logger.warning(f"render_montage_short error: {type(e).__name__}: {e}")
        return False


async def create_extras_candidates(
    ai_data: dict,
    title: str,
    performer: str,
    duration: int,
) -> dict:
    """
    Один text-only Gemini запрос на Montage + Highlights.
    Возвращает:
    {
        "montage_candidates": [...],
        "highlights_candidates": [...]
    }
    """
    if not GEMINI_CLIENTS or not HAS_GEMINI:
        return {"montage_candidates": [], "highlights_candidates": []}

    try:
        format_name      = (ai_data or {}).get("format", "other") or "other"
        real_author      = (ai_data or {}).get("real_author", "") or performer or ""
        analysis_summary = (ai_data or {}).get("analysis_summary", "") or ""
        argument_arc     = (ai_data or {}).get("argument_arc", "") or ""
        key_categories   = "; ".join((ai_data or {}).get("key_categories", []) or [])
        timestamps       = (ai_data or {}).get("timestamps", "") or ""

        prompt = EXTRAS_PROMPT.format(
            title=title,
            duration=format_timestamp(duration),
            format_name=format_name,
            real_author=real_author,
            analysis_summary=analysis_summary[:800],
            argument_arc=argument_arc[:700],
            timestamps=timestamps[:1200],
            key_categories=key_categories,
        )

        async def _call(client):
            return await client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=14000,
                ),
            )

        response = await gemini_generate(GEMINI_CLIENTS, _call)

        raw_text = ""
        try:
            raw_text = response.text or ""
        except Exception:
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if not getattr(part, "thought", False):
                        raw_text += part.text or ""

        if not raw_text.strip():
            return {"montage_candidates": [], "highlights_candidates": []}

        clean = re.sub(r"^```[a-z]*\s*", "", raw_text.strip())
        clean = re.sub(r"\s*```$", "", clean).strip()
        s, e = clean.find("{"), clean.rfind("}")
        if s == -1 or e <= s:
            return {"montage_candidates": [], "highlights_candidates": []}

        try:
            data = json.loads(clean[s:e + 1])
        except json.JSONDecodeError:
            logger.warning("Extras candidates: JSONDecodeError")
            return {"montage_candidates": [], "highlights_candidates": []}

        montage_candidates = []
        for item in (data.get("montage_candidates", []) or [])[:3]:
            if not isinstance(item, dict):
                continue
            theme = _clean_field(str(item.get("theme", "")))
            clip_title = _clean_field(str(item.get("title", "")))
            raw_tags = item.get("hashtags", [])
            hashtags = [
                str(t).strip().lstrip("#")
                for t in (raw_tags if isinstance(raw_tags, list) else [])
                if str(t).strip()
            ][:4]

            frags_raw = item.get("fragments", [])
            if not isinstance(frags_raw, list) or len(frags_raw) < 3:
                continue

            parsed_frags = []
            total_dur = 0
            prev_start = None

            for frag in frags_raw:
                if not isinstance(frag, dict):
                    continue
                ss = time_to_seconds(str(frag.get("start", "")).strip())
                ee = time_to_seconds(str(frag.get("end", "")).strip())
                if ss is None or ee is None or ee <= ss or ee > duration + 5:
                    continue
                fd = ee - ss
                if fd < 7 or fd > 30:
                    continue
                if prev_start is not None and abs(ss - prev_start) < 90:
                    continue
                parsed_frags.append({
                    "start_seconds": ss,
                    "end_seconds": ee,
                    "summary": _clean_field(str(frag.get("summary", ""))),
                })
                prev_start = ss
                total_dur += fd

            if len(parsed_frags) >= 3 and 45 <= total_dur <= 100:
                montage_candidates.append({
                    "theme": theme,
                    "title": clip_title[:120],
                    "hashtags": hashtags,
                    "fragments": parsed_frags,
                    "total_dur": total_dur,
                })

        highlights_candidates = []
        hl = data.get("highlights", {})
        if isinstance(hl, dict):
            clip_title = _clean_field(str(hl.get("title", "")))
            raw_tags = hl.get("hashtags", [])
            hashtags = [
                str(t).strip().lstrip("#")
                for t in (raw_tags if isinstance(raw_tags, list) else [])
                if str(t).strip()
            ][:4]

            frags_raw = hl.get("fragments", [])
            parsed_frags = []
            total_dur = 0

            if isinstance(frags_raw, list):
                for frag in frags_raw:
                    if not isinstance(frag, dict):
                        continue
                    ss = time_to_seconds(str(frag.get("start", "")).strip())
                    ee = time_to_seconds(str(frag.get("end", "")).strip())
                    if ss is None or ee is None or ee <= ss or ee > duration + 5:
                        continue
                    fd = ee - ss
                    if fd < 4 or fd > 18:
                        continue
                    parsed_frags.append({
                        "start_seconds": ss,
                        "end_seconds": ee,
                        "hook": _clean_field(str(frag.get("hook", ""))),
                    })
                    total_dur += fd

            if len(parsed_frags) >= 4 and 50 <= total_dur <= 100:
                highlights_candidates.append({
                    "title": clip_title[:120],
                    "hashtags": hashtags,
                    "fragments": parsed_frags,
                    "total_dur": total_dur,
                })

        logger.info(
            f"Extras candidates: montage={len(montage_candidates)} "
            f"highlights={len(highlights_candidates)}"
        )
        return {
            "montage_candidates": montage_candidates,
            "highlights_candidates": highlights_candidates,
        }

    except Exception as e:
        logger.warning(f"create_extras_candidates error: {type(e).__name__}: {e}")
        return {"montage_candidates": [], "highlights_candidates": []}


def build_montage_caption(
    theme: str, title: str, performer: str, real_author: str,
    format_name: str, fragment_count: int, hashtags: list[str],
    yt_url: str = "", vk_url: str = "", rutube_url: str = "",
) -> str:
    author_label = real_author or performer or ""
    title_tc     = title_case_fragment(title) if title else ""
    first_line   = f"{title_tc} - {author_label}" if author_label else title_tc
    context      = f"Тематическая подборка: {theme}" if theme else ""
    links_block  = _build_links_block(yt_url, rutube_url, vk_url)
    tags_line    = " ".join(f"#{t}" for t in hashtags[:4]) if hashtags else ""
    parts = [p for p in [first_line, context, links_block, tags_line] if p]
    return "\n\n".join(parts)


def build_highlights_caption(
    title: str, performer: str, real_author: str, format_name: str,
    fragment_count: int, hashtags: list[str],
    yt_url: str = "", vk_url: str = "", rutube_url: str = "",
) -> str:
    author_label = real_author or performer or ""
    title_tc     = title_case_fragment(title) if title else ""
    first_line   = f"{title_tc} - {author_label}" if author_label else title_tc
    context      = f"Лучшие моменты ({fragment_count} фрагментов)"
    links_block  = _build_links_block(yt_url, rutube_url, vk_url)
    tags_line    = " ".join(f"#{t}" for t in hashtags[:4]) if hashtags else ""
    parts = [p for p in [first_line, context, links_block, tags_line] if p]
    return "\n\n".join(parts)


async def _run_montage_or_highlights_pipeline(
    cand: dict, video_path: Path, media_id: str, prefix: str,
    ai_data: dict, performer: str, url: str, rutube_url: str, vk_url: str,
    update, caption_fn,
) -> bool:
    """Общий рендер-pipeline для montage и highlights."""
    format_name  = (ai_data or {}).get("format", "other") or "other"
    real_author  = (ai_data or {}).get("real_author", "") or performer or ""
    visual_mode  = get_shorts_visual_mode(format_name)
    do_normalize = await asettings_get("shorts_audio_normalize")
    do_snapshot  = await asettings_get("shorts_snapshot")
    do_subtitles = await asettings_get("shorts_subtitles")
    do_poster    = await asettings_get("shorts_title_poster")
    speed        = float(shorts_speed_get())

    raw_path      = DOWNLOAD_DIR / f"{media_id}_{prefix}_raw.mp4"
    post_path     = DOWNLOAD_DIR / f"{media_id}_{prefix}_post.mp4"
    sub_path      = DOWNLOAD_DIR / f"{media_id}_{prefix}_sub.mp4"
    snapshot_path = DOWNLOAD_DIR / f"{media_id}_{prefix}_snap.jpg"
    poster_path   = DOWNLOAD_DIR / f"{media_id}_{prefix}_poster.jpg"
    cleanup_paths = [raw_path, post_path, sub_path, snapshot_path, poster_path]

    try:
        ok = await render_montage_short(
            source_video_path=video_path, output_path=raw_path,
            fragments=cand["fragments"], visual_mode=visual_mode,
        )
        if not ok:
            return False
        size_mb = raw_path.stat().st_size / (1024 * 1024) if raw_path.exists() else 0
        if size_mb > 50:
            logger.warning(f"{prefix}: файл {size_mb:.1f}MB > 50MB, пропускаем")
            return False

        need_post = do_normalize or (abs(speed - 1.0) > 0.01)
        current_path = raw_path
        if need_post:
            post_ok = await postprocess_short(raw_path, post_path, normalize_audio=do_normalize, speed=speed)
            if post_ok:
                current_path = post_path

        if do_subtitles and HAS_FASTER_WHISPER:
            try:
                segments = await transcribe_short_clip(current_path, ai_data=ai_data)
                if segments:
                    sub_ok = await burn_subtitles_into_short(current_path, sub_path, segments)
                    if sub_ok:
                        current_path = sub_path
            except Exception as sub_err:
                logger.warning(f"{prefix}: subtitle error: {sub_err}")

        thumb_buf = None
        total_dur = cand["total_dur"]
        if do_poster:
            try:
                if await create_short_title_poster(current_path, poster_path, cand["title"], total_dur) and poster_path.exists():
                    thumb_buf = BytesIO(poster_path.read_bytes())
                    thumb_buf.name = poster_path.name
            except Exception:
                pass
        if thumb_buf is None and do_snapshot:
            try:
                if await create_short_snapshot(current_path, snapshot_path, total_dur) and snapshot_path.exists():
                    thumb_buf = BytesIO(snapshot_path.read_bytes())
                    thumb_buf.name = snapshot_path.name
            except Exception:
                pass

        caption = caption_fn()
        if len(caption) > 1024:
            caption = caption[:1021] + "..."

        try:
            with open(current_path, "rb") as vf:
                await update.message.reply_video(
                    video=vf, caption=caption, duration=int(total_dur),
                    width=720, height=1280, thumbnail=thumb_buf,
                    parse_mode="HTML",
                    write_timeout=120, read_timeout=120, connect_timeout=30,
                )
            return True
        except Exception as send_err:
            logger.warning(f"{prefix}: ошибка отправки: {send_err}")
            return False
        finally:
            if thumb_buf:
                try: thumb_buf.close()
                except Exception: pass
    finally:
        for p in cleanup_paths:
            try: p.unlink(missing_ok=True)
            except Exception: pass


async def process_and_send_montage(
    url: str, media_id: str, mp3_path: Path, title: str, performer: str,
    duration: int, ai_data: dict, update,
    existing_audio_part=None, existing_client=None,
    rutube_url: str = "", vk_url: str = "",
    prefetched_candidates: list[dict] | None = None,
) -> None:
    video_path, owned_video = None, False
    _has_highlights = await asettings_get("shorts_highlights")
    try:
        candidates = prefetched_candidates or []
        if not candidates:
            logger.info("Montage: кандидаты не найдены")
            try:
                await update.message.reply_text("🎬 Montage для этого материала не найден.")
            except Exception:
                pass
            return
        video_path = await download_video_for_shorts(url, media_id)
        if not video_path:
            logger.warning("Montage: не удалось скачать видео")
            return
        owned_video = True
        real_author = (ai_data or {}).get("real_author", "") or performer or ""
        format_name = (ai_data or {}).get("format", "other") or "other"
        sent = 0
        for i, cand in enumerate(candidates, 1):
            logger.info(f"Montage: рендер {i}/{len(candidates)} '{cand['title']}'")
            ok = await _run_montage_or_highlights_pipeline(
                cand=cand, video_path=video_path, media_id=media_id,
                prefix=f"montage_{i}", ai_data=ai_data, performer=performer,
                url=url, rutube_url=rutube_url, vk_url=vk_url, update=update,
                caption_fn=lambda c=cand: build_montage_caption(
                    theme=c["theme"], title=c["title"], performer=performer,
                    real_author=real_author, format_name=format_name,
                    fragment_count=len(c["fragments"]), hashtags=c["hashtags"],
                    yt_url=url, vk_url=vk_url, rutube_url=rutube_url,
                ),
            )
            if ok:
                sent += 1
        logger.info(f"Montage: итого отправлено {sent}/{len(candidates)}")
    except Exception as e:
        logger.warning(f"Montage process_and_send error: {e}")
    finally:
        if video_path and owned_video:
            try:
                if not _has_highlights:
                    video_path.unlink(missing_ok=True)
            except Exception:
                pass


async def process_and_send_highlights(
    url: str, media_id: str, mp3_path: Path, title: str, performer: str,
    duration: int, ai_data: dict, update,
    existing_audio_part=None, existing_client=None,
    rutube_url: str = "", vk_url: str = "",
    prefetched_candidates: list[dict] | None = None,
) -> None:
    video_path, owned_video = None, False
    try:
        candidates = prefetched_candidates or []
        if not candidates:
            logger.info("Highlights: кандидат не найден")
            try:
                await update.message.reply_text("🌟 Highlights для этого материала не найден.")
            except Exception:
                pass
            return
        video_path = await download_video_for_shorts(url, media_id)
        if not video_path:
            logger.warning("Highlights: не удалось скачать видео")
            return
        owned_video = True
        real_author = (ai_data or {}).get("real_author", "") or performer or ""
        format_name = (ai_data or {}).get("format", "other") or "other"
        cand = candidates[0]
        logger.info(f"Highlights: рендер '{cand['title']}'")
        await _run_montage_or_highlights_pipeline(
            cand=cand, video_path=video_path, media_id=media_id,
            prefix="highlights", ai_data=ai_data, performer=performer,
            url=url, rutube_url=rutube_url, vk_url=vk_url, update=update,
            caption_fn=lambda c=cand: build_highlights_caption(
                title=c["title"], performer=performer, real_author=real_author,
                format_name=format_name, fragment_count=len(c["fragments"]),
                hashtags=c["hashtags"], yt_url=url, vk_url=vk_url, rutube_url=rutube_url,
            ),
        )
    except Exception as e:
        logger.warning(f"Highlights process_and_send error: {e}")
    finally:
        if video_path and owned_video:
            try: video_path.unlink(missing_ok=True)
            except Exception: pass


# ─── Прогресс-бар ────────────────────────────────────────────

PROGRESS_STEPS = [
    (10,  "🔍 Получаю информацию..."),
    (20,  "🖼  Скачиваю обложку..."),
    (40,  "📥 Скачиваю аудио..."),
    (60,  "🧠 AI анализирует материал..."),
    (80,  "📝 Собираю разбор и ссылки..."),
    (100, "📤 Отправляю аудиофайл..."),
]

def _progress_bar(pct: int) -> str:
    filled = pct // 10
    return "🟦" * filled + "⬜" * (10 - filled) + f" {pct}%"

async def set_progress(status_msg, step: int, info: dict | None = None, prefix: str = ""):
    lines = []
    if prefix:
        lines.append(prefix.strip())
    for i, (pct, label) in enumerate(PROGRESS_STEPS):
        if i < step - 1:
            lines.append(f"✅ {label.rstrip('.')}")
        elif i == step - 1:
            lines.append(f"⏳ {label}")
            lines.append(_progress_bar(pct))
            break
    if info:
        lines.append("")
        for val in info.values():
            if val:
                lines.append(val)
    try:
        await status_msg.edit_text("\n".join(lines))
    except Exception:
        pass


# ─── Обработка одного видео ───────────────────────────────────

async def process_single_video(url, update, status_msg=None, progress_prefix="", context=None):
    thumb_buffer = None
    _pp = progress_prefix  # short alias for progress calls
    used_audio_part = None  # инициализируем здесь — finally обращается к ним безусловно
    used_client = None       # если исключение до их присвоения внутри try — UnboundLocalError
    try:
        if status_msg:
            await set_progress(status_msg, 1, prefix=_pp)
        else:
            status_msg = await update.message.reply_text("⏳ Обрабатываю...")
            await set_progress(status_msg, 1, prefix=_pp)

        info_cmd = YTDLP_BASE_ARGS + [
            "--dump-json", "--no-playlist", url,
        ]
        info_proc = await asyncio.get_running_loop().run_in_executor(
            None, lambda: subprocess.run(info_cmd, capture_output=True, text=True, timeout=60)
        )
        if info_proc.returncode != 0:
            raise Exception(info_proc.stderr[-500:] if info_proc.stderr else "yt-dlp info error")
        info_dict = None
        for line in info_proc.stdout.strip().splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    info_dict = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if not info_dict:
            raise Exception("Не удалось получить метаданные видео")

        full_title   = info_dict.get("title", "audio")
        channel_name = info_dict.get("uploader", info_dict.get("channel", ""))
        logger.info(f"YouTube channel_name: '{channel_name}'")
        duration     = info_dict.get("duration", 0)
        media_id     = info_dict.get("id", "media")
        performer, title = parse_title(full_title, channel_name)

        # Проверяем кэш — если видео уже обработано и кэш актуален, отдаём результат сразу
        cached = await adb_get(media_id)
        cache_ok, cache_reason = is_cache_valid(cached)

        # Страховка от "грязного кэша" — если в ai_data есть мусор, пересчитываем
        if cache_ok and cached:
            dirty = False
            c_ai = cached.get("ai_data") or {}
            for field in ("main_topic", "real_author", "real_title", "real_event"):
                if _has_dirty_meta(c_ai.get(field, "")):
                    dirty = True
                    break
            if not dirty:
                for q in (cached.get("questions") or []):
                    if _has_dirty_meta(str(q)):
                        dirty = True
                        break
            if dirty:
                cache_ok = False
                cache_reason = "dirty_meta"
                logger.info(f"Кэш skip: {media_id} reason=dirty_meta")

        if cache_ok and cached.get("ai_data"):
            logger.info(f"Кэш hit: {media_id} — отдаём без Gemini")
            await set_progress(status_msg, 6, prefix=_pp)
            c_tg   = cached["telegraph_url"]
            c_qtg  = cached.get("quotes_tg_url", "")
            c_qqtg = cached.get("questions_tg_url", "")
            _c_study      = cached.get("study_tg_url") or ""
            _c_reflection = cached.get("reflection_tg_url") or ""
            # Термины скрываем если обе новые страницы есть
            c_ttg  = "" if (_c_study and _c_reflection) else (cached.get("terms_tg_url") or "")
            # Обложка
            thumb_buffer = None
            try:
                _existing_thumb = list(THUMBS_DIR.glob(f"{media_id}_thumb*"))
                if _existing_thumb:
                    thumb_buffer = prepare_thumbnail(_existing_thumb[0])
                    logger.info(f"Обложка кэш (hit): используем {_existing_thumb[0].name}")
                else:
                    thumb_cmd = YTDLP_BASE_ARGS + [
                        "--skip-download", "--write-thumbnail", "--no-playlist",
                        "--convert-thumbnails", "jpg",
                        "--output", str(THUMBS_DIR / f"{media_id}_thumb.%(ext)s"), url,
                    ]
                    _thumb_proc = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: subprocess.run(thumb_cmd, capture_output=True, text=True, timeout=30)
                    )
                    if _thumb_proc.returncode != 0:
                        _thumb_err = (_thumb_proc.stderr or "").strip()[-300:]
                        logger.warning(f"Обложка (кэш) yt-dlp rc={_thumb_proc.returncode}: {_thumb_err}")
                    thumb_candidates = list(THUMBS_DIR.glob(f"{media_id}_thumb*"))
                    if thumb_candidates:
                        thumb_buffer = prepare_thumbnail(thumb_candidates[0])
                        logger.info(f"Обложка (кэш) скачана: {thumb_candidates[0].name}")
                    else:
                        logger.warning(f"Обложка (кэш): файл не найден после yt-dlp (rc={_thumb_proc.returncode})")
            except Exception as e:
                logger.warning(f"Обложка (кэш) не скачана: {e}")
            # Скачиваем аудио
            # Если длительность > 40 минут — сразу качаем в 64kbps
            audio_quality = "64K" if duration > 2400 else "128K"
            mp3_path = DOWNLOAD_DIR / f"{media_id}.mp3"
            if not mp3_path.exists():
                dl_cmd = YTDLP_BASE_ARGS + [
                    "--extract-audio", "--audio-format", "mp3", "--audio-quality", audio_quality,
                    "--no-playlist", "--output", str(DOWNLOAD_DIR / f"{media_id}.%(ext)s"), url,
                ]
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: subprocess.run(dl_cmd, capture_output=True, timeout=1800))
            if not mp3_path.exists():
                raise Exception("Не удалось скачать аудио")
            file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
            bitrate = "64" if duration > 2400 else "128"
            logger.info(f"Кэш аудио: {mp3_path.name} = {file_size_mb:.1f} MB")
            # Сжимаем если больше лимита
            if file_size_mb > MAX_FILE_SIZE_MB:
                mp3_64_path = DOWNLOAD_DIR / f"{media_id}_64.mp3"
                ffmpeg = shutil.which("ffmpeg")
                if ffmpeg:
                    await asyncio.get_running_loop().run_in_executor(
                        None, lambda: subprocess.run(
                            [ffmpeg, "-i", str(mp3_path), "-b:a", "64k", "-y", str(mp3_64_path)],
                            capture_output=True, timeout=300))
                    if mp3_64_path.exists():
                        mp3_path.unlink(missing_ok=True)
                        mp3_path = mp3_64_path
                        file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
                        bitrate = "64"
                if file_size_mb > MAX_FILE_SIZE_MB:
                    await update.message.reply_text(f"⚠️ Файл слишком большой ({file_size_mb:.1f} МБ) даже после сжатия.")
                    cleanup_files(media_id)
                    return False
            # Ищем альт-ссылки: берём из кэша если сохранены, иначе делаем запрос
            _cached_rutube = (cached or {}).get("rutube_url", "") or ""
            _cached_vk     = (cached or {}).get("vk_url", "") or ""
            if _cached_rutube or _cached_vk:
                rutube_url = _cached_rutube
                vk_url     = _cached_vk
                logger.info(f"Кэш alt-links: rutube={bool(rutube_url)} vk={bool(vk_url)}")
            else:
                _cache_search_title = _build_search_title(c_ai, full_title)
                alt_links = await find_alternative_links(_cache_search_title, channel_name, duration, ai_data=c_ai, fallback_title=full_title)
                rutube_url = (alt_links or {}).get("rutube") or ""
                vk_url     = (alt_links or {}).get("vk") or ""

            def _build_c(data, **kw):
                return build_caption(performer, title, duration, file_size_mb,
                                     data, bitrate, url, c_tg, rutube_url, vk_url,
                                     c_qtg, c_qqtg, c_ttg,
                                     study_tg_url=_c_study,
                                     reflection_tg_url=_c_reflection,
                                     **kw)

            def _build_full_c(data):
                return build_caption(performer, title, duration, file_size_mb,
                                     data, bitrate, url, c_tg, rutube_url, vk_url,
                                     c_qtg, c_qqtg, c_ttg,
                                     study_tg_url=_c_study,
                                     reflection_tg_url=_c_reflection,
                                     full_mode=True)

            _c_fmt    = (c_ai or {}).get("format", "other")
            _ts_limit = get_caption_timestamp_limit(_c_fmt)
            # Применяем format-лимит к таймкодам сразу (до проверки переполнения)
            _ai_for_caption = c_ai
            if c_ai and c_ai.get("timestamps"):
                ts_limited = _trim_timestamps(c_ai["timestamps"], _ts_limit)
                if ts_limited != c_ai["timestamps"]:
                    _ai_for_caption = {**c_ai, "timestamps": ts_limited}
            caption = _build_c(_ai_for_caption)
            # Умное обрезание до 1024 видимых символов (без HTML-тегов)
            _cap_ts_str = (_ai_for_caption or {}).get("timestamps", "") or ""  # отслеживаем фактические ts в caption
            # Шаг 1: убрать хэштеги
            if visible_length(caption) > 1024 and _ai_for_caption and _ai_for_caption.get("hashtags"):
                caption = _build_c({**_ai_for_caption, "hashtags": ""})
            # Шаг 1.5: убрать main_topic
            if visible_length(caption) > 1024 and _ai_for_caption and _ai_for_caption.get("main_topic"):
                caption = _build_c({**_ai_for_caption, "hashtags": "", "main_topic": ""})
            # Шаг 2: сократить таймкоды ещё (половина лимита)
            if visible_length(caption) > 1024 and _ai_for_caption and _ai_for_caption.get("timestamps"):
                ts_half = _trim_timestamps(_ai_for_caption["timestamps"], max(_ts_limit // 2, 3))
                caption = _build_c({**_ai_for_caption, "hashtags": "", "timestamps": ts_half})
                _cap_ts_str = ts_half
            # Шаг 3: убрать таймкоды полностью
            if visible_length(caption) > 1024 and _ai_for_caption and _ai_for_caption.get("timestamps"):
                caption = _build_c({**_ai_for_caption, "hashtags": "", "timestamps": ""})
                _cap_ts_str = ""
            # Шаг 4: последний резерв — шапка + ссылки, обрезаем без лома тегов
            if visible_length(caption) > 1024:
                platform_block = build_platform_links(url, rutube_url, vk_url)
                tg_block = build_telegraph_links(
                    c_tg or "", c_qtg or "", c_qqtg or "",
                    c_ttg,
                    study_tg_url=_c_study,
                    reflection_tg_url=_c_reflection,
                )
                footer = "\n".join(filter(None, [platform_block, tg_block]))
                header = "\n".join(caption.split("\n")[:4])
                caption = header + ("\n\n" + footer if footer else "")
                caption = safe_trim_caption(caption, 1024)
                _cap_ts_str = ""
            _ts_in_cap = len([l for l in _cap_ts_str.split("\n") if l.strip()])
            logger.info(f"Кэш caption visible_len={visible_length(caption)} format={_c_fmt} ts_limit={_ts_limit} ts_in_cap={_ts_in_cap}")
            with open(mp3_path, "rb") as af:
                _title_c     = (c_ai or {}).get("real_title") or title
                _performer_c = (c_ai or {}).get("real_author") or performer
                for _attempt in range(3):
                    try:
                        af.seek(0)
                        await update.message.reply_audio(
                            audio=af, title=_title_c, performer=_performer_c,
                            thumbnail=thumb_buffer, duration=duration,
                            caption=caption, parse_mode="HTML",
                            write_timeout=180, read_timeout=180, connect_timeout=60,
                        )
                        break
                    except Exception as upload_err:
                        err_name = type(upload_err).__name__
                        err_str  = str(upload_err).lower()
                        _retryable_names = ("Timeout", "NetworkError", "TimedOut", "ReadError", "ConnectError")
                        _retryable_strs  = ("internal server error", "server error", "bad gateway", "gateway timeout")
                        _is_retryable = (
                            any(x in err_name for x in _retryable_names) or
                            any(x in err_str  for x in _retryable_strs)
                        )
                        if _attempt < 2 and _is_retryable:
                            logger.warning(f"Кэш upload попытка {_attempt+1}/3 ({err_name}: {str(upload_err)[:120]}), повтор...")
                            await asyncio.sleep(5 * (_attempt + 1))
                        else:
                            raise
            # Отправляем полное описание отдельным сообщением (кэш-hit)
            _feat_full_c = await asettings_get("caption_full_text")
            if _feat_full_c and c_ai:
                full_caption_c = _build_full_c(c_ai)
                if visible_length(full_caption_c) > 4096:
                    full_caption_c = safe_trim_caption(full_caption_c, 4096)
                if full_caption_c and full_caption_c != caption:
                    try:
                        await update.message.reply_text(full_caption_c, parse_mode="HTML")
                    except Exception as _fe:
                        logger.warning(f"Кэш full caption send error: {_fe}")

            # ── PDF (кэш-hit, опционально) ────────────────────────────
            _feat_pdf_c = await asettings_get("generate_pdf")
            if _feat_pdf_c and c_ai:
                _pdf_status = None
                _pdf_path_c = None
                try:
                    from pdf_generator import generate_sermon_pdf_async

                    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
                    _pdf_path_c = str(DOWNLOAD_DIR / f"{media_id}_{uuid.uuid4().hex[:6]}.pdf")

                    _raw_t_c = (c_ai.get("real_title") or "").strip() or (title or "").strip()
                    _raw_a_c = (c_ai.get("real_author") or "").strip() or (performer or "").strip()
                    _pdf_title_c = normalize_title_text(_raw_t_c) if _raw_t_c else "Без названия"
                    _pdf_author_c = normalize_author_name(_raw_a_c) if _raw_a_c else "Неизвестный"
                    _dur_str_c = format_timestamp(duration) if duration else ""

                    _pdf_urls_c = {}
                    if c_tg: _pdf_urls_c["synopsis"] = c_tg
                    if _c_study: _pdf_urls_c["study"] = _c_study
                    if _c_reflection: _pdf_urls_c["reflection"] = _c_reflection
                    if c_ttg: _pdf_urls_c["terms"] = c_ttg

                    if _pdf_urls_c:
                        logger.info(f"PDF (кэш): генерирую ({list(_pdf_urls_c.keys())})")
                        _pdf_status = await update.message.reply_text("📄 Генерирую PDF…")

                        async def _pdf_progress_c(stage: str, pct: int):
                            try:
                                await _pdf_status.edit_text(f"📄 PDF: {stage} ({pct}%)")
                            except Exception:
                                pass

                        _pdf_result_c = await generate_sermon_pdf_async(
                            output_path=_pdf_path_c,
                            title=_pdf_title_c,
                            performer=_pdf_author_c,
                            duration_str=_dur_str_c,
                            urls=_pdf_urls_c,
                            progress_callback=_pdf_progress_c,
                        )

                        if _pdf_result_c and Path(_pdf_result_c).exists():
                            _size_c = Path(_pdf_result_c).stat().st_size
                            if 200 < _size_c < 49 * 1024 * 1024:
                                def _safe_fn_c(s: str) -> str:
                                    return re.sub(r'[\\/:*?"<>|\n\r\t]', '', s).strip()[:80] or "doc"
                                _fn_c = f"{_safe_fn_c(_pdf_author_c)} — {_safe_fn_c(_pdf_title_c)}.pdf"
                                with open(_pdf_result_c, "rb") as _pdf_f:
                                    await update.message.reply_document(
                                        document=_pdf_f,
                                        filename=_fn_c,
                                        caption="📄 <b>PDF-версия материала</b>",
                                        parse_mode="HTML",
                                    )
                                logger.info("PDF (кэш): отправлен")

                except ImportError:
                    pass  # pdf_generator не установлен — тихо пропускаем
                except Exception as _pdf_err_c:
                    logger.warning(f"PDF (кэш) не удался: {_pdf_err_c}")
                finally:
                    if _pdf_path_c:
                        try:
                            Path(_pdf_path_c).unlink(missing_ok=True)
                        except Exception:
                            pass
                    if _pdf_status:
                        try:
                            await _pdf_status.delete()
                        except Exception:
                            pass

            cleanup_files(media_id)
            logger.info(f"Кэш hit: {media_id}")
            return True
            logger.info(f"Кэш skip: {media_id} reason={cache_reason}")

        await set_progress(status_msg, 2, {'title': f'📝 {title}', 'author': f'👤 {performer}  •  {duration//60}:{duration%60:02d}'}, prefix=_pp)
        try:
            # Проверяем кэш обложки — если файл уже есть, не скачиваем
            _existing_thumb = list(THUMBS_DIR.glob(f"{media_id}_thumb*"))
            if _existing_thumb:
                thumb_buffer = prepare_thumbnail(_existing_thumb[0])
                logger.info(f"Обложка кэш: используем {_existing_thumb[0].name}")
            else:
                thumb_cmd = YTDLP_BASE_ARGS + [
                    "--skip-download", "--write-thumbnail", "--no-playlist",
                    "--convert-thumbnails", "jpg",
                    "--output", str(THUMBS_DIR / f"{media_id}_thumb.%(ext)s"),
                    url,
                ]
                _thumb_proc = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: subprocess.run(thumb_cmd, capture_output=True, text=True, timeout=30)
                )
                if _thumb_proc.returncode != 0:
                    _thumb_err = (_thumb_proc.stderr or "").strip()[-300:]
                    logger.warning(f"Обложка yt-dlp returncode={_thumb_proc.returncode}: {_thumb_err}")
                thumb_candidates = list(THUMBS_DIR.glob(f"{media_id}_thumb*"))
                if thumb_candidates:
                    thumb_buffer = prepare_thumbnail(thumb_candidates[0])
                    logger.info(f"Обложка скачана: {thumb_candidates[0].name}")
                else:
                    logger.warning(f"Обложка: файл не найден после yt-dlp (rc={_thumb_proc.returncode})")
        except Exception as e:
            logger.warning(f"Обложка не скачана: {e}")

        await set_progress(status_msg, 3, {'title': f'📝 {title}', 'author': f'👤 {performer}  •  {duration//60}:{duration%60:02d}', 'fmt': '🎵 128 kbps  •  MP3'}, prefix=_pp)

        # Проверяем кэш MP3 — если файл уже есть, скачивание пропускаем
        mp3_path = DOWNLOAD_DIR / f"{media_id}.mp3"
        _existing_mp3 = list(DOWNLOAD_DIR.glob(f"{media_id}*.mp3"))
        if _existing_mp3:
            mp3_path = _existing_mp3[0]
            logger.info(f"MP3 кэш: используем существующий файл {mp3_path.name}")
        else:
            _audio_quality_dl = "64K" if duration > 2400 else "128K"
            audio_cmd = YTDLP_BASE_ARGS + [
                "--format", "bestaudio/best",
                "--extract-audio", "--audio-format", "mp3", "--audio-quality", _audio_quality_dl,
                "--no-playlist",
                "--output", str(DOWNLOAD_DIR / f"{media_id}.%(ext)s"),
                url,
            ]
            proc = await asyncio.get_running_loop().run_in_executor(
                None, lambda: subprocess.run(audio_cmd, capture_output=True, text=True, timeout=600)
            )
            if proc.returncode != 0:
                raise Exception(proc.stderr[-500:] if proc.stderr else "yt-dlp error")

            mp3_path = DOWNLOAD_DIR / f"{media_id}.mp3"
            if not mp3_path.exists():
                mp3_files = list(DOWNLOAD_DIR.glob(f"{media_id}*.mp3"))
                if mp3_files:
                    mp3_path = mp3_files[0]
                else:
                    raise FileNotFoundError("MP3 файл не найден")

        file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
        if file_size_mb > MAX_FILE_SIZE_MB:
            await set_progress(status_msg, 3, {"title": f"📝 {title}", "info": f"⚙️ Файл {file_size_mb:.1f} МБ — пересжимаю в 64 kbps..."}, prefix=_pp)
            mp3_64_path = DOWNLOAD_DIR / f"{media_id}_64.mp3"
            # Re-encode existing mp3 via ffmpeg directly
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg:
                proc = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: subprocess.run(
                        [ffmpeg, "-i", str(mp3_path), "-b:a", "64k", "-y", str(mp3_64_path)],
                        capture_output=True, timeout=300
                    )
                )
                if mp3_64_path.exists():
                    mp3_path.unlink(missing_ok=True)
                    mp3_path = mp3_64_path
                    file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
            if file_size_mb > MAX_FILE_SIZE_MB:
                await update.message.reply_text(
                    f"⚠️ Файл слишком большой ({file_size_mb:.1f} МБ) даже после сжатия до 64 kbps.\n"
                    f"Telegram принимает максимум 50 МБ. Попробуйте более короткое видео."
                )
                cleanup_files(media_id)
                return False

        bitrate = "64" if ("_64" in mp3_path.name or duration > 2400) else "128"

        ai_data          = None
        synopsis_outline = None  # инициализируем здесь — closure-функции ниже ссылаются безусловно
        telegraph_url    = None
        used_audio_part  = None
        used_client      = None
        _early_alt  = None  # alt-links ищем до конспекта если ai_data есть
        _pre_rutube = ""    # инициализируем здесь — closure-функции ниже всегда могут обращаться к ним
        _pre_vk     = ""    # даже если ai_data = None (Gemini недоступен или упал)
        if GEMINI_CLIENTS:
            _fmb = mp3_path.stat().st_size / (1024*1024)
            await set_progress(status_msg, 4, {"title": f"📝 {title}", "author": f"👤 {performer}  •  {duration//60}:{duration%60:02d}", "size": f"🎵 {_fmb:.1f} МБ  •  {bitrate} kbps"}, prefix=_pp)
            ai_data, used_client, used_audio_part = await gemini_analyze_audio(
                mp3_path, title, performer, duration, status_msg, progress_prefix
            )
            if ai_data:
                await set_progress(status_msg, 5, prefix=_pp)

                # Ищем alt-links ДО публикации конспекта — чтобы таймкоды
                # RuTube/VK были доступны уже при первой публикации страницы
                _early_search_title = _build_search_title(ai_data, full_title)
                try:
                    _early_alt = await find_alternative_links(
                        _early_search_title, channel_name, duration,
                        ai_data=ai_data, fallback_title=full_title
                    )
                except Exception as _ea_err:
                    logger.warning(f"early alt-links error: {_ea_err}")
                    _early_alt = None
                _pre_rutube = (_early_alt or {}).get("rutube") or ""
                _pre_vk     = (_early_alt or {}).get("vk")     or ""

                if await asettings_get("synopsis"):
                    telegraph_url, synopsis_outline = await create_telegraph_synopsis(
                        mp3_path, title, performer, duration, url,
                        existing_audio_part=used_audio_part,
                        existing_client=used_client,
                        ai_data=ai_data,
                        rutube_url=_pre_rutube,
                        vk_url=_pre_vk,
                    )
                else:
                    telegraph_url    = None
                    synopsis_outline = None
            # НЕ удаляем audio_part здесь — он нужен для Shorts и Clips.
            # Удаление происходит в блоке finally ниже.

        await set_progress(status_msg, 6, prefix=_pp)
        search_title = _build_search_title(ai_data, full_title)
        tg_author    = normalize_author_name((ai_data or {}).get("real_author") or performer) or performer
        _questions   = (ai_data or {}).get("questions", []) if ai_data else []
        _terms_data  = (ai_data or {}).get("terms_data", {}) or {} if ai_data else {}

        # -- Читаем настройки одним batch вызовом -------------------------
        _all_settings                = await asettings_get_all()
        _feat_analytics              = _all_settings.get("analytics", True)
        _feat_questions              = _all_settings.get("questions", True)
        _feat_terms                  = _all_settings.get("terms", True)
        _feat_study_analysis         = _all_settings.get("study_analysis", True)
        _feat_reflection_application = _all_settings.get("reflection_application", True)
        _mat_format   = (ai_data or {}).get("format", "other")
        _ts_total     = len([l for l in ((ai_data or {}).get("timestamps") or "").split("\n") if l.strip()])
        _ts_cap_limit = get_caption_timestamp_limit(_mat_format)

        _terms_total = sum(len(_terms_data.get(k, [])) for k in ("concepts", "scripture", "translations", "lexicon_notes"))
        _is_qa = (_mat_format == "qa")
        logger.info(
            f"format={_mat_format} is_qa={_is_qa} ts_total={_ts_total} ts_cap={_ts_cap_limit} "
            f"synopsis={'ok' if telegraph_url else 'none'} "
            f"terms_items={_terms_total} "
            f"(concepts={len(_terms_data.get('concepts',[]))} scripture={len(_terms_data.get('scripture',[]))} "
            f"transl={len(_terms_data.get('translations',[]))} lex={len(_terms_data.get('lexicon_notes',[]))}) "
            f"| feat: analytics={_feat_analytics} questions={_feat_questions} terms={_feat_terms} "
            f"study_analysis={_feat_study_analysis} reflection={_feat_reflection_application}"
        )
        async def _noop():
            return None

        # -- Pipeline factories с fallback ----------------------------------

        async def _analytics_pipeline():
            # Пропускаем legacy Аналитику если новая Study Analysis включена
            if _feat_study_analysis:
                return None   # Study Analysis заменяет Аналитику
            if not _feat_analytics:
                return None
            return await create_telegraph_analytics(ai_data, search_title, tg_author, url)

        async def _questions_pipeline():
            # Пропускаем legacy Вопросы если новое Размышление включено
            if _feat_reflection_application:
                return None   # Reflection заменяет Questions
            if not _feat_questions:
                return None
            return await create_telegraph_questions(_questions, search_title, tg_author)

        async def _terms_pipeline():
            # Термины пропускаем если обе новые страницы включены
            # (они уже включают термины в своих разделах)
            if _feat_study_analysis and _feat_reflection_application:
                return None
            if not _feat_terms:
                return None
            return await create_telegraph_terms(_terms_data, search_title, tg_author, url)

        async def _study_analysis_pipeline():
            if not _feat_study_analysis:
                return None
            compact = lambda: create_telegraph_analytics(ai_data, search_title, tg_author, url)
            logger.info("StudyAnalysis: enabled")
            return await create_telegraph_study_analysis(
                ai_data, search_title, tg_author, yt_url=url, compact_fn=compact,
                rutube_url=_pre_rutube, vk_url=_pre_vk,
                synopsis_outline=synopsis_outline,
            )

        async def _reflection_application_pipeline():
            if not _feat_reflection_application:
                return None
            compact = lambda: create_telegraph_questions(_questions, search_title, tg_author)
            logger.info("ReflectionApplication: enabled")
            return await create_telegraph_reflection_application(
                _questions, search_title, tg_author,
                ai_data=ai_data, duration=duration, yt_url=url,
                compact_fn=compact,
                rutube_url=_pre_rutube, vk_url=_pre_vk,
                synopsis_outline=synopsis_outline,
            )

        async def _alt_links_result():
            # Если alt-links уже нашли до публикации конспекта — не ищем повторно
            if _early_alt is not None and isinstance(_early_alt, dict):
                return _early_alt
            return await find_alternative_links(
                search_title, channel_name, duration,
                ai_data=ai_data, fallback_title=full_title
            )

        # _has_early_alt больше не нужен
        _ = None

        (
            alt_links, quotes_tg, questions_tg, terms_tg,
            study_analysis_tg, reflection_application_tg,
        ) = await asyncio.gather(
            _alt_links_result(),
            _analytics_pipeline(),
            _questions_pipeline(),
            _terms_pipeline(),
            _study_analysis_pipeline(),
            _reflection_application_pipeline(),
            return_exceptions=True,
        )
        if isinstance(alt_links, Exception):
            logger.warning(f"find_alternative_links error: {alt_links}")
            alt_links = {"rutube": None, "vk": None}
        if isinstance(quotes_tg, Exception):
            logger.warning(f"analytics error: {quotes_tg}")
            quotes_tg = None
        if isinstance(questions_tg, Exception):
            logger.warning(f"questions error: {questions_tg}")
            questions_tg = None
        if isinstance(terms_tg, Exception):
            logger.warning(f"terms error: {terms_tg}")
            terms_tg = None
        if isinstance(study_analysis_tg, Exception):
            logger.warning(f"study_analysis error: {study_analysis_tg}")
            study_analysis_tg = None
        if isinstance(reflection_application_tg, Exception):
            logger.warning(f"reflection_application error: {reflection_application_tg}")
            reflection_application_tg = None

        # Новые страницы имеют приоритет над legacy compact в caption
        _q_link     = study_analysis_tg         or quotes_tg    or ""
        _ref_link   = reflection_application_tg or questions_tg or ""
        # Термины скрываем если обе новые страницы есть — они уже включены в Разбор материала
        _terms_link = "" if (study_analysis_tg and reflection_application_tg) else (terms_tg or "")

        rutube_url = alt_links.get("rutube") or ""
        vk_url     = alt_links.get("vk")     or ""

        def _build(data, **kw):
            return build_caption(performer, title, duration, file_size_mb,
                                 data, bitrate, url, telegraph_url, rutube_url, vk_url,
                                 _q_link, _ref_link, _terms_link,
                                 study_tg_url=study_analysis_tg or "",
                                 reflection_tg_url=reflection_application_tg or "",
                                 **kw)

        _ts_limit = get_caption_timestamp_limit(_mat_format)
        # Применяем format-лимит к таймкодам сразу (до проверки переполнения)
        _ai_for_caption = ai_data
        if ai_data and ai_data.get("timestamps"):
            ts_limited = _trim_timestamps(ai_data["timestamps"], _ts_limit)
            if ts_limited != ai_data["timestamps"]:
                _ai_for_caption = {**ai_data, "timestamps": ts_limited}
        caption = _build(_ai_for_caption)
        # Полный текст для отдельного сообщения (лимит 4096) — без обрезки
        # Все таймкоды + полный main_topic + хэштеги
        def _build_full(data):
            return build_caption(performer, title, duration, file_size_mb,
                                 data, bitrate, url, telegraph_url, rutube_url, vk_url,
                                 _q_link, _ref_link, _terms_link,
                                 study_tg_url=study_analysis_tg or "",
                                 reflection_tg_url=reflection_application_tg or "",
                                 full_mode=True)
        full_caption = _build_full(ai_data) if ai_data else caption
        if visible_length(full_caption) > 4096:
            full_caption = safe_trim_caption(full_caption, 4096)
        # Умное обрезание до 1024 видимых символов (без HTML-тегов)
        _cap_ts_str = (_ai_for_caption or {}).get("timestamps", "") or ""  # отслеживаем фактические ts в caption
        # Шаг 1: убрать хэштеги
        if visible_length(caption) > 1024 and _ai_for_caption and _ai_for_caption.get("hashtags"):
            caption = _build({**_ai_for_caption, "hashtags": ""})
        # Шаг 1.5: убрать main_topic
        if visible_length(caption) > 1024 and _ai_for_caption and _ai_for_caption.get("main_topic"):
            caption = _build({**_ai_for_caption, "hashtags": "", "main_topic": ""})
        # Шаг 2: сократить таймкоды ещё сильнее (половина лимита)
        if visible_length(caption) > 1024 and _ai_for_caption and _ai_for_caption.get("timestamps"):
            ts_half = _trim_timestamps(_ai_for_caption["timestamps"], max(_ts_limit // 2, 3))
            caption = _build({**_ai_for_caption, "hashtags": "", "timestamps": ts_half})
            _cap_ts_str = ts_half
        # Шаг 3: убрать таймкоды полностью
        if visible_length(caption) > 1024 and _ai_for_caption and _ai_for_caption.get("timestamps"):
            caption = _build({**_ai_for_caption, "hashtags": "", "timestamps": ""})
            _cap_ts_str = ""
        # Шаг 4: последний резерв — шапка + ссылки, обрезаем без лома тегов
        if visible_length(caption) > 1024:
            platform_block = build_platform_links(url, rutube_url, vk_url)
            tg_block = build_telegraph_links(
                telegraph_url or "", _q_link, _ref_link, _terms_link,
                study_tg_url=study_analysis_tg or "",
                reflection_tg_url=reflection_application_tg or "",
            )
            footer = "\n".join(filter(None, [platform_block, tg_block]))
            header_lines = caption.split("\n")[:4]
            header = "\n".join(header_lines)
            caption = header + ("\n\n" + footer if footer else "")
            caption = safe_trim_caption(caption, 1024)
            _cap_ts_str = ""

        # ts_in_cap — считаем из финального состояния после всех шагов trimming
        _ts_in_cap = len([l for l in _cap_ts_str.split("\n") if l.strip()])
        logger.info(f"Caption visible_len={visible_length(caption)} format={_mat_format} ts_total={_ts_total} ts_cap_limit={_ts_limit} ts_in_cap={_ts_in_cap}")
        if ai_data:
            # Добавляем duration в ai_data чтобы /pdf мог его читать из кэша
            if duration and not ai_data.get("duration"):
                ai_data = {**ai_data, "duration": duration}
            await adb_save(video_id=media_id, url=url,
                    questions=ai_data.get("questions", []),
                    # Сохраняем приоритетные ссылки: новые страницы > legacy compact
                    quotes_tg_url=_q_link,
                    questions_tg_url=_ref_link,
                    ai_data=ai_data,
                    telegraph_url=telegraph_url or "",
                    cache_version=CACHE_VERSION,
                    prompt_version=get_prompt_fingerprint(),
                    model_name=GEMINI_MODEL,
                    terms_tg_url=_terms_link,
                    rutube_url=rutube_url or "",
                    vk_url=vk_url or "",
                    study_tg_url=study_analysis_tg or "",
                    reflection_tg_url=reflection_application_tg or "")

        with open(mp3_path, "rb") as audio_file:
            audio_title     = normalize_title_text((ai_data or {}).get("real_title")  or title) or title
            audio_performer = normalize_author_name((ai_data or {}).get("real_author") or performer) or performer
            # Увеличенный таймаут для больших файлов + retry при сетевых ошибках
            for _attempt in range(3):
                try:
                    audio_file.seek(0)
                    await update.message.reply_audio(
                        audio=audio_file, title=audio_title, performer=audio_performer,
                        thumbnail=thumb_buffer, duration=duration, caption=caption,
                        parse_mode="HTML",
                        write_timeout=180, read_timeout=180, connect_timeout=60,
                    )
                    break  # успех
                except Exception as upload_err:
                    err_name = type(upload_err).__name__
                    err_str  = str(upload_err).lower()
                    _retryable_names = ("Timeout", "NetworkError", "TimedOut", "ReadError", "ConnectError")
                    _retryable_strs  = ("internal server error", "server error", "bad gateway", "gateway timeout")
                    _is_retryable = (
                        any(x in err_name for x in _retryable_names) or
                        any(x in err_str  for x in _retryable_strs)
                    )
                    if _attempt < 2 and _is_retryable:
                        logger.warning(f"Upload попытка {_attempt+1}/3 не удалась ({err_name}: {str(upload_err)[:120]}), повтор...")
                        await asyncio.sleep(5 * (_attempt + 1))
                    else:
                        raise  # последняя попытка или не сетевая ошибка

        logger.info(f"Done: [{normalize_author_name(performer) or performer}] {normalize_title_text(title) or title} ({file_size_mb:.1f} MB, 128kbps)")

        # Отправляем полное описание отдельным сообщением (лимит 4096)
        _feat_full_caption = await asettings_get("caption_full_text")
        if _feat_full_caption and full_caption and full_caption != caption:
            try:
                await update.message.reply_text(full_caption, parse_mode="HTML")
            except Exception as _fe:
                logger.warning(f"Full caption send error: {_fe}")

        # ── PDF (опционально) ────────────────────────────────────
        _feat_pdf = await asettings_get("generate_pdf")
        if _feat_pdf and ai_data:
            _status_msg = None
            _pdf_path = None
            try:
                from pdf_generator import generate_sermon_pdf_async

                DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

                _pdf_path = str(DOWNLOAD_DIR / f"{media_id}_{uuid.uuid4().hex[:6]}.pdf")

                _raw_t = (ai_data.get("real_title") or "").strip() or (title or "").strip()
                _raw_a = (ai_data.get("real_author") or "").strip() or (performer or "").strip()
                _pdf_title  = normalize_title_text(_raw_t)  if _raw_t else "Без названия"
                _pdf_author = normalize_author_name(_raw_a) if _raw_a else "Неизвестный"
                _dur_str    = format_timestamp(duration) if duration else ""

                _pdf_urls = {}
                if telegraph_url:              _pdf_urls["synopsis"]   = telegraph_url
                if study_analysis_tg:          _pdf_urls["study"]      = study_analysis_tg
                if reflection_application_tg:  _pdf_urls["reflection"] = reflection_application_tg
                if terms_tg:                   _pdf_urls["terms"]      = terms_tg

                if _pdf_urls:
                    logger.info(f"PDF: генерирую ({list(_pdf_urls.keys())})")

                    _status_msg = await update.message.reply_text("📄 Генерирую PDF…")

                    async def _pdf_progress(stage: str, pct: int):
                        try:
                            await _status_msg.edit_text(f"📄 PDF: {stage} ({pct}%)")
                        except Exception:
                            pass

                    _pdf_result = await generate_sermon_pdf_async(
                        output_path      = _pdf_path,
                        title            = _pdf_title,
                        performer        = _pdf_author,
                        duration_str     = _dur_str,
                        urls             = _pdf_urls,
                        progress_callback = _pdf_progress,
                    )

                    if _pdf_result and Path(_pdf_result).exists():
                        _size = Path(_pdf_result).stat().st_size
                        if _size < 200:
                            logger.warning(f"PDF пустой: {_size} байт")
                        elif _size > 49 * 1024 * 1024:
                            logger.warning(f"PDF слишком большой: {_size} байт")
                        else:
                            def _safe_fn(s: str) -> str:
                                return re.sub(r'[\\/:*?"<>|\n\r\t]', '', s).strip()[:80] or "doc"

                            _fn = f"{_safe_fn(_pdf_author)} — {_safe_fn(_pdf_title)}.pdf"

                            with open(_pdf_result, "rb") as _pdf_f:
                                await update.message.reply_document(
                                    document   = _pdf_f,
                                    filename   = _fn,
                                    caption    = "📄 <b>PDF-версия материала</b>",
                                    parse_mode = "HTML",
                                )
                            logger.info("PDF: отправлен")

            except Exception as _pdf_err:
                logger.warning(f"PDF генерация не удалась: {_pdf_err}", exc_info=True)
            finally:
                if _pdf_path:
                    try:
                        Path(_pdf_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                if _status_msg:
                    try:
                        await _status_msg.delete()
                    except Exception:
                        pass

        # ── Shorts (после основного результата) ─────────
        _feat_shorts = await asettings_get("shorts")
        if ai_data and _feat_shorts:
            logger.info("Shorts: feature enabled, starting pipeline")
            await process_and_send_shorts(
                url=url,
                media_id=media_id,
                mp3_path=mp3_path,
                title=title,
                performer=performer,
                duration=duration,
                ai_data=ai_data,
                update=update,
                existing_audio_part=used_audio_part,   # ← REUSE
                existing_client=used_client,            # ← REUSE
                rutube_url=rutube_url,
                vk_url=vk_url,
            )
        else:
            logger.info(f"Shorts: skipped (feat={_feat_shorts}, ai_data={'yes' if ai_data else 'no'})")

        # ── Clips (после Shorts) ──────────────────────────
        _feat_clips = await asettings_get("clips")
        if ai_data and _feat_clips:
            logger.info("Clips: feature enabled, starting pipeline")
            await process_and_send_clips(
                url=url,
                media_id=media_id,
                mp3_path=mp3_path,
                title=title,
                performer=performer,
                duration=duration,
                ai_data=ai_data,
                update=update,
                existing_audio_part=used_audio_part,   # ← REUSE
                existing_client=used_client,            # ← REUSE
                rutube_url=rutube_url,
                vk_url=vk_url,
            )
        else:
            logger.info(f"Clips: skipped (feat={_feat_clips}, ai_data={'yes' if ai_data else 'no'})")

        # ── Montage + Highlights (один общий Gemini text-only вызов) ─
        _prefetched_extras = {"montage_candidates": [], "highlights_candidates": []}
        _feat_montage = await asettings_get("shorts_montage")
        _feat_highlights = await asettings_get("shorts_highlights")

        if ai_data and (_feat_montage or _feat_highlights):
            logger.info("Extras: feature enabled, requesting ONE Gemini text call for montage+highlights")
            _prefetched_extras = await create_extras_candidates(
                ai_data=ai_data,
                title=title,
                performer=performer,
                duration=duration,
            )

        if ai_data and _feat_montage:
            logger.info("Montage: feature enabled, starting pipeline")
            await process_and_send_montage(
                url=url, media_id=media_id, mp3_path=mp3_path,
                title=title, performer=performer, duration=duration,
                ai_data=ai_data, update=update,
                rutube_url=rutube_url, vk_url=vk_url,
                prefetched_candidates=_prefetched_extras.get("montage_candidates", []),
            )
        else:
            logger.info(f"Montage: skipped (feat={_feat_montage})")

        if ai_data and _feat_highlights:
            logger.info("Highlights: feature enabled, starting pipeline")
            await process_and_send_highlights(
                url=url, media_id=media_id, mp3_path=mp3_path,
                title=title, performer=performer, duration=duration,
                ai_data=ai_data, update=update,
                rutube_url=rutube_url, vk_url=vk_url,
                prefetched_candidates=_prefetched_extras.get("highlights_candidates", []),
            )
        else:
            logger.info(f"Highlights: skipped (feat={_feat_highlights})")

        cleanup_files(media_id)

        return True

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")
        try:
            cleanup_files(media_id)
        except NameError:
            pass  # media_id ещё не определён
        return False
    finally:
        # Удаляем audio_part из Gemini Files API — ТОЛЬКО ЗДЕСЬ,
        # после того как все задачи (Synopsis, Shorts, Clips) завершены.
        if used_audio_part and hasattr(used_audio_part, 'name') and used_client:
            try:
                await used_client.aio.files.delete(name=used_audio_part.name)
                logger.info("Gemini Files: audio_part удалён")
            except Exception as _del_err:
                logger.warning(f"Gemini Files delete error: {_del_err}")
        if thumb_buffer:
            thumb_buffer.close()


# ─── Обработка плейлиста ─────────────────────────────────────

async def handle_playlist(url, update, context):
    status_msg = await update.message.reply_text("📋 Загружаю плейлист...")
    try:
        playlist_opts = {
            "extractor_args": {"youtube": {"player_client": ["web"]}},
            "sleep_interval": 2,
            "quiet": True,
            "extract_flat": True,
        }
        if COOKIES_FILE.exists():
            playlist_opts["cookiefile"] = str(COOKIES_FILE)
        elif shutil.which("firefox"):
            playlist_opts["cookiesfrombrowser"] = ("firefox",)
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(
            None,
            lambda: yt_dlp.YoutubeDL(playlist_opts).extract_info(url, download=False),
        )
        if not info:
            await status_msg.edit_text("❌ Не удалось загрузить плейлист.")
            return
        title   = info.get("title", "Плейлист")
        entries = info.get("entries", [])[:MAX_PLAYLIST_SIZE]
        total   = len(entries)
        if not entries:
            await status_msg.edit_text("❌ Плейлист пуст.")
            return
        ai_status = "✅" if GEMINI_CLIENTS else "❌"
        await status_msg.edit_text(
            f"📋 {title}\n🎬 Записей: {total}\n🧠 AI: {ai_status}\n🎵 128 kbps\n⏳ Начинаю..."
        )
        await asyncio.sleep(2)
        success = fail = 0
        for i, entry in enumerate(entries, 1):
            vid = entry.get("id") or entry.get("url")
            if not vid:
                fail += 1
                continue
            media_url = f"https://www.youtube.com/watch?v={vid}"
            prefix = f"[{i}/{total}] "
            await status_msg.edit_text(
                f"📋 {title}\n🔄 {i}/{total} | ✅ {success} | ❌ {fail}"
            )
            ok = await process_single_video(media_url, update, status_msg, prefix, context=context)
            if ok:
                success += 1
            else:
                fail += 1
            if i < total:
                await asyncio.sleep(1)
        await status_msg.edit_text(
            f"🏁 Плейлист обработан!\n📋 {title}\n✅ {success} | ❌ {fail} | 📊 {total}"
        )
    except Exception as e:
        logger.error(f"Ошибка плейлиста: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {str(e)[:300]}")


# ─── Команды бота ─────────────────────────────────────────────

async def start(update, context):
    user_id   = update.effective_user.id
    is_vip    = user_id in WHITELIST_IDS
    is_admin  = user_id in ADMIN_IDS
    ai_status = "✅" if GEMINI_CLIENTS else "❌"

    if is_vip:
        admin_section = ""
        if is_admin:
            admin_section = (
                "\n\n🔧 <b>Команды администратора:</b>\n"
                "/resetcache &lt;url или video_id&gt;\n"
                "/resetcache all — очистить весь кэш"
            )
        text = (
            f"🎵 <b>Media Audio Converter</b>\n\n"
            f"👑 <b>VIP-доступ активен — без ограничений!</b>\n\n"
            f"<b>Как пользоваться:</b>\n"
            f"• Отправьте ссылку на видео или плейлист\n"
            f"• Получите MP3 128kbps + обложка\n\n"
            f"<b>AI-анализ каждого видео:</b>\n"
            f"🧠 AI: {ai_status}\n"
            f"📌 Тема и описание\n"
            f"⏱ Таймкоды по смысловым блокам\n"
            f"💬 Цитаты дословно\n"
            f"🏷 Хэштеги на русском\n"
            f"📋 Конспект в Telegraph\n"
            f"📊 Аналитика: Писание, богословы, аргументы\n"
            f"🗣 8 вопросов для размышления\n"
            f"📺 Поиск на RuTube и VK\n\n"
            f"<b>Команды:</b>\n"
            f"/start — эта панель\n"
            f"/help — краткая справка\n"
            f"/settings — настройки функций бота"
            f"{admin_section}\n\n"
            f"🪪 Ваш Telegram ID: <code>{user_id}</code>"
        )
        await update.message.reply_text(text, parse_mode="HTML")
    else:
        limit_line = f"📵 Лимит: {DAILY_LIMIT} видео/день | ⏳ 1 запрос/мин"
        await update.message.reply_text(
            f"🎵 Media Audio Converter\n\n"
            f"Отправьте ссылку на видео или плейлист!\n\n"
            f"✨ MP3 128kbps + обложка + автор + тема\n"
            f"🧠 AI: {ai_status}\n"
            f"📋 Плейлисты до {MAX_PLAYLIST_SIZE} видео\n"
            f"{limit_line}\n\n"
            f"🪪 Ваш Telegram ID: <code>{user_id}</code>",
            parse_mode="HTML"
        )


async def help_command(update, context):
    user_id = update.effective_user.id
    is_vip  = user_id in WHITELIST_IDS
    limit_line = (
        "👑 VIP — без ограничений"
        if is_vip
        else f"📵 {DAILY_LIMIT} видео/день • 1 запрос/мин"
    )
    await update.message.reply_text(
        f"ℹ️ Помощь\n\n"
        f"Отправьте ссылку на видео или плейлист → получите MP3 128kbps!\n\n"
        f"🧠 AI:\n"
        f"📌 Тема • ⏱ Таймкоды • 🏷 Хэштеги\n\n"
        f"🔒 Ваши лимиты: {limit_line}\n\n"
        f"/start — Приветствие\n"
        f"/help  — Справка"
    )


def _extract_yt_id_from_text(text: str) -> str | None:
    """Извлекает YouTube video_id из произвольного текста. Возвращает None если не найден."""
    m = re.search(r"(?:v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/live/)([A-Za-z0-9_-]{11})", text)
    return m.group(1) if m else None


async def _do_resetcache_one(video_id: str, update) -> None:
    """Удаляет одну запись кэша и отвечает в чат."""
    loop = asyncio.get_running_loop()
    def _delete():
        with sqlite3.connect(DB_PATH) as conn:
            r = conn.execute("DELETE FROM video_cache WHERE video_id = ?", (video_id,)).rowcount
            conn.commit()
            return r
    rows = await loop.run_in_executor(None, _delete)
    if rows:
        await update.message.reply_text(
            f"✅ Кэш сброшен: `{video_id}`\nТеперь отправь ссылку заново.",
            parse_mode="Markdown")
    else:
        await update.message.reply_text(
            f"⚠️ Не найдено в кэше: `{video_id}`",
            parse_mode="Markdown")


async def reset_cache_command(update, context):
    """Удаляет кэш видео — для принудительной переобработки."""

    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"⛔ Нет доступа.\n"
            f"Ваш Telegram ID: `{user_id}`\n"
            f"Добавьте его в переменную ADMIN_IDS на Render.", parse_mode="Markdown")
        return

    args = context.args
    arg  = args[0].strip() if args else ""

    # ── Нет аргумента — пробуем извлечь ссылку из reply ──────
    if not arg:
        reply = update.message.reply_to_message
        if reply and reply.text:
            video_id = _extract_yt_id_from_text(reply.text)
            if video_id:
                await _do_resetcache_one(video_id, update)
                return

        # reply не помог — показываем краткую инструкцию
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Удалить весь кэш", callback_data="resetcache:all")
        ]])
        await update.message.reply_text(
            "🗑 <b>Сброс кэша</b>\n\n"
            "Скопируйте команду и вставьте ссылку после неё:\n\n"
            "<code>/resetcache </code>\n\n"
            "Примеры:\n"
            "<code>/resetcache https://youtu.be/VIDEO_ID</code>\n"
            "<code>/resetcache VIDEO_ID</code>\n\n"
            "Полная очистка: <code>/resetcache all</code>",
            parse_mode="HTML",
            reply_markup=keyboard)
        return

    # ── /resetcache all ───────────────────────────────────────
    if arg.lower() == "all":
        loop = asyncio.get_running_loop()
        def _delete_all():
            with sqlite3.connect(DB_PATH) as conn:
                r = conn.execute("DELETE FROM video_cache").rowcount
                conn.commit()
                return r
        rows = await loop.run_in_executor(None, _delete_all)
        await update.message.reply_text(
            f"🗑 Весь кэш удалён: {rows} записей.", parse_mode="Markdown")
        return

    # ── /resetcache <url или video_id> ────────────────────────
    video_id = _extract_yt_id_from_text(arg) or arg
    await _do_resetcache_one(video_id, update)


async def pdf_command(update, context):
    """Генерирует и отправляет PDF из кэша для указанного видео."""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"⛔ Нет доступа.\nВаш Telegram ID: `{user_id}`", parse_mode="Markdown")
        return

    args = context.args
    arg  = args[0].strip() if args else ""

    # Пробуем вытащить video_id из reply-сообщения
    if not arg:
        reply = update.message.reply_to_message
        if reply and reply.text:
            arg = _extract_yt_id_from_text(reply.text) or ""

    if not arg:
        await update.message.reply_text(
            "📄 <b>PDF из кэша</b>\n\n"
            "Передайте ссылку или video_id:\n"
            "<code>/pdf https://youtu.be/VIDEO_ID</code>\n"
            "<code>/pdf VIDEO_ID</code>\n\n"
            "Или ответьте этой командой на сообщение с ссылкой.",
            parse_mode="HTML")
        return

    video_id = _extract_yt_id_from_text(arg) or arg

    msg = await update.message.reply_text("⏳ Ищу в кэше и генерирую PDF…")

    rec = await adb_get(video_id)
    if not rec:
        await msg.edit_text(f"⚠️ Видео <code>{video_id}</code> не найдено в кэше.", parse_mode="HTML")
        return

    ai_data = rec.get("ai_data")
    if not ai_data:
        await msg.edit_text("⚠️ В кэше нет AI-данных для этого видео. Обработайте видео заново.")
        return

    telegraph_url     = rec.get("telegraph_url", "")
    study_tg_url      = rec.get("study_tg_url", "")
    reflection_tg_url = rec.get("reflection_tg_url", "")
    terms_tg_url      = rec.get("terms_tg_url", "")

    pdf_urls = {}
    if telegraph_url:     pdf_urls["synopsis"]   = telegraph_url
    if study_tg_url:      pdf_urls["study"]      = study_tg_url
    if reflection_tg_url: pdf_urls["reflection"] = reflection_tg_url
    if terms_tg_url:      pdf_urls["terms"]      = terms_tg_url

    if not pdf_urls:
        await msg.edit_text("⚠️ Нет Telegraph-страниц для сборки PDF. Обработайте видео заново.")
        return

    title     = normalize_title_text(ai_data.get("real_title") or "") or video_id
    # ai_data не содержит поля "performer" — оно хранится в short_trims, но не в video_cache.
    # Единственный надёжный источник автора — real_author из AI-анализа.
    performer = normalize_author_name(ai_data.get("real_author") or "") or ""
    duration  = ai_data.get("duration", 0) or 0
    dur_str   = format_timestamp(duration) if duration else ""

    try:
        from pdf_generator import generate_sermon_pdf_async
        pdf_path = str(DOWNLOAD_DIR / f"{video_id}_cmd.pdf")
        result   = await generate_sermon_pdf_async(
            output_path  = pdf_path,
            title        = title,
            performer    = performer,
            duration_str = dur_str,
            urls         = pdf_urls,
        )
        if result and Path(result).exists():
            filename = f"{performer} — {title}.pdf" if performer else f"{title}.pdf"
            with open(result, "rb") as f:
                await update.message.reply_document(
                    document   = f,
                    filename   = filename,
                    caption    = f"📄 <b>PDF</b>: {html_mod.escape(title)}",
                    parse_mode = "HTML",
                )
            try: Path(result).unlink()
            except Exception: pass
            await msg.delete()
        else:
            await msg.edit_text("❌ PDF не удалось сгенерировать. Проверьте pdf_generator.")
    except ImportError:
        await msg.edit_text("❌ pdf_generator не установлен/не найден рядом с bot.py.")
    except Exception as e:
        logger.warning(f"PDF команда ошибка: {e}")
        await msg.edit_text(f"❌ Ошибка генерации PDF: {e}")


async def stop_command(update, context):
    """Останавливает бота. Только для администраторов."""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"⛔ Нет доступа.\nВаш Telegram ID: `{user_id}`", parse_mode="Markdown"
        )
        return
    await update.message.reply_text("🛑 Останавливаю бота...")
    logger.info(f"Stop command от admin {user_id} — завершаю процесс")
    asyncio.get_running_loop().call_later(1.0, lambda: os._exit(0))


async def handle_message(update, context):
    text = update.message.text.strip()
    if not is_media_url(text):
        await update.message.reply_text("🤔 Отправьте ссылку на видео или плейлист.")
        return

    # ── Проверка лимитов ──────────────────────────────────────
    user_id = update.effective_user.id
    is_vip  = user_id in WHITELIST_IDS
    allowed, reason = check_rate_limit(user_id)
    if not allowed:
        await update.message.reply_text(reason)
        return

    url = extract_media_url(text)
    if not url:
        await update.message.reply_text("❌ Не удалось распознать ссылку.")
        return
    if not url.startswith("http"):
        url = "https://" + url

    if is_playlist_url(text):
        await handle_playlist(url, update, context)
        update_rate_limit(user_id)  # списываем после завершения
    else:
        label = " 👑" if is_vip else ""
        msg   = await update.message.reply_text(f"⏳ Обрабатываю...{label}")
        # ── Per-video lock: если одно и то же видео уже обрабатывается другим
        # пользователем — ждём завершения, после чего process_single_video
        # обнаружит готовый кэш и отдаст результат без повторного скачивания.
        _vid_id_hint = _extract_yt_id_from_text(url) or url
        _vlock = _get_video_lock(_vid_id_hint)
        if _vlock.locked():
            logger.info(f"Video {_vid_id_hint}: параллельный запрос — жду завершения первого...")
        async with _vlock:
            ok = await process_single_video(url, update, msg, context=context)
        # Удаляем лок из словаря после завершения — предотвращаем утечку памяти.
        # Следующий запрос к тому же video_id создаст новый лок.
        with _video_locks_mutex:
            _video_processing_locks.pop(_vid_id_hint, None)
        if ok:
            update_rate_limit(user_id)
        try:
            await msg.delete()
        except Exception:
            pass


# ─── Запуск ──────────────────────────────────────────────────

async def run_bot_async():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не найден!")
        return
    logger.info("🚀 Бот запускается...")
    logger.info(f"🧠 AI ({GEMINI_MODEL}): {'✅' if GEMINI_CLIENTS else '❌'} (ключей: {len(GEMINI_CLIENTS)})")
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
            logger.info(f"💬 Субтитры Shorts: ✅ включены ({_sub_mode})")
            # Preload запустим в фоне после старта бота — не блокируем polling
            async def _preload_whisper_bg():
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, lambda: _get_whisper_model(_sub_cfg["model_name"]))
                    logger.info(f"🎤 Whisper preloaded: {_sub_cfg['model_name']}")
                except Exception as _e:
                    logger.warning(f"⚠️  Whisper preload не удался: {_e}")
            asyncio.ensure_future(_preload_whisper_bg())
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

        while True:
            await asyncio.sleep(3600)


def run_bot():
    restart_delay = 5  # секунд между перезапусками
    while True:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_bot_async())
        except Exception as e:
            print(f"❌ Ошибка бота: {e}")
            logger.error(f"run_bot завершился с ошибкой: {e}")
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