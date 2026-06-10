#!/usr/bin/env python3
# AUDIT-V2-ADMIN: ADMIN_IDS bypass fixed
"""
Command Handlers — /start, /help, /settings, /resetcache, /pdf, /stop.
Извлечено из bot.py строки 13513–13822.
"""
from core.globals import (
    DOWNLOAD_DIR, HAS_GEMINI,
    GEMINI_CLIENTS, DAILY_LIMIT, COOLDOWN_SECONDS,   # FIX #9
    InlineKeyboardButton, InlineKeyboardMarkup,       # FIX #9
    html_mod,                                          # FIX #9
    _get_video_lock, _release_video_lock,              # FIX #9 / PART5
)
from core.database import (
    adb_get, adb_save, asettings_get, asettings_get_all,
    db_init, is_cache_valid,
    WHITELIST_IDS, ADMIN_IDS, GEMINI_MODEL,
    MAX_PLAYLIST_SIZE, MAX_FILE_SIZE_MB, DB_PATH,
    areserve_rate_limit,  # AUDIT M4/PART5
)
from core.utils import (
    mask_api_key as _mask,
    is_media_url, is_playlist_url,
    extract_media_url, format_timestamp,               # FIX #9
)
from core.text_utils import normalize_author_name, normalize_title_text  # FIX #9
from core.render_locks import release_render_lock, render_lock_key, try_acquire_render_lock
from core.segment_planner import (
    build_segments_from_timestamps, format_segments_text, get_segment_by_index, parse_segment_selection, seconds_to_timestamp,
)
from core.observability import format_gemini_metrics_report
from core.prompt_health import format_prompt_health_report
from core.code_health import format_code_health_report
from core.archive_quality import format_archive_quality_report, format_prompt_variant_comparison
from core.archive_quality import format_quality_records_report, recommend_prompt_variant
from core.archive_quality import awrite_archive_quality_exports
from core.generated_pages import (
    ARCHIVE_DIR, aget_generated_page_record, aquery_generated_pages,
    asave_segment_plan_export, aupdate_generated_page_repair_status,
    get_archive_export_path, get_segment_export_paths,
)
from services.telegraph_repair import (
    collect_record_page_urls, format_batch_repair_results, format_repair_results,
    repair_generated_page_record, repair_generated_page_records,
    repair_telegraph_page_url, telegraph_path_from_url,
)
from pipelines.main_pipeline import process_single_video
from services.shorts_video import (
    HAS_FASTER_WHISPER, burn_subtitles_into_short, download_video_for_shorts,
    transcribe_short_clip,
)
from services.render_clips_montage import render_clip
from pipelines.playlist import handle_playlist
from core.progress import safe_edit_text

import asyncio
import logging
import os        # FIX #9
import re        # FIX #9
import sqlite3   # FIX #9
import uuid
from pathlib import Path  # FIX #9

logger = logging.getLogger(__name__)

async def start(update, context):
    user_id   = update.effective_user.id
    is_vip    = user_id in WHITELIST_IDS
    is_admin  = user_id in ADMIN_IDS
    ai_status = "✅" if GEMINI_CLIENTS else "❌"

    if is_vip:
        admin_section = ""
        if is_admin:
            admin_section = (
                "\n\n🔧 <b>Команды администратора:</b>\n"
                "/resetcache &lt;url или video_id&gt;\n"
                "/resetcache all — очистить весь кэш\n"
                "/metrics [hours] — Gemini метрики\n"
                "/archive [n] — последние опубликованные страницы\n"
                "/search &lt;текст&gt; — поиск по архиву\n"
                "/repairpage &lt;url|video_id|last&gt; — перечинить Telegraph без Gemini\n"
                "/prompthealth — аудит размера и баланса промптов\n"
                "/codehealth — аудит regex/postprocess слоя\n"
                "/archivequality [n] — качество архива и prompt A/B\n"
                "/comparevariants A B [n] — сравнить prompt variants\n"
                "/archivequalityfile [md|json] [n] — export качества архива\n"
                "/qualityrecords [n|all n] — записи на ручную проверку\n"
                "/promptrecommend [n] — лучший prompt variant"
            )
        text = (
            f"🎵 <b>Media Audio Converter</b>\n\n"
            f"👑 <b>VIP-доступ активен — без ограничений!</b>\n\n"
            f"<b>Как пользоваться:</b>\n"
            f"• Отправьте ссылку на видео или плейлист\n"
            f"• Получите MP3 128kbps + обложка\n\n"
            f"<b>AI-анализ каждого видео:</b>\n"
            f"🧠 AI: {ai_status}\n"
            f"📌 Тема и описание\n"
            f"⏱ Таймкоды по смысловым блокам\n"
            f"💬 Цитаты дословно\n"
            f"🏷 Хэштеги на русском\n"
            f"📋 Конспект в Telegraph\n"
            f"📊 Аналитика: Писание, богословы, аргументы\n"
            f"🗣 8 вопросов для размышления\n"
            f"📺 Поиск на RuTube и VK\n\n"
            f"<b>Команды:</b>\n"
            f"/start — эта панель\n"
            f"/help — краткая справка\n"
            f"/settings — настройки функций бота"
            f"{admin_section}\n\n"
            f"🪪 Ваш Telegram ID: <code>{user_id}</code>"
        )
        await update.message.reply_text(text, parse_mode="HTML")
    else:
        limit_line = f"📵 Лимит: {DAILY_LIMIT} видео/день | ⏳ 1 запрос/мин"
        await update.message.reply_text(
            f"🎵 Media Audio Converter\n\n"
            f"Отправьте ссылку на видео или плейлист!\n\n"
            f"✨ MP3 128kbps + обложка + автор + тема\n"
            f"🧠 AI: {ai_status}\n"
            f"📋 Плейлисты до {MAX_PLAYLIST_SIZE} видео\n"
            f"{limit_line}\n\n"
            f"🪪 Ваш Telegram ID: <code>{user_id}</code>",
            parse_mode="HTML"
        )


async def help_command(update, context):
    user_id = update.effective_user.id
    is_vip  = user_id in WHITELIST_IDS
    limit_line = (
        "👑 VIP — без ограничений"
        if is_vip
        else f"📵 {DAILY_LIMIT} видео/день • 1 запрос/мин"
    )
    await update.message.reply_text(
        f"ℹ️ Помощь\n\n"
        f"Отправьте ссылку на видео или плейлист → получите MP3 128kbps!\n\n"
        f"🧠 AI:\n"
        f"📌 Тема • ⏱ Таймкоды • 🏷 Хэштеги\n\n"
        f"🔒 Ваши лимиты: {limit_line}\n\n"
        f"/start — Приветствие\n"
        f"/help  — Справка\n"
        f"/mode — 🌐 Режим: RUS / ENG Full / ENG Quick\n"
        f"/archive — Последние публикации\n"
        f"/search <текст> — Поиск по архиву\n\n"
        f"🇬🇧 ENG-режимы (для англоязычных видео):\n"
        f"• ENG Full — анализ + видео с «Живыми голосами» Яндекса + проверка точности перевода\n"
        f"• ENG Quick — только переведённое видео, максимально быстро"
    )


def _extract_yt_id_from_text(text: str) -> str | None:
    """Извлекает YouTube video_id из произвольного текста. Возвращает None если не найден."""
    m = re.search(r"(?:v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/live/)([A-Za-z0-9_-]{11})", text)
    return m.group(1) if m else None


async def _do_resetcache_one(video_id: str, update) -> None:
    """Удаляет одну запись кэша и отвечает в чат."""
    loop = asyncio.get_running_loop()
    def _delete():
        with sqlite3.connect(DB_PATH) as conn:
            r = conn.execute("DELETE FROM video_cache WHERE video_id = ?", (video_id,)).rowcount
            conn.commit()
            return r
    rows = await loop.run_in_executor(None, _delete)
    if rows:
        await update.message.reply_text(
            f"✅ Кэш сброшен: <code>{video_id}</code>\nТеперь отправь ссылку заново.",
            parse_mode="HTML")
    else:
        await update.message.reply_text(
            f"⚠️ Не найдено в кэше: <code>{video_id}</code>",
            parse_mode="HTML")


async def reset_cache_command(update, context):
    """Удаляет кэш видео — для принудительной переобработки."""

    user_id = update.effective_user.id
    if not ADMIN_IDS or user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"⛔ Нет доступа.\n"
            f"Ваш Telegram ID: <code>{user_id}</code>\n"
            f"Добавьте его в переменную ADMIN_IDS на Render.", parse_mode="HTML")
        return

    args = context.args
    arg  = args[0].strip() if args else ""

    # ── Нет аргумента — пробуем извлечь ссылку из reply ──────
    if not arg:
        reply = update.message.reply_to_message
        if reply and reply.text:
            video_id = _extract_yt_id_from_text(reply.text)
            if video_id:
                await _do_resetcache_one(video_id, update)
                return
            await update.message.reply_text(
                "⚠️ Не удалось извлечь YouTube ID из сообщения, на которое вы ответили.\n"
                "Используйте: <code>/resetcache &lt;url или video_id&gt;</code>",
                parse_mode="HTML",
            )
            return

        # reply не помог — показываем краткую инструкцию
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Удалить весь кэш", callback_data="resetcache:all")
        ]])
        await update.message.reply_text(
            "🗑 <b>Сброс кэша</b>\n\n"
            "Скопируйте команду и вставьте ссылку после неё:\n\n"
            "<code>/resetcache </code>\n\n"
            "Примеры:\n"
            "<code>/resetcache https://youtu.be/VIDEO_ID</code>\n"
            "<code>/resetcache VIDEO_ID</code>\n\n"
            "Полная очистка: <code>/resetcache all</code>",
            parse_mode="HTML",
            reply_markup=keyboard)
        return

    # ── /resetcache all ───────────────────────────────────────
    if arg.lower() == "all":
        loop = asyncio.get_running_loop()
        def _delete_all():
            with sqlite3.connect(DB_PATH) as conn:
                r = conn.execute("DELETE FROM video_cache").rowcount
                conn.commit()
                return r
        rows = await loop.run_in_executor(None, _delete_all)
        await update.message.reply_text(
            f"🗑 Весь кэш удалён: {rows} записей.", parse_mode="HTML")
        return

    # ── /resetcache <url или video_id> ────────────────────────
    video_id = _extract_yt_id_from_text(arg) or arg
    await _do_resetcache_one(video_id, update)


async def metrics_command(update, context):
    """Показывает администратору сводку Gemini observability."""
    user_id = update.effective_user.id
    if not ADMIN_IDS or user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML"
        )
        return

    hours = 24
    if context.args:
        try:
            hours = int(str(context.args[0]).strip())
        except (TypeError, ValueError):
            hours = 24
    hours = max(1, min(hours, 24 * 30))

    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, lambda: format_gemini_metrics_report(hours=hours, recent_limit=7))
    await update.message.reply_text(report, parse_mode="HTML", disable_web_page_preview=True)


async def codehealth_command(update, context):
    """Admin readout for regex/postprocess code health."""
    user_id = update.effective_user.id
    if not ADMIN_IDS or user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML"
        )
        return
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, format_code_health_report)
    await update.message.reply_text(report, parse_mode="HTML", disable_web_page_preview=True)


async def archivequality_command(update, context):
    """Admin readout for archive quality warnings and prompt variants."""
    user_id = update.effective_user.id
    if not ADMIN_IDS or user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML"
        )
        return
    # _archive_parse_limit определена ниже в этом же модуле — на момент ВЫЗОВА
    # хендлера имя уже разрешено; старый guard через globals() был мёртвым кодом.
    limit = _archive_parse_limit(context.args, 50)
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, lambda: format_archive_quality_report(limit=limit))
    await update.message.reply_text(report, parse_mode="HTML", disable_web_page_preview=True)


async def comparevariants_command(update, context):
    """Compare two prompt variants from the archive quality readout."""
    user_id = update.effective_user.id
    if not ADMIN_IDS or user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML"
        )
        return
    args = list(context.args or [])
    if len(args) < 2:
        await update.message.reply_text(
            "🧪 Использование: <code>/comparevariants default synopsis-v2 [50]</code>",
            parse_mode="HTML",
        )
        return
    left, right = args[0], args[1]
    limit = _archive_parse_limit(args[2:], 50) if len(args) > 2 else 50
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(
        None,
        lambda: format_prompt_variant_comparison(left, right, limit=limit),
    )
    await update.message.reply_text(report, parse_mode="HTML", disable_web_page_preview=True)


async def archivequalityfile_command(update, context):
    """Build and send archive quality markdown/json export."""
    user_id = update.effective_user.id
    if not ADMIN_IDS or user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML"
        )
        return
    args = list(context.args or [])
    kind = (args[0].strip().lower() if args else "md")
    if kind not in {"md", "json"}:
        kind = "md"
        limit_args = args
    else:
        limit_args = args[1:]
    limit = _archive_parse_limit(limit_args, 50)
    paths = await awrite_archive_quality_exports(limit=limit)
    path = Path(paths.get(kind, ""))
    if not path.exists():
        await update.message.reply_text("⚠️ Archive quality export не создан.")
        return
    with open(path, "rb") as f:
        await update.message.reply_document(
            document=f,
            filename=path.name,
            caption=f"🧪 Archive quality export: {path.name}",
            write_timeout=180,
            read_timeout=180,
            connect_timeout=60,
        )


async def qualityrecords_command(update, context):
    """List archive records that need manual quality review."""
    user_id = update.effective_user.id
    if not ADMIN_IDS or user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML"
        )
        return
    args = list(context.args or [])
    warnings_only = True
    if args and args[0].strip().lower() == "all":
        warnings_only = False
        args = args[1:]
    limit = _archive_parse_limit(args, 20)
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(
        None,
        lambda: format_quality_records_report(limit=limit, warnings_only=warnings_only),
    )
    if len(report) > 3900:
        report = report[:3850] + "\n…обрезано"
    await update.message.reply_text(report, parse_mode="HTML", disable_web_page_preview=True)


async def promptrecommend_command(update, context):
    """Recommend prompt variant promotion candidates from archive warnings."""
    user_id = update.effective_user.id
    if not ADMIN_IDS or user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML"
        )
        return
    limit = _archive_parse_limit(context.args, 50)
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, lambda: recommend_prompt_variant(limit=limit))
    await update.message.reply_text(report, parse_mode="HTML", disable_web_page_preview=True)


async def prompthealth_command(update, context):
    """Admin readout for prompt size/balance diagnostics."""
    user_id = update.effective_user.id
    if not ADMIN_IDS or user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML"
        )
        return
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, format_prompt_health_report)
    await update.message.reply_text(report, parse_mode="HTML", disable_web_page_preview=True)


async def pdf_command(update, context):
    """Генерирует и отправляет PDF из кэша для указанного видео."""
    user_id = update.effective_user.id
    if not ADMIN_IDS or user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML")
        return

    args = context.args
    arg  = args[0].strip() if args else ""

    # Пробуем вытащить video_id из reply-сообщения
    if not arg:
        reply = update.message.reply_to_message
        if reply and reply.text:
            arg = _extract_yt_id_from_text(reply.text) or ""

    if not arg:
        await update.message.reply_text(
            "📄 <b>PDF из кэша</b>\n\n"
            "Передайте ссылку или video_id:\n"
            "<code>/pdf https://youtu.be/VIDEO_ID</code>\n"
            "<code>/pdf VIDEO_ID</code>\n\n"
            "Или ответьте этой командой на сообщение с ссылкой.",
            parse_mode="HTML")
        return

    video_id = _extract_yt_id_from_text(arg) or arg

    msg = await update.message.reply_text("⏳ Ищу в кэше и генерирую PDF…")

    rec = await adb_get(video_id)
    if not rec:
        await safe_edit_text(msg, f"⚠️ Видео <code>{video_id}</code> не найдено в кэше.", parse_mode="HTML")
        return

    ai_data = rec.get("ai_data")
    if not ai_data:
        await safe_edit_text(msg, "⚠️ В кэше нет AI-данных для этого видео. Обработайте видео заново.")
        return

    telegraph_url     = rec.get("telegraph_url", "")
    study_tg_url      = rec.get("study_tg_url", "")
    reflection_tg_url = rec.get("reflection_tg_url", "")
    terms_tg_url      = rec.get("terms_tg_url", "")

    pdf_urls = {}
    if telegraph_url:     pdf_urls["synopsis"]   = telegraph_url
    if study_tg_url:      pdf_urls["study"]      = study_tg_url
    if reflection_tg_url: pdf_urls["reflection"] = reflection_tg_url
    if terms_tg_url:      pdf_urls["terms"]      = terms_tg_url

    if not pdf_urls:
        await safe_edit_text(msg, "⚠️ Нет Telegraph-страниц для сборки PDF. Обработайте видео заново.")
        return

    title     = normalize_title_text(ai_data.get("real_title") or "") or video_id
    # ai_data не содержит поля "performer" — оно хранится в short_trims, но не в video_cache.
    # Единственный надёжный источник автора — real_author из AI-анализа.
    performer = normalize_author_name(ai_data.get("real_author") or "") or ""
    duration  = ai_data.get("duration", 0) or 0
    dur_str   = format_timestamp(duration) if duration else ""

    try:
        from services.pdf_generator import generate_sermon_pdf_async
        pdf_path = str(DOWNLOAD_DIR / f"{video_id}_cmd.pdf")
        result   = await generate_sermon_pdf_async(
            output_path  = pdf_path,
            title        = title,
            performer    = performer,
            duration_str = dur_str,
            urls         = pdf_urls,
        )
        if result and Path(result).exists():
            # AUDIT-V2-PDF: performer="" → leading " — " в имени файла
            _safe_perf = (performer or "").strip()
            _safe_titl = (title or "").strip() or "Без названия"
            filename = f"{_safe_perf} — {_safe_titl}.pdf" if _safe_perf else f"{_safe_titl}.pdf"
            with open(result, "rb") as f:
                await update.message.reply_document(
                    document   = f,
                    filename   = filename,
                    caption    = f"📄 <b>PDF</b>: {html_mod.escape(title)}",
                    parse_mode = "HTML",
                )
            try: Path(result).unlink()
            except Exception: pass
            await msg.delete()
        else:
            await safe_edit_text(msg, "❌ PDF не удалось сгенерировать. Проверьте pdf_generator.")
    except ImportError:
        await safe_edit_text(msg, "❌ pdf_generator не установлен/не найден рядом с bot.py.")
    except Exception as e:
        logger.warning(f"PDF команда ошибка: {e}")
        await safe_edit_text(msg, f"❌ Ошибка генерации PDF: {_mask(str(e))[:200]}")


async def stop_command(update, context):
    """Останавливает бота. Только для администраторов."""
    user_id = update.effective_user.id
    if not ADMIN_IDS or user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML"
        )
        return
    # V3-P0: нормальный /stop больше не делает os._exit() как основной путь.
    # Команда ставит флаг, main.py выходит из polling-loop, async context PTB
    # корректно закрывает updater/application, а run_bot() НЕ перезапускает процесс.
    # Жёсткий выход оставлен только как аварийный env-fallback для хостингов.
    await update.message.reply_text("🛑 Останавливаю бота gracefully...")
    logger.info(f"Stop command от admin {user_id} — graceful shutdown requested")
    app = context.application
    app.bot_data["stop_requested"] = True

    # Авто-exit для локального режима: Flask отключён → main thread = run_bot(),
    # после stop_requested run_bot() выходит и процесс завершается сам.
    # Но если Flask включён, процесс висит → нужен force exit.
    _force = os.getenv("FORCE_EXIT_ON_STOP", "").strip().lower() in {"1", "true", "yes", "on"}
    _local = os.getenv("DISABLE_HEALTH_CHECK", "").strip().lower() in {"1", "true", "yes", "on"}
    if _force or (not _local and os.getenv("PORT")):
        async def _force_exit_fallback():
            await asyncio.sleep(5)
            logger.warning("FORCE_EXIT_ON_STOP=1 — аварийный os._exit(0) после grace period")
            os._exit(0)

        asyncio.create_task(_force_exit_fallback())


async def handle_message(update, context):
    # AUDIT M20: отбиваем сообщения от других ботов
    if update.effective_user and update.effective_user.is_bot:
        return

    text = update.message.text.strip()
    if not is_media_url(text):
        await update.message.reply_text("🤔 Отправьте ссылку на видео или плейлист.")
        return

    # ── Проверка лимитов ──────────────────────────────────────
    user_id = update.effective_user.id
    is_vip  = user_id in WHITELIST_IDS

    url = extract_media_url(text)
    if not url:
        await update.message.reply_text("❌ Не удалось распознать ссылку.")
        return
    if not url.startswith("http"):
        url = "https://" + url

    if is_playlist_url(text):
        # Плейлист сам резервирует лимит перед каждым видео; не списываем отдельный
        # слот за сам факт отправки playlist URL.
        await handle_playlist(url, update, context, user_id=user_id)
    else:
        # PART5: check+reserve под per-user async lock. Раньше check и update были
        # разделены, поэтому два параллельных запроса могли одновременно пройти лимит.
        if not is_vip:
            allowed, reason = await areserve_rate_limit(user_id)
            if not allowed:
                await update.message.reply_text(reason)
                return
        label = " 👑" if is_vip else ""
        msg   = await update.message.reply_text(f"⏳ Обрабатываю...{label}")
        _vid_id_hint = _extract_yt_id_from_text(url) or url
        _vlock = _get_video_lock(_vid_id_hint)
        if _vlock.locked():
            logger.info(
                f"Video {_vid_id_hint}: параллельный запрос — жду завершения первого..."
            )
        _lock_acquired = False
        try:
            # V3-P0: timeout только на ОЖИДАНИЕ чужой обработки этого же video_id.
            # Сам process_single_video может длиться дольше 5 минут на длинном видео,
            # поэтому не оборачиваем весь pipeline в asyncio.timeout().
            _lock_timeout = float(os.getenv("VIDEO_LOCK_WAIT_TIMEOUT_SEC", "300"))
            await asyncio.wait_for(_vlock.acquire(), timeout=_lock_timeout)
            _lock_acquired = True
            await process_single_video(url, update, msg, context=context)
        except asyncio.TimeoutError:
            logger.error(
                "Video lock timeout для %s после %.0fs ожидания",
                _vid_id_hint, _lock_timeout,
            )
            try:
                await msg.edit_text("⚠️ Это видео уже обрабатывается слишком долго. Попробуйте позже.")
            except Exception:
                await update.message.reply_text("⚠️ Это видео уже обрабатывается слишком долго. Попробуйте позже.")
            return
        finally:
            if _lock_acquired:
                try:
                    if _vlock.locked():
                        _vlock.release()
                    else:
                        logger.warning("Video lock %s уже был освобождён до release", _vid_id_hint)
                except RuntimeError:
                    logger.warning("Video lock %s release вызвал RuntimeError", _vid_id_hint)
            _release_video_lock(_vid_id_hint, _vlock)
        # PART5: rate-limit уже зарезервирован до обработки через areserve_rate_limit().
        try:
            await msg.delete()
        except Exception:
            pass


# ─── Запуск ──────────────────────────────────────────────────


def _archive_parse_limit(args, default: int = 7) -> int:
    if not args:
        return default
    try:
        return max(1, min(int(str(args[-1]).strip()), 20))
    except (TypeError, ValueError):
        return default


def _archive_format_records(records: list[dict], *, title: str) -> str:
    if not records:
        return f"📚 <b>{html_mod.escape(title)}</b>\n\nНичего не найдено."
    parts = [f"📚 <b>{html_mod.escape(title)}</b>", ""]
    for i, r in enumerate(records, 1):
        name = html_mod.escape(r.get("title") or "Без названия")
        author = html_mod.escape(r.get("author") or "Автор не указан")
        status = html_mod.escape(r.get("publication_status") or "unknown")
        parts.append(f"<b>{i}. {name}</b>")
        parts.append(f"👤 {author} · <code>{status}</code>")
        links = []
        for label, key in (("YouTube", "youtube_url"), ("Конспект", "synopsis_url"), ("Разбор", "study_url"), ("Размышление", "reflection_url")):
            url = r.get(key) or ""
            if url:
                links.append(f'<a href="{html_mod.escape(url)}">{label}</a>')
        if links:
            parts.append(" · ".join(links))
        if r.get("publication_warning"):
            parts.append("⚠️ " + html_mod.escape(str(r.get("publication_warning"))))
        parts.append("")
    text = "\n".join(parts).strip()
    if len(text) > 3900:
        text = text[:3850] + "\n\n…обрезано, уточните запрос."
    return text


async def archive_command(update, context):
    """Shows latest generated Telegraph pages from the durable archive."""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML")
        return
    limit = _archive_parse_limit(context.args, 7)
    records = await aquery_generated_pages(limit=limit)
    text = _archive_format_records(records, title=f"Последние публикации ({limit})")
    text += f"\n\n📁 Папка архива: <code>{html_mod.escape(str(ARCHIVE_DIR))}</code>"
    await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)


async def lastpages_command(update, context):
    """Alias for /archive."""
    await archive_command(update, context)


async def search_archive_command(update, context):
    """Search generated pages archive by free text."""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML")
        return
    query = " ".join(context.args or []).strip()
    if not query:
        await update.message.reply_text("🔎 Использование: <code>/search молитва</code>", parse_mode="HTML")
        return
    records = await aquery_generated_pages(limit=10, query=query)
    await update.message.reply_text(
        _archive_format_records(records, title=f"Поиск: {query}"),
        parse_mode="HTML", disable_web_page_preview=True,
    )


async def author_archive_command(update, context):
    """Search archive by author."""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML")
        return
    author = " ".join(context.args or []).strip()
    if not author:
        await update.message.reply_text("👤 Использование: <code>/author Джон МакАртур</code>", parse_mode="HTML")
        return
    records = await aquery_generated_pages(limit=10, author=author)
    await update.message.reply_text(
        _archive_format_records(records, title=f"Автор: {author}"),
        parse_mode="HTML", disable_web_page_preview=True,
    )


async def scripture_archive_command(update, context):
    """Search archive by scripture reference."""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML")
        return
    ref = " ".join(context.args or []).strip()
    if not ref:
        await update.message.reply_text("📖 Использование: <code>/scripture Исаия 53</code>", parse_mode="HTML")
        return
    records = await aquery_generated_pages(limit=10, scripture=ref)
    await update.message.reply_text(
        _archive_format_records(records, title=f"Писание: {ref}"),
        parse_mode="HTML", disable_web_page_preview=True,
    )


async def repairpage_command(update, context):
    """Repair Telegraph pages using current deterministic postprocess, without Gemini calls."""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML")
        return
    target = " ".join(context.args or []).strip()
    if not target:
        await update.message.reply_text(
            "🛠 Использование:\n"
            "<code>/repairpage https://telegra.ph/...</code>\n"
            "<code>/repairpage VIDEO_ID</code>\n"
            "<code>/repairpage last</code>\n\n"
            "Gemini не вызывается: только fetch → postprocess → editPage.",
            parse_mode="HTML",
        )
        return

    if target.lower().startswith("recent"):
        parts = target.split()
        limit = _archive_parse_limit(parts[1:], 5)
        records = await aquery_generated_pages(limit=limit)
        batch = await repair_generated_page_records(records, limit=limit)
        for item in batch:
            await aupdate_generated_page_repair_status(
                item.video_id,
                changed_pages=sum(1 for r in item.results if r.changed),
                errors=[r.error for r in item.results if r.error],
            )
        text = html_mod.escape(format_batch_repair_results(batch))
        if len(text) > 3900:
            text = text[:3850] + "\n…обрезано"
        await update.message.reply_text(f"<pre>{text}</pre>", parse_mode="HTML", disable_web_page_preview=True)
        return

    if telegraph_path_from_url(target):
        results = [await repair_telegraph_page_url(target)]
    else:
        if target.lower() == "last":
            records = await aquery_generated_pages(limit=1)
            record = records[0] if records else None
        else:
            record = await aget_generated_page_record(target)
            if record is None:
                matches = await aquery_generated_pages(limit=1, query=target)
                record = matches[0] if matches else None
        if not record:
            await update.message.reply_text("⚠️ Не найдено в архиве. Попробуйте URL Telegraph или точный video_id.")
            return
        if not collect_record_page_urls(record):
            await update.message.reply_text("⚠️ В записи архива нет Telegraph-ссылок для ремонта.")
            return
        results = await repair_generated_page_record(record)
        await aupdate_generated_page_repair_status(
            str(record.get("video_id") or ""),
            changed_pages=sum(1 for r in results if r.changed),
            errors=[r.error for r in results if r.error],
        )

    text = html_mod.escape(format_repair_results(results))
    if len(text) > 3900:
        text = text[:3850] + "\n…обрезано"
    await update.message.reply_text(f"<pre>{text}</pre>", parse_mode="HTML", disable_web_page_preview=True)


async def repairrecent_command(update, context):
    """Repair recent archive records without Gemini calls."""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML")
        return
    limit = _archive_parse_limit(context.args, 5)
    records = await aquery_generated_pages(limit=limit)
    batch = await repair_generated_page_records(records, limit=limit)
    for item in batch:
        await aupdate_generated_page_repair_status(
            item.video_id,
            changed_pages=sum(1 for r in item.results if r.changed),
            errors=[r.error for r in item.results if r.error],
        )
    text = html_mod.escape(format_batch_repair_results(batch))
    if len(text) > 3900:
        text = text[:3850] + "\n…обрезано"
    await update.message.reply_text(f"<pre>{text}</pre>", parse_mode="HTML", disable_web_page_preview=True)


async def _resolve_segment_source(target: str) -> tuple[str, dict | None, dict | None]:
    """Resolve target into (video_id, cache_record, archive_record)."""
    target = str(target or "").strip()
    archive_record = None
    if target.lower() == "last":
        records = await aquery_generated_pages(limit=1)
        archive_record = records[0] if records else None
        target = str((archive_record or {}).get("video_id") or "")
    cache = await adb_get(target) if target else None
    if archive_record is None and target:
        archive_record = await aget_generated_page_record(target)
    return target, cache, archive_record


def _segments_from_cache(cache: dict | None, archive_record: dict | None = None) -> tuple[list, str, int, str, str]:
    ai = (cache or {}).get("ai_data") or {}
    duration = int((ai or {}).get("duration") or (archive_record or {}).get("duration") or 0)
    title = (ai or {}).get("real_title") or (archive_record or {}).get("title") or "Без названия"
    fmt = (ai or {}).get("format") or (archive_record or {}).get("format") or ""
    segments = build_segments_from_timestamps((ai or {}).get("timestamps", ""), duration, format_name=fmt)
    status = (ai or {}).get("segments_status") or (archive_record or {}).get("segments_status") or "complete"
    return segments, title, duration, fmt, status


def _build_segments_keyboard(video_id: str, segments: list, *, page: int = 0, per_page: int = 12) -> InlineKeyboardMarkup | None:
    """Build paginated inline keyboard for segment rendering."""
    if not segments:
        return None
    total_pages = max(1, (len(segments) + per_page - 1) // per_page)
    page = max(0, min(int(page or 0), total_pages - 1))
    visible = segments[page * per_page:page * per_page + per_page]
    buttons = []
    row = []
    for segment in visible:
        row.append(InlineKeyboardButton(f"🎬 {segment.index}", callback_data=f"segcut:{video_id}:{segment.index}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️", callback_data=f"segpage:{video_id}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("➡️", callback_data=f"segpage:{video_id}:{page + 1}"))
        buttons.append(nav)
    return InlineKeyboardMarkup(buttons) if buttons else None


def _format_segments_page_text(video_id: str, title: str, fmt: str, segments: list, *, page: int = 0, per_page: int = 12) -> str:
    total_pages = max(1, (len(segments) + per_page - 1) // per_page) if segments else 1
    page = max(0, min(int(page or 0), total_pages - 1))
    visible = segments[page * per_page:page * per_page + per_page]
    text = format_segments_text(visible, title=f"Сегменты: {title} ({fmt or 'format?'}) · стр. {page + 1}/{total_pages}")
    text += f"\n\nВырезать: /cutseg {video_id} N"
    if total_pages > 1:
        text += "\nЛистайте кнопками ⬅️/➡️."
    return text


async def segments_command(update, context):
    """List selectable Q&A/theme segments from cached AI timestamps."""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML")
        return
    if not await asettings_get("segments"):
        await update.message.reply_text("🧩 Сегменты выключены в настройках. Включите: /settings → 🧩 Сегменты")
        return
    target = (context.args[0].strip() if context.args else "last")
    video_id, cache, archive_record = await _resolve_segment_source(target)
    if not cache:
        await update.message.reply_text("⚠️ Нужна запись в video_cache с ai_data. Используйте video_id недавней обработки или `last`.")
        return
    segments, title, _duration, fmt, seg_status = _segments_from_cache(cache, archive_record)
    text = format_segments_text(segments, title=f"Сегменты: {title} ({fmt or 'format?'})")
    if seg_status == "partial":
        text = "⚠️ Сегменты построены по неполной сетке таймкодов.\n\n" + text
    text += f"\n\nВырезать: /cutseg {video_id} N"
    safe = html_mod.escape(text)
    if len(safe) > 3900:
        safe = safe[:3850] + "\n…обрезано"
    buttons = []
    row = []
    for s in segments[:12]:
        row.append(InlineKeyboardButton(f"🎬 {s.index}", callback_data=f"segcut:{video_id}:{s.index}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    markup = InlineKeyboardMarkup(buttons) if buttons else None
    await update.message.reply_text(
        f"<pre>{safe}</pre>",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=markup,
    )


async def cutseg_command(update, context):
    """Render and send deterministic segment(s) by timestamp boundary."""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML")
        return
    if len(context.args or []) < 2:
        await update.message.reply_text(
            "🎬 Использование: <code>/cutseg VIDEO_ID N</code>, "
            "<code>/cutseg VIDEO_ID 1,3,5</code> или <code>/cutseg last N</code>",
            parse_mode="HTML",
        )
        return
    if not await asettings_get("segments"):
        await update.message.reply_text("🧩 Сегменты выключены в настройках. Включите: /settings → 🧩 Сегменты")
        return
    if not await asettings_get("segments_render"):
        await update.message.reply_text("🎞 Рендер сегментов выключен в настройках. Включите: /settings → 🧩 Сегменты")
        return
    target = context.args[0].strip()
    selection = context.args[1].strip()
    video_id, cache, archive_record = await _resolve_segment_source(target)
    if not cache:
        await update.message.reply_text("⚠️ Нужна запись в video_cache с ai_data для нарезки.")
        return
    segments, title, _duration, fmt, _seg_status = _segments_from_cache(cache, archive_record)
    indexes = parse_segment_selection(selection, len(segments), max_count=5)
    if not indexes:
        await update.message.reply_text("⚠️ Сегмент не найден. Посмотрите список: <code>/segments VIDEO_ID</code>", parse_mode="HTML")
        return
    if len(indexes) > 1 and not await asettings_get("segments_batch_render"):
        await update.message.reply_text("📦 Пакетная нарезка выключена. Включите: /settings → 🧩 Сегменты → 📦 Пакетная нарезка")
        return
    source_url = (cache or {}).get("url") or (archive_record or {}).get("source_url") or (archive_record or {}).get("youtube_url") or ""
    if not source_url:
        await update.message.reply_text("⚠️ Нет source_url для скачивания видео.")
        return
    _render_token = await try_acquire_render_lock(render_lock_key("segment", video_id))
    if _render_token is None:
        await update.message.reply_text("⏳ Для этого видео уже идёт рендер сегмента. Дождитесь окончания или выберите позже.")
        return
    msg = await update.message.reply_text(f"🎬 Готовлю видео и рендерю сегменты: {', '.join(map(str, indexes))}")
    video_path = None
    clip_paths: list[Path] = []
    sub_paths: list[Path] = []
    sent = 0
    try:
        video_path = await download_video_for_shorts(source_url, video_id)
        if not video_path:
            await msg.edit_text("❌ Не удалось скачать видео для сегмента.")
            return
        for idx in indexes:
            segment = get_segment_by_index(segments, idx)
            if not segment:
                continue
            clip_path = DOWNLOAD_DIR / f"{video_id}_segment_{segment.index}_{uuid.uuid4().hex[:6]}.mp4"
            clip_paths.append(clip_path)
            await safe_edit_text(msg,
                f"🎬 Рендерю {segment.index}/{len(segments)}: {html_mod.escape(segment.title[:120])}\n"
                f"{seconds_to_timestamp(segment.start)}–{seconds_to_timestamp(segment.end)}"
            )
            ok = await render_clip(video_path, clip_path, segment.start, segment.end)
            if not ok or not clip_path.exists():
                await update.message.reply_text(f"❌ Не удалось вырезать сегмент {segment.index}.")
                continue
            final_clip_path = clip_path
            if await asettings_get("segments_subtitles"):
                if not HAS_FASTER_WHISPER:
                    logger.warning("Segments subtitles: faster-whisper не установлен, отправляю без субтитров")
                else:
                    sub_path = DOWNLOAD_DIR / f"{video_id}_segment_{segment.index}_{uuid.uuid4().hex[:6]}_sub.mp4"
                    sub_paths.append(sub_path)
                    try:
                        sub_segments = await transcribe_short_clip(clip_path, ai_data=(cache or {}).get("ai_data") or {})
                        if sub_segments:
                            sub_ok = await burn_subtitles_into_short(clip_path, sub_path, sub_segments)
                            if sub_ok and sub_path.exists():
                                final_clip_path = sub_path
                        else:
                            logger.warning("Segments subtitles: пустая транскрипция для segment %s", segment.index)
                    except Exception as _seg_sub_err:
                        logger.warning("Segments subtitles failed for %s: %s", segment.index, _seg_sub_err)
            size_mb = final_clip_path.stat().st_size / (1024 * 1024)
            if size_mb > MAX_FILE_SIZE_MB:
                await update.message.reply_text(f"❌ Сегмент {segment.index} слишком большой: {size_mb:.0f}MB.")
                continue
            caption = (
                f"🎬 <b>{html_mod.escape(title)}</b>\n"
                f"{seconds_to_timestamp(segment.start)}–{seconds_to_timestamp(segment.end)} "
                f"({seconds_to_timestamp(segment.duration)})\n"
                f"<b>{html_mod.escape(segment.title)}</b>"
            )
            if len(caption) > 1024:
                caption = (
                    f"🎬 <b>{html_mod.escape(title[:120])}</b>\n"
                    f"{seconds_to_timestamp(segment.start)}–{seconds_to_timestamp(segment.end)} "
                    f"({seconds_to_timestamp(segment.duration)})\n"
                    f"<b>{html_mod.escape(segment.title[:220])}</b>"
                )
            with open(final_clip_path, "rb") as vf:
                await update.message.reply_video(
                    video=vf,
                    caption=caption,
                    duration=int(segment.duration),
                    supports_streaming=True,
                    parse_mode="HTML",
                    write_timeout=300,
                    read_timeout=300,
                    connect_timeout=60,
                )
            sent += 1
        await safe_edit_text(msg, f"✅ Отправлено сегментов: {sent}/{len(indexes)}.")
    except Exception as exc:
        logger.warning("cutseg failed: %s", exc, exc_info=True)
        await safe_edit_text(msg, f"❌ Ошибка нарезки: {_mask(str(exc))[:180]}")
    finally:
        for clip_path in clip_paths:
            try:
                clip_path.unlink(missing_ok=True)
            except Exception:
                pass
        for sub_path in sub_paths:
            try:
                sub_path.unlink(missing_ok=True)
            except Exception:
                pass
        await release_render_lock(_render_token)


async def archivefile_command(update, context):
    """Send human-readable archive export files."""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML")
        return
    kind = (context.args[0].strip() if context.args else "latest")
    path = get_archive_export_path(kind)
    if not path or not path.exists():
        await update.message.reply_text(
            "⚠️ Файл архива не найден. Варианты: <code>latest</code>, <code>all</code>, <code>jsonl</code>, <code>readme</code>, <code>sqlite</code>",
            parse_mode="HTML",
        )
        return
    with open(path, "rb") as f:
        await update.message.reply_document(
            document=f,
            filename=path.name,
            caption=f"📁 Archive export: {path.name}",
            write_timeout=180,
            read_timeout=180,
            connect_timeout=60,
        )


async def segmentfile_command(update, context):
    """Send segment plan export for a cached/archived video."""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(f"⛔ Нет доступа.\nВаш Telegram ID: <code>{user_id}</code>", parse_mode="HTML")
        return
    target = (context.args[0].strip() if context.args else "last")
    kind = (context.args[1].strip().lower() if len(context.args or []) > 1 else "md")
    if kind not in {"md", "json"}:
        kind = "md"
    video_id, cache, archive_record = await _resolve_segment_source(target)
    if not video_id:
        await update.message.reply_text("⚠️ Не найден video_id. Используйте <code>/segmentfile last</code> или <code>/segmentfile VIDEO_ID</code>.", parse_mode="HTML")
        return
    paths = get_segment_export_paths(video_id)
    path = Path(paths.get(kind, ""))
    if (not path.exists()) and cache:
        ai = (cache or {}).get("ai_data") or {}
        try:
            await asave_segment_plan_export(
                video_id=video_id,
                title=(ai.get("real_title") or (archive_record or {}).get("title") or video_id),
                author=(ai.get("real_author") or (archive_record or {}).get("author") or ""),
                timestamps=ai.get("timestamps", ""),
                duration=int(ai.get("duration") or (archive_record or {}).get("duration") or 0),
                format_name=(ai.get("format") or (archive_record or {}).get("format") or ""),
            )
        except Exception as exc:
            logger.warning("segmentfile export rebuild failed: %s", exc)
    if not path.exists():
        await update.message.reply_text("⚠️ Файл сегментов не найден. Нужен video_cache с ai_data или уже созданный export.")
        return
    with open(path, "rb") as f:
        await update.message.reply_document(
            document=f,
            filename=path.name,
            caption=f"🧩 Segment export: {path.name}",
            write_timeout=180,
            read_timeout=180,
            connect_timeout=60,
        )
