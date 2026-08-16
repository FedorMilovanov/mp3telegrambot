#!/usr/bin/env python3
"""Move direct final-audit composition into the canonical CLI source owner."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli.py"
AUDIT = ROOT / "tools" / "voxcpm2" / "direct_final_audit_v3.py"

BLOCK = r'''
FINAL_AUDIT_POLICY = final_audit.POLICY
_FINAL_AUDIT_STATE: dict[str, Any] = {
    "segments": [],
    "segments_json": None,
    "segments_json_sha256": "",
    "work_dir": None,
    "preflight_done": False,
    "model_context": {},
}
_final_audit_base_read_segments = read_segments
_final_audit_base_prepare_reference = prepare_reference
_final_audit_base_get_backend = get_backend


def _final_audit_base_context() -> dict[str, Any]:
    return {
        "final_audit_policy": final_audit.POLICY,
        "final_audit_sha256": final_audit._module_sha256(sha256_file),
        "segments_json_sha256": _FINAL_AUDIT_STATE.get("segments_json_sha256") or "",
        **dict(_FINAL_AUDIT_STATE.get("model_context") or {}),
    }


def _final_audit_persist_context() -> dict[str, Any]:
    work = _FINAL_AUDIT_STATE.get("work_dir")
    if work is None:
        return _final_audit_base_context()
    current = dict(surgical_runtime.guard.load_signature_context(Path(work)))
    current.update(_final_audit_base_context())
    surgical_runtime.guard.write_signature_context(Path(work), current)
    return current


def read_segments(path: Path) -> list[dict[str, Any]]:
    source = Path(path).resolve()
    final_audit._raw_segments(source)
    values = list(_final_audit_base_read_segments(source))
    if not values:
        raise RuntimeError("Direct renderer получил пустой список сегментов.")
    _FINAL_AUDIT_STATE.update(
        segments=values,
        segments_json=source,
        segments_json_sha256=str(sha256_file(source)),
        work_dir=None,
        preflight_done=False,
        model_context={},
    )
    return values


def _final_audit_model_discovered(model_path: Path) -> None:
    model = Path(model_path).resolve()
    _FINAL_AUDIT_STATE["model_context"] = final_audit._model_context(
        model,
        sha256_file,
    )
    _final_audit_persist_context()


def get_backend(name: str) -> Any:
    backend = _final_audit_base_get_backend(name)
    if str(getattr(backend, "backend_id", "")).strip().casefold() != "voxcpm2":
        return backend
    setter = getattr(backend, "set_model_discovery_callback", None)
    if not callable(setter):
        raise RuntimeError(
            "VoxCPM2 backend не поддерживает source-owned model discovery audit callback."
        )
    setter(_final_audit_model_discovered)
    return backend


def prepare_reference(source: Path, output: Path, sf_module: Any) -> dict[str, Any]:
    target = Path(output).resolve()
    work = (
        target.parent.parent
        if target.parent.name == "references_guarded"
        else target.parent
    )
    _FINAL_AUDIT_STATE["work_dir"] = work
    if not bool(_FINAL_AUDIT_STATE.get("preflight_done")):
        segments = list(_FINAL_AUDIT_STATE.get("segments") or [])
        if not segments:
            raise RuntimeError("Direct timing preflight вызван до read_segments.")
        context = _final_audit_persist_context()
        report = surgical_runtime.guard.run_pre_model_guard(
            segments,
            work_dir=work,
            max_tempo=float(MAX_TEMPO),
            signature_context=context,
        )
        _FINAL_AUDIT_STATE["preflight_done"] = True
        warnings = report.get("warning_ids") if isinstance(report, Mapping) else []
        log(
            "direct final timing preflight passed before references/model: "
            f"warnings={warnings or []}"
        )
    return dict(_final_audit_base_prepare_reference(source, output, sf_module))
'''


def _top_function_spans(text: str, names: set[str]) -> list[tuple[int, int]]:
    tree = ast.parse(text, filename=str(AUDIT))
    spans: list[tuple[int, int]] = []
    found: set[str] = set()
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            found.add(node.name)
            start = node.lineno - 1
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            spans.append((start, node.end_lineno or node.lineno))
    missing = names - found
    if missing:
        raise RuntimeError(f"final audit functions missing before cleanup: {sorted(missing)}")
    return spans


def _clean_helper(text: str) -> str:
    remove = {
        "_strict_segment_id",
        "_marker_id",
        "_prune_timing_archives",
        "_patch_shared_contracts",
        "install_final_audit",
    }
    lines = text.splitlines(keepends=True)
    for start, end in sorted(_top_function_spans(text, remove), reverse=True):
        del lines[start:end]
    updated = "".join(lines)
    updated = updated.replace("from collections.abc import Mapping, MutableMapping\n", "from collections.abc import Mapping\n")
    updated = updated.replace("from tools.voxcpm2 import direct_retry_epoch as retry\n", "")
    updated = updated.replace("from tools.voxcpm2 import direct_timing_guard as guard\n", "")
    for declaration in (
        "_INSTALLED_NAMESPACES: set[int] = set()\n",
        "_GUARD_PATCHED = False\n",
        "_RETRY_PATCHED = False\n",
    ):
        updated = updated.replace(declaration, "")

    tree = ast.parse(updated, filename=str(AUDIT))
    lines = updated.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            lines[node.lineno - 1 : (node.end_lineno or node.lineno)] = [
                '__all__ = ["MAX_SEGMENTS_BYTES", "POLICY"]\n'
            ]
            break
    updated = "".join(lines)
    forbidden = (
        "install_final_audit",
        "retry._strict_segment_id =",
        "guard.load_matching_timing_block =",
        ".discover_model =",
        "namespace[",
        "MutableMapping",
        "_GUARD_PATCHED",
        "_RETRY_PATCHED",
        "_INSTALLED_NAMESPACES",
        "def _patch_shared_contracts",
    )
    bad = [token for token in forbidden if token in updated]
    if bad:
        raise RuntimeError(f"final audit mutation survived helper cleanup: {bad}")
    ast.parse(updated, filename=str(AUDIT))
    return updated


def main() -> int:
    audit_text = AUDIT.read_text(encoding="utf-8")
    cli = CLI.read_text(encoding="utf-8")
    import_line = "from tools.voxcpm2.direct_final_audit_v3 import install_final_audit\n"
    if import_line not in cli:
        raise RuntimeError("direct CLI final-audit installer import missing")
    cli = cli.replace(
        import_line,
        "from tools.voxcpm2 import direct_final_audit_v3 as final_audit\n",
        1,
    )
    call = "install_final_audit(globals())\n"
    if call not in cli:
        raise RuntimeError("direct CLI final-audit installer call missing")
    cli = cli.replace(call, BLOCK.strip() + "\n\n", 1)
    forbidden_cli = (
        "install_final_audit",
        "namespace[",
        "retry._strict_segment_id =",
        "guard.load_matching_timing_block =",
        ".discover_model =",
    )
    bad = [token for token in forbidden_cli if token in cli]
    if bad:
        raise RuntimeError(f"direct CLI final-audit mutation survived: {bad}")
    ast.parse(cli, filename=str(CLI))
    CLI.write_text(cli, encoding="utf-8")

    AUDIT.write_text(_clean_helper(audit_text), encoding="utf-8")

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
