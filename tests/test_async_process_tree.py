from __future__ import annotations

import asyncio
import ctypes
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from services import async_process


class _CompletedProcess:
    pid = 43210
    returncode = 0

    async def communicate(self):
        return b"out", b"err"


@pytest.mark.asyncio
async def test_posix_spawn_starts_new_session(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create(*args, **kwargs):
        captured.update(kwargs)
        return _CompletedProcess()

    monkeypatch.setattr(async_process.os, "name", "posix")
    monkeypatch.setattr(async_process.asyncio, "create_subprocess_exec", fake_create)

    result = await async_process.run_cancellable_process(
        ["ffmpeg", "-version"],
        timeout=60,
    )

    assert result.returncode == 0
    assert captured["start_new_session"] is True
    assert "creationflags" not in captured


@pytest.mark.asyncio
async def test_windows_spawn_uses_new_process_group(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create(*args, **kwargs):
        captured.update(kwargs)
        return _CompletedProcess()

    monkeypatch.setattr(async_process.os, "name", "nt")
    monkeypatch.setattr(async_process.asyncio, "create_subprocess_exec", fake_create)

    result = await async_process.run_cancellable_process(
        ["ffmpeg", "-version"],
        timeout=60,
    )

    assert result.returncode == 0
    assert captured["creationflags"] == async_process._WINDOWS_CREATE_NEW_PROCESS_GROUP
    assert "start_new_session" not in captured


def _pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        try:
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            return bool(ok and exit_code.value == still_active)
        finally:
            kernel32.CloseHandle(handle)

    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        try:
            fields = proc_stat.read_text(encoding="utf-8").split()
            if len(fields) > 2 and fields[2] == "Z":
                return False
        except OSError:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _force_cleanup(pid: int) -> None:
    if not _pid_is_running(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            timeout=5,
        )
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


@pytest.mark.asyncio
async def test_timeout_stops_grandchild_that_ignores_graceful_signal(
    tmp_path: Path,
) -> None:
    child_script = tmp_path / "tree_child.py"
    parent_script = tmp_path / "tree_parent.py"
    child_pid_file = tmp_path / "child.pid"

    child_script.write_text(
        "import os\n"
        "import signal\n"
        "import time\n"
        "if os.name != 'nt':\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "while True:\n"
        "    time.sleep(1)\n",
        encoding="utf-8",
    )
    parent_script.write_text(
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, sys.argv[1]])\n"
        "Path(sys.argv[2]).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    child_pid: int | None = None
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            await async_process.run_cancellable_process(
                [
                    sys.executable,
                    str(parent_script),
                    str(child_script),
                    str(child_pid_file),
                ],
                timeout=2.0,
                grace_seconds=0.5,
            )

        assert child_pid_file.exists(), "parent did not publish grandchild pid"
        child_pid = int(child_pid_file.read_text(encoding="utf-8").strip())

        for _ in range(100):
            if not _pid_is_running(child_pid):
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail(f"grandchild process {child_pid} survived owner timeout")
    finally:
        if child_pid is not None:
            _force_cleanup(child_pid)
