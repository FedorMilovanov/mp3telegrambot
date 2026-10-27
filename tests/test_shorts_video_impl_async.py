from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import eng_subtitles, shorts_video_impl


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _write_download_candidates(directory: Path, media_id: str) -> None:
    _write_bytes(directory / f"{media_id}_video.webm", b"webm")
    _write_bytes(directory / f"{media_id}_video.mp4", b"mp4")


def _patch_render_runtime(monkeypatch, run) -> None:
    async def no_snap(path, target, search_window=5.0):
        return target

    async def no_crop(path, start_seconds=0.0):
        return ""

    monkeypatch.setattr(shorts_video_impl.shutil, "which", lambda name: "/ffmpeg")
    monkeypatch.setattr(shorts_video_impl, "_find_silence_end", no_snap)
    monkeypatch.setattr(shorts_video_impl, "_detect_black_bars", no_crop)
    monkeypatch.setattr(
        shorts_video_impl,
        "_get_video_encoder",
        lambda: ("libx264", ["-crf", "23"], ["-preset", "veryfast"]),
    )
    monkeypatch.setattr(shorts_video_impl, "run_cancellable_process", run)


@pytest.mark.asyncio
async def test_shorts_download_reuses_verified_workdir_video(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "workdir"
    download_dir = tmp_path / "downloads"
    audio = workdir / "original_video.f140.m4a"
    video = workdir / "original_video.mp4"
    _write_bytes(audio, b"audio")
    _write_bytes(video, b"video")
    run = AsyncMock()
    monkeypatch.setattr(shorts_video_impl, "DOWNLOAD_DIR", download_dir)
    monkeypatch.setattr(shorts_video_impl, "run_cancellable_process", run)
    monkeypatch.setattr(eng_subtitles, "_has_video_stream", lambda path: path.suffix != ".m4a")

    result = await shorts_video_impl._unowned_download_video_for_shorts(
        "https://example.test/video",
        "media",
        workdir,
    )

    assert result == video
    run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("workdir_kind", ["missing", "rejected"])
async def test_shorts_download_falls_back_when_workdir_has_no_reusable_video(
    monkeypatch,
    tmp_path: Path,
    workdir_kind: str,
) -> None:
    workdir = tmp_path / "workdir"
    download_dir = tmp_path / "downloads"
    if workdir_kind == "rejected":
        _write_bytes(workdir / "original_video.part", b"partial")
        _write_bytes(workdir / "original_video.m4a", b"audio")
    monkeypatch.setattr(shorts_video_impl, "DOWNLOAD_DIR", download_dir)
    monkeypatch.setattr(eng_subtitles, "_has_video_stream", lambda path: False)

    async def fake_run(command, **kwargs):
        await asyncio.to_thread(_write_download_candidates, download_dir, "media")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(shorts_video_impl, "run_cancellable_process", fake_run)

    result = await shorts_video_impl._unowned_download_video_for_shorts(
        "https://example.test/video",
        "media",
        workdir,
    )

    assert result == download_dir / "media_video.mp4"


@pytest.mark.asyncio
async def test_shorts_download_discovers_preferred_video_after_process(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(shorts_video_impl, "DOWNLOAD_DIR", tmp_path)

    async def fake_run(command, **kwargs):
        await asyncio.to_thread(_write_download_candidates, tmp_path, "media")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(shorts_video_impl, "run_cancellable_process", fake_run)

    result = await shorts_video_impl._unowned_download_video_for_shorts("https://example.test/video", "media")

    assert result == tmp_path / "media_video.mp4"
    assert result is not None and result.read_bytes() == b"mp4"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (None, False),
        (b"", False),
        (b"x" * 10_239, False),
        (b"x" * 10_240, True),
    ],
    ids=["missing", "empty", "below-gate", "at-gate"],
)
async def test_legacy_short_render_uses_single_nonempty_size_gate(
    monkeypatch,
    tmp_path: Path,
    payload: bytes | None,
    expected: bool,
) -> None:
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "short.mp4"
    _write_bytes(source_path, b"source")

    async def fake_run(command, **kwargs):
        if payload is not None:
            await asyncio.to_thread(_write_bytes, output_path, payload)
        return SimpleNamespace(returncode=0, stderr="")

    _patch_render_runtime(monkeypatch, fake_run)

    result = await shorts_video_impl._unowned_render_short_clip(source_path, output_path, 10, 40)

    assert result is expected
    if expected:
        assert output_path.read_bytes() == payload
    elif payload:
        assert not output_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [(None, False), (b"", False), (b"processed", True)],
    ids=["missing", "empty", "nonempty"],
)
async def test_legacy_short_transform_validates_nonempty_output(
    monkeypatch,
    tmp_path: Path,
    payload: bytes | None,
    expected: bool,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "processed.mp4"
    _write_bytes(input_path, b"input")

    async def fake_run(command, **kwargs):
        if payload is not None:
            await asyncio.to_thread(_write_bytes, output_path, payload)
        return SimpleNamespace(returncode=0, stderr="")

    _patch_render_runtime(monkeypatch, fake_run)

    result = await shorts_video_impl._unowned_short_transform(
        input_path,
        output_path,
        normalize_audio=False,
        speed=1.5,
    )

    assert result is expected
    if expected:
        assert output_path.read_bytes() == payload


@pytest.mark.asyncio
async def test_legacy_short_noop_transform_copies_in_owned_worker(monkeypatch, tmp_path: Path) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "processed.mp4"
    _write_bytes(input_path, b"input")
    run = AsyncMock()
    monkeypatch.setattr(shorts_video_impl.shutil, "which", lambda name: "/ffmpeg")
    monkeypatch.setattr(shorts_video_impl, "run_cancellable_process", run)

    result = await shorts_video_impl._unowned_short_transform(
        input_path,
        output_path,
        normalize_audio=False,
        speed=1.0,
    )

    assert result is True
    assert output_path.read_bytes() == b"input"
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_short_transform_missing_input_does_not_start_process(monkeypatch, tmp_path: Path) -> None:
    run = AsyncMock()
    monkeypatch.setattr(shorts_video_impl.shutil, "which", lambda name: "/ffmpeg")
    monkeypatch.setattr(shorts_video_impl, "run_cancellable_process", run)

    result = await shorts_video_impl._unowned_short_transform(
        tmp_path / "missing.mp4",
        tmp_path / "processed.mp4",
        normalize_audio=False,
        speed=1.5,
    )

    assert result is False
    run.assert_not_awaited()
