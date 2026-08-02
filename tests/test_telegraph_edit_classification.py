import asyncio

import pytest
import requests

from services.telegraph_edit import (
    TelegraphEditResult,
    classify_telegraph_edit_error,
    edit_telegraph_page_once,
    run_telegraph_edit_with_retry,
    telegraph_page_path,
    telegraph_retry_delay,
)


def test_content_too_big_is_deterministic_and_not_retryable() -> None:
    result = classify_telegraph_edit_error("CONTENT_TOO_BIG", status_code=200)
    assert result.ok is False
    assert result.error == "CONTENT_TOO_BIG"
    assert result.retryable is False
    assert result.retry_after_seconds == 0
    assert telegraph_retry_delay(result, 0) == 0


def test_flood_wait_preserves_retry_delay() -> None:
    result = classify_telegraph_edit_error("FLOOD_WAIT_7", status_code=200)
    assert result.retryable is True
    assert result.retry_after_seconds == 7
    assert telegraph_retry_delay(result, 0) == 7


def test_http_overload_is_retryable_but_bad_payload_is_not() -> None:
    assert classify_telegraph_edit_error("upstream", status_code=503).retryable is True
    assert classify_telegraph_edit_error("TITLE_REQUIRED", status_code=200).retryable is False


def test_page_path_accepts_url_and_raw_path() -> None:
    assert telegraph_page_path("https://telegra.ph/Lyudi-Slova-08-02") == "Lyudi-Slova-08-02"
    assert telegraph_page_path("/Lyudi-Slova-08-02") == "Lyudi-Slova-08-02"
    assert telegraph_page_path("") == ""


@pytest.mark.asyncio
async def test_one_shot_edit_returns_api_error_without_sleeping() -> None:
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": False, "error": "CONTENT_TOO_BIG"}

    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    result = await edit_telegraph_page_once(
        "https://telegra.ph/Lyudi-Slova-08-02",
        "Люди Слова",
        "Пол Вошер",
        [{"tag": "p", "children": ["Текст"]}],
        asyncio.get_running_loop(),
        token="token",
        post=post,
    )

    assert result.error == "CONTENT_TOO_BIG"
    assert result.retryable is False
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_network_timeout_is_retryable() -> None:
    def post(_url, **_kwargs):
        raise requests.Timeout("slow")

    result = await edit_telegraph_page_once(
        "https://telegra.ph/Page",
        "Title",
        "Author",
        [],
        asyncio.get_running_loop(),
        token="token",
        post=post,
    )

    assert result.retryable is True
    assert "Timeout" in result.error


@pytest.mark.asyncio
async def test_retry_runner_returns_content_too_big_after_one_call_and_zero_sleeps() -> None:
    calls = 0
    sleeps: list[float] = []

    async def operation() -> TelegraphEditResult:
        nonlocal calls
        calls += 1
        return TelegraphEditResult(ok=False, error="CONTENT_TOO_BIG", retryable=False)

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    result = await run_telegraph_edit_with_retry(operation, sleep=sleep)

    assert result.error == "CONTENT_TOO_BIG"
    assert calls == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_retry_runner_retries_transient_error_then_succeeds() -> None:
    outcomes = iter(
        [
            TelegraphEditResult(ok=False, error="upstream", retryable=True),
            TelegraphEditResult(ok=True),
        ]
    )
    sleeps: list[float] = []

    async def operation() -> TelegraphEditResult:
        return next(outcomes)

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    result = await run_telegraph_edit_with_retry(operation, sleep=sleep)

    assert result.ok is True
    assert sleeps == [3]
