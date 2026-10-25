#!/usr/bin/env python3
"""Cache-hit подготовка ссылок и подписи после получения аудио.

Сохранённые Telegraph-ссылки читаются отдельно и в прежней точке оркестратора.
После аудио стадия решает, нужны ли новые alt-links: если в кэше есть хотя бы
одна ссылка RuTube/VK, внешний поиск не выполняется. Затем используется общая
``caption_stage`` — никакой второй копии алгоритма обрезки подписи.

Поиск ссылок остаётся критичной операцией, как раньше: его ошибка не маскируется
под пустой результат и проходит в общий обработчик ``process_single_video``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from converters.md_telegraph import visible_length
from pipelines.caption_stage import CaptionSource, fit_caption

logger = logging.getLogger(__name__)

LinksFn = Callable[..., Awaitable[dict[str, Any] | None]]
SearchTitleFn = Callable[[dict[str, Any], str], str]


@dataclass(frozen=True)
class CachedPageLinks:
    """Telegraph-ссылки из записи кэша с историческим скрытием дубля Терминов."""

    synopsis_url: str
    quotes_url: str
    questions_url: str
    terms_url: str
    study_url: str
    reflection_url: str


@dataclass(frozen=True)
class CachedPresentationRequest:
    """Данные, появившиеся к моменту завершения cache-hit аудио стадии."""

    links: CachedPageLinks
    ai_data: dict[str, Any]
    full_title: str
    channel_name: str
    title: str
    performer: str
    duration: int
    file_size_mb: float
    bitrate: str
    source_url: str
    cached_rutube_url: str = ""
    cached_vk_url: str = ""


@dataclass(frozen=True)
class CachedCaption:
    """Обе формы подписи, которые нужны cache-hit отправке."""

    caption: str
    full_caption: str


@dataclass(frozen=True)
class CachedPresentationDeps:
    """Внешний поиск; Telegraph и caption algorithm здесь не выполняют I/O."""

    find_links: LinksFn
    build_search_title: SearchTitleFn


def production_deps() -> CachedPresentationDeps:
    """Боевой поиск импортируется только при реальном вызове стадии."""
    from services.search import _build_search_title, find_alternative_links

    return CachedPresentationDeps(
        find_links=find_alternative_links,
        build_search_title=_build_search_title,
    )


def cached_page_links(cached: dict[str, Any]) -> CachedPageLinks:
    """Прочитать ссылки без мутации записи и без изменения required-key policy."""
    study = cached.get("study_tg_url") or ""
    reflection = cached.get("reflection_tg_url") or ""
    return CachedPageLinks(
        synopsis_url=cached["telegraph_url"],
        quotes_url=cached.get("quotes_tg_url", ""),
        questions_url=cached.get("questions_tg_url", ""),
        terms_url="" if (study and reflection) else (cached.get("terms_tg_url") or ""),
        study_url=study,
        reflection_url=reflection,
    )


async def prepare_cached_presentation(
    request: CachedPresentationRequest,
    deps: CachedPresentationDeps | None = None,
) -> CachedCaption:
    """Разрешить alt-links и собрать cache-hit подпись общей стадией."""
    deps = deps or production_deps()

    if request.cached_rutube_url or request.cached_vk_url:
        rutube_url = request.cached_rutube_url
        vk_url = request.cached_vk_url
        logger.info("Кэш alt-links: rutube=%s vk=%s", bool(rutube_url), bool(vk_url))
    else:
        search_title = deps.build_search_title(request.ai_data, request.full_title)
        alt_links = await deps.find_links(
            search_title,
            request.channel_name,
            request.duration,
            ai_data=request.ai_data,
            fallback_title=request.full_title,
        )
        rutube_url = (alt_links or {}).get("rutube") or ""
        vk_url = (alt_links or {}).get("vk") or ""

    source = CaptionSource(
        performer=request.performer,
        title=request.title,
        duration=request.duration,
        file_size_mb=request.file_size_mb,
        bitrate=request.bitrate,
        url=request.source_url,
        telegraph_url=request.links.synopsis_url,
        rutube_url=rutube_url,
        vk_url=vk_url,
        quotes_link=request.links.quotes_url,
        questions_link=request.links.questions_url,
        terms_link=request.links.terms_url,
        study_link=request.links.study_url,
        reflection_link=request.links.reflection_url,
        format_name=request.ai_data.get("format", "other"),
    )
    fit = fit_caption(source, request.ai_data or None)
    logger.info(
        "Кэш caption visible_len=%d format=%s ts_total=%d ts_in_cap=%d stage=%s",
        visible_length(fit.caption),
        source.format_name,
        fit.timestamps_total,
        fit.timestamps_shown,
        fit.trim_stage or "none",
    )
    return CachedCaption(caption=fit.caption, full_caption=fit.full_caption)


__all__ = [
    "CachedCaption",
    "CachedPageLinks",
    "CachedPresentationDeps",
    "CachedPresentationRequest",
    "cached_page_links",
    "prepare_cached_presentation",
    "production_deps",
]
