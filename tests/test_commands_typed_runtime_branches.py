"""Executed branches introduced while typing legacy command callbacks."""

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from handlers import commands


class Message:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return SimpleNamespace()


def _update(*, user_id=1):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=Message(),
        effective_message=None,
    )


@pytest.fixture
def connection(monkeypatch):
    class Connection:
        def __init__(self):
            self.commits = 0
            self.statements = []

        def execute(self, statement, params=()):
            self.statements.append((statement, params))
            return SimpleNamespace(rowcount=1)

        def commit(self):
            self.commits += 1

    conn = Connection()

    @contextlib.contextmanager
    def connect():
        yield conn

    monkeypatch.setattr(commands, "_db_conn", connect)
    return conn


def test_reset_cache_delete_workers_execute_with_owned_connections(connection, monkeypatch):
    update = _update()
    context = SimpleNamespace(args=["all"])
    monkeypatch.setattr(commands, "ADMIN_IDS", {1})

    asyncio.run(commands._do_resetcache_one("video-id", update))
    asyncio.run(commands.reset_cache_command(update, context))

    assert connection.commits == 2
    assert connection.statements[0][1] == ("video-id",)
    assert len(update.message.replies) == 2


def test_stop_force_exit_fallback_is_executed_without_exiting_pytest(monkeypatch):
    update = _update()
    context = SimpleNamespace(application=SimpleNamespace(bot_data={}))
    monkeypatch.setattr(commands, "ADMIN_IDS", {1})
    monkeypatch.setenv("FORCE_EXIT_ON_STOP", "1")
    sleep = AsyncMock()
    exit_mock = Mock()
    monkeypatch.setattr(commands.asyncio, "sleep", sleep)
    monkeypatch.setattr(commands.os, "_exit", exit_mock)

    async def exercise():
        created = []

        def capture(coroutine):
            created.append(coroutine)
            return SimpleNamespace()

        monkeypatch.setattr(commands.asyncio, "create_task", capture)
        await commands.stop_command(update, context)
        assert len(created) == 1
        await created[0]

    asyncio.run(exercise())

    assert context.application.bot_data["stop_requested"] is True
    sleep.assert_awaited_once_with(5)
    exit_mock.assert_called_once_with(0)


def test_resolve_segment_source_executes_typed_archive_boundary(monkeypatch):
    monkeypatch.setattr(commands, "adb_get", AsyncMock(return_value=None))
    archive = AsyncMock(return_value=None)
    monkeypatch.setattr(commands, "aget_generated_page_record", archive)

    result = asyncio.run(commands._resolve_segment_source(" video-id "))

    assert result == ("video-id", None, None)
    archive.assert_awaited_once_with("video-id")
