#!/usr/bin/env python3
"""Standalone ENG translation-editor workflow with explicit progress ownership."""
from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from core.globals import DOWNLOAD_DIR
from core.utils import parse_title
from services.shorts_factory_capacity import await_with_heartbeat, safe_status
from services.shorts_factory_execution_guard import (
    enforce_factory_translation_preflight,
    factory_preflight_issues,
)
from services.shorts_factory_source import prepare_factory_translation_video
from services.translation_editorial_factory import (
    prepare_factory_editorial_review,
    send_factory_editorial_files,
)

logger = logging.getLogger(__name__)


async def process_translation_editorial_only(
    url: str,
    update: Any,
    status_msg: Any = None,
    progress_prefix: str = "",
    context: Any = None,
    silent_errors: bool = False,
) -> bool:
    """Yandex master → original SRT + Russian Whisper large-v3 → review ZIP."""
    del progress_prefix, context
    import pipelines.shorts_factory as factory_module
    import services.shorts_video_impl as shorts_video_impl
    from services.media_delivery_probe import media_probe_is_deliverable, probe_media_async

    workdir = Path(tempfile.mkdtemp(prefix="translation_editorial_only_"))
    if status_msg is None:
        status_msg = await update.message.reply_text(
            "🔎 РЕДАКТОР ПЕРЕВОДА: получаю метаданные…"
        )
    try:
        info = await factory_module._load_video_info(url)
        try:
            duration = int(float(info.get("duration") or 0))
        except (TypeError, ValueError, OverflowError):
            duration = 0
        if duration <= 0:
            raise RuntimeError("Не удалось определить длительность видео")

        language = str(info.get("language") or "").strip().lower()
        if language and not language.startswith("en"):
            raise RuntimeError(
                "Режим «ENG Редактор перевода» принимает английский источник; "
                f"metadata language={language!r}."
            )

        free_gb = shutil.disk_usage(DOWNLOAD_DIR).free / (1024**3)
        issues = factory_preflight_issues(
            gemini_available=True,
            whisper_available=bool(shorts_video_impl.HAS_FASTER_WHISPER),
            ffmpeg_available=bool(shutil.which("ffmpeg")),
            ffprobe_available=bool(shutil.which("ffprobe")),
            free_gb=free_gb,
            min_free_gb=2.0,
        )
        if issues:
            raise RuntimeError("Editorial preflight failed: " + "; ".join(issues))
        enforce_factory_translation_preflight()

        media_id = factory_module._media_id(info, url)
        full_title = str(info.get("title") or "Видео").strip()
        channel = str(info.get("channel") or info.get("uploader") or "").strip()
        performer, title = parse_title(full_title, channel)

        translated = await await_with_heartbeat(
            prepare_factory_translation_video(url, workdir, duration, "en"),
            label="🎙 ENG Редактор: Яндекс переводит и собирает полный master…",
            status_msg=status_msg,
            heartbeat=60.0,
        )
        probe = await probe_media_async(translated)
        if not media_probe_is_deliverable(probe):
            raise RuntimeError("Yandex editorial master failed video+audio probe")
        assert probe is not None

        pack, review, markdown = await await_with_heartbeat(
            prepare_factory_editorial_review(
                url=url,
                media_id=media_id,
                title=title or full_title,
                performer=performer or channel,
                duration=float(probe.duration),
                source_language="en",
                translated_video_path=translated,
                shorts_candidates=[],
                long_candidates=[],
                ai_data=None,
            ),
            label="🔎 ENG Редактор: original SRT + Russian Whisper large-v3…",
            status_msg=status_msg,
            heartbeat=60.0,
        )
        await send_factory_editorial_files(
            update,
            pack_path=pack,
            review_path=review,
            markdown_path=markdown,
        )
        await safe_status(
            status_msg,
            "✅ РЕДАКТОР ПЕРЕВОДА: ZIP готов. Пришлите его в ChatGPT.",
        )
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Translation editorial-only mode failed: %s", exc)
        if not silent_errors:
            text = f"❌ РЕДАКТОР ПЕРЕВОДА: {str(exc)[:500]}"
            try:
                await status_msg.edit_text(text)
            except Exception:
                await update.message.reply_text(text)
        return False
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


__all__ = ["process_translation_editorial_only"]
