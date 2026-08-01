#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safe selection and durable binding of production TTS model profiles."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
from typing import Any

from services.dub_studio import DubStore, utc_now
from services.speech_backends import (
    DEFAULT_BACKEND_ID,
    DEFAULT_MODEL_PROFILE_ID,
    SpeechModelProfile,
    get_model_profile,
    model_profile_source_evidence,
    normalize_production_speech_request,
    registered_model_profiles,
    resolve_model_profile_id,
    select_production_speech,
)

TTS_PROFILE_SELECTION_POLICY = "durable-production-tts-profile-selection-v1"
TTS_PROJECT_REBIND_POLICY = "inactive-project-tts-profile-rebind-v1"
_MAX_REQUEST_BYTES = 1_000_000
_REBINDABLE_PROJECT_STATES = {"draft", "failed", "cancelled"}
_ACTIVE_JOB_STATES = {"queued", "running", "cancel_requested"}
_TTS_RESERVED_KEYS = {
    "speech_backend",
    "speech_model_profile",
    "speech_options",
    "speech_backend_config",
    "speech_profile_fingerprint",
}


@dataclass(frozen=True)
class ProductionTTSProfileChoice:
    profile_id: str
    backend_id: str
    display_name: str
    model_family: str
    model_revision: str
    fingerprint: str
    is_default: bool
    source_kind: str
    source: str
    source_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "backend_id": self.backend_id,
            "display_name": self.display_name,
            "model_family": self.model_family,
            "model_revision": self.model_revision,
            "fingerprint": self.fingerprint,
            "is_default": self.is_default,
            "source_kind": self.source_kind,
            "source": self.source,
            "source_sha256": self.source_sha256,
            "selection_policy": TTS_PROFILE_SELECTION_POLICY,
        }


@dataclass(frozen=True)
class ProjectTTSProfileRebindResult:
    project_id: str
    previous_profile_id: str
    choice: ProductionTTSProfileChoice
    changed: bool
    request: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "request", dict(self.request))

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "previous_profile_id": self.previous_profile_id,
            "choice": self.choice.as_dict(),
            "changed": self.changed,
            "request": dict(self.request),
            "rebind_policy": TTS_PROJECT_REBIND_POLICY,
        }


def _choice(profile: SpeechModelProfile) -> ProductionTTSProfileChoice:
    # Selection is exercised here, not merely inferred from registry presence.
    selection = select_production_speech(
        profile.backend_id,
        profile.profile_id,
        request={"speech_model_profile": profile.profile_id},
        default_backend_id=DEFAULT_BACKEND_ID,
        default_model_profile_id=DEFAULT_MODEL_PROFILE_ID,
    )
    evidence = model_profile_source_evidence(profile.profile_id)
    return ProductionTTSProfileChoice(
        profile_id=selection.model_profile.profile_id,
        backend_id=selection.backend_id,
        display_name=selection.model_profile.display_name,
        model_family=selection.model_profile.model_family,
        model_revision=selection.model_profile.model_revision,
        fingerprint=selection.model_profile.fingerprint(),
        is_default=selection.model_profile.profile_id == DEFAULT_MODEL_PROFILE_ID,
        source_kind=str(evidence.get("source_kind") or ""),
        source=str(evidence.get("source") or ""),
        source_sha256=str(evidence.get("source_sha256") or ""),
    )


def production_tts_profile_choices() -> tuple[ProductionTTSProfileChoice, ...]:
    """Return every selectable production profile, default first."""
    choices = tuple(
        _choice(profile)
        for profile in registered_model_profiles()
        if profile.production_enabled
    )
    if not choices:
        raise RuntimeError("Нет ни одного production-enabled TTS model profile.")
    if not any(choice.profile_id == DEFAULT_MODEL_PROFILE_ID for choice in choices):
        raise RuntimeError(
            f"Default TTS profile отсутствует в production catalog: {DEFAULT_MODEL_PROFILE_ID}"
        )
    return tuple(
        sorted(
            choices,
            key=lambda item: (
                not item.is_default,
                item.display_name.casefold(),
                item.profile_id,
            ),
        )
    )


def production_tts_profile_choice(value: object) -> ProductionTTSProfileChoice:
    profile = get_model_profile(value)
    if not profile.production_enabled:
        raise RuntimeError(f"TTS profile отключён для production: {profile.profile_id}")
    return _choice(profile)


def _all_tts_flat_keys() -> set[str]:
    result = set(_TTS_RESERVED_KEYS)
    for profile in registered_model_profiles():
        result.update(spec.name for spec in profile.option_specs)
        result.update(str(key) for key in profile.backend_defaults)
    return result


def rebind_production_tts_profile(
    payload: Mapping[str, Any],
    profile_value: object,
) -> dict[str, Any]:
    """Replace old TTS-owned fields and return one normalized durable request."""
    if not isinstance(payload, Mapping):
        raise ValueError("Dub request должен быть JSON-объектом.")
    profile = get_model_profile(profile_value)
    if not profile.production_enabled:
        raise RuntimeError(f"TTS profile отключён для production: {profile.profile_id}")

    request = dict(payload)
    for key in _all_tts_flat_keys():
        request.pop(key, None)
    request["speech_backend"] = profile.backend_id
    request["speech_model_profile"] = profile.profile_id
    normalized = normalize_production_speech_request(
        request,
        default_backend_id=DEFAULT_BACKEND_ID,
        default_model_profile_id=DEFAULT_MODEL_PROFILE_ID,
    )
    if normalized.get("speech_model_profile") != profile.profile_id:
        raise RuntimeError("TTS profile normalizer вернул другой profile_id.")
    if normalized.get("speech_backend") != profile.backend_id:
        raise RuntimeError("TTS profile normalizer вернул другой backend_id.")
    return normalized


def normalize_new_production_tts_request(
    payload: Mapping[str, Any],
    profile_value: object,
) -> dict[str, Any]:
    """Normalize a new request while retaining explicitly supplied TTS overrides."""
    if not isinstance(payload, Mapping):
        raise ValueError("Dub request должен быть JSON-объектом.")
    profile = get_model_profile(profile_value)
    if not profile.production_enabled:
        raise RuntimeError(f"TTS profile отключён для production: {profile.profile_id}")
    request = dict(payload)
    request.pop("speech_profile_fingerprint", None)
    request["speech_backend"] = profile.backend_id
    request["speech_model_profile"] = profile.profile_id
    return normalize_production_speech_request(
        request,
        default_backend_id=DEFAULT_BACKEND_ID,
        default_model_profile_id=DEFAULT_MODEL_PROFILE_ID,
    )


def _reject_constant(value: str) -> Any:
    raise ValueError(f"JSON constant запрещён: {value}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON содержит дублирующийся ключ: {key}")
        result[key] = value
    return result


def read_durable_request(path: Path) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"request.json не найден: {target}")
    size = target.stat().st_size
    if not 1 <= size <= _MAX_REQUEST_BYTES:
        raise ValueError(f"Некорректный размер request.json: {size} bytes.")
    try:
        payload = json.loads(
            target.read_text(encoding="utf-8-sig"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Некорректный request.json: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("request.json должен содержать JSON-объект.")
    return payload


def write_durable_request(path: Path, payload: Mapping[str, Any]) -> None:
    if not isinstance(payload, Mapping):
        raise ValueError("Durable request должен быть JSON-объектом.")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        dict(payload),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ) + "\n"
    if len(serialized.encode("utf-8")) > _MAX_REQUEST_BYTES:
        raise ValueError("Durable request превышает допустимый размер.")
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    )
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def rebind_durable_request_file(path: Path, profile_value: object) -> dict[str, Any]:
    current = read_durable_request(path)
    rebound = rebind_production_tts_profile(current, profile_value)
    write_durable_request(path, rebound)
    return rebound


def rebind_inactive_project_tts_profile(
    store: DubStore,
    project_id: str,
    *,
    owner_user_id: int,
    request_path: Path,
    profile_value: object,
) -> ProjectTTSProfileRebindResult:
    """Rebind one inactive project with a durable file/DB compensation barrier."""
    if not isinstance(store, DubStore):
        raise TypeError("store должен быть DubStore.")
    project = store.get_project(project_id)
    if int(project.get("owner_user_id") or 0) != int(owner_user_id):
        raise PermissionError("Это не ваш Dub Studio проект.")
    project_status = str(project.get("status") or "").strip().lower()
    if project_status not in _REBINDABLE_PROJECT_STATES:
        raise RuntimeError(
            "TTS-профиль можно менять только у draft/failed/cancelled проекта; "
            f"текущий status={project_status or 'unknown'}."
        )

    target = Path(request_path).resolve()
    current = read_durable_request(target)
    choice = production_tts_profile_choice(profile_value)
    previous_raw = current.get("speech_model_profile") or DEFAULT_MODEL_PROFILE_ID
    try:
        previous_profile_id = resolve_model_profile_id(previous_raw)
    except (RuntimeError, ValueError):
        previous_profile_id = str(previous_raw or "unknown")
    current_fingerprint = str(current.get("speech_profile_fingerprint") or "")
    if (
        previous_profile_id == choice.profile_id
        and current_fingerprint == choice.fingerprint
    ):
        return ProjectTTSProfileRebindResult(
            project_id=str(project["id"]),
            previous_profile_id=previous_profile_id,
            choice=choice,
            changed=False,
            request=current,
        )

    rebound = rebind_production_tts_profile(current, choice.profile_id)
    request_written = False
    try:
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT owner_user_id, status, metadata_json
                FROM dub_projects WHERE id=?
                """,
                (str(project["id"]),),
            ).fetchone()
            if row is None:
                raise KeyError(f"Проект не найден: {project['id']}")
            if int(row["owner_user_id"] or 0) != int(owner_user_id):
                raise PermissionError("Владелец проекта изменился во время TTS rebind.")
            locked_status = str(row["status"] or "").strip().lower()
            if locked_status not in _REBINDABLE_PROJECT_STATES:
                raise RuntimeError(
                    "Project status изменился во время TTS rebind: "
                    f"{locked_status or 'unknown'}."
                )
            active = conn.execute(
                """
                SELECT id, status FROM dub_jobs
                WHERE project_id=? AND status IN ('queued','running','cancel_requested')
                ORDER BY id DESC LIMIT 1
                """,
                (str(project["id"]),),
            ).fetchone()
            if active is not None:
                raise RuntimeError(
                    "Нельзя менять TTS-профиль при active job "
                    f"#{int(active['id'])} ({active['status']})."
                )

            try:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
            except json.JSONDecodeError as exc:
                raise RuntimeError("metadata_json проекта повреждён.") from exc
            if not isinstance(metadata, dict):
                raise RuntimeError("metadata_json проекта должен быть JSON-объектом.")
            metadata.update(
                {
                    "speech_backend": choice.backend_id,
                    "speech_model_profile": choice.profile_id,
                    "speech_model_revision": choice.model_revision,
                    "speech_profile_fingerprint": choice.fingerprint,
                }
            )

            write_durable_request(target, rebound)
            request_written = True
            now = utc_now()
            conn.execute(
                """
                UPDATE dub_projects
                SET metadata_json=?, stage='tts_profile_rebound', progress=0,
                    last_error='', updated_at=?
                WHERE id=?
                """,
                (
                    json.dumps(
                        metadata,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                    now,
                    str(project["id"]),
                ),
            )
            store._insert_event(
                conn,
                str(project["id"]),
                None,
                "tts_profile_rebound",
                "info",
                (
                    f"TTS profile изменён: {previous_profile_id} -> "
                    f"{choice.profile_id} ({choice.model_revision})"
                ),
                {
                    "previous_profile_id": previous_profile_id,
                    "profile_id": choice.profile_id,
                    "backend_id": choice.backend_id,
                    "model_revision": choice.model_revision,
                    "profile_fingerprint": choice.fingerprint,
                    "policy": TTS_PROJECT_REBIND_POLICY,
                },
            )
            conn.commit()
    except Exception as exc:
        if request_written:
            try:
                write_durable_request(target, current)
            except Exception as restore_exc:
                raise RuntimeError(
                    "TTS rebind не завершён, и старый request.json не удалось "
                    "восстановить. Требуется ручная проверка проекта."
                ) from restore_exc
        raise exc

    return ProjectTTSProfileRebindResult(
        project_id=str(project["id"]),
        previous_profile_id=previous_profile_id,
        choice=choice,
        changed=True,
        request=rebound,
    )


__all__ = [
    "TTS_PROFILE_SELECTION_POLICY",
    "TTS_PROJECT_REBIND_POLICY",
    "ProductionTTSProfileChoice",
    "ProjectTTSProfileRebindResult",
    "normalize_new_production_tts_request",
    "production_tts_profile_choice",
    "production_tts_profile_choices",
    "read_durable_request",
    "rebind_durable_request_file",
    "rebind_inactive_project_tts_profile",
    "rebind_production_tts_profile",
    "write_durable_request",
]
