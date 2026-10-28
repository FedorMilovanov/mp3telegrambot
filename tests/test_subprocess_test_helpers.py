from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

_MODULE_PATH = Path(__file__).with_name("test_livedub_audio_media_integration.py")
_SPEC = importlib.util.spec_from_file_location("livedub_audio_media_test_helper", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
integration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(integration)


def test_livedub_media_test_helper_preserves_checked_command(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(integration.subprocess, "run", run)
    command = ["/resolved/ffmpeg", "-version"]

    assert integration._run(command) is None
    assert run.call_args.args[0] == command
    assert run.call_args.kwargs == {
        "check": True,
        "stdout": integration.subprocess.DEVNULL,
        "stderr": integration.subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 60,
    }
