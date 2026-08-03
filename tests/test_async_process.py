from __future__ import annotations

import asyncio
import subprocess

import pytest

from services import async_process


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.terminated = 0
        self.killed = 0
        self.wait_calls = 0
        self.communicate_started = asyncio.Event()
        self.release_communicate = asyncio.Event()

    async def communicate(self):
        self.communicate_started.set()
        await self.release_communicate.wait()
        self.returncode = 0
        return b"out", b"err"

    def terminate(self) -> None:
        self.terminated += 1
        self.returncode = -15

    def kill(self) -> None:
        self.killed += 1
        self.returncode = -9

    async def wait(self) -> int:
        self.wait_calls += 1
        if self.returncode is None:
            await asyncio.sleep(3600)
        return int(self.returncode)


class _TerminateLookupRace:
    def __init__(self) -> None:
        self.returncode = None
        self.terminated = 0
        self.wait_calls = 0

    def terminate(self) -> None:
        self.terminated += 1
        raise ProcessLookupError

    def kill(self) -> None:
        raise AssertionError("kill must not be needed after successful reap")

    async def wait(self) -> int:
        self.wait_calls += 1
        self.returncode = 0
        return 0


class _KillLookupRace:
    def __init__(self) -> None:
        self.returncode = None
        self.terminated = 0
        self.killed = 0
        self.wait_calls = 0

    def terminate(self) -> None:
        self.terminated += 1

    def kill(self) -> None:
        self.killed += 1
        raise ProcessLookupError

    async def wait(self) -> int:
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise asyncio.TimeoutError
        self.returncode = 0
        return 0


@pytest.mark.asyncio
async def test_terminate_lookup_race_is_still_reaped() -> None:
    process = _TerminateLookupRace()

    await async_process._stop_process(process, grace_seconds=0.1)

    assert process.terminated == 1
    assert process.wait_calls == 1
    assert process.returncode == 0


@pytest.mark.asyncio
async def test_kill_lookup_race_is_still_reaped() -> None:
    process = _KillLookupRace()

    await async_process._stop_process(process, grace_seconds=0.1)

    assert process.terminated == 1
    assert process.killed == 1
    assert process.wait_calls == 2
    assert process.returncode == 0


@pytest.mark.asyncio
async def test_timeout_stops_and_reaps_child(monkeypatch) -> None:
    process = _FakeProcess()

    async def fake_create(*args, **kwargs):
        return process

    monkeypatch.setattr(async_process.asyncio, "create_subprocess_exec", fake_create)

    with pytest.raises(subprocess.TimeoutExpired):
        await async_process.run_cancellable_process(
            ["ffmpeg", "-version"],
            timeout=0.01,
        )

    assert process.terminated == 1
    assert process.killed == 0
    assert process.wait_calls == 1
    assert process.returncode == -15


@pytest.mark.asyncio
async def test_cancellation_stops_child_before_task_finishes(monkeypatch) -> None:
    process = _FakeProcess()

    async def fake_create(*args, **kwargs):
        return process

    monkeypatch.setattr(async_process.asyncio, "create_subprocess_exec", fake_create)
    task = asyncio.create_task(
        async_process.run_cancellable_process(
            ["ffmpeg", "-version"],
            timeout=60,
        )
    )
    await process.communicate_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.terminated == 1
    assert process.wait_calls == 1
    assert process.returncode == -15


@pytest.mark.asyncio
async def test_success_returns_completed_process_and_closes_stdin(monkeypatch) -> None:
    process = _FakeProcess()
    process.release_communicate.set()
    captured_kwargs = {}

    async def fake_create(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return process

    monkeypatch.setattr(async_process.asyncio, "create_subprocess_exec", fake_create)
    result = await async_process.run_cancellable_process(
        ["ffmpeg", "-version"],
        timeout=60,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == "out"
    assert result.stderr == "err"
    assert process.terminated == 0
    assert captured_kwargs["stdin"] is asyncio.subprocess.DEVNULL
