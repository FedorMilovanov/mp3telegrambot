from __future__ import annotations

import asyncio
from types import SimpleNamespace

from services import gemini_capacity_control as control
from services import livedub_publication_core as publication


def test_publication_respects_open_inference_circuit(monkeypatch):
    import core.globals as core_globals

    calls: list[str] = []

    class Models:
        async def generate_content(self, **_kwargs):
            calls.append("network")
            return SimpleNamespace(text='{"title":"Тема","author":"","description":"Описание"}')

    client = SimpleNamespace(aio=SimpleNamespace(models=Models()))
    monkeypatch.setattr(core_globals, "GEMINI_CLIENTS", [client])

    def circuit_open(_domain="inference"):
        raise control.GeminiCapacityCircuitOpen("open")

    monkeypatch.setattr(control, "require_domain_available", circuit_open)

    assert asyncio.run(publication._generate_quality("Topic - Author")) is None
    assert calls == []


def test_publication_attempts_are_capped_at_three_even_if_env_is_higher(monkeypatch):
    import core.globals as core_globals

    calls: list[str] = []

    class Models:
        def __init__(self, name: str) -> None:
            self.name = name

        async def generate_content(self, **_kwargs):
            calls.append(self.name)
            raise RuntimeError("503 UNAVAILABLE: high demand")

    clients = [
        SimpleNamespace(aio=SimpleNamespace(models=Models(f"c{i}")))
        for i in range(1, 5)
    ]
    monkeypatch.setattr(core_globals, "GEMINI_CLIENTS", clients)
    monkeypatch.setenv("LIVEDUB_PUBLICATION_MAX_ATTEMPTS", "8")
    monkeypatch.setattr(publication, "_quality_config", lambda _model: object())
    monkeypatch.setattr(control, "require_domain_available", lambda _domain="inference": None)
    monkeypatch.setattr(control, "transient_retry_delay", lambda _attempt: 0.0)
    monkeypatch.setattr(control, "note_overload", lambda *_args, **_kwargs: None)

    async def direct(call, *, domain="inference"):
        assert domain == "inference"
        return await call()

    monkeypatch.setattr(control, "run_heavy_gemini_call", direct)

    assert asyncio.run(publication._generate_quality("Topic - Author")) is None
    assert calls == ["c1", "c2", "c3"]
