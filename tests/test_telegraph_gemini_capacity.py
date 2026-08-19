from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core import globals as core_globals
from services import gemini_capacity_control as control
from services import telegraph_pages as telegraph


class _FakeClient:
    def __init__(self, name: str, responder):
        self.name = name
        self.calls = 0

        class _Models:
            async def generate_content(_self, **kwargs):
                del kwargs
                self.calls += 1
                return await responder(self.calls)

        self.aio = SimpleNamespace(models=_Models())


async def _noop_observe(**_kwargs):
    return None


def _prepare(monkeypatch, clients):
    monkeypatch.setenv("GEMINI_TRANSIENT_MAX_ATTEMPTS", "3")
    monkeypatch.setattr(control, "transient_retry_delay", lambda _attempt: 0.0)
    monkeypatch.setattr(control, "note_overload", lambda _delay: None)
    monkeypatch.setattr(core_globals, "_current_client_idx", 0)
    monkeypatch.setattr(telegraph, "GEMINI_CLIENTS", clients)
    monkeypatch.setattr(telegraph, "make_text_config_smart", lambda **kwargs: kwargs)
    monkeypatch.setattr(telegraph, "alog_gemini_response", _noop_observe)
    monkeypatch.setattr(telegraph, "alog_gemini_run", _noop_observe)


def test_telegraph_503_never_expands_past_three_network_calls(monkeypatch):
    async def fail_503(_count):
        raise RuntimeError("503 UNAVAILABLE: high demand")

    clients = [_FakeClient(f"c{i}", fail_503) for i in range(1, 5)]
    _prepare(monkeypatch, clients)

    result = asyncio.run(
        telegraph._gemini_text_request(
            "prompt",
            response_mime_type="application/json",
            response_schema={"type": "object"},
        )
    )

    assert result is None
    assert sum(client.calls for client in clients) == 3


def test_telegraph_timeout_never_triggers_schema_fallback_or_storm(monkeypatch):
    async def fail_timeout(_count):
        raise asyncio.TimeoutError("request timed out")

    clients = [_FakeClient(f"c{i}", fail_timeout) for i in range(1, 5)]
    _prepare(monkeypatch, clients)

    result = asyncio.run(
        telegraph._gemini_text_request(
            "prompt",
            response_mime_type="application/json",
            response_schema={"type": "object"},
        )
    )

    assert result is None
    assert sum(client.calls for client in clients) == 3


def test_telegraph_schema_compatibility_gets_one_schema_less_retry(monkeypatch):
    async def schema_then_ok(count):
        if count == 1:
            raise RuntimeError("response_schema unsupported")
        return SimpleNamespace(
            text='{"ok": true}',
            candidates=[],
            usage_metadata=None,
        )

    client = _FakeClient("c1", schema_then_ok)
    _prepare(monkeypatch, [client])

    result = asyncio.run(
        telegraph._gemini_text_request(
            "prompt",
            response_mime_type="application/json",
            response_schema={"type": "object"},
        )
    )

    assert result == '{"ok": true}'
    assert client.calls == 2


def test_telegraph_has_no_model_global_quota_blacklist():
    source = open("services/telegraph_pages.py", encoding="utf-8").read()
    assert "mark_model_exhausted" not in source
    assert "is_model_exhausted" not in source
