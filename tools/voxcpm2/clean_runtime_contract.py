#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runtime fingerprints including universal VoxCPM2 production hardening."""
from pathlib import Path

_ORIGINAL_NAME = __name__
_BASE = Path(__file__).with_name("_clean_runtime_contract_base.py")
if not _BASE.is_file():
    raise RuntimeError(f"Missing runtime contract base snapshot: {_BASE}")
globals()["__name__"] = "tools.voxcpm2._clean_runtime_contract_base_exec"
exec(compile(_BASE.read_text(encoding="utf-8-sig"), str(_BASE), "exec"), globals())
globals()["__name__"] = _ORIGINAL_NAME

from tools.voxcpm2.direct_universal_runtime import install_runtime_fingerprint

install_runtime_fingerprint(globals())
_RENDER_MODULES = tuple(
    dict.fromkeys(
        (
            *_RENDER_MODULES,
            "tools/voxcpm2/direct_failure_recovery.py",
            "tools/voxcpm2/direct_surgical_guard.py",
            "tools/voxcpm2/direct_surgical_io.py",
            "tools/voxcpm2/direct_surgical_runtime.py",
            "tools/voxcpm2/direct_surgical_polish_v2.py",
            "services/speech_backends/audited_voxcpm2.py",
            "services/speech_backends/base.py",
            "services/speech_backends/control_plane.py",
            "services/speech_backends/execution_plan.py",
            "services/speech_backends/model_profiles.py",
            "services/speech_backends/registry.py",
        )
    )
)
