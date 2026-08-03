from __future__ import annotations

import asyncio

import pytest

from services.async_worker import (
    await_owned_coroutine,
    await_owned_with_soft_timeout,
)


@pytest.mark.asyncio
async def test_owned_coroutine_returns_result() -> None:
    async def inner() -> str:
        await asyncio.sleep(0)
        return "done"

    assert await await_owned_coroutine(inner()) == "done"


@pytest.mark.asyncio
async def test_owned_coroutine_accepts_existing_task() -> None:
    async def inner() -> str:
        await asyncio.sleep(0)
        return "task-result"

    inner_task = asyncio.create_task(inner())
    assert await await_owned_coroutine(inner_task) == "task-result"


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_inner_completion() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    inner_cancelled = False

    async def inner() -> str:
        nonlocal inner_cancelled
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            inner_cancelled = True
            raise
        return "finished"

    task = asyncio.create_task(await_owned_coroutine(inner()))
    await started.wait()

    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)

    assert task.done() is False
    assert inner_cancelled is False

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert inner_cancelled is False


@pytest.mark.asyncio
async def test_inner_exception_is_preserved_without_outer_cancellation() -> None:
    async def inner() -> None:
        raise RuntimeError("worker failed")

    with pytest.raises(RuntimeError, match="worker failed"):
        await await_owned_coroutine(inner())


@pytest.mark.asyncio
async def test_outer_cancellation_wins_after_inner_failure() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def inner() -> None:
        started.set()
        await release.wait()
        raise RuntimeError("late worker failure")

    task = asyncio.create_task(await_owned_coroutine(inner()))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await task

    assert isinstance(exc_info.value.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_inner_self_cancellation_is_preserved() -> None:
    async def inner() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await await_owned_coroutine(inner())


@pytest.mark.asyncio
async def test_soft_timeout_keeps_and_returns_late_success() -> None:
    release = asyncio.Event()

    async def inner() -> str:
        await release.wait()
        return "late-success"

    asyncio.get_running_loop().call_later(0.04, release.set)
    result, deadline_exceeded = await await_owned_with_soft_timeout(
        inner(),
        timeout=0.01,
    )

    assert result == "late-success"
    assert deadline_exceeded is True


@pytest.mark.asyncio
async def test_soft_timeout_reports_fast_success() -> None:
    async def inner() -> str:
        return "fast-success"

    result, deadline_exceeded = await await_owned_with_soft_timeout(
        inner(),
        timeout=1.0,
    )

    assert result == "fast-success"
    assert deadline_exceeded is False


@pytest.mark.asyncio
async def test_repeated_cancel_after_soft_timeout_does_not_orphan_inner() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    inner_cancelled = False

    async def inner() -> str:
        nonlocal inner_cancelled
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            inner_cancelled = True
            raise
        return "finished"

    outer = asyncio.create_task(
        await_owned_with_soft_timeout(inner(), timeout=0.01)
    )
    await started.wait()
    await asyncio.sleep(0.03)

    outer.cancel()
    await asyncio.sleep(0)
    outer.cancel()
    await asyncio.sleep(0)

    assert outer.done() is False
    assert inner_cancelled is False
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await outer
    assert inner_cancelled is False


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), "bad"])
async def test_soft_timeout_rejects_invalid_deadline_before_ownership(timeout) -> None:
    future = asyncio.get_running_loop().create_future()
    with pytest.raises(ValueError, match="finite positive"):
        await await_owned_with_soft_timeout(future, timeout=timeout)
    assert future.done() is False
    future.cancel()
