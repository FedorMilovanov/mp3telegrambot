#!/usr/bin/env python3
"""AST-driven promotion of the clean direct route into generic_direct_runtime."""
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

REQUIRED_BASE = {
    "_load_request", "_target_duration", "_checkpoint_prefix", "load_checkpoint",
    "save_checkpoint", "record_checkpoint", "preserve_checkpoint",
    "_run_clean_voxcpm_and_master", "main",
}
REQUIRED_WRAPPER = {
    "_checkpoint_target", "_candidate_checksum", "_signature_valid_checkpoint_set",
    "load_checkpoint", "save_checkpoint", "record_checkpoint", "preserve_checkpoint",
}


def parsed(text: str, path: Path) -> ast.Module:
    return ast.parse(text, filename=str(path))


def imports_from(text: str, path: Path, *, skip_modules: set[str]) -> list[str]:
    result: list[str] = []
    for node in parsed(text, path).body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        if isinstance(node, ast.ImportFrom) and str(node.module or "") in skip_modules:
            continue
        if isinstance(node, ast.Import):
            names = {alias.name for alias in node.names}
            if names & skip_modules:
                continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            source = ast.get_source_segment(text, node)
            if source:
                result.append(source)
    return result


def top_functions(text: str, path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in parsed(text, path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            source = ast.get_source_segment(text, node)
            if source:
                out[node.name] = textwrap.dedent(source)
    return out


def top_classes(text: str, path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in parsed(text, path).body:
        if isinstance(node, ast.ClassDef):
            source = ast.get_source_segment(text, node)
            if source:
                out[node.name] = textwrap.dedent(source)
    return out


def transform_base(source: str, name: str) -> str:
    source = source.replace("production.", "")
    source = re.sub(r"\bPOLICY\b", "CLEAN_DIRECT_POLICY", source)
    source = re.sub(r"\b_STAGES\b", "_CLEAN_DIRECT_STAGES", source)
    if name == "main":
        # The canonical module owns these globals directly; no assignment through
        # an imported module is necessary or allowed.
        lines = []
        for line in source.splitlines():
            stripped = line.strip()
            if stripped in {
                "load_checkpoint = load_checkpoint",
                "save_checkpoint = save_checkpoint",
                "record_checkpoint = record_checkpoint",
                "preserve_checkpoint = preserve_checkpoint",
            }:
                continue
            lines.append(line)
        source = "\n".join(lines)
        source = source.replace("return main()", "return _production_main()")
    return source


def remove_function(text: str, path: Path, name: str) -> str:
    tree = parsed(text, path)
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            del lines[start : (node.end_lineno or node.lineno)]
            return "".join(lines)
    raise RuntimeError(f"{path}: missing function {name}")


def remove_export(text: str, path: Path, name: str) -> str:
    tree = parsed(text, path)
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if not isinstance(value, list):
                raise RuntimeError(f"{path}: non-literal __all__")
            value = [item for item in value if item != name]
            lines[node.lineno - 1 : (node.end_lineno or node.lineno)] = [f"__all__ = {value!r}\n"]
            return "".join(lines)
    raise RuntimeError(f"{path}: __all__ not found")


def rewrite_contract(text: str) -> str:
    tree = parsed(text, CONTRACT)
    lines = text.splitlines(keepends=True)
    touched = False
    retired = {
        "tools/voxcpm2/generic_clean_direct_runtime.py",
        "tools/voxcpm2/_generic_clean_direct_runtime_base.py",
    }
    canonical = "tools/voxcpm2/generic_direct_runtime.py"
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if not set(names) & {"_RENDER_MODULES", "_RELEASE_MODULES"}:
            continue
        value = ast.literal_eval(node.value)
        if not isinstance(value, tuple):
            continue
        updated = [item for item in value if item not in retired]
        if names[0] == "_RENDER_MODULES" and canonical not in updated:
            updated.append(canonical)
        lines[node.lineno - 1 : (node.end_lineno or node.lineno)] = [f"{names[0]} = {tuple(updated)!r}\n"]
        touched = True
    if not touched:
        raise RuntimeError("runtime fingerprint tuples not found")
    return "".join(lines)


def rewrite_health(text: str) -> str:
    text = re.sub(
        r'\s*"direct_runtime": voxcpm / "generic_clean_direct_runtime\.py",\n'
        r'\s*"direct_runtime_base": voxcpm / "_generic_clean_direct_runtime_base\.py",\n',
        '\n        "direct_runtime": voxcpm / "generic_direct_runtime.py",\n',
        text,
        count=1,
    )
    text = text.replace(
        '"render_direct": "tools.voxcpm2.generic_clean_direct_runtime"',
        '"render_direct": "tools.voxcpm2.generic_direct_runtime"',
    )
    text = text.replace('"direct_runtime_base",\n', "")
    if "generic_clean_direct_runtime" in text or "_generic_clean_direct_runtime_base" in text:
        raise RuntimeError("health still references retired clean direct route")
    return text


def main() -> int:
    if not all(path.is_file() for path in (OWNER, WRAPPER, BASE, UNIVERSAL, RECIPE, HEALTH, CONTRACT)):
        raise RuntimeError("generic direct migration inputs are incomplete")
    owner = OWNER.read_text(encoding="utf-8")
    base = BASE.read_text(encoding="utf-8-sig")
    wrapper = WRAPPER.read_text(encoding="utf-8")
    base_functions = top_functions(base, BASE)
    wrapper_functions = top_functions(wrapper, WRAPPER)
    wrapper_classes = top_classes(wrapper, WRAPPER)
    missing_base = REQUIRED_BASE - set(base_functions)
    missing_wrapper = REQUIRED_WRAPPER - set(wrapper_functions)
    if missing_base or missing_wrapper or "RetryAwareManifest" not in wrapper_classes:
        raise RuntimeError(
            f"direct clean contract diverged: base={sorted(missing_base)} "
            f"wrapper={sorted(missing_wrapper)} class={'RetryAwareManifest' in wrapper_classes}"
        )

    # Preserve every clean-base helper in source order so internal helper calls retain
    # the exact audited behavior. Imports are copied, but the old production self-module
    # import and generic-preflight installer are deliberately excluded.
    base_order = [
        node.name for node in parsed(base, BASE).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    imports = imports_from(
        base,
        BASE,
        skip_modules={"tools.voxcpm2.generic_direct_runtime"},
    )
    imports += imports_from(
        wrapper,
        WRAPPER,
        skip_modules={
            "tools.voxcpm2.direct_universal_runtime",
            "tools.voxcpm2._generic_clean_direct_runtime_base_exec",
        },
    )
    # Deterministic de-duplication while preserving import order.
    imports = list(dict.fromkeys(imports))

    owner += "\n\n# ---- Source-owned clean direct production contract ----\n"
    owner += "\n".join(imports) + "\n\n"
    owner += '''CLEAN_DIRECT_POLICY = "generic-clean-direct-v2"\nDIRECT_TARGET_POLICY = "direct-ready-srt-target-v1"\n_CLEAN_DIRECT_STAGES = ("srt", "segments", "source_audio", "reference", "voice", "master", "video")\n_production_main = main\n\n'''

    clean_main = None
    for name in base_order:
        transformed = transform_base(base_functions[name], name)
        if name == "main":
            clean_main = transformed
            continue
        owner += transformed.rstrip() + "\n\n"
    if clean_main is None:
        raise RuntimeError("clean direct main missing after extraction")

    owner += "_base_clean_checkpoint_prefix = _checkpoint_prefix\n\n"
    owner += wrapper_classes["RetryAwareManifest"].rstrip() + "\n\n"
    for name in (
        "_checkpoint_target", "_candidate_checksum", "_signature_valid_checkpoint_set",
        "load_checkpoint", "save_checkpoint", "record_checkpoint", "preserve_checkpoint",
    ):
        source = wrapper_functions[name].replace(
            "_legacy_checkpoint_prefix", "_base_clean_checkpoint_prefix"
        )
        owner += source.rstrip() + "\n\n"

    owner += '''_clean_direct_run_voxcpm_and_master = _run_clean_voxcpm_and_master\n\n\ndef _run_clean_voxcpm_and_master(\n    root: Path,\n    request: dict[str, Any],\n    source: Path,\n    ready_srt: Path,\n) -> None:\n    work_dir = root / "work"\n    segments_json = work_dir / "segments.json"\n    direct_timing_guard.run_pre_model_guard(\n        direct_universal_runtime._read_segments(segments_json),\n        work_dir=work_dir,\n        max_tempo=float(MAX_TEMPO),\n        signature_context=direct_timing_guard.load_signature_context(work_dir),\n    )\n    log("generic direct preflight passed before VoxCPM synthesis")\n    return _clean_direct_run_voxcpm_and_master(root, request, source, ready_srt)\n\n'''
    if "from tools.voxcpm2 import direct_timing_guard" not in owner:
        owner += "from tools.voxcpm2 import direct_timing_guard\n"
    if "from tools.voxcpm2 import direct_universal_runtime" not in owner:
        owner += "from tools.voxcpm2 import direct_universal_runtime\n"
    owner += "\n" + clean_main.rstrip() + "\n"

    forbidden = (
        "production.load_checkpoint =", "production.save_checkpoint =",
        "production.record_checkpoint =", "production.preserve_checkpoint =",
        "install_generic_preflight", "exec(compile(", "_generic_clean_direct_runtime_base",
        "return main()",
    )
    bad = [token for token in forbidden if token in owner]
    if bad:
        raise RuntimeError(f"canonical generic direct owner retained {bad}")
    parsed(owner, OWNER)
    OWNER.write_text(owner, encoding="utf-8")

    recipe = json.loads(RECIPE.read_text(encoding="utf-8"))
    action = recipe["actions"]["render_direct"]
    legacy = "tools.voxcpm2.generic_clean_direct_runtime"
    if action.get("python_module") != legacy:
        raise RuntimeError(f"unexpected render_direct route: {action.get('python_module')!r}")
    action["python_module"] = "tools.voxcpm2.generic_direct_runtime"
    RECIPE.write_text(json.dumps(recipe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    health = rewrite_health(HEALTH.read_text(encoding="utf-8"))
    parsed(health, HEALTH)
    HEALTH.write_text(health, encoding="utf-8")
    contract = rewrite_contract(CONTRACT.read_text(encoding="utf-8"))
    parsed(contract, CONTRACT)
    CONTRACT.write_text(contract, encoding="utf-8")

    universal = UNIVERSAL.read_text(encoding="utf-8")
    universal = remove_function(universal, UNIVERSAL, "install_generic_preflight")
    universal = remove_export(universal, UNIVERSAL, "install_generic_preflight")
    if "install_generic_preflight" in universal:
        raise RuntimeError("generic preflight installer survived")
    parsed(universal, UNIVERSAL)
    UNIVERSAL.write_text(universal, encoding="utf-8")

    WRAPPER.unlink()
    BASE.unlink()

    retired = (
        "tools.voxcpm2.generic_clean_direct_runtime",
        "generic_clean_direct_runtime.py",
        "_generic_clean_direct_runtime_base.py",
        "install_generic_preflight",
    )
    blockers: list[str] = []
    for path in ROOT.rglob("*.py"):
        if path.resolve() == Path(__file__).resolve() or "tests" in path.parts or ".git" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("tools/") and "source_own_generic_direct_route" in rel:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(token in text for token in retired):
            blockers.append(rel)
    if blockers:
        raise RuntimeError("retired clean direct references remain: " + ", ".join(sorted(set(blockers))))
    print("generic direct clean route is source-owned by generic_direct_runtime")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
