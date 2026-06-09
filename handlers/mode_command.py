#!/usr/bin/env python3
"""
Команда /mode — выбор режима обработки видео.
ENG: Анализ + конспекты + Shorts + Живые голоса (Yandex livedub)
RUS: Обычный режим (как раньше)
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import logging

logger = logging.getLogger(__name__)


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        from core.database import asettings_get
        # asettings_get возвращает bool, но мы храним строку через bot_settings
        current = _get_user_mode_raw(user_id)
    except Exception:
        current = "rus"
    current_text = "🇷🇺 Русский" if current == "rus" else "🇬🇧 English + Живые голоса"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇷🇺 Русский — обычный анализ", callback_data="set_mode:rus")],
        [InlineKeyboardButton("🇬🇧 English — анализ + живой перевод", callback_data="set_mode:eng")],
    ])

    await update.message.reply_text(
        f"<b>Режим:</b> {current_text}\n\nВыберите режим обработки видео:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def handle_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mode = query.data.split(":", 1)[1]
    user_id = update.effective_user.id

    try:
        _set_user_mode_raw(user_id, mode)
    except Exception as e:
        logger.error(f"mode save err: {e}")
        await query.edit_message_text("❌ Ошибка сохранения режима.")
        return

    label = "🇷🇺 Русский" if mode == "rus" else "🇬🇧 English + Живые голоса"
    await query.edit_message_text(
        f"✅ Режим установлен: <b>{label}</b>",
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
        if row:
            return row[0]
    except Exception:
        pass
    return "rus"


def _set_user_mode_raw(user_id: int, mode: str) -> None:
    """Сохраняет строковое значение режима пользователя."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            (f"user_mode_{user_id}", mode)
        )
        conn.commit()
