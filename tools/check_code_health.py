#!/usr/bin/env python3
"""Code-health CLI for regex/postprocess trend tracking.

The human report remains advisory. ``--fail-on-regression`` compares the current
snapshot with the committed baseline and fails only when regex or postprocess
markers grow beyond an explicitly allowed delta. Existing technical debt is
therefore visible but does not block unrelated work.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.code_health import (  # noqa: E402
    collect_code_health,
    compare_code_health,
    format_code_health_report,
    load_code_health_baseline,
    write_code_health_baseline,
)


def _configure_stdout() -> None:
    """Keep Cyrillic/emoji diagnostics safe on Windows legacy consoles."""
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except Exception:
        pass


def _safe_print(value: object = "") -> None:
    _configure_stdout()
    try:
        print(value)
    except UnicodeEncodeError:
        text = str(value)
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = text.encode(encoding, errors="backslashreplace").decode(
            encoding, errors="replace"
        )
        print(safe)


def _env_nonnegative_int(name: str, default: int = 0) -> int:
    try:
        return max(0, int(os.getenv(name, str(default)).strip() or str(default)))
    except (TypeError, ValueError):
        return default


def _regression_exit_code(root: Path, baseline_path: str | Path | None) -> int:
    baseline = load_code_health_baseline(root, baseline_path)
    if baseline is None:
        _safe_print("CODE_HEALTH_BASELINE_MISSING")
        return 2

    report = collect_code_health(root)
    delta = compare_code_health(report, baseline)
    regex_allowance = _env_nonnegative_int("CODE_HEALTH_MAX_REGEX_DELTA")
    post_allowance = _env_nonnegative_int("CODE_HEALTH_MAX_POSTPROCESS_DELTA")
    regex_bad = delta.total_regex_delta > regex_allowance
    post_bad = delta.total_postprocess_delta > post_allowance

    _safe_print(
        "CODE_HEALTH_REGRESSION "
        f"regex_delta={delta.total_regex_delta} allowance={regex_allowance} "
        f"postprocess_delta={delta.total_postprocess_delta} allowance={post_allowance}"
    )
    if regex_bad or post_bad:
        _safe_print(
            "CODE_HEALTH_REGRESSION_FAILED: review the new deterministic layer, "
            "add focused regression tests, then intentionally refresh the baseline."
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regex/postprocess code-health report")
    parser.add_argument("--root", default=str(ROOT), help="repository root")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="legacy mode: exit 1 when the absolute regex threshold is exceeded",
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="compare against baseline and reject positive regex/postprocess growth",
    )
    parser.add_argument("--write-baseline", action="store_true", help="write baseline JSON")
    parser.add_argument(
        "--baseline-path",
        default=None,
        help="custom baseline path for reading or writing",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    if args.write_baseline:
        path = write_code_health_baseline(root, args.baseline_path)
        _safe_print(f"WROTE_BASELINE {path}")
        return 0

    _safe_print(format_code_health_report(root))
    report = collect_code_health(root)
    if args.fail_on_regression:
        return _regression_exit_code(root, args.baseline_path)
    if args.strict and report.regex_over_threshold:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
