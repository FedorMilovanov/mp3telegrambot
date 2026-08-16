#!/usr/bin/env python3
"""Delete proven-unreferenced legacy runtime patch modules. Temporary tool."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DELETE = (
    "services/cloud_media_fallback.py",
    "services/cut_mode_source_policy.py",
    "services/cut_replay_delivery_policy.py",
    "services/livedub_delivery_hardening.py",
    "services/livedub_dual_audio_policy.py",
    "services/livedub_info_guard.py",
    "services/livedub_info_presentation.py",
    "services/livedub_new_delivery_atomicity.py",
    "services/livedub_cached_delivery_atomicity.py",
    "services/livedub_deep_audit.py",
    "services/livedub_publication_error_diagnostics.py",
    "services/shorts_factory_portable_publication.py",
)
SKIP_PARTS = {"tests", ".git", ".venv", "venv", "__pycache__", ".pytest_cache"}
TEMP = {
    "tools/dead_runtime_cleanup.py",
    "tools/runtime_reference_audit.py",
    "tools/runtime_surgery_audit.py",
    "tools/zero_runtime_marathon.py",
    "tools/repair_title_runner.py",
}


def module_name(rel: str) -> str:
    return rel[:-3].replace("/", ".")


def main() -> int:
    deleting = {module_name(rel) for rel in DELETE}
    # Fail closed: no production source outside this deletion set may import/reference
    # the exact fully-qualified module names.
    blockers: list[str] = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in DELETE or rel in TEMP or any(p in SKIP_PARTS for p in path.relative_to(ROOT).parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for mod in deleting:
            if mod in text:
                blockers.append(f"{rel}: references {mod}")
    if blockers:
        raise RuntimeError("dead-runtime cleanup blocked:\n" + "\n".join(blockers))
    for rel in DELETE:
        path = ROOT / rel
        if not path.is_file():
            raise RuntimeError(f"expected dead runtime missing: {rel}")
        path.unlink()
        print(f"deleted {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
