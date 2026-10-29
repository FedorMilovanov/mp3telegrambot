"""Fresh caption finalization keeps fit, cache, and archive contracts together."""

from __future__ import annotations
from typing import Any

import asyncio
import logging

import pytest

from core.publication_status import PublicationStatus
from pipelines.archive_stage import ArchiveRequest
from pipelines.caption_inputs import CaptionInputs, PageLinks
from pipelines.caption_stage import CaptionFit, CaptionSource
from pipelines.fresh_caption_stage import FreshCaption, finalize_fresh_caption
from pipelines.publication_context_stage import PublicationContext
from pipelines.publication_plan import PagePlan
from pipelines.telegraph_render import RenderedPages
from pipelines.telegraph_repair_stage import RepairReport


def _source() -> CaptionSource:
    return CaptionSource(
        performer="Performer",
        title="Title",
        duration=1200,
        file_size_mb=12.5,
        bitrate="128k",
        url="https://youtube.com/watch?v=media",
        telegraph_url="https://telegra.ph/synopsis",
        rutube_url="rutube",
        vk_url="vk",
        quotes_link="priority-quotes",
        questions_link="priority-questions",
        terms_link="priority-terms",
        study_link="study",
        reflection_link="reflection",
        format_name="sermon",
    )


def _inputs(ai_data: dict[str, Any] | None, caption_base: dict[str, Any] | None) -> CaptionInputs:
    return CaptionInputs(
        links=PageLinks(
            quotes="priority-quotes",
            questions="priority-questions",
            terms="priority-terms",
        ),
        rutube_url="rutube",
        vk_url="vk",
        ai_data=ai_data,
        publication=PublicationStatus(
            status="partial",
            missing=("reflection",),
            warning="Incomplete publication",
        ),
        caption_base=caption_base,
    )


def _publication() -> PublicationContext:
    return PublicationContext(
        search_title="Search title",
        tg_author="Telegram author",
        questions=[],
        terms_data={},
        format_name="sermon",
        timestamps_total=42,
        plan=PagePlan(
            analytics=False,
            questions=False,
            terms=True,
            study_analysis=True,
            reflection_application=False,
        ),
    )


def _rendered() -> RenderedPages:
    return RenderedPages(
        analytics_url="analytics",
        questions_url="questions",
        terms_url="terms",
        study_analysis_url="study",
        reflection_url="reflection",
    )


def _fit(trim_stage: str = "timestamps_trimmed") -> CaptionFit:
    return CaptionFit(
        caption="caption",
        full_caption="full caption",
        timestamps_total=42,
        timestamps_shown=7,
        trim_stage=trim_stage,
        data={"enriched_by_fit": True},
    )


def _dependencies(events: list[tuple[Any, ...]], *, fitted: CaptionFit | None = None) -> dict[str, Any]:
    fitted = fitted or _fit()

    def fit(source: CaptionSource, caption_base: dict[str, Any] | None) -> CaptionFit:
        events.append(("fit", source, caption_base))
        return fitted

    def visible_length(caption: str) -> int:
        events.append(("measure", caption))
        return 7

    def prompt_version() -> str:
        events.append(("prompt",))
        return f"prompt-{sum(event[0] == 'prompt' for event in events)}"

    async def save(**kwargs: Any) -> None:
        events.append(("save", kwargs))

    async def archive(request: ArchiveRequest, *, repair: RepairReport) -> None:
        events.append(("archive", request, repair))

    return {
        "fit": fit,
        "visible_length": visible_length,
        "prompt_version": prompt_version,
        "save": save,
        "archive": archive,
    }


def _run(
    inputs: CaptionInputs,
    dependencies: dict[str, Any],
    *,
    duration: int = 1200,
    repair: RepairReport | None = None,
) -> FreshCaption:
    return asyncio.run(
        finalize_fresh_caption(
            source=_source(),
            inputs=inputs,
            publication=_publication(),
            rendered=_rendered(),
            repair=repair or RepairReport(),
            media_id="media",
            duration=duration,
            cache_version="cache-v1",
            model_name="model-v1",
            **dependencies,
        )
    )


def test_analysis_fit_cache_and_archive_keep_order_and_values(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROMPT_EXPERIMENT_TAG", "experiment")
    original = {"real_title": "Title", "questions": [{"text": "Q"}]}
    caption_base = {"caption_base": True}
    inputs = _inputs(original, caption_base)
    repair = RepairReport()
    events: list[tuple[Any, ...]] = []

    with caplog.at_level(logging.INFO):
        result = _run(inputs, _dependencies(events), repair=repair)

    assert [event[0] for event in events] == [
        "fit",
        "measure",
        "prompt",
        "save",
        "prompt",
        "archive",
    ]
    assert events[0][2] is caption_base
    assert result.caption == "caption"
    assert result.full_caption == "full caption"
    assert original == {"real_title": "Title", "questions": [{"text": "Q"}]}

    saved = events[3][1]
    saved_ai_data = saved["ai_data"]
    assert result.ai_data is not None
    enriched = result.ai_data
    assert enriched is saved_ai_data
    assert enriched["caption_trim_stage"] == "timestamps_trimmed"
    assert enriched["caption_timestamps_total"] == 42
    assert enriched["caption_timestamps_shown"] == 7
    assert enriched["duration"] == 1200
    assert saved["video_id"] == "media"
    assert saved["url"] == "https://youtube.com/watch?v=media"
    assert saved["questions"] == [{"text": "Q"}]
    assert saved["quotes_tg_url"] == "priority-quotes"
    assert saved["questions_tg_url"] == "priority-questions"
    assert saved["terms_tg_url"] == "priority-terms"
    assert saved["telegraph_url"] == "https://telegra.ph/synopsis"
    assert saved["cache_version"] == "cache-v1"
    assert saved["prompt_version"] == "prompt-1"
    assert saved["model_name"] == "model-v1"
    assert saved["rutube_url"] == "rutube"
    assert saved["vk_url"] == "vk"
    assert saved["study_tg_url"] == "study"
    assert saved["reflection_tg_url"] == "reflection"
    assert saved["publication_status"] == "partial"
    assert saved["publication_missing"] == '["reflection"]'
    assert saved["publication_warning"] == "Incomplete publication"

    archive_request = events[5][1]
    assert events[5][2] is repair
    assert archive_request.ai_data is enriched
    assert archive_request.media_id == "media"
    assert archive_request.source_url == "https://youtube.com/watch?v=media"
    assert archive_request.publication is inputs.publication
    assert archive_request.title == "Title"
    assert archive_request.performer == "Performer"
    assert archive_request.search_title == "Search title"
    assert archive_request.author == "Telegram author"
    assert archive_request.format_name == "sermon"
    assert archive_request.duration == 1200
    assert archive_request.rutube_url == "rutube"
    assert archive_request.vk_url == "vk"
    assert archive_request.synopsis_url == "https://telegra.ph/synopsis"
    assert archive_request.study_url == "study"
    assert archive_request.reflection_url == "reflection"
    assert archive_request.terms_url == "terms"
    assert archive_request.questions_url == "questions"
    assert archive_request.model == "model-v1"
    assert archive_request.prompt_version == "prompt-2"
    assert archive_request.prompt_variant == "experiment"
    assert (
        "Caption visible_len=7 format=sermon ts_total=42 ts_cap_limit=30 ts_in_cap=7 stage=timestamps_trimmed"
    ) in caplog.text


def test_missing_analysis_fits_degraded_caption_without_persistence(caplog: pytest.LogCaptureFixture) -> None:
    degraded = {"_ai_unavailable_warning": "No AI"}
    events: list[tuple[Any, ...]] = []

    with caplog.at_level(logging.INFO):
        result = _run(
            _inputs(None, degraded),
            _dependencies(events, fitted=_fit(trim_stage="")),
        )

    assert [event[0] for event in events] == ["fit", "measure"]
    assert events[0][2] is degraded
    assert result.caption == "caption"
    assert result.full_caption == "full caption"
    assert result.ai_data is None
    assert "ts_in_cap=7 stage=none" in caplog.text


def test_existing_duration_is_not_overwritten() -> None:
    events: list[tuple[Any, ...]] = []

    result = _run(_inputs({"duration": 999}, {}), _dependencies(events))

    saved = events[3][1]
    assert saved["ai_data"]["duration"] == 999
    assert events[5][1].duration == 1200
    assert result.ai_data is saved["ai_data"]


def test_cache_save_failure_propagates_and_skips_archive() -> None:
    events: list[tuple[Any, ...]] = []
    dependencies = _dependencies(events)

    async def broken_save(**kwargs: Any) -> None:
        events.append(("save", kwargs))
        raise RuntimeError("cache unavailable")

    dependencies["save"] = broken_save

    with pytest.raises(RuntimeError, match="cache unavailable"):
        _run(_inputs({"real_title": "Title"}, {}), dependencies)

    assert [event[0] for event in events] == ["fit", "measure", "prompt", "save"]
