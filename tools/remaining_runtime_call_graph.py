#!/usr/bin/env python3
"""Branch-only production call graph for remaining runtime-surgery entrypoints."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "tests"}
SKIP_TOOL_PREFIXES = (
    "tools/refactor_", "tools/flatten_", "tools/source_own_", "tools/runtime_",
    "tools/remaining_runtime_call_graph.py", "tools/dead_runtime_cleanup.py",
    "tools/pure_policy_cleanup.py", "tools/prune_zero_call_installers.py",
    "tools/voxcpm_collision_audit.py", "tools/zero_runtime_marathon.py",
)
TARGETS = {
    "install_stale_card_fallback",
    "_wrap_reply_audio", "_patch_companion_success_hooks", "_wrap_send_video", "_wrap_send_message",
    "_patch_pipeline_title", "_wrap_send_audio", "harden_livedub_audio_dedupe",
    "_reuse_and_suppress_legacy_info_card", "_install_source_context", "install_livedub_publication",
    "install_main_failure_recovery", "install_final_audit", "install_guard_contract",
    "install_global_polish", "install_surgical_runtime", "install_worker_progress",
    "install_runtime_fingerprint", "install_generic_preflight", "install_direct_runtime",
    "install_gemini_quality", "install_direct_quality", "_install_voxcpm_patch",
    "install_repair_diagnostics", "_install_clean_runtime_adapters", "install_runtime_adapters",
    "_install_fail_closed_timeline", "_install_semantic_patch", "install_preflight_json",
    "install",
}


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def main() -> int:
    rows: list[tuple[str, int, str]] = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        parts = path.relative_to(ROOT).parts
        if any(part in SKIP_PARTS for part in parts):
            continue
        if any(rel.startswith(prefix) for prefix in SKIP_TOOL_PREFIXES):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = dotted(node.func)
            leaf = name.rsplit(".", 1)[-1]
            if leaf in TARGETS:
                rows.append((rel, node.lineno, name))
    for rel, line, name in sorted(rows):
        print(f"{rel}:{line}: {name}")
    print(f"ROOT_CALLS={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
