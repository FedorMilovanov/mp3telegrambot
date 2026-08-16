#!/usr/bin/env python3
"""Remove service installer functions proven to have zero production call sites."""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "services/conspect_audit_runtime.py": {"install_conspect_audit_runtime"},
    "services/conspect_quality_contract.py": {"install_conspect_quality_contract"},
    "services/gemini_max_quality.py": {"install_max_quality_runtime"},
    "services/livedub_audio_cache_recovery.py": {"install_livedub_audio_cache_recovery"},
    "services/livedub_audio_companion.py": {"install_livedub_audio_companion"},
    "services/livedub_audio_dedupe.py": {"install_livedub_audio_dedupe"},
    "services/livedub_audio_quality_guard.py": {"install_livedub_audio_quality_guard"},
    "services/livedub_long_qa.py": {"install_livedub_long_qa"},
    "services/livedub_output_policy.py": {"install_livedub_output_policy"},
    "services/livedub_publication.py": {"_install_source_context", "install_livedub_publication"},
    "services/livedub_qa_hardening.py": {"install_qa_hardening"},
    "services/livedub_qa_trust.py": {"install_livedub_qa_trust"},
    "services/livedub_quality_runtime.py": {"install_livedub_quality_runtime"},
    "services/livedub_ru_provenance.py": {"install_livedub_ru_provenance"},
    "services/polling_reliability_runtime.py": {"install_polling_reliability_runtime"},
    "services/study_synthesis_runtime.py": {"install_teacherly_study_runtime"},
}


def remove_functions(rel: str, names: set[str]) -> None:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=rel)
    spans = []
    found = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            found.add(node.name)
            spans.append((node.lineno, node.end_lineno or node.lineno))
    missing = names - found
    if missing:
        raise RuntimeError(f"{rel}: missing installer definitions {sorted(missing)}")
    lines = text.splitlines(keepends=True)
    for start, end in sorted(spans, reverse=True):
        lo = start - 1
        while lo > 0 and not lines[lo - 1].strip():
            lo -= 1
        del lines[lo:end]
    updated = "".join(lines)
    for name in names:
        updated = re.sub(rf'^\s*["\']{re.escape(name)}["\'],?\s*\n', '', updated, flags=re.MULTILINE)
    path.write_text(updated, encoding="utf-8")
    print(f"pruned {rel}: {sorted(names)}")


def production_call_blockers(names: set[str]) -> list[str]:
    blockers=[]
    skip={"tests", ".git", ".venv", "venv", "__pycache__", ".pytest_cache"}
    for path in ROOT.rglob("*.py"):
        rel=path.relative_to(ROOT).as_posix()
        if any(p in skip for p in path.relative_to(ROOT).parts) or rel.startswith("tools/"):
            continue
        text=path.read_text(encoding="utf-8", errors="replace")
        try: tree=ast.parse(text)
        except SyntaxError: continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn=node.func
                leaf = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
                if leaf in names:
                    blockers.append(f"{rel}:{node.lineno}:{leaf}")
    return blockers


def main() -> int:
    all_names=set().union(*TARGETS.values())
    public_roots={name for name in all_names if name.startswith("install_")}
    blockers=production_call_blockers(public_roots)
    if blockers:
        raise RuntimeError("zero-call proof invalidated:\n" + "\n".join(blockers))
    for rel,names in TARGETS.items():
        remove_functions(rel,names)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
