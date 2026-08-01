#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed provisioning check for the Windows weighted-TTS runner.

This module never registers a GitHub runner and never accepts a registration
credential.  It validates an already configured persistent runner, verifies the
three machine-environment bindings consumed by the manual weighted smoke, and
executes the existing no-weights runner doctor.  Only a privacy-allowlisted
setup report is retained.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
from typing import Any

from services.tts_weighted_smoke import _assert_privacy_allowlist, _atomic_report
from services.tts_weighted_smoke_runner import (
    TTS_WEIGHTED_SMOKE_RUNNER_POLICY,
    WeightedTTSSmokeRunnerConfig,
    run_weighted_tts_runner_doctor,
)

TTS_WEIGHTED_RUNNER_PROVISIONING_POLICY = "windows-weighted-tts-runner-provisioning-v1"
TTS_WEIGHTED_RUNNER_PROVISIONING_REPORT_POLICY = (
    "privacy-safe-weighted-tts-runner-provisioning-report-v1"
)
REQUIRED_RUNNER_LABELS = ("self-hosted", "Windows", "X64", "tts-weights")
REQUIRED_ENVIRONMENT_KEYS = (
    "TTS_SMOKE_PYTHON",
    "TTS_SMOKE_MODEL_ROOT",
    "TTS_SMOKE_REFERENCE_WAV",
)
_REQUIRED_RUNNER_FILES = (
    "config.cmd",
    "run.cmd",
    "bin/Runner.Listener.exe",
)
_MAX_RUNNER_CONFIG_BYTES = 256 * 1024
_MAX_SERVICE_DESCRIPTOR_BYTES = 2048


@dataclass(frozen=True)
class WeightedTTSRunnerProvisioningConfig:
    runner_directory: Path
    repository: str
    profile_id: str
    python_executable: Path
    model_directory: Path
    reference_wav: Path
    work_directory: Path

    def __post_init__(self) -> None:
        repository = _normalize_repository(self.repository)
        profile_id = str(self.profile_id or "").strip()
        if not profile_id:
            raise ValueError("profile_id не может быть пустым.")
        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "profile_id", profile_id)
        for field in (
            "runner_directory",
            "python_executable",
            "model_directory",
            "reference_wav",
            "work_directory",
        ):
            value = Path(getattr(self, field)).expanduser().resolve()
            object.__setattr__(self, field, value)


def _normalize_repository(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    prefix = "https://github.com/"
    if text.casefold().startswith(prefix):
        text = text[len(prefix) :]
    text = text.strip("/")
    if text.casefold().endswith(".git"):
        text = text[:-4]
    parts = text.split("/")
    if len(parts) != 2 or any(not part for part in parts):
        raise ValueError("repository должен иметь форму owner/name.")
    for part in parts:
        if not 1 <= len(part) <= 100:
            raise ValueError("repository owner/name имеет недопустимую длину.")
        if any(
            not (char.isascii() and (char.isalnum() or char in "-_."))
            for char in part
        ):
            raise ValueError("repository owner/name содержит запрещённые символы.")
        if part[0] in ".-" or part[-1] in ".-" or ".." in part:
            raise ValueError("repository owner/name имеет небезопасную форму.")
    return f"{parts[0]}/{parts[1]}"


def _strict_json_object(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"{label} отсутствует.")
    size = path.stat().st_size
    if not 1 <= size <= _MAX_RUNNER_CONFIG_BYTES:
        raise RuntimeError(f"{label} имеет недопустимый размер.")

    def reject_constant(value: str) -> Any:
        raise ValueError(f"{label}: запрещённая JSON-константа {value}.")

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label}: дублирующийся JSON key {key}.")
            result[key] = value
        return result

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8-sig"),
            object_pairs_hook=no_duplicates,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"{label} не является строгим JSON-объектом.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} должен быть JSON-объектом.")
    return payload


def _positive_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RuntimeError(f"Runner config {field_name} должен быть положительным int.")
    return value


def _safe_runner_name(value: object) -> str:
    text = str(value or "").strip()
    if not 1 <= len(text) <= 128:
        raise RuntimeError("Runner config agentName имеет недопустимую длину.")
    if any(
        not (char.isascii() and (char.isalnum() or char in "-_."))
        for char in text
    ):
        raise RuntimeError("Runner config agentName содержит запрещённые символы.")
    if text[0] in ".-" or text[-1] in ".-" or ".." in text:
        raise RuntimeError("Runner config agentName имеет небезопасную форму.")
    return text


def _safe_work_folder(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or len(text) > 260 or text.startswith("/"):
        raise RuntimeError("Runner config workFolder имеет недопустимую форму.")
    parts = [part for part in text.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise RuntimeError("Runner config workFolder содержит небезопасный сегмент.")
    return text


def _canonical_github_url(value: object) -> str:
    return "https://github.com/" + _normalize_repository(value)


def _service_name(path: Path) -> str:
    if not path.is_file():
        raise RuntimeError("Runner .service descriptor отсутствует.")
    if not 1 <= path.stat().st_size <= _MAX_SERVICE_DESCRIPTOR_BYTES:
        raise RuntimeError("Runner .service descriptor имеет недопустимый размер.")
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()]
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("Runner .service descriptor не читается.") from exc
    lines = [line for line in lines if line]
    if len(lines) != 1:
        raise RuntimeError("Runner .service descriptor должен содержать одну строку.")
    name = lines[0]
    prefix = "actions.runner."
    suffix = ".service"
    if not name.startswith(prefix) or not name.endswith(suffix):
        raise RuntimeError("Runner .service descriptor имеет неизвестный формат.")
    if not 20 <= len(name) <= 240 or ".." in name:
        raise RuntimeError("Runner .service descriptor имеет небезопасную длину.")
    if any(
        not (char.isascii() and (char.isalnum() or char in "-_."))
        for char in name
    ):
        raise RuntimeError("Runner .service descriptor содержит запрещённые символы.")
    return name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_service_probe(service_name: str) -> bool:
    if platform.system() != "Windows":
        raise RuntimeError("Проверка Windows runner service запущена не на Windows.")
    executable = shutil.which("sc.exe") or shutil.which("sc")
    if not executable:
        raise RuntimeError("Windows Service Controller не найден.")
    process = subprocess.run(
        [executable, "query", service_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if process.returncode != 0:
        return False
    for line in (process.stdout or "").splitlines():
        if "STATE" not in line.upper() or ":" not in line:
            continue
        fields = line.split(":", 1)[1].strip().split()
        return bool(fields and fields[0] == "4")
    return False


def _runner_installation(
    config: WeightedTTSRunnerProvisioningConfig,
    *,
    service_probe: Callable[[str], bool],
) -> dict[str, Any]:
    root = config.runner_directory
    if not root.is_dir():
        raise RuntimeError("GitHub Actions runner directory не найдена.")
    for relative in _REQUIRED_RUNNER_FILES:
        if not (root / Path(relative)).is_file():
            raise RuntimeError("GitHub Actions runner installation неполна.")

    runner_path = root / ".runner"
    settings = _strict_json_object(runner_path, label="Runner .runner config")
    agent_id = _positive_int(settings.get("agentId"), field_name="agentId")
    pool_id = _positive_int(settings.get("poolId"), field_name="poolId")
    agent_name = _safe_runner_name(settings.get("agentName"))
    _safe_work_folder(settings.get("workFolder"))
    github_url = _canonical_github_url(settings.get("gitHubUrl"))
    expected_url = f"https://github.com/{config.repository}"
    if github_url.casefold() != expected_url.casefold():
        raise RuntimeError("Runner зарегистрирован для другого GitHub repository/org.")
    if settings.get("ephemeral") is not False:
        raise RuntimeError("Weighted TTS runner должен быть persistent, не ephemeral.")

    descriptor = root / ".service"
    service_name = _service_name(descriptor)
    if not service_probe(service_name):
        raise RuntimeError("GitHub Actions runner service не находится в Running state.")
    return {
        "configured": True,
        "persistent": True,
        "repository_match": True,
        "service_registered": True,
        "service_running": True,
        "required_files": len(_REQUIRED_RUNNER_FILES),
        "agent_id": agent_id,
        "pool_id": pool_id,
        "agent_name_sha256": hashlib.sha256(agent_name.encode("utf-8")).hexdigest(),
        "runner_config_sha256": _sha256(runner_path),
        "service_descriptor_sha256": _sha256(descriptor),
    }


def _environment_binding(config: WeightedTTSRunnerProvisioningConfig) -> dict[str, Any]:
    expected = {
        "TTS_SMOKE_PYTHON": config.python_executable,
        "TTS_SMOKE_MODEL_ROOT": config.model_directory,
        "TTS_SMOKE_REFERENCE_WAV": config.reference_wav,
    }
    for key, path in expected.items():
        raw = os.environ.get(key, "").strip()
        if not raw:
            raise RuntimeError(f"Runner environment binding отсутствует: {key}.")
        actual = Path(raw).expanduser().resolve()
        try:
            same = os.path.samefile(actual, path)
        except OSError:
            same = actual == path
        if not same:
            raise RuntimeError(f"Runner environment binding не совпадает: {key}.")
    return {
        "verified": True,
        "keys": list(REQUIRED_ENVIRONMENT_KEYS),
        "scope_required": "Machine",
        "service_restart_required_after_change": True,
    }


def _doctor_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    if report.get("passed") is not True:
        raise RuntimeError("Runner doctor report не подтверждает passed=true.")
    if report.get("policy") != TTS_WEIGHTED_SMOKE_RUNNER_POLICY:
        raise RuntimeError("Runner doctor report имеет неизвестную policy.")
    runtime = report.get("runtime")
    profile = report.get("profile")
    backend = report.get("backend")
    model = report.get("model")
    imports = report.get("imports")
    ffprobe = report.get("ffprobe")
    storage = report.get("storage")
    environment = report.get("environment")
    if not all(isinstance(item, Mapping) for item in (
        runtime,
        profile,
        backend,
        model,
        imports,
        ffprobe,
        storage,
        environment,
    )):
        raise RuntimeError("Runner doctor report не содержит обязательные sections.")
    if runtime.get("weights_loaded") is not False or runtime.get("session_opened") is not False:
        raise RuntimeError("Runner doctor нарушил no-weights/no-session invariant.")
    if not all(storage.get(key) is True for key in ("write", "fsync", "replace", "readback", "cleanup")):
        raise RuntimeError("Runner doctor не подтвердил atomic storage contract.")
    modules = imports.get("modules")
    if not isinstance(modules, list) or not modules:
        raise RuntimeError("Runner doctor не подтвердил runtime imports.")
    module_names = sorted(
        str(item.get("name") or "")
        for item in modules
        if isinstance(item, Mapping) and str(item.get("name") or "")
    )
    source = profile.get("source")
    if not isinstance(source, Mapping):
        raise RuntimeError("Runner doctor не содержит model source evidence.")
    threads = environment.get("configured_threads")
    if threads is not None and (isinstance(threads, bool) or not isinstance(threads, int) or threads <= 0):
        raise RuntimeError("Runner doctor вернул некорректный configured_threads.")
    return {
        "policy": str(report["policy"]),
        "profile_id": str(profile.get("profile_id") or ""),
        "backend_id": str(backend.get("backend_id") or ""),
        "model_revision": str(profile.get("model_revision") or ""),
        "profile_fingerprint": str(profile.get("profile_fingerprint") or ""),
        "manifest_sha256": str(source.get("source_sha256") or ""),
        "model_config_sha256": str(model.get("config_sha256") or ""),
        "runtime_modules": module_names,
        "ffprobe_version": str(ffprobe.get("version") or ""),
        "atomic_storage": True,
        "offline_hf": environment.get("hf_hub_offline") is True,
        "offline_transformers": environment.get("transformers_offline") is True,
        "configured_threads": threads,
        "python_version": str(runtime.get("python_version") or ""),
        "platform_system": str(runtime.get("platform_system") or ""),
        "machine": str(runtime.get("machine") or ""),
        "weights_loaded": False,
        "session_opened": False,
    }


def _prepare_work_directory(path: Path) -> None:
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise RuntimeError("Provisioning work directory должна быть пустой директорией.")
    else:
        path.mkdir(parents=True, exist_ok=False)


def run_weighted_tts_runner_provisioning_check(
    config: WeightedTTSRunnerProvisioningConfig,
    *,
    service_probe: Callable[[str], bool] = _default_service_probe,
    doctor_runner: Callable[[WeightedTTSSmokeRunnerConfig], Mapping[str, Any]] = (
        run_weighted_tts_runner_doctor
    ),
) -> dict[str, Any]:
    """Validate one configured Windows runner and retain a sanitized report."""
    if not isinstance(config, WeightedTTSRunnerProvisioningConfig):
        raise TypeError("config должен быть WeightedTTSRunnerProvisioningConfig.")
    if platform.system() != "Windows" and service_probe is _default_service_probe:
        raise RuntimeError("Weighted TTS runner provisioning поддерживает только Windows.")
    if not config.python_executable.is_file():
        raise RuntimeError("Configured trusted Python executable не найден.")
    if not config.model_directory.is_dir():
        raise RuntimeError("Configured model directory не найдена.")
    if not config.reference_wav.is_file():
        raise RuntimeError("Configured reference WAV не найден.")

    _prepare_work_directory(config.work_directory)
    doctor_dir = config.work_directory / "doctor"
    report_path = config.work_directory / "setup-report.json"
    try:
        runner = _runner_installation(config, service_probe=service_probe)
        environment = _environment_binding(config)
        doctor_report = doctor_runner(
            WeightedTTSSmokeRunnerConfig(
                profile_id=config.profile_id,
                model_root=config.model_directory,
                reference_wav=config.reference_wav,
                work_dir=doctor_dir,
                expected_python=config.python_executable,
            )
        )
        doctor = _doctor_summary(doctor_report)
        if doctor["profile_id"] != config.profile_id:
            raise RuntimeError("Runner doctor разрешил другой profile_id.")
        report = {
            "schema_version": 1,
            "policy": TTS_WEIGHTED_RUNNER_PROVISIONING_POLICY,
            "report_policy": TTS_WEIGHTED_RUNNER_PROVISIONING_REPORT_POLICY,
            "passed": True,
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "repository": config.repository,
            "required_labels": list(REQUIRED_RUNNER_LABELS),
            "runner": runner,
            "environment": environment,
            "doctor": doctor,
            "next_action": "workflow-dispatch:TTS Weighted Smoke@main",
        }
        forbidden = (
            str(config.runner_directory),
            str(config.python_executable),
            str(config.model_directory),
            str(config.reference_wav),
            str(config.work_directory),
            str(Path.cwd().resolve()),
        )
        _assert_privacy_allowlist(report, forbidden)
        _atomic_report(report_path, report)
        return report
    finally:
        shutil.rmtree(doctor_dir, ignore_errors=True)


__all__ = [
    "REQUIRED_ENVIRONMENT_KEYS",
    "REQUIRED_RUNNER_LABELS",
    "TTS_WEIGHTED_RUNNER_PROVISIONING_POLICY",
    "TTS_WEIGHTED_RUNNER_PROVISIONING_REPORT_POLICY",
    "WeightedTTSRunnerProvisioningConfig",
    "run_weighted_tts_runner_provisioning_check",
]
