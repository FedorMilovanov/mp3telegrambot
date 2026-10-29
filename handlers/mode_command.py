#!/usr/bin/env python3
"""Unified /mode navigation for analysis, Yandex LiveDub and Shorts Factory."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from core.database import ADMIN_IDS
from core.user_modes import (
    EDITORIAL_MODE,
    VALID_MODES,
    _get_shorts_factory_full_video_raw as _get_shorts_factory_full_video_raw,
    _get_user_mode_raw as _get_user_mode_raw,
    _read_user_mode,
    _set_shorts_factory_full_video_raw as _set_shorts_factory_full_video_raw,
    _set_user_mode_raw as _set_user_mode_raw,
    get_shorts_factory_full_video,
    get_user_mode,
)

logger = logging.getLogger(__name__)

_DUB_WIZARD_KEY = "dub_universal_wizard"

MODE_LABELS = {
    "rus": "🇷🇺 Русский — полный анализ",
    "eng": "🇬🇧 ENG Full — анализ + перевод + проверка",
    "eng_fast": "⚡ ENG Quick — перевод + два MP3",
    "eng_fast_qa": "⚡🔍 ENG Quick QA — перевод + два MP3 + проверка",
    "shorts_max": "✂️🧠 SHORTS FACTORY MAX — нарезка без конспектов",
    EDITORIAL_MODE: "🔎 ENG Редактор перевода — Yandex + Whisper → ZIP",
}

MODE_BUTTON_LABELS = {
    "rus": "🇷🇺 RUS — анализ",
    "eng": "🇬🇧 ENG Full",
    "eng_fast": "⚡ ENG Quick",
    "eng_fast_qa": "⚡🔍 Quick QA",
    "shorts_max": "✂️🧠 SHORTS FACTORY MAX",
    EDITORIAL_MODE: "🔎 ENG Редактор перевода",
}

_AUDIO_SET = "видео с переводом, чистый русский MP3 и финальный объединённый MP3"

MODE_DESCRIPTIONS = {
    "rus": "Конспект, цитаты, вопросы и Shorts. Перевода нет.",
    "eng": ("Полный анализ и комплект LiveDub: " + _AUDIO_SET + ". Gemini сверяет дубляж с оригиналом."),
    "eng_fast": ("Только комплект LiveDub: " + _AUDIO_SET + ". Без конспекта и смысловой проверки."),
    "eng_fast_qa": (
        "Комплект LiveDub и лёгкая проверка коротких роликов. "
        "Подтверждённые major-ошибки приглушаются в финальном миксе."
    ),
    "shorts_max": (
        "Без конспектов и Telegraph. Сильнейшая настроенная Gemini слушает весь материал, "
        "тремя проходами проверяет смысл и монтажные границы, затем делает Shorts до 3 минут с "
        "субтитрами и законченные фрагменты 5–15 минут. Английская озвучка — только "
        "Яндекс «Живые голоса», без собственного нейроперевода."
    ),
    EDITORIAL_MODE: ("Без Gemini-нарезки: Yandex LiveDub → original SRT → Russian Whisper large-v3 → ZIP для ChatGPT."),
}


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id in ADMIN_IDS)


def _clear_dub_wizard_state(context: ContextTypes.DEFAULT_TYPE) -> bool:
    if context.user_data is None:
        return False
    return context.user_data.pop(_DUB_WIZARD_KEY, None) is not None


def _selected_label(mode: str, current: str) -> str:
    prefix = "✓ " if mode == current else ""
    return prefix + MODE_BUTTON_LABELS[mode]


def _mode_home_keyboard(current: str, *, is_admin: bool) -> InlineKeyboardMarkup:
    del current
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                "📚 Анализ, LiveDub и нарезка",
                callback_data="mode_menu:analysis",
            )
        ]
    ]
    if is_admin:
        rows.extend(
            [
                [InlineKeyboardButton("🤖 Дубляж — Gemini MAX", callback_data="dubwiz|mode|gemini")],
                [InlineKeyboardButton("✍️ Дубляж — мой готовый SRT", callback_data="dubwiz|mode|direct")],
                [
                    InlineKeyboardButton("📂 Проекты", callback_data="dubwiz|projects|list"),
                    InlineKeyboardButton("🩺 Проверка", callback_data="mode_menu:dubcheck"),
                ],
            ]
        )
    return InlineKeyboardMarkup(rows)


def _analysis_keyboard(current: str, *, full_video: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(_selected_label("rus", current), callback_data="set_mode:rus"),
            InlineKeyboardButton(_selected_label("eng", current), callback_data="set_mode:eng"),
        ],
        [
            InlineKeyboardButton(_selected_label("eng_fast", current), callback_data="set_mode:eng_fast"),
            InlineKeyboardButton(_selected_label("eng_fast_qa", current), callback_data="set_mode:eng_fast_qa"),
        ],
        [InlineKeyboardButton(_selected_label("shorts_max", current), callback_data="set_mode:shorts_max")],
        [
            InlineKeyboardButton(
                _selected_label(EDITORIAL_MODE, current),
                callback_data=f"set_mode:{EDITORIAL_MODE}",
            )
        ],
    ]
    if current == "shorts_max":
        rows.append(
            [
                InlineKeyboardButton(
                    ("☑️ Полный видео-перевод + нарезки" if full_video else "⬜ Только нарезки (без полного видео)"),
                    callback_data="mode_menu:factory_full_video_toggle",
                )
            ]
        )
    rows.append([InlineKeyboardButton("↩️ Все режимы", callback_data="mode_menu:home")])
    return InlineKeyboardMarkup(rows)


def _home_text(current: str, *, is_admin: bool) -> str:
    lines = [
        "🎛 <b>Режимы бота</b>",
        "",
        "📚 <b>Обычная ссылка</b>",
        f"Сейчас: <b>{MODE_LABELS.get(current, MODE_LABELS['rus'])}</b>",
        "<i>Используется, когда ссылка отправляется прямо в чат.</i>",
    ]
    if is_admin:
        lines.extend(
            [
                "",
                "🎙 <b>Дубляж видео</b>",
                "🤖 <b>Gemini MAX</b> — перевод и проверки полностью автоматически.",
                "✍️ <b>Мой SRT</b> — ваш окончательный текст без редакторских правок.",
            ]
        )
    lines.extend(["", "Выберите действие:"])
    return "\n".join(lines)


def _analysis_text(
    current: str,
    *,
    saved: bool = False,
    full_video: bool = False,
) -> str:
    lines = ["📚 <b>Анализ, LiveDub и нарезка</b>", ""]
    if saved:
        lines.extend(
            [
                f"✅ Установлен: <b>{MODE_LABELS.get(current, current)}</b>",
                "",
            ]
        )
    else:
        lines.extend(
            [
                f"Текущий режим: <b>{MODE_LABELS.get(current, MODE_LABELS['rus'])}</b>",
                "",
            ]
        )
    for mode in VALID_MODES:
        lines.append(f"{MODE_BUTTON_LABELS[mode]} — <i>{MODE_DESCRIPTIONS[mode]}</i>")
    if current == "shorts_max":
        full_video_label = "☑️ полный русский видео-перевод + нарезки" if full_video else "⬜ только нарезки"
        lines.extend(
            [
                "",
                f"📺 <b>Полная проповедь:</b> {full_video_label}.",
                "<i>Для иностранного источника используется тот же готовый Yandex LiveDub-файл; "
                "повторный перевод не запускается.</i>",
            ]
        )
    lines.extend(
        [
            "",
            "Этот выбор действует для обычной ссылки и для элементов плейлиста.",
        ]
    )
    return "\n".join(lines)


async def _safe_edit(query: Any, text: str, *, reply_markup: Any = None) -> None:
    try:
        await query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except BadRequest as exc:
        if "message is not modified" not in str(exc).casefold():
            raise


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_dub_wizard_state(context)
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    current = await _read_user_mode(user.id)
    await message.reply_text(
        _home_text(current, is_admin=_is_admin(update)),
        reply_markup=_mode_home_keyboard(current, is_admin=_is_admin(update)),
        parse_mode="HTML",
    )


async def handle_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = str(query.data or "")
    user = update.effective_user
    if user is None:
        return
    user_id = user.id

    if data == "mode_menu:home":
        _clear_dub_wizard_state(context)
        current = await _read_user_mode(user_id)
        await _safe_edit(
            query,
            _home_text(current, is_admin=_is_admin(update)),
            reply_markup=_mode_home_keyboard(current, is_admin=_is_admin(update)),
        )
        return

    if data == "mode_menu:analysis":
        _clear_dub_wizard_state(context)
        current = await _read_user_mode(user_id)
        full_video = await get_shorts_factory_full_video(user_id)
        await _safe_edit(
            query,
            _analysis_text(current, full_video=full_video),
            reply_markup=_analysis_keyboard(current, full_video=full_video),
        )
        return

    if data == "mode_menu:factory_full_video_toggle":
        _clear_dub_wizard_state(context)
        current = await _read_user_mode(user_id)
        if current != "shorts_max":
            await _safe_edit(
                query,
                _analysis_text(current),
                reply_markup=_analysis_keyboard(current),
            )
            return
        try:
            enabled = not await get_shorts_factory_full_video(user_id)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                _set_shorts_factory_full_video_raw,
                user_id,
                enabled,
            )
        except Exception as exc:
            logger.error("Shorts Factory full-video setting save err: %s", exc)
            await _safe_edit(query, "❌ Ошибка сохранения настройки полного видео.")
            return
        await _safe_edit(
            query,
            _analysis_text(current, full_video=enabled),
            reply_markup=_analysis_keyboard(current, full_video=enabled),
        )
        return

    if data == "mode_menu:dubcheck":
        _clear_dub_wizard_state(context)
        from handlers.dub_health import dubcheck_command

        await dubcheck_command(update, context)
        return

    if not data.startswith("set_mode:"):
        return
    _clear_dub_wizard_state(context)
    mode = data.split(":", 1)[1]
    if mode not in VALID_MODES:
        await _safe_edit(query, "❌ Неизвестный режим.")
        return

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _set_user_mode_raw, user_id, mode)
    except Exception as exc:
        logger.error("mode save err: %s", exc)
        await _safe_edit(query, "❌ Ошибка сохранения режима.")
        return

    full_video = await get_shorts_factory_full_video(user_id)
    await _safe_edit(
        query,
        _analysis_text(mode, saved=True, full_video=full_video),
        reply_markup=_analysis_keyboard(mode, full_video=full_video),
    )


__all__ = [
    "EDITORIAL_MODE",
    "MODE_DESCRIPTIONS",
    "MODE_LABELS",
    "VALID_MODES",
    "_analysis_keyboard",
    "_clear_dub_wizard_state",
    "_mode_home_keyboard",
    "get_shorts_factory_full_video",
    "get_user_mode",
    "handle_mode_callback",
    "mode_command",
]
