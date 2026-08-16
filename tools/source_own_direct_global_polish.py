#!/usr/bin/env python3
"""Move direct global polish behavior into the modules that own each contract."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLISH = ROOT / "tools" / "voxcpm2" / "direct_surgical_polish_v2.py"
GUARD = ROOT / "tools" / "voxcpm2" / "direct_timing_guard.py"
RETRY = ROOT / "tools" / "voxcpm2" / "direct_retry_epoch.py"
SIO = ROOT / "tools" / "voxcpm2" / "direct_surgical_io.py"
RUNTIME = ROOT / "tools" / "voxcpm2" / "direct_surgical_runtime.py"
CLI = ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli.py"
GENERIC = ROOT / "tools" / "voxcpm2" / "generic_clean_direct_runtime.py"


def rename_function(text: str, path: Path, old: str, new: str, *, occurrence: int = 1) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    seen = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == old:
            seen += 1
            if seen != occurrence:
                continue
            line = lines[node.lineno - 1]
            needle = f"def {old}("
            if needle not in line:
                raise RuntimeError(f"unexpected definition line for {old}: {line!r}")
            lines[node.lineno - 1] = line.replace(needle, f"def {new}(", 1)
            return "".join(lines)
    raise RuntimeError(f"{path}: function {old} occurrence {occurrence} not found")


def replace_function(text: str, path: Path, name: str, source: str) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            lines[start : (node.end_lineno or node.lineno)] = ["\n\n" + source.strip() + "\n"]
            return "".join(lines)
    raise RuntimeError(f"{path}: function {name} not found")


def remove_function(text: str, path: Path, name: str) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            del lines[start : (node.end_lineno or node.lineno)]
            return "".join(lines)
    raise RuntimeError(f"{path}: function {name} not found")


GUARD_WRAPPERS = r'''
MARKER_SCHEMA_VERSION = polish.MARKER_SCHEMA_VERSION


def run_pre_model_guard(
    segments,
    *,
    work_dir,
    max_tempo,
    signature_context,
):
    return _polish_base_run_pre_model_guard(
        polish._segments(segments),
        work_dir=work_dir,
        max_tempo=max_tempo,
        signature_context=signature_context,
    )


def load_signature_context(work_dir: Path) -> dict[str, Any]:
    value = dict(_polish_base_load_signature_context(work_dir))
    value.update(polish._runtime_marker(work_dir))
    value["surgical_polish_policy"] = polish.POLICY
    return value


def persist_timing_block(
    work_dir: Path,
    *,
    segment: Mapping[str, Any],
    signature_context: Mapping[str, Any] | None,
    retry_epoch: int,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    clean = polish._segments([dict(segment)])[0]
    value = dict(
        _polish_base_persist_timing_block(
            work_dir,
            segment=clean,
            signature_context=signature_context,
            retry_epoch=retry_epoch,
            evidence=evidence,
        )
    )
    value.update(
        schema_version=polish.MARKER_SCHEMA_VERSION,
        policy=polish.MARKER_POLICY,
        segment_id=int(clean["id"]),
        signature=failure_scope_fingerprint(
            clean,
            signature_context=signature_context,
        ),
        speech_slot=round(
            clean["end"] - clean["start"] - clean["tail_guard"],
            6,
        ),
        retry_epoch=polish._integer(retry_epoch, "retry_epoch"),
    )
    polish._atomic(polish._marker_path(work_dir, clean["id"]), value)
    return value


def load_matching_timing_block(
    work_dir: Path,
    *,
    segment: Mapping[str, Any],
    signature_context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    clean = polish._segments([dict(segment)])[0]
    path = polish._marker_path(work_dir, clean["id"])
    if not path.is_file():
        return None
    try:
        value = polish._read(path)
        expected = failure_scope_fingerprint(
            clean,
            signature_context=signature_context,
        )
        slot = clean["end"] - clean["start"] - clean["tail_guard"]
        try:
            recommendation = value.get("recommendation")
            valid = bool(
                value.get("schema_version") == polish.MARKER_SCHEMA_VERSION
                and value.get("policy") == polish.MARKER_POLICY
                and polish._integer(value.get("segment_id"), "marker.id") == clean["id"]
                and polish._sha(value.get("signature")) == expected
                and 0 <= polish._integer(value.get("retry_epoch"), "marker.epoch") < polish.MAX_SCOPE_EPOCH
                and abs(polish._number(value.get("speech_slot"), "marker.slot") - slot) <= 1e-6
                and isinstance(value.get("evidence"), dict)
                and isinstance(recommendation, Mapping)
                and polish._number(
                    recommendation.get("hard_minimum_speech_slot"),
                    "hard_slot",
                ) + 1e-6 >= slot
                and 0 <= polish._integer(
                    recommendation.get("hard_shorten_percent"),
                    "shorten",
                ) <= 100
            )
        except RuntimeError:
            valid = False
        if valid:
            return value
        polish._archive(
            path,
            "input-changed"
            if value.get("signature") != expected
            else "contract-mismatch",
        )
        return None
    finally:
        _prune_marker_archives(path.parent, path.name, limit=8)
'''

RETRY_WRAPPERS = r'''
def _scope_epochs(payload: Mapping[str, Any]) -> dict[str, int]:
    return polish._scope_epochs(payload)


def invalidate_segment_for_retry(
    work_dir: Path,
    segment: dict[str, Any],
    *,
    reason: str,
    fitted_path: Path | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence_payload = dict(evidence or {})
    fingerprint = polish._sha(evidence_payload.get("failure_scope_fingerprint"))
    result = dict(
        _polish_base_invalidate_segment_for_retry(
            work_dir,
            segment,
            reason=reason,
            fitted_path=fitted_path,
            evidence=evidence_payload,
        )
    )
    result["raw_retry_epoch"] = int(result.get("retry_epoch") or 0)
    if fingerprint:
        epoch = load_retry_epoch(
            work_dir,
            segment.get("id"),
            scope_fingerprint=fingerprint,
        )
        result.update(
            retry_epoch=epoch,
            scope_retry_epoch=epoch,
            last_scope_epoch=epoch,
            scope_fingerprint=fingerprint,
            policy=polish.POLICY,
        )
    return result
'''


def clean_polish() -> None:
    text = POLISH.read_text(encoding="utf-8")
    for line in (
        "from tools.voxcpm2 import direct_retry_epoch as retry\n",
        "from tools.voxcpm2 import direct_surgical_io as sio\n",
        "from tools.voxcpm2 import direct_surgical_runtime as runtime\n",
        "from tools.voxcpm2 import direct_timing_guard as guard\n",
        "_INSTALLED = False\n",
    ):
        text = text.replace(line, "")
    text = remove_function(text, POLISH, "install_global_polish")
    tree = ast.parse(text, filename=str(POLISH))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            lines[node.lineno - 1 : (node.end_lineno or node.lineno)] = [
                '__all__ = ["MARKER_POLICY", "MARKER_SCHEMA_VERSION", "POLICY"]\n'
            ]
            break
    text = "".join(lines)
    forbidden = (
        "install_global_polish",
        "guard.run_pre_model_guard =",
        "retry._scope_epochs =",
        "sio.MutableAudioSpec =",
        "runtime._segments_by_id =",
        "_INSTALLED",
    )
    bad = [token for token in forbidden if token in text]
    if bad:
        raise RuntimeError(f"global polish mutation survived helper cleanup: {bad}")
    ast.parse(text, filename=str(POLISH))
    POLISH.write_text(text, encoding="utf-8")


def own_guard() -> None:
    text = GUARD.read_text(encoding="utf-8")
    if "direct_surgical_polish_v2 as polish" not in text:
        anchor = "from typing import Any\n"
        if anchor not in text:
            anchor = "from typing import Any,"
        if anchor == "from typing import Any\n":
            text = text.replace(
                anchor,
                anchor + "\nfrom tools.voxcpm2 import direct_surgical_polish_v2 as polish\n",
                1,
            )
        else:
            # Stable fallback: insert after future import block.
            insert = "from tools.voxcpm2 import direct_surgical_polish_v2 as polish\n"
            pos = text.find("\n\n", text.find("from __future__ import annotations"))
            text = text[:pos+2] + insert + text[pos+2:]
    text = rename_function(text, GUARD, "run_pre_model_guard", "_polish_base_run_pre_model_guard")
    text = rename_function(text, GUARD, "load_signature_context", "_polish_base_load_signature_context")
    text = rename_function(text, GUARD, "persist_timing_block", "_polish_base_persist_timing_block")
    # Replace the current v2 loader with the v3 source-owned wrapper from polish.
    text = replace_function(
        text,
        GUARD,
        "load_matching_timing_block",
        GUARD_WRAPPERS.split("def load_matching_timing_block", 1)[1].join(["def load_matching_timing_block", ""]),
    )
    # The split trick above is awkward for preserving the full body; replace it below
    # with the exact function extracted from the constant.
    loader_source = "def load_matching_timing_block" + GUARD_WRAPPERS.split("def load_matching_timing_block", 1)[1]
    # replace_function already inserted a malformed placeholder if reached; reparse from
    # the pre-loader renamed text instead by reconstructing once more from source.
    # This branch-only migration keeps a clean deterministic transformation.
    original = GUARD.read_text(encoding="utf-8")
    if "direct_surgical_polish_v2 as polish" not in original:
        insert = "from tools.voxcpm2 import direct_surgical_polish_v2 as polish\n"
        pos = original.find("\n\n", original.find("from __future__ import annotations"))
        original = original[:pos+2] + insert + original[pos+2:]
    original = rename_function(original, GUARD, "run_pre_model_guard", "_polish_base_run_pre_model_guard")
    original = rename_function(original, GUARD, "load_signature_context", "_polish_base_load_signature_context")
    original = rename_function(original, GUARD, "persist_timing_block", "_polish_base_persist_timing_block")
    original = replace_function(original, GUARD, "load_matching_timing_block", loader_source)
    all_index = original.rfind("\n__all__")
    if all_index < 0:
        raise RuntimeError("direct_timing_guard __all__ not found")
    prelude = GUARD_WRAPPERS.split("def load_matching_timing_block", 1)[0].rstrip()
    original = original[:all_index] + "\n\n" + prelude + "\n" + original[all_index:]
    ast.parse(original, filename=str(GUARD))
    GUARD.write_text(original, encoding="utf-8")


def own_retry() -> None:
    text = RETRY.read_text(encoding="utf-8")
    if "direct_surgical_polish_v2 as polish" not in text:
        insert = "from tools.voxcpm2 import direct_surgical_polish_v2 as polish\n"
        pos = text.find("\n\n", text.find("from __future__ import annotations"))
        text = text[:pos+2] + insert + text[pos+2:]
    text = rename_function(
        text,
        RETRY,
        "invalidate_segment_for_retry",
        "_polish_base_invalidate_segment_for_retry",
        occurrence=1,
    )
    text = replace_function(
        text,
        RETRY,
        "_scope_epochs",
        "def _scope_epochs(payload: Mapping[str, Any]) -> dict[str, int]:\n    return polish._scope_epochs(payload)\n",
    )
    all_index = text.rfind("\n__all__")
    if all_index < 0:
        raise RuntimeError("direct_retry_epoch __all__ not found")
    invalidate_source = RETRY_WRAPPERS.split("def invalidate_segment_for_retry", 1)[1]
    text = text[:all_index] + "\n\ndef invalidate_segment_for_retry" + invalidate_source + "\n" + text[all_index:]
    ast.parse(text, filename=str(RETRY))
    RETRY.write_text(text, encoding="utf-8")


def own_io() -> None:
    text = SIO.read_text(encoding="utf-8")
    if "direct_surgical_polish_v2 as polish" not in text:
        insert = "from tools.voxcpm2 import direct_surgical_polish_v2 as polish\n"
        pos = text.find("\n\n", text.find("from __future__ import annotations"))
        text = text[:pos+2] + insert + text[pos+2:]
    binding = '''\n\n# Source-owned strengthened IO contract. The implementation is shared with the\n# pure polish policy module; no imported module is mutated.\nPOLICY = polish.POLICY\nMutableAudioSpec = polish._AudioSpec\nLazySession = polish._LazySession\ncached_reference = polish._cached_reference\nenrich_reference_report = polish._enrich_reference_report\n'''
    all_index = text.rfind("\n__all__")
    if all_index < 0:
        raise RuntimeError("direct_surgical_io __all__ not found")
    text = text[:all_index] + binding + text[all_index:]
    ast.parse(text, filename=str(SIO))
    SIO.write_text(text, encoding="utf-8")


def own_runtime() -> None:
    text = RUNTIME.read_text(encoding="utf-8")
    if "direct_surgical_polish_v2 as polish" not in text:
        text = text.replace(
            "from tools.voxcpm2 import direct_surgical_io\n",
            "from tools.voxcpm2 import direct_surgical_io\nfrom tools.voxcpm2 import direct_surgical_polish_v2 as polish\n",
            1,
        )
    text = text.replace('POLICY = "voxcpm2-surgical-runtime-v1"', 'POLICY = "voxcpm2-surgical-runtime-v2"', 1)
    all_index = text.rfind("\n__all__")
    if all_index < 0:
        raise RuntimeError("direct_surgical_runtime __all__ not found")
    binding = '''\n\n_segments_by_id = polish._segments_by_id\n_RUNTIME_SCOPE_FILES = tuple(\n    dict.fromkeys((*_RUNTIME_SCOPE_FILES, *polish._EXTRA_SCOPE))\n)\n'''
    text = text[:all_index] + binding + text[all_index:]
    ast.parse(text, filename=str(RUNTIME))
    RUNTIME.write_text(text, encoding="utf-8")


def remove_consumers() -> None:
    for path in (CLI, GENERIC):
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "from tools.voxcpm2.direct_surgical_polish_v2 import install_global_polish\n",
            "",
        )
        text = text.replace("install_global_polish()\n", "")
        if "install_global_polish" in text:
            raise RuntimeError(f"{path}: global polish installer survived")
        ast.parse(text, filename=str(path))
        path.write_text(text, encoding="utf-8")


def main() -> int:
    # Consumers are rewritten after owner modules so the commit is atomic.
    clean_polish()
    own_guard()
    own_retry()
    own_io()
    own_runtime()
    remove_consumers()

    blockers: list[str] = []
    for path in ROOT.rglob("*.py"):
        if path.resolve() == Path(__file__).resolve() or "tests" in path.parts or ".git" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("tools/") and any(tag in rel for tag in (
            "source_own_", "rewrite_", "runtime_", "refactor_", "flatten_", "remove_", "prune_"
        )):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "install_global_polish" in text:
            blockers.append(rel)
    if blockers:
        raise RuntimeError("install_global_polish still referenced: " + ", ".join(sorted(set(blockers))))
    print("direct global polish is source-owned by timing/retry/io/runtime owners")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
