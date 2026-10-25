"""Behavior contract for LiveDub video metadata, thumbnail, and cover assets."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import services.livedub_mix as mix
import services.livedub_qa as qa
from pipelines.livedub_video_assets_stage import (
    get_livedub_technical_warnings,
    prepare_livedub_video_assets,
)


def _prepare(video: Path, media_id: str = "media1", thumbs_dir: Path | None = None):
    return asyncio.run(
        prepare_livedub_video_assets(
            video,
            media_id=media_id,
            thumbs_dir=thumbs_dir or video.parent,
        )
    )


def _technical_warnings(video: Path, *, is_fallback: bool = False):
    return asyncio.run(
        get_livedub_technical_warnings(
            video,
            expected_duration=40,
            is_fallback=is_fallback,
        )
    )


def test_technical_check_is_skipped_for_original_fallback(monkeypatch, tmp_path):
    def unexpected_call(*_args):
        raise AssertionError("technical_check must not run for fallback")

    monkeypatch.setattr(qa, "technical_check", unexpected_call)

    assert _technical_warnings(tmp_path / "original.mp4", is_fallback=True) == []


def test_technical_check_returns_and_logs_each_warning(monkeypatch, tmp_path, caplog):
    video = tmp_path / "dub.mp4"
    calls = []

    def check(path, expected_duration):
        calls.append((path, expected_duration))
        return ["short", "silent"]

    monkeypatch.setattr(qa, "technical_check", check)

    with caplog.at_level(logging.WARNING):
        assert _technical_warnings(video) == ["short", "silent"]
    assert calls == [(video, 40)]
    assert "[LiveDubQA] tech: short" in caplog.text
    assert "[LiveDubQA] tech: silent" in caplog.text


def test_technical_check_failure_is_nonfatal(monkeypatch, tmp_path, caplog):
    def fail(*_args):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(qa, "technical_check", fail)

    with caplog.at_level(logging.WARNING):
        assert _technical_warnings(tmp_path / "dub.mp4") == []
    assert "[LiveDubQA] technical_check сбой: probe failed" in caplog.text


def test_assets_probe_in_executor_and_select_real_cover(monkeypatch, tmp_path):
    video = tmp_path / "dub.mp4"
    video.write_bytes(b"video")
    cover = tmp_path / "media1_thumb.jpg"
    cover.write_bytes(b"x" * 1025)
    thumbnail = object()
    calls = []

    def probe(path):
        calls.append(("probe", path))
        return {"width": 640, "height": 360, "duration": 12.5}

    def make_thumbnail(path):
        calls.append(("thumbnail", path))
        return thumbnail

    monkeypatch.setattr(mix, "probe_video_meta", probe)
    monkeypatch.setattr(mix, "make_video_thumbnail", make_thumbnail)

    assert _prepare(video) == (
        {"width": 640, "height": 360, "duration": 12.5},
        thumbnail,
        cover,
    )
    assert calls == [("probe", video), ("thumbnail", video)]


def test_probe_failure_uses_empty_assets_but_still_selects_cover(monkeypatch, tmp_path, caplog):
    video = tmp_path / "dub.mp4"
    cover = tmp_path / "media1_thumb.jpg"
    cover.write_bytes(b"x" * 1025)
    thumbnail_calls = []

    def probe(_path):
        raise RuntimeError("ffprobe failed")

    monkeypatch.setattr(mix, "probe_video_meta", probe)
    monkeypatch.setattr(mix, "make_video_thumbnail", lambda path: thumbnail_calls.append(path))

    with caplog.at_level(logging.WARNING):
        assert _prepare(video) == ({}, None, cover)
    assert thumbnail_calls == []
    assert "[LiveDub] video meta: ffprobe failed" in caplog.text


def test_thumbnail_failure_preserves_legacy_all_or_nothing_assignment(monkeypatch, tmp_path):
    video = tmp_path / "dub.mp4"

    monkeypatch.setattr(mix, "probe_video_meta", lambda _path: {"width": 640})

    def fail_thumbnail(_path):
        raise RuntimeError("thumbnail failed")

    monkeypatch.setattr(mix, "make_video_thumbnail", fail_thumbnail)

    assert _prepare(video) == ({}, None, None)


def test_only_first_sorted_cover_candidate_controls_selection(monkeypatch, tmp_path):
    video = tmp_path / "dub.mp4"
    (tmp_path / "media1_thumb_a.jpg").write_bytes(b"small")
    (tmp_path / "media1_thumb_b.jpg").write_bytes(b"x" * 2048)
    monkeypatch.setattr(mix, "probe_video_meta", lambda _path: {})
    monkeypatch.setattr(mix, "make_video_thumbnail", lambda _path: None)

    assert _prepare(video)[2] is None


def test_cover_exactly_1024_bytes_is_rejected(monkeypatch, tmp_path):
    video = tmp_path / "dub.mp4"
    (tmp_path / "media1_thumb.jpg").write_bytes(b"x" * 1024)
    monkeypatch.setattr(mix, "probe_video_meta", lambda _path: {})
    monkeypatch.setattr(mix, "make_video_thumbnail", lambda _path: None)

    assert _prepare(video)[2] is None


def test_cover_stat_error_is_nonfatal(monkeypatch, tmp_path):
    video = tmp_path / "dub.mp4"
    cover = tmp_path / "media1_thumb.jpg"
    cover.write_bytes(b"x" * 1025)
    monkeypatch.setattr(mix, "probe_video_meta", lambda _path: {"width": 640})
    monkeypatch.setattr(mix, "make_video_thumbnail", lambda _path: None)
    real_stat = Path.stat

    def broken_stat(path, *args, **kwargs):
        if path == cover:
            raise OSError("stat failed")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", broken_stat)

    assert _prepare(video) == ({"width": 640}, None, None)
