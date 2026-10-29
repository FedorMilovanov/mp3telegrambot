"""Cached LiveDub pair delivery and rollback contracts."""

from typing import Any
from collections.abc import Awaitable, Callable

import asyncio
from types import SimpleNamespace

import pytest

from pipelines.livedub_cached_delivery_stage import deliver_cached_livedub


class Bot:
    def __init__(self, *, video_error: BaseException | None = None, message_error: BaseException | None = None) -> None:
        self.video_error = video_error
        self.message_error = message_error
        self.video_message = SimpleNamespace(message_id=99)
        self.videos: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []

    async def send_video(self, **kwargs: Any) -> SimpleNamespace:
        self.videos.append(kwargs)
        if self.video_error is not None:
            raise self.video_error
        return self.video_message

    async def send_message(self, **kwargs: Any) -> None:
        self.messages.append(kwargs)
        if self.message_error is not None:
            raise self.message_error


def _invoke(
    bot: Bot,
    monkeypatch: pytest.MonkeyPatch,
    *,
    companions_ok: bool = True,
    invalidate: Callable[..., Awaitable[None]] | None = None,
    flush: Callable[[str], Awaitable[None]] | None = None,
) -> list[tuple[Any, ...]]:
    events: list[tuple[Any, ...]] = []
    card = {"title": "Cached title"}

    async def companions(received_bot: Any, **kwargs: Any) -> bool:
        events.append(("companions", received_bot, kwargs))
        return companions_ok

    async def delete(*args: Any) -> None:
        events.append(("delete", *args))

    invalidate_callback = invalidate
    if invalidate_callback is None:

        async def default_invalidate(*args: Any) -> None:
            events.append(("invalidate", *args))

        invalidate_callback = default_invalidate

    flush_callback = flush
    if flush_callback is None:

        async def default_flush(reason: str) -> None:
            events.append(("flush", reason))

        flush_callback = default_flush

    def discard(reason: str) -> None:
        events.append(("discard", reason))

    monkeypatch.setattr(
        "pipelines.livedub_cached_delivery_stage.coordinator.deliver_cached_companions",
        companions,
    )
    monkeypatch.setattr(
        "pipelines.livedub_cached_delivery_stage.coordinator.delete_message_best_effort",
        delete,
    )
    monkeypatch.setattr("core.database.adb_set_livedub_file_id", invalidate_callback)
    asyncio.run(
        deliver_cached_livedub(
            bot,
            chat_id=123,
            reply_to=777,
            video_file_id="cached-video",
            caption="caption",
            publication_card=card,
            media_id="media1",
            flush_source_audio=flush_callback,
            discard_source_audio=discard,
        )
    )
    return events


def test_cached_pair_success_discards_deferred_source_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = Bot()
    events = _invoke(bot, monkeypatch)

    assert bot.videos == [
        {
            "chat_id": 123,
            "video": "cached-video",
            "caption": "caption",
            "parse_mode": "HTML",
            "reply_to_message_id": 777,
            "supports_streaming": True,
        }
    ]
    assert events[0] == (
        "companions",
        bot,
        {
            "chat_id": 123,
            "video_file_id": "cached-video",
            "publication_card": {"title": "Cached title"},
            "reply_to": 777,
        },
    )
    assert events[1:] == [("discard", "cached LiveDub pair delivered")]
    assert bot.messages == []


def test_missing_companions_rolls_back_invalidates_flushes_and_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = Bot()
    events = _invoke(bot, monkeypatch, companions_ok=False)

    assert events[0][0] == "companions"
    assert events[1] == ("delete", bot, 123, bot.video_message)
    assert events[2] == ("invalidate", "media1", "")
    assert events[3] == ("flush", "cached LiveDub pair unavailable")
    assert not any(event[0] == "discard" for event in events)
    assert len(bot.messages) == 1
    assert bot.messages[0]["chat_id"] == 123
    assert bot.messages[0]["reply_to_message_id"] == 777
    assert "MP3-комплект устарел" in bot.messages[0]["text"]


def test_invalidation_failure_still_flushes_and_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = Bot()
    calls: list[tuple[Any, ...]] = []

    async def invalidate(*args: Any) -> None:
        calls.append(args)
        raise RuntimeError("database unavailable")

    events = _invoke(bot, monkeypatch, companions_ok=False, invalidate=invalidate)

    assert calls == [("media1", "")]
    assert ("flush", "cached LiveDub pair unavailable") in events
    assert len(bot.messages) == 1


def test_video_rejection_has_no_message_rollback_but_invalidates_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = Bot(video_error=RuntimeError("stale file id"))
    events = _invoke(bot, monkeypatch)

    assert not any(event[0] == "companions" for event in events)
    assert not any(event[0] == "delete" for event in events)
    assert [event[0] for event in events] == ["invalidate", "flush"]
    assert len(bot.messages) == 1


def test_invalidation_and_notice_failures_are_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    async def invalidate(*_args: Any) -> None:
        raise RuntimeError("database unavailable")

    bot = Bot(message_error=RuntimeError("notice unavailable"))
    events = _invoke(
        bot,
        monkeypatch,
        companions_ok=False,
        invalidate=invalidate,
    )

    assert [event[0] for event in events] == ["companions", "delete", "flush"]
    assert len(bot.messages) == 1


def test_source_audio_flush_error_is_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    async def flush(_reason: str) -> None:
        raise RuntimeError("source audio failed")

    bot = Bot()
    with pytest.raises(RuntimeError, match="source audio failed"):
        _invoke(bot, monkeypatch, companions_ok=False, flush=flush)

    assert bot.messages == []


def test_cancellation_propagates_without_cached_pair_invalidation(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = Bot(video_error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        _invoke(bot, monkeypatch)

    assert bot.messages == []
