from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.voxcpm2 import dub_quality_v4, master_quality_v4, production_preflight
from tools.voxcpm2.examples.john_piper_z20py4yqhyq import master_constant_mix


def test_quality_reference_window_uses_fixed_ffmpeg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "part.wav"
    output.write_bytes(b"wav")
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(dub_quality_v4.subprocess, "run", run)
    samples = dub_quality_v4.np.array([0.1, -0.1], dtype=dub_quality_v4.np.float32)
    monkeypatch.setattr(dub_quality_v4.sf, "read", lambda *args, **kwargs: (samples, 16000))

    audio = dub_quality_v4._extract_reference_part(source, 0.0, 1.0, output)

    assert audio.tolist() == pytest.approx([0.1, -0.1])
    command = run.call_args.args[0]
    assert command[0] == "ffmpeg"
    assert command[command.index("-i") + 1] == str(source)
    assert command[-1] == str(output)
    assert run.call_args.kwargs == {"check": False}


def test_quality_silence_onset_uses_fixed_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    run = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stderr="silence_start: 0\nsilence_end: 0.75\n",
        )
    )
    monkeypatch.setattr(dub_quality_v4.subprocess, "run", run)

    assert dub_quality_v4._source_speech_onset(source) == 0.75
    command = run.call_args.args[0]
    assert command[0] == "ffmpeg"
    assert command[command.index("-i") + 1] == str(source)
    assert run.call_args.kwargs["stdout"] == dub_quality_v4.subprocess.DEVNULL
    assert "shell" not in run.call_args.kwargs


def test_constant_mix_master_runner_preserves_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="probe", stderr=""))
    monkeypatch.setattr(master_constant_mix.subprocess, "run", run)
    command = ["/resolved/ffprobe", "-v", "error"]

    result = master_constant_mix.run(command, capture=True)

    assert result.stdout == "probe"
    assert run.call_args.args[0] == command
    assert run.call_args.kwargs["check"] is False
    assert "shell" not in run.call_args.kwargs


def test_quality_master_runner_preserves_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="probe", stderr=""))
    monkeypatch.setattr(master_quality_v4.subprocess, "run", run)
    command = ["/resolved/ffmpeg", "-v", "error"]

    result = master_quality_v4.run(command, capture=True)

    assert result.stdout == "probe"
    assert run.call_args.args[0] == command
    assert run.call_args.kwargs["check"] is False
    assert "shell" not in run.call_args.kwargs


def test_production_preflight_compile_uses_fixed_python_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    python = tmp_path / "python"
    script = tmp_path / "renderer.py"
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(production_preflight.subprocess, "run", run)

    production_preflight.compile_script(python, script)

    assert run.call_args.args[0] == [str(python), "-m", "py_compile", str(script)]
    assert run.call_args.kwargs["check"] is False
    assert "shell" not in run.call_args.kwargs
