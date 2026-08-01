#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VoxCPM2 adapter implementing the generic speech-backend contract."""
from __future__ import annotations

import inspect
import math
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from services.speech_backends.base import (
    BackendAudioSpec,
    BackendCapabilities,
    BackendGenerationLengthPlan,
    BackendGenerationRequest,
    BackendIdentity,
    BackendProcessEnvironment,
    BackendRuntimePaths,
    BackendSessionConfig,
    BackendSynthesisSession,
)

ADAPTER_POLICY = "voxcpm2-speech-backend-adapter-v8"
GENERATION_CALL_POLICY = "typed-backend-generation-request-v1"
GENERATION_LENGTH_POLICY = "voxcpm2-duration-to-token-window-v1"
SESSION_CALL_POLICY = "typed-backend-session-config-v1"
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

_BASE_MIN_LEN = 2
_RETRY_MIN_LEN = 4
_MIN_MAX_LEN = 24
_MAX_LEN_RATIO = 1.40
_SHORT_OUTPUT_RATIO = 0.48
_SHORT_RETRY_MIN_RATIO = 0.42


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


def _needs_normalization(text: str) -> bool:
    return bool(re.search(r"\d|[%№$€£]", text))


def _previous_durations(values: tuple[float, ...]) -> tuple[float, ...]:
    result: list[float] = []
    for value in values:
        if isinstance(value, bool):
            raise ValueError("previous_output_durations не может содержать bool.")
        number = float(value)
        if not math.isfinite(number) or number < 0.0:
            raise ValueError(
                "previous_output_durations должен содержать конечные числа >= 0."
            )
        result.append(number)
    return tuple(result)


class VoxCPM2Session:
    """Low-level VoxCPM2 model call hidden behind the backend adapter."""

    supports_continuation_context = True

    def __init__(self, model: Any, audio_spec: BackendAudioSpec) -> None:
        self._model = model
        self.audio_spec = audio_spec

    def generate(self, request: BackendGenerationRequest) -> Any:
        """Generate audio from the model-neutral request contract only."""
        if not isinstance(request, BackendGenerationRequest):
            raise TypeError("VoxCPM2Session.generate ожидает BackendGenerationRequest.")

        cfg = request.option_float("cfg", default=1.9, low=0.1, high=10.0)
        steps = request.option_int("steps", default=16, low=1, high=256)
        min_len = request.option_int("min_len", default=2, low=1, high=512)
        max_len = request.option_int("max_len", default=64, low=2, high=512)
        if min_len >= max_len:
            raise ValueError("VoxCPM2 min_len должен быть меньше max_len.")

        parameters = inspect.signature(self._model.generate).parameters
        generation_max_len = min(
            512,
            max(max_len, int(math.ceil(max_len * 1.45))),
        )
        kwargs: dict[str, Any] = {
            "text": request.text,
            "reference_wav_path": str(request.reference_audio),
            "cfg_value": cfg,
            "inference_timesteps": steps,
            "min_len": min_len,
            "max_len": generation_max_len,
            "normalize": _needs_normalization(request.text),
            "denoise": False,
        }
        optional = {
            "retry_badcase": True,
            "retry_badcase_max_times": 2,
            "retry_badcase_ratio_threshold": 6.0,
            "seed": int(request.seed) if request.seed is not None else None,
        }
        continuation_reference = request.continuation_reference
        if continuation_reference is not None and continuation_reference.is_file():
            if "prompt_wav_path" in parameters:
                kwargs["prompt_wav_path"] = str(continuation_reference)
            continuation_text = str(request.continuation_text or "").strip()
            if "prompt_text" in parameters and continuation_text:
                kwargs["prompt_text"] = continuation_text
            elif "reference_text" in parameters and continuation_text:
                kwargs["reference_text"] = continuation_text
        for name, value in optional.items():
            if name in parameters and value is not None:
                kwargs[name] = value
        return self._model.generate(**kwargs)


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
            continuation_context=True,
        )

    def process_environment(
        self,
        request: dict[str, Any],
        *,
        base_environment: Mapping[str, str] | None = None,
    ) -> BackendProcessEnvironment:
        """Own VoxCPM2's CPU/offline process policy outside shared orchestration."""
        del base_environment
        raw_threads = request.get("threads", 10)
        if isinstance(raw_threads, bool):
            raise RuntimeError("VoxCPM2 threads не может быть bool.")
        try:
            threads = int(raw_threads)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("VoxCPM2 threads должен быть целым числом.") from exc
        if not 1 <= threads <= 64:
            raise RuntimeError("VoxCPM2 threads должен быть в диапазоне 1..64.")
        return BackendProcessEnvironment(
            backend_id=self.backend_id,
            set_values=(
                ("CUDA_DEVICE_ORDER", "PCI_BUS_ID"),
                ("CUDA_VISIBLE_DEVICES", "-1"),
                ("HF_HUB_OFFLINE", "1"),
                ("TRANSFORMERS_OFFLINE", "1"),
                ("MKL_NUM_THREADS", str(threads)),
                ("OMP_NUM_THREADS", str(threads)),
                ("PYTHONIOENCODING", "utf-8"),
                ("PYTHONUTF8", "1"),
                ("TOKENIZERS_PARALLELISM", "false"),
            ),
            removed_keys=(
                "VOXCPM_ORIGINAL_RENDERER",
                "VOXCPM_PROMPT_TEXTS_JSON",
                "VOXCPM_RESCUE_RENDERER",
                "VOXCPM_SEMANTIC_GUARD_VERSION",
            ),
        )

    def plan_generation_length(
        self,
        audio_spec: BackendAudioSpec,
        *,
        duration_budget: float,
        attempt: int,
        previous_output_durations: tuple[float, ...] = (),
    ) -> BackendGenerationLengthPlan:
        """Translate neutral duration evidence into VoxCPM2 token-window options."""
        if not isinstance(audio_spec, BackendAudioSpec):
            raise TypeError("VoxCPM2 length planner ожидает BackendAudioSpec.")
        if audio_spec.seconds_per_step is None:
            raise RuntimeError(
                "VoxCPM2 length planner требует audio_spec.seconds_per_step."
            )
        if isinstance(attempt, bool) or int(attempt) < 1:
            raise ValueError("VoxCPM2 attempt должен быть целым числом >= 1.")
        budget = float(duration_budget)
        if not math.isfinite(budget) or budget <= 0.0:
            raise ValueError("VoxCPM2 duration_budget должен быть конечным числом > 0.")
        previous = _previous_durations(tuple(previous_output_durations))
        seconds_per_step = float(audio_spec.seconds_per_step)
        desired_steps = budget / seconds_per_step
        max_len = max(_MIN_MAX_LEN, int(math.ceil(desired_steps * _MAX_LEN_RATIO)))
        short_retry = (
            int(attempt) >= 3
            and bool(previous)
            and all(value < budget * _SHORT_OUTPUT_RATIO for value in previous)
        )
        min_len = _BASE_MIN_LEN
        if short_retry:
            min_len = max(
                _RETRY_MIN_LEN,
                int(math.floor(desired_steps * _SHORT_RETRY_MIN_RATIO)),
            )
        min_len = min(min_len, max_len - 1)
        return BackendGenerationLengthPlan(
            backend_id=self.backend_id,
            duration_budget=budget,
            attempt=int(attempt),
            backend_options={
                "min_len": min_len,
                "max_len": max_len,
            },
            metadata={
                "policy": GENERATION_LENGTH_POLICY,
                "seconds_per_step": seconds_per_step,
                "desired_steps": desired_steps,
                "short_retry": short_retry,
                "previous_output_durations": list(previous),
                "short_output_ratio": _SHORT_OUTPUT_RATIO,
                "short_retry_min_ratio": _SHORT_RETRY_MIN_RATIO,
                "max_len_ratio": _MAX_LEN_RATIO,
            },
        )

    def open_session(
        self,
        config: BackendSessionConfig,
    ) -> BackendSynthesisSession:
        """Load VoxCPM2 from the model-neutral session configuration."""
        if not isinstance(config, BackendSessionConfig):
            raise TypeError("VoxCPM2Backend.open_session ожидает BackendSessionConfig.")
        model_path = config.model_path
        raw_cache_length = config.options.get("cache_length", 4096)
        if isinstance(raw_cache_length, bool):
            raise RuntimeError("VoxCPM2 cache_length не может быть bool.")
        try:
            resolved_cache_length = int(raw_cache_length)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("VoxCPM2 cache_length должен быть целым числом.") from exc
        if not 2048 <= resolved_cache_length <= 131072:
            raise RuntimeError("VoxCPM2 cache_length должен быть в диапазоне 2048..131072.")

        from voxcpm import VoxCPM

        model = VoxCPM.from_pretrained(
            str(Path(model_path).resolve()),
            device="cpu",
            optimize=False,
            load_denoiser=False,
        )
        cache_dtype = next(model.tts_model.parameters()).dtype
        cache_device = model.tts_model.device
        model.tts_model.base_lm.setup_cache(
            1,
            resolved_cache_length,
            cache_device,
            cache_dtype,
        )
        model.tts_model.residual_lm.setup_cache(
            1,
            resolved_cache_length,
            cache_device,
            cache_dtype,
        )
        encode_sr = int(model.tts_model._encode_sample_rate)
        output_sr = int(model.tts_model.sample_rate)
        if encode_sr <= 0 or output_sr <= 0:
            raise RuntimeError(
                f"VoxCPM2 вернул некорректный sample rate: {encode_sr}->{output_sr}."
            )
        seconds_per_step = (
            int(model.tts_model.patch_size)
            * int(model.tts_model.chunk_size)
            / encode_sr
        )
        return VoxCPM2Session(
            model,
            BackendAudioSpec(
                encode_sample_rate=encode_sr,
                output_sample_rate=output_sr,
                seconds_per_step=seconds_per_step,
                cache_length=resolved_cache_length,
            ),
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

    @staticmethod
    def _required(values: dict[str, Any], key: str) -> str:
        value = str(values.get(key) or "").strip()
        if not value:
            raise RuntimeError(f"VoxCPM2 command context missing {key}.")
        return value

    def build_renderer_command(
        self,
        runtime: BackendRuntimePaths,
        *,
        values: dict[str, Any],
    ) -> list[str]:
        return [
            str(runtime.cpu_python),
            str(runtime.renderer_entrypoint),
            "--speech-backend", self.backend_id,
            "--archive-root", str(runtime.archive_root),
            "--extended-reference", self._required(values, "extended_reference"),
            "--composite-reference", self._required(values, "composite_reference"),
            "--segments-json", self._required(values, "segments_json"),
            "--work-dir", self._required(values, "segment_work"),
            "--output", self._required(values, "timeline"),
            "--threads", self._required(values, "threads"),
            "--steps", self._required(values, "steps"),
            "--cfg", self._required(values, "cfg"),
            "--cache-length", self._required(values, "cache_length"),
            "--video-duration", self._required(values, "duration"),
            "--base-seed", self._required(values, "base_seed"),
        ]

    def build_master_command(
        self,
        runtime: BackendRuntimePaths,
        *,
        values: dict[str, Any],
    ) -> list[str]:
        return [
            str(runtime.cpu_python),
            str(runtime.master_entrypoint),
            "--source-video", self._required(values, "source"),
            "--russian-wav", self._required(values, "timeline"),
            "--work-dir", self._required(values, "master_work"),
            "--mixed-video", self._required(values, "final_mixed"),
            "--russian-only-video", self._required(values, "final_russian"),
            "--original-level", self._required(values, "original_level"),
            "--target-i", self._required(values, "target_i"),
            "--target-lra", self._required(values, "target_lra"),
            "--target-tp", self._required(values, "target_tp"),
        ]


__all__ = [
    "ADAPTER_POLICY",
    "GENERATION_CALL_POLICY",
    "GENERATION_LENGTH_POLICY",
    "MASTER_SELECTION_POLICY",
    "SESSION_CALL_POLICY",
    "VoxCPM2Backend",
    "VoxCPM2Session",
]
