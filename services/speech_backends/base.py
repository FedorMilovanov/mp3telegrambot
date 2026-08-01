#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Model-independent contracts for speech synthesis engines."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
import math
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

BACKEND_CONTRACT_POLICY = "speech-backend-contract-v3"
BACKEND_RUNTIME_PATH_POLICY = "speech-backend-runtime-paths-v1"
BACKEND_COMMAND_POLICY = "speech-backend-command-builder-v1"
BACKEND_ENVIRONMENT_POLICY = "speech-backend-process-environment-v1"
PRODUCTION_CAPABILITY_POLICY = "production-speech-capability-gate-v2"
GENERATION_REQUEST_POLICY = "model-neutral-generation-request-v1"
GENERATION_LENGTH_POLICY = "model-neutral-generation-length-plan-v1"
SESSION_CONFIG_POLICY = "model-neutral-session-config-v1"
REQUIRED_PRODUCTION_CAPABILITIES = (
    "voice_cloning",
    "reference_audio",
    "deterministic_seed",
    "pcm_output",
    "checkpointable_segments",
)


@dataclass(frozen=True)
class BackendCapabilities:
    voice_cloning: bool
    reference_audio: bool
    deterministic_seed: bool
    style_instruction: bool
    cpu_inference: bool
    pcm_output: bool
    checkpointable_segments: bool
    continuation_context: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {key: bool(value) for key, value in asdict(self).items()}

    def missing(
        self,
        required: tuple[str, ...] = REQUIRED_PRODUCTION_CAPABILITIES,
    ) -> tuple[str, ...]:
        values = self.as_dict()
        return tuple(name for name in required if values.get(name) is not True)


@dataclass(frozen=True)
class BackendProcessEnvironment:
    """Backend-owned child-process environment transformation."""

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
    """Audio facts exposed by a loaded engine.

    ``encode_sample_rate``, ``seconds_per_step`` and ``cache_length`` are
    optional because API, autoregressive and non-VAE engines need not expose
    VoxCPM-style internals. ``output_sample_rate`` is the only universal fact.
    """

    encode_sample_rate: int | None
    output_sample_rate: int
    seconds_per_step: float | None
    cache_length: int | None

    def __post_init__(self) -> None:
        if isinstance(self.output_sample_rate, bool) or int(self.output_sample_rate) <= 0:
            raise ValueError("output_sample_rate должен быть положительным целым числом.")
        for name, value in (
            ("encode_sample_rate", self.encode_sample_rate),
            ("cache_length", self.cache_length),
        ):
            if value is not None and (isinstance(value, bool) or int(value) <= 0):
                raise ValueError(f"{name} должен быть положительным целым числом или None.")
        if self.seconds_per_step is not None and (
            not math.isfinite(float(self.seconds_per_step))
            or float(self.seconds_per_step) <= 0.0
        ):
            raise ValueError("seconds_per_step должен быть конечным числом > 0 или None.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "encode_sample_rate": (
                int(self.encode_sample_rate)
                if self.encode_sample_rate is not None
                else None
            ),
            "output_sample_rate": int(self.output_sample_rate),
            "seconds_per_step": (
                float(self.seconds_per_step)
                if self.seconds_per_step is not None
                else None
            ),
            "cache_length": int(self.cache_length) if self.cache_length is not None else None,
        }


@dataclass(frozen=True)
class BackendGenerationLengthPlan:
    """Backend-owned translation from duration facts to model-specific options."""

    backend_id: str
    duration_budget: float
    attempt: int
    backend_options: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        backend_id = str(self.backend_id or "").casefold().strip()
        if not backend_id:
            raise ValueError("BackendGenerationLengthPlan.backend_id не может быть пустым.")
        if not math.isfinite(float(self.duration_budget)) or float(self.duration_budget) <= 0.0:
            raise ValueError("duration_budget должен быть конечным числом > 0.")
        if isinstance(self.attempt, bool) or int(self.attempt) < 1:
            raise ValueError("attempt должен быть целым числом >= 1.")
        object.__setattr__(self, "backend_id", backend_id)
        object.__setattr__(self, "duration_budget", float(self.duration_budget))
        object.__setattr__(self, "attempt", int(self.attempt))
        object.__setattr__(self, "backend_options", dict(self.backend_options))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend_id": self.backend_id,
            "duration_budget": self.duration_budget,
            "attempt": self.attempt,
            "backend_options": dict(self.backend_options),
            "metadata": dict(self.metadata),
            "generation_length_policy": GENERATION_LENGTH_POLICY,
        }


@dataclass(frozen=True)
class BackendGenerationRequest:
    """Engine-neutral synthesis request passed to a backend session."""

    text: str
    reference_audio: Path
    seed: int | None = None
    duration_budget: float | None = None
    style_instruction: str = ""
    continuation_reference: Path | None = None
    continuation_text: str = ""
    backend_options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        text = str(self.text or "").strip()
        if not text:
            raise ValueError("BackendGenerationRequest.text не может быть пустым.")
        reference = Path(self.reference_audio)
        if "\x00" in str(reference):
            raise ValueError("BackendGenerationRequest.reference_audio содержит NUL.")
        if self.seed is not None and isinstance(self.seed, bool):
            raise ValueError("BackendGenerationRequest.seed не может быть bool.")
        if self.seed is not None:
            int(self.seed)
        if self.duration_budget is not None and (
            not math.isfinite(float(self.duration_budget))
            or float(self.duration_budget) <= 0.0
        ):
            raise ValueError("duration_budget должен быть конечным числом > 0 или None.")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "reference_audio", reference)
        if self.continuation_reference is not None:
            object.__setattr__(
                self,
                "continuation_reference",
                Path(self.continuation_reference),
            )
        object.__setattr__(self, "backend_options", dict(self.backend_options))

    def option_int(self, name: str, *, default: int, low: int, high: int) -> int:
        value = self.backend_options.get(name, default)
        if isinstance(value, bool):
            raise ValueError(f"backend_options.{name} не может быть bool.")
        try:
            result = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"backend_options.{name} должен быть целым числом.") from exc
        if not low <= result <= high:
            raise ValueError(f"backend_options.{name} должен быть в диапазоне {low}..{high}.")
        return result

    def option_float(
        self,
        name: str,
        *,
        default: float,
        low: float,
        high: float,
    ) -> float:
        value = self.backend_options.get(name, default)
        if isinstance(value, bool):
            raise ValueError(f"backend_options.{name} не может быть bool.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"backend_options.{name} должен быть числом.") from exc
        if not math.isfinite(result) or not low <= result <= high:
            raise ValueError(f"backend_options.{name} должен быть в диапазоне {low}..{high}.")
        return result


@dataclass(frozen=True)
class BackendSessionConfig:
    """Generic session configuration; model-specific knobs stay in options."""

    model_path: Path
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        model_path = Path(self.model_path)
        if "\x00" in str(model_path):
            raise ValueError("BackendSessionConfig.model_path содержит NUL.")
        object.__setattr__(self, "model_path", model_path)
        object.__setattr__(self, "options", dict(self.options))


@runtime_checkable
class BackendSynthesisSession(Protocol):
    audio_spec: BackendAudioSpec
    supports_continuation_context: bool

    def generate(self, request: BackendGenerationRequest) -> Any: ...


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

    def plan_generation_length(
        self,
        audio_spec: BackendAudioSpec,
        *,
        duration_budget: float,
        attempt: int,
        previous_output_durations: tuple[float, ...] = (),
    ) -> BackendGenerationLengthPlan: ...

    def open_session(self, config: BackendSessionConfig) -> BackendSynthesisSession: ...

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
    "GENERATION_LENGTH_POLICY",
    "GENERATION_REQUEST_POLICY",
    "PRODUCTION_CAPABILITY_POLICY",
    "REQUIRED_PRODUCTION_CAPABILITIES",
    "SESSION_CONFIG_POLICY",
    "BackendAudioSpec",
    "BackendCapabilities",
    "BackendGenerationLengthPlan",
    "BackendGenerationRequest",
    "BackendIdentity",
    "BackendProcessEnvironment",
    "BackendRuntimePaths",
    "BackendSessionConfig",
    "BackendSynthesisSession",
    "SpeechBackend",
]
