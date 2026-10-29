"""Executed regression coverage for typed RuTube/VK listing helpers."""

import asyncio
from types import SimpleNamespace

from services import search


class ImmediateLoop:
    async def run_in_executor(self, _executor, callback):
        return callback()


def test_rutube_listing_stops_cleanly_on_first_empty_page(monkeypatch):
    response = SimpleNamespace(status_code=200, json=lambda: {"results": []})
    monkeypatch.setattr(search.requests, "get", lambda *_args, **_kwargs: response)

    results, complete = asyncio.run(search._load_rutube_listing("channel", {}, ImmediateLoop(), "Title", 60))

    assert results == []
    assert complete is True


def test_rutube_listing_fast_path_scores_cached_results(monkeypatch):
    cached = [{"id": "video", "title": "Title", "duration": 60}]
    monkeypatch.setattr(search, "get_channel_mapping", lambda _channel: {"rutube_channel_id": "channel"})
    monkeypatch.setattr(search, "_get_rutube_listing_cache", lambda _channel: (cached, True))
    monkeypatch.setattr(search, "_best_match", lambda *_args, **_kwargs: ("https://rutube.ru/video/video/", 0.9))

    result = asyncio.run(search.search_rutube("channel", "Title", 60, "Title"))

    assert result == "https://rutube.ru/video/video/"


def test_vk_search_title_builder_executes_all_nested_helpers():
    title = "Title"
    result = search._build_vk_search_title(
        "Author Name — Why did Paul write Romans?",
        "Why did Paul write Romans?",
        "Who wrote Romans?",
        "Author Name",
    )

    assert result
    assert title in result or "Paul" in result or "Romans" in result


def test_vk_search_builds_request_params_and_returns_canonical_match(monkeypatch):
    item = {"owner_id": 123, "id": 456, "title": "Title", "duration": 60}
    response = SimpleNamespace(status_code=200, json=lambda: {"response": {"items": [item]}})
    monkeypatch.setattr(search, "get_channel_mapping", lambda _channel: {"vk_owner_id": 123})
    monkeypatch.setattr(search.requests, "get", lambda *_args, **_kwargs: response)
    monkeypatch.setattr(search, "_best_match", lambda items, *_args: (items[0]["url"], 0.9))
    monkeypatch.delenv("VK_API_TOKEN", raising=False)

    result = asyncio.run(search.search_vk_video("Title", "channel", 60, {}))

    assert result == "https://vk.com/video123_456"
    assert item["_canonical"] == result
