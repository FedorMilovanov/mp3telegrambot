#!/usr/bin/env python3
"""
Database layer — db_init, db_save, db_get, settings, rate_limit, cache.
Извлечено из bot.py строки 88–555.
"""
from core.globals import (
    BOT_TOKEN, GEMINI_API_KEY, DOWNLOAD_DIR, THUMBS_DIR,
    DB_PATH, HAS_GEMINI, HAS_PILLOW, LOCAL_BOT_API_URL,
    flask_app, _video_processing_locks, _video_locks_mutex, _get_video_lock,
)

import asyncio
import hashlib
from pathlib import Path  # round 31: db_backup  # FIX #6: был не импортирован — нужен для get_prompt_fingerprint
import json
import logging
import os          # FIX #6: был не импортирован — нужен для os.getenv
import re
import sqlite3
import time

logger = logging.getLogger(__name__)

# Версия Telegram file_id для LiveDub. Важно отдельно от AI cache_version:
# file_id может указывать на старый уже отправленный mp4. При изменении микса
# (например, tail guard против обрывов Shorts) старый file_id надо игнорировать
# и пересобрать видео, иначе пользователь будет получать прежний баг из кэша.
LIVEDUB_FILE_ID_CACHE_VERSION = "tail_guard_named_v1"


def current_livedub_file_id_cache_version() -> str:
    """Версия LiveDub file_id с учётом knobs, меняющих готовый mp4.

    Если пользователь подкрутил delay/tail/баланс громкости/RNNoise, старый
    Telegram file_id больше не соответствует ожидаемому результату — надо
    пересобрать, а не мгновенно отправлять старый вариант из кэша.
    """
    knobs = {
        "orig": os.getenv("LIVEDUB_ORIG_VOLUME", "0.45").strip() or "0.45",
        "ru": os.getenv("LIVEDUB_TRANS_VOLUME", "1.3").strip() or "1.3",
        "delay": os.getenv("LIVEDUB_DELAY_MS", "600").strip() or "600",
        "tail": os.getenv("LIVEDUB_TAIL_MARGIN_MS", "1000").strip() or "1000",
        "freeze": os.getenv("LIVEDUB_TAIL_FREEZE_MAX_SEC", "180").strip() or "180",
        "votdur": "floor",
        "eq": os.getenv("LIVEDUB_VOICE_EQ", "1").strip() or "1",
        "rnnoise": os.getenv("LIVEDUB_RNNOISE_MODEL", "").strip(),
    }
    return LIVEDUB_FILE_ID_CACHE_VERSION + ":" + ";".join(
        f"{k}={v}" for k, v in knobs.items()
    )


def current_livedub_file_id_cache_fingerprint() -> str:
    """Короткий fingerprint версии LiveDub file_id для логов и /status."""
    return hashlib.sha1(
        current_livedub_file_id_cache_version().encode("utf-8")
    ).hexdigest()[:10]

# DB_PATH импортирован из globals — дублирующее определение убрано (FIX #19)

def db_init():
    with sqlite3.connect(DB_PATH) as conn:
        # AUDIT M17: WAL + busy_timeout — для безопасной параллельной записи
        # rate_limit / video_cache / bot_settings из разных async-обработчиков.
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
        except Exception:
            pass
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS gemini_runs (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                ts                 REAL NOT NULL,
                video_id           TEXT DEFAULT '',
                task               TEXT NOT NULL,
                model              TEXT DEFAULT '',
                thinking_level     TEXT DEFAULT '',
                input_tokens       INTEGER DEFAULT 0,
                output_tokens      INTEGER DEFAULT 0,
                cached_tokens      INTEGER DEFAULT 0,
                thinking_tokens    INTEGER DEFAULT 0,
                total_tokens       INTEGER DEFAULT 0,
                duration_ms        INTEGER DEFAULT 0,
                retry_num          INTEGER DEFAULT 0,
                is_fallback        INTEGER DEFAULT 0,
                json_valid         INTEGER,
                postprocess_fixes  INTEGER DEFAULT 0,
                validation_summary TEXT DEFAULT '',
                finish_reason      TEXT DEFAULT '',
                error              TEXT DEFAULT '',
                prompt_version     TEXT DEFAULT '',
                cache_key          TEXT DEFAULT ''
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gemini_runs_ts ON gemini_runs(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gemini_runs_task_ts ON gemini_runs(task, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gemini_runs_video_id ON gemini_runs(video_id)")
        try:
            conn.execute("ALTER TABLE gemini_runs ADD COLUMN validation_summary TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
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
            # Publication completeness/status (V3-P23)
            ("publication_status",  "'unknown'"),
            ("publication_missing", "'[]'"),
            ("publication_warning", "''"),
            # LIVEDUB: telegram file_id готового перевода — повторная отправка
            # мгновенна (без второго прогона vot-cli и заливки сотен МБ).
            # livedub_file_id_version защищает от ресенда старых mp4 после
            # изменения микса/таймингов.
            ("livedub_file_id",         "''"),
            ("livedub_file_id_version", "''"),
            # MP3 file_id — мгновенный resend при кэш-хите (без заливки 50МБ)
            ("audio_file_id",           "''"),
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
        # AUDIT M5: добавляем длительность исходного видео — для ограничения end_s при ретриме
        try:
            conn.execute("ALTER TABLE short_trims ADD COLUMN source_duration INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # Колонка уже есть
        conn.commit()
        if migrated:
            pass

def db_cleanup_old_records():
    """Удаляет записи кэша старше CACHE_TTL_DAYS. Вызывать после определения констант.

    AUDIT M18: чистим и legacy-записи (updated_at=0) по created_at — иначе они копятся вечно.
    """
    try:
        cutoff = int(time.time()) - CACHE_TTL_DAYS * 86400
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA busy_timeout=5000")
            deleted = conn.execute(
                "DELETE FROM video_cache WHERE updated_at > 0 AND updated_at < ?", (cutoff,)
            ).rowcount
            # AUDIT M18: legacy записи без updated_at — чистим по created_at
            try:
                legacy = conn.execute(
                    "DELETE FROM video_cache WHERE updated_at = 0 AND created_at < ?",
                    (cutoff,),
                ).rowcount
            except sqlite3.OperationalError:
                legacy = 0
            # AUDIT M9: чистим short_trims старше 7 дней
            try:
                trims_cutoff = int(time.time()) - 7 * 86400
                trims = conn.execute(
                    "DELETE FROM short_trims WHERE created_at < ?", (trims_cutoff,)
                ).rowcount
            except sqlite3.OperationalError:
                trims = 0
            conn.commit()
        total = (deleted or 0) + (legacy or 0)
        if total or trims:
            logging.getLogger(__name__).info(
                f"db_cleanup: video_cache -{total} (legacy {legacy}), short_trims -{trims}"
            )
    except Exception as _ce:
        logger.warning("db_cleanup_old_records failed: %s", _ce, exc_info=True)

def db_save(video_id: str, url: str, questions: list,
            quotes_tg_url: str = "", questions_tg_url: str = "",
            ai_data: dict = None, telegraph_url: str = "",
            cache_version: str = "", prompt_version: str = "", model_name: str = "",
            terms_tg_url: str = "",
            rutube_url: str = "", vk_url: str = "",
            study_tg_url: str = "", reflection_tg_url: str = "",
            publication_status: str = "unknown", publication_missing: str = "[]",
            publication_warning: str = ""):
    # busy_timeout is set inside the sqlite connection below.
    _cache_version  = cache_version  or CACHE_VERSION
    _prompt_version = prompt_version or get_prompt_fingerprint()
    _model_name     = model_name     or GEMINI_MODEL
    _updated_at     = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("""
            INSERT INTO video_cache
                (video_id, url, questions, quotes_tg_url, questions_tg_url,
                 ai_data, telegraph_url,
                 cache_version, prompt_version, model_name, updated_at, terms_tg_url,
                 rutube_url, vk_url, study_tg_url, reflection_tg_url,
                 publication_status, publication_missing, publication_warning)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                reflection_tg_url = excluded.reflection_tg_url,
                publication_status = excluded.publication_status,
                publication_missing = excluded.publication_missing,
                publication_warning = excluded.publication_warning
        """, (video_id, url,
              json.dumps(questions, ensure_ascii=False),
              quotes_tg_url, questions_tg_url,
              json.dumps(ai_data, ensure_ascii=False) if ai_data else "",
              telegraph_url or "",
              _cache_version, _prompt_version, _model_name, _updated_at,
              terms_tg_url or "",
              rutube_url or "", vk_url or "",
              study_tg_url or "", reflection_tg_url or "",
              publication_status or "unknown", publication_missing or "[]",
              publication_warning or ""))
        conn.commit()

_FILE_ID_COLUMNS = {"livedub_file_id", "audio_file_id"}


def _db_set_file_id(video_id: str, column: str, file_id: str) -> None:
    """Точечно сохраняет telegram file_id (resend мгновенный и бесплатный).
    column валидируется по белому списку — никакого SQL-injection."""
    if column not in _FILE_ID_COLUMNS:
        raise ValueError(f"недопустимая колонка file_id: {column!r}")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        if column == "livedub_file_id":
            version = current_livedub_file_id_cache_version() if file_id else ""
            cur = conn.execute(
                "UPDATE video_cache SET livedub_file_id = ?, livedub_file_id_version = ? "
                "WHERE video_id = ?",
                (file_id or "", version, video_id),
            )
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT OR IGNORE INTO video_cache "
                    "(video_id, livedub_file_id, livedub_file_id_version) VALUES (?, ?, ?)",
                    (video_id, file_id or "", version),
                )
        else:
            cur = conn.execute(
                f"UPDATE video_cache SET {column} = ? WHERE video_id = ?",
                (file_id or "", video_id),
            )
            if cur.rowcount == 0:
                conn.execute(
                    f"INSERT OR IGNORE INTO video_cache (video_id, {column}) VALUES (?, ?)",
                    (video_id, file_id or ""),
                )
        conn.commit()


def db_set_livedub_file_id(video_id: str, file_id: str) -> None:
    _db_set_file_id(video_id, "livedub_file_id", file_id)


def db_set_audio_file_id(video_id: str, file_id: str) -> None:
    _db_set_file_id(video_id, "audio_file_id", file_id)


async def adb_set_audio_file_id(video_id: str, file_id: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, db_set_audio_file_id, video_id, file_id)


async def adb_set_livedub_file_id(video_id: str, file_id: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, db_set_livedub_file_id, video_id, file_id)


def db_backup(max_keep: int = 7) -> str:
    """Горячий бэкап БД нативным sqlite3 backup API (корректен при WAL).

    Вся память бота — кэш анализов, настройки, file_id, rate-limits —
    живёт в одном файле; до 2026-06-10 не бэкапился вовсе. Копии кладутся
    рядом: bot.db.bak.YYYYMMDD (одна в сутки, хранится max_keep штук).
    Возвращает путь созданной копии или '' (уже есть сегодняшняя/ошибка).
    """
    import time as _time
    try:
        day = _time.strftime("%Y%m%d")
        dst = Path(str(DB_PATH) + f".bak.{day}")
        if dst.exists():
            return ""
        src = sqlite3.connect(DB_PATH)
        try:
            dst_conn = sqlite3.connect(str(dst))
            try:
                src.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src.close()
        # ротация
        baks = sorted(Path(str(DB_PATH)).parent.glob(Path(str(DB_PATH)).name + ".bak.*"))
        for old in baks[:-max_keep]:
            try:
                old.unlink()
            except OSError:
                pass
        logger.info("DB backup: %s (%d KB)", dst.name, dst.stat().st_size // 1024)
        return str(dst)
    except Exception as e:
        logger.warning("DB backup failed: %s", e)
        return ""


def db_get(video_id: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        row = conn.execute(
            "SELECT url, questions, quotes_tg_url, questions_tg_url, ai_data, telegraph_url, "
            "cache_version, prompt_version, model_name, updated_at, terms_tg_url, "
            "rutube_url, vk_url, study_tg_url, reflection_tg_url, "
            "publication_status, publication_missing, publication_warning, "
            "livedub_file_id, livedub_file_id_version, audio_file_id "
            "FROM video_cache WHERE video_id = ?", (video_id,)
        ).fetchone()
    if not row:
        return None
    ai_data = None
    if row[4]:
        try:
            ai_data = json.loads(row[4])
        except Exception as _e:
            logger.debug("db_get: ai_data JSON parse failed for %s: %s", video_id, _e)
    questions = []
    try:
        questions = json.loads(row[1]) if row[1] else []
    except Exception as _e:
        logger.debug("db_get: questions JSON parse failed for %s: %s", video_id, _e)
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
        "publication_status":  row[15] or "unknown",
        "publication_missing": row[16] or "[]",
        "publication_warning": row[17] or "",
        "livedub_file_id":     row[18] or "",
        "livedub_file_id_version": row[19] or "",
        "audio_file_id":       row[20] or "",
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

def _one_time_enable_subtitles():
    """AUDIT: одноразовая миграция — включаем красивые субтитры по умолчанию.

    Срабатывает ОДИН раз в жизни базы (по флагу _migration_enable_subs_v1).
    Если пользователь после этого выключит вручную — повторно не включаем.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT value FROM bot_settings WHERE key = '_migration_enable_subs_v1'"
            ).fetchone()
            if row:
                return  # уже применено
            for k in ("shorts_subtitles", "shorts_subtitles_karaoke"):
                conn.execute(
                    "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, '1')",
                    (k,),
                )
            conn.execute(
                "INSERT INTO bot_settings (key, value) VALUES "
                "('_migration_enable_subs_v1', '1')"
            )
            conn.commit()
        logging.getLogger(__name__).info(
            "🎨 Миграция: красивые субтитры Shorts включены по умолчанию"
        )
    except Exception:
        pass


_one_time_enable_subtitles()


# ─── Short trim helpers ───────────────────────────────────────

def short_trim_save(short_id: str, video_path: str, start_seconds: int, end_seconds: int,
                    visual_mode: str = "full_frame_vertical", yt_url: str = "",
                    vk_url: str = "", rutube_url: str = "", performer: str = "",
                    real_author: str = "", real_event: str = "", format_name: str = "",
                    candidate_json: str = "{}", video_path_nosub: str = "",
                    nosub_expiry: int = 0, source_duration: int = 0) -> None:
    """AUDIT M5: source_duration — длительность исходного видео, для ограничения end_s."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("""
            INSERT OR REPLACE INTO short_trims
                (short_id, video_path, start_seconds, end_seconds, visual_mode,
                 yt_url, vk_url, rutube_url, performer, real_author, real_event,
                 format_name, candidate_json, video_path_nosub, nosub_expiry,
                 source_duration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (short_id, video_path, start_seconds, end_seconds, visual_mode,
              yt_url, vk_url, rutube_url, performer, real_author, real_event,
              format_name, candidate_json, video_path_nosub, nosub_expiry,
              source_duration))
        conn.commit()

def short_trim_get(short_id: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        # AUDIT M5: добавлено поле source_duration
        try:
            row = conn.execute(
                "SELECT video_path, start_seconds, end_seconds, visual_mode, yt_url, vk_url, "
                "rutube_url, performer, real_author, real_event, format_name, candidate_json, "
                "video_path_nosub, nosub_expiry, source_duration "
                "FROM short_trims WHERE short_id = ?", (short_id,)
            ).fetchone()
            has_src_dur = True
        except sqlite3.OperationalError:
            # Колонка ещё не смигрировала — fallback на старую схему
            row = conn.execute(
                "SELECT video_path, start_seconds, end_seconds, visual_mode, yt_url, vk_url, "
                "rutube_url, performer, real_author, real_event, format_name, candidate_json, "
                "video_path_nosub, nosub_expiry "
                "FROM short_trims WHERE short_id = ?", (short_id,)
            ).fetchone()
            has_src_dur = False
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
        "source_duration": (row[14] or 0) if has_src_dur else 0,
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
        return False, "cache_version_mismatch"
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
    "shorts_subtitles":         True,   # Субтитры (burn-in) для Shorts — ВКЛ по умолчанию (AUDIT)
    "shorts_subtitles_karaoke": True,   # Karaoke word-level подсветка
    "shorts_subtitles_light":   False,  # Лёгкий режим: medium модель вместо large-v3
    "shorts_subtitles_gemini_hints": True,  # Gemini terms/title hints for Whisper initial_prompt
    "shorts_boundary_padding": True,  # Take a little before/after selected short boundaries
    "shorts_montage":         False,  # Тематическая склейка из разных моментов
    "shorts_highlights":      False,  # Рекламный highlights reel
    "shorts_title_poster":    True,   # Стильный постер с заголовком для Shorts
    "clips":                  False,  # Длинные clips (5–15 мин) из Q&A / лекций
    "clips_snapshot":         True,   # Poster/snapshot для каждого clip
    "segments":               False,  # Selectable timestamp-based Q&A/theme cuts
    "segments_render":        True,   # Allow /cutseg rendering from timestamp segments
    "segments_subtitles":     True,   # Burn subtitles into /cutseg rendered segments
    "segments_batch_render":  False,  # Allow /cutseg VIDEO_ID 1,3,5 / all
    # ── Новая продуктовая модель: две сильных article-like страницы ──────────
    "study_analysis":         True,   # «Разбор материала» (аналитика+термины+богословие)
    "reflection_application": True,   # «Размышление и применение» (пасторский guide)
    "caption_full_text":      True,   # Отправлять полный текст отдельным сообщением
    "generate_pdf":           False,  # Генерировать PDF (выключено по умолчанию)
    "eng_subtitles":          True,   # Субтитры для ENG режима (Whisper+Gemini)
    "livedub_qa":             True,   # Смысловая проверка перевода через Gemini (ENG Full)
    "livedub_pro_mix":        True,   # Собственный микс: слышный оригинал + ducking + задержка
    "livedub_autofix":        True,   # Авто-правка по QA: глушить major-искажения перевода
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
    "shorts_subtitles_gemini_hints": "🧠 Gemini hints",
    "shorts_boundary_padding": "↔️ Запас краёв",
    "shorts_title_poster":    "🖼 Poster",
    "shorts_montage":         "🎬 Montage",
    "shorts_highlights":      "🌟 Highlights",
    "clips":                  "🎬 Clips",
    "clips_snapshot":         "📸 Poster",
    "segments":               "🧩 Сегменты",
    "segments_render":        "🎞 Рендер сегментов",
    "segments_subtitles":     "💬 Субтитры сегментов",
    "segments_batch_render":  "📦 Пакетная нарезка",
    "study_analysis":         "📖 Разбор материала",
    "reflection_application": "🙏 Размышление и применение",
    "caption_full_text":      "📋 Полный текст отдельно",
    "generate_pdf":           "📄 Генерировать PDF",
    "eng_subtitles":          "💬 Субтитры ENG",
    "livedub_qa":             "🔍 Проверка перевода",
    "livedub_pro_mix":        "🎚 Pro-микс звука",
    "livedub_autofix":        "🩹 Авто-правка перевода",
}

# ─── Скорость Shorts (не bool, отдельная настройка) ──────────
SHORTS_SPEED_STEPS: list[str] = ["1.0", "1.1", "1.3", "1.5"]
SHORTS_SPEED_DEFAULT: str     = "1.0"

def shorts_speed_get() -> str:
    """Читает текущую скорость Shorts из БД."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA busy_timeout=5000")
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
        conn.execute("PRAGMA busy_timeout=5000")
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
            conn.execute("PRAGMA busy_timeout=5000")
            row = conn.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)).fetchone()
        if row:
            return row[0].lower() in ("1", "true", "yes")
    except Exception:
        pass
    return SETTINGS_DEFAULTS.get(key, True)


def settings_set(key: str, value: bool) -> None:
    """Сохраняет настройку в БД."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            (key, "1" if value else "0")
        )
        conn.commit()


def settings_get_all() -> dict[str, bool]:
    """Возвращает все настройки за один SELECT вместо N отдельных соединений."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA busy_timeout=5000")
            rows = conn.execute("SELECT key, value FROM bot_settings").fetchall()
        db_vals = {k: (v.lower() in ("1", "true", "yes")) for k, v in rows}
    except Exception:
        db_vals = {}
    return {k: db_vals.get(k, SETTINGS_DEFAULTS.get(k, True)) for k in SETTINGS_DEFAULTS}


async def asettings_get_all() -> dict[str, bool]:
    """Async-обёртка: возвращает все настройки за один вызов executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, settings_get_all)


# AUDIT M4: async-обёртки для shorts_speed_* — больше не блокируют event loop
async def ashorts_speed_get() -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, shorts_speed_get)


async def ashorts_speed_cycle() -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, shorts_speed_cycle)


async def acheck_rate_limit(user_id: int):
    """AUDIT M4: async-обёртка для check_rate_limit (вызывается в handle_message)."""
    from core.utils import check_rate_limit
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, check_rate_limit, user_id)


async def aupdate_rate_limit(user_id: int) -> None:
    """AUDIT M4: async-обёртка для update_rate_limit."""
    from core.utils import update_rate_limit
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, update_rate_limit, user_id)

# PART5: atomic-ish reservation for async handlers in this bot process.
# check_rate_limit() и update_rate_limit() исторически разделены, поэтому два
# параллельных handler-а могли одновременно пройти check. Этот lock сериализует
# check+reserve per user_id. Резервация делается ДО обработки: это защищает лимит
# от bypass ценой того, что неуспешная обработка тоже считается попыткой.
_rate_limit_async_locks: dict[int, asyncio.Lock] = {}
_rate_limit_locks_guard = asyncio.Lock()


async def _get_rate_limit_lock(user_id: int) -> asyncio.Lock:
    async with _rate_limit_locks_guard:
        # FIX 2026-05-24: cleanup stale locks to prevent memory leak
        if len(_rate_limit_async_locks) > 500:
            stale = [uid for uid, lk in _rate_limit_async_locks.items() if not lk.locked()]
            for uid in stale:
                del _rate_limit_async_locks[uid]
        lock = _rate_limit_async_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _rate_limit_async_locks[user_id] = lock
        return lock


async def areserve_rate_limit(user_id: int) -> tuple[bool, str]:
    """Атомарно для текущего процесса: проверить лимит и сразу зарезервировать слот.

    Возвращает (allowed, reason). VIP/whitelist проходят без записи.
    Использовать в Telegram handlers вместо пары acheck_rate_limit()+aupdate_rate_limit(),
    когда важно не допустить параллельный bypass.
    """
    if user_id in WHITELIST_IDS:
        return True, ""
    lock = await _get_rate_limit_lock(user_id)
    async with lock:
        allowed, reason = await acheck_rate_limit(user_id)
        if not allowed:
            return False, reason
        await aupdate_rate_limit(user_id)
        return True, ""


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
    """AUDIT M8: subset-match по словам вместо fuzzy in.

    Раньше "fedor" → "fedor milovanov" давало True (ложное совпадение).
    Теперь нужно чтобы ВСЕ слова map_key входили в имя канала.
    """
    if not channel_name:
        return None
    key = channel_name.lower().strip()
    if key in CHANNEL_MAP:
        return CHANNEL_MAP[key]
    key_words = set(re.findall(r"\w+", key))
    if not key_words:
        return None
    for map_key, mapping in CHANNEL_MAP.items():
        map_words = set(re.findall(r"\w+", map_key))
        if map_words and map_words.issubset(key_words):
            return mapping
    return None
# ─── Лимит размера отправляемых файлов ──────────────────────────────────
# Облачный Bot API (api.telegram.org): максимум 50 МБ на отправку.
# Локальный Bot API сервер (LOCAL_BOT_API_URL задан): до 2000 МБ.
# Можно переопределить вручную через MAX_FILE_SIZE_MB в .env.
_default_max_mb = 2000 if LOCAL_BOT_API_URL else 50
try:
    MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "") or _default_max_mb)
except ValueError:
    MAX_FILE_SIZE_MB = _default_max_mb
MAX_PLAYLIST_SIZE = 50
# ─── Gemini модели (v10, 2026-05-21) ────────────────────────────────────
# История: до v7 — 2.5-pro/1.5-pro (платные); v7-v8 — 3.1-pro (ПЛАТНАЯ, убрана)
# Сейчас: gemini-3.5-flash — GA 19.05.2026, бесплатный tier, thinking_level=high
# Резерв при 429: gemini-2.5-flash-lite (в _gemini_text_request)
# ВАЖНО: gemini-3.1-pro ПЛАТНАЯ — не использовать как fallback
# ВАЖНО: смена GEMINI_MODEL автоматически инвалидирует кэш (model_mismatch в database.py)
GEMINI_MODEL  = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

# ─── Умный кэш — версионирование ─────────────────────────────
CACHE_VERSION         = os.getenv("CACHE_VERSION",         "2026-03-22-v5")
CACHE_TTL_DAYS        = int(os.getenv("CACHE_TTL_DAYS",    "45"))
PROMPT_SCHEMA_VERSION = os.getenv("PROMPT_SCHEMA_VERSION", "analysis-deep-v7")
_AUDIO_ANALYSIS_MODE = os.getenv("AUDIO_ANALYSIS_MODE", "deep").strip().lower() or "deep"
if _AUDIO_ANALYSIS_MODE not in {"deep", "balanced", "fast"}:
    _AUDIO_ANALYSIS_MODE = "deep"


def _hash_prompts_source() -> str:
    r"""SHA generation/render contract for cache invalidation.

    Старое имя историческое: это не только prompts.py. Telegraph-страницы
    зависят ещё от renderer/postprocess/audit helpers. Иначе баги вроде
    видимых ``\*\*`` или некликабельных timestamp'ов исправлены в коде, но
    кэш продолжает отдавать старые Telegraph URL без регенерации.
    """
    try:
        from pathlib import Path as _P
        root = _P(__file__).resolve().parent.parent
        rels = [
            "core/prompts.py",
            "core/reasoning_guidance.py",
            "core/prompt_rules.py",
            "core/synopsis_quality.py",
            "core/content_audit.py",
            "core/text_utils.py",
            "core/core_utils.py",
            "core/source_titles.py",
            "converters/md_telegraph.py",
            "services/telegraph.py",
            "services/telegraph_pages.py",
            "services/youtube_transcript.py",
        ]
        h = hashlib.sha256()
        for rel in rels:
            p = root / rel
            h.update(rel.encode("utf-8") + b"\0")
            if p.exists():
                h.update(p.read_bytes())
            h.update(b"\0")
        return h.hexdigest()[:8]
    except Exception:
        return "no-hash"


def get_prompt_fingerprint() -> str:
    """SHA-256 от сочетания версии промпта, режима, модели и SHA исходника промтов."""
    raw = f"{PROMPT_SCHEMA_VERSION}|{GEMINI_MODEL}|{_AUDIO_ANALYSIS_MODE}|{_hash_prompts_source()}"
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


# AUDIT M19: SETTINGS_GROUPS переехал сюда из services/search.py —
# конфигурация UI должна жить рядом с SETTINGS_DEFAULTS/SETTINGS_LABELS.
SETTINGS_GROUPS: list[tuple[str, list[str]]] = [
    ("📋 Конспект", ["synopsis", "caption_full_text", "generate_pdf"]),
    ("📖 Разборы",  ["study_analysis", "reflection_application"]),
    ("📊 Компактные", ["analytics", "questions", "terms"]),
    ("✂️ Шортс",   ["shorts", "shorts_audio_normalize", "shorts_subtitles",
                    "shorts_subtitles_karaoke", "shorts_subtitles_light",
                    "shorts_subtitles_gemini_hints", "shorts_boundary_padding",
                    "shorts_title_poster", "shorts_snapshot",
                    "shorts_montage", "shorts_highlights",
                    "__speed__"]),
    ("🎬 Клипы",    ["clips", "clips_snapshot"]),
    ("🧩 Сегменты", ["segments", "segments_render", "segments_subtitles", "segments_batch_render"]),
    ("🇬🇧 ENG Режим", ["eng_subtitles", "livedub_qa", "livedub_pro_mix", "livedub_autofix"]),
]