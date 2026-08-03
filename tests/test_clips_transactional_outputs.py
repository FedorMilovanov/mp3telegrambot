from __future__ import annotations

import ast
import asyncio
import subprocess
from pathlib import Path

import pytest

from services import render_clips_montage


SOURCE_PATH = Path("services/render_clips_montage.py")


def _function_source(name: str) -> str:
    source = SOURCE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.AsyncFunctionDef) and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


def _configure_clip_render(monkeypatch, owner) -> None:
    async def no_snap(path, target, search_window=5.0):
        return target

    monkeypatch.setattr(render_clips_montage.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(render_clips_montage, "_find_silence_end", no_snap)
    monkeypatch.setattr(
        render_clips_montage,
        "_get_video_encoder",
        lambda: ("libx264", ["-crf", "23"], ["-preset", "veryfast"]),
    )
    monkeypatch.setattr(render_clips_montage, "run_cancellable_process", owner)


def test_render_surfaces_have_transactional_cleanup_contract() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")
    clip = _function_source("render_clip")
    snapshot = _function_source("create_clip_snapshot")
    montage = _function_source("render_montage_short")

    assert "def _unlink_render_paths(" in source
    assert clip.count("_unlink_render_paths(output_path)") >= 4
    assert snapshot.count("_unlink_render_paths(snapshot_path)") >= 3
    assert montage.count("_unlink_render_paths(") >= 7
    assert "except asyncio.CancelledError:" in clip
    assert "except asyncio.CancelledError:" in snapshot
    assert "except asyncio.CancelledError:" in montage
    assert "for p in temp_parts: p.unlink" not in montage


@pytest.mark.asyncio
async def test_stale_clip_cannot_satisfy_signal_two_exception(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "clip.mp4"
    source_path.write_bytes(b"source")
    output_path.write_bytes(b"old valid clip")

    async def failed_owner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            "",
            "received signal 2",
        )

    _configure_clip_render(monkeypatch, failed_owner)

    result = await render_clips_montage.render_clip(
        source_path,
        output_path,
        10,
        40,
    )

    assert result is False
    assert output_path.exists() is False


@pytest.mark.asyncio
async def test_fresh_clip_is_still_accepted_after_signal_two(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "clip.mp4"
    source_path.write_bytes(b"source")
    output_path.write_bytes(b"old clip")

    async def signal_owner(command, **kwargs):
        Path(command[-1]).write_bytes(b"fresh clip")
        return subprocess.CompletedProcess(
            command,
            1,
            "",
            "received signal 2",
        )

    _configure_clip_render(monkeypatch, signal_owner)

    result = await render_clips_montage.render_clip(
        source_path,
        output_path,
        10,
        40,
    )

    assert result is True
    assert output_path.read_bytes() == b"fresh clip"


@pytest.mark.asyncio
async def test_clip_cancellation_removes_partial_output(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "clip.mp4"
    source_path.write_bytes(b"source")
    output_path.write_bytes(b"old clip")

    async def cancelled_owner(command, **kwargs):
        Path(command[-1]).write_bytes(b"partial clip")
        raise asyncio.CancelledError

    _configure_clip_render(monkeypatch, cancelled_owner)

    with pytest.raises(asyncio.CancelledError):
        await render_clips_montage.render_clip(
            source_path,
            output_path,
            10,
            40,
        )

    assert output_path.exists() is False


@pytest.mark.asyncio
async def test_snapshot_failure_removes_stale_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "video.mp4"
    snapshot_path = tmp_path / "snapshot.jpg"
    video_path.write_bytes(b"video")
    snapshot_path.write_bytes(b"old snapshot")

    async def failed_owner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "failed")

    monkeypatch.setattr(render_clips_montage.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(render_clips_montage, "run_cancellable_process", failed_owner)

    result = await render_clips_montage.create_clip_snapshot(
        video_path,
        snapshot_path,
        30,
    )

    assert result is False
    assert snapshot_path.exists() is False


@pytest.mark.asyncio
async def test_montage_cancellation_removes_all_partial_artifacts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "montage.mp4"
    part0 = tmp_path / "montage_part0.mp4"
    part1 = tmp_path / "montage_part1.mp4"
    concat = tmp_path / "montage_concat.txt"
    source_path.write_bytes(b"source")
    output_path.write_bytes(b"old montage")
    part0.write_bytes(b"old part zero")
    part1.write_bytes(b"old part one")
    concat.write_text("old concat", encoding="utf-8")

    async def cancelled_owner(command, **kwargs):
        Path(command[-1]).write_bytes(b"partial current render")
        raise asyncio.CancelledError

    monkeypatch.setattr(render_clips_montage.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(
        render_clips_montage,
        "_get_video_encoder",
        lambda: ("libx264", ["-crf", "23"], ["-preset", "veryfast"]),
    )
    monkeypatch.setattr(
        render_clips_montage,
        "run_cancellable_process",
        cancelled_owner,
    )

    with pytest.raises(asyncio.CancelledError):
        await render_clips_montage.render_montage_short(
            source_path,
            output_path,
            [
                {"start_seconds": 0, "end_seconds": 10},
                {"start_seconds": 20, "end_seconds": 30},
            ],
        )

    for path in (output_path, part0, part1, concat):
        assert path.exists() is False
