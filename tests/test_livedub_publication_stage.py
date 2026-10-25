"""LiveDub publication data remains lazy, source-compatible, and HTML-safe."""

from __future__ import annotations

import asyncio
import html

from pipelines.livedub_publication_stage import prepare_livedub_publication


def _missing():
    raise NameError("historical local is not defined on this path")


def _forbidden():
    raise AssertionError("lazy fallback must not be evaluated")


def _prepare(*, ai_data, cached_ai_data, title="", performer="", full_title=""):
    return asyncio.run(
        prepare_livedub_publication(
            ai_data=ai_data,
            cached_ai_data=cached_ai_data,
            title=title,
            performer=performer,
            full_title=full_title,
            source_url="source-url",
            html_module=html,
        )
    )


def _patch_card(monkeypatch, card):
    import services.livedub_publication as publication

    calls = []

    async def build(source_line, source_url):
        calls.append((source_line, source_url))
        return card

    monkeypatch.setattr(publication, "build_publication_card", build)
    return calls


def test_fresh_ai_wins_and_card_values_are_escaped(monkeypatch):
    card = {"title": "Card <Title>", "author": "Card Author"}
    calls = _patch_card(monkeypatch, card)

    result = _prepare(
        ai_data=lambda: {"real_title": "AI title", "real_author": "AI author"},
        cached_ai_data=_forbidden,
        title="Fallback title",
        performer="Fallback author",
    )

    assert calls == [("AI title - AI author", "source-url")]
    assert result.card is card
    assert result.title_line == "Card <Title> - Card Author"
    assert result.title_html == "Card &lt;Title&gt; - Card Author"


def test_missing_fresh_data_uses_cache_without_duplicate_author(monkeypatch):
    calls = _patch_card(
        monkeypatch,
        {"title": "Cached title - cached author", "author": "CACHED AUTHOR"},
    )

    result = _prepare(
        ai_data=_missing,
        cached_ai_data=lambda: {
            "real_title": "Cached title",
            "real_author": "Cached author",
        },
    )

    assert calls == [("Cached title - Cached author", "source-url")]
    assert result.title_line == "Cached title - cached author"
    assert result.title_html == "Cached title - cached author"


def test_non_dict_and_missing_values_keep_generic_title_fallback(monkeypatch):
    calls = _patch_card(monkeypatch, {"title": "", "author": ""})

    result = _prepare(
        ai_data=lambda: ["not", "a", "dict"],
        cached_ai_data=_missing,
        full_title="Full title",
    )

    assert calls == [("Full title", "source-url")]
    assert result.title_line == "Переведённое видео"
    assert result.title_html == "Переведённое видео"


def test_empty_fresh_dict_does_not_trigger_cached_fallback(monkeypatch):
    calls = _patch_card(monkeypatch, {"title": "", "author": ""})

    result = _prepare(
        ai_data=lambda: {},
        cached_ai_data=_forbidden,
        title="Fallback title",
        performer="Fallback author",
    )

    assert calls == [("Fallback title - Fallback author", "source-url")]
    assert result.title_line == "Fallback title - Fallback author"


def test_whitespace_title_preserves_empty_html_branch(monkeypatch):
    calls = _patch_card(monkeypatch, {"title": "   ", "author": ""})

    result = _prepare(ai_data=lambda: None, cached_ai_data=lambda: None)

    assert calls == [("Переведённое видео", "source-url")]
    assert result.title_line == ""
    assert result.title_html == ""
