from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from telegram.ext import Application, Updater

from services import polling_reliability_runtime as runtime


def _update(text: str, age_seconds: int = 0):
    message = SimpleNamespace(
        text=text,
        date=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    )
    return SimpleNamespace(effective_message=message, update_id=123)


def test_recent_pending_command_survives_restart() -> None:
    update = _update("   /mode", age_seconds=3600)
    reason, age = runtime._stale_pending_reason(update)
    assert runtime._is_command(update) is True
    assert reason is None
    assert age is not None and age >= 3590


def test_very_old_pending_command_is_not_executed(monkeypatch) -> None:
    monkeypatch.delenv("BOT_PENDING_COMMAND_MAX_AGE_SEC", raising=False)
    reason, age = runtime._stale_pending_reason(
        _update("/dubfix project all", age_seconds=7 * 3600)
    )
    assert reason == "stale-command"
    assert age is not None and age >= 7 * 3600 - 10


def test_stale_noncommand_backlog_is_dropped(monkeypatch) -> None:
    monkeypatch.delenv("BOT_PENDING_NONCOMMAND_MAX_AGE_SEC", raising=False)
    assert runtime._is_command(_update("https://youtube.com/watch?v=abc")) is False
    reason, _age = runtime._stale_pending_reason(
        _update("https://youtube.com/watch?v=abc", age_seconds=901)
    )
    assert reason == "stale-noncommand"


def test_pending_age_environment_is_bounded(monkeypatch) -> None:
    monkeypatch.setenv("BOT_PENDING_COMMAND_MAX_AGE_SEC", "1")
    assert runtime._max_stale_command_age() == 5 * 60
    monkeypatch.setenv("BOT_PENDING_COMMAND_MAX_AGE_SEC", str(48 * 3600))
    assert runtime._max_stale_command_age() == 24 * 3600
    monkeypatch.setenv("BOT_PENDING_COMMAND_MAX_AGE_SEC", "invalid")
    assert runtime._max_stale_command_age() == 6 * 3600


def test_runtime_is_installed_on_services_import() -> None:
    assert getattr(Updater.start_polling, "_mp3bot_reliable_polling", False)
    assert getattr(Application.process_update, "_mp3bot_update_probe", False)
