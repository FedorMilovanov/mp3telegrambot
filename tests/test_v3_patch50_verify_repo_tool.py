"""Regression tests for v3 patch 50 — full repo verification tool."""

import subprocess
import sys
from pathlib import Path


def test_verify_repo_tool_exists_and_documents_all_checks():
    src = Path("tools/verify_repo.py").read_text(encoding="utf-8")
    assert "compileall" in src
    assert "pytest" in src
    assert "ruff" in src
    assert "git diff --check" in src
    assert "Repository verification passed" in src


def test_verify_repo_executes_ruff_format_gate(monkeypatch, tmp_path):
    from tools import verify_repo

    calls = []

    def fake_run(name, command, cwd):
        calls.append((name, command, cwd))
        return verify_repo.CheckResult(name=name, command=tuple(command), returncode=0)

    monkeypatch.setattr(verify_repo, "_run", fake_run)
    results = verify_repo.run_checks(tmp_path, skip_pytest=True, skip_typecheck=True)

    assert all(result.ok for result in results)
    assert (
        "ruff format",
        [sys.executable, "-m", "ruff", "format", "--check", "."],
        tmp_path,
    ) in calls


def test_verify_repo_tool_help_runs():
    proc = subprocess.run(
        [sys.executable, "tools/verify_repo.py", "--help"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0
    assert "Run full mp3telegrambot repository verification" in proc.stdout


def test_verify_repo_tool_smoke_skip_heavy_checks():
    proc = subprocess.run(
        [sys.executable, "tools/verify_repo.py", "--skip-pytest", "--skip-ruff"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0
    assert "compileall" in proc.stdout
    assert "Repository verification passed" in proc.stdout
