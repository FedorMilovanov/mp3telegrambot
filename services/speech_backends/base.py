#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Model-independent contracts for speech synthesis engines."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

BACKEND_CONTRACT_POLICY = "speech-backend-contract-v1"


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


@runtime_checkable
class SpeechBackend(Protocol):
    backend_id: str
    aliases: tuple[str, ...]
    adapter_policy: str

    def capabilities(self) -> BackendCapabilities: ...

    def discover_model(self, archive_root: Path) -> Path: ...

    def identity(self, archive_root: Path) -> BackendIdentity: ...


__all__ = [
    "BACKEND_CONTRACT_POLICY",
    "BackendCapabilities",
    "BackendIdentity",
    "SpeechBackend",
]
