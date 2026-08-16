#!/usr/bin/env python3
"""Branch-only reference classifier for remaining runtime-surgery modules."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    "tools/voxcpm2/examples/john_piper_z20py4yqhyq/voxcpm2_cpu_semantic_wrapper.py",
    "tools/voxcpm2/generic_audio_repair_runtime.py",
    "tools/voxcpm2/generic_audio_repair_runtime_bootstrap.py",
    "tools/voxcpm2/generic_audio_repair_runtime_v45.py",
    "tools/voxcpm2/generic_direct_checked_runtime.py",
    "tools/voxcpm2/generic_direct_checked_runtime_v45.py",
    "tools/voxcpm2/generic_gemini_runtime_v45.py",
    "tools/voxcpm2/generic_project_runtime.py",
    "tools/voxcpm2/generic_short_runtime.py",
    "tools/voxcpm2/generic_gemini_runtime.py",
    "tools/voxcpm2/generic_clean_gemini_runtime.py",
    "tools/voxcpm2/generic_clean_custom_runtime.py",
    "tools/voxcpm2/monolithic_runtime_install.py",
    "tools/voxcpm2/semantic_tts_guard_v47.py",
    "tools/voxcpm2/voxcpm2_semantic_rescue_v47.py",
)
SUFFIXES = {".py", ".json", ".ps1", ".yml", ".yaml", ".toml"}
SKIP_PARTS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "tests"}
SKIP_PREFIXES = (
    "tools/refactor_", "tools/flatten_", "tools/source_own_", "tools/runtime_",
    "tools/classify_remaining_runtime_roots.py", "tools/remaining_runtime_call_graph.py",
    "tools/zero_runtime_marathon.py", "tools/prune_", "tools/dead_", "tools/pure_",
)


def tokens_for(rel: str) -> tuple[str, ...]:
    stem = Path(rel).stem
    module = rel[:-3].replace("/", ".") if rel.endswith(".py") else rel
    return (stem, module, Path(rel).name)


def main() -> int:
    candidates = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SUFFIXES:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if any(part in SKIP_PARTS for part in path.relative_to(ROOT).parts):
            continue
        if any(rel.startswith(prefix) for prefix in SKIP_PREFIXES):
            continue
        candidates.append((rel, path))
    for target in TARGETS:
        print(f"===== {target} =====")
        tokens = tokens_for(target)
        refs: list[str] = []
        for rel, path in candidates:
            if rel == target:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(token in text for token in tokens):
                kind = "health" if rel.startswith(("handlers/dub_health.py", "services/dub_release_health")) else "runtime"
                refs.append(f"{kind}: {rel}")
        if refs:
            for ref in sorted(set(refs)):
                print(ref)
        else:
            print("NO_EXTERNAL_REFS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
