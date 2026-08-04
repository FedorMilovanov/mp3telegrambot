"""AUDIT R4 (2026-07-05): infrastructure regressions.

Covers: event-loop-bound lock cleanup on restart, token masking on handlers
and tracebacks, tzdata for Moscow-midnight limits, SIGTERM in health-check
mode, /stop process exit, guarded env parsing, Start Bot.bat launcher.
"""
import logging
import sys
from pathlib import Path


def test_restart_clears_rate_limit_and_video_lock_meta():
    """asyncio.Lock'и переживают пересоздание event loop после краша:
    без очистки первый же не-VIP запрос падает с RuntimeError
    "bound to a different event loop"."""
    src = Path("main.py").read_text(encoding="utf-8")
    assert "_rate_limit_async_locks.clear()" in src
    assert "_rate_limit_locks_guard = asyncio.Lock()" in src
    assert "_video_lock_meta.clear()" in src


def test_token_mask_filter_attached_to_handlers():
    """Фильтры логгера НЕ выполняются для записей дочерних логгеров при
    propagation — маскирование обязано висеть на хендлерах."""
    src = Path("core/globals.py").read_text(encoding="utf-8")
    assert "_h.addFilter(_token_filter)" in src


def test_token_mask_filter_masks_child_logger_record(monkeypatch):
    monkeypatch.setenv("VK_API_TOKEN", "vk1.a.SECRETSECRETSECRET")
    from core.globals import _TokenMaskFilter

    f = _TokenMaskFilter()
    rec = logging.LogRecord(
        name="services.search", level=logging.ERROR, pathname=__file__,
        lineno=1, msg="GET ?access_token=vk1.a.SECRETSECRETSECRET failed",
        args=(), exc_info=None,
    )
    assert f.filter(rec) is True
    assert "SECRETSECRET" not in rec.getMessage()
    assert "***" in rec.getMessage()


def test_token_mask_filter_masks_exception_traceback(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1234567890:AAASECRETTOKENVALUE")
    from core.globals import _TokenMaskFilter

    f = _TokenMaskFilter()
    try:
        raise RuntimeError("boom 1234567890:AAASECRETTOKENVALUE")
    except RuntimeError:
        exc_info = sys.exc_info()
    rec = logging.LogRecord(
        name="main", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="Unhandled handler error", args=(), exc_info=exc_info,
    )
    f.filter(rec)
    assert rec.exc_text
    assert "AAASECRETTOKENVALUE" not in rec.exc_text
    assert "***" in rec.exc_text


def test_tzdata_declared_for_moscow_limit_reset():
    """Windows не имеет IANA tz-базы: без tzdata ZoneInfo("Europe/Moscow")
    падает и дневной лимит тихо сбрасывается по времени сервера."""
    req = Path("requirements.txt").read_text(encoding="utf-8")
    assert "tzdata" in req


def test_sigterm_registered_in_main_thread_for_health_check_mode():
    """В режиме health-check run_bot живёт в daemon-потоке, где signal.signal
    не работает. SIGTERM обязан регистрироваться в main() (main thread)."""
    src = Path("main.py").read_text(encoding="utf-8")
    assert "_STOP_EVENT = threading.Event()" in src
    assert "signal.signal(_sig, _sig_stop)" in src
    # watchdog обязан слушать событие из сигнала, а не только bot_data
    assert 'app.bot_data.get("stop_requested") or _STOP_EVENT.is_set()' in src


def test_stop_terminates_process_in_health_check_mode():
    """После /stop waitress в main thread жил бы вечно, а Render перезапускал
    бы контейнер по STALE health-check, тихо отменяя /stop."""
    src = Path("main.py").read_text(encoding="utf-8")
    assert "_run_bot_then_exit" in src
    assert "os._exit(0)" in src


def test_default_scope_set_my_commands_guarded():
    """Сбой косметической регистрации меню не должен ронять запущенный
    polling в полный restart-цикл."""
    src = Path("main.py").read_text(encoding="utf-8")
    idx = src.find("set_my_commands(default_commands")
    assert idx != -1
    assert "try:" in src[idx - 120:idx]


def test_env_int_parsing_guarded():
    """Пустое/нечисловое значение в .env не должно валить импорт трейсбеком —
    паттерн MAX_FILE_SIZE_MB (try/except с дефолтом) обязателен для всех."""
    db = Path("core/database.py").read_text(encoding="utf-8")
    assert 'int(os.getenv("CACHE_TTL_DAYS", "45").strip() or "45")' in db
    g = Path("core/globals.py").read_text(encoding="utf-8")
    assert 'float(os.getenv("VIDEO_LOCK_TTL_SEC", "3600").strip() or "3600")' in g
    m = Path("main.py").read_text(encoding="utf-8")
    assert 'int(os.environ.get("PORT", "10000").strip() or "10000")' in m


def test_start_bat_accepts_every_supported_python_without_single_version_pin():
    """Bootstrap may prefer newer supported Python, but must fall through
    3.13 -> 3.12 -> 3.11 instead of requiring one exact installation."""
    bat = Path("Start Bot.bat").read_text(encoding="utf-8")
    assert "for %%V in (3.13 3.12 3.11) do (" in bat
    assert 'set "PYTHON_COMMAND=py -%%V"' in bat
    assert "(3, 11) <= sys.version_info[:2] < (3, 14)" in bat
