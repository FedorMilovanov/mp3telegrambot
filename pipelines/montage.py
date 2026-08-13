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
    create_short_snapshot,        # FIX montage
    create_short_title_poster,    # FIX montage
    get_shorts_visual_mode,       # FIX montage
)
from services.shorts_subtitle_burn import burn_subtitles_into_short
from services.highlights_candidate_gate import verify_highlights_candidate
from converters.md_telegraph import visible_length, safe_trim_caption
from services.media_delivery_probe import (
    file_size_mb,
    media_probe_is_deliverable,
    probe_media_async,
    select_delivery_file,
    verify_highlights_delivery,
)
from telegram import InputFile  # AUDIT R25: thumbnail без BufferedReader.name (py3.13)

import json
import logging
import math
from pathlib import Path

logger = logging.getLogger(__name__)


def _validated_extras_speed(value) -> tuple[float, bool] | None:
    """Return finite positive speed and whether the public transform is required."""
    try:
        speed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(speed) or speed <= 0.0:
        return None
    return speed, abs(speed - 1.0) > 0.01


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
    speed_state = _validated_extras_speed(await ashorts_speed_get())
    if speed_state is None:
        logger.warning("%s: invalid Shorts speed setting; refusing public render", prefix)
        return False
    speed, speed_required = speed_state

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

        total_dur = float(cand["total_dur"])
        raw_probe = await probe_media_async(raw_path)
        raw_duration = (
            raw_probe.duration
            if raw_probe is not None and raw_probe.duration > 0
            else max(0.001, total_dur)
        )
        size_mb = file_size_mb(raw_path)
        if size_mb > get_max_file_size_mb():
            logger.warning(
                "%s: файл %.1fMB > %sMB, пропускаем",
                prefix,
                size_mb,
                get_max_file_size_mb(),
            )
            return False

        need_post = do_normalize or speed_required
        current_path = raw_path
        speed_applied = False
        if need_post:
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
                    "%s: required speed transform %.6g failed; refusing raw "
                    "fallback with the wrong playback speed",
                    prefix,
                    speed,
                )
                return False
            else:
                logger.warning(
                    "%s: optional normalize failed; verified raw media remains "
                    "eligible because playback speed is 1.0",
                    prefix,
                )

        expected_delivery_duration = (
            raw_duration / speed if speed_applied else raw_duration
        )
        pre_subtitle_path = current_path

        if do_subtitles and HAS_FASTER_WHISPER:
            try:
                segments = cand.get("_subtitle_segments") if verified_highlights else None
                if segments:
                    from services.highlights_quality import scale_subtitle_segments

                    segments = scale_subtitle_segments(segments, speed if speed_applied else 1.0)
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

        max_upload_mb = get_max_file_size_mb()
        selection = select_delivery_file(
            current_path,
            pre_subtitle_path if current_path != pre_subtitle_path else None,
            max_size_mb=max_upload_mb,
        )
        if selection.path is None:
            logger.warning(
                "%s: нет пригодного финального файла: reason=%s primary=%.1fMB "
                "fallback=%.1fMB limit=%sMB",
                prefix,
                selection.reason,
                selection.primary_size_mb,
                selection.fallback_size_mb,
                max_upload_mb,
            )
            return False
        current_path = selection.path
        delivery_selection = selection.selected
        delivery_reason = selection.reason
        if selection.selected == "fallback":
            logger.warning(
                "%s: subtitle-артефакт отклонён (%s, %.1fMB); "
                "использую pre-subtitle файл %.1fMB",
                prefix,
                selection.reason,
                selection.primary_size_mb,
                selection.fallback_size_mb,
            )

        final_probe = await probe_media_async(current_path)
        if not media_probe_is_deliverable(final_probe):
            if current_path != pre_subtitle_path:
                fallback_selection = select_delivery_file(
                    pre_subtitle_path,
                    None,
                    max_size_mb=max_upload_mb,
                )
                fallback_probe = (
                    await probe_media_async(fallback_selection.path)
                    if fallback_selection.path is not None
                    else None
                )
                if (
                    fallback_selection.path is not None
                    and media_probe_is_deliverable(fallback_probe)
                ):
                    logger.warning(
                        "%s: subtitle-артефакт не прошёл media probe; "
                        "использую проверенный pre-subtitle файл. probe=%r",
                        prefix,
                        final_probe,
                    )
                    current_path = fallback_selection.path
                    final_probe = fallback_probe
                    delivery_selection = "fallback"
                    delivery_reason = "fallback_after_primary_media_probe_rejection"
            if not media_probe_is_deliverable(final_probe):
                logger.warning(
                    "%s: ни primary, ни fallback media probe не подтвердили "
                    "пригодный video+audio файл",
                    prefix,
                )
                return False

        assert final_probe is not None
        delivery_duration = final_probe.duration
        delivery_report = None
        if verified_highlights:
            delivery_report = await verify_highlights_delivery(
                current_path,
                expected_duration=expected_delivery_duration,
            )
            if (
                not delivery_report.get("accepted")
                and current_path != pre_subtitle_path
            ):
                fallback_selection = select_delivery_file(
                    pre_subtitle_path,
                    None,
                    max_size_mb=max_upload_mb,
                )
                if fallback_selection.path is not None:
                    fallback_report = await verify_highlights_delivery(
                        fallback_selection.path,
                        expected_duration=expected_delivery_duration,
                    )
                    if fallback_report.get("accepted"):
                        logger.warning(
                            "%s: subtitle-версия не прошла final QA; "
                            "pre-subtitle версия прошла и будет доставлена. "
                            "subtitle_report=%s",
                            prefix,
                            json.dumps(delivery_report, ensure_ascii=False)[:3000],
                        )
                        current_path = fallback_selection.path
                        final_probe = await probe_media_async(current_path)
                        if not media_probe_is_deliverable(final_probe):
                            return False
                        assert final_probe is not None
                        delivery_duration = final_probe.duration
                        delivery_report = fallback_report
                        delivery_selection = "fallback"
                        delivery_reason = "fallback_after_primary_final_qa_rejection"
            if not delivery_report.get("accepted"):
                logger.warning(
                    "%s: final delivery QA rejected: %s",
                    prefix,
                    json.dumps(delivery_report, ensure_ascii=False)[:5000],
                )
                return False
            logger.info(
                "%s: final delivery QA accepted: %s",
                prefix,
                json.dumps(delivery_report, ensure_ascii=False)[:5000],
            )

        logger.info(
            "%s: delivery evidence selected=%s reason=%s duration=%.3fs "
            "size=%.1fMB",
            prefix,
            delivery_selection,
            delivery_reason,
            delivery_duration,
            file_size_mb(current_path),
        )

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
        verified_cand, quality_report = await verify_highlights_candidate(
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
