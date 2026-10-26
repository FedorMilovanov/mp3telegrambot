#!/usr/bin/env python3
"""Cache-hit ссылки и подпись: без внешнего поиска и второй копии обрезки."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

import pipelines.cached_presentation_stage as presentation_stage
from pipelines.cached_presentation_stage import (
    CachedPageLinks,
    CachedPresentationDeps,
    CachedPresentationRequest,
    cached_page_links,
    prepare_cached_presentation,
)
from pipelines.caption_stage import CaptionFit, CaptionSource

URL = "https://youtu.be/video-id"
AI_DATA = {
    "real_title": "Название",
    "real_author": "Автор",
    "format": "sermon",
    "timestamps": "0:00 — начало",
}


def _links(**overrides: Any) -> CachedPageLinks:
    values: dict[str, Any] = {
        "synopsis_url": "https://telegra.ph/synopsis",
        "quotes_url": "https://telegra.ph/quotes",
        "questions_url": "https://telegra.ph/questions",
        "terms_url": "https://telegra.ph/terms",
        "study_url": "https://telegra.ph/study",
        "reflection_url": "",
    }
    values.update(overrides)
    return CachedPageLinks(**values)


def _request(**overrides: Any) -> CachedPresentationRequest:
    values: dict[str, Any] = {
        "links": _links(),
        "ai_data": AI_DATA,
        "full_title": "Author: Full title",
        "channel_name": "Channel",
        "title": "Название",
        "performer": "Автор",
        "duration": 600,
        "file_size_mb": 12.5,
        "bitrate": "128",
        "source_url": URL,
        "cached_rutube_url": "https://rutube.example/video",
        "cached_vk_url": "https://vk.example/video",
    }
    values.update(overrides)
    return CachedPresentationRequest(**values)


async def _unexpected_links(*_args: Any, **_kwargs: Any) -> dict[str, Any] | None:
    raise AssertionError("внешний поиск не нужен при сохранённых alt-links")


def _unexpected_search_title(_ai: dict[str, Any], _title: str) -> str:
    raise AssertionError("search title не нужен при сохранённых alt-links")


def _deps(
    *,
    find_links: Any = _unexpected_links,
    build_search_title: Any = _unexpected_search_title,
) -> CachedPresentationDeps:
    return CachedPresentationDeps(
        find_links=find_links,
        build_search_title=build_search_title,
    )


def _capture_fit(
    monkeypatch: pytest.MonkeyPatch,
    *,
    trim_stage: str = "timestamps_trimmed",
) -> list[tuple[CaptionSource, dict[str, Any] | None]]:
    calls: list[tuple[CaptionSource, dict[str, Any] | None]] = []

    def fake_fit(source: CaptionSource, ai_data: dict[str, Any] | None) -> CaptionFit:
        calls.append((source, ai_data))
        return CaptionFit(
            caption="caption",
            full_caption="full caption",
            timestamps_total=4,
            timestamps_shown=2,
            trim_stage=trim_stage,
            data=ai_data,
        )

    monkeypatch.setattr(presentation_stage, "fit_caption", fake_fit)
    return calls


def _prepare(
    request: CachedPresentationRequest,
    deps: CachedPresentationDeps,
) -> Any:
    return asyncio.run(prepare_cached_presentation(request, deps))


def test_cached_page_links_preserve_all_pages_and_terms() -> None:
    cached = {
        "telegraph_url": "synopsis",
        "quotes_tg_url": "quotes",
        "questions_tg_url": "questions",
        "terms_tg_url": "terms",
        "study_tg_url": "study",
        "reflection_tg_url": "",
    }
    before = repr(cached)

    links = cached_page_links(cached)

    assert links == CachedPageLinks(
        synopsis_url="synopsis",
        quotes_url="quotes",
        questions_url="questions",
        terms_url="terms",
        study_url="study",
        reflection_url="",
    )
    assert repr(cached) == before


def test_cached_page_links_hide_terms_only_with_both_new_pages() -> None:
    links = cached_page_links(
        {
            "telegraph_url": "synopsis",
            "terms_tg_url": "terms",
            "study_tg_url": "study",
            "reflection_tg_url": "reflection",
        }
    )

    assert links.terms_url == ""
    assert links.study_url == "study"
    assert links.reflection_url == "reflection"


def test_cached_page_links_keep_required_synopsis_policy() -> None:
    with pytest.raises(KeyError, match="telegraph_url"):
        cached_page_links({})


@pytest.mark.parametrize(
    ("rutube", "vk"),
    [
        ("https://rutube.example/video", ""),
        ("", "https://vk.example/video"),
        ("https://rutube.example/video", "https://vk.example/video"),
    ],
)
def test_any_cached_alt_link_avoids_external_search(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    rutube: str,
    vk: str,
) -> None:
    fits = _capture_fit(monkeypatch)
    request = _request(cached_rutube_url=rutube, cached_vk_url=vk)

    with caplog.at_level(logging.INFO):
        result = _prepare(request, _deps())

    assert result.caption == "caption"
    assert result.full_caption == "full caption"
    source, ai_data = fits[0]
    assert source.rutube_url == rutube
    assert source.vk_url == vk
    assert ai_data is AI_DATA
    assert f"Кэш alt-links: rutube={bool(rutube)} vk={bool(vk)}" in caplog.text


def test_missing_alt_links_use_search_with_legacy_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fits = _capture_fit(monkeypatch)
    search_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def build_title(ai_data: dict[str, Any], full_title: str) -> str:
        assert ai_data is AI_DATA
        assert full_title == "Author: Full title"
        return "safe search title"

    async def find_links(*args: Any, **kwargs: Any) -> dict[str, Any]:
        search_calls.append((args, kwargs))
        return {"rutube": "found-rutube", "vk": "found-vk"}

    result = _prepare(
        _request(cached_rutube_url="", cached_vk_url=""),
        _deps(find_links=find_links, build_search_title=build_title),
    )

    assert result.caption == "caption"
    assert search_calls == [
        (
            ("safe search title", "Channel", 600),
            {"ai_data": AI_DATA, "fallback_title": "Author: Full title"},
        )
    ]
    source, _ai_data = fits[0]
    assert source.rutube_url == "found-rutube"
    assert source.vk_url == "found-vk"


def test_none_search_result_becomes_empty_platform_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fits = _capture_fit(monkeypatch)

    async def no_links(*_args: Any, **_kwargs: Any) -> None:
        return None

    _prepare(
        _request(cached_rutube_url="", cached_vk_url=""),
        _deps(find_links=no_links, build_search_title=lambda _ai, _title: "search"),
    )

    source, _ai_data = fits[0]
    assert source.rutube_url == ""
    assert source.vk_url == ""


def test_search_failure_is_not_silently_downgraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fits = _capture_fit(monkeypatch)

    async def broken_search(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("search unavailable")

    with pytest.raises(RuntimeError, match="search unavailable"):
        _prepare(
            _request(cached_rutube_url="", cached_vk_url=""),
            _deps(find_links=broken_search, build_search_title=lambda _ai, _title: "search"),
        )

    assert fits == []


def test_caption_source_preserves_page_links_metadata_and_log(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fits = _capture_fit(monkeypatch)
    links = _links(reflection_url="https://telegra.ph/reflection")

    with caplog.at_level(logging.INFO):
        _prepare(_request(links=links), _deps())

    source, ai_data = fits[0]
    assert source == CaptionSource(
        performer="Автор",
        title="Название",
        duration=600,
        file_size_mb=12.5,
        bitrate="128",
        url=URL,
        telegraph_url=links.synopsis_url,
        rutube_url="https://rutube.example/video",
        vk_url="https://vk.example/video",
        quotes_link=links.quotes_url,
        questions_link=links.questions_url,
        terms_link=links.terms_url,
        study_link=links.study_url,
        reflection_link=links.reflection_url,
        format_name="sermon",
    )
    assert ai_data is AI_DATA
    assert ("Кэш caption visible_len=7 format=sermon ts_total=4 ts_in_cap=2 stage=timestamps_trimmed") in caplog.text


def test_empty_analysis_uses_none_and_default_format(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fits = _capture_fit(monkeypatch, trim_stage="")

    with caplog.at_level(logging.INFO):
        _prepare(_request(ai_data={}), _deps())

    source, ai_data = fits[0]
    assert source.format_name == "other"
    assert ai_data is None
    assert "stage=none" in caplog.text


def test_real_caption_stage_receives_resolved_links() -> None:
    result = _prepare(_request(), _deps())

    assert result.caption != "caption"  # это не fake-fit маркер
    assert "https://telegra.ph/synopsis" in result.caption
    assert "https://rutube.example/video" in result.caption
    assert result.full_caption


def test_default_dependencies_are_used(monkeypatch: pytest.MonkeyPatch) -> None:
    deps = _deps()
    monkeypatch.setattr(presentation_stage, "production_deps", lambda: deps)
    _capture_fit(monkeypatch)

    result = asyncio.run(prepare_cached_presentation(_request()))

    assert result.caption == "caption"


def test_production_dependencies_keep_search_bindings() -> None:
    deps = presentation_stage.production_deps()

    assert deps.find_links.__name__ == "find_alternative_links"
    assert deps.build_search_title.__name__ == "_build_search_title"


def test_main_pipeline_wires_cached_links_and_shared_presentation() -> None:
    source = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    stage = Path("pipelines/cache_hit_delivery_stage.py").read_text(encoding="utf-8")

    assert source.count("c_links = cached_page_links(cached)") == 1
    assert source.count("return await deliver_cache_hit(") == 1
    assert "prepare=prepare_cached_presentation" in source
    assert stage.count("cached_caption = await prepare(") == 1
    assert "caption=cached_caption.caption" in stage
    assert "full_caption=cached_caption.full_caption" in stage
    assert 'synopsis_url=request.links.synopsis_url or ""' in stage
    assert "study_url=request.links.study_url" in stage
    assert "reflection_url=request.links.reflection_url" in stage
    assert "terms_url=request.links.terms_url" in stage

    cache_branch = source[source.index("if cache_decision.hit:") :]
    assert cache_branch.index("c_links = cached_page_links(cached)") < cache_branch.index("await load_thumbnail(")
    assert cache_branch.index("await load_thumbnail(") < cache_branch.index("await prepare_audio(")
    assert cache_branch.index("await prepare_audio(") < cache_branch.index("return await deliver_cache_hit(")
