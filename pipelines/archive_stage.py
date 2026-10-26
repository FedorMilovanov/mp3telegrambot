#!/usr/bin/env python3
"""Долговременный архив опубликованных страниц и плана сегментов.

Операционный кэш может истечь или быть инвалидирован новой версией промпта;
архив должен остаться: SQLite/JSONL/Markdown для страниц, отдельные JSON/MD
для сегментов и результат автопочинки. Раньше сборка этой записи занимала
большой блок внутри `process_single_video` и проверялась только поиском строк
в исходнике.

Граница стадии намеренно некритичная: сбой архива логируется и возвращается в
`ArchiveResult.error`, но не отменяет отправку уже готового MP3 пользователю.
Порядок записи сохранён: страница → статус починки (если был проход) → сегменты.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from core.generated_pages import (
    build_generated_page_record,
    collect_quality_warnings,
    extract_scripture_refs,
    timestamp_coverage_archive_fields,
)
from core.publication_status import PublicationStatus
from core.text_utils import (
    _scrub_inline,
    normalize_author_name,
    normalize_common_typos,
    normalize_title_text,
)
from pipelines.telegraph_repair_stage import RepairReport

logger = logging.getLogger(__name__)

SaveRecordFn = Callable[[dict[str, Any]], Awaitable[None]]
UpdateRepairFn = Callable[..., Awaitable[None]]
SaveSegmentsFn = Callable[..., Awaitable[dict[str, str]]]


@dataclass(frozen=True)
class ArchiveRequest:
    """Входные данные архива; стадия не читает замыкания пайплайна."""

    media_id: str
    source_url: str
    ai_data: dict[str, Any]
    publication: PublicationStatus
    title: str
    performer: str
    search_title: str = ""
    author: str = ""
    format_name: str = ""
    duration: int = 0
    rutube_url: str = ""
    vk_url: str = ""
    synopsis_url: str = ""
    study_url: str = ""
    reflection_url: str = ""
    terms_url: str = ""
    questions_url: str = ""
    model: str = ""
    prompt_version: str = ""
    prompt_variant: str = ""


@dataclass(frozen=True)
class ArchiveDeps:
    """Три операции записи; в тестах заменяются детерминированными фейками."""

    save_record: SaveRecordFn
    update_repair: UpdateRepairFn
    save_segments: SaveSegmentsFn


@dataclass(frozen=True)
class ArchiveResult:
    """Что удалось сохранить и где остановилась некритичная стадия."""

    record: dict[str, Any] | None = None
    segment_export: dict[str, str] = field(default_factory=dict)
    saved: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        """Вся стадия завершилась без ошибки (сегментов может быть ноль)."""
        return self.saved and not self.error


def production_deps() -> ArchiveDeps:
    """Боевые операции архива из `core.generated_pages`."""
    from core.generated_pages import (
        asave_generated_page_record,
        asave_segment_plan_export,
        aupdate_generated_page_repair_status,
    )

    return ArchiveDeps(
        save_record=asave_generated_page_record,
        update_repair=aupdate_generated_page_repair_status,
        save_segments=asave_segment_plan_export,
    )


def build_archive_record(request: ArchiveRequest) -> dict[str, Any]:
    """Собрать нормализованную запись без I/O.

    Приоритет метаданных тот же, что был в пайплайне: результат анализа,
    затем поисковый/Telegram-вариант, затем исходные название и исполнитель.
    """
    ai = request.ai_data
    title_fallback = request.search_title or request.title
    author_fallback = request.author or request.performer
    coverage_ratio, segments_status = timestamp_coverage_archive_fields(ai)
    return build_generated_page_record(
        video_id=request.media_id,
        source_url=request.source_url,
        title=normalize_common_typos(normalize_title_text(ai.get("real_title") or title_fallback) or title_fallback),
        author=normalize_common_typos(
            normalize_author_name(ai.get("real_author") or author_fallback) or author_fallback
        ),
        event=normalize_common_typos(_scrub_inline(ai.get("real_event", ""))),
        format_name=request.format_name,
        duration=request.duration,
        youtube_url=request.source_url,
        rutube_url=request.rutube_url,
        vk_url=request.vk_url,
        synopsis_url=request.synopsis_url,
        study_url=request.study_url,
        reflection_url=request.reflection_url,
        terms_url=request.terms_url,
        questions_url=request.questions_url,
        hashtags=ai.get("hashtags", []),
        key_categories=ai.get("key_categories", []),
        scripture_refs=extract_scripture_refs(ai),
        publication_status=request.publication.status,
        publication_missing=request.publication.missing,
        publication_warning=request.publication.warning,
        quality_warnings=collect_quality_warnings(ai),
        timestamp_coverage_ratio=coverage_ratio,
        segments_status=segments_status,
        caption_trim_stage=ai.get("caption_trim_stage", ""),
        caption_timestamps_total=ai.get("caption_timestamps_total", 0),
        caption_timestamps_shown=ai.get("caption_timestamps_shown", 0),
        model=request.model,
        prompt_version=request.prompt_version,
        prompt_variant=request.prompt_variant,
    )


async def archive_pages(
    request: ArchiveRequest,
    *,
    repair: RepairReport | None = None,
    deps: ArchiveDeps | None = None,
) -> ArchiveResult:
    """Сохранить страницу, статус починки и сегменты; наружу исключения не уходят."""
    deps = deps or production_deps()
    record: dict[str, Any] | None = None
    saved = False
    try:
        record = build_archive_record(request)
        await deps.save_record(record)
        saved = True
        if repair is not None and repair.results:
            await deps.update_repair(
                request.media_id,
                changed_pages=repair.changed_count,
                errors=repair.errors,
            )
        segment_export = await deps.save_segments(
            video_id=request.media_id,
            title=record.get("title", ""),
            author=record.get("author", ""),
            timestamps=request.ai_data.get("timestamps", ""),
            duration=request.duration,
            format_name=request.format_name,
            segments_status=request.ai_data.get("segments_status", "complete"),
            timestamp_coverage_ratio=request.ai_data.get("timestamp_coverage_ratio", 0.0),
        )
    except Exception as err:
        logger.warning("Generated pages archive save failed for %s: %s", request.media_id, err)
        return ArchiveResult(record=record, saved=saved, error=str(err))

    logger.info(
        "Generated pages archive saved for %s; segments=%s",
        request.media_id,
        segment_export.get("count"),
    )
    return ArchiveResult(record=record, segment_export=segment_export, saved=True)


__all__ = [
    "ArchiveDeps",
    "ArchiveRequest",
    "ArchiveResult",
    "archive_pages",
    "build_archive_record",
    "production_deps",
]
