"""Build a LiveDub video with optional Pro-mix and the historical fallback."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
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


async def wait_for_livedub_video(
    dub_task: Awaitable[Path | None],
    workdir: Path,
) -> Path | None:
    """Await the dub task and record the historical failure marker on errors."""
    try:
        return await dub_task
    except RuntimeError as exc:
        if "LIVEDUB_AUTH_REQUIRED" in str(exc):
            logger.warning(
                "[LiveDub] Яндекс требует OAuth-токен для живых голосов "
                "(нет/протух VOT_API_TOKEN в .env — см. .env.example)"
            )
            try:
                (workdir / ".auth_required").write_text("1", encoding="utf-8")
            except OSError:
                pass
        elif "LIVEDUB_NOT_AVAILABLE" in str(exc):
            logger.info("[LiveDub] Перевод недоступен для этого видео")
            try:
                (workdir / ".not_available").write_text("1", encoding="utf-8")
            except OSError:
                pass
        else:
            logger.warning(f"[LiveDub] Ошибка: {exc}")
            try:
                (workdir / ".livedub_failed").write_text(str(exc)[:500], encoding="utf-8")
            except OSError:
                pass
    except Exception as exc:
        logger.warning(f"[LiveDub] Неизвестная ошибка: {exc}")
        try:
            (workdir / ".livedub_failed").write_text(str(exc)[:500], encoding="utf-8")
        except OSError:
            pass
    return None
