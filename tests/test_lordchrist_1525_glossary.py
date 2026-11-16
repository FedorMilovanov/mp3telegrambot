"""Regression tests: historical popups may never break MP3Bot callbacks."""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from typing import Any, cast

from telegram import Update
from telegram.ext import ContextTypes

from handlers.lordchrist_1525_glossary import (
    BOT_ID,
    CALLBACK_PATTERN,
    CALLBACK_PREFIX,
    CHANNEL_ID,
    DEFINITIONS,
    MESSAGE_ID,
    answer_lordchrist_1525,
    register_lordchrist_1525_glossary,
)


class FakeQuery:
    def __init__(
        self,
        key: str = "swabia",
        chat_id: int = CHANNEL_ID,
        message_id: int = MESSAGE_ID,
    ) -> None:
        self.data = CALLBACK_PREFIX + key
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=chat_id),
            message_id=message_id,
        )
        self.calls: list[dict[str, object]] = []

    async def answer(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def invoke(query: FakeQuery, bot_id: int = BOT_ID) -> list[dict[str, object]]:
    asyncio.run(
        answer_lordchrist_1525(
            cast(Update, SimpleNamespace(callback_query=query)),
            cast(ContextTypes.DEFAULT_TYPE, SimpleNamespace(bot=SimpleNamespace(id=bot_id))),
        )
    )
    return query.calls


def test_four_short_editorial_definitions() -> None:
    assert set(DEFINITIONS) == {"swabia", "articles", "dependency", "memmingen"}
    assert all(0 < len(text) <= 200 for text in DEFINITIONS.values())


def test_every_term_opens_private_native_popup_without_public_reply() -> None:
    for key, definition in DEFINITIONS.items():
        replies = invoke(FakeQuery(key))
        assert replies == [{"text": definition, "show_alert": True, "cache_time": 0}]


def test_other_messages_channels_bots_cannot_trigger_popups() -> None:
    cases = [
        (FakeQuery(), BOT_ID + 1),
        (FakeQuery(message_id=1620), BOT_ID),
        (FakeQuery(chat_id=-100123), BOT_ID),
        (FakeQuery(key="pastor"), BOT_ID),
    ]
    for query, bot_id in cases:
        assert invoke(query, bot_id) == [{"cache_time": 0}]


def test_isolated_callback_registration_does_not_intercept_mp3bot() -> None:
    from telegram.ext import CallbackQueryHandler

    class FakeApp:
        def __init__(self) -> None:
            self.handlers: list[Any] = []

        def add_handler(self, handler: Any) -> None:
            self.handlers.append(handler)

    app = FakeApp()
    register_lordchrist_1525_glossary(app)
    assert len(app.handlers) == 1
    handler = app.handlers[0]
    assert isinstance(handler, CallbackQueryHandler)
    assert handler.callback == answer_lordchrist_1525
    assert re.fullmatch(CALLBACK_PATTERN, "lc1525:swabia")
    assert re.fullmatch(CALLBACK_PATTERN, "lc1525:memmingen")
    assert not re.fullmatch(CALLBACK_PATTERN, "set_mode:ru")
    assert not re.fullmatch(CALLBACK_PATTERN, "quiz:1")
    assert not re.fullmatch(CALLBACK_PATTERN, "lc1525:pastor")


def test_registered_before_generic_callback_catchall() -> None:
    from pathlib import Path

    main = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
    assert main.index("register_lordchrist_1525_glossary(app)") < main.index(
        "app.add_handler(CallbackQueryHandler(handle_callback))"
    )
