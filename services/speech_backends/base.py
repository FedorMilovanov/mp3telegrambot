#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Model-independent contracts for speech synthesis engines."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

BACKEND_CONTRACT_POLICY = "speech-backend-contract-v1"
BACKEND_RUNTIME_PATH_POLICY = "speech-backend-runtime-paths-v1"
BACKEND_COMMAND_POLICY = "speech-backend-command-builder-v1"
BACKEND_ENVIRONMENT_POLICY = "speech-backend-process-environment-v1"


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
class BackendProcessEnvironment:
    """Backend-owned child-process environment transformation.

    Shared orchestration must not decide whether a model needs CPU-only mode,
    CUDA visibility, offline Hub access, or tokenizer thread settings. The
    adapter returns the complete environment transformation and the core only
    applies it to the child process.
    """

    backend_id: str
    set_values: tuple[tuple[str, str], ...]
    removed_keys: tuple[str, ...]

    def as_dict(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        result = {str(key): str(value) for key, value in (base or {}).items()}
        for key in self.removed_keys:
            result.pop(key, None)
        result.update({str(key): str(value) for key, value in self.set_values})
        return result

    def as_metadata(self) -> dict[str, Any]:
        return {
            "backend_id": self.backend_id,
            "set_values": {key: value for key, value in self.set_values},
            "removed_keys": list(self.removed_keys),
            "environment_policy": BACKEND_ENVIRONMENT_POLICY,
        }


@dataclass(frozen=True)
class BackendAudioSpec:
    """Audio/runtime facts exposed by a loaded synthesis engine."""

    encode_sample_rate: int
    output_sample_rate: int
    seconds_per_step: float
    cache_length: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "encode_sample_rate": int(self.encode_sample_rate),
            "output_sample_rate": int(self.output_sample_rate),
            "seconds_per_step": float(self.seconds_per_step),
            "cache_length": int(self.cache_length),
        }


@runtime_checkable
class BackendSynthesisSession(Protocol):
    audio_spec: BackendAudioSpec

    def generate(
        self,
        *,
        text: str,
        reference: Path,
        cfg: float,
        steps: int,
        min_len: int,
        max_len: int,
        seed: int,
        continuation_reference: Path | None = None,
        continuation_text: str = "",
    ) -> Any: ...


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
            "runtime_path_policy": BACKEND_RUNTIME_PATH_POLICY,
            "command_policy": BACKEND_COMMAND_POLICY,
            "environment_policy": BACKEND_ENVIRONMENT_POLICY,
        }


@runtime_checkable
class SpeechBackend(Protocol):
    backend_id: str
    aliases: tuple[str, ...]
    adapter_policy: str

    def capabilities(self) -> BackendCapabilities: ...

    def discover_model(self, archive_root: Path) -> Path: ...

    def identity(self, archive_root: Path) -> BackendIdentity: ...

    def process_environment(
        self,
        request: dict[str, Any],
        *,
        base_environment: Mapping[str, str] | None = None,
    ) -> BackendProcessEnvironment: ...

    def open_session(
        self,
        model_path: Path,
        *,
        cache_length: int,
        torch_module: Any,
    ) -> BackendSynthesisSession: ...

    def runtime_paths(
        self,
        repo_root: Path,
        request: dict[str, Any],
    ) -> BackendRuntimePaths: ...

    def build_renderer_command(
        self,
        runtime: BackendRuntimePaths,
        *,
        values: dict[str, Any],
    ) -> list[str]: ...

    def build_master_command(
        self,
        runtime: BackendRuntimePaths,
        *,
        values: dict[str, Any],
    ) -> list[str]: ...


__all__ = [
    "BACKEND_COMMAND_POLICY",
    "BACKEND_CONTRACT_POLICY",
    "BACKEND_ENVIRONMENT_POLICY",
    "BACKEND_RUNTIME_PATH_POLICY",
    "BackendAudioSpec",
    "BackendCapabilities",
    "BackendIdentity",
    "BackendProcessEnvironment",
    "BackendRuntimePaths",
    "BackendSynthesisSession",
    "SpeechBackend",
]
