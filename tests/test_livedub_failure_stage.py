"""Behavior contract for the ENG Full LiveDub failure notification."""

from __future__ import annotations
from typing import Any

import asyncio
import html
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipelines.livedub_failure_stage import (
    notify_livedub_send_failure,
    reject_oversized_livedub_video,
    send_full_livedub_failure_notice,
    send_livedub_timeout_notice,
)


class _Bot:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.messages: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        if self.error is not None:
            raise self.error
        self.messages.append(kwargs)


def _send(bot: _Bot, reason: str = "reason", *, flushes: list[str] | None = None) -> bool:
    calls = flushes if flushes is not None else []

    async def flush() -> None:
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


def test_failure_notice_flushes_and_preserves_html_contract() -> None:
    bot = _Bot()
    flushes: list[str] = []

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
def test_failure_notice_uses_historical_default_reason(reason: str) -> None:
    bot = _Bot()

    assert _send(bot, reason) is True
    assert "Яндекс/VOT не отдал живой дубляж для этого ролика." in bot.messages[0]["text"]


def test_failure_notice_send_error_is_nonfatal_and_log_is_truncated(caplog: pytest.LogCaptureFixture) -> None:
    bot = _Bot(RuntimeError("x" * 200))

    with caplog.at_level(logging.INFO):
        assert _send(bot) is False

    assert bot.messages == []
    assert "failure notice not sent: " + "x" * 160 in caplog.text
    assert "x" * 161 not in caplog.text


def test_source_audio_flush_error_still_propagates_before_notice() -> None:
    bot = _Bot()

    async def flush() -> None:
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


def test_notice_cancellation_is_not_swallowed() -> None:
    bot = _Bot(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        _send(bot)


def _send_timeout(bot: _Bot) -> list[str]:
    flushes: list[str] = []

    async def flush() -> None:
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


def _notify_send_failure(bot: _Bot, error: Exception, *, limit: int = 50) -> tuple[None, list[str], list[int]]:
    flushes: list[str] = []
    limit_calls: list[int] = []

    async def flush() -> None:
        flushes.append("LiveDub send/generation failure")

    def max_file_size_mb() -> int:
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


def test_timeout_notice_flushes_and_preserves_message_contract() -> None:
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


def test_timeout_notice_send_error_propagates() -> None:
    bot = _Bot(RuntimeError("timeout send failed"))

    with pytest.raises(RuntimeError, match="timeout send failed"):
        _send_timeout(bot)


def test_send_failure_notice_preserves_size_hint_escaping_and_truncation() -> None:
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


def test_send_failure_size_hint_is_absent_above_cloud_limit() -> None:
    bot = _Bot()

    _, _, limit_calls = _notify_send_failure(bot, RuntimeError("too large"), limit=51)

    assert limit_calls == [51]
    assert "LOCAL_BOT_API_URL" not in bot.messages[0]["text"]


def test_send_failure_non_size_error_does_not_query_file_limit() -> None:
    bot = _Bot()

    _, _, limit_calls = _notify_send_failure(bot, ValueError("ordinary <error>"))

    assert limit_calls == []
    assert "ordinary &lt;error&gt;" in bot.messages[0]["text"]
    assert "LOCAL_BOT_API_URL" not in bot.messages[0]["text"]


def test_send_failure_notice_error_is_nonfatal_and_log_is_truncated(caplog: pytest.LogCaptureFixture) -> None:
    bot = _Bot(RuntimeError("x" * 200))

    with caplog.at_level(logging.INFO):
        result, _, _ = _notify_send_failure(bot, RuntimeError("boom"))

    assert result is None
    assert "[LiveDub] fail: boom" in caplog.text
    assert "send-failure notice not sent: " + "x" * 160 in caplog.text
    assert "x" * 161 not in caplog.text


def test_send_failure_flush_error_propagates_before_notice() -> None:
    bot = _Bot()

    async def flush() -> None:
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


def test_send_failure_notice_cancellation_is_not_swallowed() -> None:
    bot = _Bot(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        _notify_send_failure(bot, RuntimeError("boom"))


def _reject_oversize(
    bot: _Bot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    size_mb: float,
    limit: int,
    flushes: list[str] | None = None,
    flush_error: Exception | None = None,
) -> tuple[bool, list[str], list[int]]:
    video = tmp_path / "dub.mp4"
    real_stat = Path.stat

    def fake_stat(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == video:
            return SimpleNamespace(st_size=int(size_mb * 1024 * 1024))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)
    flush_calls = flushes if flushes is not None else []
    limit_calls: list[int] = []

    async def flush() -> None:
        flush_calls.append("LiveDub video exceeds Telegram limit")
        if flush_error is not None:
            raise flush_error

    def max_file_size_mb() -> int:
        limit_calls.append(limit)
        return limit

    result = asyncio.run(
        reject_oversized_livedub_video(
            video,
            bot=bot,
            chat_id=123,
            reply_to_message_id=456,
            max_file_size_mb=max_file_size_mb,
            flush_source_audio=flush,
        )
    )
    return result, flush_calls, limit_calls


@pytest.mark.parametrize(("size_mb", "limit"), [(1.0, 50), (50.0, 50)])
def test_oversize_guard_allows_video_at_or_below_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size_mb: float, limit: int
) -> None:
    bot = _Bot()

    result, flushes, limit_calls = _reject_oversize(bot, tmp_path, monkeypatch, size_mb=size_mb, limit=limit)

    assert result is False
    assert flushes == []
    assert limit_calls == [limit]
    assert bot.messages == []


def test_oversize_guard_notifies_then_flushes_with_cloud_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    bot = _Bot()

    with caplog.at_level(logging.WARNING):
        result, flushes, limit_calls = _reject_oversize(bot, tmp_path, monkeypatch, size_mb=51.5, limit=50)

    assert result is True
    assert flushes == ["LiveDub video exceeds Telegram limit"]
    assert limit_calls == [50, 50, 50, 50]
    assert "51.5MB (лимит 50MB)" in caplog.text
    assert bot.messages == [
        {
            "chat_id": 123,
            "text": (
                "⚠️ Видео с переводом готово, но оно весит 51.5 МБ. "
                "Текущий лимит отправки — 50 МБ."
                "\n💡 Подними локальный Bot API сервер (LOCAL_BOT_API_URL), "
                "чтобы отправлять до 2000 МБ."
            ),
            "reply_to_message_id": 456,
        }
    ]


def test_oversize_guard_omits_hint_above_cloud_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bot = _Bot()

    result, _, limit_calls = _reject_oversize(bot, tmp_path, monkeypatch, size_mb=52.0, limit=51)

    assert result is True
    assert limit_calls == [51, 51, 51, 51]
    assert "LOCAL_BOT_API_URL" not in bot.messages[0]["text"]


def test_oversize_notice_error_prevents_flush_and_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bot = _Bot(RuntimeError("size notice failed"))
    flushes: list[str] = []

    with pytest.raises(RuntimeError, match="size notice failed"):
        _reject_oversize(
            bot,
            tmp_path,
            monkeypatch,
            size_mb=51.0,
            limit=50,
            flushes=flushes,
        )
    assert flushes == []


def test_oversize_flush_error_propagates_after_notice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bot = _Bot()

    with pytest.raises(RuntimeError, match="flush failed"):
        _reject_oversize(
            bot,
            tmp_path,
            monkeypatch,
            size_mb=51.0,
            limit=50,
            flush_error=RuntimeError("flush failed"),
        )
    assert len(bot.messages) == 1
