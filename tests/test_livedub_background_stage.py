"""Behavior contracts for the parallel LiveDub background coordinator."""

import asyncio

import pytest

from pipelines.livedub_background_stage import run_livedub_background


async def _wait_video(task, _workdir):
    return await task


def _run(
    tmp_path,
    *,
    subtitles_enabled=False,
    translation_subtitles_enabled=False,
    create_subtitles=None,
    make_video=None,
    wait_video=None,
    merge_subtitles=None,
    download_original=None,
):
    dub_path = tmp_path / "dub.mp4"
    dub_path.write_bytes(b"dub")

    async def default_make(*_args, **_kwargs):
        return dub_path

    async def default_create(*_args, **_kwargs):
        return None

    async def default_merge(video, _srt, *, is_fallback):
        return tmp_path / ("fallback.mp4" if is_fallback else "merged.mp4")

    async def default_download(*_args):
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


def test_dub_without_subtitles_returns_plain_video_and_threads_arguments(tmp_path):
    calls = []

    async def make_video(*args, **kwargs):
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


def test_dub_and_subtitles_are_merged_after_translation_subtitles_finish(tmp_path, monkeypatch):
    srt = tmp_path / "gemini.srt"
    srt.write_text("subtitle", encoding="utf-8")
    calls = []

    async def create_subtitles(*args, **kwargs):
        calls.append(("create", args, kwargs))
        return srt

    async def merge_subtitles(video, subtitle_path, *, is_fallback):
        calls.append(("merge", video, subtitle_path, is_fallback))
        merged = tmp_path / "with-subs.mp4"
        merged.write_bytes(b"merged")
        return merged

    translation_finished = False

    async def translation_subtitles(url, workdir):
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


def test_subtitle_failure_is_noncritical(tmp_path, caplog):
    async def create_subtitles(*_args, **_kwargs):
        raise RuntimeError("subtitle unavailable")

    result = _run(
        tmp_path,
        subtitles_enabled=True,
        create_subtitles=create_subtitles,
    )

    assert result == (tmp_path / "dub.mp4", False, False)
    assert "subtitle unavailable" in caplog.text


def test_translation_subtitle_failure_is_noncritical(tmp_path, monkeypatch, caplog):
    async def translation_subtitles(*_args):
        raise RuntimeError("translation subtitles unavailable")

    monkeypatch.setattr(
        "services.yandex_live_dub.get_translation_subtitles",
        translation_subtitles,
    )
    result = _run(tmp_path, translation_subtitles_enabled=True)

    assert result == (tmp_path / "dub.mp4", False, False)
    assert "translation subtitles unavailable" in caplog.text


def test_missing_dub_with_subtitles_downloads_original_and_marks_fallback(tmp_path):
    srt = tmp_path / "gemini.srt"
    srt.write_text("subtitle", encoding="utf-8")
    original = tmp_path / "source.mp4"
    original.write_bytes(b"source")
    calls = []

    async def make_video(*_args, **_kwargs):
        return None

    async def create_subtitles(*_args, **_kwargs):
        return srt

    async def download_original(*args):
        calls.append(("download", args))
        return original

    async def merge_subtitles(video, subtitle_path, *, is_fallback):
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


def test_missing_dub_without_subtitles_returns_no_result(tmp_path):
    async def make_video(*_args, **_kwargs):
        return None

    async def unexpected_download(*_args):
        raise AssertionError("original download must not run without subtitles")

    result = _run(
        tmp_path,
        make_video=make_video,
        download_original=unexpected_download,
    )

    assert result == (None, False, False)


def test_background_error_is_logged_and_normalized(tmp_path, caplog):
    async def wait_video(_task, _workdir):
        raise RuntimeError("dub failed")

    result = _run(tmp_path, wait_video=wait_video)

    assert result == (None, False, False)
    assert "[LiveDub Background] Ошибка: dub failed" in caplog.text


def test_parent_cancellation_cancels_every_pending_child(tmp_path):
    async def scenario():
        started = [asyncio.Event() for _ in range(3)]
        cancelled = []

        async def blocking_child(index):
            started[index].set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(index)

        async def create_subtitles(*_args, **_kwargs):
            return await blocking_child(0)

        async def make_video(*_args, **_kwargs):
            return await blocking_child(1)

        async def translation_subtitles(*_args):
            return await blocking_child(2)

        import services.yandex_live_dub as yandex_live_dub

        original = yandex_live_dub.get_translation_subtitles
        yandex_live_dub.get_translation_subtitles = translation_subtitles
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
                    merge_subtitles=None,
                    download_original=None,
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


def test_teardown_base_exception_does_not_replace_result(tmp_path, monkeypatch):
    async def scenario():
        started = asyncio.Event()
        cancelled = False

        async def translation_subtitles(*_args):
            nonlocal cancelled
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled = True

        async def make_video(*_args, **_kwargs):
            return None

        async def fail_wait(_task, _workdir):
            await started.wait()
            raise RuntimeError("primary failure")

        async def broken_gather(*_args, **_kwargs):
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
            create_subtitles=None,
            make_video=make_video,
            wait_video=fail_wait,
            merge_subtitles=None,
            download_original=None,
        )

        assert result == (None, False, False)
        await asyncio.sleep(0)
        assert cancelled

    asyncio.run(scenario())
