#!/usr/bin/env python3
"""
Clips Pipeline — process_and_send_clips.
Извлечено из bot.py строки 11355–11548.
"""
from core.globals import DOWNLOAD_DIR
from core.database import (
    adb_get, adb_save, asettings_get,
    settings_get,           # FIX pipeline_clips
    MAX_FILE_SIZE_MB,       # FIX pipeline_clips
)
from core.utils import cleanup_files
from services.render_clips_montage import render_clip, create_clip_snapshot, build_clip_caption
from services.shorts_candidates import create_clips_candidates   # FIX pipeline_clips
from services.shorts_video import download_video_for_shorts      # FIX pipeline_clips
from services.telegraph import create_telegraph_synopsis
from converters.caption import build_caption
from core.progress import set_progress
from converters.md_telegraph import visible_length, safe_trim_caption

import asyncio
import logging
from io import BytesIO    # FIX pipeline_clips
from pathlib import Path

logger = logging.getLogger(__name__)

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
    clip_paths: list[Path] = []
    snap_paths: list[Path] = []
    try:
        # ── Шаг 1: найти кандидатов ──────────────────────────
        candidates = await create_clips_candidates(
            mp3_path=mp3_path,
            ai_data=ai_data,
            title=title,
            performer=performer,
            duration=duration,
            existing_audio_part=existing_audio_part,
            existing_client=existing_client,
        )
        if not candidates:
            logger.info("Clips: кандидаты не найдены")
            return

        logger.info(f"Clips: найдено {len(candidates)} кандидатов, скачиваю видео...")

        # ── Шаг 2: скачать видео (тот же хелпер что у Shorts) ──
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

            # Проверка размера: Telegram лимит 2GB, но для video-сообщений ~50MB удобнее
            clip_size_mb = clip_path.stat().st_size / (1024 * 1024)
            if clip_size_mb > MAX_FILE_SIZE_MB:
                logger.warning(
                    f"Clips {i}/{total}: файл слишком большой ({clip_size_mb:.0f}MB), пропускаем"
                )
                continue

            # ── Шаг 4: snapshot (опционально) ────────────────
            thumb_buf = None
            if do_snapshot:
                try:
                    snap_ok = await create_clip_snapshot(
                        clip_path, snap_path, c["duration_seconds"]
                    )
                    if snap_ok and snap_path.exists():
                        thumb_buf = BytesIO(snap_path.read_bytes())
                        thumb_buf.name = snap_path.name
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
                    f"Clips: отправляю {i}/{total} "
                    f"({clip_size_mb:.1f}MB, {c['duration_seconds']:.0f}s)"
                )
                with open(clip_path, "rb") as vf:
                    # Получаем оригинальные размеры через ffprobe если возможно,
                    # иначе не передаём — Telegram сам определит
                    await update.message.reply_video(
                        video=vf,
                        caption=caption,
                        duration=int(c["duration_seconds"]),
                        thumbnail=thumb_buf,
                        supports_streaming=True,
                        parse_mode="HTML",
                        write_timeout=300,   # длинные clips = больше времени на отправку
                        read_timeout=300,
                        connect_timeout=30,
                    )
                sent += 1
                logger.info(
                    f"Clips: отправлен {i}/{total} "
                    f"({c['start']}–{c['end']}) '{c['title']}'"
                )
            except Exception as send_err:
                logger.warning(f"Clips: ошибка отправки {i}/{total}: {send_err}")
            finally:
                if thumb_buf:
                    try:
                        thumb_buf.close()
                    except Exception:
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
                # Не удаляем видео если следом идут Montage или Highlights —
                # они используют тот же файл (тот же media_id) и скачают заново если его нет.
                # Проверяем настройки синхронно (допустимо в finally).
                _clips_keep = settings_get("shorts_montage") or settings_get("shorts_highlights")
                if not _clips_keep:
                    video_path.unlink(missing_ok=True)
            except Exception:
                pass


