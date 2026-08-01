from __future__ import annotations

from types import ModuleType

import pytest

from services.bot_lifecycle import BOT_LIFECYCLE_POLICY, run_bot_process


def _module(result):
    module = ModuleType("test_main")

    async def run_bot_async():
        return result

    module.run_bot_async = run_bot_async
    module.flask_app = object()
    return module


def test_single_loop_returns_zero_for_normal_stop(monkeypatch):
    monkeypatch.setenv("DISABLE_HEALTH_CHECK", "1")
    assert BOT_LIFECYCLE_POLICY == "single-event-loop-external-supervisor-v1"
    assert run_bot_process(_module("stop_requested")) == 0


def test_singleton_conflict_is_nonzero_without_retry(monkeypatch):
    monkeypatch.setenv("DISABLE_HEALTH_CHECK", "1")
    calls = []
    module = ModuleType("test_main")

    async def run_bot_async():
        calls.append("run")
        return "singleton_conflict"

    module.run_bot_async = run_bot_async
    module.flask_app = object()

    assert run_bot_process(module) == 2
    assert calls == ["run"]


def test_programming_error_escapes_for_external_supervisor(monkeypatch):
    monkeypatch.setenv("DISABLE_HEALTH_CHECK", "1")
    module = ModuleType("test_main")

    async def run_bot_async():
        raise RuntimeError("PROGRAMMING_SENTINEL")

    module.run_bot_async = run_bot_async
    module.flask_app = object()

    with pytest.raises(RuntimeError, match="PROGRAMMING_SENTINEL"):
        run_bot_process(module)


def test_unexpected_result_fails_closed(monkeypatch):
    monkeypatch.setenv("DISABLE_HEALTH_CHECK", "1")
    with pytest.raises(RuntimeError, match="Unexpected"):
        run_bot_process(_module("restart_me"))
