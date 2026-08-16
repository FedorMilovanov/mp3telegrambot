#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Narrow release-health upgrade for the v6.8 semantic-block direct quality release.

All established Dub environment, worker, renderer, cadence, pronunciation and
post-AAC checks remain authoritative. This module replaces only superseded
source-bed health and adds typical-reference, transactional-import and
analysis-window-aware terminal-noise checks.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from services.dub_worker_release import (
    BACKEND_COMMAND_POLICY,
    BACKEND_ENVIRONMENT_POLICY,
    PRODUCTION_CAPABILITY_POLICY,
    LEGACY_IMPORT_POLICY,
    MASTER_MIX_POLICY,
    REFERENCE_POLICY,
    REFERENCE_SELECTION_POLICY,
    SEMANTIC_BLOCK_POLICY,
    SOURCE_BED_POLICY,
    SOURCE_PROSODY_ROLE_POLICY,
    TAIL_BRACKETING_POLICY,
    WORKER_RUNTIME,
)

POLICY = "truthful-master-reference-import-and-tail-health-v4"
_SUPERSEDED_CHECK = "dialogue-suppressed-master"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _all(text: str, *markers: str) -> bool:
    return bool(text) and all(marker in text for marker in markers)


def _v68_quality_contract(repo: Path) -> tuple[bool, str]:
    root = Path(repo)
    voxcpm = root / "tools" / "voxcpm2"
    contract = _read(voxcpm / "spatial_bed_contract.py")
    master = _read(voxcpm / "master_monolithic_mix.py")
    qa = _read(voxcpm / "final_media_spatial_bed.py")
    reference = _read(voxcpm / "continuous_reference_policy" / "__init__.py")
    quality_facade = _read(voxcpm / "dub_quality_v4" / "__init__.py")
    tail_facade = _read(voxcpm / "direct_tail_artifact" / "__init__.py")
    backend_base = _read(root / "services" / "speech_backends" / "base.py")
    backend_vox = _read(root / "services" / "speech_backends" / "voxcpm2.py")
    clean_core = _read(voxcpm / "clean_production_core.py")
    source_policy = _read(voxcpm / "source_prosody_policy.py")
    semantic_blocks = _read(voxcpm / "semantic_block_runtime.py")
    direct_runtime = _read(voxcpm / "generic_direct_runtime.py")
    direct_cli = _read(voxcpm / "direct_max_quality_cli.py")
    render_core = _read(voxcpm / "direct_max_quality_render.py")
    project_runtime = _read(voxcpm / "generic_project_runtime" / "__init__.py")
    project_runtime_legacy = _read(voxcpm / "generic_project_runtime.py")
    wizard = _read(root / "handlers" / "dub_wizard" / "__init__.py")
    wizard_legacy = _read(root / "handlers" / "dub_wizard.py")
    tests = _read(root / "tests" / "test_spatial_bed_final_media_qa.py")
    master_tests = _read(root / "tests" / "test_dialogue_suppressed_master.py")
    reference_tests = _read(root / "tests" / "test_continuous_reference_typical_f0.py")
    import_tests = _read(root / "tests" / "test_legacy_facade_module_registration.py")
    tail_tests = _read(root / "tests" / "test_embedded_terminal_noise_gate.py")

    checks = {
        "zero-source-contract": _all(
            contract,
            f'POLICY = "{MASTER_MIX_POLICY}"',
            'QA_POLICY = "post-aac-zero-source-bed-v2"',
            f'SOURCE_BED_POLICY = "{SOURCE_BED_POLICY}"',
            "CENTER_FLOOR_RATIO = 0.0",
            "MAX_CENTER_FLOOR = 0.0",
            "SIDE_BED_RATIO = 0.0",
            '"source_bed_applied": False',
            '"applied_original_level": 0.0',
            '"original_mid_and_side_may_both_contain_dialogue"',
        ),
        "russian-only-filter": _all(
            master,
            "from tools.voxcpm2.spatial_bed_contract import (",
            "SOURCE_BED_POLICY",
            "def build_dialogue_suppressed_mix(",
            'if float(levels["applied_original_level"]) != 0.0:',
            'f"[1:a]asetpts=PTS-STARTPTS,highpass=f=35,volume={russian_gain:.9f},"',
            "source is audit input, never a mix stem",
            "source_bed_applied=False",
            "_legacy.build_constant_mix = build_dialogue_suppressed_mix",
            "Importing the module is enough to make direct callers safe",
        ),
        "post-aac-zero-source": _all(
            qa,
            "POLICY = spatial_bed_contract.QA_POLICY",
            "def estimate_spatial_bed(",
            "estimated_center_level",
            "estimated_side_level",
            "normalized_residual",
        ),
        "master-negative-regressions": _all(
            tests,
            "test_old_full_eighteen_percent_source_bed_is_rejected",
            "test_old_side_only_bed_is_rejected_when_side_contains_speech",
            "test_post_aac_accepts_russian_only_mix_for_nonzero_requested_setting",
        ) and _all(
            master_tests,
            "test_import_immediately_overrides_legacy_mixer_and_calibration",
            "test_requested_source_level_is_audit_only_and_applied_level_is_zero",
            "test_mix_graph_uses_only_russian_audio",
            'assert "[0:a]" not in graph',
        ),
        "typical-continuous-reference": _all(
            reference,
            f'POLICY = "{REFERENCE_POLICY}"',
            f'SELECTION_POLICY = "{REFERENCE_SELECTION_POLICY}"',
            'RETIRED_SELECTION_POLICY = "absolute-f0-low-bias-retired-v1"',
            "def _quality_score(",
            "def _candidate_windows(",
            "robust_median = float(np.median(median_values))",
            "robust_p90 = float(np.median(p90_values))",
            "median_distance * MEDIAN_F0_WEIGHT",
            "p90_distance * P90_F0_WEIGHT",
            "_legacy._candidate_windows = _candidate_windows",
        ) and _all(
            reference_tests,
            "test_equal_quality_windows_choose_typical_pitch_not_lowest_pitch",
            'assert selected["stats"]["f0_median"] == 170.0',
            "test_quality_metrics_remain_part_of_reference_ranking",
        ),
        "transactional-legacy-import": _all(
            quality_facade,
            "_previous_legacy = sys.modules.get(_SPEC.name)",
            "sys.modules[_SPEC.name] = _legacy",
            "_SPEC.loader.exec_module(_legacy)",
            "sys.modules.pop(_SPEC.name, None)",
            "sys.modules[_SPEC.name] = _previous_legacy",
        ) and _all(
            import_tests,
            "test_dub_quality_legacy_module_is_registered_for_dataclasses",
            "test_dub_quality_import_succeeds_in_fresh_python_process",
            "test_original_failed_import_chains_succeed_in_fresh_python_process",
            "test_every_package_over_dataclass_file_registers_before_execution",
            "sys.modules.get(legacy.__name__) is legacy",
        ),
        "model-independent-backend-boundary": _all(
            backend_base,
            f'BACKEND_COMMAND_POLICY = "{BACKEND_COMMAND_POLICY}"',
            f'BACKEND_ENVIRONMENT_POLICY = "{BACKEND_ENVIRONMENT_POLICY}"',
            f'PRODUCTION_CAPABILITY_POLICY = "{PRODUCTION_CAPABILITY_POLICY}"',
            "REQUIRED_PRODUCTION_CAPABILITIES = (",
            "def missing(",
            "class BackendAudioSpec:",
            "def __post_init__(self)",
            "class BackendProcessEnvironment:",
            "class BackendSynthesisSession(Protocol):",
            "def open_session(",
            "def build_renderer_command(",
            "def build_master_command(",
            "def process_environment(",
        ) and _all(
            backend_vox,
            "def build_renderer_command(",
            "def build_master_command(",
            "def process_environment(",
            "def open_session(",
            "class VoxCPM2Session:",
            "BackendProcessEnvironment(",
            "CUDA_VISIBLE_DEVICES",
            "--segments-json",
            "--russian-only-video",
        ) and _all(
            clean_core,
            "backend.build_renderer_command(",
            "backend.build_master_command(",
            "backend.process_environment(",
            "get_backend(",
        ),
        "speech-backend-request-boundary": _all(
            project_runtime,
            "from services.speech_backends import DEFAULT_BACKEND_ID, get_backend, resolve_backend_id",
            "backend_id = resolve_backend_id(",
            "backend = get_backend(backend_id)",
            "backend.capabilities().missing()",
            'result["speech_backend"] = backend_id',
            "Некорректный speech_backend",
        ) and (
            "from tools.voxcpm2 import clean_production_core" not in project_runtime
        ) and _all(
            wizard_legacy,
            '"speech_backend": os.getenv("DUB_SPEECH_BACKEND", "voxcpm2")',
            "def _request_payload(",
        ) and _all(
            wizard,
            "generic_project_runtime.validate_request_payload(payload)",
        ),
        "generic-engine-hook": _all(
            project_runtime_legacy,
            "def _run_speech_and_master(",
            "russian_timeline = _run_speech_and_master(",
            "backend = get_backend(request.get(\"speech_backend\") or DEFAULT_BACKEND_ID)",
            "backend.process_environment(",
            "backend.build_renderer_command(",
            "backend.build_master_command(",
        ) and _all(
            direct_runtime,
            "clean.render_and_master(",
        ),
        "semantic-block-direct-runtime": _all(
            semantic_blocks,
            f'POLICY = "{SEMANTIC_BLOCK_POLICY}"',
            "MIN_BLOCK_SECONDS = 7.0",
            "TARGET_BLOCK_SECONDS = 10.5",
            "MAX_BLOCK_SECONDS = 15.0",
            f'CONTINUATION_POLICY = "previous-block-prompt-with-fixed-anchor-v1"',
            "def group_ready_srt(",
            "def build_direct_segments(",
        ) and _all(
            direct_runtime,
            "semantic_block_runtime.group_ready_srt",
            "semantic_block_runtime.build_direct_segments(",
            "_run_speech_and_master",
        ) and _all(
            direct_cli,
            'continuation_hook = globals().get("set_continuation_context")',
            'continuation_reset = globals().get("set_continuation_context")',
            "backend = get_backend(args.speech_backend)",
            "backend.open_session(",
            "_backend_generate(",
            "session.generate(",
        ) and _all(
            render_core,
            "continuation_reference: Path | None = None",
            '"prompt_wav_path"',
            '"prompt_text"',
        ),
        "source-prosody-diagnostic-only": _all(
            source_policy,
            f'POLICY = "{SOURCE_PROSODY_ROLE_POLICY}"',
            "def ranking_view(",
            "def mark_diagnostic_only(",
            'result.pop("source_prosody", None)',
        ) and _all(
            _read(voxcpm / "direct_max_quality_cli" / "__init__.py"),
            "source_prosody_policy.ranking_view(display_segment)",
            "source_prosody_policy.mark_diagnostic_only(item)",
        ),
        "overlap-aware-terminal-noise": _all(
            tail_facade,
            f'BRACKETING_POLICY = "{TAIL_BRACKETING_POLICY}"',
            "FRAME_OVERLAP_TOLERANCE = 2",
            "def _bracketing_voice_runs(",
            "item[1] <= burst_start + tolerance",
            "item[0] >= burst_end - tolerance",
            "overlap_before + overlap_after > tolerance",
            "gap_before = max(0, burst_start - previous[1]) * 0.010",
            "gap_after = max(0, following[0] - burst_end) * 0.010",
            "0.04 <= terminal_residue_seconds <= 0.34",
            '"analysis_overlap_after_frames": overlap_after',
        ) and _all(
            tail_tests,
            "test_quiet_dip_noise_island_and_voice_residue_is_rejected",
            "test_continuous_terminal_fricative_without_quiet_dip_is_not_embedded_artifact",
            "test_bracketing_rejects_overlap_beyond_analysis_window_tolerance",
            'assert report["analysis_overlap_after_frames"] <= 2',
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        return False, "Dub v6.8 quality-contract не прошёл: " + ", ".join(failed)
    return True, (
        "Russian-only direct master: requested source level is audit-only; applied center/side "
        "are zero; speech-bearing original is absent from the FFmpeg graph; post-AAC center "
        "and side leakage regressions are fail-closed; continuous reference selection is "
        "quality-gated and ranked around the speaker's robust median F0; legacy dataclass "
        f"modules use {LEGACY_IMPORT_POLICY} before execution with rollback on failure; "
        f"terminal broadband islands use {TAIL_BRACKETING_POLICY} with bounded frame overlap; "
        f"semantic blocks use {SEMANTIC_BLOCK_POLICY}; backend={BACKEND_COMMAND_POLICY}; "
        f"process environment={BACKEND_ENVIRONMENT_POLICY}; capability gate={PRODUCTION_CAPABILITY_POLICY}; "
        "request backend selector is fail-closed; "
        f"source prosody role is {SOURCE_PROSODY_ROLE_POLICY}"
    )


def _v67_quality_contract(repo: Path) -> tuple[bool, str]:
    """Compatibility alias retained for existing v6.7 callers."""
    return _v68_quality_contract(repo)


def _v66_quality_contract(repo: Path) -> tuple[bool, str]:
    """Compatibility alias retained for existing callers."""
    return _v68_quality_contract(repo)


def _v65_quality_contract(repo: Path) -> tuple[bool, str]:
    """Compatibility alias retained for existing callers."""
    return _v68_quality_contract(repo)


def _russian_only_master_contract(repo: Path) -> tuple[bool, str]:
    """Compatibility alias retained for existing tests and diagnostics."""
    return _v68_quality_contract(repo)


__all__ = [
    "POLICY",
    "WORKER_RUNTIME",
    "_russian_only_master_contract",
    "_v65_quality_contract",
    "_v66_quality_contract",
    "_v67_quality_contract",
    "_v68_quality_contract",
]
