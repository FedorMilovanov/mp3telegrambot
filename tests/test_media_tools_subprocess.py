from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import check_youtube_gvs
from tools import verify_factory_media_quality as factory
from tools import verify_generic_video_generation_loss as generic


def test_gvs_duration_probe_uses_resolved_ffprobe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    media = tmp_path / "source.m4a"
    monkeypatch.setattr(check_youtube_gvs.shutil, "which", lambda name: "/resolved/ffprobe")
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="12.5\n", stderr=""))
    monkeypatch.setattr(check_youtube_gvs.subprocess, "run", run)

    assert check_youtube_gvs._ffprobe_duration(media) == 12.5
    command = run.call_args.args[0]
    assert command[0] == "/resolved/ffprobe"
    assert command[-1] == str(media)
    assert run.call_args.kwargs["timeout"] == 60
    assert "shell" not in run.call_args.kwargs


def test_factory_verifier_run_uses_owned_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="ok", stderr=""))
    monkeypatch.setattr(factory.subprocess, "run", run)
    command = ["/resolved/ffmpeg", "-v", "error"]

    result = factory._run(command, timeout=60)

    assert result.stdout == "ok"
    assert run.call_args.args[0] == command
    assert run.call_args.kwargs["timeout"] == 60
    assert run.call_args.kwargs["check"] is False
    assert "shell" not in run.call_args.kwargs


def test_factory_ssim_uses_resolved_ffmpeg_and_validated_graph(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    reference = tmp_path / "reference.mp4"
    distorted = tmp_path / "distorted.mp4"
    probe = {"width": 1920, "height": 1080}
    monkeypatch.setattr(factory, "_probe", Mock(side_effect=[probe, probe]))
    monkeypatch.setattr(factory, "_which_required", lambda name: "/resolved/ffmpeg")
    run = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="Parsed_ssim_0 All:0.987654 (19.123456)\n",
        )
    )
    monkeypatch.setattr(factory.subprocess, "run", run)

    assert factory._ssim(reference, distorted) == pytest.approx(0.987654)
    command = run.call_args.args[0]
    assert command[0] == "/resolved/ffmpeg"
    assert command[command.index("-lavfi") + 1].endswith("[dist][ref]ssim")
    assert run.call_args.kwargs["timeout"] == 1800
    assert "shell" not in run.call_args.kwargs


def test_generic_evidence_run_uses_fixed_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="hash", stderr=""))
    monkeypatch.setattr(generic.subprocess, "run", run)
    command = ["/resolved/ffmpeg", "-v", "error"]

    result = generic._run(command)

    assert result.stdout == "hash"
    assert run.call_args.args[0] == command
    assert run.call_args.kwargs == {
        "check": True,
        "capture_output": True,
        "text": True,
    }
