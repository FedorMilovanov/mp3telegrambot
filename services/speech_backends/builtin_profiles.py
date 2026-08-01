#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Built-in pinned TTS profiles loaded from repository-owned manifests."""
from __future__ import annotations

import os

from services.speech_backends.model_profiles import SpeechModelProfile
from services.speech_backends.profile_manifests import (
    ProfileManifestError,
    ProfileManifestRecord,
    load_profile_catalog,
)

_FALLBACK_MODEL_PROFILE_ID = "voxcpm2-production-v1"
DEFAULT_MODEL_PROFILE_ID = (
    os.getenv("DUB_DEFAULT_TTS_PROFILE", _FALLBACK_MODEL_PROFILE_ID).strip()
    or _FALLBACK_MODEL_PROFILE_ID
)
_BUILTIN_PROFILE_RECORDS = load_profile_catalog()


def builtin_model_profile_records() -> tuple[ProfileManifestRecord, ...]:
    return _BUILTIN_PROFILE_RECORDS


def builtin_model_profiles() -> tuple[SpeechModelProfile, ...]:
    return tuple(record.profile for record in _BUILTIN_PROFILE_RECORDS)


def _profile_by_id(profile_id: str) -> SpeechModelProfile:
    for record in _BUILTIN_PROFILE_RECORDS:
        if record.profile.profile_id == profile_id:
            return record.profile
    raise ProfileManifestError(
        f"Обязательный встроенный TTS profile отсутствует: {profile_id}"
    )


def voxcpm2_production_profile() -> SpeechModelProfile:
    """Return the pinned VoxCPM2 deployment from its declarative manifest."""
    profile = _profile_by_id(_FALLBACK_MODEL_PROFILE_ID)
    legacy_revision = os.getenv("DUB_VOX_MODEL_REVISION", "").strip()
    if legacy_revision and legacy_revision != profile.model_revision:
        raise ProfileManifestError(
            "DUB_VOX_MODEL_REVISION больше не может скрыто менять model identity. "
            f"Создайте новый config/tts_models/*.json profile; manifest revision="
            f"{profile.model_revision!r}, env revision={legacy_revision!r}."
        )
    return profile


__all__ = [
    "DEFAULT_MODEL_PROFILE_ID",
    "builtin_model_profile_records",
    "builtin_model_profiles",
    "voxcpm2_production_profile",
]
