#!/usr/bin/env python3
"""Стадия рендера: какие Telegraph-страницы выпустить и что вернулось.

Раньше это был клубок из пяти closure-фабрик, последовательного прохода для
Study/Reflection и отдельного opt-in «объединённого» режима. Проверить его было
нечем — нужны настоящие Telegraph и Gemini. Здесь решение отделено от I/O, а
все шесть публикующих функций передаются как зависимости.

Порядок и исключения, которые нельзя менять незаметно:

* Study Analysis заменяет Аналитику, Reflection заменяет Вопросы;
* если включены обе новые страницы, Термины не публикуются (они уже внутри);
* Study и Reflection по умолчанию идут **последовательно**: на free tier
  параллельный запуск даёт каскадные 503, а объединённый режим — opt-in;
* сбой одной страницы не отменяет остальные: каждая ошибка логируется, а
  страница остаётся пустой;
* если Reflection вернул компактную замену (URL «Вопросы»), он переезжает в
  `questions`, иначе навигация назовёт Вопросами чужую страницу.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, cast

from pipelines.publication_plan import PagePlan

logger = logging.getLogger(__name__)

#: Префикс настоящей страницы «Размышление» в telegra.ph.
REFLECTION_PREFIX = "razmyshlenie-"
#: Префикс страницы «Вопросы» — так выглядит компактная замена.
QUESTIONS_PREFIX = "voprosy-"


class CombinedResult(Protocol):
    """Что вернул объединённый режим Study+Reflection."""

    study_url: str | None
    reflection_url: str | None
    study_page_type: str
    reflection_page_type: str
    mode: str


#: Публикующая функция страницы: возвращает URL или None.
PageFn = Callable[..., Awaitable[str | None]]
#: Объединённая публикация Study+Reflection.
CombinedFn = Callable[..., Awaitable[CombinedResult | None]]
#: Поиск альтернативных ссылок: возвращает `{"rutube": ..., "vk": ...}`.
LinksFn = Callable[..., Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True)
class RenderRequest:
    """Всё, что нужно для рендера; замыканий нет."""

    ai_data: dict[str, Any] | None
    questions: list[Any]
    terms_data: dict[str, Any]
    plan: PagePlan
    search_title: str
    author: str
    url: str
    channel_name: str
    full_title: str
    duration: int
    media_id: str
    rutube_url: str = ""
    vk_url: str = ""
    synopsis_outline: list[dict[str, Any]] | None = None
    early_links: dict[str, Any] | None = None
    combined_enabled: bool = False


@dataclass(frozen=True)
class RenderedPages:
    """Что опубликовано и что с этим делать дальше."""

    analytics_url: str | None = None
    questions_url: str | None = None
    terms_url: str | None = None
    study_analysis_url: str | None = None
    reflection_url: str | None = None
    alt_links: dict[str, Any] = field(default_factory=dict)
    reflection_moved_to_questions: bool = False

    def as_nav_pages(self, synopsis_url: str) -> list[tuple[str, str]]:
        """Пары «подпись → URL» для блока навигации, без пустых."""
        pages = [
            ("Конспект", synopsis_url or ""),
            ("Разбор материала", self.study_analysis_url or ""),
            ("Размышление и применение", self.reflection_url or ""),
        ]
        return [(label, url) for label, url in pages if url]


@dataclass(frozen=True)
class RenderDeps:
    """Шесть публикующих функций и поиск ссылок — больше стадии ничего не нужно."""

    analytics: PageFn
    questions: PageFn
    terms: PageFn
    study_analysis: PageFn
    reflection: PageFn
    combined: CombinedFn
    find_links: LinksFn


def production_deps() -> RenderDeps:
    """Боевые зависимости; импорт внутри — чтобы модуль не тянул сеть при импорте."""
    from services.search import find_alternative_links
    from services.telegraph_pages import (
        create_telegraph_analytics,
        create_telegraph_questions,
        create_telegraph_reflection_application,
        create_telegraph_study_analysis,
        create_telegraph_study_reflection_combined,
        create_telegraph_terms,
    )

    return RenderDeps(
        analytics=create_telegraph_analytics,
        questions=create_telegraph_questions,
        terms=create_telegraph_terms,
        study_analysis=create_telegraph_study_analysis,
        reflection=create_telegraph_reflection_application,
        # `services/` вне типизированного среза, поэтому результат объединённой
        # публикации приводим к протоколу явно, а не «на слово».
        combined=cast(CombinedFn, create_telegraph_study_reflection_combined),
        find_links=find_alternative_links,
    )


def is_reflection_fallback(url: str | None) -> bool:
    """Похож ли URL на компактную замену «Вопросы» вместо «Размышления».

    Настоящее Размышление всегда начинается с `razmyshlenie-`. Live-аудит 39
    страниц нашёл 2 случая из 13 (15 %), когда подмена выдавалась за
    Размышление.
    """
    if not url:
        return False
    path = url.replace("https://telegra.ph/", "").lower()
    if path.startswith(REFLECTION_PREFIX):
        return False
    if path.startswith(QUESTIONS_PREFIX):
        return True
    return True


def _as_url(value: Any) -> str | None:
    """Привести результат страницы к `str | None` без утечки `Any`."""
    if isinstance(value, str) and value:
        return value
    return None


def _unwrap(result: Any, label: str) -> str | None:
    """Развернуть результат `asyncio.gather(return_exceptions=True)`."""
    if isinstance(result, BaseException):
        logger.warning("%s error: %s", label, result, exc_info=result)
        return None
    return _as_url(result)


async def _alt_links(
    request: RenderRequest,
    deps: RenderDeps,
) -> dict[str, Any]:
    """Ссылки RuTube/VK: уже найденные не ищем повторно."""
    if isinstance(request.early_links, dict):
        return request.early_links
    if not request.ai_data:
        return {"rutube": None, "vk": None}
    try:
        found = await deps.find_links(
            request.search_title,
            request.channel_name,
            request.duration,
            ai_data=request.ai_data,
            fallback_title=request.full_title,
        )
    except Exception as err:  # поиск ссылок не критичен для публикации
        logger.warning("find_alternative_links error: %s", err, exc_info=err)
        return {"rutube": None, "vk": None}
    return found or {"rutube": None, "vk": None}


async def _render_combined(
    request: RenderRequest,
    deps: RenderDeps,
) -> tuple[str | None, str | None]:
    """Opt-in: Study и Reflection одной публикацией. При сбое — (None, None)."""
    try:
        logger.info("Study+Reflection combined: enabled by COMBINE_STUDY_REFLECTION=1")
        ai_data = request.ai_data or {}
        result = await deps.combined(
            ai_data,
            request.questions,
            request.search_title,
            request.author,
            yt_url=request.url,
            study_compact_fn=lambda: deps.analytics(ai_data, request.search_title, request.author, request.url),
            reflection_compact_fn=lambda: deps.questions(request.questions, request.search_title, request.author),
            rutube_url=request.rutube_url,
            vk_url=request.vk_url,
            synopsis_outline=request.synopsis_outline,
            duration=request.duration,
        )
        if not result:
            return None, None
        logger.info(
            "Study+Reflection combined result: mode=%s study_type=%s reflection_type=%s",
            result.mode,
            result.study_page_type,
            result.reflection_page_type,
        )
        return result.study_url, result.reflection_url
    except Exception as err:
        logger.warning("Study+Reflection combined pipeline error: %s", err)
        return None, None


async def _render_study_or_reflection(
    request: RenderRequest,
    deps: RenderDeps,
    *,
    combined: bool,
) -> tuple[str | None, str | None]:
    """Study и Reflection: последовательно, с fallback на отдельные вызовы."""
    plan = request.plan
    study: str | None = None
    reflection: str | None = None

    if combined and plan.study_analysis and plan.reflection_application:
        study, reflection = await _render_combined(request, deps)

    if not study and plan.study_analysis:
        ai_data = request.ai_data or {}
        try:
            logger.info("StudyAnalysis: enabled")
            study = await deps.study_analysis(
                ai_data,
                request.search_title,
                request.author,
                yt_url=request.url,
                compact_fn=lambda: deps.analytics(ai_data, request.search_title, request.author, request.url),
                rutube_url=request.rutube_url,
                vk_url=request.vk_url,
                synopsis_outline=request.synopsis_outline,
                duration=request.duration,
                video_id=request.media_id,
            )
        except Exception as err:
            logger.warning("StudyAnalysis pipeline error: %s", err)
            study = None

    if not reflection and plan.reflection_application:
        try:
            logger.info("ReflectionApplication: enabled")
            reflection = await deps.reflection(
                request.questions,
                request.search_title,
                request.author,
                ai_data=request.ai_data or {},
                duration=request.duration,
                yt_url=request.url,
                compact_fn=lambda: deps.questions(request.questions, request.search_title, request.author),
                rutube_url=request.rutube_url,
                vk_url=request.vk_url,
                synopsis_outline=request.synopsis_outline,
                video_id=request.media_id,
            )
        except Exception as err:
            logger.warning("ReflectionApplication pipeline error: %s", err)
            reflection = None

    return study, reflection


async def render_pages(
    request: RenderRequest,
    deps: RenderDeps | None = None,
) -> RenderedPages:
    """Опубликовать все включённые страницы.

    Аналитика/Вопросы/Термины идут параллельно, Study/Reflection —
    последовательно (free tier не выдерживает параллельных крупных запросов).
    """
    deps = deps or production_deps()
    plan = request.plan
    ai_data = request.ai_data or {}
    questions_data = request.questions or []
    terms_data = request.terms_data or {}

    async def _analytics() -> str | None:
        if plan.study_analysis or not plan.analytics:
            return None
        return await deps.analytics(ai_data, request.search_title, request.author, request.url)

    async def _questions() -> str | None:
        if plan.reflection_application or not plan.questions:
            return None
        return await deps.questions(questions_data, request.search_title, request.author)

    async def _terms() -> str | None:
        if plan.study_analysis and plan.reflection_application:
            return None
        if not plan.terms:
            return None
        return await deps.terms(terms_data, request.search_title, request.author, request.url)

    alt_links, analytics, questions, terms = await asyncio.gather(
        _alt_links(request, deps),
        _analytics(),
        _questions(),
        _terms(),
        return_exceptions=True,
    )

    if isinstance(alt_links, BaseException):
        # Сюда попадают только отмена и BaseException-ы мимо `except Exception`
        # в `_alt_links`. Их не глотаем: иначе `finally` вызывающего кода не
        # узнает об отмене, а SystemExit превратится в «ссылок нет».
        raise alt_links
    resolved_links = alt_links

    study, reflection = await _render_study_or_reflection(request, deps, combined=request.combined_enabled)

    moved = False
    if reflection and is_reflection_fallback(reflection):
        logger.warning(
            "Navigation v10: reflection URL выглядит как fallback Вопросы (%s) — перемещаю в questions, из nav убираю",
            reflection,
        )
        questions = questions or reflection
        reflection = None
        moved = True

    return RenderedPages(
        analytics_url=_unwrap(analytics, "analytics"),
        questions_url=_unwrap(questions, "questions"),
        terms_url=_unwrap(terms, "terms"),
        study_analysis_url=_unwrap(study, "study_analysis"),
        reflection_url=_unwrap(reflection, "reflection_application"),
        alt_links=resolved_links,
        reflection_moved_to_questions=moved,
    )


__all__ = [
    "QUESTIONS_PREFIX",
    "REFLECTION_PREFIX",
    "RenderDeps",
    "RenderRequest",
    "RenderedPages",
    "is_reflection_fallback",
    "production_deps",
    "render_pages",
]
