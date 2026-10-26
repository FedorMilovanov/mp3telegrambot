"""Fit a fresh caption, then persist its cache metadata and durable archive."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from core.publication_status import missing_to_json
from pipelines.archive_stage import ArchiveRequest
from pipelines.caption_inputs import CaptionInputs
from pipelines.caption_stage import CaptionFit, CaptionSource
from pipelines.publication_context_stage import PublicationContext
from pipelines.telegraph_render import RenderedPages
from pipelines.telegraph_repair_stage import RepairReport

logger = logging.getLogger(__name__)

FitFn = Callable[[CaptionSource, dict[str, Any] | None], CaptionFit]
VisibleLengthFn = Callable[[str], int]
PromptVersionFn = Callable[[], str]
SaveFn = Callable[..., Awaitable[Any]]
ArchiveFn = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class FreshCaption:
    """Only the values needed by delivery and optional extras."""

    caption: str
    full_caption: str
    ai_data: dict[str, Any] | None


async def finalize_fresh_caption(
    *,
    source: CaptionSource,
    inputs: CaptionInputs,
    publication: PublicationContext,
    rendered: RenderedPages,
    repair: RepairReport,
    media_id: str,
    duration: int,
    cache_version: str,
    model_name: str,
    prompt_version: PromptVersionFn,
    fit: FitFn,
    visible_length: VisibleLengthFn,
    save: SaveFn,
    archive: ArchiveFn,
) -> FreshCaption:
    """Preserve fresh-path fit, diagnostics, save, and archive ordering."""
    ai_data = inputs.ai_data
    fitted = fit(source, inputs.caption_base)
    caption = fitted.caption
    full_caption = fitted.full_caption
    timestamps_shown = fitted.timestamps_shown
    trim_stage = fitted.trim_stage
    logger.info(
        "Caption visible_len=%d format=%s ts_total=%d ts_cap_limit=%d ts_in_cap=%d stage=%s",
        visible_length(caption),
        publication.format_name,
        publication.timestamps_total,
        source.timestamps_limit(),
        timestamps_shown,
        trim_stage or "none",
    )
    if ai_data:
        ai_data = {
            **ai_data,
            "caption_trim_stage": trim_stage,
            "caption_timestamps_total": publication.timestamps_total,
            "caption_timestamps_shown": timestamps_shown,
        }
        # /pdf reads duration from the cached analysis data.
        if duration and not ai_data.get("duration"):
            ai_data = {**ai_data, "duration": duration}
        await save(
            video_id=media_id,
            url=source.url,
            questions=ai_data.get("questions", []),
            # New pages take priority over legacy compact pages.
            quotes_tg_url=inputs.links.quotes,
            questions_tg_url=inputs.links.questions,
            ai_data=ai_data,
            telegraph_url=source.telegraph_url,
            cache_version=cache_version,
            prompt_version=prompt_version(),
            model_name=model_name,
            terms_tg_url=inputs.links.terms,
            rutube_url=inputs.rutube_url or "",
            vk_url=inputs.vk_url or "",
            study_tg_url=rendered.study_analysis_url or "",
            reflection_tg_url=rendered.reflection_url or "",
            publication_status=inputs.publication.status,
            publication_missing=missing_to_json(inputs.publication.missing),
            publication_warning=inputs.publication.warning,
        )
        await archive(
            ArchiveRequest(
                media_id=media_id,
                source_url=source.url,
                ai_data=ai_data,
                publication=inputs.publication,
                title=source.title,
                performer=source.performer,
                search_title=publication.search_title,
                author=publication.tg_author,
                format_name=publication.format_name,
                duration=duration,
                rutube_url=inputs.rutube_url,
                vk_url=inputs.vk_url,
                synopsis_url=source.telegraph_url,
                study_url=rendered.study_analysis_url or "",
                reflection_url=rendered.reflection_url or "",
                terms_url=rendered.terms_url or "",
                questions_url=rendered.questions_url or "",
                model=model_name,
                prompt_version=prompt_version(),
                prompt_variant=os.getenv("PROMPT_EXPERIMENT_TAG", ""),
            ),
            repair=repair,
        )
    return FreshCaption(
        caption=caption,
        full_caption=full_caption,
        ai_data=ai_data,
    )
