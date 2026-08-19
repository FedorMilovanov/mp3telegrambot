#!/usr/bin/env python3
"""Shared in-process capacity control for expensive Gemini requests.

Google documents 429/5xx as transient capacity/service failures and recommends
exponential backoff with jitter while avoiding traffic spikes.  This module is
the single source owner for the local side of that contract:

* heavy Gemini inference is serialized by default (configurable up to four);
* a 503 establishes a process-wide cooldown so another coroutine cannot
  immediately hammer the same backend while the failing request sleeps;
* one transient event gets at most three network attempts total (initial call
  plus two retries), independent of how many API keys are configured.

The gate does not downgrade models or thinking quality.  It only controls when
requests are allowed to reach the service.
"""
from __future__ import annotations

import asyncio
import math
import os
import random
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Awaitable, Callable, TypeVar

_T = TypeVar("_T")

_DEFAULT_HEAVY_CONCURRENCY = 1
_MAX_HEAVY_CONCURRENCY = 4
_DEFAULT_TRANSIENT_ATTEMPTS = 3
_MAX_TRANSIENT_ATTEMPTS = 3
_DEFAULT_RETRY_BASE_SECONDS = 15.0
_DEFAULT_RETRY_MAX_SECONDS = 60.0
_DEFAULT_RETRY_JITTER_SECONDS = 5.0


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, "") or default).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.getenv(name, "") or default).strip())
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, min(value, maximum))


def heavy_concurrency() -> int:
    """Maximum simultaneous expensive Gemini inference calls in this process."""
    return _env_int(
        "GEMINI_HEAVY_MAX_CONCURRENCY",
        _DEFAULT_HEAVY_CONCURRENCY,
        1,
        _MAX_HEAVY_CONCURRENCY,
    )


def transient_attempt_limit() -> int:
    """Initial request + at most two retries, never multiplied by API-key count."""
    return _env_int(
        "GEMINI_TRANSIENT_MAX_ATTEMPTS",
        _DEFAULT_TRANSIENT_ATTEMPTS,
        1,
        _MAX_TRANSIENT_ATTEMPTS,
    )


def transient_retry_delay(failure_number: int) -> float:
    """Exponential backoff with jitter for the Nth transient failure."""
    base = _env_float(
        "GEMINI_RETRY_BASE_SECONDS",
        _DEFAULT_RETRY_BASE_SECONDS,
        1.0,
        120.0,
    )
    maximum = _env_float(
        "GEMINI_RETRY_MAX_SECONDS",
        _DEFAULT_RETRY_MAX_SECONDS,
        base,
        300.0,
    )
    jitter = _env_float(
        "GEMINI_RETRY_JITTER_SECONDS",
        _DEFAULT_RETRY_JITTER_SECONDS,
        0.0,
        30.0,
    )
    delay = min(maximum, base * (2 ** max(0, int(failure_number) - 1)))
    return delay + random.uniform(0.0, jitter)


@dataclass
class GeminiRetryBudget:
    """Request-attempt budget shared across API-key rotation for one operation."""

    limit: int = field(default_factory=transient_attempt_limit)
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def claim(self) -> int:
        if self.exhausted:
            raise RuntimeError(
                "Gemini transient retry budget exhausted before network request"
            )
        self.used += 1
        return self.used


@dataclass
class _LoopState:
    semaphore: asyncio.Semaphore
    cooldown_until: float = 0.0


_LOOP_STATES: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopState] = (
    weakref.WeakKeyDictionary()
)


def _loop_state() -> _LoopState:
    loop = asyncio.get_running_loop()
    state = _LOOP_STATES.get(loop)
    if state is None:
        state = _LoopState(asyncio.Semaphore(heavy_concurrency()))
        _LOOP_STATES[loop] = state
    return state


async def _respect_cooldown(state: _LoopState) -> None:
    loop = asyncio.get_running_loop()
    delay = state.cooldown_until - loop.time()
    if delay > 0:
        await asyncio.sleep(delay)


@asynccontextmanager
async def heavy_gemini_slot():
    """Serialize/smooth expensive Gemini calls without holding a slot while queued."""
    state = _loop_state()
    await state.semaphore.acquire()
    try:
        await _respect_cooldown(state)
        yield
    finally:
        state.semaphore.release()


async def run_heavy_gemini_call(call: Callable[[], Awaitable[_T]]) -> _T:
    """Execute one expensive network call under the shared capacity gate."""
    async with heavy_gemini_slot():
        return await call()


def note_overload(delay_seconds: float) -> None:
    """Publish a cooldown to all heavy calls running on the same event loop."""
    try:
        state = _loop_state()
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    delay = max(0.0, float(delay_seconds or 0.0))
    state.cooldown_until = max(state.cooldown_until, loop.time() + delay)


__all__ = [
    "GeminiRetryBudget",
    "heavy_concurrency",
    "heavy_gemini_slot",
    "note_overload",
    "run_heavy_gemini_call",
    "transient_attempt_limit",
    "transient_retry_delay",
]
