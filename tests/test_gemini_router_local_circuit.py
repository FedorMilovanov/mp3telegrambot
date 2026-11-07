from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

import pytest

from services import gemini_capacity_control as control
from services import gemini_router


class RecordedBudget(control.GeminiRetryBudget):
    last: "RecordedBudget | None" = None

    def __init__(self) -> None:
        super().__init__(limit=3)
        type(self).last = self


def test_preexisting_local_circuit_does_not_rotate_or_consume_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients = [SimpleNamespace(name="one"), SimpleNamespace(name="two")]
    provider_calls: list[str] = []
    overload_notes: list[float] = []
    monkeypatch.setattr(gemini_router, "_current_client_idx", 0)
    monkeypatch.setattr(control, "GeminiRetryBudget", RecordedBudget)
    monkeypatch.setattr(control, "note_overload", lambda delay: overload_notes.append(delay))
    monkeypatch.setattr(control, "transient_retry_delay", lambda attempt: 0.0)

    async def invoke(client: Any) -> None:
        provider_calls.append(client.name)

    async def scenario() -> None:
        control.trip_overload_circuit(domain="inference", seconds=30)
        with pytest.raises(control.GeminiCapacityCircuitOpen):
            await gemini_router.gemini_generate(clients, invoke)

    asyncio.run(scenario())
    assert provider_calls == []
    assert overload_notes == []
    assert RecordedBudget.last is not None
    assert RecordedBudget.last.used == 0
    assert gemini_router._current_client_idx == 0


def test_circuit_race_after_claim_refunds_without_provider_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients = [SimpleNamespace(name="one"), SimpleNamespace(name="two")]
    provider_calls: list[str] = []
    overload_notes: list[float] = []
    monkeypatch.setattr(gemini_router, "_current_client_idx", 0)
    monkeypatch.setattr(control, "GeminiRetryBudget", RecordedBudget)
    monkeypatch.setattr(control, "note_overload", lambda delay: overload_notes.append(delay))
    monkeypatch.setattr(control, "transient_retry_delay", lambda attempt: 0.0)

    async def invoke(client: Any) -> None:
        provider_calls.append(client.name)

    async def locally_rejected(call: Callable[[], Awaitable[Any]]) -> None:
        raise control.GeminiCapacityCircuitOpen("local circuit 503", retry_after_seconds=8)

    monkeypatch.setattr(control, "run_heavy_gemini_call", locally_rejected)

    async def scenario() -> None:
        with pytest.raises(control.GeminiCapacityCircuitOpen):
            await gemini_router.gemini_generate(clients, invoke)

    asyncio.run(scenario())
    assert provider_calls == []
    assert overload_notes == []
    assert RecordedBudget.last is not None
    assert RecordedBudget.last.used == 0
    assert gemini_router._current_client_idx == 0
