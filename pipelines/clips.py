#!/usr/bin/env python3
"""
Clips Pipeline — process_and_send_clips.
Извлечено из bot.py строки 11355–11548.
"""
from core.globals import DOWNLOAD_DIR
from core.database import (
    adb_get, adb_save, asettings_get,
    settings_get,           # FIX pipeline_clips
    get_max_file_size_mb,   # FIX pipeline_clips
)
from core.utils import cleanup_files
from services.render_clips_montage import render_clip, create_clip_snapshot, build_clip_caption
from services.shorts_candidates import create_clips_candidates   # FIX pipeline_clips
from services.shorts_video import download_video_for_shorts      # FIX pipeline_clips
from services.media_delivery_probe import media_probe_is_deliverable, probe_media_async
from services.telegraph import create_telegraph_synopsis
from converters.caption import build_caption
from core.progress import set_progress
from converters.md_telegraph import visible_length, safe_trim_caption
from telegram import InputFile  # AUDIT R25: thumbnail без BufferedReader.name (py3.13)

import asyncio
import logging
import os
from io import BytesIO    # FIX pipeline_clips
from pathlib import Path

logger = logging.getLogger(__name__)


def _clips_candidate_budget_seconds() -> float:
    """Wall-clock budget for the optional Gemini candidate search.

    Clips run after the primary delivery and must never keep the whole job alive
    through a long chain of overload retries.  The shared Gemini retry policy is
    intentionally left untouched; this boundary applies only to the optional
    Clips feature.
    """
    raw = (os.getenv("CLIPS_CANDIDATE_BUDGET_SECONDS", "90") or "90").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 90.0
    return max(15.0, min(value, 300.0))


async def process_and_send_clips(
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
    livedub_video_path=None,  # ENG: path to translated video
) -> None:
    """
    Полный clips-пайплайн:
      1. Найти clip-кандидатов (Gemini)
      2. Скачать видео (переиспользуем download_video_for_shorts)
      3. Вырезать clip (render_clip) — оригинальное соотношение сторон
      4. [опц] Snapshot-постер (create_clip_snapshot)
      5. Отправить в Telegram как video
      6. Логировать результат

    Ошибки не пробрасываются — clips не должны валить основной результат.
    MP3-пайплайн и Shorts-пайплайн не затронуты.
    """
    video_path = None
    borrowed_video = False
    clip_paths: list[Path] = []
    snap_paths: list[Path] = []
    try:
        # ── Шаг 1: найти кандидатов ──────────────────────────
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
                "Clips: optional candidate search exceeded %.0fs budget — "
                "skipping Clips without delaying the completed primary delivery",
                candidate_budget,
            )
            return

        if not candidates:
            logger.info("Clips: кандидаты не найдены")
            return

        logger.info(f"Clips: найдено {len(candidates)} кандидатов, скачиваю видео...")

        # ── Шаг 2: скачать видео (тот же хелпер что у Shorts) ──
        # ENG mode: prefer translated (LiveDub) video. This path is borrowed
        # from the LiveDub owner and must never be deleted by the Clips callee.
        from pathlib import Path as _Path
        if livedub_video_path and _Path(livedub_video_path).exists():
            video_path = _Path(livedub_video_path)
            borrowed_video = True
            logger.info(f"Clips: using LiveDub video: {video_path.name}")
        else:
            video_path = await download_video_for_shorts(url, media_id)
        if not video_path:
            logger.warning("Clips: не удалось скачать видео")
            await update.message.reply_text("🎬 Не удалось скачать видео для Clips.")
            return

        format_name = (ai_data or {}).get("format", "other") or "other"
        real_author = (ai_data or {}).get("real_author", "") or performer or ""
        real_event  = (ai_data or {}).get("real_event", "") or ""
        do_snapshot = await asettings_get("clips_snapshot")

        logger.info(
            f"Clips: format={format_name} snapshot={do_snapshot} "
            f"кандидатов={len(candidates)}"
        )

        total = len(candidates)
        sent  = 0

        for i, c in enumerate(candidates, 1):
            clip_path = DOWNLOAD_DIR / f"{media_id}_clip_{i}.mp4"
            snap_path = DOWNLOAD_DIR / f"{media_id}_clip_{i}_snap.jpg"
            clip_paths.append(clip_path)
            snap_paths.append(snap_path)

            logger.info(
                f"Clips: рендер {i}/{total} "
                f"({c['start']}–{c['end']}) '{c['title']}'"
            )

            # ── Шаг 3: вырезать ──────────────────────────────
            ok = await render_clip(
                video_path, clip_path,
                c["start_seconds"], c["end_seconds"],
            )
            if not ok:
                logger.warning(
                    f"Clips: не удалось вырезать {i}/{total} ({c['start']}–{c['end']})"
                )
                continue

            # A non-empty file is not delivery evidence. render_clip historically
            # tolerated a Windows ffmpeg signal-2 exit if bytes existed, so prove
            # the public artifact has decodable video+audio before size/snapshot/send.
            clip_probe = await probe_media_async(clip_path)
            if not media_probe_is_deliverable(clip_probe):
                logger.warning(
                    "Clips %d/%d: rendered file failed final media probe — skipping",
                    i,
                    total,
                )
                continue
            assert clip_probe is not None
            delivery_duration = float(clip_probe.duration)

            # Проверка размера: Telegram лимит 2GB, но для video-сообщений ~50MB удобнее
            clip_size_mb = clip_path.stat().st_size / (1024 * 1024)
            if clip_size_mb > get_max_file_size_mb():
                logger.warning(
                    f"Clips {i}/{total}: файл слишком большой ({clip_size_mb:.0f}MB), пропускаем"
                )
                continue

            # ── Шаг 4: snapshot (опционально) ────────────────
            thumb_buf = None
            if do_snapshot:
                try:
                    snap_ok = await create_clip_snapshot(
                        clip_path, snap_path, delivery_duration
                    )
                    if snap_ok and snap_path.exists():
                        thumb_buf = InputFile(snap_path.read_bytes(), filename=snap_path.name)
                except Exception as snap_err:
                    logger.warning(f"Clips {i}/{total}: snapshot error: {snap_err}")

            # ── Шаг 5: caption ────────────────────────────────
            caption = build_clip_caption(
                candidate=c,
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

            # ── Шаг 6: отправить ─────────────────────────────
            try:
                logger.info(
                    "Clips: отправляю %d/%d (%.1fMB, final=%.3fs)",
                    i,
                    total,
                    clip_size_mb,
                    delivery_duration,
                )
                # Path вместо handle: при local_mode PTB шлёт file:// —
                # сервер читает с диска, без HTTP-передачи (большие клипы
                # >100MB по HTTP ловили TimedOut; см. fix LIVEDUB)
                await update.message.reply_video(
                    video=clip_path,
                    caption=caption,
                    duration=max(1, int(round(delivery_duration))),
                    thumbnail=thumb_buf,
                    supports_streaming=True,
                    parse_mode="HTML",
                    write_timeout=300,   # длинные clips = больше времени на отправку
                    read_timeout=300,
                    connect_timeout=30,
                )
                sent += 1
                logger.info(
                    "Clips: отправлен %d/%d (%s–%s) %r, final=%.3fs",
                    i,
                    total,
                    c["start"],
                    c["end"],
                    c["title"],
                    delivery_duration,
                )
            except Exception as send_err:
                logger.warning(f"Clips: ошибка отправки {i}/{total}: {send_err}")
            finally:
                # AUDIT R25: thumb_buf теперь InputFile (данные в памяти) —
                # закрывать нечего.
                pass

        # ── Шаг 6: итог ──────────────────────────────────────
        if sent == 0:
            logger.warning("Clips: ни один clip не был отправлен")
        else:
            logger.info(f"Clips: итого отправлено {sent}/{total}")

    except Exception as e:
        logger.warning(f"Clips process_and_send error: {e}")
        try:
            await update.message.reply_text(
                f"🎬 Ошибка при подготовке Clips: {str(e)[:150]}"
            )
        except Exception:
            pass
    finally:
        for p in clip_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        for p in snap_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        if video_path:
            try:
                # Owned generic downloads may be kept for Montage/Highlights.
                # A LiveDub path is borrowed from the outer pipeline and must be
                # cleaned only by that owner, even when Clips is the last consumer.
                _clips_keep = settings_get("shorts_montage") or settings_get("shorts_highlights")
                if not _clips_keep and not borrowed_video:
                    video_path.unlink(missing_ok=True)
            except Exception:
                pass


