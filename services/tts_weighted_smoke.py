#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backend-neutral weighted TTS smoke with privacy-safe durable evidence."""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from typing import Any

import numpy as np
import soundfile as sf

from services.speech_backends import (
    DEFAULT_BACKEND_ID,
    DEFAULT_MODEL_PROFILE_ID,
    GENERATION_EXECUTION_PLAN_POLICY,
    BackendGenerationLengthPlan,
    BackendGenerationLengthRequest,
    BackendGenerationProfilePlan,
    BackendGenerationProfileRequest,
    BackendGenerationRequest,
    BackendIdentity,
    BackendSessionConfig,
    SpeechModelProfile,
    SpeechModelResolution,
    model_profile_source_evidence,
    select_production_speech,
)
from services.tts_profile_selection import normalize_new_production_tts_request

TTS_WEIGHTED_SMOKE_POLICY = "backend-neutral-weighted-tts-smoke-v1"
TTS_WEIGHTED_SMOKE_REPORT_POLICY = "privacy-safe-weighted-tts-smoke-report-v1"
_DEFAULT_TEXT = "Это короткая проверка настоящего синтеза русской речи."
_MAX_TEXT_CHARS = 500
_MAX_REFERENCE_BYTES = 250 * 1024 * 1024
_MIN_REFERENCE_SECONDS = 2.0
_MAX_REFERENCE_SECONDS = 180.0
_MIN_OUTPUT_SECONDS = 0.20
_MAX_OUTPUT_SECONDS = 45.0
_MAX_CLIPPING_RATIO = 0.05
_MIN_RMS = 1e-5
_REPORT_FORBIDDEN_KEYS = {
    "path",
    "paths",
    "root",
    "archive",
    "venv",
    "executable",
    "model_path",
    "reference_audio",
    "reference_wav_path",
    "prompt_wav_path",
    "backend_defaults",
    "backend_config",
    "speech_backend_config",
}


@dataclass(frozen=True)
class WeightedTTSSmokeConfig:
    profile_id: str
    model_root: Path
    reference_wav: Path
    work_dir: Path
    expected_python: Path | None = None
    text: str = _DEFAULT_TEXT
    duration_budget: float = 4.0
    seed: int = 2026080101

    def __post_init__(self) -> None:
        profile_id = str(self.profile_id or "").strip()
        text = str(self.text or "").strip()
        duration = float(self.duration_budget)
        if not profile_id:
            raise ValueError("profile_id не может быть пустым.")
        if not text or len(text) > _MAX_TEXT_CHARS:
            raise ValueError(
                f"Smoke text должен содержать 1..{_MAX_TEXT_CHARS} символов."
            )
        if not math.isfinite(duration) or not 0.2 <= duration <= 30.0:
            raise ValueError("duration_budget должен быть конечным числом 0.2..30 сек.")
        if isinstance(self.seed, bool):
            raise ValueError("seed не может быть bool.")
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "model_root", Path(self.model_root).expanduser().resolve())
        object.__setattr__(self, "reference_wav", Path(self.reference_wav).expanduser().resolve())
        object.__setattr__(self, "work_dir", Path(self.work_dir).expanduser().resolve())
        if self.expected_python is not None:
            object.__setattr__(
                self,
                "expected_python",
                Path(self.expected_python).expanduser().resolve(),
            )
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "duration_budget", duration)
        object.__setattr__(self, "seed", int(self.seed))


@dataclass(frozen=True)
class WeightedTTSSmokeRuntime:
    backend: Any
    profile: SpeechModelProfile
    resolution: SpeechModelResolution
    source_evidence: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_evidence", dict(self.source_evidence))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_json_object(text: str, *, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> Any:
        raise ValueError(f"{label}: JSON constant запрещён: {value}")

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label}: дублирующийся JSON key: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            text,
            object_pairs_hook=no_duplicates,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}: некорректный JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label}: ожидается JSON-объект.")
    return payload


def _validate_expected_python(expected: Path | None) -> None:
    if expected is None:
        return
    current = Path(sys.executable).resolve()
    if not expected.is_file():
        raise RuntimeError("Configured smoke Python не найден.")
    try:
        same = os.path.samefile(current, expected)
    except OSError:
        same = current == expected
    if not same:
        raise RuntimeError(
            "Weighted smoke запущен не тем Python interpreter, который настроен "
            "на self-hosted runner."
        )


def _validate_work_dir(work_dir: Path) -> None:
    if work_dir.exists():
        if not work_dir.is_dir():
            raise RuntimeError("Smoke work_dir существует и не является директорией.")
        if any(work_dir.iterdir()):
            raise RuntimeError("Smoke work_dir должен быть пустым.")
    else:
        work_dir.mkdir(parents=True, exist_ok=False)


def _validate_reference(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError("TTS smoke reference WAV не найден.")
    size = path.stat().st_size
    if not 1 <= size <= _MAX_REFERENCE_BYTES:
        raise RuntimeError("TTS smoke reference WAV имеет недопустимый размер.")
    try:
        info = sf.info(str(path))
    except (RuntimeError, OSError) as exc:
        raise RuntimeError("TTS smoke reference не читается как аудиофайл.") from exc
    duration = float(info.duration)
    sample_rate = int(info.samplerate)
    channels = int(info.channels)
    if not _MIN_REFERENCE_SECONDS <= duration <= _MAX_REFERENCE_SECONDS:
        raise RuntimeError(
            "TTS smoke reference duration должен быть в диапазоне "
            f"{_MIN_REFERENCE_SECONDS}..{_MAX_REFERENCE_SECONDS} сек."
        )
    if not 8_000 <= sample_rate <= 192_000 or not 1 <= channels <= 2:
        raise RuntimeError("TTS smoke reference имеет неподдерживаемый audio format.")
    try:
        samples, _ = sf.read(
            str(path),
            dtype="float32",
            always_2d=True,
        )
    except (RuntimeError, OSError) as exc:
        raise RuntimeError("TTS smoke reference samples не читаются.") from exc
    array = np.asarray(samples, dtype=np.float32)
    if array.size == 0 or not np.isfinite(array).all():
        raise RuntimeError("TTS smoke reference содержит пустые или non-finite samples.")
    rms = float(np.sqrt(np.mean(np.square(array, dtype=np.float64))))
    if not math.isfinite(rms) or rms < _MIN_RMS:
        raise RuntimeError("TTS smoke reference практически бесшумен.")
    return {
        "duration_seconds": round(duration, 6),
        "sample_rate": sample_rate,
        "channels": channels,
        "format": str(info.format or ""),
        "subtype": str(info.subtype or ""),
        "rms": round(rms, 8),
        "bytes": int(size),
    }


def normalize_generated_pcm(value: Any) -> np.ndarray:
    """Normalize only unambiguous mono/channel-first/channel-last PCM shapes."""
    try:
        array = np.asarray(value, dtype=np.float32)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("TTS backend вернул нечисловой PCM result.") from exc
    array = np.squeeze(array)
    if array.ndim == 1:
        mono = array
    elif array.ndim == 2:
        rows, columns = array.shape
        if 1 <= rows <= 8 and columns > rows:
            mono = np.mean(array, axis=0, dtype=np.float32)
        elif 1 <= columns <= 8 and rows > columns:
            mono = np.mean(array, axis=1, dtype=np.float32)
        else:
            raise RuntimeError(
                f"TTS backend вернул неоднозначную PCM shape: {array.shape}."
            )
    else:
        raise RuntimeError(f"TTS backend вернул неподдерживаемую PCM shape: {array.shape}.")
    mono = np.ascontiguousarray(mono, dtype=np.float32)
    if mono.size == 0:
        raise RuntimeError("TTS backend вернул пустой PCM result.")
    if not np.isfinite(mono).all():
        raise RuntimeError("TTS backend вернул NaN/Infinity в PCM result.")
    peak = float(np.max(np.abs(mono)))
    rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64))))
    if not math.isfinite(peak) or not math.isfinite(rms) or rms < _MIN_RMS:
        raise RuntimeError("TTS backend вернул практически бесшумный PCM result.")
    if peak > 8.0:
        raise RuntimeError("TTS backend вернул PCM с недопустимой амплитудой.")
    return mono


def _pcm_metrics(samples: np.ndarray, sample_rate: int) -> dict[str, Any]:
    if sample_rate <= 0:
        raise RuntimeError("TTS backend вернул некорректный output sample rate.")
    duration = float(len(samples)) / float(sample_rate)
    if not _MIN_OUTPUT_SECONDS <= duration <= _MAX_OUTPUT_SECONDS:
        raise RuntimeError(
            "Weighted TTS output duration вне безопасного диапазона: "
            f"{duration:.3f} сек."
        )
    peak = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    clipping = float(np.mean(np.abs(samples) >= 0.999))
    if clipping > _MAX_CLIPPING_RATIO:
        raise RuntimeError(
            f"Weighted TTS output clipping ratio слишком высокий: {clipping:.6f}."
        )
    return {
        "samples": int(len(samples)),
        "sample_rate": int(sample_rate),
        "duration_seconds": round(duration, 6),
        "peak": round(peak, 8),
        "rms": round(rms, 8),
        "clipping_ratio": round(clipping, 8),
    }


def _write_and_readback(output: Path, samples: np.ndarray, sample_rate: int) -> dict[str, Any]:
    sf.write(str(output), samples, sample_rate, subtype="PCM_24", format="WAV")
    if not output.is_file() or output.stat().st_size <= 44:
        raise RuntimeError("Weighted TTS output WAV не создан.")
    try:
        info = sf.info(str(output))
        readback, read_rate = sf.read(
            str(output),
            dtype="float32",
            always_2d=True,
        )
    except (RuntimeError, OSError) as exc:
        raise RuntimeError("Weighted TTS output WAV не прошёл read-back.") from exc
    if int(info.channels) != 1 or int(read_rate) != int(sample_rate):
        raise RuntimeError("Weighted TTS output WAV изменил channels/sample rate.")
    if str(info.subtype or "") != "PCM_24":
        raise RuntimeError(
            f"Weighted TTS output subtype должен быть PCM_24, получено {info.subtype}."
        )
    readback_mono = normalize_generated_pcm(readback)
    metrics = _pcm_metrics(readback_mono, int(read_rate))
    metrics.update(
        {
            "format": str(info.format or ""),
            "subtype": str(info.subtype or ""),
            "bytes": int(output.stat().st_size),
        }
    )
    return metrics


def _ffprobe_output(output: Path) -> dict[str, Any]:
    executable = shutil.which("ffprobe")
    if not executable:
        raise RuntimeError("ffprobe не найден в PATH self-hosted runner.")
    process = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,codec_type,sample_rate,channels,duration",
            "-of",
            "json",
            str(output),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(
            "ffprobe weighted TTS output завершился с кодом "
            f"{process.returncode}: {(process.stderr or '')[-500:]}"
        )
    payload = _strict_json_object(process.stdout or "{}", label="ffprobe")
    streams = payload.get("streams")
    if not isinstance(streams, list) or len(streams) != 1:
        raise RuntimeError("ffprobe ожидает ровно один output stream.")
    stream = streams[0]
    if not isinstance(stream, dict) or stream.get("codec_type") != "audio":
        raise RuntimeError("ffprobe output stream не является audio.")
    try:
        sample_rate = int(stream.get("sample_rate"))
        channels = int(stream.get("channels"))
        duration = float(stream.get("duration"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("ffprobe вернул некорректные audio facts.") from exc
    if channels != 1 or sample_rate <= 0 or not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("ffprobe не подтвердил валидный mono PCM output.")
    codec = str(stream.get("codec_name") or "")
    if not codec.startswith("pcm_"):
        raise RuntimeError(f"ffprobe codec должен быть PCM, получено {codec!r}.")
    return {
        "codec_name": codec,
        "sample_rate": sample_rate,
        "channels": channels,
        "duration_seconds": round(duration, 6),
    }


def _merge_backend_options(*sources: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for source in sources:
        overlap = sorted(set(merged).intersection(source))
        if overlap:
            raise RuntimeError(
                "Weighted smoke backend plans конфликтуют по keys: "
                + ", ".join(overlap)
            )
        merged.update(dict(source))
    return merged


def _execution_evidence(path: Path, *, backend_id: str, required: bool) -> dict[str, Any]:
    if not path.is_file():
        if required:
            raise RuntimeError("Weighted TTS synthesis завершился без execution-plan evidence.")
        return {"required": False, "present": False}
    if path.stat().st_size > 1_000_000:
        raise RuntimeError("Execution-plan evidence превышает допустимый размер.")
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError("Weighted smoke ожидает ровно один execution-plan record.")
    payload = _strict_json_object(lines[0], label="execution plan")
    if payload.get("policy") != GENERATION_EXECUTION_PLAN_POLICY:
        raise RuntimeError("Execution-plan evidence имеет неизвестную policy.")
    if str(payload.get("backend_id") or "") != str(backend_id):
        raise RuntimeError("Execution-plan evidence принадлежит другому backend.")
    kwargs = payload.get("model_kwargs")
    if not isinstance(kwargs, dict):
        raise RuntimeError("Execution-plan evidence не содержит model_kwargs.")
    safe_scalars: dict[str, Any] = {}
    for key in (
        "cfg_value",
        "inference_timesteps",
        "min_len",
        "max_len",
        "normalize",
        "denoise",
        "retry_badcase",
        "retry_badcase_max_times",
        "retry_badcase_ratio_threshold",
        "seed",
    ):
        value = kwargs.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe_scalars[key] = value
    return {
        "required": bool(required),
        "present": True,
        "policy": str(payload["policy"]),
        "backend_id": str(payload["backend_id"]),
        "adapter_policy": str(payload.get("adapter_policy") or ""),
        "planned_max_len": int(payload.get("planned_max_len") or 0),
        "executed_max_len": int(payload.get("executed_max_len") or 0),
        "model_kwarg_names": sorted(str(key) for key in kwargs),
        "model_scalar_arguments": safe_scalars,
        "accepted_optional_parameters": sorted(
            str(value) for value in payload.get("accepted_optional_parameters") or []
        ),
        "omitted_optional_parameters": sorted(
            str(value) for value in payload.get("omitted_optional_parameters") or []
        ),
    }


def _safe_identity(identity: BackendIdentity) -> dict[str, Any]:
    return {
        "backend_id": identity.backend_id,
        "family": identity.family,
        "adapter_policy": identity.adapter_policy,
        "runtime_module": identity.runtime_module,
        "parameter_schema": list(identity.parameter_schema),
        "output_contract": identity.output_contract,
    }


def _model_config_evidence(model_path: Path) -> dict[str, Any]:
    config = Path(model_path) / "config.json"
    if not config.is_file():
        return {"config_present": False, "config_sha256": ""}
    if config.stat().st_size > 20 * 1024 * 1024:
        raise RuntimeError("Model config.json имеет недопустимый размер.")
    return {"config_present": True, "config_sha256": _sha256(config)}


def _text_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _assert_privacy_allowlist(report: Mapping[str, Any], forbidden_values: tuple[str, ...]) -> None:
    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for raw_key, item in value.items():
                key = str(raw_key).casefold()
                if key in _REPORT_FORBIDDEN_KEYS or key.endswith("_path"):
                    raise RuntimeError(
                        f"Weighted smoke report содержит запрещённый key: {raw_key}"
                    )
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(report)
    serialized = json.dumps(
        dict(report),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    for raw in forbidden_values:
        value = str(raw or "").strip()
        if value and value in serialized:
            raise RuntimeError("Weighted smoke report содержит локальное path/config value.")


def _atomic_report(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _temporary_environment(values: Mapping[str, str]):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update({str(key): str(value) for key, value in values.items()})
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def resolve_weighted_smoke_runtime(config: WeightedTTSSmokeConfig) -> WeightedTTSSmokeRuntime:
    request = normalize_new_production_tts_request(
        {"schema_version": 1},
        config.profile_id,
    )
    selection = select_production_speech(
        request.get("speech_backend"),
        request.get("speech_model_profile"),
        request=request,
        default_backend_id=DEFAULT_BACKEND_ID,
        default_model_profile_id=DEFAULT_MODEL_PROFILE_ID,
    )
    return WeightedTTSSmokeRuntime(
        backend=selection.backend,
        profile=selection.model_profile,
        resolution=selection.resolution,
        source_evidence=model_profile_source_evidence(selection.model_profile.profile_id),
    )


def run_weighted_tts_smoke(
    config: WeightedTTSSmokeConfig,
    *,
    runtime: WeightedTTSSmokeRuntime | None = None,
) -> dict[str, Any]:
    """Run one real backend session and retain only a sanitized JSON report."""
    if not isinstance(config, WeightedTTSSmokeConfig):
        raise TypeError("config должен быть WeightedTTSSmokeConfig.")
    _validate_expected_python(config.expected_python)
    _validate_work_dir(config.work_dir)
    if not config.model_root.exists():
        raise RuntimeError("TTS smoke model root не найден.")
    reference = _validate_reference(config.reference_wav)
    runtime = runtime or resolve_weighted_smoke_runtime(config)
    backend = runtime.backend
    profile = runtime.profile
    resolution = runtime.resolution
    if profile.profile_id != config.profile_id:
        raise RuntimeError("Weighted smoke runtime разрешил другой profile_id.")
    if not profile.production_enabled:
        raise RuntimeError("Weighted smoke запрещён для disabled profile.")

    output = config.work_dir / "weighted-smoke.wav"
    execution_log = config.work_dir / "execution-plan.jsonl"
    report_path = config.work_dir / "report.json"
    session: Any = None
    started = time.perf_counter()
    request = dict(resolution.request)
    environment = backend.process_environment(
        request,
        base_environment=os.environ,
    ).as_dict(os.environ)
    environment["DUB_BACKEND_EXECUTION_PLAN_LOG"] = str(execution_log)
    identity = backend.identity(config.model_root)
    model_path = Path(identity.model_path).resolve()
    if not model_path.exists():
        raise RuntimeError("Speech backend не обнаружил model path в configured root.")

    try:
        with _temporary_environment(environment):
            session = backend.open_session(
                BackendSessionConfig(
                    model_path=model_path,
                    options=dict(resolution.options),
                )
            )
            audio_spec = session.audio_spec
            length_request = BackendGenerationLengthRequest(
                duration_budget=config.duration_budget,
                attempt=1,
                previous_output_durations=(),
                minimum_completion_ratio=0.40,
                metadata={"source": "weighted-smoke"},
            )
            length_plan = backend.plan_generation_length(audio_spec, length_request)
            if not isinstance(length_plan, BackendGenerationLengthPlan):
                raise TypeError("Weighted smoke length planner вернул неверный тип.")
            profile_request = BackendGenerationProfileRequest(
                attempt=1,
                base_backend_options=dict(resolution.options),
                metadata={"source": "weighted-smoke"},
            )
            profile_plan = backend.plan_generation_profile(profile_request)
            if not isinstance(profile_plan, BackendGenerationProfilePlan):
                raise TypeError("Weighted smoke profile planner вернул неверный тип.")
            if length_plan.backend_id != backend.backend_id:
                raise RuntimeError("Weighted smoke length plan принадлежит другому backend.")
            if profile_plan.backend_id != backend.backend_id:
                raise RuntimeError("Weighted smoke profile plan принадлежит другому backend.")
            options = _merge_backend_options(
                length_plan.backend_options,
                profile_plan.backend_options,
            )
            generation = BackendGenerationRequest(
                text=config.text,
                reference_audio=config.reference_wav,
                seed=config.seed,
                duration_budget=config.duration_budget,
                style_instruction="neutral production smoke",
                backend_options=options,
            )
            try:
                import torch
            except ImportError:
                inference_context = nullcontext()
            else:
                threads = int(resolution.options.get("threads", 1))
                torch.set_num_threads(max(1, threads))
                try:
                    torch.set_num_interop_threads(1)
                except RuntimeError:
                    pass
                inference_context = torch.inference_mode()
            synthesis_started = time.perf_counter()
            with inference_context:
                generated = session.generate(generation)
            synthesis_seconds = time.perf_counter() - synthesis_started

        samples = normalize_generated_pcm(generated)
        pcm_metrics = _pcm_metrics(samples, int(audio_spec.output_sample_rate))
        readback = _write_and_readback(
            output,
            samples,
            int(audio_spec.output_sample_rate),
        )
        ffprobe = _ffprobe_output(output)
        evidence = _execution_evidence(
            execution_log,
            backend_id=backend.backend_id,
            required=bool(profile.requires_execution_plan_evidence),
        )
        if ffprobe["sample_rate"] != pcm_metrics["sample_rate"]:
            raise RuntimeError("FFprobe sample rate не совпадает с backend audio spec.")
        if abs(ffprobe["duration_seconds"] - pcm_metrics["duration_seconds"]) > 0.05:
            raise RuntimeError("FFprobe duration не совпадает с PCM duration.")

        report = {
            "schema_version": 1,
            "policy": TTS_WEIGHTED_SMOKE_POLICY,
            "report_policy": TTS_WEIGHTED_SMOKE_REPORT_POLICY,
            "passed": True,
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "profile": {
                "profile_id": profile.profile_id,
                "backend_id": profile.backend_id,
                "display_name": profile.display_name,
                "model_family": profile.model_family,
                "model_revision": profile.model_revision,
                "profile_fingerprint": profile.fingerprint(),
                "source": dict(runtime.source_evidence),
            },
            "backend": _safe_identity(identity),
            "model": _model_config_evidence(model_path),
            "request": {
                "text_sha256": _text_fingerprint(config.text),
                "text_characters": len(config.text),
                "duration_budget": config.duration_budget,
                "seed": config.seed,
                "generation_length_plan": length_plan.as_dict(),
                "generation_profile_plan": profile_plan.as_dict(),
            },
            "reference": reference,
            "output": {
                "pcm": pcm_metrics,
                "readback": readback,
                "ffprobe": ffprobe,
                "audio_retained": False,
            },
            "execution_plan": evidence,
            "runtime": {
                "python_version": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform_system": platform.system(),
                "synthesis_seconds": round(synthesis_seconds, 6),
                "total_seconds": round(time.perf_counter() - started, 6),
            },
        }
        forbidden = (
            str(config.model_root),
            str(model_path),
            str(config.reference_wav),
            str(config.work_dir),
            str(config.expected_python or ""),
            str(sys.executable),
        )
        _assert_privacy_allowlist(report, forbidden)
        _atomic_report(report_path, report)
        return report
    finally:
        output.unlink(missing_ok=True)
        execution_log.unlink(missing_ok=True)
        session = None
        gc.collect()


__all__ = [
    "TTS_WEIGHTED_SMOKE_POLICY",
    "TTS_WEIGHTED_SMOKE_REPORT_POLICY",
    "WeightedTTSSmokeConfig",
    "WeightedTTSSmokeRuntime",
    "normalize_generated_pcm",
    "resolve_weighted_smoke_runtime",
    "run_weighted_tts_smoke",
]
