from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from telegram.ext import ApplicationBuilder, CallbackQueryHandler, MessageHandler

from handlers.dub_wizard import (
    _home_keyboard as dub_home_keyboard,
    register_dub_wizard_handlers,
)
from handlers.mode_command import (
    _analysis_keyboard,
    _clear_dub_wizard_state,
    _mode_home_keyboard,
)


def _callbacks(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


def test_unified_mode_menu_exposes_clear_admin_paths() -> None:
    markup = _mode_home_keyboard("rus", is_admin=True)
    assert len(markup.inline_keyboard) == 4
    assert all(len(row) <= 2 for row in markup.inline_keyboard)
    assert _callbacks(markup) == [
        "mode_menu:analysis",
        "dubwiz|mode|gemini",
        "dubwiz|mode|direct",
        "dubwiz|projects|list",
        "mode_menu:dubcheck",
    ]
    assert markup.inline_keyboard[1][0].text == "🤖 Дубляж — Gemini MAX"
    assert markup.inline_keyboard[2][0].text == "✍️ Дубляж — мой готовый SRT"


def test_regular_user_sees_only_normal_processing_modes() -> None:
    assert _callbacks(_mode_home_keyboard("rus", is_admin=False)) == [
        "mode_menu:analysis"
    ]


def test_analysis_menu_is_compact_and_has_back_button() -> None:
    markup = _analysis_keyboard("eng_fast")
    assert len(markup.inline_keyboard) == 5
    assert all(len(row) <= 2 for row in markup.inline_keyboard)
    assert _callbacks(markup) == [
        "set_mode:rus",
        "set_mode:eng",
        "set_mode:eng_fast",
        "set_mode:eng_fast_qa",
        "set_mode:shorts_max",
        "set_mode:translation_editorial",
        "mode_menu:home",
    ]
    assert markup.inline_keyboard[1][0].text.startswith("✓ ")
    assert markup.inline_keyboard[-1][0].text == "↩️ Все режимы"


def test_leaving_dub_wizard_clears_hidden_prompt_state() -> None:
    context = SimpleNamespace(
        user_data={
            "dub_universal_wizard": {"awaiting": "url", "mode": "gemini"},
            "other": "keep",
        }
    )
    assert _clear_dub_wizard_state(context) is True
    assert context.user_data == {"other": "keep"}
    assert _clear_dub_wizard_state(context) is False


def test_dub_home_returns_to_all_modes() -> None:
    assert "mode_menu:home" in _callbacks(dub_home_keyboard())


def test_dub_wizard_handlers_run_before_generic_link_handlers() -> None:
    application = ApplicationBuilder().token("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi").build()
    register_dub_wizard_handlers(application)

    callback_handlers = [
        handler
        for handler in application.handlers[-60]
        if isinstance(handler, CallbackQueryHandler)
    ]
    assert any(
        getattr(handler.pattern, "pattern", "") == r"^dubwiz\|"
        for handler in callback_handlers
    )

    early_message_handlers = [
        handler
        for handler in application.handlers[-59]
        if isinstance(handler, MessageHandler)
    ]
    assert len(early_message_handlers) == 2


def test_main_routes_mode_callbacks_before_global_callback() -> None:
    source = Path("main.py").read_text(encoding="utf-8")
    mode_route = 'handle_mode_callback, pattern=r"^(?:set_mode:|mode_menu:)"'
    global_route = "CallbackQueryHandler(handle_callback)"
    assert mode_route in source
    assert source.index(mode_route) < source.index(global_route)
    assert 'BotCommand("dub",        "🎙 Дубляж: Gemini MAX / готовый SRT")' in source


def test_dub_command_has_one_owner_and_start_cancels_wizard() -> None:
    wizard = Path("handlers/dub_wizard.py").read_text(encoding="utf-8")
    commands = Path("handlers/dub_commands.py").read_text(encoding="utf-8")
    start_commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert wizard.count('CommandHandler("dub", dub_home_command') == 1
    assert 'CommandHandler("dub", dub_command' not in commands
    assert 'context.user_data.pop("dub_universal_wizard", None)' in start_commands


def test_dubcheck_does_not_block_telegram_event_loop() -> None:
    source = Path("handlers/dub_health.py").read_text(encoding="utf-8")
    assert "checks = await asyncio.to_thread(collect_dub_health)" in source
