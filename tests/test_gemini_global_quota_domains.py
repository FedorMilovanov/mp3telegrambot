from __future__ import annotations

import asyncio
import logging
import sys
from types import ModuleType, SimpleNamespace

import pytest


def _install_import_stubs() -> None:
    """Keep this focused policy test independent of production SDK installs."""
    try:
        import dotenv  # noqa: F401
    except ImportError:
        dotenv_stub = ModuleType("dotenv")
        dotenv_stub.load_dotenv = lambda: None
        sys.modules["dotenv"] = dotenv_stub

    try:
        import flask  # noqa: F401
    except ImportError:
        flask_stub = ModuleType("flask")

        class _Flask:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def route(self, *args, **kwargs):
                def decorator(func):
                    return func

                return decorator

        flask_stub.Flask = _Flask
        sys.modules["flask"] = flask_stub

    try:
        import telegram  # noqa: F401
    except ImportError:
        telegram_stub = ModuleType("telegram")
        telegram_stub.InlineKeyboardButton = object
        telegram_stub.InlineKeyboardMarkup = object
        sys.modules["telegram"] = telegram_stub


_install_import_stubs()

from core import globals as core_globals
from services import gemini_capacity_control as capacity_control
from services import gemini_quota_domains as quota_domains


class _ServiceError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class _Budget:
    def __init__(self, limit: int = 3) -> None:
        self.limit = limit
        self.used = 0

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit

    def claim(self) -> None:
        if self.exhausted:
            raise RuntimeError("test retry budget exhausted")
        self.used += 1


def _install_capacity_stubs(monkeypatch) -> None:
    async def run_heavy(call):
        return await call()

    monkeypatch.setattr(capacity_control, "GeminiRetryBudget", _Budget)
    monkeypatch.setattr(capacity_control, "run_heavy_gemini_call", run_heavy)
    monkeypatch.setattr(capacity_control, "is_timeout_error", lambda exc: False)
    monkeypatch.setattr(capacity_control, "transient_retry_delay", lambda used: 0.0)
    monkeypatch.setattr(capacity_control, "note_overload", lambda delay: None)
    monkeypatch.setattr(core_globals, "_current_client_idx", 0)


def _install_known_clients(monkeypatch, domains: list[str]):
    clients = [SimpleNamespace(name=f"client-{index}") for index in range(len(domains))]
    monkeypatch.setattr(core_globals, "GEMINI_CLIENTS", clients)
    monkeypatch.setattr(core_globals, "GEMINI_CLIENT_QUOTA_DOMAINS", domains)
    return clients


def test_shared_project_scope_detector_is_conservative() -> None:
    assert quota_domains.is_project_scoped_quota_error(
        _ServiceError(
            429,
            "RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel-FreeTier",
        )
    ) is True
    assert quota_domains.is_project_scoped_quota_error(
        _ServiceError(429, "RESOURCE_EXHAUSTED")
    ) is False
    assert quota_domains.is_project_scoped_quota_error(
        _ServiceError(503, "UNAVAILABLE high demand PerProject")
    ) is False


def test_global_route_skips_known_same_domain_after_project_scoped_429(monkeypatch) -> None:
    _install_capacity_stubs(monkeypatch)
    first, second, third = _install_known_clients(
        monkeypatch,
        ["domain-a", "domain-a", "domain-b"],
    )
    calls: list[str] = []

    async def fn(client):
        calls.append(client.name)
        if client is first:
            raise _ServiceError(
                429,
                "RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel-FreeTier",
            )
        return "ok"

    result = asyncio.run(core_globals.gemini_generate([first, second, third], fn))

    assert result == "ok"
    assert calls == ["client-0", "client-2"]


def test_global_route_generic_429_still_rotates_to_same_labeled_domain(monkeypatch) -> None:
    _install_capacity_stubs(monkeypatch)
    first, second = _install_known_clients(monkeypatch, ["domain-a", "domain-a"])
    calls: list[str] = []

    async def fn(client):
        calls.append(client.name)
        if client is first:
            raise _ServiceError(429, "RESOURCE_EXHAUSTED")
        return "ok"

    result = asyncio.run(core_globals.gemini_generate([first, second], fn))

    assert result == "ok"
    assert calls == ["client-0", "client-1"]


def test_unknown_client_list_disables_same_project_suppression(monkeypatch) -> None:
    _install_capacity_stubs(monkeypatch)
    _install_known_clients(monkeypatch, ["domain-a", "domain-a"])
    first = SimpleNamespace(name="external-0")
    second = SimpleNamespace(name="external-1")
    calls: list[str] = []

    async def fn(client):
        calls.append(client.name)
        if client is first:
            raise _ServiceError(
                429,
                "RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel-FreeTier",
            )
        return "ok"

    result = asyncio.run(core_globals.gemini_generate([first, second], fn))

    assert result == "ok"
    assert calls == ["external-0", "external-1"]


def test_same_domain_skip_never_logs_or_raises_local_label(monkeypatch, caplog) -> None:
    _install_capacity_stubs(monkeypatch)
    first, second, third = _install_known_clients(
        monkeypatch,
        ["private-domain-name", "private-domain-name", "private-domain-name"],
    )
    calls: list[str] = []

    async def fn(client):
        calls.append(client.name)
        raise _ServiceError(
            429,
            "RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel-FreeTier",
        )

    caplog.set_level(logging.INFO)
    with pytest.raises(_ServiceError) as raised:
        asyncio.run(core_globals.gemini_generate([first, second, third], fn))

    assert calls == ["client-0"]
    assert "private-domain-name" not in str(raised.value)
    assert "private-domain-name" not in caplog.text
    assert "label omitted" in caplog.text


def test_503_budget_semantics_remain_same_client_retry_then_rotate(monkeypatch) -> None:
    _install_capacity_stubs(monkeypatch)
    first, second, third = _install_known_clients(
        monkeypatch,
        ["domain-a", "domain-a", "domain-b"],
    )
    calls: list[str] = []

    async def fn(client):
        calls.append(client.name)
        if client is first:
            raise _ServiceError(503, "UNAVAILABLE: high demand")
        return "ok"

    result = asyncio.run(core_globals.gemini_generate([first, second, third], fn))

    assert result == "ok"
    assert calls == ["client-0", "client-0", "client-1"]


def test_response_validator_rotates_semantic_rejection_within_global_budget(monkeypatch) -> None:
    _install_capacity_stubs(monkeypatch)
    first, second, third = _install_known_clients(
        monkeypatch, ["domain-a", "domain-a", "domain-b"]
    )
    calls: list[str] = []

    async def fn(client):
        calls.append(client.name)
        return SimpleNamespace(accepted=client is third)

    result = asyncio.run(
        core_globals.gemini_generate(
            [first, second, third],
            fn,
            response_validator=lambda response: response.accepted,
        )
    )

    assert result.accepted is True
    assert calls == ["client-0", "client-1", "client-2"]


def test_response_validator_cannot_expand_global_retry_budget(monkeypatch) -> None:
    _install_capacity_stubs(monkeypatch)
    clients = _install_known_clients(
        monkeypatch, ["domain-a", "domain-b", "domain-c", "domain-d"]
    )
    calls: list[str] = []

    async def fn(client):
        calls.append(client.name)
        return SimpleNamespace(accepted=False)

    with pytest.raises(RuntimeError, match="response rejected"):
        asyncio.run(
            core_globals.gemini_generate(
                clients,
                fn,
                response_validator=lambda response: response.accepted,
            )
        )

    assert calls == ["client-0", "client-1", "client-2"]
