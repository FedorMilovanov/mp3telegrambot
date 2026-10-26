"""Coordinate parallel LiveDub video and subtitle production."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BackgroundResult = tuple[Path | None, bool, bool]
PathCoroutine = Coroutine[Any, Any, Path | None]


async def run_livedub_background(
    video_url: str,
    workdir: Path,
    *,
    source_lang: str,
    duration: float,
    subtitles_enabled: bool,
    translation_subtitles_enabled: bool,
    pro_mix: bool,
    create_subtitles: Callable[..., PathCoroutine],
    make_video: Callable[..., PathCoroutine],
    wait_video: Callable[[Awaitable[Path | None], Path], Awaitable[Path | None]],
    merge_subtitles: Callable[..., Awaitable[Path]],
    download_original: Callable[..., Awaitable[Path]],
) -> BackgroundResult:
    """Build LiveDub and optional subtitles, always tearing down child tasks."""
    subs_task: asyncio.Task[Path | None] | None = None
    dub_task: asyncio.Task[Path | None] | None = None
    dub_subs_task: asyncio.Task[Path | None] | None = None
    try:
        if subtitles_enabled:
            subs_task = asyncio.create_task(
                create_subtitles(
                    video_url,
                    workdir,
                    known_duration=duration,
                    lang=source_lang,
                )
            )

        dub_task = asyncio.create_task(
            make_video(
                video_url,
                workdir,
                pro_mix=pro_mix,
                duration=duration,
                source_lang=source_lang,
            )
        )

        if translation_subtitles_enabled:
            # Keep the optional service import at the historical call boundary.
            from services.yandex_live_dub import get_translation_subtitles

            dub_subs_task = asyncio.create_task(get_translation_subtitles(video_url, workdir))

        srt_path = None
        if subs_task:
            try:
                srt_path = await subs_task
            except Exception as exc:
                logger.warning(f"[EngSubtitles] Ошибка создания сабов: {exc}")

        dub_path = await wait_video(dub_task, workdir)

        if dub_subs_task:
            try:
                await asyncio.wait_for(dub_subs_task, timeout=60)
            except Exception as exc:
                logger.info(f"[LiveDub] субтитры перевода не получены: {exc}")

        if dub_path and dub_path.exists():
            if srt_path and srt_path.exists():
                final_video = await merge_subtitles(dub_path, srt_path, is_fallback=False)
                return final_video, False, True
            return dub_path, False, False

        if srt_path and srt_path.exists():
            logger.info("[LiveDub] Перевод не удался, отправляем резерв с субтитрами")
            original = await download_original(video_url, workdir)
            final_video = await merge_subtitles(original, srt_path, is_fallback=True)
            return final_video, True, True
        return None, False, False
    except Exception as exc:
        logger.warning(f"[LiveDub Background] Ошибка: {exc}")
        return None, False, False
    finally:
        inner = [task for task in (subs_task, dub_task, dub_subs_task) if task is not None and not task.done()]
        for task in inner:
            task.cancel()
        if inner:
            with contextlib.suppress(BaseException):
                # Teardown must not replace the result or cancellation reason.
                await asyncio.gather(*inner, return_exceptions=True)
