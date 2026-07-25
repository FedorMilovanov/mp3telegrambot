#!/usr/bin/env python3
"""
Команда /mode — выбор режима обработки видео.

rus         : 🇷🇺 полный анализ без перевода.
eng         : 🇬🇧 полный анализ + LiveDub-видео + два MP3 + смысловая QA.
eng_fast    : ⚡ LiveDub-видео + чистый RU MP3 + финальный микс, без анализа и QA.
eng_fast_qa : ⚡🔍 тот же комплект + лёгкая QA коротких роликов.
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import logging

logger = logging.getLogger(__name__)

VALID_MODES = ("rus", "eng", "eng_fast", "eng_fast_qa")

MODE_LABELS = {
    "rus": "🇷🇺 Русский — полный анализ",
    "eng": "🇬🇧 ENG Full — анализ + перевод + проверка",
    "eng_fast": "⚡ ENG Quick — перевод + два MP3",
    "eng_fast_qa": "⚡🔍 ENG Quick QA — перевод + два MP3 + проверка",
}

_AUDIO_SET = "видео с переводом, чистый русский MP3 и финальный объединённый MP3"

MODE_DESCRIPTIONS = {
    "rus": "Конспект, цитаты, вопросы, Shorts — как обычно. Перевода нет.",
    "eng": (
        "Полный анализ и комплект LiveDub: " + _AUDIO_SET + ". "
        "Gemini сверяет дубляж с оригиналом и присылает отчёт о точности."
    ),
    "eng_fast": (
        "Только комплект LiveDub: " + _AUDIO_SET + ". "
        "Без конспекта и без смысловой проверки перевода."
    ),
    "eng_fast_qa": (
        "Комплект LiveDub: " + _AUDIO_SET + ". "
        "Короткие ролики дополнительно проверяются; подтверждённые major-ошибки "
        "приглушаются в финальном миксе."
    ),
}


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        # Синхронный SQLite выполняем вне event loop.
        import asyncio as _asyncio
        loop = _asyncio.get_running_loop()
        current = await loop.run_in_executor(None, _get_user_mode_raw, user_id)
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
    for mode in VALID_MODES:
        lines.append(MODE_LABELS[mode])
        lines.append(f"      <i>{MODE_DESCRIPTIONS[mode]}</i>")
    lines.extend(("", "Выберите режим обработки видео:"))

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
        import asyncio as _asyncio
        loop = _asyncio.get_running_loop()
        await loop.run_in_executor(None, _set_user_mode_raw, user_id, mode)
    except Exception as exc:
        logger.error("mode save err: %s", exc)
        await query.edit_message_text("❌ Ошибка сохранения режима.")
        return

    label = MODE_LABELS.get(mode, mode)
    try:
        await query.edit_message_text(
            f"✅ Режим установлен: <b>{label}</b>\n<i>{MODE_DESCRIPTIONS.get(mode, '')}</i>",
            parse_mode="HTML",
        )
    except Exception as exc:
        # Повторный тап по той же кнопке даёт Telegram BadRequest
        # "Message is not modified" и не является ошибкой сохранения.
        if "is not modified" not in str(exc).lower():
            raise


# --- helpers: direct DB access ---

from core.database import _db_conn


def _get_user_mode_raw(user_id: int) -> str:
    """Читает строковое значение режима пользователя из bot_settings."""
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
    """Сохраняет строковое значение режима пользователя."""
    if mode not in VALID_MODES:
        raise ValueError(f"invalid mode: {mode}")
    with _db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            (f"user_mode_{user_id}", mode),
        )
        conn.commit()
