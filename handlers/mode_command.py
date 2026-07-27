#!/usr/bin/env python3
"""Unified /mode navigation for analysis, LiveDub and VoxCPM2 Dub Studio."""
from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from core.database import ADMIN_IDS, _db_conn

logger = logging.getLogger(__name__)

VALID_MODES = ("rus", "eng", "eng_fast", "eng_fast_qa")
_DUB_WIZARD_KEY = "dub_universal_wizard"

MODE_LABELS = {
    "rus": "🇷🇺 Русский — полный анализ",
    "eng": "🇬🇧 ENG Full — анализ + перевод + проверка",
    "eng_fast": "⚡ ENG Quick — перевод + два MP3",
    "eng_fast_qa": "⚡🔍 ENG Quick QA — перевод + два MP3 + проверка",
}

MODE_BUTTON_LABELS = {
    "rus": "🇷🇺 RUS — анализ",
    "eng": "🇬🇧 ENG Full",
    "eng_fast": "⚡ ENG Quick",
    "eng_fast_qa": "⚡🔍 Quick QA",
}

_AUDIO_SET = "видео с переводом, чистый русский MP3 и финальный объединённый MP3"

MODE_DESCRIPTIONS = {
    "rus": "Конспект, цитаты, вопросы и Shorts. Перевода нет.",
    "eng": (
        "Полный анализ и комплект LiveDub: " + _AUDIO_SET + ". "
        "Gemini сверяет дубляж с оригиналом."
    ),
    "eng_fast": (
        "Только комплект LiveDub: " + _AUDIO_SET + ". "
        "Без конспекта и смысловой проверки."
    ),
    "eng_fast_qa": (
        "Комплект LiveDub и лёгкая проверка коротких роликов. "
        "Подтверждённые major-ошибки приглушаются в финальном миксе."
    ),
}


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id in ADMIN_IDS)


def _clear_dub_wizard_state(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Cancel an unfinished Dub Studio prompt when the user leaves its wizard."""
    return context.user_data.pop(_DUB_WIZARD_KEY, None) is not None


def _selected_label(mode: str, current: str) -> str:
    prefix = "✓ " if mode == current else ""
    return prefix + MODE_BUTTON_LABELS[mode]


def _mode_home_keyboard(current: str, *, is_admin: bool) -> InlineKeyboardMarkup:
    del current
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                "📚 Анализ и LiveDub",
                callback_data="mode_menu:analysis",
            )
        ]
    ]
    if is_admin:
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        "🤖 Дубляж — Gemini MAX",
                        callback_data="dubwiz|mode|gemini",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "✍️ Дубляж — мой готовый SRT",
                        callback_data="dubwiz|mode|direct",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📂 Проекты",
                        callback_data="dubwiz|projects|list",
                    ),
                    InlineKeyboardButton(
                        "🩺 Проверка",
                        callback_data="mode_menu:dubcheck",
                    ),
                ],
            ]
        )
    return InlineKeyboardMarkup(rows)


def _analysis_keyboard(current: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _selected_label("rus", current),
                    callback_data="set_mode:rus",
                ),
                InlineKeyboardButton(
                    _selected_label("eng", current),
                    callback_data="set_mode:eng",
                ),
            ],
            [
                InlineKeyboardButton(
                    _selected_label("eng_fast", current),
                    callback_data="set_mode:eng_fast",
                ),
                InlineKeyboardButton(
                    _selected_label("eng_fast_qa", current),
                    callback_data="set_mode:eng_fast_qa",
                ),
            ],
            [
                InlineKeyboardButton(
                    "↩️ Все режимы",
                    callback_data="mode_menu:home",
                )
            ],
        ]
    )


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


def _analysis_text(current: str, *, saved: bool = False) -> str:
    lines = [
        "📚 <b>Анализ и LiveDub</b>",
        "",
    ]
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
    lines.extend(
        [
            "",
            "Этот выбор действует для обычной ссылки, отправленной прямо в чат.",
        ]
    )
    return "\n".join(lines)


async def _safe_edit(query, text: str, *, reply_markup=None) -> None:
    try:
        await query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except BadRequest as exc:
        if "message is not modified" not in str(exc).casefold():
            raise


async def _read_user_mode(user_id: int) -> str:
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _get_user_mode_raw, user_id)
    except Exception:
        return "rus"


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_dub_wizard_state(context)
    current = await _read_user_mode(update.effective_user.id)
    await update.effective_message.reply_text(
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
    user_id = update.effective_user.id

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
        await _safe_edit(
            query,
            _analysis_text(current),
            reply_markup=_analysis_keyboard(current),
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

    await _safe_edit(
        query,
        _analysis_text(mode, saved=True),
        reply_markup=_analysis_keyboard(mode),
    )


# --- helpers: direct DB access ---


def _get_user_mode_raw(user_id: int) -> str:
    """Read the user's normal link-processing mode from bot_settings."""
    try:
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT value FROM bot_settings WHERE key = ?",
                (f"user_mode_{user_id}",),
            ).fetchone()
        if row and row[0] in VALID_MODES:
            return row[0]
    except Exception:
        pass
    return "rus"


def _set_user_mode_raw(user_id: int, mode: str) -> None:
    """Persist the user's normal link-processing mode."""
    if mode not in VALID_MODES:
        raise ValueError(f"invalid mode: {mode}")
    with _db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            (f"user_mode_{user_id}", mode),
        )
        conn.commit()


__all__ = [
    "MODE_DESCRIPTIONS",
    "MODE_LABELS",
    "VALID_MODES",
    "_analysis_keyboard",
    "_clear_dub_wizard_state",
    "_mode_home_keyboard",
    "handle_mode_callback",
    "mode_command",
]
