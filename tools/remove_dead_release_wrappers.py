#!/usr/bin/env python3
"""Delete dead release wrappers after proving no production import remains."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tools" / "voxcpm2" / "clean_runtime_contract.py"
TARGETS = {
    "master_monolithic_mix": ROOT / "tools" / "voxcpm2" / "master_monolithic_mix.py",
    "preflight_json_protocol": ROOT / "tools" / "voxcpm2" / "preflight_json_protocol.py",
}
MODULES = {f"tools.voxcpm2.{name}" for name in TARGETS}
SKIP_PARTS = {"tests", ".git", "__pycache__", ".pytest_cache", ".venv", "venv"}
SKIP_FILES = {
    Path(__file__).resolve(),
    CONTRACT.resolve(),
    (ROOT / "services" / "dub_release_health_v64.py").resolve(),
    (ROOT / "tools" / "rewrite_dub_release_health.py").resolve(),
    *[path.resolve() for path in TARGETS.values()],
}


def import_blockers() -> list[str]:
    blockers: list[str] = []
    for path in ROOT.rglob("*.py"):
        if path.resolve() in SKIP_FILES or any(part in SKIP_PARTS for part in path.relative_to(ROOT).parts):
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("tools/") and any(
            marker in rel for marker in (
                "runtime_", "refactor_", "source_own_", "flatten_", "prune_", "remove_", "rewrite_"
            )
        ):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=rel)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in MODULES:
                        blockers.append(f"{rel}:{node.lineno}:import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                if module in MODULES:
                    blockers.append(f"{rel}:{node.lineno}:from {module}")
                if module == "tools.voxcpm2":
                    for alias in node.names:
                        if alias.name in TARGETS:
                            blockers.append(f"{rel}:{node.lineno}:from tools.voxcpm2 import {alias.name}")
    return blockers


def remove_release_entries(text: str) -> str:
    tree = ast.parse(text, filename=str(CONTRACT))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "_RELEASE_MODULES" for target in node.targets):
            continue
        value = ast.literal_eval(node.value)
        if not isinstance(value, tuple):
            raise RuntimeError("_RELEASE_MODULES is not a tuple")
        retired = {f"tools/voxcpm2/{name}.py" for name in TARGETS}
        updated = tuple(item for item in value if item not in retired)
        missing = retired.intersection(value)
        if missing != retired:
            raise RuntimeError(f"release fingerprint entries already diverged: missing={sorted(retired - missing)}")
        lines[node.lineno - 1 : (node.end_lineno or node.lineno)] = [f"_RELEASE_MODULES = {updated!r}\n"]
        return "".join(lines)
    raise RuntimeError("_RELEASE_MODULES assignment not found")


def main() -> int:
    blockers = import_blockers()
    if blockers:
        raise RuntimeError("dead-wrapper proof invalidated:\n" + "\n".join(blockers))
    contract = remove_release_entries(CONTRACT.read_text(encoding="utf-8"))
    ast.parse(contract, filename=str(CONTRACT))
    CONTRACT.write_text(contract, encoding="utf-8")
    for name, path in TARGETS.items():
        if not path.is_file():
            raise RuntimeError(f"target already missing: {path}")
        path.unlink()
        print(f"deleted dead release wrapper: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
