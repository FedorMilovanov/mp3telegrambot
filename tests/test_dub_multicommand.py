"""Direct behavior tests for reliable multiline Dub command dispatch."""

import asyncio
from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop

import handlers.dub_multicommand as multicommand


def test_parser_rejects_malformed_empty_and_oversized_messages():
    assert multicommand.parse_dub_command_lines("") == []
    assert multicommand.parse_dub_command_lines(None) == []
    assert multicommand.parse_dub_command_lines("/dubworker\nnot-a-command") == []
    assert multicommand.parse_dub_command_lines("/dubworker\n/start") == []
    assert multicommand.parse_dub_command_lines("\n".join(["/dubworker"] * 9)) == []


def test_parser_preserves_casefolded_names_and_argument_order():
    assert multicommand.parse_dub_command_lines("  /DUBRUN@Bot_Name one two  \n\n/dubstatus last ") == [
        ("dubrun", ["one", "two"]),
        ("dubstatus", ["last"]),
    ]


def test_command_dispatch_table_covers_every_supported_command():
    handlers = multicommand._command_handlers()

    assert set(handlers) == multicommand._SUPPORTED_COMMANDS
    assert len(handlers) == 17
    assert all(callable(handler) for handler in handlers.values())


def test_non_multicommand_message_returns_without_loading_handlers(monkeypatch):
    def fail_if_loaded():
        raise AssertionError("single commands must use their normal handlers")

    monkeypatch.setattr(multicommand, "_command_handlers", fail_if_loaded)
    update = SimpleNamespace(effective_message=SimpleNamespace(text="/dubworker"))
    context = SimpleNamespace(args=["unchanged"])

    asyncio.run(multicommand.handle_dub_multicommand(update, context))

    assert context.args == ["unchanged"]


def test_each_command_runs_once_in_order_and_original_args_are_restored(monkeypatch):
    calls = []

    async def first(_update, context):
        calls.append(("first", list(context.args)))

    async def second(_update, context):
        calls.append(("second", list(context.args)))

    monkeypatch.setattr(
        multicommand,
        "_command_handlers",
        lambda: {"dubnew": first, "dubstatus": second},
    )
    update = SimpleNamespace(effective_message=SimpleNamespace(text="/dubnew one two\n/dubstatus last"))
    original_args = ["original"]
    context = SimpleNamespace(args=original_args)

    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(multicommand.handle_dub_multicommand(update, context))

    assert calls == [("first", ["one", "two"]), ("second", ["last"])]
    assert context.args == original_args
    assert context.args == ["original"]


def test_original_args_are_restored_when_a_command_fails(monkeypatch):
    async def fail(_update, context):
        assert context.args == ["bad"]
        raise RuntimeError("boom")

    monkeypatch.setattr(
        multicommand,
        "_command_handlers",
        lambda: {"dubrun": fail, "dubstatus": fail},
    )
    update = SimpleNamespace(effective_message=SimpleNamespace(text="/dubrun bad\n/dubstatus last"))
    context = SimpleNamespace(args=["original"])

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(multicommand.handle_dub_multicommand(update, context))

    assert context.args == ["original"]


def test_registration_is_idempotent_and_runs_before_normal_handlers():
    class Application:
        def __init__(self):
            self.bot_data = {}
            self.registrations = []

        def add_handler(self, handler, group):
            self.registrations.append((handler, group))

    application = Application()
    multicommand.register_dub_multicommand_handler(application)
    multicommand.register_dub_multicommand_handler(application)

    assert len(application.registrations) == 1
    handler, group = application.registrations[0]
    assert group == -100
    assert handler.block is True
    assert handler.callback is multicommand.handle_dub_multicommand
    assert application.bot_data["dub_multicommand_registered"] is True
