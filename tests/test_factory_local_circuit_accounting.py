from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from services import gemini_capacity_control as control
from services import shorts_factory_capacity_runtime as runtime


def test_local_circuit_is_not_provider_overload() -> None:
    error = control.GeminiCapacityCircuitOpen(
        "Gemini inference 503 overload circuit open; retry after about 10s",
        retry_after_seconds=10.0,
    )
    assert runtime.factory_client_retry_action(error) == "circuit"
    assert error.retry_after_seconds == 10.0


def test_inference_waits_out_local_circuit_without_consuming_network_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.shorts_factory_candidates as candidates

    actual_calls: list[Any] = []

    async def run_pass(client: Any, **kwargs: Any) -> dict[str, Any]:
        actual_calls.append(client)
        return {"ok": True}

    monkeypatch.setattr(candidates, "_run_pass", run_pass)
    budget = control.GeminiRetryBudget(limit=5)
    client = SimpleNamespace(name="client")

    async def scenario() -> Any:
        control.trip_overload_circuit(domain="inference", seconds=0.04)
        return await runtime._run_pass_with_capacity_retry(
            client,
            model="gemini-3.8-flash",
            audio_part=object(),
            prompt="prompt",
            max_tokens=100,
            label="Factory HIGH pass",
            retry_budget=budget,
        )

    assert asyncio.run(scenario()) == {"ok": True}
    assert budget.used == 1
    assert actual_calls == [client]


def test_local_circuit_timeout_has_zero_real_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.shorts_factory_candidates as candidates

    actual_calls: list[object] = []

    async def run_pass(client: Any, **kwargs: Any) -> dict[str, Any]:
        actual_calls.append(client)
        return {}

    monkeypatch.setattr(candidates, "_run_pass", run_pass)
    monkeypatch.setattr(runtime, "_FACTORY_LOCAL_CIRCUIT_MAX_WAIT_SECONDS", 0.01)
    budget = control.GeminiRetryBudget(limit=5)

    async def scenario() -> None:
        control.trip_overload_circuit(domain="inference", seconds=1.0)
        with pytest.raises(control.GeminiCapacityCircuitOpen):
            await runtime._run_pass_with_capacity_retry(
                SimpleNamespace(name="client"),
                model="gemini-3.8-flash",
                audio_part=object(),
                prompt="prompt",
                max_tokens=100,
                label="Factory HIGH pass",
                retry_budget=budget,
            )

    asyncio.run(scenario())
    assert budget.used == 0
    assert actual_calls == []


def test_circuit_race_after_claim_refunds_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.shorts_factory_candidates as candidates

    budget = control.GeminiRetryBudget(limit=5)
    calls: list[str] = []

    async def run_pass(client: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append("provider")
        return {"ok": True}

    monkeypatch.setattr(candidates, "_run_pass", run_pass)
    real_gate = control.require_domain_available
    checks = 0

    def raced_gate(domain: str = "inference") -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise control.GeminiCapacityCircuitOpen(
                "Local inference circuit open",
                retry_after_seconds=0.01,
            )
        real_gate(domain)

    monkeypatch.setattr(control, "require_domain_available", raced_gate)

    async def scenario() -> Any:
        return await runtime._run_pass_with_capacity_retry(
            SimpleNamespace(name="client"),
            model="gemini-3.8-flash",
            audio_part=object(),
            prompt="prompt",
            max_tokens=100,
            label="Factory HIGH pass",
            retry_budget=budget,
        )

    assert asyncio.run(scenario()) == {"ok": True}
    assert checks >= 4
    assert calls == ["provider"]
    assert budget.used == 1


def test_files_circuit_does_not_upload_or_rotate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    import services.shorts_factory_candidates as candidates
    import services.shorts_factory_capacity as capacity
    import services.shorts_factory_source as source

    audio = tmp_path / "factory.aac"
    with audio.open("wb") as output:
        output.truncate(19 * 1024 * 1024)

    class Files:
        def __init__(self) -> None:
            self.upload_calls = 0

        async def upload(self, **kwargs: Any) -> Any:
            self.upload_calls += 1
            raise AssertionError("Files API must not be called while local circuit is open")

    first_files = Files()
    second_files = Files()
    first = SimpleNamespace(aio=SimpleNamespace(files=first_files))
    second = SimpleNamespace(aio=SimpleNamespace(files=second_files))
    monkeypatch.setattr(capacity, "factory_gemini_clients", lambda: [first, second])
    monkeypatch.setattr(
        candidates,
        "types",
        SimpleNamespace(
            UploadFileConfig=lambda **kwargs: SimpleNamespace(**kwargs),
            Part=SimpleNamespace(from_bytes=lambda **kwargs: SimpleNamespace(**kwargs)),
        ),
    )

    async def verified_duration(_: Any) -> float:
        return 120.0

    monkeypatch.setattr(source, "measure_factory_audio_duration", verified_duration)
    monkeypatch.setattr(source, "factory_audio_mime_type", lambda _: "audio/aac")
    monkeypatch.setattr(source, "factory_duration_matches", lambda *args: True)

    async def scenario() -> None:
        control.trip_overload_circuit(domain="files", seconds=30.0)
        with pytest.raises(control.GeminiCapacityCircuitOpen):
            await runtime.create_factory_plan_resumable(
                audio,
                title="Title",
                performer="Author",
                duration=120,
            )

    asyncio.run(scenario())
    assert first_files.upload_calls == 0
    assert second_files.upload_calls == 0
