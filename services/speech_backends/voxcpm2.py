#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VoxCPM2 adapter implementing the generic speech-backend contract."""
from __future__ import annotations

from pathlib import Path

from services.speech_backends.base import BackendCapabilities, BackendIdentity

ADAPTER_POLICY = "voxcpm2-speech-backend-adapter-v1"


class VoxCPM2Backend:
    backend_id = "voxcpm2"
    aliases = ("vox-cpm2", "openbmb-voxcpm2", "openbmb")
    adapter_policy = ADAPTER_POLICY

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            voice_cloning=True,
            reference_audio=True,
            deterministic_seed=True,
            style_instruction=True,
            cpu_inference=True,
            pcm_output=True,
            checkpointable_segments=True,
        )

    def discover_model(self, archive_root: Path) -> Path:
        # Imported lazily so the generic service layer does not import model code
        # merely to list registered backends.
        from tools.voxcpm2.direct_max_quality_io import discover_model

        return discover_model(Path(archive_root))

    def identity(self, archive_root: Path) -> BackendIdentity:
        model = self.discover_model(Path(archive_root)).resolve()
        return BackendIdentity(
            backend_id=self.backend_id,
            family="reference-conditioned-generative-tts",
            adapter_policy=self.adapter_policy,
            model_path=str(model),
            runtime_module="voxcpm",
            parameter_schema=(
                "threads",
                "steps",
                "cfg",
                "base_seed",
                "cache_length",
            ),
            output_contract="mono-pcm-wav-segment-v1",
        )


__all__ = ["ADAPTER_POLICY", "VoxCPM2Backend"]
