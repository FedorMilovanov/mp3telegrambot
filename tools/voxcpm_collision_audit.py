#!/usr/bin/env python3
"""Temporary map of .py/package import-name collisions under tools/voxcpm2."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tools" / "voxcpm2"
SIGNALS = (
    "sys.modules", "spec_from_file_location", "module_from_spec", "exec(",
    "setattr(", "globals()[", "__class__", "._legacy", "install_",
)


def ast_stats(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {"defs": -1, "assign_attr": -1, "calls": -1}
    defs = sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for n in tree.body)
    assign_attr = 0
    calls = 0
    for n in ast.walk(tree):
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            assign_attr += sum(isinstance(t, ast.Attribute) for t in targets)
        if isinstance(n, ast.Call):
            calls += 1
    return {"defs": defs, "assign_attr": assign_attr, "calls": calls}


def main() -> int:
    pairs=[]
    for py in sorted(ROOT.glob("*.py")):
        stem=py.stem
        init=ROOT/stem/"__init__.py"
        if not init.is_file():
            continue
        py_text=py.read_text(encoding="utf-8", errors="replace")
        init_text=init.read_text(encoding="utf-8", errors="replace")
        signals=[s for s in SIGNALS if s in init_text]
        pairs.append((stem,py,init,signals,ast_stats(py),ast_stats(init)))
    print(f"COLLISIONS={len(pairs)}")
    for stem,py,init,signals,pstats,istats in pairs:
        print(f"\n## {stem}")
        print(f"py_lines={len(py.read_text(encoding='utf-8',errors='replace').splitlines())} init_lines={len(init.read_text(encoding='utf-8',errors='replace').splitlines())}")
        print(f"py_stats={pstats} init_stats={istats}")
        print("signals=" + ",".join(signals))
        for i,line in enumerate(init.read_text(encoding='utf-8',errors='replace').splitlines(),1):
            if any(s in line for s in SIGNALS):
                print(f"  {i:04d}: {line.strip()[:240]}")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
