#!/usr/bin/env python3
"""Classify a diff as formatting-only without trusting whitespace or tests.

A mass-formatting change cannot meaningfully satisfy line-based diff coverage:
every reformatted expression looks uncovered even when its behavior is unchanged.
This classifier is the explicit exception path for that one situation. It rejects
new/deleted Python files, configuration and workflow edits, and any production
AST change. Test ASTs may differ only in docstrings. Markdown and the blame-ignore
ledger are the sole allowed non-Python changes.

The command prints a GitHub Actions output (``pure=true`` or ``pure=false``) and
always exits successfully after a completed classification. A false result means
"use the ordinary diff-cover gate", not "the pull request failed".
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
_ALLOWED_NON_PYTHON = {".git-blame-ignore-revs"}


def _ast_dump(source: str, *, ignore_docstrings: bool = False) -> str:
    tree = ast.parse(source)
    if ignore_docstrings:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body[0].value.value = "<docstring>"
    return ast.dump(tree, include_attributes=False)


def same_ast(before: str, after: str, *, ignore_docstrings: bool = False) -> bool:
    """Return whether two valid Python sources have the same executable AST."""
    try:
        return _ast_dump(before, ignore_docstrings=ignore_docstrings) == _ast_dump(
            after, ignore_docstrings=ignore_docstrings
        )
    except SyntaxError:
        return False


def _changed_paths(root: Path, compare_ref: str) -> list[tuple[str, str]]:
    result = subprocess.run(
        ["git", "diff", "--name-status", "-z", "--no-renames", compare_ref, "--"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    fields = result.stdout.decode("utf-8").split("\0")
    if fields[-1]:
        raise ValueError("unterminated git diff output")
    fields.pop()
    if len(fields) % 2:
        raise ValueError("unexpected git name-status output")
    return list(zip(fields[::2], fields[1::2], strict=True))


def _base_source(root: Path, compare_ref: str, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{compare_ref}:{path}"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.decode("utf-8")


def classify(root: Path, compare_ref: str) -> tuple[bool, list[str]]:
    """Return ``(is_pure_formatting, reasons_for_rejection)`` for a worktree."""
    changes = _changed_paths(root, compare_ref)
    if not changes:
        return False, ["empty diff"]
    reasons: list[str] = []
    for status, path in changes:
        if path.endswith(".py"):
            if status != "M":
                reasons.append(f"{path}: Python status {status} is not modification")
                continue
            before = _base_source(root, compare_ref, path)
            after = (root / path).read_text(encoding="utf-8")
            if not same_ast(before, after, ignore_docstrings=path.startswith("tests/")):
                reasons.append(f"{path}: executable AST changed")
        elif not (path.endswith(".md") or path in _ALLOWED_NON_PYTHON):
            reasons.append(f"{path}: non-formatting file type")
        elif status not in {"A", "M"}:
            reasons.append(f"{path}: unsupported status {status}")
    return not reasons, reasons


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--compare-ref", default="origin/main")
    args = parser.parse_args(argv)
    try:
        pure, reasons = classify(args.root.resolve(), args.compare_ref)
    except (OSError, UnicodeError, ValueError, subprocess.CalledProcessError) as exc:
        pure, reasons = False, [f"classifier error: {exc}"]
    print(f"pure={str(pure).lower()}")
    for reason in reasons:
        print(f"ordinary diff-cover required: {reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
