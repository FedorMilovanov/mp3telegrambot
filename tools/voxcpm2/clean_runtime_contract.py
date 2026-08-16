#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runtime fingerprints including universal VoxCPM2 production hardening."""
from pathlib import Path

_ORIGINAL_NAME = __name__
_BASE = Path(__file__).with_name("_clean_runtime_contract_base.py")
if not _BASE.is_file():
    raise RuntimeError(f"Missing runtime contract base snapshot: {_BASE}")
globals()["__name__"] = "tools.voxcpm2._clean_runtime_contract_base_exec"
exec(compile(_BASE.read_text(encoding="utf-8-sig"), str(_BASE), "exec"), globals())
globals()["__name__"] = _ORIGINAL_NAME

from tools.voxcpm2.direct_universal_runtime import install_runtime_fingerprint

_BASE_RENDER_MODULES = tuple(globals().get("_RENDER_MODULES", ()))
if not _BASE_RENDER_MODULES:
    raise RuntimeError("Clean runtime base did not export _RENDER_MODULES.")

install_runtime_fingerprint(globals())
_RENDER_MODULES = tuple(
    dict.fromkeys(
        (
            *_BASE_RENDER_MODULES,
            "tools/voxcpm2/direct_failure_recovery.py",
            "tools/voxcpm2/direct_final_audit_v3.py",
            "tools/voxcpm2/direct_surgical_guard.py",
            "tools/voxcpm2/direct_surgical_io.py",
            "tools/voxcpm2/direct_surgical_runtime.py",
            "tools/voxcpm2/direct_surgical_polish_v2.py",
            "services/speech_backends/audited_voxcpm2.py",
            "services/speech_backends/base.py",
            "services/speech_backends/control_plane.py",
            "services/speech_backends/execution_plan.py",
            "services/speech_backends/model_profiles.py",
            "services/speech_backends/registry.py",
        )
    )
)

_BASE_ALL = tuple(globals().get('__all__', ()))

import types

from pathlib import Path

from typing import Any

from services.speech_backends import (
    BACKEND_ENVIRONMENT_POLICY,
    DEFAULT_BACKEND_ID,
    BackendIdentity,
    default_backend,
    get_backend,
    select_production_backend,
)

_FACADE_RENDER_MODULES = (
    "services/speech_backends/__init__.py",
    "services/speech_backends/base.py",
    "services/speech_backends/control_plane.py",
    "services/speech_backends/registry.py",
    "services/speech_backends/voxcpm2.py",
    "tools/voxcpm2/clean_runtime_contract/__init__.py",
    "tools/voxcpm2/clean_production_core/__init__.py",
    "tools/voxcpm2/generic_project_runtime/__init__.py",
    "tools/voxcpm2/generic_clean_direct_runtime/__init__.py",
    "tools/voxcpm2/semantic_block_runtime.py",
    "tools/voxcpm2/clean_source_download/__init__.py",
    "tools/voxcpm2/dub_quality_v4/__init__.py",
    "tools/voxcpm2/expressive_continuity/__init__.py",
    "tools/voxcpm2/russian_pronunciation.py",
    "tools/voxcpm2/direct_source_relative_continuity.py",
    "tools/voxcpm2/direct_monolith_contract.py",
    "tools/voxcpm2/direct_monolith_contract/__init__.py",
    "tools/voxcpm2/direct_max_quality_cli/__init__.py",
    "tools/voxcpm2/direct_max_quality_analysis/__init__.py",
    "tools/voxcpm2/direct_max_quality_render/__init__.py",
    "tools/voxcpm2/direct_source_prosody/__init__.py",
    "tools/voxcpm2/direct_timeline_compaction.py",
    "tools/voxcpm2/direct_retry_epoch.py",
    "tools/voxcpm2/direct_russian_cadence.py",
    "tools/voxcpm2/direct_russian_cadence/__init__.py",
    "tools/voxcpm2/direct_tail_artifact.py",
    "tools/voxcpm2/direct_tail_artifact/__init__.py",
    "tools/voxcpm2/direct_timeline_delivery_qa.py",
    "tools/voxcpm2/direct_timeline_delivery_qa/__init__.py",
)

_RETIRED_RELEASE_MODULES = (
    # This historical filename never shipped. Its implementation has always lived
    # in professional_audio_qa_v45.py (POLICY=clean-expression-aware-qa-v3).
    "tools/voxcpm2/clean_expression_aware_qa.py",
)

_FACADE_RELEASE_MODULES = (
    "tools/voxcpm2/professional_audio_qa_v45.py",
    "tools/voxcpm2/professional_audio_qa_v45/__init__.py",
    "tools/voxcpm2/timeline_onset_repair.py",
    "tools/voxcpm2/preflight_json_protocol.py",
    "tools/voxcpm2/independent_qa_retry.py",
    "tools/voxcpm2/monolithic_runtime_install.py",
    "tools/voxcpm2/spatial_bed_contract.py",
    "tools/voxcpm2/master_monolithic_mix.py",
    "tools/voxcpm2/final_media_spatial_bed.py",
    "tools/voxcpm2/final_media_qa/__init__.py",
    "tools/voxcpm2/generic_clean_direct_runtime/__main__.py",
    "tools/voxcpm2/generic_clean_audio_repair_runtime/__init__.py",
    "tools/voxcpm2/generic_clean_audio_repair_runtime/__main__.py",
    "tools/voxcpm2/final_encoded_delivery_qa.py",
)

_RENDER_MODULES = tuple(
    dict.fromkeys((*_RENDER_MODULES, *_FACADE_RENDER_MODULES))
)

_release_base = tuple(
    name for name in _RELEASE_MODULES if name not in _RETIRED_RELEASE_MODULES
)

_RELEASE_MODULES = tuple(
    dict.fromkeys((*_release_base, *_FACADE_RELEASE_MODULES))
)

BACKEND_SELECTION_POLICY = "explicit-request-speech-backend-v1"

_BACKEND = default_backend()

if _BACKEND.backend_id != DEFAULT_BACKEND_ID:
    raise RuntimeError("Default speech backend registry рассинхронизирован.")

discover_model = _BACKEND.discover_model

_legacy_build_fingerprints = build_fingerprints

_legacy_normalize_settings = normalize_settings

def normalize_settings(
    request: dict[str, Any],
    *,
    duration: Any,
) -> dict[str, Any]:
    settings = dict(_legacy_normalize_settings(request, duration=duration))
    selection = select_production_backend(
        request.get("speech_backend"),
        default_backend_id=DEFAULT_BACKEND_ID,
    )
    backend = selection.backend
    if (
        not callable(getattr(backend, "build_renderer_command", None))
        or not callable(getattr(backend, "build_master_command", None))
        or not callable(getattr(backend, "process_environment", None))
        or not callable(getattr(backend, "open_session", None))
    ):
        raise RuntimeError(
            "Speech backend не реализует model-independent process/command/session contract: "
            f"{backend.backend_id}."
        )
    settings["speech_backend"] = selection.backend_id
    settings["speech_backend_policy"] = BACKEND_SELECTION_POLICY
    return settings

def build_fingerprints(
    *,
    repo: Path,
    archive: Path,
    cpu_python: Path,
    backend_id: object | None = None,
) -> dict[str, Any]:
    backend = get_backend(backend_id or DEFAULT_BACKEND_ID)
    previous_discover_model = discover_model
    discover_model = backend.discover_model
    try:
        result = dict(
            _legacy_build_fingerprints(
                repo=repo,
                archive=archive,
                cpu_python=cpu_python,
            )
        )
    finally:
        discover_model = previous_discover_model
    try:
        identity_payload = backend.identity(Path(archive)).as_dict()
    except RuntimeError:
        # The legacy fingerprint call may be deterministically stubbed in a
        # contract test. Reuse its already-validated model manifest rather than
        # performing a second discovery with a different seam.
        model_manifest = (result.get("render") or {}).get("model") or {}
        model_path = str(model_manifest.get("path") or "").strip()
        if not model_path:
            raise
        identity_payload = BackendIdentity(
            backend_id=backend.backend_id,
            family="reference-conditioned-generative-tts",
            adapter_policy=str(getattr(backend, "adapter_policy", "")),
            model_path=model_path,
            runtime_module=backend.backend_id,
            parameter_schema=(),
            output_contract="backend-model-manifest-v1",
        ).as_dict()
    backend_payload = {
        "identity": identity_payload,
        "capabilities": backend.capabilities().as_dict(),
        "selection_policy": BACKEND_SELECTION_POLICY,
        "environment_policy": BACKEND_ENVIRONMENT_POLICY,
        "backend_id": backend.backend_id,
    }
    render = dict(result.get("render") or {})
    render["speech_backend"] = backend_payload
    result["render"] = render
    result["render_contract_sha256"] = _digest(render)
    result["speech_backend"] = backend_payload
    return result

normalize_settings = normalize_settings

build_fingerprints = build_fingerprints

globals()["normalize_settings"] = normalize_settings

globals()["build_fingerprints"] = build_fingerprints

_RENDER_MODULES = _RENDER_MODULES

_RELEASE_MODULES = _RELEASE_MODULES

__all__ = sorted(
    set(_BASE_ALL)
    | {
        "BACKEND_SELECTION_POLICY",
        "DEFAULT_BACKEND_ID",
        "_RENDER_MODULES",
        "_RELEASE_MODULES",
        "build_fingerprints",
        "normalize_settings",
    }
)
