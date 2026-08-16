#!/usr/bin/env python3
"""Flatten direct retry epoch base snapshot into one canonical source owner."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools" / "voxcpm2" / "direct_retry_epoch.py"
BASE = ROOT / "tools" / "voxcpm2" / "_direct_retry_epoch_base.py"


def strip_all(text: str, path: Path) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in reversed(tree.body):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            start = node.lineno - 1
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            del lines[start : (node.end_lineno or node.lineno)]
            return "".join(lines).rstrip() + "\n"
    raise RuntimeError(f"{path}: __all__ not found")


def main() -> int:
    if not TARGET.is_file() or not BASE.is_file():
        raise RuntimeError("direct retry snapshot pair is incomplete")
    base = strip_all(BASE.read_text(encoding="utf-8-sig"), BASE)
    current = TARGET.read_text(encoding="utf-8")
    marker = 'POLICY = "failed-segment-seed-epoch-scope-v2"\n'
    index = current.find(marker)
    if index < 0:
        raise RuntimeError("scope-aware retry marker not found")
    tail = current[index:]
    prefix = '''\n\nBASE_POLICY = POLICY\n_base_load_retry_epoch = load_retry_epoch\n_base_advance_retry_epoch = advance_retry_epoch\n\n'''
    merged = base.rstrip() + prefix + tail
    forbidden = (
        "_direct_retry_epoch_base.py",
        "exec(compile(",
        "globals()[\"__name__\"]",
        "_ORIGINAL_NAME",
        "_required_export(",
        "_required_callable(",
    )
    bad = [token for token in forbidden if token in merged]
    if bad:
        raise RuntimeError(f"direct retry snapshot loader survived: {bad}")
    ast.parse(merged, filename=str(TARGET))
    TARGET.write_text(merged, encoding="utf-8")
    BASE.unlink()

    blockers: list[str] = []
    for path in ROOT.rglob("*.py"):
        if path.resolve() == Path(__file__).resolve() or "tests" in path.parts or ".git" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("tools/") and any(tag in rel for tag in (
            "source_own_", "rewrite_", "runtime_", "refactor_", "flatten_", "remove_", "prune_"
        )):
            continue
        if "_direct_retry_epoch_base.py" in path.read_text(encoding="utf-8", errors="replace"):
            blockers.append(rel)
    if blockers:
        raise RuntimeError("deleted retry snapshot still referenced: " + ", ".join(sorted(set(blockers))))
    print("flattened direct retry epoch snapshot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
