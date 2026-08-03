from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from services import eng_subtitles


SOURCE_PATH = Path("services/eng_subtitles.py")


def _function_source(name: str) -> str:
    source = SOURCE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


def test_external_media_work_has_one_process_owner_contract() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")

    assert "from services.async_process import run_cancellable_process" in source
    assert source.count("await run_cancellable_process(") == 6
    assert "run_in_executor" not in source
    assert "shell=" not in source
    assert "subprocess.run(" not in source


def test_whisper_worker_remains_owned_through_cancellation() -> None:
    function = _function_source("create_gemini_subtitles")

    assert "await await_owned_coroutine(" in function
    assert "asyncio.to_thread(_run_whisper)" in function


def test_ffmpeg_outputs_are_transactional() -> None:
    burn = _function_source("_burn_subtitles")
    merge = _function_source("merge_subtitles")

    assert burn.count("output_path.unlink(missing_ok=True)") >= 3
    assert merge.count("output_path.unlink(missing_ok=True)") >= 5
    assert "proc.returncode == 0" in burn
    assert "proc.returncode == 0" in merge
    assert "proc2.returncode == 0" in merge
    assert merge.count("await run_cancellable_process(") == 2


@pytest.mark.asyncio
async def test_audio_duration_uses_cancellable_ffprobe(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    async def fake_owner(command, **kwargs):
        calls.append((list(command), dict(kwargs)))
        return subprocess.CompletedProcess(command, 0, "12.75\n", "")

    monkeypatch.setattr(eng_subtitles.shutil, "which", lambda name: "ffprobe")
    monkeypatch.setattr(eng_subtitles, "run_cancellable_process", fake_owner)

    duration = await eng_subtitles._get_audio_duration(Path("sample.wav"))

    assert duration == 12.75
    command, kwargs = calls[0]
    assert command[0] == "ffprobe"
    assert command[-1] == "sample.wav"
    assert kwargs == {"timeout": 30, "text": True}


@pytest.mark.asyncio
async def test_original_video_download_rejects_failed_owner(monkeypatch, tmp_path) -> None:
    async def fake_owner(command, **kwargs):
        return subprocess.CompletedProcess(command, 7, "", "download failed")

    monkeypatch.setattr(eng_subtitles, "run_cancellable_process", fake_owner)
    monkeypatch.setattr(eng_subtitles, "_has_video_stream", lambda path: False)

    with pytest.raises(RuntimeError, match="yt-dlp rc=7"):
        await eng_subtitles.download_original_video(
            "https://example.invalid/video",
            tmp_path,
        )


@pytest.mark.asyncio
async def test_hardsub_failure_removes_stale_output(monkeypatch, tmp_path) -> None:
    video_path = tmp_path / "video.mp4"
    srt_path = tmp_path / "subs.srt"
    output_path = video_path.with_suffix(".sub.mp4")
    video_path.write_bytes(b"video")
    srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nText\n", encoding="utf-8")
    output_path.write_bytes(b"stale output")

    async def fake_owner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "encode failed")

    monkeypatch.setattr(eng_subtitles, "run_cancellable_process", fake_owner)
    monkeypatch.setattr(
        "services.ffmpeg._get_video_encoder",
        lambda: ("libx264", ["-crf", "23"], ["-preset", "veryfast"]),
    )

    result = await eng_subtitles._burn_subtitles(video_path, srt_path, "ffmpeg")

    assert result is None
    assert output_path.exists() is False
