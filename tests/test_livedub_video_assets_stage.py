"""Behavior contract for LiveDub video metadata, thumbnail, and cover assets."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import services.livedub_mix as mix
from pipelines.livedub_video_assets_stage import prepare_livedub_video_assets


def _prepare(video: Path, media_id: str = "media1", thumbs_dir: Path | None = None):
    return asyncio.run(
        prepare_livedub_video_assets(
            video,
            media_id=media_id,
            thumbs_dir=thumbs_dir or video.parent,
        )
    )


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
