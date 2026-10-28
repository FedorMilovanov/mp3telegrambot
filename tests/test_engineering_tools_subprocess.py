from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import model_upgrade_drill as drill
from scripts import render_env_reference as env_reference
from tools import install_from_lock, run_pytest_with_annotations, verify_repo


def test_model_drill_run_uses_owned_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(drill.subprocess, "run", run)
    command = [drill.sys.executable, str(drill.RENDER), "--check"]

    result = drill._run(command, quiet=True)

    assert result.returncode == 0
    assert run.call_args.args[0] == command
    assert run.call_args.kwargs == {
        "cwd": drill.ROOT,
        "capture_output": True,
        "text": True,
        "check": False,
    }


def test_env_policy_snapshot_uses_current_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"group": ["POLICY_NAME"]}
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""))
    monkeypatch.setattr(env_reference.subprocess, "run", run)

    assert env_reference._policy_owned() == payload
    command = run.call_args.args[0]
    assert command[:2] == [env_reference.sys.executable, "-c"]
    assert "configure_max_quality_env()" in command[2]
    assert "configure_gemini_policy()" in command[2]
    assert run.call_args.kwargs["cwd"] == str(env_reference.ROOT)
    assert "shell" not in run.call_args.kwargs


def test_lock_install_uses_current_interpreter_and_exact_pin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lock = tmp_path / "requirements-lock.txt"
    lock.write_text("pytest==9.1.1\n", encoding="utf-8")
    call = Mock(return_value=0)
    monkeypatch.setattr(install_from_lock.subprocess, "call", call)

    assert install_from_lock.main(["--lock", str(lock), "pytest"]) == 0
    assert call.call_args.args[0] == [
        install_from_lock.sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "pytest==9.1.1",
    ]
    assert call.call_args.kwargs == {}


def test_pytest_annotation_runner_keeps_arguments_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=""))
    monkeypatch.setattr(run_pytest_with_annotations.subprocess, "run", run)

    assert run_pytest_with_annotations.main(["-q", "tests/example.py"]) == 0
    assert run.call_args.args[0] == [
        run_pytest_with_annotations.sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/example.py",
    ]
    assert run.call_args.kwargs["check"] is False
    assert "shell" not in run.call_args.kwargs


def test_verifier_run_executes_fixed_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(verify_repo.subprocess, "run", run)
    command = [verify_repo.sys.executable, "-m", "compileall", "-q", "."]

    result = verify_repo._run("compileall", command, tmp_path)

    assert result.ok is True
    assert result.command == tuple(command)
    assert run.call_args.args[0] == command
    assert run.call_args.kwargs == {"cwd": str(tmp_path), "check": False}
