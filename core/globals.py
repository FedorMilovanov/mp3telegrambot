#!/usr/bin/env python3
"""Globals — imports, clients, configuration and process-wide utilities."""

import asyncio
import html as html_mod
import logging
import os
import re
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("HF_HOME", str(Path.home() / ".cache" / "huggingface"))

from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

flask_app = Flask(__name__)


@flask_app.route("/")
def home():
    return "Bot is running!"


_LAST_BOT_OK_TS: float = 0.0


def mark_bot_alive() -> None:
    global _LAST_BOT_OK_TS
    _LAST_BOT_OK_TS = time.time()


@flask_app.route("/health")
def health():
    age = time.time() - _LAST_BOT_OK_TS if _LAST_BOT_OK_TS else 999999
    if age > 300:
        return ("STALE", 503)
    return ("OK", 200)


try:
    import PIL  # noqa: F401
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

try:
    from google import genai
    from google.genai import types
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False
    genai = None  # type: ignore
    types = None  # type: ignore


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)
THUMBS_DIR = DOWNLOAD_DIR / "thumbs"
THUMBS_DIR.mkdir(exist_ok=True)
DB_PATH = Path("bot_cache.db")

DAILY_LIMIT = 2
COOLDOWN_SECONDS = 60
_THUMBS_CLEANUP_INTERVAL: float = 3600.0

GEMINI_API_KEY_2 = os.getenv("GEMINI_API_KEY_2", "").strip()
GEMINI_API_KEY_3 = os.getenv("GEMINI_API_KEY_3", "").strip()
GEMINI_API_KEY_4 = os.getenv("GEMINI_API_KEY_4", "").strip()
TELEGRAPH_TOKEN = os.getenv("TELEGRAPH_TOKEN", "").strip()


def configured_gemini_service_tier() -> str:
    """Return the source-owned GenerateContent service tier.

    Priority is opt-in and invalid configuration fails during module startup,
    before any request can be routed with an unintended service class.
    """
    value = os.getenv("GEMINI_SERVICE_TIER", "standard").strip().lower()
    value = {"": "standard", "default": "standard"}.get(value, value)
    if value not in {"standard", "priority"}:
        raise RuntimeError(
            "GEMINI_SERVICE_TIER must be 'standard' or 'priority'; "
            f"got {value!r}"
        )
    return value


GEMINI_SERVICE_TIER = configured_gemini_service_tier()

_proxy_url = (
    os.environ.get("HTTPS_PROXY")
    or os.environ.get("https_proxy")
    or os.environ.get("HTTP_PROXY")
    or os.environ.get("http_proxy")
)
_gemini_proxy_log: str = ""
_proxy_was_auto = False
if not _proxy_url:
    _fallback_proxy = (
        os.getenv("GEMINI_PROXY_URL", "").strip()
        or os.getenv("TELEGRAM_PROXY_URL", "").strip()
        or os.getenv("LOCAL_BOT_API_PROXY_URL", "").strip()
    )
    if _fallback_proxy:
        from urllib.parse import urlparse as _urlparse

        _u = _urlparse(_fallback_proxy)
        if (_u.scheme or "").lower().startswith("socks"):
            _sp = _fallback_proxy.split("://", 1)
            _proxy_url = "http://" + _sp[1] if len(_sp) == 2 else _fallback_proxy
            _gemini_proxy_log = (
                f"🌐 Gemini proxy: SOCKS → HTTP fallback: {_proxy_url} "
                f"(из {_fallback_proxy})"
            )
        else:
            _proxy_url = _fallback_proxy
            _gemini_proxy_log = f"🌐 Gemini proxy: {_proxy_url}"
        _proxy_was_auto = True
if _proxy_url:
    os.environ["HTTPS_PROXY"] = _proxy_url
    os.environ["https_proxy"] = _proxy_url
    os.environ["HTTP_PROXY"] = _proxy_url
    os.environ["http_proxy"] = _proxy_url
    if _proxy_was_auto:
        _no_proxy = os.environ.get("NO_PROXY", "")
        _auto_no_proxy = (
            "rutube.ru,api.vk.com,vk.com,api.telegram.org,127.0.0.1,localhost"
        )
        for _host in _auto_no_proxy.split(","):
            if _host not in _no_proxy:
                _no_proxy = f"{_no_proxy},{_host}" if _no_proxy else _host
        os.environ["NO_PROXY"] = _no_proxy
        os.environ["no_proxy"] = _no_proxy

if os.name == "nt":
    try:
        import sys as _sys

        _sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        _front = [
            os.path.dirname(_sys.executable),
            os.path.join(_sysroot, "System32"),
            _sysroot,
        ]
        _cur_path = os.environ.get("PATH", "")
        _cur_dirs = [p for p in _cur_path.split(os.pathsep) if p]
        _front = [d for d in _front if d]
        _front_norm = {os.path.normcase(x) for x in _front}
        _rest = [d for d in _cur_dirs if os.path.normcase(d) not in _front_norm]
        os.environ["PATH"] = os.pathsep.join(_front + _rest)
    except Exception:
        pass

gemini_client = None
gemini_client_2 = None
gemini_client_3 = None
gemini_client_4 = None

# The application owns retry semantics. Keep the SDK at one network attempt so
# future SDK defaults cannot silently multiply our bounded retry/rotation policy.
_gemini_http_options = None
if HAS_GEMINI:
    try:
        _gemini_http_options = types.HttpOptions(
            timeout=900_000,
            retry_options=types.HttpRetryOptions(attempts=1),
        )
    except (AttributeError, TypeError):
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

GEMINI_CLIENTS = [
    c
    for c in [gemini_client, gemini_client_2, gemini_client_3, gemini_client_4]
    if c
]


def _is_gemini_3x(model_name: str) -> bool:
    if not model_name:
        return False
    m = model_name.lower().strip()
    return bool(re.fullmatch(r"gemini-3(?:[.\-]\d+)?(?:[\-_.].*)?", m))


def _build_thinking_config(level: str = "high"):
    if not HAS_GEMINI or types is None:
        return None
    try:
        return types.ThinkingConfig(thinking_level=level.upper())
    except (AttributeError, TypeError):
        try:
            return types.ThinkingConfig(thinking_level=level)
        except Exception:
            try:
                budget_map = {
                    "minimal": 4096,
                    "low": 8192,
                    "medium": 16384,
                    "high": 24576,
                }
                return types.ThinkingConfig(
                    thinking_budget=budget_map.get(level, 24576)
                )
            except Exception:
                return None


def _effective_thinking_level(model_name: str, requested: str) -> str:
    model = str(model_name or "").strip().casefold()
    level = str(requested or "high").strip().lower() or "high"
    if model == "gemini-3.7-flash":
        # Gemini 3.7 supports low/medium/high and rejects minimal. Preserve the
        # HIGH production default, but do not silently turn an explicit LOW
        # recovery request back into HIGH (the old behavior defeated recovery).
        return level if level in {"low", "medium", "high"} else "high"
    if model in {"gemini-3.5-flash-lite", "gemini-3.5-flash"}:
        return "minimal"
    return level


def _apply_gemini_service_tier(kwargs: dict) -> None:
    if GEMINI_SERVICE_TIER == "priority":
        kwargs["service_tier"] = "priority"


def make_audio_config(
    temperature: float = 0.1,
    max_output_tokens: int = 65536,
    model_name: str = None,
    thinking_level: str = "high",
    response_mime_type: str | None = None,
    response_schema=None,
):
    if not HAS_GEMINI or types is None:
        return None
    if model_name is None:
        try:
            from core.database import GEMINI_MODEL as _m
            model_name = _m
        except Exception:
            model_name = ""

    is_3x = _is_gemini_3x(model_name)
    thinking_level = _effective_thinking_level(model_name, thinking_level)
    _safe_max = min(max_output_tokens, 65000) if is_3x else max_output_tokens
    kwargs = {"max_output_tokens": _safe_max}
    if response_mime_type:
        kwargs["response_mime_type"] = response_mime_type
    if response_schema is not None:
        kwargs["response_schema"] = response_schema
    if is_3x:
        if (
            (os.getenv("GEMINI_SCHEMA_THINKING", "1") or "1").strip().lower()
            not in {"0", "false", "no", "off"}
        ):
            tc = _build_thinking_config(thinking_level)
            if tc is not None:
                kwargs["thinking_config"] = tc
    else:
        kwargs["temperature"] = temperature
    _apply_gemini_service_tier(kwargs)
    return types.GenerateContentConfig(**kwargs)


def make_text_config_smart(
    temperature: float = 0.4,
    max_output_tokens: int = 14000,
    model_name: str = None,
    thinking_level: str = "high",
    response_mime_type: str | None = None,
    response_schema=None,
):
    if not HAS_GEMINI or types is None:
        return None
    if model_name is None:
        try:
            from core.database import GEMINI_MODEL as _m
            model_name = _m
        except Exception:
            model_name = ""

    is_3x = _is_gemini_3x(model_name)
    thinking_level = _effective_thinking_level(model_name, thinking_level)
    _safe_max = min(max_output_tokens, 65000) if is_3x else max_output_tokens
    kwargs = {"max_output_tokens": _safe_max}
    if response_mime_type:
        kwargs["response_mime_type"] = response_mime_type
    if response_schema is not None:
        kwargs["response_schema"] = response_schema
    if is_3x:
        if (
            (os.getenv("GEMINI_SCHEMA_THINKING", "1") or "1").strip().lower()
            not in {"0", "false", "no", "off"}
        ):
            tc = _build_thinking_config(thinking_level)
            if tc is not None:
                kwargs["thinking_config"] = tc
    else:
        kwargs["temperature"] = temperature
    _apply_gemini_service_tier(kwargs)
    return types.GenerateContentConfig(**kwargs)


def make_text_config(temperature: float = 0.2, max_output_tokens: int = 14000):
    model_name = (
        os.getenv("GEMINI_MODEL", "gemini-3.7-flash").strip()
        or "gemini-3.7-flash"
    )
    return make_text_config_smart(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        model_name=model_name,
        thinking_level="high",
    )


_video_processing_locks: dict[str, asyncio.Lock] = {}
_video_lock_meta: dict[str, float] = {}
_video_locks_mutex = threading.Lock()
try:
    _VIDEO_LOCK_TTL_SEC = float(
        os.getenv("VIDEO_LOCK_TTL_SEC", "3600").strip() or "3600"
    )
except ValueError:
    _VIDEO_LOCK_TTL_SEC = 3600.0


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
    video_id = str(video_id or "").strip() or "unknown"
    with _video_locks_mutex:
        current = _video_processing_locks.get(video_id)
        if (
            current is not None
            and (lock is None or current is lock)
            and not current.locked()
        ):
            _video_processing_locks.pop(video_id, None)
            _video_lock_meta.pop(video_id, None)
        else:
            _video_lock_meta[video_id] = time.time()


def is_quota_error(e) -> bool:
    s = str(e).lower()
    return "quota" in s or "429" in s or "resource_exhausted" in s


def is_overload_error(e) -> bool:
    s = str(e).lower()
    name = type(e).__name__.lower()
    return (
        "503" in s
        or bool(re.search(r"\b500\b", s))
        or "unavailable" in s
        or "overloaded" in s
        or "high demand" in s
        or "internal server error" in s
        or "remoteprotocolerror" in name
        or "disconnect" in s
        or "server disconnected" in s
        or "without sending a response" in s
    )


_current_client_idx = 0


async def gemini_generate(client_list, fn, model_name: str = ""):
    """Run one Gemini operation with one global transient budget across keys.

    Quota/429 rotates immediately because retrying the same project-bound client
    cannot create capacity. Overload and transport timeout may reuse one client
    once, then rotate, while the global budget remains initial + at most two
    retries. Only confirmed overload publishes the overload circuit.
    """
    global _current_client_idx
    from services import gemini_capacity_control as capacity_control

    last_err = None
    if not client_list:
        raise RuntimeError("No Gemini clients available")

    budget = capacity_control.GeminiRetryBudget()
    start_idx = _current_client_idx
    for i in range(len(client_list)):
        if budget.exhausted:
            break
        idx = (start_idx + i) % len(client_list)
        client = client_list[idx]
        _current_client_idx = (idx + 1) % len(client_list)
        same_client_transients = 0

        while not budget.exhausted:
            budget.claim()
            try:
                return await capacity_control.run_heavy_gemini_call(
                    lambda _client=client: fn(_client)
                )
            except Exception as e:
                if is_quota_error(e):
                    logging.getLogger(__name__).warning(
                        "Gemini квота/429 на клиенте %s; вращаю клиент в пределах общего budget %s/%s",
                        idx,
                        budget.used,
                        budget.limit,
                    )
                    last_err = e
                    break

                overloaded = is_overload_error(e)
                timed_out = capacity_control.is_timeout_error(e)
                if overloaded or timed_out:
                    last_err = e
                    same_client_transients += 1
                    delay = capacity_control.transient_retry_delay(budget.used)
                    if overloaded:
                        capacity_control.note_overload(delay)
                    kind = "500/503 overload" if overloaded else "timeout"
                    logging.getLogger(__name__).warning(
                        "Gemini transient %s, global attempt %s/%s; backoff %.1fс",
                        kind,
                        budget.used,
                        budget.limit,
                        delay,
                    )
                    if budget.exhausted:
                        break
                    # One retry may reuse the same client; a second transient
                    # moves the final remaining attempt to another configured
                    # client. Key count can never expand the global budget.
                    if same_client_transients == 1:
                        await asyncio.sleep(delay)
                        continue
                    break
                raise

    raise last_err or RuntimeError(
        "Gemini transient retry budget exhausted or no configured client succeeded"
    )


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

try:
    from logging.handlers import RotatingFileHandler

    _file_handler = RotatingFileHandler(
        "bot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    _file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    _file_handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(_file_handler)
except Exception:
    pass
logger = logging.getLogger(__name__)

_CRED_PATTERNS: tuple[tuple, ...] = (
    (re.compile(r"(://)[^/\s:@]+:[^/\s@]+@"), r"\1***:***@"),
    (re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}"), "***"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "***"),
)


def mask_credentials(text: str) -> str:
    out = str(text)
    for _pat, _repl in _CRED_PATTERNS:
        out = _pat.sub(_repl, out)
    return out


class _TokenMaskFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self._secrets: list[str] | None = None

    def _get_secrets(self) -> list[str]:
        if self._secrets is None:
            candidates = [
                os.getenv("BOT_TOKEN", "").strip(),
                os.getenv("GEMINI_API_KEY", "").strip(),
                os.getenv("GEMINI_API_KEY_2", "").strip(),
                os.getenv("GEMINI_API_KEY_3", "").strip(),
                os.getenv("GEMINI_API_KEY_4", "").strip(),
                os.getenv("TELEGRAPH_TOKEN", "").strip(),
                os.getenv("VK_API_TOKEN", "").strip(),
            ]
            self._secrets = [s for s in candidates if len(s) >= 8]
        return self._secrets

    def _mask(self, text: str) -> str:
        for secret in self._get_secrets():
            if secret in text:
                text = text.replace(secret, "***")
        return mask_credentials(text)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = self._mask(record.msg)
            if record.args:
                formatted = record.getMessage()
                masked = self._mask(formatted)
                if masked != formatted:
                    record.msg = masked
                    record.args = ()
            elif record.msg is not None and not isinstance(record.msg, str):
                record.msg = self._mask(str(record.msg))
            if record.exc_info and record.exc_info[1] is not None and not record.exc_text:
                exc_text = logging.Formatter().formatException(record.exc_info)
                record.exc_text = self._mask(exc_text)
        except Exception:
            pass
        return True


_token_filter = _TokenMaskFilter()
logging.getLogger().addFilter(_token_filter)
for _h in logging.getLogger().handlers:
    _h.addFilter(_token_filter)

logging.getLogger("httpx").setLevel(logging.WARNING)
