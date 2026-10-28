from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import services.livedub_long_qa as long_qa
import services.livedub_qa as qa


def _resolved(name: str) -> str:
    return f"/resolved/{name}"


def test_qa_ffprobe_json_executes_resolved_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "dub.mp4"
    payload = {"format": {"duration": "12.0"}, "streams": [{"codec_type": "audio"}]}
    monkeypatch.setattr(qa.shutil, "which", _resolved)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""))
    monkeypatch.setattr(qa.subprocess, "run", run)

    assert qa._ffprobe_json(source) == payload
    command = run.call_args.args[0]
    assert command[0] == "/resolved/ffprobe"
    assert command[-1] == str(source)
    assert "shell" not in run.call_args.kwargs


def test_qa_volume_probe_executes_resolved_ffmpeg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "dub.mp4"
    monkeypatch.setattr(qa.shutil, "which", _resolved)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr="mean_volume: -23.5 dB"))
    monkeypatch.setattr(qa.subprocess, "run", run)

    assert qa._mean_volume_db(source, start=12.9, dur=120) == -23.5
    command = run.call_args.args[0]
    assert command[0] == "/resolved/ffmpeg"
    assert command[1:5] == ["-hide_banner", "-ss", "12", "-t"]
    assert command[-3:] == ["-f", "null", "-"]
    assert "shell" not in run.call_args.kwargs


def test_qa_audio_extract_validates_nonempty_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "dub.mp4"
    output = tmp_path / "qa.mp3"
    monkeypatch.setattr(qa.shutil, "which", _resolved)

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        Path(command[-1]).write_bytes(b"x" * 1025)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    process = Mock(side_effect=run)
    monkeypatch.setattr(qa.subprocess, "run", process)

    assert qa._extract_audio_for_qa(source, output) == output
    assert output.stat().st_size == 1025
    command = process.call_args.args[0]
    assert command[0] == "/resolved/ffmpeg"
    assert command[-1] == str(output)
    assert "libmp3lame" in command
    assert "shell" not in process.call_args.kwargs


def test_long_qa_duration_executes_resolved_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "segment.mp3"
    monkeypatch.setattr(long_qa.shutil, "which", _resolved)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="48.0\n", stderr=""))
    monkeypatch.setattr(long_qa.subprocess, "run", run)

    assert long_qa._ffprobe_duration(source) == 48.0
    command = run.call_args.args[0]
    assert command[0] == "/resolved/ffprobe"
    assert command[-1] == str(source)
    assert "shell" not in run.call_args.kwargs


def test_long_qa_segment_extract_commits_output_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "dub.mp4"
    output = tmp_path / "segment.mp3"
    source.write_bytes(b"video")
    monkeypatch.setattr(long_qa.shutil, "which", _resolved)
    monkeypatch.setattr(long_qa, "_ffprobe_duration", lambda path: 48.0)

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        Path(command[-1]).write_bytes(b"mp3")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    process = Mock(side_effect=run)
    monkeypatch.setattr(long_qa.subprocess, "run", process)

    assert long_qa._extract_audio_segment(source, start=20, length=48, output=output) == output
    assert output.read_bytes() == b"mp3"
    temp = output.with_name(f".{output.stem}.{os.getpid()}.part.mp3")
    assert not temp.exists()
    command = process.call_args.args[0]
    assert command[0] == "/resolved/ffmpeg"
    assert command[1:5] == ["-y", "-ss", "20", "-t"]
    assert command[-1] == str(temp)
    assert "shell" not in process.call_args.kwargs
