import asyncio

import pytest
import requests

from services import telegraph
from services.telegraph_edit import TelegraphEditResult


@pytest.mark.asyncio
async def test_telegraph_post_retries_transient_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    sleeps = []

    class Response:
        @staticmethod
        def json():
            return {"ok": True, "result": {"url": "https://telegra.ph/Page"}}

    def post(_url, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise requests.Timeout("temporary")
        return Response()

    async def no_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(telegraph, "TELEGRAPH_TOKEN", "token")
    monkeypatch.setattr(telegraph.requests, "post", post)
    monkeypatch.setattr(telegraph.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(telegraph, "_final_telegraph_polish", lambda nodes: list(nodes))
    monkeypatch.setattr(telegraph, "_clean_telegraph_nodes", lambda nodes: list(nodes))

    url = await telegraph._telegraph_post(
        "Title",
        "Author",
        [{"tag": "p", "children": ["Text"]}],
        asyncio.get_running_loop(),
    )

    assert url == "https://telegra.ph/Page"
    assert calls == 2
    assert sleeps == [1]


@pytest.mark.asyncio
async def test_service_edit_returns_content_too_big_after_one_transport_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    prepared_payloads: list[list] = []

    monkeypatch.setattr(telegraph, "TELEGRAPH_TOKEN", "token")
    monkeypatch.setattr(telegraph, "_clean_telegraph_nodes", lambda nodes: list(nodes))
    monkeypatch.setattr(telegraph, "_postprocess_telegraph_nodes", lambda nodes: list(nodes))
    monkeypatch.setattr(telegraph, "_final_telegraph_polish", lambda nodes: list(nodes))
    monkeypatch.setattr(telegraph, "audit_telegraph_page", lambda *_args, **_kwargs: [])

    async def edit_once(
        _page_url,
        _title,
        _author,
        nodes,
        _loop,
        **_kwargs,
    ) -> TelegraphEditResult:
        nonlocal calls
        calls += 1
        prepared_payloads.append(nodes)
        return TelegraphEditResult(
            ok=False,
            error="CONTENT_TOO_BIG",
            retryable=False,
        )

    monkeypatch.setattr(telegraph, "edit_telegraph_page_once", edit_once)

    nodes = [{"tag": "p", "children": ["Текст"]}]
    result = await telegraph._edit_telegraph_page_classified(
        "https://telegra.ph/Page",
        "Title",
        "Author",
        nodes,
        asyncio.get_running_loop(),
    )

    assert result.error == "CONTENT_TOO_BIG"
    assert calls == 1
    assert prepared_payloads == [nodes]


@pytest.mark.asyncio
async def test_service_edit_retries_transient_transport_and_invalidates_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = iter(
        [
            TelegraphEditResult(ok=False, error="upstream", retryable=True),
            TelegraphEditResult(ok=True),
        ]
    )
    calls = 0

    monkeypatch.setattr(telegraph, "TELEGRAPH_TOKEN", "token")
    monkeypatch.setattr(telegraph, "_clean_telegraph_nodes", lambda nodes: list(nodes))
    monkeypatch.setattr(telegraph, "_postprocess_telegraph_nodes", lambda nodes: list(nodes))
    monkeypatch.setattr(telegraph, "_final_telegraph_polish", lambda nodes: list(nodes))
    monkeypatch.setattr(telegraph, "audit_telegraph_page", lambda *_args, **_kwargs: [])

    async def edit_once(*_args, **_kwargs) -> TelegraphEditResult:
        nonlocal calls
        calls += 1
        return next(outcomes)

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(telegraph, "edit_telegraph_page_once", edit_once)
    original_retry = telegraph.run_telegraph_edit_with_retry

    async def patched_retry(operation, **_kwargs):
        return await original_retry(operation, max_attempts=3, sleep=no_sleep)

    monkeypatch.setattr(telegraph, "run_telegraph_edit_with_retry", patched_retry)

    result = await telegraph._edit_telegraph_page_classified(
        "https://telegra.ph/Page",
        "Title",
        "Author",
        [],
        asyncio.get_running_loop(),
    )

    assert result.ok is True
    assert calls == 2
