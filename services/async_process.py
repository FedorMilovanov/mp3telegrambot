#!/usr/bin/env python3
"""Cancellation-safe ownership of long-running process trees."""
from __future__ import annotations

import asyncio
import math
import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


_WINDOWS_CREATE_NEW_PROCESS_GROUP = getattr(
    subprocess,
    "CREATE_NEW_PROCESS_GROUP",
    0x00000200,
)
_WINDOWS_CREATE_NO_WINDOW = getattr(
    subprocess,
    "CREATE_NO_WINDOW",
    0x08000000,
)


def _finite_seconds(value: Any, *, name: str) -> float:
    """Resolve a finite positive wait value before any child is spawned."""
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(seconds):
        raise ValueError(f"{name} must be a finite number")
    return max(0.1, seconds)


def _process_pid(process: asyncio.subprocess.Process) -> int | None:
    """Return a usable process/group id without assuming a concrete transport."""
    try:
        pid = int(process.pid)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    return pid if pid > 0 else None


def _spawn_group_kwargs() -> dict[str, Any]:
    """Isolate every command so cancellation can target its complete tree."""
    if os.name == "nt":
        return {"creationflags": _WINDOWS_CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _signal_posix_tree(
    process: asyncio.subprocess.Process,
    sig: signal.Signals,
) -> None:
    """Signal the isolated POSIX group, falling back to the direct child."""
    pid = _process_pid(process)
    if pid is not None:
        try:
            os.killpg(pid, sig)
            return
        except ProcessLookupError:
            return
        except (PermissionError, OSError):
            # A mocked/non-session child or an unusual platform can still be
            # cleaned up directly. Real owned processes are always sessions.
            pass

    if process.returncode is not None:
        return
    try:
        if sig == signal.SIGKILL:
            process.kill()
        else:
            process.terminate()
    except ProcessLookupError:
        pass


async def _taskkill_windows_tree(pid: int, *, timeout: float) -> bool:
    """Force-stop a Windows process and all descendants with the OS tool."""
    try:
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(pid),
            "/T",
            "/F",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=_WINDOWS_CREATE_NO_WINDOW,
        )
    except (FileNotFoundError, OSError):
        return False

    try:
        await asyncio.wait_for(killer.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            killer.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(killer.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return False
    return killer.returncode == 0


async def _stop_direct_process_fallback(
    process: asyncio.subprocess.Process,
    *,
    grace_seconds: float,
) -> None:
    """Preserve terminate→grace→kill semantics when tree tooling fails."""
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        pass

    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        return
    except asyncio.TimeoutError:
        pass

    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass

    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            "direct child did not stop after terminate/kill fallback"
        ) from exc


async def _stop_windows_tree(
    process: asyncio.subprocess.Process,
    *,
    grace_seconds: float,
) -> None:
    """Stop a Windows tree before its leader can orphan descendants."""
    pid = _process_pid(process)
    tree_stopped = False
    if pid is not None:
        tree_stopped = await _taskkill_windows_tree(pid, timeout=grace_seconds)

    if not tree_stopped:
        await _stop_direct_process_fallback(
            process,
            grace_seconds=grace_seconds,
        )
        return

    if process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "Windows process tree did not stop after taskkill"
            ) from exc


async def _stop_posix_tree(
    process: asyncio.subprocess.Process,
    *,
    grace_seconds: float,
) -> None:
    """Gracefully stop, then force-sweep the complete isolated POSIX group."""
    if process.returncode is None:
        _signal_posix_tree(process, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        except asyncio.TimeoutError:
            pass

    # Always sweep the group with SIGKILL. The leader may have exited after
    # SIGTERM while a grandchild ignored it and kept stdout/stderr pipes open.
    _signal_posix_tree(process, signal.SIGKILL)

    if process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "POSIX process tree did not stop after SIGTERM/SIGKILL"
            ) from exc


async def _stop_process(
    process: asyncio.subprocess.Process,
    *,
    grace_seconds: float = 3.0,
) -> None:
    """Stop and reap the complete owned process tree before returning."""
    grace = _finite_seconds(grace_seconds, name="grace_seconds")
    if os.name == "nt":
        await _stop_windows_tree(process, grace_seconds=grace)
    else:
        await _stop_posix_tree(process, grace_seconds=grace)


async def _stop_before_returning(
    process: asyncio.subprocess.Process,
    *,
    grace_seconds: float,
) -> None:
    """Finish tree cleanup before propagating one or many cancellations."""
    stop_task = asyncio.create_task(
        _stop_process(process, grace_seconds=grace_seconds)
    )
    cancellation: asyncio.CancelledError | None = None

    while True:
        try:
            await asyncio.shield(stop_task)
            break
        except asyncio.CancelledError as exc:
            # Shield prevents the outer cancellation from cancelling the owned
            # cleanup task. Keep waiting through repeated cancel() calls.
            if stop_task.cancelled():
                raise RuntimeError("child cleanup task was cancelled") from exc
            cancellation = exc
            if stop_task.done():
                stop_task.result()
                break

    if cancellation is not None:
        raise cancellation


async def run_cancellable_process(
    command: Sequence[str | os.PathLike[str]],
    *,
    timeout: float,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    text: bool = False,
    grace_seconds: float = 3.0,
) -> subprocess.CompletedProcess[Any]:
    """Run one command and retain ownership through timeout or cancellation.

    Unlike ``run_in_executor(subprocess.run(...))``, cancelling the coroutine
    cannot release a semaphore or remove temporary files while yt-dlp, FFmpeg,
    or another descendant keeps running. The isolated process tree is stopped
    and its direct child is reaped first.
    """
    deadline = _finite_seconds(timeout, name="timeout")
    cleanup_grace = _finite_seconds(grace_seconds, name="grace_seconds")
    argv = [os.fspath(value) for value in command]
    if not argv:
        raise ValueError("command must not be empty")

    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=os.fspath(Path(cwd)) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **_spawn_group_kwargs(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=deadline,
        )
    except asyncio.TimeoutError as exc:
        await _stop_before_returning(
            process,
            grace_seconds=cleanup_grace,
        )
        raise subprocess.TimeoutExpired(argv, timeout=deadline) from exc
    except asyncio.CancelledError:
        await _stop_before_returning(
            process,
            grace_seconds=cleanup_grace,
        )
        raise

    if text:
        stdout_value: Any = (stdout or b"").decode("utf-8", errors="replace")
        stderr_value: Any = (stderr or b"").decode("utf-8", errors="replace")
    else:
        stdout_value = stdout
        stderr_value = stderr
    return subprocess.CompletedProcess(
        argv,
        int(process.returncode or 0),
        stdout_value,
        stderr_value,
    )


__all__ = ["run_cancellable_process"]
