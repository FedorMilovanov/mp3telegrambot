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

from services import async_process, bgutil_http_runtime, dub_execution, dub_studio, dub_studio_runtime, dub_worker


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
    run = Mock(side_effect=OSError("no taskkill"))
    monkeypatch.setattr(bgutil_http_runtime, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        bgutil_http_runtime, "resolve_required_executable", lambda _name: r"C:\Windows\System32\taskkill.exe"
    )
    monkeypatch.setattr(bgutil_http_runtime.subprocess, "run", run)

    bgutil_http_runtime._force_stop_tree(process)

    assert run.call_args.args[0][0] == r"C:\Windows\System32\taskkill.exe"
    process.kill.assert_called_once_with()


def test_execution_windows_taskkill_uses_resolved_executable(monkeypatch) -> None:
    run = Mock(return_value=None)
    monkeypatch.setattr(dub_execution, "_pid_is_running", Mock(side_effect=[True, False, False]))
    monkeypatch.setattr(dub_execution, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        dub_execution,
        "resolve_required_executable",
        lambda _name: r"C:\Windows\System32\taskkill.exe",
    )
    monkeypatch.setattr(dub_execution.subprocess, "run", run)

    dub_execution.terminate_process_tree(123, timeout=1)

    assert run.call_args.args[0] == [
        r"C:\Windows\System32\taskkill.exe",
        "/PID",
        "123",
        "/T",
        "/F",
    ]


def test_bgutil_posix_group_failure_falls_back_to_kill(monkeypatch) -> None:
    process = SimpleNamespace(pid=123, poll=Mock(return_value=None), kill=Mock())
    monkeypatch.setattr(
        bgutil_http_runtime,
        "os",
        SimpleNamespace(name="posix", killpg=Mock(side_effect=ProcessLookupError)),
    )

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
    monkeypatch.setattr(bgutil_http_runtime, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        bgutil_http_runtime,
        "resolve_required_executable",
        lambda _name: r"C:\Windows\System32\taskkill.exe",
    )
    monkeypatch.setattr(bgutil_http_runtime.subprocess, "run", Mock(return_value=None))

    bgutil_http_runtime.stop_owned_bgutil_http_runtime()

    assert process.wait.call_count == 2
    assert bgutil_http_runtime._OWNED_PROCESS is None


def test_runner_sigkill_falls_back_to_single_pid(monkeypatch) -> None:
    running_states = iter([True, False, True])
    monkeypatch.setattr(dub_execution, "_pid_is_running", lambda pid: next(running_states))
    killpg = Mock(side_effect=[None, OSError("process group unavailable")])
    kill = Mock()
    monkeypatch.setattr(
        dub_execution,
        "os",
        SimpleNamespace(name="posix", killpg=killpg, kill=kill),
    )

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
    monkeypatch.setattr(
        dub_worker,
        "os",
        SimpleNamespace(name="posix", killpg=Mock(side_effect=OSError("no process group"))),
    )

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
    monkeypatch.setattr(
        dub_worker,
        "os",
        SimpleNamespace(name="posix", killpg=Mock(side_effect=OSError("no process group"))),
    )

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
    run = Mock(side_effect=OSError("no taskkill"))
    monkeypatch.setattr(dub_worker, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        dub_worker,
        "resolve_required_executable",
        lambda _name: r"C:\Windows\System32\taskkill.exe",
    )
    monkeypatch.setattr(dub_worker.subprocess, "run", run)

    dub_worker._terminate_process(process)

    assert run.call_args.args[0][0] == r"C:\Windows\System32\taskkill.exe"
    process.terminate.assert_called_once_with()
    process.kill.assert_not_called()


def test_studio_stale_worker_uses_resolved_taskkill(monkeypatch) -> None:
    run = Mock(return_value=None)
    monkeypatch.setattr(
        dub_studio_runtime,
        "resolve_required_executable",
        lambda _name: r"C:\Windows\System32\taskkill.exe",
    )
    monkeypatch.setattr(dub_studio_runtime.subprocess, "run", run)

    dub_studio_runtime._stop_stale_windows_worker(123)

    assert run.call_args.args[0] == [
        r"C:\Windows\System32\taskkill.exe",
        "/PID",
        "123",
        "/T",
        "/F",
    ]


def test_dub_worker_windows_pid_probe_closes_process_handle(monkeypatch) -> None:
    import ctypes

    kernel32 = SimpleNamespace(OpenProcess=Mock(return_value=66), CloseHandle=Mock())
    monkeypatch.setattr(dub_worker, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False)

    assert dub_worker._pid_is_running(123) is True
    kernel32.OpenProcess.assert_called_once_with(0x1000, False, 123)
    kernel32.CloseHandle.assert_called_once_with(66)


def test_studio_windows_pid_probe_closes_process_handle(monkeypatch) -> None:
    import ctypes

    kernel32 = SimpleNamespace(OpenProcess=Mock(return_value=77), CloseHandle=Mock())
    monkeypatch.setattr(dub_studio_runtime, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(kernel32=kernel32),
        raising=False,
    )

    assert dub_studio_runtime._pid_running(123) is True
    kernel32.OpenProcess.assert_called_once_with(0x1000, False, 123)
    kernel32.CloseHandle.assert_called_once_with(77)


def test_studio_stale_worker_preserves_nonfatal_failure(monkeypatch, caplog) -> None:
    monkeypatch.setattr(dub_studio_runtime, "resolve_required_executable", lambda _name: "/resolved/taskkill")
    monkeypatch.setattr(dub_studio_runtime.subprocess, "run", Mock(side_effect=OSError("unavailable")))
    caplog.set_level("WARNING", logger="services.dub_studio_runtime")

    dub_studio_runtime._stop_stale_windows_worker(123)

    assert "Could not replace old Dub Studio worker" in caplog.text


def test_studio_worker_replacement_executes_stale_windows_cleanup(monkeypatch, tmp_path: Path) -> None:
    class Store:
        def latest_worker(self):
            return {"pid": 123, "status": "idle", "details": {"runtime": "old"}}

    stop = Mock()
    popen = Mock(return_value=SimpleNamespace(pid=456))
    monkeypatch.setattr(dub_studio_runtime, "enabled", lambda: True)
    monkeypatch.setattr(dub_studio_runtime, "_flag", lambda _name, _default: True)
    monkeypatch.setattr(dub_studio_runtime, "_pid_running", lambda _pid: True)
    monkeypatch.setattr(dub_studio_runtime, "_stop_stale_windows_worker", stop)
    monkeypatch.setattr(dub_studio_runtime, "os", SimpleNamespace(name="nt", environ={}))
    monkeypatch.setattr(dub_studio_runtime.subprocess, "Popen", popen)
    monkeypatch.setattr(dub_studio, "DubStore", Store)
    monkeypatch.setattr(dub_studio, "worker_is_fresh", lambda _worker: True)
    monkeypatch.setattr(dub_studio, "studio_root", lambda: tmp_path)
    monkeypatch.setattr(dub_studio, "repo_root", lambda: tmp_path)

    assert dub_studio_runtime.ensure_worker_running() is True
    stop.assert_called_once_with(123)
    popen.assert_called_once()


def test_quality_retry_log_is_archived(tmp_path: Path) -> None:
    source = tmp_path / "job-000007.log"
    source.write_text("render log", encoding="utf-8")
    store = SimpleNamespace(logs_dir=tmp_path)

    dub_worker._archive_quality_retry_log(store, 7, 2)

    assert (tmp_path / "job-000007.quality-retry-02.log").read_text(encoding="utf-8") == "render log"
