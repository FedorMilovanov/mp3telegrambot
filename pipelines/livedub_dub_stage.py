"""Build a LiveDub video with optional Pro-mix and the historical fallback."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def make_livedub_video(
    video_url: str,
    workdir: Path,
    *,
    pro_mix: bool,
    duration: float,
    source_lang: str = "",
) -> Path:
    """Try Pro-mix when enabled, then use the ordinary LiveDub video path."""
    if pro_mix:
        try:
            # Keep imports at call time: tests and embedders patch the service
            # boundary, and the background task must observe those patches.
            from services.livedub_mix import build_pro_dub

            pro = await build_pro_dub(
                video_url,
                workdir,
                duration=duration,
                lang=source_lang,
            )
            if pro and pro.exists():
                return pro
            logger.warning("[LiveDub] pro-микс не собрался — fallback на vot-merge")
        except RuntimeError:
            raise  # LIVEDUB_NOT_AVAILABLE — наверх
        except Exception as exc:
            logger.warning(f"[LiveDub] pro-микс сбой ({exc}) — fallback на vot-merge")

    from services.yandex_live_dub import get_live_dub_video

    return await get_live_dub_video(
        video_url,
        workdir,
        original_volume=0.3,
        translation_volume=1.5,
        keep_original_audio=True,
        duration=duration,
        lang=source_lang,
    )
