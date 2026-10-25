#!/usr/bin/env python3
"""Run the executable production-layer contract from ``.import-linter``."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ".import-linter"
_CLI = "from importlinter.cli import lint_imports_command; lint_imports_command()"


def command(root: Path = ROOT) -> list[str]:
    """Build an isolated CLI invocation.

    import-linter configures logging in-process. Keeping that side effect out of
    pytest and the repository verifier preserves their callers' logging policy.
    """
    return [
        sys.executable,
        "-c",
        _CLI,
        "--config",
        str(root / CONFIG),
        "--no-cache",
        "--no-logo",
    ]


def check(root: Path = ROOT) -> int:
    """Return the import-linter exit status without creating a graph cache."""
    return subprocess.run(command(root), cwd=root, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    return check(args.root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
