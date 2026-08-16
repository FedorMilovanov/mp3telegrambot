#!/usr/bin/env python3
"""Delete the proven-dead VoxCPM v45/runtime installer cluster."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tools/voxcpm2/clean_runtime_contract.py"
DEAD = (
    "tools/voxcpm2/generic_audio_repair_runtime_v45.py",
    "tools/voxcpm2/generic_direct_checked_runtime_v45.py",
    "tools/voxcpm2/generic_gemini_runtime_v45.py",
    "tools/voxcpm2/generic_audio_repair_runtime_bootstrap.py",
    "tools/voxcpm2/semantic_tts_guard_v47.py",
    "tools/voxcpm2/voxcpm2_semantic_rescue_v47.py",
    "tools/voxcpm2/monolithic_runtime_install.py",
)
SKIP_PARTS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "tests"}
SKIP_PREFIXES = (
    "tools/refactor_", "tools/flatten_", "tools/source_own_", "tools/runtime_",
    "tools/prune_dead_voxcpm_runtime_cluster.py", "tools/classify_remaining_runtime_roots.py",
    "tools/remaining_runtime_call_graph.py", "tools/zero_runtime_marathon.py",
    ".github/workflows/prune-dead-voxcpm-runtime.yml",
)


def main() -> int:
    dead_paths = {ROOT / rel for rel in DEAD}
    missing = [rel for rel in DEAD if not (ROOT / rel).is_file()]
    if missing:
        raise RuntimeError("dead-cluster inputs missing: " + ", ".join(missing))

    stems = {Path(rel).stem for rel in DEAD}
    modules = {rel[:-3].replace("/", ".") for rel in DEAD}
    blockers: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path in dead_paths:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if any(part in SKIP_PARTS for part in path.relative_to(ROOT).parts):
            continue
        if any(rel.startswith(prefix) for prefix in SKIP_PREFIXES):
            continue
        if path.suffix.lower() not in {".py", ".json", ".ps1", ".yml", ".yaml", ".toml"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = sorted(token for token in stems | modules if token in text)
        if hits:
            if path.resolve() == CONTRACT.resolve():
                continue
            blockers.append(f"{rel}: {', '.join(hits)}")
    if blockers:
        raise RuntimeError("dead runtime cluster still externally referenced:\n" + "\n".join(blockers))

    contract = CONTRACT.read_text(encoding="utf-8")
    for rel in DEAD:
        contract = contract.replace(repr(rel) + ", ", "")
        contract = contract.replace(", " + repr(rel), "")
        contract = contract.replace(repr(rel), "")
    for rel in DEAD:
        if rel in contract:
            raise RuntimeError(f"fingerprint still references {rel}")
    ast.parse(contract, filename=str(CONTRACT))
    CONTRACT.write_text(contract, encoding="utf-8")

    for path in dead_paths:
        path.unlink()

    for rel in DEAD:
        if (ROOT / rel).exists():
            raise RuntimeError(f"failed to delete {rel}")
    print(f"deleted {len(DEAD)} proven-dead runtime files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
