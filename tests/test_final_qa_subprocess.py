from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.voxcpm2 import final_media_qa, final_media_spatial_bed, professional_audio_v45


def test_final_media_text_command_uses_internal_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="probe", stderr=""))
    monkeypatch.setattr(final_media_qa.subprocess, "run", run)
    command = ["/resolved/ffprobe", "-v", "error"]

    result = final_media_qa._run(command)

    assert result.stdout == "probe"
    assert run.call_args.args[0] == command
    assert run.call_args.kwargs["check"] is False
    assert "shell" not in run.call_args.kwargs


def test_final_media_audio_decode_enforces_timeout_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=b"pcm", stderr=b""))
    monkeypatch.setattr(final_media_qa.subprocess, "run", run)
    command = ["/resolved/ffmpeg", "-f", "f32le", "pipe:1"]

    assert final_media_qa._run_audio_bytes(command, timeout=12.0) == b"pcm"
    assert run.call_args.args[0] == command
    assert run.call_args.kwargs["timeout"] == 30.0
    assert run.call_args.kwargs["text"] is False
    assert "shell" not in run.call_args.kwargs


def test_spatial_bed_audio_decode_uses_bounded_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=b"pcm", stderr=b""))
    monkeypatch.setattr(final_media_spatial_bed.subprocess, "run", run)
    command = ["/resolved/ffmpeg", "-f", "f32le", "pipe:1"]

    assert final_media_spatial_bed._run_audio_bytes(command, timeout=45.0) == b"pcm"
    assert run.call_args.args[0] == command
    assert run.call_args.kwargs["timeout"] == 45.0
    assert "shell" not in run.call_args.kwargs


def test_professional_reference_decode_uses_fixed_ffmpeg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "reference.wav"
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(professional_audio_v45.subprocess, "run", run)
    samples = professional_audio_v45.np.array([0.1, -0.1], dtype=professional_audio_v45.np.float32)
    monkeypatch.setattr(professional_audio_v45.sf, "read", lambda *args, **kwargs: (samples, 16000))

    audio, sample_rate = professional_audio_v45._decode(source, output)

    assert sample_rate == 16000
    assert audio.tolist() == pytest.approx([0.1, -0.1])
    command = run.call_args.args[0]
    assert command[0] == "ffmpeg"
    assert command[command.index("-i") + 1] == str(source)
    assert command[-1] == str(output)
    assert run.call_args.kwargs == {"check": False}
