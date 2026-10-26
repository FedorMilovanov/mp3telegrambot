from __future__ import annotations

import asyncio
import io
import queue
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services import async_process, bgutil_http_runtime, dub_execution, dub_worker


class DirectProcess:
    def __init__(self, *, terminate_stops: bool) -> None:
        self.returncode: int | None = None
        self.terminate_stops = terminate_stops
        self.terminate_calls = 0
        self.kill_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.terminate_stops:
            self.returncode = 0

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            raise asyncio.TimeoutError
        return self.returncode


@pytest.mark.asyncio
@pytest.mark.parametrize("terminate_stops", [True, False])
async def test_direct_process_fallback_preserves_terminate_then_kill(terminate_stops: bool) -> None:
    process = DirectProcess(terminate_stops=terminate_stops)

    await async_process._stop_direct_process_fallback(process, grace_seconds=0.01)

    assert process.terminate_calls == 1
    assert process.kill_calls == (0 if terminate_stops else 1)


def test_bgutil_windows_taskkill_failure_falls_back_to_kill(monkeypatch) -> None:
    process = SimpleNamespace(pid=123, poll=Mock(return_value=None), kill=Mock())
    monkeypatch.setattr(bgutil_http_runtime.os, "name", "nt")
    monkeypatch.setattr(bgutil_http_runtime.subprocess, "run", Mock(side_effect=OSError("no taskkill")))

    bgutil_http_runtime._force_stop_tree(process)

    process.kill.assert_called_once_with()


def test_bgutil_posix_group_failure_falls_back_to_kill(monkeypatch) -> None:
    process = SimpleNamespace(pid=123, poll=Mock(return_value=None), kill=Mock())
    monkeypatch.setattr(bgutil_http_runtime.os, "name", "posix")
    monkeypatch.setattr(bgutil_http_runtime.os, "killpg", Mock(side_effect=ProcessLookupError))

    bgutil_http_runtime._force_stop_tree(process)

    process.kill.assert_called_once_with()


def test_bgutil_final_wait_timeout_is_ignored_after_force_stop(monkeypatch) -> None:
    process = SimpleNamespace(
        pid=123,
        poll=Mock(return_value=None),
        terminate=Mock(),
        kill=Mock(),
        wait=Mock(side_effect=subprocess.TimeoutExpired("provider", 1)),
    )
    monkeypatch.setattr(bgutil_http_runtime, "_OWNED_PROCESS", process)
    monkeypatch.setattr(bgutil_http_runtime.os, "name", "nt")
    monkeypatch.setattr(bgutil_http_runtime.subprocess, "run", Mock(return_value=None))

    bgutil_http_runtime.stop_owned_bgutil_http_runtime()

    assert process.wait.call_count == 2
    assert bgutil_http_runtime._OWNED_PROCESS is None


def test_runner_sigkill_falls_back_to_single_pid(monkeypatch) -> None:
    running_states = iter([True, False, True])
    monkeypatch.setattr(dub_execution, "_pid_is_running", lambda pid: next(running_states))
    monkeypatch.setattr(dub_execution.os, "name", "posix")
    killpg = Mock(side_effect=[None, OSError("process group unavailable")])
    kill = Mock()
    monkeypatch.setattr(dub_execution.os, "killpg", killpg)
    monkeypatch.setattr(dub_execution.os, "kill", kill)

    dub_execution.terminate_process_tree(123, timeout=1)

    kill.assert_called_once_with(123, signal.SIGKILL)


def test_dub_worker_reader_always_closes_pipe() -> None:
    pipe = io.StringIO("first\nsecond\n")
    output: queue.Queue[str] = queue.Queue()

    dub_worker._reader(pipe, output)

    assert pipe.closed
    assert [output.get_nowait(), output.get_nowait()] == ["first\n", "second\n"]


def test_basic_terminate_uses_kill_for_group_failure_and_timeout(monkeypatch) -> None:
    process = SimpleNamespace(
        pid=123,
        poll=Mock(return_value=None),
        kill=Mock(),
        wait=Mock(side_effect=subprocess.TimeoutExpired("runner", 15)),
    )
    monkeypatch.setattr(dub_worker.os, "name", "posix")
    monkeypatch.setattr(dub_worker.os, "killpg", Mock(side_effect=OSError("no process group")))

    dub_worker._basic_terminate_process(process)

    assert process.kill.call_count == 2


def test_terminate_uses_fallback_for_group_failure_and_timeout(monkeypatch) -> None:
    process = SimpleNamespace(
        pid=123,
        poll=Mock(return_value=None),
        terminate=Mock(),
        kill=Mock(),
        wait=Mock(side_effect=subprocess.TimeoutExpired("runner", 15)),
    )
    monkeypatch.setattr(dub_worker.os, "name", "posix")
    monkeypatch.setattr(dub_worker.os, "killpg", Mock(side_effect=OSError("no process group")))

    dub_worker._terminate_process(process)

    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()


def test_windows_taskkill_failure_falls_back_to_terminate(monkeypatch) -> None:
    process = SimpleNamespace(
        pid=123,
        poll=Mock(return_value=None),
        terminate=Mock(),
        kill=Mock(),
        wait=Mock(return_value=0),
    )
    monkeypatch.setattr(dub_worker.os, "name", "nt")
    monkeypatch.setattr(dub_worker.subprocess, "run", Mock(side_effect=OSError("no taskkill")))

    dub_worker._terminate_process(process)

    process.terminate.assert_called_once_with()
    process.kill.assert_not_called()


def test_quality_retry_log_is_archived(tmp_path: Path) -> None:
    source = tmp_path / "job-000007.log"
    source.write_text("render log", encoding="utf-8")
    store = SimpleNamespace(logs_dir=tmp_path)

    dub_worker._archive_quality_retry_log(store, 7, 2)

    assert (tmp_path / "job-000007.quality-retry-02.log").read_text(encoding="utf-8") == "render log"
