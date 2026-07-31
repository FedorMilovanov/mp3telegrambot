#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Public model-independent speech backend API."""
from __future__ import annotations

from services.speech_backends.base import (
    BACKEND_CONTRACT_POLICY,
    BackendCapabilities,
    BackendIdentity,
    BackendRuntimePaths,
    SpeechBackend,
)
from services.speech_backends.registry import (
    REGISTRY_POLICY,
    backend_ids,
    get_backend,
    register_backend,
    registered_backends,
    resolve_backend_id,
)
from services.speech_backends.voxcpm2 import VoxCPM2Backend

DEFAULT_BACKEND_ID = "voxcpm2"

_VOXCPM2 = VoxCPM2Backend()
register_backend(_VOXCPM2)


def default_backend() -> SpeechBackend:
    return get_backend(DEFAULT_BACKEND_ID)


__all__ = [
    "BACKEND_CONTRACT_POLICY",
    "DEFAULT_BACKEND_ID",
    "REGISTRY_POLICY",
    "BackendCapabilities",
    "BackendIdentity",
    "BackendRuntimePaths",
    "SpeechBackend",
    "VoxCPM2Backend",
    "backend_ids",
    "default_backend",
    "get_backend",
    "register_backend",
    "registered_backends",
    "resolve_backend_id",
]
