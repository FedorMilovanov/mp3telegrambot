from __future__ import annotations

import ast
import asyncio
import subprocess
from pathlib import Path

import pytest

from services import shorts_video


SOURCE_PATH = Path("services/shorts_video.py")
FUNCTIONS = (
    "_unowned_render_short_clip",
    "_unowned_short_transform",
    "_unowned_create_short_title_poster",
    "_unowned_create_short_snapshot",
)


def _function_source(name: str) -> str:
    source = SOURCE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.AsyncFunctionDef) and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


def _configure_render(monkeypatch, owner) -> None:
    async def no_snap(path, target, search_window=5.0):
        return target

    async def no_crop(path, start_seconds=0.0):
        return ""

    monkeypatch.setattr(shorts_video.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(shorts_video, "_find_silence_end", no_snap)
    monkeypatch.setattr(shorts_video, "_detect_black_bars", no_crop)
    monkeypatch.setattr(
        shorts_video,
        "_get_video_encoder",
        lambda: ("libx264", ["-crf", "23"], ["-preset", "veryfast"]),
    )
    monkeypatch.setattr(shorts_video, "run_cancellable_process", owner)


def test_active_short_outputs_have_transactional_contract() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")
    selected = "\n".join(_function_source(name) for name in FUNCTIONS)

    assert "def _same_short_path(" in source
    assert "def _unlink_short_paths(" in source
    assert selected.count("except asyncio.CancelledError:") == 4
    assert selected.count("_unlink_short_paths(") >= 14
    assert "protected=(source_video_path,)" in selected
    assert "protected=(input_path,)" in selected
    assert "protected=(video_path,)" in selected
    assert "_same_short_path(input_path, output_path)" in selected


@pytest.mark.asyncio
async def test_stale_render_is_not_accepted_when_ffmpeg_writes_nothing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "short.mp4"
    source_path.write_bytes(b"source")
    output_path.write_bytes(b"old valid short" * 2048)

    async def empty_success(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "", "")

    _configure_render(monkeypatch, empty_success)

    result = await shorts_video._unowned_render_short_clip(
        source_path,
        output_path,
        10,
        40,
    )

    assert result is False
    assert output_path.exists() is False


@pytest.mark.asyncio
async def test_render_cancellation_removes_partial_output(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "short.mp4"
    source_path.write_bytes(b"source")
    output_path.write_bytes(b"old short")

    async def cancelled_owner(command, **kwargs):
        Path(command[-1]).write_bytes(b"partial short")
        raise asyncio.CancelledError

    _configure_render(monkeypatch, cancelled_owner)

    with pytest.raises(asyncio.CancelledError):
        await shorts_video._unowned_render_short_clip(
            source_path,
            output_path,
            10,
            40,
        )

    assert output_path.exists() is False
    assert source_path.read_bytes() == b"source"


@pytest.mark.asyncio
async def test_noop_transform_preserves_same_path_input(tmp_path: Path) -> None:
    media_path = tmp_path / "same.mp4"
    media_path.write_bytes(b"same path media")

    result = await shorts_video._unowned_short_transform(
        media_path,
        media_path,
        normalize_audio=False,
        speed=1.0,
    )

    assert result is True
    assert media_path.read_bytes() == b"same path media"


@pytest.mark.asyncio
async def test_filtered_transform_rejects_same_path_without_deleting_input(
    tmp_path: Path,
) -> None:
    media_path = tmp_path / "same.mp4"
    media_path.write_bytes(b"same path media")

    result = await shorts_video._unowned_short_transform(
        media_path,
        media_path,
        normalize_audio=True,
        speed=1.0,
    )

    assert result is False
    assert media_path.read_bytes() == b"same path media"


@pytest.mark.asyncio
async def test_transform_cancellation_removes_partial_output(
    monkeypatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "processed.mp4"
    input_path.write_bytes(b"input")
    output_path.write_bytes(b"old processed")

    async def cancelled_owner(command, **kwargs):
        Path(command[-1]).write_bytes(b"partial processed")
        raise asyncio.CancelledError

    monkeypatch.setattr(shorts_video.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(shorts_video, "run_cancellable_process", cancelled_owner)

    with pytest.raises(asyncio.CancelledError):
        await shorts_video._unowned_short_transform(
            input_path,
            output_path,
            normalize_audio=True,
            speed=1.0,
        )

    assert output_path.exists() is False
    assert input_path.read_bytes() == b"input"


@pytest.mark.asyncio
async def test_transform_without_ffmpeg_removes_stale_output(
    monkeypatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "processed.mp4"
    input_path.write_bytes(b"input")
    output_path.write_bytes(b"old processed")

    monkeypatch.setattr(shorts_video.shutil, "which", lambda name: None)

    result = await shorts_video._unowned_short_transform(
        input_path,
        output_path,
        normalize_audio=True,
        speed=1.0,
    )

    assert result is False
    assert output_path.exists() is False
    assert input_path.read_bytes() == b"input"


@pytest.mark.asyncio
async def test_poster_ffmpeg_failure_removes_stale_poster(
    monkeypatch,
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "video.mp4"
    poster_path = tmp_path / "poster.jpg"
    video_path.write_bytes(b"video")
    poster_path.write_bytes(b"old poster")

    async def failed_owner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "frame failed")

    monkeypatch.setattr(shorts_video, "HAS_PILLOW", True)
    monkeypatch.setattr(shorts_video.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(shorts_video, "run_cancellable_process", failed_owner)

    result = await shorts_video._unowned_create_short_title_poster(
        video_path,
        poster_path,
        "Test title",
        30,
    )

    assert result is False
    assert poster_path.exists() is False


@pytest.mark.asyncio
async def test_snapshot_failure_removes_stale_snapshot(
    monkeypatch,
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "video.mp4"
    snapshot_path = tmp_path / "snapshot.jpg"
    video_path.write_bytes(b"video")
    snapshot_path.write_bytes(b"old snapshot")

    async def failed_owner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "snapshot failed")

    monkeypatch.setattr(shorts_video.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(shorts_video, "run_cancellable_process", failed_owner)

    result = await shorts_video._unowned_create_short_snapshot(
        video_path,
        snapshot_path,
        30,
    )

    assert result is False
    assert snapshot_path.exists() is False
