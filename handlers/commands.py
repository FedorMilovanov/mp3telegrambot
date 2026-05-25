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
    MAX_PLAYLIST_SIZE, DB_PATH,
    areserve_rate_limit,  # AUDIT M4/PART5
)
from core.utils import (
    is_media_url, is_playlist_url,
    extract_media_url, format_timestamp,               # FIX #9
)
from core.text_utils import normalize_author_name, normalize_title_text  # FIX #9
from pipelines.main_pipeline import process_single_video
from pipelines.playlist import handle_playlist

import asyncio
import logging
import os        # FIX #9
import re        # FIX #9
import sqlite3   # FIX #9
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
                "/resetcache all — очистить весь кэш"
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
        f"/help  — Справка"
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
            f"✅ Кэш сброшен: `{video_id}`\nТеперь отправь ссылку заново.",
            parse_mode="Markdown")
    else:
        await update.message.reply_text(
            f"⚠️ Не найдено в кэше: `{video_id}`",
            parse_mode="Markdown")


async def reset_cache_command(update, context):
    """Удаляет кэш видео — для принудительной переобработки."""

    user_id = update.effective_user.id
    if not ADMIN_IDS or user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"⛔ Нет доступа.\n"
            f"Ваш Telegram ID: `{user_id}`\n"
            f"Добавьте его в переменную ADMIN_IDS на Render.", parse_mode="Markdown")
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
            f"🗑 Весь кэш удалён: {rows} записей.", parse_mode="Markdown")
        return

    # ── /resetcache <url или video_id> ────────────────────────
    video_id = _extract_yt_id_from_text(arg) or arg
    await _do_resetcache_one(video_id, update)


async def pdf_command(update, context):
    """Генерирует и отправляет PDF из кэша для указанного видео."""
    user_id = update.effective_user.id
    if not ADMIN_IDS or user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"⛔ Нет доступа.\nВаш Telegram ID: `{user_id}`", parse_mode="Markdown")
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
        await msg.edit_text(f"⚠️ Видео <code>{video_id}</code> не найдено в кэше.", parse_mode="HTML")
        return

    ai_data = rec.get("ai_data")
    if not ai_data:
        await msg.edit_text("⚠️ В кэше нет AI-данных для этого видео. Обработайте видео заново.")
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
        await msg.edit_text("⚠️ Нет Telegraph-страниц для сборки PDF. Обработайте видео заново.")
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
            await msg.edit_text("❌ PDF не удалось сгенерировать. Проверьте pdf_generator.")
    except ImportError:
        await msg.edit_text("❌ pdf_generator не установлен/не найден рядом с bot.py.")
    except Exception as e:
        logger.warning(f"PDF команда ошибка: {e}")
        await msg.edit_text(f"❌ Ошибка генерации PDF: {e}")


async def stop_command(update, context):
    """Останавливает бота. Только для администраторов."""
    user_id = update.effective_user.id
    if not ADMIN_IDS or user_id not in ADMIN_IDS:
        await update.message.reply_text(
            f"⛔ Нет доступа.\nВаш Telegram ID: `{user_id}`", parse_mode="Markdown"
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

    if os.getenv("FORCE_EXIT_ON_STOP", "0").strip().lower() in {"1", "true", "yes", "on"}:
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
            ok = await process_single_video(url, update, msg, context=context)
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

