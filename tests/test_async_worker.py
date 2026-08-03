from __future__ import annotations

import asyncio

import pytest

from services.async_worker import await_owned_coroutine


@pytest.mark.asyncio
async def test_owned_coroutine_returns_result() -> None:
    async def inner() -> str:
        await asyncio.sleep(0)
        return "done"

    assert await await_owned_coroutine(inner()) == "done"


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
