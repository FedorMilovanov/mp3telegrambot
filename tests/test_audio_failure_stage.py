"""Contracts for the shared fresh/cache ``AudioTooLarge`` early return."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from types import SimpleNamespace

import pytest

from pipelines.audio_failure_stage import fail_audio_too_large
from pipelines.audio_stage import AudioTooLarge


class _Message:
    def __init__(self, events: list[tuple[str, Any]], *, error: BaseException | None = None) -> None:
        self.events = events
        self.error = error

    async def reply_text(self, text: str) -> None:
        self.events.append(("reply", text))
        if self.error is not None:
            raise self.error


def _update(events: list[tuple[str, Any]], *, error: BaseException | None = None) -> SimpleNamespace:
    return SimpleNamespace(message=_Message(events, error=error))


def _sender(
    events: list[tuple[str, Any]], *, delivered: bool = True, error: BaseException | None = None
) -> Callable[[], Awaitable[bool]]:
    async def send() -> bool:
        events.append(("send", None))
        if error is not None:
            raise error
        return delivered

    return send


def _cleanup(events: list[tuple[str, Any]], *, error: BaseException | None = None) -> Callable[[str], None]:
    def cleanup(media_id: str) -> None:
        events.append(("cleanup", media_id))
        if error is not None:
            raise error

    return cleanup


def _run(
    events: list[tuple[str, Any]],
    *,
    cache_hit: bool,
    send: Callable[[], Awaitable[bool]] | None = None,
    update: Any = None,
    cleanup: Callable[[str], None] | None = None,
) -> bool:
    return asyncio.run(
        fail_audio_too_large(
            AudioTooLarge(60.0, 50),
            update or _update(events),
            send or _sender(events),
            "media-1",
            cleanup or _cleanup(events),
            cache_hit=cache_hit,
        )
    )


def _kinds(events: list[tuple[str, Any]]) -> list[str]:
    return [event[0] for event in events]


def test_fresh_path_uses_exception_message_and_returns_false() -> None:
    events: list[tuple[str, Any]] = []

    result = _run(events, cache_hit=False)

    assert result is False
    assert _kinds(events) == ["reply", "send", "cleanup"]
    assert events[0][1] == str(AudioTooLarge(60.0, 50))
    assert events[-1][1] == "media-1"


def test_cache_hit_preserves_its_historical_size_message() -> None:
    events: list[tuple[str, Any]] = []

    result = _run(
        events,
        cache_hit=True,
        send=_sender(events, delivered=False),
    )

    assert result is False
    assert events[0][1] == "⚠️ Файл слишком большой (60.0 МБ) даже после сжатия."
    assert _kinds(events) == ["reply", "send", "cleanup"]


def test_livedub_failure_is_suppressed_and_cleanup_still_runs() -> None:
    events: list[tuple[str, Any]] = []

    result = _run(
        events,
        cache_hit=False,
        send=_sender(events, error=RuntimeError("delivery failed")),
    )

    assert result is False
    assert _kinds(events) == ["reply", "send", "cleanup"]


def test_reply_failure_prevents_delivery_and_cleanup() -> None:
    events: list[tuple[str, Any]] = []

    with pytest.raises(RuntimeError, match="reply failed"):
        _run(
            events,
            cache_hit=False,
            update=_update(events, error=RuntimeError("reply failed")),
        )

    assert _kinds(events) == ["reply"]


def test_cleanup_failure_propagates_after_delivery_attempt() -> None:
    events: list[tuple[str, Any]] = []

    with pytest.raises(OSError, match="cleanup failed"):
        _run(
            events,
            cache_hit=False,
            cleanup=_cleanup(events, error=OSError("cleanup failed")),
        )

    assert _kinds(events) == ["reply", "send", "cleanup"]


def test_cancelled_livedub_delivery_is_not_swallowed() -> None:
    events: list[tuple[str, Any]] = []

    with pytest.raises(asyncio.CancelledError):
        _run(
            events,
            cache_hit=False,
            send=_sender(events, error=asyncio.CancelledError()),
        )

    assert _kinds(events) == ["reply", "send"]
