#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if old not in text:
        raise RuntimeError(f"v12 expected text missing in {path}: {old!r}")
    write(path, text.replace(old, new, 1))


def remove_once(path: str, old: str) -> None:
    replace_once(path, old, "")


def remove_all_but_last_top_level(path: str, name: str) -> None:
    text = read(path)
    tree = ast.parse(text, filename=path)
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name == name
    ]
    if len(nodes) < 2:
        raise RuntimeError(
            f"v12 expected shadowed top-level definitions: {path}::{name}"
        )
    lines = text.splitlines(keepends=True)
    ranges: list[tuple[int, int]] = []
    for node in nodes[:-1]:
        start = node.lineno - 1
        end = node.end_lineno
        while end < len(lines) and not lines[end].strip():
            end += 1
        ranges.append((start, end))
    for start, end in reversed(ranges):
        del lines[start:end]
    write(path, "".join(lines))


# Retired installers must not remain in public export lists.
replace_once(
    "services/conspect_audit_runtime.py",
    '__all__ = ["_normalize_legacy_lexicon", "install_conspect_audit_runtime"]',
    '__all__ = ["_normalize_legacy_lexicon"]',
)
replace_once(
    "services/gemini_max_quality.py",
    '__all__ = ["configure_max_quality_env", "install_max_quality_runtime"]',
    '__all__ = ["configure_max_quality_env"]',
)

# LiveDub output policy delegates title translation directly to the existing
# presentation source owner; no separate runtime translator is installed.
replace_once(
    "services/livedub_output_policy.py",
    "from core.media_title_policy import canonical_media_title\n",
    "from core.media_title_policy import canonical_media_title\n"
    "from services.livedub_info_presentation_policy import "
    "_translate_title_second_chance\n",
)
replace_once(
    "services/livedub_output_policy.py",
    "translated = await _translate_title_line(raw_line)",
    "translated = await _translate_title_second_chance(raw_line)",
)

# Repeated v8 finalizer runs appended the same helper test four times.
remove_all_but_last_top_level(
    "tests/test_clean_request_settings.py",
    "test_repair_owner_preserves_runtime_helpers",
)

# clean_runtime_contract: keep only backend-owned discovery and the final
# backend-aware fingerprint builder.
remove_once(
    "tools/voxcpm2/clean_runtime_contract.py",
    "from tools.voxcpm2.direct_max_quality_io import discover_model\n\n",
)
remove_all_but_last_top_level(
    "tools/voxcpm2/clean_runtime_contract.py",
    "build_fingerprints",
)

# Direct CLI uses timing and hashing as ordinary source imports. The imported
# IO POLICY was never read before the CLI-owned policy replaced it.
replace_once(
    "tools/voxcpm2/direct_max_quality_cli.py",
    "import argparse\n",
    "import argparse\nimport hashlib\n",
)
replace_once(
    "tools/voxcpm2/direct_max_quality_cli.py",
    "from tools.voxcpm2.direct_max_quality_io import (\n    POLICY,\n",
    "from tools.voxcpm2.direct_max_quality_io import (\n",
)
replace_once(
    "tools/voxcpm2/direct_max_quality_cli.py",
    "from tools.voxcpm2.direct_max_quality_io import (",
    "from tools.voxcpm2 import direct_timing_guard\n"
    "from tools.voxcpm2.direct_max_quality_io import (",
)

# The first renderer implementation is completely shadowed. The old hook-sync
# seam was also ineffective (local assignments only), so remove it rather than
# preserving facade injection behavior.
remove_all_but_last_top_level(
    "tools/voxcpm2/direct_max_quality_render.py",
    "fit_without_slowdown",
)
for block in (
    'HOOK_SYNC_POLICY = "facade-runtime-hook-sync-v2"\n\n',
    "_DEFAULT_PROBE_DURATION = probe_duration\n\n",
    "_DEFAULT_RUN_CHECKED = run_checked\n\n",
    "_DEFAULT_TIMELINE_QA = direct_timeline_delivery_qa\n\n",
    "    _sync_legacy_hooks()\n",
):
    remove_once("tools/voxcpm2/direct_max_quality_render.py", block)
text = read("tools/voxcpm2/direct_max_quality_render.py")
tree = ast.parse(text)
node = next(
    item
    for item in tree.body
    if isinstance(item, ast.FunctionDef) and item.name == "_sync_legacy_hooks"
)
lines = text.splitlines(keepends=True)
start = node.lineno - 1
end = node.end_lineno
while end < len(lines) and not lines[end].strip():
    end += 1
del lines[start:end]
write("tools/voxcpm2/direct_max_quality_render.py", "".join(lines))

# direct_surgical_io keeps LazyBackend locally and obtains the shared IO types
# from the pure polish owner. Remove the earlier fully-shadowed copies.
for name in (
    "MutableAudioSpec",
    "LazySession",
    "cached_reference",
    "enrich_reference_report",
):
    remove_all_but_last_top_level("tools/voxcpm2/direct_surgical_io.py", name)

# Consolidated source files: the last definition is the active production
# contract; Ruff confirms the earlier binding is unused before it is replaced.
for path, names in (
    ("tools/voxcpm2/dub_quality_v4.py", ("group_ready_srt_v4",)),
    (
        "tools/voxcpm2/expressive_continuity.py",
        ("plan_json", "build_controlled_expressive_reference"),
    ),
    (
        "tools/voxcpm2/final_media_qa.py",
        ("estimate_original_bed", "verify_original_bed", "verify_final_outputs"),
    ),
    (
        "tools/voxcpm2/generic_project_runtime.py",
        ("project_root", "load_request", "save_json"),
    ),
):
    for name in names:
        remove_all_but_last_top_level(path, name)

print("v12 source consolidation applied")
