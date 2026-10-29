"""Executed Telegram command contracts for the typed Dub control surface."""

import asyncio
from types import SimpleNamespace
from collections.abc import Coroutine, Callable
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from handlers import dub_commands as commands


class Message:
    def __init__(self) -> None:
        self.replies: list[tuple[str, dict[str, Any]]] = []

    async def reply_text(self, text: str, **kwargs: Any) -> None:
        self.replies.append((text, kwargs))


class Query:
    def __init__(self, data: str) -> None:
        self.data = data
        self.answers = 0

    async def answer(self) -> None:
        self.answers += 1


def _update(
    *, with_message: bool = True, with_user: bool = True, with_chat: bool = True, query: Query | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=1) if with_user else None,
        effective_message=Message() if with_message else None,
        effective_chat=SimpleNamespace(id=777) if with_chat else None,
        callback_query=query,
    )


def _context(*args: str) -> Any:
    return SimpleNamespace(args=list(args))


def _project() -> dict[str, Any]:
    return {
        "id": "project",
        "owner_user_id": 1,
        "recipe_id": "recipe",
        "metadata": {},
        "status": "done",
        "title": "Title",
        "progress": 0,
    }


class Store:
    def __init__(self) -> None:
        self.worker: dict[str, Any] | None = None
        self.projects: list[dict[str, Any]] = []
        self.fail_create = False
        self.fail_enqueue = False

    def latest_worker(self) -> dict[str, Any] | None:
        return self.worker

    def create_project(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        if self.fail_create:
            raise RuntimeError("create failed")
        return _project()

    def list_projects(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return self.projects

    def enqueue_job(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        if self.fail_enqueue:
            raise RuntimeError("enqueue failed")
        return {"id": 7}

    def request_cancel(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"id": 8}

    def get_project(self, _project_id: str) -> dict[str, Any]:
        return _project()


@pytest.fixture(autouse=True)
def admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands, "ADMIN_IDS", {1})


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    value = Store()
    monkeypatch.setattr(commands, "_store", lambda: value)
    monkeypatch.setattr(commands, "worker_is_fresh", lambda worker: bool(worker and worker.get("fresh")))
    monkeypatch.setattr(commands, "list_recipes", lambda: [])
    return value


def test_missing_command_actors_fail_closed(store: Store) -> None:
    callbacks: list[Callable[..., Coroutine[Any, Any, Any]]] = [
        commands.dub_command,
        commands.dubnew_command,
        commands.dublist_command,
        commands.dubstatus_command,
        commands.dublog_command,
        commands.dubrun_command,
        commands.dubrepair_command,
        commands.dubcancel_command,
        commands.dubfiles_command,
        commands.dubworker_command,
    ]
    update = _update(with_message=False)

    for callback in callbacks:
        asyncio.run(callback(cast(Any, update), _context()))
    asyncio.run(commands._enqueue(cast(Any, update), _project(), "render"))

    assert commands._command_actors(cast(Any, update)) is None
    valid = _update()
    actors = commands._command_actors(cast(Any, valid))
    assert actors is not None
    user, message = actors
    assert user.id == 1
    assert message is valid.effective_message


def test_dub_home_and_new_project_paths(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands, "_project_card", lambda _project: ("Title", None))
    store.worker = {
        "worker_id": "worker",
        "status": "idle",
        "fresh": True,
    }
    update = _update()
    asyncio.run(commands.dub_command(cast(Any, update), _context()))
    store.worker["fresh"] = False
    asyncio.run(commands.dub_command(cast(Any, update), _context()))
    assert "✅ worker" in update.effective_message.replies[0][0]
    assert "не отвечает" in update.effective_message.replies[1][0]

    asyncio.run(commands.dubnew_command(cast(Any, update), _context()))
    assert "Использование" in update.effective_message.replies[-1][0]

    no_chat = _update(with_chat=False)
    asyncio.run(commands.dubnew_command(cast(Any, no_chat), _context("recipe")))
    assert no_chat.effective_message.replies == []

    store.fail_create = True
    asyncio.run(commands.dubnew_command(cast(Any, update), _context("recipe")))
    assert "create failed" in update.effective_message.replies[-1][0]
    store.fail_create = False
    asyncio.run(commands.dubnew_command(cast(Any, update), _context("recipe")))
    assert update.effective_message.replies[-1][0] == "Title"


def test_dub_list_status_log_run_cancel_and_files_paths(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project()
    update = _update()
    monkeypatch.setattr(commands, "_project_card", lambda _project: ("Card", None))
    monkeypatch.setattr(commands, "_log_text", lambda _project: ("Log", None))
    monkeypatch.setattr(commands, "_outputs_text", lambda _project: "Files")
    monkeypatch.setattr(commands, "_resolve_project", lambda *_args: project)

    asyncio.run(commands.dublist_command(cast(Any, update), _context()))
    assert "Проектов пока нет" in update.effective_message.replies[-1][0]
    store.projects = [project]
    asyncio.run(commands.dublist_command(cast(Any, update), _context()))
    assert "Последние Dub Studio проекты" in update.effective_message.replies[-1][0]

    for callback in (
        commands.dubstatus_command,
        commands.dublog_command,
        commands.dubrun_command,
        commands.dubcancel_command,
        commands.dubfiles_command,
    ):
        asyncio.run(callback(cast(Any, update), _context()))
    assert update.effective_message.replies[-1][0] == "Files"

    def fail_resolve(*_args: Any) -> None:
        raise KeyError("missing")

    monkeypatch.setattr(commands, "_resolve_project", fail_resolve)
    for callback in (
        commands.dubstatus_command,
        commands.dublog_command,
        commands.dubrun_command,
        commands.dubcancel_command,
        commands.dubfiles_command,
    ):
        asyncio.run(callback(cast(Any, update), _context()))
        assert "missing" in update.effective_message.replies[-1][0]


def test_dub_repair_worker_and_enqueue_error_paths(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project()
    update = _update()
    monkeypatch.setattr(commands, "_resolve_project", lambda *_args: project)
    recipe = SimpleNamespace(
        actions={"one": {"label": "One"}, "two": {"label": "Two"}},
        repair_actions=lambda: ["one", "two"],
    )
    monkeypatch.setattr(commands, "load_recipe", lambda _recipe_id: recipe)

    asyncio.run(commands.dubrepair_command(cast(Any, update), _context("project")))
    assert update.effective_message.replies[-1][0] == "Выберите точечный ремонт:"

    def fail_resolve(*_args: Any) -> None:
        raise KeyError("missing")

    monkeypatch.setattr(commands, "_resolve_project", fail_resolve)
    asyncio.run(commands.dubrepair_command(cast(Any, update), _context("project")))
    assert "missing" in update.effective_message.replies[-1][0]

    store.worker = {
        "worker_id": "worker",
        "status": "idle",
        "fresh": True,
        "pid": 123,
        "heartbeat_at": "now",
        "details": {},
    }
    asyncio.run(commands.dubworker_command(cast(Any, update), _context()))
    assert "Dub Studio worker" in update.effective_message.replies[-1][0]

    store.fail_enqueue = True
    asyncio.run(commands._enqueue(cast(Any, update), project, "render"))
    assert "enqueue failed" in update.effective_message.replies[-1][0]


def test_dub_callback_narrows_user_and_checks_project_owner(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    query = Query("dub|unknown|project")
    update = _update(query=query)
    asyncio.run(commands.handle_dub_callback(cast(Any, update), _context()))
    assert query.answers == 1

    monkeypatch.setattr(commands, "_admin", AsyncMock(return_value=True))
    missing_user_query = Query("dub|unknown|project")
    missing_user = _update(with_user=False, query=missing_user_query)
    asyncio.run(commands.handle_dub_callback(cast(Any, missing_user), _context()))
    assert missing_user_query.answers == 1
