#!/usr/bin/env python3
"""Move generic short runtime adapters into canonical production owners."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / "tools/voxcpm2/generic_short_production.py"
RUNTIME = ROOT / "tools/voxcpm2/generic_short_runtime.py"
PROJECT = ROOT / "tools/voxcpm2/generic_project_runtime.py"
SOURCE = ROOT / "tools/voxcpm2/clean_source_download.py"
CONTRACT = ROOT / "tools/voxcpm2/clean_runtime_contract.py"
HEALTH = ROOT / "handlers/dub_health.py"
SHORT_RECIPE = ROOT / "tools/voxcpm2/recipes/short_tnliocegylk.json"
WORKFLOWS = ROOT / ".github/workflows"

FUNCTIONS = (
    "standardize_russian_title",
    "_standardize_title_payload",
    "_ytdlp_base",
    "download_source",
    "download_captions",
    "_bounded_env_seconds",
    "_translation_timeouts",
    "_load_dotenv_for_manual_run",
    "_translation_keys",
    "_translation_client",
    "_close_client",
    "_prompt_label",
    "_generation_config",
    "gemini_json",
)
CONSTANTS = (
    "_TITLE_PROMPT_MARKER",
    "_JOHN_PIPER_RE",
    "_GEMINI_KEY_NAMES",
    "_DEFAULT_REQUEST_TIMEOUT_SECONDS",
    "_DEFAULT_PASS_TIMEOUT_SECONDS",
    "_MIN_REQUEST_TIMEOUT_SECONDS",
)


def nodes_by_name(text: str, path: Path) -> dict[str, ast.AST]:
    tree = ast.parse(text, filename=str(path))
    result: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Assign, ast.AnnAssign)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result[node.name] = node
            else:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        result[target.id] = node
    return result


def source_of(text: str, node: ast.AST) -> str:
    lines = text.splitlines()
    return "\n".join(lines[node.lineno - 1 : node.end_lineno])


def replace_function(text: str, path: Path, name: str, replacement: str) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            lines[node.lineno - 1 : node.end_lineno] = [replacement.rstrip() + "\n"]
            return "".join(lines)
    raise RuntimeError(f"{path}: missing function {name}")


def main() -> int:
    for path in (PROD, RUNTIME, PROJECT, SOURCE, CONTRACT, HEALTH, SHORT_RECIPE):
        if not path.is_file():
            raise RuntimeError(f"missing input: {path}")
    runtime = RUNTIME.read_text(encoding="utf-8")
    production = PROD.read_text(encoding="utf-8")
    nodes = nodes_by_name(runtime, RUNTIME)
    missing = [name for name in (*CONSTANTS, *FUNCTIONS) if name not in nodes]
    if missing:
        raise RuntimeError("runtime contract nodes missing: " + ", ".join(missing))

    # Canonical owner needs the hardened runtime dependencies itself.
    if "import time\n" not in production:
        production = production.replace("import tempfile\n", "import tempfile\nimport time\n", 1)
    import_anchor = "from services.speech_backends import DEFAULT_BACKEND_ID, get_backend\n"
    if import_anchor not in production:
        raise RuntimeError("production import anchor changed")
    owner_imports = (
        "from core.media_title_policy import canonical_media_title\n"
        "from core.text_utils import title_case_fragment\n"
    )
    production = production.replace(import_anchor, owner_imports + import_anchor, 1)

    constants_src = "\n\n".join(source_of(runtime, nodes[name]) for name in CONSTANTS)
    functions: dict[str, str] = {}
    for name in FUNCTIONS:
        value = source_of(runtime, nodes[name])
        value = value.replace("pipeline.run_checked", "run_checked")
        value = value.replace("pipeline.parse_vtt", "parse_vtt")
        value = value.replace("pipeline.log", "log")
        value = value.replace("pipeline._extract_json", "_extract_json")
        value = value.replace("list[pipeline.Cue]", "list[Cue]")
        functions[name] = value

    # Replace pre-existing generic implementations with hardened owner versions.
    for name in ("download_source", "download_captions", "gemini_json"):
        production = replace_function(production, PROD, name, functions.pop(name))

    insertion_anchor = "\ndef download_source(url: str, source: Path) -> dict[str, Any]:\n"
    if insertion_anchor not in production:
        raise RuntimeError("download_source insertion anchor changed")
    helpers = constants_src + "\n\n" + "\n\n".join(functions.values()) + "\n\n"
    production = production.replace(insertion_anchor, "\n" + helpers + insertion_anchor, 1)
    # There is no runtime installer in the canonical owner.
    for token in ("install_runtime_adapters", "semantic_tts_guard", "pipeline.", "generic_short_runtime"):
        if token in production:
            raise RuntimeError(f"canonical short production retained runtime token: {token}")
    for required in (
        "def _ytdlp_base(",
        "def standardize_russian_title(",
        "DUB_GEMINI_REQUEST_TIMEOUT_SEC",
        "types.HttpOptions(timeout=",
        "load_dotenv(override=False)",
        "пробую следующий",
    ):
        if required not in production:
            raise RuntimeError(f"canonical short production missing {required}")
    ast.parse(production, filename=str(PROD))
    PROD.write_text(production, encoding="utf-8")

    project = PROJECT.read_text(encoding="utf-8")
    project = project.replace("from tools.voxcpm2 import generic_short_runtime as hardened\n", "")
    project = project.replace("hardened.", "pipeline.")
    project = project.replace("    pipeline.install_runtime_adapters()\n", "")
    project = project.replace("    hardened.install_runtime_adapters()\n", "")
    # If the prefix replacement happened first, remove that exact line too.
    project = project.replace("    pipeline.install_runtime_adapters()\n", "")
    if "generic_short_runtime" in project or "install_runtime_adapters" in project or "hardened." in project:
        raise RuntimeError("project runtime retained adapter-layer references")
    ast.parse(project, filename=str(PROJECT))
    PROJECT.write_text(project, encoding="utf-8")

    source = SOURCE.read_text(encoding="utf-8")
    source = source.replace("from tools.voxcpm2 import generic_short_runtime as hardened\n", "")
    source = source.replace("hardened._ytdlp_base()", "pipeline._ytdlp_base()")
    if "generic_short_runtime" in source or "hardened." in source:
        raise RuntimeError("clean source download retained runtime adapter refs")
    ast.parse(source, filename=str(SOURCE))
    SOURCE.write_text(source, encoding="utf-8")

    recipe = json.loads(SHORT_RECIPE.read_text(encoding="utf-8"))
    changed_recipe = False
    for action in (recipe.get("actions") or {}).values():
        if isinstance(action, dict) and action.get("module") == "tools.voxcpm2.generic_short_runtime":
            action["module"] = "tools.voxcpm2.generic_short_production"
            changed_recipe = True
    if not changed_recipe:
        raise RuntimeError("short recipe did not reference generic_short_runtime")
    SHORT_RECIPE.write_text(json.dumps(recipe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    contract = CONTRACT.read_text(encoding="utf-8")
    contract = contract.replace("'tools/voxcpm2/generic_short_runtime.py', ", "")
    if "generic_short_runtime.py" in contract:
        raise RuntimeError("runtime fingerprint retained generic_short_runtime")
    ast.parse(contract, filename=str(CONTRACT))
    CONTRACT.write_text(contract, encoding="utf-8")

    health = HEALTH.read_text(encoding="utf-8")
    health = health.replace('"gemini_runtime": voxcpm / "generic_short_runtime.py"', '"gemini_runtime": voxcpm / "generic_short_production.py"')
    if "generic_short_runtime.py" in health:
        raise RuntimeError("dub health retained generic_short_runtime")
    ast.parse(health, filename=str(HEALTH))
    HEALTH.write_text(health, encoding="utf-8")

    # Canonical CI/workflows must not assert the retired adapter module.
    for path in WORKFLOWS.glob("*.yml"):
        if path.name in {"zero-runtime-marathon.yml", "classify-runtime-roots.yml", "prune-dead-voxcpm-runtime.yml"}:
            continue
        text = path.read_text(encoding="utf-8")
        if "generic_short_runtime" in text:
            text = text.replace("tools.voxcpm2.generic_short_runtime", "tools.voxcpm2.generic_short_production")
            text = text.replace("tools/voxcpm2/generic_short_runtime.py", "tools/voxcpm2/generic_short_production.py")
            path.write_text(text, encoding="utf-8")

    RUNTIME.unlink()

    blockers: list[str] = []
    for root_name in ("tools/voxcpm2", "services", "handlers", "core", "pipelines"):
        for path in (ROOT / root_name).rglob("*.py"):
            if "tests" in path.parts:
                continue
            if "generic_short_runtime" in path.read_text(encoding="utf-8", errors="replace"):
                blockers.append(path.relative_to(ROOT).as_posix())
    if blockers:
        raise RuntimeError("retired generic_short_runtime refs remain: " + ", ".join(sorted(set(blockers))))
    print("generic short/project foundation source-owned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
