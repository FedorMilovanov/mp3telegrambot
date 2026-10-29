from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import services.livedub_mix as mix


def _resolved(name: str) -> str:
    return f"/resolved/{name}"


@pytest.mark.parametrize("platform", ["posix", "nt"])
def test_measure_loudness_executes_resolved_ffmpeg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str
) -> None:
    source = tmp_path / "voice.mp3"
    source.write_bytes(b"voice")
    monkeypatch.setattr(mix, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(mix, "_loudness_cache", {})
    monkeypatch.setattr(mix.shutil, "which", _resolved)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr='{"input_i": "-20.5"}'))
    monkeypatch.setattr(mix.subprocess, "run", run)

    assert mix.measure_loudness(source) == -20.5
    command = run.call_args.args[0]
    assert command[0] == "/resolved/ffmpeg"
    assert command[1:3] == ["-i", str(source)]
    assert "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json" in command
    assert "shell" not in run.call_args.kwargs
    assert ("creationflags" in run.call_args.kwargs) is (platform == "nt")


def test_probe_media_duration_executes_resolved_ffprobe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "voice.mp3"
    monkeypatch.setattr(mix.shutil, "which", _resolved)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="12.5\n", stderr=""))
    monkeypatch.setattr(mix.subprocess, "run", run)

    assert mix.probe_media_duration(source) == 12.5
    command = run.call_args.args[0]
    assert command[0] == "/resolved/ffprobe"
    assert command[-1] == str(source)
    assert "shell" not in run.call_args.kwargs


@pytest.mark.parametrize("platform", ["posix", "nt"])
def test_video_stream_probe_executes_resolved_ffprobe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str
) -> None:
    source = tmp_path / "original.mp4"
    source.write_bytes(b"\0" * (100 * 1024 + 1))
    monkeypatch.setattr(mix, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(mix.shutil, "which", _resolved)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="video\n", stderr=""))
    monkeypatch.setattr(mix.subprocess, "run", run)

    assert mix.has_video_stream(source) is True
    command = run.call_args.args[0]
    assert command[0] == "/resolved/ffprobe"
    assert command[-1] == str(source)
    assert "shell" not in run.call_args.kwargs
    assert ("creationflags" in run.call_args.kwargs) is (platform == "nt")


def test_video_metadata_executes_resolved_ffprobe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "original.mp4"
    payload = {"streams": [{"width": 1920, "height": 1080}], "format": {"duration": "12.9"}}
    monkeypatch.setattr(mix.shutil, "which", _resolved)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""))
    monkeypatch.setattr(mix.subprocess, "run", run)

    assert mix.probe_video_meta(source) == {"width": 1920, "height": 1080, "duration": 12}
    command = run.call_args.args[0]
    assert command[0] == "/resolved/ffprobe"
    assert command[-1] == str(source)
    assert "shell" not in run.call_args.kwargs


@pytest.mark.parametrize("platform", ["posix", "nt"])
def test_thumbnail_executes_resolved_ffmpeg_and_validates_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str
) -> None:
    source = tmp_path / "original.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(mix, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(mix.shutil, "which", _resolved)

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        Path(command[-1]).write_bytes(b"jpeg")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    process = Mock(side_effect=run)
    monkeypatch.setattr(mix.subprocess, "run", process)

    thumbnail = source.with_suffix(".thumb.jpg")
    assert mix.make_video_thumbnail(source) == thumbnail
    assert thumbnail.read_bytes() == b"jpeg"
    command = process.call_args.args[0]
    assert command[0] == "/resolved/ffmpeg"
    assert command[-1] == str(thumbnail)
    assert "shell" not in process.call_args.kwargs
    assert ("creationflags" in process.call_args.kwargs) is (platform == "nt")


@pytest.mark.parametrize("platform", ["posix", "nt"])
def test_run_ffmpeg_executes_resolved_binary_with_platform_flags(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
) -> None:
    monkeypatch.setattr(mix, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(mix.shutil, "which", _resolved)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(mix.subprocess, "run", run)

    assert mix._run_ffmpeg(["-version"], timeout=7) == (True, "")
    assert run.call_args.args[0] == ["/resolved/ffmpeg", "-version"]
    assert run.call_args.kwargs["timeout"] == 7
    assert "shell" not in run.call_args.kwargs
    assert ("creationflags" in run.call_args.kwargs) is (platform == "nt")


def test_video_metadata_windows_ffmpeg_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "original.mp4"
    monkeypatch.setattr(mix, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        mix.shutil,
        "which",
        lambda name: None if name == "ffprobe" else _resolved(name),
    )
    run = Mock(
        return_value=SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Duration: 00:00:12.90, Video: h264, 1920x1080",
        )
    )
    monkeypatch.setattr(mix.subprocess, "run", run)

    assert mix.probe_video_meta(source) == {"width": 1920, "height": 1080, "duration": 12}
    assert run.call_args.args[0] == ["/resolved/ffmpeg", "-i", str(source)]
    assert "creationflags" in run.call_args.kwargs


@pytest.mark.asyncio
async def test_build_pro_dub_drains_original_download_after_ru_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from services import eng_subtitles, yandex_live_dub

    original_started = asyncio.Event()
    original_drained = asyncio.Event()

    async def fail_ru(*args: object, **kwargs: object) -> None:
        await original_started.wait()
        raise ValueError("RU unavailable")

    async def pending_original(*args: object, **kwargs: object) -> None:
        original_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            original_drained.set()

    monkeypatch.setattr(yandex_live_dub, "get_live_dub_audio", fail_ru)
    monkeypatch.setattr(eng_subtitles, "download_original_video", pending_original)

    result = await mix.build_pro_dub("https://example.test/video", tmp_path)

    assert result is None
    assert original_drained.is_set()
