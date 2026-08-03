#!/usr/bin/env python3
"""Cancellation-safe ownership of long-running child processes."""
from __future__ import annotations

import asyncio
import math
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def _finite_seconds(value: Any, *, name: str) -> float:
    """Resolve a finite positive wait value before any child is spawned."""
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(seconds):
        raise ValueError(f"{name} must be a finite number")
    return max(0.1, seconds)


async def _stop_process(
    process: asyncio.subprocess.Process,
    *,
    grace_seconds: float = 3.0,
) -> None:
    """Terminate a child and do not return while it may still be running.

    ``ProcessLookupError`` only says that the signal could not be delivered;
    it does not prove the asyncio child watcher has reaped the process. We
    therefore still await ``wait()`` after a failed terminate/kill attempt.
    """
    if process.returncode is not None:
        return

    grace = _finite_seconds(grace_seconds, name="grace_seconds")
    try:
        process.terminate()
    except ProcessLookupError:
        pass

    try:
        await asyncio.wait_for(process.wait(), timeout=grace)
        return
    except asyncio.TimeoutError:
        pass

    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass

    try:
        await asyncio.wait_for(process.wait(), timeout=grace)
    except asyncio.TimeoutError as exc:
        # The OS or child watcher still owns the process. The caller must see a
        # hard failure, not proceed as if cleanup/resource release were safe.
        raise RuntimeError(
            "child process did not stop after terminate/kill"
        ) from exc


async def _stop_before_returning(
    process: asyncio.subprocess.Process,
    *,
    grace_seconds: float,
) -> None:
    """Finish child cleanup before propagating one or many cancellations."""
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
    cannot release a semaphore or remove temporary files while the child keeps
    running in a worker thread. The child is stopped and reaped first.
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
