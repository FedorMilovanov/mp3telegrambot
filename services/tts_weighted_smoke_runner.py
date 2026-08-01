#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-fast doctor for a trusted weighted-TTS self-hosted runner.

The doctor deliberately stops before ``SpeechBackend.open_session()``. It proves
that profile resolution, imports, model discovery, reference audio, ffprobe and
atomic temporary storage are ready without loading model weights or retaining
local paths in its report.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from typing import Any

from services.speech_backends import BackendIdentity
from services.tts_weighted_smoke import (
    WeightedTTSSmokeRuntime,
    _assert_privacy_allowlist,
    _atomic_report,
    _model_config_evidence,
    _validate_expected_python,
    _validate_reference,
    resolve_weighted_smoke_runtime,
)

TTS_WEIGHTED_SMOKE_RUNNER_POLICY = "trusted-weighted-tts-runner-doctor-v1"
TTS_WEIGHTED_SMOKE_RUNNER_REPORT_POLICY = "privacy-safe-runner-doctor-report-v1"
_PROBE_BYTES = b"tts-weighted-smoke-runner-doctor-v1\n"
_VERSION_RE = re.compile(r"^ffprobe version\s+(\S+)", re.IGNORECASE)


@dataclass(frozen=True)
class WeightedTTSSmokeRunnerConfig:
    profile_id: str
    model_root: Path
    reference_wav: Path
    work_dir: Path
    expected_python: Path | None = None

    def __post_init__(self) -> None:
        profile_id = str(self.profile_id or "").strip()
        if not profile_id:
            raise ValueError("profile_id не может быть пустым.")
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "model_root", Path(self.model_root).expanduser().resolve())
        object.__setattr__(
            self,
            "reference_wav",
            Path(self.reference_wav).expanduser().resolve(),
        )
        object.__setattr__(self, "work_dir", Path(self.work_dir).expanduser().resolve())
        if self.expected_python is not None:
            object.__setattr__(
                self,
                "expected_python",
                Path(self.expected_python).expanduser().resolve(),
            )


def _safe_version(value: object) -> str:
    text = " ".join(str(value or "").split())
    return text[:160]


def _module_version(module: Any) -> str:
    return _safe_version(getattr(module, "__version__", ""))


def _required_runtime_modules(identity: BackendIdentity) -> tuple[str, ...]:
    names = [identity.runtime_module, "numpy", "soundfile"]
    if identity.backend_id == "voxcpm2":
        names.extend(("torch", "voxcpm"))
    return tuple(dict.fromkeys(str(name).strip() for name in names if str(name).strip()))


def _probe_imports(identity: BackendIdentity) -> dict[str, Any]:
    modules: dict[str, Any] = {}
    imported: list[dict[str, str]] = []
    for name in _required_runtime_modules(identity):
        try:
            module = importlib.import_module(name)
        except Exception as exc:
            raise RuntimeError(
                f"Weighted smoke runner не импортирует required module {name}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        modules[name] = module
        imported.append({"name": name, "version": _module_version(module)})

    facts: dict[str, Any] = {"modules": imported}
    torch = modules.get("torch")
    if torch is not None:
        cuda = getattr(torch, "cuda", None)
        available = bool(cuda is not None and cuda.is_available())
        count = int(cuda.device_count()) if available else 0
        facts["torch"] = {
            "version": _module_version(torch),
            "cuda_available": available,
            "cuda_device_count": count,
        }
    return facts


def _probe_ffprobe() -> dict[str, Any]:
    executable = shutil.which("ffprobe")
    if not executable:
        raise RuntimeError("ffprobe не найден в PATH trusted runner.")
    process = subprocess.run(
        [executable, "-version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(
            "ffprobe -version завершился с кодом "
            f"{process.returncode}: {(process.stderr or '')[-300:]}"
        )
    first_line = (process.stdout or "").splitlines()[0:1]
    match = _VERSION_RE.match(first_line[0].strip() if first_line else "")
    if match is None:
        raise RuntimeError("ffprobe -version вернул неизвестный формат.")
    return {
        "available": True,
        "version": _safe_version(match.group(1)),
    }


def _prepare_empty_work_dir(path: Path) -> None:
    if path.exists():
        if not path.is_dir():
            raise RuntimeError("Runner doctor work_dir существует и не является директорией.")
        if any(path.iterdir()):
            raise RuntimeError("Runner doctor work_dir должен быть пустым.")
    else:
        path.mkdir(parents=True, exist_ok=False)


def _probe_atomic_storage(work_dir: Path) -> dict[str, Any]:
    _prepare_empty_work_dir(work_dir)
    source = work_dir / ".doctor-write.tmp"
    destination = work_dir / ".doctor-replace.ready"
    try:
        with source.open("x+b") as handle:
            handle.write(_PROBE_BYTES)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(source, destination)
        payload = destination.read_bytes()
        if payload != _PROBE_BYTES:
            raise RuntimeError("Runner doctor atomic storage read-back не совпал.")
        return {
            "write": True,
            "fsync": True,
            "replace": True,
            "readback": True,
            "cleanup": True,
        }
    finally:
        source.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)


def _safe_identity(identity: BackendIdentity) -> dict[str, Any]:
    return {
        "backend_id": identity.backend_id,
        "family": identity.family,
        "adapter_policy": identity.adapter_policy,
        "runtime_module": identity.runtime_module,
        "parameter_schema": list(identity.parameter_schema),
        "output_contract": identity.output_contract,
    }


def _environment_facts(runtime: WeightedTTSSmokeRuntime) -> dict[str, Any]:
    environment = runtime.backend.process_environment(
        dict(runtime.resolution.request),
        base_environment=os.environ,
    )
    values = dict(environment.set_values)
    thread_value = runtime.resolution.options.get("threads")
    threads = int(thread_value) if isinstance(thread_value, int) and not isinstance(thread_value, bool) else None
    return {
        "backend_id": environment.backend_id,
        "set_keys": sorted(str(key) for key in values),
        "removed_keys": sorted(str(key) for key in environment.removed_keys),
        "hf_hub_offline": values.get("HF_HUB_OFFLINE") == "1",
        "transformers_offline": values.get("TRANSFORMERS_OFFLINE") == "1",
        "configured_threads": threads,
    }


def _validate_model_discovery(
    config: WeightedTTSSmokeRunnerConfig,
    runtime: WeightedTTSSmokeRuntime,
) -> tuple[BackendIdentity, dict[str, Any]]:
    if not config.model_root.exists():
        raise RuntimeError("TTS smoke model root не найден.")
    identity = runtime.backend.identity(config.model_root)
    if identity.backend_id != runtime.profile.backend_id:
        raise RuntimeError("Backend identity не совпадает с выбранным TTS profile.")
    model_path = Path(identity.model_path).resolve()
    if not model_path.exists():
        raise RuntimeError("Speech backend не обнаружил model directory в configured root.")
    if not model_path.is_dir():
        raise RuntimeError("Speech backend model discovery должен вернуть директорию.")
    config_evidence = _model_config_evidence(model_path)
    if identity.backend_id == "voxcpm2" and not config_evidence["config_present"]:
        raise RuntimeError("VoxCPM2 model directory не содержит config.json.")
    return identity, config_evidence


def run_weighted_tts_runner_doctor(
    config: WeightedTTSSmokeRunnerConfig,
    *,
    runtime: WeightedTTSSmokeRuntime | None = None,
) -> dict[str, Any]:
    """Validate a trusted runner without opening a synthesis session."""
    if not isinstance(config, WeightedTTSSmokeRunnerConfig):
        raise TypeError("config должен быть WeightedTTSSmokeRunnerConfig.")
    _validate_expected_python(config.expected_python)
    runtime = runtime or resolve_weighted_smoke_runtime(
        # The resolver only consumes identity fields from this compatible object.
        type(
            "_ResolverConfig",
            (),
            {"profile_id": config.profile_id},
        )()
    )
    if runtime.profile.profile_id != config.profile_id:
        raise RuntimeError("Runner doctor runtime разрешил другой profile_id.")
    if not runtime.profile.production_enabled:
        raise RuntimeError("Runner doctor запрещён для disabled profile.")

    identity, model = _validate_model_discovery(config, runtime)
    imports = _probe_imports(identity)
    reference = _validate_reference(config.reference_wav)
    ffprobe = _probe_ffprobe()
    storage = _probe_atomic_storage(config.work_dir)
    report_path = config.work_dir / "report.json"
    report = {
        "schema_version": 1,
        "policy": TTS_WEIGHTED_SMOKE_RUNNER_POLICY,
        "report_policy": TTS_WEIGHTED_SMOKE_RUNNER_REPORT_POLICY,
        "passed": True,
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": {
            "profile_id": runtime.profile.profile_id,
            "backend_id": runtime.profile.backend_id,
            "display_name": runtime.profile.display_name,
            "model_family": runtime.profile.model_family,
            "model_revision": runtime.profile.model_revision,
            "profile_fingerprint": runtime.profile.fingerprint(),
            "source": dict(runtime.source_evidence),
        },
        "backend": _safe_identity(identity),
        "model": model,
        "imports": imports,
        "reference": reference,
        "ffprobe": ffprobe,
        "environment": _environment_facts(runtime),
        "storage": storage,
        "runtime": {
            "python_version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform_system": platform.system(),
            "machine": platform.machine(),
            "weights_loaded": False,
            "session_opened": False,
        },
    }
    forbidden_values = (
        str(config.model_root),
        str(Path(identity.model_path).resolve()),
        str(config.reference_wav),
        str(config.work_dir),
        str(config.expected_python or ""),
        str(Path(sys.executable).resolve()),
        str(Path.cwd().resolve()),
    )
    _assert_privacy_allowlist(report, forbidden_values)
    _atomic_report(report_path, report)
    return report


__all__ = [
    "TTS_WEIGHTED_SMOKE_RUNNER_POLICY",
    "TTS_WEIGHTED_SMOKE_RUNNER_REPORT_POLICY",
    "WeightedTTSSmokeRunnerConfig",
    "run_weighted_tts_runner_doctor",
]
