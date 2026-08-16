#!/usr/bin/env python3
"""Remove direct-universal installers proven to have zero production root calls."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools" / "voxcpm2" / "direct_universal_runtime.py"
NAMES = {"install_worker_progress", "install_runtime_fingerprint"}
SKIP_PARTS = {"tests", ".git", ".venv", "venv", "__pycache__", ".pytest_cache"}


def leaf(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def prove_zero_calls() -> None:
    blockers: list[str] = []
    for path in ROOT.rglob("*.py"):
        if path.resolve() in {TARGET.resolve(), Path(__file__).resolve()}:
            continue
        relpath = path.relative_to(ROOT)
        rel = relpath.as_posix()
        if any(part in SKIP_PARTS for part in relpath.parts):
            continue
        if rel.startswith("tools/") and any(tag in rel for tag in (
            "source_own_", "rewrite_", "runtime_", "refactor_", "flatten_",
            "remove_", "prune_", "remaining_runtime_call_graph.py",
        )):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=rel)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and leaf(node.func) in NAMES:
                blockers.append(f"{rel}:{node.lineno}:{leaf(node.func)}")
    if blockers:
        raise RuntimeError("zero-call proof invalidated:\n" + "\n".join(blockers))


def remove_functions(text: str) -> str:
    tree = ast.parse(text, filename=str(TARGET))
    lines = text.splitlines(keepends=True)
    spans: list[tuple[int, int]] = []
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in NAMES:
            found.add(node.name)
            start = node.lineno - 1
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            spans.append((start, node.end_lineno or node.lineno))
    if found != NAMES:
        raise RuntimeError(f"installer definitions diverged: found={sorted(found)}")
    for start, end in sorted(spans, reverse=True):
        del lines[start:end]
    updated = "".join(lines)
    updated = updated.replace(', "install_runtime_fingerprint", "install_worker_progress"', "")
    updated = updated.replace('"install_runtime_fingerprint", "install_worker_progress", ', "")
    updated = updated.replace('    "install_runtime_fingerprint",\n', "")
    updated = updated.replace('    "install_worker_progress",\n', "")
    return updated


def main() -> int:
    prove_zero_calls()
    text = remove_functions(TARGET.read_text(encoding="utf-8"))
    for name in NAMES:
        if f"def {name}" in text:
            raise RuntimeError(f"dead installer survived: {name}")
    ast.parse(text, filename=str(TARGET))
    TARGET.write_text(text, encoding="utf-8")
    print("pruned dead direct-universal worker/fingerprint installers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
