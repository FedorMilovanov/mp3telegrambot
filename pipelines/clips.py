#!/usr/bin/env python3
"""Long Clips pipeline with explicit candidate and public-duration ownership."""
from __future__ import annotations

import asyncio
import copy
import logging
import os
from pathlib import Path
from typing import Any

from telegram import InputFile

from core.database import asettings_get, get_max_file_size_mb, settings_get
from core.globals import DOWNLOAD_DIR
from converters.md_telegraph import safe_trim_caption, visible_length
from services.clip_renderer import clip_snap_ceiling, render_clip
from services.media_delivery_probe import media_probe_is_deliverable, probe_media_async
from services.render_clips_montage import build_clip_caption, create_clip_snapshot
from services.shorts_candidates import create_clips_candidates
from services.shorts_factory_publication import wrap_factory_caption_builder
from services.shorts_video import download_video_for_shorts

logger = logging.getLogger(__name__)
PUBLIC_CLIP_DURATION_EPSILON_SEC = 0.05
_FACTORY_CLIP_CAPTION_BUILDER = wrap_factory_caption_builder(build_clip_caption)


def _clips_candidate_budget_seconds() -> float:
    raw = (os.getenv("CLIPS_CANDIDATE_BUDGET_SECONDS", "90") or "90").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 90.0
    return max(15.0, min(value, 300.0))


def _candidate_label(candidate: dict[str, Any], key: str, fallback_key: str) -> str:
    value = candidate.get(key)
    if value not in (None, ""):
        return str(value)
    return str(candidate.get(fallback_key, ""))


async def process_and_send_clips(
    url: str,
    media_id: str,
    mp3_path: Path,
    title: str,
    performer: str,
    duration: int | float,
    ai_data: dict,
    update,
    existing_audio_part=None,
    existing_client=None,
    rutube_url: str = "",
    vk_url: str = "",
    livedub_video_path=None,
    *,
    candidates_override: list[dict[str, Any]] | None = None,
    public_max_seconds: float | None = None,
    snapshot_override: bool | None = None,
    factory_publication: bool = False,
) -> int:
    """Render and send long clips, returning the number actually delivered."""
    video_path: Path | None = None
    borrowed_video = False
    clip_paths: list[Path] = []
    snap_paths: list[Path] = []
    sent = 0
    try:
        if candidates_override is None:
            candidate_budget = _clips_candidate_budget_seconds()
            try:
                candidates = await asyncio.wait_for(
                    create_clips_candidates(
                        mp3_path=mp3_path,
                        ai_data=ai_data,
                        title=title,
                        performer=performer,
                        duration=duration,
                        existing_audio_part=existing_audio_part,
                        existing_client=existing_client,
                    ),
                    timeout=candidate_budget,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Clips: optional candidate search exceeded %.0fs budget; skipping",
                    candidate_budget,
                )
                return 0
        else:
            candidates = copy.deepcopy(candidates_override)

        if not candidates:
            logger.info("Clips: no candidates")
            return 0

        if livedub_video_path and Path(livedub_video_path).exists():
            video_path = Path(livedub_video_path)
            borrowed_video = True
            logger.info("Clips: using owned/borrowed prepared video: %s", video_path.name)
        else:
            video_path = await download_video_for_shorts(url, media_id)
        if not video_path:
            logger.warning("Clips: video download failed")
            await update.message.reply_text("🎬 Не удалось скачать видео для Clips.")
            return 0

        format_name = (ai_data or {}).get("format", "other") or "other"
        real_author = (ai_data or {}).get("real_author", "") or performer or ""
        real_event = (ai_data or {}).get("real_event", "") or ""
        do_snapshot = (
            bool(snapshot_override)
            if snapshot_override is not None
            else bool(await asettings_get("clips_snapshot"))
        )
        caption_builder = _FACTORY_CLIP_CAPTION_BUILDER if factory_publication else build_clip_caption

        total = len(candidates)
        logger.info(
            "Clips: format=%s snapshot=%s candidates=%d public_max=%s factory_publication=%s",
            format_name,
            do_snapshot,
            total,
            public_max_seconds,
            factory_publication,
        )

        for i, candidate in enumerate(candidates, 1):
            if not isinstance(candidate, dict):
                logger.warning("Clips: invalid candidate %d/%d: %r", i, total, candidate)
                continue
            clip_path = DOWNLOAD_DIR / f"{media_id}_clip_{i}.mp4"
            snap_path = DOWNLOAD_DIR / f"{media_id}_clip_{i}_snap.jpg"
            clip_paths.append(clip_path)
            snap_paths.append(snap_path)

            try:
                start_seconds = float(candidate["start_seconds"])
                end_seconds = float(candidate["end_seconds"])
            except (KeyError, TypeError, ValueError, OverflowError):
                logger.warning("Clips: candidate %d has invalid boundaries", i)
                continue

            snap_ceiling = clip_snap_ceiling(
                start_seconds,
                public_max_seconds,
                duration,
            )
            if snap_ceiling is not None and end_seconds > snap_ceiling + 1e-9:
                logger.warning(
                    "Clips %d/%d rejected before render: %.3f..%.3f exceeds ceiling %.3f",
                    i,
                    total,
                    start_seconds,
                    end_seconds,
                    snap_ceiling,
                )
                continue

            logger.info(
                "Clips: render %d/%d (%.3f..%.3f) %r",
                i,
                total,
                start_seconds,
                end_seconds,
                candidate.get("title", ""),
            )
            ok = await render_clip(
                video_path,
                clip_path,
                start_seconds,
                end_seconds,
                silence_snap_max_end=snap_ceiling,
            )
            if not ok:
                continue

            clip_probe = await probe_media_async(clip_path)
            if not media_probe_is_deliverable(clip_probe):
                logger.warning(
                    "Clips %d/%d: rendered file failed final media probe",
                    i,
                    total,
                )
                continue
            assert clip_probe is not None
            delivery_duration = float(clip_probe.duration)
            if (
                public_max_seconds is not None
                and delivery_duration
                > float(public_max_seconds) + PUBLIC_CLIP_DURATION_EPSILON_SEC
            ):
                logger.warning(
                    "Clips %d/%d rejected: final %.3fs exceeds public %.3fs",
                    i,
                    total,
                    delivery_duration,
                    float(public_max_seconds),
                )
                continue

            clip_size_mb = clip_path.stat().st_size / (1024 * 1024)
            if clip_size_mb > get_max_file_size_mb():
                logger.warning(
                    "Clips %d/%d: file too large (%.0fMB)",
                    i,
                    total,
                    clip_size_mb,
                )
                continue

            thumb_buf = None
            if do_snapshot:
                try:
                    snap_ok = await create_clip_snapshot(
                        clip_path,
                        snap_path,
                        delivery_duration,
                    )
                    if snap_ok and snap_path.exists():
                        thumb_buf = InputFile(
                            snap_path.read_bytes(),
                            filename=snap_path.name,
                        )
                except Exception as snap_err:
                    logger.warning("Clips %d/%d snapshot error: %s", i, total, snap_err)

            caption = caption_builder(
                candidate=candidate,
                performer=performer,
                real_author=real_author,
                real_event=real_event,
                format_name=format_name,
                yt_url=url,
                vk_url=vk_url,
                rutube_url=rutube_url,
            )
            if visible_length(caption) > 1024:
                caption = safe_trim_caption(caption, 1024)

            try:
                await update.message.reply_video(
                    video=clip_path,
                    caption=caption,
                    duration=max(1, int(round(delivery_duration))),
                    thumbnail=thumb_buf,
                    supports_streaming=True,
                    parse_mode="HTML",
                    write_timeout=300,
                    read_timeout=300,
                    connect_timeout=30,
                )
                sent += 1
                logger.info(
                    "Clips: sent %d/%d (%s-%s) %r, final=%.3fs",
                    i,
                    total,
                    _candidate_label(candidate, "start", "start_seconds"),
                    _candidate_label(candidate, "end", "end_seconds"),
                    candidate.get("title", ""),
                    delivery_duration,
                )
            except Exception as send_err:
                logger.warning("Clips: send error %d/%d: %s", i, total, send_err)

        if sent == 0:
            logger.warning("Clips: no clips delivered")
        else:
            logger.info("Clips: delivered %d/%d", sent, total)
        return sent

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Clips process_and_send error: %s", exc)
        try:
            await update.message.reply_text(
                f"🎬 Ошибка при подготовке Clips: {str(exc)[:150]}"
            )
        except Exception:
            pass
        return sent
    finally:
        for path in (*clip_paths, *snap_paths):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        if video_path:
            try:
                keep = settings_get("shorts_montage") or settings_get("shorts_highlights")
                if not keep and not borrowed_video:
                    video_path.unlink(missing_ok=True)
            except Exception:
                pass


__all__ = ["PUBLIC_CLIP_DURATION_EPSILON_SEC", "process_and_send_clips"]
