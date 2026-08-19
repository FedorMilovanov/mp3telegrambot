from __future__ import annotations

import asyncio

import pytest

from services import gemini_capacity_control as control


def test_transient_attempt_limit_is_hard_capped_at_three(monkeypatch):
    monkeypatch.delenv("GEMINI_TRANSIENT_MAX_ATTEMPTS", raising=False)
    assert control.transient_attempt_limit() == 3
    monkeypatch.setenv("GEMINI_TRANSIENT_MAX_ATTEMPTS", "99")
    assert control.transient_attempt_limit() == 3
    monkeypatch.setenv("GEMINI_TRANSIENT_MAX_ATTEMPTS", "1")
    assert control.transient_attempt_limit() == 1


def test_retry_budget_is_shared_and_fail_closed(monkeypatch):
    monkeypatch.setenv("GEMINI_TRANSIENT_MAX_ATTEMPTS", "3")
    budget = control.GeminiRetryBudget()
    assert [budget.claim(), budget.claim(), budget.claim()] == [1, 2, 3]
    assert budget.exhausted is True
    assert budget.remaining == 0
    with pytest.raises(RuntimeError, match="budget exhausted"):
        budget.claim()


def test_retry_delay_is_exponential_jittered_and_capped(monkeypatch):
    monkeypatch.delenv("GEMINI_RETRY_BASE_SECONDS", raising=False)
    monkeypatch.delenv("GEMINI_RETRY_MAX_SECONDS", raising=False)
    monkeypatch.delenv("GEMINI_RETRY_JITTER_SECONDS", raising=False)
    monkeypatch.setattr(control.random, "uniform", lambda _a, _b: 0.0)
    assert control.transient_retry_delay(1) == 15.0
    assert control.transient_retry_delay(2) == 30.0
    assert control.transient_retry_delay(3) == 60.0
    assert control.transient_retry_delay(8) == 60.0


def test_heavy_concurrency_defaults_to_one_and_is_bounded(monkeypatch):
    monkeypatch.delenv("GEMINI_HEAVY_MAX_CONCURRENCY", raising=False)
    assert control.heavy_concurrency() == 1
    monkeypatch.setenv("GEMINI_HEAVY_MAX_CONCURRENCY", "99")
    assert control.heavy_concurrency() == 4


def test_heavy_gate_serializes_expensive_calls_by_default(monkeypatch):
    monkeypatch.setenv("GEMINI_HEAVY_MAX_CONCURRENCY", "1")

    async def scenario():
        order: list[str] = []
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        async def first():
            async with control.heavy_gemini_slot():
                order.append("first-enter")
                first_entered.set()
                await release_first.wait()
                order.append("first-exit")

        async def second():
            await first_entered.wait()
            async with control.heavy_gemini_slot():
                order.append("second-enter")

        t1 = asyncio.create_task(first())
        t2 = asyncio.create_task(second())
        await first_entered.wait()
        await asyncio.sleep(0)
        assert order == ["first-enter"]
        release_first.set()
        await asyncio.gather(t1, t2)
        return order

    assert asyncio.run(scenario()) == ["first-enter", "first-exit", "second-enter"]


def test_overload_cooldown_delays_following_heavy_call(monkeypatch):
    monkeypatch.setenv("GEMINI_HEAVY_MAX_CONCURRENCY", "1")

    async def scenario():
        loop = asyncio.get_running_loop()
        started = loop.time()
        control.note_overload(0.03)
        await control.run_heavy_gemini_call(lambda: asyncio.sleep(0))
        return loop.time() - started

    elapsed = asyncio.run(scenario())
    assert elapsed >= 0.02
