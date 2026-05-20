#!/usr/bin/env python3
"""
Handlers — settings keyboard, handle_callback.
Извлечено из bot.py строки 8365–8722.
"""
from core.globals import (
    HAS_GEMINI, DOWNLOAD_DIR, DB_PATH,          # FIX #8
    InlineKeyboardButton, InlineKeyboardMarkup,  # FIX #8
)
from core.database import (
    asettings_get, asettings_set, asettings_get_all,
    shorts_speed_get, shorts_speed_set, shorts_speed_cycle,
    short_trim_get, short_trim_save,
    SETTINGS_DEFAULTS, SETTINGS_LABELS,           # FIX #8
    ADMIN_IDS,                                    # FIX #8
    settings_get_all,                             # FIX handlers_callbacks
)
from converters.md_telegraph import visible_length, safe_trim_caption  # FIX #8
from services.shorts_video import build_short_caption, render_short_clip  # FIX #8
from services.search import _SETTINGS_GROUPS                 # FIX #8: пока живёт в search.py

import asyncio
import json       # FIX #8
import logging
import sqlite3    # FIX #8
import time       # FIX #8
import uuid       # FIX #8
from pathlib import Path  # FIX #8

logger = logging.getLogger(__name__)

def _build_settings_keyboard(settings: dict[str, bool] | None = None) -> InlineKeyboardMarkup:
    """Строит клавиатуру настроек: по 2 кнопки в ряд для коротких, 1 для длинных.

    Принимает словарь настроек ``settings`` (уже загруженный через settings_get_all).
    Если не передан — делает одно синхронное чтение всех настроек за раз.
    Вызывайте из async-кода через: settings_get_all() → run_in_executor → передать сюда,
    чтобы не блокировать event loop.
    """
    if settings is None:
        settings = settings_get_all()
    buttons: list[list[InlineKeyboardButton]] = []
    speed   = shorts_speed_get()

    # Кнопки длиннее этого порога (символов в label) — на всю строку
    WIDE_THRESHOLD = 18

    for group_title, keys in _SETTINGS_GROUPS:
        # Разделитель-заголовок (не-кликабельный)
        buttons.append([InlineKeyboardButton(f"─── {group_title} ───", callback_data="noop")])

        # Собираем кнопки группы, потом укладываем по 2 в ряд где возможно
        group_btns: list[InlineKeyboardButton] = []
        for key in keys:
            if key == "__speed__":
                # Скорость — сначала сбрасываем буфер, потом на отдельную строку
                if group_btns:
                    # укладываем накопленные кнопки
                    _flush_buttons(buttons, group_btns, WIDE_THRESHOLD)
                    group_btns = []
                buttons.append([InlineKeyboardButton(
                    f"⚡ Скорость: {speed}x",
                    callback_data="setting_speed"
                )])
            else:
                state = settings.get(key, SETTINGS_DEFAULTS.get(key, True))
                icon  = "✅" if state else "☑️"
                label = SETTINGS_LABELS.get(key, key)
                group_btns.append(InlineKeyboardButton(
                    f"{icon} {label}",
                    callback_data=f"setting:{key}"
                ))

        if group_btns:
            _flush_buttons(buttons, group_btns, WIDE_THRESHOLD)

    return InlineKeyboardMarkup(buttons)


def _flush_buttons(
    rows: list,
    btns: list,
    wide_threshold: int,
) -> None:
    """Укладывает список кнопок в rows: короткие по 2 в ряд, длинные по одной."""
    i = 0
    while i < len(btns):
        label = btns[i].text  # текст уже с иконкой
        # Убираем иконку состояния (✅/☑️) для замера длины — ☑️ = 2 кодпойнта
        # поэтому используем lstrip вместо slicing
        pure = label.lstrip("✅☑️️ ").strip()
        if len(pure) > wide_threshold:
            # Длинная — на всю строку
            rows.append([btns[i]])
            i += 1
        else:
            # Короткая — пробуем взять следующую в пару
            if i + 1 < len(btns):
                next_label = btns[i + 1].text
                # FIX #17: используем тот же lstrip что и для текущей кнопки —
                # срез [2:] некорректен для ✅ (1 кодпоинт) vs ☑️ (2 кодпоинта)
                next_pure  = next_label.lstrip("✅☑️️ ").strip()
                if len(next_pure) <= wide_threshold:
                    rows.append([btns[i], btns[i + 1]])
                    i += 2
                    continue
            rows.append([btns[i]])
            i += 1


async def settings_command(update, context):
    """Показывает панель настроек с переключаемыми галочками."""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Настройки доступны только администраторам.")
        return
    all_settings = await asettings_get_all()
    await update.message.reply_text(
        "⚙️ <b>Настройки</b>\n\n"
        "Нажмите на пункт — он сразу переключится.\n"
        "<i>Shorts и Clips влияют только на видеофрагменты.</i>",
        parse_mode="HTML",
        reply_markup=_build_settings_keyboard(all_settings)
    )


async def handle_callback(update, context) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    # ── Разделитель (нажатие игнорируется) ──────────────────────
    if data == "noop":
        return

    # ── Скорость Shorts (цикличное переключение) ─────────────
    if data == "setting_speed":
        user_id = update.effective_user.id
        if ADMIN_IDS and user_id not in ADMIN_IDS:
            await query.answer("⛔ Нет доступа.", show_alert=True)
            return
        new_speed = shorts_speed_cycle()
        await query.answer(f"⚡ Скорость Shorts: {new_speed}x")
        try:
            all_settings = await asettings_get_all()
            await query.edit_message_reply_markup(reply_markup=_build_settings_keyboard(all_settings))
        except Exception:
            pass
        return

    # ── Настройки (toggle bool) ───────────────────────────────
    if data.startswith("setting:"):
        user_id = update.effective_user.id
        if ADMIN_IDS and user_id not in ADMIN_IDS:
            await query.answer("⛔ Нет доступа.", show_alert=True)
            return
        key = data.split(":", 1)[1]
        if key in SETTINGS_DEFAULTS:
            new_val = not await asettings_get(key)
            await asettings_set(key, new_val)
            state_text = "включено ✅" if new_val else "выключено ☑️"
            label = SETTINGS_LABELS.get(key, key)
            await query.answer(f"{label}: {state_text}")
            try:
                all_settings = await asettings_get_all()
                await query.edit_message_reply_markup(reply_markup=_build_settings_keyboard(all_settings))
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
        rec = short_trim_get(short_id)
        if not rec:
            await query.answer("Данные Short не найдены — возможно видео устарело.", show_alert=True)
            return
        video_path = Path(rec["video_path"])
        if not video_path.exists():
            await query.answer("Исходное видео не найдено на сервере.", show_alert=True)
            return

        start_s = rec["start_seconds"]
        end_s   = rec["end_seconds"]

        if mode == "nosub":
            # Отправить версию без субтитров
            nosub_path_str = rec.get("video_path_nosub", "")
            if not nosub_path_str:
                await query.answer("🚫 Версия без субтитров недоступна для этого клипа.", show_alert=True)
                return
            # #31: проверяем срок хранения файла перед тем как лезть на диск
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
                if visible_length(caption) > 1024:
                    caption = safe_trim_caption(caption, 1024)

                with open(nosub_path, "rb") as vf:
                    await query.message.reply_video(
                        video=vf,
                        caption=f"🚫 Без субтитров\n\n{caption}",
                        duration=end_s - start_s,
                        width=720,
                        height=1280,
                        parse_mode="HTML",
                        write_timeout=120,
                        read_timeout=120,
                        connect_timeout=30,
                    )
            except Exception as nosub_err:
                logger.warning(f"strim:nosub error: {nosub_err}")
                await query.message.reply_text(f"❌ Ошибка: {str(nosub_err)[:150]}")
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

        if start_s >= end_s:
            await query.answer("⚠️ Начало не может быть позже конца.", show_alert=True)
            return

        await query.answer(f"✂️ {label}, перерезаю...")

        candidate = {}
        try:
            candidate = json.loads(rec["candidate_json"])
        except Exception:
            pass
        candidate["start_seconds"] = start_s
        candidate["end_seconds"]   = end_s
        candidate["duration_seconds"] = end_s - start_s

        try:
            out_path = DOWNLOAD_DIR / f"{uuid.uuid4().hex[:16]}_trim.mp4"
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

            # Сохраняем новый short для возможных повторных trim
            new_short_id = uuid.uuid4().hex[:16]
            short_trim_save(
                short_id=new_short_id,
                video_path=str(out_path),
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
                video_path_nosub=rec.get("video_path_nosub", ""),  # сохраняем путь до субтитров
                nosub_expiry=rec.get("nosub_expiry", 0),  # #31: переносим срок из оригинальной записи
            )
            # Кнопки ретрима + 🚫Sub если есть версия без субтитров
            _new_nosub_path = rec.get("video_path_nosub", "")
            _new_nosub_buttons = []
            if _new_nosub_path and Path(_new_nosub_path).exists():
                _new_nosub_buttons = [InlineKeyboardButton("🚫Sub", callback_data=f"strim:nosub:{new_short_id}")]
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
            if visible_length(caption) > 1024:
                caption = safe_trim_caption(caption, 1024)

            with open(out_path, "rb") as vf:
                await query.message.reply_video(
                    video=vf,
                    caption=f"✂️ {label}\n\n{caption}",
                    duration=end_s - start_s,
                    width=720,
                    height=1280,
                    parse_mode="HTML",
                    reply_markup=new_trim_keyboard,
                    write_timeout=120,
                    read_timeout=120,
                    connect_timeout=30,
                )
            # Trim-файл удаляем после отправки — иначе они копятся вечно.
            # При повторном нажатии ретрим-кнопок video_path.exists()=False →
            # бот ответит "видео не найдено" — это ожидаемое поведение.
            try:
                out_path.unlink(missing_ok=True)
            except Exception:
                pass
        except Exception as trim_err:
            logger.warning(f"strim callback error: {trim_err}")
            await query.message.reply_text(f"❌ Ошибка при перерезке: {str(trim_err)[:150]}")
            try:
                out_path.unlink(missing_ok=True)
            except Exception:
                pass
        return

    # ── Сброс всего кэша ─────────────────────────────────────
    if data == "resetcache:all":
        user_id = update.effective_user.id
        if ADMIN_IDS and user_id not in ADMIN_IDS:
            await query.answer("⛔ Нет доступа.", show_alert=True)
            return
        loop = asyncio.get_running_loop()
        def _delete_all_cache():
            with sqlite3.connect(DB_PATH) as conn:
                r = conn.execute("DELETE FROM video_cache").rowcount
                conn.commit()
                return r
        rows = await loop.run_in_executor(None, _delete_all_cache)
        await query.edit_message_text(f"🗑 Весь кэш удалён: {rows} записей.")
        return


    if ":" not in data:
        await query.answer("Неизвестное действие.", show_alert=True)
        return
    action, video_id = data.split(":", 1)
    # Нет зарегистрированных обработчиков для этого action.
    # Сообщаем пользователю вместо молчаливого завершения.
    logger.warning(f"handle_callback: необработанный action={action!r} video_id={video_id!r}")
    await query.answer("⚠️ Действие устарело или не поддерживается.", show_alert=True)


# ─── Shorts MVP ───────────────────────────────────────────────

