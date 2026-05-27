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


def test_verify_repo_tool_help_runs():
    proc = subprocess.run(
        [sys.executable, "tools/verify_repo.py", "--help"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0
    assert "Run full mp3telegrambot repository verification" in proc.stdout


def test_verify_repo_tool_smoke_skip_heavy_checks():
    proc = subprocess.run(
        [sys.executable, "tools/verify_repo.py", "--skip-pytest", "--skip-ruff"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0
    assert "compileall" in proc.stdout
    assert "Repository verification passed" in proc.stdout
