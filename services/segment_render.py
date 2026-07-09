#!/usr/bin/env python3
"""Shared segment rendering logic for /cut command and inline buttons.

Extracts the common render → subtitle → send pipeline to avoid code
duplication between cutseg_command and segcut callback.
"""
from __future__ import annotations

import html as html_mod
import logging
import uuid
from pathlib import Path
from typing import Optional

from core.database import get_max_file_size_mb, asettings_get
from core.globals import DOWNLOAD_DIR
from core.render_locks import release_render_lock, render_lock_key, try_acquire_render_lock
from core.segment_planner import PlannedSegment, seconds_to_timestamp
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
    """Render one segment and send it as a Telegram video.

    Args:
        reply_target: message object to reply_video to
        status_msg: editable status message for progress updates
        video_id: media identifier
        source_url: URL to download video from
        segment: PlannedSegment with start/end/title
        title: video title for caption
        total_segments: total number of segments (for display)
        ai_data: optional AI analysis data for subtitle context

    Returns:
        True if segment was sent successfully.
    """
    # Sanitize video_id to prevent path traversal (defense in depth)
    safe_id = video_id.replace("/", "_").replace("\\", "_").replace("..", "_")
    clip_path = DOWNLOAD_DIR / f"{safe_id}_segment_{segment.index}_{uuid.uuid4().hex[:6]}.mp4"
    sub_path = DOWNLOAD_DIR / f"{safe_id}_segment_{segment.index}_{uuid.uuid4().hex[:6]}_sub.mp4"

    try:
        # Progress update
        try:
            await status_msg.edit_text(
                f"🎬 Рендерю {segment.index}/{total_segments}: "
                f"{html_mod.escape(segment.title[:120])}\n"
                f"{seconds_to_timestamp(segment.start)}–{seconds_to_timestamp(segment.end)}"
            )
        except Exception:
            pass

        # Download video (cached by media_id in DOWNLOAD_DIR)
        video_path = await download_video_for_shorts(source_url, video_id)
        if not video_path:
            try:
                await status_msg.edit_text("❌ Не удалось скачать видео для сегмента.")
            except Exception:
                pass
            return False

        # Render clip
        ok = await render_clip(video_path, clip_path, segment.start, segment.end)
        if not ok or not clip_path.exists():
            await reply_target.reply_text(f"❌ Не удалось вырезать сегмент {segment.index}.")
            return False

        # Optional subtitles
        final_clip_path = clip_path
        if await asettings_get("segments_subtitles"):
            if not HAS_FASTER_WHISPER:
                logger.warning("Segment subtitles: faster-whisper not installed")
            else:
                try:
                    sub_segments = await transcribe_short_clip(
                        clip_path, ai_data=ai_data or {}
                    )
                    if sub_segments:
                        sub_ok = await burn_subtitles_into_short(
                            clip_path, sub_path, sub_segments
                        )
                        if sub_ok and sub_path.exists():
                            final_clip_path = sub_path
                    else:
                        logger.warning(
                            "Segment subtitles: empty transcription for segment %s",
                            segment.index,
                        )
                except Exception as e:
                    logger.warning(
                        "Segment subtitles failed for %s: %s", segment.index, e
                    )

        # Size check
        size_mb = final_clip_path.stat().st_size / (1024 * 1024)
        if size_mb > get_max_file_size_mb():
            await reply_target.reply_text(
                f"❌ Сегмент {segment.index} слишком большой: {size_mb:.0f} МБ "
                f"(лимит {get_max_file_size_mb()} МБ)."
            )
            return False

        # Build caption
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

        # Send video
        await reply_target.reply_video(
            video=final_clip_path,
            caption=caption,
            duration=int(segment.duration),
            supports_streaming=True,
            parse_mode="HTML",
            write_timeout=300,
            read_timeout=300,
            connect_timeout=60,
        )
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
