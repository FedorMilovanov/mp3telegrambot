#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Public model-independent speech backend API."""
from __future__ import annotations

from services.speech_backends.base import (
    BACKEND_COMMAND_POLICY,
    BACKEND_CONTRACT_POLICY,
    BACKEND_ENVIRONMENT_POLICY,
    BACKEND_RUNTIME_PATH_POLICY,
    BackendAudioSpec,
    BackendCapabilities,
    BackendIdentity,
    BackendProcessEnvironment,
    BackendRuntimePaths,
    BackendSynthesisSession,
    SpeechBackend,
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
    "DEFAULT_BACKEND_ID",
    "REGISTRY_POLICY",
    "BackendAudioSpec",
    "BackendCapabilities",
    "BackendIdentity",
    "BackendProcessEnvironment",
    "BackendRuntimePaths",
    "BackendSynthesisSession",
    "SpeechBackend",
    "VoxCPM2Backend",
    "VoxCPM2Session",
    "backend_ids",
    "default_backend",
    "get_backend",
    "register_backend",
    "registered_backends",
    "resolve_backend_id",
    "unregister_backend",
]
