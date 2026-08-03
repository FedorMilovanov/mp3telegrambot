from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from services import dub_rendering


ROOT = Path(__file__).resolve().parents[1]


def test_john_piper_workflow_is_valid_yaml() -> None:
    path = ROOT / ".github" / "workflows" / "john-piper-parser.yml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert "jobs" in payload
    assert "validate" in payload["jobs"]


def test_process_timeout_defaults_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DUB_TEST_TIMEOUT", raising=False)
    assert dub_rendering._process_timeout_seconds(
        {}, request_key="timeout", env_name="DUB_TEST_TIMEOUT", default=600
    ) == 600

    monkeypatch.setenv("DUB_TEST_TIMEOUT", "5")
    assert dub_rendering._process_timeout_seconds(
        {}, request_key="timeout", env_name="DUB_TEST_TIMEOUT", default=600
    ) == 60

    monkeypatch.setenv("DUB_TEST_TIMEOUT", "999999")
    assert dub_rendering._process_timeout_seconds(
        {}, request_key="timeout", env_name="DUB_TEST_TIMEOUT", default=600
    ) == 86400

    assert dub_rendering._process_timeout_seconds(
        {"timeout": "120"},
        request_key="timeout",
        env_name="DUB_TEST_TIMEOUT",
        default=600,
    ) == 120


def test_run_passes_finite_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(dub_rendering.subprocess, "run", fake_run)
    dub_rendering._run(
        ["python", "worker.py"],
        cwd=tmp_path,
        env={"X": "1"},
        label="Worker",
        timeout_seconds=321,
    )

    assert observed["timeout"] == 321
    assert observed["check"] is False
    assert observed["cwd"] == str(tmp_path)


def test_run_converts_timeout_to_operator_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, timeout=kwargs["timeout"])

    monkeypatch.setattr(dub_rendering.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="превысил лимит 90 секунд"):
        dub_rendering._run(
            ["python", "worker.py"],
            cwd=tmp_path,
            env={},
            label="Speech backend",
            timeout_seconds=90,
        )
