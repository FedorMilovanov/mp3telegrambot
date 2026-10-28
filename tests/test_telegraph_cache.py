"""Executed ownership and behavior contracts for the shared Telegraph cache."""

import asyncio

from converters import md_telegraph
from core import telegraph_cache
from services import pdf_generator


def test_lru_cache_behaves_as_a_bounded_thread_safe_cache() -> None:
    cache = telegraph_cache._LRUCache[str, str](maxsize=2)
    assert cache.get_cached("missing") is None

    cache.put("a", "1")
    cache.put("b", "2")
    assert cache.get_cached("a") == "1"
    cache.put("a", "updated")
    cache.put("c", "3")

    assert cache.get_cached("b") is None
    assert cache.get_cached("a") == "updated"
    assert cache.delete("a") is True
    assert cache.delete("a") is False
    cache.clear_safe()
    assert cache.get_cached("c") is None


def test_pdf_exports_use_the_shared_core_cache() -> None:
    assert pdf_generator._TELEGRAPH_CACHE is telegraph_cache._TELEGRAPH_CACHE
    assert pdf_generator.invalidate_telegraph_url is telegraph_cache.invalidate_telegraph_url
    assert pdf_generator.clear_telegraph_cache is telegraph_cache.clear_telegraph_cache

    url = "https://telegra.ph/cache-ownership-test"
    telegraph_cache._TELEGRAPH_CACHE.put(url, "cached")
    pdf_generator.invalidate_telegraph_url(url)
    assert telegraph_cache._TELEGRAPH_CACHE.get_cached(url) is None

    telegraph_cache._TELEGRAPH_CACHE.put(url, "cached-again")
    pdf_generator.clear_telegraph_cache()
    assert telegraph_cache._TELEGRAPH_CACHE.get_cached(url) is None


def test_successful_legacy_edit_invalidates_core_cache(monkeypatch) -> None:
    page_url = "https://telegra.ph/cache-edit-test"
    invalidated: list[str] = []
    monkeypatch.setattr(md_telegraph, "TELEGRAPH_TOKEN", "token")
    monkeypatch.setattr(telegraph_cache, "invalidate_telegraph_url", invalidated.append)

    class Response:
        def json(self):
            return {"ok": True}

    monkeypatch.setattr(md_telegraph.requests, "post", lambda *_args, **_kwargs: Response())

    async def edit():
        loop = asyncio.get_running_loop()
        return await md_telegraph._edit_telegraph_page(
            page_url,
            "Title",
            "Author",
            [{"tag": "p", "children": ["Real content"]}],
            loop,
        )

    assert asyncio.run(edit()) is True
    assert invalidated == [page_url]


def test_successful_legacy_edit_survives_cache_invalidation_failure(monkeypatch) -> None:
    monkeypatch.setattr(md_telegraph, "TELEGRAPH_TOKEN", "token")

    def invalidate(_url):
        raise RuntimeError("cache unavailable")

    class Response:
        def json(self):
            return {"ok": True}

    monkeypatch.setattr(telegraph_cache, "invalidate_telegraph_url", invalidate)
    monkeypatch.setattr(md_telegraph.requests, "post", lambda *_args, **_kwargs: Response())

    async def edit():
        return await md_telegraph._edit_telegraph_page(
            "https://telegra.ph/cache-edit-failure",
            "Title",
            "Author",
            [{"tag": "p", "children": ["Real content"]}],
            asyncio.get_running_loop(),
        )

    assert asyncio.run(edit()) is True
