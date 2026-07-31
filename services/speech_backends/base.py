#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Model-independent contracts for speech synthesis engines."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

BACKEND_CONTRACT_POLICY = "speech-backend-contract-v2"


@dataclass(frozen=True)
class BackendCapabilities:
    voice_cloning: bool
    reference_audio: bool
    deterministic_seed: bool
    style_instruction: bool
    cpu_inference: bool
    pcm_output: bool
    checkpointable_segments: bool

    def as_dict(self) -> dict[str, bool]:
        return {key: bool(value) for key, value in asdict(self).items()}


@dataclass(frozen=True)
class BackendIdentity:
    backend_id: str
    family: str
    adapter_policy: str
    model_path: str
    runtime_module: str
    parameter_schema: tuple[str, ...]
    output_contract: str

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["parameter_schema"] = list(self.parameter_schema)
        payload["contract_policy"] = BACKEND_CONTRACT_POLICY
        return payload


@dataclass(frozen=True)
class BackendRuntimePaths:
    """Exact interpreter/model/entrypoint paths owned by one speech backend."""

    backend_id: str
    repo_root: Path
    cpu_python: Path
    archive_root: Path
    renderer_entrypoint: Path
    master_entrypoint: Path
    import_modules: tuple[str, ...]
    renderer_module: str
    master_module: str
    final_qa_module: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend_id": self.backend_id,
            "repo_root": str(self.repo_root),
            "cpu_python": str(self.cpu_python),
            "archive_root": str(self.archive_root),
            "renderer_entrypoint": str(self.renderer_entrypoint),
            "master_entrypoint": str(self.master_entrypoint),
            "import_modules": list(self.import_modules),
            "renderer_module": self.renderer_module,
            "master_module": self.master_module,
            "final_qa_module": self.final_qa_module,
            "contract_policy": BACKEND_CONTRACT_POLICY,
        }


@runtime_checkable
class SpeechBackend(Protocol):
    backend_id: str
    aliases: tuple[str, ...]
    adapter_policy: str

    def capabilities(self) -> BackendCapabilities: ...

    def discover_model(self, archive_root: Path) -> Path: ...

    def identity(self, archive_root: Path) -> BackendIdentity: ...

    def runtime_paths(
        self,
        repo_root: Path,
        request: dict[str, Any],
    ) -> BackendRuntimePaths: ...


__all__ = [
    "BACKEND_CONTRACT_POLICY",
    "BackendCapabilities",
    "BackendIdentity",
    "BackendRuntimePaths",
    "SpeechBackend",
]
