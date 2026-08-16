#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path


def replace_top_level_function(path: Path, name: str, replacement: str) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            lines[start:node.end_lineno] = [replacement.rstrip() + "\n"]
            path.write_text("".join(lines), encoding="utf-8")
            return
    raise RuntimeError(f"missing top-level function {name} in {path}")


def add_direct_semantic_block_metadata(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    target_function = None
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "test_direct_segments_apply_420ms_delay_without_rewriting"
        ):
            target_function = node
            break
    if target_function is None:
        raise SystemExit("generic direct semantic-block test function missing")

    for node in target_function.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "groups" for target in node.targets):
            continue
        value = node.value
        if not (
            isinstance(value, ast.List)
            and len(value.elts) == 1
            and isinstance(value.elts[0], ast.Dict)
        ):
            continue
        item = value.elts[0]
        existing = {
            key.value
            for key in item.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        additions = (
            ("semantic_block_id", ast.Constant(value=1)),
            ("source_cue_count", ast.Constant(value=1)),
            ("semantic_block_duration", ast.Constant(value=3.0)),
            ("source_parts", ast.List(elts=[ast.Constant(value="Точный текст.")], ctx=ast.Load())),
        )
        for key, value_node in additions:
            if key not in existing:
                item.keys.append(ast.Constant(value=key))
                item.values.append(value_node)
        ast.fix_missing_locations(tree)
        path.write_text(ast.unparse(tree).rstrip() + "\n", encoding="utf-8")
        return
    raise SystemExit("generic direct groups assignment missing")


# Fingerprint the contract implementation itself so changes to validation rules
# invalidate stale render baselines just like renderer changes do.
contract = Path("tools/voxcpm2/clean_runtime_contract.py")
text = contract.read_text(encoding="utf-8")
self_path = "'tools/voxcpm2/clean_runtime_contract.py'"
if self_path not in text:
    anchor = "_RENDER_MODULES = ("
    if anchor not in text:
        raise SystemExit("clean_runtime_contract _RENDER_MODULES anchor missing")
    text = text.replace(anchor, anchor + self_path + ", ", 1)
contract.write_text(text, encoding="utf-8")


# Replace the old release-health string archaeology with checks against the
# actual canonical owners and recipe wiring. This remains fail-closed but does
# not require deleted compatibility wrappers to exist.
health = Path("handlers/dub_health.py")
replace_top_level_function(
    health,
    "_quality_contract",
    r'''def _quality_contract(repo: Path) -> tuple[bool, str]:
    root = Path(repo)
    voxcpm = root / "tools" / "voxcpm2"
    required = {
        "runtime_contract": voxcpm / "clean_runtime_contract.py",
        "core": voxcpm / "clean_production_core.py",
        "source_download": voxcpm / "clean_source_download.py",
        "request_settings": voxcpm / "clean_request_settings.py",
        "translation": voxcpm / "strict_translation_payload.py",
        "gemini": voxcpm / "generic_gemini_runtime.py",
        "direct": voxcpm / "generic_direct_runtime.py",
        "custom": voxcpm / "generic_custom_runtime.py",
        "repair": voxcpm / "generic_clean_audio_repair_runtime.py",
        "semantic_blocks": voxcpm / "semantic_block_runtime.py",
        "direct_io": voxcpm / "direct_max_quality_io.py",
        "direct_cli": voxcpm / "direct_max_quality_cli.py",
        "retry_epoch": voxcpm / "direct_retry_epoch.py",
        "direct_master": voxcpm / "master_direct_russian_only.py",
        "preflight": voxcpm / "dub_job_preflight.py",
        "backend": root / "services" / "speech_backends" / "voxcpm2.py",
        "worker": root / "services" / "dub_worker.py",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        return False, "не найдены canonical owners: " + ", ".join(sorted(missing))

    text = {name: _read(path) for name, path in required.items()}
    failed: list[str] = []

    active_route_names = ("gemini", "direct", "custom", "repair", "direct_master", "preflight")
    forbidden = (
        "sys.modules[",
        "setattr(module",
        "def install_runtime",
        "def install_preflight",
        "ContextVar(",
    )
    if any(token in text[name] for name in active_route_names for token in forbidden):
        failed.append("runtime-safety")

    expected_routes = {
        "render": "tools.voxcpm2.generic_gemini_runtime",
        "render_gemini": "tools.voxcpm2.generic_gemini_runtime",
        "render_direct": "tools.voxcpm2.generic_direct_runtime",
        "repair_audio": "tools.voxcpm2.generic_clean_audio_repair_runtime",
        "prepare_custom": "tools.voxcpm2.generic_custom_runtime",
        "render_custom": "tools.voxcpm2.generic_custom_runtime",
    }
    try:
        recipe = load_recipe("generic_short_v1")
        recipe_ok = all(
            str(recipe.action(action).get("runner") or "") == "python_module"
            and str(recipe.action(action).get("module") or "") == module
            for action, module in expected_routes.items()
        )
    except Exception:
        recipe_ok = False
    if not recipe_ok:
        failed.append("recipe-routing")

    master_ok = (
        'POLICY = spatial_bed_contract.POLICY' in text["direct_master"]
        and '"source_bed_applied": False' in text["direct_master"]
        and '"applied_original_level": 0.0' in text["direct_master"]
        and "master_monolithic_mix" not in text["direct_master"]
        and 'tools.voxcpm2.master_direct_russian_only' in text["backend"]
        and 'master_direct_russian_only.py' in text["core"]
    )
    if not master_ok:
        failed.append("direct-master")

    fingerprint_ok = (
        'POLICY = "clean-runtime-contract-v2"' in text["runtime_contract"]
        and 'tools/voxcpm2/clean_runtime_contract.py' in text["runtime_contract"]
        and 'tools/voxcpm2/master_direct_russian_only.py' in text["runtime_contract"]
        and 'tools/voxcpm2/generic_project_runtime.py' in text["runtime_contract"]
        and 'tools/voxcpm2/generic_direct_runtime.py' in text["runtime_contract"]
        and 'tools/voxcpm2/generic_clean_audio_repair_runtime.py' in text["runtime_contract"]
        and "def build_fingerprints(" in text["runtime_contract"]
    )
    if not fingerprint_ok:
        failed.append("fingerprints")

    preflight_ok = (
        'PREFLIGHT_JSON_TRANSPORT_POLICY = "marked-preflight-json-transport-v2"' in text["preflight"]
        and "backend.runtime_paths(repo, request)" in text["preflight"]
        and "backend.process_environment(" in text["preflight"]
        and "def _decode_probe_payload(" in text["preflight"]
    )
    if not preflight_ok:
        failed.append("preflight")

    worker_ok = (
        "def build_command(" in text["worker"]
        and "from tools.voxcpm2 import dub_job_preflight" in text["worker"]
        and "from services.dub_worker import build_command" in _read(health)
    )
    if not worker_ok:
        failed.append("worker")

    direct_ok = (
        'POLICY = "voxcpm2-direct-max-quality-v3"' in text["direct_io"]
        and "from collections.abc import Mapping" in text["retry_epoch"]
        and "semantic_block_runtime.build_direct_segments(" in text["direct"]
        and "ProjectRoute" in text["gemini"]
        and "ProjectRoute" in text["custom"]
    )
    if not direct_ok:
        failed.append("direct-runtime")

    if failed:
        return False, "не прошли: " + ", ".join(failed)
    return True, (
        "runtime-safety; recipe-routing; direct-master Russian-only; fingerprints; "
        "source-owned preflight; services.dub_worker; typed direct retry"
    )''',
)


# Direct runtime now consumes semantic blocks, not raw legacy groups. Supply the
# truthful block metadata in the unit test instead of asking the owner to infer it.
direct_test = Path("tests/test_generic_direct_runtime.py")
add_direct_semantic_block_metadata(direct_test)


# The failed-probe test must reject both the existing output freshness probe and
# the newly encoded temporary file; otherwise the owner correctly reuses a valid
# existing output and never enters the transaction.
mp3_test = Path("tests/test_audio_conversion_postcondition.py")
text = mp3_test.read_text(encoding="utf-8")
old = '''    async def fake_probe(path):
        return Path(path) == output'''
new = '''    async def fake_probe(path):
        return False'''
if old not in text:
    raise SystemExit("MP3 failed-probe test anchor missing")
text = text.replace(old, new, 1)
mp3_test.write_text(text, encoding="utf-8")

print("source-owner regression finalizer v5 applied")