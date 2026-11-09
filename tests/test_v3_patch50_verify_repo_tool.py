"""Regression tests for v3 patch 50 — full repo verification tool."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest


def test_verify_repo_tool_exists_and_documents_all_checks() -> None:
    src = Path("tools/verify_repo.py").read_text(encoding="utf-8")
    assert "compileall" in src
    assert "pytest" in src
    assert "ruff" in src
    assert "git diff --check" in src
    assert "Repository verification passed" in src


def test_verify_repo_executes_ruff_format_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tools import verify_repo

    calls: list[tuple[str, list[str], Path]] = []

    def fake_run(name: str, command: list[str], cwd: Path) -> verify_repo.CheckResult:
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


def test_compileall_excludes_local_venv_but_compiles_project_sources(tmp_path: Path) -> None:
    from tools import verify_repo

    pattern = re.compile(verify_repo._COMPILEALL_EXCLUDE)
    assert pattern.search(r".venv/Lib/site-packages/numpy/_typing/_shape.py")
    assert pattern.search(r"venv\Lib\site-packages\package.py")
    assert not pattern.search("core/analysis.py")

    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "bad_vendor.py").write_text("def invalid(", encoding="utf-8")
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "analysis.py").write_text("VALUE = 42", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "-x", verify_repo._COMPILEALL_EXCLUDE, "."],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert list((tmp_path / "core" / "__pycache__").glob("analysis.*.pyc"))


def test_repo_verification_uses_supported_python_not_global_311() -> None:
    instructions = Path("AGENTS.md").read_text(encoding="utf-8")
    assert "Python >=3.12" in instructions
    assert r".venv\Scripts\python.exe" in instructions
    assert "isolated VoxCPM2 synthesis environment" in instructions


def test_verify_repo_tool_help_runs() -> None:
    proc = subprocess.run(
        [sys.executable, "tools/verify_repo.py", "--help"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0
    assert "Run full mp3telegrambot repository verification" in proc.stdout


def test_verify_repo_tool_smoke_skip_heavy_checks() -> None:
    proc = subprocess.run(
        [sys.executable, "tools/verify_repo.py", "--skip-pytest", "--skip-ruff", "--skip-typecheck"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0
    assert "compileall" in proc.stdout
    assert "Repository verification passed" in proc.stdout
