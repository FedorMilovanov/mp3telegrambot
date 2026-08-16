#!/usr/bin/env python3
"""Flatten VoxCPM .py/package wrappers into one canonical .py owner."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tools" / "voxcpm2"
TARGETS = (
    "clean_runtime_contract",
    "continuous_reference_policy",
    "direct_max_quality_cli",
    "direct_monolith_contract",
    "direct_source_prosody",
    "direct_tail_artifact",
    "dub_quality_v4",
    "final_media_spatial_bed",
    "generic_clean_direct_runtime",
    "generic_project_runtime",
    "professional_audio_qa_v45",
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
        segment=segment.replace("getattr(_legacy,", "globals().get(")
        segment=segment.replace("_legacy.", "")
        pieces.append(segment)
    merged=base.rstrip()+"\n\n_BASE_ALL = tuple(globals().get('__all__', ()))\n\n"+"\n\n".join(pieces)+"\n"
    parsed=ast.parse(merged, filename=str(py))
    forbidden=("spec_from_file_location", "module_from_spec", "exec_module", "sys.modules", "_legacy.")
    bad=[token for token in forbidden if token in merged]
    legacy_names=[n.lineno for n in ast.walk(parsed) if isinstance(n,ast.Name) and n.id=="_legacy"]
    if bad or legacy_names:
        raise RuntimeError(f"{stem}: forbidden survived tokens={bad} _legacy_lines={legacy_names}")
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
