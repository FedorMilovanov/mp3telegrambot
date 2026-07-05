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
    ashorts_speed_get,                            # AUDIT M4
)
from core.utils import cleanup_files
from services.shorts_video import (
    render_short_clip, postprocess_short, create_short_snapshot,
    build_short_caption, HAS_FASTER_WHISPER,
    download_video_for_shorts,      # FIX shorts
    transcribe_short_clip,          # FIX shorts
    burn_subtitles_into_short,      # FIX shorts
    create_short_title_poster,      # FIX shorts
    get_shorts_visual_mode,         # FIX shorts
)
from converters.md_telegraph import visible_length, safe_trim_caption
from services.shorts_candidates import create_shorts_candidates

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
    short_paths: list[Path] = []
    poster_paths: list[Path] = []
    # AUDIT M4: ashorts_speed_get вместо синхронного — не блокирует event loop
    speed = float(await ashorts_speed_get())
    # Читаем заранее — используется в finally (await там нельзя).
    # Если исключение случится до строки внутри try где это читалось — UnboundLocalError.
    _keep_for_montage = False  # будет перезаписан внутри try после загрузки видео
    try:
        # ── Шаг 1: найти кандидатов ──────────────────────────
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
        if not candidates:
            logger.info("Shorts: кандидаты не найдены")
            return

        logger.info(f"Shorts: найдено {len(candidates)} кандидатов, скачиваю видео...")

        # ── Шаг 2: скачать видео ─────────────────────────────
        # ENG mode: prefer the translated (LiveDub) video for shorts
        if livedub_video_path and livedub_video_path.exists():
            video_path = livedub_video_path
            logger.info(f"Shorts: using LiveDub video: {video_path.name}")
        else:
            video_path = await download_video_for_shorts(url, media_id, workdir=workdir)
        if not video_path:
            logger.warning("Shorts: не удалось скачать видео")
            await update.message.reply_text("✂️ Не удалось скачать видео для Shorts.")
            return

        # ── Настройки ─────────────────────────────────────────
        format_name      = (ai_data or {}).get("format", "other") or "other"
        real_author      = (ai_data or {}).get("real_author", "") or performer or ""
        real_event       = (ai_data or {}).get("real_event", "") or ""

        visual_mode      = get_shorts_visual_mode(format_name)
        do_normalize     = await asettings_get("shorts_audio_normalize")
        do_snapshot      = await asettings_get("shorts_snapshot")
        do_subtitles     = await asettings_get("shorts_subtitles")
        do_boundary_pad  = await asettings_get("shorts_boundary_padding")
        do_title_poster  = await asettings_get("shorts_title_poster")
        # speed читается в начале функции — не повторяем здесь
        # Читаем заранее — используется в finally (await там нельзя)
        # FIX AUDIT R4: clips тоже переиспользуют это видео — иначе после
        # шортов clips перекачивал оригинал (в ENG-режиме — молча рендерил
        # клипы из английского видео вместо перевода).
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

        # Для multi-speaker форматов субтитры работают, но могут ошибаться
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

            # ── Шаг 3: вырезать ──────────────────────────────
            # Если будет ускорение, расширяем окно конца клипа чтобы
            # после speed пользователь слышал завершённую мысль.
            # Пример: speed=1.1, end=808s → реально берём до 808+5=813s,
            # после ускорения это ужмётся обратно до ~807s.
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

            # ── Шаг 4: постобработка ─────────────────────────
            need_post = do_normalize or (abs(speed - 1.0) > 0.01)
            current_path = raw_path
            if need_post:
                post_ok = await postprocess_short(
                    raw_path, post_path,
                    normalize_audio=do_normalize,
                    speed=speed,
                )
                if post_ok:
                    current_path = post_path
                    # Логируем итоговую длительность после speed/normalize
                    _orig_dur = c["duration_seconds"]
                    _final_dur = round(_orig_dur / speed) if abs(speed - 1.0) > 0.01 else _orig_dur
                    logger.info(
                        f"Shorts {i}/{total}: postprocess OK — "
                        f"исходно {_orig_dur}s, после speed={speed} → ~{_final_dur}s"
                    )
                else:
                    logger.warning(
                        f"Shorts: постобработка {i}/{total} не удалась, использую raw"
                    )

            # ── Шаг 5: субтитры (опционально) ────────────────
            subtitles_applied = False
            nosub_path = None  # Путь к файлу ДО субтитров
            # Сохраняем копию до субтитров (для кнопки 🚫Sub)
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
                                # burn-in упал — nosub-копия бесполезна, удаляем сразу
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
                            # транскрипция пустая — nosub-копия тоже не нужна
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
                        # исключение — nosub-копия не нужна
                        if nosub_path:
                            try:
                                nosub_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                            nosub_path = None

            # ── Шаг 6: постер с заголовком (опционально) ─────
            thumb_buf = None
            if do_title_poster:
                try:
                    poster_ok = await create_short_title_poster(
                        current_path, title_poster_path,
                        c["title"], c["duration_seconds"],
                    )
                    if poster_ok and title_poster_path.exists():
                        thumb_buf = open(title_poster_path, "rb")
                        thumb_buf.name = title_poster_path.name
                        logger.info(f"Shorts {i}/{total}: title poster создан")
                except Exception as poster_err:
                    logger.warning(f"Shorts {i}/{total}: title poster error: {poster_err}")

            # ── Шаг 7: snapshot (fallback, если постер не создан) ─
            if thumb_buf is None and do_snapshot:
                try:
                    snap_ok = await create_short_snapshot(
                        current_path, snapshot_path, c["duration_seconds"]
                    )
                    if snap_ok and snapshot_path.exists():
                        thumb_buf = open(snapshot_path, "rb")
                        thumb_buf.name = snapshot_path.name
                except Exception as snap_err:
                    logger.warning(f"Shorts {i}/{total}: snapshot error: {snap_err}")

            # ── Шаг 8: caption ───────────────────────────────
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
            if visible_length(caption) > 1024:
                caption = safe_trim_caption(caption, 1024)

            # ── Шаг 9: отправить ─────────────────────────────
            try:
                logger.info(f"Shorts: отправляю {i}/{total}")
                # Сохраняем данные для trim-кнопок
                short_id = uuid.uuid4().hex[:16]
                # FIX AUDIT R4: в trim-записи хранится ИСХОДНОЕ видео, а не
                # отрендеренный 20-60с клип. start/end_seconds — абсолютные
                # координаты исходника; клип к тому же удаляется в finally,
                # поэтому все trim-кнопки были мертвы («Исходное видео не
                # найдено»). Если исходник удалят позже — callback перекачает
                # его заново по yt_url.
                short_trim_save(
                    short_id=short_id,
                    video_path=str(video_path),
                    start_seconds=c.get("start_seconds", 0),
                    end_seconds=c.get("end_seconds", 0),
                    visual_mode=visual_mode,
                    yt_url=url,
                    vk_url=vk_url,
                    rutube_url=rutube_url,
                    performer=performer,
                    real_author=real_author,
                    real_event=real_event,
                    format_name=format_name,
                    candidate_json=json.dumps(c, ensure_ascii=False),
                    video_path_nosub=str(nosub_path) if nosub_path else "",
                    nosub_expiry=int(time.time()) + 86400 if nosub_path else 0,  # #31: 24ч
                    source_duration=int(duration or 0),  # AUDIT M5: для ограничения end_s при ретриме
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
                # Path: file:// при local_mode (см. fix LIVEDUB)
                await update.message.reply_video(
                    video=current_path,
                    caption=caption,
                    duration=int(c["duration_seconds"]),
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
                    f"Shorts: отправлен {i}/{total} ({c['start']}–{c['end']}) '{c['title']}'"
                )
                # Уведомляем если субтитры были включены но не применились
                if do_subtitles and not subtitles_applied and HAS_FASTER_WHISPER:
                    try:
                        await update.message.reply_text(
                            "⚠️ Субтитры для этого Short не удалось создать "
                            "(тихая речь, смешанный язык или ошибка транскрипции)."
                        )
                    except Exception:
                        pass
            except Exception as send_err:
                logger.warning(f"Shorts: ошибка отправки {i}/{total}: {send_err}")
            finally:
                if thumb_buf:
                    try:
                        thumb_buf.close()
                    except Exception:
                        pass

        # ── Шаг 9: итог ──────────────────────────────────────
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
                # FIX AUDIT R4: LiveDub-видео принадлежит основному пайплайну
                # (ld_work), shorts его лишь одалживает — не удаляем чужое.
                _borrowed = livedub_video_path is not None and video_path == livedub_video_path
                if not _keep_for_montage and not _borrowed:
                    video_path.unlink(missing_ok=True)
            except Exception:
                pass


# ─── Montage Short & Highlights Reel ─────────────────────────


