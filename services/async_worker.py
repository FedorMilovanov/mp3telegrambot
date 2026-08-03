#!/usr/bin/env python3
"""Keep non-cancellable inner work owned through outer task cancellation."""
from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable
from typing import TypeVar


_Result = TypeVar("_Result")


def _finite_timeout(value: float) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("timeout must be a finite positive number") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("timeout must be a finite positive number")
    return seconds


async def await_owned_coroutine(awaitable: Awaitable[_Result]) -> _Result:
    """Await one inner operation without letting outer cancellation orphan it.

    Some legacy operations ultimately run native work in an executor thread.
    Cancelling their asyncio Future does not stop that thread. This helper keeps
    the inner Task/Future shielded through repeated ``cancel()`` calls, waits for
    its real completion, and only then propagates the caller's cancellation.
    """
    task = asyncio.ensure_future(awaitable)
    cancellation: asyncio.CancelledError | None = None

    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError as exc:
            if task.cancelled():
                # The inner operation cancelled itself; preserve that contract.
                raise
            cancellation = exc
            if task.done():
                try:
                    result = task.result()
                except BaseException as inner_exc:
                    raise cancellation from inner_exc
                break
        except BaseException as inner_exc:
            if cancellation is not None:
                raise cancellation from inner_exc
            raise

    if cancellation is not None:
        raise cancellation
    return result


async def await_owned_with_soft_timeout(
    awaitable: Awaitable[_Result],
    timeout: float,
) -> tuple[_Result, bool]:
    """Apply a deadline without orphaning work that cannot be force-stopped.

    Returns ``(result, deadline_exceeded)``. When the deadline expires, the
    operation remains owned and is awaited to its real completion. A late
    successful result is preserved instead of launching a duplicate fallback.
    Outer cancellation is likewise propagated only after the inner work ends.
    """
    deadline = _finite_timeout(timeout)
    task = asyncio.ensure_future(awaitable)
    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=deadline)
        return result, False
    except asyncio.TimeoutError:
        result = await await_owned_coroutine(task)
        return result, True
    except asyncio.CancelledError as cancellation:
        if task.cancelled():
            raise
        try:
            await await_owned_coroutine(task)
        except BaseException as inner_exc:
            raise cancellation from inner_exc
        raise cancellation


__all__ = ["await_owned_coroutine", "await_owned_with_soft_timeout"]
