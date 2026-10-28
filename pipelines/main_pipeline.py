#!/usr/bin/env python3
"""
Main Pipeline — process_single_video.
Извлечено из bot.py строки 12470–13446.
"""

import html as html_mod

from services.ffmpeg import YTDLP_BASE_ARGS  # FIX #23: нужен результат, не функция
from services.async_process import run_cancellable_process
from services.async_worker import await_owned_with_soft_timeout
from core.globals import (
    DOWNLOAD_DIR,
    THUMBS_DIR,
    GEMINI_CLIENTS,
)
from core.database import (
    _db_conn,
    adb_get,
    adb_save,
    asettings_get,
    asettings_get_all,
    is_cache_valid,
    GEMINI_MODEL,
    CACHE_VERSION,  # FIX #11
    get_max_file_size_mb,
    current_livedub_file_id_cache_version,
    get_prompt_fingerprint,  # FIX #11
)
from core.utils import cleanup_files, parse_title
from converters.md_telegraph import (
    visible_length,  # FIX #11
    get_caption_timestamp_limit,  # FIX #11
)
from pipelines.analysis_stage import AnalysisRequest, run_analysis
from pipelines.archive_stage import archive_pages
from pipelines.caption_inputs import build_caption_inputs
from pipelines.caption_stage import CaptionSource, fit_caption
from pipelines.cache_stage import load_cache
from pipelines.cache_hit_delivery_stage import CacheHitDeliveryRequest, deliver_cache_hit
from pipelines.cached_presentation_stage import (
    cached_page_links,
    prepare_cached_presentation,
)
from pipelines.delivery_stage import DeliveryRequest, deliver_cached_result, deliver_result
from pipelines.extras_stage import ExtrasRequest, run_extras
from pipelines.fresh_caption_stage import finalize_fresh_caption
from pipelines.livedub_background_stage import run_livedub_background
from pipelines.livedub_cached_delivery_stage import deliver_cached_livedub
from pipelines.livedub_dub_stage import make_livedub_video, wait_for_livedub_video
from pipelines.livedub_full_qa_stage import run_livedub_full_qa
from pipelines.livedub_failure_stage import (
    notify_livedub_send_failure,
    reject_oversized_livedub_video,
    send_full_livedub_failure_notice,
    send_livedub_timeout_notice,
)
from pipelines.livedub_publication_stage import prepare_livedub_publication
from pipelines.livedub_quick_qa_stage import run_livedub_quick_qa
from pipelines.livedub_quick_stage import finish_livedub_quick
from pipelines.livedub_video_assets_stage import (
    get_livedub_technical_warnings,
    prepare_livedub_video_assets,
)
from pipelines.livedub_video_delivery_stage import send_livedub_video_with_cover
from pipelines.publication_context_stage import prepare_publication_context
from services.telegraph_pages import combined_study_reflection_enabled
from pipelines.telegraph_nav import add_navigation
from pipelines.telegraph_repair_stage import repair_enabled, repair_pages
from pipelines.telegraph_render import RenderRequest, render_pages
from pipelines.thumbnail_stage import load_thumbnail
from core.url_utils import get_youtube_video_url
from core.progress import set_progress
from pipelines.audio_failure_stage import fail_audio_too_large
from pipelines.audio_stage import (
    AudioTooLarge,
    prepare_audio,
)
from pipelines.resource_cleanup_stage import cleanup_pipeline_resources
from pipelines.media_guards import (
    DEFAULT_MAX_DURATION_SEC,
    disk_skip,
    duration_skip,
    env_int,
    first_entry,
    info_timeout_seconds,
    live_skip,
    lowest_free_gb,
    parse_info_stdout,
    temp_dir,
)
from pipelines.livedub_title import (
    # Compatibility re-exports; new code should import livedub_title directly.
    _LIVEDUB_TITLE_CACHE as _LIVEDUB_TITLE_CACHE,
    _LIVEDUB_TITLE_CACHE_MAX as _LIVEDUB_TITLE_CACHE_MAX,
    _clean_livedub_meta_text as _clean_livedub_meta_text,
    _replace_known_author_names as _replace_known_author_names,
    _split_title_author_line as _split_title_author_line,
    _title_part_has_cyrillic as _title_part_has_cyrillic,
    _split_livedub_title_author as _split_livedub_title_author,
    _fallback_livedub_ru_title as _fallback_livedub_ru_title,
    _translate_livedub_title_for_caption as _translate_livedub_title_for_caption_impl,
    _format_livedub_title_line as _format_livedub_title_line,
)

import asyncio
import contextlib
import os  # FIX #11
import logging
import subprocess  # FIX #11
from pathlib import Path

logger = logging.getLogger(__name__)


async def _translate_livedub_title_for_caption(
    full_title: str,
    parsed_title: str,
    parsed_performer: str,
    channel_name: str,
    analysis_title: str = "",
    analysis_author: str = "",
) -> tuple[str, str]:
    """Keep the historical main-module Gemini monkeypatch seam intact."""
    return await _translate_livedub_title_for_caption_impl(
        full_title,
        parsed_title,
        parsed_performer,
        channel_name,
        analysis_title,
        analysis_author,
        _gemini_clients=GEMINI_CLIENTS,
    )


def _sponsorblock_args() -> list:
    """SponsorBlock-вырезка для mp3 (opt-in, default OFF).

    ВАЖНО: вырезка сегментов СДВИГАЕТ таймлайн mp3 относительно YouTube —
    ссылки ⏱ в caption после вырезанного куска будут опережать видео на
    длину выреза. Таймкоды конспекта при этом ТОЧНЫ для mp3 (Gemini
    анализирует уже обрезанный файл). Для проповедей спонсор-вставки
    редки; включать осознанно: SPONSORBLOCK_REMOVE=sponsor,selfpromo
    Категории: sponsor, selfpromo, interaction, intro, outro, preview,
    filler, music_offtopic. Пусто/не задано = выключено.
    """
    cats = os.getenv("SPONSORBLOCK_REMOVE", "").strip()
    if not cats:
        return []
    _allowed = {
        "sponsor",
        "selfpromo",
        "interaction",
        "intro",
        "outro",
        "preview",
        "filler",
        "music_offtopic",
        "all",
        "default",
    }
    parts = [c.strip() for c in cats.split(",") if c.strip()]
    if not all(c.lstrip("-") in _allowed for c in parts):
        logger.warning("SPONSORBLOCK_REMOVE: недопустимые категории %r — игнорирую", cats)
        return []
    return ["--sponsorblock-remove", ",".join(parts)]


async def _ytdlp_info_inprocess(url: str, timeout: int):
    """AUDIT R31 (живой баг: `python.exe Application Error 0xc0000142` при
    спавне `sys.executable -m yt_dlp` из бота — при этом `python -m yt_dlp`
    из чистого терминала работает). Причина: дочерний python.exe наследует
    PATH процесса-бота, где какой-то DLL-каталог (CUDA/cuDNN/ffmpeg,
    добавленный лаунчером) перебивает системную DLL и ломает загрузчик
    нового процесса. Лечение: берём метаданные видео БЕЗ спавна — через
    yt-dlp Python API прямо в процессе бота (как pipelines/playlist.py),
    новый процесс не создаётся и 0xc0000142 невозможен.

    Возвращает info-dict или None (тогда caller откатывается на subprocess).
    """
    try:
        import yt_dlp
        from services.ffmpeg import YTDLP_BASE_ARGS
    except Exception:
        return None

    # AUDIT R31b (живой лог 2026-07-10: in-process ВСЕГДА падал на YouTube с
    # «Only images available» → откат на subprocess): прежняя версия жёстко
    # просила только web-клиента и НЕ передавала js-runtime, поэтому YouTube-n-
    # challenge не решался. Правильно — точный паритет с рабочим subprocess:
    # берём те же флаги (cookies, proxy, --js-runtimes, --remote-components,
    # --format-sort) и превращаем их в ydl_opts через parse_options. На любой
    # сбой возвращаем None → безопасный откат на subprocess (как и раньше).
    try:
        _flags = list(YTDLP_BASE_ARGS)
        for _i, _tok in enumerate(_flags):
            if _tok in ("yt_dlp", "yt-dlp"):
                _flags = _flags[_i + 1 :]
                break
        _parsed = yt_dlp.parse_options(_flags + ["--no-playlist", "--quiet"])
        opts = getattr(_parsed, "ydl_opts", None)
        if opts is None and isinstance(_parsed, (tuple, list)):
            opts = _parsed[-1]  # старые версии yt-dlp: 4-кортеж
        if not isinstance(opts, dict):
            return None
        opts.setdefault("socket_timeout", 30)
        opts["noplaylist"] = True
    except Exception as e:
        logger.info("yt-dlp in-process: parse_options не сработал (%s) — subprocess", str(e)[:150])
        return None

    def _run():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info, deadline_exceeded = await await_owned_with_soft_timeout(
            asyncio.to_thread(_run),
            timeout=timeout,
        )
        if deadline_exceeded:
            logger.warning(
                "yt-dlp in-process превысил мягкий лимит %ss, "
                "но завершился; использую поздний результат без "
                "параллельного subprocess-fallback",
                timeout,
            )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.info(
            "yt-dlp in-process info не удался (%s) — откат на subprocess",
            str(e)[:200],
        )
        return None

    # Если пришёл плейлист-подобный ответ — берём первую реальную запись.
    return first_entry(info)


async def process_single_video(
    url, update, status_msg=None, progress_prefix="", context=None, silent_errors: bool = False
):
    url = get_youtube_video_url(url)
    thumb_buffer = None
    _pp = progress_prefix  # short alias for progress calls
    used_audio_part = None  # инициализируем здесь — finally обращается к ним безусловно
    used_client = None  # если исключение до их присвоения внутри try — UnboundLocalError
    try:
        if status_msg:
            await set_progress(status_msg, 1, prefix=_pp)
        else:
            status_msg = await update.message.reply_text("⏳ Обрабатываю...")
            await set_progress(status_msg, 1, prefix=_pp)

        info_cmd = YTDLP_BASE_ARGS + [
            "--dump-json",
            "--no-playlist",
            url,
        ]
        _info_timeout = info_timeout_seconds()

        # AUDIT R31: сначала пробуем yt-dlp В ПРОЦЕССЕ (без спавна дочернего
        # python.exe) — это обходит краш 0xc0000142 на машинах, где PATH
        # бота ломает загрузчик нового процесса. Только если не вышло —
        # откат на прежний subprocess-путь (поведение сохранено).
        info_dict = await _ytdlp_info_inprocess(url, _info_timeout)

        if info_dict is None:
            try:
                info_proc = await run_cancellable_process(info_cmd, timeout=_info_timeout, text=True)
            except subprocess.TimeoutExpired as _ytdlp_timeout:
                logger.warning("yt-dlp --dump-json timeout after %ss: %s", _info_timeout, str(_ytdlp_timeout)[:300])
                raise Exception(
                    "yt-dlp не успел получить метаданные YouTube за "
                    f"{_info_timeout}с. Проверь proxy для yt-dlp: YTDLP_PROXY_URL "
                    "или TELEGRAM_PROXY_URL (для v2rayN mixed обычно http://127.0.0.1:10808)."
                ) from None
            if info_proc.returncode != 0:
                raise Exception(info_proc.stderr[-500:] if info_proc.stderr else "yt-dlp info error")
            info_dict = parse_info_stdout(info_proc.stdout)
        if not info_dict:
            raise Exception("Не удалось получить метаданные видео")

        # AUDIT M10: отбивка live-stream — yt-dlp начнёт качать бесконечный поток
        _skip = live_skip(info_dict)
        if _skip:
            if not silent_errors:
                await update.message.reply_text(_skip.message)
            logger.info("Пропуск %s: %s (%s)", _skip.reason, url, _skip.details)
            return False

        # AUDIT M10: ограничение по длительности — Gemini обрежет конспект для слишком длинных,
        # пользователь получит обрезанный результат при потраченной квоте.
        _max_dur = env_int("MAX_VIDEO_DURATION_SEC", DEFAULT_MAX_DURATION_SEC, minimum=60)  # дефолт 3 часа
        _video_duration_meta = int(info_dict.get("duration") or 0)
        _skip = duration_skip(_video_duration_meta, _max_dur)
        if _skip:
            if not silent_errors:
                await update.message.reply_text(_skip.message)
            logger.info("Пропуск %s: %s (%s)", _skip.reason, url, _skip.details)
            return False

        # AUDIT: проверка свободного места на диске перед скачиванием
        # ENG-режимы пишут видео в %TEMP% (ld_work) — он может быть на другом
        # диске; берём минимум из двух. Недоступный путь не блокирует обработку.
        _free_gb = lowest_free_gb(DOWNLOAD_DIR, temp_dir())
        _skip = disk_skip(_free_gb)
        if _skip:
            if not silent_errors:
                await update.message.reply_text(_skip.message)
            logger.warning("Пропуск: мало места на диске (%s)", _skip.details)
            return False

        full_title = info_dict.get("title", "audio")
        channel_name = info_dict.get("uploader", info_dict.get("channel", ""))
        source_lang = str(info_dict.get("language") or "").lower()
        logger.info(f"YouTube channel_name: '{channel_name}' (lang: {source_lang})")
        duration = int(info_dict.get("duration") or 0)
        media_id = info_dict.get("id", "media")

        # --- LIVEDUB: проверяем режим пользователя ---
        user_id = update.effective_user.id if (update.effective_user and update.effective_user.id) else None
        user_mode = "rus"
        if user_id:
            try:
                with _db_conn() as conn:
                    row = conn.execute(
                        "SELECT value FROM bot_settings WHERE key = ?", (f"user_mode_{user_id}",)
                    ).fetchone()
                if row and row[0] in ("eng", "eng_fast", "eng_fast_qa"):
                    user_mode = row[0]
            except Exception:
                logger.warning("Failed to read user mode; using default RUS mode", exc_info=True)

        live_dub_task = None
        _livedub_qa_enabled = False
        _livedub_cached_file_id = ""
        if user_mode in ("eng", "eng_fast", "eng_fast_qa"):
            # FEATURE 2026-06-10: реюз file_id — если этот перевод уже отправлялся,
            # Telegram отдаст его мгновенно, без vot-cli и заливки сотен МБ.
            try:
                _ld_cached = await adb_get(media_id)
                _ld_version = (_ld_cached or {}).get("livedub_file_id_version") or ""
                _ld_expected_version = current_livedub_file_id_cache_version()
                if _ld_version == _ld_expected_version:
                    _livedub_cached_file_id = (_ld_cached or {}).get("livedub_file_id") or ""
                elif (_ld_cached or {}).get("livedub_file_id"):
                    logger.info(
                        "[LiveDub] кэш file_id устарел (%s != %s) — пересобираю перевод",
                        _ld_version or "legacy",
                        _ld_expected_version,
                    )
            except Exception:
                _livedub_cached_file_id = ""
        if _livedub_cached_file_id and user_mode in ("eng", "eng_fast", "eng_fast_qa"):
            logger.info(f"[LiveDub] кэш file_id найден — повторная отправка мгновенно ({media_id})")
        elif user_mode in ("eng", "eng_fast", "eng_fast_qa"):
            # FIX 2026-06-10 fail-fast: без vot-cli юзер раньше узнавал о
            # проблеме через минуты (или никогда в Quick). Проверяем сразу.
            from services.yandex_live_dub import _check_vot_cli, _ensure_vot_helper
            from services.eng_subtitles import create_gemini_subtitles, merge_subtitles, download_original_video
            import tempfile

            try:
                # Новый протокол (node + vot_helper) теперь основной. Старый
                # vot-cli-live больше не обязателен, если helper доступен.
                _helper_ok = False
                try:
                    _helper_ok = bool(await asyncio.get_running_loop().run_in_executor(None, _ensure_vot_helper))
                except Exception as _helper_err:
                    logger.warning(f"[LiveDub] helper нового протокола недоступен: {_helper_err}")
                if not _helper_ok:
                    _check_vot_cli()
            except RuntimeError as _vot_err:
                logger.warning(f"[LiveDub] нет клиента VOT: {_vot_err}")
                if not silent_errors:
                    with contextlib.suppress(Exception):
                        await update.message.reply_text(
                            "⚠️ Режим ENG: нет клиента для Яндекс LiveDub.\n"
                            "Установите Node.js (helper поставит @vot.js/node сам) "
                            "или старый резерв: npm install -g vot-cli-live\n"
                            + ("Продолжаю обычный анализ без перевода." if user_mode == "eng" else "")
                        )
                if user_mode in ("eng_fast", "eng_fast_qa"):
                    cleanup_files(media_id)
                    return False
                user_mode = "rus"  # ENG Full деградирует до обычного анализа
        if user_mode in ("eng", "eng_fast", "eng_fast_qa") and live_dub_task is None and not _livedub_cached_file_id:
            ld_work = Path(tempfile.gettempdir()) / f"livedub_{media_id}"
            ld_work.mkdir(exist_ok=True)

            # ENG Quick: только перевод — без субтитров и без QA (экономим CPU и квоту)
            eng_subs_enabled = (user_mode == "eng") and await asettings_get("eng_subtitles")
            _livedub_qa_enabled = (user_mode == "eng") and await asettings_get("livedub_qa")
            _livedub_quick_qa_enabled = user_mode == "eng_fast_qa"
            _livedub_pro_mix = await asettings_get("livedub_pro_mix")
            _livedub_autofix = (_livedub_qa_enabled or _livedub_quick_qa_enabled) and await asettings_get(
                "livedub_autofix"
            )
            try:
                from services.livedub_info import livedub_info_enabled

                _livedub_info_enabled = (user_mode in ("eng_fast", "eng_fast_qa")) and livedub_info_enabled()
            except Exception:
                _livedub_info_enabled = False

            async def _run_livedub_bg(video_url, workdir, source_lang=""):
                # Keep local service imports as historical monkeypatch seams.
                return await run_livedub_background(
                    video_url,
                    workdir,
                    source_lang=source_lang,
                    duration=duration,
                    subtitles_enabled=eng_subs_enabled,
                    translation_subtitles_enabled=(
                        _livedub_qa_enabled or _livedub_info_enabled or _livedub_quick_qa_enabled
                    ),
                    pro_mix=_livedub_pro_mix,
                    create_subtitles=create_gemini_subtitles,
                    make_video=make_livedub_video,
                    wait_video=wait_for_livedub_video,
                    merge_subtitles=merge_subtitles,
                    download_original=download_original_video,
                )

            live_dub_task = asyncio.create_task(_run_livedub_bg(url, ld_work, source_lang=source_lang))
        # --- END LIVEDUB ---

        _livedub_failure_notice_sent = False
        _livedub_result_sent = False
        _livedub_video_for_downstream = None

        from services.livedub_delivery_coordinator import create_source_audio_deferral

        _source_audio_delivery = create_source_audio_deferral(
            bot=context.bot if context else None,
            chat_id=update.effective_chat.id if context else None,
            reply_to=update.message.message_id if context else None,
            enabled=bool(context and user_mode == "eng" and (_livedub_cached_file_id or live_dub_task)),
        )

        async def _send_livedub_result() -> bool:
            """Отправляет готовый LIVEDUB-перевод пользователю.

            Возвращает True, если пользователю что-то отправлено (видео или
            внятное сообщение); False — если перевод тихо не состоялся.
            Общий хелпер для обеих веток (кэш-hit и полная обработка).
            """
            nonlocal _livedub_failure_notice_sent, _livedub_result_sent, _livedub_video_for_downstream
            if _livedub_result_sent:
                return True
            if not context:
                return False
            if not (_livedub_cached_file_id or live_dub_task):
                return False

            async def _notify_full_livedub_failure(reason: str = "") -> bool:
                """ENG Full не должен молча терять обещанный перевод+QA."""
                nonlocal _livedub_failure_notice_sent, _livedub_result_sent
                if user_mode != "eng" or _livedub_failure_notice_sent:
                    return False
                _livedub_failure_notice_sent = True
                _sent = await send_full_livedub_failure_notice(
                    bot=context.bot,
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    reason=reason,
                    flush_source_audio=lambda: _source_audio_delivery.flush("LiveDub unavailable"),
                    escape=html_mod.escape,
                )
                if _sent:
                    _livedub_result_sent = True
                return _sent

            from services.livedub_publication import format_video_caption

            (
                _publication_card,
                _livedub_title_line,
                _livedub_title_html,
            ) = await prepare_livedub_publication(
                ai_data=lambda: ai_data,
                cached_ai_data=lambda: c_ai,
                title=title,
                performer=performer,
                full_title=full_title,
                source_url=url,
                html_module=html_mod,
            )

            # Мгновенная повторная отправка по кэшированному file_id
            if _livedub_cached_file_id and context:
                _cached_internal_caption = (
                    f"<b>{_livedub_title_html}</b>\n🎬 Живые голоса Яндекса"
                    if _livedub_title_html
                    else "🎬 Живые голоса Яндекса"
                )
                await deliver_cached_livedub(
                    context.bot,
                    chat_id=update.effective_chat.id,
                    reply_to=update.message.message_id,
                    video_file_id=_livedub_cached_file_id,
                    caption=format_video_caption(_publication_card, _cached_internal_caption),
                    publication_card=_publication_card,
                    media_id=media_id,
                    flush_source_audio=_source_audio_delivery.flush,
                    discard_source_audio=_source_audio_delivery.discard,
                )
                _livedub_result_sent = True
                return True
            if not live_dub_task:
                return False
            try:
                # 1800с: vot-cli теперь ретраит (Яндекс готовит длинный перевод
                # минутами) — старый потолок 600с отменял задачу раньше ретраев
                livedub_result = await asyncio.wait_for(live_dub_task, timeout=1800)
                if not livedub_result:
                    return await _notify_full_livedub_failure()
                livedub_path, is_fallback, has_subs = livedub_result
                if not (livedub_path and livedub_path.exists()):
                    _reason = ""
                    try:
                        if "ld_work" in locals() and (ld_work / ".auth_required").exists():
                            _reason = "Яндекс потребовал авторизацию VOT_API_TOKEN/YANDEX_OAUTH_TOKEN."
                        elif "ld_work" in locals() and (ld_work / ".not_available").exists():
                            _reason = "Яндекс/VOT вернул Translation not available для этого видео."
                        elif "ld_work" in locals() and (ld_work / ".livedub_failed").exists():
                            _reason = "Внутренняя ошибка сборки LiveDub; подробности есть в логах."
                    except OSError:
                        pass
                    return await _notify_full_livedub_failure(_reason)

                _quick_qa_report_html = ""
                _quick_qa_skip_note = ""
                # ── ENG Quick QA: лёгкая проверка коротких роликов до отправки ──
                if user_mode == "eng_fast_qa" and not is_fallback:
                    (
                        livedub_path,
                        _quick_qa_report_html,
                        _quick_qa_skip_note,
                    ) = await run_livedub_quick_qa(
                        video_path=livedub_path,
                        workdir=ld_work if "ld_work" in locals() else None,
                        duration=duration,
                        video_url=url,
                        bot=context.bot,
                        chat_id=update.effective_chat.id,
                        reply_to=update.message.message_id,
                    )

                # Telegram/локальный Bot API берёт basename файла. Без этого
                # пользователь скачивает десятки одинаковых pro_dub.mp4.
                try:
                    from services.livedub_mix import prepare_livedub_send_path

                    livedub_path = prepare_livedub_send_path(
                        livedub_path,
                        _livedub_title_line,
                        fallback=media_id,
                    )
                except Exception as _name_err:
                    logger.info("[LiveDub] filename prepare skip: %s", str(_name_err)[:120])

                # ── ENG Full: смысловая QA + авто-приглушение ДО отправки ──
                # Раньше видео уходило СРАЗУ, а «приглушение звука» в местах
                # искажения приходило отдельным «🩹»-роликом ПОСЛЕ — основной
                # ролик у пользователя оставался с ошибкой (правка «на словах»).
                # Теперь проверяем и чиним ДО отправки: единственный присланный
                # ролик уже исправлен. Конспект/анализ опубликованы раньше и
                # отдельно — публикация не блокируется, ждёт только сам дубляж
                # (ради чего QA и включают).
                _qa_result_full = None
                _autofix_note = ""
                if _livedub_qa_enabled and not is_fallback and livedub_path and livedub_path.exists():
                    (
                        livedub_path,
                        _qa_result_full,
                        _autofix_note,
                    ) = await run_livedub_full_qa(
                        video_path=livedub_path,
                        workdir=ld_work if "ld_work" in locals() else None,
                        download_dir=DOWNLOAD_DIR,
                        media_id=media_id,
                        title_line=_livedub_title_line,
                        duration=duration,
                        autofix=_livedub_autofix,
                        audio_part=used_audio_part if "used_audio_part" in locals() else None,
                        client=used_client if "used_client" in locals() else None,
                        bot=context.bot,
                        chat_id=update.effective_chat.id,
                        reply_to=update.message.message_id,
                        get_cached=adb_get,
                        validate_cache=is_cache_valid,
                        max_file_size_mb=get_max_file_size_mb,
                    )

                if not is_fallback and livedub_path and livedub_path.exists():
                    _livedub_video_for_downstream = Path(livedub_path)

                # ── Техническая проверка целостности (всегда, дёшево) ──
                _tech_warnings = await get_livedub_technical_warnings(
                    livedub_path, expected_duration=duration, is_fallback=is_fallback
                )

                if await reject_oversized_livedub_video(
                    livedub_path,
                    bot=context.bot,
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    max_file_size_mb=get_max_file_size_mb,
                    flush_source_audio=lambda: _source_audio_delivery.flush("LiveDub video exceeds Telegram limit"),
                ):
                    _livedub_result_sent = True
                    return True
                _title_prefix = f"<b>{_livedub_title_html}</b>\n" if _livedub_title_html else ""
                if is_fallback:
                    caption = (
                        _title_prefix
                        + "⚠️ Живой перевод Яндекса недоступен/сломался.\nОтправляю резерв: оригинальное видео"
                        + (" + русские субтитры." if has_subs else ".")
                    )
                else:
                    # round 38: если сработал tts-fallback — честный caption
                    _tts_marker = False
                    with contextlib.suppress(OSError):
                        _tts_marker = "ld_work" in locals() and (ld_work / ".voice_style_tts").exists()
                    _voice_label = "🎬 Перевод Яндекса (обычные голоса)" if _tts_marker else "🎬 Живые голоса Яндекса"
                    caption = (
                        _title_prefix
                        + _voice_label
                        + ("\n💬 Русские субтитры сделаны независимо через Whisper + Gemini" if has_subs else "")
                    )
                    if _tech_warnings:
                        caption += "\n⚠️ " + "; ".join(_tech_warnings)[:500]
                    # ENG Full: если QA нашёл major-искажения и мы приглушили дубляж —
                    # честно помечаем это прямо на присланном (уже исправленном) ролике.
                    if _autofix_note:
                        caption += "\n" + _autofix_note
                    # Режим называется «Quick QA» — если проверка не запускалась,
                    # юзер должен это видеть, а не думать, что перевод проверен.
                    if user_mode == "eng_fast_qa" and _quick_qa_skip_note:
                        caption += f"\n🔍 {_quick_qa_skip_note}"
                if not is_fallback:
                    caption = format_video_caption(_publication_card, caption)

                # Path вместо file handle: с локальным Bot API (local_mode=True)
                # PTB передаёт file:// URI — сервер читает файл с диска напрямую,
                # без HTTP-загрузки сотен МБ (это причина TimedOut на больших файлах).
                # В облачном режиме PTB сам откроет путь и зальёт как раньше.
                # width/height/duration/thumbnail: для видео >10 МБ Telegram сам
                # их НЕ определяет — без них нет превью, длительности и стриминга.
                _v_meta, _v_thumb, _v_cover = await prepare_livedub_video_assets(
                    livedub_path, media_id=media_id, thumbs_dir=THUMBS_DIR
                )
                _send_kwargs = dict(
                    chat_id=update.effective_chat.id,
                    video=livedub_path,
                    caption=caption,
                    parse_mode="HTML",
                    width=_v_meta.get("width"),
                    height=_v_meta.get("height"),
                    duration=_v_meta.get("duration"),
                    thumbnail=_v_thumb,
                    reply_to_message_id=update.message.message_id,
                    supports_streaming=True,
                    write_timeout=600,
                    read_timeout=600,
                    connect_timeout=60,
                )
                _sent_msg = await send_livedub_video_with_cover(
                    bot=context.bot,
                    chat_id=update.effective_chat.id,
                    cover=_v_cover,
                    send_kwargs=_send_kwargs,
                )
                # Companion delivery is an explicit transaction. Only a complete
                # video+MP3 result enters the fast-resend video cache.
                _video_file_id = str(getattr(getattr(_sent_msg, "video", None), "file_id", "") or "").strip()
                if is_fallback:
                    await _source_audio_delivery.flush("LiveDub fell back to original video")
                else:
                    from services.livedub_delivery_coordinator import deliver_new_companions

                    _companions_ok = False
                    try:
                        _companions_ok = await deliver_new_companions(
                            context.bot,
                            chat_id=update.effective_chat.id,
                            video_path=Path(livedub_path),
                            publication_card=_publication_card,
                            reply_to=update.message.message_id,
                            thumbnail=_v_thumb,
                            video_file_id=_video_file_id,
                        )
                    except Exception as _companion_err:
                        logger.exception(
                            "[LiveDub] companion transaction failed: %s",
                            str(_companion_err)[:180],
                        )
                        await _source_audio_delivery.flush("LiveDub companion transaction failed")
                        with contextlib.suppress(Exception):
                            await context.bot.send_message(
                                chat_id=update.effective_chat.id,
                                text=(
                                    "⚠️ Видео с переводом отправлено, но полный проверенный "
                                    "MP3-комплект сформировать не удалось. Видео не сохранено "
                                    "в быстрый кэш как полный комплект."
                                ),
                                reply_to_message_id=update.message.message_id,
                            )

                    if _companions_ok:
                        _source_audio_delivery.discard("complete LiveDub video+MP3 set delivered")
                        if _video_file_id:
                            try:
                                from core.database import adb_set_livedub_file_id

                                await adb_set_livedub_file_id(media_id, _video_file_id)
                                logger.info("[LiveDub] complete pair file_id cached (%s)", media_id)
                            except Exception as _fid_save_err:
                                logger.warning("[LiveDub] complete pair cache save failed: %s", _fid_save_err)
                                try:
                                    import services.livedub_audio_companion as _companion_cache

                                    _companion_cache._cache_drop(_video_file_id)
                                except Exception:
                                    logger.debug("[LiveDub] companion cache invalidation failed", exc_info=True)

                # В Quick QA проверка нужна ДО отправки (чтобы успеть авто-вырезать
                # major), но отчёт показываем ПОСЛЕ видео: главный результат режима
                # — готовый перевод, а не сначала диагностическая простыня.
                if _quick_qa_report_html:
                    try:
                        # 2026-06-11: При использовании Local Bot API (local_mode=True)
                        # send_video возвращает управление мгновенно. Чтобы отчёт
                        # гарантированно был в чате ПОСЛЕ видео (как и задумано),
                        # даём Telegram время «продышаться».
                        await asyncio.sleep(1.5)
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=_quick_qa_report_html,
                            parse_mode="HTML",
                            reply_to_message_id=update.message.message_id,
                            disable_web_page_preview=True,
                        )
                    except Exception as _qrep_err:
                        logger.info("[LiveDubQuickQA] report send failed: %s", str(_qrep_err)[:160])

                # ── Отчёт QA (ENG Full): всегда объясняем найденные искажения ──
                # Проверка и авто-приглушение уже выполнены ДО отправки видео
                # (блок выше): присланный ролик — уже исправленный. Здесь только
                # показываем отчёт по искажениям (после видео, как и раньше).
                if _qa_result_full:
                    try:
                        from services.livedub_qa import format_qa_report

                        await asyncio.sleep(1.0)
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=format_qa_report(_qa_result_full, video_url=url),
                            parse_mode="HTML",
                            reply_to_message_id=update.message.message_id,
                            disable_web_page_preview=True,
                        )
                    except Exception as _qrep_err:
                        logger.info("[LiveDubQA] отчёт не отправлен: %s", str(_qrep_err)[:160])
                _livedub_result_sent = True
                return True
            except asyncio.TimeoutError:
                await send_livedub_timeout_notice(
                    bot=context.bot,
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    flush_source_audio=lambda: _source_audio_delivery.flush("LiveDub timeout"),
                )
                _livedub_result_sent = True
                return True
            except Exception as e:
                await notify_livedub_send_failure(
                    bot=context.bot,
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    error=e,
                    max_file_size_mb=get_max_file_size_mb,
                    flush_source_audio=lambda: _source_audio_delivery.flush("LiveDub send/generation failure"),
                    escape=html_mod.escape,
                )
                return False

        performer, title = parse_title(full_title, channel_name)

        # ── ENG Quick: только перевод, весь анализ пропускаем ────────────
        if user_mode in ("eng_fast", "eng_fast_qa"):
            return await finish_livedub_quick(
                update=update,
                status_msg=status_msg,
                title=title,
                send_result=_send_livedub_result,
                silent_errors=silent_errors,
                workdir=ld_work if "ld_work" in locals() else None,
                media_id=media_id,
                cleanup=cleanup_files,
            )

        # Стадия кэша объединяет TTL/версии из БД и защиту от dirty/corrupt
        # метаданных. Любой miss идёт по обычному fresh-пути ниже.
        cache_decision = await load_cache(media_id)
        cached = cache_decision.cached or {}

        if cache_decision.hit:
            c_ai = cache_decision.ai_data or {}
            logger.info(f"Кэш hit: {media_id} — отдаём без Gemini")
            await set_progress(status_msg, 6, prefix=_pp)
            # Telegraph-ссылки читаются в прежней точке, до thumbnail/audio;
            # required-key policy и скрытие дубля Терминов не меняются.
            c_links = cached_page_links(cached)
            # Обложка — общая необязательная стадия для fresh и cache-hit.
            thumb_buffer = await load_thumbnail(
                media_id=media_id,
                url=url,
                thumbs_dir=THUMBS_DIR,
                cache_hit=True,
            )
            # Скачиваем аудио. Стадия вынесена в pipelines/audio_stage.py:
            # реюз файла, нормализация, сжатие под лимит — одна реализация на
            # два места (здесь и в основном пути), а не две разошедшиеся копии.
            try:
                _audio = await prepare_audio(
                    media_id=media_id,
                    url=url,
                    duration=duration,
                    download_dir=DOWNLOAD_DIR,
                    max_size_mb=get_max_file_size_mb(),
                    timeout=1800,
                    sponsorblock_args=_sponsorblock_args(),
                    log_prefix="Кэш аудио",
                    missing_message="Не удалось скачать аудио",
                )
            except AudioTooLarge as _too_big:
                return await fail_audio_too_large(
                    _too_big,
                    update,
                    _send_livedub_result,
                    media_id,
                    cleanup_files,
                    cache_hit=True,
                )
            mp3_path = _audio.path
            file_size_mb = _audio.size_mb
            bitrate = _audio.bitrate
            logger.info(f"Кэш аудио: {mp3_path.name} = {file_size_mb:.1f} MB")
            # Alt-links и подпись — одна покрытая cache-hit стадия.
            # Сохранённые ссылки не вызывают внешний поиск; обрезка остаётся
            # в общей pipelines/caption_stage.py, как и на fresh-пути.
            # Cache-hit presentation/delivery используют общие значения;
            # thumbnail остаётся во владении внешнего finally.
            return await deliver_cache_hit(
                CacheHitDeliveryRequest(
                    links=c_links,
                    ai_data=c_ai,
                    full_title=full_title,
                    channel_name=channel_name,
                    title=title,
                    performer=performer,
                    duration=duration,
                    file_size_mb=file_size_mb,
                    bitrate=bitrate,
                    source_url=url,
                    cached_rutube_url=cached.get("rutube_url", "") or "",
                    cached_vk_url=cached.get("vk_url", "") or "",
                    update=update,
                    context=context,
                    audio_delivery=_source_audio_delivery,
                    mp3_path=mp3_path,
                    media_id=media_id,
                    download_dir=DOWNLOAD_DIR,
                    thumbs_dir=THUMBS_DIR,
                    thumbnail=thumb_buffer,
                    cached_file_id=(cached or {}).get("audio_file_id") or "",
                    send_livedub=_send_livedub_result,
                    cleanup=cleanup_files,
                ),
                prepare=prepare_cached_presentation,
                deliver=deliver_cached_result,
            )

        await set_progress(
            status_msg,
            2,
            {"title": f"📝 {title}", "author": f"👤 {performer}  •  {duration // 60}:{duration % 60:02d}"},
            prefix=_pp,
        )
        thumb_buffer = await load_thumbnail(
            media_id=media_id,
            url=url,
            thumbs_dir=THUMBS_DIR,
            cache_hit=False,
        )

        await set_progress(
            status_msg,
            3,
            {
                "title": f"📝 {title}",
                "author": f"👤 {performer}  •  {duration // 60}:{duration % 60:02d}",
                "fmt": "🎵 128 kbps  •  MP3",
            },
            prefix=_pp,
        )

        # Проверяем кэш MP3 — если файл уже есть, скачивание пропускаем
        async def _squeeze_notice(current_mb: float) -> None:
            await set_progress(
                status_msg,
                3,
                {"title": f"📝 {title}", "info": f"⚙️ Файл {current_mb:.1f} МБ — пересжимаю в 64 kbps..."},
                prefix=_pp,
            )

        try:
            _audio = await prepare_audio(
                media_id=media_id,
                url=url,
                duration=duration,
                download_dir=DOWNLOAD_DIR,
                max_size_mb=get_max_file_size_mb(),
                timeout=600,
                sponsorblock_args=_sponsorblock_args(),
                extra_args=("--format", "bestaudio/best"),
                log_prefix="MP3",
                on_squeeze=_squeeze_notice,
            )
        except AudioTooLarge as _too_big:
            return await fail_audio_too_large(
                _too_big,
                update,
                _send_livedub_result,
                media_id,
                cleanup_files,
                cache_hit=False,
            )
        mp3_path = _audio.path
        file_size_mb = _audio.size_mb
        bitrate = _audio.bitrate

        # Стадия анализа вынесена в pipelines/analysis_stage.py: прогресс,
        # Gemini, аудит темы заголовка, ранние alt-links, конспект и честная
        # деградация. Порядок шагов («ссылки до конспекта», «заголовок до
        # публикации») теперь закреплён тестами, а не комментариями.
        _analysis = await run_analysis(
            AnalysisRequest(
                mp3_path=mp3_path,
                title=title,
                performer=performer,
                duration=duration,
                url=url,
                full_title=full_title,
                channel_name=channel_name,
                media_id=media_id,
                download_dir=DOWNLOAD_DIR,
                bitrate=bitrate,
                source_lang=source_lang,
                progress_prefix=progress_prefix,
                status_msg=status_msg,
            ),
            enabled=bool(GEMINI_CLIENTS),
        )
        ai_data = _analysis.ai_data
        synopsis_outline = _analysis.synopsis_outline
        telegraph_url = _analysis.telegraph_url
        used_audio_part = _analysis.used_audio_part
        used_client = _analysis.used_client
        _degraded_caption_data = _analysis.degraded_caption_data
        _early_alt = _analysis.early_links
        _pre_rutube = _analysis.rutube_url
        _pre_vk = _analysis.vk_url
        # НЕ удаляем audio_part здесь — он нужен для Shorts и Clips.
        # Удаление происходит в блоке finally ниже.

        await set_progress(status_msg, 6, prefix=_pp)
        _publication = await prepare_publication_context(
            ai_data=ai_data,
            degraded=_degraded_caption_data,
            full_title=full_title,
            performer=performer,
            media_id=media_id,
            telegraph_url=telegraph_url,
            settings_get_all=asettings_get_all,
        )
        search_title = _publication.search_title
        tg_author = _publication.tg_author
        _questions = _publication.questions
        _terms_data = _publication.terms_data
        _mat_format = _publication.format_name
        _ts_total = _publication.timestamps_total
        _plan = _publication.plan
        _feat_study_analysis = _plan.study_analysis
        _feat_reflection_application = _plan.reflection_application

        # Стадия рендера вынесена в pipelines/telegraph_render.py: пять фабрик,
        # «компактные» замены, opt-in объединённый режим Study+Reflection и
        # перенос fallback-«Вопросов» из навигации. Сбой одной страницы не
        # отменяет остальные, Study/Reflection идут последовательно (free tier).
        _rendered = await render_pages(
            RenderRequest(
                ai_data=ai_data,
                questions=_questions,
                terms_data=_terms_data,
                plan=_plan,
                search_title=search_title,
                author=tg_author,
                url=url,
                channel_name=channel_name,
                full_title=full_title,
                duration=duration,
                media_id=media_id,
                rutube_url=_pre_rutube,
                vk_url=_pre_vk,
                synopsis_outline=synopsis_outline,
                early_links=_early_alt,
                combined_enabled=combined_study_reflection_enabled(),
            )
        )
        alt_links = _rendered.alt_links
        quotes_tg = _rendered.analytics_url
        questions_tg = _rendered.questions_url
        terms_tg = _rendered.terms_url
        study_analysis_tg = _rendered.study_analysis_url
        reflection_application_tg = _rendered.reflection_url
        _nav_filled = _rendered.as_nav_pages(telegraph_url or "")

        # Навигация вынесена в pipelines/telegraph_nav.py: сборка блока,
        # повторный прогон без дублей, компактный вариант у лимита размера,
        # обход частей по «➡ Дальше» и две попытки при сетевом сбое.
        await add_navigation(_nav_filled)

        # Deterministic post-publish safety repair. This is not a manual
        # operator step and does not call Gemini: after createPage/editPage
        # navigation, fetch the just-created Telegraph pages once, run the
        # current postprocess/audit repair, and edit them if deterministic
        # cleanup changes nodes. Keeps technical pages readable while audit
        # history still records bug classes for generator fixes.
        # Починка после публикации вынесена в pipelines/telegraph_repair_stage.py:
        # разворот цепочки частей, изоляция страниц, структурный отчёт.
        _repair = await repair_pages(
            [telegraph_url, study_analysis_tg, reflection_application_tg, quotes_tg, questions_tg, terms_tg],
            enabled=repair_enabled(),
        )

        # Входные данные подписи вынесены в pipelines/caption_inputs.py:
        # приоритет ссылок, скрытие дубля Терминов, статус публикации, покрытие
        # таймкодами и предупреждение о неполной публикации — чистые функции.
        _caption_inputs = build_caption_inputs(
            ai_data=ai_data,
            degraded=_degraded_caption_data,
            duration=duration,
            format_name=_mat_format,
            timestamps_total=_ts_total,
            timestamp_limit=get_caption_timestamp_limit,
            study=study_analysis_tg,
            reflection=reflection_application_tg,
            analytics=quotes_tg,
            questions=questions_tg,
            terms=terms_tg,
            synopsis=telegraph_url,
            plan_study=_feat_study_analysis,
            plan_reflection=_feat_reflection_application,
            rutube_url=alt_links.get("rutube"),
            vk_url=alt_links.get("vk"),
        )
        _q_link = _caption_inputs.links.quotes
        _ref_link = _caption_inputs.links.questions
        _terms_link = _caption_inputs.links.terms
        rutube_url = _caption_inputs.rutube_url
        vk_url = _caption_inputs.vk_url
        ai_data = _caption_inputs.ai_data
        _pub_status = _caption_inputs.publication
        if ai_data and _pub_status.warning:
            logger.warning(
                "Publication status for %s: status=%s missing=%s",
                media_id,
                _pub_status.status,
                ",".join(_pub_status.missing),
            )

        # Стадия подписи: сборка, обрезка до 1024 видимых символов, метрики.
        # Реализация одна на этот путь и на кэш-хит (pipelines/caption_stage.py).
        _cap_source = CaptionSource(
            performer=performer,
            title=title,
            duration=duration,
            file_size_mb=file_size_mb,
            bitrate=bitrate,
            url=url,
            telegraph_url=telegraph_url or "",
            rutube_url=rutube_url,
            vk_url=vk_url,
            quotes_link=_q_link,
            questions_link=_ref_link,
            terms_link=_terms_link,
            study_link=study_analysis_tg or "",
            reflection_link=reflection_application_tg or "",
            format_name=_mat_format,
        )
        # Fresh caption fit, cache metadata и долговременный архив — одна
        # стадия; при отсутствии анализа сохранение по-прежнему пропускается.
        _fresh_caption = await finalize_fresh_caption(
            source=_cap_source,
            inputs=_caption_inputs,
            publication=_publication,
            rendered=_rendered,
            repair=_repair,
            media_id=media_id,
            duration=duration,
            cache_version=CACHE_VERSION,
            model_name=GEMINI_MODEL,
            prompt_version=get_prompt_fingerprint,
            fit=fit_caption,
            visible_length=visible_length,
            save=adb_save,
            archive=archive_pages,
        )
        caption = _fresh_caption.caption
        full_caption = _fresh_caption.full_caption
        ai_data = _fresh_caption.ai_data

        # Свежий результат отправляется одной стадией: ID3 → MP3 с retry →
        # полный текст → PDF → quiz. Ошибка MP3 остаётся критичной, а сбои
        # необязательных дополнений изолированы в pipelines/delivery_stage.py.
        await deliver_result(
            DeliveryRequest(
                update=update,
                context=context,
                audio_delivery=_source_audio_delivery,
                mp3_path=mp3_path,
                media_id=media_id,
                source_url=url,
                title=title,
                performer=performer,
                duration=duration,
                file_size_mb=file_size_mb,
                caption=caption,
                full_caption=full_caption,
                download_dir=DOWNLOAD_DIR,
                thumbs_dir=THUMBS_DIR,
                ai_data=ai_data,
                thumbnail=thumb_buffer,
                synopsis_url=telegraph_url or "",
                study_url=study_analysis_tg or "",
                reflection_url=reflection_application_tg or "",
                terms_url=terms_tg or "",
                used_audio_part=used_audio_part,
                used_client=used_client,
            )
        )

        # Дополнения идут после основного MP3 в прежнем порядке.
        # Общий candidates-запрос, изоляция optional-ошибок и выбор
        # LiveDub-файла для ENG находятся внутри покрытой стадии.
        await run_extras(
            ExtrasRequest(
                source_url=url,
                media_id=media_id,
                mp3_path=mp3_path,
                title=title,
                performer=performer,
                duration=duration,
                ai_data=ai_data,
                update=update,
                get_livedub_path=lambda: _livedub_video_for_downstream,
                send_livedub=_send_livedub_result,
                rutube_url=rutube_url,
                vk_url=vk_url,
                used_audio_part=used_audio_part,
                used_client=used_client,
                workdir=ld_work if "ld_work" in locals() else None,
                user_mode=user_mode,
                live_dub_pending=bool(live_dub_task and context),
            )
        )

        # --- LIVEDUB: отправка результата (общий хелпер) ---
        await _send_livedub_result()
        if _source_audio_delivery.has_pending:
            await _source_audio_delivery.flush("pipeline completed without a complete LiveDub pair")
        cleanup_files(media_id)

        return True

    except Exception as e:
        # AUDIT M3: silent_errors=True (плейлист) — не спамим пользователю
        # AUDIT M15: проверяем media_id через locals() вместо except NameError,
        # который опасно поглощает OSError от cleanup_files.
        from core.utils import mask_api_key as _mask

        _safe = _mask(str(e))
        logger.error(f"Ошибка: {_safe}", exc_info=True)
        # AUDIT R42: не теряем УЖЕ ГОТОВЫЙ дубляж, если упали на «extras»
        # (PDF/quiz/Shorts) ПОСЛЕ основной отправки, но ДО отправки перевода.
        # Раньше finally просто отменял live_dub_task — обещанный перевод молча
        # пропадал, а пользователь видел лишь «❌ Ошибка». Хелпер идемпотентен и
        # сам awaits задачу до конца, поэтому finally затем пропустит cancel.
        if "_send_livedub_result" in locals():
            try:
                await _send_livedub_result()
            except Exception as _ld_flush_err:
                logger.warning("[LiveDub] flush в except не удался: %s", str(_ld_flush_err)[:160])
        if not silent_errors:
            with contextlib.suppress(Exception):
                await update.message.reply_text(f"❌ Ошибка: {_safe[:200]}")
        if "media_id" in locals():
            try:
                cleanup_files(media_id)
            except Exception as _ce:
                logger.warning(f"cleanup_files после ошибки: {_ce}")
        return False
    finally:
        await cleanup_pipeline_resources(
            live_dub_task=live_dub_task if "live_dub_task" in locals() else None,
            workdir=ld_work if "ld_work" in locals() else None,
            used_audio_part=used_audio_part,
            used_client=used_client,
            thumbnail=thumb_buffer,
        )


# ─── Обработка плейлиста ─────────────────────────────────────
