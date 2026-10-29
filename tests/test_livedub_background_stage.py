"""Behavior contracts for the parallel LiveDub background coordinator."""

from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, cast

import pathlib

import asyncio

import pytest

from pipelines.livedub_background_stage import BackgroundResult, run_livedub_background


async def _wait_video(task: Awaitable[pathlib.Path | None], _workdir: pathlib.Path) -> pathlib.Path | None:
    return await task


def _run(
    tmp_path: pathlib.Path,
    *,
    subtitles_enabled: bool = False,
    translation_subtitles_enabled: bool = False,
    create_subtitles: Callable[..., Coroutine[Any, Any, pathlib.Path | None]] | None = None,
    make_video: Callable[..., Coroutine[Any, Any, pathlib.Path | None]] | None = None,
    wait_video: Callable[[Awaitable[pathlib.Path | None], pathlib.Path], Awaitable[pathlib.Path | None]] | None = None,
    merge_subtitles: Callable[..., Awaitable[pathlib.Path]] | None = None,
    download_original: Callable[..., Awaitable[pathlib.Path]] | None = None,
) -> BackgroundResult:
    dub_path = tmp_path / "dub.mp4"
    dub_path.write_bytes(b"dub")

    async def default_make(*_args: Any, **_kwargs: Any) -> pathlib.Path:
        return dub_path

    async def default_create(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def default_merge(video: pathlib.Path, _srt: pathlib.Path, *, is_fallback: bool) -> pathlib.Path:
        return tmp_path / ("fallback.mp4" if is_fallback else "merged.mp4")

    async def default_download(*_args: Any) -> pathlib.Path:
        original = tmp_path / "original.mp4"
        original.write_bytes(b"original")
        return original

    return asyncio.run(
        run_livedub_background(
            "https://example.test/video",
            tmp_path,
            source_lang="en",
            duration=42,
            subtitles_enabled=subtitles_enabled,
            translation_subtitles_enabled=translation_subtitles_enabled,
            pro_mix=True,
            create_subtitles=create_subtitles or default_create,
            make_video=make_video or default_make,
            wait_video=wait_video or _wait_video,
            merge_subtitles=merge_subtitles or default_merge,
            download_original=download_original or default_download,
        )
    )


def test_dub_without_subtitles_returns_plain_video_and_threads_arguments(tmp_path: pathlib.Path) -> None:
    calls: list[Any] = []

    async def make_video(*args: Any, **kwargs: Any) -> pathlib.Path:
        calls.append((args, kwargs))
        path = tmp_path / "plain.mp4"
        path.write_bytes(b"video")
        return path

    result = _run(tmp_path, make_video=make_video)

    assert result == (tmp_path / "plain.mp4", False, False)
    assert calls == [
        (
            ("https://example.test/video", tmp_path),
            {"pro_mix": True, "duration": 42, "source_lang": "en"},
        )
    ]


def test_dub_and_subtitles_are_merged_after_translation_subtitles_finish(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    srt = tmp_path / "gemini.srt"
    srt.write_text("subtitle", encoding="utf-8")
    calls: list[Any] = []

    async def create_subtitles(*args: Any, **kwargs: Any) -> pathlib.Path:
        calls.append(("create", args, kwargs))
        return srt

    async def merge_subtitles(video: pathlib.Path, subtitle_path: pathlib.Path, *, is_fallback: bool) -> pathlib.Path:
        calls.append(("merge", video, subtitle_path, is_fallback))
        merged = tmp_path / "with-subs.mp4"
        merged.write_bytes(b"merged")
        return merged

    translation_finished = False

    async def translation_subtitles(url: str, workdir: pathlib.Path) -> None:
        nonlocal translation_finished
        translation_finished = True
        assert (url, workdir) == ("https://example.test/video", tmp_path)
        return None

    monkeypatch.setattr(
        "services.yandex_live_dub.get_translation_subtitles",
        translation_subtitles,
    )
    result = _run(
        tmp_path,
        subtitles_enabled=True,
        translation_subtitles_enabled=True,
        create_subtitles=create_subtitles,
        merge_subtitles=merge_subtitles,
    )

    assert result == (tmp_path / "with-subs.mp4", False, True)
    assert translation_finished
    assert calls[0] == (
        "create",
        ("https://example.test/video", tmp_path),
        {"known_duration": 42, "lang": "en"},
    )
    assert calls[1][0] == "merge"
    assert calls[1][2:] == (srt, False)


def test_subtitle_failure_is_noncritical(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    async def create_subtitles(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("subtitle unavailable")

    result = _run(
        tmp_path,
        subtitles_enabled=True,
        create_subtitles=create_subtitles,
    )

    assert result == (tmp_path / "dub.mp4", False, False)
    assert "subtitle unavailable" in caplog.text


def test_translation_subtitle_failure_is_noncritical(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def translation_subtitles(*_args: Any) -> None:
        raise RuntimeError("translation subtitles unavailable")

    monkeypatch.setattr(
        "services.yandex_live_dub.get_translation_subtitles",
        translation_subtitles,
    )
    result = _run(tmp_path, translation_subtitles_enabled=True)

    assert result == (tmp_path / "dub.mp4", False, False)
    assert "translation subtitles unavailable" in caplog.text


def test_missing_dub_with_subtitles_downloads_original_and_marks_fallback(tmp_path: pathlib.Path) -> None:
    srt = tmp_path / "gemini.srt"
    srt.write_text("subtitle", encoding="utf-8")
    original = tmp_path / "source.mp4"
    original.write_bytes(b"source")
    calls: list[Any] = []

    async def make_video(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def create_subtitles(*_args: Any, **_kwargs: Any) -> pathlib.Path:
        return srt

    async def download_original(*args: Any) -> pathlib.Path:
        calls.append(("download", args))
        return original

    async def merge_subtitles(video: pathlib.Path, subtitle_path: pathlib.Path, *, is_fallback: bool) -> pathlib.Path:
        calls.append(("merge", video, subtitle_path, is_fallback))
        fallback = tmp_path / "fallback-result.mp4"
        fallback.write_bytes(b"fallback")
        return fallback

    result = _run(
        tmp_path,
        subtitles_enabled=True,
        make_video=make_video,
        create_subtitles=create_subtitles,
        download_original=download_original,
        merge_subtitles=merge_subtitles,
    )

    assert result == (tmp_path / "fallback-result.mp4", True, True)
    assert calls == [
        ("download", ("https://example.test/video", tmp_path)),
        ("merge", original, srt, True),
    ]


def test_missing_dub_without_subtitles_returns_no_result(tmp_path: pathlib.Path) -> None:
    async def make_video(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def unexpected_download(*_args: Any) -> pathlib.Path:
        raise AssertionError("original download must not run without subtitles")

    result = _run(
        tmp_path,
        make_video=make_video,
        download_original=unexpected_download,
    )

    assert result == (None, False, False)


def test_background_error_is_logged_and_normalized(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    async def wait_video(_task: Awaitable[pathlib.Path | None], _workdir: pathlib.Path) -> None:
        raise RuntimeError("dub failed")

    result = _run(tmp_path, wait_video=wait_video)

    assert result == (None, False, False)
    assert "[LiveDub Background] Ошибка: dub failed" in caplog.text


def test_parent_cancellation_cancels_every_pending_child(tmp_path: pathlib.Path) -> None:
    async def scenario() -> None:
        started = [asyncio.Event() for _ in range(3)]
        cancelled: list[int] = []

        async def blocking_child(index: int) -> None:
            started[index].set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(index)

        async def create_subtitles(*_args: Any, **_kwargs: Any) -> None:
            return await blocking_child(0)

        async def make_video(*_args: Any, **_kwargs: Any) -> None:
            return await blocking_child(1)

        async def translation_subtitles(*_args: Any) -> None:
            return await blocking_child(2)

        import services.yandex_live_dub as yandex_live_dub

        original = yandex_live_dub.get_translation_subtitles
        yandex_live_dub.get_translation_subtitles = cast(Any, translation_subtitles)
        try:
            parent = asyncio.create_task(
                run_livedub_background(
                    "url",
                    tmp_path,
                    source_lang="en",
                    duration=1,
                    subtitles_enabled=True,
                    translation_subtitles_enabled=True,
                    pro_mix=False,
                    create_subtitles=create_subtitles,
                    make_video=make_video,
                    wait_video=_wait_video,
                    merge_subtitles=cast(Any, None),
                    download_original=cast(Any, None),
                )
            )
            await asyncio.gather(*(event.wait() for event in started))
            parent.cancel()
            with pytest.raises(asyncio.CancelledError):
                await parent
        finally:
            yandex_live_dub.get_translation_subtitles = original

        assert sorted(cancelled) == [0, 1, 2]

    asyncio.run(scenario())


def test_teardown_base_exception_does_not_replace_result(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        cancelled = False

        async def translation_subtitles(*_args: Any) -> None:
            nonlocal cancelled
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled = True

        async def make_video(*_args: Any, **_kwargs: Any) -> None:
            return None

        async def fail_wait(_task: Awaitable[pathlib.Path | None], _workdir: pathlib.Path) -> None:
            await started.wait()
            raise RuntimeError("primary failure")

        async def broken_gather(*_args: Any, **_kwargs: Any) -> None:
            raise BaseException("teardown failure")

        monkeypatch.setattr(
            "services.yandex_live_dub.get_translation_subtitles",
            translation_subtitles,
        )
        monkeypatch.setattr(asyncio, "gather", broken_gather)
        result = await run_livedub_background(
            "url",
            tmp_path,
            source_lang="en",
            duration=1,
            subtitles_enabled=False,
            translation_subtitles_enabled=True,
            pro_mix=False,
            create_subtitles=cast(Any, None),
            make_video=make_video,
            wait_video=fail_wait,
            merge_subtitles=cast(Any, None),
            download_original=cast(Any, None),
        )

        assert result == (None, False, False)
        await asyncio.sleep(0)
        assert cancelled

    asyncio.run(scenario())
