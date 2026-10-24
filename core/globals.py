#!/usr/bin/env python3
"""Globals — imports, clients, configuration and process-wide utilities."""

import asyncio
import html as html_mod
import json
import logging
import os
import re
import threading
import time
from typing import Any, Awaitable, Callable
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("HF_HOME", str(Path.home() / ".cache" / "huggingface"))

from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from core import runtime_status
from core.model_catalog import (
    ROLE_PRIMARY,
    configured_model,
    thinking_level_for,
    uses_thinking_config,
)
from services import gemini_quota_domains as quota_domains

flask_app = Flask(__name__)

# `core.runtime_status` импортируется здесь, на старте процесса: он запоминает
# время инициализации, и `uptime_seconds` в /healthz не должен зависеть от
# того, когда впервые пришёл запрос.


@flask_app.route("/")
def home() -> str:
    return "Bot is running!"


_LAST_BOT_OK_TS: float = 0.0


def mark_bot_alive() -> None:
    global _LAST_BOT_OK_TS
    _LAST_BOT_OK_TS = time.time()


@flask_app.route("/health")
def health() -> tuple[str, int]:
    """Бинарный ответ для внешних мониторов — оставлен как есть.

    Его формат (`OK` / `503 STALE`) менять нельзя: на него могут смотреть
    сторонние проверки, которые ничего не знают про наше JSON-схему.
    """
    age = time.time() - _LAST_BOT_OK_TS if _LAST_BOT_OK_TS else 999999
    if age > 300:
        return ("STALE", 503)
    return ("OK", 200)


@flask_app.route("/healthz")
def healthz() -> Any:
    """Детальный отчёт: что именно не так, а не только «что-то не так».

    Отдаёт 503, если бот не обрабатывает апдейты, недоступна БД, мало места или
    нет ключей Gemini. Секретов и путей в ответе нет.
    """
    from core.resource_scheduler import scheduler
    from core.version import __version__
    from services.health_report import (
        build_health,
        database_ok,
        disk_free_gb,
        gemini_keys_configured,
    )

    payload, status = build_health(
        snapshot=runtime_status.snapshot(),
        db_ok=database_ok(DB_PATH),
        free_disk_gb=disk_free_gb(Path(DB_PATH).parent),
        version=__version__,
        keys_configured=gemini_keys_configured(),
        jobs=scheduler.utilization(),
    )
    return flask_app.response_class(
        response=json.dumps(payload, ensure_ascii=False),
        status=status,
        mimetype="application/json",
    )


@flask_app.route("/metrics")
def metrics() -> Any:
    """Метрики в формате Prometheus text exposition."""
    from core.resource_scheduler import scheduler
    from services.health_report import (
        database_ok,
        disk_free_gb,
        gemini_keys_configured,
        render_prometheus,
    )

    text = render_prometheus(
        snapshot=runtime_status.snapshot(),
        db_ok=database_ok(DB_PATH),
        free_disk_gb=disk_free_gb(Path(DB_PATH).parent),
        jobs=scheduler.utilization(),
        keys_configured=gemini_keys_configured(),
    )
    # content_type, а не mimetype: иначе Flask дописывает свой charset и в
    # заголовке оказывается «charset=utf-8; charset=utf-8».
    return flask_app.response_class(
        response=text,
        status=200,
        content_type="text/plain; version=0.0.4; charset=utf-8",
    )


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
    # google-genai — опциональная зависимость: ниже весь код конфигурации
    # проверяет HAS_GEMINI перед обращением, поэтому подмена модулей на None
    # безопасна и намеренна.
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]


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


def _make_gemini_client(api_key: str) -> Any:
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
_GEMINI_KEY_SLOTS = (
    GEMINI_API_KEY,
    GEMINI_API_KEY_2,
    GEMINI_API_KEY_3,
    GEMINI_API_KEY_4,
)
GEMINI_CLIENT_QUOTA_DOMAINS = (
    quota_domains.quota_domains_for_keys(_GEMINI_KEY_SLOTS)
    if HAS_GEMINI
    else []
)
if len(GEMINI_CLIENT_QUOTA_DOMAINS) != len(GEMINI_CLIENTS):
    raise RuntimeError(
        "Gemini client/quota-domain metadata alignment failed during startup"
    )


def _is_gemini_3x(model_name: str) -> bool:
    """Whether the model takes ``thinking_config`` instead of ``temperature``.

    Kept under the historical name because production callers and regression
    tests import it. The capability itself is owned by ``core.model_catalog``
    and covers Gemini 4 too, so a new flagship model needs no change here.
    """
    return uses_thinking_config(model_name)


#: Бюджет размышления для случаев, когда уровень нельзя передать enum'ом
#: (незнакомое значение или SDK без ``ThinkingLevel``). Число принимают все
#: версии google-genai.
_THINKING_BUDGETS = {
    "MINIMAL": 4096,
    "LOW": 8192,
    "MEDIUM": 16384,
    "HIGH": 24576,
}
_DEFAULT_THINKING_BUDGET = 24576


def _thinking_level_enum(level: str) -> Any:
    """Уровень как enum SDK; ``None``, если такого уровня нет.

    SDK ожидает ``ThinkingLevel``, а строку принимает лишь по факту (не по
    типам). Поэтому переводим имя явно и заранее знаем, когда уровня нет, —
    вместо того чтобы полагаться на исключение внутри конструктора.
    """
    enum_type = getattr(types, "ThinkingLevel", None) if types is not None else None
    if enum_type is None:
        return None
    return getattr(enum_type, str(level or "").strip().upper(), None)


def _build_thinking_config(level: str = "high") -> Any:
    if not HAS_GEMINI or types is None:
        return None
    enum_level = _thinking_level_enum(level)
    if enum_level is not None:
        try:
            return types.ThinkingConfig(thinking_level=enum_level)
        except (AttributeError, TypeError):
            pass
    name = str(level or "").strip().upper()
    try:
        return types.ThinkingConfig(
            thinking_budget=_THINKING_BUDGETS.get(name, _DEFAULT_THINKING_BUDGET)
        )
    except Exception:
        return None


def _effective_thinking_level(model_name: str, requested: str) -> str:
    """Clamp a thinking level to what the model accepts (catalog-owned)."""
    return thinking_level_for(model_name, requested)


def _apply_gemini_service_tier(kwargs: dict[str, Any]) -> None:
    if GEMINI_SERVICE_TIER == "priority":
        kwargs["service_tier"] = "priority"


def make_audio_config(
    temperature: float = 0.1,
    max_output_tokens: int = 65536,
    model_name: str | None = None,
    thinking_level: str = "high",
    response_mime_type: str | None = None,
    response_schema: Any = None,
) -> Any:
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
    # Значения разнотипные (int/str/float/config): без аннотации mypy
    # выводит dict[str, int] и отвергает остальные ключи.
    kwargs: dict[str, Any] = {"max_output_tokens": _safe_max}
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
    model_name: str | None = None,
    thinking_level: str = "high",
    response_mime_type: str | None = None,
    response_schema: Any = None,
) -> Any:
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
    # Значения разнотипные (int/str/float/config): без аннотации mypy
    # выводит dict[str, int] и отвергает остальные ключи.
    kwargs: dict[str, Any] = {"max_output_tokens": _safe_max}
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


def make_text_config(temperature: float = 0.2, max_output_tokens: int = 14000) -> Any:
    model_name = configured_model(ROLE_PRIMARY)
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


def is_quota_error(e: object) -> bool:
    s = str(e).lower()
    return "quota" in s or "429" in s or "resource_exhausted" in s


def is_overload_error(e: object) -> bool:
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


def _quota_domains_for_client_list(client_list: list[Any]) -> list[str]:
    """Return privacy-safe domain metadata for known global client objects only."""
    domain_by_client_id = {
        id(client): domain
        for client, domain in zip(GEMINI_CLIENTS, GEMINI_CLIENT_QUOTA_DOMAINS)
    }
    return [domain_by_client_id.get(id(client), "") for client in client_list]


_current_client_idx = 0


async def gemini_generate(
    client_list: list[Any],
    fn: Callable[[Any], Awaitable[Any]],
    model_name: str = "",
    response_validator: Callable[[Any], object] | None = None,
) -> Any:
    """Run one Gemini operation with one global transient budget across keys.

    Quota/429 rotates immediately. If an explicitly labeled credential returns a
    project-scoped 429, later known credentials with the same local opaque label
    are skipped for this call only. Generic 429 remains conservative and rotates
    normally. Overload and transport timeout may reuse one client once, then
    rotate, while the global budget remains initial + at most two retries. Only
    confirmed overload publishes the overload circuit. An optional caller
    response validator may reject a transport-successful response; that consumes
    the current global attempt and rotates without marking any quota domain.
    """
    global _current_client_idx
    from services import gemini_capacity_control as capacity_control

    last_err: Exception | None = None
    if not client_list:
        raise RuntimeError("No Gemini clients available")

    budget = capacity_control.GeminiRetryBudget()
    client_quota_domains = _quota_domains_for_client_list(client_list)
    exhausted_project_quota_domains: set[str] = set()
    same_project_domain_skipped = 0
    start_idx = _current_client_idx
    for i in range(len(client_list)):
        if budget.exhausted:
            break
        idx = (start_idx + i) % len(client_list)
        quota_domain = client_quota_domains[idx]
        if quota_domain and quota_domain in exhausted_project_quota_domains:
            same_project_domain_skipped += 1
            _current_client_idx = (idx + 1) % len(client_list)
            logging.getLogger(__name__).info(
                "Gemini client %s skipped after project-scoped quota on same explicit "
                "local domain; label omitted; skipped=%s",
                idx,
                same_project_domain_skipped,
            )
            continue

        client = client_list[idx]
        _current_client_idx = (idx + 1) % len(client_list)
        same_client_transients = 0

        while not budget.exhausted:
            budget.claim()
            try:
                # Обычная функция вместо lambda с дефолтным аргументом: у
                # lambda mypy не выводит тип параметра, а тут он очевиден.
                def _bound_call(_client: Any = client) -> Awaitable[Any]:
                    return fn(_client)

                result = await capacity_control.run_heavy_gemini_call(_bound_call)
                if response_validator is not None and not bool(response_validator(result)):
                    last_err = RuntimeError("Gemini response rejected by caller validator")
                    break
                return result
            except Exception as e:
                if is_quota_error(e):
                    project_scoped = bool(
                        quota_domain
                        and quota_domains.is_project_scoped_quota_error(e)
                    )
                    if project_scoped:
                        exhausted_project_quota_domains.add(quota_domain)
                    logging.getLogger(__name__).warning(
                        "Gemini квота/429 на клиенте %s; project_scoped=%s; "
                        "вращаю клиент в пределах общего budget %s/%s",
                        idx,
                        project_scoped,
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


# AUDIT 2026-10-24: вместо basicConfig — общая настройка проекта.
# Она даёт LOG_FORMAT=text|json, LOG_FILE/LOG_LEVEL, маскирование значений
# секретов и update_id в каждой строке (см. core/logging_setup.py).
from core.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

_CRED_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
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
    def __init__(self) -> None:
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
