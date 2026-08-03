#!/usr/bin/env python3
"""Keep non-cancellable inner work owned through outer task cancellation."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar


_Result = TypeVar("_Result")


async def await_owned_coroutine(awaitable: Awaitable[_Result]) -> _Result:
    """Await one inner coroutine without letting outer cancellation orphan it.

    Some legacy operations ultimately run native work in an executor thread.
    Cancelling their asyncio Future does not stop that thread. This helper keeps
    the inner Task shielded through repeated ``cancel()`` calls, waits for its
    real completion, and only then propagates the caller's cancellation.
    """
    task = asyncio.create_task(awaitable)
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


__all__ = ["await_owned_coroutine"]
