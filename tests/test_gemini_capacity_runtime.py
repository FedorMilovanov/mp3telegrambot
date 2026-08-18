from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import gemini_capacity_runtime as cap


class _FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def generate_content(self, *args, **kwargs):
        self.calls += 1
        if not self.outcomes:
            return SimpleNamespace(text="OK")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _FakeAio:
    def __init__(self, models):
        self.models = models
        self.files = SimpleNamespace()


class _FakeClient:
    def __init__(self, models):
        self.aio = _FakeAio(models)


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    cap.reset_capacity_state()
    monkeypatch.setenv("GEMINI_CAPACITY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("GEMINI_CAPACITY_COOLDOWN_SECONDS", "120")
    monkeypatch.setenv("GEMINI_CAPACITY_PROBE_TIMEOUT_SECONDS", "5")

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(cap.asyncio, "sleep", no_sleep)
    yield
    cap.reset_capacity_state()


def _503():
    return RuntimeError(
        "503 UNAVAILABLE. This model is currently experiencing high demand."
    )


@pytest.mark.asyncio
async def test_retries_same_request_then_succeeds_without_key_rotation():
    models = _FakeModels([_503(), _503(), SimpleNamespace(text="done")])
    client = cap.wrap_gemini_client(_FakeClient(models))

    response = await client.aio.models.generate_content(
        model="gemini-3.7-flash",
        contents="heavy",
    )

    assert response.text == "done"
    assert models.calls == 3


@pytest.mark.asyncio
async def test_repeated_503_plus_healthy_probe_opens_terminal_circuit():
    models = _FakeModels([
        _503(),
        _503(),
        _503(),
        SimpleNamespace(text="OK"),  # tiny probe
    ])
    client = cap.wrap_gemini_client(_FakeClient(models))

    with pytest.raises(cap.GeminiRequestCapacityRejected):
        await client.aio.models.generate_content(
            model="gemini-3.7-flash",
            contents="heavy",
        )

    assert models.calls == 4

    # Outer legacy retry loops must not trigger another network request while the
    # anti-stampede circuit is open.
    with pytest.raises(cap.GeminiCapacityCircuitOpen):
        await client.aio.models.generate_content(
            model="gemini-3.7-flash",
            contents="same heavy request again",
        )
    assert models.calls == 4


@pytest.mark.asyncio
async def test_repeated_503_and_failed_probe_classifies_backend_saturation():
    models = _FakeModels([_503(), _503(), _503(), _503()])
    client = cap.wrap_gemini_client(_FakeClient(models))

    with pytest.raises(cap.GeminiCapacityCircuitOpen):
        await client.aio.models.generate_content(
            model="gemini-3.7-flash",
            contents="heavy",
        )

    assert models.calls == 4


@pytest.mark.asyncio
async def test_429_is_not_swallowed_or_retried_by_capacity_layer():
    err = RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded")
    models = _FakeModels([err])
    client = cap.wrap_gemini_client(_FakeClient(models))

    with pytest.raises(RuntimeError, match="429"):
        await client.aio.models.generate_content(
            model="gemini-3.7-flash",
            contents="request",
        )

    assert models.calls == 1


@pytest.mark.asyncio
async def test_non_capacity_network_error_is_not_reclassified():
    err = RuntimeError("connection reset by peer")
    models = _FakeModels([err])
    client = cap.wrap_gemini_client(_FakeClient(models))

    with pytest.raises(RuntimeError, match="connection reset"):
        await client.aio.models.generate_content(
            model="gemini-3.7-flash",
            contents="request",
        )

    assert models.calls == 1


def test_capacity_terminal_error_does_not_look_like_raw_overload():
    exc = cap.GeminiCapacityCircuitOpen("Gemini capacity circuit active")
    assert cap.is_capacity_terminal_error(exc)
    assert not cap.is_backend_capacity_error(exc)
