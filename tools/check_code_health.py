#!/usr/bin/env python3
"""Advisory code-health CLI for regex/postprocess trend tracking.

Default mode is non-blocking: it prints a report and exits 0. Use --strict if a
future CI job should fail when the soft regex threshold is exceeded.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.code_health import (  # noqa: E402
    collect_code_health,
    format_code_health_report,
    write_code_health_baseline,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advisory regex/postprocess code-health report")
    parser.add_argument("--root", default=str(ROOT), help="repository root")
    parser.add_argument("--strict", action="store_true", help="exit 1 when regex markers exceed threshold")
    parser.add_argument("--write-baseline", action="store_true", help="write docs/code_health_baseline.json")
    parser.add_argument("--baseline-path", default=None, help="custom baseline path for --write-baseline")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if args.write_baseline:
        path = write_code_health_baseline(root, args.baseline_path)
        print(f"WROTE_BASELINE {path}")
        return 0

    print(format_code_health_report(root))
    report = collect_code_health(root)
    if args.strict and report.regex_over_threshold:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
