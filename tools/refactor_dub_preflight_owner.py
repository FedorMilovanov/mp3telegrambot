#!/usr/bin/env python3
"""Flatten Dub preflight shadow package into one source-owned module."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "tools" / "voxcpm2" / "dub_job_preflight.py"
PACKAGE = ROOT / "tools" / "voxcpm2" / "dub_job_preflight" / "__init__.py"


def _base_modules(text: str) -> tuple[str, ...]:
    tree = ast.parse(text, filename=str(BASE))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "_MODULES" for target in node.targets):
            value = ast.literal_eval(node.value)
            if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
                raise RuntimeError("dub_job_preflight._MODULES is not a literal tuple[str, ...]")
            return value
    raise RuntimeError("dub_job_preflight._MODULES not found")


def _base_function(text: str, name: str) -> str:
    tree = ast.parse(text, filename=str(BASE))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            source = ast.get_source_segment(text, node)
            if source:
                return source
    raise RuntimeError(f"dub_job_preflight.{name} not found")


def main() -> int:
    if not BASE.is_file() or not PACKAGE.is_file():
        raise RuntimeError("dub_job_preflight collision is missing")

    base_text = BASE.read_text(encoding="utf-8")
    text = PACKAGE.read_text(encoding="utf-8")
    modules = _base_modules(base_text)
    read_json = _base_function(base_text, "_read_json")

    text = text.replace(
        '"""Strict compatibility facade for the Dub production preflight.\n\n'
        'The parallel agent\'s durable implementation remains in ``dub_job_preflight.py``.\n'
        'This package shadows it for normal imports and strengthens only the trust\n'
        'boundaries: canonical project/request identity, complete implementation/model/\n'
        'runtime-aware cache signatures, deterministic child imports, collision-safe\n'
        'report writes, and a worker heartbeat during potentially long checks.\n"""',
        '"""Source-owned fail-closed Dub production preflight.\n\n'
        'Canonical project/request identity, implementation/model/runtime-aware cache\n'
        'signatures, deterministic child imports, collision-safe reports and worker\n'
        'heartbeat are implemented directly here; no shadow package is involved.\n"""',
    )
    text = text.replace("import importlib.util\n", "")
    text = text.replace("import sys\n", "")
    if "import subprocess\n" not in text:
        text = text.replace("import shutil\n", "import shutil\nimport subprocess\n", 1)
    text = text.replace(
        "from services.dub_studio import DubStore\n",
        "from services.dub_studio import DubStore, repo_root, studio_root\n",
        1,
    )

    loader_start = text.index("_LEGACY_PATH =")
    policy_start = text.index("POLICY =", loader_start)
    text = text[:loader_start] + text[policy_start:]

    text = text.replace("_MODULES = tuple(_legacy._MODULES)", f"_MODULES = {modules!r}")
    text = text.replace("generic_project_runtime._legacy._PROJECT_RE", "generic_project_runtime._PROJECT_RE")
    text = text.replace("_legacy.studio_root()", "studio_root()")
    text = text.replace("_legacy.repo_root()", "repo_root()")
    text = text.replace("_legacy.subprocess.", "subprocess.")
    text = text.replace('                "tools/voxcpm2/dub_job_preflight/__init__.py",\n', "")

    read_anchor = "\n\ndef _sha256"
    if read_anchor not in text:
        raise RuntimeError("dub_job_preflight sha256 anchor missing")
    text = text.replace(read_anchor, f"\n\n{read_json}\n{read_anchor}", 1)

    old_final_qa = '''    final_qa = Path(loaded["tools.voxcpm2.final_media_qa"]).resolve()\n    if final_qa.name != "__init__.py" or final_qa.parent.name != "final_media_qa":\n        raise RuntimeError("Preflight: active final_media_qa compatibility package не загружен.")\n'''
    new_final_qa = '''    final_qa = Path(loaded["tools.voxcpm2.final_media_qa"]).resolve()\n    expected_final_qa = repo / "tools" / "voxcpm2" / "final_media_qa.py"\n    if _normalized_path(final_qa) != _normalized_path(expected_final_qa):\n        raise RuntimeError("Preflight: final_media_qa загружен не из canonical source owner.")\n'''
    if old_final_qa not in text:
        raise RuntimeError("legacy final_media_qa package assertion not found")
    text = text.replace(old_final_qa, new_final_qa, 1)

    patch_start = text.index("# Patch the parallel agent's module")
    text = text[:patch_start].rstrip() + '''\n\n\n__all__ = sorted({\n    "POLICY",\n    "PREFLIGHT_HEARTBEAT_SECONDS",\n    "REPORT_SCHEMA",\n    "_ACTIONS",\n    "_MODULES",\n    "_atomic_json",\n    "_cache_hit",\n    "_claimed_job_context",\n    "_implementation_identity",\n    "_preflight_heartbeat",\n    "_probe_imports",\n    "_project_root",\n    "_read_json",\n    "_read_report",\n    "_runtime_paths",\n    "_signature",\n    "os",\n    "repo_root",\n    "run",\n    "shutil",\n    "studio_root",\n    "subprocess",\n})\n'''

    forbidden = (
        "spec_from_file_location",
        "module_from_spec",
        "exec_module",
        "sys.modules",
        "_legacy",
        "dub_job_preflight/__init__.py",
        "compatibility package",
    )
    bad = [token for token in forbidden if token in text]
    if bad:
        raise RuntimeError(f"dub_job_preflight forbidden tokens survived: {bad}")
    parsed = ast.parse(text, filename=str(BASE))
    if any(isinstance(node, ast.Name) and node.id == "_legacy" for node in ast.walk(parsed)):
        raise RuntimeError("dub_job_preflight still references _legacy")

    BASE.write_text(text, encoding="utf-8")
    PACKAGE.unlink()
    print("flattened dub_job_preflight into canonical source owner")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
