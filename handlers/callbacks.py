#!/usr/bin/env python3
# AUDIT-V2-ADMIN: ADMIN_IDS bypass fixed
"""
Handlers — settings keyboard, handle_callback.
Извлечено из bot.py строки 8365–8722.

AUDIT FIX C1: убран безусловный query.answer() в начале — он закрывал
callback_query и все последующие query.answer(text, show_alert=True)
молча игнорировались Telegram'ом. Теперь каждая ветка САМА вызывает
answer ровно один раз.

AUDIT FIX M4: shorts_speed_get() и settings_get_all() обёрнуты в
run_in_executor — больше не блокируют event loop.

AUDIT FIX L9: re.sub вместо некорректного lstrip("✅☑️️ ") (☑️ = 2 кодпоинта).
"""
from core.globals import (
    HAS_GEMINI, DOWNLOAD_DIR, DB_PATH,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from core.database import (
    _db_conn,
    asettings_get, asettings_set, asettings_get_all,
    shorts_speed_get, shorts_speed_set, shorts_speed_cycle,
    ashorts_speed_get, ashorts_speed_cycle,        # AUDIT M4
    short_trim_get, short_trim_save,
    SETTINGS_DEFAULTS, SETTINGS_LABELS,
    ADMIN_IDS,
    settings_get_all,
    SETTINGS_GROUPS,                                # AUDIT M19 (перенесено сюда)
)
from converters.md_telegraph import visible_length, safe_trim_caption
from core.utils import mask_api_key as _mask
from core.progress import safe_edit_text
from services.shorts_video import build_short_caption, render_short_clip

import asyncio
import json
import logging
import re                                            # AUDIT L9
import sqlite3
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# AUDIT L9: вместо некорректного lstrip("✅☑️️ ") — re.sub по классу.
# ☑️ = U+2611 + U+FE0F (2 кодпоинта), lstrip удалял посимвольно.
_BTN_ICON_PREFIX_RE = re.compile(r"^[✅☑⚡🚫⬅➡⏪⏭✂️\s\u2611\u2705\uFE0F]+")


def _strip_btn_icon(label: str) -> str:
    """Убирает иконку (любую комбинацию emoji + selector) в начале метки кнопки."""
    return _BTN_ICON_PREFIX_RE.sub("", label).strip()


def _build_settings_keyboard(
    settings: dict[str, bool] | None = None,
    speed: str | None = None,
) -> InlineKeyboardMarkup:
    """Строит клавиатуру настроек: по 2 кнопки в ряд для коротких, 1 для длинных.

    AUDIT M4: и settings, и speed теперь принимаются параметрами,
    чтобы вызывающий код мог прогрузить их через run_in_executor
    и не блокировать event loop.
    """
    if settings is None:
        # Fallback для совместимости (если кто-то вызовет синхронно)
        settings = settings_get_all()
    if speed is None:
        speed = shorts_speed_get()

    buttons: list[list[InlineKeyboardButton]] = []
    WIDE_THRESHOLD = 18

    for group_title, keys in SETTINGS_GROUPS:
        buttons.append([InlineKeyboardButton(f"─── {group_title} ───", callback_data="noop")])

        group_btns: list[InlineKeyboardButton] = []
        for key in keys:
            if key == "__speed__":
                if group_btns:
                    _flush_buttons(buttons, group_btns, WIDE_THRESHOLD)
                    group_btns = []
                buttons.append([InlineKeyboardButton(
                    f"⚡ Скорость: {speed}x",
                    callback_data="setting_speed",
                )])
            else:
                state = settings.get(key, SETTINGS_DEFAULTS.get(key, True))
                icon  = "✅" if state else "☑️"
                label = SETTINGS_LABELS.get(key, key)
                group_btns.append(InlineKeyboardButton(
                    f"{icon} {label}",
                    callback_data=f"setting:{key}",
                ))

        if group_btns:
            _flush_buttons(buttons, group_btns, WIDE_THRESHOLD)

    return InlineKeyboardMarkup(buttons)


def _flush_buttons(rows: list, btns: list, wide_threshold: int) -> None:
    """Укладывает кнопки: короткие по 2 в ряд, длинные — по одной."""
    i = 0
    while i < len(btns):
        pure = _strip_btn_icon(btns[i].text)
        if len(pure) > wide_threshold:
            rows.append([btns[i]])
            i += 1
        else:
            if i + 1 < len(btns):
                next_pure = _strip_btn_icon(btns[i + 1].text)
                if len(next_pure) <= wide_threshold:
                    rows.append([btns[i], btns[i + 1]])
                    i += 2
                    continue
            rows.append([btns[i]])
            i += 1


async def _build_keyboard_async() -> InlineKeyboardMarkup:
    """AUDIT M4: загружает настройки и скорость без блокировки event loop."""
    all_settings, speed = await asyncio.gather(
        asettings_get_all(),
        ashorts_speed_get(),
    )
    return _build_settings_keyboard(all_settings, speed)


async def settings_command(update, context):
    """Показывает панель настроек."""
    user_id = update.effective_user.id
    if not ADMIN_IDS or user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Настройки доступны только администраторам.")
        return
    keyboard = await _build_keyboard_async()
    await update.message.reply_text(
        "⚙️ <b>Настройки</b>\n\n"
        "Нажмите на пункт — он сразу переключится.\n"
        "<i>Shorts и Clips влияют только на видеофрагменты.</i>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def handle_callback(update, context) -> None:
    """AUDIT FIX C1: query.answer() вызывается ровно ОДИН раз в каждой ветке.

    Раньше безусловный await query.answer() в начале функции закрывал
    callback_query, и все последующие query.answer(text, show_alert=True)
    с текстом (включено/выключено, ⛔ Нет доступа, ✂️ перерезаю...) молча
    игнорировались Telegram'ом — пользователь не видел popup-уведомлений.
    """
    query = update.callback_query
    data = query.data or ""

    # ── Разделитель (нажатие игнорируется) ──────────────────────
    if data == "noop":
        await query.answer()
        return

    # ── Скорость Shorts (цикличное переключение) ─────────────
    if data == "setting_speed":
        user_id = update.effective_user.id
        if not ADMIN_IDS or user_id not in ADMIN_IDS:
            await query.answer("⛔ Нет доступа.", show_alert=True)
            return
        new_speed = await ashorts_speed_cycle()
        await query.answer(f"⚡ Скорость Shorts: {new_speed}x")
        try:
            await query.edit_message_reply_markup(reply_markup=await _build_keyboard_async())
        except Exception as e:
            logger.debug(f"edit_message_reply_markup (speed): {e}")
        return

    # ── Настройки (toggle bool) ───────────────────────────────
    if data.startswith("setting:"):
        user_id = update.effective_user.id
        if not ADMIN_IDS or user_id not in ADMIN_IDS:
            await query.answer("⛔ Нет доступа.", show_alert=True)
            return
        key = data.split(":", 1)[1]
        if key not in SETTINGS_DEFAULTS:
            # AUDIT FIX (продолжение C1): теперь это уведомление РЕАЛЬНО показывается
            await query.answer(f"⚠️ Неизвестная настройка: {key}", show_alert=True)
            return
        new_val = not await asettings_get(key)
        await asettings_set(key, new_val)
        state_text = "включено ✅" if new_val else "выключено ☑️"
        label = SETTINGS_LABELS.get(key, key)
        await query.answer(f"{label}: {state_text}")
        try:
            await query.edit_message_reply_markup(reply_markup=await _build_keyboard_async())
        except Exception as e:
            logger.debug(f"edit_message_reply_markup (toggle): {e}")
        return



    # ── Segment list pagination from /segments ─────────────────
    if data.startswith("segpage:"):
        user_id = update.effective_user.id
        if not ADMIN_IDS or user_id not in ADMIN_IDS:
            await query.answer("⛔ Нет доступа.", show_alert=True)
            return
        parts = data.split(":", 2)
        if len(parts) != 3:
            await query.answer("Неверная страница.", show_alert=True)
            return
        _, video_id, page_raw = parts
        try:
            page = int(page_raw)
        except ValueError:
            page = 0
        if not await asettings_get("segments"):
            await query.answer("🧩 Сегменты выключены.", show_alert=True)
            return
        try:
            from handlers.commands import _build_segments_keyboard, _format_segments_page_text, _html_pre_message, _resolve_segment_source, _segments_from_cache
            _vid, cache, archive_record = await _resolve_segment_source(video_id)
            if not cache:
                await query.answer("Нет video_cache с ai_data.", show_alert=True)
                return
            segments, title, _duration, fmt, _seg_status = _segments_from_cache(cache, archive_record)
            text = _format_segments_page_text(_vid, title, fmt, segments, page=page)
            safe = _html_pre_message(text)
            await query.answer(f"Страница {page + 1}")
            await query.edit_message_text(
                safe,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=_build_segments_keyboard(_vid, segments, page=page),
            )
        except Exception as exc:
            logger.warning("segpage callback failed: %s", exc, exc_info=True)
            await query.answer("Ошибка страницы сегментов.", show_alert=True)
        return

    # ── Segment cut from /segments inline buttons ─────────────
    if data.startswith("segcut:"):
        user_id = update.effective_user.id
        if not ADMIN_IDS or user_id not in ADMIN_IDS:
            await query.answer("⛔ Нет доступа.", show_alert=True)
            return
        parts = data.split(":", 2)
        if len(parts) != 3:
            await query.answer("Неверный сегмент.", show_alert=True)
            return
        _, video_id, idx = parts
        if not await asettings_get("segments"):
            await query.answer("🧩 Сегменты выключены.", show_alert=True)
            return
        if not await asettings_get("segments_render"):
            await query.answer("🎞 Рендер сегментов выключен.", show_alert=True)
            return
        await query.answer(f"🎬 Рендерю сегмент {idx}...")
        _render_token = None
        try:
            from core.render_locks import release_render_lock, render_lock_key, try_acquire_render_lock
            from core.segment_planner import get_segment_by_index, seconds_to_timestamp
            from handlers.commands import _resolve_segment_source, _segments_from_cache
            from services.segment_render import render_and_send_segment
            import html as _html

            _vid, cache, archive_record = await _resolve_segment_source(video_id)
            if not cache:
                await query.message.reply_text("⚠️ Нужна запись в video_cache с ai_data для нарезки.")
                return
            segments, title, _duration, _fmt, _seg_status = _segments_from_cache(cache, archive_record)
            segment = get_segment_by_index(segments, idx)
            if not segment:
                await query.message.reply_text("⚠️ Сегмент не найден.")
                return
            source_url = (cache or {}).get("url") or (archive_record or {}).get("source_url") or (archive_record or {}).get("youtube_url") or ""
            if not source_url:
                await query.message.reply_text("⚠️ Нет source_url для скачивания видео.")
                return
            _render_token = await try_acquire_render_lock(render_lock_key("segment", _vid))
            if _render_token is None:
                await query.message.reply_text("⏳ Для этого видео уже идёт рендер сегмента. Дождитесь окончания.")
                return
            # FIX AUDIT R4: reply_text без parse_mode — plain text, escape
            # здесь показывал юзеру буквальные &amp; в заголовке сегмента.
            msg = await query.message.reply_text(
                f"🎬 Рендерю сегмент {segment.index}: {segment.title[:120]}\n"
                f"{seconds_to_timestamp(segment.start)}–{seconds_to_timestamp(segment.end)}"
            )
            ai_data = (cache or {}).get("ai_data") or {}
            ok = await render_and_send_segment(
                reply_target=query.message,
                status_msg=msg,
                video_id=_vid,
                source_url=source_url,
                segment=segment,
                title=title,
                total_segments=len(segments),
                ai_data=ai_data,
            )
            await safe_edit_text(msg, "✅ Сегмент отправлен." if ok else "❌ Не удалось скачать видео или отправить сегмент.")
        except Exception as exc:
            logger.warning("segcut callback failed: %s", exc, exc_info=True)
            try:
                await query.message.reply_text(f"❌ Ошибка сегмента: {_mask(str(exc))[:160]}")
            except Exception:
                pass
        finally:
            if _render_token is not None:
                try:
                    from core.render_locks import release_render_lock
                    await release_render_lock(_render_token)
                except Exception:
                    pass
        return

    # ── Short trim (подрезка начала/конца) ──────────────────────
    if data.startswith("strim:"):
        parts = data.split(":", 2)
        if len(parts) != 3:
            await query.answer("Неверный формат.", show_alert=True)
            return
        _, mode, short_id = parts

        # AUDIT M4: short_trim_get — синхронный SQLite, в executor
        loop = asyncio.get_running_loop()
        rec = await loop.run_in_executor(None, short_trim_get, short_id)
        if not rec:
            await query.answer("Данные Short не найдены — возможно видео устарело.", show_alert=True)
            return
        # FIX AUDIT R4: отсутствие исходника больше не мгновенный отказ —
        # nosub он вообще не нужен, а для ретрима перекачиваем по yt_url ниже.
        video_path = Path(rec["video_path"])

        start_s = rec["start_seconds"]
        end_s   = rec["end_seconds"]
        # AUDIT M5: ограничение по длительности оригинала
        source_duration = rec.get("source_duration", 0) or 0

        if mode == "nosub":
            await _handle_strim_nosub(query, rec, start_s, end_s)
            return

        if mode == "s10":
            start_s = max(0, start_s - 10)
            label = "Начало расширено -10 сек"
        elif mode == "e10":
            end_s = end_s + 10
            label = "Конец расширен +10 сек"
        elif mode == "e20":
            end_s = end_s + 20
            label = "Конец расширен +20 сек"
        else:
            await query.answer("Неизвестный режим.", show_alert=True)
            return

        # AUDIT M5: жёстко ограничиваем end_s длительностью исходника
        if source_duration:
            end_s = min(end_s, source_duration)

        if start_s >= end_s:
            await query.answer("⚠️ Начало не может быть позже конца.", show_alert=True)
            return

        await query.answer(f"✂️ {label}, перерезаю...")
        if not video_path.exists():
            # Исходник удалён после отправки шортов — перекачиваем по yt_url.
            yt_url = rec.get("yt_url", "")
            if not yt_url:
                await query.message.reply_text(
                    "⚠️ Исходное видео удалено и нет ссылки для повторного скачивания."
                )
                return
            _was_livedub = "original_video" in video_path.name or "livedub" in str(video_path).lower()
            _dl_note = (
                "⬇️ Переведённый исходник удалён — скачиваю оригинал (перерезка будет БЕЗ перевода)…"
                if _was_livedub else
                "⬇️ Исходник удалён — скачиваю заново для перерезки…"
            )
            status = await query.message.reply_text(_dl_note)
            from services.shorts_video import download_video_for_shorts
            import hashlib as _hashlib
            _m = re.match(r"^(.+?)_video\.[A-Za-z0-9]+$", video_path.name)
            _media_id = _m.group(1) if _m else "retrim_" + _hashlib.sha1(yt_url.encode()).hexdigest()[:12]
            new_src = await download_video_for_shorts(yt_url, _media_id)
            if not new_src:
                await safe_edit_text(status, "❌ Не удалось скачать исходное видео заново.")
                return
            await safe_edit_text(status, "✂️ Исходник скачан, перерезаю…")
            video_path = new_src
        await _handle_strim_retrim(query, rec, video_path, start_s, end_s, label, source_duration)
        return

    # ── Сброс всего кэша ─────────────────────────────────────
    if data == "resetcache:all":
        user_id = update.effective_user.id
        if not ADMIN_IDS or user_id not in ADMIN_IDS:
            await query.answer("⛔ Нет доступа.", show_alert=True)
            return
        await query.answer("🗑 Чистим...")
        loop = asyncio.get_running_loop()

        def _delete_all_cache():
            with _db_conn() as conn:
                r = conn.execute("DELETE FROM video_cache").rowcount
                conn.commit()
                return r
        rows = await loop.run_in_executor(None, _delete_all_cache)
        try:
            await query.edit_message_text(f"🗑 Весь кэш удалён: {rows} записей.")
        except Exception:
            pass
        return

    # ── Неизвестный action ─────────────────────────────────────
    if ":" not in data:
        await query.answer("⚠️ Неизвестное действие.", show_alert=True)
        return
    action, video_id = data.split(":", 1)
    logger.warning(f"handle_callback: необработанный action={action!r} video_id={video_id!r}")
    await query.answer("⚠️ Действие устарело или не поддерживается.", show_alert=True)


# ─── Подпрограммы strim ──────────────────────────────────────────

async def _handle_strim_nosub(query, rec: dict, start_s: int, end_s: int) -> None:
    """Отправить ранее сохранённую версию без субтитров."""
    nosub_path_str = rec.get("video_path_nosub", "")
    if not nosub_path_str:
        await query.answer("🚫 Версия без субтитров недоступна для этого клипа.", show_alert=True)
        return
    nosub_expiry = rec.get("nosub_expiry", 0)
    if nosub_expiry and int(time.time()) > nosub_expiry:
        await query.answer(
            "⏳ Срок хранения файла без субтитров истёк (24ч).\n"
            "Отправьте ссылку на видео заново — бот создаст новый клип.",
            show_alert=True,
        )
        return
    nosub_path = Path(nosub_path_str)
    if not nosub_path.exists():
        await query.answer("⏳ Файл без субтитров истёк — пришлите видео заново.", show_alert=True)
        return

    await query.answer("🚫 Отправляю без субтитров...")

    try:
        candidate = {}
        try:
            candidate = json.loads(rec.get("candidate_json", "{}"))
        except Exception:
            pass

        caption = build_short_caption(
            candidate=candidate,
            performer=rec.get("performer", ""),
            real_author=rec.get("real_author", ""),
            real_event=rec.get("real_event", ""),
            format_name=rec.get("format_name", ""),
            yt_url=rec.get("yt_url", ""),
            vk_url=rec.get("vk_url", ""),
            rutube_url=rec.get("rutube_url", ""),
        )
        # FIX AUDIT R4: лимит 1024 проверяем ПОСЛЕ добавления префикса —
        # иначе на длинных caption получали MEDIA_CAPTION_TOO_LONG.
        caption = f"🚫 Без субтитров\n\n{caption}"
        if visible_length(caption) > 1024:
            caption = safe_trim_caption(caption, 1024)

        # Path: file:// при local_mode
        await query.message.reply_video(
            video=nosub_path,
            caption=caption,
            duration=end_s - start_s,
            width=720,
            height=1280,
            parse_mode="HTML",
            write_timeout=120,
            read_timeout=120,
            connect_timeout=30,
        )
        # AUDIT M9 (баг 9): сам nosub_path НЕ удаляем — он может ещё пригодиться
        # для повторных нажатий, его удалит cleanup_nosub_files по TTL.
    except Exception as nosub_err:
        logger.warning(f"strim:nosub error: {nosub_err}")
        await query.message.reply_text(f"❌ Ошибка: {str(nosub_err)[:150]}")


async def _handle_strim_retrim(
    query, rec: dict, video_path: Path,
    start_s: int, end_s: int, label: str,
    source_duration: int,
) -> None:
    """Перерезать short по новым start_s/end_s и отправить."""
    candidate = {}
    try:
        candidate = json.loads(rec["candidate_json"])
    except Exception:
        pass
    candidate["start_seconds"]    = start_s
    candidate["end_seconds"]      = end_s
    candidate["duration_seconds"] = end_s - start_s

    out_path = DOWNLOAD_DIR / f"{uuid.uuid4().hex[:16]}_trim.mp4"
    try:
        ok = await render_short_clip(
            source_video_path=video_path,
            output_path=out_path,
            start_seconds=start_s,
            end_seconds=end_s,
            visual_mode=rec.get("visual_mode", "full_frame_vertical"),
        )
        if not ok or not out_path.exists():
            await query.message.reply_text("❌ Не удалось перерезать Short.")
            return

        new_short_id = uuid.uuid4().hex[:16]
        # AUDIT M4: short_trim_save — синхронный SQLite, в executor
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: short_trim_save(
                short_id=new_short_id,
                # FIX AUDIT R4: в новой записи — ИСХОДНИК, не out_path:
                # out_path удаляется в finally ниже, и кнопки нового
                # сообщения были бы навсегда мертвы.
                video_path=str(video_path),
                start_seconds=start_s,
                end_seconds=end_s,
                visual_mode=rec.get("visual_mode", "full_frame_vertical"),
                yt_url=rec.get("yt_url", ""),
                vk_url=rec.get("vk_url", ""),
                rutube_url=rec.get("rutube_url", ""),
                performer=rec.get("performer", ""),
                real_author=rec.get("real_author", ""),
                real_event=rec.get("real_event", ""),
                format_name=rec.get("format_name", ""),
                candidate_json=json.dumps(candidate, ensure_ascii=False),
                video_path_nosub=rec.get("video_path_nosub", ""),
                nosub_expiry=rec.get("nosub_expiry", 0),
                source_duration=source_duration,
            ),
        )

        _new_nosub_path = rec.get("video_path_nosub", "")
        _new_nosub_buttons = []
        if _new_nosub_path and Path(_new_nosub_path).exists():
            _new_nosub_buttons = [
                InlineKeyboardButton("🚫Sub", callback_data=f"strim:nosub:{new_short_id}")
            ]
        new_trim_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("⏪ Начало -10", callback_data=f"strim:s10:{new_short_id}"),
            InlineKeyboardButton("⏭ Конец +10",  callback_data=f"strim:e10:{new_short_id}"),
            InlineKeyboardButton("⏭⏭ Конец +20", callback_data=f"strim:e20:{new_short_id}"),
            *_new_nosub_buttons,
        ]])

        caption = build_short_caption(
            candidate=candidate,
            performer=rec.get("performer", ""),
            real_author=rec.get("real_author", ""),
            real_event=rec.get("real_event", ""),
            format_name=rec.get("format_name", ""),
            yt_url=rec.get("yt_url", ""),
            vk_url=rec.get("vk_url", ""),
            rutube_url=rec.get("rutube_url", ""),
        )
        # FIX AUDIT R4: лимит 1024 проверяем ПОСЛЕ добавления префикса —
        # иначе на длинных caption получали MEDIA_CAPTION_TOO_LONG.
        caption = f"✂️ {label}\n\n{caption}"
        if visible_length(caption) > 1024:
            caption = safe_trim_caption(caption, 1024)

        # Path: file:// при local_mode
        await query.message.reply_video(
            video=out_path,
            caption=caption,
            duration=end_s - start_s,
            width=720,
            height=1280,
            parse_mode="HTML",
            reply_markup=new_trim_keyboard,
            write_timeout=120,
            read_timeout=120,
            connect_timeout=30,
        )
    except Exception as trim_err:
        logger.warning(f"strim retrim error: {trim_err}")
        try:
            await query.message.reply_text(f"❌ Ошибка при перерезке: {str(trim_err)[:150]}")
        except Exception:
            pass
    finally:
        # Trim-файл удаляем только после успешной отправки —
        # повторные ретрим-кнопки будут пересоздавать новый файл.
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass
