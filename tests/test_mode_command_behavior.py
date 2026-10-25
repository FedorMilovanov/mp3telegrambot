"""Executed behavior contracts for unified mode navigation and persistence."""

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest

import handlers.dub_health as dub_health
import handlers.mode_command as mode


class Message:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class Query:
    def __init__(self, data):
        self.data = data
        self.answers = 0
        self.edits = []

    async def answer(self):
        self.answers += 1

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


def _update(*, user_id=1, with_user=True, query_data=None, with_query=False):
    query = Query(query_data) if with_query else None
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id) if with_user else None,
        effective_message=Message(),
        callback_query=query,
    )
    return update, query


def _context():
    return SimpleNamespace(user_data={"dub_universal_wizard": {"awaiting": "url"}, "other": "keep"})


def _run(callback, *args):
    return asyncio.run(callback(*args))


def test_admin_detection_handles_missing_regular_and_admin_users(monkeypatch):
    monkeypatch.setattr(mode, "ADMIN_IDS", {1})
    update, _ = _update(with_user=False)
    assert mode._is_admin(update) is False
    update, _ = _update(user_id=2)
    assert mode._is_admin(update) is False
    update, _ = _update(user_id=1)
    assert mode._is_admin(update) is True


def test_safe_edit_sends_html_and_only_ignores_unmodified_message():
    query = Query(None)
    _run(mode._safe_edit, query, "text")
    assert query.edits == [("text", {"reply_markup": None, "parse_mode": "HTML"})]

    class UnmodifiedQuery(Query):
        async def edit_message_text(self, text, **kwargs):
            raise BadRequest("Message is not modified")

    _run(mode._safe_edit, UnmodifiedQuery(None), "same")

    class BrokenQuery(Query):
        async def edit_message_text(self, text, **kwargs):
            raise BadRequest("Message is too old")

    with pytest.raises(BadRequest, match="too old"):
        _run(mode._safe_edit, BrokenQuery(None), "text")


def test_async_mode_readers_use_executor_and_fall_back_on_errors(monkeypatch):
    monkeypatch.setattr(mode, "_get_user_mode_raw", lambda user_id: "eng")
    monkeypatch.setattr(mode, "_get_shorts_factory_full_video_raw", lambda user_id: True)
    assert _run(mode.get_user_mode, 7) == "eng"
    assert _run(mode.get_shorts_factory_full_video, 7) is True

    def fail_read(_user_id):
        raise RuntimeError("database down")

    monkeypatch.setattr(mode, "_get_user_mode_raw", fail_read)
    monkeypatch.setattr(mode, "_get_shorts_factory_full_video_raw", fail_read)
    assert _run(mode.get_user_mode, 7) == "rus"
    assert _run(mode.get_shorts_factory_full_video, 7) is False


def test_mode_command_clears_wizard_and_renders_admin_home(monkeypatch):
    monkeypatch.setattr(mode, "ADMIN_IDS", {1})

    async def read_mode(user_id):
        assert user_id == 1
        return "eng"

    monkeypatch.setattr(mode, "_read_user_mode", read_mode)
    update, _ = _update(user_id=1)
    context = _context()

    _run(mode.mode_command, update, context)

    assert context.user_data == {"other": "keep"}
    text, kwargs = update.effective_message.replies[0]
    assert mode.MODE_LABELS["eng"] in text
    assert "🎙 <b>Дубляж видео</b>" in text
    assert kwargs["parse_mode"] == "HTML"
    callbacks = [button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row]
    assert "dubwiz|mode|gemini" in callbacks


def test_mode_callback_without_query_is_a_noop():
    update, _ = _update()
    _run(mode.handle_mode_callback, update, _context())


def test_home_callback_clears_wizard_and_renders_regular_home(monkeypatch):
    monkeypatch.setattr(mode, "ADMIN_IDS", {1})

    async def read_mode(_user_id):
        return "unknown"

    monkeypatch.setattr(mode, "_read_user_mode", read_mode)
    update, query = _update(user_id=2, with_query=True, query_data="mode_menu:home")
    context = _context()

    _run(mode.handle_mode_callback, update, context)

    assert query.answers == 1
    assert context.user_data == {"other": "keep"}
    text, kwargs = query.edits[0]
    assert mode.MODE_LABELS["rus"] in text
    assert "🎙 <b>Дубляж видео</b>" not in text
    callbacks = [button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row]
    assert callbacks == ["mode_menu:analysis"]


def test_analysis_callback_loads_full_video_setting(monkeypatch):
    async def read_mode(_user_id):
        return "shorts_max"

    async def read_full_video(user_id):
        assert user_id == 1
        return True

    monkeypatch.setattr(mode, "_read_user_mode", read_mode)
    monkeypatch.setattr(mode, "get_shorts_factory_full_video", read_full_video)
    update, query = _update(with_query=True, query_data="mode_menu:analysis")

    _run(mode.handle_mode_callback, update, _context())

    text, kwargs = query.edits[0]
    assert "☑️ полный русский видео-перевод + нарезки" in text
    buttons = [button.text for row in kwargs["reply_markup"].inline_keyboard for button in row]
    assert "☑️ Полный видео-перевод + нарезки" in buttons


def test_full_video_toggle_outside_factory_only_renders_analysis(monkeypatch):
    async def read_mode(_user_id):
        return "rus"

    async def setting_must_not_be_read(_user_id):
        raise AssertionError("non-factory modes cannot toggle the factory setting")

    monkeypatch.setattr(mode, "_read_user_mode", read_mode)
    monkeypatch.setattr(mode, "get_shorts_factory_full_video", setting_must_not_be_read)
    update, query = _update(with_query=True, query_data="mode_menu:factory_full_video_toggle")

    _run(mode.handle_mode_callback, update, _context())

    assert "Текущий режим:" in query.edits[0][0]


def test_full_video_toggle_persists_inverted_setting(monkeypatch):
    async def read_mode(_user_id):
        return "shorts_max"

    async def read_full_video(_user_id):
        return False

    writes = []
    monkeypatch.setattr(mode, "_read_user_mode", read_mode)
    monkeypatch.setattr(mode, "get_shorts_factory_full_video", read_full_video)
    monkeypatch.setattr(
        mode,
        "_set_shorts_factory_full_video_raw",
        lambda user_id, enabled: writes.append((user_id, enabled)),
    )
    update, query = _update(with_query=True, query_data="mode_menu:factory_full_video_toggle")

    _run(mode.handle_mode_callback, update, _context())

    assert writes == [(1, True)]
    assert "☑️ полный русский видео-перевод + нарезки" in query.edits[0][0]


def test_full_video_toggle_reports_save_errors(monkeypatch):
    async def read_mode(_user_id):
        return "shorts_max"

    async def read_full_video(_user_id):
        return True

    def fail_write(_user_id, _enabled):
        raise RuntimeError("write failed")

    errors = []
    monkeypatch.setattr(mode, "_read_user_mode", read_mode)
    monkeypatch.setattr(mode, "get_shorts_factory_full_video", read_full_video)
    monkeypatch.setattr(mode, "_set_shorts_factory_full_video_raw", fail_write)
    monkeypatch.setattr(mode.logger, "error", lambda *args: errors.append(args))
    update, query = _update(with_query=True, query_data="mode_menu:factory_full_video_toggle")

    _run(mode.handle_mode_callback, update, _context())

    assert query.edits[0][0] == "❌ Ошибка сохранения настройки полного видео."
    assert len(errors) == 1


def test_dubcheck_callback_delegates_to_health_owner(monkeypatch):
    calls = []

    async def check(update, context):
        calls.append((update, context))

    monkeypatch.setattr(dub_health, "dubcheck_command", check)
    update, query = _update(with_query=True, query_data="mode_menu:dubcheck")
    context = _context()

    _run(mode.handle_mode_callback, update, context)

    assert calls == [(update, context)]
    assert context.user_data == {"other": "keep"}
    assert query.edits == []


def test_unrelated_and_unknown_mode_callbacks_do_not_write(monkeypatch):
    def write_must_not_run(*args):
        raise AssertionError("invalid callbacks must not write settings")

    monkeypatch.setattr(mode, "_set_user_mode_raw", write_must_not_run)
    update, query = _update(with_query=True, query_data="other callback")
    _run(mode.handle_mode_callback, update, _context())
    assert query.answers == 1
    assert query.edits == []

    update, query = _update(with_query=True, query_data="set_mode:not-real")
    _run(mode.handle_mode_callback, update, _context())
    assert query.edits[0][0] == "❌ Неизвестный режим."


def test_mode_selection_reports_save_errors(monkeypatch):
    def fail_write(_user_id, _mode):
        raise RuntimeError("write failed")

    errors = []
    monkeypatch.setattr(mode, "_set_user_mode_raw", fail_write)
    monkeypatch.setattr(mode.logger, "error", lambda *args: errors.append(args))
    update, query = _update(with_query=True, query_data="set_mode:eng")

    _run(mode.handle_mode_callback, update, _context())

    assert query.edits[0][0] == "❌ Ошибка сохранения режима."
    assert len(errors) == 1


def test_mode_selection_persists_and_renders_saved_mode(monkeypatch):
    writes = []
    monkeypatch.setattr(
        mode,
        "_set_user_mode_raw",
        lambda user_id, selected: writes.append((user_id, selected)),
    )

    async def read_full_video(_user_id):
        return False

    monkeypatch.setattr(mode, "get_shorts_factory_full_video", read_full_video)
    update, query = _update(with_query=True, query_data="set_mode:shorts_max")
    context = _context()

    _run(mode.handle_mode_callback, update, context)

    assert writes == [(1, "shorts_max")]
    assert context.user_data == {"other": "keep"}
    text, kwargs = query.edits[0]
    assert "✅ Установлен:" in text
    assert "⬜ только нарезки" in text
    assert kwargs["reply_markup"].inline_keyboard[-2][0].callback_data == ("mode_menu:factory_full_video_toggle")


def test_raw_mode_persistence_validates_values_and_falls_back(monkeypatch, tmp_path):
    db_path = tmp_path / "settings.sqlite3"

    def connect():
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        return conn

    monkeypatch.setattr(mode, "_db_conn", connect)
    assert mode._get_user_mode_raw(1) == "rus"
    mode._set_user_mode_raw(1, "eng")
    assert mode._get_user_mode_raw(1) == "eng"

    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            ("user_mode_2", "obsolete"),
        )
        conn.commit()
    assert mode._get_user_mode_raw(2) == "rus"
    with pytest.raises(ValueError, match="invalid mode"):
        mode._set_user_mode_raw(1, "obsolete")

    def fail_connect():
        raise RuntimeError("database down")

    monkeypatch.setattr(mode, "_db_conn", fail_connect)
    assert mode._get_user_mode_raw(1) == "rus"
    assert mode._get_shorts_factory_full_video_raw(1) is False
