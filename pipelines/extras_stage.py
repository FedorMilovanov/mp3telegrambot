#!/usr/bin/env python3
"""Необязательные видео-дополнения после отправки основного MP3.

Shorts, Clips, Montage и Highlights сохраняют исторический порядок. Montage и
Highlights по-прежнему делят один Gemini text-only запрос. В ENG-режимах стадия
сначала получает готовый LiveDub-файл, чтобы нарезки не использовали молча
английский оригинал.

Каждый необязательный рендер изолирован: его ошибка логируется и не отменяет
остальные дополнения или уже отправленный MP3. Отмена задачи, чтение настроек
и получение LiveDub остаются критичными, как и в исходном оркестраторе.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

ENG_MODES = frozenset({"eng", "eng_fast", "eng_fast_qa"})

SettingFn = Callable[[str], Awaitable[Any]]
StageFn = Callable[..., Awaitable[Any]]
GetLiveDubPathFn = Callable[[], Path | None]
SendLiveDubFn = Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class ExtrasRequest:
    """Входные данные и per-request границы LiveDub без скрытых замыканий стадии."""

    source_url: str
    media_id: str
    mp3_path: Path
    title: str
    performer: str
    duration: int
    ai_data: dict[str, Any] | None
    update: Any
    get_livedub_path: GetLiveDubPathFn
    send_livedub: SendLiveDubFn
    rutube_url: str = ""
    vk_url: str = ""
    used_audio_part: Any = None
    used_client: Any = None
    workdir: Path | None = None
    user_mode: str = "rus"
    live_dub_pending: bool = False


@dataclass(frozen=True)
class ExtrasDeps:
    """Внешние зависимости; тесты не запускают Gemini, FFmpeg или Telegram."""

    get_setting: SettingFn
    run_shorts: StageFn
    run_clips: StageFn
    create_candidates: StageFn
    run_montage: StageFn
    run_highlights: StageFn


def production_deps() -> ExtrasDeps:
    """Боевые зависимости импортируются только при реальном запуске стадии."""
    from core.database import asettings_get
    from pipelines.clips import process_and_send_clips
    from pipelines.montage import process_and_send_highlights, process_and_send_montage
    from pipelines.shorts import process_and_send_shorts
    from services.render_clips_montage import create_extras_candidates

    return ExtrasDeps(
        get_setting=asettings_get,
        run_shorts=process_and_send_shorts,
        run_clips=process_and_send_clips,
        create_candidates=create_extras_candidates,
        run_montage=process_and_send_montage,
        run_highlights=process_and_send_highlights,
    )


async def run_optional_stage(
    name: str,
    awaitable: Awaitable[Any],
    default: Any = False,
) -> Any:
    """Изолировать необязательную стадию, но никогда не глотать отмену."""
    try:
        return await awaitable
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(
            "Optional stage %s failed but pipeline continues: %s",
            name,
            exc,
            exc_info=True,
        )
        return default


def empty_candidates() -> dict[str, list[Any]]:
    """Новый пустой результат для каждого запуска, без разделяемых списков."""
    return {"montage_candidates": [], "highlights_candidates": []}


async def _downstream_livedub_path(request: ExtrasRequest) -> Path | None:
    """Дождаться перевода только там, где это делал исходный оркестратор."""
    if request.user_mode not in ENG_MODES:
        return None

    livedub_path = request.get_livedub_path()
    if livedub_path is None and request.live_dub_pending:
        logger.info("Shorts/Clips/Montage: waiting for LiveDub video for ENG downstream renders")
        await request.send_livedub()
        livedub_path = request.get_livedub_path()

    if livedub_path and livedub_path.exists():
        logger.info("Shorts/Clips/Montage: using TRANSLATED video (LiveDub) for ENG renders")
        return livedub_path
    return None


async def run_extras(
    request: ExtrasRequest,
    deps: ExtrasDeps | None = None,
) -> None:
    """Запустить дополнения в порядке Shorts → Clips → Montage → Highlights."""
    deps = deps or production_deps()

    feat_shorts = await deps.get_setting("shorts")
    livedub_path = await _downstream_livedub_path(request)
    if request.ai_data and feat_shorts:
        logger.info("Shorts: feature enabled, starting pipeline")
        await run_optional_stage(
            "process_and_send_shorts",
            deps.run_shorts(
                url=request.source_url,
                media_id=request.media_id,
                mp3_path=request.mp3_path,
                title=request.title,
                performer=request.performer,
                duration=request.duration,
                ai_data=request.ai_data,
                update=request.update,
                existing_audio_part=request.used_audio_part,
                existing_client=request.used_client,
                rutube_url=request.rutube_url,
                vk_url=request.vk_url,
                workdir=request.workdir,
                livedub_video_path=livedub_path,
            ),
            False,
        )
    else:
        logger.info(
            "Shorts: skipped (feat=%s, ai_data=%s)",
            feat_shorts,
            "yes" if request.ai_data else "no",
        )

    feat_clips = await deps.get_setting("clips")
    if request.ai_data and feat_clips:
        logger.info("Clips: feature enabled, starting pipeline")
        await run_optional_stage(
            "process_and_send_clips",
            deps.run_clips(
                url=request.source_url,
                media_id=request.media_id,
                mp3_path=request.mp3_path,
                title=request.title,
                performer=request.performer,
                duration=request.duration,
                ai_data=request.ai_data,
                update=request.update,
                existing_audio_part=request.used_audio_part,
                existing_client=request.used_client,
                rutube_url=request.rutube_url,
                vk_url=request.vk_url,
                livedub_video_path=livedub_path,
            ),
            False,
        )
    else:
        logger.info(
            "Clips: skipped (feat=%s, ai_data=%s)",
            feat_clips,
            "yes" if request.ai_data else "no",
        )

    prefetched_extras = empty_candidates()
    feat_montage = await deps.get_setting("shorts_montage")
    feat_highlights = await deps.get_setting("shorts_highlights")

    if request.ai_data and (feat_montage or feat_highlights):
        logger.info("Extras: feature enabled, requesting ONE Gemini text call for montage+highlights")
        prefetched_extras = await run_optional_stage(
            "create_extras_candidates",
            deps.create_candidates(
                ai_data=request.ai_data,
                title=request.title,
                performer=request.performer,
                duration=request.duration,
            ),
            empty_candidates(),
        )

    if request.ai_data and feat_montage:
        logger.info("Montage: feature enabled, starting pipeline")
        await run_optional_stage(
            "process_and_send_montage",
            deps.run_montage(
                url=request.source_url,
                media_id=request.media_id,
                mp3_path=request.mp3_path,
                title=request.title,
                performer=request.performer,
                duration=request.duration,
                ai_data=request.ai_data,
                update=request.update,
                rutube_url=request.rutube_url,
                vk_url=request.vk_url,
                prefetched_candidates=prefetched_extras.get("montage_candidates", []),
                livedub_video_path=livedub_path,
            ),
            False,
        )
    else:
        logger.info("Montage: skipped (feat=%s)", feat_montage)

    if request.ai_data and feat_highlights:
        logger.info("Highlights: feature enabled, starting pipeline")
        await run_optional_stage(
            "process_and_send_highlights",
            deps.run_highlights(
                url=request.source_url,
                media_id=request.media_id,
                mp3_path=request.mp3_path,
                title=request.title,
                performer=request.performer,
                duration=request.duration,
                ai_data=request.ai_data,
                update=request.update,
                rutube_url=request.rutube_url,
                vk_url=request.vk_url,
                prefetched_candidates=prefetched_extras.get("highlights_candidates", []),
                livedub_video_path=livedub_path,
            ),
            False,
        )
    else:
        logger.info("Highlights: skipped (feat=%s)", feat_highlights)


__all__ = [
    "ENG_MODES",
    "ExtrasDeps",
    "ExtrasRequest",
    "empty_candidates",
    "production_deps",
    "run_extras",
    "run_optional_stage",
]
