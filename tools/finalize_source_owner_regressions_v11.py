#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
from pathlib import Path


def read(path_s: str) -> str:
    return Path(path_s).read_text(encoding="utf-8")


def write(path_s: str, text: str) -> None:
    Path(path_s).write_text(text, encoding="utf-8")


def replace_once(path_s: str, old: str, new: str) -> None:
    text = read(path_s)
    if old not in text:
        raise RuntimeError(f"v11 expected text missing in {path_s}: {old[:120]!r}")
    write(path_s, text.replace(old, new, 1))


def replace_all(path_s: str, old: str, new: str, *, minimum: int = 1) -> None:
    text = read(path_s)
    count = text.count(old)
    if count < minimum:
        raise RuntimeError(
            f"v11 expected >= {minimum} occurrences in {path_s}, got {count}: {old!r}"
        )
    write(path_s, text.replace(old, new))


def _function_node(path_s: str, name: str):
    text = read(path_s)
    tree = ast.parse(text, filename=path_s)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return text, node
    raise RuntimeError(f"v11 function not found: {path_s}::{name}")


def replace_function(path_s: str, name: str, replacement: str) -> None:
    text, node = _function_node(path_s, name)
    lines = text.splitlines(keepends=True)
    start = node.lineno - 1
    end = node.end_lineno
    repl = replacement.rstrip() + "\n\n"
    write(path_s, "".join(lines[:start]) + repl + "".join(lines[end:]))


def remove_function(path_s: str, name: str) -> None:
    text, node = _function_node(path_s, name)
    lines = text.splitlines(keepends=True)
    start = node.lineno - 1
    end = node.end_lineno
    # Remove at most one immediately trailing blank line as well.
    while end < len(lines) and lines[end].strip() == "":
        end += 1
        break
    write(path_s, "".join(lines[:start]) + "".join(lines[end:]))


# ---------------------------------------------------------------------------
# Production fixes: source owners only, no runtime surgery.
# ---------------------------------------------------------------------------

# clean_production_core: explicit child-process owner, correct repository root.
path = "tools/voxcpm2/clean_production_core.py"
text = read(path)
text = text.replace(
    "_REPO_ROOT = Path(__file__).resolve().parents[3]",
    "_REPO_ROOT = Path(__file__).resolve().parents[2]",
    1,
)
text = text.replace(
    "result = subprocess.run(command, cwd=str(repo), env=env, check=False)",
    "result = _run_child_process(command, cwd=str(repo), env=env, check=False)",
    1,
)
text = text.replace(
    "result = subprocess.run(master_command, cwd=str(repo), env=env, check=False)",
    "result = _run_child_process(master_command, cwd=str(repo), env=env, check=False)",
    1,
)
text, count = re.subn(
    r"\nclass _SubprocessProxy:\n.*?(?=\ndef _finite\()",
    "\n",
    text,
    count=1,
    flags=re.S,
)
if count != 1:
    raise RuntimeError("v11 failed to remove _SubprocessProxy")
if "subprocess = _SubprocessProxy()" not in text:
    raise RuntimeError("v11 expected clean-core subprocess proxy binding")
text = text.replace("\nsubprocess = _SubprocessProxy()\n", "\n", 1)
if "_SubprocessProxy" in text:
    raise RuntimeError("v11 clean-core proxy residue remains")
write(path, text)

# expressive_continuity: the early source-analysis helper and the later
# monolithic presentation helper accidentally shared the same global name.
replace_once(
    "tools/voxcpm2/expressive_continuity.py",
    "def _style(\n    score: float,\n    rate_z: float,\n    text: str,\n) -> tuple[str, str]:",
    "def _source_style(\n    score: float,\n    rate_z: float,\n    text: str,\n) -> tuple[str, str]:",
)
replace_once(
    "tools/voxcpm2/expressive_continuity.py",
    "tier, instruction = _style(\n            score,\n            rate_z[index],\n            text,\n        )",
    "tier, instruction = _source_style(\n            score,\n            rate_z[index],\n            text,\n        )",
)

# Restart cleanup is a normal source-owned call before every fresh event loop.
replace_once(
    "main.py",
    "def run_bot():\n    restart_delay = 5",
    "def run_bot():\n    from services.restart_state_runtime import reset_cross_loop_state\n\n    restart_delay = 5",
)
replace_once(
    "main.py",
    "    while True:\n        loop = asyncio.new_event_loop()",
    "    while True:\n        reset_cross_loop_state()\n        loop = asyncio.new_event_loop()",
)

# ---------------------------------------------------------------------------
# Tests: point behavior checks at canonical owners; retire surgery mechanics.
# ---------------------------------------------------------------------------

replace_once(
    "tests/test_clean_master_child_process_contract.py",
    'str(ROOT / "tools" / "voxcpm2" / "master_monolithic_mix.py")',
    'str(ROOT / "tools" / "voxcpm2" / "master_direct_russian_only.py")',
)
replace_function(
    "tests/test_clean_master_child_process_contract.py",
    "test_subprocess_proxy_is_scoped_to_clean_legacy_module",
    '''def test_clean_core_uses_explicit_child_process_owner() -> None:
    source = Path(core.__file__).read_text(encoding="utf-8")
    assert core.subprocess is subprocess
    assert subprocess.run is core._stdlib_subprocess.run
    assert "_SubprocessProxy" not in source
    assert "subprocess = _SubprocessProxy()" not in source
    assert core.CHILD_PYTHON_POLICY == "repo-root-pythonpath-master-stderr-and-post-aac-v2"''',
)

# Guard the clean owner so a local proxy/rebinding cannot silently return.
replace_once(
    "tests/test_no_runtime_surgery_contract.py",
    '    "pipelines/main_pipeline.py",\n)',
    '    "pipelines/main_pipeline.py",\n    "tools/voxcpm2/clean_production_core.py",\n)',
)
replace_once(
    "tests/test_no_runtime_surgery_contract.py",
    '    ".STUDY_ANALYSIS_PROMPT =",\n)',
    '    ".STUDY_ANALYSIS_PROMPT =",\n    "_SubprocessProxy",\n    "subprocess = _SubprocessProxy()",\n)',
)

# Fingerprint assertions should be semantic membership, not tuple quote style.
for marker in (
    "tools/voxcpm2/direct_source_prosody.py",
    "tools/voxcpm2/clean_source_download.py",
    "tools/voxcpm2/clean_request_settings.py",
    "tools/voxcpm2/strict_translation_payload.py",
    "tools/voxcpm2/legacy_segment_migration_v45.py",
):
    replace_once(
        "tests/test_clean_runtime_contract.py",
        f'''    assert '\"{marker}\"' in contract_source''',
        f'''    assert "{marker}" in contract._RENDER_MODULES''',
    )

# Source-owned clean routes.
for test_path in (
    "tests/test_continuous_reference_policy.py",
    "tests/test_continuous_reference_release_contract.py",
    "tests/test_controlled_reference_gate.py",
    "tests/test_expressive_dub_policy.py",
):
    for old, new in (
        ("generic_clean_gemini_runtime.py", "generic_gemini_runtime.py"),
        ("generic_clean_direct_runtime.py", "generic_direct_runtime.py"),
        ("generic_clean_custom_runtime.py", "generic_custom_runtime.py"),
    ):
        if old in read(test_path):
            replace_all(test_path, old, new)

replace_once(
    "tests/test_dub_architecture_boundaries.py",
    'assert "master_monolithic_mix.py" in command[1]',
    'assert "master_direct_russian_only.py" in command[1]',
)
replace_once(
    "tests/test_dub_audio_repair.py",
    "tools/voxcpm2/generic_clean_audio_repair_runtime/__init__.py",
    "tools/voxcpm2/generic_clean_audio_repair_runtime.py",
)
replace_all(
    "tests/test_dub_audio_repair_handler_contract.py",
    "_legacy_dubfix_command",
    "_dubfix_command_unlocked",
)
replace_all(
    "tests/test_dubfix_error_propagation.py",
    "_legacy_dubfix_command",
    "_dubfix_command_unlocked",
)

# Pure package/installer wiring tests are obsolete; behavior is covered in the
# same files by source-owned functional tests.
remove_function(
    "tests/test_dub_continuation_and_command_recovery.py",
    "test_runtime_fingerprints_compaction_but_production_keeps_source_timeline",
)
remove_function(
    "tests/test_dub_quality_v4.py",
    "test_quality_v4_entrypoints_disable_legacy_prompt_guard",
)
remove_function(
    "tests/test_independent_qa_segment_retry.py",
    "test_ready_srt_entrypoint_installs_release_scoped_recovery",
)
remove_function(
    "tests/test_semantic_tts_guard.py",
    "test_voxcpm_wrapper_supports_old_new_and_legacy_kwargs_apis",
)

replace_once(
    "tests/test_dub_job_preflight_v2.py",
    '    assert not any("/__init__.py" in path for path in files)',
    '''    for retired in (
        "tools/voxcpm2/clean_runtime_contract/__init__.py",
        "tools/voxcpm2/generic_clean_direct_runtime/__init__.py",
        "tools/voxcpm2/direct_max_quality_cli/__init__.py",
    ):
        assert retired not in files''',
)
replace_once(
    "tests/test_dub_professional_audio_v45.py",
    '"tools.voxcpm2.generic_clean_direct_runtime"',
    '"tools.voxcpm2.generic_direct_runtime"',
)
replace_once(
    "tests/test_dub_title_policy.py",
    '"tools/voxcpm2/generic_short_runtime.py": "canonical_media_title"',
    '"tools/voxcpm2/generic_short_production.py": "canonical_media_title"',
)
replace_once(
    "tests/test_expressive_dub_policy.py",
    'assert "production.translate_groups_max = expressive_translation.translate_groups" in route',
    'assert "translate_groups=expressive_translation.translate_groups" in route',
)

# async ffmpeg owner count is five in ffmpeg.py; static motion probing has its
# own source owner with one cancellable process call.
replace_once(
    "tests/test_ffmpeg_probe_ownership.py",
    'STATIC_RUNTIME_PATH = Path("services/shorts_static_runtime.py")',
    'STATIC_RUNTIME_PATH = Path("services/shorts_static_policy.py")',
)
replace_once(
    "tests/test_ffmpeg_probe_ownership.py",
    'assert selected.count("await run_cancellable_process(") == 6',
    'assert selected.count("await run_cancellable_process(") == 5',
)

# Use a test-private key; reset intentionally preserves confirmed success TTLs.
replace_once(
    "tests/test_livedub_quality_runtime.py",
    'key = ("new", "chat", "reply", "video")',
    'key = ("new", "failure-test-chat", "failure-test-reply", "failure-test-video")',
)

replace_function(
    "tests/test_original_bed_alignment.py",
    "test_zero_safe_package_keeps_legacy_alignment_bounded",
    '''def test_source_owned_alignment_is_bounded() -> None:
    assert Path(final_media_qa.__file__).name == "final_media_qa.py"
    source = Path(final_media_qa.__file__).read_text(encoding="utf-8")
    assert "ORIGINAL_ALIGNMENT_MAX_SECONDS = 0.15" in source
    assert "ORIGINAL_ALIGNMENT_PROBE_SECONDS = 180.0" in source
    assert "_estimate_alignment_lag" in source
    assert "_align_three" in source''',
)

replace_function(
    "tests/test_shorts_factory_editorial_lifecycle.py",
    "test_translation_editorial_runner_is_the_only_standalone_owner",
    '''def test_translation_editorial_runner_is_the_only_standalone_owner():
    dispatcher = Path("pipelines/video_dispatch.py").read_text(encoding="utf-8")
    retired_bridge = Path("services/shorts_factory_overload_editorial_polish.py")
    assert "services.translation_editorial_runner" in dispatcher
    assert not retired_bridge.exists()
    assert "shorts_factory_editorial_bridge" not in dispatcher''',
)

replace_function(
    "tests/test_timeline_onset_repair_and_speech_backends.py",
    "test_runtime_contract_fingerprints_backend_and_onset_repair_sources",
    '''def test_runtime_contract_fingerprints_backend_and_onset_repair_sources() -> None:
    from tools.voxcpm2 import clean_runtime_contract

    names = set(clean_runtime_contract._RENDER_MODULES) | set(
        clean_runtime_contract._RELEASE_MODULES
    )
    for marker in (
        "services/speech_backends/base.py",
        "services/speech_backends/registry.py",
        "services/speech_backends/voxcpm2.py",
        "tools/voxcpm2/timeline_onset_repair.py",
        "tools/voxcpm2/professional_audio_qa_v45.py",
        "tools/voxcpm2/generic_direct_runtime.py",
    ):
        assert marker in names''',
)
replace_function(
    "tests/test_timeline_onset_repair_and_speech_backends.py",
    "test_complete_retry_round_checkpoints_are_migratable_without_audio_loss",
    '''def test_complete_retry_round_checkpoints_are_migratable_without_audio_loss() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "tools" / "voxcpm2" / "generic_direct_runtime.py").read_text(
        encoding="utf-8"
    )
    for marker in (
        "_legacy_checkpoint_prefix",
        "selected_raw_pitch_evidence_ok",
        "model_config_sha256",
        "reference_sha256",
        "accepted_ids != list(range(1, accepted_ids[-1] + 1))",
        "checkpoint_resume_migration",
    ):
        assert marker in source
    assert "generic_clean_direct_runtime" not in source''',
)

replace_once(
    "tests/test_transcript_source_priority.py",
    'CLEAN = ROOT / "tools" / "voxcpm2" / "generic_clean_gemini_runtime.py"',
    'CLEAN = ROOT / "tools" / "voxcpm2" / "generic_gemini_runtime.py"',
)
replace_once(
    "tests/test_transcript_source_priority.py",
    'assert "production.parse_manual_vtt = checked.parse_creator_vtt_preserving_text" in clean',
    '''assert 'kwargs["manual_vtt_parser"] = parse_creator_vtt_preserving_text' in clean
    assert "production.parse_manual_vtt =" not in clean''',
)

replace_once(
    "tests/test_v3_audit_r24_local_botapi_patient_wait.py",
    'assert REQUIRED.count("process_runtime._start_server(host, port)") == 1',
    '''assert REQUIRED.count("process_runtime._start_server(") == 1
    assert "proxy_url=proxy_url" in REQUIRED''',
)

replace_function(
    "tests/test_v3_audit_r28_static_fit_mode.py",
    "test_is_static_video_is_async_and_uses_freezedetect",
    '''def test_is_static_video_is_async_and_uses_source_owned_static_policy():
    assert inspect.iscoroutinefunction(_is_static_video)
    wrapper = Path("services/ffmpeg.py").read_text(encoding="utf-8")
    policy = Path("services/shorts_static_policy.py").read_text(encoding="utf-8")
    assert "_is_static_video_confident" in wrapper
    assert "freezedetect" in policy
    assert "moving/default-crop" in policy''',
)
replace_function(
    "tests/test_v3_audit_r38_review_hardening.py",
    "test_r28b_static_requires_dominant_freeze",
    '''def test_r28b_static_requires_dominant_freeze():
    src = Path("services/shorts_static_policy.py").read_text(encoding="utf-8")
    assert "freeze_duration" in src
    assert "freeze_ratio >= freeze_min" in src
    assert 'SHORTS_STATIC_FREEZE_RATIO_MIN", 0.86' in src''',
)

replace_function(
    "tests/test_v3_audit_r4_infra.py",
    "test_restart_clears_rate_limit_and_video_lock_meta",
    '''def test_restart_uses_source_owned_cross_loop_cleanup():
    main = Path("main.py").read_text(encoding="utf-8")
    owner = Path("services/restart_state_runtime.py").read_text(encoding="utf-8")
    run_block = main[main.index("def run_bot():"):main.index("def main():")]
    assert "from services.restart_state_runtime import reset_cross_loop_state" in run_block
    assert run_block.index("reset_cross_loop_state()") < run_block.index("asyncio.new_event_loop()")
    assert "reset_delivery_runtime_state" in owner
    assert "ContextVar" not in owner''',
)

print("source-owner regression finalizer v11 applied")
