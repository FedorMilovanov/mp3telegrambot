#!/usr/bin/env python3
"""
Shorts Pipeline — process_and_send_shorts.
Извлечено из bot.py строки 11549–11920.
"""
from __future__ import annotations

from core.globals import (
    DOWNLOAD_DIR, HAS_GEMINI,
    InlineKeyboardButton, InlineKeyboardMarkup,  # FIX shorts
)
from core.database import (
    adb_get, adb_save, asettings_get, settings_get,
    short_trim_save, shorts_speed_get,
    ashorts_speed_get, get_max_file_size_mb,      # AUDIT M4
)
from core.utils import cleanup_files
from services.shorts_video import (
    render_short_clip, postprocess_short, create_short_snapshot,
    build_short_caption, HAS_FASTER_WHISPER,
    download_video_for_shorts,      # FIX shorts
    transcribe_short_clip,          # FIX shorts
    create_short_title_poster,      # FIX shorts
    get_shorts_visual_mode,         # FIX shorts
)
from services.shorts_subtitle_burn import burn_subtitles_into_short
from converters.md_telegraph import visible_length, safe_trim_caption
from services.shorts_candidates import create_shorts_candidates
from services.shorts_factory_media import (
    align_livedub_candidates,
    probe_livedub_source_duration,
)
from services.media_delivery_probe import (
    file_size_mb,
    media_probe_is_deliverable,
    probe_media_async,
    resolve_delivery_timing,
    select_delivery_file,
)
from telegram import InputFile  # AUDIT R25: thumbnail без BufferedReader.name (py3.13)

import json
import logging
import os
import shutil
import time
import uuid
from io import BytesIO    # FIX shorts
from pathlib import Path
from typing import Optional

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
    """Trim once and log the exact public caption that will be sent.

    The log is intentionally placed at the final delivery boundary, after every
    formatter and Telegram-size trim. It records only text that is already
    public, so live regressions can be reproduced without guessing which
    intermediate hook/title survived the pipeline.
    """
    final_caption = str(caption or "")
    raw_visible_len = visible_length(final_caption)
    if raw_visible_len > 1024:
        final_caption = safe_trim_caption(final_caption, 1024)
    final_visible_len = visible_length(final_caption)
    logger.info(
        "Shorts public caption: media_id=%s index=%d/%d range=%s-%s "
        "raw_visible_len=%d final_visible_len=%d caption=%r",
        media_id,
        index,
        total,
        start,
        end,
        raw_visible_len,
        final_visible_len,
        final_caption,
    )
    return final_caption


async def process_and_send_shorts(
    url: str,
    media_id: str,
    mp3_path: Path,
    title: str,
    performer: str,
    duration: int,
    ai_data: dict,
    update,
    existing_audio_part=None,
    existing_client=None,
    rutube_url: str = "",
    vk_url: str = "",
    workdir: Optional[Path] = None,  # 2026-06-11: пробрасываем временную папку для реюза видео
    livedub_video_path: Optional[Path] = None,  # ENG: path to translated video for shorts
) -> None:
    """
    Полный shorts-пайплайн v2:
      1. Найти кандидатов (Gemini)
      2. Скачать видео
      3. Вырезать short (render_short_clip)
      4. Постобработка: normalize + speed (postprocess_short)
      5. [опц] Субтитры: transcribe → burn_subtitles_into_short
      6. [опц] Постер с заголовком: create_short_title_poster
      7. [опц] Snapshot: create_short_snapshot (fallback thumbnail)
      8. Отправить в Telegram
      9. Логировать результат

    Ошибки в шагах 5–7 не прерывают пайплайн.
    MP3-пайплайн не затронут.
    """
    video_path = None
    livedub_source: Path | None = None
    short_paths: list[Path] = []
    poster_paths: list[Path] = []
    speed = float(await ashorts_speed_get())
    _keep_for_montage = False
    try:
        if livedub_video_path:
            candidate_source = Path(livedub_video_path)
            if candidate_source.exists():
                livedub_source = candidate_source
                duration = int(round(await probe_livedub_source_duration(
                    livedub_source,
                    fallback_duration=float(duration or 0),
                )))

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

        logger.info(f"Shorts: найдено {len(candidates)} кандидатов, скачиваю видео...")

        if livedub_source is not None:
            video_path = livedub_source
            logger.info(f"Shorts: using LiveDub video: {video_path.name}")
        else:
            video_path = await download_video_for_shorts(url, media_id, workdir=workdir)
        if not video_path:
            logger.warning("Shorts: не удалось скачать видео")
            await update.message.reply_text("✂️ Не удалось скачать видео для Shorts.")
            return

        format_name      = (ai_data or {}).get("format", "other") or "other"
        real_author      = (ai_data or {}).get("real_author", "") or performer or ""
        real_event       = (ai_data or {}).get("real_event", "") or ""

        visual_mode      = get_shorts_visual_mode(format_name)
        do_normalize     = await asettings_get("shorts_audio_normalize")
        do_snapshot      = await asettings_get("shorts_snapshot")
        do_subtitles     = await asettings_get("shorts_subtitles")
        do_boundary_pad  = (
            False
            if livedub_source is not None
            else await asettings_get("shorts_boundary_padding")
        )
        do_title_poster  = await asettings_get("shorts_title_poster")
        _keep_for_montage = (
            await asettings_get("shorts_montage")
            or await asettings_get("shorts_highlights")
            or await asettings_get("clips")
        )

        logger.info(
            f"Shorts: format={format_name} visual={visual_mode} "
            f"normalize={do_normalize} speed={speed} snapshot={do_snapshot} "
            f"subtitles={do_subtitles} title_poster={do_title_poster}"
        )

        if do_subtitles and format_name in ("qa", "discussion", "interview"):
            logger.info("Shorts subtitles: формат multi-speaker — субтитры могут ошибаться")

        total = len(candidates)
        sent  = 0

        for i, c in enumerate(candidates, 1):
            raw_path          = DOWNLOAD_DIR / f"{media_id}_short_{i}_raw.mp4"
            post_path         = DOWNLOAD_DIR / f"{media_id}_short_{i}_post.mp4"
            sub_path          = DOWNLOAD_DIR / f"{media_id}_short_{i}_sub.mp4"
            snapshot_path     = DOWNLOAD_DIR / f"{media_id}_short_{i}_snap.jpg"
            title_poster_path = DOWNLOAD_DIR / f"{media_id}_short_{i}_poster.jpg"

            short_paths  += [raw_path, post_path, sub_path]
            poster_paths += [snapshot_path, title_poster_path]

            logger.info(
                f"Shorts: рендер {i}/{total} "
                f"({c['start']}–{c['end']}) '{c['title']}' [{visual_mode}]"
            )

            try:
                render_start = max(0.0, float(c.get("start_seconds", 0)))
                source_end = float(c.get("end_seconds", 0))
            except (TypeError, ValueError):
                logger.warning("Shorts: invalid candidate times %s/%s: %r", i, total, c)
                continue

            if do_boundary_pad:
                pre_roll = float(os.getenv("SHORTS_PREROLL_SECONDS", "1.5"))
                post_roll = float(os.getenv("SHORTS_POSTROLL_SECONDS", "2.5"))
                render_start = max(0.0, render_start - pre_roll)
                source_end = source_end + post_roll

            if render_start != c.get("start_seconds") and not do_boundary_pad:
                logger.warning(
                    "Shorts: start_seconds %.2f < 0 или невалиден — clamp до %.2f",
                    float(c.get("start_seconds", 0) or 0), render_start,
                )
                c["start_seconds"] = render_start
                c["start"] = "0:00"

            speed_extra = 0
            if speed > 1.01:
                speed_extra = int((source_end - render_start) * (speed - 1.0)) + 2
            render_end = source_end + speed_extra
            if duration:
                render_end = min(float(duration), float(render_end))
            if render_end <= render_start:
                logger.warning(
                    f"Shorts: invalid render range {i}/{total}: "
                    f"start={render_start} end={render_end} duration={duration}"
                )
                continue

            ok = await render_short_clip(
                video_path, raw_path,
                render_start, render_end,
                visual_mode=visual_mode,
            )
            if not ok:
                logger.warning(
                    f"Shorts: не удалось вырезать {i}/{total} ({c['start']}–{c['end']})"
                )
                continue

            raw_probe = await probe_media_async(raw_path)
            raw_duration = (
                raw_probe.duration
                if raw_probe is not None and raw_probe.duration > 0
                else max(0.001, render_end - render_start)
            )

            need_post = do_normalize or (abs(speed - 1.0) > 0.01)
            current_path = raw_path
            speed_applied = False
            if need_post:
                post_ok = await postprocess_short(
                    raw_path, post_path,
                    normalize_audio=do_normalize,
                    speed=speed,
                )
                if post_ok:
                    current_path = post_path
                    speed_applied = abs(speed - 1.0) > 0.01
                    estimated = raw_duration / speed if speed_applied else raw_duration
                    logger.info(
                        "Shorts %d/%d: обработка OK — raw=%.3fs speed=%s "
                        "expected_delivery=%.3fs",
                        i,
                        total,
                        raw_duration,
                        speed,
                        estimated,
                    )
                else:
                    logger.warning(
                        "Shorts: обработка %d/%d не удалась, использую raw без "
                        "ложного пересчёта speed=%s",
                        i,
                        total,
                        speed,
                    )

            pre_subtitle_path = current_path
            subtitles_applied = False
            subtitle_fallback_notice = ""
            nosub_path = None
            if do_subtitles and HAS_FASTER_WHISPER:
                nosub_save_path = DOWNLOAD_DIR / f"{media_id}_short_{i}_nosub.mp4"
                try:
                    shutil.copy2(current_path, nosub_save_path)
                    nosub_path = nosub_save_path
                except Exception as _cp_err:
                    logger.warning(f"Shorts {i}/{total}: не удалось сохранить nosub копию: {_cp_err}")
                    nosub_path = None
            if do_subtitles:
                if not HAS_FASTER_WHISPER:
                    logger.warning(
                        "Subtitles: faster-whisper не установлен. "
                        "Установите: pip install faster-whisper"
                    )
                else:
                    try:
                        logger.info(
                            f"Shorts {i}/{total}: subtitle pipeline start, "
                            f"файл={current_path.name} "
                            f"exists={current_path.exists()} "
                            f"backend=faster-whisper({HAS_FASTER_WHISPER})"
                        )
                        segments = await transcribe_short_clip(current_path, ai_data=ai_data)
                        if segments:
                            logger.info(
                                f"Shorts {i}/{total}: транскрипция OK "
                                f"({len(segments)} сегм.), запускаю burn-in..."
                            )
                            sub_ok = await burn_subtitles_into_short(
                                current_path, sub_path, segments
                            )
                            if sub_ok:
                                current_path = sub_path
                                subtitles_applied = True
                                logger.info(
                                    f"Shorts {i}/{total}: субтитры вшиты → {sub_path.name}"
                                )
                            else:
                                logger.warning(
                                    f"Shorts {i}/{total}: burn-in не удался, "
                                    "продолжаем без субтитров"
                                )
                                if nosub_path:
                                    try:
                                        nosub_path.unlink(missing_ok=True)
                                    except Exception:
                                        pass
                                    nosub_path = None
                        else:
                            logger.warning(
                                f"Shorts {i}/{total}: транскрипция вернула пустой список — "
                                "субтитры пропущены (см. логи выше)"
                            )
                            if nosub_path:
                                try:
                                    nosub_path.unlink(missing_ok=True)
                                except Exception:
                                    pass
                                nosub_path = None
                    except Exception as sub_err:
                        logger.warning(
                            f"Shorts {i}/{total}: subtitle pipeline exception "
                            f"({type(sub_err).__name__}): {sub_err}"
                        )
                        if nosub_path:
                            try:
                                nosub_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                            nosub_path = None

            max_upload_mb = get_max_file_size_mb()
            selection = select_delivery_file(
                current_path,
                pre_subtitle_path if current_path != pre_subtitle_path else None,
                max_size_mb=max_upload_mb,
            )
            if selection.path is None:
                logger.warning(
                    "Shorts %d/%d: нет пригодного финального файла: reason=%s "
                    "primary=%.1fMB fallback=%.1fMB limit=%sMB",
                    i,
                    total,
                    selection.reason,
                    selection.primary_size_mb,
                    selection.fallback_size_mb,
                    max_upload_mb,
                )
                if nosub_path:
                    try:
                        nosub_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                continue

            current_path = selection.path
            delivery_selection = selection.selected
            delivery_reason = selection.reason
            if selection.selected == "fallback":
                logger.warning(
                    "Shorts %d/%d: subtitle-версия отклонена (%s, %.1fMB); "
                    "доставляю проверенную pre-subtitle версию %.1fMB",
                    i,
                    total,
                    selection.reason,
                    selection.primary_size_mb,
                    selection.fallback_size_mb,
                )
                subtitles_applied = False
                subtitle_fallback_notice = (
                    "⚠️ Субтитры сняты: версия с ними превысила допустимый размер "
                    "или не сохранилась корректно. Видео отправлено без потери основного материала."
                )
                if nosub_path:
                    try:
                        nosub_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    nosub_path = None

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
                            "Shorts %d/%d: subtitle-версия не прошла media probe; "
                            "использую проверенную pre-subtitle версию. probe=%r",
                            i,
                            total,
                            final_probe,
                        )
                        current_path = fallback_selection.path
                        final_probe = fallback_probe
                        delivery_selection = "fallback"
                        delivery_reason = "fallback_after_primary_media_probe_rejection"
                        subtitles_applied = False
                        subtitle_fallback_notice = (
                            "⚠️ Субтитры сняты: версия с ними превысила допустимый размер "
                            "или не сохранилась корректно. Видео отправлено без потери основного материала."
                        )
                        if nosub_path:
                            try:
                                nosub_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                            nosub_path = None
                if not media_probe_is_deliverable(final_probe):
                    logger.warning(
                        "Shorts %d/%d: ни primary, ни fallback media probe не "
                        "подтвердили пригодный video+audio файл — отправка отменена",
                        i,
                        total,
                    )
                    if nosub_path:
                        try:
                            nosub_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                    continue

            assert final_probe is not None
            timing = resolve_delivery_timing(
                source_start=render_start,
                raw_duration=raw_duration,
                source_duration=float(duration or 0),
                speed=speed,
                speed_applied=speed_applied,
                final_duration=final_probe.duration,
            )
            delivery_duration = timing.delivery_duration
            delivery_candidate = {
                **c,
                "_render_start_seconds": timing.source_start,
                "_render_end_seconds": timing.source_end,
                "_raw_duration_seconds": timing.raw_duration,
                "_delivery_duration_seconds": timing.delivery_duration,
                "_speed_applied": timing.speed_applied,
                "_delivery_file_selection": delivery_selection,
                "_delivery_file_reason": delivery_reason,
            }
            final_size = file_size_mb(current_path)
            logger.info(
                "Shorts %d/%d delivery evidence: source=%.3f-%.3f raw=%.3fs "
                "final=%.3fs speed_applied=%s size=%.1fMB selected=%s reason=%s",
                i,
                total,
                timing.source_start,
                timing.source_end,
                timing.raw_duration,
                timing.delivery_duration,
                timing.speed_applied,
                final_size,
                delivery_selection,
                delivery_reason,
            )

            thumb_buf = None
            if do_title_poster:
                try:
                    poster_ok = await create_short_title_poster(
                        current_path, title_poster_path,
                        c["title"], delivery_duration,
                    )
                    if poster_ok and title_poster_path.exists():
                        thumb_buf = InputFile(title_poster_path.read_bytes(), filename=title_poster_path.name)
                        logger.info(f"Shorts {i}/{total}: title poster создан")
                except Exception as poster_err:
                    logger.warning(f"Shorts {i}/{total}: title poster error: {poster_err}")

            if thumb_buf is None and do_snapshot:
                try:
                    snap_ok = await create_short_snapshot(
                        current_path, snapshot_path, delivery_duration
                    )
                    if snap_ok and snapshot_path.exists():
                        thumb_buf = InputFile(snapshot_path.read_bytes(), filename=snapshot_path.name)
                except Exception as snap_err:
                    logger.warning(f"Shorts {i}/{total}: snapshot error: {snap_err}")

            caption = build_short_caption(
                candidate=c,
                performer=performer,
                real_author=real_author,
                real_event=real_event,
                format_name=format_name,
                yt_url=url,
                vk_url=vk_url,
                rutube_url=rutube_url,
            )
            caption = _finalize_short_caption_for_delivery(
                caption,
                media_id=media_id,
                index=i,
                total=total,
                start=c.get("start"),
                end=c.get("end"),
            )

            try:
                logger.info(f"Shorts: отправляю {i}/{total}")
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
                    source_duration=int(duration or 0),
                )
                _nosub_buttons = []
                if subtitles_applied:
                    _nosub_buttons = [InlineKeyboardButton("🚫Sub", callback_data=f"strim:nosub:{short_id}")]
                trim_keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏪ Начало -10", callback_data=f"strim:s10:{short_id}"),
                    InlineKeyboardButton("⏭ Конец +10",  callback_data=f"strim:e10:{short_id}"),
                    InlineKeyboardButton("⏭⏭ Конец +20", callback_data=f"strim:e20:{short_id}"),
                    *_nosub_buttons,
                ]])
                await update.message.reply_video(
                    video=current_path,
                    caption=caption,
                    duration=max(1, int(round(delivery_duration))),
                    width=720,
                    height=1280,
                    thumbnail=thumb_buf,
                    parse_mode="HTML",
                    reply_markup=trim_keyboard,
                    write_timeout=120,
                    read_timeout=120,
                    connect_timeout=30,
                )
                sent += 1
                logger.info(
                    "Shorts: отправлен %d/%d (%s–%s) %r, final=%.3fs",
                    i,
                    total,
                    c["start"],
                    c["end"],
                    c["title"],
                    delivery_duration,
                )
                if subtitle_fallback_notice:
                    try:
                        await update.message.reply_text(subtitle_fallback_notice)
                    except Exception:
                        pass
                elif do_subtitles and not subtitles_applied and HAS_FASTER_WHISPER:
                    try:
                        await update.message.reply_text(
                            "⚠️ Субтитры для этого Short не удалось создать "
                            "(тихая речь, смешанный язык или ошибка транскрипции)."
                        )
                    except Exception:
                        pass
            except Exception as send_err:
                logger.warning(f"Shorts: ошибка отправки {i}/{total}: {send_err}")
                if nosub_path:
                    try:
                        nosub_path.unlink(missing_ok=True)
                    except Exception:
                        pass

        if sent == 0:
            logger.warning("Shorts: ни один short не был отправлен")
        else:
            logger.info(f"Shorts: итого отправлено {sent}/{total}")

    except Exception as e:
        logger.warning(f"Shorts process_and_send error: {e}")
        try:
            await update.message.reply_text(
                f"✂️ Ошибка при подготовке Shorts: {str(e)[:150]}"
            )
        except Exception:
            pass
    finally:
        for p in short_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        for p in poster_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        if video_path:
            try:
                _borrowed = livedub_source is not None and video_path == livedub_source
                if not _keep_for_montage and not _borrowed:
                    video_path.unlink(missing_ok=True)
            except Exception:
                pass


# ─── Montage Short & Highlights Reel ─────────────────────────
