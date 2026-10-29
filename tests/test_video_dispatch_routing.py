#!/usr/bin/env python3
"""Маршрутизация видео по режиму пользователя (`pipelines/video_dispatch.py`).

Ошибка здесь — самая дорогая из возможных: видео уходит не в тот пайплайн, и
пользователь получает не тот результат (или не получает ничего). До этих тестов
файл был покрыт на 0 %.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from core import user_modes
from pipelines import video_dispatch


class _FakeMessage:
    def __init__(self, *, fail: bool = False) -> None:
        self.replies: list[str] = []
        self._fail = fail

    async def reply_text(self, text: str, **_kwargs: Any) -> None:
        if self._fail:
            raise RuntimeError("Telegram недоступен")
        self.replies.append(text)


class _FakeUser:
    def __init__(self, user_id: int | str) -> None:
        self.id = user_id


class _FakeUpdate:
    def __init__(self, user_id: int | str | None = 42, *, message_fail: bool = False) -> None:
        self.effective_user = _FakeUser(user_id) if user_id is not None else None
        self.effective_message = _FakeMessage(fail=message_fail)


@pytest.fixture
def traces(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Собираем outcome'ы трейса вместо записи в лог."""
    outcomes: list[str] = []
    monkeypatch.setattr(video_dispatch, "begin_latency_trace", lambda _mode: "tok")
    monkeypatch.setattr(
        video_dispatch,
        "finish_latency_trace",
        lambda _handle, *, outcome: outcomes.append(outcome),
    )
    return outcomes


def _mode_reader(mode: str = "rus") -> Callable[[int], Awaitable[str]]:
    async def reader(_user_id: int) -> str:
        return mode

    return reader


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[tuple[Any, ...]]]:
    """Подменяем все три пайплайна и запоминаем, кто был вызван."""
    recorded: dict[str, list[tuple[Any, ...]]] = {"main": [], "editorial": [], "factory": []}

    async def main_video(url: str, update: Any, **kwargs: Any) -> bool:
        recorded["main"].append((url, update, kwargs))
        return True

    async def editorial(url: str, update: Any, **kwargs: Any) -> bool:
        recorded["editorial"].append((url, update, kwargs))
        return True

    async def factory(url: str, update: Any, **kwargs: Any) -> bool:
        recorded["factory"].append((url, update, kwargs))
        return True

    import pipelines.main_pipeline
    import pipelines.shorts_factory
    import pipelines.translation_editorial_runner
    import services.shorts_video

    monkeypatch.setattr(pipelines.main_pipeline, "process_single_video", main_video)
    monkeypatch.setattr(
        pipelines.translation_editorial_runner,
        "process_translation_editorial_only",
        editorial,
    )
    monkeypatch.setattr(pipelines.shorts_factory, "process_shorts_factory", factory)
    monkeypatch.setattr(services.shorts_video, "HAS_FASTER_WHISPER", True)
    monkeypatch.setattr(user_modes, "get_user_mode", _mode_reader())
    return recorded


def _dispatch(update: Any, *, silent_errors: bool = False) -> bool:
    return asyncio.run(
        video_dispatch.process_single_video(
            "https://youtu.be/abc",
            update,
            status_msg="status",
            progress_prefix="p",
            context="ctx",
            silent_errors=silent_errors,
        )
    )


def test_default_mode_goes_to_main_pipeline(calls: dict[str, list[tuple[Any, ...]]], traces: list[str]) -> None:
    update = _FakeUpdate()
    assert _dispatch(update) is True
    assert len(calls["main"]) == 1
    assert calls["editorial"] == [] and calls["factory"] == []
    assert traces == ["ok"]


def test_arguments_are_passed_through(calls: dict[str, list[tuple[Any, ...]]], traces: list[str]) -> None:
    """Потеря `context`/`status_msg` ломает прогресс и отмену задач."""
    update = _FakeUpdate()
    _dispatch(update)
    url, passed_update, kwargs = calls["main"][0]
    assert url == "https://youtu.be/abc"
    assert passed_update is update
    assert kwargs == {
        "status_msg": "status",
        "progress_prefix": "p",
        "context": "ctx",
        "silent_errors": False,
    }


def test_editorial_mode_goes_to_editorial_runner(
    calls: dict[str, list[tuple[Any, ...]]], traces: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setattr(user_modes, "get_user_mode", _mode_reader(user_modes.EDITORIAL_MODE))
    assert _dispatch(_FakeUpdate()) is True
    assert len(calls["editorial"]) == 1
    assert calls["main"] == [] and calls["factory"] == []


def test_shorts_max_goes_to_factory(
    calls: dict[str, list[tuple[Any, ...]]], traces: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setattr(user_modes, "get_user_mode", _mode_reader("shorts_max"))
    assert _dispatch(_FakeUpdate()) is True
    assert len(calls["factory"]) == 1
    assert calls["main"] == []


def test_shorts_max_without_whisper_is_refused(
    calls: dict[str, list[tuple[Any, ...]]], traces: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без faster-whisper фабрика не стартует — пользователь узнаёт почему."""
    import services.shorts_video

    monkeypatch.setattr(user_modes, "get_user_mode", _mode_reader("shorts_max"))
    monkeypatch.setattr(services.shorts_video, "HAS_FASTER_WHISPER", False)
    update = _FakeUpdate()

    assert _dispatch(update) is False
    assert calls["factory"] == []
    assert "SHORTS FACTORY MAX" in update.effective_message.replies[0]
    assert traces == ["rejected:no_whisper"]


def test_shorts_max_refusal_is_silent_in_playlist_mode(
    calls: dict[str, list[tuple[Any, ...]]], traces: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import services.shorts_video

    monkeypatch.setattr(user_modes, "get_user_mode", _mode_reader("shorts_max"))
    monkeypatch.setattr(services.shorts_video, "HAS_FASTER_WHISPER", False)
    update = _FakeUpdate()

    assert _dispatch(update, silent_errors=True) is False
    assert update.effective_message.replies == []


def test_refusal_survives_broken_telegram(
    calls: dict[str, list[tuple[Any, ...]]], traces: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Не смогли предупредить — всё равно возвращаем отказ, а не падаем."""
    import services.shorts_video

    monkeypatch.setattr(user_modes, "get_user_mode", _mode_reader("shorts_max"))
    monkeypatch.setattr(services.shorts_video, "HAS_FASTER_WHISPER", False)

    assert _dispatch(_FakeUpdate(message_fail=True)) is False


def test_false_result_is_reported_as_failed(
    calls: dict[str, list[tuple[Any, ...]]], traces: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_main(_url: str, _update: Any, **_kwargs: Any) -> bool:
        return False

    import pipelines.main_pipeline

    monkeypatch.setattr(pipelines.main_pipeline, "process_single_video", failing_main)
    assert _dispatch(_FakeUpdate()) is False
    assert traces == ["failed"]


def test_exception_is_reported_and_propagated(
    calls: dict[str, list[tuple[Any, ...]]], traces: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exploding_main(_url: str, _update: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("ffmpeg упал")

    import pipelines.main_pipeline

    monkeypatch.setattr(pipelines.main_pipeline, "process_single_video", exploding_main)
    with pytest.raises(RuntimeError, match="ffmpeg упал"):
        _dispatch(_FakeUpdate())
    assert traces == ["error:RuntimeError"]


def test_cancellation_is_not_swallowed(
    calls: dict[str, list[tuple[Any, ...]]], traces: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def cancelled_main(_url: str, _update: Any, **_kwargs: Any) -> bool:
        raise asyncio.CancelledError

    import pipelines.main_pipeline

    monkeypatch.setattr(pipelines.main_pipeline, "process_single_video", cancelled_main)
    with pytest.raises(asyncio.CancelledError):
        _dispatch(_FakeUpdate())
    assert traces == ["cancelled"]


def test_unknown_user_does_not_read_the_database(
    calls: dict[str, list[tuple[Any, ...]]], traces: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Служебный апдейт без пользователя не должен ходить в БД за режимом."""

    asked: list[int] = []

    async def reader(user_id: int) -> str:
        asked.append(user_id)
        return "rus"

    monkeypatch.setattr(user_modes, "get_user_mode", reader)
    assert _dispatch(_FakeUpdate(user_id=None)) is True
    assert asked == []
    assert len(calls["main"]) == 1


def test_effective_user_id_is_int() -> None:
    assert video_dispatch._effective_user_id(_FakeUpdate(user_id="17")) == 17
    assert video_dispatch._effective_user_id(_FakeUpdate(user_id=None)) == 0
    assert video_dispatch._effective_user_id(object()) == 0
