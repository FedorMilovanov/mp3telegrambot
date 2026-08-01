#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Configuration contracts for built-in speech backend adapters."""
from __future__ import annotations

from services.speech_backends.profile_contracts import BackendModelProfileContract


def voxcpm2_model_profile_contract() -> BackendModelProfileContract:
    return BackendModelProfileContract(
        backend_id="voxcpm2",
        option_keys=("threads", "steps", "cfg", "cache_length", "base_seed"),
        required_option_keys=(
            "threads",
            "steps",
            "cfg",
            "cache_length",
            "base_seed",
        ),
        backend_config_keys=("vox_archive", "cpu_venv"),
        required_backend_config_keys=("vox_archive", "cpu_venv"),
        execution_plan_evidence_supported=True,
    )


def deterministic_model_profile_contract(
    backend_id: str = "deterministic-ci",
) -> BackendModelProfileContract:
    return BackendModelProfileContract(
        backend_id=backend_id,
        option_keys=("sample_rate",),
        required_option_keys=("sample_rate",),
        backend_config_keys=("deterministic_archive",),
        required_backend_config_keys=("deterministic_archive",),
        execution_plan_evidence_supported=False,
    )


__all__ = [
    "deterministic_model_profile_contract",
    "voxcpm2_model_profile_contract",
]
