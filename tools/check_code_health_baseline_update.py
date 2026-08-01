#!/usr/bin/env python3
"""Require machine-readable evidence for every code-health baseline change."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

BASELINE = Path("docs/code_health_baseline.json")
REPORT_PREFIX = "docs/code_health_reports/"
POLICY = "evidence-backed-code-health-baseline-update-v1"


def _git(*args: str) -> str:
    process = subprocess.run(
        ["git", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {(process.stderr or process.stdout).strip()}"
        )
    return process.stdout


def _default_base() -> tuple[str, bool]:
    if os.getenv("GITHUB_EVENT_NAME") == "pull_request":
        base = os.getenv("GITHUB_BASE_REF", "main").strip() or "main"
        return f"origin/{base}", True
    return "HEAD^", False


def changed_files(base_ref: str, *, merge_base: bool) -> tuple[str, ...]:
    separator = "..." if merge_base else ".."
    output = _git("diff", "--name-only", f"{base_ref}{separator}HEAD")
    return tuple(line.strip().replace("\\", "/") for line in output.splitlines() if line.strip())


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid JSON evidence file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Evidence file must contain a JSON object: {path}")
    return payload


def _snapshot(payload: dict[str, Any]) -> dict[str, int]:
    required = (
        "files_scanned",
        "total_regex_markers",
        "total_postprocess_markers",
    )
    result: dict[str, int] = {}
    for key in required:
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeError(f"Baseline field {key} must be a non-negative integer.")
        result[key] = value
    return result


def validate_update(changed: tuple[str, ...]) -> dict[str, Any]:
    normalized_baseline = BASELINE.as_posix()
    if normalized_baseline not in changed:
        return {
            "policy": POLICY,
            "baseline_changed": False,
            "reports": [],
        }

    report_paths = sorted(
        Path(path)
        for path in changed
        if path.startswith(REPORT_PREFIX) and path.endswith(".json")
    )
    if not report_paths:
        raise RuntimeError(
            "docs/code_health_baseline.json changed without a JSON report under "
            "docs/code_health_reports/."
        )

    baseline = _snapshot(_load(BASELINE))
    matching: list[str] = []
    for report_path in report_paths:
        report = _load(report_path)
        if report.get("policy") != "intentional-code-health-baseline-refresh-v1":
            continue
        current = report.get("current")
        evidence = report.get("evidence")
        reason = str(report.get("reason") or "").strip()
        if not isinstance(current, dict) or _snapshot(current) != baseline:
            continue
        if not reason:
            continue
        if not isinstance(evidence, dict):
            continue
        focused = evidence.get("focused_tests")
        if not isinstance(focused, list) or not focused or not all(
            isinstance(item, str) and item.startswith("tests/") for item in focused
        ):
            continue
        matching.append(report_path.as_posix())

    if not matching:
        raise RuntimeError(
            "No changed code-health report exactly matches the new baseline and "
            "documents focused regression tests."
        )
    return {
        "policy": POLICY,
        "baseline_changed": True,
        "reports": matching,
        "baseline": baseline,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-ref")
    parser.add_argument("--merge-base", action="store_true")
    args = parser.parse_args()
    if args.base_ref:
        base_ref = args.base_ref
        merge_base = bool(args.merge_base)
    else:
        base_ref, merge_base = _default_base()
    result = validate_update(changed_files(base_ref, merge_base=merge_base))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"CODE_HEALTH_BASELINE_UPDATE_FAILED: {type(exc).__name__}: {exc}")
        raise SystemExit(1) from exc
