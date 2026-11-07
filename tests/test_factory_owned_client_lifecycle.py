from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

import pytest

from services import shorts_factory_capacity as capacity
from services import shorts_factory_capacity_runtime as runtime


class OwnedClient:
    def __init__(self, name: str) -> None:
        self.name = name
        self.aio_closed = 0
        self.sync_closed = 0
        self.aio = SimpleNamespace(aclose=self.aclose)

    async def aclose(self) -> None:
        self.aio_closed += 1

    def close(self) -> None:
        self.sync_closed += 1


def _setup_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    clients: list[OwnedClient],
    pass_fn: Callable[..., Awaitable[dict[str, Any]]],
) -> Path:
    import services.shorts_factory_candidates as candidates
    import services.shorts_factory_quality_gate as quality_gate
    import services.shorts_factory_source as source

    audio = tmp_path / "sample.aac"
    audio.write_bytes(b"x" * 2048)

    async def verified_duration(path: Path) -> float:
        return 120.0

    monkeypatch.setattr(capacity, "factory_gemini_clients", lambda: clients)
    monkeypatch.setattr(capacity, "factory_gemini_quota_domains", lambda: [""] * len(clients))
    monkeypatch.setattr(candidates, "shorts_factory_model", lambda: "gemini-3.8-flash")
    monkeypatch.setattr(candidates, "_run_pass", pass_fn)
    monkeypatch.setattr(candidates, "_scout_prompt", lambda *args: "scout")
    monkeypatch.setattr(candidates, "_judge_prompt", lambda *args: "judge")
    monkeypatch.setattr(candidates, "_boundary_prompt", lambda *args: "boundary")
    monkeypatch.setattr(
        candidates,
        "types",
        SimpleNamespace(
            Part=SimpleNamespace(from_bytes=lambda **kw: SimpleNamespace(**kw)),
            UploadFileConfig=lambda **kw: SimpleNamespace(**kw),
        ),
    )
    monkeypatch.setattr(
        candidates,
        "validate_factory_plan",
        lambda *args, **kw: {"shorts_candidates": [{"start": 1.0, "end": 50.0}], "long_candidates": []},
    )
    monkeypatch.setattr(quality_gate, "apply_factory_quality_gate", lambda plan: plan)
    monkeypatch.setattr(quality_gate, "validated_factory_plan_language", lambda plan: "ru")
    monkeypatch.setattr(source, "measure_factory_audio_duration", verified_duration)
    monkeypatch.setattr(source, "factory_audio_mime_type", lambda path: "audio/aac")
    monkeypatch.setattr(source, "factory_duration_matches", lambda *args: True)
    return audio


def _assert_closed(clients: list[OwnedClient]) -> None:
    assert [client.aio_closed for client in clients] == [1] * len(clients)
    assert [client.sync_closed for client in clients] == [1] * len(clients)


def test_factory_closes_all_owned_clients_on_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clients = [OwnedClient("one"), OwnedClient("unused")]

    async def succeed(client: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True}

    audio = _setup_factory(monkeypatch, tmp_path, clients, succeed)
    plan = asyncio.run(runtime.create_factory_plan_resumable(audio, title="T", performer="P", duration=120))
    assert plan["review_passes"] == 3
    _assert_closed(clients)


def test_factory_closes_all_owned_clients_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clients = [OwnedClient("one"), OwnedClient("two")]

    async def fail(client: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValueError("bad plan")

    audio = _setup_factory(monkeypatch, tmp_path, clients, fail)
    with pytest.raises(RuntimeError, match="All attempted Gemini clients failed"):
        asyncio.run(runtime.create_factory_plan_resumable(audio, title="T", performer="P", duration=120))
    _assert_closed(clients)


def test_factory_closes_clients_after_cancelling_inflight_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clients = [OwnedClient("one"), OwnedClient("unused")]
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def run_pass(client: Any, **kwargs: Any) -> dict[str, Any]:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        raise AssertionError("unreachable")

    audio = _setup_factory(monkeypatch, tmp_path, clients, run_pass)

    async def scenario() -> None:
        owner = asyncio.create_task(
            runtime.create_factory_plan_resumable(audio, title="T", performer="P", duration=120)
        )
        await asyncio.wait_for(started.wait(), 1.0)
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert cancelled.is_set()
        _assert_closed(clients)

    asyncio.run(scenario())


def test_one_close_failure_does_not_leak_other_clients(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class BrokenCloseClient(OwnedClient):
        async def aclose(self) -> None:
            self.aio_closed += 1
            raise RuntimeError("simulated async close failure")

        def close(self) -> None:
            self.sync_closed += 1
            raise RuntimeError("simulated sync close failure")

    clients = [BrokenCloseClient("broken"), OwnedClient("healthy")]

    async def succeed(client: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True}

    audio = _setup_factory(monkeypatch, tmp_path, clients, succeed)
    plan = asyncio.run(runtime.create_factory_plan_resumable(audio, title="T", performer="P", duration=120))
    assert plan["review_passes"] == 3
    _assert_closed(clients)
