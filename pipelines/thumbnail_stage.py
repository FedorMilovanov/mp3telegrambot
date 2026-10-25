#!/usr/bin/env python3
"""Получение обложки MP3: reuse локального файла или необязательный yt-dlp.

Обложка — дополнение, а не обязательная часть результата. Любая ошибка этого
контура возвращает ``None`` и не отменяет аудио. Fresh и cache-hit используют
одну реализацию, но сохраняют свои исторические тексты логов.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

logger = logging.getLogger(__name__)

THUMBNAIL_TIMEOUT_SEC = 30
RunProcessFn = Callable[..., Awaitable[Any]]
PrepareThumbnailFn = Callable[[Path], BytesIO | None]


@dataclass(frozen=True)
class ThumbnailDeps:
    """Внешние границы стадии; тесты не запускают yt-dlp и Pillow."""

    run: RunProcessFn
    prepare: PrepareThumbnailFn
    base_args: tuple[str, ...]


def production_deps() -> ThumbnailDeps:
    """Боевые зависимости импортируются только при реальном вызове."""
    from core.utils import prepare_thumbnail
    from services.async_process import run_cancellable_process
    from services.ffmpeg import YTDLP_BASE_ARGS

    return ThumbnailDeps(
        run=run_cancellable_process,
        prepare=prepare_thumbnail,
        base_args=tuple(YTDLP_BASE_ARGS),
    )


def thumbnail_command(
    *,
    media_id: str,
    url: str,
    thumbs_dir: Path,
    base_args: Sequence[str],
) -> list[str]:
    """Команда скачивания без видео, с JPEG-конвертацией и стабильным именем."""
    return list(base_args) + [
        "--skip-download",
        "--write-thumbnail",
        "--no-playlist",
        "--convert-thumbnails",
        "jpg",
        "--output",
        str(thumbs_dir / f"{media_id}_thumb.%(ext)s"),
        url,
    ]


async def load_thumbnail(
    *,
    media_id: str,
    url: str,
    thumbs_dir: Path,
    cache_hit: bool = False,
    deps: ThumbnailDeps | None = None,
) -> BytesIO | None:
    """Вернуть подготовленную обложку или ``None`` без влияния на pipeline.

    Ненулевой rc сам по себе не отменяет результат: yt-dlp мог успеть создать
    пригодный файл. Поэтому после процесса каталог проверяется в любом случае.
    """
    deps = deps or production_deps()
    log_label = "Обложка (кэш)" if cache_hit else "Обложка"
    existing_label = "Обложка кэш (hit)" if cache_hit else "Обложка кэш"
    rc_label = "rc" if cache_hit else "returncode"

    try:
        existing = list(thumbs_dir.glob(f"{media_id}_thumb*"))
        if existing:
            thumbnail = deps.prepare(existing[0])
            logger.info("%s: используем %s", existing_label, existing[0].name)
            return thumbnail

        process = await deps.run(
            thumbnail_command(
                media_id=media_id,
                url=url,
                thumbs_dir=thumbs_dir,
                base_args=deps.base_args,
            ),
            timeout=THUMBNAIL_TIMEOUT_SEC,
            text=True,
        )
        if process.returncode != 0:
            error = (process.stderr or "").strip()[-300:]
            logger.warning(
                "%s yt-dlp %s=%s: %s",
                log_label,
                rc_label,
                process.returncode,
                error,
            )

        candidates = list(thumbs_dir.glob(f"{media_id}_thumb*"))
        if candidates:
            thumbnail = deps.prepare(candidates[0])
            logger.info("%s скачана: %s", log_label, candidates[0].name)
            return thumbnail

        logger.warning(
            "%s: файл не найден после yt-dlp (rc=%s)",
            log_label,
            process.returncode,
        )
    except Exception as error:
        logger.warning("%s не скачана: %s", log_label, error)
    return None


__all__ = [
    "THUMBNAIL_TIMEOUT_SEC",
    "ThumbnailDeps",
    "load_thumbnail",
    "production_deps",
    "thumbnail_command",
]
