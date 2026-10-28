from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services import tts_weighted_runner_provisioning as provisioning
from services import tts_weighted_smoke as smoke
from services import tts_weighted_smoke_runner as runner


def test_runner_service_probe_uses_resolved_sc_and_validated_name(monkeypatch: pytest.MonkeyPatch) -> None:
    service_name = "actions.runner.owner-repo.example.service"
    monkeypatch.setattr(provisioning.platform, "system", lambda: "Windows")
    monkeypatch.setattr(provisioning.shutil, "which", lambda name: "/resolved/sc.exe" if name == "sc.exe" else None)
    run = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout="        STATE              : 4  RUNNING\n",
            stderr="",
        )
    )
    monkeypatch.setattr(provisioning.subprocess, "run", run)

    assert provisioning._default_service_probe(service_name) is True
    assert run.call_args.args[0] == ["/resolved/sc.exe", "query", service_name]
    assert "shell" not in run.call_args.kwargs


def test_weighted_output_probe_uses_resolved_ffprobe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    output = tmp_path / "weighted.wav"
    payload = {
        "streams": [
            {
                "codec_type": "audio",
                "codec_name": "pcm_s24le",
                "sample_rate": "24000",
                "channels": 1,
                "duration": "4.0",
            }
        ]
    }
    monkeypatch.setattr(smoke.shutil, "which", lambda name: "/resolved/ffprobe")
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""))
    monkeypatch.setattr(smoke.subprocess, "run", run)

    result = smoke._ffprobe_output(output)

    assert result == {
        "codec_name": "pcm_s24le",
        "sample_rate": 24000,
        "channels": 1,
        "duration_seconds": 4.0,
    }
    command = run.call_args.args[0]
    assert command[0] == "/resolved/ffprobe"
    assert command[-1] == str(output)
    assert "shell" not in run.call_args.kwargs


def test_runner_doctor_version_probe_uses_resolved_ffprobe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/resolved/ffprobe")
    run = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout="ffprobe version 6.1.2-test\n",
            stderr="",
        )
    )
    monkeypatch.setattr(runner.subprocess, "run", run)

    assert runner._probe_ffprobe() == {"available": True, "version": "6.1.2-test"}
    assert run.call_args.args[0] == ["/resolved/ffprobe", "-version"]
    assert "shell" not in run.call_args.kwargs
