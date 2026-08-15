from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path

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


def test_application_owner_wires_polling_reliability_without_ptb_monkey_patch() -> None:
    main_source = Path("main.py").read_text(encoding="utf-8")
    runtime_source = Path("services/polling_reliability_runtime.py").read_text(encoding="utf-8")
    manifest_source = Path("services/runtime_manifest.py").read_text(encoding="utf-8")
    assert "TypeHandler(Update, _pending_update_guard)" in main_source
    assert "group=-100" in main_source
    assert "accept_pending_update(update, app.bot_data)" in main_source
    assert "drop_pending_updates=False" in main_source
    assert "error_callback=polling_error_callback" in main_source
    assert "Updater.start_polling =" not in runtime_source
    assert "Application.process_update =" not in runtime_source
    assert '"polling-reliability"' not in manifest_source


def test_accept_pending_update_records_live_command() -> None:
    data = {}
    update = _update("/mode", age_seconds=1)
    assert runtime.accept_pending_update(update, data) is True
    assert data["telegram_last_update_id"] == 123
    assert data["telegram_last_update_monotonic"] > 0


def test_accept_pending_update_rejects_stale_backlog() -> None:
    data = {}
    assert runtime.accept_pending_update(_update("https://example.test", age_seconds=901), data) is False
    assert data == {}
