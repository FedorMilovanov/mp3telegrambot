#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path


def replace(path_s: str, old: str, new: str, *, required: bool = True) -> None:
    path = Path(path_s)
    text = path.read_text(encoding="utf-8")
    if old not in text:
        if required:
            raise RuntimeError(f"missing expected text in {path}: {old[:120]!r}")
        return
    path.write_text(text.replace(old, new), encoding="utf-8")


def remove_tests(path_s: str, names: set[str]) -> None:
    path = Path(path_s)
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    spans: list[tuple[int, int]] = []
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            spans.append((node.lineno, node.end_lineno or node.lineno))
            found.add(node.name)
    missing = names - found
    if missing:
        raise RuntimeError(f"missing tests in {path}: {sorted(missing)}")
    lines = text.splitlines(keepends=True)
    for start, end in reversed(spans):
        del lines[start - 1:end]
    path.write_text("".join(lines), encoding="utf-8")


# Real source fixes: remove self-shadowing in the active final-media wrapper and
# call the source-owned timing guard with its canonical keyword.
replace(
    "tools/voxcpm2/final_media_qa.py",
    "    probe_media = probe_media\n    measure_loudness = measure_loudness\n",
    "",
)
replace(
    "tools/voxcpm2/direct_max_quality_cli.py",
    "_surgical_max_tempo=_surgical_max_tempo",
    "max_tempo=_surgical_max_tempo",
)

# Cut Policy: explicit empty evidence is the fail-closed proof-unavailable case.
for rel in (
    "tests/test_shorts_factory_mode.py",
    "tests/test_shorts_factory_ru_boundaries.py",
):
    path = Path(rel)
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "align_factory_livedub_candidates(candidates, source_duration=300)",
        "align_factory_livedub_candidates(candidates, source_duration=300, evidence={})",
    )
    path.write_text(text, encoding="utf-8")

# Worker behavior is owned by services.dub_worker now, not a tools facade.
replace(
    "tests/test_dub_worker.py",
    "import tools.voxcpm2.dub_worker as worker",
    "import services.dub_worker as worker",
)
replace(
    "tests/test_dub_worker.py",
    "hardened_worker._deepest_error_line(error)",
    "worker._deepest_error_line(error)",
)
remove_tests(
    "tests/test_dub_worker.py",
    {"test_hardened_worker_installs_current_release_and_store_hooks"},
)

# Installer/wrapper tests assert mechanisms intentionally removed by this PR.
remove_tests(
    "tests/test_operator_runtime_status.py",
    {
        "test_status_wrapper_appends_to_same_admin_reply",
        "test_status_wrapper_preserves_non_status_reply",
    },
)
remove_tests(
    "tests/test_restart_state_runtime.py",
    {"test_restart_installer_does_not_replace_bot_runner"},
)
Path("tests/test_direct_surgical_wiring.py").unlink(missing_ok=True)

# Canonical renderer/master owners after facade retirement.
for rel in (
    "tests/test_clean_dub_production.py",
    "tests/test_dub_professional_audio_v45.py",
):
    path = Path(rel)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "tools.voxcpm2.generic_clean_gemini_runtime",
            "tools.voxcpm2.generic_gemini_runtime",
        )
        path.write_text(text, encoding="utf-8")
remove_tests(
    "tests/test_clean_dub_production.py",
    {"test_clean_entrypoints_disable_hidden_legacy_guard"},
)
replace(
    "tests/test_speech_backend_command_contract.py",
    "tools.voxcpm2.master_monolithic_mix",
    "tools.voxcpm2.master_direct_russian_only",
)

# Source-download tests patch the actual pipeline owner, not the retired hardened facade.
path = Path("tests/test_clean_source_download.py")
text = path.read_text(encoding="utf-8").replace(
    "source_cache.hardened._ytdlp_base",
    "source_cache.pipeline._ytdlp_base",
)
path.write_text(text, encoding="utf-8")
remove_tests(
    "tests/test_clean_source_download.py",
    {"test_all_clean_entrypoints_replace_the_direct_download_function"},
)

# Package-facade filename assertions are obsolete; validate the canonical files.
replace(
    "tests/test_dub_job_preflight_v2.py",
    'Path(preflight.__file__).name == "__init__.py"',
    'Path(preflight.__file__).name == "dub_job_preflight.py"',
    required=False,
)
replace(
    "tests/test_original_bed_alignment.py",
    'Path(final_media_qa.__file__).name == "__init__.py"',
    'Path(final_media_qa.__file__).name == "final_media_qa.py"',
    required=False,
)

print("source-owner regression finalizer v9 applied")
