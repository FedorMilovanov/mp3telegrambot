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


def test_pending_command_is_recognised_even_when_old() -> None:
    update = _update("   /mode", age_seconds=3600)
    assert runtime._is_command(update) is True
    assert runtime._message_age_seconds(update) >= 3590


def test_noncommand_is_not_misclassified() -> None:
    assert runtime._is_command(_update("https://youtube.com/watch?v=abc")) is False


def test_runtime_is_installed_on_services_import() -> None:
    assert getattr(Updater.start_polling, "_mp3bot_reliable_polling", False)
    assert getattr(Application.process_update, "_mp3bot_update_probe", False)
