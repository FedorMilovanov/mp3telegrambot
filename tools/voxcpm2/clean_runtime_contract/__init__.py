#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility facade for the clean runtime fingerprint contract.

The stable contract remains in ``clean_runtime_contract.py``. This package keeps
its API, routes model discovery through the generic speech-backend registry and
extends fingerprints with every active facade, repair gate and release contract.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from services.speech_backends import (
    DEFAULT_BACKEND_ID,
    default_backend,
    get_backend,
)

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "clean_runtime_contract.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._clean_runtime_contract_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить clean runtime contract: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

_FACADE_RENDER_MODULES = (
    "services/speech_backends/__init__.py",
    "services/speech_backends/base.py",
    "services/speech_backends/registry.py",
    "services/speech_backends/voxcpm2.py",
    "tools/voxcpm2/clean_runtime_contract/__init__.py",
    "tools/voxcpm2/clean_production_core/__init__.py",
    "tools/voxcpm2/generic_project_runtime/__init__.py",
    "tools/voxcpm2/generic_clean_direct_runtime/__init__.py",
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
    "tools/voxcpm2/final_encoded_delivery_qa.py",
)
_legacy._RENDER_MODULES = tuple(
    dict.fromkeys((*_legacy._RENDER_MODULES, *_FACADE_RENDER_MODULES))
)
_release_base = tuple(
    name for name in _legacy._RELEASE_MODULES if name not in _RETIRED_RELEASE_MODULES
)
_legacy._RELEASE_MODULES = tuple(
    dict.fromkeys((*_release_base, *_FACADE_RELEASE_MODULES))
)

BACKEND_SELECTION_POLICY = "explicit-request-speech-backend-v1"
_BACKEND = default_backend()
if _BACKEND.backend_id != DEFAULT_BACKEND_ID:
    raise RuntimeError("Default speech backend registry рассинхронизирован.")
# Legacy _model_manifest resolves this global at call time. Routing it through
# the adapter is the first production seam for future engines.
_legacy.discover_model = _BACKEND.discover_model
_legacy_build_fingerprints = _legacy.build_fingerprints
_legacy_normalize_settings = _legacy.normalize_settings


def normalize_settings(
    request: dict[str, Any],
    *,
    duration: Any,
) -> dict[str, Any]:
    settings = dict(_legacy_normalize_settings(request, duration=duration))
    raw_backend = request.get("speech_backend") or DEFAULT_BACKEND_ID
    backend = get_backend(raw_backend)
    if backend.backend_id != DEFAULT_BACKEND_ID:
        raise RuntimeError(
            "Speech backend зарегистрирован, но ещё не подключён к clean renderer: "
            f"{backend.backend_id}."
        )
    settings["speech_backend"] = backend.backend_id
    settings["speech_backend_policy"] = BACKEND_SELECTION_POLICY
    return settings


def build_fingerprints(
    *,
    repo: Path,
    archive: Path,
    cpu_python: Path,
) -> dict[str, Any]:
    result = dict(
        _legacy_build_fingerprints(
            repo=repo,
            archive=archive,
            cpu_python=cpu_python,
        )
    )
    backend_payload = {
        "identity": _BACKEND.identity(Path(archive)).as_dict(),
        "capabilities": _BACKEND.capabilities().as_dict(),
        "selection_policy": BACKEND_SELECTION_POLICY,
    }
    render = dict(result.get("render") or {})
    render["speech_backend"] = backend_payload
    result["render"] = render
    result["render_contract_sha256"] = _legacy._digest(render)
    result["speech_backend"] = backend_payload
    return result


_legacy.normalize_settings = normalize_settings
_legacy.build_fingerprints = build_fingerprints

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)

# Re-apply facade overrides after exporting the legacy namespace.
globals()["normalize_settings"] = normalize_settings
globals()["build_fingerprints"] = build_fingerprints
_RENDER_MODULES = _legacy._RENDER_MODULES
_RELEASE_MODULES = _legacy._RELEASE_MODULES

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {
        "BACKEND_SELECTION_POLICY",
        "DEFAULT_BACKEND_ID",
        "_RENDER_MODULES",
        "_RELEASE_MODULES",
        "build_fingerprints",
        "normalize_settings",
    }
)
