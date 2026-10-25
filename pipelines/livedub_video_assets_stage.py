"""Run LiveDub pre-send checks and prepare metadata/thumbnail/cover assets."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


async def get_livedub_technical_warnings(
    livedub_path: Path,
    *,
    expected_duration: int,
    is_fallback: bool,
) -> list[str]:
    """Run the inexpensive integrity check unless this is the original fallback."""
    warnings: list[str] = []
    if not is_fallback:
        try:
            from services.livedub_qa import technical_check

            warnings = await asyncio.get_running_loop().run_in_executor(
                None, lambda: technical_check(livedub_path, expected_duration)
            )
            for warning in warnings:
                logger.warning(f"[LiveDubQA] tech: {warning}")
        except Exception as exc:
            logger.warning(f"[LiveDubQA] technical_check сбой: {exc}")
    return warnings


async def prepare_livedub_video_assets(
    livedub_path: Path,
    *,
    media_id: str,
    thumbs_dir: Path,
) -> tuple[Any, Any, Path | None]:
    """Return video metadata, thumbnail, and optional YouTube cover."""
    metadata, thumbnail = {}, None
    try:
        from services.livedub_mix import make_video_thumbnail, probe_video_meta

        probed_metadata = await asyncio.get_running_loop().run_in_executor(None, lambda: probe_video_meta(livedub_path))
        probed_thumbnail = await asyncio.get_running_loop().run_in_executor(
            None, lambda: make_video_thumbnail(livedub_path)
        )
        # Preserve the legacy all-or-nothing assignment if thumbnail probing fails.
        metadata, thumbnail = probed_metadata, probed_thumbnail
    except Exception as exc:
        logger.warning(f"[LiveDub] video meta: {exc}")

    # Bot API 8.3+: use the already downloaded YouTube cover when it is real.
    cover = None
    try:
        candidates = sorted(thumbs_dir.glob(f"{media_id}_thumb*"))
        if candidates and candidates[0].stat().st_size > 1024:
            cover = candidates[0]
    except Exception:
        pass
    return metadata, thumbnail, cover
