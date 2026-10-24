#!/usr/bin/env python3
"""Стадия рендера: какие страницы выпускаются, в каком порядке, что при сбое.

Все публикующие функции — фейки. Поэтому проверяется именно решение: взаимные
исключения, последовательность Study/Reflection, изоляция сбоев и перенос
компактной замены «Вопросы» из навигации.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from pipelines.publication_plan import PagePlan
from pipelines.telegraph_render import (
    RenderDeps,
    RenderRequest,
    is_reflection_fallback,
    render_pages,
)

ALL_ON = PagePlan(
    analytics=True,
    questions=True,
    terms=True,
    study_analysis=False,
    reflection_application=False,
)
STUDY_ONLY = PagePlan(
    analytics=False,
    questions=False,
    terms=False,
    study_analysis=True,
    reflection_application=False,
)
BOTH_NEW = PagePlan(
    analytics=False,
    questions=False,
    terms=False,
    study_analysis=True,
    reflection_application=True,
)

AI = {"real_title": "Тема", "main_topic": "молитва"}


class Pages:
    """Фейковые публикующие функции с записью порядка вызовов."""

    def __init__(self, results=None, errors=None, links=None, links_error=None):
        self.calls: list[str] = []
        self._results = results or {}
        self._errors = errors or {}
        self._links = links
        self._links_error = links_error
        self.kwargs: dict[str, dict] = {}

    async def _page(self, name, *args, **kwargs):
        self.calls.append(name)
        self.kwargs[name] = kwargs
        if name in self._errors:
            raise self._errors[name]
        return self._results.get(name)

    async def analytics(self, *a, **kw):
        return await self._page("analytics", *a, **kw)

    async def questions(self, *a, **kw):
        return await self._page("questions", *a, **kw)

    async def terms(self, *a, **kw):
        return await self._page("terms", *a, **kw)

    async def study(self, *a, **kw):
        return await self._page("study", *a, **kw)

    async def reflection(self, *a, **kw):
        return await self._page("reflection", *a, **kw)

    async def combined(self, *a, **kw):
        return await self._page("combined", *a, **kw)

    async def find_links(self, *a, **kw):
        self.calls.append("links")
        if self._links_error is not None:
            raise self._links_error
        return self._links

    def deps(self) -> RenderDeps:
        return RenderDeps(
            analytics=self.analytics,
            questions=self.questions,
            terms=self.terms,
            study_analysis=self.study,
            reflection=self.reflection,
            combined=self.combined,
            find_links=self.find_links,
        )


def make_request(**overrides) -> RenderRequest:
    base = dict(
        ai_data=dict(AI),
        questions=["вопрос 1"],
        terms_data={"concepts": ["термин"]},
        plan=ALL_ON,
        search_title="тема",
        author="Автор",
        url="https://youtu.be/abc",
        channel_name="Канал",
        full_title="Полное название",
        duration=600,
        media_id="vid1",
        early_links={"rutube": None, "vk": None},
    )
    base.update(overrides)
    return RenderRequest(**base)


@pytest.mark.asyncio
async def test_legacy_pages_run_in_parallel() -> None:
    pages = Pages(results={"analytics": "u-a", "questions": "u-q", "terms": "u-t"})
    out = await render_pages(make_request(), pages.deps())

    assert out.analytics_url == "u-a"
    assert out.questions_url == "u-q"
    assert out.terms_url == "u-t"
    assert out.study_analysis_url is None
    assert out.reflection_url is None
    assert sorted(pages.calls) == ["analytics", "questions", "terms"]


@pytest.mark.asyncio
async def test_disabled_pages_are_not_called() -> None:
    plan = PagePlan(
        analytics=False,
        questions=False,
        terms=False,
        study_analysis=False,
        reflection_application=False,
    )
    pages = Pages()
    out = await render_pages(make_request(plan=plan), pages.deps())

    assert pages.calls == []
    assert out.analytics_url is None


@pytest.mark.asyncio
async def test_study_replaces_analytics_even_if_plan_says_both() -> None:
    """Защита в глубину: план уже гасит Аналитику, но стадия не запустит обе."""
    plan = PagePlan(
        analytics=True,
        questions=True,
        terms=True,
        study_analysis=True,
        reflection_application=False,
    )
    pages = Pages(results={"study": "u-s"})
    out = await render_pages(make_request(plan=plan), pages.deps())

    assert "analytics" not in pages.calls
    assert out.study_analysis_url == "u-s"


@pytest.mark.asyncio
async def test_terms_skipped_when_both_new_pages_enabled() -> None:
    plan = PagePlan(
        analytics=False,
        questions=False,
        terms=True,
        study_analysis=True,
        reflection_application=True,
    )
    pages = Pages(results={"study": "u-s", "reflection": "u-r"})
    out = await render_pages(make_request(plan=plan), pages.deps())

    assert "terms" not in pages.calls
    assert out.terms_url is None


@pytest.mark.asyncio
async def test_questions_skipped_when_reflection_enabled() -> None:
    plan = PagePlan(
        analytics=False,
        questions=True,
        terms=False,
        study_analysis=False,
        reflection_application=True,
    )
    pages = Pages(results={"reflection": "https://telegra.ph/razmyshlenie-1"})
    out = await render_pages(make_request(plan=plan), pages.deps())

    assert "questions" not in pages.calls
    assert out.reflection_url == "https://telegra.ph/razmyshlenie-1"


@pytest.mark.asyncio
async def test_study_and_reflection_are_sequential() -> None:
    """Free tier: параллельные крупные запросы дают каскадные 503."""
    pages = Pages(
        results={
            "study": "u-s",
            "reflection": "https://telegra.ph/razmyshlenie-2",
        }
    )
    out = await render_pages(make_request(plan=BOTH_NEW), pages.deps())

    assert "combined" not in pages.calls
    assert pages.calls.index("study") < pages.calls.index("reflection")
    assert out.study_analysis_url == "u-s"
    assert out.reflection_url == "https://telegra.ph/razmyshlenie-2"


@pytest.mark.asyncio
async def test_combined_mode_is_opt_in_and_passes_compact_fallbacks() -> None:
    pages = Pages(
        results={
            "combined": _Combined(
                "https://telegra.ph/razbor-1", "https://telegra.ph/razmyshlenie-1"
            )
        }
    )
    out = await render_pages(
        make_request(plan=BOTH_NEW, combined_enabled=True), pages.deps()
    )

    assert "study" not in pages.calls and "reflection" not in pages.calls
    assert out.study_analysis_url == "https://telegra.ph/razbor-1"
    assert out.reflection_url == "https://telegra.ph/razmyshlenie-1"
    kwargs = pages.kwargs["combined"]
    assert kwargs["rutube_url"] == ""
    assert callable(kwargs["study_compact_fn"])
    assert callable(kwargs["reflection_compact_fn"])


@pytest.mark.asyncio
async def test_combined_falls_back_to_separate_when_disabled_for_request() -> None:
    pages = Pages(results={"study": "u-s", "reflection": "https://telegra.ph/razmyshlenie-3"})
    await render_pages(make_request(plan=BOTH_NEW, combined_enabled=False), pages.deps())

    assert "combined" not in pages.calls
    assert "study" in pages.calls and "reflection" in pages.calls


@pytest.mark.asyncio
async def test_combined_none_falls_back_to_separate() -> None:
    pages = Pages(
        results={"combined": None, "study": "u-s", "reflection": "https://telegra.ph/razmyshlenie-4"}
    )
    out = await render_pages(
        make_request(plan=BOTH_NEW, combined_enabled=True), pages.deps()
    )

    assert "study" in pages.calls
    assert out.study_analysis_url == "u-s"


@pytest.mark.asyncio
async def test_combined_error_falls_back_and_keeps_publication(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pages = Pages(
        errors={"combined": RuntimeError("503")},
        results={"study": "u-s", "reflection": "https://telegra.ph/razmyshlenie-5"},
    )
    with caplog.at_level(logging.WARNING):
        out = await render_pages(
            make_request(plan=BOTH_NEW, combined_enabled=True), pages.deps()
        )

    assert "Study+Reflection combined pipeline error: 503" in caplog.text
    assert out.study_analysis_url == "u-s"
    assert out.reflection_url == "https://telegra.ph/razmyshlenie-5"


@pytest.mark.asyncio
async def test_study_error_does_not_block_reflection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pages = Pages(
        errors={"study": RuntimeError("boom")},
        results={"reflection": "https://telegra.ph/razmyshlenie-6"},
    )
    with caplog.at_level(logging.WARNING):
        out = await render_pages(make_request(plan=BOTH_NEW), pages.deps())

    assert "StudyAnalysis pipeline error: boom" in caplog.text
    assert out.study_analysis_url is None
    assert out.reflection_url == "https://telegra.ph/razmyshlenie-6"


@pytest.mark.asyncio
async def test_page_error_is_logged_and_isolated(caplog: pytest.LogCaptureFixture) -> None:
    pages = Pages(errors={"analytics": RuntimeError("page exploded")},
                  results={"questions": "u-q"})
    with caplog.at_level(logging.WARNING):
        out = await render_pages(make_request(), pages.deps())

    assert "analytics error: page exploded" in caplog.text
    assert out.analytics_url is None
    assert out.questions_url == "u-q"


@pytest.mark.asyncio
async def test_early_links_are_reused() -> None:
    pages = Pages(results={"analytics": "u"})
    out = await render_pages(
        make_request(early_links={"rutube": "https://rt/1", "vk": ""}), pages.deps()
    )

    assert "links" not in pages.calls
    assert out.alt_links == {"rutube": "https://rt/1", "vk": ""}


@pytest.mark.asyncio
async def test_no_analysis_means_no_link_search() -> None:
    pages = Pages()
    out = await render_pages(make_request(ai_data=None, early_links=None), pages.deps())

    assert "links" not in pages.calls
    assert out.alt_links == {"rutube": None, "vk": None}


@pytest.mark.asyncio
async def test_links_error_does_not_block_pages(caplog: pytest.LogCaptureFixture) -> None:
    pages = Pages(links_error=RuntimeError("поиск упал"), results={"analytics": "u"})
    with caplog.at_level(logging.WARNING):
        out = await render_pages(make_request(early_links=None), pages.deps())

    assert "find_alternative_links error: поиск упал" in caplog.text
    assert out.alt_links == {"rutube": None, "vk": None}
    assert out.analytics_url == "u"


@pytest.mark.asyncio
async def test_links_found_are_returned() -> None:
    pages = Pages(links={"rutube": "https://rt/2", "vk": "https://vk/2"})
    out = await render_pages(make_request(early_links=None), pages.deps())

    assert out.alt_links == {"rutube": "https://rt/2", "vk": "https://vk/2"}


@pytest.mark.asyncio
async def test_reflection_fallback_moves_to_questions() -> None:
    """Компактная замена «Вопросы» не должна называться в nav «Размышлением»."""
    pages = Pages(results={"reflection": "https://telegra.ph/voprosy-123"})
    out = await render_pages(make_request(plan=BOTH_NEW), pages.deps())

    assert out.reflection_url is None
    assert out.questions_url == "https://telegra.ph/voprosy-123"
    assert out.reflection_moved_to_questions is True


@pytest.mark.asyncio
async def test_reflection_fallback_does_not_overwrite_questions() -> None:
    pages = Pages(results={"reflection": "https://telegra.ph/voprosy-9", "analytics": "u-a"})
    out = await render_pages(
        make_request(plan=BOTH_NEW, questions=["q"]), pages.deps()
    )
    assert out.reflection_url is None
    assert out.questions_url == "https://telegra.ph/voprosy-9"


@pytest.mark.asyncio
async def test_real_reflection_url_stays() -> None:
    pages = Pages(results={"reflection": "https://telegra.ph/razmyshlenie-77"})
    out = await render_pages(make_request(plan=BOTH_NEW), pages.deps())

    assert out.reflection_url == "https://telegra.ph/razmyshlenie-77"
    assert out.reflection_moved_to_questions is False


@pytest.mark.parametrize(
    "url, expected",
    [
        (None, False),
        ("", False),
        ("https://telegra.ph/razmyshlenie-1", False),
        ("https://telegra.ph/voprosy-1", True),
        ("https://telegra.ph/konspekt-1", True),
    ],
)
def test_is_reflection_fallback(url, expected) -> None:
    assert is_reflection_fallback(url) is expected


def test_nav_pages_skip_empty() -> None:
    from pipelines.telegraph_render import RenderedPages

    pages = RenderedPages(study_analysis_url="u-s")
    assert pages.as_nav_pages("u-c") == [
        ("Конспект", "u-c"),
        ("Разбор материала", "u-s"),
    ]
    assert RenderedPages().as_nav_pages("") == []


def test_production_deps_wire_real_boundaries() -> None:
    from pipelines.telegraph_render import production_deps
    from services.search import find_alternative_links
    from services.telegraph_pages import (
        create_telegraph_analytics,
        create_telegraph_questions,
        create_telegraph_reflection_application,
        create_telegraph_study_analysis,
        create_telegraph_study_reflection_combined,
        create_telegraph_terms,
    )

    deps = production_deps()
    assert deps.analytics is create_telegraph_analytics
    assert deps.questions is create_telegraph_questions
    assert deps.terms is create_telegraph_terms
    assert deps.study_analysis is create_telegraph_study_analysis
    assert deps.reflection is create_telegraph_reflection_application
    assert deps.combined is create_telegraph_study_reflection_combined
    assert deps.find_links is find_alternative_links


class _Combined:
    def __init__(self, study_url: str, reflection_url: str):
        self.study_url = study_url
        self.reflection_url = reflection_url
        self.mode = "combined"
        self.study_page_type = "study"
        self.reflection_page_type = "reflection"


def test_gather_returns_do_not_leak_exceptions() -> None:
    """`asyncio.gather(return_exceptions=True)` не должен давать URL-исключение."""
    async def main() -> None:
        results = await asyncio.gather(
            asyncio.sleep(0, result="ok"),
            asyncio.sleep(0, result=RuntimeError("x")),
            return_exceptions=True,
        )
        assert isinstance(results[1], RuntimeError)

    asyncio.run(main())


@pytest.mark.asyncio
async def test_reflection_error_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    pages = Pages(errors={"reflection": RuntimeError("refl boom")})
    with caplog.at_level(logging.WARNING):
        out = await render_pages(make_request(plan=BOTH_NEW), pages.deps())

    assert "ReflectionApplication pipeline error: refl boom" in caplog.text
    assert out.reflection_url is None


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed() -> None:
    """Отмена должна дойти до вызывающего кода: иначе `finally` не сработает."""

    class Cancelling(Pages):
        async def find_links(self, *a, **kw):
            raise asyncio.CancelledError()

    pages = Cancelling()
    with pytest.raises(asyncio.CancelledError):
        await render_pages(make_request(early_links=None), pages.deps())


@pytest.mark.asyncio
async def test_base_exception_other_than_cancellation_propagates() -> None:
    """`SystemExit`/`KeyboardInterrupt` не должны превращаться в «ссылок нет»."""

    class Boom(BaseException):
        pass

    class Exploding(Pages):
        async def find_links(self, *a, **kw):
            raise Boom()

    with pytest.raises(Boom):
        await render_pages(make_request(early_links=None), Exploding().deps())
