#!/usr/bin/env python3
"""Архив публикации: нормализация записи, порядок сохранения, сбои без потери MP3."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from core.publication_status import PublicationStatus
from pipelines.archive_stage import (
    ArchiveDeps,
    ArchiveRequest,
    archive_pages,
    build_archive_record,
    production_deps,
)
from pipelines.telegraph_repair_stage import PageRepair, RepairReport

URL = "https://youtu.be/vid1"
SYNOPSIS = "https://telegra.ph/konspekt"
STUDY = "https://telegra.ph/razbor"
REFLECTION = "https://telegra.ph/razmyshlenie"
TERMS = "https://telegra.ph/terminy"
QUESTIONS = "https://telegra.ph/voprosy"

PARTIAL = PublicationStatus(
    status="partial",
    missing=("reflection",),
    warning="Частичный разбор: не созданы размышление.",
)


def make_request(**overrides: Any) -> ArchiveRequest:
    base: dict[str, Any] = {
        "media_id": "vid1",
        "source_url": URL,
        "ai_data": {"real_title": "Тема", "real_author": "Автор"},
        "publication": PARTIAL,
        "title": "Исходное название",
        "performer": "Исходный исполнитель",
        "search_title": "Поисковое название",
        "author": "Telegram-автор",
        "format_name": "sermon",
        "duration": 1200,
        "rutube_url": "https://rutube.ru/video/1/",
        "vk_url": "https://vk.com/video1",
        "synopsis_url": SYNOPSIS,
        "study_url": STUDY,
        "reflection_url": REFLECTION,
        "terms_url": TERMS,
        "questions_url": QUESTIONS,
        "model": "gemini-test",
        "prompt_version": "prompt-v1",
        "prompt_variant": "control",
    }
    base.update(overrides)
    return ArchiveRequest(**base)


class Recorder:
    """Фейковые операции записи с порядком вызовов и точкой отказа."""

    def __init__(self, fail_at: str = "") -> None:
        self.fail_at = fail_at
        self.calls: list[tuple[str, Any]] = []
        self.record: dict[str, Any] | None = None
        self.repair_kwargs: dict[str, Any] | None = None
        self.segment_kwargs: dict[str, Any] | None = None

    def deps(self) -> ArchiveDeps:
        return ArchiveDeps(
            save_record=self.save_record,
            update_repair=self.update_repair,
            save_segments=self.save_segments,
        )

    def _fail(self, operation: str) -> None:
        if self.fail_at == operation:
            raise RuntimeError(f"{operation}: диск недоступен")

    async def save_record(self, record: dict[str, Any]) -> None:
        self.calls.append(("record", record["video_id"]))
        self.record = record
        self._fail("record")

    async def update_repair(self, media_id: str, **kwargs: Any) -> None:
        self.calls.append(("repair", media_id))
        self.repair_kwargs = kwargs
        self._fail("repair")

    async def save_segments(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(("segments", kwargs["video_id"]))
        self.segment_kwargs = kwargs
        self._fail("segments")
        return {"json": "segments.json", "md": "segments.md", "count": "2"}

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


# ── Чистая сборка записи ─────────────────────────────────────────────────


def test_record_contains_publication_quality_caption_and_all_links() -> None:
    ai = {
        "real_title": "12: Название.",
        "real_author": "Иван  Петров",
        "real_event": "Конференция",
        "hashtags": ["Вера", "Надежда"],
        "key_categories": ["soteriology"],
        "terms_data": {"scripture": ["Рим. 8:28||тема", "Мф. 7:21"]},
        "quality_warnings": ["проверить цитату"],
        "timestamp_coverage_warning": "покрыта только половина",
        "timestamp_coverage_ratio": 0.5,
        "segments_status": "partial",
        "caption_trim_stage": "timestamps",
        "caption_timestamps_total": 40,
        "caption_timestamps_shown": 30,
        "timestamps": "00:00 Начало\n10:00 Конец",
    }

    record = build_archive_record(make_request(ai_data=ai))

    assert record["video_id"] == "vid1"
    assert record["source_url"] == URL
    assert record["title"] == "Название"
    assert record["author"] == "Иван Петров"
    assert record["event"] == "Конференция"
    assert record["format"] == "sermon"
    assert record["duration"] == 1200
    assert record["synopsis_url"] == SYNOPSIS
    assert record["study_url"] == STUDY
    assert record["reflection_url"] == REFLECTION
    assert record["terms_url"] == TERMS
    assert record["questions_url"] == QUESTIONS
    assert record["rutube_url"].startswith("https://rutube.ru/")
    assert record["vk_url"] == "https://vk.com/video1"
    assert record["hashtags"] == ["Вера", "Надежда"]
    assert record["key_categories"] == ["soteriology"]
    assert record["scripture_refs"] == ["Рим. 8:28", "Мф. 7:21"]
    assert record["publication_status"] == "partial"
    assert record["publication_missing"] == ["reflection"]
    assert record["publication_warning"] == PARTIAL.warning
    assert "проверить цитату" in record["quality_warnings"]
    assert "timestamp: покрыта только половина" in record["quality_warnings"]
    assert "segments: partial timestamp coverage" in record["quality_warnings"]
    assert record["timestamp_coverage_ratio"] == 0.5
    assert record["segments_status"] == "partial"
    assert record["caption_trim_stage"] == "timestamps"
    assert record["caption_timestamps_total"] == 40
    assert record["caption_timestamps_shown"] == 30
    assert record["model"] == "gemini-test"
    assert record["prompt_version"] == "prompt-v1"
    assert record["prompt_variant"] == "control"


@pytest.mark.parametrize(
    "ai, search_title, author, expected_title, expected_author",
    [
        (
            {"real_title": "Из анализа", "real_author": "Автор анализа"},
            "Поисковое",
            "Telegram",
            "Из анализа",
            "Автор анализа",
        ),
        ({}, "Поисковое", "Telegram", "Поисковое", "Telegram"),
        (
            {"real_title": " ", "real_author": " "},
            "",
            "",
            "Исходное название",
            "Исходный исполнитель",
        ),
    ],
)
def test_metadata_fallbacks(
    ai: dict[str, Any],
    search_title: str,
    author: str,
    expected_title: str,
    expected_author: str,
) -> None:
    original = dict(ai)
    request = make_request(ai_data=ai, search_title=search_title, author=author)

    record = build_archive_record(request)

    assert record["title"] == expected_title
    assert record["author"] == expected_author
    assert ai == original


# ── Асинхронная запись ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_order_record_repair_then_segments(
    caplog: pytest.LogCaptureFixture,
) -> None:
    recorder = Recorder()
    repair = RepairReport(
        results=[
            PageRepair(url=STUDY, ok=True, changed=True),
            PageRepair(url=REFLECTION, ok=False, error="editPage упал"),
        ]
    )
    request = make_request(
        ai_data={
            "real_title": "Тема",
            "timestamps": "00:00 Начало",
            "segments_status": "partial",
            "timestamp_coverage_ratio": 0.25,
        }
    )

    with caplog.at_level(logging.INFO):
        result = await archive_pages(request, repair=repair, deps=recorder.deps())

    assert result.ok is True
    assert result.saved is True
    assert result.error == ""
    assert result.record is recorder.record
    assert result.segment_export["count"] == "2"
    assert recorder.names() == ["record", "repair", "segments"]
    assert recorder.repair_kwargs == {
        "changed_pages": 1,
        "errors": ["editPage упал"],
    }
    assert recorder.segment_kwargs == {
        "video_id": "vid1",
        "title": "Тема",
        "author": "Telegram-автор",
        "timestamps": "00:00 Начало",
        "duration": 1200,
        "format_name": "sermon",
        "segments_status": "partial",
        "timestamp_coverage_ratio": 0.25,
    }
    assert "Generated pages archive saved for vid1; segments=2" in caplog.text


@pytest.mark.asyncio
async def test_empty_repair_report_does_not_update_metadata() -> None:
    recorder = Recorder()

    result = await archive_pages(make_request(), repair=RepairReport(), deps=recorder.deps())

    assert result.ok is True
    assert recorder.names() == ["record", "segments"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at", ["record", "repair", "segments"])
async def test_every_io_failure_is_non_fatal_and_reports_progress(
    fail_at: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    recorder = Recorder(fail_at=fail_at)
    repair = RepairReport(results=[PageRepair(url=STUDY, ok=True, changed=True)])

    with caplog.at_level(logging.WARNING):
        result = await archive_pages(make_request(), repair=repair, deps=recorder.deps())

    assert result.ok is False
    assert result.error == f"{fail_at}: диск недоступен"
    assert result.record is not None
    assert result.saved is (fail_at != "record")
    assert result.segment_export == {}
    assert "Generated pages archive save failed for vid1" in caplog.text
    if fail_at == "record":
        assert recorder.names() == ["record"]
    elif fail_at == "repair":
        assert recorder.names() == ["record", "repair"]
    else:
        assert recorder.names() == ["record", "repair", "segments"]


@pytest.mark.asyncio
async def test_default_dependencies_and_no_repair_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipelines import archive_stage as stage

    recorder = Recorder()
    monkeypatch.setattr(stage, "production_deps", recorder.deps)

    result = await archive_pages(make_request())

    assert result.ok is True
    assert recorder.names() == ["record", "segments"]


def test_production_deps_are_the_real_archive_operations() -> None:
    from core.generated_pages import (
        asave_generated_page_record,
        asave_segment_plan_export,
        aupdate_generated_page_repair_status,
    )

    deps = production_deps()

    assert deps.save_record is asave_generated_page_record
    assert deps.update_repair is aupdate_generated_page_repair_status
    assert deps.save_segments is asave_segment_plan_export
