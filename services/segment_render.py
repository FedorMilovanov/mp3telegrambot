#!/usr/bin/env python3
"""Shared segment rendering logic for /cut command and inline buttons.

Extracts the common render → subtitle → verify → send pipeline to avoid code
duplication between cutseg_command and segcut callback.
"""
from __future__ import annotations

import html as html_mod
import logging
import uuid
from pathlib import Path

from core.database import asettings_get, get_max_file_size_mb
from core.globals import DOWNLOAD_DIR
from core.segment_planner import PlannedSegment, seconds_to_timestamp
from services.media_delivery_probe import (
    file_size_mb,
    media_probe_is_deliverable,
    probe_media_async,
    select_delivery_file,
)
from services.render_clips_montage import render_clip
from services.shorts_video import (
    HAS_FASTER_WHISPER,
    burn_subtitles_into_short,
    download_video_for_shorts,
    transcribe_short_clip,
)

logger = logging.getLogger(__name__)


async def render_and_send_segment(
    *,
    reply_target,
    status_msg,
    video_id: str,
    source_url: str,
    segment: PlannedSegment,
    title: str,
    total_segments: int,
    ai_data: dict | None = None,
) -> bool:
    """Render, prove and deliver one selectable Q&A/theme segment."""
    safe_id = video_id.replace("/", "_").replace("\\", "_").replace("..", "_")
    clip_path = DOWNLOAD_DIR / f"{safe_id}_segment_{segment.index}_{uuid.uuid4().hex[:6]}.mp4"
    sub_path = DOWNLOAD_DIR / f"{safe_id}_segment_{segment.index}_{uuid.uuid4().hex[:6]}_sub.mp4"

    try:
        try:
            await status_msg.edit_text(
                f"🎬 Рендерю {segment.index}/{total_segments}: "
                f"{html_mod.escape(segment.title[:120])}\n"
                f"{seconds_to_timestamp(segment.start)}–{seconds_to_timestamp(segment.end)}"
            )
        except Exception:
            pass

        video_path = await download_video_for_shorts(source_url, video_id)
        if not video_path:
            try:
                await status_msg.edit_text("❌ Не удалось скачать видео для сегмента.")
            except Exception:
                pass
            return False

        ok = await render_clip(video_path, clip_path, segment.start, segment.end)
        if not ok or not clip_path.exists():
            await reply_target.reply_text(f"❌ Не удалось вырезать сегмент {segment.index}.")
            return False

        raw_probe = await probe_media_async(clip_path)
        if not media_probe_is_deliverable(raw_probe):
            logger.warning(
                "Segment %s rejected: base render lacks verified video+audio",
                segment.index,
            )
            await reply_target.reply_text(
                f"❌ Сегмент {segment.index} не прошёл проверку видео и звука."
            )
            return False

        final_clip_path = clip_path
        subtitle_requested = bool(await asettings_get("segments_subtitles"))
        subtitles_applied = False
        if subtitle_requested:
            if not HAS_FASTER_WHISPER:
                logger.warning("Segment subtitles: faster-whisper not installed")
            else:
                try:
                    sub_segments = await transcribe_short_clip(
                        clip_path,
                        ai_data=ai_data or {},
                    )
                    if sub_segments:
                        sub_ok = await burn_subtitles_into_short(
                            clip_path,
                            sub_path,
                            sub_segments,
                        )
                        if sub_ok and sub_path.exists():
                            final_clip_path = sub_path
                            subtitles_applied = True
                    else:
                        logger.warning(
                            "Segment subtitles: empty transcription for segment %s",
                            segment.index,
                        )
                except Exception as exc:
                    logger.warning(
                        "Segment subtitles failed for %s: %s",
                        segment.index,
                        exc,
                    )

        max_upload_mb = get_max_file_size_mb()
        selection = select_delivery_file(
            final_clip_path,
            clip_path if final_clip_path != clip_path else None,
            max_size_mb=max_upload_mb,
        )
        if selection.path is None:
            await reply_target.reply_text(
                f"❌ Сегмент {segment.index} слишком большой или повреждён: "
                f"primary={selection.primary_size_mb:.1f} МБ, "
                f"fallback={selection.fallback_size_mb:.1f} МБ, "
                f"лимит {max_upload_mb} МБ."
            )
            return False

        final_clip_path = selection.path
        if selection.selected == "fallback":
            subtitles_applied = False
            logger.warning(
                "Segment %s subtitle artifact rejected (%s); using base render",
                segment.index,
                selection.reason,
            )

        final_probe = await probe_media_async(final_clip_path)
        if not media_probe_is_deliverable(final_probe):
            if final_clip_path != clip_path:
                fallback_probe = await probe_media_async(clip_path)
                if media_probe_is_deliverable(fallback_probe):
                    final_clip_path = clip_path
                    final_probe = fallback_probe
                    subtitles_applied = False
            if not media_probe_is_deliverable(final_probe):
                logger.warning(
                    "Segment %s rejected: no final file has verified video+audio",
                    segment.index,
                )
                await reply_target.reply_text(
                    f"❌ Сегмент {segment.index} не прошёл финальную проверку файла."
                )
                return False

        assert final_probe is not None
        delivery_duration = float(final_probe.duration)
        delivery_size_mb = file_size_mb(final_clip_path)
        logger.info(
            "Segment delivery evidence: index=%s selected=%s reason=%s "
            "duration=%.3fs size=%.1fMB subtitles=%s",
            segment.index,
            selection.selected,
            selection.reason,
            delivery_duration,
            delivery_size_mb,
            subtitles_applied,
        )

        caption = (
            f"🎬 <b>{html_mod.escape(title)}</b>\n"
            f"{seconds_to_timestamp(segment.start)}–{seconds_to_timestamp(segment.end)} "
            f"({seconds_to_timestamp(segment.duration)})\n"
            f"<b>{html_mod.escape(segment.title)}</b>"
        )
        if len(caption) > 1024:
            caption = (
                f"🎬 <b>{html_mod.escape(title[:120])}</b>\n"
                f"{seconds_to_timestamp(segment.start)}–{seconds_to_timestamp(segment.end)} "
                f"({seconds_to_timestamp(segment.duration)})\n"
                f"<b>{html_mod.escape(segment.title[:220])}</b>"
            )

        await reply_target.reply_video(
            video=final_clip_path,
            caption=caption,
            duration=max(1, int(round(delivery_duration))),
            supports_streaming=True,
            parse_mode="HTML",
            write_timeout=300,
            read_timeout=300,
            connect_timeout=60,
        )
        if subtitle_requested and not subtitles_applied:
            try:
                await reply_target.reply_text(
                    "⚠️ Сегмент отправлен без субтитров: версия с ними не прошла "
                    "транскрипцию, размер или финальную проверку файла."
                )
            except Exception:
                pass
        return True

    finally:
        try:
            clip_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            sub_path.unlink(missing_ok=True)
        except Exception:
            pass
