#!/usr/bin/env python3
"""Ordinary Shorts pipeline with direct source-window and delivery ownership."""
from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from telegram import InputFile

from converters.md_telegraph import safe_trim_caption, visible_length
from core.database import (
    asettings_get,
    ashorts_speed_get,
    get_max_file_size_mb,
    settings_get,
    short_trim_save,
)
from core.globals import DOWNLOAD_DIR, InlineKeyboardButton, InlineKeyboardMarkup
from services.media_delivery_probe import (
    file_size_mb,
    media_probe_is_deliverable,
    probe_media_async,
    resolve_delivery_timing,
    select_delivery_file,
)
from services.shorts_candidates import create_shorts_candidates
from services.shorts_duration_safety import (
    final_public_short_is_safe,
    plan_short_source_window,
    short_speed_transform_required,
)
from services.shorts_factory_media import (
    align_livedub_candidates,
    probe_livedub_source_duration,
)
from services.shorts_subtitle_burn import burn_subtitles_into_short
from services.shorts_video import (
    HAS_FASTER_WHISPER,
    build_short_caption,
    create_short_snapshot,
    create_short_title_poster,
    download_video_for_shorts,
    get_shorts_visual_mode,
    postprocess_short,
    render_short_clip,
    transcribe_short_clip,
)

logger = logging.getLogger(__name__)


def _finalize_short_caption_for_delivery(
    caption: str,
    *,
    media_id: str,
    index: int,
    total: int,
    start: object = "",
    end: object = "",
) -> str:
    final_caption = str(caption or "")
    raw_visible_len = visible_length(final_caption)
    if raw_visible_len > 1024:
        final_caption = safe_trim_caption(final_caption, 1024)
    logger.info(
        "Shorts public caption: media_id=%s index=%d/%d range=%s-%s "
        "raw_visible_len=%d final_visible_len=%d caption=%r",
        media_id,
        index,
        total,
        start,
        end,
        raw_visible_len,
        visible_length(final_caption),
        final_caption,
    )
    return final_caption


def _unlink(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


async def process_and_send_shorts(
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
    workdir: Optional[Path] = None,
    livedub_video_path: Optional[Path] = None,
) -> None:
    """Render, transform, verify and deliver ordinary Shorts without runtime patches."""
    video_path: Path | None = None
    livedub_source: Path | None = None
    short_paths: list[Path] = []
    poster_paths: list[Path] = []
    keep_for_montage = False

    try:
        speed = float(await ashorts_speed_get())
        if speed <= 0:
            raise RuntimeError(f"Shorts speed must be positive, got {speed!r}")

        if livedub_video_path:
            candidate_source = Path(livedub_video_path)
            if candidate_source.exists():
                livedub_source = candidate_source
                duration = await probe_livedub_source_duration(
                    livedub_source,
                    fallback_duration=float(duration or 0.0),
                )

        candidates = await create_shorts_candidates(
            mp3_path=mp3_path,
            ai_data=ai_data,
            title=title,
            performer=performer,
            duration=duration,
            existing_audio_part=existing_audio_part,
            existing_client=existing_client,
            speed=speed,
        )
        if livedub_source is not None:
            candidates = align_livedub_candidates(
                candidates,
                source_duration=float(duration),
                public_max_seconds=180.0,
            )
        if not candidates:
            logger.info("Shorts: кандидаты не найдены")
            return

        if livedub_source is not None:
            video_path = livedub_source
            logger.info("Shorts: using LiveDub video: %s", video_path.name)
        else:
            video_path = await download_video_for_shorts(url, media_id, workdir=workdir)
        if not video_path:
            await update.message.reply_text("✂️ Не удалось скачать видео для Shorts.")
            return

        format_name = str((ai_data or {}).get("format") or "other")
        real_author = str((ai_data or {}).get("real_author") or performer or "")
        real_event = str((ai_data or {}).get("real_event") or "")
        visual_mode = get_shorts_visual_mode(format_name)
        do_normalize = bool(await asettings_get("shorts_audio_normalize"))
        do_snapshot = bool(await asettings_get("shorts_snapshot"))
        do_subtitles = bool(await asettings_get("shorts_subtitles"))
        do_boundary_pad = (
            False
            if livedub_source is not None
            else bool(await asettings_get("shorts_boundary_padding"))
        )
        do_title_poster = bool(await asettings_get("shorts_title_poster"))
        keep_for_montage = bool(
            await asettings_get("shorts_montage")
            or await asettings_get("shorts_highlights")
            or await asettings_get("clips")
        )

        logger.info(
            "Shorts: format=%s visual=%s normalize=%s speed=%s snapshot=%s "
            "subtitles=%s title_poster=%s boundary_pad=%s",
            format_name,
            visual_mode,
            do_normalize,
            speed,
            do_snapshot,
            do_subtitles,
            do_title_poster,
            do_boundary_pad,
        )

        total = len(candidates)
        sent = 0
        for index, candidate in enumerate(candidates, 1):
            raw_path = DOWNLOAD_DIR / f"{media_id}_short_{index}_raw.mp4"
            post_path = DOWNLOAD_DIR / f"{media_id}_short_{index}_post.mp4"
            sub_path = DOWNLOAD_DIR / f"{media_id}_short_{index}_sub.mp4"
            snapshot_path = DOWNLOAD_DIR / f"{media_id}_short_{index}_snap.jpg"
            poster_path = DOWNLOAD_DIR / f"{media_id}_short_{index}_poster.jpg"
            short_paths.extend((raw_path, post_path, sub_path))
            poster_paths.extend((snapshot_path, poster_path))
            for path in (raw_path, post_path, sub_path, snapshot_path, poster_path):
                _unlink(path)

            window = plan_short_source_window(
                candidate,
                speed=speed,
                boundary_padding=do_boundary_pad,
                source_duration=duration,
            )
            if window is None:
                logger.warning(
                    "Shorts %d/%d rejected: invalid/over-budget source window %r",
                    index,
                    total,
                    candidate,
                )
                continue
            render_start, render_end, snap_ceiling = window

            rendered = await render_short_clip(
                video_path,
                raw_path,
                render_start,
                render_end,
                visual_mode=visual_mode,
                silence_snap_max_end=snap_ceiling,
            )
            if not rendered:
                logger.warning("Shorts %d/%d: render failed", index, total)
                continue

            raw_probe = await probe_media_async(raw_path)
            if not media_probe_is_deliverable(raw_probe):
                logger.warning("Shorts %d/%d: raw media probe failed", index, total)
                continue
            assert raw_probe is not None
            raw_duration = float(raw_probe.duration)

            current_path = raw_path
            speed_applied = False
            speed_required = short_speed_transform_required(speed)
            if do_normalize or speed_required:
                post_ok = await postprocess_short(
                    raw_path,
                    post_path,
                    normalize_audio=do_normalize,
                    speed=speed,
                )
                if post_ok:
                    current_path = post_path
                    speed_applied = speed_required
                elif speed_required:
                    logger.warning(
                        "Shorts %d/%d rejected: required speed transform %.6g failed",
                        index,
                        total,
                        speed,
                    )
                    continue
                else:
                    logger.warning(
                        "Shorts %d/%d: optional normalization failed; using verified raw media",
                        index,
                        total,
                    )

            pre_subtitle_path = current_path
            subtitles_applied = False
            subtitle_fallback_notice = ""
            nosub_path: Path | None = None

            if do_subtitles and HAS_FASTER_WHISPER:
                nosub_path = DOWNLOAD_DIR / f"{media_id}_short_{index}_nosub.mp4"
                try:
                    shutil.copy2(current_path, nosub_path)
                except Exception as exc:
                    logger.warning(
                        "Shorts %d/%d: nosub copy failed: %s",
                        index,
                        total,
                        exc,
                    )
                    nosub_path = None

            if do_subtitles:
                if not HAS_FASTER_WHISPER:
                    logger.warning("Shorts subtitles requested but faster-whisper is unavailable")
                else:
                    try:
                        segments = await transcribe_short_clip(current_path, ai_data=ai_data)
                        if segments:
                            burned = await burn_subtitles_into_short(
                                current_path,
                                sub_path,
                                segments,
                            )
                            if burned:
                                current_path = sub_path
                                subtitles_applied = True
                            else:
                                _unlink(nosub_path)
                                nosub_path = None
                        else:
                            _unlink(nosub_path)
                            nosub_path = None
                    except Exception as exc:
                        logger.warning(
                            "Shorts %d/%d subtitle pipeline failed: %s: %s",
                            index,
                            total,
                            type(exc).__name__,
                            exc,
                        )
                        _unlink(nosub_path)
                        nosub_path = None

            max_upload_mb = float(get_max_file_size_mb())
            selection = select_delivery_file(
                current_path,
                pre_subtitle_path if current_path != pre_subtitle_path else None,
                max_size_mb=max_upload_mb,
            )
            if selection.path is None:
                logger.warning(
                    "Shorts %d/%d rejected: no delivery file (%s)",
                    index,
                    total,
                    selection.reason,
                )
                _unlink(nosub_path)
                continue

            current_path = selection.path
            delivery_selection = selection.selected
            delivery_reason = selection.reason
            if selection.selected == "fallback":
                subtitles_applied = False
                subtitle_fallback_notice = (
                    "⚠️ Субтитры сняты: версия с ними превысила допустимый размер "
                    "или не сохранилась корректно. Видео отправлено без потери основного материала."
                )
                _unlink(nosub_path)
                nosub_path = None

            final_probe = await probe_media_async(current_path)
            if not media_probe_is_deliverable(final_probe) and current_path != pre_subtitle_path:
                fallback = select_delivery_file(
                    pre_subtitle_path,
                    None,
                    max_size_mb=max_upload_mb,
                )
                fallback_probe = (
                    await probe_media_async(fallback.path)
                    if fallback.path is not None
                    else None
                )
                if fallback.path is not None and media_probe_is_deliverable(fallback_probe):
                    current_path = fallback.path
                    final_probe = fallback_probe
                    delivery_selection = "fallback"
                    delivery_reason = "fallback_after_primary_media_probe_rejection"
                    subtitles_applied = False
                    subtitle_fallback_notice = (
                        "⚠️ Субтитры сняты: версия с ними не прошла финальную проверку."
                    )
                    _unlink(nosub_path)
                    nosub_path = None

            if not final_public_short_is_safe(
                final_probe,
                max_file_size_mb=max_upload_mb,
            ):
                logger.warning(
                    "Shorts %d/%d rejected by final public safety gate: probe=%r limit=%.1fMB",
                    index,
                    total,
                    final_probe,
                    max_upload_mb,
                )
                _unlink(nosub_path)
                continue
            assert final_probe is not None

            timing = resolve_delivery_timing(
                source_start=render_start,
                raw_duration=raw_duration,
                source_duration=float(duration or 0.0),
                speed=speed,
                speed_applied=speed_applied,
                final_duration=final_probe.duration,
            )
            delivery_duration = timing.delivery_duration
            delivery_candidate = {
                **candidate,
                "_render_start_seconds": timing.source_start,
                "_render_end_seconds": timing.source_end,
                "_raw_duration_seconds": timing.raw_duration,
                "_delivery_duration_seconds": timing.delivery_duration,
                "_speed_applied": timing.speed_applied,
                "_delivery_file_selection": delivery_selection,
                "_delivery_file_reason": delivery_reason,
            }
            logger.info(
                "Shorts %d/%d delivery evidence: source=%.3f-%.3f raw=%.3fs "
                "final=%.3fs speed_applied=%s size=%.1fMB selected=%s reason=%s",
                index,
                total,
                timing.source_start,
                timing.source_end,
                timing.raw_duration,
                timing.delivery_duration,
                timing.speed_applied,
                file_size_mb(current_path),
                delivery_selection,
                delivery_reason,
            )

            thumb = None
            if do_title_poster:
                try:
                    if await create_short_title_poster(
                        current_path,
                        poster_path,
                        str(candidate.get("title") or ""),
                        delivery_duration,
                    ) and poster_path.exists():
                        thumb = InputFile(poster_path.read_bytes(), filename=poster_path.name)
                except Exception as exc:
                    logger.warning("Shorts %d/%d title poster failed: %s", index, total, exc)
            if thumb is None and do_snapshot:
                try:
                    if await create_short_snapshot(
                        current_path,
                        snapshot_path,
                        delivery_duration,
                    ) and snapshot_path.exists():
                        thumb = InputFile(snapshot_path.read_bytes(), filename=snapshot_path.name)
                except Exception as exc:
                    logger.warning("Shorts %d/%d snapshot failed: %s", index, total, exc)

            caption = _finalize_short_caption_for_delivery(
                build_short_caption(
                    candidate=candidate,
                    performer=performer,
                    real_author=real_author,
                    real_event=real_event,
                    format_name=format_name,
                    yt_url=url,
                    vk_url=vk_url,
                    rutube_url=rutube_url,
                ),
                media_id=media_id,
                index=index,
                total=total,
                start=candidate.get("start"),
                end=candidate.get("end"),
            )

            try:
                short_id = uuid.uuid4().hex[:16]
                short_trim_save(
                    short_id=short_id,
                    video_path=str(video_path),
                    start_seconds=timing.source_start,
                    end_seconds=timing.source_end,
                    visual_mode=visual_mode,
                    yt_url=url,
                    vk_url=vk_url,
                    rutube_url=rutube_url,
                    performer=performer,
                    real_author=real_author,
                    real_event=real_event,
                    format_name=format_name,
                    candidate_json=json.dumps(delivery_candidate, ensure_ascii=False),
                    video_path_nosub=str(nosub_path) if nosub_path else "",
                    nosub_expiry=int(time.time()) + 86400 if nosub_path else 0,
                    source_duration=int(float(duration or 0.0)),
                )
                nosub_buttons = (
                    [InlineKeyboardButton("🚫Sub", callback_data=f"strim:nosub:{short_id}")]
                    if subtitles_applied
                    else []
                )
                trim_keyboard = InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton("⏪ Начало -10", callback_data=f"strim:s10:{short_id}"),
                        InlineKeyboardButton("⏭ Конец +10", callback_data=f"strim:e10:{short_id}"),
                        InlineKeyboardButton("⏭⏭ Конец +20", callback_data=f"strim:e20:{short_id}"),
                        *nosub_buttons,
                    ]]
                )
                await update.message.reply_video(
                    video=current_path,
                    caption=caption,
                    duration=max(1, int(round(delivery_duration))),
                    width=720,
                    height=1280,
                    thumbnail=thumb,
                    parse_mode="HTML",
                    reply_markup=trim_keyboard,
                    write_timeout=120,
                    read_timeout=120,
                    connect_timeout=30,
                )
                sent += 1
                if subtitle_fallback_notice:
                    try:
                        await update.message.reply_text(subtitle_fallback_notice)
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning("Shorts %d/%d send failed: %s", index, total, exc)
                _unlink(nosub_path)

        logger.info("Shorts: delivered %d/%d", sent, total)

    except Exception as exc:
        logger.warning("Shorts process_and_send error: %s", exc)
        try:
            await update.message.reply_text(
                f"✂️ Ошибка при подготовке Shorts: {str(exc)[:150]}"
            )
        except Exception:
            pass
    finally:
        for path in (*short_paths, *poster_paths):
            _unlink(path)
        if video_path:
            try:
                borrowed = livedub_source is not None and video_path == livedub_source
                if not keep_for_montage and not borrowed:
                    video_path.unlink(missing_ok=True)
            except OSError:
                pass


__all__ = ["process_and_send_shorts"]
