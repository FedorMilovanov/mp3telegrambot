from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.voxcpm2 import clean_runtime_contract, continuous_reference_policy, direct_max_quality_io, dub_job_preflight


def test_clean_runtime_fingerprint_uses_checked_cpu_python(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cpu_python = tmp_path / "python"
    cpu_python.write_bytes(b"python")
    payload = {
        "module": "/runtime/voxcpm/__init__.py",
        "package_root": "/runtime/voxcpm",
        "versions": {"voxcpm": "test"},
        "python_files": [{"path": "__init__.py", "size": 1, "sha256": "abc"}],
    }
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""))
    monkeypatch.setattr(clean_runtime_contract.subprocess, "run", run)

    assert clean_runtime_contract._voxcpm_runtime(cpu_python) == payload
    command = run.call_args.args[0]
    assert command[:2] == [str(cpu_python), "-c"]
    assert "import voxcpm" in command[2]
    assert run.call_args.kwargs["timeout"] == 30
    assert "shell" not in run.call_args.kwargs


def test_continuous_reference_decode_uses_fixed_ffmpeg_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "reference.wav"
    output.write_bytes(b"wav")
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(continuous_reference_policy.subprocess, "run", run)
    samples = continuous_reference_policy.np.array([0.1, -0.1], dtype=continuous_reference_policy.np.float32)
    monkeypatch.setattr(continuous_reference_policy.sf, "read", lambda *args, **kwargs: (samples, 16000))

    audio, sample_rate = continuous_reference_policy._decode_source(source, output)

    assert sample_rate == 16000
    assert audio.tolist() == pytest.approx([0.1, -0.1])
    command = run.call_args.args[0]
    assert command[0] == "ffmpeg"
    assert command[command.index("-i") + 1] == str(source)
    assert command[-1] == str(output)
    assert run.call_args.kwargs == {"check": False}


def test_direct_checked_media_command_preserves_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="12.0\n", stderr=""))
    monkeypatch.setattr(direct_max_quality_io.subprocess, "run", run)
    command = ["/resolved/ffprobe", "-v", "error"]

    result = direct_max_quality_io.run_checked(command, capture=True)

    assert result.stdout == "12.0\n"
    assert run.call_args.args[0] == command
    assert run.call_args.kwargs["check"] is False
    assert run.call_args.kwargs["stdout"] == direct_max_quality_io.subprocess.PIPE
    assert "shell" not in run.call_args.kwargs


def test_job_preflight_import_probe_uses_checked_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cpu_python = tmp_path / "python"
    renderer = repo / "renderer.py"
    master = repo / "master.py"
    cpu_python.write_bytes(b"python")
    renderer.write_text("# renderer\n", encoding="utf-8")
    master.write_text("# master\n", encoding="utf-8")
    payload = {
        "python": str(cpu_python.resolve()),
        "loaded": {"json": str(renderer.resolve())},
    }
    stdout = dub_job_preflight.PREFLIGHT_JSON_MARKER + json.dumps(payload)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr=""))
    monkeypatch.setattr(dub_job_preflight.subprocess, "run", run)
    monkeypatch.setattr(dub_job_preflight, "_signature_executable_path", lambda name: f"/resolved/{name}")
    paths = {
        "cpu_python": cpu_python,
        "repo": repo,
        "import_modules": ("json",),
        "renderer": renderer,
        "master": master,
        "final_qa_module": "json",
        "speech_backend": "voxcpm2",
    }

    result = dub_job_preflight._probe_imports(paths)

    assert result["loaded_modules"] == payload["loaded"]
    assert result["ffmpeg"] == "/resolved/ffmpeg"
    command = run.call_args.args[0]
    assert command[:2] == [str(cpu_python.resolve()), "-c"]
    assert "json" in command[2]
    assert run.call_args.kwargs["cwd"] == str(repo.resolve())
    assert run.call_args.kwargs["timeout"] == 60
    assert "shell" not in run.call_args.kwargs
