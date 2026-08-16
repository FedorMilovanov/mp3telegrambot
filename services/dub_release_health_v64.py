#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical Dub release health for the source-owned runtime architecture."""
from __future__ import annotations

from pathlib import Path

from services.dub_worker_release import (
    BACKEND_COMMAND_POLICY,
    BACKEND_ENVIRONMENT_POLICY,
    PRODUCTION_CAPABILITY_POLICY,
    SEMANTIC_BLOCK_POLICY,
    WORKER_RUNTIME,
)

POLICY = "canonical-source-owned-dub-health-v1"

_FORBIDDEN_SOURCE_TOKENS = (
    "sys.modules[",
    "spec_from_file_location(",
    "module_from_spec(",
    "exec(compile(",
    "setattr(module",
    ".__class__ =",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _has(text: str, *markers: str) -> bool:
    return bool(text) and all(marker in text for marker in markers)


def _v68_quality_contract(repo: Path) -> tuple[bool, str]:
    root = Path(repo).resolve()
    voxcpm = root / "tools" / "voxcpm2"
    services = root / "services"

    paths = {
        "runtime_contract": voxcpm / "clean_runtime_contract.py",
        "clean_core": voxcpm / "clean_production_core.py",
        "project": voxcpm / "generic_project_runtime.py",
        "direct": voxcpm / "generic_direct_runtime.py",
        "gemini": voxcpm / "generic_gemini_runtime.py",
        "custom": voxcpm / "generic_custom_runtime.py",
        "repair": voxcpm / "generic_clean_audio_repair_runtime.py",
        "semantic_blocks": voxcpm / "semantic_block_runtime.py",
        "direct_cli": voxcpm / "direct_max_quality_cli.py",
        "stable_cli": voxcpm / "examples" / "john_piper_z20py4yqhyq" / "voxcpm2_cpu_shorts_production.py",
        "backend_base": services / "speech_backends" / "base.py",
        "backend_vox": services / "speech_backends" / "voxcpm2.py",
        "preflight": voxcpm / "dub_job_preflight.py",
        "worker": services / "dub_worker.py",
        "worker_entry": voxcpm / "dub_worker.py",
        "final_qa": voxcpm / "final_media_qa.py",
        "spatial_qa": voxcpm / "final_media_spatial_bed.py",
        "master": voxcpm / "examples" / "john_piper_z20py4yqhyq" / "master_constant_mix.py",
    }
    text = {name: _read(path) for name, path in paths.items()}
    missing = [name for name, value in text.items() if not value]
    if missing:
        return False, "не найдены canonical owners: " + ", ".join(missing)

    retired = (
        voxcpm / "generic_short_runtime.py",
        voxcpm / "generic_clean_direct_runtime.py",
        voxcpm / "_generic_clean_direct_runtime_base.py",
        voxcpm / "generic_clean_gemini_runtime.py",
        voxcpm / "generic_clean_custom_runtime.py",
        voxcpm / "generic_direct_checked_runtime.py",
        voxcpm / "voxcpm2_cpu_semantic_wrapper.py",
        voxcpm / "examples" / "john_piper_z20py4yqhyq" / "voxcpm2_cpu_semantic_wrapper.py",
        voxcpm / "semantic_tts_guard_v46.py",
        voxcpm / "professional_segmentation_v45.py",
    )
    present_retired = [path.relative_to(root).as_posix() for path in retired if path.exists()]
    if present_retired:
        return False, "остались retired runtime files: " + ", ".join(present_retired)

    source_scope = "\n".join(text.values())
    forbidden = [token for token in _FORBIDDEN_SOURCE_TOKENS if token in source_scope]
    if forbidden:
        return False, "canonical source содержит runtime surgery: " + ", ".join(forbidden)

    checks = {
        "runtime-contract": _has(
            text["runtime_contract"],
            'POLICY = "clean-runtime-contract-v2"',
            "def build_fingerprints(",
            "render_contract_sha256",
            "release_contract_sha256",
        ),
        "explicit-project-route": _has(
            text["project"],
            "class ProjectRoute:",
            "def default_project_route(",
            "def main(route: ProjectRoute | None = None)",
        ),
        "direct-source-owner": _has(
            text["direct"],
            'CLEAN_DIRECT_ROUTE_POLICY = "source-owned-clean-direct-v1"',
            "semantic_block_runtime.group_ready_srt",
            "direct_timing_guard.run_pre_model_guard",
            "clean.render_and_master(",
        ),
        "gemini-source-owner": _has(
            text["gemini"],
            "production.ProjectRoute(",
            "translate_groups=expressive_translation.translate_groups",
            "run_speech_and_master=_run_clean_speech_and_master",
        ),
        "custom-source-owner": _has(
            text["custom"],
            'POLICY = "source-owned-clean-custom-v1"',
            "production.ProjectRoute(",
            "validate_translation=strict_translation_payload.validate_full",
        ),
        "repair-source-owner": _has(
            text["repair"],
            "clean.render_and_master(",
            "_source_main()",
        ) and "legacy_repair._load_segments =" not in text["repair"],
        "semantic-blocks": _has(
            text["semantic_blocks"],
            f'POLICY = "{SEMANTIC_BLOCK_POLICY}"',
            "def group_ready_srt(",
            "def build_direct_segments(",
        ),
        "direct-cli-source-policy": _has(
            text["direct_cli"],
            "def _tempo_policy_penalty(",
            "def candidate_score(",
            "backend.open_session(",
        ) and "_direct_cli.candidate_score =" not in text["stable_cli"]
          and "_direct_cli.MAX_TEMPO =" not in text["stable_cli"],
        "backend-boundary": _has(
            text["backend_base"],
            f'BACKEND_COMMAND_POLICY = "{BACKEND_COMMAND_POLICY}"',
            f'BACKEND_ENVIRONMENT_POLICY = "{BACKEND_ENVIRONMENT_POLICY}"',
            f'PRODUCTION_CAPABILITY_POLICY = "{PRODUCTION_CAPABILITY_POLICY}"',
            "def build_renderer_command(",
            "def build_master_command(",
        ) and _has(
            text["backend_vox"],
            "class VoxCPM2Session:",
            "def build_renderer_command(",
            "def build_master_command(",
            "def open_session(",
        ),
        "worker-preflight": _has(
            text["preflight"],
            "def run(",
        ) and _has(
            text["worker"],
            "class WorkerDubStore(DubStore):",
            "def _execute_runner_job(",
            "CANCELLATION_POLICY =",
            "STORE_ROOT_POLICY =",
            "from tools.voxcpm2 import dub_job_preflight",
        ) and _has(
            text["worker_entry"],
            "from services.dub_worker import main",
        ),
        "final-media-qa": _has(text["final_qa"], "def verify_final_outputs(")
          and bool(text["spatial_qa"])
          and bool(text["master"]),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        return False, "canonical Dub quality-contract не прошёл: " + ", ".join(failed)
    return True, (
        "canonical source ownership active; no runtime module surgery; explicit ProjectRoute; "
        f"backend={BACKEND_COMMAND_POLICY}; environment={BACKEND_ENVIRONMENT_POLICY}; "
        f"capabilities={PRODUCTION_CAPABILITY_POLICY}; semantic={SEMANTIC_BLOCK_POLICY}"
    )


def _v67_quality_contract(repo: Path) -> tuple[bool, str]:
    return _v68_quality_contract(repo)


def _v66_quality_contract(repo: Path) -> tuple[bool, str]:
    return _v68_quality_contract(repo)


def _v65_quality_contract(repo: Path) -> tuple[bool, str]:
    return _v68_quality_contract(repo)


def _russian_only_master_contract(repo: Path) -> tuple[bool, str]:
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
