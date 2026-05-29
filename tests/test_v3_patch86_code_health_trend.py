"""Regression tests for v3 patch 86 — code-health trend tooling."""

import json
import subprocess
import sys
from pathlib import Path

from core.code_health import (
    code_health_snapshot,
    collect_code_health,
    compare_code_health,
    format_code_health_report,
    load_code_health_baseline,
    write_code_health_baseline,
)


def test_code_health_report_includes_trend_and_typed_structure_flags():
    report = collect_code_health(Path("."))
    assert report.regex_threshold == 300
    assert report.structured_block_normalizer is True
    assert report.typed_source_cards is True
    text = format_code_health_report(Path("."))
    assert "baseline_delta" in text
    assert "structured_blocks=<code>yes</code>" in text
    assert "typed_source_cards=<code>yes</code>" in text
    assert "typed normalizers" in text


def test_code_health_snapshot_and_delta_are_stable(tmp_path):
    report = collect_code_health(Path("."))
    snap = code_health_snapshot(report)
    assert snap["total_regex_markers"] == report.total_regex_markers
    baseline = {
        "files_scanned": report.files_scanned - 1,
        "total_regex_markers": report.total_regex_markers - 2,
        "total_postprocess_markers": report.total_postprocess_markers + 3,
    }
    delta = compare_code_health(report, baseline)
    assert delta.files_scanned_delta == 1
    assert delta.total_regex_delta == 2
    assert delta.total_postprocess_delta == -3

    path = write_code_health_baseline(Path("."), tmp_path / "baseline.json")
    loaded = load_code_health_baseline(Path("."), path)
    assert loaded is not None
    assert loaded["total_regex_markers"] == report.total_regex_markers
    assert json.loads(path.read_text(encoding="utf-8"))["typed_source_cards"] is True


def test_check_code_health_cli_is_non_blocking_by_default():
    result = subprocess.run(
        [sys.executable, "tools/check_code_health.py"],
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0
    assert "Code health" in result.stdout
    assert "baseline_delta" in result.stdout


def test_ci_runs_code_health_advisory():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Code health advisory" in workflow
    assert "python tools/check_code_health.py" in workflow
