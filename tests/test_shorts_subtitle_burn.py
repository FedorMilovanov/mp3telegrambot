import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from services import shorts_subtitle_burn as subtitle_burn


class _ObservedTemporaryDirectory:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> str:
        self.path.mkdir(parents=True, exist_ok=False)
        return str(self.path)

    def __exit__(self, exc_type, exc, traceback) -> None:
        shutil.rmtree(self.path)


def _valid_ass_document() -> str:
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:00.50,Default,,0,0,0,,test\n"
    )


def _patch_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subtitle_burn.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(
        subtitle_burn,
        "get_subtitles_mode_settings",
        lambda: {"karaoke": False},
    )
    monkeypatch.setattr(
        subtitle_burn,
        "_generate_ass_from_segments",
        lambda segments, karaoke: _valid_ass_document(),
    )
    monkeypatch.setattr(
        subtitle_burn,
        "_get_video_encoder",
        lambda: ("libx264", ["-crf", "23"], ["-preset", "veryfast"]),
    )


@pytest.mark.asyncio
async def test_burn_command_delegates_to_shared_process_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def _shared_owner(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subtitle_burn, "run_cancellable_process", _shared_owner)

    result = await subtitle_burn._run_burn_command(["ffmpeg", "-version"])

    assert result.returncode == 0
    assert captured["command"] == ["ffmpeg", "-version"]
    assert captured["timeout"] == 600.0
    assert captured["text"] is True


@pytest.mark.asyncio
async def test_timeout_removes_ass_directory_and_partial_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    observed_temp = tmp_path / "observed-ass-temp"
    input_path.write_bytes(b"source")
    output_path.write_bytes(b"stale-partial-output")
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(
        subtitle_burn.tempfile,
        "TemporaryDirectory",
        lambda **kwargs: _ObservedTemporaryDirectory(observed_temp),
    )

    async def _timeout(command: list[str]):
        output_path.write_bytes(b"new-partial-output")
        raise subprocess.TimeoutExpired(command, timeout=600)

    monkeypatch.setattr(subtitle_burn, "_run_burn_command", _timeout)

    accepted = await subtitle_burn.burn_subtitles_into_short(
        input_path,
        output_path,
        [{"start": 0.0, "end": 1.0, "text": "Тест"}],
    )

    assert accepted is False
    assert not output_path.exists()
    assert not observed_temp.exists()


@pytest.mark.asyncio
async def test_cancellation_cleans_artifacts_and_is_not_swallowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    observed_temp = tmp_path / "observed-ass-temp"
    input_path.write_bytes(b"source")
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(
        subtitle_burn.tempfile,
        "TemporaryDirectory",
        lambda **kwargs: _ObservedTemporaryDirectory(observed_temp),
    )

    async def _cancel(command: list[str]):
        output_path.write_bytes(b"partial")
        raise asyncio.CancelledError

    monkeypatch.setattr(subtitle_burn, "_run_burn_command", _cancel)

    with pytest.raises(asyncio.CancelledError):
        await subtitle_burn.burn_subtitles_into_short(
            input_path,
            output_path,
            [{"start": 0.0, "end": 1.0, "text": "Тест"}],
        )

    assert not output_path.exists()
    assert not observed_temp.exists()


@pytest.mark.asyncio
async def test_success_commits_nonempty_output_and_cleans_ass_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    observed_temp = tmp_path / "observed-ass-temp"
    input_path.write_bytes(b"source")
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(
        subtitle_burn.tempfile,
        "TemporaryDirectory",
        lambda **kwargs: _ObservedTemporaryDirectory(observed_temp),
    )

    async def _success(command: list[str]):
        output_path.write_bytes(b"finished-output")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subtitle_burn, "_run_burn_command", _success)

    accepted = await subtitle_burn.burn_subtitles_into_short(
        input_path,
        output_path,
        [{"start": 0.0, "end": 1.0, "text": "Тест"}],
    )

    assert accepted is True
    assert output_path.read_bytes() == b"finished-output"
    assert not observed_temp.exists()


def test_active_pipelines_use_transactional_subtitle_owner() -> None:
    root = Path(__file__).resolve().parents[1]
    for relative_path in ("pipelines/shorts.py", "pipelines/montage.py"):
        source = (root / relative_path).read_text(encoding="utf-8")
        assert (
            "from services.shorts_subtitle_burn import "
            "burn_subtitles_into_short"
        ) in source
        shorts_import = source.split("from services.shorts_video import (", 1)[1].split(")", 1)[0]
        assert "burn_subtitles_into_short" not in shorts_import


def test_subtitle_burn_has_no_second_child_process_policy() -> None:
    source = Path(subtitle_burn.__file__).read_text(encoding="utf-8")

    assert "from services.async_process import run_cancellable_process" in source
    assert "await run_cancellable_process(" in source
    assert "create_subprocess_exec" not in source
    assert "def _terminate_process" not in source
    assert "run_in_executor" not in source