#!/usr/bin/env python3
"""Remove obsolete quality installers and source-own audio repair execution."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRO = ROOT / "tools/voxcpm2/professional_audio_v45.py"
QA = ROOT / "tools/voxcpm2/professional_audio_qa_v45.py"
CHECKED = ROOT / "tools/voxcpm2/generic_direct_checked_runtime.py"
REPAIR = ROOT / "tools/voxcpm2/generic_audio_repair_runtime.py"
CONTRACT = ROOT / "tools/voxcpm2/clean_runtime_contract.py"
HEALTH = ROOT / "handlers/dub_health.py"


def remove_top_function(text: str, path: Path, name: str) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            while start > 0 and not lines[start - 1].strip(): start -= 1
            del lines[start:node.end_lineno]
            return "".join(lines)
    raise RuntimeError(f"{path}: missing top-level {name}")


def external_refs(token: str, *, exclude: set[Path]) -> list[str]:
    refs: list[str] = []
    for root_name in ("tools/voxcpm2", "services", "handlers", "core", "pipelines"):
        for path in (ROOT / root_name).rglob("*.py"):
            if path in exclude or "tests" in path.parts: continue
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(("tools/source_own_", "tools/refactor_", "tools/runtime_", "tools/prune_", "tools/classify_")): continue
            if token in path.read_text(encoding="utf-8", errors="replace"):
                refs.append(rel)
    return sorted(set(refs))


def main() -> int:
    for path in (PRO, QA, CHECKED, REPAIR, CONTRACT, HEALTH):
        if not path.is_file(): raise RuntimeError(f"missing input: {path}")

    pro = PRO.read_text(encoding="utf-8")
    pro = remove_top_function(pro, PRO, "install")
    pro = pro.replace("_INSTALLED = False\n", "")
    # These entrypoint names existed only for the removed installer.
    pro = pro.replace('RENDERER = "voxcpm2_quality_v45_renderer.py"\n', "")
    pro = pro.replace('MASTER = "master_quality_v45.py"\n', "")
    if "generic_direct_checked_runtime" in pro or "def install(" in pro or "verify_timeline_v4 =" in pro:
        raise RuntimeError("professional audio still contains installer surgery")
    ast.parse(pro, filename=str(PRO)); PRO.write_text(pro, encoding="utf-8")

    qa = QA.read_text(encoding="utf-8")
    qa = remove_top_function(qa, QA, "install")
    if "semantic_tts_guard_v4.verify_timeline_v4 =" in qa or "def install(" in qa:
        raise RuntimeError("professional QA still contains installer surgery")
    ast.parse(qa, filename=str(QA)); QA.write_text(qa, encoding="utf-8")

    contract = CONTRACT.read_text(encoding="utf-8")
    contract = contract.replace("'tools/voxcpm2/generic_direct_checked_runtime.py', ", "")
    contract = contract.replace(", 'tools/voxcpm2/generic_direct_checked_runtime.py'", "")
    contract = contract.replace("'tools/voxcpm2/generic_direct_checked_runtime.py'", "")
    ast.parse(contract, filename=str(CONTRACT)); CONTRACT.write_text(contract, encoding="utf-8")

    health = HEALTH.read_text(encoding="utf-8")
    health = health.replace('voxcpm / "generic_direct_checked_runtime.py"', 'voxcpm / "generic_direct_runtime.py"')
    ast.parse(health, filename=str(HEALTH)); HEALTH.write_text(health, encoding="utf-8")

    refs = external_refs("generic_direct_checked_runtime", exclude={CHECKED})
    if refs:
        raise RuntimeError("checked direct wrapper still referenced after owner cleanup: " + ", ".join(refs))
    CHECKED.unlink()

    repair = REPAIR.read_text(encoding="utf-8")
    old = '''    mode = str(request.get("translation_mode") or "")\n    if mode == "direct":\n        legacy_guard.sanitize_tts_text = lambda value: str(value or "").strip()\n    semantic_tts_guard_v4.install()\n\n'''
    if old not in repair:
        raise RuntimeError("audio repair semantic installer block changed")
    repair = repair.replace(old, "", 1)
    # Remove obsolete imports only if the names are now unused.
    if repair.count("legacy_guard") == 1:
        repair = repair.replace("from tools.voxcpm2 import semantic_tts_guard as legacy_guard\n", "")
    if repair.count("semantic_tts_guard_v4") == 1:
        repair = repair.replace("from tools.voxcpm2 import semantic_tts_guard_v4\n", "")
    if "semantic_tts_guard_v4.install" in repair or "legacy_guard.sanitize_tts_text =" in repair:
        raise RuntimeError("audio repair retained runtime semantic patch")
    if "backend.build_renderer_command(" not in repair:
        raise RuntimeError("audio repair lost canonical backend renderer path")
    ast.parse(repair, filename=str(REPAIR)); REPAIR.write_text(repair, encoding="utf-8")

    print("quality/repair runtime installers removed")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
