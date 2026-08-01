"""Deterministic non-production backend proving the engine boundary in CI."""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from services.speech_backends.base import (
    BackendAudioSpec,
    BackendCapabilities,
    BackendGenerationLengthPlan,
    BackendGenerationLengthRequest,
    BackendGenerationProfilePlan,
    BackendGenerationProfileRequest,
    BackendGenerationRequest,
    BackendIdentity,
    BackendProcessEnvironment,
    BackendRuntimePaths,
    BackendSessionConfig,
)

ADAPTER_POLICY = "deterministic-ci-speech-backend-v1"


class DeterministicSession:
    supports_continuation_context = False

    def __init__(self, sample_rate: int = 22_050) -> None:
        self.audio_spec = BackendAudioSpec(
            encode_sample_rate=None,
            output_sample_rate=int(sample_rate),
            seconds_per_step=None,
            cache_length=None,
        )

    def generate(self, request: BackendGenerationRequest) -> list[float]:
        if not isinstance(request, BackendGenerationRequest):
            raise TypeError("DeterministicSession ожидает BackendGenerationRequest.")
        duration = min(2.0, max(0.1, float(request.duration_budget or 0.5)))
        total = max(1, int(round(duration * self.audio_spec.output_sample_rate)))
        seed = int(request.seed or 0) + sum(ord(char) for char in request.text)
        frequency = 160.0 + float(seed % 320)
        return [
            0.08
            * math.sin(
                2.0
                * math.pi
                * frequency
                * index
                / self.audio_spec.output_sample_rate
            )
            for index in range(total)
        ]


class DeterministicSpeechBackend:
    """A second backend with deliberately different model/runtime assumptions."""

    backend_id = "deterministic-ci"
    aliases = ("deterministic", "ci-tone")
    adapter_policy = ADAPTER_POLICY

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            voice_cloning=False,
            reference_audio=False,
            deterministic_seed=True,
            style_instruction=False,
            cpu_inference=True,
            pcm_output=True,
            checkpointable_segments=True,
            continuation_context=False,
        )

    def discover_model(self, archive_root: Path) -> Path:
        return Path(archive_root).resolve()

    def identity(self, archive_root: Path) -> BackendIdentity:
        return BackendIdentity(
            backend_id=self.backend_id,
            family="deterministic-contract-fixture",
            adapter_policy=self.adapter_policy,
            model_path=str(Path(archive_root).resolve()),
            runtime_module="services.speech_backends.deterministic_runtime",
            parameter_schema=("sample_rate", "base_seed"),
            output_contract="mono-pcm-wav-segment-v1",
        )

    def process_environment(
        self,
        request: dict[str, Any],
        *,
        base_environment: Mapping[str, str] | None = None,
    ) -> BackendProcessEnvironment:
        del request, base_environment
        return BackendProcessEnvironment(
            backend_id=self.backend_id,
            set_values=(
                ("PYTHONUTF8", "1"),
                ("PYTHONIOENCODING", "utf-8"),
            ),
            removed_keys=("CUDA_VISIBLE_DEVICES",),
        )

    def plan_generation_length(
        self,
        audio_spec: BackendAudioSpec,
        request: BackendGenerationLengthRequest,
    ) -> BackendGenerationLengthPlan:
        if not isinstance(audio_spec, BackendAudioSpec):
            raise TypeError("Deterministic planner ожидает BackendAudioSpec.")
        if not isinstance(request, BackendGenerationLengthRequest):
            raise TypeError(
                "Deterministic planner ожидает BackendGenerationLengthRequest."
            )
        return BackendGenerationLengthPlan(
            backend_id=self.backend_id,
            duration_budget=request.duration_budget,
            attempt=request.attempt,
            backend_options={},
            metadata={
                "policy": "duration-is-direct-pcm-budget-v1",
                "length_request": request.as_dict(),
                "sample_rate": audio_spec.output_sample_rate,
            },
        )

    def plan_generation_profile(
        self,
        request: BackendGenerationProfileRequest,
    ) -> BackendGenerationProfilePlan:
        if not isinstance(request, BackendGenerationProfileRequest):
            raise TypeError(
                "Deterministic profile planner ожидает BackendGenerationProfileRequest."
            )
        return BackendGenerationProfilePlan(
            backend_id=self.backend_id,
            attempt=request.attempt,
            backend_options={},
            metadata={
                "policy": "deterministic-profile-is-noop-v1",
                "profile_request": request.as_dict(),
            },
        )

    def open_session(self, config: BackendSessionConfig) -> DeterministicSession:
        if not isinstance(config, BackendSessionConfig):
            raise TypeError("Deterministic backend ожидает BackendSessionConfig.")
        raw = config.options.get("sample_rate", 22_050)
        if isinstance(raw, bool):
            raise ValueError("sample_rate не может быть bool.")
        sample_rate = int(raw)
        if not 8_000 <= sample_rate <= 96_000:
            raise ValueError("sample_rate должен быть в диапазоне 8000..96000.")
        return DeterministicSession(sample_rate)

    def runtime_paths(
        self,
        repo_root: Path,
        request: dict[str, Any],
    ) -> BackendRuntimePaths:
        repo = Path(repo_root).resolve()
        entrypoint = repo / "services" / "speech_backends" / "deterministic_runtime.py"
        archive = Path(
            str(request.get("deterministic_archive") or repo)
        ).expanduser().resolve()
        return BackendRuntimePaths(
            backend_id=self.backend_id,
            repo_root=repo,
            cpu_python=Path(sys.executable).resolve(),
            archive_root=archive,
            renderer_entrypoint=entrypoint,
            master_entrypoint=entrypoint,
            import_modules=("services.speech_backends.deterministic_runtime",),
            renderer_module="services.speech_backends.deterministic_runtime",
            master_module="services.speech_backends.deterministic_runtime",
            final_qa_module="services.media_masters",
        )

    def build_renderer_command(
        self,
        runtime: BackendRuntimePaths,
        *,
        values: dict[str, Any],
    ) -> list[str]:
        return [
            str(runtime.cpu_python),
            str(runtime.renderer_entrypoint),
            "--segments-json",
            str(values["segments_json"]),
            "--output",
            str(values["timeline"]),
            "--video-duration",
            str(values["duration"]),
            "--sample-rate",
            str(values.get("sample_rate") or 22_050),
        ]

    def build_master_command(
        self,
        runtime: BackendRuntimePaths,
        *,
        values: dict[str, Any],
    ) -> list[str]:
        del runtime, values
        raise RuntimeError(
            "Speech backend больше не владеет media master; используйте services.media_masters."
        )


__all__ = [
    "ADAPTER_POLICY",
    "DeterministicSession",
    "DeterministicSpeechBackend",
]
