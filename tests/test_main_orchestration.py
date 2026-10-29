from __future__ import annotations

import asyncio
import signal
import threading
import tomllib
from pathlib import Path
from types import TracebackType

import pytest
from telegram import Update
from telegram import request as telegram_request
from telegram.ext import Application

import core.database as database
from core import runtime_status
import handlers.dub_studio_registration as dub_registration
import main
import services.dub_studio_runtime as dub_runtime
import services.livedub_mix as livedub_mix
import services.polling_reliability_runtime as polling_runtime


class _FakeUpdater:
    def __init__(self, events: list[str], polling_error: RuntimeError | None) -> None:
        self.events = events
        self.polling_error = polling_error
        self.running = False
        self.poll_kwargs: dict[str, object] = {}

    async def start_polling(
        self,
        *,
        allowed_updates: object,
        drop_pending_updates: bool,
        error_callback: object,
    ) -> None:
        self.events.append("polling")
        self.running = True
        self.poll_kwargs = {
            "allowed_updates": allowed_updates,
            "drop_pending_updates": drop_pending_updates,
            "error_callback": error_callback,
        }
        if self.polling_error is not None:
            raise self.polling_error

    async def stop(self) -> None:
        self.events.append("updater_stop")
        self.running = False


class _FakeBot:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.command_registrations: list[tuple[object, object]] = []

    async def set_my_commands(self, commands: object, scope: object) -> None:
        self.events.append("commands")
        self.command_registrations.append((commands, scope))


class _FakeApplication:
    def __init__(self, events: list[str], polling_error: RuntimeError | None = None) -> None:
        self.events = events
        self.bot_data: dict[str, object] = {}
        self.bot = _FakeBot(events)
        self.updater = _FakeUpdater(events, polling_error)
        self.running = False
        self.handlers: list[tuple[object, int]] = []
        self.error_handlers: list[object] = []

    async def __aenter__(self) -> _FakeApplication:
        self.events.append("enter")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self.events.append("exit")

    def add_handler(self, handler: object, group: int = 0) -> None:
        self.handlers.append((handler, group))

    def add_error_handler(self, handler: object) -> None:
        self.error_handlers.append(handler)

    async def initialize(self) -> None:
        self.events.append("initialize")

    async def start(self) -> None:
        self.events.append("start")
        self.running = True

    async def stop(self) -> None:
        self.events.append("app_stop")
        self.running = False


class _FakeBuilder:
    def __init__(self, app: _FakeApplication) -> None:
        self.app = app
        self.token_value = ""

    def token(self, value: str) -> _FakeBuilder:
        self.token_value = value
        return self

    def request(self, value: object) -> _FakeBuilder:
        del value
        return self

    def get_updates_request(self, value: object) -> _FakeBuilder:
        del value
        return self

    def concurrent_updates(self, value: int) -> _FakeBuilder:
        del value
        return self

    def build(self) -> _FakeApplication:
        self.app.events.append("build")
        return self.app


def _install_offline_application(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stop_requested: bool,
    polling_error: RuntimeError | None = None,
) -> tuple[_FakeApplication, _FakeBuilder, list[str]]:
    events: list[str] = []
    app = _FakeApplication(events, polling_error)
    builder = _FakeBuilder(app)
    stop_event = threading.Event()
    if stop_requested:
        stop_event.set()

    monkeypatch.setattr(main, "BOT_TOKEN", "offline-orchestration-test-token")
    monkeypatch.setattr(main, "LOCAL_BOT_API_URL", "")
    monkeypatch.setattr(main, "HAS_FASTER_WHISPER", False)
    monkeypatch.setattr(main, "ADMIN_IDS", set())
    monkeypatch.setattr(main, "GEMINI_CLIENTS", [])
    monkeypatch.setattr(main, "_STOP_EVENT", stop_event)
    monkeypatch.setattr(main, "reset_rate_limit_async_state", lambda: 0)

    def _acquire() -> bool:
        events.append("acquire")
        return True

    async def _settings(name: str) -> bool:
        del name
        events.append("settings")
        return False

    def _request(**kwargs: object) -> object:
        del kwargs
        events.append("request")
        return object()

    def _register(application: object) -> None:
        assert application is app
        events.append("register")

    def _start_studio(application: object) -> None:
        assert application is app
        events.append("studio")

    def _polling_started() -> None:
        events.append("polling_heartbeat")

    def _ignore_signal(signum: int, handler: object) -> object:
        del signum, handler
        return None

    monkeypatch.setattr(main, "_acquire_singleton_lock", _acquire)
    monkeypatch.setattr(main, "asettings_get", _settings)
    monkeypatch.setattr(Application, "builder", lambda: builder)
    monkeypatch.setattr(telegram_request, "HTTPXRequest", _request)
    monkeypatch.setattr(polling_runtime, "PollingHeartbeatHTTPXRequest", _request)
    monkeypatch.setattr(dub_registration, "register_dub_studio", _register)
    monkeypatch.setattr(dub_runtime, "start_dub_studio_services", _start_studio)
    monkeypatch.setattr(runtime_status, "record_polling_started", _polling_started)
    monkeypatch.setattr(signal, "signal", _ignore_signal)
    monkeypatch.setattr(livedub_mix, "get_mix_params", lambda: {})
    monkeypatch.setattr(database, "current_livedub_file_id_cache_fingerprint", lambda: "offline-test")
    monkeypatch.delenv("TELEGRAM_PROXY_URL", raising=False)
    monkeypatch.delenv("LOCAL_BOT_API_PROXY_URL", raising=False)
    return app, builder, events


def _assert_ordered(events: list[str], expected: list[str]) -> None:
    positions = [events.index(item) for item in expected]
    assert positions == sorted(positions), events


def test_run_bot_async_without_token_stops_before_singleton_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def _forbidden_lock() -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(main, "BOT_TOKEN", "")
    monkeypatch.setattr(main, "_acquire_singleton_lock", _forbidden_lock)

    assert asyncio.run(main.run_bot_async()) is None
    assert called is False


def test_run_bot_async_singleton_conflict_stops_before_application_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "BOT_TOKEN", "offline-orchestration-test-token")
    monkeypatch.setattr(main, "_acquire_singleton_lock", lambda: False)
    monkeypatch.setattr(
        Application,
        "builder",
        lambda: pytest.fail("Application builder must not run after singleton conflict"),
    )

    assert asyncio.run(main.run_bot_async()) == "singleton_conflict"


def test_run_bot_async_executes_offline_application_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    app, builder, events = _install_offline_application(monkeypatch, stop_requested=True)

    result = asyncio.run(main.run_bot_async())

    assert result == "stop_requested"
    assert builder.token_value == "offline-orchestration-test-token"
    _assert_ordered(
        events,
        [
            "acquire",
            "build",
            "register",
            "enter",
            "initialize",
            "start",
            "studio",
            "polling_heartbeat",
            "polling",
            "commands",
            "updater_stop",
            "app_stop",
            "exit",
        ],
    )
    assert app.updater.poll_kwargs["allowed_updates"] == Update.ALL_TYPES
    assert app.updater.poll_kwargs["drop_pending_updates"] is False
    assert app.updater.running is False
    assert app.running is False
    assert len(app.handlers) >= 30
    assert app.handlers[0][1] == -100
    assert len(app.error_handlers) == 1


def test_run_bot_async_polling_failure_stops_started_application(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _, events = _install_offline_application(
        monkeypatch,
        stop_requested=False,
        polling_error=RuntimeError("OFFLINE_POLLING_FAILURE"),
    )

    with pytest.raises(RuntimeError, match="OFFLINE_POLLING_FAILURE"):
        asyncio.run(main.run_bot_async())

    _assert_ordered(
        events,
        ["initialize", "start", "polling", "updater_stop", "app_stop", "exit"],
    )
    assert "commands" not in events
    assert app.updater.running is False
    assert app.running is False


def test_supported_root_entrypoints_are_inside_coverage_source() -> None:
    with Path("pyproject.toml").open("rb") as handle:
        source = tomllib.load(handle)["tool"]["coverage"]["run"]["source"]
    assert {"main", "bot_new", "bot"}.issubset(source)
