#!/usr/bin/env python3
"""
Globals — импорты и глобальные переменные.
Telegram Bot: Media Audio Converter + AI Analysis
"""

import os
import re
import html as html_mod
import asyncio
import logging
import time
import threading
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# Явно указываем кэш HuggingFace — модели Whisper ищутся здесь
os.environ.setdefault("HF_HOME", str(Path.home() / ".cache" / "huggingface"))

from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# === Flask-сервер (обязателен для Render.com!) ===
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bot is running!"

# Состояние бота для умного /health (обновляется из main.py)
_LAST_BOT_OK_TS: float = 0.0

def mark_bot_alive() -> None:
    """Зовётся из run_bot_async() каждые N секунд, чтобы /health знал что live."""
    global _LAST_BOT_OK_TS
    _LAST_BOT_OK_TS = time.time()


@flask_app.route("/health")
def health():
    """Возвращает 503 если бот не пинговал > 5 минут — Render/Railway перезапустит контейнер."""
    age = time.time() - _LAST_BOT_OK_TS if _LAST_BOT_OK_TS else 999999
    if age > 300:
        return ("STALE", 503)
    return ("OK", 200)

try:
    import PIL  # noqa: F401 — HAS_PILLOW flag only
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

try:
    from google import genai
    from google.genai import types
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False
    genai = None      # type: ignore
    types = None      # type: ignore

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

# FIXED #36: throttle для обхода THUMBS_DIR — не чаще раза в час.
# AUDIT L1: реальная переменная _THUMBS_LAST_CLEANUP живёт в core/utils.py;
# здесь хранится только interval.
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
    except Exception:
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


# ─── Единый конфиг для Gemini-вызовов с аудио ─────────────────
# audio_timestamp=True ОБЯЗАТЕЛЕН для audio-only входов по официальной
# документации Google: https://ai.google.dev/gemini-api/docs/audio
# Без него точность таймкодов снижается ~30%, а у нас на таймкодах построены
# конспекты, Shorts, Clips и Montage.
def _is_gemini_3x(model_name: str) -> bool:
    """Определяет 3.x семейство Gemini моделей.

    Важно: проверяем всё семейство 3.x, а не только текущий default
    gemini-3.5-flash. Иначе резервные/preview-имена вида gemini-3.1-*
    или gemini-3-* получают конфиг как 2.x: temperature вместо
    thinking_config. Это не security-баг, но реальная quality-regression.
    """
    if not model_name:
        return False
    m = model_name.lower().strip()
    # gemini-3.5-flash, gemini-3.1-flash-lite, gemini-3-flash-preview.
    # Use fullmatch so aliases like "my-gemini-3-proxy" do not get 3.x config.
    return bool(re.fullmatch(r'gemini-3(?:[.\-]\d+)?(?:[\-_.].*)?', m))


def _build_thinking_config(level: str = "high"):
    """ULTIMATE FIX: создаёт ThinkingConfig с fallback на старый thinking_budget."""
    if not HAS_GEMINI or types is None:
        return None
    try:
        return types.ThinkingConfig(thinking_level=level)
    except (AttributeError, TypeError):
        # SDK старый — пробуем deprecated thinking_budget
        try:
            budget_map = {"minimal": 4096, "low": 8192, "medium": 16384, "high": 24576}
            return types.ThinkingConfig(thinking_budget=budget_map.get(level, 24576))
        except Exception:
            return None


def make_audio_config(temperature: float = 0.1, max_output_tokens: int = 65536, model_name: str = None, thinking_level: str = "high", response_mime_type: str | None = None, response_schema=None):
    """ULTIMATE FIX 3.5-FLASH: конфиг для audio-вызовов с авто-адаптацией под поколение.

    Для 3.x: thinking_level настраивается по задаче, без temperature
    Для 2.x: temperature как раньше

    max_output_tokens снижен до 40000 для 3.x — оставляем место для thinking-токенов
    (high может занимать до 30K токенов до начала финального ответа).

    AUDIT FIX 2026-05-20: google-genai 1.x не поддерживает audio_timestamp.
    """
    if not HAS_GEMINI or types is None:
        return None

    if model_name is None:
        try:
            from core.database import GEMINI_MODEL as _m
            model_name = _m
        except Exception:
            model_name = ""

    is_3x = _is_gemini_3x(model_name)
    # FIX 2026-05-21 #12 P2: gemini-3.5-flash поддерживает 65k output tokens [Google I/O 2026].
    # Раньше cap=40000 урезал доступный потолок, что снижало качество длинных анализов.
    _safe_max = min(max_output_tokens, 65000) if is_3x else max_output_tokens

    kwargs = {"max_output_tokens": _safe_max}
    if response_mime_type:
        kwargs["response_mime_type"] = response_mime_type
    if response_schema is not None:
        kwargs["response_schema"] = response_schema

    if is_3x:
        # 3.x supports schema-constrained output together with thinking_config.
        # Keep an opt-out env flag for SDK/API regressions.
        if (os.getenv("GEMINI_SCHEMA_THINKING", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}:
            tc = _build_thinking_config(thinking_level)
            if tc is not None:
                kwargs["thinking_config"] = tc
        # На 3.x Google рекомендует НЕ переопределять temperature/top_p/top_k
        # (Gemini Developer Guide май 2026)
    else:
        kwargs["temperature"] = temperature

    return types.GenerateContentConfig(**kwargs)


def make_text_config_smart(temperature: float = 0.4, max_output_tokens: int = 14000, model_name: str = None, thinking_level: str = "high", response_mime_type: str | None = None, response_schema=None):
    """ULTIMATE FIX 3.5-FLASH: конфиг для ТЕКСТОВЫХ вызовов с thinking_level для 3.x.

    Используется в services/telegraph_pages.py:_gemini_text_request для StudyAnalysis
    и ReflectionApplication. Это даёт глубокий анализ на 3.5-flash вместо поверхностного.

    Параметры:
        thinking_level: "minimal" | "low" | "medium" | "high" — на 3.x. По умолчанию "high"
                        для качественного анализа Reflection/Study.
    """
    if not HAS_GEMINI or types is None:
        return None

    if model_name is None:
        try:
            from core.database import GEMINI_MODEL as _m
            model_name = _m
        except Exception:
            model_name = ""

    is_3x = _is_gemini_3x(model_name)
    # FIX 2026-05-21 #12 P2: cap 65k для 3.x (см. выше в make_audio_config).
    _safe_max = min(max_output_tokens, 65000) if is_3x else max_output_tokens

    kwargs = {"max_output_tokens": _safe_max}
    if response_mime_type:
        kwargs["response_mime_type"] = response_mime_type
    if response_schema is not None:
        kwargs["response_schema"] = response_schema

    if is_3x:
        if (os.getenv("GEMINI_SCHEMA_THINKING", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}:
            tc = _build_thinking_config(thinking_level)
            if tc is not None:
                kwargs["thinking_config"] = tc
    else:
        kwargs["temperature"] = temperature

    return types.GenerateContentConfig(**kwargs)


def make_text_config(temperature: float = 0.2, max_output_tokens: int = 14000):
    """Для чисто текстовых вызовов Gemini — audio_timestamp не нужен."""
    if not HAS_GEMINI or types is None:
        return None
    return types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )


# FIX #2: убран вызов db_cleanup_old_records() — функция из database.py,
# которая здесь не импортирована (импортировать нельзя — циклическая зависимость).
# db_cleanup_old_records() вызывается в конце database.py после определения констант.

# ─── Per-video lock ───────────────────────────────────────────
# dict[str, asyncio.Lock] оставлен для совместимости импортов, но добавлена
# мета-информация и централизованный release. TTL чистит только НЕЗАНЯТЫЕ
# lock-и: принудительно "разлочивать" активный asyncio.Lock небезопасно.
_video_processing_locks: dict[str, asyncio.Lock] = {}
_video_lock_meta: dict[str, float] = {}
_video_locks_mutex = threading.Lock()
_VIDEO_LOCK_TTL_SEC = float(os.getenv("VIDEO_LOCK_TTL_SEC", "3600"))

def _cleanup_video_locks_locked(now: float | None = None) -> None:
    now = now or time.time()
    stale = []
    for vid, lock in list(_video_processing_locks.items()):
        last_seen = _video_lock_meta.get(vid, now)
        if not lock.locked() and now - last_seen > _VIDEO_LOCK_TTL_SEC:
            stale.append(vid)
    for vid in stale:
        _video_processing_locks.pop(vid, None)
        _video_lock_meta.pop(vid, None)

def _get_video_lock(video_id: str) -> asyncio.Lock:
    """Возвращает (или создаёт) asyncio.Lock для данного video_id.

    Созданный, но не захваченный lock не блокирует обработку; проблема была
    не в deadlock, а в потенциальном росте словаря. TTL решает именно это.
    """
    video_id = str(video_id or "").strip() or "unknown"
    now = time.time()
    with _video_locks_mutex:
        _cleanup_video_locks_locked(now)
        lock = _video_processing_locks.get(video_id)
        if lock is None:
            lock = asyncio.Lock()
            _video_processing_locks[video_id] = lock
        _video_lock_meta[video_id] = now
        return lock

def _release_video_lock(video_id: str, lock: asyncio.Lock | None = None) -> None:
    """Удаляет per-video lock из registry, если он уже свободен.

    Важно проверять identity: если за время ожидания был создан новый lock с тем
    же video_id, старый обработчик не должен удалить чужую запись.
    """
    video_id = str(video_id or "").strip() or "unknown"
    with _video_locks_mutex:
        current = _video_processing_locks.get(video_id)
        if current is not None and (lock is None or current is lock) and not current.locked():
            _video_processing_locks.pop(video_id, None)
            _video_lock_meta.pop(video_id, None)
        else:
            _video_lock_meta[video_id] = time.time()


_EXHAUSTED_MODELS: dict[str, float] = {}


def _quota_retry_delay_seconds(e, default: int = 3600) -> int:
    m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+)s", str(e), re.IGNORECASE)
    if not m:
        m = re.search(r"retry in\s+([0-9]+(?:\.[0-9]+)?)s", str(e), re.IGNORECASE)
    if m:
        try:
            return max(60, min(int(float(m.group(1))) + 5, 24 * 3600))
        except (TypeError, ValueError):
            pass
    return default


def mark_model_exhausted(model_name: str, err=None, *, seconds: int | None = None) -> None:
    """Remember project/model-level Gemini quota exhaustion in-memory."""
    model = str(model_name or "").strip()
    if not model:
        m = re.search(r"model['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9_.-]+)", str(err), re.IGNORECASE)
        model = m.group(1) if m else ""
    if not model:
        return
    ttl = int(seconds if seconds is not None else _quota_retry_delay_seconds(err))
    _EXHAUSTED_MODELS[model] = time.time() + max(60, ttl)


def is_model_exhausted(model_name: str) -> bool:
    model = str(model_name or "").strip()
    if not model:
        return False
    until = _EXHAUSTED_MODELS.get(model, 0)
    if until and until > time.time():
        return True
    if until:
        _EXHAUSTED_MODELS.pop(model, None)
    return False

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


_current_client_idx = 0

async def gemini_generate(client_list, fn, model_name: str = ""):
    global _current_client_idx
    last_err = None
    if not client_list:
        raise RuntimeError("No Gemini clients available")
        
    if model_name and is_model_exhausted(model_name):
        raise RuntimeError(f"Gemini model quota exhausted in-memory: {model_name}")

    start_idx = _current_client_idx
    for i in range(len(client_list)):
        idx = (start_idx + i) % len(client_list)
        client = client_list[idx]
        _current_client_idx = (idx + 1) % len(client_list)
        
        for attempt in range(3):
            try:
                return await fn(client)
            except Exception as e:
                if is_quota_error(e):
                    # ВАЖНО: Не баним модель глобально при первом 429!
                    # Ключи могут иметь РАЗНЫЕ квоты (разные проекты).
                    logger.warning(f"Gemini квота на ключе {idx}; перехожу к следующему ключу...")
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

# Файловый лог — сохраняет логи при закрытии консоли (Windows local)
try:
    from logging.handlers import RotatingFileHandler
    _file_handler = RotatingFileHandler(
        "bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    _file_handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    ))
    _file_handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(_file_handler)
except Exception:
    pass  # Не блокируем старт если лог-файл недоступен
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
