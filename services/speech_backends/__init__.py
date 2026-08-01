#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Public model-independent speech backend API."""
from __future__ import annotations

from services.speech_backends.base import (
    BACKEND_COMMAND_POLICY,
    BACKEND_CONTRACT_POLICY,
    BACKEND_ENVIRONMENT_POLICY,
    BACKEND_RUNTIME_PATH_POLICY,
    GENERATION_REQUEST_POLICY,
    PRODUCTION_CAPABILITY_POLICY,
    REQUIRED_PRODUCTION_CAPABILITIES,
    SESSION_CONFIG_POLICY,
    BackendAudioSpec,
    BackendCapabilities,
    BackendGenerationRequest,
    BackendIdentity,
    BackendProcessEnvironment,
    BackendRuntimePaths,
    BackendSessionConfig,
    BackendSynthesisSession,
    SpeechBackend,
)
from services.speech_backends.control_plane import (
    CONTROL_PLANE_POLICY,
    BackendCapabilityError,
    BackendSelection,
    SpeechBackendSelectionError,
    UnknownSpeechBackendError,
    normalize_production_backend,
    select_production_backend,
)
from services.speech_backends.registry import (
    REGISTRY_POLICY,
    backend_ids,
    get_backend,
    register_backend,
    registered_backends,
    resolve_backend_id,
    unregister_backend,
)
from services.speech_backends.voxcpm2 import VoxCPM2Backend, VoxCPM2Session

DEFAULT_BACKEND_ID = "voxcpm2"

_VOXCPM2 = VoxCPM2Backend()
register_backend(_VOXCPM2)


def default_backend() -> SpeechBackend:
    return get_backend(DEFAULT_BACKEND_ID)


__all__ = [
    "BACKEND_COMMAND_POLICY",
    "BACKEND_CONTRACT_POLICY",
    "BACKEND_ENVIRONMENT_POLICY",
    "BACKEND_RUNTIME_PATH_POLICY",
    "CONTROL_PLANE_POLICY",
    "GENERATION_REQUEST_POLICY",
    "PRODUCTION_CAPABILITY_POLICY",
    "REQUIRED_PRODUCTION_CAPABILITIES",
    "SESSION_CONFIG_POLICY",
    "DEFAULT_BACKEND_ID",
    "REGISTRY_POLICY",
    "BackendAudioSpec",
    "BackendCapabilities",
    "BackendCapabilityError",
    "BackendGenerationRequest",
    "BackendIdentity",
    "BackendProcessEnvironment",
    "BackendRuntimePaths",
    "BackendSelection",
    "BackendSessionConfig",
    "BackendSynthesisSession",
    "SpeechBackend",
    "SpeechBackendSelectionError",
    "UnknownSpeechBackendError",
    "VoxCPM2Backend",
    "VoxCPM2Session",
    "backend_ids",
    "default_backend",
    "get_backend",
    "normalize_production_backend",
    "register_backend",
    "registered_backends",
    "resolve_backend_id",
    "select_production_backend",
    "unregister_backend",
]
