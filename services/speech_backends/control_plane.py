#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Model-independent backend selection and production capability validation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.speech_backends.base import (
    REQUIRED_PRODUCTION_CAPABILITIES,
    BackendCapabilities,
    SpeechBackend,
)
from services.speech_backends.registry import get_backend, resolve_backend_id

CONTROL_PLANE_POLICY = "speech-backend-control-plane-v1"


class SpeechBackendSelectionError(RuntimeError):
    """Base error for request-time backend selection failures."""


class UnknownSpeechBackendError(SpeechBackendSelectionError):
    """Raised when a request names an unregistered backend or alias."""


class BackendCapabilityError(SpeechBackendSelectionError):
    """Raised when a registered backend cannot satisfy production policy."""


@dataclass(frozen=True)
class BackendSelection:
    """Validated backend identity shared by control-plane callers."""

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


def _requested_backend(value: object, *, default_backend_id: str) -> object:
    default_id = str(default_backend_id or "").strip()
    if not default_id:
        raise RuntimeError("default_backend_id не может быть пустым.")
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
    except RuntimeError as exc:
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


__all__ = [
    "CONTROL_PLANE_POLICY",
    "BackendCapabilityError",
    "BackendSelection",
    "SpeechBackendSelectionError",
    "UnknownSpeechBackendError",
    "normalize_production_backend",
    "select_production_backend",
]
