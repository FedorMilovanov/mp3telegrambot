#!/usr/bin/env python3
"""Promote the clean direct Dub route into generic_direct_runtime source ownership."""
from __future__ import annotations

import ast
import json
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER = ROOT / "tools" / "voxcpm2" / "generic_direct_runtime.py"
WRAPPER = ROOT / "tools" / "voxcpm2" / "generic_clean_direct_runtime.py"
BASE = ROOT / "tools" / "voxcpm2" / "_generic_clean_direct_runtime_base.py"
UNIVERSAL = ROOT / "tools" / "voxcpm2" / "direct_universal_runtime.py"
RECIPE = ROOT / "tools" / "voxcpm2" / "recipes" / "generic_short_v1.json"
HEALTH = ROOT / "handlers" / "dub_health.py"
CONTRACT = ROOT / "tools" / "voxcpm2" / "clean_runtime_contract.py"

BASE_FUNCTIONS = (
    "_project_request_path",
    "_read_json_value",
    "_write_json_value",
    "_load_request",
    "_target_duration",
    "_checkpoint_prefix",
    "load_checkpoint",
    "save_checkpoint",
    "record_checkpoint",
    "preserve_checkpoint",
    "_highest_completed_stage",
    "_is_direct_project",
    "_ready_translation_file",
    "_validate_ready_translation",
    "_source_url",
    "_segments_paths",
    "_run_clean_voxcpm_and_master",
    "_final_media_report",
    "main",
)
WRAPPER_FUNCTIONS = (
    "_checkpoint_target",
    "_candidate_checksum",
    "_signature_valid_checkpoint_set",
    "load_checkpoint",
    "save_checkpoint",
    "record_checkpoint",
    "preserve_checkpoint",
)
WRAPPER_CLASSES = ("RetryAwareManifest",)


def top_source(text: str, path: Path, name: str, kinds: tuple[type, ...]) -> str:
    tree = ast.parse(text, filename=str(path))
    for node in tree.body:
        if isinstance(node, kinds) and getattr(node, "name", None) == name:
            source = ast.get_source_segment(text, node)
            if source:
                return textwrap.dedent(source)
    raise RuntimeError(f"{path}: missing top-level {name}")


def clean_base_function(source: str, name: str) -> str:
    source = source.replace("production.", "")
    source = re.sub(r"\bPOLICY\b", "CLEAN_DIRECT_POLICY", source)
    source = re.sub(r"\b_STAGES\b", "_CLEAN_DIRECT_STAGES", source)
    if name == "main":
        # Source-owned late definitions already replace the checkpoint globals used
        # by _production_main; the old imported-module assignments are unnecessary.
        source = re.sub(
            r"\n\s*load_checkpoint\s*=\s*load_checkpoint\n"
            r"\s*save_checkpoint\s*=\s*save_checkpoint\n"
            r"\s*record_checkpoint\s*=\s*record_checkpoint\n"
            r"\s*preserve_checkpoint\s*=\s*preserve_checkpoint\n",
            "\n",
            source,
        )
        source = source.replace("return main()", "return _production_main()")
    return source


def wrapper_source(text: str) -> str:
    pieces: list[str] = []
    for name in WRAPPER_CLASSES:
        pieces.append(top_source(text, WRAPPER, name, (ast.ClassDef,)))
    for name in WRAPPER_FUNCTIONS:
        source = top_source(text, WRAPPER, name, (ast.FunctionDef, ast.AsyncFunctionDef))
        source = source.replace("_legacy_checkpoint_prefix", "_base_clean_checkpoint_prefix")
        pieces.append(source)
    block = "\n\n".join(pieces)
    forbidden = ("production.", "_legacy_checkpoint_prefix", "def main(")
    bad = [token for token in forbidden if token in block]
    if bad:
        raise RuntimeError(f"retry-aware direct wrapper transfer retained {bad}")
    return block


def preflight_wrapper() -> str:
    return r'''
_clean_direct_run_voxcpm_and_master = _run_clean_voxcpm_and_master


def _run_clean_voxcpm_and_master(
    root: Path,
    request: dict[str, Any],
    source: Path,
    ready_srt: Path,
) -> None:
    work_dir = root / "work"
    output_dir = root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    clean_audio = work_dir / "reference_clean.wav"
    segments_json = work_dir / "segments.json"
    direct_timing_guard.run_pre_model_guard(
        direct_universal_runtime._read_segments(segments_json),
        work_dir=work_dir,
        max_tempo=float(MAX_TEMPO),
        signature_context=direct_timing_guard.load_signature_context(work_dir),
    )
    log("generic direct preflight passed before VoxCPM synthesis")
    return _clean_direct_run_voxcpm_and_master(root, request, source, ready_srt)
'''.strip()


def rewrite_render_modules(text: str) -> str:
    tree = ast.parse(text, filename=str(CONTRACT))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "_RENDER_MODULES" for target in node.targets):
            continue
        value = list(ast.literal_eval(node.value))
        retired = {
            "tools/voxcpm2/generic_clean_direct_runtime.py",
            "tools/voxcpm2/_generic_clean_direct_runtime_base.py",
        }
        value = [item for item in value if item not in retired]
        canonical = "tools/voxcpm2/generic_direct_runtime.py"
        if canonical not in value:
            value.append(canonical)
        lines[node.lineno - 1 : (node.end_lineno or node.lineno)] = [f"_RENDER_MODULES = {tuple(value)!r}\n"]
        return "".join(lines)
    raise RuntimeError("clean runtime contract _RENDER_MODULES not found")


def remove_top_function(text: str, path: Path, name: str) -> str:
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


def remove_export(text: str, path: Path, name: str) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if not isinstance(value, list):
                raise RuntimeError(f"{path}: __all__ must be literal list")
            value = [item for item in value if item != name]
            lines[node.lineno - 1 : (node.end_lineno or node.lineno)] = [f"__all__ = {value!r}\n"]
            return "".join(lines)
    raise RuntimeError(f"{path}: __all__ not found")


def main() -> int:
    owner = OWNER.read_text(encoding="utf-8")
    base = BASE.read_text(encoding="utf-8-sig")
    wrapper = WRAPPER.read_text(encoding="utf-8")

    if "CLEAN_DIRECT_POLICY" in owner:
        raise RuntimeError("generic direct route already migrated")
    owner += '''\n\n# ---- Source-owned clean direct production contract ----\nfrom tools.voxcpm2 import clean_production_core as clean\nfrom tools.voxcpm2 import clean_source_download\nfrom tools.voxcpm2 import continuous_reference_policy\nfrom tools.voxcpm2 import controlled_reference_gate\nfrom tools.voxcpm2 import expressive_continuity\nfrom tools.voxcpm2 import direct_timing_guard\nfrom tools.voxcpm2 import direct_universal_runtime\n\nCLEAN_DIRECT_POLICY = "generic-clean-direct-v2"\nDIRECT_TARGET_POLICY = "direct-ready-srt-target-v1"\n_CLEAN_DIRECT_STAGES = ("srt", "segments", "source_audio", "reference", "voice", "master", "video")\n_production_main = main\n'''

    clean_functions: list[str] = []
    for name in BASE_FUNCTIONS:
        source = top_source(base, BASE, name, (ast.FunctionDef, ast.AsyncFunctionDef))
        clean_functions.append(clean_base_function(source, name))
    # Keep the base prefix as an explicit alias before retry-aware checkpoint
    # functions override the public checkpoint API.
    clean_block = "\n\n".join(clean_functions)
    main_pos = clean_block.rfind("\ndef main(")
    if main_pos < 0:
        raise RuntimeError("clean direct main extraction failed")
    helper_part = clean_block[:main_pos]
    main_part = clean_block[main_pos + 1 :]
    owner += "\n\n" + helper_part + "\n\n_base_clean_checkpoint_prefix = _checkpoint_prefix\n\n"
    owner += wrapper_source(wrapper) + "\n\n"
    owner += preflight_wrapper() + "\n\n"
    owner += main_part + "\n"

    forbidden_owner = (
        "production.load_checkpoint =",
        "production.save_checkpoint =",
        "production.record_checkpoint =",
        "production.preserve_checkpoint =",
        "install_generic_preflight",
        "exec(compile(",
        "_generic_clean_direct_runtime_base",
    )
    bad = [token for token in forbidden_owner if token in owner]
    if bad:
        raise RuntimeError(f"generic direct source ownership retained {bad}")
    ast.parse(owner, filename=str(OWNER))
    OWNER.write_text(owner, encoding="utf-8")

    recipe = json.loads(RECIPE.read_text(encoding="utf-8"))
    direct = recipe["actions"]["render_direct"]
    if direct.get("python_module") != "tools.voxcpm2.generic_clean_direct_runtime":
        raise RuntimeError("recipe render_direct no longer points at expected legacy route")
    direct["python_module"] = "tools.voxcpm2.generic_direct_runtime"
    RECIPE.write_text(json.dumps(recipe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    health = HEALTH.read_text(encoding="utf-8")
    health = health.replace(
        '"direct_runtime": voxcpm / "generic_clean_direct_runtime.py",\n        "direct_runtime_base": voxcpm / "_generic_clean_direct_runtime_base.py",\n',
        '"direct_runtime": voxcpm / "generic_direct_runtime.py",\n',
        1,
    )
    health = health.replace('"render_direct": "tools.voxcpm2.generic_clean_direct_runtime",', '"render_direct": "tools.voxcpm2.generic_direct_runtime",', 1)
    health = health.replace('"direct_runtime_base",\n', "")
    HEALTH.write_text(health, encoding="utf-8")

    CONTRACT.write_text(rewrite_render_modules(CONTRACT.read_text(encoding="utf-8")), encoding="utf-8")

    universal = UNIVERSAL.read_text(encoding="utf-8")
    universal = remove_top_function(universal, UNIVERSAL, "install_generic_preflight")
    universal = remove_export(universal, UNIVERSAL, "install_generic_preflight")
    if "install_generic_preflight" in universal:
        raise RuntimeError("generic preflight installer survived direct route migration")
    ast.parse(universal, filename=str(UNIVERSAL))
    UNIVERSAL.write_text(universal, encoding="utf-8")

    WRAPPER.unlink()
    BASE.unlink()

    blockers: list[str] = []
    retired_tokens = (
        "tools.voxcpm2.generic_clean_direct_runtime",
        "generic_clean_direct_runtime.py",
        "_generic_clean_direct_runtime_base.py",
        "install_generic_preflight",
    )
    for path in ROOT.rglob("*.py"):
        if path.resolve() == Path(__file__).resolve() or "tests" in path.parts or ".git" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("tools/") and any(tag in rel for tag in (
            "source_own_", "rewrite_", "runtime_", "refactor_", "flatten_", "remove_", "prune_"
        )):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(token in text for token in retired_tokens):
            blockers.append(rel)
    if blockers:
        raise RuntimeError("retired clean direct route still referenced: " + ", ".join(sorted(set(blockers))))

    for path in (HEALTH, CONTRACT):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    print("generic direct clean route promoted into canonical generic_direct_runtime")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
