#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Public model-independent speech backend API."""
from __future__ import annotations

from services.speech_backends.audited_voxcpm2 import (
    GENERATION_EXECUTION_CALL_POLICY,
    AuditedVoxCPM2Backend,
    AuditedVoxCPM2Session,
)
from services.speech_backends.base import (
    BACKEND_COMMAND_POLICY,
    BACKEND_CONTRACT_POLICY,
    BACKEND_ENVIRONMENT_POLICY,
    BACKEND_RUNTIME_PATH_POLICY,
    GENERATION_LENGTH_POLICY,
    GENERATION_LENGTH_REQUEST_POLICY,
    GENERATION_PROFILE_POLICY,
    GENERATION_PROFILE_REQUEST_POLICY,
    GENERATION_REQUEST_POLICY,
    PRODUCTION_CAPABILITY_POLICY,
    REQUIRED_PRODUCTION_CAPABILITIES,
    SESSION_CONFIG_POLICY,
    BackendAudioSpec,
    BackendCapabilities,
    BackendGenerationLengthPlan,
    BackendGenerationLengthRequest,
    BackendGenerationProfilePlan,
    BackendGenerationProfileRequest,
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
from services.speech_backends.deterministic import (
    DeterministicSession,
    DeterministicSpeechBackend,
)
from services.speech_backends.execution_plan import (
    GENERATION_EXECUTION_PLAN_POLICY,
    BackendGenerationExecutionPlan,
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

_VOXCPM2 = AuditedVoxCPM2Backend()
register_backend(_VOXCPM2)


def default_backend() -> SpeechBackend:
    return get_backend(DEFAULT_BACKEND_ID)


__all__ = [
    "BACKEND_COMMAND_POLICY",
    "BACKEND_CONTRACT_POLICY",
    "BACKEND_ENVIRONMENT_POLICY",
    "BACKEND_RUNTIME_PATH_POLICY",
    "CONTROL_PLANE_POLICY",
    "GENERATION_EXECUTION_CALL_POLICY",
    "GENERATION_EXECUTION_PLAN_POLICY",
    "GENERATION_LENGTH_POLICY",
    "GENERATION_LENGTH_REQUEST_POLICY",
    "GENERATION_PROFILE_POLICY",
    "GENERATION_PROFILE_REQUEST_POLICY",
    "GENERATION_REQUEST_POLICY",
    "PRODUCTION_CAPABILITY_POLICY",
    "REQUIRED_PRODUCTION_CAPABILITIES",
    "SESSION_CONFIG_POLICY",
    "DEFAULT_BACKEND_ID",
    "REGISTRY_POLICY",
    "AuditedVoxCPM2Backend",
    "AuditedVoxCPM2Session",
    "BackendAudioSpec",
    "BackendCapabilities",
    "BackendCapabilityError",
    "BackendGenerationExecutionPlan",
    "BackendGenerationLengthPlan",
    "BackendGenerationLengthRequest",
    "BackendGenerationProfilePlan",
    "BackendGenerationProfileRequest",
    "BackendGenerationRequest",
    "BackendIdentity",
    "BackendProcessEnvironment",
    "BackendRuntimePaths",
    "BackendSelection",
    "BackendSessionConfig",
    "BackendSynthesisSession",
    "DeterministicSession",
    "DeterministicSpeechBackend",
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
