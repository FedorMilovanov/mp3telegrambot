#!/usr/bin/env python3
"""
Montage / Highlights Pipeline.
Извлечено из bot.py строки 12240–12432.
"""
from core.globals import DOWNLOAD_DIR
from core.database import asettings_get, shorts_speed_get, ashorts_speed_get, MAX_FILE_SIZE_MB  # AUDIT M4
from core.utils import cleanup_files
from services.render_clips_montage import (
    render_montage_short, create_extras_candidates,
    build_montage_caption, build_highlights_caption,  # FIX montage
)
from services.telegraph import create_telegraph_synopsis
from core.progress import set_progress
from services.shorts_video import (
    HAS_FASTER_WHISPER,
    download_video_for_shorts,    # FIX montage
    postprocess_short,            # FIX montage
    transcribe_short_clip,        # FIX montage
    burn_subtitles_into_short,    # FIX montage
    create_short_snapshot,        # FIX montage
    create_short_title_poster,    # FIX montage
    get_shorts_visual_mode,       # FIX montage
)
from converters.md_telegraph import visible_length, safe_trim_caption

import asyncio
import logging
from io import BytesIO    # FIX montage
from pathlib import Path

logger = logging.getLogger(__name__)

async def _run_montage_or_highlights_pipeline(
    cand: dict, video_path: Path, media_id: str, prefix: str,
    ai_data: dict, performer: str, url: str, rutube_url: str, vk_url: str,
    update, caption_fn,
) -> bool:
    """Общий рендер-pipeline для montage и highlights."""
    format_name  = (ai_data or {}).get("format", "other") or "other"
    # real_author передаётся снаружи через caption_fn (closure) — внутри pipeline не нужен
    visual_mode  = get_shorts_visual_mode(format_name)
    do_normalize = await asettings_get("shorts_audio_normalize")
    do_snapshot  = await asettings_get("shorts_snapshot")
    do_subtitles = await asettings_get("shorts_subtitles")
    do_poster    = await asettings_get("shorts_title_poster")
    speed        = float(await ashorts_speed_get())  # AUDIT M4

    raw_path      = DOWNLOAD_DIR / f"{media_id}_{prefix}_raw.mp4"
    post_path     = DOWNLOAD_DIR / f"{media_id}_{prefix}_post.mp4"
    sub_path      = DOWNLOAD_DIR / f"{media_id}_{prefix}_sub.mp4"
    snapshot_path = DOWNLOAD_DIR / f"{media_id}_{prefix}_snap.jpg"
    poster_path   = DOWNLOAD_DIR / f"{media_id}_{prefix}_poster.jpg"
    cleanup_paths = [raw_path, post_path, sub_path, snapshot_path, poster_path]

    try:
        ok = await render_montage_short(
            source_video_path=video_path, output_path=raw_path,
            fragments=cand["fragments"], visual_mode=visual_mode,
        )
        if not ok:
            return False
        size_mb = raw_path.stat().st_size / (1024 * 1024) if raw_path.exists() else 0
        if size_mb > MAX_FILE_SIZE_MB:
            logger.warning(f"{prefix}: файл {size_mb:.1f}MB > {MAX_FILE_SIZE_MB}MB, пропускаем")
            return False

        need_post = do_normalize or (abs(speed - 1.0) > 0.01)
        current_path = raw_path
        if need_post:
            post_ok = await postprocess_short(raw_path, post_path, normalize_audio=do_normalize, speed=speed)
            if post_ok:
                current_path = post_path

        if do_subtitles and HAS_FASTER_WHISPER:
            try:
                segments = await transcribe_short_clip(current_path, ai_data=ai_data)
                if segments:
                    sub_ok = await burn_subtitles_into_short(current_path, sub_path, segments)
                    if sub_ok:
                        current_path = sub_path
            except Exception as sub_err:
                logger.warning(f"{prefix}: subtitle error: {sub_err}")

        thumb_buf = None
        total_dur = cand["total_dur"]
        if do_poster:
            try:
                if await create_short_title_poster(current_path, poster_path, cand["title"], total_dur) and poster_path.exists():
                    thumb_buf = open(poster_path, "rb")
                    thumb_buf.name = poster_path.name
            except Exception:
                pass
        if thumb_buf is None and do_snapshot:
            try:
                if await create_short_snapshot(current_path, snapshot_path, total_dur) and snapshot_path.exists():
                    thumb_buf = open(snapshot_path, "rb")
                    thumb_buf.name = snapshot_path.name
            except Exception:
                pass

        caption = caption_fn()
        if visible_length(caption) > 1024:
            caption = safe_trim_caption(caption, 1024)

        try:
            with open(current_path, "rb") as vf:
                await update.message.reply_video(
                    video=vf, caption=caption, duration=int(total_dur),
                    width=720, height=1280, thumbnail=thumb_buf,
                    parse_mode="HTML",
                    write_timeout=120, read_timeout=120, connect_timeout=30,
                )
            return True
        except Exception as send_err:
            logger.warning(f"{prefix}: ошибка отправки: {send_err}")
            return False
        finally:
            if thumb_buf:
                try: thumb_buf.close()
                except Exception: pass
    finally:
        for p in cleanup_paths:
            try: p.unlink(missing_ok=True)
            except Exception: pass


async def process_and_send_montage(
    url: str, media_id: str, mp3_path: Path, title: str, performer: str,
    duration: int, ai_data: dict, update,
    existing_audio_part=None, existing_client=None,
    rutube_url: str = "", vk_url: str = "",
    prefetched_candidates: list[dict] | None = None,
) -> None:
    video_path, owned_video = None, False
    try:
        candidates = prefetched_candidates or []
        if not candidates:
            logger.info("Montage: кандидаты не найдены")
            try:
                await update.message.reply_text("🎬 Montage для этого материала не найден.")
            except Exception:
                pass
            return
        video_path = await download_video_for_shorts(url, media_id)
        if not video_path:
            logger.warning("Montage: не удалось скачать видео")
            return
        owned_video = True
        real_author = (ai_data or {}).get("real_author", "") or performer or ""
        format_name = (ai_data or {}).get("format", "other") or "other"
        sent = 0
        for i, cand in enumerate(candidates, 1):
            logger.info(f"Montage: рендер {i}/{len(candidates)} '{cand['title']}'")
            ok = await _run_montage_or_highlights_pipeline(
                cand=cand, video_path=video_path, media_id=media_id,
                prefix=f"montage_{i}", ai_data=ai_data, performer=performer,
                url=url, rutube_url=rutube_url, vk_url=vk_url, update=update,
                caption_fn=lambda c=cand: build_montage_caption(
                    theme=c["theme"], title=c["title"], performer=performer,
                    real_author=real_author, format_name=format_name,
                    fragment_count=len(c["fragments"]), hashtags=c["hashtags"],
                    yt_url=url, vk_url=vk_url, rutube_url=rutube_url,
                ),
            )
            if ok:
                sent += 1
        logger.info(f"Montage: итого отправлено {sent}/{len(candidates)}")
    except Exception as e:
        logger.warning(f"Montage process_and_send error: {e}")
    finally:
        if video_path and owned_video:
            try:
                # V3-P0: montage больше не оставляет видео "для highlights".
                # Если highlights-кандидатов нет или highlights упадёт до download,
                # файл становился orphaned. Лучше удалить и при необходимости скачать
                # заново в highlights, чем копить большие видео в downloads/.
                video_path.unlink(missing_ok=True)
            except Exception as _cleanup_err:
                logger.warning("Montage: не удалось удалить временное видео %s: %s", video_path, _cleanup_err)


async def process_and_send_highlights(
    url: str, media_id: str, mp3_path: Path, title: str, performer: str,
    duration: int, ai_data: dict, update,
    existing_audio_part=None, existing_client=None,
    rutube_url: str = "", vk_url: str = "",
    prefetched_candidates: list[dict] | None = None,
) -> None:
    video_path, owned_video = None, False
    try:
        candidates = prefetched_candidates or []
        if not candidates:
            logger.info("Highlights: кандидат не найден")
            try:
                await update.message.reply_text("🌟 Highlights для этого материала не найден.")
            except Exception:
                pass
            return
        video_path = await download_video_for_shorts(url, media_id)
        if not video_path:
            logger.warning("Highlights: не удалось скачать видео")
            return
        owned_video = True
        real_author = (ai_data or {}).get("real_author", "") or performer or ""
        format_name = (ai_data or {}).get("format", "other") or "other"
        cand = candidates[0]
        if not cand.get("fragments") or not cand.get("title"):
            logger.warning("Highlights: кандидат невалидный (нет fragments/title): %s", list(cand.keys()))
            try:
                await update.message.reply_text("🌟 Highlights: данные кандидата повреждены.")
            except Exception:
                pass
            return
        logger.info(f"Highlights: рендер '{cand['title']}'")
        ok = await _run_montage_or_highlights_pipeline(
            cand=cand, video_path=video_path, media_id=media_id,
            prefix="highlights", ai_data=ai_data, performer=performer,
            url=url, rutube_url=rutube_url, vk_url=vk_url, update=update,
            caption_fn=lambda c=cand: build_highlights_caption(
                title=c["title"], performer=performer, real_author=real_author,
                format_name=format_name, fragment_count=len(c["fragments"]),
                hashtags=c["hashtags"], yt_url=url, vk_url=vk_url, rutube_url=rutube_url,
            ),
        )
        if not ok:
            logger.warning("Highlights: рендер не удался")
    except Exception as e:
        logger.warning(f"Highlights process_and_send error: {e}")
    finally:
        if video_path and owned_video:
            try: video_path.unlink(missing_ok=True)
            except Exception: pass


# ─── Прогресс-бар ────────────────────────────────────────────

