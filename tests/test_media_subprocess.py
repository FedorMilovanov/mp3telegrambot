from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services import dub_preflight, dub_rendering, media_delivery_probe, media_masters
from services import shorts_factory_video_quality as factory


def _resolved(name: str) -> str:
    return f"/resolved/{name}"


def test_preflight_import_probe_uses_checked_python(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    speech_python = tmp_path / "speech-python"
    media_python = tmp_path / "media-python"
    speech_python.write_bytes(b"python")
    media_python.write_bytes(b"python")
    backend = SimpleNamespace(process_environment=lambda *args, **kwargs: SimpleNamespace(as_dict=lambda base: {}))
    plan = {
        "speech_runtime": SimpleNamespace(cpu_python=speech_python),
        "media_runtime": SimpleNamespace(python_executable=media_python),
        "modules": ["json"],
        "backend": backend,
        "request": {},
        "repo": tmp_path,
    }
    monkeypatch.setattr(dub_preflight.shutil, "which", _resolved)
    run = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"json": "/resolved/json.py"}),
            stderr="",
        )
    )
    monkeypatch.setattr(dub_preflight.subprocess, "run", run)

    result = dub_preflight._probe_imports(plan)

    assert result["loaded_modules"] == {"json": "/resolved/json.py"}
    assert result["ffmpeg"] == "/resolved/ffmpeg"
    command = run.call_args.args[0]
    assert command[:2] == [str(speech_python), "-c"]
    assert "json" in command[2]
    assert run.call_args.kwargs["cwd"] == str(tmp_path)
    assert "shell" not in run.call_args.kwargs


def test_dub_component_run_uses_bounded_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(dub_rendering.subprocess, "run", run)
    command = ["/resolved/python", "renderer"]

    dub_rendering._run(command, cwd=tmp_path, env={"TEST": "1"}, label="renderer", timeout_seconds=60)

    assert run.call_args.args[0] == command
    assert run.call_args.kwargs == {
        "cwd": str(tmp_path),
        "env": {"TEST": "1"},
        "check": False,
        "timeout": 60,
    }


def test_delivery_probe_executes_resolved_ffprobe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "delivery.mp4"
    source.write_bytes(b"video")
    payload = {
        "format": {"duration": "12.0"},
        "streams": [
            {"codec_type": "video", "width": 1920, "height": 1080},
            {"codec_type": "audio", "sample_rate": "48000", "codec_name": "aac"},
        ],
    }
    monkeypatch.setattr(media_delivery_probe.shutil, "which", _resolved)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""))
    monkeypatch.setattr(media_delivery_probe.subprocess, "run", run)

    probe = media_delivery_probe.probe_media(source)

    assert probe is not None
    assert media_delivery_probe.media_probe_is_deliverable(probe) is True
    command = run.call_args.args[0]
    assert command[0] == "/resolved/ffprobe"
    assert command[-1] == str(source)
    assert "shell" not in run.call_args.kwargs


def test_final_av_probe_executes_resolved_ffprobe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "final.mp4"
    source.write_bytes(b"x" * 1025)
    payload = {"format": {"duration": "12.0"}, "streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}
    monkeypatch.setattr(media_masters.shutil, "which", _resolved)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""))
    monkeypatch.setattr(media_masters.subprocess, "run", run)

    result = media_masters.FFprobeFinalMediaValidator._probe(source)

    assert result["duration"] == 12.0
    assert result["streams"] == ["audio", "video"]
    command = run.call_args.args[0]
    assert command[0] == "/resolved/ffprobe"
    assert command[-1] == str(source)
    assert "shell" not in run.call_args.kwargs


def test_factory_encoder_probe_uses_fixed_argv_and_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIDEO_FORCE_CPU", raising=False)
    monkeypatch.setattr(factory, "_H264_NVENC_AVAILABLE", None)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="h264_nvenc\n", stderr=""))
    monkeypatch.setattr(factory.subprocess, "run", run)

    assert factory._h264_nvenc_available_sync("/resolved/ffmpeg") is True
    assert factory._h264_nvenc_available_sync("/resolved/ffmpeg") is True
    assert run.call_count == 1
    assert run.call_args.args[0] == ["/resolved/ffmpeg", "-hide_banner", "-encoders"]
    assert "shell" not in run.call_args.kwargs
