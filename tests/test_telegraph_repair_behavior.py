"""Executed Telegraph edit/repair transport and archive-chain behavior."""

from collections.abc import Callable, Coroutine
from typing import Any

import asyncio

import pytest

from services import telegraph_edit as edit
from services import telegraph_repair as repair


def _run(callback: Callable[..., Coroutine[Any, Any, Any]], *args: Any, **kwargs: Any) -> Any:
    return asyncio.run(callback(*args, **kwargs))


class Response:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def json(self) -> Any:
        return self.payload


def test_record_and_part_url_empty_guards() -> None:
    assert repair.collect_record_page_urls(None) == []
    assert repair.guess_telegraph_part_url("https://telegra.ph/Page/", 1) == ("https://telegra.ph/Page")
    assert _run(repair.expand_telegraph_page_chain, "https://example.com/Page") == []


def test_chain_stops_on_api_error_and_survives_request_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        repair.requests,
        "get",
        lambda *_args, **_kwargs: Response({"ok": False, "error": "missing"}),
    )
    assert _run(repair.expand_telegraph_page_chain, "https://telegra.ph/Page") == ["https://telegra.ph/Page"]

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("offline")

    monkeypatch.setattr(repair.requests, "get", fail)
    assert _run(repair.expand_telegraph_page_chain, "https://telegra.ph/Page") == ["https://telegra.ph/Page"]


def test_chain_probes_conventional_second_part(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def get(url: str, **kwargs: Any) -> Response:
        calls.append((url, kwargs))
        if "return_content=false" in url:
            return Response({"ok": True})
        return Response({"ok": True, "result": {"content": []}})

    monkeypatch.setattr(repair.requests, "get", get)
    assert _run(repair.expand_telegraph_page_chain, "https://telegra.ph/Page") == [
        "https://telegra.ph/Page",
        "https://telegra.ph/Page-2",
    ]
    assert calls == [
        ("https://api.telegra.ph/getPage/Page?return_content=true", {"timeout": 30}),
        ("https://api.telegra.ph/getPage/Page-2?return_content=false", {"timeout": 30}),
        ("https://api.telegra.ph/getPage/Page-2?return_content=true", {"timeout": 30}),
    ]


def test_chain_ignores_failed_second_part_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    def get(url: str, **_kwargs: Any) -> Response:
        if "return_content=false" in url:
            raise OSError("probe unavailable")
        return Response({"ok": True, "result": {"content": []}})

    monkeypatch.setattr(repair.requests, "get", get)
    assert _run(repair.expand_telegraph_page_chain, "https://telegra.ph/Page") == ["https://telegra.ph/Page"]


def test_repair_page_rejects_invalid_url_and_getpage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(repair.repair_telegraph_page_url, "https://example.com/Page")
    assert result.ok is False
    assert result.error == "not_telegra_ph_url"

    monkeypatch.setattr(
        repair.requests,
        "get",
        lambda *_args, **_kwargs: Response({"ok": False, "error": "PAGE_NOT_FOUND"}),
    )
    result = _run(repair.repair_telegraph_page_url, "https://telegra.ph/Page")
    assert result.ok is False
    assert result.error == "PAGE_NOT_FOUND"


def test_repair_page_skips_edit_when_nodes_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = [{"tag": "p", "children": ["clean"]}]
    monkeypatch.setattr(
        repair.requests,
        "get",
        lambda *_args, **_kwargs: Response(
            {
                "ok": True,
                "result": {"title": "Title", "author_name": "Author", "content": nodes},
            }
        ),
    )
    monkeypatch.setattr(repair, "_postprocess_telegraph_nodes", lambda value: list(value))
    monkeypatch.setattr(repair, "audit_telegraph_page", lambda *_a, **_k: ["warning"])
    monkeypatch.setattr(repair, "format_audit_issues", lambda *_a, **_k: "audit summary")

    async def must_not_edit(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("unchanged nodes must not be edited")

    monkeypatch.setattr(repair, "_edit_telegraph_page", must_not_edit)
    result = _run(repair.repair_telegraph_page_url, "https://telegra.ph/Page")
    assert result.ok is True
    assert result.changed is False
    assert result.title == "Title"
    assert result.audit_summary == "audit summary"


@pytest.mark.parametrize("edit_ok", [False, True])
def test_repair_page_edits_changed_nodes_and_preserves_outcome(monkeypatch: pytest.MonkeyPatch, edit_ok: bool) -> None:
    nodes = [{"tag": "p", "children": ["dirty"]}]
    monkeypatch.setattr(
        repair.requests,
        "get",
        lambda *_args, **_kwargs: Response(
            {
                "ok": True,
                "result": {"title": "Title", "author_name": "Author", "content": nodes},
            }
        ),
    )
    monkeypatch.setattr(
        repair,
        "_postprocess_telegraph_nodes",
        lambda value: [*value, {"tag": "hr"}],
    )
    monkeypatch.setattr(repair, "audit_telegraph_page", lambda *_a, **_k: [])
    monkeypatch.setattr(repair, "format_audit_issues", lambda *_a, **_k: "")
    edits: list[tuple[str, str, str, list[Any]]] = []

    async def edit_page(
        url: str, title: str, author: str, fixed_nodes: list[Any], _loop: asyncio.AbstractEventLoop
    ) -> bool:
        edits.append((url, title, author, fixed_nodes))
        return edit_ok

    monkeypatch.setattr(repair, "_edit_telegraph_page", edit_page)
    result = _run(repair.repair_telegraph_page_url, "https://telegra.ph/Page")
    assert result.ok is edit_ok
    assert result.changed is True
    assert result.error == ("" if edit_ok else "editPage_failed")
    assert edits[0][3][-1] == {"tag": "hr"}


def test_repair_page_converts_request_exception_to_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("offline")

    monkeypatch.setattr(repair.requests, "get", fail)
    result = _run(repair.repair_telegraph_page_url, "https://telegra.ph/Page")
    assert result.ok is False
    assert result.error == "OSError: offline"


def test_repair_record_expands_deduplicates_and_paces(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(repair.repair_generated_page_record, {}) == []

    async def expand(url: str) -> list[str]:
        if url.endswith("syn"):
            return []
        return [url, url]

    repaired: list[str] = []

    async def repair_url(url: str) -> repair.TelegraphRepairResult:
        repaired.append(url)
        return repair.TelegraphRepairResult(url=url, ok=True)

    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(repair, "expand_telegraph_page_chain", expand)
    monkeypatch.setattr(repair, "repair_telegraph_page_url", repair_url)
    monkeypatch.setattr(repair.asyncio, "sleep", sleep)
    record = {
        "synopsis_url": "https://telegra.ph/syn",
        "study_url": "https://telegra.ph/study",
    }
    results = _run(repair.repair_generated_page_record, record)
    assert repaired == ["https://telegra.ph/syn", "https://telegra.ph/study"]
    assert [result.url for result in results] == repaired
    assert sleeps == [1.0, 1.0]


def test_repair_formatters_handle_empty_batches() -> None:
    assert "Нечего чинить" in repair.format_repair_results([])
    assert "Нечего чинить" in repair.format_batch_repair_results([])


def test_batch_repairs_respect_limit_and_format_overflow(monkeypatch: pytest.MonkeyPatch) -> None:
    async def repair_record(record: dict[str, Any]) -> list[repair.TelegraphRepairResult]:
        return [repair.TelegraphRepairResult(url=f"https://telegra.ph/{record['video_id']}", ok=True)]

    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(repair, "repair_generated_page_record", repair_record)
    monkeypatch.setattr(repair.asyncio, "sleep", sleep)
    records = [{"video_id": f"v{index}", "title": f"T{index}"} for index in range(3)]
    items = _run(repair.repair_generated_page_records, records, limit=2)
    assert [item.video_id for item in items] == ["v0", "v1"]
    assert sleeps == [1.0, 1.0]

    results = [
        repair.TelegraphRepairResult(
            url=f"https://telegra.ph/p{index}",
            ok=index != 0,
            changed=True,
            error="failed" if index == 0 else "",
        )
        for index in range(6)
    ]
    text = repair.format_batch_repair_results(
        [repair.TelegraphRepairBatchItem(video_id="v", title="Title", results=results)]
    )
    assert "pages=5/6 OK" in text
    assert "failed" in text
    assert "…ещё 1 страниц" in text


def test_one_shot_edit_validates_token_and_path() -> None:
    result = _run(
        edit.edit_telegraph_page_once,
        "https://telegra.ph/Page",
        "Title",
        "Author",
        [],
        None,
        token="",
    )
    assert result.error == "no_token"
    result = _run(
        edit.edit_telegraph_page_once,
        "",
        "Title",
        "Author",
        [],
        None,
        token="token",
    )
    assert result.error == "invalid_page_path"


def test_one_shot_edit_marks_malformed_response_nonretryable() -> None:
    def post(*_args: Any, **_kwargs: Any) -> Response:
        return Response(None)

    async def invoke() -> edit.TelegraphEditResult:
        return await edit.edit_telegraph_page_once(
            "https://telegra.ph/Page",
            "Title",
            "Author",
            [],
            asyncio.get_running_loop(),
            token="token",
            post=post,
        )

    result = _run(invoke)
    assert result.ok is False
    assert result.retryable is False
    assert "AttributeError" in result.error


def test_wait_before_retry_only_sleeps_for_positive_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(edit.asyncio, "sleep", sleep)
    retryable = edit.TelegraphEditResult(ok=False, error="wait", retryable=True, retry_after_seconds=2)
    _run(edit.wait_before_telegraph_retry, retryable, 0)
    _run(
        edit.wait_before_telegraph_retry,
        edit.TelegraphEditResult(ok=False, error="fatal", retryable=False),
        0,
    )
    assert sleeps == [2]
