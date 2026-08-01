from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import check_code_health_baseline_update as guard


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _baseline() -> dict:
    return {
        "files_scanned": 10,
        "total_regex_markers": 20,
        "total_postprocess_markers": 30,
    }


def _report(current: dict) -> dict:
    return {
        "policy": "intentional-code-health-baseline-refresh-v1",
        "reason": "Reviewed architecture change.",
        "current": current,
        "evidence": {"focused_tests": ["tests/test_example.py"]},
    }


def test_unchanged_baseline_needs_no_report(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    assert guard.validate_update(("services/example.py",)) == {
        "policy": guard.POLICY,
        "baseline_changed": False,
        "reports": [],
    }


def test_changed_baseline_without_report_fails(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    _write(guard.BASELINE, _baseline())
    with pytest.raises(RuntimeError, match="without a JSON report"):
        guard.validate_update((guard.BASELINE.as_posix(),))


def test_matching_report_authorizes_reviewed_baseline(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    baseline = _baseline()
    report_path = Path("docs/code_health_reports/review.json")
    _write(guard.BASELINE, baseline)
    _write(report_path, _report(baseline))

    result = guard.validate_update(
        (guard.BASELINE.as_posix(), report_path.as_posix())
    )

    assert result["baseline_changed"] is True
    assert result["baseline"] == baseline
    assert result["reports"] == [report_path.as_posix()]


def test_report_must_match_new_snapshot(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    baseline = _baseline()
    report_path = Path("docs/code_health_reports/review.json")
    _write(guard.BASELINE, baseline)
    _write(report_path, _report({**baseline, "total_regex_markers": 19}))

    with pytest.raises(RuntimeError, match="exactly matches"):
        guard.validate_update(
            (guard.BASELINE.as_posix(), report_path.as_posix())
        )
