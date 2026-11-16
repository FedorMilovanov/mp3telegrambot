"""Read-only historical glossary popups for the existing @lordchrist/1619.

This handler *only* answers callback_query updates. It never sends messages,
edits posts, accesses the publication ledger, or interferes with the MP3
converter's existing conversation handlers.
"""

from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes

_LOG = logging.getLogger(__name__)

BOT_ID = 8716602202
CHANNEL_ID = -1001295216957
MESSAGE_ID = 1619
CALLBACK_PREFIX = "lc1525:"
CALLBACK_PATTERN = r"^lc1525:(?:swabia|articles|dependency|memmingen)$"

DEFINITIONS: dict[str, str] = {
    "swabia": (
        "Швабия — историческая область Центральной Европы. В 1525 году её земли "
        "входили в Священную Римскую империю, преимущественно на юге современной Германии."
    ),
    "articles": (
        "«Двенадцать статей» — свод двенадцати требований швабских крестьян, "
        "опубликованный в 1525 году. «Статья» здесь означает пункт исторического документа."
    ),
    "dependency": (
        "Личная зависимость — правовые ограничения свободы крестьянина по отношению "
        "к господину. Формы зависимости различались и не совпадали полностью с "
        "российским крепостным правом."
    ),
    "memmingen": (
        "Мемминген — вольный имперский город в 1525 году, ныне Бавария, Германия. "
        "Здесь был составлен знаменитый свод «Двенадцати статей»."
    ),
}

assert all(0 < len(definition) <= 200 for definition in DEFINITIONS.values())


async def answer_lordchrist_1525(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send one native Telegram alert to the clicker, never a public message."""
    query = update.callback_query
    if query is None:
        return

    message = query.message
    key = (query.data or "").removeprefix(CALLBACK_PREFIX)
    allowed = (
        context.bot.id == BOT_ID
        and message is not None
        and getattr(message, "message_id", None) == MESSAGE_ID
        and getattr(getattr(message, "chat", None), "id", None) == CHANNEL_ID
        and key in DEFINITIONS
    )
    if not allowed:
        _LOG.warning("Rejected out-of-scope LordChrist glossary callback")
        await query.answer(cache_time=0)
        return

    await query.answer(
        text=DEFINITIONS[key],
        show_alert=True,
        cache_time=0,
    )


def register_lordchrist_1525_glossary(app: Any) -> None:
    """Register before the converter's generic CallbackQueryHandler catch-all."""
    app.add_handler(
        CallbackQueryHandler(
            answer_lordchrist_1525,
            pattern=CALLBACK_PATTERN,
        )
    )
