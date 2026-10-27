"""Executed behavior contracts for Dub Studio audio repair controls."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from handlers import dub_audio_repair as repair


class Message:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


def _update(*, user_id=1, with_user=True, with_message=True):
    message = Message() if with_message else None
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id) if with_user else None,
        effective_message=message,
    )
    return update, message


def _run(callback, *args):
    return asyncio.run(callback(*args))


def test_process_lock_path_creates_studio_root(monkeypatch, tmp_path):
    root = tmp_path / "studio"
    monkeypatch.setattr(repair, "studio_root", lambda: root)

    path = repair._process_lock_path()

    assert path == root.resolve() / ".dubfix.request.lock"
    assert root.is_dir()


def test_process_lock_bounded_when_competitor_disappears(monkeypatch):
    class DisappearingPath:
        def stat(self):
            raise FileNotFoundError

    attempts = []

    def already_exists(*args):
        attempts.append(args)
        raise FileExistsError

    monkeypatch.setattr(repair, "_process_lock_path", DisappearingPath)
    monkeypatch.setattr(repair.os, "open", already_exists)

    with pytest.raises(RuntimeError, match="Не удалось захватить"):
        with repair._dubfix_process_lock():
            raise AssertionError("lock must not be acquired")
    assert len(attempts) == 2


def test_strict_ids_require_a_nonempty_list_and_short_bounds_text():
    with pytest.raises(RuntimeError, match="непустым списком"):
        repair._strict_ids([], field="ids")
    assert repair._short(" one\n two ") == "one two"
    assert repair._short("x" * 20, limit=5) == "xxxx…"


def test_repair_admin_gate_handles_all_user_shapes(monkeypatch):
    monkeypatch.setattr(repair, "ADMIN_IDS", {1})
    update, _ = _update(user_id=1)
    assert _run(repair._admin, update) is True

    update, message = _update(user_id=2)
    assert _run(repair._admin, update) is False
    assert message.replies == [("⛔ Ремонт Dub Studio доступен только администратору.", {})]

    update, _ = _update(with_user=False, with_message=False)
    assert _run(repair._admin, update) is False


def test_project_resolution_enforces_owner_and_generic_recipe():
    project = {
        "id": "p",
        "owner_user_id": 7,
        "recipe_id": "generic_short_v1",
    }

    class Store:
        def __init__(self):
            self.project = project
            self.explicit = []
            self.latest = []

        def get_project(self, token):
            self.explicit.append(token)
            return self.project

        def latest_project(self, *, owner_user_id):
            self.latest.append(owner_user_id)
            return self.project

    store = Store()
    assert repair._resolve_project(store, 7, " P ") is project
    assert repair._resolve_project(store, 7, "last") is project
    assert repair._resolve_project(store, 7, None) is project
    assert store.explicit == ["p"]
    assert store.latest == [7, 7]

    store.project = None
    with pytest.raises(KeyError, match="не найден"):
        repair._resolve_project(store, 7, None)
    store.project = {**project, "owner_user_id": 8}
    with pytest.raises(PermissionError, match="не ваш проект"):
        repair._resolve_project(store, 7, None)
    store.project = {**project, "recipe_id": "other"}
    with pytest.raises(RuntimeError, match="только универсальным"):
        repair._resolve_project(store, 7, None)


def test_project_root_and_segments_path_are_contained(monkeypatch, tmp_path):
    monkeypatch.setattr(repair, "studio_root", lambda: tmp_path)

    assert repair._project_root("project") == tmp_path / "projects" / "project"
    assert repair._segments_path("project") == (tmp_path / "projects" / "project" / "segments_ru_final.json")
    with pytest.raises(ValueError):
        repair._project_root("../outside")


def test_segment_loader_requires_an_existing_valid_json_file(monkeypatch, tmp_path):
    path = tmp_path / "segments.json"
    monkeypatch.setattr(repair, "_segments_path", lambda _project_id: path)

    with pytest.raises(RuntimeError, match="ещё нет"):
        repair.load_repair_segments("p")
    path.write_text("{", encoding="utf-8")
    with pytest.raises(RuntimeError, match="повреждён"):
        repair.load_repair_segments("p")


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ([], "пуст или повреждён"),
        ({}, "пуст или повреждён"),
        (["not-a-dict"], "должен быть JSON-объектом"),
        (
            [{"id": 1, "start": 1.0, "end": 1.0, "text": "x"}],
            "Некорректный timing",
        ),
        (
            [
                {
                    "id": 1,
                    "start": 2.0,
                    "end": 3.0,
                    "source_end": 1.0,
                    "text": "x",
                }
            ],
            "source_end",
        ),
        (
            [{"id": 1, "start": 0.0, "end": 1.0, "text": " "}],
            "не содержит текста",
        ),
    ],
)
def test_segment_loader_rejects_malformed_payloads(monkeypatch, tmp_path, payload, match):
    path = tmp_path / "segments.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(repair, "_segments_path", lambda _project_id: path)

    with pytest.raises(RuntimeError, match=match):
        repair.load_repair_segments("p")


def test_segment_loader_normalizes_source_end_and_sorts_ids(monkeypatch, tmp_path):
    path = tmp_path / "segments.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": 2,
                    "start": 2.0,
                    "end": 4.0,
                    "source_end": 5.0,
                    "start_delay_ms": 250,
                    "text": "second",
                },
                {"id": 1, "start": 0.0, "end": 1.0, "text": "first"},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(repair, "_segments_path", lambda _project_id: path)

    segments = repair.load_repair_segments("p")

    assert [item["id"] for item in segments] == [1, 2]
    assert segments[0]["start_delay_ms"] == 0
    assert segments[1]["source_end"] == 5.0
    assert segments[1]["start_delay_ms"] == 250


def test_segment_selector_rejects_empty_malformed_oversized_and_blank_selection():
    with pytest.raises(ValueError, match="Не указаны"):
        repair.parse_segment_selector(" ", [1])
    with pytest.raises(ValueError, match="Некорректный диапазон"):
        repair.parse_segment_selector("1,bad", [1])
    with pytest.raises(ValueError, match="больше 101"):
        repair.parse_segment_selector("1-102", range(1, 103))
    with pytest.raises(ValueError, match="Не выбрана"):
        repair.parse_segment_selector(",,", [1])
    assert repair.parse_segment_selector("1,,2,", [1, 2]) == [1, 2]


def test_segments_text_escapes_content_and_bounds_long_project_list():
    segments = [
        {
            "id": 1,
            "start": 1.0,
            "source_end": 3.0,
            "start_delay_ms": 500,
            "display_text": "<shown> " + "x" * 200,
        }
    ]
    segments.extend({"id": index, "start": 0.0, "end": 1.0, "text": "line"} for index in range(2, 62))

    text = repair._segments_text("<project>", segments)

    assert "&lt;project&gt;" in text
    assert "<code>1</code> · 1.50–3.00" in text
    assert "&lt;shown&gt;" in text
    assert "x" * 116 not in text
    assert "… ещё 1 реплик" in text
    assert "/dubfix &lt;project&gt; all" in text


def test_request_writer_validates_project_and_segments_existence(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="JSON-объектом"):
        repair._write_repair_request(None, [], [], requested_by=1)
    with pytest.raises(RuntimeError, match="Project ID пуст"):
        repair._write_repair_request({"id": " "}, [], [], requested_by=1)

    monkeypatch.setattr(repair, "_project_root", lambda _project_id: tmp_path)
    monkeypatch.setattr(
        repair,
        "_segments_path",
        lambda _project_id: tmp_path / "missing-segments.json",
    )
    with pytest.raises(RuntimeError, match="исчез"):
        repair._write_repair_request(
            {"id": "p"},
            [{"id": 1}],
            [1],
            requested_by=1,
        )


def test_dubsegments_command_renders_project_segments(monkeypatch):
    monkeypatch.setattr(repair, "ADMIN_IDS", {1})
    project = {"id": "p"}
    calls = {}
    monkeypatch.setattr(repair, "DubStore", lambda: object())

    def resolve(_store, user_id, token):
        calls["resolve"] = (user_id, token)
        return project

    monkeypatch.setattr(repair, "_resolve_project", resolve)
    monkeypatch.setattr(
        repair,
        "load_repair_segments",
        lambda project_id: [{"id": 1, "start": 0.0, "end": 1.0, "text": "line"}],
    )
    update, message = _update()

    _run(repair.dubsegments_command, update, SimpleNamespace(args=["p"]))

    assert calls["resolve"] == (1, "p")
    assert "Реплики проекта p" in message.replies[0][0]
    assert message.replies[0][1] == {"parse_mode": "HTML"}


def test_dubsegments_command_escapes_and_bounds_errors(monkeypatch):
    monkeypatch.setattr(repair, "ADMIN_IDS", {1})

    class Store:
        def __init__(self):
            raise RuntimeError("<" + "x" * 1200)

    monkeypatch.setattr(repair, "DubStore", Store)
    update, message = _update()

    _run(repair.dubsegments_command, update, SimpleNamespace(args=[]))

    text, kwargs = message.replies[0]
    assert text.startswith("⚠️ &lt;")
    assert len(text) < 1050
    assert kwargs == {"parse_mode": "HTML"}


def test_segment_commands_return_after_admin_rejection(monkeypatch):
    monkeypatch.setattr(repair, "ADMIN_IDS", set())

    def store_must_not_run():
        raise AssertionError("unauthorized commands must not touch the store")

    monkeypatch.setattr(repair, "DubStore", store_must_not_run)
    update, message = _update(user_id=2)
    context = SimpleNamespace(args=[])
    _run(repair.dubsegments_command, update, context)
    _run(repair._dubfix_command_unlocked, update, context)

    assert len(message.replies) == 2
    assert all(text.startswith("⛔") for text, _kwargs in message.replies)


class Store:
    def __init__(self, *, enqueue_error=None):
        self.enqueue_error = enqueue_error
        self.enqueued = []

    def enqueue_job(self, project_id, kind):
        self.enqueued.append((project_id, kind))
        if self.enqueue_error:
            raise self.enqueue_error
        return {"id": 42}


def _fix_dependencies(monkeypatch, tmp_path, store):
    project = {"id": "p", "owner_user_id": 1, "recipe_id": "generic_short_v1"}
    segments = [
        {"id": 1, "start": 0.0, "end": 1.0, "text": "one"},
        {"id": 2, "start": 1.0, "end": 2.0, "text": "two"},
    ]
    request_path = tmp_path / "audio_repair.json"
    calls = {"tokens": [], "writes": [], "slots": []}
    monkeypatch.setattr(repair, "DubStore", lambda: store)

    def resolve(_store, _user_id, token):
        calls["tokens"].append(token)
        return project

    def write(actual_project, actual_segments, selected, *, requested_by):
        calls["writes"].append((actual_project, actual_segments, selected, requested_by))
        request_path.write_text("request", encoding="utf-8")
        return request_path

    monkeypatch.setattr(repair, "_resolve_project", resolve)
    monkeypatch.setattr(repair, "load_repair_segments", lambda _project_id: segments)
    monkeypatch.setattr(
        repair,
        "_ensure_repair_slot",
        lambda _store, project_id: calls["slots"].append(project_id),
    )
    monkeypatch.setattr(repair, "_write_repair_request", write)
    return calls, request_path


def test_dubfix_without_selector_only_displays_segments(monkeypatch, tmp_path):
    monkeypatch.setattr(repair, "ADMIN_IDS", {1})
    store = Store()
    calls, _request_path = _fix_dependencies(monkeypatch, tmp_path, store)
    update, message = _update()

    _run(repair._dubfix_command_unlocked, update, SimpleNamespace(args=[]))

    assert calls["tokens"] == [None]
    assert calls["writes"] == []
    assert store.enqueued == []
    assert "Реплики проекта p" in message.replies[0][0]


@pytest.mark.parametrize(
    ("selector", "expected_text", "expected_detail"),
    [
        ("all", "Реплики: <code>все</code>", "Старые TTS checkpoints"),
        ("1", "Реплики: <code>1</code>", "Выбранные реплики получают новый seed"),
    ],
)
def test_dubfix_enqueues_full_and_partial_repairs(monkeypatch, tmp_path, selector, expected_text, expected_detail):
    monkeypatch.setattr(repair, "ADMIN_IDS", {1})
    store = Store()
    calls, _request_path = _fix_dependencies(monkeypatch, tmp_path, store)
    update, message = _update()

    _run(
        repair._dubfix_command_unlocked,
        update,
        SimpleNamespace(args=["p", selector]),
    )

    assert calls["tokens"] == ["p"]
    assert calls["slots"] == ["p"]
    assert len(calls["writes"]) == 1
    assert store.enqueued == [("p", "repair_audio")]
    text, kwargs = message.replies[0]
    assert "очередь: #42" in text
    assert expected_text in text
    assert expected_detail in text
    assert kwargs == {"parse_mode": "HTML"}


def test_dubfix_enqueue_failure_removes_request_and_reports_error(monkeypatch, tmp_path):
    monkeypatch.setattr(repair, "ADMIN_IDS", {1})
    store = Store(enqueue_error=RuntimeError("<" + "x" * 1200))
    _calls, request_path = _fix_dependencies(monkeypatch, tmp_path, store)
    update, message = _update()

    _run(
        repair._dubfix_command_unlocked,
        update,
        SimpleNamespace(args=["p", "all"]),
    )

    assert not request_path.exists()
    text, kwargs = message.replies[0]
    assert text.startswith("⚠️ &lt;")
    assert len(text) < 1150
    assert kwargs == {"parse_mode": "HTML"}


def test_dubfix_wrapper_reports_runtime_error_to_message(monkeypatch, tmp_path):
    monkeypatch.setattr(repair, "_process_lock_path", lambda: tmp_path / "dubfix.lock")

    async def fail(_update, _context):
        raise RuntimeError("repair failed")

    monkeypatch.setattr(repair, "_dubfix_command_unlocked", fail)
    update, message = _update()

    _run(repair.dubfix_command, update, SimpleNamespace(args=[]))

    assert message.replies == [("⚠️ repair failed", {})]
    assert not (tmp_path / "dubfix.lock").exists()


def test_audio_repair_registration_is_idempotent():
    class Application:
        def __init__(self):
            self.bot_data = {}
            self.handlers = []

        def add_handler(self, handler):
            self.handlers.append(handler)

    application = Application()
    repair.register_dub_audio_repair_handlers(application)
    repair.register_dub_audio_repair_handlers(application)

    assert len(application.handlers) == 2
    assert application.bot_data["dub_audio_repair_handlers_registered"] is True
    assert application.handlers[0].commands == frozenset({"dubsegments"})
    assert application.handlers[1].commands == frozenset({"dubfix"})
