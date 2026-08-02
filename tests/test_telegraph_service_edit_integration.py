import asyncio

import pytest

from services import telegraph
from services.telegraph_edit import TelegraphEditResult


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
