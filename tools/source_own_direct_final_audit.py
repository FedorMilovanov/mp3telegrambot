#!/usr/bin/env python3
"""Move direct final-audit composition into the canonical CLI source owner."""
from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli.py"
AUDIT = ROOT / "tools" / "voxcpm2" / "direct_final_audit_v3.py"

FUNC_RENAMES = {
    "acceptable_candidates": "_acceptable_candidates",
    "raw_failure_evidence": "_raw_failure_evidence",
    "segment_signature": "_segment_signature",
}
NAME_RENAMES = {
    "original_read_segments": "_final_audit_base_read_segments",
    "original_get_backend": "_final_audit_base_get_backend",
    "original_prepare": "_final_audit_base_prepare_reference",
    "original_load_epoch": "_final_audit_base_load_retry_epoch",
    "original_seed": "_final_audit_base_seed_for_attempt",
    "original_acceptable": "_final_audit_base_acceptable_candidates",
    "original_raw": "_final_audit_base_raw_failure_evidence",
    "original_signature": "_final_audit_base_segment_signature",
    "original_invalidate": "_final_audit_base_invalidate_segment_for_retry",
    "original_build": "_final_audit_base_build_timeline",
    "hash_file": "_final_audit_hash_file",
    "policy": "_final_audit_renderer_policy",
    "retry_policy": "_final_audit_retry_policy",
    "state": "_FINAL_AUDIT_STATE",
    "_final_context": "final_audit._final_context",
    "_write_signature_context": "final_audit._write_signature_context",
    "_fingerprint": "final_audit._fingerprint",
    "_prune_checkpoints": "final_audit._prune_checkpoints",
    "_prune_marker_archives": "final_audit._prune_marker_archives",
    "_artifact_count": "final_audit._artifact_count",
}


def _word_replace(source: str, old: str, new: str) -> str:
    return re.sub(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])", new, source)


def _installer(text: str) -> ast.FunctionDef:
    tree = ast.parse(text, filename=str(AUDIT))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "install_final_audit":
            return node
    raise RuntimeError("install_final_audit not found")


def _build_block(text: str) -> str:
    installer = _installer(text)
    nested = [node for node in installer.body if isinstance(node, ast.FunctionDef)]
    names = {node.name for node in nested}
    expected = {
        "_update_context", "read_segments", "_wrap_discover_model", "get_backend",
        "prepare_reference", "_work_dir_from_candidates", "load_retry_epoch",
        "seed_for_attempt", "acceptable_candidates", "raw_failure_evidence",
        "segment_signature", "invalidate_segment_for_retry", "build_timeline",
    }
    if names != expected:
        raise RuntimeError(
            f"final audit nested contract diverged: missing={sorted(expected-names)} extra={sorted(names-expected)}"
        )
    header = '''FINAL_AUDIT_POLICY = final_audit.POLICY
_FINAL_AUDIT_STATE: dict[str, Any] = {
    "context": {},
    "segments": {},
    "work_dir": None,
}
_final_audit_base_read_segments = read_segments
_final_audit_base_get_backend = get_backend
_final_audit_base_prepare_reference = prepare_reference
_final_audit_base_load_retry_epoch = load_retry_epoch
_final_audit_base_seed_for_attempt = seed_for_attempt
_final_audit_base_acceptable_candidates = _acceptable_candidates
_final_audit_base_raw_failure_evidence = _raw_failure_evidence
_final_audit_base_segment_signature = _segment_signature
_final_audit_base_invalidate_segment_for_retry = invalidate_segment_for_retry
_final_audit_base_build_timeline = build_timeline
_final_audit_hash_file = sha256_file
_final_audit_renderer_policy = str(POLICY)
_final_audit_retry_policy = str(RETRY_EPOCH_POLICY)
'''
    pieces = [header.rstrip()]
    for node in nested:
        if node.name in {"_wrap_discover_model", "get_backend"}:
            continue
        source = ast.get_source_segment(text, node)
        if not source:
            raise RuntimeError(f"cannot extract final audit nested function {node.name}")
        source = textwrap.dedent(source)
        if node.name in FUNC_RENAMES:
            source = source.replace(
                f"def {node.name}(",
                f"def {FUNC_RENAMES[node.name]}(",
                1,
            )
        for old, new in NAME_RENAMES.items():
            source = _word_replace(source, old, new)
        pieces.append(source.rstrip())

    callback = '''def _final_audit_model_discovered(model_path: Path) -> None:
    model = Path(model_path).resolve()
    context = dict(_FINAL_AUDIT_STATE.get("context") or {})
    context["model_dir"] = str(model)
    context["model_artifact_count"] = final_audit._artifact_count(model)
    _FINAL_AUDIT_STATE["context"] = context
    _update_context()


def get_backend(identifier: Any) -> Any:
    backend = _final_audit_base_get_backend(identifier)
    setter = getattr(backend, "set_model_discovery_callback", None)
    if not callable(setter):
        raise RuntimeError(
            "Direct backend не поддерживает source-owned model discovery audit callback."
        )
    setter(_final_audit_model_discovered)
    return backend
'''
    # Place callback after _update_context so it can call it immediately.
    pieces.insert(2, callback.rstrip())
    block = "\n\n".join(pieces) + "\n"
    if "namespace[" in block or "setattr(" in block or "install_final_audit" in block:
        raise RuntimeError("final audit source block still contains runtime surgery")
    ast.parse(block, filename="<final-audit-source-block>")
    return block


def _remove_installer(text: str) -> str:
    node = _installer(text)
    lines = text.splitlines(keepends=True)
    start = node.lineno - 1
    while start > 0 and not lines[start - 1].strip():
        start -= 1
    del lines[start : (node.end_lineno or node.lineno)]
    updated = "".join(lines)
    updated = updated.replace("from collections.abc import MutableMapping\n", "")
    updated = updated.replace("from numbers import Integral\n", "")
    updated = updated.replace("from services.speech_backends import get_backend as resolve_backend\n", "")
    updated = updated.replace("from tools.voxcpm2 import direct_retry_epoch as retry\n", "")
    updated = updated.replace("from tools.voxcpm2 import direct_timing_guard as guard\n", "")
    tree = ast.parse(updated, filename=str(AUDIT))
    lines = updated.splitlines(keepends=True)
    for item in tree.body:
        if isinstance(item, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in item.targets
        ):
            value = ast.literal_eval(item.value)
            exports = [name for name in value if name != "install_final_audit"]
            lines[item.lineno - 1 : (item.end_lineno or item.lineno)] = [f"__all__ = {exports!r}\n"]
            break
    updated = "".join(lines)
    forbidden = (
        "install_final_audit", "retry._strict_segment_id =", "guard.load_matching_timing_block =",
        ".discover_model =", "namespace[", "setattr(",
    )
    bad = [token for token in forbidden if token in updated]
    if bad:
        raise RuntimeError(f"final audit mutation survived helper cleanup: {bad}")
    ast.parse(updated, filename=str(AUDIT))
    return updated


def main() -> int:
    audit_text = AUDIT.read_text(encoding="utf-8")
    block = _build_block(audit_text)
    cli = CLI.read_text(encoding="utf-8")
    import_line = "from tools.voxcpm2.direct_final_audit_v3 import install_final_audit\n"
    if import_line not in cli:
        raise RuntimeError("direct CLI final-audit installer import missing")
    cli = cli.replace(import_line, "from tools.voxcpm2 import direct_final_audit_v3 as final_audit\n", 1)
    call = "install_final_audit(globals())\n"
    if call not in cli:
        raise RuntimeError("direct CLI final-audit installer call missing")
    cli = cli.replace(call, block + "\n", 1)
    if "install_final_audit" in cli or "namespace[" in cli:
        raise RuntimeError("direct CLI final-audit mutation survived")
    ast.parse(cli, filename=str(CLI))
    CLI.write_text(cli, encoding="utf-8")

    AUDIT.write_text(_remove_installer(audit_text), encoding="utf-8")

    blockers: list[str] = []
    for path in ROOT.rglob("*.py"):
        if path.resolve() == Path(__file__).resolve() or "tests" in path.parts or ".git" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("tools/") and any(tag in rel for tag in (
            "source_own_", "rewrite_", "runtime_", "refactor_", "flatten_", "remove_", "prune_"
        )):
            continue
        if "install_final_audit" in path.read_text(encoding="utf-8", errors="replace"):
            blockers.append(rel)
    if blockers:
        raise RuntimeError("install_final_audit still referenced: " + ", ".join(sorted(set(blockers))))
    print("direct final audit is source-owned by canonical CLI and dependency owners")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
