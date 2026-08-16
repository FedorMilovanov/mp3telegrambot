#!/usr/bin/env python3
"""Move direct surgical runtime wrappers into the canonical CLI source owner."""
from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli.py"
SURGICAL = ROOT / "tools" / "voxcpm2" / "direct_surgical_runtime.py"

FUNC_RENAMES = {
    "build_generation_length_request": "_build_generation_length_request",
    "acceptable_candidates": "_acceptable_candidates",
    "raw_failure_evidence": "_raw_failure_evidence",
}
NAME_RENAMES = {
    "original_log": "_surgical_base_log",
    "original_get_backend": "_surgical_base_get_backend",
    "original_prepare": "_surgical_base_prepare_reference",
    "original_read": "_surgical_base_read_segments",
    "original_build": "_surgical_base_build_generation_length_request",
    "original_acceptable": "_surgical_base_acceptable_candidates",
    "original_raw": "_surgical_base_raw_failure_evidence",
    "hash_file": "_surgical_hash_file",
    "max_tempo": "_surgical_max_tempo",
    "expected_encode": "_surgical_expected_encode",
    "expected_output": "_surgical_expected_output",
    "state": "_SURGICAL_RUNTIME_STATE",
    "_RUNTIME_SCOPE_FILES": "surgical_runtime._RUNTIME_SCOPE_FILES",
    "_segments_by_id": "surgical_runtime._segments_by_id",
    "_progress_value": "surgical_runtime._progress_value",
    "direct_surgical_io": "surgical_runtime.direct_surgical_io",
    "direct_retry_epoch": "surgical_runtime.direct_retry_epoch",
    "guard": "surgical_runtime.guard",
    "POLICY": "surgical_runtime.POLICY",
}


def _word_replace(source: str, old: str, new: str) -> str:
    return re.sub(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])", new, source)


def _installer_node(text: str) -> ast.FunctionDef:
    tree = ast.parse(text, filename=str(SURGICAL))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "install_surgical_runtime":
            return node
    raise RuntimeError("install_surgical_runtime not found")


def _source_owned_block(text: str) -> str:
    installer = _installer_node(text)
    nested = [node for node in installer.body if isinstance(node, ast.FunctionDef)]
    expected = {
        "log", "get_backend", "prepare_reference", "read_segments", "_segment",
        "_runtime_context", "_scope", "load_retry_epoch",
        "invalidate_segment_for_retry", "_position",
        "build_generation_length_request", "seed_for_attempt",
        "acceptable_candidates", "raw_failure_evidence",
    }
    found = {node.name for node in nested}
    if found != expected:
        raise RuntimeError(
            f"surgical installer nested contract diverged: missing={sorted(expected-found)} extra={sorted(found-expected)}"
        )

    header = '''SURGICAL_RUNTIME_POLICY = surgical_runtime.POLICY
_SURGICAL_RUNTIME_STATE: dict[str, Any] = {
    "segments": {},
    "work_dir": None,
    "retry_epochs": {},
    "current_segment_id": None,
    "runtime_context": None,
}
_surgical_base_log = log
_surgical_base_get_backend = get_backend
_surgical_base_prepare_reference = prepare_reference
_surgical_base_read_segments = read_segments
_surgical_base_build_generation_length_request = _build_generation_length_request
_surgical_base_acceptable_candidates = _acceptable_candidates
_surgical_base_raw_failure_evidence = _raw_failure_evidence
_surgical_hash_file = sha256_file
_surgical_max_tempo = float(MAX_TEMPO)
_surgical_expected_encode = int(EXPECTED_ENCODE_SR)
_surgical_expected_output = int(EXPECTED_OUTPUT_SR)
'''
    pieces = [header.rstrip()]
    for node in nested:
        source = ast.get_source_segment(text, node)
        if not source:
            raise RuntimeError(f"cannot extract surgical nested function {node.name}")
        source = textwrap.dedent(source)
        if node.name in FUNC_RENAMES:
            source = source.replace(
                f"def {node.name}(",
                f"def {FUNC_RENAMES[node.name]}(",
                1,
            )
        source = source.replace('namespace["__file__"]', "__file__")
        for old, new in NAME_RENAMES.items():
            source = _word_replace(source, old, new)
        pieces.append(source.rstrip())
    block = "\n\n".join(pieces) + "\n"
    if "namespace[" in block or "MutableMapping" in block or "install_surgical_runtime" in block:
        raise RuntimeError("surgical source block still contains runtime mutation")
    ast.parse(block, filename="<surgical-source-block>")
    return block


def _remove_installer(text: str) -> str:
    installer = _installer_node(text)
    lines = text.splitlines(keepends=True)
    start = installer.lineno - 1
    while start > 0 and not lines[start - 1].strip():
        start -= 1
    del lines[start : (installer.end_lineno or installer.lineno)]
    updated = "".join(lines)
    updated = updated.replace("from collections.abc import MutableMapping\n", "")
    updated = updated.replace("from typing import Any, Callable\n", "from typing import Any\n")
    updated = updated.replace(
        '    "tools/voxcpm2/direct_max_quality_cli/__init__.py",\n',
        "",
    )
    tree = ast.parse(updated, filename=str(SURGICAL))
    lines = updated.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            exports = [item for item in value if item != "install_surgical_runtime"]
            lines[node.lineno - 1 : (node.end_lineno or node.lineno)] = [f"__all__ = {exports!r}\n"]
            break
    updated = "".join(lines)
    if "install_surgical_runtime" in updated or "MutableMapping" in updated or "namespace[" in updated:
        raise RuntimeError("surgical installer/mutation survived helper module cleanup")
    ast.parse(updated, filename=str(SURGICAL))
    return updated


def main() -> int:
    surgical_text = SURGICAL.read_text(encoding="utf-8")
    block = _source_owned_block(surgical_text)

    cli = CLI.read_text(encoding="utf-8")
    import_line = "from tools.voxcpm2.direct_surgical_runtime import install_surgical_runtime\n"
    if import_line not in cli:
        raise RuntimeError("direct CLI surgical installer import missing")
    cli = cli.replace(
        import_line,
        "from tools.voxcpm2 import direct_surgical_runtime as surgical_runtime\n",
        1,
    )
    call = "install_surgical_runtime(globals())\n"
    if call not in cli:
        raise RuntimeError("direct CLI surgical installer call missing")
    cli = cli.replace(call, block + "\n", 1)
    if "install_surgical_runtime" in cli or "namespace[" in cli:
        raise RuntimeError("direct CLI surgical mutation survived")
    ast.parse(cli, filename=str(CLI))
    CLI.write_text(cli, encoding="utf-8")

    SURGICAL.write_text(_remove_installer(surgical_text), encoding="utf-8")

    blockers: list[str] = []
    for path in ROOT.rglob("*.py"):
        if path.resolve() == Path(__file__).resolve() or "tests" in path.parts or ".git" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("tools/") and any(tag in rel for tag in (
            "source_own_", "rewrite_", "runtime_", "refactor_", "flatten_", "remove_", "prune_"
        )):
            continue
        if "install_surgical_runtime" in path.read_text(encoding="utf-8", errors="replace"):
            blockers.append(rel)
    if blockers:
        raise RuntimeError("install_surgical_runtime still referenced: " + ", ".join(sorted(set(blockers))))

    print("direct surgical runtime is source-owned by canonical CLI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
