#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Built-in pinned TTS model profiles shipped with the bot."""
from __future__ import annotations

import os

from services.speech_backends.model_profiles import ModelOptionSpec, SpeechModelProfile

DEFAULT_MODEL_PROFILE_ID = "voxcpm2-production-v1"
_DEFAULT_CPU_VENV = r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv"
_DEFAULT_ARCHIVE = r"C:\AI-Archive\VoxCPM2-paused-RTX3060"


def voxcpm2_production_profile() -> SpeechModelProfile:
    """Return the currently pinned production deployment of VoxCPM2."""
    return SpeechModelProfile(
        profile_id=DEFAULT_MODEL_PROFILE_ID,
        backend_id="voxcpm2",
        display_name="VoxCPM2 production",
        model_family="OpenBMB/VoxCPM2",
        model_revision=os.getenv("DUB_VOX_MODEL_REVISION", "local-archive-pinned-v1"),
        aliases=("voxcpm2-default", "default-tts"),
        option_specs=(
            ModelOptionSpec("threads", "int", 10, minimum=1, maximum=64),
            ModelOptionSpec("steps", "int", 16, minimum=1, maximum=256),
            ModelOptionSpec("cfg", "float", 1.8, minimum=0.1, maximum=10.0),
            ModelOptionSpec(
                "cache_length",
                "int",
                4096,
                minimum=2048,
                maximum=131072,
            ),
            ModelOptionSpec(
                "base_seed",
                "int",
                2026072800,
                minimum=0,
                maximum=2147483647,
            ),
        ),
        backend_defaults={
            "vox_archive": os.getenv("DUB_VOX_ARCHIVE", _DEFAULT_ARCHIVE),
            "cpu_venv": os.getenv("DUB_CPU_VENV", _DEFAULT_CPU_VENV),
        },
        backend_override_keys=("vox_archive", "cpu_venv"),
        requires_execution_plan_evidence=True,
    )


__all__ = ["DEFAULT_MODEL_PROFILE_ID", "voxcpm2_production_profile"]
