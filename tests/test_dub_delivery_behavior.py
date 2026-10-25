"""Direct behavior contracts for Dub Studio output discovery and delivery."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import handlers.dub_delivery as delivery


class Status:
    def __init__(self, *, fail_edit=False):
        self.fail_edit = fail_edit
        self.edits = []

    async def edit_text(self, text, **kwargs):
        if self.fail_edit:
            raise RuntimeError("edit failed")
        self.edits.append((text, kwargs))


class Message:
    def __init__(self, status=None):
        self.status = status or Status()
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return self.status


def _update(*, user_id=1, with_user=True, with_message=True, status=None):
    message = Message(status) if with_message else None
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id) if with_user else None,
        effective_message=message,
        effective_chat=SimpleNamespace(id=777),
    )
    return update, message


def test_short_normalizes_whitespace_and_bounds_text():
    assert delivery._short("  one\n two\tthree  ") == "one two three"
    assert delivery._short("x" * 20, limit=5) == "xxxx…"
    assert delivery._short("") == ""


def test_admin_gate_accepts_admin_and_rejects_other_users(monkeypatch):
    monkeypatch.setattr(delivery, "ADMIN_IDS", {1})
    admin_update, _ = _update(user_id=1)
    assert asyncio.run(delivery._admin(admin_update)) is True

    denied_update, message = _update(user_id=2)
    assert asyncio.run(delivery._admin(denied_update)) is False
    assert message.replies == [("⛔ Отправка Dub Studio доступна только администратору.", {})]

    missing_update, _ = _update(with_user=False, with_message=False)
    assert asyncio.run(delivery._admin(missing_update)) is False


def test_project_resolution_supports_explicit_and_latest_tokens():
    project = {"id": "p", "owner_user_id": "7"}

    class Store:
        def __init__(self):
            self.explicit = []
            self.latest = []

        def get_project(self, token):
            self.explicit.append(token)
            return project

        def latest_project(self, *, owner_user_id):
            self.latest.append(owner_user_id)
            return project

    store = Store()
    assert delivery._resolve_project(store, 7, "  PROJECT  ") is project
    assert store.explicit == ["project"]

    for token in (None, "", "last", "последний", "all", "все"):
        assert delivery._resolve_project(store, 7, token) is project
    assert store.latest == [7] * 6


def test_project_resolution_rejects_missing_and_foreign_projects():
    class Store:
        def __init__(self, project):
            self.project = project

        def latest_project(self, *, owner_user_id):
            return self.project

    with pytest.raises(KeyError, match="ещё нет"):
        delivery._resolve_project(Store(None), 7, None)
    with pytest.raises(PermissionError, match="не ваш проект"):
        delivery._resolve_project(Store({"owner_user_id": 8}), 7, None)


def test_dynamic_project_root_cannot_escape_projects_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, "studio_root", lambda: tmp_path)
    assert delivery._dynamic_project_root({"id": "project"}) == (tmp_path / "projects" / "project")
    with pytest.raises(ValueError):
        delivery._dynamic_project_root({"id": "../outside"})


@pytest.mark.parametrize("content", ["{", "[]", "{}"])
def test_invalid_or_unusable_dynamic_manifest_has_no_outputs(tmp_path, monkeypatch, content):
    monkeypatch.setattr(delivery, "studio_root", lambda: tmp_path)
    root = tmp_path / "projects" / "project"
    root.mkdir(parents=True)
    (root / "output").mkdir()
    (root / "output" / "manifest.json").write_text(content, encoding="utf-8")

    assert delivery._dynamic_outputs({"id": "project"}, include_all_video=False) == []


def _dynamic_manifest(tmp_path: Path):
    root = tmp_path / "projects" / "project"
    output = root / "output"
    output.mkdir(parents=True)
    files = {}
    for name in (
        "primary.mp4",
        "secondary.mp4",
        "notes.txt",
        "hidden.txt",
        "flagged.bin",
        "bad.exe",
    ):
        path = output / name
        path.write_bytes(b"data")
        files[name] = path
    empty = output / "empty.srt"
    empty.touch()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    specs = [
        "not-a-spec",
        {"path": str(outside)},
        {"path": str(output / "missing.txt")},
        {"path": str(empty)},
        {
            "path": str(files["primary.mp4"]),
            "primary": True,
            "filename": "../Primary.mp4",
            "name": "z-primary",
        },
        {"path": str(files["primary.mp4"]), "primary": True},
        {"path": str(files["secondary.mp4"]), "name": "secondary"},
        {"path": str(files["notes.txt"]), "name": "notes"},
        {
            "path": str(files["hidden.txt"]),
            "name": "hidden",
            "send_default": False,
        },
        {"path": str(files["bad.exe"]), "name": "bad"},
        {
            "path": str(files["flagged.bin"]),
            "name": "a-flagged",
            "video": True,
            "primary": True,
        },
    ]
    (output / "manifest.json").write_text(json.dumps({"telegram_outputs": specs}), encoding="utf-8")
    return root, files


def test_dynamic_outputs_filter_deduplicate_sort_and_prefer_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, "studio_root", lambda: tmp_path)
    monkeypatch.setattr(delivery, "canonical_delivery_filename", lambda value: f"canonical:{value}")

    def recipe_fallback_must_not_run(*args, **kwargs):
        raise AssertionError("a non-empty dynamic manifest owns delivery")

    monkeypatch.setattr(delivery, "_recipe_outputs", recipe_fallback_must_not_run)
    _root, _files = _dynamic_manifest(tmp_path)
    project = {"id": "project"}

    default = delivery.available_outputs(project)
    assert [row["name"] for row in default] == ["a-flagged", "z-primary", "notes"]
    assert [row["filename"] for row in default] == [
        "canonical:flagged.bin",
        "canonical:Primary.mp4",
        "canonical:notes.txt",
    ]

    all_outputs = delivery.available_outputs(project, include_all_video=True)
    assert [row["name"] for row in all_outputs] == [
        "a-flagged",
        "z-primary",
        "secondary",
        "hidden",
        "notes",
    ]


def test_recipe_outputs_skip_empty_duplicate_and_unsupported_paths(tmp_path, monkeypatch):
    primary = tmp_path / "primary.mp4"
    secondary = tmp_path / "secondary.mp4"
    document = tmp_path / "notes.txt"
    unsupported = tmp_path / "payload.exe"
    empty = tmp_path / "empty.txt"
    for path in (primary, secondary, document, unsupported):
        path.write_bytes(b"data")
    empty.touch()
    recipe = SimpleNamespace(
        outputs={
            "empty-path": {"path": ""},
            "missing": {"path": str(tmp_path / "missing.txt")},
            "empty-file": {"path": str(empty)},
            "primary": {"path": str(primary), "primary": True},
            "duplicate": {"path": str(primary), "primary": True},
            "secondary": {"path": str(secondary)},
            "document": {"path": str(document)},
            "unsupported": {"path": str(unsupported)},
        }
    )
    monkeypatch.setattr(delivery, "load_recipe", lambda _recipe_id: recipe)
    monkeypatch.setattr(
        delivery,
        "resolve_recipe_path",
        lambda raw_path, *, work_root: Path(raw_path),
    )
    project = {"recipe_id": "recipe", "work_root": "work"}

    default = delivery._recipe_outputs(project, include_all_video=False)
    assert [row["name"] for row in default] == ["primary", "document"]

    all_outputs = delivery._recipe_outputs(project, include_all_video=True)
    assert [row["name"] for row in all_outputs] == [
        "primary",
        "secondary",
        "document",
    ]


def test_send_project_outputs_reports_when_nothing_is_ready(monkeypatch):
    monkeypatch.setattr(delivery, "available_outputs", lambda *args, **kwargs: [])
    sent, failures = asyncio.run(delivery.send_project_outputs(object(), 777, {"id": "p"}))
    assert sent == 0
    assert failures == ["Готовые файлы ещё не найдены."]


def test_send_project_outputs_sends_both_media_types_and_continues_after_failure(tmp_path, monkeypatch):
    good_video = tmp_path / "good.mp4"
    bad_video = tmp_path / "bad.mp4"
    document = tmp_path / "notes.txt"
    for path in (good_video, bad_video, document):
        path.write_bytes(b"data")
    outputs = [
        {
            "path": good_video,
            "video": True,
            "filename": "",
            "label": "  Video\n title  ",
        },
        {
            "path": bad_video,
            "video": True,
            "filename": "bad.mp4",
            "label": "Bad",
        },
        {
            "path": document,
            "video": False,
            "filename": "notes.txt",
            "label": "x" * 900,
        },
    ]
    monkeypatch.setattr(delivery, "available_outputs", lambda *args, **kwargs: outputs)

    class Bot:
        def __init__(self):
            self.calls = []

        async def send_video(self, **kwargs):
            self.calls.append(("video", kwargs))
            assert kwargs["video"].read() == b"data"
            if kwargs["filename"] == "bad.mp4":
                raise RuntimeError(" boom\n" + "x" * 300)

        async def send_document(self, **kwargs):
            self.calls.append(("document", kwargs))
            assert kwargs["document"].read() == b"data"

    bot = Bot()
    sent, failures = asyncio.run(delivery.send_project_outputs(bot, 777, {"id": "p"}, include_all_video=True))

    assert sent == 2
    assert [kind for kind, _kwargs in bot.calls] == ["video", "video", "document"]
    assert len(failures) == 1
    assert failures[0].startswith("bad.mp4: boom ")
    assert len(failures[0]) < 200
    video_kwargs = bot.calls[0][1]
    assert video_kwargs["chat_id"] == 777
    assert video_kwargs["filename"] == "good.mp4"
    assert video_kwargs["caption"] == "Video title"
    assert video_kwargs["supports_streaming"] is True
    assert video_kwargs["write_timeout"] == 1800
    document_kwargs = bot.calls[2][1]
    assert len(document_kwargs["caption"]) == 800
    assert document_kwargs["caption"].endswith("…")


def _command_dependencies(monkeypatch, *, sent, failures):
    project = {"id": "project<&>", "owner_user_id": 1}
    calls = {}
    monkeypatch.setattr(delivery, "DubStore", lambda: object())

    def resolve(_store, user_id, token):
        calls["resolve"] = (user_id, token)
        return project

    async def send(bot, chat_id, actual_project, *, include_all_video):
        calls["send"] = (bot, chat_id, actual_project, include_all_video)
        return sent, failures

    monkeypatch.setattr(delivery, "_resolve_project", resolve)
    monkeypatch.setattr(delivery, "send_project_outputs", send)
    return calls


def test_dubsend_command_sends_all_and_edits_status_with_bounded_failures(
    monkeypatch,
):
    monkeypatch.setattr(delivery, "ADMIN_IDS", {1})
    status = Status()
    update, message = _update(status=status)
    bot = object()
    context = SimpleNamespace(args=["ALL", " PROJECT "], bot=bot)
    failures = ["<bad>"] + [f"failure-{index}" for index in range(10)]
    calls = _command_dependencies(monkeypatch, sent=2, failures=failures)

    asyncio.run(delivery.dubsend_command(update, context))

    assert calls["resolve"] == (1, " PROJECT ")
    assert calls["send"] == (bot, 777, {"id": "project<&>", "owner_user_id": 1}, True)
    initial, initial_kwargs = message.replies[0]
    assert "project&lt;&amp;&gt;" in initial
    assert initial_kwargs == {"parse_mode": "HTML"}
    assert len(status.edits) == 1
    edited, edit_kwargs = status.edits[0]
    assert edited.startswith("✅ Отправлено файлов: 2.")
    assert "• &lt;bad&gt;" in edited
    assert edited.count("•") == 8
    assert "failure-8" not in edited
    assert edit_kwargs == {"parse_mode": "HTML"}


def test_dubsend_command_falls_back_to_reply_when_status_edit_fails(monkeypatch):
    monkeypatch.setattr(delivery, "ADMIN_IDS", {1})
    status = Status(fail_edit=True)
    update, message = _update(status=status)
    context = SimpleNamespace(args=[], bot=object())
    calls = _command_dependencies(monkeypatch, sent=0, failures=[])

    asyncio.run(delivery.dubsend_command(update, context))

    assert calls["resolve"] == (1, None)
    assert calls["send"][3] is False
    assert len(message.replies) == 2
    fallback, kwargs = message.replies[-1]
    assert fallback == "⚠️ Готовые файлы пока не найдены."
    assert kwargs == {"parse_mode": "HTML"}


def test_dubsend_command_reports_escaped_bounded_outer_errors(monkeypatch):
    monkeypatch.setattr(delivery, "ADMIN_IDS", {1})

    class Store:
        def __init__(self):
            raise RuntimeError("<" + "x" * 1000)

    monkeypatch.setattr(delivery, "DubStore", Store)
    update, message = _update()
    context = SimpleNamespace(args=[], bot=object())

    asyncio.run(delivery.dubsend_command(update, context))

    text, kwargs = message.replies[0]
    assert text.startswith("❌ Отправка: &lt;")
    assert len(text) < 950
    assert kwargs == {"parse_mode": "HTML"}


def test_dubsend_command_returns_after_admin_rejection(monkeypatch):
    monkeypatch.setattr(delivery, "ADMIN_IDS", set())

    def store_must_not_be_created():
        raise AssertionError("unauthorized delivery must not touch the store")

    monkeypatch.setattr(delivery, "DubStore", store_must_not_be_created)
    update, message = _update(user_id=2)
    context = SimpleNamespace(args=["project"], bot=object())

    asyncio.run(delivery.dubsend_command(update, context))

    assert len(message.replies) == 1
    assert message.replies[0][0].startswith("⛔")


def test_delivery_registration_is_idempotent():
    class Application:
        def __init__(self):
            self.bot_data = {}
            self.handlers = []

        def add_handler(self, handler):
            self.handlers.append(handler)

    application = Application()
    delivery.register_dub_delivery_handlers(application)
    delivery.register_dub_delivery_handlers(application)

    assert len(application.handlers) == 1
    assert application.bot_data["dub_studio_delivery_registered"] is True
    assert application.handlers[0].commands == frozenset({"dubsend"})
    assert application.handlers[0].callback is delivery.dubsend_command
