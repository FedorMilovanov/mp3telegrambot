#!/usr/bin/env python3
"""
Montage / Highlights Pipeline.
Извлечено из bot.py строки 12240–12432.
"""
from core.globals import DOWNLOAD_DIR
from core.database import asettings_get, ashorts_speed_get, get_max_file_size_mb  # AUDIT M4
from services.render_clips_montage import (
    render_montage_short, create_extras_candidates,
    build_montage_caption, build_highlights_caption,  # FIX montage
)
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
from telegram import InputFile  # AUDIT R25: thumbnail без BufferedReader.name (py3.13)

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def _run_montage_or_highlights_pipeline(
    cand: dict, video_path: Path, media_id: str, prefix: str,
    ai_data: dict, performer: str, url: str, rutube_url: str, vk_url: str,
    update, caption_fn, *, verified_highlights: bool = False,
) -> bool:
    """Общий render pipeline; Highlights may use the verified renderer."""
    format_name = (ai_data or {}).get("format", "other") or "other"
    visual_mode = get_shorts_visual_mode(format_name)
    do_normalize = await asettings_get("shorts_audio_normalize")
    do_snapshot = await asettings_get("shorts_snapshot")
    do_subtitles = await asettings_get("shorts_subtitles")
    do_poster = await asettings_get("shorts_title_poster")
    speed = float(await ashorts_speed_get())  # AUDIT M4

    raw_path = DOWNLOAD_DIR / f"{media_id}_{prefix}_raw.mp4"
    post_path = DOWNLOAD_DIR / f"{media_id}_{prefix}_post.mp4"
    sub_path = DOWNLOAD_DIR / f"{media_id}_{prefix}_sub.mp4"
    snapshot_path = DOWNLOAD_DIR / f"{media_id}_{prefix}_snap.jpg"
    poster_path = DOWNLOAD_DIR / f"{media_id}_{prefix}_poster.jpg"
    cleanup_paths = [raw_path, post_path, sub_path, snapshot_path, poster_path]

    try:
        if verified_highlights:
            from services.highlights_quality import render_verified_highlights

            ok = await render_verified_highlights(
                source_video_path=video_path,
                output_path=raw_path,
                fragments=cand["fragments"],
                visual_mode=visual_mode,
            )
        else:
            ok = await render_montage_short(
                source_video_path=video_path,
                output_path=raw_path,
                fragments=cand["fragments"],
                visual_mode=visual_mode,
            )
        if not ok:
            return False

        size_mb = raw_path.stat().st_size / (1024 * 1024) if raw_path.exists() else 0
        if size_mb > get_max_file_size_mb():
            logger.warning(
                "%s: файл %.1fMB > %sMB, пропускаем",
                prefix,
                size_mb,
                get_max_file_size_mb(),
            )
            return False

        total_dur = float(cand["total_dur"])
        need_post = do_normalize or (abs(speed - 1.0) > 0.01)
        current_path = raw_path
        if need_post:
            post_ok = await postprocess_short(
                raw_path,
                post_path,
                normalize_audio=do_normalize,
                speed=speed,
            )
            if post_ok:
                current_path = post_path

        delivery_duration = total_dur / speed if speed > 0 else total_dur

        if do_subtitles and HAS_FASTER_WHISPER:
            try:
                segments = cand.get("_subtitle_segments") if verified_highlights else None
                if segments:
                    from services.highlights_quality import scale_subtitle_segments

                    segments = scale_subtitle_segments(segments, speed)
                    logger.info(
                        "%s: используем проверенную source-context расшифровку (%d сегм.)",
                        prefix,
                        len(segments),
                    )
                else:
                    segments = await transcribe_short_clip(current_path, ai_data=ai_data)
                if segments:
                    sub_ok = await burn_subtitles_into_short(
                        current_path,
                        sub_path,
                        segments,
                    )
                    if sub_ok:
                        current_path = sub_path
            except Exception as sub_err:
                logger.warning("%s: subtitle error: %s", prefix, sub_err)

        thumb_buf = None
        if do_poster:
            try:
                if (
                    await create_short_title_poster(
                        current_path,
                        poster_path,
                        cand["title"],
                        delivery_duration,
                    )
                    and poster_path.exists()
                ):
                    thumb_buf = InputFile(poster_path.read_bytes(), filename=poster_path.name)
            except Exception:
                pass
        if thumb_buf is None and do_snapshot:
            try:
                if (
                    await create_short_snapshot(
                        current_path,
                        snapshot_path,
                        delivery_duration,
                    )
                    and snapshot_path.exists()
                ):
                    thumb_buf = InputFile(snapshot_path.read_bytes(), filename=snapshot_path.name)
            except Exception:
                pass

        caption = caption_fn()
        if visible_length(caption) > 1024:
            caption = safe_trim_caption(caption, 1024)

        try:
            await update.message.reply_video(
                video=current_path,
                caption=caption,
                duration=max(1, int(round(delivery_duration))),
                width=720,
                height=1280,
                thumbnail=thumb_buf,
                parse_mode="HTML",
                write_timeout=120,
                read_timeout=120,
                connect_timeout=30,
            )
            return True
        except Exception as send_err:
            logger.warning("%s: ошибка отправки: %s", prefix, send_err)
            return False
    finally:
        for path in cleanup_paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


async def process_and_send_montage(
    url: str, media_id: str, mp3_path: Path, title: str, performer: str,
    duration: int, ai_data: dict, update,
    existing_audio_part=None, existing_client=None,
    rutube_url: str = "", vk_url: str = "",
    prefetched_candidates: list[dict] | None = None,
    livedub_video_path=None,  # ENG: path to translated video
) -> None:
    video_path, owned_video = None, False
    try:
        candidates = prefetched_candidates or []
        if not candidates:
            logger.info("Montage: кандидаты не найдены")
            try:
                await update.message.reply_text(
                    "🎬 Montage для этого материала не найден."
                )
            except Exception:
                pass
            return

        from pathlib import Path as _P

        if livedub_video_path and _P(livedub_video_path).exists():
            video_path = _P(livedub_video_path)
            logger.info("Montage: using LiveDub video: %s", video_path.name)
        else:
            video_path = await download_video_for_shorts(url, media_id)
            owned_video = True
        if not video_path:
            logger.warning("Montage: не удалось скачать видео")
            return

        real_author = (ai_data or {}).get("real_author", "") or performer or ""
        format_name = (ai_data or {}).get("format", "other") or "other"
        sent = 0
        for index, cand in enumerate(candidates, 1):
            logger.info(
                "Montage: рендер %d/%d %r",
                index,
                len(candidates),
                cand["title"],
            )
            ok = await _run_montage_or_highlights_pipeline(
                cand=cand,
                video_path=video_path,
                media_id=media_id,
                prefix=f"montage_{index}",
                ai_data=ai_data,
                performer=performer,
                url=url,
                rutube_url=rutube_url,
                vk_url=vk_url,
                update=update,
                caption_fn=lambda c=cand: build_montage_caption(
                    theme=c["theme"],
                    title=c["title"],
                    performer=performer,
                    real_author=real_author,
                    format_name=format_name,
                    fragment_count=len(c["fragments"]),
                    hashtags=c["hashtags"],
                    yt_url=url,
                    vk_url=vk_url,
                    rutube_url=rutube_url,
                ),
            )
            if ok:
                sent += 1
        logger.info("Montage: итого отправлено %d/%d", sent, len(candidates))
    except Exception as exc:
        logger.warning("Montage process_and_send error: %s", exc)
    finally:
        if video_path and owned_video:
            try:
                video_path.unlink(missing_ok=True)
            except Exception as cleanup_err:
                logger.warning(
                    "Montage: не удалось удалить временное видео %s: %s",
                    video_path,
                    cleanup_err,
                )


async def process_and_send_highlights(
    url: str, media_id: str, mp3_path: Path, title: str, performer: str,
    duration: int, ai_data: dict, update,
    existing_audio_part=None, existing_client=None,
    rutube_url: str = "", vk_url: str = "",
    prefetched_candidates: list[dict] | None = None,
    livedub_video_path=None,  # ENG: path to translated video
) -> None:
    video_path, owned_video = None, False
    try:
        candidates = prefetched_candidates or []
        if not candidates:
            logger.info("Highlights: кандидат не найден")
            try:
                await update.message.reply_text(
                    "🌟 Highlights для этого материала не найден."
                )
            except Exception:
                pass
            return

        from pathlib import Path as _P

        if livedub_video_path and _P(livedub_video_path).exists():
            video_path = _P(livedub_video_path)
            logger.info("Highlights: using LiveDub video: %s", video_path.name)
        else:
            video_path = await download_video_for_shorts(url, media_id)
            owned_video = True
        if not video_path:
            logger.warning("Highlights: не удалось скачать видео")
            return

        real_author = (ai_data or {}).get("real_author", "") or performer or ""
        format_name = (ai_data or {}).get("format", "other") or "other"
        cand = candidates[0]
        if not cand.get("fragments") or not cand.get("title"):
            logger.warning(
                "Highlights: кандидат невалидный (нет fragments/title): %s",
                list(cand.keys()),
            )
            try:
                await update.message.reply_text(
                    "🌟 Highlights: данные кандидата повреждены."
                )
            except Exception:
                pass
            return

        # The extras pass only proposes approximate times from compressed text.
        # Before touching the public renderer, prove actual speech boundaries and
        # thematic coherence from one source-context Whisper probe.
        from services.highlights_quality import refine_highlights_candidate

        verified_cand, quality_report = await refine_highlights_candidate(
            video_path,
            cand,
            ai_data=ai_data,
            source_duration=float(duration or 0),
        )
        if verified_cand is None:
            logger.warning(
                "Highlights rejected by quality gate: %s",
                json.dumps(quality_report, ensure_ascii=False)[:5000],
            )
            try:
                await update.message.reply_text(
                    "🌟 Highlights пропущен: автоматическая проверка не смогла "
                    "доказать целостные границы фраз и единую тему. "
                    "Основные материалы уже готовы."
                )
            except Exception:
                pass
            return
        cand = verified_cand

        logger.info(
            "Highlights: verified render %r fragments=%d total=%.2fs",
            cand["title"],
            len(cand["fragments"]),
            float(cand["total_dur"]),
        )
        ok = await _run_montage_or_highlights_pipeline(
            cand=cand,
            video_path=video_path,
            media_id=media_id,
            prefix="highlights",
            ai_data=ai_data,
            performer=performer,
            url=url,
            rutube_url=rutube_url,
            vk_url=vk_url,
            update=update,
            caption_fn=lambda c=cand: build_highlights_caption(
                title=c["title"],
                performer=performer,
                real_author=real_author,
                format_name=format_name,
                fragment_count=len(c["fragments"]),
                hashtags=c["hashtags"],
                yt_url=url,
                vk_url=vk_url,
                rutube_url=rutube_url,
            ),
            verified_highlights=True,
        )
        if not ok:
            logger.warning("Highlights: verified render не удался")
    except Exception as exc:
        logger.warning("Highlights process_and_send error: %s", exc)
    finally:
        if video_path and owned_video:
            try:
                video_path.unlink(missing_ok=True)
            except Exception:
                pass


# Re-export for the main pipeline; candidate generation remains one shared
# text-only proposal request, while public Highlights now has a separate proof gate.
__all__ = [
    "create_extras_candidates",
    "process_and_send_highlights",
    "process_and_send_montage",
]
