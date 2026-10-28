from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pipelines import clips as clips_pipeline
from pipelines import montage
from pipelines import shorts as shorts_pipeline
from pipelines import shorts_factory
from services import livedub_delivery_coordinator as coordinator
from services import shorts_static_policy


def _write_file(path: Path, data: bytes = b"x" * 2048) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _exists(path: Path) -> bool:
    return path.exists()


def test_factory_validated_source_persistence_and_missing_source(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(shorts_factory, "DOWNLOAD_DIR", tmp_path / "downloads")
    source = _write_file(tmp_path / "source.mp4")

    persisted = shorts_factory._persist_validated_factory_source(source, "media")

    assert persisted.name == "media_factory_source.mp4"
    assert persisted.read_bytes() == b"x" * 2048
    assert not source.exists()
    with pytest.raises(RuntimeError, match="видеоисточник"):
        shorts_factory._persist_validated_factory_source(tmp_path / "missing.mp4", "media")


def test_factory_cleanup_honors_keep_source_and_removes_workdir(tmp_path: Path) -> None:
    mp3 = _write_file(tmp_path / "audio.mp3")
    persisted = _write_file(tmp_path / "source.mp4")
    workdir = _write_file(tmp_path / "work" / "artifact.txt", b"artifact").parent

    shorts_factory._cleanup_factory_files(
        mp3_path=mp3,
        persistent_source_path=persisted,
        keep_source=False,
        workdir=workdir,
    )

    assert not mp3.exists()
    assert not persisted.exists()
    assert not workdir.exists()

    kept = _write_file(tmp_path / "kept.mp4")
    second_workdir = _write_file(tmp_path / "work-two" / "artifact.txt", b"artifact").parent
    shorts_factory._cleanup_factory_files(
        mp3_path=None,
        persistent_source_path=kept,
        keep_source=True,
        workdir=second_workdir,
    )
    assert kept.exists()
    assert not second_workdir.exists()


@pytest.mark.asyncio
async def test_deferral_recovers_source_from_audio_name(monkeypatch, tmp_path: Path) -> None:
    source = _write_file(tmp_path / "source.mp3", b"source audio")
    monkeypatch.setattr(coordinator.tempfile, "gettempdir", lambda: str(tmp_path / "deferred"))
    delivery = coordinator.create_source_audio_deferral(bot=SimpleNamespace(), chat_id=7, reply_to=9, enabled=True)
    message = SimpleNamespace(reply_audio=AsyncMock())

    placeholder = await delivery.send_or_defer(
        message,
        audio=SimpleNamespace(name=str(source)),
        fallback_path=tmp_path / "missing.mp3",
        title="Source",
    )

    assert placeholder.audio.file_id == ""
    deferred_path = delivery._audio_path
    assert deferred_path is not None
    assert _read_bytes(deferred_path) == b"source audio"
    delivery.discard("test complete")
    await asyncio.sleep(0)
    assert not delivery.has_pending
    assert not _exists(deferred_path)
    message.reply_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_static_policy_missing_file_fails_safe_without_probe(monkeypatch, tmp_path: Path) -> None:
    run = AsyncMock()
    monkeypatch.setattr(shorts_static_policy.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(shorts_static_policy, "run_cancellable_process", run)

    assert await shorts_static_policy._is_static_video_confident(tmp_path / "missing.mp4") is False
    run.assert_not_awaited()


def test_clips_cleanup_removes_outputs_and_owned_source(monkeypatch, tmp_path: Path) -> None:
    clip = _write_file(tmp_path / "clip.mp4")
    snapshot = _write_file(tmp_path / "snapshot.png")
    source = _write_file(tmp_path / "source.mp4")
    monkeypatch.setattr(clips_pipeline, "settings_get", lambda key: False)

    clips_pipeline._cleanup_clip_files([clip], [snapshot], source, borrowed_video=False)

    assert not clip.exists()
    assert not snapshot.exists()
    assert not source.exists()


def test_clips_cleanup_preserves_borrowed_source(tmp_path: Path) -> None:
    source = _write_file(tmp_path / "borrowed.mp4")

    clips_pipeline._cleanup_clip_files([], [], source, borrowed_video=True)

    assert source.exists()


def test_shorts_cleanup_removes_outputs_and_owned_source(tmp_path: Path) -> None:
    short = _write_file(tmp_path / "short.mp4")
    poster = _write_file(tmp_path / "poster.jpg")
    source = _write_file(tmp_path / "source.mp4")
    livedub_source = _write_file(tmp_path / "livedub.mp4")

    shorts_pipeline._cleanup_short_files(
        [short],
        [poster],
        source,
        livedub_source,
        keep_for_montage=False,
    )

    assert not short.exists()
    assert not poster.exists()
    assert not source.exists()
    assert livedub_source.exists()


def test_shorts_cleanup_preserves_borrowed_livedub_source(tmp_path: Path) -> None:
    source = _write_file(tmp_path / "livedub.mp4")

    shorts_pipeline._cleanup_short_files([], [], source, source, keep_for_montage=False)

    assert source.exists()


def test_clips_cleanup_suppresses_settings_failure(monkeypatch, tmp_path: Path) -> None:
    source = _write_file(tmp_path / "source.mp4")

    def _fail_settings(key: str) -> bool:
        raise RuntimeError(key)

    monkeypatch.setattr(clips_pipeline, "settings_get", _fail_settings)

    clips_pipeline._cleanup_clip_files([], [], source, borrowed_video=False)

    assert source.exists()


@pytest.mark.asyncio
async def test_montage_thumbnail_prefers_ready_poster(monkeypatch, tmp_path: Path) -> None:
    poster = _write_file(tmp_path / "poster.jpg")
    create_poster = AsyncMock(return_value=True)
    create_snapshot = AsyncMock(return_value=True)
    monkeypatch.setattr(montage, "create_short_title_poster", create_poster)
    monkeypatch.setattr(montage, "create_short_snapshot", create_snapshot)

    thumbnail = await montage._select_optional_thumbnail(
        tmp_path / "video.mp4",
        poster,
        tmp_path / "snapshot.jpg",
        "Title",
        30.0,
        do_poster=True,
        do_snapshot=True,
    )

    assert thumbnail is not None
    assert thumbnail.filename == poster.name
    create_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_montage_thumbnail_falls_back_to_snapshot_after_poster_failure(monkeypatch, tmp_path: Path) -> None:
    snapshot = _write_file(tmp_path / "snapshot.jpg")
    monkeypatch.setattr(
        montage,
        "create_short_title_poster",
        AsyncMock(side_effect=RuntimeError("poster failed")),
    )
    monkeypatch.setattr(montage, "create_short_snapshot", AsyncMock(return_value=True))

    thumbnail = await montage._select_optional_thumbnail(
        tmp_path / "video.mp4",
        tmp_path / "poster.jpg",
        snapshot,
        "Title",
        30.0,
        do_poster=True,
        do_snapshot=True,
    )

    assert thumbnail is not None
    assert thumbnail.filename == snapshot.name


@pytest.mark.asyncio
async def test_montage_thumbnail_returns_none_after_both_failures(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        montage,
        "create_short_title_poster",
        AsyncMock(side_effect=RuntimeError("poster failed")),
    )
    monkeypatch.setattr(
        montage,
        "create_short_snapshot",
        AsyncMock(side_effect=RuntimeError("snapshot failed")),
    )

    thumbnail = await montage._select_optional_thumbnail(
        tmp_path / "video.mp4",
        tmp_path / "poster.jpg",
        tmp_path / "snapshot.jpg",
        "Title",
        30.0,
        do_poster=True,
        do_snapshot=True,
    )

    assert thumbnail is None


def test_shorts_cleanup_suppresses_source_unlink_oserror(monkeypatch, tmp_path: Path) -> None:
    source = _write_file(tmp_path / "source.mp4")

    def _fail_unlink(self: Path, *, missing_ok: bool = False) -> None:
        raise OSError("locked")

    monkeypatch.setattr(Path, "unlink", _fail_unlink)

    shorts_pipeline._cleanup_short_files([], [], source, None, keep_for_montage=False)

    assert source.exists()
