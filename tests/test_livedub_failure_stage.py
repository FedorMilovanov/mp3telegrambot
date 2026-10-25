"""Behavior contract for the ENG Full LiveDub failure notification."""

from __future__ import annotations

import asyncio
import html
import logging

import pytest

from pipelines.livedub_failure_stage import send_full_livedub_failure_notice


class _Bot:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.messages = []

    async def send_message(self, **kwargs):
        if self.error is not None:
            raise self.error
        self.messages.append(kwargs)


def _send(bot: _Bot, reason: str = "reason", *, flushes: list | None = None):
    calls = flushes if flushes is not None else []

    async def flush():
        calls.append("LiveDub unavailable")

    return asyncio.run(
        send_full_livedub_failure_notice(
            bot=bot,
            chat_id=123,
            reply_to_message_id=456,
            reason=reason,
            flush_source_audio=flush,
            escape=html.escape,
        )
    )


def test_failure_notice_flushes_and_preserves_html_contract():
    bot = _Bot()
    flushes = []

    assert _send(bot, '  <bad> & "quote"  ', flushes=flushes) is True

    assert flushes == ["LiveDub unavailable"]
    assert len(bot.messages) == 1
    message = bot.messages[0]
    assert message["chat_id"] == 123
    assert message["reply_to_message_id"] == 456
    assert message["parse_mode"] == "HTML"
    assert "⚠️ <b>Живой перевод Яндекса не получился</b>" in message["text"]
    assert "&lt;bad&gt; &amp; &quot;quote&quot;" in message["text"]
    assert "<bad>" not in message["text"]
    assert "проверка точности перевода не запускались" in message["text"]


@pytest.mark.parametrize("reason", ["", "  \n  "])
def test_failure_notice_uses_historical_default_reason(reason):
    bot = _Bot()

    assert _send(bot, reason) is True
    assert "Яндекс/VOT не отдал живой дубляж для этого ролика." in bot.messages[0]["text"]


def test_failure_notice_send_error_is_nonfatal_and_log_is_truncated(caplog):
    bot = _Bot(RuntimeError("x" * 200))

    with caplog.at_level(logging.INFO):
        assert _send(bot) is False

    assert bot.messages == []
    assert "failure notice not sent: " + "x" * 160 in caplog.text
    assert "x" * 161 not in caplog.text


def test_source_audio_flush_error_still_propagates_before_notice():
    bot = _Bot()

    async def flush():
        raise RuntimeError("flush failed")

    with pytest.raises(RuntimeError, match="flush failed"):
        asyncio.run(
            send_full_livedub_failure_notice(
                bot=bot,
                chat_id=123,
                reply_to_message_id=456,
                reason="reason",
                flush_source_audio=flush,
                escape=html.escape,
            )
        )
    assert bot.messages == []


def test_notice_cancellation_is_not_swallowed():
    bot = _Bot(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        _send(bot)
