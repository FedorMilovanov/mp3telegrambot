from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services import gemini_capacity_control as capacity_control
from services import shorts_factory_capacity as capacity
from services import shorts_factory_capacity_runtime as capacity_runtime


class _ServiceError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def _install_fake_factory_modules(monkeypatch, run_pass):
    import services.shorts_factory_candidates as candidates
    import services.shorts_factory_quality_gate as quality_gate
    import services.shorts_factory_source as source

    async def verified_duration(_path):
        return 120.0

    monkeypatch.setattr(
        candidates,
        "types",
        SimpleNamespace(
            Part=SimpleNamespace(
                from_bytes=lambda *, data, mime_type: SimpleNamespace(
                    data=data, mime_type=mime_type
                )
            ),
            UploadFileConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        ),
    )
    monkeypatch.setattr(candidates, "shorts_factory_model", lambda: "gemini-3.8-flash")
    monkeypatch.setattr(candidates, "_run_pass", run_pass)
    monkeypatch.setattr(candidates, "_scout_prompt", lambda *args: "scout")
    monkeypatch.setattr(candidates, "_judge_prompt", lambda *args: "judge")
    monkeypatch.setattr(candidates, "_boundary_prompt", lambda *args: "boundary")
    monkeypatch.setattr(
        candidates,
        "validate_factory_plan",
        lambda *args, **kwargs: {
            "shorts_candidates": [{"start": 1.0, "end": 10.0}],
            "long_candidates": [],
        },
    )
    monkeypatch.setattr(quality_gate, "apply_factory_quality_gate", lambda plan: plan)
    monkeypatch.setattr(quality_gate, "validated_factory_plan_language", lambda plan: "ru")
    monkeypatch.setattr(source, "factory_audio_mime_type", lambda path: "audio/flac")
    monkeypatch.setattr(source, "measure_factory_audio_duration", verified_duration)
    monkeypatch.setattr(
        source,
        "factory_duration_matches",
        lambda actual, expected: abs(float(actual) - float(expected)) <= 2.0,
    )


def test_quota_claim_refund_preserves_existing_capacity_usage() -> None:
    budget = capacity_control.GeminiRetryBudget(limit=5)
    for _ in range(5):
        budget.claim()
    assert budget.exhausted is True

    budget.refund_last_claim()
    assert budget.used == 4
    assert budget.remaining == 1
    assert budget.exhausted is False

    budget.claim()
    assert budget.used == 5
    assert budget.exhausted is True


def test_retry_action_separates_capacity_quota_and_other_transients() -> None:
    assert capacity_runtime.factory_client_retry_action(
        _ServiceError(503, "UNAVAILABLE: high demand")
    ) == "capacity"
    assert capacity_runtime.factory_client_retry_action(
        _ServiceError(429, "RESOURCE_EXHAUSTED")
    ) == "quota"
    assert capacity_runtime.factory_client_retry_action(
        _ServiceError(500, "INTERNAL")
    ) == "rotate"


def test_503_x4_then_429_rotates_even_when_stage_budget_is_exhausted(monkeypatch, tmp_path):
    """Regression for the live failure that stopped at client 1/4.

    Backend overload consumes four calls, the fifth changes failure domain to
    quota exhaustion, and the next configured client must still be attempted.
    Completed quality passes must not be downgraded or skipped.
    """
    audio = tmp_path / "factory.flac"
    audio.write_bytes(b"x" * 2048)
    first = SimpleNamespace(name="first")
    second = SimpleNamespace(name="second")
    calls: list[str] = []
    first_calls = 0

    async def run_pass(client, **kwargs):
        nonlocal first_calls
        calls.append(client.name)
        if client is first:
            first_calls += 1
            if first_calls <= 4:
                raise _ServiceError(503, "UNAVAILABLE: high demand")
            raise _ServiceError(429, "RESOURCE_EXHAUSTED")
        return {"ok": True, "pass": len(calls)}

    _install_fake_factory_modules(monkeypatch, run_pass)
    monkeypatch.setattr(capacity_runtime, "_capacity_retry_delay", lambda attempt: 0.0)
    monkeypatch.setattr(capacity, "factory_gemini_clients", lambda: [first, second])

    plan = asyncio.run(
        capacity_runtime.create_factory_plan_resumable(
            audio,
            title="Title",
            performer="Author",
            duration=120,
        )
    )

    assert calls[:6] == ["first", "first", "first", "first", "first", "second"]
    assert calls == [
        "first",
        "first",
        "first",
        "first",
        "first",
        "second",
        "second",
        "second",
    ]
    assert plan["model"] == "gemini-3.8-flash"
    assert plan["thinking_level"] == "high"
    assert plan["review_passes"] == 3
    assert plan["strict_quality"] is True


def test_late_429_allows_only_one_remaining_capacity_probe(monkeypatch, tmp_path):
    """Quota failover must not create a fresh five-call 503 window."""
    audio = tmp_path / "factory.flac"
    audio.write_bytes(b"x" * 2048)
    first = SimpleNamespace(name="first")
    second = SimpleNamespace(name="second")
    third = SimpleNamespace(name="third")
    calls: list[str] = []
    first_calls = 0

    async def run_pass(client, **kwargs):
        nonlocal first_calls
        calls.append(client.name)
        if client is first:
            first_calls += 1
            if first_calls <= 4:
                raise _ServiceError(503, "UNAVAILABLE: high demand")
            raise _ServiceError(429, "RESOURCE_EXHAUSTED")
        if client is second:
            raise _ServiceError(503, "UNAVAILABLE: high demand")
        return {"ok": True}

    _install_fake_factory_modules(monkeypatch, run_pass)
    monkeypatch.setattr(capacity_runtime, "_capacity_retry_delay", lambda attempt: 0.0)
    monkeypatch.setattr(
        capacity,
        "factory_gemini_clients",
        lambda: [first, second, third],
    )

    with pytest.raises(RuntimeError, match="503/high demand") as raised:
        asyncio.run(
            capacity_runtime.create_factory_plan_resumable(
                audio,
                title="Title",
                performer="Author",
                duration=120,
            )
        )

    # Four backend 503s on client 1 + one quota response + exactly one final
    # backend-capacity probe on client 2. Client 3 must never receive a new
    # capacity window.
    assert calls == [
        "first",
        "first",
        "first",
        "first",
        "first",
        "second",
    ]
    assert "Проверено клиентов: 2/3" in str(raised.value)
    assert "third" not in calls


def test_persistent_503_still_does_not_multiply_across_clients(monkeypatch, tmp_path):
    audio = tmp_path / "factory.flac"
    audio.write_bytes(b"x" * 2048)
    first = SimpleNamespace(name="first")
    second = SimpleNamespace(name="second")
    calls: list[str] = []

    async def run_pass(client, **kwargs):
        calls.append(client.name)
        raise _ServiceError(503, "UNAVAILABLE: high demand")

    _install_fake_factory_modules(monkeypatch, run_pass)
    monkeypatch.setattr(capacity_runtime, "_capacity_retry_delay", lambda attempt: 0.0)
    monkeypatch.setattr(capacity, "factory_gemini_clients", lambda: [first, second])

    try:
        asyncio.run(
            capacity_runtime.create_factory_plan_resumable(
                audio,
                title="Title",
                performer="Author",
                duration=120,
            )
        )
    except RuntimeError as exc:
        assert "503/high demand" in str(exc)
    else:
        raise AssertionError("persistent 503 must fail closed")

    assert calls == ["first", "first", "first", "first", "first"]
