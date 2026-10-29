"""Behavior contract for LiveDub cover-aware Telegram video delivery."""

from __future__ import annotations
from typing import Any

import asyncio
from collections.abc import Iterable
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipelines.livedub_video_delivery_stage import send_livedub_video_with_cover


class _Bot:
    def __init__(
        self,
        *,
        errors: Iterable[BaseException | None] = (),
        action_error: BaseException | None = None,
    ) -> None:
        self.errors = list(errors)
        self.action_error = action_error
        self.video_calls: list[dict[str, Any]] = []
        self.actions: list[dict[str, Any]] = []

    async def send_chat_action(self, **kwargs: Any) -> None:
        self.actions.append(kwargs)
        if self.action_error is not None:
            raise self.action_error

    async def send_video(self, **kwargs: Any) -> SimpleNamespace:
        self.video_calls.append(kwargs)
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        return SimpleNamespace(video=SimpleNamespace(file_id="video-id"))


def _send(bot: _Bot, *, cover: Path | None = None) -> Any:
    return asyncio.run(
        send_livedub_video_with_cover(
            bot=bot,
            chat_id=123,
            cover=cover,
            send_kwargs={"chat_id": 123, "video": Path("dub.mp4"), "caption": "caption"},
        )
    )


def test_no_cover_upload_sends_action_and_returns_video_message() -> None:
    bot = _Bot()

    message = _send(bot)

    assert message.video.file_id == "video-id"
    assert bot.actions == [{"chat_id": 123, "action": "upload_video"}]
    assert len(bot.video_calls) == 1
    assert "cover" not in bot.video_calls[0]


def test_upload_action_error_is_nonfatal() -> None:
    bot = _Bot(action_error=RuntimeError("action failed"))

    assert _send(bot).video.file_id == "video-id"
    assert len(bot.video_calls) == 1


def test_no_cover_video_error_propagates_without_retry() -> None:
    bot = _Bot(errors=[RuntimeError("send failed")])

    with pytest.raises(RuntimeError, match="send failed"):
        _send(bot)
    assert len(bot.video_calls) == 1


def test_cover_success_uses_single_cover_call(tmp_path: Path) -> None:
    cover = tmp_path / "cover.jpg"
    bot = _Bot()

    assert _send(bot, cover=cover).video.file_id == "video-id"
    assert len(bot.video_calls) == 1
    assert bot.video_calls[0]["cover"] == cover


@pytest.mark.parametrize("error_text", ["Request Entity Too Large", "Too Large"])
def test_cover_size_error_never_retries_without_cover(error_text: str, tmp_path: Path) -> None:
    bot = _Bot(errors=[RuntimeError(error_text)])

    with pytest.raises(RuntimeError, match=error_text):
        _send(bot, cover=tmp_path / "cover.jpg")
    assert len(bot.video_calls) == 1


def test_non_size_cover_error_retries_without_cover_and_truncates_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cover = tmp_path / "cover.jpg"
    bot = _Bot(errors=[RuntimeError("x" * 150), None])

    with caplog.at_level(logging.INFO):
        assert _send(bot, cover=cover).video.file_id == "video-id"

    assert len(bot.video_calls) == 2
    assert bot.video_calls[0]["cover"] == cover
    assert "cover" not in bot.video_calls[1]
    assert "cover не принят (" + "x" * 100 + ") — отправка без cover" in caplog.text
    assert "x" * 101 not in caplog.text


def test_retry_without_cover_error_propagates(tmp_path: Path) -> None:
    bot = _Bot(errors=[RuntimeError("unknown cover"), RuntimeError("retry failed")])

    with pytest.raises(RuntimeError, match="retry failed"):
        _send(bot, cover=tmp_path / "cover.jpg")
    assert len(bot.video_calls) == 2
    assert "cover" not in bot.video_calls[1]


def test_upload_action_cancellation_is_not_swallowed() -> None:
    bot = _Bot(action_error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        _send(bot)
    assert bot.video_calls == []
