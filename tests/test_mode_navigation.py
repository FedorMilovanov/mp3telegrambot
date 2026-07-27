from __future__ import annotations

from handlers.dub_wizard import _home_keyboard as dub_home_keyboard
from handlers.mode_command import _analysis_keyboard, _mode_home_keyboard


def _callbacks(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data]


def test_unified_mode_menu_exposes_both_dub_paths_for_admin() -> None:
    callbacks = _callbacks(_mode_home_keyboard("rus", is_admin=True))
    assert "mode_menu:analysis" in callbacks
    assert "dubwiz|mode|gemini" in callbacks
    assert "dubwiz|mode|direct" in callbacks
    assert "dubwiz|home|show" in callbacks
    assert "dubwiz|projects|list" in callbacks


def test_regular_user_sees_only_normal_processing_modes() -> None:
    assert _callbacks(_mode_home_keyboard("rus", is_admin=False)) == ["mode_menu:analysis"]


def test_analysis_menu_is_compact_and_has_back_button() -> None:
    markup = _analysis_keyboard("eng_fast")
    assert len(markup.inline_keyboard) == 3
    assert _callbacks(markup) == [
        "set_mode:rus", "set_mode:eng", "set_mode:eng_fast",
        "set_mode:eng_fast_qa", "mode_menu:home",
    ]
    assert markup.inline_keyboard[1][0].text.startswith("✓ ")


def test_dub_home_returns_to_all_modes() -> None:
    assert "mode_menu:home" in _callbacks(dub_home_keyboard())
