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
    DEFAULT_MODEL_PROFILE_ID as CONFIGURED_DEFAULT_MODEL_PROFILE_ID,
    builtin_model_profile_records,
    builtin_model_profiles,
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
from services.speech_backends.profile_manifests import (
    PROFILE_MANIFEST_POLICY,
    PROFILE_MANIFEST_SCHEMA_VERSION,
    ProfileManifestError,
    ProfileManifestRecord,
    catalog_snapshot,
    default_profile_manifest_root,
    load_profile_catalog,
    load_profile_manifest,
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
RUNTIME_PROFILE_SOURCE_POLICY = "runtime-registered-tts-profile-source-v1"

_VOXCPM2 = AuditedVoxCPM2Backend()
register_backend(_VOXCPM2)
register_backend_model_contract(voxcpm2_model_profile_contract())
register_backend_model_contract(deterministic_model_profile_contract())
_PROFILE_MANIFEST_RECORDS = builtin_model_profile_records()
for _record in _PROFILE_MANIFEST_RECORDS:
    _contract = get_backend_model_contract(_record.profile.backend_id)
    _contract.validate_profile(_record.profile)
    register_model_profile(_record.profile)
DEFAULT_MODEL_PROFILE_ID = resolve_model_profile_id(CONFIGURED_DEFAULT_MODEL_PROFILE_ID)
_DEFAULT_MODEL_PROFILE = get_model_profile(DEFAULT_MODEL_PROFILE_ID)
if not _DEFAULT_MODEL_PROFILE.production_enabled:
    raise RuntimeError(
        f"Default TTS model profile отключён для production: {DEFAULT_MODEL_PROFILE_ID}"
    )
# Preserve the legacy revision guard even though the profile is now declarative.
voxcpm2_production_profile()


def default_backend() -> SpeechBackend:
    return get_backend(DEFAULT_BACKEND_ID)


def default_model_profile() -> SpeechModelProfile:
    return get_model_profile(DEFAULT_MODEL_PROFILE_ID)


def model_profile_manifest_records() -> tuple[ProfileManifestRecord, ...]:
    return _PROFILE_MANIFEST_RECORDS


def model_profile_manifest_record(
    value: object,
) -> ProfileManifestRecord | None:
    """Return repository manifest provenance for one registered profile."""
    canonical = resolve_model_profile_id(value)
    matches = tuple(
        record
        for record in _PROFILE_MANIFEST_RECORDS
        if record.profile.profile_id == canonical
    )
    if len(matches) > 1:
        raise RuntimeError(
            f"TTS profile {canonical} имеет несколько manifest records."
        )
    return matches[0] if matches else None


def model_profile_source_evidence(value: object) -> dict[str, object]:
    """Return safe provenance without exposing backend config or secret values."""
    canonical = resolve_model_profile_id(value)
    record = model_profile_manifest_record(canonical)
    if record is not None:
        payload = record.as_dict(root=default_profile_manifest_root())
        payload["profile_id"] = canonical
        payload["source_kind"] = "repository-manifest"
        return payload
    profile = get_model_profile(canonical)
    return {
        "schema_version": 1,
        "profile_id": canonical,
        "backend_id": profile.backend_id,
        "model_revision": profile.model_revision,
        "source": "runtime-registration",
        "source_kind": "runtime-registration",
        "source_sha256": "",
        "manifest_policy": RUNTIME_PROFILE_SOURCE_POLICY,
    }


def model_profile_catalog_snapshot() -> dict[str, object]:
    return catalog_snapshot(_PROFILE_MANIFEST_RECORDS)


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
    "PROFILE_MANIFEST_POLICY",
    "PROFILE_MANIFEST_SCHEMA_VERSION",
    "PRODUCTION_CAPABILITY_POLICY",
    "REQUIRED_PRODUCTION_CAPABILITIES",
    "RUNTIME_PROFILE_SOURCE_POLICY",
    "SESSION_CONFIG_POLICY",
    "CONFIGURED_DEFAULT_MODEL_PROFILE_ID",
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
    "ProfileManifestError",
    "ProfileManifestRecord",
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
    "builtin_model_profile_records",
    "builtin_model_profiles",
    "catalog_snapshot",
    "default_backend",
    "default_model_profile",
    "default_profile_manifest_root",
    "deterministic_model_profile_contract",
    "get_backend",
    "get_backend_model_contract",
    "get_model_profile",
    "load_profile_catalog",
    "load_profile_manifest",
    "model_profile_catalog_snapshot",
    "model_profile_ids",
    "model_profile_manifest_record",
    "model_profile_manifest_records",
    "model_profile_source_evidence",
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
    "voxcpm2_production_profile",
]
