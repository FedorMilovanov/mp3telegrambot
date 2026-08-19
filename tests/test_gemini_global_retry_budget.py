from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core import globals as core_globals
from services import gemini_capacity_control as control


def test_generic_503_is_capped_at_three_calls_across_four_clients(monkeypatch):
    clients = [SimpleNamespace(name=f"c{i}") for i in range(1, 5)]
    calls: list[str] = []

    async def invoke(client):
        calls.append(client.name)
        raise RuntimeError("503 UNAVAILABLE: high demand")

    monkeypatch.setenv("GEMINI_TRANSIENT_MAX_ATTEMPTS", "3")
    monkeypatch.setattr(control, "transient_retry_delay", lambda _attempt: 0.0)
    monkeypatch.setattr(control, "note_overload", lambda _delay: None)
    monkeypatch.setattr(core_globals, "_current_client_idx", 0)

    with pytest.raises(RuntimeError, match="503"):
        asyncio.run(core_globals.gemini_generate(clients, invoke, "gemini-3.7-flash"))

    assert calls == ["c1", "c1", "c2"]


def test_generic_429_rotates_but_cannot_expand_past_global_budget(monkeypatch):
    clients = [SimpleNamespace(name=f"c{i}") for i in range(1, 5)]
    calls: list[str] = []

    async def invoke(client):
        calls.append(client.name)
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setenv("GEMINI_TRANSIENT_MAX_ATTEMPTS", "3")
    monkeypatch.setattr(core_globals, "_current_client_idx", 0)

    with pytest.raises(RuntimeError, match="429"):
        asyncio.run(core_globals.gemini_generate(clients, invoke, ""))

    assert calls == ["c1", "c2", "c3"]


def test_generic_clients_explicitly_disable_sdk_retry_stack():
    options = core_globals._gemini_http_options
    if options is None or not hasattr(options, "retry_options"):
        pytest.skip("installed google-genai does not expose retry_options")
    assert options.retry_options.attempts == 1
