#!/usr/bin/env python3
"""Cancellation-safe ownership of long-running child processes."""
from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


async def _stop_process(
    process: asyncio.subprocess.Process,
    *,
    grace_seconds: float = 3.0,
) -> None:
    """Terminate a child and do not return while it may still be running."""
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return

    try:
        await asyncio.wait_for(process.wait(), timeout=max(0.1, grace_seconds))
        return
    except asyncio.TimeoutError:
        pass

    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=max(0.1, grace_seconds))
        except asyncio.TimeoutError:
            # The OS still owns the process. The caller must see a hard failure,
            # not proceed as if cleanup and resource release were safe.
            raise RuntimeError("child process did not stop after terminate/kill")


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
    argv = [os.fspath(value) for value in command]
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=os.fspath(Path(cwd)) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=max(0.1, float(timeout)),
        )
    except asyncio.TimeoutError as exc:
        await asyncio.shield(
            _stop_process(process, grace_seconds=grace_seconds)
        )
        raise subprocess.TimeoutExpired(argv, timeout=timeout) from exc
    except asyncio.CancelledError:
        await asyncio.shield(
            _stop_process(process, grace_seconds=grace_seconds)
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
