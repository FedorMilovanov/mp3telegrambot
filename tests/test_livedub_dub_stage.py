"""Pro-mix selection and ordinary LiveDub fallback keep their exact contract."""

from __future__ import annotations

import asyncio
import logging

import pytest

from pipelines.livedub_dub_stage import make_livedub_video


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
