#!/usr/bin/env python3
"""
Команда /mode — выбор режима обработки видео.

rus      : 🇷🇺 Обычный режим — полный анализ (конспект, цитаты, Shorts...), без перевода.
eng      : 🇬🇧 ENG Full — полный анализ + Живые голоса + смысловая проверка перевода (Gemini QA).
eng_fast : ⚡ ENG Quick — ТОЛЬКО перевод Живые голоса, без анализа и без QA. Быстро и дёшево.
eng_fast_qa : ⚡🔍 ENG Quick QA — быстрый перевод + лёгкая проверка коротких роликов.
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import logging

logger = logging.getLogger(__name__)

VALID_MODES = ("rus", "eng", "eng_fast", "eng_fast_qa")

MODE_LABELS = {
    "rus":      "🇷🇺 Русский — полный анализ",
    "eng":      "🇬🇧 ENG Full — анализ + перевод + проверка",
    "eng_fast": "⚡ ENG Quick — только перевод",
    "eng_fast_qa": "⚡🔍 ENG Quick QA — перевод + проверка",
}

MODE_DESCRIPTIONS = {
    "rus":      "Конспект, цитаты, вопросы, Shorts — как обычно. Перевода нет.",
    "eng":      "Всё то же + видео с «Живыми голосами» Яндекса + Gemini сверяет дубляж с оригиналом и присылает отчёт о точности.",
    "eng_fast": "Только переведённое видео, максимально быстро. Без конспектов, без проверки, без расхода квоты Gemini.",
    "eng_fast_qa": "Переведённое видео без конспекта, но короткие ролики проверяются лёгкой Gemini-моделью; major-ошибки автоматически вырезаются.",
}


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        current = _get_user_mode_raw(user_id)
    except Exception:
        current = "rus"
    current_label = MODE_LABELS.get(current, MODE_LABELS["rus"])

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(MODE_LABELS["rus"], callback_data="set_mode:rus")],
        [InlineKeyboardButton(MODE_LABELS["eng"], callback_data="set_mode:eng")],
        [InlineKeyboardButton(MODE_LABELS["eng_fast"], callback_data="set_mode:eng_fast")],
        [InlineKeyboardButton(MODE_LABELS["eng_fast_qa"], callback_data="set_mode:eng_fast_qa")],
    ])

    lines = [f"<b>Текущий режим:</b> {current_label}", ""]
    for m in VALID_MODES:
        lines.append(f"{MODE_LABELS[m]}")
        lines.append(f"      <i>{MODE_DESCRIPTIONS[m]}</i>")
    lines.append("")
    lines.append("Выберите режим обработки видео:")

    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def handle_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mode = query.data.split(":", 1)[1]
    if mode not in VALID_MODES:
        await query.edit_message_text("❌ Неизвестный режим.")
        return
    user_id = update.effective_user.id

    try:
        _set_user_mode_raw(user_id, mode)
    except Exception as e:
        logger.error(f"mode save err: {e}")
        await query.edit_message_text("❌ Ошибка сохранения режима.")
        return

    label = MODE_LABELS.get(mode, mode)
    await query.edit_message_text(
        f"✅ Режим установлен: <b>{label}</b>\n<i>{MODE_DESCRIPTIONS.get(mode, '')}</i>",
        parse_mode="HTML",
    )


# --- helpers: direct DB access ---

import sqlite3
from core.globals import DB_PATH


def _get_user_mode_raw(user_id: int) -> str:
    """Читает строковое значение режима пользователя из bot_settings."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA busy_timeout=5000")
            row = conn.execute(
                "SELECT value FROM bot_settings WHERE key = ?",
                (f"user_mode_{user_id}",)
            ).fetchone()
        if row and row[0] in VALID_MODES:
            return row[0]
    except Exception:
        pass
    return "rus"


def _set_user_mode_raw(user_id: int, mode: str) -> None:
    """Сохраняет строковое значение режима пользователя."""
    if mode not in VALID_MODES:
        raise ValueError(f"invalid mode: {mode}")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            (f"user_mode_{user_id}", mode)
        )
        conn.commit()
