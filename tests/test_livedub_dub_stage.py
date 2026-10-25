"""Pro-mix selection and ordinary LiveDub fallback keep their exact contract."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from pipelines.livedub_dub_stage import make_livedub_video, wait_for_livedub_video


def test_plain_path_uses_original_live_dub_arguments(monkeypatch, tmp_path):
    import services.livedub_mix as mix
    import services.yandex_live_dub as live_dub

    live_path = tmp_path / "live.mp4"
    pro_calls = []
    live_calls = []

    async def pro(*args, **kwargs):
        pro_calls.append((args, kwargs))
        return live_path

    async def live(*args, **kwargs):
        live_calls.append((args, kwargs))
        return live_path

    monkeypatch.setattr(mix, "build_pro_dub", pro)
    monkeypatch.setattr(live_dub, "get_live_dub_video", live)

    result = asyncio.run(
        make_livedub_video(
            "video-url",
            tmp_path,
            pro_mix=False,
            duration=600,
            source_lang="ru",
        )
    )

    assert result == live_path
    assert pro_calls == []
    assert live_calls == [
        (
            ("video-url", tmp_path),
            {
                "original_volume": 0.3,
                "translation_volume": 1.5,
                "keep_original_audio": True,
                "duration": 600,
                "lang": "ru",
            },
        )
    ]


def test_existing_pro_mix_wins_without_fallback(monkeypatch, tmp_path):
    import services.livedub_mix as mix
    import services.yandex_live_dub as live_dub

    pro_path = tmp_path / "pro.mp4"
    pro_path.write_bytes(b"video")
    pro_calls = []
    live_calls = []

    async def pro(*args, **kwargs):
        pro_calls.append((args, kwargs))
        return pro_path

    async def live(*args, **kwargs):
        live_calls.append((args, kwargs))
        return tmp_path / "unexpected.mp4"

    monkeypatch.setattr(mix, "build_pro_dub", pro)
    monkeypatch.setattr(live_dub, "get_live_dub_video", live)

    result = asyncio.run(
        make_livedub_video(
            "video-url",
            tmp_path,
            pro_mix=True,
            duration=600,
            source_lang="de",
        )
    )

    assert result == pro_path
    assert pro_calls == [(("video-url", tmp_path), {"duration": 600, "lang": "de"})]
    assert live_calls == []


def test_missing_pro_mix_falls_back_with_legacy_log(monkeypatch, tmp_path, caplog):
    import services.livedub_mix as mix
    import services.yandex_live_dub as live_dub

    live_path = tmp_path / "live.mp4"

    async def pro(*_args, **_kwargs):
        return None

    async def live(*_args, **_kwargs):
        return live_path

    monkeypatch.setattr(mix, "build_pro_dub", pro)
    monkeypatch.setattr(live_dub, "get_live_dub_video", live)

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(
            make_livedub_video(
                "video-url",
                tmp_path,
                pro_mix=True,
                duration=600,
            )
        )

    assert result == live_path
    assert "pro-микс не собрался — fallback на vot-merge" in caplog.text


def test_pro_runtime_error_reaches_background_error_policy(monkeypatch, tmp_path):
    import services.livedub_mix as mix
    import services.yandex_live_dub as live_dub

    live_calls = []

    async def pro(*_args, **_kwargs):
        raise RuntimeError("LIVEDUB_NOT_AVAILABLE")

    async def live(*args, **kwargs):
        live_calls.append((args, kwargs))
        return tmp_path / "unexpected.mp4"

    monkeypatch.setattr(mix, "build_pro_dub", pro)
    monkeypatch.setattr(live_dub, "get_live_dub_video", live)

    with pytest.raises(RuntimeError, match="LIVEDUB_NOT_AVAILABLE"):
        asyncio.run(
            make_livedub_video(
                "video-url",
                tmp_path,
                pro_mix=True,
                duration=600,
            )
        )
    assert live_calls == []


def test_other_pro_error_falls_back_with_legacy_log(monkeypatch, tmp_path, caplog):
    import services.livedub_mix as mix
    import services.yandex_live_dub as live_dub

    live_path = tmp_path / "live.mp4"

    async def pro(*_args, **_kwargs):
        raise ValueError("bad mix")

    async def live(*_args, **_kwargs):
        return live_path

    monkeypatch.setattr(mix, "build_pro_dub", pro)
    monkeypatch.setattr(live_dub, "get_live_dub_video", live)

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(
            make_livedub_video(
                "video-url",
                tmp_path,
                pro_mix=True,
                duration=600,
            )
        )

    assert result == live_path
    assert "pro-микс сбой (bad mix) — fallback на vot-merge" in caplog.text


def _wait_with_error(error, workdir):
    async def task():
        raise error

    return asyncio.run(wait_for_livedub_video(task(), workdir))


def test_wait_for_dub_returns_success_without_markers(tmp_path):
    expected = tmp_path / "dub.mp4"

    async def task():
        return expected

    result = asyncio.run(wait_for_livedub_video(task(), tmp_path))

    assert result == expected
    assert not (tmp_path / ".auth_required").exists()
    assert not (tmp_path / ".not_available").exists()
    assert not (tmp_path / ".livedub_failed").exists()


@pytest.mark.parametrize(
    ("error", "marker", "expected_text", "expected_log"),
    [
        (
            RuntimeError("LIVEDUB_AUTH_REQUIRED"),
            ".auth_required",
            "1",
            "Яндекс требует OAuth-токен",
        ),
        (
            RuntimeError("LIVEDUB_NOT_AVAILABLE"),
            ".not_available",
            "1",
            "Перевод недоступен для этого видео",
        ),
        (
            RuntimeError("runtime failure"),
            ".livedub_failed",
            "runtime failure",
            "[LiveDub] Ошибка: runtime failure",
        ),
        (
            ValueError("unknown failure"),
            ".livedub_failed",
            "unknown failure",
            "[LiveDub] Неизвестная ошибка: unknown failure",
        ),
    ],
)
def test_wait_for_dub_records_historical_error_policy(error, marker, expected_text, expected_log, tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        result = _wait_with_error(error, tmp_path)

    assert result is None
    assert (tmp_path / marker).read_text(encoding="utf-8") == expected_text
    assert expected_log in caplog.text


def test_wait_for_dub_truncates_failure_marker(tmp_path):
    result = _wait_with_error(RuntimeError("x" * 600), tmp_path)

    assert result is None
    assert (tmp_path / ".livedub_failed").read_text(encoding="utf-8") == "x" * 500


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("LIVEDUB_AUTH_REQUIRED"),
        RuntimeError("LIVEDUB_NOT_AVAILABLE"),
        RuntimeError("runtime failure"),
        ValueError("unknown failure"),
    ],
)
def test_wait_for_dub_ignores_marker_write_os_errors(error, tmp_path, monkeypatch):
    def broken_write(*_args, **_kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "write_text", broken_write)

    assert _wait_with_error(error, tmp_path) is None


def test_wait_for_dub_propagates_cancellation_without_failed_marker(tmp_path):
    async def task():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(wait_for_livedub_video(task(), tmp_path))

    assert not (tmp_path / ".livedub_failed").exists()
