from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from telegram.request import HTTPXRequest, RequestData

from core import runtime_status
from services.polling_reliability_runtime import PollingHeartbeatHTTPXRequest


@pytest.fixture(autouse=True)
def _fresh_runtime_status() -> Iterator[None]:
    runtime_status.reset()
    yield
    runtime_status.reset()


@pytest.mark.parametrize(
    ("url", "status", "witnessed"),
    [
        ("https://api.telegram.org/bot-token/getUpdates", 200, True),
        ("https://local.bot/bot-token/getUpdates?timeout=10", 200, True),
        ("https://api.telegram.org/bot-token/getUpdates", 500, False),
        ("https://api.telegram.org/bot-token/sendMessage", 200, False),
    ],
)
def test_only_successful_get_updates_records_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    status: int,
    witnessed: bool,
) -> None:
    async def fake_do_request(
        _self: HTTPXRequest,
        _url: str,
        _method: str,
        _request_data: RequestData | None = None,
        *_timeouts: Any,
    ) -> tuple[int, bytes]:
        return status, b'{"ok":true,"result":[]}'

    monkeypatch.setattr(HTTPXRequest, "do_request", fake_do_request)
    request = object.__new__(PollingHeartbeatHTTPXRequest)

    asyncio.run(request.do_request(url, "POST"))

    assert (runtime_status.snapshot()["last_polling_age_seconds"] is not None) is witnessed


@pytest.mark.parametrize(
    "payload",
    [
        b'{"ok":false,"result":[]}',
        b'{"ok":true,"result":{}}',
        b'{"ok":true,"result":["not-an-update"]}',
        b'{"result":[]}',
        b"not-json",
    ],
)
def test_invalid_or_unsuccessful_telegram_payload_does_not_record_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> None:
    async def fake_do_request(
        _self: HTTPXRequest,
        _url: str,
        _method: str,
        _request_data: RequestData | None = None,
        *_timeouts: Any,
    ) -> tuple[int, bytes]:
        return 200, payload

    monkeypatch.setattr(HTTPXRequest, "do_request", fake_do_request)
    request = object.__new__(PollingHeartbeatHTTPXRequest)

    asyncio.run(request.do_request("https://api.telegram.org/bot-token/getUpdates", "POST"))

    assert runtime_status.snapshot()["last_polling_age_seconds"] is None


def test_failed_get_updates_does_not_record_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_do_request(
        _self: HTTPXRequest,
        _url: str,
        _method: str,
        _request_data: RequestData | None = None,
        *_timeouts: Any,
    ) -> tuple[int, bytes]:
        raise TimeoutError("poll timed out")

    monkeypatch.setattr(HTTPXRequest, "do_request", fake_do_request)
    request = object.__new__(PollingHeartbeatHTTPXRequest)

    with pytest.raises(TimeoutError, match="poll timed out"):
        asyncio.run(request.do_request("https://api.telegram.org/bot-token/getUpdates", "POST"))
    assert runtime_status.snapshot()["last_polling_age_seconds"] is None


def test_main_wires_heartbeat_transport_and_polling_start() -> None:
    source = Path("main.py").read_text(encoding="utf-8")

    assert source.count("get_updates_request = PollingHeartbeatHTTPXRequest") == 2
    assert source.index("runtime_status.record_polling_started()") < source.index("await updater.start_polling(")
