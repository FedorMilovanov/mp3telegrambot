#!/usr/bin/env python3
"""Shared in-process capacity control for expensive Gemini requests.

The application owns three local guarantees:

* heavy work is smoothed through one process-wide semaphore;
* Files API and model inference keep independent cooldown/circuit state;
* one transient event gets at most initial + two retries, regardless of key count.

An inference circuit also blocks preparatory Files uploads while inference is
known unavailable. The reverse is intentionally false: a Files outage must not
block text-only GenerateContent, because the two Google surfaces can fail
independently.
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
_DEFAULT_OVERLOAD_CIRCUIT_SECONDS = 120.0
_VALID_DOMAINS = {"inference", "files"}


class GeminiCapacityCircuitOpen(RuntimeError):
    """Raised before network I/O while a known-overloaded domain is open."""


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


def _domain(value: str) -> str:
    domain = str(value or "inference").strip().lower() or "inference"
    if domain not in _VALID_DOMAINS:
        raise ValueError(f"Unsupported Gemini capacity domain: {domain!r}")
    return domain


def _blocking_domains(domain: str) -> tuple[str, ...]:
    domain = _domain(domain)
    return ("files", "inference") if domain == "files" else ("inference",)


def heavy_concurrency() -> int:
    return _env_int(
        "GEMINI_HEAVY_MAX_CONCURRENCY",
        _DEFAULT_HEAVY_CONCURRENCY,
        1,
        _MAX_HEAVY_CONCURRENCY,
    )


def transient_attempt_limit() -> int:
    return _env_int(
        "GEMINI_TRANSIENT_MAX_ATTEMPTS",
        _DEFAULT_TRANSIENT_ATTEMPTS,
        1,
        _MAX_TRANSIENT_ATTEMPTS,
    )


def _retry_max_seconds() -> float:
    base = _env_float(
        "GEMINI_RETRY_BASE_SECONDS",
        _DEFAULT_RETRY_BASE_SECONDS,
        1.0,
        120.0,
    )
    return _env_float(
        "GEMINI_RETRY_MAX_SECONDS",
        _DEFAULT_RETRY_MAX_SECONDS,
        base,
        300.0,
    )


def overload_circuit_seconds() -> float:
    return _env_float(
        "GEMINI_OVERLOAD_CIRCUIT_SECONDS",
        _DEFAULT_OVERLOAD_CIRCUIT_SECONDS,
        30.0,
        600.0,
    )


def transient_retry_delay(failure_number: int) -> float:
    base = _env_float(
        "GEMINI_RETRY_BASE_SECONDS",
        _DEFAULT_RETRY_BASE_SECONDS,
        1.0,
        120.0,
    )
    maximum = _retry_max_seconds()
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
    cooldown_until: dict[str, float] = field(default_factory=dict)
    circuit_until: dict[str, float] = field(default_factory=dict)


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


def _circuit_remaining(state: _LoopState, domain: str) -> float:
    loop = asyncio.get_running_loop()
    return max(
        0.0,
        *(
            state.circuit_until.get(item, 0.0) - loop.time()
            for item in _blocking_domains(domain)
        ),
    )


def domain_circuit_open(domain: str = "inference") -> bool:
    try:
        return _circuit_remaining(_loop_state(), domain) > 0
    except RuntimeError:
        return False


def require_domain_available(domain: str = "inference") -> None:
    state = _loop_state()
    remaining = _circuit_remaining(state, domain)
    if remaining > 0:
        raise GeminiCapacityCircuitOpen(
            f"Gemini {_domain(domain)} 503 overload circuit open; "
            f"retry after about {remaining:.1f}s"
        )


async def _respect_capacity_state(state: _LoopState, domain: str) -> None:
    require_domain_available(domain)
    loop = asyncio.get_running_loop()
    delay = max(
        0.0,
        *(
            state.cooldown_until.get(item, 0.0) - loop.time()
            for item in _blocking_domains(domain)
        ),
    )
    if delay > 0:
        await asyncio.sleep(delay)
    require_domain_available(domain)


@asynccontextmanager
async def heavy_gemini_slot(domain: str = "inference"):
    domain = _domain(domain)
    state = _loop_state()
    require_domain_available(domain)
    await state.semaphore.acquire()
    try:
        await _respect_capacity_state(state, domain)
        yield
    finally:
        state.semaphore.release()


async def run_heavy_gemini_call(
    call: Callable[[], Awaitable[_T]],
    *,
    domain: str = "inference",
) -> _T:
    async with heavy_gemini_slot(domain=domain):
        return await call()


def trip_overload_circuit(
    *,
    domain: str = "inference",
    seconds: float | None = None,
) -> None:
    try:
        state = _loop_state()
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    domain = _domain(domain)
    hold = overload_circuit_seconds() if seconds is None else max(0.0, float(seconds))
    state.circuit_until[domain] = max(
        state.circuit_until.get(domain, 0.0),
        loop.time() + hold,
    )
    state.cooldown_until[domain] = max(
        state.cooldown_until.get(domain, 0.0),
        loop.time() + hold,
    )


def note_overload(delay_seconds: float, *, domain: str = "inference") -> None:
    """Publish cooldown and open a circuit once backoff reaches its ceiling."""
    try:
        state = _loop_state()
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    domain = _domain(domain)
    delay = max(0.0, float(delay_seconds or 0.0))
    state.cooldown_until[domain] = max(
        state.cooldown_until.get(domain, 0.0),
        loop.time() + delay,
    )
    # With the default 15/30/60 schedule, the third overload reaches the retry
    # ceiling. Opening the circuit there prevents segmented/parallel workflows
    # from treating the exhausted event as a fresh request storm.
    if delay >= _retry_max_seconds():
        trip_overload_circuit(domain=domain)


__all__ = [
    "GeminiCapacityCircuitOpen",
    "GeminiRetryBudget",
    "domain_circuit_open",
    "heavy_concurrency",
    "heavy_gemini_slot",
    "note_overload",
    "overload_circuit_seconds",
    "require_domain_available",
    "run_heavy_gemini_call",
    "transient_attempt_limit",
    "transient_retry_delay",
    "trip_overload_circuit",
]
