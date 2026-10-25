"""Behavior contract for the ENG Full LiveDub failure notification."""

from __future__ import annotations

import asyncio
import html
import logging

import pytest

from pipelines.livedub_failure_stage import (
    notify_livedub_send_failure,
    send_full_livedub_failure_notice,
    send_livedub_timeout_notice,
)


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


def _send_timeout(bot: _Bot):
    flushes = []

    async def flush():
        flushes.append("LiveDub timeout")

    asyncio.run(
        send_livedub_timeout_notice(
            bot=bot,
            chat_id=123,
            reply_to_message_id=456,
            flush_source_audio=flush,
        )
    )
    return flushes


def _notify_send_failure(bot: _Bot, error: Exception, *, limit: int = 50):
    flushes = []
    limit_calls = []

    async def flush():
        flushes.append("LiveDub send/generation failure")

    def max_file_size_mb():
        limit_calls.append(limit)
        return limit

    result = asyncio.run(
        notify_livedub_send_failure(
            bot=bot,
            chat_id=123,
            reply_to_message_id=456,
            error=error,
            max_file_size_mb=max_file_size_mb,
            flush_source_audio=flush,
            escape=html.escape,
        )
    )
    return result, flushes, limit_calls


def test_timeout_notice_flushes_and_preserves_message_contract():
    bot = _Bot()

    assert _send_timeout(bot) == ["LiveDub timeout"]
    assert bot.messages == [
        {
            "chat_id": 123,
            "text": (
                "⏳ Перевод «Живые голоса» не успел за 30 минут и был отменён. "
                "Отправьте ссылку ещё раз — попробуем заново."
            ),
            "reply_to_message_id": 456,
        }
    ]


def test_timeout_notice_send_error_propagates():
    bot = _Bot(RuntimeError("timeout send failed"))

    with pytest.raises(RuntimeError, match="timeout send failed"):
        _send_timeout(bot)


def test_send_failure_notice_preserves_size_hint_escaping_and_truncation():
    error = RuntimeError("Request Entity Too Large " + "<bad>&" * 60)
    bot = _Bot()

    result, flushes, limit_calls = _notify_send_failure(bot, error)

    assert result is None
    assert flushes == ["LiveDub send/generation failure"]
    assert limit_calls == [50]
    message = bot.messages[0]
    assert message["parse_mode"] == "HTML"
    assert message["reply_to_message_id"] == 456
    assert html.escape(str(error)[:300]) in message["text"]
    assert "LOCAL_BOT_API_URL" in message["text"]
    assert "Перевод сгенерирован, но отправка не удалась." in message["text"]


def test_send_failure_size_hint_is_absent_above_cloud_limit():
    bot = _Bot()

    _, _, limit_calls = _notify_send_failure(bot, RuntimeError("too large"), limit=51)

    assert limit_calls == [51]
    assert "LOCAL_BOT_API_URL" not in bot.messages[0]["text"]


def test_send_failure_non_size_error_does_not_query_file_limit():
    bot = _Bot()

    _, _, limit_calls = _notify_send_failure(bot, ValueError("ordinary <error>"))

    assert limit_calls == []
    assert "ordinary &lt;error&gt;" in bot.messages[0]["text"]
    assert "LOCAL_BOT_API_URL" not in bot.messages[0]["text"]


def test_send_failure_notice_error_is_nonfatal_and_log_is_truncated(caplog):
    bot = _Bot(RuntimeError("x" * 200))

    with caplog.at_level(logging.INFO):
        result, _, _ = _notify_send_failure(bot, RuntimeError("boom"))

    assert result is None
    assert "[LiveDub] fail: boom" in caplog.text
    assert "send-failure notice not sent: " + "x" * 160 in caplog.text
    assert "x" * 161 not in caplog.text


def test_send_failure_flush_error_propagates_before_notice():
    bot = _Bot()

    async def flush():
        raise RuntimeError("flush failed")

    with pytest.raises(RuntimeError, match="flush failed"):
        asyncio.run(
            notify_livedub_send_failure(
                bot=bot,
                chat_id=123,
                reply_to_message_id=456,
                error=RuntimeError("boom"),
                max_file_size_mb=lambda: 50,
                flush_source_audio=flush,
                escape=html.escape,
            )
        )
    assert bot.messages == []


def test_send_failure_notice_cancellation_is_not_swallowed():
    bot = _Bot(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        _notify_send_failure(bot, RuntimeError("boom"))
