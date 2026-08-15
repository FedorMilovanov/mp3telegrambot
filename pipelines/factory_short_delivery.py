#!/usr/bin/env python3
"""Strict source-owned delivery path for Factory Shorts."""
from __future__ import annotations

import asyncio
import logging
import math
from pathlib import Path
from typing import Any

from telegram import InputFile

from converters.md_telegraph import safe_trim_caption, visible_length
from core.database import get_max_file_size_mb
from core.globals import DOWNLOAD_DIR
from services.media_delivery_probe import (
    file_size_mb,
    media_probe_is_deliverable,
    probe_media_async,
    resolve_delivery_timing,
)
from services.shorts_factory_publication import (
    enrich_factory_candidates,
    wrap_factory_caption_builder,
)
from services.shorts_subtitle_burn import burn_subtitles_into_short
from services.shorts_transcription import (
    factory_subtitle_profile,
    transcribe_short_clip,
)
from services.shorts_video import (
    HAS_FASTER_WHISPER,
    build_short_caption,
    create_short_snapshot,
    create_short_title_poster,
    get_shorts_visual_mode,
    postprocess_short,
    render_short_clip,
)

logger = logging.getLogger(__name__)

FACTORY_SHORT_PUBLIC_MAX_SEC = 180.0
FACTORY_DURATION_EPSILON_SEC = 0.05
_FACTORY_CAPTION_BUILDER = wrap_factory_caption_builder(build_short_caption)


def _factory_snap_ceiling(
    start_seconds: float,
    source_duration: float,
) -> float:
    ceiling = float(start_seconds) + FACTORY_SHORT_PUBLIC_MAX_SEC
    if source_duration > 0.0:
        ceiling = min(ceiling, float(source_duration))
    return ceiling


def _valid_boundary(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _final_caption(caption: str) -> str:
    value = str(caption or "")
    return safe_trim_caption(value, 1024) if visible_length(value) > 1024 else value


def _remove(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


async def process_and_send_factory_shorts(
    *,
    url: str,
    media_id: str,
    mp3_path: Path,
    title: str,
    performer: str,
    source_duration: float,
    ai_data: dict[str, Any],
    candidates: list[dict[str, Any]],
    source_video_path: Path,
    update,
    rutube_url: str = "",
    vk_url: str = "",
) -> int:
    """Render judged Factory candidates and count only proved Telegram deliveries."""
    if not HAS_FASTER_WHISPER:
        raise RuntimeError(
            "SHORTS FACTORY MAX requires faster-whisper for mandatory burned subtitles"
        )
    source_video_path = Path(source_video_path)
    source_duration = max(0.0, float(source_duration or 0.0))
    if not source_video_path.is_file():
        raise RuntimeError("Factory Short source video is missing")

    enriched = await enrich_factory_candidates(
        candidates,
        call_kwargs={
            "mp3_path": mp3_path,
            "ai_data": ai_data,
            "title": title,
            "performer": performer,
            "duration": source_duration,
        },
        kind="short",
    )
    if not enriched:
        return 0

    format_name = str((ai_data or {}).get("format") or "other")
    real_author = str((ai_data or {}).get("real_author") or performer or "")
    real_event = str((ai_data or {}).get("real_event") or "")
    visual_mode = get_shorts_visual_mode(format_name)
    subtitle_profile = factory_subtitle_profile()
    sent = 0

    for index, candidate in enumerate(enriched, 1):
        raw_path = DOWNLOAD_DIR / f"{media_id}_factory_short_{index}_raw.mp4"
        post_path = DOWNLOAD_DIR / f"{media_id}_factory_short_{index}_post.mp4"
        sub_path = DOWNLOAD_DIR / f"{media_id}_factory_short_{index}_sub.mp4"
        snapshot_path = DOWNLOAD_DIR / f"{media_id}_factory_short_{index}_snap.jpg"
        poster_path = DOWNLOAD_DIR / f"{media_id}_factory_short_{index}_poster.jpg"
        paths = (raw_path, post_path, sub_path, snapshot_path, poster_path)
        for path in paths:
            _remove(path)

        try:
            start = _valid_boundary(candidate.get("start_seconds"))
            end = _valid_boundary(candidate.get("end_seconds"))
            if start is None or end is None or start < 0.0 or end <= start:
                logger.warning("Factory Short %d rejected: invalid boundaries", index)
                continue
            ceiling = _factory_snap_ceiling(start, source_duration)
            if end > ceiling + 1e-9:
                logger.warning(
                    "Factory Short %d rejected before render: %.3f..%.3f ceiling=%.3f",
                    index,
                    start,
                    end,
                    ceiling,
                )
                continue

            rendered = await render_short_clip(
                source_video_path,
                raw_path,
                start,
                end,
                visual_mode=visual_mode,
                silence_snap_max_end=ceiling,
                snap_to_silence=False,
            )
            if not rendered:
                continue
            raw_probe = await probe_media_async(raw_path)
            if not media_probe_is_deliverable(raw_probe):
                logger.warning("Factory Short %d rejected: raw media probe failed", index)
                continue
            assert raw_probe is not None

            normalized = await postprocess_short(
                raw_path,
                post_path,
                normalize_audio=True,
                speed=1.0,
            )
            if not normalized:
                logger.warning(
                    "Factory Short %d rejected: mandatory audio normalization failed",
                    index,
                )
                continue
            current_path = post_path

            segments = await transcribe_short_clip(
                current_path,
                ai_data=ai_data,
                subtitle_profile=subtitle_profile,
            )
            if not segments:
                logger.warning("Factory Short %d rejected: mandatory transcript missing", index)
                continue
            burned = await burn_subtitles_into_short(
                current_path,
                sub_path,
                segments,
                karaoke=bool(subtitle_profile["karaoke"]),
            )
            if not burned:
                logger.warning("Factory Short %d rejected: mandatory subtitle burn failed", index)
                continue

            final_probe = await probe_media_async(sub_path)
            if not media_probe_is_deliverable(final_probe):
                logger.warning("Factory Short %d rejected: final media probe failed", index)
                continue
            assert final_probe is not None
            if final_probe.duration > FACTORY_SHORT_PUBLIC_MAX_SEC + FACTORY_DURATION_EPSILON_SEC:
                logger.warning(
                    "Factory Short %d rejected: final %.3fs exceeds %.0fs public cap",
                    index,
                    final_probe.duration,
                    FACTORY_SHORT_PUBLIC_MAX_SEC,
                )
                continue
            if file_size_mb(sub_path) > get_max_file_size_mb():
                logger.warning("Factory Short %d rejected: final file exceeds upload cap", index)
                continue

            timing = resolve_delivery_timing(
                source_start=start,
                raw_duration=raw_probe.duration,
                source_duration=source_duration,
                speed=1.0,
                speed_applied=False,
                final_duration=final_probe.duration,
            )
            delivery_candidate = {
                **candidate,
                "_render_start_seconds": timing.source_start,
                "_render_end_seconds": timing.source_end,
                "_raw_duration_seconds": timing.raw_duration,
                "_delivery_duration_seconds": timing.delivery_duration,
                "_speed_applied": False,
                "_delivery_file_selection": "subtitled",
                "_delivery_file_reason": "factory_mandatory_subtitles",
            }

            thumb = None
            try:
                poster_ok = await create_short_title_poster(
                    sub_path,
                    poster_path,
                    str(candidate.get("title") or ""),
                    timing.delivery_duration,
                )
                if poster_ok and poster_path.is_file():
                    thumb = InputFile(poster_path.read_bytes(), filename=poster_path.name)
            except Exception as exc:
                logger.warning("Factory Short %d poster failed: %s", index, exc)
            if thumb is None:
                try:
                    snap_ok = await create_short_snapshot(
                        sub_path,
                        snapshot_path,
                        timing.delivery_duration,
                    )
                    if snap_ok and snapshot_path.is_file():
                        thumb = InputFile(
                            snapshot_path.read_bytes(),
                            filename=snapshot_path.name,
                        )
                except Exception as exc:
                    logger.warning("Factory Short %d snapshot failed: %s", index, exc)

            caption = _final_caption(
                _FACTORY_CAPTION_BUILDER(
                    candidate=delivery_candidate,
                    performer=performer,
                    real_author=real_author,
                    real_event=real_event,
                    format_name=format_name,
                    yt_url=url,
                    vk_url=vk_url,
                    rutube_url=rutube_url,
                )
            )
            await update.message.reply_video(
                video=sub_path,
                caption=caption,
                duration=max(1, int(round(final_probe.duration))),
                width=720,
                height=1280,
                thumbnail=thumb,
                parse_mode="HTML",
                write_timeout=120,
                read_timeout=120,
                connect_timeout=30,
            )
            sent += 1
            logger.info(
                "Factory Short delivered %d/%d: %.3f..%.3f final=%.3fs model=%s",
                index,
                len(enriched),
                timing.source_start,
                timing.source_end,
                final_probe.duration,
                subtitle_profile["model_name"],
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Factory Short %d delivery error: %s: %s",
                index,
                type(exc).__name__,
                exc,
            )
        finally:
            for path in paths:
                _remove(path)

    return sent


__all__ = [
    "FACTORY_DURATION_EPSILON_SEC",
    "FACTORY_SHORT_PUBLIC_MAX_SEC",
    "process_and_send_factory_shorts",
]
