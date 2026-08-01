#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Public model-independent speech backend and model-profile API."""
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
from services.speech_backends.builtin_contracts import (
    deterministic_model_profile_contract,
    voxcpm2_model_profile_contract,
)
from services.speech_backends.builtin_profiles import (
    DEFAULT_MODEL_PROFILE_ID,
    voxcpm2_production_profile,
)
from services.speech_backends.control_plane import (
    CONTROL_PLANE_POLICY,
    BackendCapabilityError,
    BackendSelection,
    SpeechBackendSelectionError,
    SpeechModelConfigurationError,
    SpeechModelProfileDisabledError,
    SpeechModelProfileMismatchError,
    SpeechSelection,
    UnknownSpeechBackendError,
    UnknownSpeechModelProfileError,
    normalize_production_backend,
    normalize_production_speech_request,
    select_production_backend,
    select_production_speech,
)
from services.speech_backends.deterministic import (
    DeterministicSession,
    DeterministicSpeechBackend,
)
from services.speech_backends.execution_plan import (
    GENERATION_EXECUTION_PLAN_POLICY,
    BackendGenerationExecutionPlan,
)
from services.speech_backends.model_profiles import (
    MODEL_CATALOG_POLICY,
    MODEL_OPTION_POLICY,
    MODEL_PROFILE_POLICY,
    ModelOptionSpec,
    SpeechModelProfile,
    SpeechModelResolution,
    get_model_profile,
    model_profile_ids,
    register_model_profile,
    registered_model_profiles,
    resolve_model_profile_id,
    unregister_model_profile,
)
from services.speech_backends.profile_contracts import (
    MODEL_PROFILE_CONTRACT_POLICY,
    BackendModelProfileContract,
    ModelProfileContractError,
    backend_model_contract_ids,
    get_backend_model_contract,
    register_backend_model_contract,
    unregister_backend_model_contract,
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
register_backend_model_contract(voxcpm2_model_profile_contract())
register_backend_model_contract(deterministic_model_profile_contract())
_VOXCPM2_PRODUCTION_PROFILE = voxcpm2_production_profile()
register_model_profile(_VOXCPM2_PRODUCTION_PROFILE)


def default_backend() -> SpeechBackend:
    return get_backend(DEFAULT_BACKEND_ID)


def default_model_profile() -> SpeechModelProfile:
    return get_model_profile(DEFAULT_MODEL_PROFILE_ID)


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
    "MODEL_CATALOG_POLICY",
    "MODEL_OPTION_POLICY",
    "MODEL_PROFILE_CONTRACT_POLICY",
    "MODEL_PROFILE_POLICY",
    "PRODUCTION_CAPABILITY_POLICY",
    "REQUIRED_PRODUCTION_CAPABILITIES",
    "SESSION_CONFIG_POLICY",
    "DEFAULT_BACKEND_ID",
    "DEFAULT_MODEL_PROFILE_ID",
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
    "BackendModelProfileContract",
    "BackendProcessEnvironment",
    "BackendRuntimePaths",
    "BackendSelection",
    "BackendSessionConfig",
    "BackendSynthesisSession",
    "DeterministicSession",
    "DeterministicSpeechBackend",
    "ModelOptionSpec",
    "ModelProfileContractError",
    "SpeechBackend",
    "SpeechBackendSelectionError",
    "SpeechModelConfigurationError",
    "SpeechModelProfile",
    "SpeechModelProfileDisabledError",
    "SpeechModelProfileMismatchError",
    "SpeechModelResolution",
    "SpeechSelection",
    "UnknownSpeechBackendError",
    "UnknownSpeechModelProfileError",
    "VoxCPM2Backend",
    "VoxCPM2Session",
    "backend_ids",
    "backend_model_contract_ids",
    "default_backend",
    "default_model_profile",
    "deterministic_model_profile_contract",
    "get_backend",
    "get_backend_model_contract",
    "get_model_profile",
    "model_profile_ids",
    "normalize_production_backend",
    "normalize_production_speech_request",
    "register_backend",
    "register_backend_model_contract",
    "register_model_profile",
    "registered_backends",
    "registered_model_profiles",
    "resolve_backend_id",
    "resolve_model_profile_id",
    "select_production_backend",
    "select_production_speech",
    "unregister_backend",
    "unregister_backend_model_contract",
    "unregister_model_profile",
    "voxcpm2_model_profile_contract",
]
