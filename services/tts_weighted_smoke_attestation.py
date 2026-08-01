#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a durable, privacy-safe attestation for one real weighted TTS run.

The source doctor and synthesis reports remain ephemeral.  This module copies
only a strict allowlist of cross-checked facts, binds them to immutable GitHub
Actions identity and adds a canonical SHA-256 digest.  It never carries audio,
reference/model paths, raw backend configuration or source report payloads.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

from services.tts_weighted_smoke import (
    TTS_WEIGHTED_SMOKE_POLICY,
    TTS_WEIGHTED_SMOKE_REPORT_POLICY,
    _assert_privacy_allowlist,
    _atomic_report,
    _strict_json_object,
)
from services.tts_weighted_smoke_runner import (
    TTS_WEIGHTED_SMOKE_RUNNER_POLICY,
    TTS_WEIGHTED_SMOKE_RUNNER_REPORT_POLICY,
)

TTS_WEIGHTED_SMOKE_ATTESTATION_POLICY = "github-actions-weighted-tts-attestation-v1"
TTS_WEIGHTED_SMOKE_ATTESTATION_SCHEMA_VERSION = 1
_EXPECTED_REF = "refs/heads/main"
_EXPECTED_EVENT = "workflow_dispatch"
_EXPECTED_WORKFLOW = ".github/workflows/tts-weighted-smoke.yml"


@dataclass(frozen=True)
class WeightedTTSSmokeAttestationContext:
    repository: str
    commit_sha: str
    ref: str
    event_name: str
    workflow_ref: str
    run_id: int
    run_attempt: int

    def __post_init__(self) -> None:
        repository = str(self.repository or "").strip()
        commit_sha = str(self.commit_sha or "").strip().casefold()
        ref = str(self.ref or "").strip()
        event_name = str(self.event_name or "").strip()
        workflow_ref = str(self.workflow_ref or "").strip()
        run_id = _positive_int(self.run_id, "run_id")
        run_attempt = _positive_int(self.run_attempt, "run_attempt")

        _validate_repository(repository)
        _validate_hex(commit_sha, length=40, label="commit_sha")
        if ref != _EXPECTED_REF:
            raise ValueError(f"Weighted smoke attestation требует ref {_EXPECTED_REF!r}.")
        if event_name != _EXPECTED_EVENT:
            raise ValueError(
                f"Weighted smoke attestation требует event {_EXPECTED_EVENT!r}."
            )
        expected_workflow_ref = f"{repository}/{_EXPECTED_WORKFLOW}@{_EXPECTED_REF}"
        if workflow_ref != expected_workflow_ref:
            raise ValueError("workflow_ref не совпадает с trusted weighted smoke workflow.")

        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "commit_sha", commit_sha)
        object.__setattr__(self, "ref", ref)
        object.__setattr__(self, "event_name", event_name)
        object.__setattr__(self, "workflow_ref", workflow_ref)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "run_attempt", run_attempt)


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} не может быть bool.")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} должен быть положительным целым числом.") from exc
    if result <= 0:
        raise ValueError(f"{label} должен быть положительным целым числом.")
    return result


def _validate_repository(value: str) -> None:
    parts = value.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError("repository должен иметь форму owner/name.")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    for part in parts:
        if len(part) > 100 or any(character not in allowed for character in part):
            raise ValueError("repository содержит недопустимые символы.")


def _validate_hex(value: object, *, length: int, label: str) -> str:
    text = str(value or "").strip().casefold()
    if len(text) != length or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{label} должен быть {length}-символьным hex.")
    return text


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} должен быть JSON-объектом.")
    return value


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} должен быть JSON-массивом.")
    return value


def _required(mapping: Mapping[str, Any], key: str, label: str) -> Any:
    if key not in mapping:
        raise ValueError(f"{label} не содержит обязательный key {key!r}.")
    return mapping[key]


def _finite_number(value: object, label: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} не может быть bool.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} должен быть конечным числом.") from exc
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{label} должен быть конечным числом >= {minimum}.")
    return result


def _aware_timestamp(value: object, label: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} должен быть ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} должен содержать timezone.")
    return text


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest_payload(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def load_weighted_tts_report(path: Path, *, label: str) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        raise RuntimeError(f"{label} report не найден.")
    if not 1 <= source.stat().st_size <= 2_000_000:
        raise RuntimeError(f"{label} report имеет недопустимый размер.")
    return _strict_json_object(source.read_text(encoding="utf-8"), label=label)


def _profile_facts(report: Mapping[str, Any], label: str) -> dict[str, str]:
    profile = _object(_required(report, "profile", label), f"{label}.profile")
    source = _object(_required(profile, "source", f"{label}.profile"), f"{label}.source")
    facts = {
        "profile_id": str(_required(profile, "profile_id", f"{label}.profile")),
        "backend_id": str(_required(profile, "backend_id", f"{label}.profile")),
        "model_revision": str(
            _required(profile, "model_revision", f"{label}.profile")
        ),
        "profile_fingerprint": _validate_hex(
            _required(profile, "profile_fingerprint", f"{label}.profile"),
            length=64,
            label=f"{label}.profile_fingerprint",
        ),
        "source_kind": str(_required(source, "source_kind", f"{label}.source")),
        "source_sha256": str(source.get("source_sha256") or ""),
        "manifest_policy": str(
            _required(source, "manifest_policy", f"{label}.source")
        ),
    }
    if not all(facts[key] for key in ("profile_id", "backend_id", "model_revision")):
        raise ValueError(f"{label}.profile содержит пустую identity.")
    if facts["source_sha256"]:
        facts["source_sha256"] = _validate_hex(
            facts["source_sha256"],
            length=64,
            label=f"{label}.source_sha256",
        )
    return facts


def _model_facts(report: Mapping[str, Any], label: str) -> dict[str, Any]:
    model = _object(_required(report, "model", label), f"{label}.model")
    present = model.get("config_present") is True
    sha = str(model.get("config_sha256") or "")
    if present:
        sha = _validate_hex(sha, length=64, label=f"{label}.model.config_sha256")
    elif sha:
        raise ValueError(f"{label}.model содержит SHA без config_present=true.")
    return {"config_present": present, "config_sha256": sha}


def _validate_source_reports(
    doctor: Mapping[str, Any],
    smoke: Mapping[str, Any],
    *,
    forbidden_values: tuple[str, ...],
) -> dict[str, Any]:
    _assert_privacy_allowlist(doctor, forbidden_values)
    _assert_privacy_allowlist(smoke, forbidden_values)

    if doctor.get("schema_version") != 1:
        raise ValueError("Runner doctor report schema_version должен быть 1.")
    if doctor.get("policy") != TTS_WEIGHTED_SMOKE_RUNNER_POLICY:
        raise ValueError("Runner doctor report имеет неизвестную policy.")
    if doctor.get("report_policy") != TTS_WEIGHTED_SMOKE_RUNNER_REPORT_POLICY:
        raise ValueError("Runner doctor report имеет неизвестную report_policy.")
    if doctor.get("passed") is not True:
        raise ValueError("Runner doctor report не прошёл.")

    if smoke.get("schema_version") != 1:
        raise ValueError("Weighted smoke report schema_version должен быть 1.")
    if smoke.get("policy") != TTS_WEIGHTED_SMOKE_POLICY:
        raise ValueError("Weighted smoke report имеет неизвестную policy.")
    if smoke.get("report_policy") != TTS_WEIGHTED_SMOKE_REPORT_POLICY:
        raise ValueError("Weighted smoke report имеет неизвестную report_policy.")
    if smoke.get("passed") is not True:
        raise ValueError("Weighted smoke report не прошёл.")

    doctor_profile = _profile_facts(doctor, "doctor")
    smoke_profile = _profile_facts(smoke, "smoke")
    if doctor_profile != smoke_profile:
        raise ValueError("Doctor и synthesis reports относятся к разным TTS profiles.")

    doctor_model = _model_facts(doctor, "doctor")
    smoke_model = _model_facts(smoke, "smoke")
    if doctor_model != smoke_model:
        raise ValueError("Doctor и synthesis reports относятся к разным model configs.")

    doctor_backend = _object(_required(doctor, "backend", "doctor"), "doctor.backend")
    smoke_backend = _object(_required(smoke, "backend", "smoke"), "smoke.backend")
    backend_id = str(_required(smoke_backend, "backend_id", "smoke.backend"))
    adapter_policy = str(
        _required(smoke_backend, "adapter_policy", "smoke.backend")
    )
    if backend_id != doctor_profile["backend_id"]:
        raise ValueError("Synthesis backend не совпадает с profile backend.")
    if str(doctor_backend.get("backend_id") or "") != backend_id:
        raise ValueError("Doctor backend не совпадает с synthesis backend.")
    if str(doctor_backend.get("adapter_policy") or "") != adapter_policy:
        raise ValueError("Doctor adapter policy не совпадает с synthesis adapter policy.")

    doctor_runtime = _object(
        _required(doctor, "runtime", "doctor"), "doctor.runtime"
    )
    if doctor_runtime.get("weights_loaded") is not False:
        raise ValueError("Runner doctor не должен загружать model weights.")
    if doctor_runtime.get("session_opened") is not False:
        raise ValueError("Runner doctor не должен открывать synthesis session.")

    storage = _object(_required(doctor, "storage", "doctor"), "doctor.storage")
    for key in ("write", "fsync", "replace", "readback", "cleanup"):
        if storage.get(key) is not True:
            raise ValueError(f"Runner doctor storage probe не подтвердил {key}.")

    imports = _object(_required(doctor, "imports", "doctor"), "doctor.imports")
    modules = _list(_required(imports, "modules", "doctor.imports"), "doctor.modules")
    module_names = sorted(
        str(_required(_object(item, "doctor.module"), "name", "doctor.module"))
        for item in modules
    )
    if not module_names or any(not name for name in module_names):
        raise ValueError("Runner doctor не подтвердил required runtime modules.")

    ffprobe = _object(_required(doctor, "ffprobe", "doctor"), "doctor.ffprobe")
    if ffprobe.get("available") is not True:
        raise ValueError("Runner doctor не подтвердил ffprobe.")
    ffprobe_version = str(_required(ffprobe, "version", "doctor.ffprobe"))
    if not ffprobe_version:
        raise ValueError("Runner doctor не зафиксировал ffprobe version.")

    output = _object(_required(smoke, "output", "smoke"), "smoke.output")
    if output.get("audio_retained") is not False:
        raise ValueError("Weighted smoke report не подтверждает удаление audio.")
    pcm = _object(_required(output, "pcm", "smoke.output"), "smoke.output.pcm")
    readback = _object(
        _required(output, "readback", "smoke.output"), "smoke.output.readback"
    )
    output_probe = _object(
        _required(output, "ffprobe", "smoke.output"), "smoke.output.ffprobe"
    )
    duration = _finite_number(
        _required(pcm, "duration_seconds", "smoke.output.pcm"),
        "smoke.output.pcm.duration_seconds",
        minimum=0.2,
    )
    sample_rate = _positive_int(
        _required(pcm, "sample_rate", "smoke.output.pcm"),
        "smoke.output.pcm.sample_rate",
    )
    if readback.get("subtype") != "PCM_24":
        raise ValueError("Weighted smoke read-back subtype должен быть PCM_24.")
    if _positive_int(output_probe.get("channels"), "smoke.output.ffprobe.channels") != 1:
        raise ValueError("Weighted smoke output должен быть mono.")
    if _positive_int(
        output_probe.get("sample_rate"), "smoke.output.ffprobe.sample_rate"
    ) != sample_rate:
        raise ValueError("PCM и ffprobe sample rate не совпадают.")

    execution = _object(
        _required(smoke, "execution_plan", "smoke"), "smoke.execution_plan"
    )
    execution_required = execution.get("required") is True
    execution_present = execution.get("present") is True
    if execution_required and not execution_present:
        raise ValueError("Required execution-plan evidence отсутствует.")

    synthesis_runtime = _object(
        _required(smoke, "runtime", "smoke"), "smoke.runtime"
    )
    synthesis_seconds = _finite_number(
        _required(synthesis_runtime, "synthesis_seconds", "smoke.runtime"),
        "smoke.runtime.synthesis_seconds",
        minimum=0.000001,
    )
    total_seconds = _finite_number(
        _required(synthesis_runtime, "total_seconds", "smoke.runtime"),
        "smoke.runtime.total_seconds",
        minimum=synthesis_seconds,
    )

    doctor_completed = _aware_timestamp(doctor.get("completed_at"), "doctor.completed_at")
    smoke_completed = _aware_timestamp(smoke.get("completed_at"), "smoke.completed_at")
    doctor_dt = datetime.fromisoformat(doctor_completed.replace("Z", "+00:00"))
    smoke_dt = datetime.fromisoformat(smoke_completed.replace("Z", "+00:00"))
    if doctor_dt > smoke_dt:
        raise ValueError("Runner doctor timestamp не может быть позже synthesis.")

    source_sha = doctor_profile["source_sha256"]
    return {
        "profile_id": doctor_profile["profile_id"],
        "backend_id": backend_id,
        "adapter_policy": adapter_policy,
        "model_revision": doctor_profile["model_revision"],
        "profile_fingerprint": doctor_profile["profile_fingerprint"],
        "source_kind": doctor_profile["source_kind"],
        "source_sha256": source_sha,
        "manifest_policy": doctor_profile["manifest_policy"],
        "model_config_present": doctor_model["config_present"],
        "model_config_sha256": doctor_model["config_sha256"],
        "doctor_completed_at": doctor_completed,
        "smoke_completed_at": smoke_completed,
        "runtime_modules": module_names,
        "ffprobe_version": ffprobe_version,
        "output_duration_seconds": round(duration, 6),
        "output_sample_rate": sample_rate,
        "output_subtype": "PCM_24",
        "synthesis_seconds": round(synthesis_seconds, 6),
        "total_seconds": round(total_seconds, 6),
        "execution_required": execution_required,
        "execution_present": execution_present,
        "execution_policy": str(execution.get("policy") or ""),
        "planned_max_len": int(execution.get("planned_max_len") or 0),
        "executed_max_len": int(execution.get("executed_max_len") or 0),
    }


def build_weighted_tts_attestation(
    doctor_report: Mapping[str, Any],
    smoke_report: Mapping[str, Any],
    context: WeightedTTSSmokeAttestationContext,
    *,
    forbidden_values: tuple[str, ...] = (),
) -> dict[str, Any]:
    if not isinstance(context, WeightedTTSSmokeAttestationContext):
        raise TypeError("context должен быть WeightedTTSSmokeAttestationContext.")
    facts = _validate_source_reports(
        _object(doctor_report, "doctor_report"),
        _object(smoke_report, "smoke_report"),
        forbidden_values=forbidden_values,
    )
    statement = {
        "schema_version": TTS_WEIGHTED_SMOKE_ATTESTATION_SCHEMA_VERSION,
        "policy": TTS_WEIGHTED_SMOKE_ATTESTATION_POLICY,
        "subject": {
            "repository": context.repository,
            "commit_sha": context.commit_sha,
            "ref": context.ref,
            "event_name": context.event_name,
            "workflow_ref": context.workflow_ref,
            "run_id": context.run_id,
            "run_attempt": context.run_attempt,
        },
        "result": {
            "passed": True,
            "doctor_policy": TTS_WEIGHTED_SMOKE_RUNNER_POLICY,
            "smoke_policy": TTS_WEIGHTED_SMOKE_POLICY,
            **facts,
            "doctor_weights_loaded": False,
            "doctor_session_opened": False,
            "audio_retained": False,
        },
    }
    attestation = {**statement, "digest_sha256": _digest_payload(statement)}
    validate_weighted_tts_attestation(attestation, forbidden_values=forbidden_values)
    return attestation


def validate_weighted_tts_attestation(
    payload: Mapping[str, Any],
    *,
    forbidden_values: tuple[str, ...] = (),
) -> None:
    attestation = _object(payload, "attestation")
    expected_top = {"schema_version", "policy", "subject", "result", "digest_sha256"}
    if set(attestation) != expected_top:
        raise ValueError("Attestation содержит неизвестные или отсутствующие top-level keys.")
    if attestation.get("schema_version") != TTS_WEIGHTED_SMOKE_ATTESTATION_SCHEMA_VERSION:
        raise ValueError("Attestation schema_version не поддерживается.")
    if attestation.get("policy") != TTS_WEIGHTED_SMOKE_ATTESTATION_POLICY:
        raise ValueError("Attestation policy не поддерживается.")

    subject = _object(attestation.get("subject"), "attestation.subject")
    context = WeightedTTSSmokeAttestationContext(
        repository=subject.get("repository", ""),
        commit_sha=subject.get("commit_sha", ""),
        ref=subject.get("ref", ""),
        event_name=subject.get("event_name", ""),
        workflow_ref=subject.get("workflow_ref", ""),
        run_id=subject.get("run_id", 0),
        run_attempt=subject.get("run_attempt", 0),
    )
    del context

    result = _object(attestation.get("result"), "attestation.result")
    if result.get("passed") is not True:
        raise ValueError("Attestation result не прошёл.")
    if result.get("doctor_weights_loaded") is not False:
        raise ValueError("Attestation утверждает загрузку весов в doctor.")
    if result.get("doctor_session_opened") is not False:
        raise ValueError("Attestation утверждает открытие session в doctor.")
    if result.get("audio_retained") is not False:
        raise ValueError("Attestation утверждает сохранение audio.")
    _validate_hex(result.get("profile_fingerprint"), length=64, label="profile_fingerprint")
    if result.get("source_sha256"):
        _validate_hex(result["source_sha256"], length=64, label="source_sha256")
    if result.get("model_config_present") is True:
        _validate_hex(
            result.get("model_config_sha256"),
            length=64,
            label="model_config_sha256",
        )
    _positive_int(result.get("output_sample_rate"), "output_sample_rate")
    _finite_number(result.get("output_duration_seconds"), "output_duration_seconds", minimum=0.2)
    _finite_number(result.get("synthesis_seconds"), "synthesis_seconds", minimum=0.000001)
    _finite_number(result.get("total_seconds"), "total_seconds", minimum=0.000001)

    digest = _validate_hex(
        attestation.get("digest_sha256"), length=64, label="digest_sha256"
    )
    statement = {key: value for key, value in attestation.items() if key != "digest_sha256"}
    if digest != _digest_payload(statement):
        raise ValueError("Attestation digest не совпадает с payload.")
    _assert_privacy_allowlist(attestation, forbidden_values)


def write_weighted_tts_attestation(path: Path, payload: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    validate_weighted_tts_attestation(payload)
    _atomic_report(destination, payload)


__all__ = [
    "TTS_WEIGHTED_SMOKE_ATTESTATION_POLICY",
    "TTS_WEIGHTED_SMOKE_ATTESTATION_SCHEMA_VERSION",
    "WeightedTTSSmokeAttestationContext",
    "build_weighted_tts_attestation",
    "load_weighted_tts_report",
    "validate_weighted_tts_attestation",
    "write_weighted_tts_attestation",
]
