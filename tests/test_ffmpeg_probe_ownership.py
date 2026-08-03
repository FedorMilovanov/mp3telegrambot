from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

from services import ffmpeg


SOURCE_PATH = Path("services/ffmpeg.py")
PROBE_FUNCTIONS = (
    "_find_silence_end",
    "_is_static_video",
    "_detect_black_bars",
    "probe_video_language",
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


def test_async_probe_surface_uses_process_tree_owner() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")
    selected = "\n".join(_function_source(name) for name in PROBE_FUNCTIONS)

    assert "from services.async_process import run_cancellable_process" in source
    assert selected.count("await run_cancellable_process(") == 6
    assert "run_in_executor" not in selected
    assert "from subprocess import run" not in selected


@pytest.mark.asyncio
async def test_silence_probe_preserves_nearest_boundary(monkeypatch, tmp_path) -> None:
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")

    async def fake_owner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "",
            "[silencedetect] silence_end: 2.000 | silence_duration: 0.5\n",
        )

    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(ffmpeg, "run_cancellable_process", fake_owner)

    result = await ffmpeg._find_silence_end(video_path, target_end=10.0)

    assert result == 10.0


@pytest.mark.asyncio
async def test_freeze_probe_preserves_static_decision(monkeypatch, tmp_path) -> None:
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")

    async def fake_owner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "",
            "freeze_start: 0\nfreeze_end: 5\nfreeze_duration: 5.0\n",
        )

    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(ffmpeg, "run_cancellable_process", fake_owner)

    assert await ffmpeg._is_static_video(video_path, probe_seconds=6.0) is True


@pytest.mark.asyncio
async def test_crop_probe_owns_header_samples_and_dimensions(monkeypatch, tmp_path) -> None:
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    calls: list[tuple[list[str], dict[str, object]]] = []

    responses = iter(
        [
            "Duration: 00:00:30.00, start: 0.000000\n",
            "crop=100:100:0:0\n",
            "crop=100:100:0:0\n",
            "crop=100:100:0:0\n",
            "Video: h264, yuv420p, 1920x1080\n",
        ]
    )

    async def fake_owner(command, **kwargs):
        calls.append((list(command), dict(kwargs)))
        return subprocess.CompletedProcess(command, 0, "", next(responses))

    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(ffmpeg, "run_cancellable_process", fake_owner)

    result = await ffmpeg._detect_black_bars(video_path)

    assert result == "crop=100:100:0:0"
    assert [kwargs["timeout"] for _command, kwargs in calls] == [10, 30, 30, 30, 5]
    assert all(kwargs["text"] is True for _command, kwargs in calls)


@pytest.mark.asyncio
async def test_language_probe_uses_owned_ytdlp_metadata(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    async def fake_owner(command, **kwargs):
        calls.append((list(command), dict(kwargs)))
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"language": "EN"}),
            "",
        )

    monkeypatch.setattr(ffmpeg, "run_cancellable_process", fake_owner)

    result = await ffmpeg.probe_video_language("https://example.invalid/video")

    assert result == "en"
    command, kwargs = calls[0]
    assert command[-2:] == ["--dump-json", "https://example.invalid/video"]
    assert kwargs == {"timeout": 30, "text": True}
