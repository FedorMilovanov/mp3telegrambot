#!/usr/bin/env python3
"""
Main Pipeline — process_single_video.
Извлечено из bot.py строки 12470–13446.
"""
from services.ffmpeg import YTDLP_BASE_ARGS                   # FIX #23: нужен результат, не функция
from services.async_process import run_cancellable_process
from services.async_worker import (
    await_owned_coroutine,
    await_owned_with_soft_timeout,
)
from core.globals import (
    DOWNLOAD_DIR, THUMBS_DIR, HAS_GEMINI, HAS_PILLOW, DB_PATH,
    GEMINI_CLIENTS, TELEGRAPH_TOKEN,                  # FIX #11
    html_mod,                                          # FIX #11
)
from core.database import (
    _db_conn,
    adb_get, adb_save, asettings_get, asettings_get_all,
    is_cache_valid, db_init,
    GEMINI_MODEL, CACHE_VERSION,    # FIX #11
    get_max_file_size_mb,
    current_livedub_file_id_cache_version,
    get_prompt_fingerprint,                            # FIX #11
)
from core.utils import (
    cleanup_files, is_media_url, parse_title,
    check_rate_limit, update_rate_limit, format_timestamp,  # FIX #11
)
from core.text_utils import (
    normalize_author_name, normalize_title_text,
    normalize_common_typos, title_case_fragment,                    # FIX #11
)
from core.person_names import (
    KNOWN_AUTHOR_RU as _KNOWN_AUTHOR_RU,
    KNOWN_CHANNEL_AUTHOR_RU as _KNOWN_CHANNEL_AUTHOR_RU,
    known_author_from_text as _known_author_from_text,
    known_ru_author_from_text as _known_ru_author_from_text,
    looks_like_author_list as _looks_like_author_list,
)
from converters.md_telegraph import (
    visible_length,                                    # FIX #11
    get_caption_timestamp_limit,                       # FIX #11
)
from services.search import (
    find_alternative_links, _build_search_title,
)
from pipelines.analysis_stage import AnalysisRequest, run_analysis
from pipelines.archive_stage import ArchiveRequest, archive_pages
from pipelines.caption_inputs import build_caption_inputs
from pipelines.caption_stage import CaptionSource, fit_caption
from pipelines.cache_stage import load_cache
from pipelines.cached_presentation_stage import (
    CachedPresentationRequest,
    cached_page_links,
    prepare_cached_presentation,
)
from pipelines.delivery_stage import DeliveryRequest, deliver_cached_result, deliver_result
from pipelines.extras_stage import ExtrasRequest, run_extras
from services.telegraph_pages import combined_study_reflection_enabled
from pipelines.telegraph_nav import add_navigation
from pipelines.telegraph_repair_stage import repair_enabled, repair_pages
from pipelines.telegraph_render import RenderRequest, render_pages
from pipelines.thumbnail_stage import load_thumbnail
from core.url_utils import get_youtube_video_url, get_youtube_timestamp_url
from core.progress import set_progress
from core.publication_status import missing_to_json
from core.bounded_cache import BoundedLRUDict
from pipelines.audio_stage import (
    AudioTooLarge,
    prepare_audio,
)
from pipelines.publication_plan import (
    build_plan,
    has_ai_analysis,
    terms_total as _plan_terms_total,
)
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
from services.mp3_conversion import reencode_mp3_64k_atomic

import asyncio
import json
import os       # FIX #11
import logging
import re         # FIX #11
import shutil     # FIX #11
import subprocess # FIX #11
import uuid
import sqlite3  # LIVEDUB: direct DB read for user mode
from pathlib import Path
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)


# ── LiveDub caption title helpers ─────────────────────────────────

try:
    _LIVEDUB_TITLE_CACHE_MAX = int(
        os.getenv("LIVEDUB_TITLE_CACHE_MAX", "256").strip() or "256"
    )
except ValueError:
    _LIVEDUB_TITLE_CACHE_MAX = 256
_LIVEDUB_TITLE_CACHE: BoundedLRUDict = BoundedLRUDict(
    max_entries=_LIVEDUB_TITLE_CACHE_MAX
)


def _clean_livedub_meta_text(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"(?i)#\s*(shorts?|ytshorts|youtube|sermon|preaching)\b", "", text)
    text = re.sub(r"(?i)\b(shorts?|youtube shorts?)\b", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -—–|:;\t\n\r")
    return text.strip()


def _replace_known_author_names(text: str) -> str:
    out = str(text or "")
    for en, ru in _KNOWN_AUTHOR_RU.items():
        out = re.sub(rf"(?i)\b{re.escape(en)}\b", ru, out)
    out = re.sub(r"\s+(?:and|&)\s+", " & ", out, flags=re.IGNORECASE)
    out = re.sub(r"\s{2,}", " ", out).strip(" -—–|,;")
    return normalize_author_name(out)


def _split_title_author_line(line: str) -> tuple[str, str]:
    """Нормализует одну строку в порядок «Название - Автор».

    YouTube часто даёт author-first: «R.C. Sproul: True ...» или
    «Р. Ч. Спроул - True ...». Для DUB-caption/download filename нам нужен
    стабильный порядок title-first.
    """
    line = _clean_livedub_meta_text(line)
    if not line:
        return "", ""
    for sep in (": ", " - ", " — ", " – ", " | ", " // "):
        if sep not in line:
            continue
        left, right = [x.strip() for x in line.split(sep, 1)]
        if not (left and right):
            continue
        left_known = _known_author_from_text(left) or _known_ru_author_from_text(left)
        right_known = _known_author_from_text(right) or _known_ru_author_from_text(right)
        if left_known:
            return right, _replace_known_author_names(left) or left_known
        if right_known:
            return left, (_replace_known_author_names(right) if _looks_like_author_list(right) else right_known)
        if _looks_like_author_list(left) and not _looks_like_author_list(right):
            return right, _replace_known_author_names(left)
        if _looks_like_author_list(right):
            return left, _replace_known_author_names(right)
    return line, ""


def _title_part_has_cyrillic(line: str) -> bool:
    title_part, _author = _split_title_author_line(line)
    # 2026-06-11: Титл считается «русским», только если в самой части НАЗВАНИЯ
    # есть кириллица. Если кириллица только в имени автора (которое мы
    # подставляем из базы), а название всё ещё английское — нужно переводить.
    target = title_part if title_part else line
    return bool(re.search(r"[А-Яа-яЁё]", target))


def _split_livedub_title_author(full_title: str, parsed_title: str, parsed_performer: str, channel_name: str) -> tuple[str, str]:
    full_title = _clean_livedub_meta_text(full_title)
    parsed_title = _clean_livedub_meta_text(parsed_title)
    parsed_performer = _clean_livedub_meta_text(parsed_performer)
    channel_name = _clean_livedub_meta_text(channel_name)

    title_from_line, author_from_line = _split_title_author_line(full_title)
    if author_from_line:
        return title_from_line, author_from_line

    author = _known_author_from_text(full_title, parsed_performer, channel_name)
    if not author and parsed_performer and not _looks_like_author_list(parsed_title):
        author = parsed_performer

    title = parsed_title or full_title
    if _looks_like_author_list(title) and full_title:
        title = full_title
    return title, author


def _fallback_livedub_ru_title(full_title: str, parsed_title: str, parsed_performer: str, channel_name: str) -> tuple[str, str]:
    raw_title, raw_author = _split_livedub_title_author(full_title, parsed_title, parsed_performer, channel_name)
    title_ru = title_case_fragment(normalize_title_text(_replace_known_author_names(raw_title)) or raw_title)
    author_ru = _replace_known_author_names(raw_author) or _known_author_from_text(full_title, parsed_performer, channel_name)
    if author_ru and author_ru.lower() in {"grace to you", "heartcry", "heartcry missionary society"}:
        author_ru = _known_author_from_text(author_ru) or author_ru
    return title_ru.strip(), author_ru.strip()


async def _translate_livedub_title_for_caption(
    full_title: str,
    parsed_title: str,
    parsed_performer: str,
    channel_name: str,
    analysis_title: str = "",
    analysis_author: str = "",
) -> tuple[str, str]:
    """Return (Russian title, Russian author) for all ENG/LiveDub captions.

    Cost policy:
      • ENG Full/cache-hit: use existing Gemini analysis fields real_title/real_author.
      • ENG Quick: no extra Gemini by default; deterministic fallback from YouTube metadata.
      • Extra Gemini title-only translation is opt-in: LIVEDUB_TITLE_TRANSLATE=1.
    """
    cache_key = (
        str(full_title or ""), str(parsed_title or ""), str(channel_name or ""),
        str(analysis_title or ""), str(analysis_author or ""),
    )
    if cache_key in _LIVEDUB_TITLE_CACHE:
        return _LIVEDUB_TITLE_CACHE[cache_key]

    fallback_title, fallback_author = _fallback_livedub_ru_title(
        full_title, parsed_title, parsed_performer, channel_name
    )

    # Best path: use already-paid analysis result (ENG Full / cache-hit), no extra AI call.
    analysis_title = normalize_common_typos(
        normalize_title_text(analysis_title) or str(analysis_title or "")
    ).strip()
    analysis_author = normalize_author_name(analysis_author).strip()
    if analysis_title or analysis_author:
        result = (analysis_title or fallback_title, analysis_author or fallback_author)
        _LIVEDUB_TITLE_CACHE[cache_key] = result
        return result

    # ENG Quick/default title translation: use the lightweight info pipeline,
    # not the heavy audio-analysis model directly. This keeps the DUB caption
    # Russian while still preferring cheap/free Flash-Lite models.
    if (GEMINI_CLIENTS
            and os.getenv("LIVEDUB_TITLE_TRANSLATE", "1").strip().lower() in {"1", "true", "yes", "on", "always"}):
        try:
            from services.livedub_info import build_livedub_info_card
            base_line = _format_livedub_title_line(fallback_title, fallback_author)
            card = await build_livedub_info_card(base_line, None, source_url="", force=True)
            yt_title = _clean_livedub_meta_text((card or {}).get("youtube_title") or "")
            if yt_title:
                yt_title_norm, yt_author_norm = _split_title_author_line(yt_title)
                yt_line = (
                    f"{yt_title_norm.strip()} - {yt_author_norm.strip()}"
                    if yt_author_norm else yt_title_norm.strip()
                )
                if _title_part_has_cyrillic(yt_line):
                    result = (yt_line, "")
                    _LIVEDUB_TITLE_CACHE[cache_key] = result
                    return result
                logger.info("[LiveDubTitle] light title is not Russian enough — trying main title prompt")
        except Exception as _light_title_err:
            logger.info("[LiveDubTitle] light title translation failed: %s", str(_light_title_err)[:120])

    # If lightweight title translation is disabled/unavailable, deterministic metadata fallback.
    if (not GEMINI_CLIENTS
            or os.getenv("LIVEDUB_TITLE_TRANSLATE", "1").strip().lower() not in {"1", "true", "yes", "on", "always"}):
        _LIVEDUB_TITLE_CACHE[cache_key] = (fallback_title, fallback_author)
        return fallback_title, fallback_author

    prompt = (
        "Переведи название и автора христианского видео на РУССКИЙ язык для публикации в Telegram.\n"
        "Верни СТРОГО JSON: {\"title_ru\":\"...\",\"author_ru\":\"...\"}.\n"
        "ПРАВИЛА:\n"
        "- title_ru: ОБЯЗАТЕЛЬНО на русском языке; естественное название, без #shorts, без слова Shorts, без кавычек;\n"
        "- author_ru: имя автора/спикера на русском, если оно явно есть в названии/канале "
        "или общеизвестно по каналу; иначе пустая строка;\n"
        "- не добавляй отсебятину, не меняй богословский смысл;\n"
        "- ПЕРЕВОДИ ВСЁ на русский, не оставляй английских слов в названии;\n"
        "- известные имена: John MacArthur=Джон МакАртур, Paul Washer=Пол Вошер, "
        "Abner Chou=Абнер Чау, Costi Hinn=Кости Хинн, R.C. Sproul=Р. Ч. Спроул;\n"
        "- Grace to You обычно означает Джон МакАртур; HeartCry часто означает Пол Вошер, "
        "но если в title указан другой спикер — выбери его.\n\n"
        f"title: {full_title}\n"
        f"parsed_title: {parsed_title}\n"
        f"parsed_performer: {parsed_performer}\n"
        f"channel: {channel_name}\n"
    )
    try:
        from core.globals import gemini_generate, make_text_config_smart
        cfg = make_text_config_smart(
            temperature=0.1,
            max_output_tokens=512,
            thinking_level="minimal",
            response_mime_type="application/json",
        )
        async def _generate_title(client):
            return await client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=cfg,
            )

        resp = await asyncio.wait_for(
            gemini_generate(
                GEMINI_CLIENTS,
                _generate_title,
                model_name=GEMINI_MODEL,
            ),
            timeout=30.0,  # total SLA for this tiny title translation
        )
        raw = (getattr(resp, "text", "") or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        title_ru = title_case_fragment(normalize_title_text(str(data.get("title_ru") or "")))
        author_ru = _replace_known_author_names(str(data.get("author_ru") or ""))
        if not title_ru:
            title_ru = fallback_title
        if not author_ru:
            author_ru = fallback_author
        result = (title_ru[:180].strip(), author_ru[:80].strip())
    except Exception as e:
        logger.info("[LiveDubTitle] fallback title translation: %s", str(e)[:120])
        result = (fallback_title, fallback_author)

    _LIVEDUB_TITLE_CACHE[cache_key] = result
    return result


def _format_livedub_title_line(title_ru: str, author_ru: str) -> str:
    raw_title = normalize_title_text(title_ru) or str(title_ru or "").strip()
    author_ru = normalize_author_name(author_ru).strip()
    # If the light model already returned a complete line, normalize order:
    # author-first («Р. Ч. Спроул - True...») -> title-first.
    if raw_title and not author_ru and re.search(r"\s[-–—]\s|:\s", raw_title):
        split_title, split_author = _split_title_author_line(raw_title)
        if split_author:
            # Уже готовая строка от light-model: не sentence-case'им весь title,
            # чтобы не портить собственные имена/богословские термины.
            return f"{split_title.strip()} - {split_author}".strip(" -")
        return normalize_common_typos(raw_title).strip()
    title_ru = title_case_fragment(raw_title).strip()
    if title_ru and author_ru and author_ru.lower() not in title_ru.lower():
        return f"{title_ru} - {author_ru}"
    return title_ru or author_ru or "Переведённое видео"

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
    _allowed = {"sponsor", "selfpromo", "interaction", "intro", "outro",
                "preview", "filler", "music_offtopic", "all", "default"}
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
                _flags = _flags[_i + 1:]
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


async def process_single_video(url, update, status_msg=None, progress_prefix="", context=None, silent_errors: bool = False):
    url = get_youtube_video_url(url)
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
        _info_timeout = info_timeout_seconds()

        # AUDIT R31: сначала пробуем yt-dlp В ПРОЦЕССЕ (без спавна дочернего
        # python.exe) — это обходит краш 0xc0000142 на машинах, где PATH
        # бота ломает загрузчик нового процесса. Только если не вышло —
        # откат на прежний subprocess-путь (поведение сохранено).
        info_dict = await _ytdlp_info_inprocess(url, _info_timeout)

        if info_dict is None:
            try:
                info_proc = await run_cancellable_process(
                    info_cmd, timeout=_info_timeout, text=True
                )
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
        _max_dur = env_int(
            "MAX_VIDEO_DURATION_SEC", DEFAULT_MAX_DURATION_SEC, minimum=60
        )  # дефолт 3 часа
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

        full_title   = info_dict.get("title", "audio")
        channel_name = info_dict.get("uploader", info_dict.get("channel", ""))
        source_lang  = str(info_dict.get("language") or "").lower()
        logger.info(f"YouTube channel_name: '{channel_name}' (lang: {source_lang})")
        duration     = int(info_dict.get("duration") or 0)
        media_id     = info_dict.get("id", "media")

        # --- LIVEDUB: проверяем режим пользователя ---
        user_id = update.effective_user.id if (update.effective_user and update.effective_user.id) else None
        user_mode = "rus"
        if user_id:
            try:
                with _db_conn() as conn:
                    row = conn.execute(
                        "SELECT value FROM bot_settings WHERE key = ?",
                        (f"user_mode_{user_id}",)
                    ).fetchone()
                if row and row[0] in ("eng", "eng_fast", "eng_fast_qa"):
                    user_mode = row[0]
            except Exception:
                pass

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
                        _ld_version or "legacy", _ld_expected_version,
                    )
            except Exception:
                _livedub_cached_file_id = ""
        if _livedub_cached_file_id and user_mode in ("eng", "eng_fast", "eng_fast_qa"):
            logger.info(f"[LiveDub] кэш file_id найден — повторная отправка мгновенно ({media_id})")
        elif user_mode in ("eng", "eng_fast", "eng_fast_qa"):
            # FIX 2026-06-10 fail-fast: без vot-cli юзер раньше узнавал о
            # проблеме через минуты (или никогда в Quick). Проверяем сразу.
            from services.yandex_live_dub import get_live_dub_video, _check_vot_cli, _ensure_vot_helper
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
                    try:
                        await update.message.reply_text(
                            "⚠️ Режим ENG: нет клиента для Яндекс LiveDub.\n"
                            "Установите Node.js (helper поставит @vot.js/node сам) "
                            "или старый резерв: npm install -g vot-cli-live\n"
                            + ("Продолжаю обычный анализ без перевода." if user_mode == "eng" else "")
                        )
                    except Exception:
                        pass
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
            _livedub_quick_qa_enabled = (user_mode == "eng_fast_qa")
            _livedub_pro_mix = await asettings_get("livedub_pro_mix")
            _livedub_autofix = (_livedub_qa_enabled or _livedub_quick_qa_enabled) and await asettings_get("livedub_autofix")
            try:
                from services.livedub_info import livedub_info_enabled
                _livedub_info_enabled = (user_mode in ("eng_fast", "eng_fast_qa")) and livedub_info_enabled()
            except Exception:
                _livedub_info_enabled = False

            async def _run_livedub_bg(video_url, workdir, source_lang=""):
                # FIX AUDIT R4: внутренние таски объявлены до try — finally
                # ниже отменяет их при cancel() родителя, иначе vot-cli/ffmpeg/
                # Whisper продолжали качать сотни МБ и писать в каталог,
                # который пайплайн уже rmtree-нул.
                subs_task = None
                dub_task = None
                dub_subs_task = None
                try:
                    # Запускаем создание субтитров параллельно с скачиванием LiveDub
                    if eng_subs_enabled:
                        subs_task = asyncio.create_task(
                            create_gemini_subtitles(video_url, workdir, known_duration=duration, lang=source_lang)
                        )
                    
                    async def _make_dub():
                        """Pro-микс (свой ffmpeg: слышный оригинал, ducking,
                        задержка перевода) с fallback на vot-cli --merge-video."""
                        if _livedub_pro_mix:
                            try:
                                from services.livedub_mix import build_pro_dub
                                pro = await build_pro_dub(video_url, workdir, duration=duration, lang=source_lang)
                                if pro and pro.exists():
                                    return pro
                                logger.warning("[LiveDub] pro-микс не собрался — fallback на vot-merge")
                            except RuntimeError:
                                raise  # LIVEDUB_NOT_AVAILABLE — наверх
                            except Exception as _pme:
                                logger.warning(f"[LiveDub] pro-микс сбой ({_pme}) — fallback на vot-merge")
                        from services.yandex_live_dub import get_live_dub_video
                        return await get_live_dub_video(
                            video_url, workdir,
                            original_volume=0.3,
                            translation_volume=1.5,
                            keep_original_audio=True,
                            duration=duration,
                            lang=source_lang,
                        )

                    dub_task = asyncio.create_task(_make_dub())

                    # Субтитры перевода Яндекса — точный текст дубляжа для QA.
                    # Качаются параллельно, сбой не критичен.
                    if _livedub_qa_enabled or _livedub_info_enabled or _livedub_quick_qa_enabled:
                        from services.yandex_live_dub import get_translation_subtitles
                        dub_subs_task = asyncio.create_task(
                            get_translation_subtitles(video_url, workdir)
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
                        if "LIVEDUB_AUTH_REQUIRED" in str(e):
                            logger.warning(
                                "[LiveDub] Яндекс требует OAuth-токен для живых голосов "
                                "(нет/протух VOT_API_TOKEN в .env — см. .env.example)"
                            )
                            try:
                                (workdir / ".auth_required").write_text("1", encoding="utf-8")
                            except OSError:
                                pass
                        elif "LIVEDUB_NOT_AVAILABLE" in str(e):
                            logger.info("[LiveDub] Перевод недоступен для этого видео")
                            try:
                                (workdir / ".not_available").write_text("1", encoding="utf-8")
                            except OSError:
                                pass
                        else:
                            logger.warning(f"[LiveDub] Ошибка: {e}")
                            try:
                                (workdir / ".livedub_failed").write_text(str(e)[:500], encoding="utf-8")
                            except OSError:
                                pass
                    except Exception as e:
                        logger.warning(f"[LiveDub] Неизвестная ошибка: {e}")
                        try:
                            (workdir / ".livedub_failed").write_text(str(e)[:500], encoding="utf-8")
                        except OSError:
                            pass

                    # Дожидаемся субтитров перевода (обычно готовы раньше дубляжа)
                    if dub_subs_task:
                        try:
                            await asyncio.wait_for(dub_subs_task, timeout=60)
                        except Exception as _dse:
                            logger.info(f"[LiveDub] субтитры перевода не получены: {_dse}")

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
                finally:
                    _inner = [t for t in (subs_task, dub_task, dub_subs_task)
                              if t is not None and not t.done()]
                    for _t in _inner:
                        _t.cancel()
                    if _inner:
                        try:
                            await asyncio.gather(*_inner, return_exceptions=True)
                        except BaseException:
                            pass

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
            enabled=bool(
                context
                and user_mode == "eng"
                and (_livedub_cached_file_id or live_dub_task)
            ),
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
                await _source_audio_delivery.flush("LiveDub unavailable")
                reason = str(reason or "").strip()
                if not reason:
                    reason = "Яндекс/VOT не отдал живой дубляж для этого ролика."
                try:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=(
                            "⚠️ <b>Живой перевод Яндекса не получился</b>\n"
                            f"{html_mod.escape(reason)}\n\n"
                            "Анализ/конспект я всё равно отправил, но видео с дубляжом "
                            "и проверка точности перевода не запускались — проверять нечего."
                        ),
                        parse_mode="HTML",
                        reply_to_message_id=update.message.message_id,
                    )
                    _livedub_result_sent = True
                    return True
                except Exception as _ld_notice_err:
                    logger.info("[LiveDub] failure notice not sent: %s", str(_ld_notice_err)[:160])
                    return False

            from services.livedub_publication import (
                build_publication_card,
                format_video_caption,
            )

            _publication_ai = None
            try:
                _publication_ai = ai_data if isinstance(ai_data, dict) else None
            except NameError:
                _publication_ai = None
            if _publication_ai is None:
                try:
                    _publication_ai = c_ai if isinstance(c_ai, dict) else None
                except NameError:
                    _publication_ai = None

            _publication_title = str((_publication_ai or {}).get("real_title") or title or "").strip()
            _publication_author = str((_publication_ai or {}).get("real_author") or performer or "").strip()
            _publication_source_line = (
                f"{_publication_title} - {_publication_author}"
                if _publication_author
                else (_publication_title or full_title or "Переведённое видео")
            )
            _publication_card = await build_publication_card(_publication_source_line, url)
            _pub_title = str(_publication_card.get("title") or _publication_title or "Переведённое видео").strip()
            _pub_author = str(_publication_card.get("author") or _publication_author or "").strip()
            _livedub_title_line = (
                f"{_pub_title} - {_pub_author}"
                if _pub_author and _pub_author.casefold() not in _pub_title.casefold()
                else _pub_title
            )
            _livedub_title_html = html_mod.escape(_livedub_title_line) if _livedub_title_line else ""

            # Мгновенная повторная отправка по кэшированному file_id
            if _livedub_cached_file_id and context:
                from services.livedub_delivery_coordinator import (
                    delete_message_best_effort,
                    deliver_cached_companions,
                )

                _cached_internal_caption = (
                    f"<b>{_livedub_title_html}</b>\n🎬 Живые голоса Яндекса"
                    if _livedub_title_html else "🎬 Живые голоса Яндекса"
                )
                _cached_cap = format_video_caption(_publication_card, _cached_internal_caption)
                _cached_video_message = None
                try:
                    _cached_video_message = await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=_livedub_cached_file_id,
                        caption=_cached_cap,
                        parse_mode="HTML",
                        reply_to_message_id=update.message.message_id,
                        supports_streaming=True,
                    )
                    companions_ok = await deliver_cached_companions(
                        context.bot,
                        chat_id=update.effective_chat.id,
                        video_file_id=_livedub_cached_file_id,
                        publication_card=_publication_card,
                        reply_to=update.message.message_id,
                    )
                    if not companions_ok:
                        raise RuntimeError("cached LiveDub companion set is missing")
                except Exception as _fid_err:
                    if _cached_video_message is not None:
                        await delete_message_best_effort(
                            context.bot, update.effective_chat.id, _cached_video_message
                        )
                    logger.warning("[LiveDub] cached pair rejected: %s", _fid_err)
                    try:
                        from core.database import adb_set_livedub_file_id
                        await adb_set_livedub_file_id(media_id, "")
                    except Exception:
                        pass
                    await _source_audio_delivery.flush("cached LiveDub pair unavailable")
                    try:
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=(
                                "⚠️ Кэшированный перевод или его MP3-комплект устарел. "
                                "Отправьте ссылку ещё раз — полный комплект будет создан заново."
                            ),
                            reply_to_message_id=update.message.message_id,
                        )
                    except Exception:
                        pass
                    _livedub_result_sent = True
                    return True

                _source_audio_delivery.discard("cached LiveDub pair delivered")
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
                    try:
                        _max_q = int(os.getenv("LIVEDUB_QUICK_QA_MAX_DURATION", "120") or "120")
                    except ValueError:
                        _max_q = 120
                    if duration and duration <= _max_q:
                        try:
                            from services.livedub_qa import run_translation_qa, format_qa_report
                            from services.livedub_info import get_light_model
                            _quick_srt = None
                            _orig_for_quick = None
                            _clean_ru_quick = None  # AUDIT R42: чистая RU-дорожка
                            if "ld_work" in locals() and ld_work.exists():
                                _srt_candidates = sorted(
                                    (f for f in ld_work.glob("*.srt") if f.name != "gemini_subs.srt"),
                                    key=lambda f: f.stat().st_mtime, reverse=True,
                                )
                                _quick_srt = _srt_candidates[0] if _srt_candidates else None
                                _orig_candidates = sorted(
                                    ld_work.glob("original_video.*"),
                                    key=lambda f: f.stat().st_mtime,
                                    reverse=True,
                                )
                                _orig_for_quick = _orig_candidates[0] if _orig_candidates else None
                                # AUDIT R42: чистая RU-дорожка (без EN-фона микса) —
                                # точнее для аудио-сравнения, чем звук из готового видео.
                                try:
                                    from services.livedub_mix import find_pro_tracks
                                    _clean_ru_quick = find_pro_tracks(ld_work)[1]
                                except Exception:
                                    _clean_ru_quick = None
                            # AUDIT ENG (2026-07-05): SRT больше НЕ обязателен.
                            # На установках без старого vot-cli субтитры перевода
                            # не скачиваются никогда — старый гейт «srt AND orig»
                            # молча выключал ВСЮ проверку режима Quick QA.
                            # Без SRT run_translation_qa сам извлечёт звук дубляжа
                            # из готового видео и сравнит аудио-с-аудио.
                            if _orig_for_quick:
                                _quick_qa = await run_translation_qa(
                                    dub_video_path=livedub_path,
                                    original_audio_path=_orig_for_quick,
                                    ai_data=None,
                                    duration=duration,
                                    model_name=os.getenv("LIVEDUB_QUICK_QA_MODEL", "").strip() or get_light_model(),
                                    dub_srt_path=_quick_srt,
                                    dub_audio_path=_clean_ru_quick,
                                    thinking_level=os.getenv("LIVEDUB_QUICK_QA_THINKING", "minimal").strip() or "minimal",
                                )
                                _quick_majors = [i for i in ((_quick_qa or {}).get("issues") or [])
                                                 if str(i.get("severity")) == "major"]
                                if _quick_majors:
                                    logger.warning(
                                        "[LiveDubQuickQA] major issues=%d — пробую автофикс до отправки",
                                        len(_quick_majors),
                                    )
                                    try:
                                        from services.livedub_mix import apply_qa_audio_fixes
                                        fixed = await apply_qa_audio_fixes(ld_work, (_quick_qa or {}).get("issues") or [])
                                        if fixed and fixed.exists():
                                            livedub_path = fixed
                                            await context.bot.send_message(
                                                chat_id=update.effective_chat.id,
                                                text=("⚠️ Быстрая проверка нашла серьёзное искажение перевода. "
                                                      "Отправляю версию, где проблемный русский фрагмент вырезан, "
                                                      "а оригинал поднят."),
                                                reply_to_message_id=update.message.message_id,
                                            )
                                    except Exception as _qfix_err:
                                        logger.warning("[LiveDubQuickQA] автофикс не удался: %s", str(_qfix_err)[:160])
                                if _quick_qa:
                                    _quick_qa_report_html = format_qa_report(_quick_qa, video_url=url)
                                else:
                                    _quick_qa_skip_note = "Проверка перевода не удалась (Gemini не дал ответ)"
                            else:
                                logger.warning("[LiveDubQuickQA] skipped: оригинальная дорожка не сохранилась в workdir")
                                _quick_qa_skip_note = "Проверка перевода пропущена: оригинальная дорожка не сохранилась"
                        except Exception as _quick_qa_err:
                            logger.info("[LiveDubQuickQA] skip: %s", str(_quick_qa_err)[:160])
                            _quick_qa_skip_note = "Проверка перевода не удалась (внутренняя ошибка)"
                    else:
                        logger.info("[LiveDubQuickQA] skipped: duration=%s > max=%s", duration, _max_q)
                        _quick_qa_skip_note = f"Проверка перевода пропущена: ролик длиннее {_max_q} сек"

                # Telegram/локальный Bot API берёт basename файла. Без этого
                # пользователь скачивает десятки одинаковых pro_dub.mp4.
                try:
                    from services.livedub_mix import prepare_livedub_send_path
                    livedub_path = prepare_livedub_send_path(
                        livedub_path, _livedub_title_line, fallback=media_id,
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
                    _qa_status_full = None
                    try:
                        _qa_status_full = await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text="🔍 Проверяю точность перевода перед отправкой (Gemini сравнивает дубляж с оригиналом)…",
                            reply_to_message_id=update.message.message_id,
                        )
                    except Exception:
                        pass
                    try:
                        from services.livedub_qa import run_translation_qa
                        _orig_audio = None
                        _mp3_candidate = DOWNLOAD_DIR / f"{media_id}.mp3"
                        if _mp3_candidate.exists():
                            _orig_audio = _mp3_candidate
                        else:
                            _mp3_64 = DOWNLOAD_DIR / f"{media_id}_64.mp3"
                            if _mp3_64.exists():
                                _orig_audio = _mp3_64
                        _qa_ai_data = None
                        try:
                            _qa_cached = await adb_get(media_id)
                            _qa_cache_ok, _qa_cache_reason = is_cache_valid(_qa_cached)
                            if _qa_cache_ok:
                                _qa_ai_data = (_qa_cached or {}).get("ai_data")
                            elif _qa_cached:
                                logger.info(
                                    "[LiveDubQA] stale cached analysis ignored: %s",
                                    _qa_cache_reason,
                                )
                        except Exception as _qa_cache_err:
                            logger.info(
                                "[LiveDubQA] cached analysis unavailable: %s",
                                str(_qa_cache_err)[:160],
                            )
                        _dub_srt = None
                        _clean_ru_full = None  # AUDIT R42: чистая RU-дорожка
                        try:
                            if "ld_work" in locals() and ld_work.exists():
                                _srt_candidates = sorted(
                                    (f for f in ld_work.glob("*.srt")
                                     if f.name != "gemini_subs.srt"),
                                    key=lambda f: f.stat().st_mtime, reverse=True,
                                )
                                _dub_srt = _srt_candidates[0] if _srt_candidates else None
                                # AUDIT R42: чистая RU-дорожка (без EN-фона микса)
                                try:
                                    from services.livedub_mix import find_pro_tracks
                                    _clean_ru_full = find_pro_tracks(ld_work)[1]
                                except Exception:
                                    _clean_ru_full = None
                        except Exception:
                            pass
                        _qa_part = None
                        _qa_client = None
                        try:
                            if used_audio_part is not None and hasattr(used_audio_part, "name"):
                                _qa_part = used_audio_part
                                _qa_client = used_client
                        except NameError:
                            pass
                        _qa_result_full = await run_translation_qa(
                            dub_video_path=livedub_path,
                            original_audio_path=_orig_audio,
                            ai_data=_qa_ai_data,
                            duration=duration,
                            dub_srt_path=_dub_srt,
                            dub_audio_path=_clean_ru_full,
                            existing_audio_part=_qa_part,
                            existing_client=_qa_client,
                        )
                        # Автоправка: major-искажения глушим ПРЯМО в основном ролике.
                        if _qa_result_full and _livedub_autofix and "ld_work" in locals():
                            _qa_majors = [i for i in (_qa_result_full.get("issues") or [])
                                          if str(i.get("severity")) == "major"]
                            if _qa_majors:
                                try:
                                    from services.livedub_mix import (
                                        apply_qa_audio_fixes,
                                        prepare_livedub_send_path as _prep_fixed,
                                    )
                                    fixed = await apply_qa_audio_fixes(
                                        ld_work, _qa_result_full.get("issues") or [])
                                    if fixed and fixed.exists():
                                        fixed = _prep_fixed(
                                            fixed, _livedub_title_line,
                                            fallback=f"{media_id}_fixed")
                                        if fixed.stat().st_size / (1024 * 1024) <= get_max_file_size_mb():
                                            livedub_path = fixed  # основной ролик = исправленный
                                            _autofix_note = (
                                                f"🩹 В {len(_qa_majors)} месте(ах) с искажением "
                                                f"русский дубляж приглушён, оригинал выведен в полный голос."
                                            )
                                        else:
                                            logger.info("[LiveDubQA] фикс-версия превышает лимит — шлю оригинал")
                                    else:
                                        # find_pro_tracks не нашёл чистые дорожки
                                        # (глубокий vot-cli --merge-video fallback):
                                        # честно скажем, что приглушить не смогли.
                                        _autofix_note = (
                                            "🩹 Найдены искажения, но авто-приглушение не удалось "
                                            "(чистые дорожки не сохранились) — детали в отчёте ниже."
                                        )
                                except Exception as _fx_err:
                                    logger.warning(f"[LiveDubQA] авто-правка сбой: {_fx_err}")
                    except Exception as _qa_err:
                        logger.warning(f"[LiveDubQA] сбой проверки: {_qa_err}")
                    finally:
                        if _qa_status_full is not None:
                            try:
                                await _qa_status_full.delete()
                            except Exception:
                                pass

                if not is_fallback and livedub_path and livedub_path.exists():
                    _livedub_video_for_downstream = Path(livedub_path)

                # ── Техническая проверка целостности (всегда, дёшево) ──
                _tech_warnings: list[str] = []
                if not is_fallback:
                    try:
                        from services.livedub_qa import technical_check
                        _tech_warnings = await asyncio.get_running_loop().run_in_executor(
                            None, lambda: technical_check(livedub_path, duration)
                        )
                        for _tw in _tech_warnings:
                            logger.warning(f"[LiveDubQA] tech: {_tw}")
                    except Exception as _tqe:
                        logger.warning(f"[LiveDubQA] technical_check сбой: {_tqe}")

                file_size = livedub_path.stat().st_size / (1024 * 1024)
                if file_size > get_max_file_size_mb():
                    logger.warning(f"[LiveDub] Файл слишком большой для отправки: {file_size:.1f}MB (лимит {get_max_file_size_mb()}MB)")
                    _livedub_hint = "" if get_max_file_size_mb() > 50 else "\n💡 Подними локальный Bot API сервер (LOCAL_BOT_API_URL), чтобы отправлять до 2000 МБ."
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"⚠️ Видео с переводом готово, но оно весит {file_size:.1f} МБ. Текущий лимит отправки — {get_max_file_size_mb()} МБ.{_livedub_hint}",
                        reply_to_message_id=update.message.message_id,
                    )
                    await _source_audio_delivery.flush("LiveDub video exceeds Telegram limit")
                    _livedub_result_sent = True
                    return True
                _title_prefix = f"<b>{_livedub_title_html}</b>\n" if _livedub_title_html else ""
                if is_fallback:
                    caption = _title_prefix + "⚠️ Живой перевод Яндекса недоступен/сломался.\nОтправляю резерв: оригинальное видео" + (" + русские субтитры." if has_subs else ".")
                else:
                    # round 38: если сработал tts-fallback — честный caption
                    _tts_marker = False
                    try:
                        _tts_marker = ("ld_work" in locals() and (ld_work / ".voice_style_tts").exists())
                    except OSError:
                        pass
                    _voice_label = "🎬 Перевод Яндекса (обычные голоса)" if _tts_marker else "🎬 Живые голоса Яндекса"
                    caption = _title_prefix + _voice_label + ("\n💬 Русские субтитры сделаны независимо через Whisper + Gemini" if has_subs else "")
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
                _v_meta, _v_thumb = {}, None
                try:
                    from services.livedub_mix import probe_video_meta, make_video_thumbnail
                    _v_meta = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: probe_video_meta(livedub_path))
                    _v_thumb = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: make_video_thumbnail(livedub_path))
                except Exception as _vm_err:
                    logger.warning(f"[LiveDub] video meta: {_vm_err}")
                # Bot API 8.3+: cover — настоящая обложка видео в чате.
                # Используем YouTube-обложку (уже скачана основным пайплайном).
                _v_cover = None
                try:
                    _cover_candidates = sorted(THUMBS_DIR.glob(f"{media_id}_thumb*"))
                    if _cover_candidates and _cover_candidates[0].stat().st_size > 1024:
                        _v_cover = _cover_candidates[0]
                except Exception:
                    pass
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
                    write_timeout=600, read_timeout=600, connect_timeout=60,
                )
                try:
                    await context.bot.send_chat_action(
                        chat_id=update.effective_chat.id, action="upload_video")
                except Exception:
                    pass
                try:
                    if _v_cover is not None:
                        _sent_msg = await context.bot.send_video(cover=_v_cover, **_send_kwargs)
                    else:
                        _sent_msg = await context.bot.send_video(**_send_kwargs)
                except Exception as _cov_err:
                    if _v_cover is None:
                        raise
                    # AUDIT R21 (живой лог: "Request Entity Too Large"): размер
                    # payload не зависит от cover — повтор без него гарантированно
                    # провалится ещё раз тем же способом и просто тратит время
                    # на повторную загрузку тяжёлого видео. Повторяем только когда
                    # ошибка НЕ похожа на превышение размера.
                    _cov_err_text = str(_cov_err).lower()
                    if "entity too large" in _cov_err_text or "too large" in _cov_err_text:
                        raise
                    # Старый Bot API сервер/прокси может не знать cover — повтор без него
                    logger.info(f"[LiveDub] cover не принят ({str(_cov_err)[:100]}) — отправка без cover")
                    _sent_msg = await context.bot.send_video(**_send_kwargs)
                # Companion delivery is an explicit transaction. Only a complete
                # video+MP3 result enters the fast-resend video cache.
                _video_file_id = str(
                    getattr(getattr(_sent_msg, "video", None), "file_id", "") or ""
                ).strip()
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
                        try:
                            await context.bot.send_message(
                                chat_id=update.effective_chat.id,
                                text=(
                                    "⚠️ Видео с переводом отправлено, но полный проверенный "
                                    "MP3-комплект сформировать не удалось. Видео не сохранено "
                                    "в быстрый кэш как полный комплект."
                                ),
                                reply_to_message_id=update.message.message_id,
                            )
                        except Exception:
                            pass

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
                                    pass

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
                await _source_audio_delivery.flush("LiveDub timeout")
                # wait_for отменяет задачу при таймауте — перевод уже не придёт.
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="⏳ Перевод «Живые голоса» не успел за 30 минут и был отменён. Отправьте ссылку ещё раз — попробуем заново.",
                    reply_to_message_id=update.message.message_id,
                )
                _livedub_result_sent = True
                return True
            except Exception as e:
                await _source_audio_delivery.flush("LiveDub send/generation failure")
                logger.warning(f"[LiveDub] fail: {e}")
                # AUDIT R21 (живой лог: перевод сгенерировался, но отправка
                # видео упала с "Request Entity Too Large" — юзер не получил
                # вообще никакого объяснения, в отличие от case'ов таймаута
                # и превышения размера ДО отправки, которые уже уведомляют).
                try:
                    _fail_text = str(e)
                    _size_related = (
                        "entity too large" in _fail_text.lower()
                        or "too large" in _fail_text.lower()
                    )
                    _hint = (
                        "\n💡 Подними локальный Bot API сервер (LOCAL_BOT_API_URL), "
                        "чтобы отправлять до 2000 МБ."
                        if _size_related and get_max_file_size_mb() <= 50 else ""
                    )
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=(
                            "⚠️ <b>Не удалось отправить видео с переводом</b>\n"
                            f"{html_mod.escape(_fail_text[:300])}{_hint}\n\n"
                            "Перевод сгенерирован, но отправка не удалась."
                        ),
                        parse_mode="HTML",
                        reply_to_message_id=update.message.message_id,
                    )
                except Exception as _notify_err:
                    logger.info(
                        "[LiveDub] send-failure notice not sent: %s",
                        str(_notify_err)[:160],
                    )
                return False

        performer, title = parse_title(full_title, channel_name)

        # ── ENG Quick: только перевод, весь анализ пропускаем ────────────
        if user_mode in ("eng_fast", "eng_fast_qa"):
            if status_msg:
                try:
                    await status_msg.edit_text(
                        f"⚡ Режим ENG Quick: жду перевод «Живые голоса»...\n📝 {title}"
                    )
                except Exception:
                    pass
            _delivered = await _send_livedub_result()
            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
            if not _delivered:
                # FIX 2026-06-10: раньше при сбое перевода ENG Quick юзер
                # оставался ВООБЩЕ без ответа (статус удалён, видео нет).
                # ROUND 39: если отказ из-за отсутствия OAuth-токена — говорим
                # прямо, как починить (это и была причина «Translation not
                # available» на Shorts и новых видео).
                _auth_required = False
                try:
                    _auth_required = ("ld_work" in locals()
                                      and (ld_work / ".auth_required").exists())
                except OSError:
                    pass
                # FIX AUDIT R4: в плейлисте (silent_errors=True) не шумим
                # отдельным сообщением на каждый неудачный entry — итоговую
                # сводку даёт handle_playlist (AUDIT M3).
                try:
                    if silent_errors:
                        pass
                    elif _auth_required:
                        await update.message.reply_text(
                            "❌ Яндекс теперь требует авторизацию для «Живых голосов».\n"
                            "🔑 Добавь VOT_API_TOKEN в .env (инструкция в .env.example):\n"
                            "1) открой https://rust-server-531j.onrender.com/v1/auth/handle\n"
                            "2) войди в аккаунт Яндекса\n"
                            "3) скопируй access_token из адресной строки после редиректа\n"
                            "4) VOT_API_TOKEN=<токен> (или YANDEX_OAUTH_TOKEN=<токен>) в .env и перезапусти бота."
                        )
                    else:
                        await update.message.reply_text(
                            "❌ Перевод «Живые голоса» не получился для этого видео.\n"
                            "Возможные причины: перевод недоступен у Яндекса, vot-cli-live "
                            "не установлен, или видео слишком длинное.\n"
                            "Попробуйте режим 🇬🇧 ENG Full (/mode) — там будет хотя бы анализ."
                        )
                except Exception:
                    pass
            cleanup_files(media_id)
            logger.info(f"ENG Quick done: {media_id} delivered={_delivered}")
            # FIX AUDIT R4: return _delivered — иначе handle_playlist считал
            # недоставленный entry успешным (✅) при уже списанном слоте лимита.
            return _delivered

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
                await update.message.reply_text(
                    f"⚠️ Файл слишком большой ({_too_big.size_mb:.1f} МБ) "
                    "даже после сжатия."
                )
                # FIX AUDIT R4: не теряем обещанный ENG-перевод (см. полную ветку).
                try:
                    await _send_livedub_result()
                except Exception as _ld_send_err:
                    logger.warning("[LiveDub] send on early return failed: %s", _ld_send_err)
                cleanup_files(media_id)
                return False
            mp3_path = _audio.path
            file_size_mb = _audio.size_mb
            bitrate = _audio.bitrate
            logger.info(f"Кэш аудио: {mp3_path.name} = {file_size_mb:.1f} MB")
            # Alt-links и подпись — одна покрытая cache-hit стадия.
            # Сохранённые ссылки не вызывают внешний поиск; обрезка остаётся
            # в общей pipelines/caption_stage.py, как и на fresh-пути.
            _cached_caption = await prepare_cached_presentation(
                CachedPresentationRequest(
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
                )
            )
            # Cache-hit отправка использует тот же проверенный файловый upload,
            # но сначала пробует мгновенный resend по Telegram file_id. Отличия
            # cache-PDF/ID3 и отсутствие quiz остаются явной политикой стадии.
            await deliver_cached_result(
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
                    caption=_cached_caption.caption,
                    full_caption=_cached_caption.full_caption,
                    download_dir=DOWNLOAD_DIR,
                    thumbs_dir=THUMBS_DIR,
                    ai_data=c_ai,
                    thumbnail=thumb_buffer,
                    synopsis_url=c_links.synopsis_url or "",
                    study_url=c_links.study_url,
                    reflection_url=c_links.reflection_url,
                    terms_url=c_links.terms_url,
                    cached_file_id=(cached or {}).get("audio_file_id") or "",
                )
            )

            # --- LIVEDUB: отправка результата (общий хелпер) ---
            # FIX: раньше cleanup/return были случайно вложены в
            # `if live_dub_task and context:` — кэш-хит в RUS-режиме
            # не делал return и видео обрабатывалось заново.
            await _send_livedub_result()
            cleanup_files(media_id)
            logger.info(f"Кэш hit: {media_id}")
            return True

        await set_progress(status_msg, 2, {'title': f'📝 {title}', 'author': f'👤 {performer}  •  {duration//60}:{duration%60:02d}'}, prefix=_pp)
        thumb_buffer = await load_thumbnail(
            media_id=media_id,
            url=url,
            thumbs_dir=THUMBS_DIR,
            cache_hit=False,
        )

        await set_progress(status_msg, 3, {'title': f'📝 {title}', 'author': f'👤 {performer}  •  {duration//60}:{duration%60:02d}', 'fmt': '🎵 128 kbps  •  MP3'}, prefix=_pp)

        # Проверяем кэш MP3 — если файл уже есть, скачивание пропускаем
        async def _squeeze_notice(current_mb: float) -> None:
            await set_progress(status_msg, 3, {"title": f"📝 {title}", "info": f"⚙️ Файл {current_mb:.1f} МБ — пересжимаю в 64 kbps..."}, prefix=_pp)

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
            await update.message.reply_text(str(_too_big))
            # FIX AUDIT R4: у LiveDub-видео свой независимый size-check и
            # своё сообщение — не теряем обещанный ENG-перевод из-за
            # слишком большого MP3.
            try:
                await _send_livedub_result()
            except Exception as _ld_send_err:
                logger.warning("[LiveDub] send on early return failed: %s", _ld_send_err)
            cleanup_files(media_id)
            return False
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
        ai_data          = _analysis.ai_data
        synopsis_outline = _analysis.synopsis_outline
        telegraph_url    = _analysis.telegraph_url
        used_audio_part  = _analysis.used_audio_part
        used_client      = _analysis.used_client
        _degraded_caption_data = _analysis.degraded_caption_data
        _early_alt       = _analysis.early_links
        _pre_rutube      = _analysis.rutube_url
        _pre_vk          = _analysis.vk_url
        # НЕ удаляем audio_part здесь — он нужен для Shorts и Clips.
        # Удаление происходит в блоке finally ниже.

        await set_progress(status_msg, 6, prefix=_pp)
        search_title = _build_search_title(ai_data, full_title)
        tg_author    = normalize_author_name((ai_data or {}).get("real_author") or performer) or performer
        _questions   = (ai_data or {}).get("questions", []) if ai_data else []
        _terms_data  = (ai_data or {}).get("terms_data", {}) or {} if ai_data else {}

        # -- Читаем настройки одним batch вызовом -------------------------
        # Решение «что публикуем» вынесено в pipelines/publication_plan.py:
        # пять флагов и три взаимных исключения больше не размазаны по телу
        # функции, и все 32 комбинации проверяются тестами.
        _all_settings = await asettings_get_all()
        _caption_source_data = ai_data or _degraded_caption_data
        _mat_format   = (_caption_source_data or {}).get("format", "other")
        _ts_total     = len([l for l in ((_caption_source_data or {}).get("timestamps") or "").split("\n") if l.strip()])
        _ts_cap_limit = get_caption_timestamp_limit(_mat_format)

        _terms_total = _plan_terms_total(_terms_data)
        _flags, _plan = build_plan(_all_settings, ai_data=ai_data)
        # Отдельно от плана: «анализ есть» и «пользователь всё выключил» — разные
        # ситуации, и предупреждение про галлюцинации относится только к первой.
        _has_ai_analysis = has_ai_analysis(ai_data)
        _feat_analytics              = _plan.analytics
        _feat_questions              = _plan.questions
        _feat_terms                  = _plan.terms
        _feat_study_analysis         = _plan.study_analysis
        _feat_reflection_application = _plan.reflection_application
        if not _has_ai_analysis:
            logger.warning(
                "AI analysis is missing/invalid for %s — skipping AI-derived Telegraph pages to avoid hallucinated partial publications",
                media_id,
            )
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
        alt_links                 = _rendered.alt_links
        quotes_tg                 = _rendered.analytics_url
        questions_tg              = _rendered.questions_url
        terms_tg                  = _rendered.terms_url
        study_analysis_tg         = _rendered.study_analysis_url
        reflection_application_tg = _rendered.reflection_url
        _nav_filled               = _rendered.as_nav_pages(telegraph_url or "")

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
            [telegraph_url, study_analysis_tg, reflection_application_tg,
             quotes_tg, questions_tg, terms_tg],
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
        _q_link     = _caption_inputs.links.quotes
        _ref_link   = _caption_inputs.links.questions
        _terms_link = _caption_inputs.links.terms
        rutube_url  = _caption_inputs.rutube_url
        vk_url      = _caption_inputs.vk_url
        ai_data     = _caption_inputs.ai_data
        _pub_status = _caption_inputs.publication
        _ai_caption_base = _caption_inputs.caption_base
        if ai_data and _pub_status.warning:
            logger.warning(
                "Publication status for %s: status=%s missing=%s",
                media_id, _pub_status.status, ",".join(_pub_status.missing),
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
        _fit = fit_caption(_cap_source, _ai_caption_base)
        caption = _fit.caption
        full_caption = _fit.full_caption
        _ts_in_cap = _fit.timestamps_shown
        _caption_trim_stage = _fit.trim_stage
        if _fit.data is not None:
            _ai_caption_base = _fit.data
        logger.info(
            "Caption visible_len=%d format=%s ts_total=%d ts_cap_limit=%d ts_in_cap=%d stage=%s",
            visible_length(caption),
            _mat_format,
            _ts_total,
            _cap_source.timestamps_limit(),
            _ts_in_cap,
            _caption_trim_stage or "none",
        )
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
            # Сборка записи, статус починки и экспорт сегментов — одна
            # некритичная стадия: её сбой не отменяет отправку готового MP3.
            await archive_pages(
                ArchiveRequest(
                    media_id=media_id,
                    source_url=url,
                    ai_data=ai_data,
                    publication=_pub_status,
                    title=title,
                    performer=performer,
                    search_title=search_title,
                    author=tg_author,
                    format_name=_mat_format,
                    duration=duration,
                    rutube_url=rutube_url,
                    vk_url=vk_url,
                    synopsis_url=telegraph_url or "",
                    study_url=study_analysis_tg or "",
                    reflection_url=reflection_application_tg or "",
                    terms_url=terms_tg or "",
                    questions_url=questions_tg or "",
                    model=GEMINI_MODEL,
                    prompt_version=get_prompt_fingerprint(),
                    prompt_variant=os.getenv("PROMPT_EXPERIMENT_TAG", ""),
                ),
                repair=_repair,
            )

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


