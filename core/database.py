#!/usr/bin/env python3
"""
Database layer — db_init, db_save, db_get, settings, rate_limit, cache.
Извлечено из bot.py строки 88–555.
"""
from core.globals import (
    BOT_TOKEN, GEMINI_API_KEY, DOWNLOAD_DIR, THUMBS_DIR,
    DB_PATH, HAS_GEMINI, HAS_PILLOW,
    flask_app, _video_processing_locks, _video_locks_mutex, _get_video_lock,
)

import asyncio
import hashlib  # FIX #6: был не импортирован — нужен для get_prompt_fingerprint
import json
import logging
import os          # FIX #6: был не импортирован — нужен для os.getenv
import sqlite3
import time

logger = logging.getLogger(__name__)

# DB_PATH импортирован из globals — дублирующее определение убрано (FIX #19)

def db_init():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS video_cache (
                video_id        TEXT PRIMARY KEY,
                url             TEXT DEFAULT '',
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
            # Блок «Слова»
            ("terms_tg_url",     "''"),
            # Альтернативные платформы (кэшируем чтобы не искать повторно)
            ("rutube_url",           "''"),
            ("vk_url",               "''"),
            # Новые article-like страницы
            ("study_tg_url",         "''"),
            ("reflection_tg_url",    "''"),
        ]:
            # FIX SQL-injection: валидация ВНЕ try/except — ValueError не должен
            # быть поглощён блоком except sqlite3.OperationalError
            if not col.replace('_', '').isalnum():
                raise ValueError(f"Небезопасное имя колонки для миграции: {col!r}")
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
        # #31: миграция — добавляем метку истечения срока nosub-файла (через 24ч)
        try:
            conn.execute("ALTER TABLE short_trims ADD COLUMN nosub_expiry INTEGER DEFAULT 0")
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
        if deleted:
            logging.getLogger(__name__).info(f"db_cleanup: удалено {deleted} устаревших записей")
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
                    candidate_json: str = "{}", video_path_nosub: str = "",
                    nosub_expiry: int = 0) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO short_trims
                (short_id, video_path, start_seconds, end_seconds, visual_mode,
                 yt_url, vk_url, rutube_url, performer, real_author, real_event,
                 format_name, candidate_json, video_path_nosub, nosub_expiry)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (short_id, video_path, start_seconds, end_seconds, visual_mode,
              yt_url, vk_url, rutube_url, performer, real_author, real_event,
              format_name, candidate_json, video_path_nosub, nosub_expiry))
        conn.commit()

def short_trim_get(short_id: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT video_path, start_seconds, end_seconds, visual_mode, yt_url, vk_url, "
            "rutube_url, performer, real_author, real_event, format_name, candidate_json, "
            "video_path_nosub, nosub_expiry "
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
        "video_path_nosub": row[12] or "",
        "nosub_expiry": row[13] or 0,
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
    """Возвращает все настройки за один SELECT вместо N отдельных соединений."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute("SELECT key, value FROM bot_settings").fetchall()
        db_vals = {k: (v.lower() in ("1", "true", "yes")) for k, v in rows}
    except Exception:
        db_vals = {}
    return {k: db_vals.get(k, SETTINGS_DEFAULTS.get(k, True)) for k in SETTINGS_DEFAULTS}


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
PROMPT_SCHEMA_VERSION = os.getenv("PROMPT_SCHEMA_VERSION", "analysis-deep-v7")
_AUDIO_ANALYSIS_MODE = os.getenv("AUDIO_ANALYSIS_MODE", "deep").strip().lower() or "deep"
if _AUDIO_ANALYSIS_MODE not in {"deep", "balanced", "fast"}:
    _AUDIO_ANALYSIS_MODE = "deep"


def get_prompt_fingerprint() -> str:
    """SHA-256 от сочетания версии промпта, режима анализа и модели → первые 16 hex-символов."""
    raw = f"{PROMPT_SCHEMA_VERSION}|{GEMINI_MODEL}|{_AUDIO_ANALYSIS_MODE}"
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

# FIX #2: вызываем после определения CACHE_TTL_DAYS и всех констант
db_cleanup_old_records()
