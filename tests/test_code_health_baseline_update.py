from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import check_code_health_baseline_update as guard


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _baseline() -> dict[str, Any]:
    return {
        "files_scanned": 10,
        "total_regex_markers": 20,
        "total_postprocess_markers": 30,
    }


def _report(current: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy": "intentional-code-health-baseline-refresh-v1",
        "reason": "Reviewed architecture change.",
        "current": current,
        "evidence": {"focused_tests": ["tests/test_example.py"]},
    }


def test_git_requires_and_uses_a_resolved_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="payload\n", stderr="")

    monkeypatch.setattr(guard.shutil, "which", lambda name: "/usr/bin/git" if name == "git" else None)
    monkeypatch.setattr(guard.subprocess, "run", run)

    assert guard._git("status", "--short") == "payload\n"
    assert commands == [["/usr/bin/git", "status", "--short"]]

    monkeypatch.setattr(guard.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="git executable not found"):
        guard._git("status")


def test_changed_files_places_revision_after_option_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str) -> str:
        calls.append(args)
        return "services/example.py\n"

    monkeypatch.setattr(guard, "_git", fake_git)

    assert guard.changed_files("--output=unexpected", merge_base=False) == ("services/example.py",)
    assert calls == [
        (
            "diff",
            "--name-only",
            "--end-of-options",
            "--output=unexpected..HEAD",
            "--",
        )
    ]


def test_unchanged_baseline_needs_no_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    assert guard.validate_update(("services/example.py",)) == {
        "policy": guard.POLICY,
        "baseline_changed": False,
        "reports": [],
    }


def test_changed_baseline_without_report_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    _write(guard.BASELINE, _baseline())
    with pytest.raises(RuntimeError, match="without a JSON report"):
        guard.validate_update((guard.BASELINE.as_posix(),))


def test_matching_report_authorizes_reviewed_baseline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    baseline = _baseline()
    report_path = Path("docs/code_health_reports/review.json")
    _write(guard.BASELINE, baseline)
    _write(report_path, _report(baseline))

    result = guard.validate_update((guard.BASELINE.as_posix(), report_path.as_posix()))

    assert result["baseline_changed"] is True
    assert result["baseline"] == baseline
    assert result["reports"] == [report_path.as_posix()]


def test_report_must_match_new_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    baseline = _baseline()
    report_path = Path("docs/code_health_reports/review.json")
    _write(guard.BASELINE, baseline)
    _write(report_path, _report({**baseline, "total_regex_markers": 19}))

    with pytest.raises(RuntimeError, match="exactly matches"):
        guard.validate_update((guard.BASELINE.as_posix(), report_path.as_posix()))
