#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict, code-free loader for repository-owned TTS model profile manifests."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from services.speech_backends.model_profiles import ModelOptionSpec, SpeechModelProfile

PROFILE_MANIFEST_POLICY = "repo-owned-tts-profile-manifest-v1"
PROFILE_MANIFEST_SCHEMA_VERSION = 1
_MAX_MANIFEST_BYTES = 128 * 1024
_PROFILE_KEYS = {
    "schema_version",
    "profile_id",
    "backend_id",
    "display_name",
    "model_family",
    "model_revision",
    "aliases",
    "production_enabled",
    "required_capabilities",
    "option_specs",
    "backend_defaults",
    "backend_override_keys",
    "requires_execution_plan_evidence",
}
_REQUIRED_PROFILE_KEYS = {
    "schema_version",
    "profile_id",
    "backend_id",
    "display_name",
    "model_family",
    "model_revision",
    "option_specs",
    "backend_defaults",
}
_OPTION_KEYS = {
    "name",
    "value_type",
    "default",
    "minimum",
    "maximum",
    "choices",
    "overridable",
}
_REQUIRED_OPTION_KEYS = {"name", "value_type", "default"}


class ProfileManifestError(RuntimeError):
    """Raised when a declarative TTS profile manifest is unsafe or invalid."""


@dataclass(frozen=True)
class ProfileManifestRecord:
    profile: SpeechModelProfile
    source_path: Path
    source_sha256: str
    schema_version: int = PROFILE_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_path", Path(self.source_path).resolve())
        digest = str(self.source_sha256 or "").strip().casefold()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("source_sha256 должен быть SHA-256 hex.")
        object.__setattr__(self, "source_sha256", digest)

    def as_dict(self, *, root: Path | None = None) -> dict[str, Any]:
        source = self.source_path
        if root is not None:
            try:
                source_value = str(source.relative_to(Path(root).resolve()))
            except ValueError:
                source_value = str(source)
        else:
            source_value = str(source)
        return {
            "schema_version": self.schema_version,
            "source": source_value,
            "source_sha256": self.source_sha256,
            "profile": self.profile.as_dict(),
            "manifest_policy": PROFILE_MANIFEST_POLICY,
        }


def default_profile_manifest_root() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "tts_models"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reject_constant(value: str) -> None:
    raise ProfileManifestError(f"JSON constant запрещён: {value}.")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProfileManifestError(f"JSON содержит дублирующийся ключ: {key!r}.")
        result[key] = value
    return result


def _json_object(data: bytes, *, path: Path) -> dict[str, Any]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ProfileManifestError(f"Manifest не UTF-8: {path}") from exc
    try:
        payload = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except ProfileManifestError:
        raise
    except json.JSONDecodeError as exc:
        raise ProfileManifestError(f"Некорректный JSON manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProfileManifestError(f"TTS manifest должен быть JSON-объектом: {path}")
    return payload


def _exact_keys(
    payload: dict[str, Any],
    *,
    allowed: set[str],
    required: set[str],
    context: str,
) -> None:
    unknown = sorted(set(payload) - allowed)
    missing = sorted(required - set(payload))
    if unknown:
        raise ProfileManifestError(
            f"{context} содержит неизвестные поля: {', '.join(unknown)}."
        )
    if missing:
        raise ProfileManifestError(
            f"{context} не содержит обязательные поля: {', '.join(missing)}."
        )


def _string_list(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ProfileManifestError(f"{field_name} должен быть JSON-массивом строк.")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ProfileManifestError(f"{field_name} содержит нестроковое/пустое значение.")
        result.append(item.strip())
    return tuple(result)


def _bool(value: Any, *, field_name: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ProfileManifestError(f"{field_name} должен быть bool.")
    return value


def _option_specs(value: Any, *, context: str) -> tuple[ModelOptionSpec, ...]:
    if not isinstance(value, list):
        raise ProfileManifestError(f"{context}.option_specs должен быть JSON-массивом.")
    specs: list[ModelOptionSpec] = []
    for index, raw in enumerate(value):
        item_context = f"{context}.option_specs[{index}]"
        if not isinstance(raw, dict):
            raise ProfileManifestError(f"{item_context} должен быть JSON-объектом.")
        _exact_keys(
            raw,
            allowed=_OPTION_KEYS,
            required=_REQUIRED_OPTION_KEYS,
            context=item_context,
        )
        choices = raw.get("choices", [])
        if not isinstance(choices, list):
            raise ProfileManifestError(f"{item_context}.choices должен быть массивом.")
        try:
            specs.append(
                ModelOptionSpec(
                    name=raw["name"],
                    value_type=raw["value_type"],
                    default=raw["default"],
                    minimum=raw.get("minimum"),
                    maximum=raw.get("maximum"),
                    choices=tuple(choices),
                    overridable=_bool(
                        raw.get("overridable"),
                        field_name=f"{item_context}.overridable",
                        default=True,
                    ),
                )
            )
        except (TypeError, ValueError) as exc:
            raise ProfileManifestError(f"Некорректный {item_context}: {exc}") from exc
    return tuple(specs)


def _profile_from_payload(payload: dict[str, Any], *, path: Path) -> SpeechModelProfile:
    context = f"TTS manifest {path.name}"
    _exact_keys(
        payload,
        allowed=_PROFILE_KEYS,
        required=_REQUIRED_PROFILE_KEYS,
        context=context,
    )
    schema = payload.get("schema_version")
    if isinstance(schema, bool) or schema != PROFILE_MANIFEST_SCHEMA_VERSION:
        raise ProfileManifestError(
            f"{context}.schema_version должен быть {PROFILE_MANIFEST_SCHEMA_VERSION}."
        )
    backend_defaults = payload.get("backend_defaults")
    if not isinstance(backend_defaults, dict):
        raise ProfileManifestError(f"{context}.backend_defaults должен быть JSON-объектом.")
    try:
        profile = SpeechModelProfile(
            profile_id=payload["profile_id"],
            backend_id=payload["backend_id"],
            display_name=payload["display_name"],
            model_family=payload["model_family"],
            model_revision=payload["model_revision"],
            aliases=_string_list(payload.get("aliases"), field_name=f"{context}.aliases"),
            production_enabled=_bool(
                payload.get("production_enabled"),
                field_name=f"{context}.production_enabled",
                default=True,
            ),
            required_capabilities=_string_list(
                payload.get("required_capabilities"),
                field_name=f"{context}.required_capabilities",
            ),
            option_specs=_option_specs(payload["option_specs"], context=context),
            backend_defaults=dict(backend_defaults),
            backend_override_keys=_string_list(
                payload.get("backend_override_keys"),
                field_name=f"{context}.backend_override_keys",
            ),
            requires_execution_plan_evidence=_bool(
                payload.get("requires_execution_plan_evidence"),
                field_name=f"{context}.requires_execution_plan_evidence",
                default=False,
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ProfileManifestError(f"Некорректный {context}: {exc}") from exc
    if path.stem.casefold() != profile.profile_id:
        raise ProfileManifestError(
            f"Имя файла {path.name} должно совпадать с profile_id={profile.profile_id}."
        )
    return profile


def load_profile_manifest(
    path: Path,
    *,
    catalog_root: Path | None = None,
) -> ProfileManifestRecord:
    root = Path(catalog_root or default_profile_manifest_root()).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise ProfileManifestError(f"Symlink TTS manifest запрещён: {candidate}")
    resolved = candidate.resolve()
    if resolved.parent != root:
        raise ProfileManifestError(
            f"TTS manifest должен находиться непосредственно в catalog root: {resolved}"
        )
    if resolved.suffix.casefold() != ".json":
        raise ProfileManifestError(f"TTS manifest должен иметь расширение .json: {resolved}")
    if not resolved.is_file():
        raise ProfileManifestError(f"TTS manifest не найден: {resolved}")
    size = resolved.stat().st_size
    if size <= 0 or size > _MAX_MANIFEST_BYTES:
        raise ProfileManifestError(
            f"Размер TTS manifest вне диапазона 1..{_MAX_MANIFEST_BYTES}: {resolved}"
        )
    data = resolved.read_bytes()
    profile = _profile_from_payload(_json_object(data, path=resolved), path=resolved)
    return ProfileManifestRecord(
        profile=profile,
        source_path=resolved,
        source_sha256=_sha256_bytes(data),
    )


def load_profile_catalog(
    root: Path | None = None,
    *,
    require_profiles: bool = True,
) -> tuple[ProfileManifestRecord, ...]:
    catalog_root = Path(root or default_profile_manifest_root()).resolve()
    if not catalog_root.is_dir():
        raise ProfileManifestError(f"TTS profile catalog directory не найден: {catalog_root}")
    paths = sorted(catalog_root.glob("*.json"), key=lambda item: item.name.casefold())
    if require_profiles and not paths:
        raise ProfileManifestError(f"TTS profile catalog пуст: {catalog_root}")
    records = tuple(
        load_profile_manifest(path, catalog_root=catalog_root)
        for path in paths
    )
    profile_ids: set[str] = set()
    aliases: dict[str, str] = {}
    for record in records:
        profile = record.profile
        if profile.profile_id in profile_ids:
            raise ProfileManifestError(
                f"Дублирующийся profile_id в catalog: {profile.profile_id}"
            )
        profile_ids.add(profile.profile_id)
        for alias in (profile.profile_id, *profile.aliases):
            owner = aliases.get(alias)
            if owner is not None and owner != profile.profile_id:
                raise ProfileManifestError(
                    f"Alias {alias} занят profiles {owner} и {profile.profile_id}."
                )
            aliases[alias] = profile.profile_id
    return records


def catalog_snapshot(
    records: tuple[ProfileManifestRecord, ...],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    catalog_root = Path(root or default_profile_manifest_root()).resolve()
    return {
        "schema_version": 1,
        "policy": PROFILE_MANIFEST_POLICY,
        "catalog_root": str(catalog_root),
        "profile_count": len(records),
        "profiles": [record.as_dict(root=catalog_root) for record in records],
    }


__all__ = [
    "PROFILE_MANIFEST_POLICY",
    "PROFILE_MANIFEST_SCHEMA_VERSION",
    "ProfileManifestError",
    "ProfileManifestRecord",
    "catalog_snapshot",
    "default_profile_manifest_root",
    "load_profile_catalog",
    "load_profile_manifest",
]
