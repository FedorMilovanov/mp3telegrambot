#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VoxCPM2 adapter implementing the generic speech-backend contract."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from services.speech_backends.base import (
    BackendCapabilities,
    BackendIdentity,
    BackendRuntimePaths,
)

ADAPTER_POLICY = "voxcpm2-speech-backend-adapter-v4"
MASTER_SELECTION_POLICY = "translation-mode-specific-master-entrypoint-v1"

_DEFAULT_CPU_VENV = r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv"
_DEFAULT_ARCHIVE = r"C:\AI-Archive\VoxCPM2-paused-RTX3060"
_RENDERER_MODULE = (
    "tools.voxcpm2.examples.john_piper_z20py4yqhyq.voxcpm2_cpu_shorts_production"
)
_LEGACY_MASTER_MODULE = (
    "tools.voxcpm2.examples.john_piper_z20py4yqhyq.master_constant_mix"
)
_DIRECT_MASTER_MODULE = "tools.voxcpm2.master_monolithic_mix"
_FINAL_QA_MODULE = "tools.voxcpm2.final_media_qa"


def _request_path(request: dict[str, Any], key: str, default: str) -> Path:
    value = default if key not in request or request[key] is None else request[key]
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise RuntimeError(f"Speech backend request.{key} должен быть непустым путём.")
    return Path(value.strip()).expanduser().resolve()


def _master_contract(
    repo: Path,
    request: dict[str, Any],
) -> tuple[Path, str]:
    mode = str(request.get("translation_mode") or "").casefold().strip()
    if mode == "direct":
        return (
            repo / "tools" / "voxcpm2" / "master_monolithic_mix.py",
            _DIRECT_MASTER_MODULE,
        )
    example = repo / "tools" / "voxcpm2" / "examples" / "john_piper_z20py4yqhyq"
    return example / "master_constant_mix.py", _LEGACY_MASTER_MODULE


class VoxCPM2Backend:
    backend_id = "voxcpm2"
    aliases = ("vox-cpm2", "openbmb-voxcpm2", "openbmb")
    adapter_policy = ADAPTER_POLICY

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            voice_cloning=True,
            reference_audio=True,
            deterministic_seed=True,
            style_instruction=True,
            cpu_inference=True,
            pcm_output=True,
            checkpointable_segments=True,
        )

    def discover_model(self, archive_root: Path) -> Path:
        from tools.voxcpm2.direct_max_quality_io import discover_model

        return discover_model(Path(archive_root))

    def identity(self, archive_root: Path) -> BackendIdentity:
        model = self.discover_model(Path(archive_root)).resolve()
        return BackendIdentity(
            backend_id=self.backend_id,
            family="reference-conditioned-generative-tts",
            adapter_policy=self.adapter_policy,
            model_path=str(model),
            runtime_module="voxcpm",
            parameter_schema=(
                "threads",
                "steps",
                "cfg",
                "base_seed",
                "cache_length",
            ),
            output_contract="mono-pcm-wav-segment-v1",
        )

    def runtime_paths(
        self,
        repo_root: Path,
        request: dict[str, Any],
    ) -> BackendRuntimePaths:
        repo = Path(repo_root).resolve()
        venv = _request_path(request, "cpu_venv", _DEFAULT_CPU_VENV)
        archive = _request_path(request, "vox_archive", _DEFAULT_ARCHIVE)
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        example = repo / "tools" / "voxcpm2" / "examples" / "john_piper_z20py4yqhyq"
        master_entrypoint, master_module = _master_contract(repo, request)
        import_modules = (
            _FINAL_QA_MODULE,
            master_module,
            _RENDERER_MODULE,
            "voxcpm",
            "torch",
            "soundfile",
        )
        return BackendRuntimePaths(
            backend_id=self.backend_id,
            repo_root=repo,
            cpu_python=python,
            archive_root=archive,
            renderer_entrypoint=example / "voxcpm2_cpu_shorts_production.py",
            master_entrypoint=master_entrypoint,
            import_modules=import_modules,
            renderer_module=_RENDERER_MODULE,
            master_module=master_module,
            final_qa_module=_FINAL_QA_MODULE,
        )


__all__ = [
    "ADAPTER_POLICY",
    "MASTER_SELECTION_POLICY",
    "VoxCPM2Backend",
]
