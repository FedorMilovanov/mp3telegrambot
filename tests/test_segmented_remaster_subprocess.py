from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.voxcpm2 import remaster_delayed_nvenc, segmented_cpu_dub


def test_remaster_runner_preserves_internal_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="probe", stderr=""))
    monkeypatch.setattr(remaster_delayed_nvenc.subprocess, "run", run)
    command = ["/resolved/ffprobe", "-v", "error"]

    result = remaster_delayed_nvenc.run(command)

    assert result.stdout == "probe"
    assert run.call_args.args[0] == command
    assert run.call_args.kwargs["check"] is False
    assert "shell" not in run.call_args.kwargs


def test_segmented_checked_runner_preserves_internal_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(segmented_cpu_dub.subprocess, "run", run)
    command = ["/resolved/ffmpeg", "-v", "error"]

    segmented_cpu_dub.run_checked(command)
    assert run.call_args.args[0] == command
    assert run.call_args.kwargs["check"] is False
    assert "shell" not in run.call_args.kwargs
