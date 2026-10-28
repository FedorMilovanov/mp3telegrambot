#!/usr/bin/env python3
"""Full repository verification runner.

Runs the same non-negotiable checks we use before patch handoff:

1. compileall
2. generated model identifiers are in sync with core/model_catalog.py
3. generated env reference is in sync with the code
4. generated docs index (docs/README.md) is in sync with docs/
5. pytest
6. ruff fatal lint subset from pyproject
7. ruff formatting is clean repository-wide
8. mypy strict for the declared type slice (``tools/check_types.py``)
9. production import layers (``tools/check_import_layers.py``)
10. git diff --check, when inside a git checkout

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
    proc = subprocess.run(command, cwd=str(cwd), check=False)  # noqa: S603 -- fixed verifier argv, no shell
    return CheckResult(name=name, command=tuple(command), returncode=proc.returncode)


def _is_git_checkout(root: Path) -> bool:
    return (root / ".git").exists()


def run_checks(
    root: Path,
    *,
    skip_pytest: bool = False,
    skip_ruff: bool = False,
    skip_typecheck: bool = False,
) -> list[CheckResult]:
    py = sys.executable
    checks: list[tuple[str, list[str]]] = [
        ("compileall", [py, "-m", "compileall", "-q", "."]),
        # Model identifiers are generated from core/model_catalog.py.
        ("model catalog", [py, "scripts/render_model_catalog.py", "--check"]),
        # The env reference is generated from the code; stale means the docs lie.
        ("env reference", [py, "scripts/render_env_reference.py", "--check"]),
        # The docs index is generated from the directory: stale means a document
        # exists that nobody can find, or an entry points at a deleted file.
        ("docs index", [py, "tools/render_docs_index.py", "--check"]),
    ]
    if not skip_pytest:
        checks.append(("pytest", [py, "-m", "pytest", "-q"]))
    if not skip_ruff:
        checks.extend(
            [
                ("ruff", [py, "-m", "ruff", "check", "."]),
                ("ruff format", [py, "-m", "ruff", "format", "--check", "."]),
            ]
        )
    if not skip_typecheck:
        # Тот же гейт, что в CI: объявленный срез типов (см. tools/check_types.py).
        checks.append(("typecheck", [py, "tools/check_types.py"]))
    checks.append(("import layers", [py, "tools/check_import_layers.py"]))
    if _is_git_checkout(root):
        checks.append(("git diff --check", ["git", "diff", "--check"]))

    results: list[CheckResult] = []
    for name, command in checks:
        result = _run(name, command, root)
        results.append(result)
        if not result.ok:
            print(f"\n[FAIL] {name} failed with exit code {result.returncode}")
            break
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run full mp3telegrambot repository verification.")
    parser.add_argument("--root", default=".", help="Repository root, default: current directory")
    parser.add_argument("--skip-pytest", action="store_true", help="Skip pytest")
    parser.add_argument("--skip-ruff", action="store_true", help="Skip ruff")
    parser.add_argument("--skip-typecheck", action="store_true", help="Skip mypy type check")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    results = run_checks(
        root,
        skip_pytest=args.skip_pytest,
        skip_ruff=args.skip_ruff,
        skip_typecheck=args.skip_typecheck,
    )
    failed = [r for r in results if not r.ok]
    print("\n=== SUMMARY ===")
    for r in results:
        mark = "OK" if r.ok else "FAIL"
        print(f"[{mark}] {r.name}")
    if failed:
        return failed[0].returncode or 1
    print("Repository verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
