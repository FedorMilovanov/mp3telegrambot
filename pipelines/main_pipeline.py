#!/usr/bin/env python3
"""
Main Pipeline — process_single_video.
Извлечено из bot.py строки 12470–13446.
"""
from services.ffmpeg import YTDLP_BASE_ARGS                   # FIX #23: нужен результат, не функция
from core.globals import (
    DOWNLOAD_DIR, THUMBS_DIR, HAS_GEMINI, HAS_PILLOW, DB_PATH,
    GEMINI_CLIENTS, TELEGRAPH_TOKEN,                  # FIX #11
    html_mod,                                          # FIX #11
)
from core.database import (
    adb_get, adb_save, asettings_get, asettings_get_all,
    is_cache_valid, db_init,
    GEMINI_MODEL, MAX_FILE_SIZE_MB, CACHE_VERSION,    # FIX #11
    get_prompt_fingerprint,                            # FIX #11
)
from core.utils import (
    cleanup_files, is_media_url, parse_title, prepare_thumbnail,
    check_rate_limit, update_rate_limit, format_timestamp,  # FIX #11
)
from core.text_utils import (
    _scrub_inline, normalize_author_name, normalize_title_text,
    normalize_common_typos, _has_dirty_meta,                                   # FIX #11
)
from converters.md_telegraph import (
    visible_length, safe_trim_caption,                 # FIX #11
    _trim_timestamps, get_caption_timestamp_limit,     # FIX #11
    _edit_telegraph_page,                              # FIX #11
)
from services.search import (
    find_alternative_links, _build_search_title,
    build_platform_links, build_telegraph_links,       # FIX #11
)
from services.gemini_analyze import gemini_analyze_audio
from converters.caption import build_caption
from services.telegraph import create_telegraph_synopsis
from services.telegraph_pages import (
    create_telegraph_analytics, create_telegraph_questions,
    create_telegraph_terms, create_telegraph_study_analysis,
    create_telegraph_reflection_application, create_telegraph_study_reflection_combined,
    combined_study_reflection_enabled,
    _gemini_last_was_fallback,
)
from services.render_clips_montage import create_extras_candidates  # FIX #11
from pipelines.shorts import process_and_send_shorts
from pipelines.clips import process_and_send_clips
from pipelines.montage import process_and_send_montage, process_and_send_highlights
from core.progress import set_progress
from core.title_topic_audit import choose_safe_public_title
from core.publication_status import build_publication_status, missing_to_json
from core.generated_pages import (
    asave_generated_page_record, asave_segment_plan_export,
    build_generated_page_record, collect_quality_warnings, extract_scripture_refs,
    timestamp_coverage_archive_fields,
)
from core.timestamp_quality import timestamp_coverage_ratio

import asyncio
import json
import os       # FIX #11
import logging
import re         # FIX #11
import requests   # FIX #11
import shutil     # FIX #11
import subprocess # FIX #11
import uuid
import sqlite3  # LIVEDUB: direct DB read for user mode
from pathlib import Path

logger = logging.getLogger(__name__)

async def process_single_video(url, update, status_msg=None, progress_prefix="", context=None, silent_errors: bool = False):
    thumb_buffer = None
    _pp = progress_prefix  # short alias for progress calls
    used_audio_part = None  # инициализируем здесь — finally обращается к ним безусловно
    used_client = None       # если исключение до их присвоения внутри try — UnboundLocalError
    try:
        if status_msg:
            await set_progress(status_msg, 1, prefix=_pp)
        else:
            status_msg = await update.message.reply_text("⏳ Обрабатываю...")
            await set_progress(status_msg, 1, prefix=_pp)

        info_cmd = YTDLP_BASE_ARGS + [
            "--dump-json", "--no-playlist", url,
        ]
        info_proc = await asyncio.get_running_loop().run_in_executor(
            None, lambda: subprocess.run(info_cmd, capture_output=True, text=True, timeout=60)
        )
        if info_proc.returncode != 0:
            raise Exception(info_proc.stderr[-500:] if info_proc.stderr else "yt-dlp info error")
        info_dict = None
        for line in info_proc.stdout.strip().splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    info_dict = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if not info_dict:
            raise Exception("Не удалось получить метаданные видео")

        # AUDIT M10: отбивка live-stream — yt-dlp начнёт качать бесконечный поток
        if info_dict.get("is_live") or info_dict.get("live_status") in (
            "is_live", "is_upcoming", "post_live"
        ):
            msg = "⚠️ Live-трансляции не поддерживаются. Дождитесь окончания и загрузите запись."
            if not silent_errors:
                await update.message.reply_text(msg)
            logger.info(f"Пропуск live-stream: {url}")
            return False

        # AUDIT M10: ограничение по длительности — Gemini обрежет конспект для слишком длинных,
        # пользователь получит обрезанный результат при потраченной квоте.
        _max_dur = int(os.getenv("MAX_VIDEO_DURATION_SEC", "10800"))  # дефолт 3 часа
        _video_duration_meta = int(info_dict.get("duration") or 0)
        if _video_duration_meta and _video_duration_meta > _max_dur:
            msg = (
                f"⚠️ Видео {_video_duration_meta // 60} мин — превышает лимит "
                f"{_max_dur // 60} мин. Скиньте видео покороче."
            )
            if not silent_errors:
                await update.message.reply_text(msg)
            logger.info(
                f"Пропуск длинного видео: {url} ({_video_duration_meta}s > {_max_dur}s)"
            )
            return False

        # AUDIT: проверка свободного места на диске перед скачиванием
        try:
            _free_gb = shutil.disk_usage(DOWNLOAD_DIR).free / (1024 ** 3)
            if _free_gb < 2.0:
                msg = f"⚠️ Мало места на диске ({_free_gb:.1f} ГБ свободно). Освободите место и повторите."
                if not silent_errors:
                    await update.message.reply_text(msg)
                logger.warning("Пропуск: мало места на диске (%.1f ГБ)", _free_gb)
                return False
        except OSError:
            pass  # disk_usage может не работать на некоторых путях

        full_title   = info_dict.get("title", "audio")
        channel_name = info_dict.get("uploader", info_dict.get("channel", ""))
        logger.info(f"YouTube channel_name: '{channel_name}'")
        duration     = int(info_dict.get("duration") or 0)
        media_id     = info_dict.get("id", "media")

        # --- LIVEDUB: проверяем режим пользователя ---
        user_id = update.effective_user.id if (update.effective_user and update.effective_user.id) else None
        user_mode = "rus"
        if user_id:
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("PRAGMA busy_timeout=5000")
                    row = conn.execute(
                        "SELECT value FROM bot_settings WHERE key = ?",
                        (f"user_mode_{user_id}",)
                    ).fetchone()
                if row and row[0] == "eng":
                    user_mode = "eng"
            except Exception:
                pass

        live_dub_task = None
        if user_mode == "eng":
            from services.yandex_live_dub import get_live_dub_video
            from services.eng_subtitles import create_gemini_subtitles, merge_subtitles, download_original_video
            import tempfile
            ld_work = Path(tempfile.gettempdir()) / f"livedub_{media_id}"
            ld_work.mkdir(exist_ok=True)
            
            eng_subs_enabled = await asettings_get("eng_subtitles")

            async def _run_livedub_bg(video_url, workdir):
                try:
                    # Запускаем создание субтитров параллельно с скачиванием LiveDub
                    subs_task = None
                    if eng_subs_enabled:
                        subs_task = asyncio.create_task(create_gemini_subtitles(video_url, workdir))
                    
                    dub_task = asyncio.create_task(
                        get_live_dub_video(
                            video_url, workdir,
                            original_volume=0.3,
                            translation_volume=1.5,
                            keep_original_audio=True,
                        )
                    )
                    
                    srt_path = None
                    if subs_task:
                        try:
                            srt_path = await subs_task
                        except Exception as e:
                            logger.warning(f"[EngSubtitles] Ошибка создания сабов: {e}")

                    dub_path = None
                    try:
                        dub_path = await dub_task
                    except RuntimeError as e:
                        if "LIVEDUB_NOT_AVAILABLE" in str(e):
                            logger.info("[LiveDub] Перевод недоступен для этого видео")
                        else:
                            logger.warning(f"[LiveDub] Ошибка: {e}")
                    except Exception as e:
                        logger.warning(f"[LiveDub] Неизвестная ошибка: {e}")

                    if dub_path and dub_path.exists():
                        # Яндекс отработал, вшиваем сабы
                        if srt_path and srt_path.exists():
                            final_video = await merge_subtitles(dub_path, srt_path, is_fallback=False)
                            return final_video, False, True
                        return dub_path, False, False
                    else:
                        # Яндекс упал, скачиваем оригинал и вшиваем сабы
                        if srt_path and srt_path.exists():
                            logger.info("[LiveDub] Перевод не удался, отправляем резерв с субтитрами")
                            orig_video = await download_original_video(video_url, workdir)
                            final_video = await merge_subtitles(orig_video, srt_path, is_fallback=True)
                            return final_video, True, True
                        return None, False, False

                except Exception as e:
                    logger.warning(f"[LiveDub Background] Ошибка: {e}")
                    return None, False, False

            live_dub_task = asyncio.create_task(_run_livedub_bg(url, ld_work))
        # --- END LIVEDUB ---

        performer, title = parse_title(full_title, channel_name)

        # Проверяем кэш — если видео уже обработано и кэш актуален, отдаём результат сразу
        cached = await adb_get(media_id)
        cache_ok, cache_reason = is_cache_valid(cached)

        # Страховка от "грязного кэша" — если в ai_data есть мусор, пересчитываем
        if cache_ok and cached:
            dirty = False
            c_ai = cached.get("ai_data") or {}
            for field in ("main_topic", "real_author", "real_title", "real_event"):
                if _has_dirty_meta(c_ai.get(field, "")):
                    dirty = True
                    break
            if not dirty:
                for q in (cached.get("questions") or []):
                    if _has_dirty_meta(str(q)):
                        dirty = True
                        break
            if dirty:
                cache_ok = False
                cache_reason = "dirty_meta"
                logger.info(f"Кэш skip: {media_id} reason=dirty_meta")

        if cache_ok and cached.get("ai_data"):
            logger.info(f"Кэш hit: {media_id} — отдаём без Gemini")
            await set_progress(status_msg, 6, prefix=_pp)
            c_tg   = cached["telegraph_url"]
            c_qtg  = cached.get("quotes_tg_url", "")
            c_qqtg = cached.get("questions_tg_url", "")
            _c_study      = cached.get("study_tg_url") or ""
            _c_reflection = cached.get("reflection_tg_url") or ""
            # Термины скрываем если обе новые страницы есть
            c_ttg  = "" if (_c_study and _c_reflection) else (cached.get("terms_tg_url") or "")
            # Обложка
            thumb_buffer = None
            try:
                _existing_thumb = list(THUMBS_DIR.glob(f"{media_id}_thumb*"))
                if _existing_thumb:
                    thumb_buffer = prepare_thumbnail(_existing_thumb[0])
                    logger.info(f"Обложка кэш (hit): используем {_existing_thumb[0].name}")
                else:
                    thumb_cmd = YTDLP_BASE_ARGS + [
                        "--skip-download", "--write-thumbnail", "--no-playlist",
                        "--convert-thumbnails", "jpg",
                        "--output", str(THUMBS_DIR / f"{media_id}_thumb.%(ext)s"), url,
                    ]
                    _thumb_proc = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: subprocess.run(thumb_cmd, capture_output=True, text=True, timeout=30)
                    )
                    if _thumb_proc.returncode != 0:
                        _thumb_err = (_thumb_proc.stderr or "").strip()[-300:]
                        logger.warning(f"Обложка (кэш) yt-dlp rc={_thumb_proc.returncode}: {_thumb_err}")
                    thumb_candidates = list(THUMBS_DIR.glob(f"{media_id}_thumb*"))
                    if thumb_candidates:
                        thumb_buffer = prepare_thumbnail(thumb_candidates[0])
                        logger.info(f"Обложка (кэш) скачана: {thumb_candidates[0].name}")
                    else:
                        logger.warning(f"Обложка (кэш): файл не найден после yt-dlp (rc={_thumb_proc.returncode})")
            except Exception as e:
                logger.warning(f"Обложка (кэш) не скачана: {e}")
            # Скачиваем аудио
            # Если длительность > 40 минут — сразу качаем в 64kbps
            audio_quality = "64K" if duration > 3600 else "128K"
            mp3_path = DOWNLOAD_DIR / f"{media_id}.mp3"
            if not mp3_path.exists():
                dl_cmd = YTDLP_BASE_ARGS + [
                    "--extract-audio", "--audio-format", "mp3", "--audio-quality", audio_quality,
                    "--no-playlist", "--output", str(DOWNLOAD_DIR / f"{media_id}.%(ext)s"), url,
                ]
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: subprocess.run(dl_cmd, capture_output=True, timeout=1800))
            if not mp3_path.exists():
                raise Exception("Не удалось скачать аудио")
            file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
            bitrate = "64" if duration > 3600 else "128"
            logger.info(f"Кэш аудио: {mp3_path.name} = {file_size_mb:.1f} MB")
            # Сжимаем если больше лимита
            if file_size_mb > MAX_FILE_SIZE_MB:
                mp3_64_path = DOWNLOAD_DIR / f"{media_id}_64.mp3"
                ffmpeg = shutil.which("ffmpeg")
                if ffmpeg:
                    await asyncio.get_running_loop().run_in_executor(
                        None, lambda: subprocess.run(
                            [ffmpeg, "-i", str(mp3_path), "-b:a", "64k", "-y", str(mp3_64_path)],
                            capture_output=True, timeout=300))
                    if mp3_64_path.exists():
                        mp3_path.unlink(missing_ok=True)
                        mp3_path = mp3_64_path
                        file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
                        bitrate = "64"
                if file_size_mb > MAX_FILE_SIZE_MB:
                    await update.message.reply_text(f"⚠️ Файл слишком большой ({file_size_mb:.1f} МБ) даже после сжатия.")
                    cleanup_files(media_id)
                    return False
            # Ищем альт-ссылки: берём из кэша если сохранены, иначе делаем запрос
            _cached_rutube = (cached or {}).get("rutube_url", "") or ""
            _cached_vk     = (cached or {}).get("vk_url", "") or ""
            if _cached_rutube or _cached_vk:
                rutube_url = _cached_rutube
                vk_url     = _cached_vk
                logger.info(f"Кэш alt-links: rutube={bool(rutube_url)} vk={bool(vk_url)}")
            else:
                _cache_search_title = _build_search_title(c_ai, full_title)
                alt_links = await find_alternative_links(_cache_search_title, channel_name, duration, ai_data=c_ai, fallback_title=full_title)
                rutube_url = (alt_links or {}).get("rutube") or ""
                vk_url     = (alt_links or {}).get("vk") or ""

            def _build_c(data, **kw):
                return build_caption(performer, title, duration, file_size_mb,
                                     data, bitrate, url, c_tg, rutube_url, vk_url,
                                     c_qtg, c_qqtg, c_ttg,
                                     study_tg_url=_c_study,
                                     reflection_tg_url=_c_reflection,
                                     **kw)

            def _build_full_c(data):
                return build_caption(performer, title, duration, file_size_mb,
                                     data, bitrate, url, c_tg, rutube_url, vk_url,
                                     c_qtg, c_qqtg, c_ttg,
                                     study_tg_url=_c_study,
                                     reflection_tg_url=_c_reflection,
                                     full_mode=True)

            _c_fmt    = (c_ai or {}).get("format", "other")
            _ts_limit = get_caption_timestamp_limit(_c_fmt)
            _c_ts_total = len([l for l in ((c_ai or {}).get("timestamps") or "").split("\n") if l.strip()])
            # Применяем format-лимит к таймкодам сразу (до проверки переполнения)
            _ai_for_caption = c_ai
            if c_ai and c_ai.get("timestamps"):
                ts_limited = _trim_timestamps(c_ai["timestamps"], _ts_limit)
                if ts_limited != c_ai["timestamps"]:
                    _ai_for_caption = {
                        **c_ai,
                        "timestamps": ts_limited,
                        "_caption_timestamps_trimmed": True,
                        "_caption_timestamps_total": _c_ts_total,
                        "_caption_timestamps_shown": min(_c_ts_total, _ts_limit),
                    }
            caption = _build_c(_ai_for_caption)
            # Умное обрезание до 1024 видимых символов (без HTML-тегов)
            _cap_ts_str = (_ai_for_caption or {}).get("timestamps", "") or ""  # отслеживаем фактические ts в caption
            # Шаг 1: убрать хэштеги
            if visible_length(caption) > 1024 and _ai_for_caption and _ai_for_caption.get("hashtags"):
                caption = _build_c({**_ai_for_caption, "hashtags": ""})
            # Шаг 1.5: убрать main_topic
            if visible_length(caption) > 1024 and _ai_for_caption and _ai_for_caption.get("main_topic"):
                caption = _build_c({**_ai_for_caption, "hashtags": "", "main_topic": ""})
            # Шаг 2: сократить таймкоды ещё (половина лимита)
            if visible_length(caption) > 1024 and _ai_for_caption and _ai_for_caption.get("timestamps"):
                ts_half = _trim_timestamps(_ai_for_caption["timestamps"], max(_ts_limit // 2, 3))
                caption = _build_c({**_ai_for_caption, "hashtags": "", "timestamps": ts_half})
                _cap_ts_str = ts_half
            # Шаг 3: сохранить мини-набор таймкодов вместо полного удаления
            if visible_length(caption) > 1024 and _ai_for_caption and _ai_for_caption.get("timestamps"):
                _fit_found = False
                for _mini_limit in (7, 5, 3):
                    ts_mini = _trim_timestamps(_ai_for_caption["timestamps"], min(_mini_limit, _ts_limit))
                    candidate = _build_c({
                        **_ai_for_caption,
                        "hashtags": "",
                        "main_topic": "",
                        "timestamps": ts_mini,
                        "_caption_timestamps_trimmed": True,
                    })
                    if visible_length(candidate) <= 1024:
                        caption = candidate
                        _cap_ts_str = ts_mini
                        _fit_found = True
                        break
                if not _fit_found:
                    caption = _build_c({**_ai_for_caption, "hashtags": "", "main_topic": "", "timestamps": "", "_caption_timestamps_trimmed": True})
                    _cap_ts_str = ""
            # Шаг 4: последний резерв — шапка + ссылки, обрезаем без лома тегов
            if visible_length(caption) > 1024:
                platform_block = build_platform_links(url, rutube_url, vk_url)
                tg_block = build_telegraph_links(
                    c_tg or "", c_qtg or "", c_qqtg or "",
                    c_ttg,
                    study_tg_url=_c_study,
                    reflection_tg_url=_c_reflection,
                )
                footer = "\n".join(filter(None, [platform_block, tg_block]))
                header = "\n".join(caption.split("\n")[:4])
                caption = header + ("\n\n" + footer if footer else "")
                caption = safe_trim_caption(caption, 1024)
                _cap_ts_str = ""
            _ts_in_cap = len([l for l in _cap_ts_str.split("\n") if l.strip()])
            logger.info(f"Кэш caption visible_len={visible_length(caption)} format={_c_fmt} ts_limit={_ts_limit} ts_in_cap={_ts_in_cap}")
            with open(mp3_path, "rb") as af:
                _title_c     = (c_ai or {}).get("real_title") or title
                _performer_c = (c_ai or {}).get("real_author") or performer
                for _attempt in range(3):
                    try:
                        af.seek(0)
                        await update.message.reply_audio(
                            audio=af, title=_title_c, performer=_performer_c,
                            thumbnail=thumb_buffer, duration=duration,
                            caption=caption, parse_mode="HTML",
                            write_timeout=180, read_timeout=180, connect_timeout=60,
                        )
                        break
                    except Exception as upload_err:
                        err_name = type(upload_err).__name__
                        err_str  = str(upload_err).lower()
                        _retryable_names = ("Timeout", "NetworkError", "TimedOut", "ReadError", "ConnectError")
                        _retryable_strs  = ("internal server error", "server error", "bad gateway", "gateway timeout")
                        _is_retryable = (
                            any(x in err_name for x in _retryable_names) or
                            any(x in err_str  for x in _retryable_strs)
                        )
                        if _attempt < 2 and _is_retryable:
                            logger.warning(f"Кэш upload попытка {_attempt+1}/3 ({err_name}: {str(upload_err)[:120]}), повтор...")
                            await asyncio.sleep(5 * (_attempt + 1))
                        else:
                            raise
            # Отправляем полное описание отдельным сообщением (кэш-hit)
            _feat_full_c = await asettings_get("caption_full_text")
            if _feat_full_c and c_ai:
                full_caption_c = _build_full_c(c_ai)
                if visible_length(full_caption_c) > 4096:
                    full_caption_c = safe_trim_caption(full_caption_c, 4096)
                if full_caption_c and full_caption_c != caption:
                    try:
                        await update.message.reply_text(full_caption_c, parse_mode="HTML")
                    except Exception as _fe:
                        logger.warning(f"Кэш full caption send error: {_fe}")

            # ── PDF (кэш-hit, опционально) ────────────────────────────
            _feat_pdf_c = await asettings_get("generate_pdf")
            if _feat_pdf_c and c_ai:
                _pdf_status = None
                _pdf_path_c = None
                try:
                    from services.pdf_generator import generate_sermon_pdf_async

                    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
                    _pdf_path_c = str(DOWNLOAD_DIR / f"{media_id}_{uuid.uuid4().hex[:6]}.pdf")

                    _raw_t_c = (c_ai.get("real_title") or "").strip() or (title or "").strip()
                    _raw_a_c = (c_ai.get("real_author") or "").strip() or (performer or "").strip()
                    _pdf_title_c = normalize_title_text(_raw_t_c) if _raw_t_c else "Без названия"
                    _pdf_author_c = normalize_author_name(_raw_a_c) if _raw_a_c else "Неизвестный"
                    _dur_str_c = format_timestamp(duration) if duration else ""

                    _pdf_urls_c = {}
                    if c_tg: _pdf_urls_c["synopsis"] = c_tg
                    if _c_study: _pdf_urls_c["study"] = _c_study
                    if _c_reflection: _pdf_urls_c["reflection"] = _c_reflection
                    if c_ttg: _pdf_urls_c["terms"] = c_ttg

                    if _pdf_urls_c:
                        logger.info(f"PDF (кэш): генерирую ({list(_pdf_urls_c.keys())})")
                        _pdf_status = await update.message.reply_text("📄 Генерирую PDF…")

                        async def _pdf_progress_c(stage: str, pct: int):
                            try:
                                await _pdf_status.edit_text(f"📄 PDF: {stage} ({pct}%)")
                            except Exception:
                                pass

                        _pdf_result_c = await generate_sermon_pdf_async(
                            output_path=_pdf_path_c,
                            title=_pdf_title_c,
                            performer=_pdf_author_c,
                            duration_str=_dur_str_c,
                            urls=_pdf_urls_c,
                            progress_callback=_pdf_progress_c,
                        )

                        if _pdf_result_c and Path(_pdf_result_c).exists():
                            _size_c = Path(_pdf_result_c).stat().st_size
                            if 200 < _size_c < 49 * 1024 * 1024:
                                def _safe_fn_c(s: str) -> str:
                                    return re.sub(r'[\\/:*?"<>|\n\r\t]', '', s).strip()[:80] or "doc"
                                _fn_c = f"{_safe_fn_c(_pdf_author_c)} — {_safe_fn_c(_pdf_title_c)}.pdf"
                                with open(_pdf_result_c, "rb") as _pdf_f:
                                    await update.message.reply_document(
                                        document=_pdf_f,
                                        filename=_fn_c,
                                        caption="📄 <b>PDF-версия материала</b>",
                                        parse_mode="HTML",
                                    )
                                logger.info("PDF (кэш): отправлен")
                            else:
                                logger.warning(f"PDF (кэш): размер вне допустимого диапазона: {_size_c} байт")
                                await update.message.reply_text("❌ Не удалось создать PDF.")
                        else:
                            logger.warning("PDF (кэш): generate_sermon_pdf_async вернул None или файл не создан")
                            await update.message.reply_text("❌ Не удалось создать PDF.")

                except ImportError:
                    pass  # pdf_generator не установлен — тихо пропускаем
                except Exception as _pdf_err_c:
                    logger.warning(f"PDF (кэш) не удался: {_pdf_err_c}")
                finally:
                    if _pdf_path_c:
                        try:
                            Path(_pdf_path_c).unlink(missing_ok=True)
                        except Exception:
                            pass
                    if _pdf_status:
                        try:
                            await _pdf_status.delete()
                        except Exception:
                            pass


        # --- LIVEDUB: отправка результата ---
        if live_dub_task and context:
            try:
                livedub_result = await asyncio.wait_for(live_dub_task, timeout=600)
                if livedub_result:
                    livedub_path, is_fallback, has_subs = livedub_result
                    if livedub_path and livedub_path.exists():
                        if is_fallback:
                            caption = "⚠️ Живой перевод Яндекса недоступен/сломался.\nОтправляю резерв: оригинальное видео" + (" + русские субтитры." if has_subs else ".")
                        else:
                            caption = "🎬 Живые голоса Яндекса" + ("\n💬 Русские субтитры сделаны независимо через Whisper + Gemini" if has_subs else "")
                        with open(livedub_path, "rb") as f:
                            await context.bot.send_video(
                                chat_id=update.effective_chat.id,
                                video=f,
                                caption=caption,
                                reply_to_message_id=update.message.message_id,
                                supports_streaming=True,
                            )
            except asyncio.TimeoutError:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="⏳ Перевод «Живые голоса» ещё генерируется. Попробуйте /dub <url>",
                    reply_to_message_id=update.message.message_id,
                )
            except Exception as e:
                logger.warning(f"[LiveDub] fail: {e}")
        # --- END LIVEDUB ---
            cleanup_files(media_id)
            logger.info(f"Кэш hit: {media_id}")
            return True

        await set_progress(status_msg, 2, {'title': f'📝 {title}', 'author': f'👤 {performer}  •  {duration//60}:{duration%60:02d}'}, prefix=_pp)
        try:
            # Проверяем кэш обложки — если файл уже есть, не скачиваем
            _existing_thumb = list(THUMBS_DIR.glob(f"{media_id}_thumb*"))
            if _existing_thumb:
                thumb_buffer = prepare_thumbnail(_existing_thumb[0])
                logger.info(f"Обложка кэш: используем {_existing_thumb[0].name}")
            else:
                thumb_cmd = YTDLP_BASE_ARGS + [
                    "--skip-download", "--write-thumbnail", "--no-playlist",
                    "--convert-thumbnails", "jpg",
                    "--output", str(THUMBS_DIR / f"{media_id}_thumb.%(ext)s"),
                    url,
                ]
                _thumb_proc = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: subprocess.run(thumb_cmd, capture_output=True, text=True, timeout=30)
                )
                if _thumb_proc.returncode != 0:
                    _thumb_err = (_thumb_proc.stderr or "").strip()[-300:]
                    logger.warning(f"Обложка yt-dlp returncode={_thumb_proc.returncode}: {_thumb_err}")
                thumb_candidates = list(THUMBS_DIR.glob(f"{media_id}_thumb*"))
                if thumb_candidates:
                    thumb_buffer = prepare_thumbnail(thumb_candidates[0])
                    logger.info(f"Обложка скачана: {thumb_candidates[0].name}")
                else:
                    logger.warning(f"Обложка: файл не найден после yt-dlp (rc={_thumb_proc.returncode})")
        except Exception as e:
            logger.warning(f"Обложка не скачана: {e}")

        await set_progress(status_msg, 3, {'title': f'📝 {title}', 'author': f'👤 {performer}  •  {duration//60}:{duration%60:02d}', 'fmt': '🎵 128 kbps  •  MP3'}, prefix=_pp)

        # Проверяем кэш MP3 — если файл уже есть, скачивание пропускаем
        mp3_path = DOWNLOAD_DIR / f"{media_id}.mp3"
        _existing_mp3 = list(DOWNLOAD_DIR.glob(f"{media_id}*.mp3"))
        if _existing_mp3:
            mp3_path = _existing_mp3[0]
            logger.info(f"MP3 кэш: используем существующий файл {mp3_path.name}")
        else:
            _audio_quality_dl = "64K" if duration > 3600 else "128K"
            audio_cmd = YTDLP_BASE_ARGS + [
                "--format", "bestaudio/best",
                "--extract-audio", "--audio-format", "mp3", "--audio-quality", _audio_quality_dl,
                "--no-playlist",
                "--output", str(DOWNLOAD_DIR / f"{media_id}.%(ext)s"),
                url,
            ]
            proc = await asyncio.get_running_loop().run_in_executor(
                None, lambda: subprocess.run(audio_cmd, capture_output=True, text=True, timeout=600)
            )
            if proc.returncode != 0:
                raise Exception(proc.stderr[-500:] if proc.stderr else "yt-dlp error")

            mp3_path = DOWNLOAD_DIR / f"{media_id}.mp3"
            if not mp3_path.exists():
                mp3_files = list(DOWNLOAD_DIR.glob(f"{media_id}*.mp3"))
                if mp3_files:
                    mp3_path = mp3_files[0]
                else:
                    raise FileNotFoundError("MP3 файл не найден")

        file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
        if file_size_mb > MAX_FILE_SIZE_MB:
            await set_progress(status_msg, 3, {"title": f"📝 {title}", "info": f"⚙️ Файл {file_size_mb:.1f} МБ — пересжимаю в 64 kbps..."}, prefix=_pp)
            mp3_64_path = DOWNLOAD_DIR / f"{media_id}_64.mp3"
            # Re-encode existing mp3 via ffmpeg directly
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg:
                proc = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: subprocess.run(
                        [ffmpeg, "-i", str(mp3_path), "-b:a", "64k", "-y", str(mp3_64_path)],
                        capture_output=True, timeout=300
                    )
                )
                if mp3_64_path.exists():
                    mp3_path.unlink(missing_ok=True)
                    mp3_path = mp3_64_path
                    file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
            if file_size_mb > MAX_FILE_SIZE_MB:
                await update.message.reply_text(
                    f"⚠️ Файл слишком большой ({file_size_mb:.1f} МБ) даже после сжатия до 64 kbps.\n"
                    f"Telegram принимает максимум 50 МБ. Попробуйте более короткое видео."
                )
                cleanup_files(media_id)
                return False

        bitrate = "64" if ("_64" in mp3_path.name or duration > 3600) else "128"

        ai_data          = None
        synopsis_outline = None  # инициализируем здесь — closure-функции ниже ссылаются безусловно
        telegraph_url    = None
        used_audio_part  = None
        used_client      = None
        _early_alt  = None  # alt-links ищем до конспекта если ai_data есть
        _pre_rutube = ""    # инициализируем здесь — closure-функции ниже всегда могут обращаться к ним
        _pre_vk     = ""    # даже если ai_data = None (Gemini недоступен или упал)
        if GEMINI_CLIENTS:
            _fmb = mp3_path.stat().st_size / (1024*1024)
            await set_progress(status_msg, 4, {"title": f"📝 {title}", "author": f"👤 {performer}  •  {duration//60}:{duration%60:02d}", "size": f"🎵 {_fmb:.1f} МБ  •  {bitrate} kbps"}, prefix=_pp)
            ai_data, used_client, used_audio_part = await gemini_analyze_audio(
                mp3_path, title, performer, duration, status_msg, progress_prefix
            )
            if ai_data:
                await set_progress(status_msg, 5, prefix=_pp)

                if ai_data.get("title_topic_warning"):
                    _safe_title = normalize_title_text(choose_safe_public_title(ai_data, full_title))
                    if _safe_title and _safe_title != ai_data.get("real_title"):
                        logger.warning(
                            "Title-topic warning: using fallback title for publication: %r -> %r",
                            ai_data.get("real_title"), _safe_title,
                        )
                        ai_data = {**ai_data, "real_title": _safe_title}

                # Ищем alt-links ДО публикации конспекта — чтобы таймкоды
                # RuTube/VK были доступны уже при первой публикации страницы
                _early_search_title = _build_search_title(ai_data, full_title)
                try:
                    _early_alt = await find_alternative_links(
                        _early_search_title, channel_name, duration,
                        ai_data=ai_data, fallback_title=full_title
                    )
                except Exception as _ea_err:
                    logger.warning(f"early alt-links error: {_ea_err}")
                    _early_alt = None
                _pre_rutube = (_early_alt or {}).get("rutube") or ""
                _pre_vk     = (_early_alt or {}).get("vk")     or ""

                if await asettings_get("synopsis"):
                    telegraph_url, synopsis_outline = await create_telegraph_synopsis(
                        mp3_path, title, performer, duration, url,
                        existing_audio_part=used_audio_part,
                        existing_client=used_client,
                        ai_data=ai_data,
                        rutube_url=_pre_rutube,
                        vk_url=_pre_vk,
                    )
                else:
                    telegraph_url    = None
                    synopsis_outline = None
            # НЕ удаляем audio_part здесь — он нужен для Shorts и Clips.
            # Удаление происходит в блоке finally ниже.

        await set_progress(status_msg, 6, prefix=_pp)
        search_title = _build_search_title(ai_data, full_title)
        tg_author    = normalize_author_name((ai_data or {}).get("real_author") or performer) or performer
        _questions   = (ai_data or {}).get("questions", []) if ai_data else []
        _terms_data  = (ai_data or {}).get("terms_data", {}) or {} if ai_data else {}

        # -- Читаем настройки одним batch вызовом -------------------------
        _all_settings                = await asettings_get_all()
        _feat_analytics              = _all_settings.get("analytics", True)
        _feat_questions              = _all_settings.get("questions", True)
        _feat_terms                  = _all_settings.get("terms", True)
        _feat_study_analysis         = _all_settings.get("study_analysis", True)
        _feat_reflection_application = _all_settings.get("reflection_application", True)
        _mat_format   = (ai_data or {}).get("format", "other")
        _ts_total     = len([l for l in ((ai_data or {}).get("timestamps") or "").split("\n") if l.strip()])
        _ts_cap_limit = get_caption_timestamp_limit(_mat_format)

        _terms_total = sum(len(_terms_data.get(k, [])) for k in ("concepts", "scripture", "translations", "lexicon_notes"))
        _is_qa = (_mat_format == "qa")
        logger.info(
            f"format={_mat_format} is_qa={_is_qa} ts_total={_ts_total} ts_cap={_ts_cap_limit} "
            f"synopsis={'ok' if telegraph_url else 'none'} "
            f"terms_items={_terms_total} "
            f"(concepts={len(_terms_data.get('concepts',[]))} scripture={len(_terms_data.get('scripture',[]))} "
            f"transl={len(_terms_data.get('translations',[]))} lex={len(_terms_data.get('lexicon_notes',[]))}) "
            f"| feat: analytics={_feat_analytics} questions={_feat_questions} terms={_feat_terms} "
            f"study_analysis={_feat_study_analysis} reflection={_feat_reflection_application}"
        )
        async def _noop():
            return None

        # -- Pipeline factories с fallback ----------------------------------

        async def _analytics_pipeline():
            # Пропускаем legacy Аналитику если новая Study Analysis включена
            if _feat_study_analysis:
                return None   # Study Analysis заменяет Аналитику
            if not _feat_analytics:
                return None
            return await create_telegraph_analytics(ai_data, search_title, tg_author, url)

        async def _questions_pipeline():
            # Пропускаем legacy Вопросы если новое Размышление включено
            if _feat_reflection_application:
                return None   # Reflection заменяет Questions
            if not _feat_questions:
                return None
            return await create_telegraph_questions(_questions, search_title, tg_author)

        async def _terms_pipeline():
            # Термины пропускаем если обе новые страницы включены
            # (они уже включают термины в своих разделах)
            if _feat_study_analysis and _feat_reflection_application:
                return None
            if not _feat_terms:
                return None
            return await create_telegraph_terms(_terms_data, search_title, tg_author, url)

        async def _study_analysis_pipeline():
            if not _feat_study_analysis:
                return None
            compact = lambda: create_telegraph_analytics(ai_data, search_title, tg_author, url)
            logger.info("StudyAnalysis: enabled")
            return await create_telegraph_study_analysis(
                ai_data, search_title, tg_author, yt_url=url, compact_fn=compact,
                rutube_url=_pre_rutube, vk_url=_pre_vk,
                synopsis_outline=synopsis_outline,
            )

        async def _reflection_application_pipeline():
            if not _feat_reflection_application:
                return None
            compact = lambda: create_telegraph_questions(_questions, search_title, tg_author)
            logger.info("ReflectionApplication: enabled")
            return await create_telegraph_reflection_application(
                _questions, search_title, tg_author,
                ai_data=ai_data, duration=duration, yt_url=url,
                compact_fn=compact,
                rutube_url=_pre_rutube, vk_url=_pre_vk,
                synopsis_outline=synopsis_outline,
            )

        async def _alt_links_result():
            # Если alt-links уже нашли до публикации конспекта — не ищем повторно
            if _early_alt is not None and isinstance(_early_alt, dict):
                return _early_alt
            return await find_alternative_links(
                search_title, channel_name, duration,
                ai_data=ai_data, fallback_title=full_title
            )

        # _has_early_alt больше не нужен
        _ = None

        # DEEP-QUALITY FIX [A]: Study + Reflection НЕ запускаются параллельно — на free tier
        # это вызывает каскадные 503. Делаем их последовательно, остальное параллельно.
        (
            alt_links, quotes_tg, questions_tg, terms_tg,
        ) = await asyncio.gather(
            _alt_links_result(),
            _analytics_pipeline(),
            _questions_pipeline(),
            _terms_pipeline(),
            return_exceptions=True,
        )
        # Study и Reflection: quality-first default keeps separate calls.
        # Optional combined mode is opt-in only (COMBINE_STUDY_REFLECTION=1)
        # and falls back to separate calls if either page is missing.
        study_analysis_tg = None
        reflection_application_tg = None
        if _feat_study_analysis and _feat_reflection_application and combined_study_reflection_enabled():
            try:
                logger.info("Study+Reflection combined: enabled by COMBINE_STUDY_REFLECTION=1")
                _study_compact = lambda: create_telegraph_analytics(ai_data, search_title, tg_author, url)
                _reflection_compact = lambda: create_telegraph_questions(_questions, search_title, tg_author)
                _combined_result = await create_telegraph_study_reflection_combined(
                    ai_data, _questions, search_title, tg_author, yt_url=url,
                    study_compact_fn=_study_compact, reflection_compact_fn=_reflection_compact,
                    rutube_url=_pre_rutube, vk_url=_pre_vk,
                    synopsis_outline=synopsis_outline, duration=duration,
                )
                if _combined_result:
                    study_analysis_tg = _combined_result.study_url
                    reflection_application_tg = _combined_result.reflection_url
                    logger.info(
                        "Study+Reflection combined result: mode=%s study_type=%s reflection_type=%s",
                        _combined_result.mode,
                        _combined_result.study_page_type,
                        _combined_result.reflection_page_type,
                    )
            except Exception as _e_combined:
                logger.warning(f"Study+Reflection combined pipeline error: {_e_combined}")
                study_analysis_tg = None
                reflection_application_tg = None

        if not study_analysis_tg:
            try:
                study_analysis_tg = await _study_analysis_pipeline()
            except Exception as _e_study:
                logger.warning(f"StudyAnalysis pipeline error: {_e_study}")
                study_analysis_tg = None
        if not reflection_application_tg:
            try:
                reflection_application_tg = await _reflection_application_pipeline()
            except Exception as _e_refl:
                logger.warning(f"ReflectionApplication pipeline error: {_e_refl}")
                reflection_application_tg = None
        if isinstance(alt_links, Exception):
            logger.warning(f"find_alternative_links error: {alt_links}", exc_info=alt_links)
            alt_links = {"rutube": None, "vk": None}
        if isinstance(quotes_tg, Exception):
            logger.warning(f"analytics error: {quotes_tg}", exc_info=quotes_tg)
            quotes_tg = None
        if isinstance(questions_tg, Exception):
            logger.warning(f"questions error: {questions_tg}", exc_info=questions_tg)
            questions_tg = None
        if isinstance(terms_tg, Exception):
            logger.warning(f"terms error: {terms_tg}", exc_info=terms_tg)
            terms_tg = None
        if isinstance(study_analysis_tg, Exception):
            logger.warning(f"study_analysis error: {study_analysis_tg}", exc_info=study_analysis_tg)
            study_analysis_tg = None
        if isinstance(reflection_application_tg, Exception):
            logger.warning(f"reflection_application error: {reflection_application_tg}", exc_info=reflection_application_tg)
            reflection_application_tg = None

        # ── Навигация между тремя страницами ──────────────────────────────
        # После публикации всех трёх страниц вшиваем ссылки друг на друга
        # через editPage. Каждая страница получает блок навигации внизу.

        # v10 FIX #16 (P1): если reflection_application_tg — это fallback compact_fn
        # (create_telegraph_questions вернул URL «Вопросы:»), не вставляем его в nav
        # как «Размышление». Live-аудит 39 страниц выявил 2 таких случая из 13 (15%).
        def _is_reflection_fallback(url: str) -> bool:
            if not url:
                return False
            path = url.replace("https://telegra.ph/", "").lower()
            # Настоящее Размышление всегда начинается с razmyshlenie-
            if path.startswith("razmyshlenie-"):
                return False
            # Явный Вопросы-URL (v9+)
            if path.startswith("voprosy-"):
                return True
            # Любой другой path (конспект-подобный и т.д.) — считаем fallback
            return True

        if reflection_application_tg and _is_reflection_fallback(reflection_application_tg):
            logger.warning(
                "Navigation v10: reflection_application_tg выглядит как fallback Вопросы "
                "(%s) — перемещаю в questions_tg, из nav убираю",
                reflection_application_tg,
            )
            if not questions_tg:
                questions_tg = reflection_application_tg
            reflection_application_tg = None

        _nav_pages = [
            ("Конспект",                telegraph_url or ""),
            ("Разбор материала",         study_analysis_tg or ""),
            ("Размышление и применение", reflection_application_tg or ""),
        ]
        _nav_filled = [(label, u) for label, u in _nav_pages if u]
        if len(_nav_filled) >= 2:
            async def _add_nav_to_page(page_url: str, page_label: str):
                """Добавляет навигационный блок на Telegraph-страницу через editPage.
                v10 FIX #17 (P2): retry при сетевом сбое — 2 попытки с паузой 5с.
                """
                for _nav_attempt in range(2):
                    try:
                        loop = asyncio.get_running_loop()
                        path = page_url.replace("https://telegra.ph/", "")
                        resp = await loop.run_in_executor(None, lambda: requests.get(
                            f"https://api.telegra.ph/getPage/{path}?return_content=true",
                            timeout=30,
                        ))
                        data = resp.json()
                        if not data.get("ok"):
                            return
                        current_nodes = data["result"].get("content", [])
                        current_title = data["result"].get("title", page_label)
                        current_author = data["result"].get("author_name", "")

                        # Строим навигационный блок
                        nav_children = []
                        for label, u in _nav_filled:
                            if u == page_url:
                                continue  # текущую страницу не ссылаем на себя
                            if nav_children:
                                nav_children.append(" · ")
                            nav_children.append({"tag": "a", "attrs": {"href": u}, "children": [label]})

                        if not nav_children:
                            return

                        nav_nodes = [
                            {"tag": "hr"},
                            # Keep the space outside <b>; Telegraph/Markdown renderers may trim
                            # trailing spaces inside bold nodes, producing **...:**[link].
                            {"tag": "p", "children": [{"tag": "b", "children": ["📂 Читать также:"]}, " "] + nav_children},
                        ]

                        # Убираем старый навигационный блок если уже был (повторный вызов)
                        clean_nodes = current_nodes
                        if (current_nodes and isinstance(current_nodes[-1], dict)
                                and current_nodes[-1].get("tag") == "p"):
                            last_children = current_nodes[-1].get("children", [])
                            if last_children and isinstance(last_children[0], dict):
                                last_text = last_children[0].get("children", [""])
                                if last_text and "Читать также" in str(last_text[0]):
                                    clean_nodes = current_nodes[:-2]  # убираем hr + p

                        final_nodes = clean_nodes + nav_nodes
                        await _edit_telegraph_page(page_url, current_title, current_author, final_nodes, loop)
                        logger.info(f"Navigation: добавлена навигация на {page_url}")

                        # BUG-C FIX v2: propagate cross-nav to ALL subsequent parts
                        # Walk chain: page 1 → page 2 → page 3 etc. via ➡ Дальше links
                        _visited_parts = {page_url}
                        _parts_to_visit = []

                        def _find_next_urls(nodes_list):
                            """Extract all ➡ Дальше URLs from nodes."""
                            urls = []
                            for _pn in nodes_list:
                                if not isinstance(_pn, dict):
                                    continue
                                for _ch in _pn.get("children", []):
                                    if (isinstance(_ch, dict) and _ch.get("tag") == "a"
                                            and "➡" in str(_ch.get("children", []))):
                                        _href = _ch.get("attrs", {}).get("href", "")
                                        if _href:
                                            # Normalize relative URLs to full
                                            if _href.startswith("/"):
                                                _href = "https://telegra.ph" + _href
                                            urls.append(_href)
                                    # Also check nested children (nav block may have deeper structure)
                                    if isinstance(_ch, dict):
                                        for _gch in _ch.get("children", []):
                                            if (isinstance(_gch, dict) and _gch.get("tag") == "a"
                                                    and "➡" in str(_gch.get("children", []))):
                                                _href = _gch.get("attrs", {}).get("href", "")
                                                if _href:
                                                    if _href.startswith("/"):
                                                        _href = "https://telegra.ph" + _href
                                                    urls.append(_href)
                            return urls

                        _parts_to_visit = _find_next_urls(current_nodes)

                        for _part_url in _parts_to_visit:
                            if _part_url in _visited_parts:
                                continue
                            _visited_parts.add(_part_url)
                            try:
                                _p_path = _part_url.replace("https://telegra.ph/", "").lstrip("/")
                                _p_resp = await loop.run_in_executor(None, lambda _pp=_p_path: requests.get(
                                    f"https://api.telegra.ph/getPage/{_pp}?return_content=true",
                                    timeout=30,
                                ))
                                _p_data = _p_resp.json()
                                if _p_data.get("ok"):
                                    _p_nodes = _p_data["result"].get("content", [])
                                    _p_title = _p_data["result"].get("title", "")
                                    _p_author = _p_data["result"].get("author_name", "")
                                    # Add any further ➡ links to the visit queue
                                    for _next in _find_next_urls(_p_nodes):
                                        if _next not in _visited_parts:
                                            _parts_to_visit.append(_next)
                                    # Check if cross-nav already added
                                    _already = any(
                                        isinstance(n, dict) and
                                        "Читать также" in str(n.get("children", ""))
                                        for n in _p_nodes[-3:]
                                    )
                                    if not _already:
                                        await _edit_telegraph_page(
                                            _part_url, _p_title, _p_author,
                                            _p_nodes + nav_nodes, loop
                                        )
                                        logger.info(f"Navigation: добавлена навигация на {_part_url} (часть 2+)")
                            except Exception as _e:
                                logger.warning(f"Navigation: не удалось добавить на часть {_part_url}: {_e}")

                        return  # успех — выходим из retry-цикла
                    except Exception as _nav_err:
                        if _nav_attempt == 0:
                            logger.warning(
                                f"Navigation: ошибка на {page_url} (попытка 1/2): {_nav_err} — retry через 5с"
                            )
                            await asyncio.sleep(5)
                        else:
                            logger.warning(
                                f"Navigation: не удалось добавить навигацию на {page_url}: {_nav_err}"
                            )

            nav_tasks = [_add_nav_to_page(u, label) for label, u in _nav_filled]
            await asyncio.gather(*nav_tasks, return_exceptions=True)

        # Новые страницы имеют приоритет над legacy compact в caption
        _q_link     = study_analysis_tg         or quotes_tg    or ""
        _ref_link   = reflection_application_tg or questions_tg or ""
        # Термины скрываем если обе новые страницы есть — они уже включены в Разбор материала
        _terms_link = "" if (study_analysis_tg and reflection_application_tg) else (terms_tg or "")

        rutube_url = alt_links.get("rutube") or ""
        vk_url     = alt_links.get("vk")     or ""

        if ai_data:
            _coverage_ratio = timestamp_coverage_ratio((ai_data or {}).get("timestamps", ""), duration)
            _segments_status = "partial" if (duration >= 600 and _coverage_ratio and _coverage_ratio < 0.75) else "complete"
            if (ai_data or {}).get("timestamp_coverage_warning") and duration >= 600:
                _segments_status = "partial"
            ai_data = {
                **ai_data,
                "timestamp_coverage_ratio": _coverage_ratio,
                "segments_status": _segments_status,
            }

        _pub_status = build_publication_status(
            synopsis_url=telegraph_url or "",
            study_url=study_analysis_tg or "",
            reflection_url=reflection_application_tg or "",
            expect_synopsis=True,
            expect_study=bool(_feat_study_analysis),
            expect_reflection=bool(_feat_reflection_application),
        )
        _ai_caption_base = ai_data
        if _ai_caption_base and _ts_total > 0:
            _cap_limit_preview = get_caption_timestamp_limit(_mat_format)
            if _ts_total > _cap_limit_preview:
                _ai_caption_base = {
                    **_ai_caption_base,
                    "_caption_timestamps_trimmed": True,
                    "_caption_timestamps_total": _ts_total,
                    "_caption_timestamps_shown": min(_ts_total, _cap_limit_preview),
                }
        if ai_data and _pub_status.warning:
            logger.warning(
                "Publication status for %s: status=%s missing=%s",
                media_id, _pub_status.status, ",".join(_pub_status.missing),
            )
            _ai_caption_base = {**(_ai_caption_base or ai_data), "_partial_publication_warning": _pub_status.warning}

        # PATCH-FIX: surface lite-model fallback in caption so user knows quality may be reduced
        if _gemini_last_was_fallback:
            _ai_caption_base = {**(_ai_caption_base or ai_data), "_gemini_was_fallback": True}

        def _build(data, **kw):
            return build_caption(performer, title, duration, file_size_mb,
                                 data, bitrate, url, telegraph_url, rutube_url, vk_url,
                                 _q_link, _ref_link, _terms_link,
                                 study_tg_url=study_analysis_tg or "",
                                 reflection_tg_url=reflection_application_tg or "",
                                 **kw)

        _ts_limit = get_caption_timestamp_limit(_mat_format)
        # Применяем format-лимит к таймкодам сразу (до проверки переполнения)
        _ai_for_caption = _ai_caption_base
        if _ai_caption_base and _ai_caption_base.get("timestamps"):
            ts_limited = _trim_timestamps(_ai_caption_base["timestamps"], _ts_limit)
            if ts_limited != _ai_caption_base["timestamps"]:
                _ai_for_caption = {
                    **_ai_caption_base,
                    "timestamps": ts_limited,
                    "_caption_timestamps_trimmed": True,
                    "_caption_timestamps_total": _ts_total,
                    "_caption_timestamps_shown": min(_ts_total, _ts_limit),
                }
        caption = _build(_ai_for_caption)
        # Полный текст для отдельного сообщения (лимит 4096) — без обрезки
        # Все таймкоды + полный main_topic + хэштеги
        def _build_full(data):
            return build_caption(performer, title, duration, file_size_mb,
                                 data, bitrate, url, telegraph_url, rutube_url, vk_url,
                                 _q_link, _ref_link, _terms_link,
                                 study_tg_url=study_analysis_tg or "",
                                 reflection_tg_url=reflection_application_tg or "",
                                 full_mode=True)
        full_caption = _build_full(_ai_caption_base) if _ai_caption_base else caption
        if visible_length(full_caption) > 4096:
            full_caption = safe_trim_caption(full_caption, 4096)
        # Умное обрезание до 1024 видимых символов (без HTML-тегов)
        _cap_ts_str = (_ai_for_caption or {}).get("timestamps", "") or ""  # отслеживаем фактические ts в caption
        # Шаг 1: убрать хэштеги
        if visible_length(caption) > 1024 and _ai_for_caption and _ai_for_caption.get("hashtags"):
            caption = _build({**_ai_for_caption, "hashtags": ""})
        # Шаг 1.5: убрать main_topic
        if visible_length(caption) > 1024 and _ai_for_caption and _ai_for_caption.get("main_topic"):
            caption = _build({**_ai_for_caption, "hashtags": "", "main_topic": ""})
        # Шаг 2: сократить таймкоды ещё сильнее (половина лимита)
        if visible_length(caption) > 1024 and _ai_for_caption and _ai_for_caption.get("timestamps"):
            ts_half = _trim_timestamps(_ai_for_caption["timestamps"], max(_ts_limit // 2, 3))
            caption = _build({**_ai_for_caption, "hashtags": "", "timestamps": ts_half, "_caption_timestamps_trimmed": True})
            _cap_ts_str = ts_half
        # Шаг 3: раньше таймкоды удалялись полностью. Это давало ts_in_cap=0
        # даже при 11–13 таймкодах и визуально выглядело как «половина ролика
        # потерялась». Сначала пробуем сверхкомпактный равномерный набор,
        # сохраняющий начало/середину/финал материала.
        if visible_length(caption) > 1024 and _ai_for_caption and _ai_for_caption.get("timestamps"):
            _fit_found = False
            for _mini_limit in (7, 5, 3):
                ts_mini = _trim_timestamps(_ai_for_caption["timestamps"], min(_mini_limit, _ts_limit))
                candidate = _build({
                    **_ai_for_caption,
                    "hashtags": "",
                    "main_topic": "",
                    "timestamps": ts_mini,
                    "_caption_timestamps_trimmed": True,
                })
                if visible_length(candidate) <= 1024:
                    caption = candidate
                    _cap_ts_str = ts_mini
                    _fit_found = True
                    break
            if not _fit_found:
                caption = _build({**_ai_for_caption, "hashtags": "", "main_topic": "", "timestamps": "", "_caption_timestamps_trimmed": True})
                _cap_ts_str = ""
        # Шаг 4: последний резерв — шапка + ссылки, обрезаем без лома тегов
        if visible_length(caption) > 1024:
            platform_block = build_platform_links(url, rutube_url, vk_url)
            tg_block = build_telegraph_links(
                telegraph_url or "", _q_link, _ref_link, _terms_link,
                study_tg_url=study_analysis_tg or "",
                reflection_tg_url=reflection_application_tg or "",
            )
            # Правильный порядок: сначала Читать Подробный Разбор, потом Смотреть Видео
            # + восстанавливаем заголовки секций (build_telegraph_links / build_platform_links их не включают)
            footer_parts = []
            if tg_block:
                footer_parts.append("<b>📂 Читать Подробный Разбор:</b>")
                footer_parts.append(tg_block)
            if platform_block:
                footer_parts.append("<b>Смотреть Видео:</b>")
                footer_parts.append(platform_block)
            footer = "\n".join(footer_parts)
            header_lines = caption.split("\n")[:4]
            header = "\n".join(header_lines)
            caption = header + ("\n\n" + footer if footer else "")
            caption = safe_trim_caption(caption, 1024)
            _cap_ts_str = ""

        # ts_in_cap — считаем из финального состояния после всех шагов trimming
        _ts_in_cap = len([l for l in _cap_ts_str.split("\n") if l.strip()])
        _caption_trim_stage = ""
        if _ts_total > _ts_in_cap:
            _caption_trim_stage = "timestamps_trimmed" if _ts_in_cap else "timestamps_removed"
        logger.info(f"Caption visible_len={visible_length(caption)} format={_mat_format} ts_total={_ts_total} ts_cap_limit={_ts_limit} ts_in_cap={_ts_in_cap}")
        if ai_data:
            ai_data = {
                **ai_data,
                "caption_trim_stage": _caption_trim_stage,
                "caption_timestamps_total": _ts_total,
                "caption_timestamps_shown": _ts_in_cap,
            }
            # Добавляем duration в ai_data чтобы /pdf мог его читать из кэша
            if duration and not ai_data.get("duration"):
                ai_data = {**ai_data, "duration": duration}
            await adb_save(video_id=media_id, url=url,
                    questions=ai_data.get("questions", []),
                    # Сохраняем приоритетные ссылки: новые страницы > legacy compact
                    quotes_tg_url=_q_link,
                    questions_tg_url=_ref_link,
                    ai_data=ai_data,
                    telegraph_url=telegraph_url or "",
                    cache_version=CACHE_VERSION,
                    prompt_version=get_prompt_fingerprint(),
                    model_name=GEMINI_MODEL,
                    terms_tg_url=_terms_link,
                    rutube_url=rutube_url or "",
                    vk_url=vk_url or "",
                    study_tg_url=study_analysis_tg or "",
                    reflection_tg_url=reflection_application_tg or "",
                    publication_status=_pub_status.status,
                    publication_missing=missing_to_json(_pub_status.missing),
                    publication_warning=_pub_status.warning)

            # V3-P24: durable human-readable archive independent of expiring cache.
            try:
                _archive_record = build_generated_page_record(
                    video_id=media_id,
                    source_url=url,
                    title=normalize_common_typos(normalize_title_text((ai_data or {}).get("real_title") or search_title or title) or (search_title or title)),
                    author=normalize_common_typos(normalize_author_name((ai_data or {}).get("real_author") or tg_author or performer) or (tg_author or performer)),
                    event=normalize_common_typos(_scrub_inline((ai_data or {}).get("real_event", ""))),
                    format_name=_mat_format,
                    duration=duration,
                    youtube_url=url,
                    rutube_url=rutube_url or "",
                    vk_url=vk_url or "",
                    synopsis_url=telegraph_url or "",
                    study_url=study_analysis_tg or "",
                    reflection_url=reflection_application_tg or "",
                    terms_url=terms_tg or "",
                    questions_url=questions_tg or "",
                    hashtags=(ai_data or {}).get("hashtags", []),
                    key_categories=(ai_data or {}).get("key_categories", []),
                    scripture_refs=extract_scripture_refs(ai_data),
                    publication_status=_pub_status.status,
                    publication_missing=_pub_status.missing,
                    publication_warning=_pub_status.warning,
                    quality_warnings=collect_quality_warnings(ai_data),
                    timestamp_coverage_ratio=timestamp_coverage_archive_fields(ai_data)[0],
                    segments_status=timestamp_coverage_archive_fields(ai_data)[1],
                    caption_trim_stage=(ai_data or {}).get("caption_trim_stage", ""),
                    caption_timestamps_total=(ai_data or {}).get("caption_timestamps_total", 0),
                    caption_timestamps_shown=(ai_data or {}).get("caption_timestamps_shown", 0),
                    model=GEMINI_MODEL,
                    prompt_version=get_prompt_fingerprint(),
                    prompt_variant=os.getenv("PROMPT_EXPERIMENT_TAG", ""),
                )
                await asave_generated_page_record(_archive_record)
                _segment_export = await asave_segment_plan_export(
                    video_id=media_id,
                    title=_archive_record.get("title", ""),
                    author=_archive_record.get("author", ""),
                    timestamps=(ai_data or {}).get("timestamps", ""),
                    duration=duration,
                    format_name=_mat_format,
                    segments_status=(ai_data or {}).get("segments_status", "complete"),
                    timestamp_coverage_ratio=(ai_data or {}).get("timestamp_coverage_ratio", 0.0),
                )
                logger.info("Generated pages archive saved for %s; segments=%s", media_id, _segment_export.get("count"))
            except Exception as _archive_err:
                logger.warning("Generated pages archive save failed for %s: %s", media_id, _archive_err)

        with open(mp3_path, "rb") as audio_file:
            audio_title     = normalize_common_typos(normalize_title_text((ai_data or {}).get("real_title")  or title) or title)
            audio_performer = normalize_common_typos(normalize_author_name((ai_data or {}).get("real_author") or performer) or performer)
            # Увеличенный таймаут для больших файлов + retry при сетевых ошибках
            for _attempt in range(3):
                try:
                    audio_file.seek(0)
                    await update.message.reply_audio(
                        audio=audio_file, title=audio_title, performer=audio_performer,
                        thumbnail=thumb_buffer, duration=duration, caption=caption,
                        parse_mode="HTML",
                        write_timeout=180, read_timeout=180, connect_timeout=60,
                    )
                    break  # успех
                except Exception as upload_err:
                    err_name = type(upload_err).__name__
                    err_str  = str(upload_err).lower()
                    _retryable_names = ("Timeout", "NetworkError", "TimedOut", "ReadError", "ConnectError")
                    _retryable_strs  = ("internal server error", "server error", "bad gateway", "gateway timeout")
                    _is_retryable = (
                        any(x in err_name for x in _retryable_names) or
                        any(x in err_str  for x in _retryable_strs)
                    )
                    if _attempt < 2 and _is_retryable:
                        logger.warning(f"Upload попытка {_attempt+1}/3 не удалась ({err_name}: {str(upload_err)[:120]}), повтор...")
                        await asyncio.sleep(5 * (_attempt + 1))
                    else:
                        raise  # последняя попытка или не сетевая ошибка

        logger.info(f"Done: [{normalize_author_name(performer) or performer}] {normalize_title_text(title) or title} ({file_size_mb:.1f} MB, 128kbps)")

        # Отправляем полное описание отдельным сообщением (лимит 4096)
        _feat_full_caption = await asettings_get("caption_full_text")
        if _feat_full_caption and full_caption and full_caption != caption:
            try:
                await update.message.reply_text(full_caption, parse_mode="HTML")
            except Exception as _fe:
                logger.warning(f"Full caption send error: {_fe}")

        # ── PDF (опционально) ────────────────────────────────────
        _feat_pdf = await asettings_get("generate_pdf")
        if _feat_pdf and ai_data:
            _status_msg = None
            _pdf_path = None
            try:
                from services.pdf_generator import generate_sermon_pdf_async

                DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

                _pdf_path = str(DOWNLOAD_DIR / f"{media_id}_{uuid.uuid4().hex[:6]}.pdf")

                _raw_t = (ai_data.get("real_title") or "").strip() or (title or "").strip()
                _raw_a = (ai_data.get("real_author") or "").strip() or (performer or "").strip()
                _pdf_title  = normalize_title_text(_raw_t)  if _raw_t else "Без названия"
                _pdf_author = normalize_author_name(_raw_a) if _raw_a else "Неизвестный"
                _dur_str    = format_timestamp(duration) if duration else ""

                _pdf_urls = {}
                if telegraph_url:              _pdf_urls["synopsis"]   = telegraph_url
                if study_analysis_tg:          _pdf_urls["study"]      = study_analysis_tg
                if reflection_application_tg:  _pdf_urls["reflection"] = reflection_application_tg
                if terms_tg:                   _pdf_urls["terms"]      = terms_tg

                if _pdf_urls:
                    logger.info(f"PDF: генерирую ({list(_pdf_urls.keys())})")

                    _status_msg = await update.message.reply_text("📄 Генерирую PDF…")

                    async def _pdf_progress(stage: str, pct: int):
                        try:
                            await _status_msg.edit_text(f"📄 PDF: {stage} ({pct}%)")
                        except Exception:
                            pass

                    _pdf_result = await generate_sermon_pdf_async(
                        output_path      = _pdf_path,
                        title            = _pdf_title,
                        performer        = _pdf_author,
                        duration_str     = _dur_str,
                        urls             = _pdf_urls,
                        progress_callback = _pdf_progress,
                    )

                    if _pdf_result and Path(_pdf_result).exists():
                        _size = Path(_pdf_result).stat().st_size
                        if _size < 200:
                            logger.warning(f"PDF пустой: {_size} байт")
                            await update.message.reply_text("❌ Не удалось создать PDF (файл пустой).")
                        elif _size > 49 * 1024 * 1024:
                            logger.warning(f"PDF слишком большой: {_size} байт")
                            await update.message.reply_text("❌ PDF слишком большой для отправки.")
                        else:
                            def _safe_fn(s: str) -> str:
                                return re.sub(r'[\\/:*?"<>|\n\r\t]', '', s).strip()[:80] or "doc"

                            _fn = f"{_safe_fn(_pdf_author)} — {_safe_fn(_pdf_title)}.pdf"

                            with open(_pdf_result, "rb") as _pdf_f:
                                await update.message.reply_document(
                                    document   = _pdf_f,
                                    filename   = _fn,
                                    caption    = "📄 <b>PDF-версия материала</b>",
                                    parse_mode = "HTML",
                                )
                            logger.info("PDF: отправлен")
                    else:
                        logger.warning("PDF: generate_sermon_pdf_async вернул None или файл не создан")
                        await update.message.reply_text("❌ Не удалось создать PDF.")

            except Exception as _pdf_err:
                logger.warning(f"PDF генерация не удалась: {_pdf_err}", exc_info=True)
            finally:
                if _pdf_path:
                    try:
                        Path(_pdf_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                if _status_msg:
                    try:
                        await _status_msg.delete()
                    except Exception:
                        pass

        # ── Shorts (после основного результата) ─────────
        _feat_shorts = await asettings_get("shorts")
        if ai_data and _feat_shorts:
            logger.info("Shorts: feature enabled, starting pipeline")
            await process_and_send_shorts(
                url=url,
                media_id=media_id,
                mp3_path=mp3_path,
                title=title,
                performer=performer,
                duration=duration,
                ai_data=ai_data,
                update=update,
                existing_audio_part=used_audio_part,   # ← REUSE
                existing_client=used_client,            # ← REUSE
                rutube_url=rutube_url,
                vk_url=vk_url,
            )
        else:
            logger.info(f"Shorts: skipped (feat={_feat_shorts}, ai_data={'yes' if ai_data else 'no'})")

        # ── Clips (после Shorts) ──────────────────────────
        _feat_clips = await asettings_get("clips")
        if ai_data and _feat_clips:
            logger.info("Clips: feature enabled, starting pipeline")
            await process_and_send_clips(
                url=url,
                media_id=media_id,
                mp3_path=mp3_path,
                title=title,
                performer=performer,
                duration=duration,
                ai_data=ai_data,
                update=update,
                existing_audio_part=used_audio_part,   # ← REUSE
                existing_client=used_client,            # ← REUSE
                rutube_url=rutube_url,
                vk_url=vk_url,
            )
        else:
            logger.info(f"Clips: skipped (feat={_feat_clips}, ai_data={'yes' if ai_data else 'no'})")

        # ── Montage + Highlights (один общий Gemini text-only вызов) ─
        _prefetched_extras = {"montage_candidates": [], "highlights_candidates": []}
        _feat_montage = await asettings_get("shorts_montage")
        _feat_highlights = await asettings_get("shorts_highlights")

        if ai_data and (_feat_montage or _feat_highlights):
            logger.info("Extras: feature enabled, requesting ONE Gemini text call for montage+highlights")
            _prefetched_extras = await create_extras_candidates(
                ai_data=ai_data,
                title=title,
                performer=performer,
                duration=duration,
            )

        if ai_data and _feat_montage:
            logger.info("Montage: feature enabled, starting pipeline")
            await process_and_send_montage(
                url=url, media_id=media_id, mp3_path=mp3_path,
                title=title, performer=performer, duration=duration,
                ai_data=ai_data, update=update,
                rutube_url=rutube_url, vk_url=vk_url,
                prefetched_candidates=_prefetched_extras.get("montage_candidates", []),
            )
        else:
            logger.info(f"Montage: skipped (feat={_feat_montage})")

        if ai_data and _feat_highlights:
            logger.info("Highlights: feature enabled, starting pipeline")
            await process_and_send_highlights(
                url=url, media_id=media_id, mp3_path=mp3_path,
                title=title, performer=performer, duration=duration,
                ai_data=ai_data, update=update,
                rutube_url=rutube_url, vk_url=vk_url,
                prefetched_candidates=_prefetched_extras.get("highlights_candidates", []),
            )
        else:
            logger.info(f"Highlights: skipped (feat={_feat_highlights})")


        # --- LIVEDUB: отправка результата ---
        if live_dub_task and context:
            try:
                livedub_result = await asyncio.wait_for(live_dub_task, timeout=600)
                if livedub_result:
                    livedub_path, is_fallback, has_subs = livedub_result
                    if livedub_path and livedub_path.exists():
                        if is_fallback:
                            caption = "⚠️ Живой перевод Яндекса недоступен/сломался.\nОтправляю резерв: оригинальное видео" + (" + русские субтитры." if has_subs else ".")
                        else:
                            caption = "🎬 Живые голоса Яндекса" + ("\n💬 Русские субтитры сделаны независимо через Whisper + Gemini" if has_subs else "")
                        with open(livedub_path, "rb") as f:
                            await context.bot.send_video(
                                chat_id=update.effective_chat.id,
                                video=f,
                                caption=caption,
                                reply_to_message_id=update.message.message_id,
                                supports_streaming=True,
                            )
            except asyncio.TimeoutError:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="⏳ Перевод «Живые голоса» ещё генерируется. Попробуйте /dub <url>",
                    reply_to_message_id=update.message.message_id,
                )
            except Exception as e:
                logger.warning(f"[LiveDub] fail: {e}")
        # --- END LIVEDUB ---
        cleanup_files(media_id)

        return True

    except Exception as e:
        # AUDIT M3: silent_errors=True (плейлист) — не спамим пользователю
        # AUDIT M15: проверяем media_id через locals() вместо except NameError,
        # который опасно поглощает OSError от cleanup_files.
        from core.utils import mask_api_key as _mask
        _safe = _mask(str(e))
        logger.error(f"Ошибка: {_safe}", exc_info=True)
        if not silent_errors:
            try:
                await update.message.reply_text(f"❌ Ошибка: {_safe[:200]}")
            except Exception:
                pass
        if "media_id" in locals():
            try:
                cleanup_files(media_id)
            except Exception as _ce:
                logger.warning(f"cleanup_files после ошибки: {_ce}")
        return False
    finally:
        # Cancel background live-dub task before cleaning its working directory
        if "live_dub_task" in locals() and live_dub_task is not None and not live_dub_task.done():
            live_dub_task.cancel()
            try:
                await live_dub_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        # Очищаем временную директорию LiveDub, если она создавалась
        if "ld_work" in locals() and ld_work.exists():
            shutil.rmtree(ld_work, ignore_errors=True)

        # Удаляем audio_part из Gemini Files API — ТОЛЬКО ЗДЕСЬ,
        # после того как все задачи (Synopsis, Shorts, Clips) завершены.
        if used_audio_part and hasattr(used_audio_part, 'name') and used_client:
            try:
                await used_client.aio.files.delete(name=used_audio_part.name)
                logger.info("Gemini Files: audio_part удалён")
            except Exception as _del_err:
                logger.warning(f"Gemini Files delete error: {_del_err}")
        if thumb_buffer:
            thumb_buffer.close()


# ─── Обработка плейлиста ─────────────────────────────────────


