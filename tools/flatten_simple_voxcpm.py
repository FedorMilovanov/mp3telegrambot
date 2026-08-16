#!/usr/bin/env python3
"""Flatten simple VoxCPM .py/package wrappers into a single canonical .py owner."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tools" / "voxcpm2"
TARGETS = (
    "clean_production_core",
    "direct_max_quality_render",
    "direct_russian_cadence",
    "direct_timeline_delivery_qa",
    "expressive_continuity",
    "final_media_qa",
    "generic_clean_audio_repair_runtime",
)


def skip_node(node: ast.AST, text: str) -> bool:
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
        return True
    if isinstance(node, ast.ImportFrom) and node.module == "__future__":
        return True
    if isinstance(node, ast.Import):
        names={a.name for a in node.names}
        if names <= {"importlib.util", "sys"}:
            return True
    if isinstance(node, ast.ImportFrom) and node.module in {"importlib", "sys"}:
        return True
    assigned=set()
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets=node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name): assigned.add(target.id)
    if assigned & {"_LEGACY_PATH", "_SPEC", "_legacy", "_previous_legacy", "_module"}:
        return True
    source=ast.get_source_segment(text,node) or ""
    if any(token in source for token in (
        "spec_from_file_location", "module_from_spec", "exec_module",
        "sys.modules[_SPEC", "sys.modules.pop(_SPEC", "sys.modules[__name__]",
    )):
        return True
    if isinstance(node, ast.For) and "dir(_legacy)" in source:
        return True
    if isinstance(node, ast.If) and "_SPEC" in source and ("loader" in source or "sys.modules" in source):
        return True
    if isinstance(node, ast.ClassDef) and node.name == "_WriteThroughModule":
        return True
    if "_module.__class__" in source:
        return True
    return False


def flatten(stem: str) -> None:
    py=ROOT/f"{stem}.py"
    init=ROOT/stem/"__init__.py"
    if not py.is_file() or not init.is_file():
        raise RuntimeError(f"collision missing for {stem}")
    base=py.read_text(encoding="utf-8")
    pkg=init.read_text(encoding="utf-8")
    tree=ast.parse(pkg, filename=str(init))
    pieces=[]
    for node in tree.body:
        if skip_node(node,pkg):
            continue
        segment=ast.get_source_segment(pkg,node)
        if not segment:
            continue
        segment=segment.replace('getattr(_legacy, "__all__", ())', '_BASE_ALL')
        segment=segment.replace("getattr(_legacy, '__all__', ())", '_BASE_ALL')
        segment=segment.replace("_legacy.", "")
        pieces.append(segment)
    merged=base.rstrip()+"\n\n_BASE_ALL = tuple(globals().get('__all__', ()))\n\n"+"\n\n".join(pieces)+"\n"
    forbidden=("spec_from_file_location", "module_from_spec", "exec_module", "sys.modules", "_legacy.")
    bad=[token for token in forbidden if token in merged]
    if bad:
        raise RuntimeError(f"{stem}: forbidden tokens survived: {bad}")
    ast.parse(merged, filename=str(py))
    py.write_text(merged, encoding="utf-8")
    init.unlink()
    main_file=ROOT/stem/"__main__.py"
    if main_file.is_file():
        main_text=main_file.read_text(encoding="utf-8", errors="replace")
        if stem in main_text or "from ." in main_text:
            main_file.unlink()
    print(f"flattened {stem}")


def main() -> int:
    for stem in TARGETS:
        flatten(stem)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
