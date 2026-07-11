#!/usr/bin/env python3
"""
Utils — cleanup_files, prepare_thumbnail, format_timestamp,
check_rate_limit, update_rate_limit, is_media_url, parse_title.
Извлечено из bot.py строки 730–1045.
"""
from core.globals import (
    BOT_TOKEN, DOWNLOAD_DIR, THUMBS_DIR, HAS_PILLOW,
    _video_processing_locks, _video_locks_mutex, _get_video_lock,
    # FIX #33: импортируем константы лимитов из globals
    COOLDOWN_SECONDS, DAILY_LIMIT,
    DB_PATH, _THUMBS_CLEANUP_INTERVAL,
)
from core.database import (
    _db_conn,
    db_get, db_init, settings_get, settings_get_all,
    # FIX #33: WHITELIST_IDS и CACHE_TTL_DAYS определены в database.py
    WHITELIST_IDS, CACHE_TTL_DAYS,
)
from core.person_names import known_author_from_text

import logging
import os
import re
import shutil
import sqlite3          # FIX #21: не было импорта
import time             # FIX #21: не было импорта
from datetime import date  # FIX #21: не было импорта
from io import BytesIO
from pathlib import Path

# FIX #21: PIL Image — импортируем только если Pillow установлен
if HAS_PILLOW:
    from PIL import Image

logger = logging.getLogger(__name__)

# AUDIT M16: расширенное покрытие — embed/, m.youtube, youtube-nocookie, vkvideo, rutube.
VIDEO_REGEX = re.compile(
    r"(?:https?://)?"
    r"(?:www\.|m\.)?"
    r"(?:"
    r"youtube\.com/(?:watch\?v=|shorts/|live/|embed/|v/)[\w\-]{11}"
    r"|youtu\.be/[\w\-]{11}"
    r"|youtube-nocookie\.com/embed/[\w\-]{11}"
    r"|vkvideo\.ru/video[\-\w]+"
    r"|vkvideo\.ru/@[\w.]+\?z=video[\-\w]+"
    r"|vk\.com/video[\-\w]+"
    r"|rutube\.ru/video/[\w\-]+"
    r")",
    re.IGNORECASE,
)

PLAYLIST_REGEX = re.compile(
    r"(?:https?://)?"
    r"(?:www\.)?"
    r"youtube\.com/(?:playlist\?list=|watch\?.*list=)"
    r"[\w\-]+"
)

TITLE_SEPARATORS = [" — ", " - ", " | ", " // ", ": ", " – "]


# AUDIT L8: дневной лимит сбрасывается в полночь по Москве, а не по UTC сервера.
try:
    from zoneinfo import ZoneInfo
    _LIMIT_TZ = ZoneInfo("Europe/Moscow")
except Exception:
    _LIMIT_TZ = None  # type: ignore


def _today_str() -> str:
    if _LIMIT_TZ is None:
        return str(date.today())
    from datetime import datetime
    return datetime.now(tz=_LIMIT_TZ).date().isoformat()


# FIX: _THUMBS_LAST_CLEANUP мутируется через global в cleanup_files —
# переменная должна быть определена в этом модуле, а не импортироваться
_THUMBS_LAST_CLEANUP: float = 0.0

# ─── Базовые настройки yt-dlp ─────────────────────────────────
# FIX #5: PYTHON_EXEC и COOKIES_FILE перенесены в ffmpeg.py (они нужны там).
# YTDLP_BASE_ARGS строится один раз в ffmpeg.py и импортируется сюда.
from services.ffmpeg import YTDLP_BASE_ARGS  # noqa: E402 (импорт после констант — намеренно

# ─── Вспомогательные функции ─────────────────────────────────

def is_media_url(text: str) -> bool:
    return bool(VIDEO_REGEX.search(text) or PLAYLIST_REGEX.search(text))


def is_playlist_url(text: str) -> bool:
    return bool(PLAYLIST_REGEX.search(text))


def extract_media_url(text: str) -> str | None:
    """AUDIT M16: расширили поддержку — vkvideo.ru, rutube.ru, youtube-nocookie.com."""
    _SUPPORTED_HOSTS = (
        "youtube.com", "youtu.be", "m.youtube.com",
        "youtube-nocookie.com",
        "vkvideo.ru", "vk.com/video",
        "rutube.ru",
    )
    url_match = re.search(r"(https?://[^\s]+)", text)
    if url_match:
        url = url_match.group(0)
        # Чистим хвост (кавычки, скобки, точки в конце)
        url = url.rstrip(').,;:!?»"\'>')
        if any(h in url for h in _SUPPORTED_HOSTS):
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
            # AUDIT R20 (лог 2026-07-09/10, реальные баги): наивный подсчёт
            # слов ниже путает title/performer, когда более длинная сторона —
            # это ОПИСАНИЕ, лишь упоминающее автора («Prayer with R.C.
            # Sproul»), либо короткая сторона — общее название формата, а не
            # имя («Questions and Answers»). Сначала пробуем распознать
            # известного автора по имени — тот же сигнал, что и в
            # _split_title_author_line (LiveDub), а не гадаем по числу слов.
            left_known  = known_author_from_text(left)
            right_known = known_author_from_text(right)
            if left_known and not right_known:
                return left_known, right
            if right_known and not left_known:
                return right_known, left
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
    # Sanitize media_id — defence in depth against glob injection
    media_id = re.sub(r'[*?\[\]/\\]', '_', str(media_id or ''))
    if not media_id:
        return
    for f in DOWNLOAD_DIR.glob(f"{media_id}*"):
        if not f.is_file():
            continue
        # Сохраняем mp3 и обложку для повторного использования
        if keep_mp3 and (f.suffix == ".mp3" or "_thumb" in f.name):
            continue
        # Сохраняем nosub-файлы: нужны для кнопки 🚫Sub в течение 24ч.
        # Их удаляет cleanup_nosub_files() по расписанию.
        if "_nosub.mp4" in f.name:
            continue
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass  # Windows: file may be locked by ffmpeg/Telegram
    # Удаляем устаревшие обложки из THUMBS_DIR (старше CACHE_TTL_DAYS)
    # FIXED #36: обход всего THUMBS_DIR не чаще одного раза в час
    global _THUMBS_LAST_CLEANUP
    _now = time.time()
    if _now - _THUMBS_LAST_CLEANUP >= _THUMBS_CLEANUP_INTERVAL:
        _THUMBS_LAST_CLEANUP = _now
        try:
            _thumbs_cutoff = _now - CACHE_TTL_DAYS * 86400
            for _tf in THUMBS_DIR.iterdir():
                if _tf.is_file() and _tf.stat().st_mtime < _thumbs_cutoff:
                    _tf.unlink(missing_ok=True)
        except Exception:
            pass


def cleanup_stale_downloads(max_age_hours: int = 3) -> int:
    """Удаляет видео-файлы из downloads/ старше max_age_hours часов.

    Защита от orphaned файлов: если highlights/montage упал после скачивания
    видео, файл оставался навсегда.  Вызывается из periodic_maintenance раз в час.

    Чистит только видео-расширения (mp4, mkv, webm, mov) — MP3 и thumb не трогает.
    Порог по умолчанию — 3 часа (достаточно для самого длинного pipeline).
    """
    import time as _time
    cutoff = _time.time() - max_age_hours * 3600
    deleted = 0
    video_exts = {".mp4", ".mkv", ".webm", ".mov"}
    try:
        for f in DOWNLOAD_DIR.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() not in video_exts:
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    deleted += 1
                    logger.info("cleanup_stale_downloads: удалён %s", f.name)
            except OSError as _e:
                logger.debug("cleanup_stale_downloads: не удалось удалить %s: %s", f.name, _e)
    except Exception as _e:
        logger.warning("cleanup_stale_downloads: ошибка обхода downloads/: %s", _e)
    if deleted:
        logger.info("cleanup_stale_downloads: удалено %d видеофайлов старше %dч", deleted, max_age_hours)
    return deleted


def cleanup_botapi_server_files(max_age_hours: int = 24) -> int:
    """Чистит рабочую папку локального telegram-bot-api сервера.

    AUDIT 2026-06-10: сервер копирует каждый file://-upload в свой
    --dir (на проде C:/ProgramData/TelegramBotAPI/data) и НЕ удаляет
    копии сам — диск растёт на сотни МБ с каждого LIVEDUB-видео.
    Папка задаётся через LOCAL_BOT_API_DATA_DIR; без неё — no-op.
    Удаляем только файлы старше max_age_hours (отправленные давно);
    структуру папок и служебные файлы сервера не трогаем.
    """
    import time as _time
    data_dir = os.getenv("LOCAL_BOT_API_DATA_DIR", "").strip()
    if not data_dir:
        # Автообнаружение (round 37: оба стандартных места). LOCALAPPDATA
        # проверяется ПЕРВОЙ — туда сервер падает ACL-fallback'ом и именно
        # она реально растёт; ProgramData может быть недоступна на запись.
        # Без гейта на os.name: на Linux этих путей просто нет (exists()=False),
        # а monkeypatch os.name в тестах ломает сам pathlib (WindowsPath).
        _candidates = []
        _lad = os.environ.get("LOCALAPPDATA", "")
        if _lad:
            _candidates.append(Path(_lad) / "TelegramBotAPI" / "data")
        _candidates.append(Path("C:/ProgramData/TelegramBotAPI/data"))
        for _cand in _candidates:
            if _cand.exists():
                data_dir = str(_cand)
                break
        if not data_dir:
            return 0
    root = Path(data_dir)
    if not root.exists():
        return 0
    cutoff = _time.time() - max_age_hours * 3600
    media_exts = {".mp4", ".mp3", ".m4a", ".mkv", ".webm", ".mov", ".jpg", ".jpeg", ".png", ".pdf", ".oga", ".ogg"}
    deleted = 0
    try:
        for f in root.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in media_exts:
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    deleted += 1
            except OSError:
                continue
    except Exception as e:
        logger.warning(f"cleanup_botapi_server_files error: {e}")
    if deleted:
        logger.info(f"cleanup_botapi_server_files: удалено {deleted} файлов из {data_dir}")
    return deleted


def cleanup_stale_livedub_dirs(max_age_hours: int = 6) -> int:
    """Удаляет osiротевшие livedub_* папки из системного temp.

    Штатно ld_work чистится в finally process_single_video, но при
    жёстком падении процесса (kill, BSOD, Ctrl+C в неудачный момент)
    папки с видео на сотни МБ остаются в %TEMP% навсегда.
    """
    import time as _time
    import tempfile
    cutoff = _time.time() - max_age_hours * 3600
    deleted = 0
    try:
        tmp_root = Path(tempfile.gettempdir())
        for d in tmp_root.glob("livedub_*"):
            if not d.is_dir():
                continue
            try:
                newest = max((f.stat().st_mtime for f in d.rglob("*") if f.is_file()), default=d.stat().st_mtime)
                if newest < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
                    deleted += 1
            except OSError:
                continue
    except Exception as e:
        logger.warning(f"cleanup_stale_livedub_dirs error: {e}")
    if deleted:
        logger.info(f"cleanup_stale_livedub_dirs: удалено {deleted} папок")
    return deleted


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

# AUDIT-V2-CLEANUP: НЕ вызываем при импорте — side effect ломает тесты
# и замедляет старт. Вызов уже есть в periodic maintenance (main.py:209).
# cleanup_nosub_files()


def format_timestamp(seconds) -> str:  # AUDIT-V2-FMT: принимает Any
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return "0:00"
    import math
    if math.isnan(seconds) or math.isinf(seconds):
        return "0:00"
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
    today = _today_str()  # AUDIT L8

    try:
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT last_request, daily_count, daily_date FROM rate_limit WHERE user_id = ?",
                (user_id,)
            ).fetchone()
    except Exception as e:
        logger.warning("check_rate_limit DB error: %s", e, exc_info=True)
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
            f"Ограничение: 1 запрос в "
            + (f"{COOLDOWN_SECONDS // 60} мин." if COOLDOWN_SECONDS >= 60 else f"{COOLDOWN_SECONDS} сек.")
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
    today = _today_str()  # AUDIT L8
    now   = time.time()
    try:
        with _db_conn() as conn:
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


def refund_rate_limit(user_id: int) -> None:
    """FIX AUDIT R4: вернуть зарезервированный слот дневного лимита.

    areserve_rate_limit списывает слот ДО обработки. Если обработка так и не
    началась (например, таймаут ожидания per-video лока), слот возвращаем —
    иначе каждая попытка занятого видео выжигала дневной лимит впустую.
    Cooldown (last_request) намеренно не откатываем: антиспам остаётся.
    """
    if user_id in WHITELIST_IDS:
        return
    today = _today_str()
    try:
        with _db_conn() as conn:
            conn.execute(
                "UPDATE rate_limit SET daily_count = MAX(daily_count - 1, 0) "
                "WHERE user_id = ? AND daily_date = ?",
                (user_id, today),
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"refund_rate_limit DB error: {e}")


# ─── Gemini AI анализ ─────────────────────────────────────────

# ─── Gemini AI анализ ─────────────────────────────────────────


# ─── Безопасное логирование — маскировка секретов ─────────────

import re as _re_utils
_API_KEY_RE = _re_utils.compile(r'AIza[0-9A-Za-z_-]{35}')

def mask_api_key(text: str) -> str:
    """Маскирует API-ключи вида AIza... для безопасного логирования.
    Используется в logger.error/warning перед выводом str(e).
    AUDIT R40: также прячет креды в URL (user:pass@ у proxy) и bot-token —
    эта маска показывается и пользователю (ошибки плейлиста), не только в лог.
    """
    out = _API_KEY_RE.sub('***APIKEY***', str(text))
    try:
        from core.globals import mask_credentials
        out = mask_credentials(out)
    except Exception:
        pass
    return out
