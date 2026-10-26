#!/usr/bin/env python3
"""Что попадает в подпись: ссылки, статус публикации и покрытие таймкодами.

Между «страницы опубликованы» и «подпись собрана» в пайплайне было ~45 строк
решений, которые целиком определяют, что увидит читатель:

* какие ссылки ставить в подпись, когда новых страниц нет (legacy compact), а
  когда они есть (новые приоритетнее);
* скрывать ли Термины: если опубликованы и Разбор, и Размышление, термины уже
  внутри них, и отдельная ссылка ведёт на дубль;
* честный статус публикации — какие страницы ожидались и каких не хватает;
* покрытие материала таймкодами: для длинных записей частичное покрытие
  помечается как `partial`, чтобы архив не выдавал обрезок за полный разбор;
* предупреждение «страница не вышла» попадает в подпись, а не только в лог.

Всё это чистые функции без I/O — поэтому проверяются тестами на таблицах
случаев, а не глазами.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from core.publication_status import PublicationStatus, build_publication_status
from core.timestamp_quality import timestamp_coverage_ratio

#: С какой длительности частичное покрытие таймкодами считается проблемой.
PARTIAL_COVERAGE_MIN_DURATION = 600
#: Ниже этой доли покрытия запись считается частично размеченной.
PARTIAL_COVERAGE_THRESHOLD = 0.75


@dataclass(frozen=True)
class PageLinks:
    """Ссылки для подписи после разрешения приоритетов."""

    quotes: str
    questions: str
    terms: str


@dataclass(frozen=True)
class CaptionInputs:
    """Всё, что нужно стадии подписи, и что уходит в кэш."""

    links: PageLinks
    rutube_url: str
    vk_url: str
    ai_data: dict[str, Any] | None
    publication: PublicationStatus
    caption_base: dict[str, Any] | None


def resolve_page_links(
    *,
    study: str | None = None,
    reflection: str | None = None,
    analytics: str | None = None,
    questions: str | None = None,
    terms: str | None = None,
) -> PageLinks:
    """Новые страницы приоритетнее legacy compact; Термины скрываются при обеих новых."""
    terms_link = "" if (study and reflection) else (terms or "")
    return PageLinks(
        quotes=study or analytics or "",
        questions=reflection or questions or "",
        terms=terms_link,
    )


def with_timestamp_coverage(
    ai_data: dict[str, Any] | None,
    duration: int,
) -> dict[str, Any] | None:
    """Добавить покрытие таймкодами и статус полноты разметки.

    Длинная запись с покрытием ниже 75 % помечается `partial`: иначе архив и
    подпись выдают обрезанный разбор за полный.
    """
    if not ai_data:
        return ai_data
    ratio = timestamp_coverage_ratio(ai_data.get("timestamps", ""), duration)
    status = (
        "partial"
        if (duration >= PARTIAL_COVERAGE_MIN_DURATION and ratio and ratio < PARTIAL_COVERAGE_THRESHOLD)
        else "complete"
    )
    if ai_data.get("timestamp_coverage_warning") and duration >= PARTIAL_COVERAGE_MIN_DURATION:
        status = "partial"
    return {
        **ai_data,
        "timestamp_coverage_ratio": ratio,
        "segments_status": status,
    }


def build_caption_base(
    *,
    ai_data: dict[str, Any] | None,
    degraded: dict[str, Any] | None,
    format_name: str,
    timestamps_total: int,
    timestamp_limit: Callable[[str], int],
    publication: PublicationStatus,
) -> dict[str, Any] | None:
    """Данные подписи: обрезка таймкодов, предупреждение о неполной публикации.

    Обрезка здесь — только **предпросмотр** для подписи; сама подпись режется в
    `pipelines/caption_stage.py`, и метрики оттуда главнее.
    """
    base = ai_data or degraded
    if base and timestamps_total > 0:
        limit = timestamp_limit(format_name)
        if timestamps_total > limit:
            base = {
                **base,
                "_caption_timestamps_trimmed": True,
                "_caption_timestamps_total": timestamps_total,
                "_caption_timestamps_shown": min(timestamps_total, limit),
            }
    if ai_data and publication.warning:
        base = {**(base or ai_data), "_partial_publication_warning": publication.warning}
    return base


def build_caption_inputs(
    *,
    ai_data: dict[str, Any] | None,
    degraded: dict[str, Any] | None,
    duration: int,
    format_name: str,
    timestamps_total: int,
    timestamp_limit: Callable[[str], int],
    study: str | None = None,
    reflection: str | None = None,
    analytics: str | None = None,
    questions: str | None = None,
    terms: str | None = None,
    synopsis: str | None = None,
    plan_study: bool = False,
    plan_reflection: bool = False,
    rutube_url: str | None = None,
    vk_url: str | None = None,
) -> CaptionInputs:
    """Собрать входные данные подписи одним вызовом.

    `expect_*` для статуса считаются от наличия анализа и плана: страница,
    которую не планировали публиковать, не делает публикацию неполной.
    """
    links = resolve_page_links(
        study=study,
        reflection=reflection,
        analytics=analytics,
        questions=questions,
        terms=terms,
    )
    ai_data = with_timestamp_coverage(ai_data, duration)
    publication = build_publication_status(
        synopsis_url=synopsis or "",
        study_url=study or "",
        reflection_url=reflection or "",
        expect_synopsis=bool(ai_data),
        expect_study=bool(ai_data and plan_study),
        expect_reflection=bool(ai_data and plan_reflection),
    )
    base = build_caption_base(
        ai_data=ai_data,
        degraded=degraded,
        format_name=format_name,
        timestamps_total=timestamps_total,
        timestamp_limit=timestamp_limit,
        publication=publication,
    )
    return CaptionInputs(
        links=links,
        rutube_url=rutube_url or "",
        vk_url=vk_url or "",
        ai_data=ai_data,
        publication=publication,
        caption_base=base,
    )


__all__ = [
    "PARTIAL_COVERAGE_MIN_DURATION",
    "PARTIAL_COVERAGE_THRESHOLD",
    "CaptionInputs",
    "PageLinks",
    "build_caption_base",
    "build_caption_inputs",
    "resolve_page_links",
    "with_timestamp_coverage",
]
