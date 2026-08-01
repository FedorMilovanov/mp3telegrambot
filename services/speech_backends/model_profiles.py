#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed catalog separating TTS adapters from concrete model deployments."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Any

from services.speech_backends.base import REQUIRED_PRODUCTION_CAPABILITIES

MODEL_OPTION_POLICY = "typed-speech-model-option-v1"
MODEL_PROFILE_POLICY = "speech-model-profile-v1"
MODEL_CATALOG_POLICY = "explicit-speech-model-catalog-v1"
_ALLOWED_OPTION_TYPES = {"bool", "float", "int", "str"}
_RESERVED_REQUEST_KEYS = {
    "schema_version",
    "speech_backend",
    "speech_model_profile",
    "speech_options",
    "speech_backend_config",
    "speech_profile_fingerprint",
}


def _normalized_id(value: object, *, field_name: str) -> str:
    result = str(value or "").strip().casefold().replace("_", "-")
    if not 3 <= len(result) <= 96:
        raise ValueError(f"{field_name} должен содержать 3..96 символов.")
    if not result[0].isalnum() or not result[-1].isalnum():
        raise ValueError(f"{field_name} должен начинаться и заканчиваться буквой или цифрой.")
    if any(not (char.isascii() and (char.isalnum() or char == "-")) for char in result):
        raise ValueError(f"{field_name} содержит запрещённые символы.")
    if "--" in result:
        raise ValueError(f"{field_name} не может содержать двойной дефис.")
    return result


def _option_name(value: object) -> str:
    result = str(value or "").strip()
    if not 1 <= len(result) <= 64:
        raise ValueError("Имя TTS option должно содержать 1..64 символа.")
    if not result[0].isalpha():
        raise ValueError("Имя TTS option должно начинаться с буквы.")
    if any(not (char.isascii() and (char.isalnum() or char == "_")) for char in result):
        raise ValueError(f"Некорректное имя TTS option: {result!r}.")
    if result in _RESERVED_REQUEST_KEYS:
        raise ValueError(f"TTS option использует зарезервированное имя: {result}.")
    return result


def _scalar(value: Any, *, field_name: str) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"{field_name} должен быть JSON scalar без NaN/Infinity.")


@dataclass(frozen=True)
class ModelOptionSpec:
    """One validated model/runtime knob exposed by a concrete profile."""

    name: str
    value_type: str
    default: Any
    minimum: float | int | None = None
    maximum: float | int | None = None
    choices: tuple[Any, ...] = ()
    overridable: bool = True

    def __post_init__(self) -> None:
        name = _option_name(self.name)
        value_type = str(self.value_type or "").strip().casefold()
        if value_type not in _ALLOWED_OPTION_TYPES:
            raise ValueError(f"Неподдерживаемый тип TTS option {name}: {value_type!r}.")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "value_type", value_type)
        object.__setattr__(self, "choices", tuple(self.choices))
        default = self.coerce(self.default, source="default")
        object.__setattr__(self, "default", default)
        if self.minimum is not None and self.maximum is not None:
            if float(self.minimum) > float(self.maximum):
                raise ValueError(f"TTS option {name}: minimum больше maximum.")

    def coerce(self, value: Any, *, source: str = "request") -> Any:
        label = f"{source}.{self.name}"
        if self.value_type == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{label} должен быть bool.")
            result: Any = value
        elif self.value_type == "int":
            if isinstance(value, bool):
                raise ValueError(f"{label} не может быть bool.")
            if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
                raise ValueError(f"{label} должен быть целым числом.")
            try:
                result = int(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{label} должен быть целым числом.") from exc
        elif self.value_type == "float":
            if isinstance(value, bool):
                raise ValueError(f"{label} не может быть bool.")
            try:
                result = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{label} должен быть числом.") from exc
            if not math.isfinite(result):
                raise ValueError(f"{label} должен быть конечным числом.")
        else:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} должен быть непустой строкой.")
            result = value.strip()

        if self.minimum is not None and float(result) < float(self.minimum):
            raise ValueError(f"{label} должен быть >= {self.minimum}.")
        if self.maximum is not None and float(result) > float(self.maximum):
            raise ValueError(f"{label} должен быть <= {self.maximum}.")
        if self.choices and result not in self.choices:
            allowed = ", ".join(repr(item) for item in self.choices)
            raise ValueError(f"{label} должен быть одним из: {allowed}.")
        return result

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value_type": self.value_type,
            "default": self.default,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "choices": list(self.choices),
            "overridable": bool(self.overridable),
            "option_policy": MODEL_OPTION_POLICY,
        }


@dataclass(frozen=True)
class SpeechModelResolution:
    profile_id: str
    backend_id: str
    profile_fingerprint: str
    request: Mapping[str, Any]
    options: Mapping[str, Any]
    backend_config: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "request", dict(self.request))
        object.__setattr__(self, "options", dict(self.options))
        object.__setattr__(self, "backend_config", dict(self.backend_config))

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "backend_id": self.backend_id,
            "profile_fingerprint": self.profile_fingerprint,
            "options": dict(self.options),
            "backend_config": dict(self.backend_config),
            "profile_policy": MODEL_PROFILE_POLICY,
        }


@dataclass(frozen=True)
class SpeechModelProfile:
    """A pinned model/revision plus validated defaults for one adapter."""

    profile_id: str
    backend_id: str
    display_name: str
    model_family: str
    model_revision: str
    aliases: tuple[str, ...] = ()
    production_enabled: bool = True
    required_capabilities: tuple[str, ...] = REQUIRED_PRODUCTION_CAPABILITIES
    option_specs: tuple[ModelOptionSpec, ...] = ()
    backend_defaults: Mapping[str, Any] = field(default_factory=dict)
    backend_override_keys: tuple[str, ...] = ()
    requires_execution_plan_evidence: bool = False

    def __post_init__(self) -> None:
        profile_id = _normalized_id(self.profile_id, field_name="profile_id")
        backend_id = _normalized_id(self.backend_id, field_name="backend_id")
        display_name = str(self.display_name or "").strip()
        model_family = str(self.model_family or "").strip()
        model_revision = str(self.model_revision or "").strip()
        if not display_name or not model_family or not model_revision:
            raise ValueError("TTS model profile требует display_name, model_family и model_revision.")

        aliases: list[str] = []
        seen_aliases: set[str] = {profile_id}
        for raw in self.aliases:
            alias = _normalized_id(raw, field_name="profile alias")
            if alias in seen_aliases:
                continue
            seen_aliases.add(alias)
            aliases.append(alias)

        specs = tuple(self.option_specs)
        spec_names = [spec.name for spec in specs]
        if len(spec_names) != len(set(spec_names)):
            raise ValueError(f"TTS model profile {profile_id} содержит дубли option names.")

        backend_defaults: dict[str, Any] = {}
        for raw_key, raw_value in dict(self.backend_defaults).items():
            key = _option_name(raw_key)
            if key in spec_names:
                raise ValueError(f"backend_defaults.{key} конфликтует с option_specs.")
            backend_defaults[key] = _scalar(raw_value, field_name=f"backend_defaults.{key}")
        override_keys = tuple(_option_name(key) for key in self.backend_override_keys)
        unknown_overrides = sorted(set(override_keys) - set(backend_defaults))
        if unknown_overrides:
            raise ValueError(
                "backend_override_keys не объявлены в backend_defaults: "
                + ", ".join(unknown_overrides)
            )

        allowed_capabilities = {
            "voice_cloning",
            "reference_audio",
            "deterministic_seed",
            "style_instruction",
            "cpu_inference",
            "pcm_output",
            "checkpointable_segments",
            "continuation_context",
        }
        required_capabilities = tuple(dict.fromkeys(self.required_capabilities))
        unknown_capabilities = sorted(set(required_capabilities) - allowed_capabilities)
        if unknown_capabilities:
            raise ValueError(
                "Неизвестные required_capabilities: " + ", ".join(unknown_capabilities)
            )

        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "backend_id", backend_id)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "model_family", model_family)
        object.__setattr__(self, "model_revision", model_revision)
        object.__setattr__(self, "aliases", tuple(aliases))
        object.__setattr__(self, "required_capabilities", required_capabilities)
        object.__setattr__(self, "option_specs", specs)
        object.__setattr__(self, "backend_defaults", backend_defaults)
        object.__setattr__(self, "backend_override_keys", override_keys)

    def _fingerprint_payload(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "backend_id": self.backend_id,
            "display_name": self.display_name,
            "model_family": self.model_family,
            "model_revision": self.model_revision,
            "aliases": list(self.aliases),
            "production_enabled": bool(self.production_enabled),
            "required_capabilities": list(self.required_capabilities),
            "option_specs": [spec.as_dict() for spec in self.option_specs],
            "backend_defaults": dict(self.backend_defaults),
            "backend_override_keys": list(self.backend_override_keys),
            "requires_execution_plan_evidence": bool(
                self.requires_execution_plan_evidence
            ),
            "profile_policy": MODEL_PROFILE_POLICY,
        }

    def fingerprint(self) -> str:
        encoded = json.dumps(
            self._fingerprint_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        payload = self._fingerprint_payload()
        payload["fingerprint"] = self.fingerprint()
        return payload

    def resolve_request(self, request: Mapping[str, Any]) -> SpeechModelResolution:
        payload = dict(request)
        raw_options = payload.get("speech_options") or {}
        if not isinstance(raw_options, Mapping):
            raise ValueError("speech_options должен быть JSON-объектом.")
        raw_options = dict(raw_options)
        known_options = {spec.name for spec in self.option_specs}
        unknown_options = sorted(set(raw_options) - known_options)
        if unknown_options:
            raise ValueError(
                f"TTS profile {self.profile_id} не поддерживает speech_options: "
                + ", ".join(unknown_options)
            )

        effective_options: dict[str, Any] = {}
        for spec in self.option_specs:
            nested_present = spec.name in raw_options
            flat_present = spec.name in payload
            nested_value = spec.coerce(raw_options[spec.name]) if nested_present else None
            flat_value = spec.coerce(payload[spec.name]) if flat_present else None
            if nested_present and flat_present and nested_value != flat_value:
                raise ValueError(
                    f"Конфликт speech_options.{spec.name} и request.{spec.name}."
                )
            value = nested_value if nested_present else flat_value if flat_present else spec.default
            if (nested_present or flat_present) and not spec.overridable and value != spec.default:
                raise ValueError(f"TTS option {spec.name} запрещено переопределять.")
            effective_options[spec.name] = value
            payload[spec.name] = value
        payload["speech_options"] = dict(effective_options)

        raw_backend_config = payload.get("speech_backend_config") or {}
        if not isinstance(raw_backend_config, Mapping):
            raise ValueError("speech_backend_config должен быть JSON-объектом.")
        raw_backend_config = dict(raw_backend_config)
        unknown_config = sorted(set(raw_backend_config) - set(self.backend_defaults))
        if unknown_config:
            raise ValueError(
                f"TTS profile {self.profile_id} не поддерживает speech_backend_config: "
                + ", ".join(unknown_config)
            )

        effective_backend_config: dict[str, Any] = {}
        override_keys = set(self.backend_override_keys)
        for key, default in self.backend_defaults.items():
            nested_present = key in raw_backend_config
            flat_present = key in payload
            nested_value = _scalar(
                raw_backend_config[key],
                field_name=f"speech_backend_config.{key}",
            ) if nested_present else None
            flat_value = _scalar(payload[key], field_name=f"request.{key}") if flat_present else None
            if nested_present and flat_present and nested_value != flat_value:
                raise ValueError(
                    f"Конфликт speech_backend_config.{key} и request.{key}."
                )
            value = nested_value if nested_present else flat_value if flat_present else default
            if (nested_present or flat_present) and key not in override_keys and value != default:
                raise ValueError(f"TTS backend config {key} запрещено переопределять.")
            effective_backend_config[key] = value
            payload[key] = value
        payload["speech_backend_config"] = dict(effective_backend_config)
        payload["speech_backend"] = self.backend_id
        payload["speech_model_profile"] = self.profile_id
        payload["speech_profile_fingerprint"] = self.fingerprint()
        return SpeechModelResolution(
            profile_id=self.profile_id,
            backend_id=self.backend_id,
            profile_fingerprint=self.fingerprint(),
            request=payload,
            options=effective_options,
            backend_config=effective_backend_config,
        )


_PROFILES: dict[str, SpeechModelProfile] = {}
_ALIASES: dict[str, str] = {}


def register_model_profile(profile: SpeechModelProfile) -> None:
    if not isinstance(profile, SpeechModelProfile):
        raise TypeError("register_model_profile ожидает SpeechModelProfile.")
    existing = _PROFILES.get(profile.profile_id)
    if existing is not None and existing is not profile:
        raise RuntimeError(f"TTS model profile уже зарегистрирован: {profile.profile_id}")
    aliases = (profile.profile_id, *profile.aliases)
    for alias in aliases:
        owner = _ALIASES.get(alias)
        if owner is not None and owner != profile.profile_id:
            raise RuntimeError(f"Alias TTS model profile уже занят: {alias}")
    _PROFILES[profile.profile_id] = profile
    for alias in aliases:
        _ALIASES[alias] = profile.profile_id


def model_profile_ids() -> tuple[str, ...]:
    return tuple(sorted(_PROFILES))


def resolve_model_profile_id(value: object) -> str:
    alias = _normalized_id(value, field_name="speech_model_profile")
    profile_id = _ALIASES.get(alias)
    if profile_id is None:
        available = ", ".join(model_profile_ids()) or "—"
        raise RuntimeError(
            f"Неизвестный TTS model profile: {value!r}. Доступно: {available}."
        )
    return profile_id


def get_model_profile(value: object) -> SpeechModelProfile:
    return _PROFILES[resolve_model_profile_id(value)]


def unregister_model_profile(value: object) -> None:
    profile_id = resolve_model_profile_id(value)
    _PROFILES.pop(profile_id, None)
    for alias, owner in tuple(_ALIASES.items()):
        if owner == profile_id:
            _ALIASES.pop(alias, None)


def registered_model_profiles() -> tuple[SpeechModelProfile, ...]:
    return tuple(_PROFILES[key] for key in sorted(_PROFILES))


__all__ = [
    "MODEL_CATALOG_POLICY",
    "MODEL_OPTION_POLICY",
    "MODEL_PROFILE_POLICY",
    "ModelOptionSpec",
    "SpeechModelProfile",
    "SpeechModelResolution",
    "get_model_profile",
    "model_profile_ids",
    "register_model_profile",
    "registered_model_profiles",
    "resolve_model_profile_id",
    "unregister_model_profile",
]
