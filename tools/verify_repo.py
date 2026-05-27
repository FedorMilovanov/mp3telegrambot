#!/usr/bin/env python3
"""Full repository verification runner.

Runs the same non-negotiable checks we use before patch handoff:

1. compileall
2. pytest
3. ruff fatal lint subset from pyproject
4. git diff --check, when inside a git checkout

The script is intentionally dependency-light and works on Windows/Linux/macOS.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckResult:
    name: str
    command: tuple[str, ...]
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _run(name: str, command: list[str], cwd: Path) -> CheckResult:
    print(f"\n=== {name} ===")
    print("$ " + " ".join(command))
    proc = subprocess.run(command, cwd=str(cwd))
    return CheckResult(name=name, command=tuple(command), returncode=proc.returncode)


def _is_git_checkout(root: Path) -> bool:
    return (root / ".git").exists()


def run_checks(root: Path, *, skip_pytest: bool = False, skip_ruff: bool = False) -> list[CheckResult]:
    py = sys.executable
    checks: list[tuple[str, list[str]]] = [
        ("compileall", [py, "-m", "compileall", "-q", "."]),
    ]
    if not skip_pytest:
        checks.append(("pytest", [py, "-m", "pytest", "-q"]))
    if not skip_ruff:
        checks.append(("ruff", [py, "-m", "ruff", "check", "."]))
    if _is_git_checkout(root):
        checks.append(("git diff --check", ["git", "diff", "--check"]))

    results: list[CheckResult] = []
    for name, command in checks:
        result = _run(name, command, root)
        results.append(result)
        if not result.ok:
            print(f"\n❌ {name} failed with exit code {result.returncode}")
            break
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run full mp3telegrambot repository verification.")
    parser.add_argument("--root", default=".", help="Repository root, default: current directory")
    parser.add_argument("--skip-pytest", action="store_true", help="Skip pytest")
    parser.add_argument("--skip-ruff", action="store_true", help="Skip ruff")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    results = run_checks(root, skip_pytest=args.skip_pytest, skip_ruff=args.skip_ruff)
    failed = [r for r in results if not r.ok]
    print("\n=== SUMMARY ===")
    for r in results:
        mark = "✅" if r.ok else "❌"
        print(f"{mark} {r.name}")
    if failed:
        return failed[0].returncode or 1
    print("✅ Repository verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
