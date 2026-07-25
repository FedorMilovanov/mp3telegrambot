from __future__ import annotations

import asyncio
from types import SimpleNamespace

from services import livedub_help_runtime as help_runtime


class _Message:
    def __init__(self):
        self.text = ""
        self.parse_mode = None

    async def reply_text(self, text, **kwargs):
        self.text = text
        self.parse_mode = kwargs.get("parse_mode")


def test_help_describes_both_mp3_variants(monkeypatch):
    import core.database as database
    import core.globals as globals_module

    monkeypatch.setattr(database, "WHITELIST_IDS", set())
    monkeypatch.setattr(globals_module, "DAILY_LIMIT", 5)
    message = _Message()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        message=message,
    )

    asyncio.run(help_runtime.help_command(update, None))

    assert message.parse_mode == "HTML"
    assert "чистый русский MP3" in message.text
    assert "финальный объединённый MP3" in message.text
    assert "ENG Quick QA" in message.text
    assert "только переведённое видео" not in message.text
    assert "5 видео/день" in message.text


def test_installer_rebinds_commands_and_main(monkeypatch):
    import handlers.commands as commands

    main_stub = SimpleNamespace(help_command=object())
    monkeypatch.setattr(help_runtime, "_INSTALLED", False)
    help_runtime.install_livedub_help_runtime(main_stub)

    assert commands.help_command is help_runtime.help_command
    assert main_stub.help_command is help_runtime.help_command
