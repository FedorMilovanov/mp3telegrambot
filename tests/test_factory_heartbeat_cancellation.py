from __future__ import annotations

import asyncio

import pytest

from services.shorts_factory_capacity import await_with_heartbeat


@pytest.mark.asyncio
async def test_cancelling_heartbeat_owner_cancels_and_joins_inflight_call() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def in_flight() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    child = asyncio.create_task(in_flight())
    owner = asyncio.create_task(await_with_heartbeat(child, label="Factory request", heartbeat=0.01))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert cancelled.is_set(), "The actual Gemini call must stop before its owner exits"
        assert child.done()
    finally:
        if not child.done():
            child.cancel()
            await asyncio.gather(child, return_exceptions=True)


@pytest.mark.asyncio
async def test_heartbeat_propagates_child_failure() -> None:
    async def fail() -> None:
        raise ValueError("provider failure")

    with pytest.raises(ValueError, match="provider failure"):
        await await_with_heartbeat(fail(), label="Factory request", heartbeat=0.01)


@pytest.mark.asyncio
async def test_heartbeat_preserves_successful_result() -> None:
    async def succeed() -> str:
        return "ok"

    assert await await_with_heartbeat(succeed(), label="Factory request", heartbeat=0.01) == "ok"
