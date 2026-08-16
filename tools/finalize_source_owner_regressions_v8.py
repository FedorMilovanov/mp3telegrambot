#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path

LEGACY_ACCESS_TESTS = (
    "tests/test_clean_master_child_process_contract.py",
    "tests/test_clean_release_marker.py",
    "tests/test_clean_repair_request_contract.py",
    "tests/test_clean_runtime_contract.py",
    "tests/test_clean_segment_contract.py",
    "tests/test_continuous_reference_typical_f0.py",
    "tests/test_direct_speech_slot_contract.py",
    "tests/test_dub_audio_repair_handler_contract.py",
    "tests/test_dub_health_supplemental_contract.py",
    "tests/test_dub_job_preflight_v2.py",
    "tests/test_dub_quality_v42_runtime_contracts.py",
    "tests/test_dub_source_identity.py",
    "tests/test_dub_wizard_request_writer.py",
    "tests/test_dub_worker.py",
    "tests/test_dub_worker_preflight_cancellation.py",
    "tests/test_dub_worker_root_fail_closed.py",
    "tests/test_generic_project_runtime_contract.py",
    "tests/test_semantic_block_runtime.py",
    "tests/test_source_identity_pre_network.py",
)

# These suites only asserted that compatibility facades patched another module.
# That mechanism is intentionally gone; source-owned behavior is covered elsewhere.
RETIRED_FACADE_TESTS = (
    "tests/test_clean_runtime_facades.py",
    "tests/test_legacy_facade_module_registration.py",
)

SURGERY_ONLY_NAME_PARTS = (
    "facade",
    "patches_legacy",
    "compatibility_only",
)


def _remove_surgery_only_tests(path: Path, text: str) -> str:
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return text
    spans: list[tuple[int, int]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        lowered = node.name.lower()
        if any(part in lowered for part in SURGERY_ONLY_NAME_PARTS):
            spans.append((node.lineno, node.end_lineno or node.lineno))
    if not spans:
        return text
    lines = text.splitlines(keepends=True)
    for start, end in reversed(spans):
        del lines[start - 1 : end]
    return "".join(lines)


for rel in LEGACY_ACCESS_TESTS:
    path = Path(rel)
    if not path.is_file():
        continue
    text = path.read_text(encoding="utf-8")
    text = text.replace("._legacy.", ".")
    text = text.replace("._legacy,", ",")
    text = text.replace("._legacy)", ")")
    text = text.replace("._legacy\n", "\n")
    text = _remove_surgery_only_tests(path, text)
    path.write_text(text, encoding="utf-8")

repair_test = Path("tests/test_clean_repair_request_contract.py")
if repair_test.is_file():
    text = repair_test.read_text(encoding="utf-8")
    text = text.replace(
        'monkeypatch.setattr(repair, "main", forbidden_main)',
        'monkeypatch.setattr(repair, "_source_main", forbidden_main)',
    )
    repair_test.write_text(text, encoding="utf-8")

for rel in RETIRED_FACADE_TESTS:
    Path(rel).unlink(missing_ok=True)

print("source-owner regression finalizer v8 applied")
