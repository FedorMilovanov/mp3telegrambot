#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Model-independent backend and concrete TTS model selection."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from services.speech_backends.base import (
    REQUIRED_PRODUCTION_CAPABILITIES,
    BackendCapabilities,
    SpeechBackend,
)
from services.speech_backends.model_profiles import (
    SpeechModelProfile,
    SpeechModelResolution,
    get_model_profile,
)
from services.speech_backends.profile_contracts import (
    BackendModelProfileContract,
    ModelProfileContractError,
    get_backend_model_contract,
)
from services.speech_backends.registry import get_backend, resolve_backend_id

# Keep the public selection-plane policy stable. Concrete model-profile
# versioning is owned by MODEL_PROFILE_POLICY and MODEL_CATALOG_POLICY.
CONTROL_PLANE_POLICY = "speech-backend-control-plane-v1"


class SpeechBackendSelectionError(RuntimeError):
    """Base error for request-time TTS selection failures."""


class UnknownSpeechBackendError(SpeechBackendSelectionError):
    """Raised when a request names an unregistered backend or alias."""


class BackendCapabilityError(SpeechBackendSelectionError):
    """Raised when a registered backend cannot satisfy production policy."""


class UnknownSpeechModelProfileError(SpeechBackendSelectionError):
    """Raised when a request names an unregistered model profile or alias."""


class SpeechModelProfileDisabledError(SpeechBackendSelectionError):
    """Raised when a known profile is not enabled for production."""


class SpeechModelProfileMismatchError(SpeechBackendSelectionError):
    """Raised when a profile is paired with a different backend adapter."""


class SpeechModelConfigurationError(SpeechBackendSelectionError):
    """Raised when profile options or backend config fail validation."""


@dataclass(frozen=True)
class BackendSelection:
    """Validated backend identity shared by legacy control-plane callers."""

    backend_id: str
    backend: SpeechBackend
    capabilities: BackendCapabilities

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend_id": self.backend_id,
            "adapter_policy": str(getattr(self.backend, "adapter_policy", "")),
            "capabilities": self.capabilities.as_dict(),
            "control_plane_policy": CONTROL_PLANE_POLICY,
        }


@dataclass(frozen=True)
class SpeechSelection:
    """Validated adapter + profile contract + model profile + effective request."""

    backend_id: str
    backend: SpeechBackend
    capabilities: BackendCapabilities
    model_contract: BackendModelProfileContract
    model_profile: SpeechModelProfile
    resolution: SpeechModelResolution

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend_id": self.backend_id,
            "adapter_policy": str(getattr(self.backend, "adapter_policy", "")),
            "capabilities": self.capabilities.as_dict(),
            "model_contract": self.model_contract.as_dict(),
            "model_profile": self.model_profile.as_dict(),
            "resolution": self.resolution.as_dict(),
            "control_plane_policy": CONTROL_PLANE_POLICY,
        }


def _requested_backend(value: object, *, default_backend_id: str) -> object:
    default_id = str(default_backend_id or "").strip()
    if not default_id:
        raise RuntimeError("default_backend_id не может быть пустым.")
    if value is None:
        return default_id
    if isinstance(value, str) and not value.strip():
        return default_id
    return value


def _requested_profile(value: object, *, default_model_profile_id: str) -> object:
    default_id = str(default_model_profile_id or "").strip()
    if not default_id:
        raise RuntimeError("default_model_profile_id не может быть пустым.")
    if value is None:
        return default_id
    if isinstance(value, str) and not value.strip():
        return default_id
    return value


def select_production_backend(
    value: object,
    *,
    default_backend_id: str,
    required_capabilities: tuple[str, ...] = REQUIRED_PRODUCTION_CAPABILITIES,
) -> BackendSelection:
    """Resolve one backend and fail closed before work is queued."""

    requested = _requested_backend(value, default_backend_id=default_backend_id)
    try:
        backend_id = resolve_backend_id(requested)
        backend = get_backend(backend_id)
    except (RuntimeError, ValueError) as exc:
        raise UnknownSpeechBackendError(str(exc)) from exc

    capabilities = backend.capabilities()
    if not isinstance(capabilities, BackendCapabilities):
        raise BackendCapabilityError(
            f"speech_backend={backend_id} вернул некорректный capabilities contract."
        )
    missing = capabilities.missing(tuple(required_capabilities))
    if missing:
        raise BackendCapabilityError(
            f"speech_backend={backend_id} не имеет обязательных production capabilities: "
            f"{', '.join(missing)}."
        )
    return BackendSelection(
        backend_id=backend_id,
        backend=backend,
        capabilities=capabilities,
    )


def select_production_speech(
    backend_value: object,
    model_profile_value: object,
    *,
    request: Mapping[str, Any] | None,
    default_backend_id: str,
    default_model_profile_id: str,
    required_capabilities: tuple[str, ...] = REQUIRED_PRODUCTION_CAPABILITIES,
) -> SpeechSelection:
    """Resolve a concrete model deployment and its adapter as one unit."""

    requested_profile = _requested_profile(
        model_profile_value,
        default_model_profile_id=default_model_profile_id,
    )
    try:
        profile = get_model_profile(requested_profile)
    except (RuntimeError, ValueError) as exc:
        raise UnknownSpeechModelProfileError(str(exc)) from exc
    if not profile.production_enabled:
        raise SpeechModelProfileDisabledError(
            f"speech_model_profile={profile.profile_id} отключён для production."
        )

    requested_backend = backend_value
    if requested_backend is None or (
        isinstance(requested_backend, str) and not requested_backend.strip()
    ):
        requested_backend = profile.backend_id
    merged_required = tuple(
        dict.fromkeys((*required_capabilities, *profile.required_capabilities))
    )
    backend_selection = select_production_backend(
        requested_backend,
        default_backend_id=default_backend_id,
        required_capabilities=merged_required,
    )
    if backend_selection.backend_id != profile.backend_id:
        raise SpeechModelProfileMismatchError(
            f"speech_model_profile={profile.profile_id} требует backend={profile.backend_id}, "
            f"но выбран backend={backend_selection.backend_id}."
        )

    try:
        model_contract = get_backend_model_contract(backend_selection.backend_id)
        model_contract.validate_profile(profile)
    except ModelProfileContractError as exc:
        raise SpeechModelConfigurationError(str(exc)) from exc

    request_payload = dict(request or {})
    stored_fingerprint = request_payload.get("speech_profile_fingerprint")
    current_fingerprint = profile.fingerprint()
    if stored_fingerprint not in (None, "") and str(stored_fingerprint) != current_fingerprint:
        raise SpeechModelConfigurationError(
            f"speech_profile_fingerprint устарел для profile={profile.profile_id}: "
            f"stored={stored_fingerprint}, current={current_fingerprint}."
        )
    try:
        resolution = profile.resolve_request(request_payload)
    except (TypeError, ValueError) as exc:
        raise SpeechModelConfigurationError(str(exc)) from exc
    return SpeechSelection(
        backend_id=backend_selection.backend_id,
        backend=backend_selection.backend,
        capabilities=backend_selection.capabilities,
        model_contract=model_contract,
        model_profile=profile,
        resolution=resolution,
    )


def normalize_production_backend(
    value: object,
    *,
    default_backend_id: str,
    required_capabilities: tuple[str, ...] = REQUIRED_PRODUCTION_CAPABILITIES,
) -> str:
    """Return the canonical id of a production-capable backend."""

    return select_production_backend(
        value,
        default_backend_id=default_backend_id,
        required_capabilities=required_capabilities,
    ).backend_id


def normalize_production_speech_request(
    payload: Mapping[str, Any],
    *,
    default_backend_id: str,
    default_model_profile_id: str,
    required_capabilities: tuple[str, ...] = REQUIRED_PRODUCTION_CAPABILITIES,
) -> dict[str, Any]:
    """Return a durable request containing canonical model identity and options."""

    if not isinstance(payload, Mapping):
        raise SpeechModelConfigurationError("TTS request должен быть JSON-объектом.")
    request = dict(payload)
    selection = select_production_speech(
        request.get("speech_backend"),
        request.get("speech_model_profile"),
        request=request,
        default_backend_id=default_backend_id,
        default_model_profile_id=default_model_profile_id,
        required_capabilities=required_capabilities,
    )
    return dict(selection.resolution.request)


__all__ = [
    "CONTROL_PLANE_POLICY",
    "BackendCapabilityError",
    "BackendSelection",
    "SpeechBackendSelectionError",
    "SpeechModelConfigurationError",
    "SpeechModelProfileDisabledError",
    "SpeechModelProfileMismatchError",
    "SpeechSelection",
    "UnknownSpeechBackendError",
    "UnknownSpeechModelProfileError",
    "normalize_production_backend",
    "normalize_production_speech_request",
    "select_production_backend",
    "select_production_speech",
]
